"""Incremental sentence chunker for sentence-streamed TTS (doc 05 §4.3).

Her reply is synthesised sentence by sentence: sentence *n* plays
while sentence *n+1* renders. The catalog renderer produces
whole-utterance files, so this splitter is what makes the loop
conversational — and its first emitted sentence must be exactly one
sentence, never the whole reply.

The chunker is incremental: :meth:`SentenceChunker.feed` accepts
arbitrary text fragments (as a streaming mind produces them) and
returns the sentences that *closed* within the buffered text.
A sentence whose terminator sits at the very end of the buffer is
held, not emitted: the next fragment may still prove it was a
decimal point (``3.`` + ``5``), add a closing quote, or continue
the sentence. :meth:`SentenceChunker.flush` settles whatever
remains at end-of-stream and returns it verbatim (stripped).

Laws kept here:

- A decimal point between digits never splits (``3.5`` stays whole).
- Terminators are ``. ! ? …`` (``...`` counts as one terminator),
  and closing quotes / brackets belong to the sentence they close
  (``"Hello."`` is one sentence, quote included).
- A terminator only closes a sentence when followed by whitespace
  (or settled by :meth:`flush` at end-of-stream).
"""
from __future__ import annotations

TERMINATORS = frozenset(".!?…")
CLOSERS = frozenset("\"'”’»›)]}")


class SentenceChunker:
    """Streaming sentence splitter; see the module docstring."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, fragment: str) -> list[str]:
        """Add a fragment; return the sentences it completed."""
        if fragment:
            self._buffer += fragment
        out: list[str] = []
        while True:
            end = self._boundary(self._buffer)
            if end is None:
                return out
            sentence = self._buffer[:end].strip()
            self._buffer = self._buffer[end:].lstrip()
            if sentence:
                out.append(sentence)

    def flush(self) -> str:
        """Return the un-emitted remainder and clear the buffer."""
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder

    @property
    def pending(self) -> str:
        return self._buffer

    @staticmethod
    def _boundary(text: str) -> int | None:
        """Index just past the first closed sentence, or ``None``.

        ``None`` means no sentence in *text* is provably closed yet
        (more input — or a flush — is needed to decide).
        """
        i = 0
        n = len(text)
        while i < n:
            char = text[i]
            if char not in TERMINATORS:
                i += 1
                continue
            if char == ".":
                prev = text[i - 1] if i > 0 else ""
                nxt = text[i + 1] if i + 1 < n else None
                if prev.isdigit() and nxt is not None and nxt.isdigit():
                    i += 1  # decimal point inside a number
                    continue
                if prev.isdigit() and nxt is None:
                    return None  # may become a decimal next fragment
            # Consume the whole terminator cluster ("...", "?!").
            j = i
            while j < n and text[j] in TERMINATORS:
                j += 1
            if j >= n:
                return None  # cluster at buffer end: not settled yet
            # Closing quotes/brackets belong to this sentence.
            k = j
            while k < n and text[k] in CLOSERS:
                k += 1
            if k >= n:
                return None  # closers at buffer end: not settled yet
            if text[k].isspace():
                return k
            # Terminator glued to more text (e.g. "e.g.x"): no split.
            i = j
        return None


def chunk_text(text: str) -> list[str]:
    """Split a complete text into sentences in one shot."""
    chunker = SentenceChunker()
    sentences = chunker.feed(text)
    tail = chunker.flush()
    if tail:
        sentences.append(tail)
    return sentences
