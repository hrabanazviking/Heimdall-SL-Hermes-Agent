"""LSL companion HUD ingest — Phase 3 slice 3 (doc 03 §7.6, doc 07 §5).

The worn companion HUD is a sixth sense organ: on a 60 s timer it
samples the wearer's situation in-world and POSTs one compact HUD1
digest line off-world (see ``heimdall-lsl/README-hud.md`` — the wire
format spec both ends are written against, and
``heimdall-lsl/heimdall_companion_hud.lsl``, the emitter).

This module is the off-world mirror. It is **transport-agnostic by
design**: :class:`HudIngest` exposes a plain ``ingest(payload)``
callable that the runtime can mount behind any HTTP endpoint (the
choice is stated in the slice-3 commit: no new server surface is
invented for a transport whose live exercise is gated on Phase 1).
Parsing is pure and separately testable via :func:`parse_hud_payload`.

Laws carried here, enforced, not by convention:

- **Fail closed, whole.** The payload is parsed and validated
  completely before anything is applied. A malformed digest is
  rejected whole and counted (``rejected_count``); no field of a bad
  payload ever reaches the tracker or the stored records.
- **Coarse tier only (doc 03 §2.1/§7.7).** ``llGetAgentList`` keys are
  reduced to anonymous positions in-world; this side they may only
  feed :meth:`PresenceTracker.update_coarse` — count + positions.
  There is no code path here that creates a full-tier avatar, and no
  name exists in a HUD1 payload to fabricate one from.
- **Attributed sensor data.** A HUD report is a sensor report, not a
  canonical fact merely because the HUD reported it (the Phase I0
  utterance/fact separation, applied to senses). Every record and
  event produced here carries ``source="hud"`` attribution.
- **No secrets retained.** The optional body-carried ``auth`` field
  (doc 07 §2.2/§5.3) is checked against the configured expected
  secret when one is set, and is never stored on any record.
- **Per-resident purge.** HUD ingest retains no per-resident data at
  all (no names, no keys — by format), so :meth:`HudIngest.purge`
  is trivially complete: it clears the coarse census and the stored
  conditions/place records.
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from .presence import Position, PresenceTracker

# Format tag + version — must match the LSL emitter exactly. The
# test-suite asserts this tag string appears verbatim in the .lsl file.
FORMAT_TAG = "HUD1"
FORMAT_VERSION = "1"

SOURCE = "hud"

# LSL-side caps mirrored here so an out-of-range payload fails closed
# instead of being trusted (doc 07 §§2.5/3.2, doc 03 §2.3).
MAX_CENSUS_AGENTS = 100  # llGetAgentList ceiling
MAX_CENSUS_POSITIONS = 32  # emitter cap (README-hud.md)
MAX_SENSOR_RESULTS = 16  # llSensor returns at most the 16 nearest
MAX_COORD = 4096.0  # generous altitude bound; x/y are region-local

_MANDATORY_KEYS = (
    "v",
    "ts",
    "region",
    "fps",
    "dil",
    "agents",
    "apos",
    "self_pos",
    "self_flags",
    "env_day_len",
    "env_day_off",
    "env_sec",
    "parcel",
    "parcel_area",
    "parcel_flags",
    "parcel_id",
    "sensor_count",
    "sensor_pos",
    "free_mem",
)
_ALLOWED_KEYS = frozenset(_MANDATORY_KEYS) | {"auth"}

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NULL_KEY = "00000000-0000-0000-0000-000000000000"


class HudPayloadError(ValueError):
    """A HUD1 payload that failed validation. Always whole-payload."""


@dataclass(frozen=True)
class HudDigest:
    """One fully validated HUD1 digest. Sensor data, attributed."""

    source: str
    timestamp: int
    region: str
    region_fps: float
    time_dilation: float
    census_reported_count: int
    census_positions: tuple[Position, ...]
    self_position: Position
    self_flags: int
    env_day_length_s: int
    env_day_offset_s: int
    env_secs_since_midnight: float
    parcel_name: str
    parcel_area: int
    parcel_flags: int
    parcel_id: str
    sensor_count: int
    sensor_positions: tuple[Position, ...]
    free_memory: int


@dataclass(frozen=True)
class HudConditions:
    """Region-health / environment facts from one digest (attributed)."""

    source: str
    region: str
    region_fps: float
    time_dilation: float
    census_reported_count: int
    self_position: Position
    self_flags: int
    env_day_length_s: int
    env_day_offset_s: int
    env_secs_since_midnight: float
    sensor_count: int
    free_memory: int
    at: float


@dataclass(frozen=True)
class HudPlace:
    """Place facts from one digest: region + parcel (attributed)."""

    source: str
    region: str
    parcel_name: str
    parcel_area: int
    parcel_flags: int
    parcel_id: str
    at: float


@dataclass(frozen=True)
class HudEvent:
    """An awareness-level event derived from a digest.

    These are HUD-vocabulary events for the caller to route (the
    dispatcher's §7.3 canonical vocabulary has no HUD entries, and
    inventing aliases there would let an unknown event type reach
    the WYRD sink — so ingest returns its events in the result
    instead of pushing them through the seam dispatcher).
    """

    name: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class HudIngestResult:
    digest: HudDigest
    conditions: HudConditions
    place: HudPlace
    events: tuple[HudEvent, ...]


def _fail(reason: str) -> HudPayloadError:
    return HudPayloadError(reason)


def _parse_int(raw: str, key: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise _fail(f"{key}: not an integer: {raw!r}") from None


def _parse_float(raw: str, key: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise _fail(f"{key}: not a float: {raw!r}") from None
    if not math.isfinite(value):
        raise _fail(f"{key}: not finite: {raw!r}")
    return value


def _parse_vector(raw: str, key: str) -> Position:
    text = raw.strip()
    if not (text.startswith("<") and text.endswith(">")):
        raise _fail(f"{key}: vector must be <x, y, z>: {raw!r}")
    parts = [p.strip() for p in text[1:-1].split(",")]
    if len(parts) != 3:
        raise _fail(f"{key}: vector needs exactly 3 components: {raw!r}")
    coords = tuple(_parse_float(p, key) for p in parts)
    for c in coords:
        if abs(c) > MAX_COORD:
            raise _fail(f"{key}: coordinate out of range: {raw!r}")
    return (coords[0], coords[1], coords[2])


def _parse_positions(raw: str, key: str) -> tuple[Position, ...]:
    if raw == "":
        return ()
    return tuple(_parse_vector(part, key) for part in raw.split(";"))


def _parse_text(raw: str, key: str) -> str:
    text = unquote(raw)
    if any(ord(ch) < 0x20 for ch in text):
        raise _fail(f"{key}: control characters are not accepted")
    return text


def parse_hud_payload(payload: str | bytes) -> HudDigest:
    """Parse + validate one HUD1 payload. Raises HudPayloadError.

    Pure: either a complete :class:`HudDigest` comes back, or nothing
    is produced at all — there is no partial-parse result to apply.
    The ``auth`` field, when present, is validated for shape only
    here (a single token); the secret comparison lives in
    :class:`HudIngest`, which owns the expected value.
    """
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise _fail("payload is not valid UTF-8") from None
    else:
        text = payload
    text = text.strip()
    if not text or "\n" in text or "\r" in text:
        raise _fail("payload must be exactly one non-empty line")
    fields = text.split("|")
    if fields[0] != FORMAT_TAG:
        raise _fail(f"bad format tag: {fields[0]!r} (expected {FORMAT_TAG!r})")

    raw_fields: dict[str, str] = {}
    for part in fields[1:]:
        key, sep, value = part.partition("=")
        if not sep or not key:
            raise _fail(f"malformed field: {part!r}")
        if key in raw_fields:
            raise _fail(f"duplicate field: {key!r}")
        if key not in _ALLOWED_KEYS:
            raise _fail(f"unknown field: {key!r}")
        raw_fields[key] = value
    missing = [k for k in _MANDATORY_KEYS if k not in raw_fields]
    if missing:
        raise _fail(f"missing fields: {', '.join(missing)}")
    if raw_fields["v"] != FORMAT_VERSION:
        raise _fail(f"unsupported version: {raw_fields['v']!r}")

    timestamp = _parse_int(raw_fields["ts"], "ts")
    if timestamp <= 0:
        raise _fail("ts: must be a positive unix timestamp")
    region_fps = _parse_float(raw_fields["fps"], "fps")
    if region_fps < 0:
        raise _fail("fps: must be >= 0")
    dilation = _parse_float(raw_fields["dil"], "dil")
    if not 0.0 <= dilation <= 1.0:
        raise _fail("dil: time dilation is 0.0-1.0 by definition")
    agents = _parse_int(raw_fields["agents"], "agents")
    if not 0 <= agents <= MAX_CENSUS_AGENTS:
        raise _fail(f"agents: outside llGetAgentList range 0-{MAX_CENSUS_AGENTS}")
    census_positions = _parse_positions(raw_fields["apos"], "apos")
    if len(census_positions) > MAX_CENSUS_POSITIONS:
        raise _fail("apos: exceeds the emitter's census-position cap")
    if len(census_positions) > agents:
        raise _fail("apos: more positions than the reported census count")
    sensor_count = _parse_int(raw_fields["sensor_count"], "sensor_count")
    if not 0 <= sensor_count <= MAX_SENSOR_RESULTS:
        raise _fail(f"sensor_count: outside llSensor range 0-{MAX_SENSOR_RESULTS}")
    sensor_positions = _parse_positions(raw_fields["sensor_pos"], "sensor_pos")
    if len(sensor_positions) != sensor_count:
        raise _fail("sensor_pos: count does not match sensor_count")
    parcel_id = raw_fields["parcel_id"]
    if parcel_id != _NULL_KEY and not _UUID_RE.match(parcel_id):
        raise _fail(f"parcel_id: not a UUID: {parcel_id!r}")
    env_day_length = _parse_int(raw_fields["env_day_len"], "env_day_len")
    env_day_offset = _parse_int(raw_fields["env_day_off"], "env_day_off")
    if env_day_length < 0 or env_day_offset < 0:
        raise _fail("env_day_len/env_day_off: must be >= 0")
    free_memory = _parse_int(raw_fields["free_mem"], "free_mem")
    if free_memory < 0:
        raise _fail("free_mem: must be >= 0")
    parcel_area = _parse_int(raw_fields["parcel_area"], "parcel_area")
    if parcel_area < 0:
        raise _fail("parcel_area: must be >= 0")

    return HudDigest(
        source=SOURCE,
        timestamp=timestamp,
        region=_parse_text(raw_fields["region"], "region"),
        region_fps=region_fps,
        time_dilation=dilation,
        census_reported_count=agents,
        census_positions=census_positions,
        self_position=_parse_vector(raw_fields["self_pos"], "self_pos"),
        self_flags=_parse_int(raw_fields["self_flags"], "self_flags"),
        env_day_length_s=env_day_length,
        env_day_offset_s=env_day_offset,
        env_secs_since_midnight=_parse_float(raw_fields["env_sec"], "env_sec"),
        parcel_name=_parse_text(raw_fields["parcel"], "parcel"),
        parcel_area=parcel_area,
        parcel_flags=_parse_int(raw_fields["parcel_flags"], "parcel_flags"),
        parcel_id=parcel_id,
        sensor_count=sensor_count,
        sensor_positions=sensor_positions,
        free_memory=free_memory,
    )


@dataclass
class HudIngest:
    """Stateful HUD ingest: parse → coarse tier + records + events.

    ``tracker`` may be None, in which case ingest still parses and
    returns records/events but touches no presence state.
    ``expected_secret``: when set, a payload must carry a matching
    ``auth`` field or it is rejected whole; when unset, a payload
    carrying ``auth`` is accepted and the value is dropped, never
    stored (the deployment that cares sets the secret).
    """

    tracker: PresenceTracker | None = None
    expected_secret: str | None = None
    on_events: Callable[[tuple[HudEvent, ...]], None] | None = None
    _clock: Callable[[], float] = field(default=time.monotonic)
    accepted_count: int = field(default=0, init=False)
    rejected_count: int = field(default=0, init=False)
    last_conditions: HudConditions | None = field(default=None, init=False)
    last_place: HudPlace | None = field(default=None, init=False)
    last_digest: HudDigest | None = field(default=None, init=False)

    def ingest(
        self, payload: str | bytes, *, now: float | None = None
    ) -> HudIngestResult | None:
        """Ingest one payload. None (and a counted rejection) if bad.

        Nothing is applied until the payload — including the secret
        check — has validated completely.
        """
        try:
            digest = parse_hud_payload(payload)
            if self.expected_secret is not None:
                auth = self._extract_auth(payload)
                if auth is None or auth != self.expected_secret:
                    raise _fail("auth: missing or mismatched shared secret")
        except HudPayloadError:
            self.rejected_count += 1
            return None

        at = self._clock() if now is None else float(now)
        conditions = HudConditions(
            source=SOURCE,
            region=digest.region,
            region_fps=digest.region_fps,
            time_dilation=digest.time_dilation,
            census_reported_count=digest.census_reported_count,
            self_position=digest.self_position,
            self_flags=digest.self_flags,
            env_day_length_s=digest.env_day_length_s,
            env_day_offset_s=digest.env_day_offset_s,
            env_secs_since_midnight=digest.env_secs_since_midnight,
            sensor_count=digest.sensor_count,
            free_memory=digest.free_memory,
            at=at,
        )
        place = HudPlace(
            source=SOURCE,
            region=digest.region,
            parcel_name=digest.parcel_name,
            parcel_area=digest.parcel_area,
            parcel_flags=digest.parcel_flags,
            parcel_id=digest.parcel_id,
            at=at,
        )
        events = (
            HudEvent(
                "hud_census",
                {
                    "source": SOURCE,
                    "region": digest.region,
                    "reported_count": digest.census_reported_count,
                    "positions": [list(p) for p in digest.census_positions],
                },
            ),
            HudEvent(
                "hud_conditions",
                {
                    "source": SOURCE,
                    "region": digest.region,
                    "region_fps": digest.region_fps,
                    "time_dilation": digest.time_dilation,
                    "self_flags": digest.self_flags,
                    "env_day_length_s": digest.env_day_length_s,
                    "env_secs_since_midnight": digest.env_secs_since_midnight,
                    "sensor_count": digest.sensor_count,
                    "free_memory": digest.free_memory,
                },
            ),
            HudEvent(
                "hud_place",
                {
                    "source": SOURCE,
                    "region": digest.region,
                    "parcel": digest.parcel_name,
                    "parcel_area": digest.parcel_area,
                    "parcel_flags": digest.parcel_flags,
                    "parcel_id": digest.parcel_id,
                },
            ),
        )

        # Apply — only now, with a fully validated digest in hand.
        # COARSE TIER ONLY: census positions replace the anonymous
        # census; the full-fidelity tier is never touched here.
        if self.tracker is not None:
            self.tracker.update_coarse(digest.census_positions, now=at)
        self.last_digest = digest
        self.last_conditions = conditions
        self.last_place = place
        self.accepted_count += 1
        if self.on_events is not None:
            self.on_events(events)
        return HudIngestResult(
            digest=digest, conditions=conditions, place=place, events=events
        )

    @staticmethod
    def _extract_auth(payload: str | bytes) -> str | None:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        for part in text.strip().split("|")[1:]:
            key, sep, value = part.partition("=")
            if sep and key == "auth":
                return value
        return None

    def purge(self) -> None:
        """Per-resident purge (doc 05): drop everything HUD-derived.

        HUD ingest retains no per-resident data by format (no names,
        no keys), so clearing the coarse census plus the stored
        records is a complete purge of HUD-held state.
        """
        if self.tracker is not None:
            self.tracker.clear_coarse()
        self.last_digest = None
        self.last_conditions = None
        self.last_place = None
