"""Inbound chat watcher — the standing ear with a 5-second check.

Volmarr's order (2026-10-31, attended session): *"create a thing
that checks every five seconds for if there's any interactions
happening both in main chat or in the IMs as well."*

Why a standing watcher is needed at all: the Core's event socket
pushes events only to consumers connected at that moment — an event
that fires while nobody is listening is gone (diagnosed 2026-10-30;
the Phase 1 session's lost test IM is the famous case). The F2
dispatcher/conversation log can only hold what actually reaches
them. This watcher is the always-attached consumer whose one job is
that nothing conversational falls on the floor unnoticed:

- It keeps a standing connection to the Core event socket and
  stages every line it receives.
- On a fixed cadence — :data:`CHECK_INTERVAL_S`, **5 seconds**,
  the interval Volmarr named — it drains the staging area into a
  channel-tagged inbox: each entry an :class:`Interaction` stamped
  LOCAL_CHAT, IM or GROUP_IM, with speaker name/UUID, text and time.
- The mind asks :meth:`ChatWatcher.pending_interactions` for what
  is new; asking consumes, so an interaction is handed over once.
  Everything classified is also kept in ``history`` for audit.
- Every drained line is stamped with its HR-005 addressed-to
  classification (``direct`` / ``named`` / ``contextual`` /
  ``ambient``; rules and the exchange-state machine live in
  :mod:`heimdall.chat.classification`). The stamp is set here, at
  classification time, from the line's arrival time.
- HR-006: when an Aurelia plug is wired (``aurelia_feed``), every
  classified interaction is *also* fed to Aurelia — after it has
  landed in ``history``/inbox — under strict failure isolation:
  a plug failure is logged and swallowed here as well as inside
  the plug itself, so Aurelia can never block the mind feed.
- Speakers are learned into :class:`KnownAvatars` as a side effect
  of observation — name <-> UUID pairs from real events only.
- When the events socket drops, the watcher reconnects with
  exponential backoff and records a :class:`GapRecord`. Events that
  fired during an outage are **lost, and the watcher says so**: a
  gap is a visible hole in the record, never papered over with
  fabricated interactions.

The cadence is a drain cadence, not a poll of the grid: the socket
is push, so lines are read as they arrive and *classified* every
5 s. Malformed lines and unknown event names fail closed — counted
(``malformed_lines`` / ``unknown_events``), never guessed into the
inbox. Non-conversation events are counted and passed over; this
watcher is the chat ear, not the whole perception stream (the
dispatcher/runtime own the rest).

Threading: :meth:`ChatWatcher.step` is the whole state machine in
one synchronous call (clock and socket factory are injectable, so
tests drive it deterministically with fakes — no live socket, no
real waiting). :meth:`start`/:meth:`stop` merely run ``step`` on a
daemon thread for deployment. Standard library only.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..core.ipc_client import DEFAULT_EVENT_SOCKET
from ..core.models import SeamEvent, parse_ndjson_line
from .channels import EVENT_CHANNELS, Interaction, KnownAvatars
from .classification import (
    DEFAULT_SELF_NAME,
    EXCHANGE_INACTIVITY_WINDOW_S,
    ExchangeTracker,
)
from .debt import find_settlement_candidate

logger = logging.getLogger(__name__)

# The check cadence Volmarr ordered: drain and classify every 5 s.
CHECK_INTERVAL_S = 5.0

# Reconnect backoff after the event socket drops: patient, bounded,
# and always recorded as a gap (see module docstring).
RECONNECT_BACKOFF_INITIAL_S = 1.0
RECONNECT_BACKOFF_MULTIPLIER = 2.0
RECONNECT_BACKOFF_MAX_S = 30.0

_RECV_SIZE = 65536
_MAX_CHUNKS_PER_STEP = 64
_POLL_SLEEP_S = 0.05
_SOCKET_TIMEOUT_S = 0.25


@dataclass
class GapRecord:
    """One interval where the ear was off the event socket.

    Events fired during a gap are lost; the record is how the loss
    stays honest and visible instead of looking like silence.
    """

    started_at: float
    reason: str
    ended_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def duration_s(self) -> float | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at


def _payload_timestamp(payload: dict[str, Any]) -> float | None:
    raw = payload.get("timestamp")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return None
    return None


class ChatWatcher:
    """Standing consumer of the Core event socket; see module docs."""

    def __init__(
        self,
        event_socket: str = DEFAULT_EVENT_SOCKET,
        *,
        socket_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        check_interval_s: float = CHECK_INTERVAL_S,
        self_name: str = DEFAULT_SELF_NAME,
        exchange_window_s: float = EXCHANGE_INACTIVITY_WINDOW_S,
        aurelia_feed: Any = None,
    ) -> None:
        self.event_socket = event_socket
        self.check_interval_s = float(check_interval_s)
        # HR-005 addressed-to classification configuration: whose
        # name makes a line ``named``, and how long an exchange
        # stays open without a refreshing line (see
        # heimdall.chat.classification for the rules).
        self.self_name = str(self_name)
        self.exchange_window_s = float(exchange_window_s)
        self.exchange_tracker = ExchangeTracker(
            self_name=self.self_name, window_s=self.exchange_window_s
        )
        # HR-006 Aurelia dual feed (optional plug): an object with
        # a ``feed(interaction)`` method — the AureliaFeed in
        # heimdall.chat.aurelia — or any callable taking the
        # interaction. None (the default) = no feed, and the
        # watcher behaves exactly as before HR-006.
        self.aurelia_feed = aurelia_feed
        self._clock = clock
        self._socket_factory = socket_factory or self._default_factory

        self._sock: Any = None
        self._buffer = b""
        self._staged: list[tuple[SeamEvent, float]] = []
        self._inbox: list[Interaction] = []
        self.history: list[Interaction] = []
        # HR-004: indices into ``history`` whose debt a
        # Core-confirmed send has settled (see
        # heimdall.chat.debt for the matching law). History itself
        # is append-only, so an index is a stable identity — two
        # textually identical interactions are two distinct debts.
        self._settled: set[int] = set()
        self.known_avatars = KnownAvatars()
        self.gaps: list[GapRecord] = []
        # Fail-closed audit counters — nothing is silently dropped.
        self.malformed_lines = 0
        self.unknown_events = 0
        self.unattributed_events = 0
        self.nonconversation_events = 0

        self._backoff_s = RECONNECT_BACKOFF_INITIAL_S
        self._reconnect_at = 0.0  # first step connects immediately
        self._next_drain_at = self._clock() + self.check_interval_s

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- connection -------------------------------------------------
    def _default_factory(self) -> Any:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(_SOCKET_TIMEOUT_S)
        sock.connect(self.event_socket)
        return sock

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def _open_gap(self, now: float, reason: str) -> None:
        # One open gap *record* at a time — the hole is already
        # named — but the retry schedule must advance on every
        # failure, or backoff would never grow while a gap is open.
        if not (self.gaps and self.gaps[-1].is_open):
            self.gaps.append(GapRecord(started_at=now, reason=reason))
        self._reconnect_at = now + self._backoff_s
        self._backoff_s = min(
            RECONNECT_BACKOFF_MAX_S,
            self._backoff_s * RECONNECT_BACKOFF_MULTIPLIER,
        )

    def _drop_socket(self, now: float, reason: str) -> None:
        sock, self._sock = self._sock, None
        self._buffer = b""
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001 — closing a dead socket must not raise into the watch loop
                pass
        self._open_gap(now, reason)

    def _try_connect(self, now: float) -> None:
        try:
            sock = self._socket_factory()
        except OSError as exc:
            self._open_gap(now, f"event socket connect failed: {exc}")
            return
        self._sock = sock
        self._buffer = b""
        self._backoff_s = RECONNECT_BACKOFF_INITIAL_S
        if self.gaps and self.gaps[-1].is_open:
            self.gaps[-1].ended_at = now

    # -- reading ------------------------------------------------------
    def _read_available(self, now: float) -> None:
        for _ in range(_MAX_CHUNKS_PER_STEP):
            try:
                chunk = self._sock.recv(_RECV_SIZE)
            except (socket.timeout, TimeoutError):
                return  # nothing more right now — the normal case
            except OSError as exc:
                self._drop_socket(now, f"event socket read failed: {exc}")
                return
            if not chunk:
                self._drop_socket(now, "event socket closed by Core")
                return
            self._buffer += chunk
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                self._stage_line(line, now)

    def _stage_line(self, line: bytes, now: float) -> None:
        wire = parse_ndjson_line(line)
        if wire is None:
            if line.strip():
                self.malformed_lines += 1
            return
        event = SeamEvent.from_wire(wire)
        if event is None:
            self.unknown_events += 1
            return
        self._staged.append((event, now))

    # -- the 5-second drain -------------------------------------------
    def drain(self, now: float | None = None) -> list[Interaction]:
        """Classify everything staged into the channel-tagged inbox."""
        now = self._clock() if now is None else now
        drained: list[Interaction] = []
        staged, self._staged = self._staged, []
        for event, arrived_at in staged:
            channel = EVENT_CHANNELS.get(event.name)
            if channel is None:
                self.nonconversation_events += 1
                continue
            payload = event.payload
            text = (
                payload.get("msg")
                or payload.get("message")
                or payload.get("text")
            )
            name = payload.get("from") or payload.get("sender_name")
            uuid = payload.get("from_uuid") or payload.get("sender_id")
            if not text or (not name and not uuid):
                # A conversation event we cannot attribute is not
                # an interaction — counted, never fabricated.
                self.unattributed_events += 1
                continue
            self.known_avatars.learn(name, uuid)
            group_uuid = payload.get("group_uuid") or payload.get("group_id")
            from_name = None if name is None else str(name)
            from_uuid = None if uuid is None else str(uuid)
            group_uuid = None if group_uuid is None else str(group_uuid)
            # HR-005: stamp addressed-to at classification time.
            # The tracker's clock is the line's arrival time, so
            # exchange expiry follows when lines were said, not
            # when this drain happened to run.
            classification = self.exchange_tracker.classify(
                channel=channel,
                msg=str(text),
                from_uuid=from_uuid,
                from_name=from_name,
                group_uuid=group_uuid,
                now=arrived_at,
            )
            entry = Interaction(
                channel=channel,
                from_name=from_name,
                from_uuid=from_uuid,
                msg=str(text),
                timestamp=_payload_timestamp(payload),
                received_at=arrived_at,
                group_uuid=group_uuid,
                classification=classification,
            )
            self.history.append(entry)
            self._inbox.append(entry)
            drained.append(entry)
            # HR-006: the dual feed fires only after the entry
            # has landed in the record, and is failure-isolated
            # (see _feed_aurelia) — Aurelia is never a gate.
            self._feed_aurelia(entry)
        return drained

    def _feed_aurelia(self, entry: Interaction) -> None:
        """Hand one classified interaction to the Aurelia plug.

        Never raises, whatever the plug does: the plug's own law
        (heimdall.chat.aurelia) is already to swallow its
        failures, and this wrapper is the second layer, so even
        a plug that violates that contract — or is not a plug at
        all — is logged and swallowed instead of breaking the
        drain for this or any later interaction.
        """
        plug = self.aurelia_feed
        if plug is None:
            return
        try:
            feed = getattr(plug, "feed", None)
            if callable(feed):
                feed(entry)
            elif callable(plug):
                plug(entry)
            else:
                logger.warning(
                    "Aurelia feed: plug %r has no feed() and is not "
                    "callable; interaction not posted",
                    plug,
                )
        except Exception as exc:  # noqa: BLE001 — isolation law: Aurelia must never raise into the watcher
            logger.warning(
                "Aurelia feed: plug raised for %s interaction; "
                "swallowed, mind feed unaffected: %s",
                entry.channel,
                exc,
            )

    # -- state machine --------------------------------------------------
    def step(self) -> None:
        """One watcher beat: (re)connect, read, drain when due."""
        now = self._clock()
        if self._sock is None and now >= self._reconnect_at:
            self._try_connect(now)
        if self._sock is not None:
            self._read_available(now)
        if now >= self._next_drain_at:
            self.drain(now)
            self._next_drain_at = now + self.check_interval_s

    # -- the mind's surface ---------------------------------------------
    def pending_interactions(self) -> list[Interaction]:
        """New interactions since the last call (asking consumes)."""
        pending, self._inbox = self._inbox, []
        return pending

    def peek_pending(self) -> list[Interaction]:
        """The pending interactions, without consuming them."""
        return list(self._inbox)

    # -- the debt surface (HR-004) ---------------------------------------
    def unanswered_interactions(
        self, channel: str | None = None
    ) -> list[Interaction]:
        """What is owed, oldest first — asking settles/consumes nothing.

        The first-class unanswered-set query (HR-004): derived from
        the conversation record (``history``) minus the debts a
        Core-confirmed send has settled. Unlike
        :meth:`pending_interactions` (a consuming handoff), this can
        be asked repeatedly and returns the same set until a verified
        send settles part of it — handing an interaction to a mind
        does not answer it, and consuming the inbox changes nothing
        here.
        """
        return [
            entry
            for idx, entry in enumerate(self.history)
            if idx not in self._settled
            and (channel is None or entry.channel == channel)
        ]

    def record_send_result(self, result: Any) -> Interaction | None:
        """Settle one debt from a send result; None when nothing settles.

        The settlement door (HR-004, receipts law): ``result`` is a
        :class:`~heimdall.chat.service.SendResult` (duck-typed: it
        needs ``ok``, ``channel`` and ``target_uuid``). A result
        with ``ok`` False — blocked, refused, governor-denied —
        settles NOTHING and returns None. A confirmed send settles
        exactly the oldest matching owed interaction under the
        channel-exact, target-exact rules in
        :mod:`heimdall.chat.debt`; when nothing matches, the send
        settles nothing and earns no credit against future debt.
        Wire it as ``ChatService(..., on_send=watcher.record_send_result)``
        or call it with each result by hand.
        """
        if not getattr(result, "ok", False):
            return None
        owed = self.unanswered_interactions()
        candidate = find_settlement_candidate(
            owed,
            channel=getattr(result, "channel", None),
            target_uuid=getattr(result, "target_uuid", None),
        )
        if candidate is None:
            return None
        # Identity is position in the record, not text: settle the
        # exact history slot the candidate came from.
        for idx, entry in enumerate(self.history):
            if idx not in self._settled and entry is candidate:
                self._settled.add(idx)
                return entry
        return None  # pragma: no cover — candidate came from history

    @property
    def staged_count(self) -> int:
        """Lines read but not yet classified (pre-drain)."""
        return len(self._staged)

    # -- deployment loop --------------------------------------------------
    def run(self, stop_event: threading.Event | None = None) -> None:
        stop = self._stop if stop_event is None else stop_event
        while not stop.is_set():
            self.step()
            stop.wait(_POLL_SLEEP_S)

    def start(self) -> threading.Thread:
        """Run the watcher on a daemon thread; returns the thread."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, name="heimdall-chat-watcher", daemon=True
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        # An operator stop is not a coverage gap: close quietly,
        # without touching the gap record.
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001 — shutdown must not raise
                pass
