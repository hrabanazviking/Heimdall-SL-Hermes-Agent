"""Single-process deployment runtime — assembly, fail-closed startup.

Fake transport + fake sink prove the wiring slice 4 assembles:
events flow dispatcher → sink, and commands route governor-first.
No real IPC socket, no secret from anywhere but the test's own env.
"""
import pytest

from heimdall.api.auth import AuthConfigError, HmacAuthenticator
from heimdall.core.governor import MessageGovernor
from heimdall.runtime import HeimdallRuntime, RuntimeConfigError, load_sink


class FakeTransport:
    """Fake Core IPC: command transport with a recorded call log."""

    def __init__(self):
        self.received = []

    def send_command(self, command):
        self.received.append(command)
        return {"status": "ok", "result": {"sent": True}}


class FakeSink:
    """Fake WYRD sink on the BifrostBridge surface."""

    def __init__(self):
        self.events = []

    def push_event(self, event_type, payload):
        self.events.append((event_type, payload))
        return True


def _build(**overrides):
    defaults = {
        "transport": FakeTransport(),
        "sink": FakeSink(),
        "authenticator": HmacAuthenticator(b"test-secret"),
        "port": 0,
    }
    defaults.update(overrides)
    return HeimdallRuntime.build(**defaults)


def test_assembly_events_flow_dispatcher_to_sink_and_commands_route():
    runtime = _build()
    runtime.dispatch_core_event(
        {"event": "ChatReceived", "from": "Mara", "msg": "the bridge is closed"}
    )
    sink = runtime.sink
    assert sink.events, "event never reached the sink"
    assert sink.events[0][0] == "ChatReceived"
    assert sink.events[0][1]["canonical_event"] == "chat"
    assert sink.events[0][1]["msg"] == "the bridge is closed"


def test_commands_route_governor_first_reflex_speech_never_reaches_core():
    runtime = _build()
    reply = runtime.router.route_wire(
        {"id": "1", "cmd": "chat", "msg": "boo", "tier": "reflex"}
    )
    assert reply["status"] == "error"
    assert reply["blocked_reason"] == "reflex_tier_may_not_speak"
    assert runtime.transport.received == []


def test_commands_route_and_count_only_confirmed_sends():
    runtime = _build(governor=MessageGovernor(cap=1))
    reply = runtime.router.route_wire(
        {"id": "1", "cmd": "chat", "msg": "hi", "channel": 0}
    )
    assert reply["status"] == "ok"
    assert len(runtime.transport.received) == 1
    assert runtime.governor.count_today() == 1
    blocked = runtime.router.route_wire(
        {"id": "2", "cmd": "chat", "msg": "again", "channel": 0}
    )
    assert blocked["blocked_reason"] == "daily_message_cap_reached"
    assert len(runtime.transport.received) == 1


def test_missing_secret_refuses_to_build():
    with pytest.raises(AuthConfigError):
        HeimdallRuntime.build(
            transport=FakeTransport(),
            sink=FakeSink(),
            environ={},  # no HEIMDALL_WS_SECRET anywhere
            port=0,
        )


def test_sinkless_without_explicit_opt_in_refuses_to_build():
    with pytest.raises(RuntimeConfigError):
        HeimdallRuntime.build(
            transport=FakeTransport(),
            authenticator=HmacAuthenticator(b"test-secret"),
            environ={"HEIMDALL_WS_SECRET": "test-secret"},
            port=0,
        )


def test_sinkless_with_explicit_opt_in_builds_for_testing():
    runtime = HeimdallRuntime.build(
        transport=FakeTransport(),
        authenticator=HmacAuthenticator(b"test-secret"),
        allow_sinkless=True,
        port=0,
    )
    assert runtime.sink is None
    assert runtime.dispatch_core_event({"event": "chat", "msg": "hi"}) is not None


def test_sink_import_failure_fails_closed_not_sinkless():
    with pytest.raises(RuntimeConfigError):
        HeimdallRuntime.build(
            transport=FakeTransport(),
            sink_spec="definitely.not.a.module:Nope",
            authenticator=HmacAuthenticator(b"test-secret"),
            port=0,
        )
    with pytest.raises(RuntimeConfigError):
        load_sink("not-a-valid-spec")


def test_sink_factory_returning_sinkless_object_fails_closed():
    class NoPush:
        pass

    with pytest.raises(RuntimeConfigError):
        HeimdallRuntime.build(
            transport=FakeTransport(),
            sink_factory=lambda: NoPush(),
            authenticator=HmacAuthenticator(b"test-secret"),
            port=0,
        )


def test_start_and_stop_bind_loopback_server():
    runtime = _build()
    with runtime:
        assert runtime.started
        assert runtime.ws_server.port != 0
        assert runtime.ws_server.authenticated_client_count == 0
    assert not runtime.started


def test_event_pump_feeds_dispatcher_from_transport_events():
    transport = FakeTransport()

    # A transport that yields real SeamEvents via a tiny generator.
    from heimdall.core.models import SeamEvent

    def events():
        yield SeamEvent(
            name="chat", raw_name="ChatReceived",
            payload={"from": "Erik", "msg": "halló"},
        )

    transport.events = events
    runtime = _build(transport=transport)
    thread = runtime.start_event_pump()
    thread.join(timeout=5)
    assert runtime.event_pump_error is None
    assert runtime.sink.events[0][1]["msg"] == "halló"


def test_unknown_event_dead_letters_and_reaches_no_sink():
    runtime = _build()
    result = runtime.dispatch_core_event({"event": "SomethingInvented", "msg": "boo"})
    assert result is None
    assert runtime.sink.events == []
    assert runtime.dispatcher.dead_letters == [
        {"event": "SomethingInvented", "msg": "boo"}
    ]
