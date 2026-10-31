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
path at all. And the friends & groups surface
(:mod:`heimdall.awareness.social`, census doc 15 slice F4):
friends with their permissions and event-only presence, group
memberships with exactly one active title, a group notices
inbox, friendship/group actions recorded only after Core
confirmation, and mute/block honouring as a hard duty.
And places & discovery
(:mod:`heimdall.awareness.places`, census doc 15 slice F5):
SLURL parsing, teleport commands recorded into history only
after Core confirmation, set/go home with no guessed
destination, region records that invent nothing, and
rate-aware people/places/events/groups search — build checks
the pacing, confirmation consumes it, never a scrape.
And object interaction
(:mod:`heimdall.awareness.objects`, census doc 15 slice F6):
a registry of known objects holding only what events carried,
touch and object-targeted sit as build-only commands recorded
only after Core confirmation, script dialogs answered through
the F1 offers queue with the choice validated against the
buttons actually offered, and animations/gestures played by
exactly the identifier supplied — nothing invented.
And appearance
(:mod:`heimdall.awareness.appearance`, census doc 15 slice F7):
an outfit that is unknown — never invented as empty — until a
snapshot arrives, entries holding exactly what they carried,
wear/remove/attach/detach/change-outfit as build-only commands
validated against a bound inventory (or explicit details) and
recorded only after Core confirmation, and appearance-change
events marking a known outfit stale without touching its
entries until the next snapshot.
And the RLV restriction store and gate
(:mod:`heimdall.awareness.rlv`, commission 4, doc 05 §6.4 +
doc 06): restrictions source-attributed and applied only
from allowlisted sources (Volmarr's objects, her own worn
items, per-place opted-in venues), everything else held and
reportable, safety invariants refusing kill-switch / Volmarr-
reachability / logout restrictions even from allowlisted
sources, per-source stack semantics, an ``rlv_blocked``
behaviour gate, felt state that always names sources, and
build-only self-use commands recorded only after Core
confirmation.
And money as a sense
(:mod:`heimdall.awareness.money`, census doc 15 slice F8):
a balance that is unknown — never invented as L$0 — until a
balance event arrives, strictly validated non-negative integer
balances, attributed transaction records held exactly as they
arrived (never promoted to facts), balance updates riding only
the events that carry them, an honest "how much L$ do I have?"
answer, and no spend path at all — the governor's payment
default-deny stands untouched.
And the map sense
(:mod:`heimdall.awareness.visual`, visual capture doc 16
slice V1, Route A): a zoom-1 map tile fetched through an
injectable fetcher for exactly the region + grid coordinates
supplied — never invented — rate-limited with loud recorded
refusals, JPEG-validated fail-closed, spooled with provenance
as an attributed ``visual_observation`` about a place
labelled a map image (never "what Yrsa sees"), an honest
vision state ("none (object-data only)" until a tile lands,
then "map image only"), and place purge of spooled tiles —
place data carrying no resident identity at all.
And movement & pathfinding
(:mod:`heimdall.awareness.navigation`, doc 14 slices M2+M3):
a traversability raster whose cells are UNKNOWN — never
treated as free — until object footprints, no-entry/ban
parcels (hard-blocked), supplied terrain heights, or her own
walked positions say otherwise, and an A* planner over it
(corner-cut-proof diagonals, line-of-sight smoothing,
distinct honest failure reasons, region crossings out of
scope) with an llGetStaticPath-style hint held as a
cross-check only, proven walked segments recorded as
attributed experience, and a build-only governor-ready
``move_to`` command (tier "mind", explicit walk/fly) that
records nothing — the M1 Core leg executes it.
"""

from .appearance import (
    APPEARANCE_EVENTS,
    WEARABLE_KINDS,
    AppearanceNotice,
    AppearanceState,
    AppearanceStore,
    OutfitChangeRecord,
    WornItem,
)
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
from .money import MONEY_EVENTS, MoneyState, MoneyStore, TransactionRecord
from .navigation import (
    CELL_BLOCKED,
    CELL_FREE,
    CELL_HARD_BLOCKED,
    CELL_UNKNOWN,
    MOVE_MODES,
    NAVIGATION_EVENTS,
    HintAgreement,
    HintSegmentResult,
    HintValidation,
    NavigationState,
    NavigationStore,
    PathValidity,
    PlanResult,
    ProvenWalkRecord,
    WalkedRecord,
)
from .objects import (
    OBJECTS_EVENTS,
    DialogRecord,
    InteractionRecord,
    KnownPlayable,
    ObjectRecord,
    ObjectStore,
    dialog_buttons,
)
from .offers import (
    EVENT_KINDS,
    OFFER_KINDS,
    OffersQueue,
    PendingOffer,
)
from .places import (
    PLACES_EVENTS,
    SEARCH_KINDS,
    Destination,
    PlaceStore,
    RegionInfo,
    SearchResult,
    TeleportRecord,
    destination_from_payload,
    parse_slurl,
)
from .presence import (
    RANGE_BANDS,
    CoarseEntry,
    FullAvatar,
    PresenceTracker,
    band_for_distance,
)
from .rlv import (
    RLV_EVENTS,
    SOURCE_KINDS,
    GateResult,
    HeldRestriction,
    ParsedRlvRule,
    RlvApplyResult,
    RlvRestriction,
    RlvState,
    RlvStore,
    SelfUseRecord,
    parse_rlv_rules,
)
from .social import (
    SOCIAL_EVENTS,
    Friend,
    GroupMembership,
    GroupNotice,
    MuteEntry,
    SocialStore,
)
from .visual import (
    VISUAL_EVENTS,
    FetchRefusal,
    MapTileRecord,
    VisualObservation,
    VisualState,
    VisualStore,
    build_map_tile_url,
    map_tile_url,
)

__all__ = [
    "APPEARANCE_EVENTS",
    "CELL_BLOCKED",
    "CELL_FREE",
    "CELL_HARD_BLOCKED",
    "CELL_UNKNOWN",
    "CONVERSATION_KINDS",
    "EVENT_KINDS",
    "FORMAT_TAG",
    "ITEM_KINDS",
    "MONEY_EVENTS",
    "MOVE_MODES",
    "NAVIGATION_EVENTS",
    "OBJECTS_EVENTS",
    "OFFER_KINDS",
    "PLACES_EVENTS",
    "RANGE_BANDS",
    "RLV_EVENTS",
    "SEARCH_KINDS",
    "SOCIAL_EVENTS",
    "SOURCE_KINDS",
    "VISUAL_EVENTS",
    "WEARABLE_KINDS",
    "AppearanceNotice",
    "AppearanceState",
    "AppearanceStore",
    "CoarseEntry",
    "ConversationLog",
    "Destination",
    "DialogRecord",
    "FetchRefusal",
    "Friend",
    "FullAvatar",
    "GateResult",
    "GroupMembership",
    "GroupNotice",
    "HeardEntry",
    "HeldRestriction",
    "HintAgreement",
    "HintSegmentResult",
    "HintValidation",
    "HudConditions",
    "HudDigest",
    "HudEvent",
    "HudIngest",
    "HudIngestResult",
    "HudPayloadError",
    "HudPlace",
    "InteractionRecord",
    "InventoryItem",
    "InventoryStore",
    "KnownPlayable",
    "LoginRecord",
    "LoginSupervisor",
    "LoginSweepConfig",
    "MapTileRecord",
    "MoneyState",
    "MoneyStore",
    "MuteEntry",
    "NavigationState",
    "NavigationStore",
    "ObjectRecord",
    "ObjectStore",
    "OffersQueue",
    "OutfitChangeRecord",
    "ParsedRlvRule",
    "PathValidity",
    "PendingOffer",
    "PlaceStore",
    "PlanResult",
    "PresenceTracker",
    "ProvenWalkRecord",
    "RegionInfo",
    "RlvApplyResult",
    "RlvRestriction",
    "RlvState",
    "RlvStore",
    "SceneDigestBuilder",
    "SearchResult",
    "SelfUseRecord",
    "SocialStore",
    "TeleportRecord",
    "TransactionRecord",
    "Utterance",
    "VisualObservation",
    "VisualState",
    "VisualStore",
    "WalkedRecord",
    "WornItem",
    "band_for_distance",
    "build_map_tile_url",
    "build_scene_digest",
    "destination_from_payload",
    "dialog_buttons",
    "map_tile_url",
    "parse_hud_payload",
    "parse_rlv_rules",
    "parse_slurl",
]
