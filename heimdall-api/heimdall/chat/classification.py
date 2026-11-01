"""Addressed-to classification — HR-005 (responsiveness Part 0).

The watcher stamps every :class:`~heimdall.chat.channels.Interaction`
with how directly it is addressed to her; this module holds the
classification rules and the exchange-state machine as a small,
explicit unit so they are documented in one place and testable
without a watcher, a socket, or a grid. HR-062/HR-063 (Part VII)
later harden this v1 into the full judgement engine; the v1 law is:

1. **Direct.** Every IM is ``direct``: an IM channel is private
   speech to her by construction. (An IM that also mentions her
   name is still direct — the channel rule wins.)
2. **Named.** A local-chat or group line whose text mentions her
   name is ``named``. The name is configurable (the watcher's
   ``self_name``, default :data:`DEFAULT_SELF_NAME`); matching is
   case-insensitive and word-boundary-ish — the name must appear
   as a whole word, so "Yrsa" matches "hey Yrsa," and "YRSA!" but
   not a longer word that merely contains it.
3. **Contextual.** A line from a speaker who has an *open
   exchange* on the same channel is ``contextual`` — a
   continuation of a conversation already addressed to her. This
   is what makes bare continuations count: "are you still there?"
   names nobody, but inside an open exchange it is plainly spoken
   to her. Any line inside the window classifies contextual,
   "you"-continuations included; there is no separate pronoun
   rule, because the exchange state *is* the context.
4. **Ambient.** Everything else is ``ambient``: overheard
   conversation, not addressed to her, carrying no reply debt
   of its own (Part VII policy decides what ambient means for
   interaction; classification only reports what a line *is*).

THE EXCHANGE-STATE MACHINE:

- State is kept per **(speaker identity, channel, group)**:
  speaker identity is the speaker's UUID when the event carried
  one, else their name (the identity model the rest of the chat
  package already uses — UUIDs address IMs, names are what local
  chat reliably carries). The group component is the line's
  ``group_uuid`` for GROUP_IM and ``None`` otherwise. Justification
  for including the group: HR-004 already established that group
  debt is group-exact — two groups are two different rooms, so an
  exchange opened in one group must never make a line in another
  group "contextual". IM and local chat have no group, so their
  key is simply (speaker, channel).
- An exchange **opens** on a direct or named line, and **refreshes**
  (its inactivity clock restarts) on every further direct, named,
  or contextual line from the same speaker+channel(+group).
  Ambient lines never open or refresh an exchange.
- An exchange **expires** after
  :data:`EXCHANGE_INACTIVITY_WINDOW_S` seconds with no refreshing
  line from that speaker on that channel. The window is a named,
  configurable value (watcher parameter ``exchange_window_s``);
  120 s is the v1 initial value — long enough for a human to
  think and type a follow-up, short enough that an unrelated
  remark minutes later is not swept into a dead conversation.
  Expiry is evaluated lazily at classification time (no timers,
  no threads): a line arriving after the window finds the
  exchange closed, and the stale entry is dropped.
- One speaker's exchange never leaks to another: a different
  speaker on the same channel has their own state (or none).

Times are the caller's clock values (the watcher passes each
line's ``received_at`` — when the line arrived, not when the
drain happened to run), so classification depends on when lines
were said, never on drain scheduling.
"""
from __future__ import annotations

import re

from .channels import (
    AMBIENT,
    CONTEXTUAL,
    DIRECT,
    GROUP_IM,
    IM,
    NAMED,
)

#: Her name, unless the watcher is configured otherwise. She is
#: Yrsa; a deployment that renames the inhabitant renames this.
DEFAULT_SELF_NAME = "Yrsa"

#: Seconds of silence after which an open exchange closes.
#: Named and configurable (watcher parameter ``exchange_window_s``)
#: — see the module docstring for why 120 s is the v1 value.
EXCHANGE_INACTIVITY_WINDOW_S = 120.0


