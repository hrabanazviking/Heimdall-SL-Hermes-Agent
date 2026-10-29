"""Voice pipeline orchestrator — the out-of-world voice loop.

Phase 4 slice 1 (voice sidecar core): the plumbing of doc 05 §4.2's
first road — cascaded streaming STT → mind → catalog-cast TTS —
with every §4.3 / §4.4 law enforced in code, not by convention:

- **Speculative ear, committed mouth.** Inbound partials flow into
  the turn detector and are visible as speculative partials, but
  nothing renders or plays until a turn is *confirmed*; a confirmed
  turn is delivered to the mind callback flagged ``channel="voice"``.
- **Reflex tiers never speak.** :meth:`VoicePipeline.speak` accepts
  utterances only with ``origin="mind"``; any other origin (reflex,
  body, a stranger's prompt) is blocked before any render or play.
- **Kill switch blocks speech.** When a governor is provided and
  its kill switch is engaged, speech is blocked outright.
- **Sentence-by-sentence.** A reply is chunked into sentences; the
  first render call carries exactly one sentence, never the whole
  reply; each sentence renders, plays, and is ledgered in order.
- **Played-prefix accounting.** The player reports how much of each
  sentence actually played; the ledger records exactly that as
  said. A barge-in mid-play (the player may report sounds through
  the ``on_sound`` callback given to it) cancels playback through
  the barge-in controller and stops the utterance: the played
  prefix is said, the rest is unsaid / interrupted.

Protocols (duck-typed, matching the seam's synchronous style):

- *TTS renderer*: ``render(text) -> audio`` (one sentence at a
  time). :class:`heimdall.voice.renderer.CatalogTtsRenderer` is the
  production implementation.
- *Audio player*: ``play(text, audio, utterance_id=, sentence_index=,
  on_sound=) -> played_chars`` (``None`` means the whole sentence),
  plus ``cancel()``, ``flush()``, and optionally
  ``current_played() -> (sentence_index, played_chars)`` for the
  barge-in controller's played report.
- *Mind callback*: ``callable(Turn)`` receiving confirmed turns.
- *Governor*: any object with a ``kill_switch_engaged`` truth value
  (the seam's ``MessageGovernor`` qualifies).

Standard library only, Python ≥3.11, like the rest of the seam.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from .barge import BackchannelClassifier, BargeInController
from .chunker import chunk_text
from .ledger import UtteranceLedger
from .turns import Turn, TurnDetector

REASON_NON_MIND = "non_mind_origin_may_not_speak"
REASON_KILL_SWITCH = "kill_switch_engaged"
REASON_EMPTY = "empty_text"


@dataclass
class SpeakResult:
    allowed: bool
    reason: str = ""
    utterance_id: str | None = None
    said_text: str = ""
    unsaid_text: str = ""
    rendered_sentences: list[str] = field(default_factory=list)
    interrupted: bool = False


class VoicePipeline:
    """Out-of-world voice loop plumbing; see the module docstring."""

    def __init__(
        self,
        *,
        renderer: Any,
        player: Any,
        mind_callback: Any = None,
        governor: Any = None,
        ledger: UtteranceLedger | None = None,
        detector: TurnDetector | None = None,
        classifier: BackchannelClassifier | None = None,
    ) -> None:
        self.renderer = renderer
        self.player = player
        self.mind_callback = mind_callback
        self.governor = governor
        self.ledger = ledger or UtteranceLedger()
        self.detector = detector or TurnDetector()
        self.barge = BargeInController(
            player, self.ledger, classifier or BackchannelClassifier()
        )

    # -- inbound: speculative ear --------------------------------------
    def feed_speech(
        self, active: bool, *, at: float | None = None
    ) -> Turn | None:
        return self._deliver(self.detector.feed_speech(active, at=at))

    def feed_partial(
        self, text: str, *, at: float | None = None
    ) -> Turn | None:
        return self._deliver(self.detector.feed_partial(text, at=at))

    def feed_final(
        self, text: str, *, at: float | None = None
    ) -> Turn | None:
        return self._deliver(self.detector.feed_final(text, at=at))

    def poll(self, *, at: float | None = None) -> Turn | None:
        return self._deliver(self.detector.poll(at=at))

    @property
    def speculative(self):
        return self.detector.speculative

    def _deliver(self, turn: Turn | None) -> Turn | None:
        if turn is not None and self.mind_callback is not None:
            self.mind_callback(turn)
        return turn

    # -- sounds during playback -----------------------------------------
    def handle_sound(
        self, duration_s: float, transcript: str | None
    ) -> bool:
        """Route a playback-time sound through barge-in control."""
        return self.barge.handle_sound(duration_s, transcript)

    on_sound = handle_sound

    # -- outbound: committed mouth ---------------------------------------
    def speak(self, text: str, *, origin: str = "mind") -> SpeakResult:
        if origin != "mind":
            # Reflex tiers never speak (doc 05): only the inhabitant
            # herself — origin "mind" — may produce voice.
            return SpeakResult(False, REASON_NON_MIND)
        if self.governor is not None and getattr(
            self.governor, "kill_switch_engaged", False
        ):
            return SpeakResult(False, REASON_KILL_SWITCH)
        sentences = chunk_text(text or "")
        if not sentences:
            return SpeakResult(False, REASON_EMPTY)

        utterance = self.ledger.start_utterance(
            text, sentences, origin=origin
        )
        uid = utterance.utterance_id
        rendered: list[str] = []
        try:
            for index, sentence in enumerate(sentences):
                # One sentence per render — the first render is a
                # single sentence, never the whole reply.
                audio = self.renderer.render(sentence)
                rendered.append(sentence)
                self.barge.set_current(uid, index)
                played = self._play(sentence, audio, uid, index)
                if utterance.interrupted:
                    # The barge-in controller already ledgered the
                    # player-reported played frontier.
                    break
                if played is None or played >= len(sentence):
                    self.ledger.mark_sentence_played(uid, index)
                else:
                    self.ledger.interrupt(uid, index, int(played))
                    break
            else:
                self.ledger.complete(uid)
        finally:
            self.barge.clear_current()
        return SpeakResult(
            True,
            "",
            utterance_id=uid,
            said_text=self.ledger.said_text(uid),
            unsaid_text=self.ledger.unsaid_text(uid),
            rendered_sentences=rendered,
            interrupted=utterance.interrupted,
        )

    def _play(
        self, sentence: str, audio: Any, uid: str, index: int
    ) -> int | None:
        play = self.player.play
        try:
            params = inspect.signature(play).parameters
        except (TypeError, ValueError):
            params = {}
        if "on_sound" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD
            for p in params.values()
        ):
            return play(
                sentence,
                audio,
                utterance_id=uid,
                sentence_index=index,
                on_sound=self.handle_sound,
            )
        # A minimal player accepting only (text, audio).
        return play(sentence, audio)
