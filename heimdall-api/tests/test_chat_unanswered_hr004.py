"""HR-004 — the unanswered-set exposure (responsiveness Part 0).

The laws under test:

- every classified interaction is owed from drain time, and the
  unanswered set is a *query*, not a queue: asking consumes nothing
  and repeated asks agree until a verified send settles a debt;
- consuming the watcher's inbox (the mind's handoff) does NOT
  answer anything — handed-over is not answered;
- a debt settles only with a Core-confirmed send (receipts law):
  refused sends and governor-blocked sends settle nothing;
- settlement is channel-exact and target-exact (IM by recipient
  UUID, group IM by group UUID, local by channel), oldest first,
  one send settles exactly one debt, and a send made before a debt
  exists earns no credit against future debt;
- identity is position, not text: two identical "test" IMs are two
  debts (the F4 lesson), and one reply settles exactly one.
"""
import json
import socket
from collections import deque

from heimdall.api.command_router import CommandRouter
from heimdall.chat import (
    GROUP_IM,
    IM,
    LOCAL_CHAT,
    ChatService,
    ChatWatcher,
    find_settlement_candidate,
    matches_settlement,
)
from heimdall.chat.channels import Interaction
from heimdall.core.governor import MessageGovernor

ERIK = "c3333333-3333-4333-8333-333333333333"
MARA = "d4444444-4444-4444-8444-444444444444"
GROUP = "e5555555-5555-4555-8555-555555555555"
OTHER_GROUP = "f6666666-6666-4666-8666-666666666666"


# -----------------------------------------------------------------
# Fakes (mirroring test_chat.py's, kept local so this file stands
# on its own)
# -----------------------------------------------------------------
class FakeTransport:
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


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSocket:
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
    def __init__(self, outcomes):
        self._outcomes = deque(outcomes)

    def __call__(self):
        item = self._outcomes.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


def ndjson(obj) -> bytes:
    return (json.dumps(obj) + "\n").encode()


def im_line(uuid=MARA, name="Mara Resident", msg="psst"):
    return ndjson(
        {"event": "IMReceived", "from": name, "from_uuid": uuid, "msg": msg}
    )


def chat_line(uuid=ERIK, name="Erik Resident", msg="hello place"):
    return ndjson(
        {"event": "ChatReceived", "from": name, "from_uuid": uuid, "msg": msg}
    )


def group_line(uuid=ERIK, name="Erik Resident", msg="hello group", group=GROUP):
    return ndjson(
        {
            "event": "GroupChatReceived",
            "from": name,
            "from_uuid": uuid,
            "msg": msg,
            "group_uuid": group,
        }
    )


def make_watcher_with(lines: bytes):
    """A watcher that has drained the given event lines."""
    clock = FakeClock()
    watcher = ChatWatcher(
        "/fake/heimdall.sock.events",
        socket_factory=FakeFactory([FakeSocket([lines])]),
        clock=clock,
    )
    watcher.step()  # connect + read (staged, not yet drained)
    clock.advance(5.0)
    watcher.step()  # the 5-second drain classifies
    return watcher


def make_service(watcher=None, *, transport=None, governor=None):
    transport = transport if transport is not None else FakeTransport()
    governor = governor if governor is not None else MessageGovernor()
    router = CommandRouter(transport, governor)
    return ChatService(
        router,
        on_send=watcher.record_send_result if watcher is not None else None,
    )


# -----------------------------------------------------------------
# The query itself
# -----------------------------------------------------------------
def test_unanswered_lists_every_classified_interaction():
    watcher = make_watcher_with(im_line() + chat_line() + group_line())
    owed = watcher.unanswered_interactions()
    assert [o.channel for o in owed] == [IM, LOCAL_CHAT, GROUP_IM]
    assert owed[0].from_uuid == MARA


def test_unanswered_query_consumes_nothing_and_is_repeatable():
    watcher = make_watcher_with(im_line(msg="one") + im_line(msg="two"))
    first = watcher.unanswered_interactions()
    second = watcher.unanswered_interactions()
    assert first == second and len(first) == 2


def test_unanswered_channel_filter():
    watcher = make_watcher_with(im_line() + chat_line())
    assert len(watcher.unanswered_interactions(IM)) == 1
    assert len(watcher.unanswered_interactions(LOCAL_CHAT)) == 1
    assert watcher.unanswered_interactions(GROUP_IM) == []


def test_consuming_the_inbox_does_not_answer_anything():
    watcher = make_watcher_with(im_line())
    handed = watcher.pending_interactions()
    assert len(handed) == 1  # the mind has it in hand...
    assert len(watcher.unanswered_interactions()) == 1  # ...still owed


def test_group_interaction_carries_its_group_uuid():
    watcher = make_watcher_with(group_line())
    (owed,) = watcher.unanswered_interactions(GROUP_IM)
    assert owed.group_uuid == GROUP


# -----------------------------------------------------------------
# Settlement — receipts law
# -----------------------------------------------------------------
def test_confirmed_im_settles_exactly_that_senders_oldest_debt():
    watcher = make_watcher_with(im_line(msg="first") + im_line(msg="second"))
    service = make_service(watcher)
    result = service.send_im(MARA, "reply")
    assert result.ok
    owed = watcher.unanswered_interactions()
    assert [o.msg for o in owed] == ["second"]


def test_identical_text_ims_are_two_debts_one_reply_settles_one():
    # The F4 lesson: identity is position, never text.
    watcher = make_watcher_with(im_line(msg="test") + im_line(msg="test"))
    assert len(watcher.unanswered_interactions()) == 2
    service = make_service(watcher)
    service.send_im(MARA, "got it")
    assert len(watcher.unanswered_interactions()) == 1


