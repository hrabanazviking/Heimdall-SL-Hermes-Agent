"""Unanswered-set matching rules — HR-004 (responsiveness Part 0).

The debt view itself lives on :class:`~heimdall.chat.watcher.ChatWatcher`
(``unanswered_interactions()`` / ``record_send_result()``); this
module holds the *settlement matching rules* as pure functions so
they are explicit, documented in one place, and testable without a
watcher, a socket, or a grid.

THE MATCHING RULES (conservative — when in doubt, do NOT settle):

1. **Receipts law.** Only a Core-confirmed send can settle anything:
   the send result must have ``ok`` True. A blocked, refused, or
   governor-denied send (``ok`` False) settles NOTHING, ever.
2. **Channel-exact.** A send settles debt only on its own channel:
   an IM send settles IM debt, a local ``say`` settles local-chat
   debt, a ``group_im`` settles group-chat debt. Never cross-channel,
   even when the same resident is involved on both channels.
3. **Target-exact where the channel has a target.**
   - IM: the send's recipient UUID must equal the owed
     interaction's ``from_uuid``.
   - Group IM: the send's group UUID must equal the owed
     interaction's ``group_uuid``.
   - Local chat has no target (it is public speech in a place), so
     a local send matches local debt on channel alone.
   A targeted channel with a missing/unknown target on *either*
   side settles nothing: an IM debt whose sender UUID is unknown,
   or a group debt whose group is unknown, cannot be settled.
4. **One send, one debt, oldest first.** A single confirmed send
   settles exactly the OLDEST matching owed interaction (FIFO).
   It never clears a whole backlog, and a send made before a debt
   exists earns no credit against future debt.
5. **Identity is position, not text.** Debts are tracked by their
   place in the conversation record, never by message text — two
   identical "test" IMs are two debts, and one reply settles one
   of them (the F4 lesson from the interim hook).
"""
from __future__ import annotations

from collections.abc import Sequence

from .channels import GROUP_IM, IM, LOCAL_CHAT, Interaction


def matches_settlement(
    interaction: Interaction,
    *,
    channel: str,
    target_uuid: str | None,
) -> bool:
    """True when a confirmed send on ``channel`` may settle ``interaction``.

    ``target_uuid`` is the recipient UUID for IM, the group UUID
    for group IM, and ignored (public channel) for local chat.
    Implements rules 2–3 of the module docstring; the caller is
    responsible for rule 1 (only call for Core-confirmed sends).
    """
    if interaction.channel != channel:
        return False
    if channel == IM:
        return (
            target_uuid is not None
            and interaction.from_uuid is not None
            and str(interaction.from_uuid) == str(target_uuid)
        )
    if channel == GROUP_IM:
        return (
            target_uuid is not None
            and interaction.group_uuid is not None
            and str(interaction.group_uuid) == str(target_uuid)
        )
    if channel == LOCAL_CHAT:
        return True
    return False


def find_settlement_candidate(
    owed: Sequence[Interaction],
    *,
    channel: str,
    target_uuid: str | None,
) -> Interaction | None:
    """The oldest owed interaction a confirmed send may settle.

    ``owed`` must be in conversation-record order; the first match
    wins (rule 4). Returns None when nothing matches — the send
    then settles nothing.
    """
    for interaction in owed:
        if matches_settlement(
            interaction, channel=channel, target_uuid=target_uuid
        ):
            return interaction
    return None
