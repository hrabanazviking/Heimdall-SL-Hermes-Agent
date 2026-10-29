"""Utterance ledger with played-prefix accounting (doc 05 §4.4).

Only audio actually played counts as *said*. When playback is
interrupted (barge-in), the ledger records exactly the played
prefix as said and the remainder as unsaid / interrupted, so the
inhabitant never "remembers" saying words nobody heard — and can
consciously choose to repeat, rephrase, or drop the thought.

Exactness contract: for every utterance, ``said_text()`` and
``unsaid_text()`` are complementary slices of the original full
text — ``said_text() + unsaid_text() == text`` always. Playback is
sequential, so the played frontier is a single offset into the
original text: the end of the last played (part of a) sentence,
including any original spacing that precedes a partially played
sentence and was therefore part of what was heard in sequence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STATUS_PENDING = "pending"
STATUS_PLAYING = "playing"
STATUS_COMPLETED = "completed"
STATUS_INTERRUPTED = "interrupted"


@dataclass
class SentenceState:
    text: str
    start: int  # offset into the utterance's full text
    end: int
    played_chars: int = 0

    @property
    def fully_played(self) -> bool:
        return self.played_chars >= len(self.text)


@dataclass
class Utterance:
    utterance_id: str
    text: str
    origin: str
    sentences: list[SentenceState] = field(default_factory=list)
    status: str = STATUS_PENDING

    @property
    def interrupted(self) -> bool:
        return self.status == STATUS_INTERRUPTED

    def _frontier(self) -> int:
        frontier = 0
        for sentence in self.sentences:
            if sentence.played_chars > 0:
                frontier = sentence.start + min(
                    sentence.played_chars, len(sentence.text)
                )
            elif frontier and sentence.start > frontier:
                break
        return frontier

    def said_text(self) -> str:
        """Exactly the played prefix of the full text."""
        return self.text[: self._frontier()]

    def unsaid_text(self) -> str:
        """Exactly the unplayed remainder of the full text."""
        return self.text[self._frontier():]


class UtteranceLedger:
    """Owns every utterance the voice pipeline has attempted."""

    def __init__(self) -> None:
        self._utterances: dict[str, Utterance] = {}
        self._counter = 0

    # -- lifecycle ------------------------------------------------------
    def start_utterance(
        self,
        text: str,
        sentences: list[str],
        *,
        origin: str = "mind",
        utterance_id: str | None = None,
    ) -> Utterance:
        self._counter += 1
        uid = utterance_id or f"utt-{self._counter}"
        states: list[SentenceState] = []
        cursor = 0
        for sentence in sentences:
            start = text.find(sentence, cursor)
            if start < 0:
                # Stripped sentence not found verbatim (unusual
                # spacing): fall back to the next non-space offset.
                while cursor < len(text) and text[cursor].isspace():
                    cursor += 1
                start = cursor
            states.append(
                SentenceState(
                    text=sentence, start=start, end=start + len(sentence)
                )
            )
            cursor = start + len(sentence)
        utterance = Utterance(
            utterance_id=uid,
            text=text,
            origin=origin,
            sentences=states,
            status=STATUS_PLAYING,
        )
        self._utterances[uid] = utterance
        return utterance

    # Aliases matching the vocabulary used around the seam.
    begin = start_utterance
    create = start_utterance

    def get(self, utterance_id: str) -> Utterance:
        return self._utterances[utterance_id]

    def __contains__(self, utterance_id: str) -> bool:
        return utterance_id in self._utterances

    def all(self) -> list[Utterance]:
        return list(self._utterances.values())

    # -- played-state recording ------------------------------------------
    def mark_sentence_played(
        self, utterance_id: str, sentence_index: int
    ) -> Utterance:
        utterance = self.get(utterance_id)
        sentence = utterance.sentences[sentence_index]
        sentence.played_chars = len(sentence.text)
        return utterance

    def mark_partial_played(
        self, utterance_id: str, sentence_index: int, played_chars: int
    ) -> Utterance:
        utterance = self.get(utterance_id)
        sentence = utterance.sentences[sentence_index]
        sentence.played_chars = max(
            0, min(int(played_chars), len(sentence.text))
        )
        return utterance

    def interrupt(
        self,
        utterance_id: str,
        sentence_index: int | None = None,
        played_chars: int | None = None,
    ) -> Utterance:
        """Record an interruption at the player-reported frontier.

        ``sentence_index`` / ``played_chars`` locate how much of the
        current sentence was actually played; sentences after it are
        unplayed by construction (playback is sequential). Calling
        this again with the same frontier is a no-op in effect, so
        the barge-in controller and the pipeline may both report the
        same interruption without double-counting.
        """
        utterance = self.get(utterance_id)
        if sentence_index is not None and played_chars is not None:
            self.mark_partial_played(
                utterance_id, sentence_index, played_chars
            )
        utterance.status = STATUS_INTERRUPTED
        return utterance

    def complete(self, utterance_id: str) -> Utterance:
        utterance = self.get(utterance_id)
        if utterance.status != STATUS_INTERRUPTED:
            utterance.status = STATUS_COMPLETED
        return utterance

    # -- exact accounting --------------------------------------------------
    def said_text(self, utterance_id: str) -> str:
        return self.get(utterance_id).said_text()

    def unsaid_text(self, utterance_id: str) -> str:
        return self.get(utterance_id).unsaid_text()
