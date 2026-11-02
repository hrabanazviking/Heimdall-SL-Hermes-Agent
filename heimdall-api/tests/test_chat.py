"""Two-channel text chat (Volmarr's order, 2026-10-31) — the laws:

- local chat, IM and group IM are three separate send paths: an IM
  send produces exactly an ``im`` command with a ``to_uuid``, a
  local send exactly a ``say`` command — never crossed, verified
  against what the (fake) transport actually received;
- an IM/group IM without its target raises before routing — it is
  never rerouted into public local chat;
- every send passes the real governor through the real router:
  kill switch, reflex-tier silence and the daily cap all block,
  and blocked/failed sends never burn message budget;
- the watcher classifies ChatReceived / IMReceived /
  GroupChatReceived into LOCAL_CHAT / IM / GROUP_IM inbox entries,
  drains on the 5-second cadence Volmarr named (CHECK_INTERVAL_S),
  learns known avatars from observed events, fails closed on
  malformed/unknown/unattributed lines, and reconnects with
  backoff while recording the coverage gap honestly.
"""
import json
import socket
import time
from collections import deque

import pytest

from heimdall.api.command_router import CommandRouter
from heimdall.chat import (
    CHECK_INTERVAL_S,
    GROUP_IM,
    IM,
    LOCAL_CHAT,
    ChatService,
    ChatWatcher,
)
from heimdall.core.governor import MessageGovernor

ERIK = "c3333333-3333-4333-8333-333333333333"
MARA = "d4444444-4444-4444-8444-444444444444"
GROUP = "e5555555-5555-4555-8555-555555555555"


# -----------------------------------------------------------------
# Outbound: fakes + fixtures
# -----------------------------------------------------------------
class FakeTransport:
    """Records every Command the router hands to the 'Core'."""

    def __init__(self, *, fail: bool = False):
        self.commands = []
        self.fail = fail

    def send_command(self, command):
        self.commands.append(command)
        if self.fail:
            return {
                "id": command.id,
                "cmd": command.cmd,
                "status": "error",
                "error": "core_refused",
            }
        return {
            "id": command.id,
            "cmd": command.cmd,
            "status": "ok",
            "result": {"sent": True},
        }


def make_service(*, governor=None, transport=None):
    transport = transport if transport is not None else FakeTransport()
    governor = governor if governor is not None else MessageGovernor()
    router = CommandRouter(transport, governor)
    return ChatService(router), transport, governor


# -----------------------------------------------------------------
# Outbound: the three paths, and their separation
# -----------------------------------------------------------------
def test_send_local_chat_uses_say_command_only():
    service, transport, _ = make_service()
    result = service.send_local_chat("hello, place")
    assert result.ok and result.channel == LOCAL_CHAT
    assert len(transport.commands) == 1
    cmd = transport.commands[0]
    assert cmd.cmd == "say"
    assert cmd.params["msg"] == "hello, place"
    # the router mapped the default speaking mode for the Core
    assert cmd.params["type"] == "normal"
    assert cmd.tier == "mind"


def test_send_local_chat_whisper_mode_maps_to_core_type():
    service, transport, _ = make_service()
    result = service.send_local_chat("quietly", mode="whisper")
    assert result.ok
    assert transport.commands[0].params["type"] == "whisper"


def test_send_im_uses_im_command_with_uuid_only():
    service, transport, _ = make_service()
    result = service.send_im(ERIK, "just between us")
    assert result.ok and result.channel == IM
    assert len(transport.commands) == 1
    cmd = transport.commands[0]
    assert cmd.cmd == "im"
    assert cmd.params["to_uuid"] == ERIK
    assert cmd.params["msg"] == "just between us"
    # no local-chat clothing on an IM: no speaking mode, no chat type
    assert "mode" not in cmd.params and "type" not in cmd.params


def test_send_group_im_uses_group_command_only():
    service, transport, _ = make_service()
    result = service.send_group_im(GROUP, "to the group")
    assert result.ok and result.channel == GROUP_IM
    cmd = transport.commands[0]
    assert cmd.cmd == "group_im"
    assert cmd.params["group_uuid"] == GROUP


