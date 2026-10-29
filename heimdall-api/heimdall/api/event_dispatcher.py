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
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from ..core.models import SeamEvent

EventHandler = Callable[[SeamEvent], None]


class WorldSink(Protocol):
    """The BifrostBridge surface: (event_type, payload), as WYRD's
    SlWyrdBridge already implements it."""

    def push_event(self, event_type: str, payload: dict[str, Any]) -> Any: ...


class EventDispatcher:
    def __init__(self, *, sink: WorldSink | None = None) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._sink = sink
        self.dead_letters: list[dict[str, Any]] = []
        self.handler_errors: list[tuple[str, Exception]] = []

    def subscribe(self, canonical_name: str, handler: EventHandler) -> None:
        self._handlers.setdefault(canonical_name, []).append(handler)

    def dispatch_wire(self, wire: dict[str, Any]) -> SeamEvent | None:
        """Dispatch one raw wire dict; None if it dead-lettered."""
        event = SeamEvent.from_wire(wire)
        if event is None:
            self.dead_letters.append(wire)
            return None
        for handler in self._handlers.get(event.name, []):
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
