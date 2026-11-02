"""Synthetic view — visual capture doc 16 slice V3 (Route C).

Doc 16's Route C: a picture she *computes*, not one she sees.
A protocol client receives structured streams (objects,
positions, scales), never a rendered frame; a Heimdall
renderer rasterises the known scene from her camera position
into a flat-shaded image plus a depth map and hands *that* to
image understanding, labelled synthetic. Its honest limits,
named there: avatars, animesh, PBR materials, particles and
lighting are absent or crude, and the X-ray problem means the
render must occlusion-cull or it will "show" her things no eye
could see — a depth-buffer render culls naturally, a list does
not. Doc 16 also rules that this route shares one meshing
layer with doc 14's M2 traversability raster, built once: the
geometry store here is that layer on the seam side, fed by the
same ``object_info`` events and the same carried
position + scale/size field names M2 and F6 already read.

This module is the seam/API side of V3 only: a geometry store
fed by ``object_info`` events, a shared meshing layer that
turns each fully-carried object into an axis-aligned box mesh,
and a software rasteriser (stdlib only) that renders that mesh
set from a supplied camera into a depth buffer, an object-id
buffer, a visible-object list, and a real PNG. No Core (C#)
change, no login, and no network anywhere — rendering is a
local computation over data events already carried.

Bounds, enforced here in the body, not by convention:

- **Geometry is exactly what events carried, nothing
  invented.** An object becomes a box mesh only when it
  carries BOTH a valid position and a valid carried
  scale/size (the F6/navigation field names: ``position`` /
  ``pos``; ``scale`` / ``size`` / ``dimensions`` as a mapping,
  a sequence, or per-axis ``scale_x`` / ``size_x`` … keys).
  The box is centred at the carried position with the carried
  dimensions — 8 vertices, 12 triangles, 6 faces. An object
  without a carried scale is held as known-but-unmeshable
  (counted, with its reason, in the state), NEVER given an
  invented size; a partially carried scale is likewise
  unmeshable, never completed by a guess.
- **Rotation is held honestly, never faked.** No sibling
  module in this codebase parses a rotation (F6 holds none,
  M2's footprints are axis-aligned), so there is no
  established rotation handling to reuse. An object carrying
  a non-identity rotation (quaternion or Euler form) is
  therefore held unmeshable with the reason
  ``rotated_non_identity`` until a rotation-aware mesher
  lands — it is never silently rendered as axis-aligned. An
  explicitly identity rotation meshes normally.
- **Occlusion is by construction.** Rasterisation runs a
  true per-pixel depth buffer over the meshed triangles
  (perspective-correct depth, near-plane clipped): a nearer
  box hides a farther box behind it, an object behind the
  camera never appears, and a camera looking away renders
  background only. Background pixels carry depth ``None``
  and object id ``None`` — absence is spoken, not zeroed.
- **Colour is a synthetic palette, labelled as such.** Each
  object's base colour is derived deterministically from its
  object id (SHA-256 based) and flat-shaded per face from the
  face normal against one fixed, documented light direction.
  It is a computed palette for telling objects apart — never
  a claimed real colour, texture, or material (textures are
  doc 16's V2, not built here).
- **A synthetic render is never "what Yrsa sees".** Every
  record and observation carries ``source="synthetic_render"``
  and the label ``synthetic_render``; the observation is an
  attributed *computed* observation in the existing
  ``visual_observation`` seam vocabulary, and the felt vision
  state names itself honestly: ``vision: none (object-data
  only)`` before any render lands, ``vision: synthetic view
  only`` once one has. The object-set timestamp (the latest
  ingest/update time, on an injectable clock) rides every
  render and record, so a stale scene can never pose as a
  fresh look.
- **Avatars are out of scope.** This store ingests
  ``object_info`` only; avatar/presence events are rejected
  into the audit like any other non-object event and are
  never drawn.
- **Fail-closed ingest, never raises.** A malformed entry
  (missing id, a present-but-invalid position or scale, NaN
  or non-finite numbers, a malformed rotation) is recorded
  in the ``rejected`` audit and applies nothing, per entry
  for list payloads, in the sibling stores' shape. An absent
  position or scale is not malformed — it is a known object
  held unmeshable with its reason.
- **Purge is per-resident, exactly as documented on
  :meth:`SyntheticStore.purge_resident`.** Spool files and
  records are removed only where a render's included-object
  set contains a purged object.

Scope honesty: this is V3's first cut, seam side only. There
are no textures (doc 16 V2), no avatar rendering (explicitly
out of V3), no mesh/sculpt asset geometry (boxes from carried
prim scale only), no rotation-aware meshing, and no Core leg:
the Core already emits the ``object_info`` events this store
consumes, and rendering emits no wire event of its own. No
new seam vocabulary: ``object_info`` and
``visual_observation`` already ride ``core/models.py``, which
this slice does not touch. If V1's ``VisualStore`` later
grows an ingest path for locally produced images, a spooled
synthetic render can be filed through it unchanged — its
record already carries source, label, spool path, byte count
and timestamps in V1's provenance shape; this slice does not
modify ``visual.py``. ``core/governor.py`` is untouched.
"""
from __future__ import annotations

