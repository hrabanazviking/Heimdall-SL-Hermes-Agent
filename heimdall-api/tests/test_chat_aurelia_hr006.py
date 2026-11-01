"""HR-006 — Aurelia dual feed v1 (responsiveness Part 0).

The laws under test:

- every classified inbound interaction is also POSTed to
  Aurelia's ``/event`` as ``{type, source, payload}`` with the
  channel mapped to ``sl.im`` / ``sl.chat`` / ``sl.group_im``,
  source ``"heimdall"``, and a payload carrying channel, speaker
  identity/name, text, the HR-005 classification stamp, and
  ``group_uuid`` where relevant;
- a failed post — transport exception or HTTP 500 alike — is
  logged and never blocks the mind feed: the interaction still
  lands in history/inbox and still shows as unanswered debt;
- plug absent or disabled = no posts and no behaviour change;
  even a plug that raises despite the plug contract cannot
  raise into the watcher;
- the base URL is configurable (constructor, then the
  ``HEIMDALL_AURELIA_URL`` env var, then the documented default).

The real-loopback-server test at the end is the slice's
synthetic-event proof: a genuine HTTP POST through the default
urllib transport, received and recorded by a fake Aurelia.
Live visibility in the real Aurelia on Mimir is a separate,
attended confirmation and is NOT claimed by these tests.
"""
import json
import logging
import socket
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from heimdall.chat import (
    AURELIA_SOURCE,
    AURELIA_URL_ENV_VAR,
    CHANNEL_EVENT_TYPES,
    CHECK_INTERVAL_S,
    DEFAULT_AURELIA_URL,
    GROUP_IM,
    IM,
    LOCAL_CHAT,
    AureliaFeed,
    ChatWatcher,
    Interaction,
    build_aurelia_event,
)
from heimdall.chat.aurelia import DEFAULT_TIMEOUT_S

ERIK = "c3333333-3333-4333-8333-333333333333"
MARA = "d4444444-4444-4444-8444-444444444444"
GROUP = "e5555555-5555-4555-8555-555555555555"


# -----------------------------------------------------------------
# Fakes (mirroring the HR-005 harness, kept local so this file
# stands on its own)
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


class RecordingPoster:
    """A fake Aurelia transport: records calls, answers a status."""

    def __init__(self, status: int = 200):
        self.status = status
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url, event, timeout_s):
        self.calls.append((url, event, timeout_s))
        return self.status


class RaisingPoster:
    def __call__(self, url, event, timeout_s):
        raise ConnectionError("aurelia is down (fake)")


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

    def flush(self) -> None:
        self.clock.advance(CHECK_INTERVAL_S + 1.0)
        self.watcher.step()


# -----------------------------------------------------------------
# (a) Types and payload fields per channel
# -----------------------------------------------------------------
def test_channel_event_type_mapping_is_the_documented_one():
    assert CHANNEL_EVENT_TYPES == {
        IM: "sl.im",
        LOCAL_CHAT: "sl.chat",
        GROUP_IM: "sl.group_im",
    }
    assert AURELIA_SOURCE == "heimdall"
    assert DEFAULT_AURELIA_URL == "http://127.0.0.1:8001"
    assert AURELIA_URL_ENV_VAR == "HEIMDALL_AURELIA_URL"