def test_channels_never_cross_across_all_three_sends():
    service, transport, _ = make_service()
    r_local = service.send_local_chat("public words")
    r_im = service.send_im(ERIK, "private words")
    r_group = service.send_group_im(GROUP, "group words")
    assert [c.cmd for c in transport.commands] == ["say", "im", "group_im"]
    assert (r_local.channel, r_im.channel, r_group.channel) == (
        LOCAL_CHAT,
        IM,
        GROUP_IM,
    )
    # the private text appears in exactly one command — the IM
    carriers = [c.cmd for c in transport.commands if "private" in c.params["msg"]]
    assert carriers == ["im"]


def test_im_without_recipient_raises_and_routes_nothing():
    service, transport, _ = make_service()
    with pytest.raises(ValueError):
        service.send_im("", "anyone there?")
    with pytest.raises(ValueError):
        service.send_im("   ", "anyone there?")
    assert transport.commands == []


def test_group_im_without_group_raises_and_routes_nothing():
    service, transport, _ = make_service()
    with pytest.raises(ValueError):
        service.send_group_im(None, "hello group")
    assert transport.commands == []


def test_empty_text_raises_on_every_path():
    service, transport, _ = make_service()
    with pytest.raises(ValueError):
        service.send_local_chat("  ")
    with pytest.raises(ValueError):
        service.send_im(ERIK, "")
    with pytest.raises(ValueError):
        service.send_group_im(GROUP, "")
    assert transport.commands == []


# -----------------------------------------------------------------
# Outbound: the governor, through the real router
# -----------------------------------------------------------------
def test_kill_switch_blocks_every_channel():
    service, transport, governor = make_service()
    governor.engage_kill_switch()
    for send in (
        lambda: service.send_local_chat("hello"),
        lambda: service.send_im(ERIK, "hello"),
        lambda: service.send_group_im(GROUP, "hello"),
    ):
        result = send()
        assert not result.ok
        assert result.blocked_reason == "kill_switch_engaged"
    assert transport.commands == []
    assert governor.count_today() == 0


def test_reflex_tier_may_not_speak_on_any_channel():
    service, transport, _ = make_service()
    result = service.send_im(ERIK, "reflex words", tier="reflex")
    assert not result.ok
    assert result.blocked_reason == "reflex_tier_may_not_speak"
    result = service.send_local_chat("reflex words", tier="reflex")
    assert not result.ok
    assert result.blocked_reason == "reflex_tier_may_not_speak"
    assert transport.commands == []


def test_daily_cap_blocks_after_confirmed_sends_only():
    governor = MessageGovernor(cap=2)
    service, transport, governor = make_service(governor=governor)
    assert service.send_local_chat("one").ok
    assert service.send_im(ERIK, "two").ok
    third = service.send_group_im(GROUP, "three")
    assert not third.ok
    assert third.blocked_reason == "daily_message_cap_reached"
    assert len(transport.commands) == 2
    assert governor.count_today() == 2


def test_core_refused_send_is_not_counted():
    transport = FakeTransport(fail=True)
    service, transport, governor = make_service(transport=transport)
    result = service.send_local_chat("will fail")
    assert not result.ok
    assert governor.count_today() == 0


def test_invalid_speaking_mode_fails_closed():
    service, transport, _ = make_service()
    result = service.send_local_chat("loud", mode="yell")
    assert not result.ok
    assert result.blocked_reason == "invalid_speaking_mode"
    assert transport.commands == []


