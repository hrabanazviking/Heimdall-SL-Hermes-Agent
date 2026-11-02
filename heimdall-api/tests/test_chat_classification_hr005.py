"""HR-005 — addressed-to classification v1 (responsiveness Part 0).

The laws under test:

- every Interaction the watcher classifies carries a stamp:
  ``direct`` for all IMs, ``named`` when the text mentions her
  name (configurable on the watcher, default "Yrsa",
  case-insensitive, word-boundary-ish), ``contextual`` for a
  line inside an open exchange with the same speaker on the
  same channel, ``ambient`` otherwise — and ``ambient`` is the
  Interaction default, so un-watcher-built records claim nothing;
- an exchange opens on direct/named, refreshes on further
  direct/named/contextual lines from the same speaker+channel,
  and expires after the named, configurable inactivity window
  (EXCHANGE_INACTIVITY_WINDOW_S = 120 s by default);
- exchange state is per speaker+channel, and per group for
  group chat: no speaker inherits another's exchange, and an
  exchange in one group (or in local chat) lends no context
  to another channel.
"""
import json
import socket
from collections import deque

from heimdall.chat import (
    AMBIENT,
    CHECK_INTERVAL_S,
    CONTEXTUAL,
    DIRECT,
    EXCHANGE_INACTIVITY_WINDOW_S,
    GROUP_IM,
    IM,
    LOCAL_CHAT,
    NAMED,
    ChatWatcher,
    ExchangeTracker,
    Interaction,
    exchange_key,
    mentions_self_name,
)

ERIK = "c3333333-3333-4333-8333-333333333333"
MARA = "d4444444-4444-4444-8444-444444444444"
GROUP = "e5555555-5555-4555-8555-555555555555"
OTHER_GROUP = "f6666666-6666-4666-8666-666666666666"


# -----------------------------------------------------------------
# Fakes (mirroring test_chat.py's, kept local so this file stands
# on its own). FakeSocket here is feedable between steps so a test
# can deliver lines at chosen arrival times.
# -----------------------------------------------------------------
class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSocket:
    def __init__(self):
        self._queue = deque()
        self.closed = False

    def feed(self, data: bytes) -> None:
        self._queue.append(data)

    def recv(self, _size):
        if not self._queue:
            raise socket.timeout()
        item = self._queue.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class FakeFactory:
    def __init__(self, sock):
        self._sock = sock

    def __call__(self):
        return self._sock


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


class Harness:
    """A watcher plus a feed: deliver lines at chosen clock times.

    Classification uses each line's arrival time, so the stamps
    below depend only on the delivery times, never on when the
    5-second drain happened to run.
    """

    def __init__(self, **watcher_kwargs):
        self.clock = FakeClock()
        self.sock = FakeSocket()
        self.watcher = ChatWatcher(
            "/fake/heimdall.sock.events",
            socket_factory=FakeFactory(self.sock),
            clock=self.clock,
            **watcher_kwargs,
        )
        self.watcher.step()  # connect; nothing staged

    def deliver(self, line: bytes, at: float) -> None:
        self.clock.t = at
        self.sock.feed(line)
        self.watcher.step()

    def stamps(self) -> list[str]:
        # Flush anything still staged, then read the record.
        self.clock.advance(CHECK_INTERVAL_S + 1.0)
        self.watcher.step()
        return [entry.classification for entry in self.watcher.history]


# -----------------------------------------------------------------
# The stamp itself: defaults and direct
# -----------------------------------------------------------------
def test_interaction_classification_defaults_to_ambient():
    entry = Interaction(
        channel=LOCAL_CHAT,
        from_name="Erik Resident",
        from_uuid=ERIK,
        msg="built by hand, never classified",
        timestamp=None,
        received_at=1000.0,
    )
    assert entry.classification == AMBIENT


