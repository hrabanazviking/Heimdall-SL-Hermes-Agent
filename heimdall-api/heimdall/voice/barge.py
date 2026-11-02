"""Backchannels and barge-in (doc 05 §4.4).

Not every sound during playback is an interruption. Coughs,
"mm-hm", and laughter are *backchannels* — listening sounds that
must NOT cancel her. True speech is a *barge*: the reflex layer
cancels in-flight TTS and flushes buffered audio fast, and the
current utterance is marked interrupted in the ledger with exactly
the player-reported played amount (played-prefix accounting lives
in :mod:`heimdall.voice.ledger`).

Classification errs toward yielding to him: a sound is only a
backchannel when it is both short and made solely of non-lexical
backchannel material (or is a short transcript-less noise such as
a cough). Anything else — any real word, or backchannel material
held implausibly long — is a barge.
"""
from __future__ import annotations

import re
from typing import Any

BACKCHANNEL = "backchannel"
BARGE_IN = "barge_in"

_BACKCHANNEL_TOKENS = frozenset({
    "mm", "mhm", "hm", "hmm", "uh", "huh", "ah", "oh",
    "ha", "heh", "hehe", "haha", "hahaha",
    "cough", "coughs", "laugh", "laughs", "laughter",
    "snort", "chuckle", "sigh",
})

_TOKEN_RE = re.compile(r"[a-z]+")


class BackchannelClassifier:
    """Classify a sound heard during playback by duration + text."""

    def __init__(
        self,
        *,
        max_backchannel_duration: float = 1.2,
        max_silent_noise_duration: float = 0.7,
    ) -> None:
        self.max_backchannel_duration = float(max_backchannel_duration)
        self.max_silent_noise_duration = float(max_silent_noise_duration)

    def classify(self, duration_s: float, transcript: str | None) -> str:
        duration = max(0.0, float(duration_s))
        tokens = _TOKEN_RE.findall((transcript or "").lower())
        if not tokens:
            # A cough / throat-clear / laugh with no transcript at
            # all: a backchannel only while it stays short.
            if duration <= self.max_silent_noise_duration:
                return BACKCHANNEL
            return BARGE_IN
        if (
            all(token in _BACKCHANNEL_TOKENS for token in tokens)
            and duration <= self.max_backchannel_duration
        ):
            return BACKCHANNEL
        return BARGE_IN

    def is_backchannel(self, duration_s: float, transcript: str | None) -> bool:
        return self.classify(duration_s, transcript) == BACKCHANNEL

    def is_barge(self, duration_s: float, transcript: str | None) -> bool:
        return self.classify(duration_s, transcript) == BARGE_IN


def _played_report(player: Any) -> tuple[int | None, int | None]:
    """Read the player's played frontier, in its offered shapes.

    Accepted: ``current_played()`` returning ``(index, chars)``, a
    mapping with ``sentence_index`` / ``played_chars``, a bare int
    (chars in the current sentence), or ``None`` (no report).
    """
    reporter = getattr(player, "current_played", None)
    if not callable(reporter):
        return None, None
    report = reporter()
    if report is None:
        return None, None
    if isinstance(report, dict):
        return report.get("sentence_index"), report.get("played_chars")
    if isinstance(report, (tuple, list)) and len(report) == 2:
        return report[0], report[1]
    if isinstance(report, int):
        return None, report
    return None, None


class BargeInController:
    """On a true barge: cancel, flush, and ledger the interruption."""

    def __init__(
        self,
        player: Any,
        ledger: Any,
        classifier: BackchannelClassifier | None = None,
    ) -> None:
        self.player = player
        self.ledger = ledger
        self.classifier = classifier or BackchannelClassifier()
        self.current_utterance_id: str | None = None
        self.current_sentence_index: int | None = None
        self.barge_count = 0
        self.last_barge_utterance_id: str | None = None

    def set_current(
        self, utterance_id: str | None, sentence_index: int | None = None
    ) -> None:
        self.current_utterance_id = utterance_id
        self.current_sentence_index = sentence_index

    def clear_current(self) -> None:
        self.set_current(None, None)

    def handle_sound(
        self,
        duration_s: float,
        transcript: str | None,
        *,
        utterance_id: str | None = None,
    ) -> bool:
        """Process one sound during playback; True iff it barged."""
        if not self.classifier.is_barge(duration_s, transcript):
            return False
        uid = utterance_id or self.current_utterance_id
        index, chars = _played_report(self.player)
        if index is None:
            index = self.current_sentence_index
        cancel = getattr(self.player, "cancel", None)
        if callable(cancel):
            cancel()
        flush = getattr(self.player, "flush", None)
        if callable(flush):
            flush()
        if uid is not None and self.ledger is not None:
            if index is not None and chars is not None:
                self.ledger.interrupt(uid, index, chars)
            elif index is not None:
                self.ledger.interrupt(uid, index, 0)
            else:
                self.ledger.interrupt(uid)
        self.barge_count += 1
        self.last_barge_utterance_id = uid
        return True

    # Aliases in the vocabulary of the design doc.
    notify_sound = handle_sound
    on_sound = handle_sound