def test_all_three_channels_post_with_correct_types_and_payloads():
    poster = RecordingPoster()
    feed = AureliaFeed("http://aurelia.test:8001/", poster=poster)
    h = Harness(aurelia_feed=feed)
    h.deliver(im_line(msg="a private word"), at=1000.0)
    h.deliver(chat_line(msg="Yrsa, a public word"), at=1010.0)
    h.deliver(group_line(msg="a group word"), at=1020.0)
    h.flush()

    assert feed.posted == 3 and feed.failed == 0
    assert len(poster.calls) == 3
    # The trailing slash on the base URL is normalised away.
    assert all(
        url == "http://aurelia.test:8001/event" for url, _, _ in poster.calls
    )
    assert all(timeout == DEFAULT_TIMEOUT_S for _, _, timeout in poster.calls)

    events = [event for _, event, _ in poster.calls]
    assert [e["type"] for e in events] == ["sl.im", "sl.chat", "sl.group_im"]
    assert all(e["source"] == "heimdall" for e in events)
    assert all(set(e) == {"type", "source", "payload"} for e in events)

    history = h.watcher.history
    assert [e.channel for e in history] == [IM, LOCAL_CHAT, GROUP_IM]
    for event, entry in zip(events, history):
        payload = event["payload"]
        assert payload["channel"] == entry.channel
        assert payload["from_name"] == entry.from_name
        assert payload["from_uuid"] == entry.from_uuid
        assert payload["text"] == entry.msg
        # The HR-005 stamp rides along, exactly as classified.
        assert payload["classification"] == entry.classification
        assert payload["received_at"] == entry.received_at
    assert [e["payload"]["classification"] for e in events] == [
        "direct",  # every IM
        "named",  # "Yrsa, ..." in local chat
        "ambient",  # group line, no name, no open group exchange
    ]
    # group_uuid rides only where relevant: the group line.
    assert "group_uuid" not in events[0]["payload"]
    assert "group_uuid" not in events[1]["payload"]
    assert events[2]["payload"]["group_uuid"] == GROUP


def test_build_aurelia_event_for_a_hand_built_interaction():
    entry = Interaction(
        channel=GROUP_IM,
        from_name="Erik Resident",
        from_uuid=ERIK,
        msg="hands",
        timestamp=None,
        received_at=1000.0,
        group_uuid=GROUP,
        classification="named",
    )
    event = build_aurelia_event(entry)
    assert event["type"] == "sl.group_im"
    assert event["payload"]["group_uuid"] == GROUP
    assert event["payload"]["classification"] == "named"


# -----------------------------------------------------------------
# (b) Failure isolation: logged, swallowed, mind feed unaffected
# -----------------------------------------------------------------
def test_raising_poster_is_logged_and_interaction_still_lands(caplog):
    feed = AureliaFeed("http://aurelia.test:8001", poster=RaisingPoster())
    h = Harness(aurelia_feed=feed)
    with caplog.at_level(logging.WARNING, logger="heimdall.chat.aurelia"):
        h.deliver(im_line(msg="still heard"), at=1000.0)
        h.flush()
    assert feed.posted == 0 and feed.failed == 1
    assert "Aurelia feed" in caplog.text and "failed" in caplog.text
    # The mind feed is untouched: record, inbox and debt all hold it.
    assert [e.msg for e in h.watcher.history] == ["still heard"]
    assert [e.msg for e in h.watcher.pending_interactions()] == ["still heard"]
    assert [e.msg for e in h.watcher.unanswered_interactions()] == ["still heard"]


def test_http_500_is_logged_and_interaction_still_lands(caplog):
    poster = RecordingPoster(status=500)
    feed = AureliaFeed("http://aurelia.test:8001", poster=poster)
    h = Harness(aurelia_feed=feed)
    with caplog.at_level(logging.WARNING, logger="heimdall.chat.aurelia"):
        h.deliver(chat_line(msg="Yrsa, still heard"), at=1000.0)
        h.flush()
    assert len(poster.calls) == 1
    assert feed.posted == 0 and feed.failed == 1
    assert "HTTP 500" in caplog.text
    assert [e.msg for e in h.watcher.history] == ["Yrsa, still heard"]
    assert [e.msg for e in h.watcher.unanswered_interactions()] == [
        "Yrsa, still heard"
    ]


def test_a_rude_plug_that_raises_cannot_raise_into_the_watcher(caplog):
    class RudePlug:
        def feed(self, _interaction):
            raise RuntimeError("plug contract violated")

    h = Harness(aurelia_feed=RudePlug())
    with caplog.at_level(logging.WARNING, logger="heimdall.chat.watcher"):
        h.deliver(im_line(msg="heard anyway"), at=1000.0)
        h.flush()
    assert "swallowed" in caplog.text
    assert [e.msg for e in h.watcher.history] == ["heard anyway"]
    assert [e.msg for e in h.watcher.pending_interactions()] == ["heard anyway"]


