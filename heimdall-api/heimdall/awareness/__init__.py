"""World awareness for the Heimdall seam — Phase 3.

Presence tiers (:mod:`heimdall.awareness.presence`) and the scene
digest (:mod:`heimdall.awareness.digest`), per design doc 03:
two-tier presence (§2), the §7.4 scene-digest format, and the §7.7
law that coarse radar dots are never presented as named people.
Plus the Astrid-Law login sweep
(:mod:`heimdall.awareness.login_sweep`, Phase 3 slice 2, doc 05 §5):
no silent shut-out — every login failure is loud, surfaced, and
retried or explicitly blocked. And the LSL companion HUD ingest
(:mod:`heimdall.awareness.hud_ingest`, Phase 3 slice 3, doc 03
§7.6): HUD1 digests from the worn sense organ, feeding the coarse
presence tier only, fail-closed. And the pending-offers queue
(:mod:`heimdall.awareness.offers`, census doc 15 slice F1, doc 03
§6): incoming social offers held as decision events — hold-and-show,
never silent auto-accept, debit permissions operator-gated.
And the per-thread conversation log
(:mod:`heimdall.awareness.conversations`, census doc 15 slice F2):
chat/IM/group-chat held as attributed utterances in re-readable
threads — outbound recorded only after Core confirmation,
per-resident purge honoured. And inventory as a sense and a hand
(:mod:`heimdall.awareness.inventory`, census doc 15 slice F3):
items browsable by folder/kind, notecards read only from content
that actually arrived, landmarks and gives as governor-ready
commands recorded only after Core confirmation — and no delete
path at all.
"""

from .conversations import (
    CONVERSATION_KINDS,
    ConversationLog,
    Utterance,
)
from .digest import HeardEntry, SceneDigestBuilder, build_scene_digest
from .hud_ingest import (
    FORMAT_TAG,
    HudConditions,
    HudDigest,
    HudEvent,
    HudIngest,
    HudIngestResult,
    HudPayloadError,
    HudPlace,
    parse_hud_payload,
)
from .inventory import ITEM_KINDS, InventoryItem, InventoryStore
from .login_sweep import LoginRecord, LoginSupervisor, LoginSweepConfig
from .offers import (
    EVENT_KINDS,
    OFFER_KINDS,
    OffersQueue,
    PendingOffer,
)
from .presence import (
    RANGE_BANDS,
    CoarseEntry,
    FullAvatar,
    PresenceTracker,
    band_for_distance,
)

__all__ = [
    "CONVERSATION_KINDS",
    "EVENT_KINDS",
    "FORMAT_TAG",
    "ITEM_KINDS",
    "OFFER_KINDS",
    "RANGE_BANDS",
    "CoarseEntry",
    "ConversationLog",
    "FullAvatar",
    "HeardEntry",
    "HudConditions",
    "HudDigest",
    "HudEvent",
    "HudIngest",
    "HudIngestResult",
    "HudPayloadError",
    "HudPlace",
    "InventoryItem",
    "InventoryStore",
    "LoginRecord",
    "LoginSupervisor",
    "LoginSweepConfig",
    "OffersQueue",
    "PendingOffer",
    "PresenceTracker",
    "SceneDigestBuilder",
    "Utterance",
    "band_for_distance",
    "build_scene_digest",
    "parse_hud_payload",
]
