"""F2 — conversation completeness (census doc 15 slice F2), seam side.

The load-bearing invariants under test:

- a conversation event with no consumer attached is BUFFERED, not
  dropped — a late consumer receives it in arrival order — and
  buffer overflow is loud (counted and kept), never silent;
- the conversation log holds per-thread attributed utterances the
  mind can re-read in order; unattributed text is refused, and a
  per-resident purge removes that resident's entries;
- outbound utterances enter the log ONLY after Core confirmation;
- the say path validates its speaking mode (whisper/say/shout,
  default say) and fails closed on an invalid one before the Core;
- outbound IM and group IM reach the (fake) Core governor-gated,
  end-to-end over the real IPC transport.
"""
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from heimdall.api.command_router import CommandRouter
from heimdall.api.event_dispatcher import EventDispatcher
from heimdall.awareness import ConversationLog
from heimdall.core.governor import MessageGovernor
from heimdall.core.ipc_client import IpcClient
from heimdall.core.models import SeamEvent

ERIK = "c3333333-3333-4333-8333-333333333333"
MARA = "d4444444-4444-4444-8444-444444444444"
GROUP = "e5555555-5555-4555-8555-555555555555"


class RecordingSink:
    def __init__(self):
        self.events = []

    def push_event(self, event_type, payload):
        self.events.append((event_type, payload))
        return True


class StubTransport:
    """In-memory Core: records commands, replies as configured."""

    def __init__(self, reply=None):
        self.received = []
        self.reply = reply or {"status": "ok", "result": {"sent": True}}

    def send_command(self, command):
        self.received.append(command)
        return dict(self.reply)


# -- buffered delivery ----------------------------------------------------

def test_conversation_event_without_consumer_is_buffered_not_dropped():
    dispatcher = EventDispatcher()
    event = dispatcher.dispatch_wire(
        {"event": "IMReceived", "from": "Erik", "from_uuid": ERIK, "msg": "hi"}
    )
    assert event is not None
    assert dispatcher.buffered_count("im") == 1
    assert dispatcher.dead_letters == []
    # A non-conversation event is never buffered.
    dispatcher.dispatch_wire({"event": "RegionChanged", "region_name": "X"})
    assert dispatcher.buffered_count() == 1


def test_late_consumer_receives_buffered_events_in_order():
    dispatcher = EventDispatcher()
    for text in ("one", "two", "three"):
        dispatcher.dispatch_wire(
            {"event": "IMReceived", "from_uuid": ERIK, "msg": text}
        )
    seen = []
    dispatcher.subscribe("im", seen.append)
    assert [e.payload["msg"] for e in seen] == ["one", "two", "three"]
    assert dispatcher.buffered_count("im") == 0
    # After attach, delivery is direct again — nothing double-held.
    dispatcher.dispatch_wire({"event": "IMReceived", "from_uuid": ERIK, "msg": "four"})
    assert [e.payload["msg"] for e in seen] == ["one", "two", "three", "four"]
    assert dispatcher.buffered_count("im") == 0


def test_buffer_overflow_is_loud_counted_and_kept():
    dispatcher = EventDispatcher(conversation_buffer_limit=3)
    for i in range(5):
        dispatcher.dispatch_wire(
            {"event": "ChatReceived", "from": "Mara", "msg": f"m{i}"}
        )
    assert dispatcher.buffered_count("chat") == 3
    assert dispatcher.buffer_overflow_count == 2
    assert [e.payload["msg"] for e in dispatcher.buffer_overflow] == ["m0", "m1"]
    seen = []
    dispatcher.subscribe("chat", seen.append)
    assert [e.payload["msg"] for e in seen] == ["m2", "m3", "m4"]


def test_buffered_event_still_reaches_the_world_sink():
    sink = RecordingSink()
    dispatcher = EventDispatcher(sink=sink)
    dispatcher.dispatch_wire({"event": "IMReceived", "from_uuid": ERIK, "msg": "hi"})
    assert sink.events and sink.events[0][1]["msg"] == "hi"
    assert dispatcher.buffered_count("im") == 1


# -- the per-thread log -----------------------------------------------------

def _log_with_events():
    log = ConversationLog()
    dispatcher = EventDispatcher()
    for name in ("chat", "im", "group_chat"):
        dispatcher.subscribe(name, log.ingest_event)
    return log, dispatcher


