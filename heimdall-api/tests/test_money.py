"""F8 — money as a sense (census doc 15 slice F8, sense-half only).

The load-bearing invariants under test:

- before any balance event the balance is UNKNOWN, never
  invented as L$0; an explicitly-zero balance event is known
  and distinguishable from unknown;
- balances validate strictly: only a non-negative int passes —
  bool, float, negative, string, and missing all fail closed
  into ``rejected`` (ingest) or raise (direct API), and ingest
  never raises;
- transactions are attributed held records only: a signed
  non-zero int amount plus a counterparty are required, sparse
  direction/kind/description stay None, malformed entries fail
  closed, and nothing is promoted to a fact;
- a balance event replaces the balance wholesale; a transaction
  carrying a resulting balance updates it to exactly that
  value; a transaction without one never moves the balance by
  arithmetic;
- ``purge_resident`` removes only that resident's transaction
  records — the balance is her own self-state and survives;
- there is NO spend surface: no pay/give/buy method exists,
  the one build helper is a read-only balance request, and the
  governor's money default-deny is untouched.
"""
import pytest

from heimdall.awareness import MoneyStore
from heimdall.core.governor import MONEY_COMMANDS, MessageGovernor
from heimdall.core.models import CORE_EVENT_ALIASES, SeamEvent, canonical_event_name

ERIK = "c3333333-3333-4333-8333-333333333333"
MARA = "d4444444-4444-4444-8444-444444444444"


def _store():
    return MoneyStore(_clock=lambda: 1000.0)


def _ingest(store, event_name, raw, **payload):
    return store.ingest_event(
        SeamEvent(name=event_name, raw_name=raw, payload=payload)
    )


def _balance(store, value=1500, **extra):
    return _ingest(store, "balance_received", "BalanceReceived",
                   balance=value, **extra)


def _tx(store, **payload):
    base = {"amount": 250, "counterparty_id": ERIK,
            "counterparty_name": "Erik"}
    base.update(payload)
    return _ingest(store, "transaction_received", "TransactionReceived", **base)


# -- vocabulary ---------------------------------------------------------

def test_money_event_names_are_canonical():
    assert canonical_event_name("BalanceReceived") == "balance_received"
    assert canonical_event_name("TransactionReceived") == "transaction_received"
    for api_form in ("balance_received", "transaction_received"):
        assert canonical_event_name(api_form) == api_form


def test_seam_event_from_wire_canonicalises_new_names():
    for raw, canonical in (
        ("BalanceReceived", "balance_received"),
        ("TransactionReceived", "transaction_received"),
    ):
        event = SeamEvent.from_wire({"event": raw, "balance": 10})
        assert event is not None and event.name == canonical


def test_no_duplicate_alias_added_for_money():
    for canonical in ("balance_received", "transaction_received"):
        forms = [k for k, v in CORE_EVENT_ALIASES.items() if v == canonical]
        assert len(forms) == 2  # Core name + API identity form only


# -- unknown vs explicitly zero -------------------------------------------

def test_balance_is_unknown_before_any_event():
    store = _store()
    assert store.balance_known is False
    assert store.balance is None
    assert store.current_balance() is None
    assert store.balance_updated_at is None
    state = store.current_state()
    assert state.known is False and state.balance is None
    assert state.transactions == 0
    text = store.describe()
    assert "unknown" in text.lower()
    assert "L$0" in text  # only inside the "not L$0" honesty clause
    assert "Balance: L$" not in text


def test_explicitly_zero_balance_is_known_and_not_unknown():
    store = _store()
    result = _balance(store, 0)
    assert result == 0
    assert store.balance_known is True
    assert store.balance == 0
    assert store.describe() == "Balance: L$0."


def test_balance_ingest_sets_balance_and_timestamp():
    store = _store()
    _balance(store, 1500, timestamp=900.0)
    assert store.balance == 1500
    assert store.balance_updated_at == 900.0
    assert "L$1500" in store.describe()
    state = store.current_state()
    assert state.known is True and state.balance == 1500


def test_balance_event_replaces_balance_wholesale():
    store = _store()
    _balance(store, 1500)
    _balance(store, 42)
    assert store.balance == 42
    _balance(store, 9000)
    assert store.balance == 9000


