"""Map sense — visual capture doc 16 slice V1 (Route A).

Doc 16's ground truth: a protocol client receives structured
streams, never a rendered frame, and there is no server
screenshot API. Route A — Linden Lab's Map API — is the
cheapest *real* image available to her: a zoom-1 map tile per
region, served directly as a JPEG at
``http://map.secondlife.com/map-1-<X>-<Y>-objects.jpg``, no
login, no account, no cost. It shows terrain and build
footprints from directly above, at whatever freshness Linden's
tile generation last produced. It cannot show people,
interiors, or anything at eye level.

This module is the seam/API side of V1 only: on a region change
(and on direct request, rate-limited) it fetches the current
region's tile through an injectable fetcher, spools the exact
bytes with provenance, and records a ``visual_observation`` —
an *attributed image observation about a place*, labelled a
map image. No Core (C#) change, no login, and no network in
tests: the fetcher is a callable this store is handed, and the
one default urllib fetcher is never invoked by the test suite.

Bounds, enforced here in the body, not by convention:

- **Coordinates are supplied, never invented.** A tile exists
  only for a region identity plus grid coordinates (X, Y) that
  a caller or an event actually carried. Missing or invalid
  region/coords refuse loudly — recorded in ``refusals`` and
  the ``rejected`` audit, fetcher never called — they are
  never guessed, defaulted, or carried over from another
  region's record.
- **A map image is never "what Yrsa sees".** Every record and
  observation carries ``source="map_tile"`` and the label
  ``map_image``; the observation is perception data about a
  place (Phase I0 discipline: perception, never canonical
  fact), and the felt vision state names itself honestly:
  ``vision: none (object-data only)`` before any tile lands,
  ``vision: map image only`` once one has (doc 16's exact
  digest wording).
- **Fetches are rate-limited, and a refusal is loud.** Doc 01
  records that unreasonable load from a scripted agent is a
  named Linden policy violation, so fetches ride a
  configurable minimum interval plus a rolling per-hour cap
  (this seam's own conservative defaults — Linden publishes
  no numeric map limit, and these are never presented as
  Linden's numbers). A rate-limited request returns a
  recorded refusal; it is never silently fetched anyway.
  Checking the limiter consumes nothing; an actual fetcher
  invocation consumes a slot, even when its payload then
  fails validation — the GET was the load.
- **Payloads fail closed.** Only bytes plausibly a JPEG (the
  ``FF D8 FF`` magic) are spooled. A fetcher error, an empty
  payload, or a non-JPEG payload files a rejection record,
  spools nothing, and never raises — fetch and ingest never
  raise.
- **Map tiles are place data, not resident data.** A tile
  names a region and grid coordinates only — no resident
  identity is carried, held, or fabricated anywhere in this
  module, so there is no per-resident purge surface to honour
  (:meth:`VisualStore.purge_resident` exists for uniform
  purge loops and always removes nothing, by that fact).
  Spooled tiles ARE removable by place:
  :meth:`VisualStore.purge_region` deletes a region's spool
  files and records.
- **No spend, no messaging, no governor change.** Fetching a
  map tile is a free plain GET; nothing here builds a
  command, and ``core/governor.py`` is untouched.

Scope honesty: V1 is the map sense only. There is no texture
sight (doc 16 V2), no synthetic render (V3), no attended photo
handoff (V4), and no own-session rendering (V5, gated on
Volmarr's ruling). The Core does not yet emit anything this
store needs — region identity and grid coordinates arrive
from callers/events on this side (the base-vocabulary
``region_changed`` event among them; it is ingested here
without duplicating its name in ``core/models.py``). The one
vocabulary addition, ``visual_observation`` /
``VisualObservation``, is the contract WYRD and any future
Core leg will speak for attributed image observations.
"""
from __future__ import annotations

import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..core.models import SeamEvent

# Canonical seam event names this store ingests. The
# ``visual_observation`` name is added to core/models.py with
# this slice; ``region_changed`` already rides the base
# vocabulary (RegionChanged / region_changed) and is ingested
# here without adding a duplicate name there.
VISUAL_EVENTS = frozenset(
    {
        "visual_observation",
        "region_changed",
    }
)