import hashlib
import math
import struct
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.models import SeamEvent

# Canonical seam event names this store ingests. ``object_info``
# already rides the base vocabulary (ObjectInfo / object_info);
# avatar and presence events are deliberately NOT here — they
# are rejected, never drawn (doc 16: avatar rendering is out of
# V3's first cut). No name is duplicated in core/models.py.
SYNTHETIC_EVENTS = frozenset({"object_info"})

# Provenance source + image label for everything V3 produces.
# The label is what the observation IS: a synthetic render — a
# computed view, never a photograph, never "what Yrsa sees".
SYNTHETIC_SOURCE = "synthetic_render"
SYNTHETIC_LABEL = "synthetic_render"

# The digest vision wordings, consistent with V1's honest
# labelling pattern (visual.py): before any render the vision
# state is the digest's existing posture; after one, the view
# names itself as the computed thing it is.
VISION_NONE_LABEL = "none (object-data only)"
VISION_SYNTHETIC_LABEL = "synthetic view only"

# Render defaults: a small frame is the point — this is a
# computed sense impression, not a screenshot. All three are
# configurable per render.
DEFAULT_RENDER_WIDTH = 96
DEFAULT_RENDER_HEIGHT = 72
DEFAULT_FOV_DEGREES = 60.0

# The one fixed light for flat shading: a direction TOWARD the
# light, in world axes, normalised at use. Documented and
# constant so shading is deterministic and never mistaken for
# in-world lighting (which V3 does not model).
LIGHT_DIRECTION = (0.5, 0.5, 1.0)

# Unmeshable reasons — the exact strings current_state() and
# describe() report, so a gap is named, never hidden.
REASON_MISSING_POSITION = "missing_position"
REASON_MISSING_SCALE = "missing_scale"
REASON_INCOMPLETE_SCALE = "incomplete_scale"
REASON_ROTATED = "rotated_non_identity"

_NEAR_PLANE = 1e-6
_WORLD_UP = (0.0, 0.0, 1.0)
_FALLBACK_UP = (0.0, 1.0, 0.0)

Vec3 = tuple[float, float, float]

_ID_KEYS = ("object_id", "id", "uuid")
_NAME_KEYS = ("name", "object_name")
_OWNER_KEYS = ("owner_id", "owner", "owner_uuid", "creator_id", "creator")


