"""Channel vocabulary for Heimdall text chat — doc 05 §4.2.

There are two genuinely different ways she speaks in text, and the
design — now Volmarr's direct order, 2026-10-31 — is that they are
never blurred:

- **Local (main) chat** is public: everyone in the place hears it.
  It travels as the Core's ``say``/``chat`` command.
- **IM** is private: one named resident, nobody else. It travels as
  the Core's ``im`` command, addressed by avatar UUID.
- **Group IM** is a third, separate room: the Core's ``group_im``
  command, addressed by group UUID.

Every send is stamped with exactly one channel, every inbound line
arrives tagged with exactly one channel, and no code path in this
package converts one channel into another. The channel strings here
are the conversation-kind strings the F2 conversation log already
uses (``local_chat`` / ``im`` / ``group_chat``), so a line's channel
means the same thing in the inbox, the log and the send result.
"""
from __future__ import annotations

from dataclasses import dataclass

LOCAL_CHAT = "local_chat"
IM = "im"
GROUP_IM = "group_chat"

CHANNELS = frozenset({LOCAL_CHAT, IM, GROUP_IM})

# Canonical seam event name -> chat channel. Events whose canonical
# name is absent here are not conversation and never enter the chat
# inbox (the watcher counts them, it does not guess at them).
EVENT_CHANNELS: dict[str, str] = {
    "chat": LOCAL_CHAT,
    "im": IM,
    "group_chat": GROUP_IM,
}


@dataclass(frozen=True)
class Interaction:
    """One inbound chat line, tagged with the channel it came on.

    An observed utterance, never a canonical fact (Phase I0
    discipline, same as the conversation log): a speaker, a text, a
    channel, and when it was said / seen.
    """

    channel: str  # LOCAL_CHAT | IM | GROUP_IM
    from_name: str | None
    from_uuid: str | None
    msg: str
    timestamp: float | None  # grid-side time, when the event carried one
    received_at: float  # watcher clock time when it was classified
    # The group a GROUP_IM line belongs to, when the event carried
    # it (HR-004: debt settlement for a group must name its group —
    # a group send may never settle another group's debt).
    group_uuid: str | None = None


class KnownAvatars:
    """Name <-> UUID bookkeeping learned from observed events.

    The Core addresses IMs by UUID and reports chat by name *and*
    UUID; this table is how the seam connects the two. It is learned
    only from events actually observed (never invented, never
    looked up off-grid), and a resident seen again under a new name
    updates the mapping — the latest observation wins.
    """

    def __init__(self) -> None:
        self._by_uuid: dict[str, str] = {}
        self._by_name: dict[str, str] = {}

    def learn(self, name: str | None, uuid: str | None) -> bool:
        """Record one observed (name, uuid) pair; True if stored."""
        if not name or not uuid:
            return False
        name, uuid = str(name), str(uuid)
        old_name = self._by_uuid.get(uuid)
        if old_name is not None and old_name != name:
            # The resident renamed: drop the stale reverse edge if
            # it still points at this UUID.
            if self._by_name.get(old_name) == uuid:
                del self._by_name[old_name]
        old_uuid = self._by_name.get(name)
        if old_uuid is not None and old_uuid != uuid:
            if self._by_uuid.get(old_uuid) == name:
                del self._by_uuid[old_uuid]
        self._by_uuid[uuid] = name
        self._by_name[name] = uuid
        return True

    def name_for(self, uuid: str | None) -> str | None:
        return self._by_uuid.get(str(uuid)) if uuid else None

    def uuid_for(self, name: str | None) -> str | None:
        return self._by_name.get(str(name)) if name else None

    def __len__(self) -> int:
        return len(self._by_uuid)

    def __contains__(self, uuid: object) -> bool:
        return uuid in self._by_uuid
