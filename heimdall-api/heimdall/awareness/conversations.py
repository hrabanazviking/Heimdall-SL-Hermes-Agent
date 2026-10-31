"""Per-thread conversation log — census doc 15 slice F2.

Every viewer keeps a Conversations surface: what was said, by whom,
in which conversation, in order, re-readable later. WYRD records
attributed utterances for the world model (Phase I0 discipline), but
until this slice the seam itself had no conversation log — an IM
that arrived while no consumer was attached was simply gone (the
Phase 1 session's lost test IM, census doc 15). This module is the
seam-side store that ends that class of loss: the runtime attaches
it to the dispatcher in every session, and the mind re-reads threads
from it.

Doc-05 / Phase I0 bounds, enforced here in the body:

- **Attributed utterances only, never canonical fact.** An entry is
  a speaker, a text, a timestamp and a direction — nothing here
  promotes chat text into a claim about the world. An utterance
  with no identifiable speaker is NOT stored: unattributed text is
  recorded in ``rejected`` instead (fail closed, auditable).
- **Outbound is recorded only after Core confirmation.** The
  command router calls :meth:`ConversationLog.record_outbound`
  only on a Core-confirmed reply — the governor's
  record-after-confirmation pattern (F1/offers) — so the log never
  claims she said something the grid never carried.
- **Per-resident purge is honoured.** :meth:`purge_resident`
  removes every entry a resident spoke and every thread held with
  that resident, and reports how many entries went.

Threads are keyed by (conversation kind, peer/group identity):
``local_chat`` (the place conversation, peer ``"local"``), ``im``
(peer = the other resident's UUID) and ``group_chat`` (peer = the
group's UUID). Group notices are not a thread kind here: the seam
vocabulary carries no group-notice event today (checked against
``core/models.py``), so there is nothing honest to key them by —
that waits on the Core leg that first emits one.

Scope honesty: this is the API/seam side of F2. Inbound entries are
built from the event forms the shipped C# Core already emits
(``from`` / ``from_uuid`` / ``msg``, plus ``timestamp`` on IMs); the
Core's IM emit does not yet carry a group/session id, so a group
chat event without one keys to the shared ``"group"`` peer until
the Core leg adds it.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core.models import SeamEvent

# Conversation kinds this log holds threads for. Anything else is
# not a conversation and fails closed at ingest.
CONVERSATION_KINDS = frozenset({"local_chat", "im", "group_chat"})

# Canonical seam event name -> conversation kind.
EVENT_KINDS: dict[str, str] = {
    "chat": "local_chat",
    "im": "im",
    "group_chat": "group_chat",
}

LOCAL_PEER = "local"
UNKNOWN_GROUP_PEER = "group"
SELF_SPEAKER = "self"


@dataclass
class Utterance:
    """One attributed utterance in one thread — never a fact."""

    speaker_id: str
    speaker_name: str | None
    text: str
    timestamp: float
    direction: str  # "in" | "out"


@dataclass
class ConversationLog:
    """The per-thread conversation store the mind can re-read."""

    _clock: Callable[[], float] = field(default=time.time)
    _threads: dict[tuple[str, str], list[Utterance]] = field(
        default_factory=dict, init=False
    )
    _peer_names: dict[tuple[str, str], str | None] = field(
        default_factory=dict, init=False
    )
    # Fail-closed audit: (reason, detail) for every refused entry.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    # -- time ---------------------------------------------------------
    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    @staticmethod
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

    # -- recording ------------------------------------------------------
    def _append(
        self,
        kind: str,
        peer_id: str,
        peer_name: str | None,
        entry: Utterance,
    ) -> Utterance:
        key = (kind, peer_id)
        self._threads.setdefault(key, []).append(entry)
        if peer_name is not None:
            self._peer_names[key] = peer_name
        elif key not in self._peer_names:
            self._peer_names[key] = None
        return entry

    def record_outbound(
        self,
        kind: str,
        *,
        peer_id: str,
        text: str,
        peer_name: str | None = None,
        now: float | None = None,
    ) -> Utterance | None:
        """Record one of the inhabitant's own utterances.

        Called by the command router ONLY after the Core confirms
        the send. Returns None (and audits in ``rejected``) for an
        unknown kind, a missing peer or an empty text — an outbound
        entry that cannot be attributed to a thread is not stored.
        """
        if kind not in CONVERSATION_KINDS:
            self.rejected.append((f"unknown conversation kind: {kind!r}", kind))
            return None
        if not peer_id or not text:
            self.rejected.append(("outbound utterance without peer or text", kind))
            return None
        return self._append(
            kind,
            str(peer_id),
            peer_name,
            Utterance(
                speaker_id=SELF_SPEAKER,
                speaker_name=None,
                text=str(text),
                timestamp=self._now(now),
                direction="out",
            ),
        )

    def ingest_event(self, event: SeamEvent) -> Utterance | None:
        """Dispatcher handler: log the utterance a seam event carries.

        Never raises — a malformed or unattributed utterance is
        recorded in ``rejected`` and returns None, so one bad event
        cannot stop the perception stream (the dispatcher's own
        isolation rule).
        """
        kind = EVENT_KINDS.get(event.name)
        if kind is None:
            self.rejected.append(("not_a_conversation_event", event.name))
            return None
        payload = event.payload
        text = payload.get("msg") or payload.get("message") or payload.get("text")
        speaker_id = payload.get("from_uuid") or payload.get("sender_id")
        speaker_name = payload.get("from") or payload.get("sender_name")
        if speaker_id is None and speaker_name is not None:
            speaker_id = str(speaker_name)
        if not text or not speaker_id:
            self.rejected.append(("unattributed_utterance", event.name))
            return None
        if kind == "local_chat":
            peer_id, peer_name = LOCAL_PEER, None
        elif kind == "im":
            peer_id, peer_name = str(speaker_id), speaker_name
        else:  # group_chat: the group is the peer, the speaker a member
            peer_id = str(
                payload.get("group_uuid")
                or payload.get("group_id")
                or payload.get("session_id")
                or UNKNOWN_GROUP_PEER
            )
            peer_name = payload.get("group_name")
        stamp = self._payload_timestamp(payload)
        return self._append(
            kind,
            peer_id,
            None if peer_name is None else str(peer_name),
            Utterance(
                speaker_id=str(speaker_id),
                speaker_name=None if speaker_name is None else str(speaker_name),
                text=str(text),
                timestamp=self._now(None) if stamp is None else stamp,
                direction="in",
            ),
        )

    # -- reading ---------------------------------------------------------
    def thread(self, kind: str, peer_id: str) -> list[Utterance]:
        """One thread's entries, chronological (arrival) order."""
        return list(self._threads.get((kind, peer_id), []))

    def threads(self) -> list[tuple[str, str]]:
        """All thread keys, (kind, peer_id), in first-seen order."""
        return list(self._threads.keys())

    def peer_name(self, kind: str, peer_id: str) -> str | None:
        return self._peer_names.get((kind, peer_id))

    def entry_count(self) -> int:
        return sum(len(entries) for entries in self._threads.values())

    # -- purge ---------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident's thread entries; return how many went.

        Every entry the resident spoke (in any thread) and every
        entry in a 1:1 thread held with that resident is removed;
        threads left empty are dropped entirely. This is the seam
        side of the per-resident purge duty (doc 01 privacy): what
        she asked to have forgotten does not linger in the log.
        """
        if not resident_id:
            return 0
        removed = 0
        for key in list(self._threads.keys()):
            kind, peer_id = key
            entries = self._threads[key]
            if kind == "im" and peer_id == resident_id:
                removed += len(entries)
                del self._threads[key]
                self._peer_names.pop(key, None)
                continue
            kept = [e for e in entries if e.speaker_id != resident_id]
            removed += len(entries) - len(kept)
            if kept:
                self._threads[key] = kept
            else:
                del self._threads[key]
                self._peer_names.pop(key, None)
        return removed