def _num(value: Any, label: str) -> float:
    """Validate one number strictly; return it as float.

    Real ints/floats pass; booleans, strings, NaN and infinity
    fail closed — a number is never coerced from text."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"a {label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"a {label} must be finite")
    return result


def _vec3(value: Any, label: str) -> Vec3:
    """Validate an (x, y, z) vector from a mapping or a triple."""
    if isinstance(value, dict):
        if not all(axis in value for axis in ("x", "y", "z")):
            raise ValueError(f"a {label} needs x, y and z")
        return (
            _num(value["x"], f"{label} x"),
            _num(value["y"], f"{label} y"),
            _num(value["z"], f"{label} z"),
        )
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return (
            _num(value[0], f"{label} x"),
            _num(value[1], f"{label} y"),
            _num(value[2], f"{label} z"),
        )
    raise TypeError(f"a {label} must be an (x, y, z) triple")


def _first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalised(v: Vec3, label: str) -> Vec3:
    length = math.sqrt(_dot(v, v))
    if length <= 0.0 or not math.isfinite(length):
        raise ValueError(f"a {label} must be a non-zero vector")
    return (v[0] / length, v[1] / length, v[2] / length)


def base_colour_for(object_id: str) -> tuple[int, int, int]:
    """The synthetic palette colour for one object id.

    Deterministic across runs and machines (SHA-256 of the id,
    never the salted built-in hash): three bytes mapped into a
    mid range so no object renders near-black or near-white by
    accident. This is a computed palette for telling objects
    apart — labelled synthetic, never a real colour."""
    digest = hashlib.sha256(str(object_id).encode("utf-8")).digest()
    return (
        64 + digest[0] % 160,
        64 + digest[1] % 160,
        64 + digest[2] % 160,
    )


# The box topology, shared by every mesh: vertices are the 8
# centre ± half-dimension corners in the order
# (-,-,-) (+,-,-) (+,+,-) (-,+,-) (-,-,+) (+,-,+) (+,+,+) (-,+,+),
# and the 12 triangles below wind counter-clockwise seen from
# outside, two per face in face order -z, +z, -y, +y, -x, +x.
_BOX_SIGNS = (
    (-1.0, -1.0, -1.0),
    (1.0, -1.0, -1.0),
    (1.0, 1.0, -1.0),
    (-1.0, 1.0, -1.0),
    (-1.0, -1.0, 1.0),
    (1.0, -1.0, 1.0),
    (1.0, 1.0, 1.0),
    (-1.0, 1.0, 1.0),
)
_BOX_TRIANGLES = (
    (0, 2, 1), (0, 3, 2),  # -z
    (4, 5, 6), (4, 6, 7),  # +z
    (0, 1, 5), (0, 5, 4),  # -y
    (3, 7, 6), (3, 6, 2),  # +y
    (0, 4, 7), (0, 7, 3),  # -x
    (1, 2, 6), (1, 6, 5),  # +x
)
_BOX_FACE_NORMALS = (
    (0.0, 0.0, -1.0),
    (0.0, 0.0, 1.0),
    (0.0, -1.0, 0.0),
    (0.0, 1.0, 0.0),
    (-1.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
)


@dataclass
class BoxMesh:
    """One object's axis-aligned box mesh: exactly the carried
    position (centre) and carried dimensions, meshed."""

    object_id: str
    centre: Vec3
    dimensions: Vec3
    vertices: tuple[Vec3, ...]
    triangles: tuple[tuple[int, int, int], ...]
    face_normals: tuple[Vec3, ...]

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def triangle_count(self) -> int:
        return len(self.triangles)


def build_box_mesh(object_id: str, centre: Vec3, dimensions: Vec3) -> BoxMesh:
    """Mesh one box from a centre and full dimensions.

    Pure geometry: 8 vertices at centre ± half-dimensions on
    each axis, the shared 12-triangle / 6-face topology with
    outward normals. Dimensions must be positive and finite —
    the caller (the store) has already validated them."""
    half = (dimensions[0] / 2.0, dimensions[1] / 2.0, dimensions[2] / 2.0)
    vertices = tuple(
        (
            centre[0] + sign[0] * half[0],
            centre[1] + sign[1] * half[1],
            centre[2] + sign[2] * half[2],
        )
        for sign in _BOX_SIGNS
    )
    return BoxMesh(
        object_id=str(object_id),
        centre=tuple(centre),  # type: ignore[arg-type]
        dimensions=tuple(dimensions),  # type: ignore[arg-type]
        vertices=vertices,
        triangles=_BOX_TRIANGLES,
        face_normals=_BOX_FACE_NORMALS,
    )


@dataclass
class SyntheticObject:
    """One known object, held exactly as its events carried it.

    ``meshable`` objects have a mesh in the store; unmeshable
    ones carry the named reason and are never rendered."""

    id: str
    name: str | None = None
    owner_id: str | None = None
    position: Vec3 | None = None
    scale: Vec3 | None = None
    rotated: bool = False
    meshable: bool = False
    unmeshable_reason: str | None = None
    updated_at: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class VisibleObject:
    """One object visible in a render: how many pixels it owns
    and the nearest depth at which it appears."""

    object_id: str
    pixel_count: int
    nearest_depth: float


@dataclass
class SyntheticRenderRecord:
    """Provenance for one synthetic render — what was computed,
    from which object set, when, and where its PNG bytes live.

    In V1's provenance shape (source, label, spool path, byte
    count, timestamps) so it can ride the same spool discipline;
    ``spool_path`` is None for a render that was computed but
    not spooled (no spool directory configured)."""

    source: str
    label: str
    camera_position: Vec3
    camera_target: Vec3
    width: int
    height: int
    fov_degrees: float
    object_set_timestamp: float | None
    rendered_at: float
    spool_path: str | None
    byte_count: int
    visible_object_ids: tuple[str, ...]


@dataclass
class SyntheticObservation:
    """One attributed computed observation, in the existing
    ``visual_observation`` seam vocabulary.

    Perception data computed from the known object set —
    labelled a synthetic render. It is never a photograph and
    never what Yrsa literally sees."""

    source: str
    label: str
    camera_position: Vec3
    width: int
    height: int
    object_set_timestamp: float | None
    rendered_at: float
    spool_path: str | None
    byte_count: int
    visible_object_ids: tuple[str, ...]
    event_name: str = "visual_observation"


@dataclass
class RenderResult:
    """The outcome of one render: buffers, visible list, the
    PNG bytes, and the provenance record for the render."""

    width: int
    height: int
    camera_position: Vec3
    camera_target: Vec3
    fov_degrees: float
    depth_buffer: list[list[float | None]]
    object_id_buffer: list[list[str | None]]
    visible: list[VisibleObject]
    object_set_timestamp: float | None
    rendered_at: float
    png_bytes: bytes
    record: SyntheticRenderRecord

    @property
    def is_empty(self) -> bool:
        """True when the render is background only: no object
        owns any pixel (an empty scene, or nothing in view)."""
        return not self.visible

    def to_observation(self) -> SyntheticObservation:
        """This render expressed as a ``visual_observation``:
        an attributed computed observation, labelled
        ``synthetic_render`` — never a photograph."""
        record = self.record
        return SyntheticObservation(
            source=record.source,
            label=record.label,
            camera_position=record.camera_position,
            width=record.width,
            height=record.height,
            object_set_timestamp=record.object_set_timestamp,
            rendered_at=record.rendered_at,
            spool_path=record.spool_path,
            byte_count=record.byte_count,
            visible_object_ids=record.visible_object_ids,
        )


@dataclass
class SyntheticState:
    """A snapshot of the synthetic-view state, honest about its
    gaps: what is meshed, what is held unmeshable and why, how
    many renders exist, and how fresh the object set is."""

    known_objects: int
    meshed: int
    unmeshable: int
    unmeshable_reasons: dict[str, int]
    renders: int
    object_set_timestamp: float | None
    last_rendered_at: float | None
    last_render_visible: int | None
    vision_label: str


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode row-major RGB bytes as a real PNG, stdlib only.

    A minimal zlib + struct writer: signature, IHDR (8-bit
    truecolour), one IDAT of filter-0 scanlines, IEND. The
    output is byte-deterministic for identical inputs."""
    if len(rgb) != width * height * 3:
        raise ValueError("RGB byte count does not match the dimensions")

    def _chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    stride = width * 3
    for row in range(height):
        raw.append(0)
        raw += rgb[row * stride:(row + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


# -- carried-field parsing -------------------------------------------------


def _extract_scale(entry: dict[str, Any]) -> tuple[Vec3 | None, bool]:
    """The carried scale/size of one entry, in F6/M2's names.

    Returns ``(scale, partial)``: a full (x, y, z) scale with
    ``partial=False``; ``(None, True)`` when SOME dimensions
    were carried but not all three (held unmeshable, never
    completed by a guess); ``(None, False)`` when no scale was
    carried at all. A present-but-invalid scale (non-positive,
    non-numeric, NaN, a scalar where a size belongs) raises —
    the entry is malformed, not merely sparse."""
    raw = (
        entry.get("scale")
        or entry.get("size")
        or entry.get("dimensions")
    )
    values: dict[str, Any] = {}
    if isinstance(raw, dict):
        values = {
            "x": raw.get("x", raw.get("width")),
            "y": raw.get("y", raw.get("depth")),
            "z": raw.get("z", raw.get("height")),
        }
    elif isinstance(raw, (list, tuple)):
        for axis, value in zip(("x", "y", "z"), raw):
            values[axis] = value
    elif raw is not None:
        raise TypeError("a carried scale must be a mapping or a sequence")
    else:
        values = {
            "x": entry.get("scale_x", entry.get("size_x")),
            "y": entry.get("scale_y", entry.get("size_y")),
            "z": entry.get("scale_z", entry.get("size_z")),
        }
    present = {axis: v for axis, v in values.items() if v is not None}
    if not present:
        return None, False
    parsed: dict[str, float] = {}
    for axis, value in present.items():
        size = _num(value, f"scale {axis}")
        if size <= 0:
            raise ValueError("an object scale must be positive")
        parsed[axis] = size
    if len(parsed) != 3:
        return None, True
    return (parsed["x"], parsed["y"], parsed["z"]), False


def _extract_rotation(entry: dict[str, Any]) -> bool | None:
    """Whether an entry carries a NON-identity rotation.

    Returns None when no rotation was carried, False for an
    explicitly identity rotation, True for a non-identity one.
    Accepts the two forms an event could carry — a quaternion
    (mapping with ``w``, or a 4-sequence) and Euler angles
    (mapping or 3-sequence, all-zero = identity). Anything
    else present under a rotation key is malformed and raises.
    (No sibling module parses rotation; see the docstring.)"""
    raw = _first_present(
        entry, ("rotation", "orientation", "quaternion", "rot")
    )
    if raw is None:
        return None
    eps = 1e-6
    if isinstance(raw, dict):
        if "w" in raw:
            x = _num(raw.get("x", 0.0), "rotation x")
            y = _num(raw.get("y", 0.0), "rotation y")
            z = _num(raw.get("z", 0.0), "rotation z")
            w = _num(raw["w"], "rotation w")
            return not (
                abs(x) <= eps and abs(y) <= eps and abs(z) <= eps
                and abs(abs(w) - 1.0) <= eps
            )
        if any(axis in raw for axis in ("x", "y", "z")):
            angles = [
                _num(raw.get(axis, 0.0), f"rotation {axis}")
                for axis in ("x", "y", "z")
            ]
            return any(abs(a) > eps for a in angles)
        raise TypeError("a carried rotation mapping has no usable axes")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        x, y, z, w = (_num(v, "rotation") for v in raw)
        return not (
            abs(x) <= eps and abs(y) <= eps and abs(z) <= eps
            and abs(abs(w) - 1.0) <= eps
        )
    if isinstance(raw, (list, tuple)) and len(raw) == 3:
        return any(abs(_num(v, "rotation")) > eps for v in raw)
    raise TypeError("a carried rotation must be a mapping or a sequence")


@dataclass
class SyntheticStore:
    """The shared meshing layer + synthetic renderer.

    Ingest ``object_info`` events, query the held objects and
    their meshes, render the meshed set from a camera, spool
    renders with provenance, and purge per resident."""

    spool_dir: Any = None
    _clock: Any = field(default=time.time)
    _objects: dict[str, SyntheticObject] = field(
        default_factory=dict, init=False
    )
    _meshes: dict[str, BoxMesh] = field(default_factory=dict, init=False)
    _records: list[SyntheticRenderRecord] = field(
        default_factory=list, init=False
    )
    _object_set_timestamp: float | None = field(default=None, init=False)
    _render_count: int = field(default=0, init=False)
    _last_rendered_at: float | None = field(default=None, init=False)
    _last_render_visible: int | None = field(default=None, init=False)
    # Fail-closed audit: (reason, detail) for every refused ingest.
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

    # -- ingest -------------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: file what a seam event carries.

        Never raises — a malformed or unknown entry is recorded
        in ``rejected`` and returns None, so one bad event cannot
        stop the perception stream (the dispatcher's own
        isolation rule). Only ``object_info`` is ingested;
        avatar/presence events are rejected, never drawn."""
        if event.name not in SYNTHETIC_EVENTS:
            self.rejected.append(("not_a_synthetic_event", event.name))
            return None
        payload = event.payload
        entries = payload.get("objects")
        if isinstance(entries, list):
            moment = self._payload_timestamp(payload, self._now(None))
            filed = []
            for entry in entries:
                try:
                    filed.append(self._record_entry(entry, moment))
                except (ValueError, KeyError, TypeError) as exc:
                    self.rejected.append((str(exc), event.name))
            return filed
        try:
            moment = self._payload_timestamp(payload, self._now(None))
            return self._record_entry(payload, moment)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    def _record_entry(
        self, entry: Any, moment: float
    ) -> SyntheticObject:
        if not isinstance(entry, dict):
            raise TypeError("object entry is not an object")
        object_id = _first_present(entry, _ID_KEYS)
        if object_id is None or not str(object_id).strip():
            raise ValueError("an object without an id cannot be filed")
        key = str(object_id)
        # Parse everything BEFORE applying anything: a malformed
        # field rejects the whole entry and changes nothing.
        name = _first_present(entry, _NAME_KEYS)
        owner = _first_present(entry, _OWNER_KEYS)
        raw_position = (
            entry["position"] if "position" in entry else entry.get("pos")
        )
        position: Vec3 | None = None
        position_supplied = raw_position is not None
        if position_supplied:
            position = _vec3(raw_position, "position")
        scale, scale_partial = _extract_scale(entry)
        scale_supplied = scale is not None or scale_partial
        rotated = _extract_rotation(entry)
        existing = self._objects.get(key)
        if existing is None:
            record = SyntheticObject(id=key)
            self._objects[key] = record
        else:
            record = existing
        if name is not None:
            record.name = str(name)
        if owner is not None:
            record.owner_id = str(owner)
        if position_supplied:
            record.position = position
        if scale_supplied:
            record.scale = scale
            record.payload["_scale_partial"] = scale_partial
        if rotated is not None:
            record.rotated = rotated
        known = {
            "object_id", "id", "uuid", "name", "object_name",
            "owner_id", "owner", "owner_uuid", "creator_id",
            "creator", "position", "pos", "scale", "size",
            "dimensions", "scale_x", "scale_y", "scale_z",
            "size_x", "size_y", "size_z", "rotation",
            "orientation", "quaternion", "rot",
        }
        record.payload.update(
            {k: v for k, v in entry.items() if k not in known}
        )
        record.updated_at = moment
        self._object_set_timestamp = (
            moment
            if self._object_set_timestamp is None
            else max(self._object_set_timestamp, moment)
        )
        self._refresh_mesh(record)
        return record

    def _refresh_mesh(self, record: SyntheticObject) -> None:
        """Recompute one object's meshability from what it
        currently carries, in a fixed order — position, then
        scale, then rotation — so the named reason is stable."""
        reason: str | None = None
        if record.position is None:
            reason = REASON_MISSING_POSITION
        elif record.scale is None:
            reason = (
                REASON_INCOMPLETE_SCALE
                if record.payload.get("_scale_partial")
                else REASON_MISSING_SCALE
            )
        elif record.rotated:
            reason = REASON_ROTATED
        record.meshable = reason is None
        record.unmeshable_reason = reason
        if reason is None:
            assert record.position is not None and record.scale is not None
            self._meshes[record.id] = build_box_mesh(
                record.id, record.position, record.scale
            )
        else:
            self._meshes.pop(record.id, None)

    # -- queries --------------------------------------------------------------
    def get_object(self, object_id: str) -> SyntheticObject | None:
        return self._objects.get(str(object_id))

    def objects(self) -> list[SyntheticObject]:
        """All held objects, in the order first recorded."""
        return list(self._objects.values())

    def mesh_for(self, object_id: str) -> BoxMesh | None:
        """The box mesh for one object, or None when the object
        is unknown or held unmeshable."""
        return self._meshes.get(str(object_id))

    def meshes(self) -> list[BoxMesh]:
        """All current box meshes, sorted by object id (the
        deterministic order the renderer also draws in)."""
        return [self._meshes[key] for key in sorted(self._meshes)]

    @property
    def object_set_timestamp(self) -> float | None:
        """When the object set was last ingested/updated, or
        None before any object event has landed. Staleness of
        any render is render time minus this."""
        return self._object_set_timestamp

    def records(self) -> list[SyntheticRenderRecord]:
        """Provenance records for SPOOLED renders only, in
        render order (an unspooled render keeps its record on
        its RenderResult, not here)."""
        return list(self._records)

    def render_count(self) -> int:
        """How many renders have been computed, spooled or not."""
        return self._render_count

    # -- honesty: state ---------------------------------------------------------
    def vision_label(self) -> str:
        """The felt vision label, in V1's honest pattern:
        ``"synthetic view only"`` once a render exists, else
        ``"none (object-data only)"``."""
        return (
            VISION_SYNTHETIC_LABEL
            if self._render_count
            else VISION_NONE_LABEL
        )

    def current_state(self) -> SyntheticState:
        reasons: dict[str, int] = {}
        unmeshable = 0
        for record in self._objects.values():
            if not record.meshable:
                unmeshable += 1
                key = record.unmeshable_reason or "unknown"
                reasons[key] = reasons.get(key, 0) + 1
        return SyntheticState(
            known_objects=len(self._objects),
            meshed=len(self._meshes),
            unmeshable=unmeshable,
            unmeshable_reasons=reasons,
            renders=self._render_count,
            object_set_timestamp=self._object_set_timestamp,
            last_rendered_at=self._last_rendered_at,
            last_render_visible=self._last_render_visible,
            vision_label=self.vision_label(),
        )

    def describe(self) -> str:
        """Answer "what can you see?" — honestly.

        Before any render: no synthetic view has been rendered,
        and that is spoken. After one: the view names itself a
        synthetic view, with meshed/unmeshable counts; a latest
        render that saw nothing says background only."""
        state = self.current_state()
        if not self._render_count:
            return (
                "vision: none (object-data only) — no synthetic "
                "view has been rendered yet. "
                f"{state.known_objects} object(s) are known, "
                f"{state.meshed} meshed and {state.unmeshable} "
                "held unmeshable, but nothing has been rendered "
                "from them."
            )
        base = (
            "vision: synthetic view only — a computed synthetic "
            f"view, not what I literally see. {state.meshed} "
            f"object(s) meshed, {state.unmeshable} held "
            f"unmeshable, {state.renders} render(s) computed."
        )
        if state.last_render_visible == 0:
            return base + " The latest render was background only."
        return base

    # -- rendering ---------------------------------------------------------------
    def render(
        self,
        camera_position: Any,
        look_at: Any = None,
        *,
        direction: Any = None,
        width: int = DEFAULT_RENDER_WIDTH,
        height: int = DEFAULT_RENDER_HEIGHT,
        fov_degrees: float = DEFAULT_FOV_DEGREES,
        now: float | None = None,
    ) -> RenderResult:
        """Render the meshed object set from one camera.

        Exactly one of ``look_at`` (a point the camera faces)
        or ``direction`` (a facing vector) must be supplied.
        Raises ValueError/TypeError at this direct API for a
        malformed camera, a missing/ambiguous facing, or bad
        dimensions/fov — a render is a caller's computation,
        not an ingest, so it fails loudly instead of guessing.
        When a spool directory is configured the PNG is written
        under a deterministic filename and the provenance
        record is kept on the store (see :meth:`records`);
        otherwise the record rides the result only."""
        if (look_at is None) == (direction is None):
            raise ValueError(
                "a render needs exactly one of look_at / direction"
            )
        camera = _vec3(camera_position, "camera position")
        if look_at is not None:
            target = _vec3(look_at, "look_at")
            facing = _sub(target, camera)
        else:
            facing = _vec3(direction, "direction")
            target = (
                camera[0] + facing[0],
                camera[1] + facing[1],
                camera[2] + facing[2],
            )
        forward = _normalised(facing, "camera facing")
        if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
            raise ValueError("a render width must be a positive integer")
        if (
            not isinstance(height, int)
            or isinstance(height, bool)
            or height <= 0
        ):
            raise ValueError("a render height must be a positive integer")
        fov = _num(fov_degrees, "fov")
        if not 0.0 < fov < 180.0:
            raise ValueError("a render fov must be between 0 and 180 degrees")
        moment = self._now(now)
        depth, ids, faces, pixels = _rasterise(
            self.meshes(), camera, forward, width, height, fov
        )
        visible = [
            VisibleObject(
                object_id=object_id,
                pixel_count=data[0],
                nearest_depth=data[1],
            )
            for object_id, data in sorted(
                pixels.items(), key=lambda item: (item[1][1], item[0])
            )
        ]
        rgb = _shade_to_rgb(ids, faces, width, height)
        png = encode_png(width, height, rgb)
        self._render_count += 1
        self._last_rendered_at = moment
        self._last_render_visible = len(visible)
        spool_path: str | None = None
        if self.spool_dir is not None:
            filename = (
                f"synthetic_render-{int(moment)}-"
                f"{self._render_count}.png"
            )
            path = Path(self.spool_dir) / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
            spool_path = str(path)
        record = SyntheticRenderRecord(
            source=SYNTHETIC_SOURCE,
            label=SYNTHETIC_LABEL,
            camera_position=camera,
            camera_target=target,
            width=width,
            height=height,
            fov_degrees=fov,
            object_set_timestamp=self._object_set_timestamp,
            rendered_at=moment,
            spool_path=spool_path,
            byte_count=len(png),
            visible_object_ids=tuple(v.object_id for v in visible),
        )
        if spool_path is not None:
            self._records.append(record)
        return RenderResult(
            width=width,
            height=height,
            camera_position=camera,
            camera_target=target,
            fov_degrees=fov,
            depth_buffer=depth,
            object_id_buffer=ids,
            visible=visible,
            object_set_timestamp=self._object_set_timestamp,
            rendered_at=moment,
            png_bytes=png,
            record=record,
        )

    # Kept for callers that prefer V1's explicit spool naming.
    spool_render = render

    # -- purge ---------------------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove one resident's objects and tied renders.

        Exactly this: every held object whose carried
        owner/creator field (``owner_id`` / ``owner`` /
        ``owner_uuid`` / ``creator_id`` / ``creator``, as filed
        at ingest) equals ``resident_id`` is removed, with its
        mesh; and every SPOOLED render record whose
        included-object set (its visible object ids) contains
        a removed object is dropped, its spool file deleted
        (a file already gone is not an error). Unspooled
        renders are not held, so they are not purgeable here;
        renders naming only untied objects stay, as do objects
        owned by anyone else and unowned objects. Returns the
        total count removed — objects plus render records. A
        resident tied to nothing removes 0."""
        if not resident_id:
            return 0
        key = str(resident_id)
        removed_ids = {
            oid
            for oid, record in self._objects.items()
            if record.owner_id == key
        }
        removed = 0
        for oid in removed_ids:
            del self._objects[oid]
            self._meshes.pop(oid, None)
            removed += 1
        kept: list[SyntheticRenderRecord] = []
        for record in self._records:
            if removed_ids.intersection(record.visible_object_ids):
                if record.spool_path is not None:
                    try:
                        Path(record.spool_path).unlink(missing_ok=True)
                    except OSError:
                        pass  # record is dropped regardless
                removed += 1
            else:
                kept.append(record)
        self._records = kept
        return removed


def _rasterise(
    meshes: list[BoxMesh],
    camera: Vec3,
    forward: Vec3,
    width: int,
    height: int,
    fov_degrees: float,
) -> tuple[
    list[list[float | None]],
    list[list[str | None]],
    list[list[int | None]],
    dict[str, list[Any]],
]:
    """Rasterise the meshes into depth + object-id buffers.

    A true per-pixel depth buffer over near-plane-clipped
    triangles, drawn in deterministic order (meshes arrive
    sorted by object id; faces in topology order) with strict
    less-than depth replacement, so equal-depth ties resolve
    identically on every run. Depth is perspective-correct
    (1/z interpolated, then inverted) — exact for the planar
    faces being drawn. Returns the depth buffer, the object-id
    buffer, the winning-face buffer (which face owns each
    pixel — the flat-shading input), plus per-object
    ``[pixel_count, nearest_depth]``."""
    up_hint = _WORLD_UP
    if abs(_dot(forward, up_hint)) > 0.999:
        up_hint = _FALLBACK_UP
    right = _normalised(_cross(forward, up_hint), "camera right")
    true_up = _cross(right, forward)
    focal = 1.0 / math.tan(math.radians(fov_degrees) / 2.0)
    aspect = width / height
    depth: list[list[float | None]] = [
        [None] * width for _ in range(height)
    ]
    ids: list[list[str | None]] = [[None] * width for _ in range(height)]
    faces: list[list[int | None]] = [
        [None] * width for _ in range(height)
    ]
    zbuffer = [[math.inf] * width for _ in range(height)]

    def _view(vertex: Vec3) -> Vec3:
        d = _sub(vertex, camera)
        return (_dot(d, right), _dot(d, true_up), _dot(d, forward))

    for mesh in meshes:
        view_verts = [_view(v) for v in mesh.vertices]
        for tri_index, tri in enumerate(mesh.triangles):
            polygon = _clip_near([view_verts[i] for i in tri])
            for fan in range(1, len(polygon) - 1):
                _fill_triangle(
                    (polygon[0], polygon[fan], polygon[fan + 1]),
                    mesh.object_id,
                    tri_index,
                    focal,
                    aspect,
                    width,
                    height,
                    zbuffer,
                    depth,
                    ids,
                    faces,
                )
    # The visible tally is derived from the FINAL buffers, so
    # a pixel a nearer triangle overwrote is never still
    # counted for the object it first landed on.
    pixels: dict[str, list[Any]] = {}
    for y in range(height):
        for x in range(width):
            object_id = ids[y][x]
            if object_id is None:
                continue
            entry = pixels.get(object_id)
            if entry is None:
                pixels[object_id] = [0, depth[y][x]]
                entry = pixels[object_id]
            entry[0] += 1
            if depth[y][x] is not None and depth[y][x] < entry[1]:
                entry[1] = depth[y][x]
    return depth, ids, faces, pixels


def _clip_near(polygon: list[Vec3]) -> list[Vec3]:
    """Clip one view-space polygon against the near plane
    (z >= _NEAR_PLANE), Sutherland–Hodgman. A triangle wholly
    behind the camera clips to nothing and is never drawn."""
    clipped: list[Vec3] = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        cur_in = current[2] >= _NEAR_PLANE
        prev_in = previous[2] >= _NEAR_PLANE
        if cur_in != prev_in:
            t = (_NEAR_PLANE - previous[2]) / (current[2] - previous[2])
            clipped.append(
                (
                    previous[0] + (current[0] - previous[0]) * t,
                    previous[1] + (current[1] - previous[1]) * t,
                    _NEAR_PLANE,
                )
            )
        if cur_in:
            clipped.append(current)
    return clipped


def _fill_triangle(
    tri: tuple[Vec3, Vec3, Vec3],
    object_id: str,
    tri_index: int,
    focal: float,
    aspect: float,
    width: int,
    height: int,
    zbuffer: list[list[float]],
    depth: list[list[float | None]],
    ids: list[list[str | None]],
    faces: list[list[int | None]],
) -> None:
    """Fill one projected triangle into the depth/id buffers."""
    screen: list[tuple[float, float, float]] = []
    for vx, vy, vz in tri:
        ndc_x = (vx / vz) * focal / aspect
        ndc_y = (vy / vz) * focal
        screen.append(
            (
                (ndc_x + 1.0) * 0.5 * width,
                (1.0 - (ndc_y + 1.0) * 0.5) * height,
                1.0 / vz,
            )
        )
    (x0, y0, iz0), (x1, y1, iz1), (x2, y2, iz2) = screen
    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if denom == 0.0:
        return
    min_x = max(0, math.floor(min(x0, x1, x2)))
    max_x = min(width - 1, math.ceil(max(x0, x1, x2)))
    min_y = max(0, math.floor(min(y0, y1, y2)))
    max_y = min(height - 1, math.ceil(max(y0, y1, y2)))
    # The face this triangle belongs to decides its flat shade.
    face_index = tri_index // 2
    for py in range(min_y, max_y + 1):
        cy = py + 0.5
        for px in range(min_x, max_x + 1):
            cx = px + 0.5
            w0 = ((y1 - y2) * (cx - x2) + (x2 - x1) * (cy - y2)) / denom
            w1 = ((y2 - y0) * (cx - x2) + (x0 - x2) * (cy - y2)) / denom
            w2 = 1.0 - w0 - w1
            if w0 < -1e-9 or w1 < -1e-9 or w2 < -1e-9:
                continue
            inv_z = w0 * iz0 + w1 * iz1 + w2 * iz2
            if inv_z <= 0.0:
                continue
            pixel_depth = 1.0 / inv_z
            if pixel_depth < zbuffer[py][px]:
                zbuffer[py][px] = pixel_depth
                depth[py][px] = pixel_depth
                ids[py][px] = object_id
                faces[py][px] = face_index


def _shade_to_rgb(
    ids: list[list[str | None]],
    faces: list[list[int | None]],
    width: int,
    height: int,
) -> bytes:
    """Flat-shade the id + face buffers into RGB bytes.

    Per face, per the flat-shading contract: a pixel takes its
    object's synthetic palette colour scaled by
    ``0.3 + 0.7 * max(0, face_normal · light)`` for the face
    that owns the pixel, against the one fixed documented
    light direction — deterministic, and never a real colour,
    texture, or in-world lighting. Background is a neutral
    dark grey, never black-as-data."""
    light = _normalised(LIGHT_DIRECTION, "light direction")
    intensities = [
        0.3 + 0.7 * max(0.0, _dot(normal, light))
        for normal in _BOX_FACE_NORMALS
    ]
    palettes: dict[str, tuple[int, int, int]] = {}
    rgb = bytearray()
    background = (24, 24, 28)
    for y in range(height):
        for x in range(width):
            object_id = ids[y][x]
            if object_id is None:
                rgb += bytes(background)
                continue
            base = palettes.get(object_id)
            if base is None:
                base = base_colour_for(object_id)
                palettes[object_id] = base
            face = faces[y][x]
            intensity = intensities[face] if face is not None else 0.3
            rgb += bytes(
                max(0, min(255, round(channel * intensity)))
                for channel in base
            )
    return bytes(rgb)


__all__ = [
    "DEFAULT_FOV_DEGREES",
    "DEFAULT_RENDER_HEIGHT",
    "DEFAULT_RENDER_WIDTH",
    "LIGHT_DIRECTION",
    "REASON_INCOMPLETE_SCALE",
    "REASON_MISSING_POSITION",
    "REASON_MISSING_SCALE",
    "REASON_ROTATED",
    "SYNTHETIC_EVENTS",
    "SYNTHETIC_LABEL",
    "SYNTHETIC_SOURCE",
    "VISION_NONE_LABEL",
    "VISION_SYNTHETIC_LABEL",
    "BoxMesh",
    "RenderResult",
    "SyntheticObject",
    "SyntheticObservation",
    "SyntheticRenderRecord",
    "SyntheticState",
    "SyntheticStore",
    "VisibleObject",
    "base_colour_for",
    "build_box_mesh",
    "encode_png",
]