# Provenance source + image label for everything V1 holds.
# The label is what the observation IS: a map image of a
# place — never a photograph, never "what Yrsa sees".
MAP_TILE_SOURCE = "map_tile"
MAP_IMAGE_LABEL = "map_image"

# The digest vision wordings (doc 16 / digest.py §8.10):
# exactly these strings, before and after the first tile.
VISION_NONE_LABEL = "none (object-data only)"
VISION_MAP_LABEL = "map image only"

# This seam's own conservative fetch defaults (see the module
# docstring: Linden publishes no numeric map limit; these are
# ours, configurable, never presented as Linden's numbers).
DEFAULT_MIN_FETCH_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_FETCHES_PER_HOUR = 20

_MAP_TILE_URL = "http://map.secondlife.com/map-1-{x}-{y}-objects.jpg"
_JPEG_MAGIC = b"\xff\xd8\xff"

_GRID_X_KEYS = ("grid_x", "region_x", "map_x", "x")
_GRID_Y_KEYS = ("grid_y", "region_y", "map_y", "y")
_REGION_KEYS = ("region", "region_name", "sim_name", "name")


class MapTileFetcher(Protocol):
    """The fetch seam: one URL in, its exact bytes out.

    Tests hand the store a fake implementing this protocol, so
    the suite never touches the network; production hands it
    :func:`urllib_map_tile_fetcher` (or any compatible GET)."""

    def __call__(self, url: str) -> bytes: ...


