"""Money as a sense — census doc 15 slice F8.

"How much L$ do I have?" is a question she should be able to
answer from state, and a change in her balance — money arriving,
money leaving — is, per doc 03's salience rules already named in
doc 15, always salient. Until this slice the seam had no balance
state and no transaction record at all: LibreMetaverse exposes
both, but nothing carried them to the mind.

This module is the seam-side store for the *sense* half of F8
only: balance and transaction events arrive as seam events, are
held with exactly the fields they carried, and can be queried
and re-read. There is deliberately **no spend path here at
all** — no pay, no give, no buy command builder of any kind.
Doc 15 is explicit: building the sense is free; building the
spend path is a separate, later slice that does not exist until
Volmarr rules on it, and the governor's default-deny for money
commands (``pay`` / ``give_money`` / ``give_linden`` →
``payment_requires_operator_approval``, in
``core/governor.py``) stands untouched by this slice.

Bounds, enforced here in the body, not by convention:

- **Balance is never invented.** Before any balance event
  arrives the balance is UNKNOWN — not L$0.
  :attr:`MoneyStore.balance_known` distinguishes an unknown
  balance from a balance event that explicitly carried zero.
  An unknown balance is never presented as L$0 anywhere,
  including :meth:`MoneyStore.describe`.
- **Balances are non-negative integers, strictly.** A balance
  (standalone, or a transaction's resulting balance) must be
  an ``int`` and >= 0. Booleans (an ``int`` subclass in Python,
  refused explicitly), floats (even integral ones), negatives,
  strings, and every other type fail closed.
- **Transactions are attributed held records, never facts.**
  A transaction holds exactly what it carried — a signed
  non-zero integer amount, a counterparty (id and/or name),
  and direction / kind / description / timestamp only when
  carried; sparse fields stay ``None``, never filled from a
  guess. A missing or invalid amount, a zero amount, or a
  transaction with no counterparty at all fails closed: an
  unattributed transfer is not a record this seam will hold.
  Nothing here promotes a transaction into a canonical claim
  about the world.
- **Balance changes ride the events that carry them.** A
  balance event replaces the balance wholesale. A transaction
  that carries a resulting balance updates the balance to
  exactly that value (validated like any balance); a
  transaction without one leaves the balance as it was — the
  seam never computes a new balance by adding an amount to the
  old one, because fees, holds, and partial reports would make
  that arithmetic a guess.
- **Unknown and malformed entries fail closed.** An event this
  store does not understand, or a malformed balance /
  transaction, goes into the ``rejected`` audit list (or raises
  at the direct API) and changes nothing; ingest never raises.

Scope honesty: this is the API/seam side of F8, sense-half
only. The C# Core does not yet emit balance or transaction
events — that is the named Core leg (a purplelongship slice,
like F1–F7's). The event names added to the seam vocabulary in
``core/models.py`` are the contract that leg will speak. Until
the Core leg lands, balance state and transaction history can
be held and queried entirely on this side. There is no Core
leg for a spend path at all: none is designed here, and none
should be inferred from this slice.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core.models import Command, SeamEvent

# Canonical seam event names this store ingests — the F8 names
# added to core/models.py with this slice.
MONEY_EVENTS = frozenset(
    {
        "balance_received",
        "transaction_received",
    }
)

# Payload keys a balance value may ride under, in priority
# order. The first key actually present wins.
_BALANCE_KEYS = ("balance", "new_balance", "current_balance", "linden_balance")
# Payload keys a transaction's resulting balance may ride under.
_RESULTING_BALANCE_KEYS = ("resulting_balance", "balance_after", "new_balance", "balance")
# Payload keys a transaction amount may ride under.
_AMOUNT_KEYS = ("amount", "value", "delta", "lindens")
_COUNTERPARTY_ID_KEYS = (
    "counterparty_id", "counterparty_uuid", "sender_id", "from_id",
    "from_uuid", "source_id", "other_id", "resident_id", "avatar_id",
    "counterparty",
)
_COUNTERPARTY_NAME_KEYS = (
    "counterparty_name", "sender_name", "from_name", "other_name",
    "resident_name", "avatar_name", "counterparty_display_name",
)


def _validate_balance(value: Any, label: str = "balance") -> int:
    """Validate a balance value strictly; return it as an int.

    Only a real ``int`` >= 0 passes. ``bool`` is refused first
    (it is an ``int`` subclass and ``True`` is not L$1), floats
    are refused even when integral, and strings / None / every
    other type are refused — a balance is never coerced."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"a {label} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"a {label} must be non-negative")
    return int(value)