# -- balance validation matrix ----------------------------------------------

@pytest.mark.parametrize("bad", [True, False, 10.0, 0.5, -1, -100,
                                 "1500", "lots", None, [1500], {"b": 1}])
def test_invalid_balance_values_fail_closed_via_ingest(bad):
    store = _store()
    assert _balance(store, bad) is None
    assert store.balance_known is False
    assert store.balance is None
    assert len(store.rejected) == 1
    assert store.rejected[0][1] == "balance_received"


def test_balance_event_without_a_balance_fails_closed():
    store = _store()
    assert _ingest(store, "balance_received", "BalanceReceived",
                   note="no balance here") is None
    assert store.balance_known is False
    assert len(store.rejected) == 1


@pytest.mark.parametrize("bad", [True, 10.0, -5, "100"])
def test_record_balance_direct_api_raises_on_invalid(bad):
    store = _store()
    with pytest.raises((ValueError, TypeError)):
        store.record_balance(bad)
    assert store.balance_known is False


def test_unknown_event_fails_closed_and_never_raises():
    store = _store()
    assert _ingest(store, "chat", "ChatReceived", msg="hi") is None
    assert store.rejected[-1] == ("not_a_money_event", "chat")
    assert store.balance_known is False and store.transactions() == []


# -- transaction ingest -------------------------------------------------------

def test_transaction_ingest_holds_exactly_what_arrived():
    store = _store()
    record = _tx(store, direction="in", kind="payment",
                 description="for the carving", timestamp=950.0)
    assert record.amount == 250
    assert record.counterparty_id == ERIK
    assert record.counterparty_name == "Erik"
    assert record.direction == "in" and record.kind == "payment"
    assert record.description == "for the carving"
    assert record.timestamp == 950.0
    assert record.resulting_balance is None


def test_transaction_sparse_fields_stay_absent():
    store = _store()
    record = _tx(store)
    assert record.direction is None and record.kind is None
    assert record.description is None and record.transaction_id is None
    # A name-only counterparty is attribution enough.
    by_name = _ingest(store, "transaction_received", "TransactionReceived",
                      amount=-40, counterparty_name="Mara")
    assert by_name.counterparty_id is None
    assert by_name.counterparty_name == "Mara"
    assert by_name.amount == -40  # signed: negative is a real amount


def test_transaction_re_read_and_recent_ordering():
    store = _store()
    for amount in (10, 20, 30, 40):
        _tx(store, amount=amount)
    assert [t.amount for t in store.transactions()] == [10, 20, 30, 40]
    assert [t.amount for t in store.transaction_history()] == [10, 20, 30, 40]
    assert [t.amount for t in store.recent_transactions(2)] == [30, 40]
    assert [t.amount for t in store.recent_transactions()] == [10, 20, 30, 40]
    assert store.recent_transactions(0) == []
    assert store.current_state().transactions == 4


def test_transaction_list_ingest_files_each_and_audits_bad_ones():
    store = _store()
    filed = _ingest(store, "transaction_received", "TransactionReceived",
                    transactions=[
                        {"amount": 100, "counterparty_id": ERIK},
                        {"amount": 0, "counterparty_id": ERIK},
                        {"counterparty_id": MARA},
                        {"amount": -25, "counterparty_id": MARA},
                        "not-a-dict",
                    ])
    assert [t.amount for t in filed] == [100, -25]
    assert len(store.rejected) == 3


@pytest.mark.parametrize("payload", [
    {"counterparty_id": ERIK},                                  # no amount
    {"amount": None, "counterparty_id": ERIK},                   # null amount
    {"amount": 0, "counterparty_id": ERIK},                      # zero amount
    {"amount": True, "counterparty_id": ERIK},                   # bool amount
    {"amount": 12.5, "counterparty_id": ERIK},                   # float amount
    {"amount": "250", "counterparty_id": ERIK},                  # string amount
    {"amount": 250},                                             # no counterparty
    {"amount": 250, "counterparty_id": "", "counterparty_name": "  "},
])
def test_malformed_transactions_fail_closed(payload):
    store = _store()
    assert _ingest(store, "transaction_received",
                   "TransactionReceived", **payload) is None
    assert store.transactions() == []
    assert len(store.rejected) == 1
    with pytest.raises((ValueError, TypeError)):
        store.record_transaction(payload.get("amount"),
                                 counterparty_id=payload.get("counterparty_id"),
                                 counterparty_name=payload.get("counterparty_name"))