def test_feed_never_raises_and_counts_each_failure():
    feed = AureliaFeed("http://aurelia.test:8001", poster=RaisingPoster())
    entry = Interaction(
        channel=IM,
        from_name="Mara Resident",
        from_uuid=MARA,
        msg="one",
        timestamp=None,
        received_at=1000.0,
        classification="direct",
    )
    assert feed.feed(entry) is False
    assert feed.feed(entry) is False
    assert feed.failed == 2 and feed.posted == 0


# -----------------------------------------------------------------
# (c) Absent / disabled plug: no posts, no behaviour change
# -----------------------------------------------------------------
def test_no_plug_means_no_posts_and_unchanged_behaviour():
    h = Harness()  # no aurelia_feed at all
    assert h.watcher.aurelia_feed is None
    h.deliver(im_line(msg="just the mind feed"), at=1000.0)
    h.flush()
    assert [e.msg for e in h.watcher.history] == ["just the mind feed"]
    assert [e.msg for e in h.watcher.pending_interactions()] == [
        "just the mind feed"
    ]


def test_disabled_plug_posts_nothing_and_feed_returns_false():
    poster = RecordingPoster()
    feed = AureliaFeed(
        "http://aurelia.test:8001", poster=poster, enabled=False
    )
    h = Harness(aurelia_feed=feed)
    h.deliver(im_line(msg="quiet"), at=1000.0)
    h.flush()
    assert poster.calls == []
    assert feed.posted == 0 and feed.failed == 0
    assert [e.msg for e in h.watcher.history] == ["quiet"]


def test_a_bare_callable_works_as_a_plug():
    seen: list[Interaction] = []
    h = Harness(aurelia_feed=seen.append)
    h.deliver(im_line(msg="via callable"), at=1000.0)
    h.flush()
    assert [e.msg for e in seen] == ["via callable"]


# -----------------------------------------------------------------
# (d) Base URL configurability
# -----------------------------------------------------------------
def test_base_url_from_constructor_env_and_default(monkeypatch):
    poster = RecordingPoster()
    explicit = AureliaFeed("http://explicit:9000/", poster=poster)
    assert explicit.base_url == "http://explicit:9000"
    assert explicit.event_url == "http://explicit:9000/event"

    monkeypatch.setenv(AURELIA_URL_ENV_VAR, "http://from-env:8001/")
    from_env = AureliaFeed(poster=poster)
    assert from_env.base_url == "http://from-env:8001"
    # An explicit constructor value outranks the environment.
    assert AureliaFeed("http://wins:1", poster=poster).base_url == "http://wins:1"

    monkeypatch.delenv(AURELIA_URL_ENV_VAR)
    assert AureliaFeed(poster=poster).base_url == DEFAULT_AURELIA_URL


# -----------------------------------------------------------------
# Synthetic-event proof: a real HTTP round trip on loopback
# through the default urllib transport, to a recording fake
# Aurelia server. (Live Aurelia on Mimir: NOT claimed here.)
# -----------------------------------------------------------------
class _RecordingAureliaHandler(BaseHTTPRequestHandler):
    received: list[tuple[str, dict]] = []

    def do_POST(self):  # noqa: N802 — stdlib handler name
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append((self.path, body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"published": true, "handlers_reached": 1}')

    def log_message(self, *_args):  # keep the test log clean
        pass


def test_synthetic_event_reaches_a_real_loopback_http_server():
    _RecordingAureliaHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingAureliaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        feed = AureliaFeed(base)  # default urllib poster, real HTTP
        entry = Interaction(
            channel=IM,
            from_name="Mara Resident",
            from_uuid=MARA,
            msg="synthetic HR-006 proof event",
            timestamp=None,
            received_at=1000.0,
            classification="direct",
        )
        assert feed.feed(entry) is True
        assert feed.posted == 1 and feed.failed == 0
    finally:
        server.shutdown()
        server.server_close()
    assert len(_RecordingAureliaHandler.received) == 1
    path, body = _RecordingAureliaHandler.received[0]
    assert path == "/event"
    assert body["type"] == "sl.im"
    assert body["source"] == "heimdall"
    assert body["payload"]["text"] == "synthetic HR-006 proof event"
    assert body["payload"]["classification"] == "direct"
    assert body["payload"]["from_uuid"] == MARA