def test_every_im_is_direct():
    h = Harness()
    h.deliver(im_line(msg="hello"), at=1000.0)
    # An IM that also mentions her name is still direct: the
    # channel rule wins over the name rule.
    h.deliver(im_line(msg="Yrsa, hello again"), at=1010.0)
    assert h.stamps() == [DIRECT, DIRECT]
    assert all(e.channel == IM for e in h.watcher.history)


def test_im_opens_an_exchange_on_the_im_channel():
    tracker = ExchangeTracker()
    stamp = tracker.classify(
        channel=IM, msg="hi", from_uuid=MARA, now=1000.0
    )
    assert stamp == DIRECT
    assert tracker.is_open(channel=IM, from_uuid=MARA, now=1001.0)


# -----------------------------------------------------------------
# Named: mention, matching rules, configurability
# -----------------------------------------------------------------
def test_named_local_mention_opens_exchange():
    h = Harness()
    h.deliver(chat_line(msg="Yrsa, are you there?"), at=1000.0)
    assert h.stamps() == [NAMED]


def test_named_matching_is_case_insensitive_and_word_bounded():
    assert mentions_self_name("hey yrsa, look", "Yrsa")
    assert mentions_self_name("YRSA! over here", "Yrsa")
    assert mentions_self_name("that is Yrsa's cloak", "Yrsa")
    # A longer word that merely contains the name is not a mention.
    assert not mentions_self_name("Yrsabelle said hi", "Yrsa")
    assert not mentions_self_name("no name in this line", "Yrsa")
    assert not mentions_self_name("anything", "")


def test_named_applies_in_group_chat_too():
    h = Harness()
    h.deliver(group_line(msg="yrsa, group question"), at=1000.0)
    assert h.stamps() == [NAMED]
    assert h.watcher.history[0].channel == GROUP_IM


def test_self_name_is_configurable_on_the_watcher():
    h = Harness(self_name="Runa")
    assert h.watcher.self_name == "Runa"
    h.deliver(chat_line(msg="hey Runa, a word"), at=1000.0)
    h.deliver(
        chat_line(uuid=MARA, name="Mara Resident", msg="hey Yrsa, a word"),
        at=1010.0,
    )
    assert h.stamps() == [NAMED, AMBIENT]


# -----------------------------------------------------------------
# Contextual: open exchange, "you"-continuations, refresh, expiry
# -----------------------------------------------------------------
def test_you_continuation_inside_open_exchange_is_contextual():
    h = Harness()
    h.deliver(chat_line(msg="Yrsa, I have a question"), at=1000.0)
    # No name, no address but the bare "you" — contextual purely
    # because Erik's exchange with her is open on this channel.
    h.deliver(chat_line(msg="you said you would look into it"), at=1030.0)
    assert h.stamps() == [NAMED, CONTEXTUAL]


def test_contextual_after_direct_named_and_within_window():
    tracker = ExchangeTracker()
    assert (
        tracker.classify(
            channel=LOCAL_CHAT, msg="Yrsa?", from_uuid=ERIK, now=1000.0
        )
        == NAMED
    )
    assert (
        tracker.classify(
            channel=LOCAL_CHAT,
            msg="and another thing",
            from_uuid=ERIK,
            now=1100.0,
        )
        == CONTEXTUAL
    )


def test_refresh_extends_the_window():
    h = Harness()
    h.deliver(chat_line(msg="Yrsa, first part"), at=1000.0)
    # 119 s later: inside the window, and it refreshes the clock.
    h.deliver(chat_line(msg="second part"), at=1119.0)
    # 119 s after the refresh (238 s after the open): still open —
    # without the refresh this line would be ambient.
    h.deliver(chat_line(msg="third part"), at=1238.0)
    assert h.stamps() == [NAMED, CONTEXTUAL, CONTEXTUAL]


def test_exchange_expires_after_the_inactivity_window():
    assert EXCHANGE_INACTIVITY_WINDOW_S == 120.0
    h = Harness()
    assert h.watcher.exchange_window_s == EXCHANGE_INACTIVITY_WINDOW_S
    h.deliver(chat_line(msg="Yrsa, a question"), at=1000.0)
    # 121 s of silence: the exchange has closed; a bare follow-up
    # with no name in it is ambient again.
    h.deliver(chat_line(msg="anyway, never mind"), at=1121.0)
    assert h.stamps() == [NAMED, AMBIENT]