# -- balance <-> transaction interplay -----------------------------------------

def test_transaction_resulting_balance_updates_balance():
    store = _store()
    _balance(store, 1000)
    _tx(store, amount=250, resulting_balance=1250)
    assert store.balance == 1250
    # The Core-style "balance" key on a transaction is its
    # resulting balance too.
    _tx(store, amount=-50, balance=1200)
    assert store.balance == 1200
    assert store.balance_known is True


def test_transaction_resulting_balance_can_establish_a_known_balance():
    store = _store()
    assert store.balance_known is False
    _tx(store, amount=75, resulting_balance=75)
    assert store.balance_known is True and store.balance == 75


def test_transaction_without_resulting_balance_never_moves_balance():
    store = _store()
    _balance(store, 1000)
    _tx(store, amount=250)
    _tx(store, amount=-999)
    # No arithmetic is guessed: the balance is exactly what the
    # last balance-carrying event said.
    assert store.balance == 1000


def test_transaction_with_invalid_resulting_balance_fails_closed():
    store = _store()
    _balance(store, 1000)
    assert _tx(store, amount=250, resulting_balance=-1) is None
    assert _tx(store, amount=250, resulting_balance=1250.0) is None
    assert store.balance == 1000
    assert store.transactions() == []
    assert len(store.rejected) == 2


# -- purge -----------------------------------------------------------------------

def test_purge_resident_removes_only_their_transactions_and_keeps_balance():
    store = _store()
    _balance(store, 700)
    _tx(store, amount=100)  # Erik
    _tx(store, amount=200, counterparty_id=MARA, counterparty_name="Mara")
    _tx(store, amount=300)  # Erik again
    removed = store.purge_resident(ERIK)
    assert removed == 2
    assert [t.amount for t in store.transactions()] == [200]
    # Balance is her own self-state, not resident data: untouched.
    assert store.balance_known is True and store.balance == 700
    assert store.purge_resident(ERIK) == 0
    assert store.purge_resident("") == 0


# -- no spend surface ---------------------------------------------------------------

def test_store_exposes_no_spend_path():
    store = _store()
    for name in ("pay", "give_money", "give_linden", "give", "buy",
                 "buy_object", "pay_object", "spend", "send_money",
                 "build_pay_command", "build_give_command",
                 "build_buy_command", "build_spend_command"):
        assert not hasattr(store, name), name
    # No callable on the store builds a governor money command.
    for attr in dir(store):
        if attr.startswith("build_"):
            cmd = getattr(store, attr)("probe")
            assert cmd.cmd not in MONEY_COMMANDS


def test_balance_request_command_is_read_only_and_records_nothing():
    store = _store()
    cmd = store.build_balance_request_command("c1")
    assert cmd.cmd == "request_balance" and cmd.tier == "mind"
    assert cmd.params == {}
    assert cmd.cmd not in MONEY_COMMANDS
    assert store.balance_known is False and store.transactions() == []


def test_governor_money_default_deny_stands_untouched():
    assert MONEY_COMMANDS == frozenset({"pay", "give_money", "give_linden"})
    governor = MessageGovernor()
    from heimdall.core.models import Command
    for money_cmd in ("pay", "give_money", "give_linden"):
        decision = governor.check(Command(id="x", cmd=money_cmd, params={}))
        assert decision.allowed is False
        assert decision.reason == "payment_requires_operator_approval"
    # The read-only balance request is not a money command.
    ok = governor.check(
        Command(id="x", cmd="request_balance", params={}, tier="mind")
    )
    assert ok.allowed is True


def test_describe_speaks_known_balance_with_transaction_count():
    store = _store()
    _balance(store, 2500)
    _tx(store, amount=250)
    text = store.describe()
    assert "L$2500" in text and "1 transaction" in text
