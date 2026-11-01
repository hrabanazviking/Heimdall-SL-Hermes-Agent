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
- HR-005 addressed-to classification — every ``Interaction``
  carries a ``classification`` stamp (``direct`` / ``named`` /
  ``contextual`` / ``ambient``, default ``ambient``) set by the
  watcher at classification time under the rules and the
  exchange-state machine in :mod:`heimdall.chat.classification`.
- HR-006 Aurelia dual feed — :class:`AureliaFeed`
  (:mod:`heimdall.chat.aurelia`), wired into the watcher as the
  optional ``aurelia_feed`` plug, POSTs every classified
  interaction to Aurelia's ``/event`` as ``sl.im`` / ``sl.chat`` /
  ``sl.group_im`` (source ``"heimdall"``, base URL configurable
  via constructor or ``HEIMDALL_AURELIA_URL``). Failure-isolated:
  a failed post is logged and never blocks the mind feed.
"""

from .aurelia import (
    AURELIA_SOURCE,
    AURELIA_URL_ENV_VAR,
    CHANNEL_EVENT_TYPES,
    DEFAULT_AURELIA_URL,
    AureliaFeed,
    build_aurelia_event,
)
from .channels import (
    AMBIENT,
    CHANNELS,
    CLASSIFICATIONS,
    CONTEXTUAL,
    DIRECT,
    EVENT_CHANNELS,
    GROUP_IM,
    IM,
    LOCAL_CHAT,
    NAMED,
    Interaction,
    KnownAvatars,
)
from .classification import (
    DEFAULT_SELF_NAME,
    EXCHANGE_INACTIVITY_WINDOW_S,
    ExchangeTracker,
    exchange_key,
    mentions_self_name,
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
    "AMBIENT",
    "AURELIA_SOURCE",
    "AURELIA_URL_ENV_VAR",
    "CHANNELS",
    "CHANNEL_EVENT_TYPES",
    "CHECK_INTERVAL_S",
    "CLASSIFICATIONS",
    "CONTEXTUAL",
    "DEFAULT_AURELIA_URL",
    "DEFAULT_SELF_NAME",
    "DIRECT",
    "EVENT_CHANNELS",
    "EXCHANGE_INACTIVITY_WINDOW_S",
    "GROUP_IM",
    "IM",
    "LOCAL_CHAT",
    "NAMED",
    "RECONNECT_BACKOFF_INITIAL_S",
    "RECONNECT_BACKOFF_MAX_S",
    "AureliaFeed",
    "ChatRouter",
    "ChatService",
    "ChatWatcher",
    "ExchangeTracker",
    "GapRecord",
    "Interaction",
    "KnownAvatars",
    "SendResult",
    "build_aurelia_event",
    "exchange_key",
    "find_settlement_candidate",
    "matches_settlement",
    "mentions_self_name",
]