def test_inactivity_window_is_configurable():
    h = Harness(exchange_window_s=10.0)
    h.deliver(chat_line(msg="Yrsa, quick question"), at=1000.0)
    h.deliver(chat_line(msg="too late a follow-up"), at=1011.0)
    assert h.stamps() == [NAMED, AMBIENT]


def test_tracker_expiry_prunes_open_exchanges():
    tracker = ExchangeTracker(window_s=120.0)
    tracker.classify(channel=IM, msg="hi", from_uuid=MARA, now=1000.0)
    assert tracker.open_exchange_count(now=1100.0) == 1
    assert tracker.open_exchange_count(now=1121.0) == 0
    assert not tracker.is_open(channel=IM, from_uuid=MARA, now=1121.0)


# -----------------------------------------------------------------
# Ambient default and speaker separation
# -----------------------------------------------------------------
def test_unrelated_local_chatter_is_ambient_and_opens_nothing():
    h = Harness()
    h.deliver(chat_line(msg="nice weather today"), at=1000.0)
    # Ambient lines never open an exchange, so the same speaker's
    # next unrelated line is ambient too, not contextual.
    h.deliver(chat_line(msg="you think it will rain?"), at=1010.0)
    assert h.stamps() == [AMBIENT, AMBIENT]


def test_different_speaker_does_not_inherit_an_open_exchange():
    h = Harness()
    h.deliver(chat_line(msg="Yrsa, question for you"), at=1000.0)
    # Mara speaks on the same channel inside Erik's window, but
    # Erik's exchange is Erik's: her line is ambient.
    h.deliver(
        chat_line(uuid=MARA, name="Mara Resident", msg="you were saying?"),
        at=1010.0,
    )
    # And Erik's own continuation is still contextual.
    h.deliver(chat_line(msg="as I was saying"), at=1020.0)
    assert h.stamps() == [NAMED, AMBIENT, CONTEXTUAL]


# -----------------------------------------------------------------
# Channel / group separation of exchange state
# -----------------------------------------------------------------
def test_exchange_key_distinguishes_groups_only_for_group_chat():
    base = dict(from_uuid=ERIK, from_name="Erik Resident")
    assert exchange_key(channel=GROUP_IM, group_uuid=GROUP, **base) != (
        exchange_key(channel=GROUP_IM, group_uuid=OTHER_GROUP, **base)
    )
    # Local chat carries no group component even if one is passed.
    assert exchange_key(channel=LOCAL_CHAT, group_uuid=GROUP, **base) == (
        ERIK,
        LOCAL_CHAT,
        None,
    )
    assert exchange_key(
        channel=LOCAL_CHAT, from_uuid=None, from_name=None, group_uuid=None
    ) is None


def test_group_exchange_does_not_leak_to_another_group():
    h = Harness()
    h.deliver(group_line(msg="Yrsa, in this group first"), at=1000.0)
    h.deliver(group_line(msg="same speaker, other room", group=OTHER_GROUP), at=1010.0)
    h.deliver(group_line(msg="back in the first room"), at=1020.0)
    assert h.stamps() == [NAMED, AMBIENT, CONTEXTUAL]


def test_local_exchange_does_not_leak_into_group_or_vice_versa():
    h = Harness()
    h.deliver(chat_line(msg="Yrsa, in local"), at=1000.0)
    # Same speaker, different channel: no context carried over.
    h.deliver(group_line(msg="continuing in the group"), at=1010.0)
    # The local exchange is untouched and still open.
    h.deliver(chat_line(msg="continuing in local"), at=1020.0)
    assert h.stamps() == [NAMED, AMBIENT, CONTEXTUAL]
    assert [e.channel for e in h.watcher.history] == [
        LOCAL_CHAT,
        GROUP_IM,
        LOCAL_CHAT,
    ]
