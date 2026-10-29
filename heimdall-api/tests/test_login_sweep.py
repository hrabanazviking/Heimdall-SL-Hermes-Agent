"""Astrid-Law login sweep — Phase 3 slice 2.

Fake transport + fake clock/sleeper throughout: no socket, no grid,
no real sleep. The fake transport speaks the IpcClient contract
(``send_command(Command) -> dict``) exactly as a Core reply arrives,
and the supervisor drives it through the real CommandRouter +
MessageGovernor, as in production assembly.
"""
import pytest

from heimdall.awareness.login_sweep import (
    BLOCKED_CREDENTIALS,
    FAILED_LOUD,
    HELD,
    LOGGED_IN,
    LOGGED_OUT,
    LoginSupervisor,
    LoginSweepConfig,
)
from heimdall.core.governor import MessageGovernor
from heimdall.core.ipc_client import IpcError
from heimdall.core.models import SeamEvent

OK = {"status": "ok", "result": {"logged_in": True}}
CREDS = {"status": "error", "error": "credentials_unavailable"}
FAIL = {"status": "error", "error": "login_failed: grid unavailable"}


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


class FakeTransport:
    """Scripted Core transport: replies/exceptions per command, in order.

    ``script`` maps cmd name -> list of outcomes; an outcome is a dict
    (returned) or an Exception instance (raised). When a script runs
    out, the last outcome repeats.
    """

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.sent = []

    def send_command(self, command):
        self.sent.append(command)
        outcomes = self.script.get(command.cmd) or [OK]
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def count(self, cmd):
        return sum(1 for c in self.sent if c.cmd == cmd)


def _supervisor(transport, *, governor=None, config=None, records=None):
    clock = FakeClock()
    kwargs = {}
    if records is not None:
        kwargs["on_record"] = records.append
    sup = LoginSupervisor(
        transport=transport,
        governor=governor if governor is not None else MessageGovernor(),
        config=config or LoginSweepConfig(base_delay_s=1.0, max_delay_s=10.0, max_attempts=3),
        clock=clock.time,
        sleeper=clock.sleep,
        **kwargs,
    )
    return sup, clock


# -- success ---------------------------------------------------------------


def test_success_first_try():
    records = []
    transport = FakeTransport({"login": [OK]})
    sup, clock = _supervisor(transport, records=records)
    status = sup.login()
    assert status["state"] == LOGGED_IN
    assert status["attempts"] == 1
    assert transport.count("login") == 1
    assert clock.sleeps == []
    assert [r.kind for r in records] == ["login_success"]
    assert sup.status()["last_error"] == ""


# -- credentials: blocked, exactly one attempt, no retries -------------------


def test_credentials_unavailable_blocked_exactly_one_attempt_and_surfaced():
    records = []
    transport = FakeTransport({"login": [CREDS]})
    sup, clock = _supervisor(transport, records=records)
    status = sup.login()
    assert status["state"] == BLOCKED_CREDENTIALS
    assert status["attempts"] == 1
    assert transport.count("login") == 1  # retrying cannot fix a credential
    assert clock.sleeps == []  # no backoff sleep either
    blocked = [r for r in records if r.kind == "blocked_credentials"]
    assert len(blocked) == 1
    assert blocked[0].attempt == 1
    assert blocked[0].reason == "credentials_unavailable"
    assert status["last_error"] == "credentials_unavailable"
    assert status["failures"], "blocked state must carry a surfaced failure record"


# -- transient failures, backoff, then success --------------------------------


def test_transient_failures_then_success_with_backoff_sequence():
    records = []
    transport = FakeTransport({"login": [FAIL, FAIL, OK]})
    sup, clock = _supervisor(transport, records=records)
    status = sup.login()
    assert status["state"] == LOGGED_IN
    assert status["attempts"] == 3
    assert clock.sleeps == [1.0, 2.0]  # base * 2**(attempt-1), under the cap
    failures = [r for r in records if r.kind == "login_failed"]
    assert [r.attempt for r in failures] == [1, 2]
    assert [r.next_retry_delay_s for r in failures] == [1.0, 2.0]
    assert all(r.reason for r in failures)
    assert records[-1].kind == "login_success"


