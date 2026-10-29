"""World awareness for the Heimdall seam — Phase 3 slice 1.

Presence tiers (:mod:`heimdall.awareness.presence`) and the scene
digest (:mod:`heimdall.awareness.digest`), per design doc 03:
two-tier presence (§2), the §7.4 scene-digest format, and the §7.7
law that coarse radar dots are never presented as named people.
"""

from .digest import HeardEntry, SceneDigestBuilder, build_scene_digest
from .presence import (
    RANGE_BANDS,
    CoarseEntry,
    FullAvatar,
    PresenceTracker,
    band_for_distance,
)

__all__ = [
    "RANGE_BANDS",
    "CoarseEntry",
    "FullAvatar",
    "HeardEntry",
    "PresenceTracker",
    "SceneDigestBuilder",
    "band_for_distance",
    "build_scene_digest",
]
