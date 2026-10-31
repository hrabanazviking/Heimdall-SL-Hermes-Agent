"""Turn detection: speculative ear, committed mouth (doc 05 §4.3).

Turn detection is by VAD + stable-partial logic, never by a fixed
silence timeout alone. Streaming STT partials are *speculative*:
they are exposed early (so the inhabitant's turn can begin forming)
but they are not a turn. A turn is *confirmed* — and only then may
anything side-effecting (TTS, speech) happen downstream — when:

1. speech has stopped (the VAD reports inactive), **and**
2. the partial transcript has been stable (unchanged) for the whole
   confirm window, counted from the later of the last partial change
   and the moment speech stopped.

An explicit STT *final* is the recogniser's own confirmation and
confirms immediately (provided speech is not still active and the
text is non-empty); partials never bypass the stability rule.

The clock is injectable and every input may carry an explicit
timestamp, so tests drive time deterministically without sleeping.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """A confirmed inbound voice turn, flagged for the mind."""

    text: str
    channel: str = "voice"
    started_at: float | None = None
    ended_at: float | None = None
    speculative: bool = False


@dataclass(frozen=True)
class SpeculativePartial:
    """The latest unconfirmed partial — visible, never committed."""

    text: str
    at: float
    speculative: bool = True


class TurnDetector:
    """VAD + stable-partial state machine; see the module docstring."""

    def __init__(
        self,
        *,
        confirm_window: float = 0.6,
        clock: Callable[[], float] | None = None,
        on_turn: Callable[[Turn], None] | None = None,
    ) -> None:
        if confirm_window < 0:
            raise ValueError("confirm_window must be >= 0")
        self.confirm_window = float(confirm_window)
        self._clock = clock or time.monotonic
        self._on_turn = on_turn
        self._speech_active = False
        self._speech_seen = False
        self._partial = ""
        self._partial_changed_at: float | None = None
        self._speech_stopped_at: float | None = None
        self._started_at: float | None = None

    # -- inputs ---------------------------------------------------------
    def feed_speech(
        self, active: bool, *, at: float | None = None
    ) -> Turn | None:
        now = self._now(at)
        if active:
            self._speech_seen = True
            if not self._speech_active and self._started_at is None:
                self._started_at = now
            self._speech_active = True
            self._speech_stopped_at = None
            return None
        if self._speech_active:
            self._speech_stopped_at = now
        self._speech_active = False
        return self.poll(at=now)

    def feed_partial(
        self, text: str, *, at: float | None = None
    ) -> Turn | None:
        now = self._now(at)
        text = text or ""
        if self._started_at is None and (text or self._speech_active):
            self._started_at = now
        if text != self._partial:
            self._partial = text
            self._partial_changed_at = now
        return self.poll(at=now)

    def feed_final(
        self, text: str, *, at: float | None = None
    ) -> Turn | None:
        """An STT final: the recogniser's own end-of-turn mark."""
        now = self._now(at)
        text = text or ""
        if self._started_at is None:
            self._started_at = now
        if text != self._partial:
            self._partial = text
            self._partial_changed_at = now
        if self._speech_active:
            # Speech is still sounding: a final cannot confirm yet.
            return None
        if not self._partial.strip():
            self._reset()
            return None
        return self._confirm(now)

    # -- confirmation -----------------------------------------------------
    def poll(self, *, at: float | None = None) -> Turn | None:
        now = self._now(at)
        if self._speech_active or not self._partial.strip():
            return None
        if self._partial_changed_at is None:
            return None
        stable_since = self._partial_changed_at
        if self._speech_seen:
            if self._speech_stopped_at is None:
                return None  # speech happened and has not stopped
            stable_since = max(stable_since, self._speech_stopped_at)
        if now - stable_since >= self.confirm_window:
            return self._confirm(now)
        return None

    # -- speculative view ---------------------------------------------------
    @property
    def speculative(self) -> SpeculativePartial | None:
        """The current unconfirmed partial, or ``None``."""
        if not self._partial:
            return None
        return SpeculativePartial(
            text=self._partial, at=self._partial_changed_at or 0.0
        )

    def speculative_text(self) -> str:
        return self._partial

    @property
    def speech_active(self) -> bool:
        return self._speech_active

    # -- internals -----------------------------------------------------------
    def _now(self, at: float | None) -> float:
        return float(at) if at is not None else float(self._clock())

    def _confirm(self, now: float) -> Turn:
        turn = Turn(
            text=self._partial,
            channel="voice",
            started_at=self._started_at,
            ended_at=now,
            speculative=False,
        )
        self._reset()
        if self._on_turn is not None:
            self._on_turn(turn)
        return turn

    def _reset(self) -> None:
        self._speech_active = False
        self._speech_seen = False
        self._partial = ""
        self._partial_changed_at = None
        self._speech_stopped_at = None
        self._started_at = None
