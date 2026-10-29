"""Two-tier presence tracking — design doc 03 §2.

Second Life presence arrives in two fidelities, and this module keeps
them in two *separate* stores that are never merged:

- **Full-fidelity tier** (§2.2): avatars inside the interest volume,
  keyed by UUID, with name, position and last-seen time. Distance from
  self and a chat range band are computed here, because raw vectors
  are not what a mind can use: whisper ≤10 m, say ≤20 m, shout
  ≤100 m, region beyond (the hard protocol ranges of doc 03 §1.1).
- **Coarse tier** (§2.1): the ``CoarseLocationUpdate`` census behind
  the mini-map dots — anonymous positions only. A coarse entry has
  **no identity fields whatsoever**: no UUID, no name, nothing that
  could be rendered as a person. It can only ever be counted
  ("coarse census: N unidentified agents in region").

That separation is doc 03 §7.7 build law: *never present coarse dots
as named people.* The two tiers are stored apart, queried apart, and
the digest builder renders them apart.

Staleness: every entry carries a last-seen time and expires after a
TTL (default 30 s, configurable). Expired entries are pruned on read,
so a caller never sees a ghost as if it were current presence.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TTL_S = 30.0

# (band name, max distance in metres) — doc 03 §1.1 chat ranges.
RANGE_BANDS: tuple[tuple[str, float], ...] = (
    ("whisper", 10.0),
    ("say", 20.0),
    ("shout", 100.0),
)
REGION_BAND = "region"


def band_for_distance(distance_m: float) -> str:
    """The chat range band for a distance: whisper/say/shout/region."""
    for band, limit in RANGE_BANDS:
        if distance_m <= limit:
            return band
    return REGION_BAND


Position = tuple[float, float, float]


def as_position(value: Any) -> Position:
    """Coerce a position given as a 3-sequence or {x,y,z} dict."""
    if isinstance(value, dict):
        return (float(value["x"]), float(value["y"]), float(value["z"]))
    x, y, z = value
    return (float(x), float(y), float(z))


def distance_between(a: Position, b: Position) -> float:
    return math.dist(a, b)


@dataclass
class FullAvatar:
    """One identified avatar in the full-fidelity tier."""

    uuid: str
    name: str
    position: Position
    last_seen: float
    display_name: str | None = None

    def distance_from(self, origin: Position) -> float:
        return distance_between(origin, self.position)

    def band_from(self, origin: Position) -> str:
        return band_for_distance(self.distance_from(origin))


@dataclass
class CoarseEntry:
    """One anonymous coarse-census dot. Identity fields: none, ever.

    Deliberately ``slots=True`` with exactly two fields, so there is
    nowhere on this type for a name or UUID to hide.
    """

    __slots__ = ("last_seen", "position")

    position: Position
    last_seen: float

    def __init__(self, position: Position, last_seen: float) -> None:
        self.position = position
        self.last_seen = last_seen


@dataclass
class PresenceTracker:
    """Self position + the two presence tiers, with TTL expiry."""

    ttl_s: float = DEFAULT_TTL_S
    coarse_ttl_s: float | None = None  # None → same TTL as full tier
    _clock: Callable[[], float] = field(default=time.monotonic)
    _self_position: Position | None = field(default=None, init=False)
    _avatars: dict[str, FullAvatar] = field(default_factory=dict, init=False)
    _coarse: list[CoarseEntry] = field(default_factory=list, init=False)

    # -- time ---------------------------------------------------------
    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    # -- self -----------------------------------------------------------
    def set_self_position(self, position: Any, *, now: float | None = None) -> None:
        # ``now`` is accepted for call-site symmetry; self position has
        # no staleness of its own in this slice.
        _ = now
        self._self_position = as_position(position)

    @property
    def self_position(self) -> Position | None:
        return self._self_position

    # -- full-fidelity tier --------------------------------------------
    def update_avatar(
        self,
        uuid: str,
        name: str,
        position: Any,
        *,
        now: float | None = None,
        display_name: str | None = None,
    ) -> FullAvatar:
        if not uuid:
            raise ValueError("full-tier avatars are keyed by UUID; uuid is required")
        if not name:
            raise ValueError("full-tier avatars carry a name; use the coarse tier for anonymous presence")
        avatar = FullAvatar(
            uuid=str(uuid),
            name=str(name),
            position=as_position(position),
            last_seen=self._now(now),
            display_name=display_name,
        )
        self._avatars[avatar.uuid] = avatar
        return avatar

    def remove_avatar(self, uuid: str) -> bool:
        return self._avatars.pop(uuid, None) is not None

    def expire(self, *, now: float | None = None) -> tuple[int, int]:
        """Prune stale entries from both tiers; return (full, coarse) counts removed."""
        at = self._now(now)
        stale = [
            uuid
            for uuid, av in self._avatars.items()
            if at - av.last_seen > self.ttl_s
        ]
        for uuid in stale:
            del self._avatars[uuid]
        coarse_ttl = self.ttl_s if self.coarse_ttl_s is None else self.coarse_ttl_s
        before = len(self._coarse)
        self._coarse = [e for e in self._coarse if at - e.last_seen <= coarse_ttl]
        return (len(stale), before - len(self._coarse))

    def avatars(self, *, now: float | None = None) -> list[FullAvatar]:
        """Live full-tier avatars (stale pruned), nearest first if self is placed."""
        self.expire(now=now)
        result = list(self._avatars.values())
        if self._self_position is not None:
            origin = self._self_position
            result.sort(key=lambda av: (av.distance_from(origin), av.name))
        else:
            result.sort(key=lambda av: av.name)
        return result

    def avatar(self, uuid: str, *, now: float | None = None) -> FullAvatar | None:
        self.expire(now=now)
        return self._avatars.get(uuid)

    def distance_to(self, avatar: FullAvatar) -> float | None:
        if self._self_position is None:
            return None
        return avatar.distance_from(self._self_position)

    def band_of(self, avatar: FullAvatar) -> str | None:
        distance = self.distance_to(avatar)
        return None if distance is None else band_for_distance(distance)

    # -- coarse tier -----------------------------------------------------
    def update_coarse(
        self, positions: Iterable[Any], *, now: float | None = None
    ) -> int:
        """Replace the coarse census with a fresh anonymous snapshot.

        ``CoarseLocationUpdate`` is a whole-census packet, so a fresh
        update *replaces* the old dots rather than accumulating them.
        Positions only — this method has no parameter that could carry
        an identity, by design.
        """
        at = self._now(now)
        self._coarse = [CoarseEntry(as_position(p), at) for p in positions]
        return len(self._coarse)

    def add_coarse(self, position: Any, *, now: float | None = None) -> None:
        self._coarse.append(CoarseEntry(as_position(position), self._now(now)))

    def clear_coarse(self) -> None:
        self._coarse = []

    def coarse_entries(self, *, now: float | None = None) -> list[CoarseEntry]:
        self.expire(now=now)
        return list(self._coarse)

    def coarse_count(self, *, now: float | None = None) -> int:
        return len(self.coarse_entries(now=now))

    def coarse_positions(self, *, now: float | None = None) -> list[Position]:
        return [e.position for e in self.coarse_entries(now=now)]