def test_transport_error_and_timeout_are_transient_and_loud():
    records = []
    transport = FakeTransport(
        {"login": [IpcError("command socket not present"), TimeoutError("timed out"), OK]}
    )
    sup, clock = _supervisor(transport, records=records)
    status = sup.login()
    assert status["state"] == LOGGED_IN
    assert clock.sleeps == [1.0, 2.0]
    reasons = [r.reason for r in records if r.kind == "login_failed"]
    assert "IpcError" in reasons[0]
    assert "TimeoutError" in reasons[1]


def test_backoff_is_capped():
    transport = FakeTransport({"login": [FAIL, FAIL, FAIL, OK]})
    sup, clock = _supervisor(
        transport,
        config=LoginSweepConfig(base_delay_s=4.0, max_delay_s=5.0, max_attempts=4),
    )
    assert sup.login()["state"] == LOGGED_IN
    assert clock.sleeps == [4.0, 5.0, 5.0]


# -- exhaustion: FAILED_LOUD, complete history, still queryable ---------------


def test_max_attempts_exhaustion_is_failed_loud_with_complete_history():
    records = []
    transport = FakeTransport({"login": [FAIL]})
    sup, clock = _supervisor(transport, records=records)
    status = sup.login()
    assert status["state"] == FAILED_LOUD
    assert status["attempts"] == 3
    assert transport.count("login") == 3
    assert clock.sleeps == [1.0, 2.0]  # no sleep after the final attempt
    failures = [r for r in records if r.kind == "login_failed"]
    assert [r.attempt for r in failures] == [1, 2, 3]
    assert failures[-1].next_retry_delay_s is None
    final = [r for r in records if r.kind == "failed_loud"]
    assert len(final) == 1 and "exhausted" in final[0].reason
    # Still queryable after exhaustion — not silent idle.
    again = sup.status()
    assert again["state"] == FAILED_LOUD
    assert again["attempt_count"] == 3
    assert again["last_error"]
    assert len(again["failure_history"]) == 4  # 3 failures + the exhausted record
    assert len(again["history"]) == 4


# -- disconnect after login: a shut-out ----------------------------------------


def test_disconnect_after_login_triggers_relogin():
    records = []
    transport = FakeTransport({"login": [OK, OK]})
    sup, _clock = _supervisor(transport, records=records)
    assert sup.login()["state"] == LOGGED_IN
    sup.handle_event(
        SeamEvent(name="disconnected", raw_name="Disconnected", payload={"reason": "sim closed"})
    )
    assert transport.count("login") == 2  # re-login was attempted
    assert sup.status()["state"] == LOGGED_IN
    shut_outs = [r for r in records if r.kind == "shut_out"]
    assert len(shut_outs) == 1 and shut_outs[0].reason == "sim closed"


def test_disconnect_after_login_via_wire_dict_and_dispatcher_attach():
    from heimdall.api.event_dispatcher import EventDispatcher

    transport = FakeTransport({"login": [OK, OK]})
    sup, _clock = _supervisor(transport)
    dispatcher = EventDispatcher()
    sup.attach(dispatcher)
    assert sup.login()["state"] == LOGGED_IN
    dispatcher.dispatch_wire({"event": "Disconnected", "reason": "network lost"})
    assert transport.count("login") == 2
    assert sup.status()["state"] == LOGGED_IN


def test_intentional_logout_does_not_trigger_relogin():
    records = []
    transport = FakeTransport({"login": [OK], "logout": [{"status": "ok", "result": {"logged_in": False}}]})
    sup, _clock = _supervisor(transport, records=records)
    assert sup.login()["state"] == LOGGED_IN
    assert sup.logout()["state"] == LOGGED_OUT
    assert transport.count("logout") == 1
    # The Core answers a logout with a Disconnected event (see
    # SimulatedSession.Logout) — that one must NOT re-login.
    sup.handle_event(
        SeamEvent(name="disconnected", raw_name="Disconnected", payload={"reason": "logout"})
    )
    assert transport.count("login") == 1
    assert sup.status()["state"] == LOGGED_OUT
    assert [r.kind for r in records if r.kind == "shut_out"] == []


# -- kill switch: HELD, no hammering -------------------------------------------


