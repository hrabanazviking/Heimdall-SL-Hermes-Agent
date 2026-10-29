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
presence tier only, fail-closed.
"""

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
from .login_sweep import LoginRecord, LoginSupervisor, LoginSweepConfig
from .presence import (
    RANGE_BANDS,
    CoarseEntry,
    FullAvatar,
    PresenceTracker,
    band_for_distance,
)

__all__ = [
    "FORMAT_TAG",
    "RANGE_BANDS",
    "CoarseEntry",
    "FullAvatar",
    "HeardEntry",
    "HudConditions",
    "HudDigest",
    "HudEvent",
    "HudIngest",
    "HudIngestResult",
    "HudPayloadError",
    "HudPlace",
    "LoginRecord",
    "LoginSupervisor",
    "LoginSweepConfig",
    "PresenceTracker",
    "SceneDigestBuilder",
    "band_for_distance",
    "build_scene_digest",
    "parse_hud_payload",
]
