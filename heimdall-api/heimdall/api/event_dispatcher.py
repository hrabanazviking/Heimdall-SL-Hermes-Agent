"""Event dispatcher — the perception side of the seam.

Inbound Core events arrive as wire dicts (§7.2/§7.3). The dispatcher
normalises each one to a :class:`SeamEvent`, calls every handler
registered for its canonical name, and forwards it to an optional
*world sink* — the seam's downstream world-model consumer.

The sink is a duck-typed protocol matching the existing
``BifrostBridge`` surface — ``push_event(event_type, payload)`` —
NOT an import of the WYRD bridge: WYRD lives in its own repository,
and its Phase I0 ``SlWyrdBridge`` implements exactly that surface
(verified against ``secondlife_bridge.py`` on 2026-10-29; an earlier
draft of this seam assumed a single-dict ``push_event`` and was
corrected before it shipped). Keeping the seam free of a cross-repo
import means each side can be tested — and versioned — on its own,
and the two are wired together in exactly one place at deployment.

Unknown events fail closed: they land in ``dead_letters`` with their
raw wire form for inspection, and no handler or sink sees them.
Handler exceptions are isolated: one bad handler never stops the
stream, and the failure is recorded in ``handler_errors``.

Conversation events are never silently dropped (census doc 15,
slice F2 — the Phase 1 session lost a test IM because nothing was
listening when it arrived). A conversation event (local chat, 1:1
IM, group chat — :data:`CONVERSATION_EVENTS`) that arrives while NO
handler is subscribed for its name is held in a bounded per-name
buffer; the next handler to subscribe for that name receives the
buffered events first, in arrival order. The buffer is bounded for
a reason — a body that is never listened to must not grow without
limit — and overflow is LOUD, never a silent drop: the oldest event
is shed, ``buffer_overflow_count`` is incremented and the shed
event is kept in ``buffer_overflow`` for inspection.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

from ..core.models import SeamEvent

EventHandler = Callable[[SeamEvent], None]

# Canonical names that carry conversation utterances (F2). These are
# the events the mind converses through; they get the no-drop buffer.
CONVERSATION_EVENTS = frozenset({"chat", "im", "group_chat"})

DEFAULT_BUFFER_LIMIT = 1000


class WorldSink(Protocol):
    """The BifrostBridge surface: (event_type, payload), as WYRD's
    SlWyrdBridge already implements it."""

    def push_event(self, event_type: str, payload: dict[str, Any]) -> Any: ...


class EventDispatcher:
    def __init__(
        self,
        *,
        sink: WorldSink | None = None,
        conversation_buffer_limit: int = DEFAULT_BUFFER_LIMIT,
    ) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._sink = sink
        self.dead_letters: list[dict[str, Any]] = []
        self.handler_errors: list[tuple[str, Exception]] = []
        self._buffer_limit = max(1, int(conversation_buffer_limit))
        self._buffer: dict[str, deque[SeamEvent]] = {}
        # Loud overflow audit (F2): every conversation event shed
        # from a full buffer is counted here and kept for inspection.
        self.buffer_overflow_count: int = 0
        self.buffer_overflow: list[SeamEvent] = []

    def subscribe(self, canonical_name: str, handler: EventHandler) -> None:
        self._handlers.setdefault(canonical_name, []).append(handler)
        # A consumer attaching late first receives whatever arrived
        # while nobody was listening — in arrival order (F2).
        buffered = self._buffer.pop(canonical_name, None)
        if buffered:
            for event in buffered:
                try:
                    handler(event)
                except Exception as exc:  # noqa: BLE001 — isolate, as in dispatch: one bad handler must not stop replay or the stream
                    self.handler_errors.append((event.name, exc))

    # -- conversation buffer (F2) ------------------------------------
    def buffered_count(self, canonical_name: str | None = None) -> int:
        """Conversation events currently held for a late consumer."""
        if canonical_name is not None:
            held = self._buffer.get(canonical_name)
            return len(held) if held is not None else 0
        return sum(len(held) for held in self._buffer.values())

    def buffered_events(self, canonical_name: str) -> list[SeamEvent]:
        """Peek at one name's held events without consuming them."""
        held = self._buffer.get(canonical_name)
        return list(held) if held is not None else []

    def _hold(self, event: SeamEvent) -> None:
        held = self._buffer.setdefault(event.name, deque())
        if len(held) >= self._buffer_limit:
            shed = held.popleft()
            self.buffer_overflow_count += 1
            self.buffer_overflow.append(shed)
        held.append(event)

    def dispatch_wire(self, wire: dict[str, Any]) -> SeamEvent | None:
        """Dispatch one raw wire dict; None if it dead-lettered."""
        event = SeamEvent.from_wire(wire)
        if event is None:
            self.dead_letters.append(wire)
            return None
        handlers = self._handlers.get(event.name, [])
        if event.name in CONVERSATION_EVENTS and not handlers:
            # Nobody is listening: hold it for a late consumer
            # instead of dropping it on the floor (F2).
            self._hold(event)
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # isolate: the stream must go on
                self.handler_errors.append((event.name, exc))
        if self._sink is not None:
            # The sink receives the event type in the Heimdall wire
            # form the WYRD bridge's translator already speaks (§7.3
            # names), with the canonical name in the payload.
            self._sink.push_event(
                event.raw_name,
                {"canonical_event": event.name, **event.payload},
            )
        return event