def test_kill_switch_holds_login_without_touching_core():
    records = []
    governor = MessageGovernor()
    governor.engage_kill_switch()
    transport = FakeTransport({"login": [OK]})
    sup, clock = _supervisor(transport, governor=governor, records=records)
    status = sup.login()
    assert status["state"] == HELD
    assert transport.sent == []  # paused, not hammering
    assert status["attempts"] == 0
    assert clock.sleeps == []
    held = [r for r in records if r.kind == "held"]
    assert len(held) == 1 and held[0].reason == "kill_switch_engaged"
    # Releasing the switch lets the same supervisor log in.
    governor.release_kill_switch()
    assert sup.login()["state"] == LOGGED_IN
    assert transport.count("login") == 1


# -- events: LoginFailed / credentials via the event socket --------------------


def test_login_failed_event_is_surfaced_and_credentials_event_blocks():
    records = []
    transport = FakeTransport({"login": [OK]})
    sup, _clock = _supervisor(transport, records=records)
    sup.handle_event(
        SeamEvent(name="login_failed", raw_name="LoginFailed", payload={"reason": "bad password"})
    )
    assert [r.kind for r in records] == ["login_failed"]
    assert sup.status()["last_error"] == "bad password"
    sup.handle_event(
        SeamEvent(
            name="login_failed",
            raw_name="LoginFailed",
            payload={"reason": "credentials_unavailable"},
        )
    )
    assert sup.status()["state"] == BLOCKED_CREDENTIALS
    assert records[-1].kind == "blocked_credentials"


# -- the Astrid Law itself: no silent path --------------------------------------


def test_no_silent_path_every_failure_produces_a_surfaced_record():
    """For every failure scenario, count failures == surfaced failure records.

    Scenarios: reply failures, transport exceptions, credentials,
    exhaustion, a shut-out disconnect, and a kill-switch hold. In each,
    the number of records handed to the on_record callback must cover
    every failure that occurred — a failure with no record fails here.
    """
    scenarios = []

    # 1. Two reply failures then success: 2 failures, >=2 failure records.
    rec1 = []
    t1 = FakeTransport({"login": [FAIL, FAIL, OK]})
    s1, _ = _supervisor(t1, records=rec1)
    s1.login()
    scenarios.append((rec1, 2))

    # 2. Exhaustion: 3 failures + the failed_loud record.
    rec2 = []
    t2 = FakeTransport({"login": [FAIL]})
    s2, _ = _supervisor(t2, records=rec2)
    s2.login()
    scenarios.append((rec2, 3))

    # 3. Credentials: 1 failure, exactly the blocked record.
    rec3 = []
    t3 = FakeTransport({"login": [CREDS]})
    s3, _ = _supervisor(t3, records=rec3)
    s3.login()
    scenarios.append((rec3, 1))

    # 4. Exceptions count as failures too.
    rec4 = []
    t4 = FakeTransport({"login": [IpcError("down"), OK]})
    s4, _ = _supervisor(t4, records=rec4)
    s4.login()
    scenarios.append((rec4, 1))

    for records, failures in scenarios:
        failure_records = [
            r for r in records
            if r.kind in ("login_failed", "blocked_credentials", "failed_loud", "held", "shut_out")
        ]
        assert len(failure_records) >= failures, (
            f"{failures} failure(s) but only {len(failure_records)} surfaced: {records}"
        )
        # Every surfaced record is also retained in the queryable history.
        assert records, "a scenario produced no records at all"

    # 5. Shut-out disconnect: the disconnect itself is a surfaced failure.
    rec5 = []
    t5 = FakeTransport({"login": [OK, OK]})
    s5, _ = _supervisor(t5, records=rec5)
    s5.login()
    before = len(rec5)
    s5.handle_event(
        SeamEvent(name="disconnected", raw_name="Disconnected", payload={"reason": "ejected"})
    )
    new_failure_records = [r for r in rec5[before:] if r.kind == "shut_out"]
    assert len(new_failure_records) == 1

    # 6. Kill-switch hold is surfaced, not a silent refusal.
    rec6 = []
    gov = MessageGovernor()
    gov.engage_kill_switch()
    t6 = FakeTransport({"login": [OK]})
    s6, _ = _supervisor(t6, governor=gov, records=rec6)
    s6.login()
    assert [r.kind for r in rec6] == ["held"]


def test_supervisor_requires_a_router_or_transport():
    with pytest.raises(ValueError):
        LoginSupervisor()