def urllib_map_tile_fetcher(url: str, *, timeout: float = 10.0) -> bytes:
    """The default fetcher: one plain GET via urllib (stdlib).

    Never called by tests. Any failure raises into
    :meth:`VisualStore.fetch_map_tile`, which fails closed —
    the exception is recorded as a refusal, never propagated.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": "Heimdall map sense (V1)"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _validate_grid_coord(value: Any, label: str) -> int:
    """Validate one grid coordinate strictly; return it as int.

    Grid coordinates are whole-region indices: a real ``int``
    passes, an integral ``float`` is accepted (region events
    elsewhere in the seam carry floats), and booleans, strings,
    non-integral floats, and negatives all fail closed."""
    if isinstance(value, bool):
        raise TypeError(f"a grid {label} must be a non-negative integer")
    if isinstance(value, float):
        # is_integer() is False for nan/inf as well as fractions.
        if not value.is_integer():
            raise TypeError(f"a grid {label} must be a non-negative integer")
        result = int(value)
    elif isinstance(value, int):
        result = int(value)
    else:
        raise TypeError(f"a grid {label} must be a non-negative integer")
    if result < 0:
        raise ValueError(f"a grid {label} must be non-negative")
    return result


def build_map_tile_url(grid_x: Any, grid_y: Any) -> str:
    """The zoom-1 map tile URL for supplied grid coordinates.

    Raises ValueError / TypeError for anything that is not a
    pair of non-negative integer coordinates — a URL is never
    built out of a guess."""
    x = _validate_grid_coord(grid_x, "x")
    y = _validate_grid_coord(grid_y, "y")
    return _MAP_TILE_URL.format(x=x, y=y)


# Kept for callers that prefer the shorter spelling.
map_tile_url = build_map_tile_url


def _region_slug(region: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(region).strip().lower()).strip("-")
    return slug or "region"


@dataclass
class MapTileRecord:
    """Provenance for one spooled map tile — what was fetched,
    for where, when, and where its exact bytes live."""

    source: str
    region: str
    grid_x: int
    grid_y: int
    fetched_at: float
    spool_path: str
    byte_count: int
    url: str


@dataclass
class VisualObservation:
    """One attributed image observation about a PLACE.

    An observation, in the seam's ``visual_observation``
    vocabulary — perception data about a place, labelled a map
    image. It is never a photograph, never "what Yrsa sees",
    and never a canonical fact about the region."""

    source: str
    label: str
    region: str
    grid_x: int
    grid_y: int
    fetched_at: float
    spool_path: str
    byte_count: int
    url: str
    event_name: str = "visual_observation"


@dataclass
class FetchRefusal:
    """One loudly recorded refusal: a fetch that did NOT happen
    (or whose payload was not spooled), with its reason."""

    region: str | None
    grid_x: int | None
    grid_y: int | None
    reason: str
    at: float


@dataclass
class VisualState:
    """A snapshot of the visual state, honest about its gaps."""

    known: bool
    vision_label: str
    region: str | None
    tiles: int
    last_fetched_at: float | None


@dataclass
class VisualStore:
    """The map-sense store: fetch, spool, observe, purge."""

    spool_dir: Any = None
    fetcher: Any = None
    on_observation: Any = None
    _clock: Any = field(default=time.time)
    min_fetch_interval_seconds: float = DEFAULT_MIN_FETCH_INTERVAL_SECONDS
    max_fetches_per_hour: int = DEFAULT_MAX_FETCHES_PER_HOUR
    _records: list[MapTileRecord] = field(default_factory=list, init=False)
    _observations: list[VisualObservation] = field(
        default_factory=list, init=False
    )
    _fetch_times: list[float] = field(default_factory=list, init=False)
    _current_region: str | None = field(default=None, init=False)
    _current_grid: tuple[int, int] | None = field(default=None, init=False)
    # Loud refusals: every fetch that was refused or failed
    # closed, structured — never a silent drop.
    refusals: list[FetchRefusal] = field(default_factory=list, init=False)
    # Fail-closed audit: (reason, detail) for every refused
    # ingest / fetch, in the sibling stores' shape.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else float(now)

    @staticmethod
    def _payload_timestamp(payload: dict[str, Any], fallback: float) -> float:
        raw = payload.get("timestamp")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw).timestamp()
            except ValueError:
                return fallback
        return fallback

    # -- honesty: vision state ---------------------------------------------
    @property
    def vision_known(self) -> bool:
        """True only once a map tile has actually landed. Before
        that the vision state is UNKNOWN — not a scene, not a
        photograph: no map image exists yet."""
        return bool(self._records)

    def vision_label(self) -> str:
        """The felt vision label, in doc 16's exact wording:
        ``"map image only"`` once a map tile exists, else
        ``"none (object-data only)"`` (the digest's existing
        posture). This is the value a digest is handed —
        ``SceneDigestBuilder.build(..., vision=...)`` — or the
        store a digest is handed via ``visual_store=``."""
        return VISION_MAP_LABEL if self._records else VISION_NONE_LABEL

    def current_state(self) -> VisualState:
        return VisualState(
            known=self.vision_known,
            vision_label=self.vision_label(),
            region=self._current_region,
            tiles=len(self._records),
            last_fetched_at=(
                self._records[-1].fetched_at if self._records else None
            ),
        )

    def describe(self) -> str:
        """Answer "what can you see?" — honestly.

        Before any tile: vision is none (object-data only), and
        that is spoken, never dressed up. After one: exactly
        ``vision: map image only`` — a map image of a place,
        named as a map image."""
        label = self.vision_label()
        if not self._records:
            return (
                f"vision: {label} — no map image has been "
                "fetched, so there is no image of anywhere yet."
            )
        last = self._records[-1]
        return (
            f"vision: {label} — the latest map image is of "
            f'region "{last.region}" (grid {last.grid_x},'
            f"{last.grid_y}), {len(self._records)} map "
            "image(s) on record. A map image is a top-down "
            "image of a place, not what I see."
        )

    # -- queries -------------------------------------------------------------
    @property
    def current_region(self) -> str | None:
        """The region most recently recorded, or None."""
        return self._current_region

    def records(self) -> list[MapTileRecord]:
        """All held tile provenance records, in fetch order."""
        return list(self._records)

    def records_for_region(self, region: str) -> list[MapTileRecord]:
        return [r for r in self._records if r.region == str(region)]

    def latest_record(self) -> MapTileRecord | None:
        return self._records[-1] if self._records else None

    def observations(self) -> list[VisualObservation]:
        """All recorded visual observations, in arrival order."""
        return list(self._observations)

    # -- rate limiting ---------------------------------------------------------
    def _recent_fetch_times(self, now: float) -> list[float]:
        cutoff = now - 3600.0
        self._fetch_times = [t for t in self._fetch_times if t > cutoff]
        return self._fetch_times

    def check_fetch_allowed(self, *, now: float | None = None) -> None:
        """Raise PermissionError when a fetch right now would
        break this seam's conservative pacing: inside the
        minimum interval since the last actual fetch, or at
        the rolling-hour cap. Checking consumes nothing."""
        moment = self._now(now)
        recent = self._recent_fetch_times(moment)
        if recent and moment - recent[-1] < float(
            self.min_fetch_interval_seconds
        ):
            raise PermissionError(
                "map tile fetch too soon after the previous fetch "
                f"(< {self.min_fetch_interval_seconds:g}s interval)"
            )
        if len(recent) >= int(self.max_fetches_per_hour):
            raise PermissionError(
                "map tile fetch cap for the rolling hour reached "
                f"({self.max_fetches_per_hour})"
            )

    def _refuse(
        self,
        region: str | None,
        grid_x: int | None,
        grid_y: int | None,
        reason: str,
        moment: float,
    ) -> None:
        """Record a refusal loudly, in both audit shapes."""
        self.refusals.append(
            FetchRefusal(
                region=region,
                grid_x=grid_x,
                grid_y=grid_y,
                reason=reason,
                at=moment,
            )
        )
        self.rejected.append((reason, region))

    # -- fetch -------------------------------------------------------------------
    def fetch_map_tile(
        self,
        region: str | None = None,
        grid_x: Any = None,
        grid_y: Any = None,
        *,
        now: float | None = None,
    ) -> MapTileRecord | None:
        """Fetch, spool, and record one region's map tile.

        Returns the provenance record on success. On ANY
        failure — missing/invalid region or coordinates, no
        fetcher or spool directory configured, rate limit, a
        fetcher error, an empty or non-JPEG payload, a spool
        error — returns None with the refusal recorded in
        ``refusals`` / ``rejected`` and nothing spooled. Never
        raises. Coordinates are used exactly as supplied; a
        region's name is never turned into coordinates."""
        moment = self._now(now)
        region_name = None if region is None else str(region).strip()
        if not region_name:
            self._refuse(None, None, None, "missing_region", moment)
            return None
        if grid_x is None or grid_y is None:
            self._refuse(
                region_name, None, None, "missing_grid_coordinates", moment
            )
            return None
        try:
            x = _validate_grid_coord(grid_x, "x")
            y = _validate_grid_coord(grid_y, "y")
        except (ValueError, TypeError) as exc:
            self._refuse(region_name, None, None, str(exc), moment)
            return None
        if self.fetcher is None:
            self._refuse(region_name, x, y, "no_fetcher_configured", moment)
            return None
        if self.spool_dir is None:
            self._refuse(
                region_name, x, y, "no_spool_directory_configured", moment
            )
            return None
        try:
            self.check_fetch_allowed(now=moment)
        except PermissionError as exc:
            self._refuse(region_name, x, y, f"rate_limited: {exc}", moment)
            return None
        url = build_map_tile_url(x, y)
        # The GET is the load the pacing governs: the slot is
        # consumed by invoking the fetcher, whatever its
        # payload then proves to be.
        self._fetch_times.append(moment)
        try:
            payload = self.fetcher(url)
        except Exception as exc:  # noqa: BLE001 — fetch never raises
            self._refuse(
                region_name, x, y, f"fetch_error: {exc}", moment
            )
            return None
        if isinstance(payload, (bytearray, memoryview)):
            payload = bytes(payload)
        if not isinstance(payload, bytes) or not payload:
            self._refuse(region_name, x, y, "empty_payload", moment)
            return None
        if not payload.startswith(_JPEG_MAGIC):
            self._refuse(region_name, x, y, "not_a_jpeg", moment)
            return None
        filename = (
            f"map-1-{x}-{y}-{_region_slug(region_name)}-"
            f"{int(moment)}.jpg"
        )
        spool_path = Path(self.spool_dir) / filename
        try:
            spool_path.parent.mkdir(parents=True, exist_ok=True)
            spool_path.write_bytes(payload)
        except OSError as exc:
            self._refuse(
                region_name, x, y, f"spool_error: {exc}", moment
            )
            return None
        record = MapTileRecord(
            source=MAP_TILE_SOURCE,
            region=region_name,
            grid_x=x,
            grid_y=y,
            fetched_at=moment,
            spool_path=str(spool_path),
            byte_count=len(payload),
            url=url,
        )
        self._records.append(record)
        self._current_region = region_name
        self._current_grid = (x, y)
        self._emit_observation(record)
        return record

    def _emit_observation(self, record: MapTileRecord) -> VisualObservation:
        """Record (and offer to the callback) the observation
        one landed tile constitutes: an attributed image
        observation about a place, labelled a map image."""
        observation = VisualObservation(
            source=record.source,
            label=MAP_IMAGE_LABEL,
            region=record.region,
            grid_x=record.grid_x,
            grid_y=record.grid_y,
            fetched_at=record.fetched_at,
            spool_path=record.spool_path,
            byte_count=record.byte_count,
            url=record.url,
        )
        self._observations.append(observation)
        if self.on_observation is not None:
            self.on_observation(observation)
        return observation

    def record_observation(
        self,
        region: str,
        grid_x: Any,
        grid_y: Any,
        *,
        spool_path: str,
        byte_count: Any = None,
        source: str | None = None,
        now: float | None = None,
    ) -> VisualObservation:
        """File a visual observation reported from elsewhere
        (the ingest path for a tile already fetched/spooled).

        Raises ValueError / TypeError for a missing region, a
        source that is not ``map_tile`` (V1 holds map images
        only), invalid coordinates, no spool path, or an
        invalid byte count. Files the provenance record and
        the observation exactly as reported — a held record of
        a reported fetch, never promoted to a fact."""
        region_name = str(region or "").strip()
        if not region_name:
            raise ValueError("a visual observation needs a region")
        if source is not None and str(source) != MAP_TILE_SOURCE:
            raise ValueError(
                f"V1 holds map images only, not source {source!r}"
            )
        x = _validate_grid_coord(grid_x, "x")
        y = _validate_grid_coord(grid_y, "y")
        if not spool_path or not str(spool_path).strip():
            raise ValueError("a visual observation needs a spool path")
        count: int | None = None
        if byte_count is not None:
            if isinstance(byte_count, bool) or not isinstance(
                byte_count, int
            ):
                raise TypeError("a byte count must be a non-negative integer")
            if byte_count < 0:
                raise ValueError("a byte count must be non-negative")
            count = int(byte_count)
        moment = self._now(now)
        record = MapTileRecord(
            source=MAP_TILE_SOURCE,
            region=region_name,
            grid_x=x,
            grid_y=y,
            fetched_at=moment,
            spool_path=str(spool_path),
            byte_count=0 if count is None else count,
            url=build_map_tile_url(x, y),
        )
        self._records.append(record)
        self._current_region = region_name
        self._current_grid = (x, y)
        return self._emit_observation(record)

    # -- region-change trigger ---------------------------------------------------
    def handle_region_changed(
        self,
        region: str | None = None,
        grid_x: Any = None,
        grid_y: Any = None,
        *,
        now: float | None = None,
    ) -> MapTileRecord | None:
        """Record a region change; fetch its tile when possible.

        The region (and coordinates, when carried) are recorded
        exactly as supplied. A fetch is attempted only when the
        change carried BOTH coordinates AND a fetcher and spool
        directory are configured — coordinates are never
        carried over from another region or guessed from the
        name, so a coordinate-less change honestly fetches
        nothing. Returns the tile record when a fetch landed,
        else None (a rate-limited or failed fetch is recorded
        in ``refusals`` by :meth:`fetch_map_tile`)."""
        region_name = None if region is None else str(region).strip()
        if not region_name:
            raise ValueError("a region change without a region cannot be filed")
        self._current_region = region_name
        coords: tuple[int, int] | None = None
        if grid_x is not None and grid_y is not None:
            coords = (
                _validate_grid_coord(grid_x, "x"),
                _validate_grid_coord(grid_y, "y"),
            )
            self._current_grid = coords
        if coords is None or self.fetcher is None or self.spool_dir is None:
            return None
        return self.fetch_map_tile(
            region_name, coords[0], coords[1], now=now
        )

    # -- ingest ---------------------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: file what a seam event carries.

        Never raises — a malformed or unknown entry is recorded
        in ``rejected`` and returns None, so one bad event cannot
        stop the perception stream (the dispatcher's own
        isolation rule). Transport-agnostic, like the HUD
        ingest: any transport that can hand this store a
        :class:`SeamEvent` can drive it."""
        if event.name not in VISUAL_EVENTS:
            self.rejected.append(("not_a_visual_event", event.name))
            return None
        try:
            handler = {
                "region_changed": self._ingest_region_changed,
                "visual_observation": self._ingest_visual_observation,
            }[event.name]
            return handler(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    @staticmethod
    def _first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
        return None

    def _region_and_coords(
        self, payload: dict[str, Any]
    ) -> tuple[str | None, Any, Any]:
        region = self._first_present(payload, _REGION_KEYS)
        grid = payload.get("grid") or payload.get("grid_coords") or {}
        if not isinstance(grid, dict):
            grid = {}
        grid_x = self._first_present(payload, _GRID_X_KEYS)
        if grid_x is None:
            grid_x = self._first_present(grid, _GRID_X_KEYS)
        grid_y = self._first_present(payload, _GRID_Y_KEYS)
        if grid_y is None:
            grid_y = self._first_present(grid, _GRID_Y_KEYS)
        return (
            None if region is None else str(region),
            grid_x,
            grid_y,
        )

    def _ingest_region_changed(self, event: SeamEvent) -> Any:
        # A region-changed event IS the trigger doc 16 names:
        # record the region, and fetch when the event carried
        # coordinates and a fetcher is configured.
        region, grid_x, grid_y = self._region_and_coords(event.payload)
        if not region or not region.strip():
            raise ValueError("a region change without a region cannot be filed")
        moment = (
            self._payload_timestamp(event.payload, self._now(None))
            if "timestamp" in event.payload
            else None
        )
        return self.handle_region_changed(
            region, grid_x, grid_y, now=moment
        )

    def _ingest_visual_observation(self, event: SeamEvent) -> VisualObservation:
        payload = event.payload
        region, grid_x, grid_y = self._region_and_coords(payload)
        if not region or not region.strip():
            raise ValueError("a visual observation needs a region")
        if grid_x is None or grid_y is None:
            raise ValueError(
                "a visual observation without grid coordinates cannot be filed"
            )
        moment = (
            self._payload_timestamp(payload, self._now(None))
            if "timestamp" in payload
            else None
        )
        return self.record_observation(
            region,
            grid_x,
            grid_y,
            spool_path=str(
                payload.get("spool_path") or payload.get("path") or ""
            ),
            byte_count=payload.get("byte_count", payload.get("bytes")),
            source=payload.get("source"),
            now=moment,
        )

    # -- purge -------------------------------------------------------------------------
    def purge_region(self, region: str) -> int:
        """Remove one region's spooled tiles and records; count
        the tile records removed (place purge).

        The region's spool files are deleted (a file already
        gone is not an error), its provenance records and
        observations are dropped, and if it was the current
        region with no tile left, the current-region record is
        cleared — the vision label then honestly falls back if
        no other region's map remains."""
        if not region:
            return 0
        key = str(region)
        removed_records = [r for r in self._records if r.region == key]
        for record in removed_records:
            try:
                Path(record.spool_path).unlink(missing_ok=True)
            except OSError:
                pass  # records are dropped regardless; files best-effort
        self._records = [r for r in self._records if r.region != key]
        self._observations = [
            o for o in self._observations if o.region != key
        ]
        if self._current_region == key:
            # No tile for this region can remain (all were just
            # dropped), so it is no longer the current region.
            self._current_region = None
            self._current_grid = None
        return len(removed_records)

    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident's records; always removes nothing.

        Map tiles are region/place data: they carry no resident
        identity — no resident id, name, or image of a person
        exists anywhere in this store — so there is nothing of
        a resident's to purge. The method exists so uniform
        per-resident purge loops can call it and get the honest
        answer. (Removing a place's tiles is
        :meth:`purge_region`.)"""
        return 0


__all__ = [
    "DEFAULT_MAX_FETCHES_PER_HOUR",
    "DEFAULT_MIN_FETCH_INTERVAL_SECONDS",
    "MAP_IMAGE_LABEL",
    "MAP_TILE_SOURCE",
    "VISION_MAP_LABEL",
    "VISION_NONE_LABEL",
    "VISUAL_EVENTS",
    "FetchRefusal",
    "MapTileFetcher",
    "MapTileRecord",
    "VisualObservation",
    "VisualState",
    "VisualStore",
    "build_map_tile_url",
    "map_tile_url",
    "urllib_map_tile_fetcher",
]
