"""HR-002 — Activation rehearsal on the bench (dress rehearsal).

Roadmap HR-002, verbatim: *"Run ChatWatcher against a fake Core
event socket + the real router with a fake transport: full dress
rehearsal of service startup, drain cadence, gap recording,
shutdown. Evidence: rehearsal log with a forced socket drop and
its GapRecord."*

This is NOT another step()-unit test (tests/test_chat.py already
covers the state machine deterministically with fake clocks and
fake sockets). This rehearsal runs the watcher the way the real
service will run it:

- the REAL :class:`ChatWatcher` in its REAL threaded mode
  (``start()`` / ``run()`` / ``stop()``), with its REAL clock and
  the REAL default cadence ``CHECK_INTERVAL_S = 5.0`` — no fake
  clock, no shortened interval, no injected socket factory;
- connected through a REAL Unix stream socket to a fake Core
  event server (this file) that speaks the Core NDJSON event
  format on a real socket path;
- alongside the REAL ``CommandRouter`` + ``MessageGovernor``
  wired to a recording fake transport, exercising one outbound
  send on each of the three channels through ``ChatService``.

The rehearsal exercises, in order, and writes each beat to the
rehearsal log:

1. service startup (server listen, watcher thread start, connect);
2. inbound events in all three conversation forms (local chat,
   IM, group chat) staged by push, then classified and drained
   at the 5-second cadence (pre-drain inbox is empty);
3. outbound sends on all three channels via the real router;
4. a FORCED socket drop mid-run (the fake Core closes the
   connection without warning);
5. the resulting GapRecord (opened on the drop, closed on
   reconnect, with its reason and duration);
6. automatic reconnect after the watcher's backoff, and a
   post-reconnect IM proving the ear is live again;
7. clean shutdown (operator stop records NO extra gap).

The rehearsal log path comes from ``HEIMDALL_HR002_LOG`` when
set, else ``hr002-rehearsal.log`` next to this file's build root
(``/tmp`` under pytest's tmp usage is avoided — the evidence must
survive the test run). Running this file directly
(``python tests/test_chat_rehearsal_hr002.py [log_path]``) runs
the same rehearsal outside pytest and exits non-zero on failure.

Bench-only: no Second Life login, no credentials, no real Core.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow direct execution (``python tests/test_chat_rehearsal_hr002.py``)
# from the heimdall-api directory as well as under pytest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heimdall.api.command_router import CommandRouter  # noqa: E402
from heimdall.chat import (  # noqa: E402
    CHECK_INTERVAL_S,
    GROUP_IM,
    IM,
    LOCAL_CHAT,
    ChatService,
    ChatWatcher,
)
from heimdall.core.governor import MessageGovernor  # noqa: E402

ERIK = "c3333333-3333-4333-8333-333333333333"
ERIK_NAME = "Erik Resident"
MARA = "d4444444-4444-4444-8444-444444444444"
MARA_NAME = "Mara Resident"
GROUP = "e5555555-5555-4555-8555-555555555555"

PRE_DROP_EVENTS = [
    {
        "event": "ChatReceived",
        "from": ERIK_NAME,
        "from_uuid": ERIK,
        "msg": "hello place, rehearsal local",
        "channel": 0,
        "type": "Normal",
    },
    {
        "event": "IMReceived",
        "from": MARA_NAME,
        "from_uuid": MARA,
        "msg": "psst, rehearsal IM",
        "timestamp": "2026-10-31T17:50:00+00:00",
    },
    {
        "event": "GroupChatReceived",
        "from": ERIK_NAME,
        "from_uuid": ERIK,
        "msg": "hello group, rehearsal group",
        "group_uuid": GROUP,
    },
]
POST_DROP_EVENT = {
    "event": "IMReceived",
    "from": MARA_NAME,
    "from_uuid": MARA,
    "msg": "back again after the drop",
    "timestamp": "2026-10-31T17:51:00+00:00",
}


def _ndjson(obj) -> bytes:
    return (json.dumps(obj) + "\n").encode()


class RehearsalLog:
    """Timestamped evidence log: elapsed + wall clock, teed to a file."""

    def __init__(self, path: Path):
        self.path = path
        self.lines: list[str] = []
        self._t0 = time.monotonic()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        elapsed = time.monotonic() - self._t0
        wall = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = f"[t=+{elapsed:7.3f}s] [{wall}] {msg}"
        self.lines.append(line)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(line, flush=True)


class FakeCoreEventServer:
    """A real Unix-socket server speaking Core NDJSON events.

    Accepts sequential watcher connections (the watcher reconnects
    to the same path after a drop), pushes event lines on demand,
    and can forcibly drop the current connection mid-run.
    """

    def __init__(self, path: str):
        self.path = path
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(path)
        self._listener.listen(4)
        self._listener.settimeout(0.25)
        self._stop = threading.Event()
        self._conn: socket.socket | None = None
        self._conn_lock = threading.Lock()
        self.connections_accepted = 0
        self._thread = threading.Thread(
            target=self._accept_loop, name="hr002-fake-core", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            with self._conn_lock:
                old, self._conn = self._conn, conn
                self.connections_accepted += 1
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass

    def has_connection(self) -> bool:
        with self._conn_lock:
            return self._conn is not None

    def push(self, payload: dict) -> None:
        with self._conn_lock:
            conn = self._conn
        if conn is None:
            raise RuntimeError("fake Core: no watcher connection to push to")
        conn.sendall(_ndjson(payload))

    def drop_current(self) -> None:
        """Forcibly close the watcher connection — a mid-run drop."""
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def stop(self) -> None:
        self._stop.set()
        self.drop_current()
        try:
            self._listener.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


class RecordingTransport:
    """Fake Core command transport: records, confirms every send."""

    def __init__(self):
        self.commands = []

    def send_command(self, command):
        self.commands.append(command)
        return {
            "id": command.id,
            "cmd": command.cmd,
            "status": "ok",
            "result": {"sent": True},
        }


def _wait_for(predicate, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout_s:.1f}s waiting for: {what}")


def run_rehearsal(log_path: Path, socket_dir: Path) -> dict:
    """Run the full HR-002 dress rehearsal; return the evidence."""
    log = RehearsalLog(log_path)
    socket_dir.mkdir(parents=True, exist_ok=True)
    event_socket = str(socket_dir / "heimdall.sock.events")

    governor = MessageGovernor()
    transport = RecordingTransport()
    router = CommandRouter(transport, governor)
    service = ChatService(router)

    server = FakeCoreEventServer(event_socket)
    # The production construction: real socket path, real clock,
    # real default socket factory, real 5-second cadence.
    watcher = ChatWatcher(event_socket)
    assert watcher.check_interval_s == CHECK_INTERVAL_S == 5.0

    evidence: dict = {}
    try:
        # -- 1. startup ------------------------------------------------
        log(
            "STARTUP fake-core listening event_socket=%s "
            "check_interval_s=%.1f governor_cap=%s"
            % (event_socket, watcher.check_interval_s, governor.cap)
        )
        server.start()
        thread = watcher.start()
        log(f"STARTUP watcher thread started name={thread.name} alive={thread.is_alive()}")
        _wait_for(lambda: watcher.connected and server.has_connection(),
                  5.0, "watcher to connect to fake Core")
        log("CONNECTED watcher holds the event socket (production factory)")

        # -- 2. inbound: three forms, staged then drained at cadence ---
        for payload in PRE_DROP_EVENTS:
            server.push(payload)
        _wait_for(lambda: watcher.staged_count == 3, 5.0, "3 events staged")
        log(
            "STAGED 3 events pushed (local_chat, im, group_chat); "
            f"staged_count={watcher.staged_count} "
            f"peek_pending={len(watcher.peek_pending())} "
            "(inbox empty before the 5s drain — cadence is real)"
        )
        assert watcher.peek_pending() == []

        # -- 3. outbound: real router + governor, fake transport -------
        r_local = service.send_local_chat("rehearsal local reply")
        r_im = service.send_im(MARA, "rehearsal IM reply")
        r_group = service.send_group_im(GROUP, "rehearsal group reply")
        log(
            "SENDS via real CommandRouter+MessageGovernor -> recording "
            f"transport: local ok={r_local.ok} channel={r_local.channel}; "
            f"im ok={r_im.ok} channel={r_im.channel}; "
            f"group ok={r_group.ok} channel={r_group.channel}; "
            f"wire cmds={[c.cmd for c in transport.commands]}; "
            f"governor_count_today={governor.count_today()}"
        )
        assert (r_local.ok, r_im.ok, r_group.ok) == (True, True, True)
        assert [c.cmd for c in transport.commands] == ["say", "im", "group_im"]
        assert governor.count_today() == 3

        # -- drain at the 5-second cadence ------------------------------
        _wait_for(lambda: len(watcher.peek_pending()) == 3, 9.0,
                  "5s drain to classify the 3 staged events")
        drained = watcher.pending_interactions()
        channels = [d.channel for d in drained]
        log(
            "DRAIN cadence drain classified 3 interactions "
            f"channels={channels} msgs={[d.msg for d in drained]} "
            f"known_avatars={len(watcher.known_avatars)} "
            f"(erik={watcher.known_avatars.uuid_for(ERIK_NAME)}, "
            f"mara={watcher.known_avatars.uuid_for(MARA_NAME)}) "
            f"history={len(watcher.history)}"
        )
        assert channels == [LOCAL_CHAT, IM, GROUP_IM]
        assert len(watcher.history) == 3

        # -- 4. forced socket drop mid-run -------------------------------
        server.drop_current()
        log("FORCED_DROP fake Core closed the event connection mid-run")
        _wait_for(lambda: len(watcher.gaps) == 1 and watcher.gaps[0].is_open,
                  5.0, "watcher to record an open GapRecord")
        _wait_for(lambda: not watcher.connected, 5.0, "watcher to notice drop")
        gap = watcher.gaps[0]
        log(
            "GAP_OPEN GapRecord opened "
            f"reason={gap.reason!r} started_at={gap.started_at:.3f} "
            f"is_open={gap.is_open}"
        )
        assert "closed by Core" in gap.reason

        # -- 5/6. reconnect + GapRecord closes ---------------------------
        _wait_for(lambda: watcher.connected and not watcher.gaps[0].is_open,
                  10.0, "watcher to reconnect and close the GapRecord")
        gap = watcher.gaps[0]
        log(
            "RECONNECTED watcher reconnected after backoff; "
            f"server_connections_accepted={server.connections_accepted}; "
            f"GapRecord reason={gap.reason!r} "
            f"started_at={gap.started_at:.3f} ended_at={gap.ended_at:.3f} "
            f"duration_s={gap.duration_s:.3f} is_open={gap.is_open}"
        )
        assert server.connections_accepted == 2
        assert gap.duration_s is not None and gap.duration_s >= 0.5
        evidence["gap"] = {
            "reason": gap.reason,
            "duration_s": gap.duration_s,
        }

        # -- post-reconnect ear check ------------------------------------
        server.push(POST_DROP_EVENT)
        _wait_for(lambda: watcher.staged_count == 1, 5.0,
                  "post-reconnect event staged")
        _wait_for(lambda: len(watcher.peek_pending()) == 1, 9.0,
                  "post-reconnect drain")
        post = watcher.pending_interactions()
        log(
            "POST_RECONNECT_DRAIN ear live after reconnect: "
            f"channel={post[0].channel} from={post[0].from_name} "
            f"msg={post[0].msg!r} history={len(watcher.history)}"
        )
        assert [p.channel for p in post] == [IM]
        assert len(watcher.history) == 4

        # -- 7. clean shutdown --------------------------------------------
        watcher.stop()
        log(
            "SHUTDOWN watcher.stop() returned; thread alive="
            f"{thread.is_alive()} connected={watcher.connected} "
            f"gaps={len(watcher.gaps)} (operator stop adds no gap) "
            f"malformed={watcher.malformed_lines} "
            f"unknown={watcher.unknown_events} "
            f"nonconversation={watcher.nonconversation_events}"
        )
        assert not thread.is_alive()
        assert len(watcher.gaps) == 1  # the forced drop only
        evidence["history"] = len(watcher.history)
        evidence["drained_channels"] = channels
        log("REHEARSAL_COMPLETE HR-002 dress rehearsal PASSED")
        return evidence
    finally:
        watcher.stop()
        server.stop()
        log("TEARDOWN fake Core stopped, socket file removed")


def _default_log_path() -> Path:
    env = os.environ.get("HEIMDALL_HR002_LOG")
    if env:
        return Path(env)
    return Path("/tmp/hr002-rehearsal.log")


def test_hr002_activation_rehearsal_on_the_bench(tmp_path):
    """HR-002: full dress rehearsal — see module docstring."""
    run_rehearsal(_default_log_path(), tmp_path / "core")


if __name__ == "__main__":
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _default_log_path()
    import tempfile

    with tempfile.TemporaryDirectory(prefix="hr002-") as td:
        run_rehearsal(log_path, Path(td) / "core")
    print(f"rehearsal log: {log_path}")