def test_refused_send_settles_nothing():
    watcher = make_watcher_with(im_line())
    service = make_service(watcher, transport=FakeTransport(fail=True))
    result = service.send_im(MARA, "reply")
    assert not result.ok
    assert len(watcher.unanswered_interactions()) == 1


def test_governor_blocked_send_settles_nothing():
    watcher = make_watcher_with(im_line())
    governor = MessageGovernor()
    governor.engage_kill_switch()
    service = make_service(watcher, governor=governor)
    result = service.send_im(MARA, "reply")
    assert not result.ok and result.blocked_reason == "kill_switch_engaged"
    assert len(watcher.unanswered_interactions()) == 1


def test_send_before_debt_earns_no_credit():
    clock = FakeClock()
    # First recv finds nothing (timeout); the IM arrives on a later beat.
    sock = FakeSocket([socket.timeout(), im_line()])
    watcher = ChatWatcher(
        "/fake/heimdall.sock.events",
        socket_factory=FakeFactory([sock]),
        clock=clock,
    )
    watcher.step()  # connect; nothing staged, nothing owed
    assert watcher.unanswered_interactions() == []
    service = make_service(watcher)
    assert service.send_im(MARA, "early").ok  # settles nothing — no debt yet
    clock.advance(5.0)
    watcher.step()  # the IM arrives and is classified
    # ...and is still fully owed: the early send earned no credit.
    assert len(watcher.unanswered_interactions()) == 1


# -----------------------------------------------------------------
# Settlement — channel-exact, target-exact
# -----------------------------------------------------------------
def test_im_settlement_is_target_exact():
    watcher = make_watcher_with(
        im_line(uuid=ERIK, name="Erik Resident", msg="from erik")
        + im_line(msg="from mara")
    )
    service = make_service(watcher)
    service.send_im(MARA, "for mara")
    owed = watcher.unanswered_interactions()
    assert [o.msg for o in owed] == ["from erik"]


def test_no_cross_channel_settlement():
    watcher = make_watcher_with(im_line() + chat_line())
    service = make_service(watcher)
    service.send_local_chat("public reply")  # settles the local debt only
    assert [o.channel for o in watcher.unanswered_interactions()] == [IM]
    service.send_im(MARA, "private reply")  # and the IM only for the IM
    assert watcher.unanswered_interactions() == []


def test_group_settlement_requires_the_same_group():
    watcher = make_watcher_with(group_line())
    service = make_service(watcher)
    assert service.send_group_im(OTHER_GROUP, "wrong room").ok
    assert len(watcher.unanswered_interactions()) == 1  # other group: no
    assert service.send_group_im(GROUP, "right room").ok
    assert watcher.unanswered_interactions() == []


def test_local_settlement_is_oldest_first():
    watcher = make_watcher_with(chat_line(msg="one") + chat_line(msg="two"))
    service = make_service(watcher)
    service.send_local_chat("answer")
    assert [o.msg for o in watcher.unanswered_interactions()] == ["two"]


# -----------------------------------------------------------------
# SendResult.target_uuid — the confirmation record's target stamp
# -----------------------------------------------------------------
def test_target_uuid_stamped_per_channel():
    service = make_service()
    assert service.send_im(MARA, "x").target_uuid == MARA
    assert service.send_group_im(GROUP, "x").target_uuid == GROUP
    assert service.send_local_chat("x").target_uuid is None


def test_on_send_observer_sees_refusals_too():
    seen = []
    transport = FakeTransport(fail=True)
    router = CommandRouter(transport, MessageGovernor())
    service = ChatService(router, on_send=seen.append)
    result = service.send_im(MARA, "x")
    assert seen == [result] and not result.ok


# -----------------------------------------------------------------
# The pure matching rules (heimdall.chat.debt)
# -----------------------------------------------------------------
def _interaction(channel, *, from_uuid=MARA, group_uuid=None):
    return Interaction(
        channel=channel,
        from_name="Mara Resident",
        from_uuid=from_uuid,
        msg="hi",
        timestamp=None,
        received_at=1000.0,
        group_uuid=group_uuid,
    )


def test_matches_settlement_edges():
    im = _interaction(IM)
    assert matches_settlement(im, channel=IM, target_uuid=MARA)
    assert not matches_settlement(im, channel=IM, target_uuid=ERIK)
    assert not matches_settlement(im, channel=IM, target_uuid=None)
    assert not matches_settlement(im, channel=LOCAL_CHAT, target_uuid=None)
    anon_im = _interaction(IM, from_uuid=None)
    assert not matches_settlement(anon_im, channel=IM, target_uuid=MARA)
    grp = _interaction(GROUP_IM, group_uuid=GROUP)
    assert matches_settlement(grp, channel=GROUP_IM, target_uuid=GROUP)
    assert not matches_settlement(grp, channel=GROUP_IM, target_uuid=OTHER_GROUP)
    unknown_group = _interaction(GROUP_IM, group_uuid=None)
    assert not matches_settlement(
        unknown_group, channel=GROUP_IM, target_uuid=GROUP
    )
    local = _interaction(LOCAL_CHAT)
    assert matches_settlement(local, channel=LOCAL_CHAT, target_uuid=None)
    assert not matches_settlement(local, channel="smoke_signal", target_uuid=None)


def test_find_settlement_candidate_is_oldest_match_or_none():
    a = _interaction(IM)
    b = _interaction(IM)
    assert find_settlement_candidate([a, b], channel=IM, target_uuid=MARA) is a
    assert find_settlement_candidate([a], channel=IM, target_uuid=ERIK) is None
    assert find_settlement_candidate([], channel=IM, target_uuid=MARA) is None
