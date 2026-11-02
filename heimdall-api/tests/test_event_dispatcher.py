"""Dispatcher: normalisation, fail-closed unknowns, sink forwarding."""
from heimdall.api.event_dispatcher import EventDispatcher


class RecordingSink:
    """Matches the BifrostBridge surface: push_event(type, payload)."""

    def __init__(self):
        self.events = []

    def push_event(self, event_type, payload):
        self.events.append((event_type, payload))
        return True


def test_core_chat_event_normalised_handled_and_forwarded():
    sink = RecordingSink()
    dispatcher = EventDispatcher(sink=sink)
    seen = []
    dispatcher.subscribe("chat", seen.append)

    wire = {
        "event": "ChatReceived",
        "from": "Erik Viking",
        "from_uuid": "c3333333-3333-4333-8333-333333333333",
        "msg": "Hello!",
        "channel": 0,
    }
    event = dispatcher.dispatch_wire(wire)

    assert event is not None and event.name == "chat"
    assert seen == [event]
    assert sink.events[0][0] == "ChatReceived"
    assert sink.events[0][1]["canonical_event"] == "chat"
    assert sink.events[0][1]["msg"] == "Hello!"
    assert dispatcher.dead_letters == []


def test_unknown_event_dead_letters_and_reaches_nobody():
    sink = RecordingSink()
    dispatcher = EventDispatcher(sink=sink)
    seen = []
    dispatcher.subscribe("chat", seen.append)

    result = dispatcher.dispatch_wire({"event": "SomethingInvented", "msg": "boo"})

    assert result is None
    assert seen == [] and sink.events == []
    assert dispatcher.dead_letters == [{"event": "SomethingInvented", "msg": "boo"}]


def test_handler_failure_is_isolated_and_recorded():
    dispatcher = EventDispatcher()

    def bad(_event):
        raise ValueError("boom")

    good = []
    dispatcher.subscribe("im", bad)
    dispatcher.subscribe("im", good.append)

    event = dispatcher.dispatch_wire({"event": "IMReceived", "msg": "hi"})

    assert event is not None
    assert good == [event]
    assert len(dispatcher.handler_errors) == 1


def test_api_form_event_names_converge_on_canonical():
    dispatcher = EventDispatcher()
    seen = []
    dispatcher.subscribe("chat", seen.append)
    dispatcher.dispatch_wire({"event": "chat", "msg": "api form"})
    assert len(seen) == 1 and seen[0].raw_name == "chat"