def test_threads_keyed_by_kind_and_peer_with_attribution():
    log, dispatcher = _log_with_events()
    dispatcher.dispatch_wire(
        {"event": "ChatReceived", "from": "Mara", "from_uuid": MARA,
         "msg": "the bridge is closed"}
    )
    dispatcher.dispatch_wire(
        {"event": "IMReceived", "from": "Erik Viking", "from_uuid": ERIK,
         "msg": "are you there?", "timestamp": "2026-10-30T12:00:00+00:00"}
    )
    dispatcher.dispatch_wire(
        {"event": "GroupChatReceived", "from": "Erik Viking", "from_uuid": ERIK,
         "group_uuid": GROUP, "group_name": "Village", "msg": "hall tonight"}
    )
    assert set(log.threads()) == {
        ("local_chat", "local"), ("im", ERIK), ("group_chat", GROUP),
    }
    local = log.thread("local_chat", "local")
    assert len(local) == 1
    assert local[0].speaker_id == MARA and local[0].speaker_name == "Mara"
    assert local[0].text == "the bridge is closed"
    assert local[0].direction == "in"
    im = log.thread("im", ERIK)
    assert im[0].speaker_name == "Erik Viking"
    assert im[0].timestamp == pytest.approx(1793361600.0)
    group = log.thread("group_chat", GROUP)
    assert group[0].speaker_id == ERIK and log.peer_name("group_chat", GROUP) == "Village"


def test_thread_reread_is_chronological():
    log, dispatcher = _log_with_events()
    for text in ("first", "second", "third"):
        dispatcher.dispatch_wire(
            {"event": "IMReceived", "from_uuid": ERIK, "msg": text}
        )
    assert [e.text for e in log.thread("im", ERIK)] == ["first", "second", "third"]


def test_unattributed_utterance_is_refused_not_stored():
    log, dispatcher = _log_with_events()
    dispatcher.dispatch_wire({"event": "IMReceived", "msg": "who said this?"})
    dispatcher.dispatch_wire({"event": "RegionChanged", "region_name": "X"})
    assert log.entry_count() == 0
    assert len(log.rejected) == 1  # the region event never reaches the log


def test_group_chat_without_group_id_keys_to_shared_group_peer():
    log = ConversationLog()
    entry = log.ingest_event(
        SeamEvent(name="group_chat", raw_name="GroupChatReceived",
                  payload={"from": "Erik", "from_uuid": ERIK, "msg": "hi all"})
    )
    assert entry is not None
    assert log.thread("group_chat", "group") == [entry]


def test_purge_resident_removes_their_entries_and_threads():
    log, dispatcher = _log_with_events()
    dispatcher.dispatch_wire(
        {"event": "IMReceived", "from_uuid": ERIK, "msg": "secret one"}
    )
    dispatcher.dispatch_wire(
        {"event": "ChatReceived", "from_uuid": ERIK, "from": "Erik", "msg": "lo"}
    )
    dispatcher.dispatch_wire(
        {"event": "ChatReceived", "from_uuid": MARA, "from": "Mara", "msg": "stay"}
    )
    removed = log.purge_resident(ERIK)
    assert removed == 2
    assert log.thread("im", ERIK) == []
    assert [e.text for e in log.thread("local_chat", "local")] == ["stay"]
    assert log.purge_resident(ERIK) == 0


# -- speaking modes -----------------------------------------------------------

def _router(transport=None, governor=None, log=None):
    return CommandRouter(
        transport or StubTransport(),
        governor or MessageGovernor(),
        conversation_log=log,
    )


def test_say_defaults_to_say_mode_and_normal_core_type():
    transport = StubTransport()
    reply = _router(transport).route_wire({"id": "1", "cmd": "say", "msg": "hi"})
    assert reply["status"] == "ok"
    sent = transport.received[0]
    assert sent.params["mode"] == "say" and sent.params["type"] == "normal"


@pytest.mark.parametrize(
    ("mode", "core_type"), [("whisper", "whisper"), ("shout", "shout")]
)
def test_say_modes_pass_through_to_the_core(mode, core_type):
    transport = StubTransport()
    reply = _router(transport).route_wire(
        {"id": "1", "cmd": "chat", "msg": "hi", "mode": mode}
    )
    assert reply["status"] == "ok"
    assert transport.received[0].params["type"] == core_type


def test_invalid_speaking_mode_fails_closed_before_the_core():
    transport = StubTransport()
    governor = MessageGovernor()
    reply = _router(transport, governor).route_wire(
        {"id": "1", "cmd": "say", "msg": "hi", "mode": "yell"}
    )
    assert reply["status"] == "error"
    assert reply["blocked_reason"] == "invalid_speaking_mode"
    assert transport.received == []
    assert governor.count_today() == 0


# -- outbound recording: only after Core confirmation --------------------------

def test_confirmed_sends_are_logged_unconfirmed_are_not():
    log = ConversationLog()
    transport = StubTransport()
    router = _router(transport, log=log)
    router.route_wire({"id": "1", "cmd": "say", "msg": "hello hall"})
    router.route_wire({"id": "2", "cmd": "im", "to_uuid": ERIK, "msg": "psst"})
    router.route_wire(
        {"id": "3", "cmd": "group_im", "group_uuid": GROUP, "msg": "hall!"}
    )
    assert [e.text for e in log.thread("local_chat", "local")] == ["hello hall"]
    im = log.thread("im", ERIK)
    assert [e.text for e in im] == ["psst"] and im[0].direction == "out"
    assert [e.text for e in log.thread("group_chat", GROUP)] == ["hall!"]

    failing = StubTransport(reply={"status": "error", "error": "not_logged_in"})
    router2 = _router(failing, log=log)
    router2.route_wire({"id": "4", "cmd": "im", "to_uuid": ERIK, "msg": "lost"})
    router2.route_wire({"id": "5", "cmd": "im", "to_uuid": ERIK, "msg": "x",
                        "tier": "reflex"})
    assert [e.text for e in log.thread("im", ERIK)] == ["psst"]  # unchanged


