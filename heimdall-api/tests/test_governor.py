"""Doc-05 invariants in the outbound governor."""
from datetime import datetime, timedelta, timezone

from heimdall.core.governor import DAILY_MESSAGE_CAP, MessageGovernor
from heimdall.core.models import Command

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def chat(tier="mind"):
    return Command(id="1", cmd="chat", params={"msg": "hi", "channel": 0}, tier=tier)


def test_mind_chat_allowed_and_counted_only_when_recorded():
    gov = MessageGovernor()
    assert gov.check(chat(), now=T0).allowed
    assert gov.count_today(T0) == 0  # checking does not burn budget
    gov.record_sent(chat(), now=T0)
    assert gov.count_today(T0) == 1


def test_daily_cap_is_the_scripted_agent_cap_and_blocks_beyond_it():
    assert DAILY_MESSAGE_CAP == 5000
    gov = MessageGovernor(cap=3)
    for _ in range(3):
        assert gov.check(chat(), now=T0).allowed
        gov.record_sent(chat(), now=T0)
    decision = gov.check(chat(), now=T0)
    assert not decision.allowed and decision.reason == "daily_message_cap_reached"


def test_cap_resets_on_utc_day_boundary():
    gov = MessageGovernor(cap=1)
    gov.record_sent(chat(), now=T0)
    assert not gov.check(chat(), now=T0).allowed
    assert gov.check(chat(), now=T0 + timedelta(days=1)).allowed


def test_kill_switch_blocks_everything_until_released():
    gov = MessageGovernor()
    gov.engage_kill_switch()
    assert not gov.check(chat(), now=T0).allowed
    move = Command(id="2", cmd="move", params={"x": 1})
    assert not gov.check(move, now=T0).allowed  # even non-speech stops
    gov.release_kill_switch()
    assert gov.check(chat(), now=T0).allowed


def test_reflex_tier_may_not_speak_but_may_move():
    gov = MessageGovernor()
    decision = gov.check(chat(tier="reflex"), now=T0)
    assert not decision.allowed and decision.reason == "reflex_tier_may_not_speak"
    move = Command(id="3", cmd="move", params={"x": 1}, tier="reflex")
    assert gov.check(move, now=T0).allowed


def test_payments_default_deny_without_operator_approval():
    gov = MessageGovernor()
    pay = Command(id="4", cmd="pay", params={"amount": 10})
    decision = gov.check(pay, now=T0)
    assert not decision.allowed and decision.reason == "payment_requires_operator_approval"
    approved = Command(id="5", cmd="pay", params={"amount": 10, "operator_approved": True})
    assert gov.check(approved, now=T0).allowed