# -----------------------------------------------------------------
# Inbound: fakes
# -----------------------------------------------------------------
class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSocket:
    """Scripted recv: bytes are returned, exceptions raised, and an
    exhausted script means 'nothing more right now' (timeout)."""

    def __init__(self, script=()):
        self._script = deque(script)
        self.closed = False

    def recv(self, _size):
        if not self._script:
            raise socket.timeout()
        item = self._script.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class FakeFactory:
    """Hands out scripted connect outcomes, in order."""

    def __init__(self, outcomes):
        self._outcomes = deque(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        item = self._outcomes.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


def ndjson(obj) -> bytes:
    return (json.dumps(obj) + "\n").encode()


CHAT_LINE = ndjson(
    {
        "event": "ChatReceived",
        "from": "Erik Resident",
        "from_uuid": ERIK,
        "msg": "hello place",
        "channel": 0,
        "type": "Normal",
    }
)
IM_LINE = ndjson(
    {
        "event": "IMReceived",
        "from": "Mara Resident",
        "from_uuid": MARA,
        "msg": "psst, privately",
        "timestamp": "2026-10-31T17:50:00+00:00",
    }
)
GROUP_LINE = ndjson(
    {
        "event": "GroupChatReceived",
        "from": "Erik Resident",
        "from_uuid": ERIK,
        "msg": "hello group",
        "group_uuid": GROUP,
    }
)


def make_watcher(factory, clock, **kwargs):
    return ChatWatcher(
        "/fake/heimdall.sock.events",
        socket_factory=factory,
        clock=clock,
        **kwargs,
    )


# -----------------------------------------------------------------
# Inbound: cadence + classification
# -----------------------------------------------------------------
def test_check_interval_is_the_ordered_five_seconds():
    assert CHECK_INTERVAL_S == 5.0
    watcher = make_watcher(FakeFactory([FakeSocket()]), FakeClock())
    assert watcher.check_interval_s == 5.0


def test_drain_happens_on_the_five_second_cadence_not_before():
    clock = FakeClock()
    watcher = make_watcher(FakeFactory([FakeSocket([CHAT_LINE])]), clock)
    watcher.step()  # t=1000: connect + read, but no drain yet
    assert watcher.staged_count == 1
    assert watcher.pending_interactions() == []
    clock.advance(4.9)
    watcher.step()
    assert watcher.pending_interactions() == []
    clock.advance(0.1)  # t=1005.0: the 5-second check falls due
    watcher.step()
    pending = watcher.pending_interactions()
    assert len(pending) == 1
    assert pending[0].channel == LOCAL_CHAT
    # asking consumes: a second ask sees nothing new
    assert watcher.pending_interactions() == []


def test_classification_of_all_three_channels():
    clock = FakeClock()
    sock = FakeSocket([CHAT_LINE + IM_LINE + GROUP_LINE])
    watcher = make_watcher(FakeFactory([sock]), clock)
    watcher.step()
    clock.advance(CHECK_INTERVAL_S)
    watcher.step()
    pending = watcher.pending_interactions()
    assert [p.channel for p in pending] == [LOCAL_CHAT, IM, GROUP_IM]
    chat, im, group = pending
    assert chat.from_name == "Erik Resident" and chat.from_uuid == ERIK
    assert chat.msg == "hello place"
    assert im.from_uuid == MARA and im.msg == "psst, privately"
    assert im.timestamp is not None and im.timestamp > 0
    assert group.msg == "hello group" and group.from_uuid == ERIK
    assert watcher.history == pending or len(watcher.history) == 3


def test_line_split_across_recvs_is_staged_whole():
    clock = FakeClock()
    cut = 17
    sock = FakeSocket([CHAT_LINE[:cut], CHAT_LINE[cut:]])
    watcher = make_watcher(FakeFactory([sock]), clock)
    watcher.step()
    assert watcher.staged_count == 1
    clock.advance(CHECK_INTERVAL_S)
    watcher.step()
    assert len(watcher.pending_interactions()) == 1


def test_known_avatars_learned_from_observed_events():
    clock = FakeClock()
    sock = FakeSocket([CHAT_LINE + IM_LINE])
    watcher = make_watcher(FakeFactory([sock]), clock)
    watcher.step()
    clock.advance(CHECK_INTERVAL_S)
    watcher.step()
    watcher.pending_interactions()
    known = watcher.known_avatars
    assert known.uuid_for("Erik Resident") == ERIK
    assert known.name_for(MARA) == "Mara Resident"
    assert len(known) == 2


def test_malformed_unknown_and_nonconversation_lines_fail_closed():
    clock = FakeClock()
    blob = (
        b"this is not json\n"
        + ndjson({"event": "MysteryEvent", "msg": "?"})
        + ndjson({"event": "RegionChanged", "region_name": "Drywall"})
        + ndjson(
            {"event": "ChatReceived", "msg": "no speaker at all"}
        )
    )
    watcher = make_watcher(FakeFactory([FakeSocket([blob])]), clock)
    watcher.step()
    clock.advance(CHECK_INTERVAL_S)
    watcher.step()
    assert watcher.pending_interactions() == []
    assert watcher.malformed_lines == 1
    assert watcher.unknown_events == 1
    assert watcher.nonconversation_events == 1
    assert watcher.unattributed_events == 1
    assert len(watcher.known_avatars) == 0


# -----------------------------------------------------------------
# Inbound: drop, gap, reconnect with backoff
# -----------------------------------------------------------------
def test_socket_close_records_a_gap_and_reconnects_after_backoff():
    clock = FakeClock()
    first = FakeSocket([CHAT_LINE, b""])  # one line, then Core closes
    second = FakeSocket([IM_LINE])
    factory = FakeFactory([first, second])
    watcher = make_watcher(factory, clock)

    watcher.step()  # connect, read the line, then the close lands
    assert not watcher.connected
    assert len(watcher.gaps) == 1 and watcher.gaps[0].is_open
    assert "closed by Core" in watcher.gaps[0].reason

    clock.advance(0.5)  # backoff (1 s) not yet elapsed: no retry
    watcher.step()
    assert factory.calls == 1

    clock.advance(0.5)  # t=+1.0: retry falls due and succeeds
    watcher.step()
    assert factory.calls == 2
    assert watcher.connected
    assert not watcher.gaps[0].is_open
    assert watcher.gaps[0].duration_s == pytest.approx(1.0)

    clock.advance(CHECK_INTERVAL_S)
    watcher.step()
    channels = [p.channel for p in watcher.pending_interactions()]
    assert channels == [LOCAL_CHAT, IM]  # both sides of the gap, in order


def test_connect_failure_backoff_grows_and_gap_stays_single():
    clock = FakeClock()
    factory = FakeFactory(
        [OSError("no socket"), OSError("still none"), FakeSocket()]
    )
    watcher = make_watcher(factory, clock)
    watcher.step()  # t=1000: attempt 1 fails, retry at +1 s
    assert factory.calls == 1
    clock.advance(1.0)
    watcher.step()  # attempt 2 fails, backoff now 2 s
    assert factory.calls == 2
    clock.advance(1.0)
    watcher.step()  # 1 s into a 2 s backoff: no attempt
    assert factory.calls == 2
    clock.advance(1.0)
    watcher.step()  # backoff elapsed: attempt 3 connects
    assert factory.calls == 3
    assert watcher.connected
    assert len(watcher.gaps) == 1  # one continuous hole, honestly told
    assert watcher.gaps[0].duration_s == pytest.approx(3.0)


def test_read_failure_drops_socket_and_records_gap():
    clock = FakeClock()
    sock = FakeSocket([OSError("connection reset")])
    watcher = make_watcher(FakeFactory([sock]), clock)
    watcher.step()
    assert not watcher.connected
    assert len(watcher.gaps) == 1
    assert "read failed" in watcher.gaps[0].reason


def test_threaded_run_delivers_interactions():
    sock = FakeSocket([CHAT_LINE])
    watcher = ChatWatcher(
        "/fake/heimdall.sock.events",
        socket_factory=FakeFactory([sock]),
        check_interval_s=0.2,
    )
    watcher.start()
    try:
        deadline = time.monotonic() + 3.0
        pending = []
        while time.monotonic() < deadline:
            pending = watcher.pending_interactions()
            if pending:
                break
            time.sleep(0.05)
        assert len(pending) == 1
        assert pending[0].channel == LOCAL_CHAT
        assert pending[0].msg == "hello place"
    finally:
        watcher.stop()
    assert watcher.gaps == []  # operator stop is not a coverage gap