def mentions_self_name(text: str | None, self_name: str) -> bool:
    """True when ``text`` mentions ``self_name`` as a whole word.

    Case-insensitive, word-boundary-ish: the name must not be
    glued to other word characters on either side. Punctuation
    and possessives are boundaries, so "Yrsa," / "yrsa!" /
    "Yrsa's" all match, while a longer name containing hers
    does not. An empty configured name never matches (fail
    closed: no name, no named classification).
    """
    if not text or not self_name or not str(self_name).strip():
        return False
    pattern = r"\b" + re.escape(str(self_name).strip()) + r"\b"
    return re.search(pattern, str(text), flags=re.IGNORECASE) is not None


def exchange_key(
    *,
    channel: str,
    from_uuid: str | None,
    from_name: str | None,
    group_uuid: str | None,
) -> tuple[str, str, str | None] | None:
    """The exchange-state key for one line, or None if unidentified.

    (speaker, channel, group) — see the module docstring for the
    identity and group justification. A line with neither a UUID
    nor a name has no speaker to hold state for; the watcher
    already refuses such lines as unattributed, so None here is
    a defensive outcome, not an expected path.
    """
    speaker = from_uuid or from_name
    if not speaker:
        return None
    group = str(group_uuid) if channel == GROUP_IM and group_uuid else None
    return (str(speaker), str(channel), group)


class ExchangeTracker:
    """Open-exchange state + the classification rules (HR-005).

    One tracker per watcher. :meth:`classify` both decides a
    line's stamp and advances the state machine, in arrival
    order — call it exactly once per line, in the order the
    lines were received.
    """

    def __init__(
        self,
        *,
        self_name: str = DEFAULT_SELF_NAME,
        window_s: float = EXCHANGE_INACTIVITY_WINDOW_S,
    ) -> None:
        # Honoured literally: an empty name disables named
        # classification (mentions_self_name fails closed on it).
        self.self_name = str(self_name)
        self.window_s = float(window_s)
        # exchange key -> clock time of the last refreshing line
        self._last_activity: dict[tuple[str, str, str | None], float] = {}

    def is_open(
        self,
        *,
        channel: str,
        from_uuid: str | None = None,
        from_name: str | None = None,
        group_uuid: str | None = None,
        now: float,
    ) -> bool:
        """True when an unexpired exchange exists for this speaker."""
        key = exchange_key(
            channel=channel,
            from_uuid=from_uuid,
            from_name=from_name,
            group_uuid=group_uuid,
        )
        if key is None:
            return False
        last = self._last_activity.get(key)
        if last is None:
            return False
        if now - last > self.window_s:
            del self._last_activity[key]  # expired: close it
            return False
        return True

    def classify(
        self,
        *,
        channel: str,
        msg: str,
        from_uuid: str | None = None,
        from_name: str | None = None,
        group_uuid: str | None = None,
        now: float,
    ) -> str:
        """Stamp one line and advance the exchange state machine.

        Rule order (module docstring): direct (IM) > named
        (name mention) > contextual (open exchange) > ambient.
        Direct/named/contextual lines refresh their exchange;
        ambient lines leave state untouched.
        """
        key = exchange_key(
            channel=channel,
            from_uuid=from_uuid,
            from_name=from_name,
            group_uuid=group_uuid,
        )
        if channel == IM:
            stamp = DIRECT
        elif mentions_self_name(msg, self.self_name):
            stamp = NAMED
        elif key is not None and self.is_open(
            channel=channel,
            from_uuid=from_uuid,
            from_name=from_name,
            group_uuid=group_uuid,
            now=now,
        ):
            stamp = CONTEXTUAL
        else:
            stamp = AMBIENT
        if stamp != AMBIENT and key is not None:
            # Open (first direct/named) or refresh (any addressed
            # or contextual continuation): the inactivity clock
            # restarts at this line's arrival time.
            self._last_activity[key] = float(now)
        return stamp

    def open_exchange_count(self, now: float) -> int:
        """Unexpired exchanges at ``now`` (expired ones are pruned)."""
        expired = [
            key
            for key, last in self._last_activity.items()
            if now - last > self.window_s
        ]
        for key in expired:
            del self._last_activity[key]
        return len(self._last_activity)