# -- outbound IM end-to-end (stub transport) --------------------------------------

def test_im_and_group_im_are_governor_gated_like_other_speech():
    transport = StubTransport()
    governor = MessageGovernor(cap=2)
    router = _router(transport, governor)
    assert router.route_wire(
        {"id": "1", "cmd": "im", "to_uuid": ERIK, "msg": "one"}
    )["status"] == "ok"
    assert router.route_wire(
        {"id": "2", "cmd": "group_im", "group_uuid": GROUP, "msg": "two"}
    )["status"] == "ok"
    capped = router.route_wire({"id": "3", "cmd": "im", "to_uuid": ERIK, "msg": "3"})
    assert capped["blocked_reason"] == "daily_message_cap_reached"
    assert len(transport.received) == 2

    reflex = _router(StubTransport())
    blocked = reflex.route_wire(
        {"id": "4", "cmd": "group_im", "group_uuid": GROUP, "msg": "boo",
         "tier": "reflex"}
    )
    assert blocked["blocked_reason"] == "reflex_tier_may_not_speak"


# -- outbound IM end-to-end over the real IPC transport ----------------------------

class LoopingFakeCore(threading.Thread):
    """§7.2 command server: answers every command ok, records wires."""

    def __init__(self, cmd_path):
        super().__init__(daemon=True)
        self.cmd_path = str(cmd_path)
        self.received = []
        self._stop = threading.Event()

    def run(self):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.cmd_path)
        srv.listen(8)
        srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                if not data:
                    continue
                wire = json.loads(data)
                self.received.append(wire)
                reply = {"id": wire["id"], "status": "ok",
                         "result": {"sent": True}}
                conn.sendall((json.dumps(reply) + "\n").encode())
        srv.close()

    def stop(self):
        self._stop.set()


@pytest.fixture()
def looping_core(tmp_path):
    core = LoopingFakeCore(tmp_path / "heimdall.sock")
    core.start()
    deadline = time.time() + 5
    while time.time() < deadline and not Path(core.cmd_path).exists():
        time.sleep(0.01)
    yield core
    core.stop()


def test_im_group_im_and_shout_reach_the_fake_core_over_ipc(looping_core, tmp_path):
    client = IpcClient(
        command_socket=str(tmp_path / "heimdall.sock"), timeout_s=5.0
    )
    governor = MessageGovernor()
    router = CommandRouter(client, governor)
    assert router.route_wire(
        {"id": "1", "cmd": "im", "to_uuid": ERIK, "msg": "hello Erik"}
    )["status"] == "ok"
    assert router.route_wire(
        {"id": "2", "cmd": "group_im", "group_uuid": GROUP, "msg": "hello hall"}
    )["status"] == "ok"
    assert router.route_wire(
        {"id": "3", "cmd": "say", "msg": "HEAR ME", "mode": "shout"}
    )["status"] == "ok"
    wires = looping_core.received
    assert wires[0]["cmd"] == "im"
    assert wires[0]["to_uuid"] == ERIK and wires[0]["msg"] == "hello Erik"
    assert wires[1]["cmd"] == "group_im" and wires[1]["group_uuid"] == GROUP
    assert wires[2]["cmd"] == "say" and wires[2]["type"] == "shout"
    assert governor.count_today() == 3


# -- runtime wiring: a consumer in every session -----------------------------------

def test_runtime_log_catches_events_with_no_client_connected():
    from heimdall.api.auth import HmacAuthenticator
    from heimdall.runtime import HeimdallRuntime

    class Sink:
        def push_event(self, event_type, payload):
            return True

    runtime = HeimdallRuntime.build(
        transport=StubTransport(),
        sink=Sink(),
        authenticator=HmacAuthenticator(b"test-secret"),
        port=0,
    )
    assert runtime.conversations is not None
    runtime.dispatch_core_event(
        {"event": "IMReceived", "from": "Erik", "from_uuid": ERIK,
         "msg": "the test IM that must not be lost"}
    )
    entries = runtime.conversations.thread("im", ERIK)
    assert [e.text for e in entries] == ["the test IM that must not be lost"]
    runtime.router.route_wire({"id": "1", "cmd": "say", "msg": "I hear you"})
    assert [e.text for e in runtime.conversations.thread("local_chat", "local")] == [
        "I hear you"
    ]