def _validate_amount(value: Any) -> int:
    """Validate a transaction amount strictly; return it.

    A signed, non-zero ``int`` only: booleans, floats, strings,
    a missing value, and zero all fail closed. Zero is refused
    because a zero-amount "transaction" moved no money — filing
    it would invent an event the grid never reported as one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("a transaction amount must be a signed integer")
    if value == 0:
        raise ValueError("a transaction amount must not be zero")
    return int(value)


def _first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


@dataclass
class TransactionRecord:
    """One transaction — an attributed held record, not a fact.

    Exactly what the event carried: a signed amount and a
    counterparty, plus direction / kind / description /
    resulting balance only when carried (``None`` otherwise)."""

    amount: int
    timestamp: float
    counterparty_id: str | None = None
    counterparty_name: str | None = None
    direction: str | None = None
    kind: str | None = None
    description: str | None = None
    resulting_balance: int | None = None
    transaction_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class MoneyState:
    """A snapshot of the money state, honest about its gaps."""

    known: bool
    balance: int | None
    updated_at: float | None
    transactions: int


@dataclass
class MoneyStore:
    """The money store: ingest, query, purge — and never spend."""

    _clock: Any = field(default=time.time)
    _balance: int | None = field(default=None, init=False)
    _known: bool = field(default=False, init=False)
    _updated_at: float | None = field(default=None, init=False)
    _transactions: list[TransactionRecord] = field(
        default_factory=list, init=False
    )
    # Fail-closed audit: (reason, detail) for every refused ingest.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else float(now)

    @staticmethod
    def _payload_timestamp(payload: dict[str, Any], fallback: float) -> float:
        raw = payload.get("timestamp")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw).timestamp()
            except ValueError:
                return fallback
        return fallback

    # -- honesty flags ------------------------------------------------------
    @property
    def balance_known(self) -> bool:
        """True only once a balance event (or a transaction
        carrying a resulting balance) has arrived. Before that
        the balance is UNKNOWN — never L$0."""
        return self._known

    @property
    def balance(self) -> int | None:
        """The current balance in L$, or None while unknown."""
        return self._balance

    @property
    def balance_updated_at(self) -> float | None:
        """When the balance last changed, if an event carried /
        implied a time; None while the balance is unknown."""
        return self._updated_at

    # -- queries --------------------------------------------------------------
    def current_balance(self) -> int | None:
        """The current balance in L$, or None while unknown."""
        return self._balance

    def current_state(self) -> MoneyState:
        return MoneyState(
            known=self._known,
            balance=self._balance,
            updated_at=self._updated_at,
            transactions=len(self._transactions),
        )

    def describe(self) -> str:
        """Answer "how much L$ do I have?" — honestly.

        An unknown balance is spoken as unknown, never as L$0;
        a known balance is spoken as the exact integer held."""
        if not self._known:
            return (
                "Balance unknown — no balance event has arrived, "
                "so how much L$ I have is not known (not L$0: "
                "unknown)."
            )
        text = f"Balance: L${self._balance}."
        if self._transactions:
            text += (
                f" {len(self._transactions)} transaction(s) on record."
            )
        return text

    def transactions(self) -> list[TransactionRecord]:
        """All held transactions, in arrival order — re-readable."""
        return list(self._transactions)

    def transaction_history(self) -> list[TransactionRecord]:
        """Alias for :meth:`transactions`, in sibling naming."""
        return self.transactions()

    def recent_transactions(
        self, limit: int = 10
    ) -> list[TransactionRecord]:
        """The most recent ``limit`` transactions, in arrival
        order (oldest of the slice first, newest last)."""
        if limit is None:
            return list(self._transactions)
        count = int(limit)
        if count <= 0:
            return []
        return list(self._transactions[-count:])

    # -- direct recording (the confirm/record step) ---------------------------
    def record_balance(
        self, balance: Any, *, now: float | None = None
    ) -> int:
        """Record a balance wholesale. Raises ValueError /
        TypeError for any value that is not a non-negative int
        (see :func:`_validate_balance`)."""
        value = _validate_balance(balance)
        moment = self._now(now)
        self._balance = value
        self._known = True
        self._updated_at = moment
        return value

    def record_transaction(
        self,
        amount: Any,
        *,
        counterparty_id: str | None = None,
        counterparty_name: str | None = None,
        direction: str | None = None,
        kind: str | None = None,
        description: str | None = None,
        resulting_balance: Any = None,
        transaction_id: str | None = None,
        payload: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> TransactionRecord:
        """File one attributed transaction. Raises ValueError /
        TypeError for a missing/invalid/zero amount, for no
        counterparty at all, or for an invalid resulting
        balance. A valid resulting balance updates the held
        balance to exactly that value."""
        value = _validate_amount(amount)
        cp_id = (
            None if counterparty_id is None else str(counterparty_id)
        )
        cp_name = (
            None if counterparty_name is None else str(counterparty_name)
        )
        if not (cp_id and cp_id.strip()) and not (
            cp_name and cp_name.strip()
        ):
            raise ValueError(
                "a transaction without a counterparty cannot be filed"
            )
        resulting: int | None = None
        if resulting_balance is not None:
            resulting = _validate_balance(
                resulting_balance, label="resulting balance"
            )
        moment = self._now(now)
        record = TransactionRecord(
            amount=value,
            timestamp=moment,
            counterparty_id=cp_id or None,
            counterparty_name=cp_name or None,
            direction=None if direction is None else str(direction),
            kind=None if kind is None else str(kind),
            description=None if description is None else str(description),
            resulting_balance=resulting,
            transaction_id=(
                None if transaction_id is None else str(transaction_id)
            ),
            payload=dict(payload or {}),
        )
        self._transactions.append(record)
        if resulting is not None:
            self._balance = resulting
            self._known = True
            self._updated_at = moment
        return record

    # -- the one build-only helper: a read-only refresh request ----------------
    def build_balance_request_command(self, command_id: str) -> Command:
        """Build (do not send, do not record) a read-only
        ``request_balance`` command (tier="mind").

        This is the ONLY command this store builds, and it asks
        for information — it moves no money, names no payee and
        no amount, and is not one of the governor's money
        commands. Building it changes no state; the balance
        changes only when a balance event arrives in answer.
        There is intentionally no pay / give / buy builder
        anywhere in this module."""
        return Command(
            id=str(command_id), cmd="request_balance", params={}, tier="mind"
        )

    # -- ingest -------------------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: file what a seam event carries.

        Never raises — a malformed or unknown entry is recorded
        in ``rejected`` and returns None, so one bad event cannot
        stop the perception stream (the dispatcher's own
        isolation rule)."""
        if event.name not in MONEY_EVENTS:
            self.rejected.append(("not_a_money_event", event.name))
            return None
        try:
            handler = {
                "balance_received": self._ingest_balance,
                "transaction_received": self._ingest_transaction,
            }[event.name]
            return handler(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    def _ingest_balance(self, event: SeamEvent) -> int:
        payload = event.payload
        # A balance may ride nested under a "balance" object in
        # some wire shapes; a dict there is data, not the value.
        raw = None
        for key in _BALANCE_KEYS:
            if key in payload and not isinstance(payload.get(key), dict):
                raw = payload[key]
                break
        if raw is None:
            raise ValueError("a balance event without a balance cannot be filed")
        moment = (
            self._payload_timestamp(payload, self._now(None))
            if "timestamp" in payload
            else None
        )
        return self.record_balance(raw, now=moment)

    def _transaction_from_entry(self, entry: Any, fallback: float) -> TransactionRecord:
        if not isinstance(entry, dict):
            raise TypeError("transaction entry is not an object")
        amount = _first_present(entry, _AMOUNT_KEYS)
        resulting = _first_present(entry, _RESULTING_BALANCE_KEYS)
        cp_id = _first_present(entry, _COUNTERPARTY_ID_KEYS)
        cp_name = _first_present(entry, _COUNTERPARTY_NAME_KEYS)
        known = set(_AMOUNT_KEYS) | set(_RESULTING_BALANCE_KEYS) | set(
            _COUNTERPARTY_ID_KEYS
        ) | set(_COUNTERPARTY_NAME_KEYS) | {
            "direction", "kind", "type", "transaction_type", "category",
            "description", "memo", "note", "details", "timestamp",
            "transaction_id", "tx_id", "id", "uuid",
        }
        moment = (
            self._payload_timestamp(entry, fallback)
            if "timestamp" in entry
            else fallback
        )
        return self.record_transaction(
            amount,
            counterparty_id=None if cp_id is None else str(cp_id),
            counterparty_name=None if cp_name is None else str(cp_name),
            direction=entry.get("direction"),
            kind=(
                entry.get("kind")
                or entry.get("type")
                or entry.get("transaction_type")
                or entry.get("category")
            ),
            description=(
                entry.get("description")
                or entry.get("memo")
                or entry.get("note")
                or entry.get("details")
            ),
            resulting_balance=resulting,
            transaction_id=(
                entry.get("transaction_id")
                or entry.get("tx_id")
                or entry.get("id")
                or entry.get("uuid")
            ),
            payload={k: v for k, v in entry.items() if k not in known},
            now=moment,
        )

    def _ingest_transaction(self, event: SeamEvent) -> Any:
        payload = event.payload
        fallback = self._now(None)
        for key in ("transactions", "records", "entries"):
            entries = payload.get(key)
            if isinstance(entries, list):
                filed = []
                for entry in entries:
                    try:
                        filed.append(
                            self._transaction_from_entry(entry, fallback)
                        )
                    except (ValueError, TypeError) as exc:
                        self.rejected.append((str(exc), event.name))
                return filed
        nested = payload.get("transaction")
        if isinstance(nested, dict):
            merged = dict(nested)
            merged.update(
                {k: v for k, v in payload.items() if k != "transaction"}
            )
            return self._transaction_from_entry(merged, fallback)
        return self._transaction_from_entry(payload, fallback)

    # -- purge ---------------------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident's transaction records; count removals.

        Transactions whose counterparty is that resident are
        removed. The balance itself is NOT touched: it is her
        own self-state — how much L$ she holds — not data about
        the resident, so purging a resident must never erase or
        alter it (the same reasoning places.py applies to her
        own movement history)."""
        if not resident_id:
            return 0
        key = str(resident_id)
        kept = [t for t in self._transactions if t.counterparty_id != key]
        removed = len(self._transactions) - len(kept)
        self._transactions = kept
        return removed


__all__ = [
    "MONEY_EVENTS",
    "MoneyState",
    "MoneyStore",
    "TransactionRecord",
]
