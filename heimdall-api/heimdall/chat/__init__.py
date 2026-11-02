"""Text chat for the Heimdall seam — two channels, kept separate.

Built to Volmarr's direct order (2026-10-31, attended session):
text chat ability for IMs *and* for main (local) chat, awareness
of which channel is which, sends kept separate per channel, and a
watcher that checks every five seconds for interactions on both.

- :class:`ChatService` — the outbound side: three send methods,
  one per channel, all routed through the command router so the
  doc-05 governor (daily cap, kill switch, reflex tiers never
  speak) is enforced in the body.
- :class:`ChatWatcher` — the inbound side: a standing consumer of
  the Core event socket that drains every
  :data:`CHECK_INTERVAL_S` seconds into a channel-tagged inbox the
  mind reads via ``pending_interactions()``, learning known
  avatars as it goes and recording connection gaps honestly.
- :class:`Interaction` / :class:`KnownAvatars` — the channel
  vocabulary and identity bookkeeping both sides share
  (:mod:`heimdall.chat.channels`).
- HR-004 debt surface — ``ChatWatcher.unanswered_interactions()``
  asks what is owed without consuming anything, and
  ``ChatWatcher.record_send_result`` (wire it as the service's
  ``on_send``) settles exactly one debt per Core-confirmed send
  under the matching rules in :mod:`heimdall.chat.debt`.
"""

from .channels import (
    CHANNELS,
    EVENT_CHANNELS,
    GROUP_IM,
    IM,
    LOCAL_CHAT,
    Interaction,
    KnownAvatars,
)
from .debt import find_settlement_candidate, matches_settlement
from .service import ChatRouter, ChatService, SendResult
from .watcher import (
    CHECK_INTERVAL_S,
    RECONNECT_BACKOFF_INITIAL_S,
    RECONNECT_BACKOFF_MAX_S,
    ChatWatcher,
    GapRecord,
)

__all__ = [
    "CHANNELS",
    "CHECK_INTERVAL_S",
    "EVENT_CHANNELS",
    "GROUP_IM",
    "IM",
    "LOCAL_CHAT",
    "RECONNECT_BACKOFF_INITIAL_S",
    "RECONNECT_BACKOFF_MAX_S",
    "ChatRouter",
    "ChatService",
    "ChatWatcher",
    "GapRecord",
    "Interaction",
    "KnownAvatars",
    "SendResult",
    "find_settlement_candidate",
    "matches_settlement",
]
