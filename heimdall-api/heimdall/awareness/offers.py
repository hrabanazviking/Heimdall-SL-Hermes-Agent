"""Pending-offers queue — design doc 03 §6, census doc 15 slice F1.

Incoming social offers are *decision events*, not noise (doc 03 §6):
teleport lures, friendship offers, group invitations, inventory
offers, calling-card offers, and script dialogs / permission requests
(objects ask for permissions — animate, attach, debit, control — and
present menus the client must answer). Each carries a sender, a
payload, and expiry semantics, and the world model holds them as
``pending_offers[]`` until resolved. Without this queue Yrsa can be
present in-world but not *reachable* — the census (doc 15) named it
the single biggest gap in the seam.

Doc-05 bounds, enforced here in the body, not by convention:

- **Hold-and-show, never silent auto-accept.** Nothing in this module
  resolves an offer on its own. Who may auto-accept what in ambient
  mode is Volmarr's open decision; until he rules, every offer waits
  for an explicit resolution by the inhabitant or the operator.
- **Debit is money.** Accepting a script-permission request that asks
  for the *debit* permission is a payment-class act: it is refused
  without an explicit operator-approval marker, mirroring the
  governor's payments default-deny. Declining is always allowed.
- **Expiry is loud, never silent.** An offer that outlives its hold
  TTL moves to the audit history as ``expired``; it never just
  disappears, and it can no longer be resolved.
- **Unknown kinds fail closed.** An offer whose kind this seam does
  not understand is rejected and recorded in ``rejected`` — never
  queued under a guessed meaning.

Scope honesty: this is the API/seam side of F1. The C# Core does not
yet emit offer events or answer ``offer_respond`` commands — that is
the named Core leg (a purplelongship slice, like Phase 2 slice 2).
The event names added to the seam vocabulary in ``core/models.py``
are the contract that leg will speak; until it lands, offers can be
recorded, held, expired, and resolved entirely on this side, and the
resolution command is ready to route through the governor-gated
``CommandRouter`` the moment Core can confirm it.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.models import Command, SeamEvent

# The offer kinds doc 03 §6 names. Anything else fails closed.
OFFER_KINDS = frozenset(
    {
        "teleport_lure",
        "friendship",
        "group_invite",
        "inventory",
        "calling_card",
        "script_dialog",
        "script_permission",
    }
)

# Canonical seam event name -> offer kind. ``offer_received`` is the
# generic form: its payload carries the kind. The rest are the
# per-kind forms a Core leg would emit directly.
EVENT_KINDS: dict[str, str] = {
    "teleport_lure": "teleport_lure",
    "friendship_offer": "friendship",
    "group_invite": "group_invite",
    "inventory_offer": "inventory",
    "calling_card_offer": "calling_card",
    "script_dialog": "script_dialog",
    "script_permission_request": "script_permission",
}

# Seam-side hold TTLs, in seconds. These are *this queue's* hold
# policy — how long the body keeps an offer open for a decision —
# not grid facts; per-kind and per-offer overrides are supported.
DEFAULT_TTL_S = 900.0
DEFAULT_KIND_TTLS: dict[str, float] = {
    "teleport_lure": 300.0,
    "script_dialog": 300.0,
    "script_permission": 300.0,
    "friendship": 86_400.0,
}

DECISIONS = frozenset({"accept", "decline", "answer"})

# Script permissions that touch money (doc 03 §6: animate, attach,
# debit, control). Only debit is payment-class under doc 05.
MONEY_PERMISSIONS = frozenset({"debit"})


@dataclass
class PendingOffer:
    """One held offer: sender, payload, expiry — until resolved."""

    id: str
    kind: str
    sender_id: str
    sender_name: str | None
    payload: dict[str, Any]
    received_at: float
    expires_at: float
    status: str = "pending"  # pending | accepted | declined | answered | expired
    resolved_at: float | None = None
    resolution_answer: Any = None


@dataclass
class OffersQueue:
    """The ``pending_offers[]`` store: hold, expire, resolve, audit."""

    default_ttl_s: float = DEFAULT_TTL_S
    kind_ttls: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_KIND_TTLS)
    )
    _clock: Callable[[], float] = field(default=time.monotonic)
    _pending: dict[str, PendingOffer] = field(default_factory=dict, init=False)
    _history: list[PendingOffer] = field(default_factory=list, init=False)
    _counter: int = field(default=0, init=False)
    # Fail-closed audit: (reason, detail) for every refused ingest.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    # -- time ---------------------------------------------------------
    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    # -- recording ------------------------------------------------------
    def record(
        self,
        kind: str,
        *,
        sender_id: str,
        sender_name: str | None = None,
        payload: dict[str, Any] | None = None,
        now: float | None = None,
        ttl_s: float | None = None,
    ) -> PendingOffer:
        """Hold one offer. Raises ValueError on an unknown kind or a
        missing sender — callers who cannot raise use ingest_event."""
        if kind not in OFFER_KINDS:
            raise ValueError(f"unknown offer kind: {kind!r}")
        if not sender_id:
            raise ValueError("an offer without a sender cannot be held or answered")
        at = self._now(now)
        body = dict(payload or {})
        hold = ttl_s
        if hold is None:
            expires_in = body.get("expires_in_s")
            if isinstance(expires_in, (int, float)) and expires_in > 0:
                hold = float(expires_in)
        if hold is None:
            hold = self.kind_ttls.get(kind, self.default_ttl_s)
        self._counter += 1
        offer = PendingOffer(
            id=f"offer-{self._counter}",
            kind=kind,
            sender_id=str(sender_id),
            sender_name=None if sender_name is None else str(sender_name),
            payload=body,
            received_at=at,
            expires_at=at + float(hold),
        )
        self._pending[offer.id] = offer
        return offer

    def ingest_event(self, event: SeamEvent) -> PendingOffer | None:
        """Dispatcher handler: hold the offer a seam event carries.

        Never raises — a malformed or unknown offer is recorded in
        ``rejected`` and returns None, so one bad event cannot stop
        the perception stream (the dispatcher's own isolation rule).
        """
        try:
            if event.name == "offer_received":
                kind = event.payload.get("kind") or event.payload.get("offer_kind")
            else:
                kind = EVENT_KINDS.get(event.name)
            if kind is None:
                self.rejected.append(("not_an_offer_event", event.name))
                return None
            payload = {
                k: v
                for k, v in event.payload.items()
                if k not in ("kind", "offer_kind", "sender_id", "sender_name")
            }
            return self.record(
                str(kind),
                sender_id=str(event.payload.get("sender_id") or ""),
                sender_name=event.payload.get("sender_name"),
                payload=payload,
            )
        except ValueError as exc:
            self.rejected.append((str(exc), event.name))
            return None

    # -- reading ---------------------------------------------------------
    def expire(self, *, now: float | None = None) -> list[PendingOffer]:
        """Move out-of-hold offers to history as ``expired`` (loudly)."""
        at = self._now(now)
        stale = [o for o in self._pending.values() if at > o.expires_at]
        for offer in stale:
            offer.status = "expired"
            offer.resolved_at = at
            del self._pending[offer.id]
            self._history.append(offer)
        return stale

    def pending(self, *, now: float | None = None) -> list[PendingOffer]:
        """Live held offers, oldest first (expiry pruned on read)."""
        self.expire(now=now)
        return sorted(self._pending.values(), key=lambda o: o.received_at)

    def get(self, offer_id: str, *, now: float | None = None) -> PendingOffer | None:
        self.expire(now=now)
        return self._pending.get(offer_id)

    def history(self) -> list[PendingOffer]:
        """Resolved and expired offers, in the order they closed."""
        return list(self._history)

    # -- resolving ---------------------------------------------------------
    def _check_decision(self, offer: PendingOffer, decision: str, answer: Any,
                          operator_approved: bool) -> None:
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision: {decision!r}")
        if offer.kind == "script_dialog":
            if decision != "answer":
                raise ValueError("a script dialog is resolved by answering it")
            if answer is None:
                raise ValueError("answering a script dialog requires an answer")
        elif decision == "answer":
            raise ValueError(f"a {offer.kind} offer is accepted or declined, not answered")
        if (
            decision == "accept"
            and offer.kind == "script_permission"
            and not operator_approved
        ):
            asked = offer.payload.get("permissions") or []
            if isinstance(asked, str):
                asked = [asked]
            if MONEY_PERMISSIONS.intersection(str(p).lower() for p in asked):
                raise PermissionError(
                    "accepting a debit permission requires operator approval"
                )

    def build_resolution_command(
        self,
        offer_id: str,
        decision: str,
        *,
        answer: Any = None,
        operator_approved: bool = False,
        now: float | None = None,
    ) -> Command:
        """Build (do not send, do not resolve) the ``offer_respond``
        command for the governor-gated router.

        Mirroring the governor's own pattern, the offer is only
        marked resolved once Core confirms the response was sent —
        the caller routes this command, then calls :meth:`resolve`
        on a confirmed reply. Decisions are the inhabitant's own
        words-class acts, so the command is tier="mind": a reflex
        tier may never commit her to anything (doc 05).
        """
        offer = self.get(offer_id, now=now)
        if offer is None:
            raise KeyError(f"no pending offer with id {offer_id!r}")
        self._check_decision(offer, decision, answer, operator_approved)
        params: dict[str, Any] = {
            "offer_id": offer.id,
            "kind": offer.kind,
            "decision": decision,
            "sender_id": offer.sender_id,
        }
        if answer is not None:
            params["answer"] = answer
        if operator_approved:
            params["operator_approved"] = True
        # Carry back whatever the Core leg needs to find the offer
        # again in-world (dialog channel, inventory item id, ...).
        for key in ("channel", "item_id", "object_id", "session_id"):
            if key in offer.payload:
                params[key] = offer.payload[key]
        return Command(
            id=f"{offer.id}-{decision}",
            cmd="offer_respond",
            params=params,
            tier="mind",
        )

    def resolve(
        self,
        offer_id: str,
        decision: str,
        *,
        answer: Any = None,
        operator_approved: bool = False,
        now: float | None = None,
    ) -> PendingOffer:
        """Mark a held offer resolved after its response was sent.

        Raises KeyError for an unknown/expired offer and ValueError
        for a decision the offer kind does not take — resolving the
        same offer twice therefore fails loudly instead of silently
        double-answering in-world.
        """
        offer = self.get(offer_id, now=now)
        if offer is None:
            raise KeyError(f"no pending offer with id {offer_id!r}")
        self._check_decision(offer, decision, answer, operator_approved)
        offer.status = {
            "accept": "accepted",
            "decline": "declined",
            "answer": "answered",
        }[decision]
        offer.resolution_answer = answer
        offer.resolved_at = self._now(now)
        del self._pending[offer.id]
        self._history.append(offer)
        return offer
