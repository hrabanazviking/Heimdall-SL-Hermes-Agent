"""Movement & pathfinding — doc 14 slices M2 + M3 (seam/API side).

Doc 14's verdict: avatar pathfinding in Second Life is a
client-side navigation problem with no Linden-provided solver.
The LSL navmesh character family moves non-avatar objects
only, RLV has no locomotion at all, and the client moves by
control flags against server-authoritative physics. Heimdall
therefore builds its own navigator: M1 closed-loop steering in
the Core, M2 a traversability model, M3 a planner, M4 follow.

This module is the seam/API side of M2 + M3 only:

- **M2 — the traversability raster.** A region-local grid
  (2 m cells over a 256x256 m region by default, both
  configurable) whose cells are UNKNOWN until data actually
  arrives: FREE only where she has stood/walked or a caller
  has explicitly marked free, BLOCKED where an object
  footprint or a failed walk says so, and HARD_BLOCKED for
  no-entry / ban parcels, which doc 14 names hard obstacles —
  the planner stops and reports rather than testing whether
  the grid enforces them. Unknown is NEVER treated as free,
  in planning or anywhere else.
- **M3 — the planner.** A* over the raster (8-directional,
  diagonals only when both orthogonal neighbours are
  traversable, so a path never corner-cuts through a blocked
  cell), with line-of-sight smoothing over traversable cells.
  A plan either succeeds with a real path or fails with a
  distinct honest reason (goal unknown / goal blocked /
  start unknown / unreachable / out of region) — a path is
  never fabricated. An ``llGetStaticPath``-style static-path
  hint (doc 14: the one pathfinding function usable by
  non-characters) may be stored and cross-checked, but it is
  a hint only: it never overrides a cell state and never
  creates a FREE cell. Successfully walked segments are
  recorded as *attributed experience* — proven edges, hers to
  remember — never as canonical facts about the world (the
  Phase I0 discipline), exactly as doc 14 prescribes.

Bounds, enforced here in the body, not by convention:

- **Nothing is invented.** An object's footprint comes from
  the position + scale/size an event actually carried; with
  no scale carried, only the object's own cell is blocked —
  never a larger guessed footprint. Terrain heights are
  stored only when a caller supplies one for a cell, never
  interpolated or guessed; a slope blocks a step only when
  BOTH cells' heights are known and their difference exceeds
  the caller-configurable threshold. Parcels are hard-blocked
  only from caller/event-supplied rectangular bounds plus an
  explicit no-entry/ban flag — no parcel is invented.
- **Walked means walked.** A cell becomes FREE through
  self-position data only where she actually stood. A proven
  walked segment marks its cells FREE for the same reason —
  except HARD_BLOCKED cells, which walked/free marking never
  overrides (parcel law outranks a walk report; only an
  explicit caller-directed reset clears a hard block).
- **Fail-closed ingest, never raises.** Events this store
  does not understand, malformed object/parcel/position
  entries, and invalid values go into the ``rejected`` audit
  list and change nothing (per-entry for list payloads, in
  the sibling stores' shape).
- **Region crossings are out of scope** (doc 14): a start or
  goal outside this region's bounds fails with an
  ``out_of_region`` reason; the planner never wanders across
  a border by accident.
- **Movement is built, never executed, here.** The one
  command builder, :meth:`NavigationStore.build_move_to_command`,
  is build-only and records nothing: it returns a
  governor-ready ``move_to`` command (tier ``"mind"``, walk
  vs fly an explicit validated mode, never silently switched)
  for the M1 Core leg to execute. The Core currently refuses
  ``move_to`` as ``not_implemented_in_minimal_client``
  (CommandProcessor.cs / SlSession.cs) — that refusal is
  honest, and this builder is the seam contract that leg will
  speak when M1 lands. A failed walk is reported as a failed
  walk here; nothing substitutes a teleport (doc 14's
  no-misdirection rule, in code).

Scope honesty: this is M2 + M3, seam side only. There is no
M1 Core steering (no control flags, no stuck detection, no
arrival loop — a purplelongship/Core slice), and no M4
follow/go-to-person. No new seam vocabulary was needed:
``object_info``, ``parcel_changed``, and
``self_position_updated`` already ride the base vocabulary in
``core/models.py`` and are ingested here without duplicating
them, and planning itself is a local computation that emits
no wire event — so ``core/models.py`` is untouched by this
slice. Navigation data is place/self experience and carries
no resident identity, so :meth:`NavigationStore.purge_resident`
honestly removes nothing, like the map sense before it.
"""
from __future__ import annotations

import heapq
import itertools
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core.models import Command, SeamEvent

# Canonical seam event names this store ingests — all three
# already ride the base vocabulary (ObjectInfo / object_info,
# ParcelEntered+ParcelChanged / parcel_changed,
# SelfPositionUpdated / self_position_updated); none is
# duplicated in core/models.py by this slice.
NAVIGATION_EVENTS = frozenset(
    {
        "object_info",
        "parcel_changed",
        "self_position_updated",
    }
)

# Cell states. UNKNOWN is the default for every cell and is
# never traversable: the planner may only walk on FREE.
CELL_UNKNOWN = "unknown"
CELL_FREE = "free"
CELL_BLOCKED = "blocked"
CELL_HARD_BLOCKED = "hard_blocked"
CELL_STATES = frozenset(
    {CELL_UNKNOWN, CELL_FREE, CELL_BLOCKED, CELL_HARD_BLOCKED}
)

# The explicit locomotion modes a move_to command may carry
# (doc 14 M1: walk vs fly is explicit, never silently switched).
MOVE_MODES = frozenset({"walk", "fly"})

# Defaults: doc 14 M2's starting raster (2 m cells, one
# 256x256 m region) and this seam's default steepness bound —
# a height step between adjacent cells larger than this (only
# when both heights are known) blocks the step. Configurable
# per store; it is a planning parameter, not a world fact.
DEFAULT_CELL_SIZE = 2.0
DEFAULT_REGION_SIZE = 256.0
DEFAULT_SLOPE_THRESHOLD = 2.0

Point = tuple[float, float]
Cell = tuple[int, int]


def _num(value: Any, label: str) -> float:
    """Validate one coordinate strictly; return it as float.

    Real ints/floats pass; booleans, strings, NaN and infinity
    fail closed — a coordinate is never coerced from text."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"a {label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"a {label} must be finite")
    return result


def _point(value: Any, label: str = "point") -> Point:
    """Validate an (x, y) point from a pair or an x/y mapping."""
    if isinstance(value, dict):
        if "x" not in value or "y" not in value:
            raise ValueError(f"a {label} needs x and y")
        return (_num(value["x"], f"{label} x"), _num(value["y"], f"{label} y"))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (_num(value[0], f"{label} x"), _num(value[1], f"{label} y"))
    raise TypeError(f"a {label} must be an (x, y) pair")


@dataclass
class WalkedRecord:
    """One place she actually stood: position, cell, and when.

    Self-experience data — proof this cell was traversable at
    that moment, nothing more."""

    x: float
    y: float
    cell: Cell
    at: float


@dataclass
class ProvenWalkRecord:
    """One walked segment, recorded as attributed experience.

    "Yrsa walked A->B at time T, it worked / it was blocked"
    (doc 14's proven-path memory): an observation about her
    own experience, re-readable, and never a canonical fact
    about the world's geometry — the world stays re-sensed."""

    start: Point
    goal: Point
    success: bool
    at: float
    blocked_at: Point | None = None
    cells: tuple[Cell, ...] = ()


@dataclass
class NavigationState:
    """A snapshot of the raster, honest about its coverage."""

    cell_size: float
    region_width: float
    region_height: float
    columns: int
    rows: int
    total_cells: int
    unknown_cells: int
    free_cells: int
    blocked_cells: int
    hard_blocked_cells: int
    known_cells: int
    unknown_percent: float
    coverage_percent: float
    walked_positions: int
    proven_walks: int
    hint_points: int
    heights_known: int


@dataclass
class PlanResult:
    """The outcome of one plan: a real path, or an honest
    failure with a distinct reason — never a fabricated path."""

    success: bool
    start: Point
    goal: Point
    path: list[Point] = field(default_factory=list)
    reason: str | None = None
    cost: float | None = None
    cells: list[Cell] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.success


@dataclass
class HintSegmentResult:
    """One hint segment, cross-checked against the raster."""

    index: int
    start: Point
    end: Point
    status: str  # clear | crosses_unknown | crosses_blocked | crosses_hard_blocked
    offending_cells: list[Cell] = field(default_factory=list)


@dataclass
class HintValidation:
    """The cross-check of the stored hint: which of its
    segments cross blocked / hard-blocked / unknown cells.

    A hint that crosses anything is not "wrong data to fix" —
    it is a static-obstacle view (doc 14) being checked
    against what this raster actually knows."""

    segments: list[HintSegmentResult] = field(default_factory=list)

    @property
    def clear(self) -> bool:
        return all(s.status == "clear" for s in self.segments)

    def __bool__(self) -> bool:
        return self.clear


@dataclass
class HintAgreement:
    """How a planned path agrees with the stored hint."""

    has_hint: bool
    validation: HintValidation
    path_follows_hint: bool
    agrees: bool


@dataclass
class PathValidity:
    """Whether a previously planned path is still walkable."""

    valid: bool
    reason: str | None = None
    offending_point: Point | None = None
    offending_cell: Cell | None = None

    def __bool__(self) -> bool:
        return self.valid


@dataclass
class NavigationStore:
    """The M2 raster + M3 planner: ingest, mark, plan, prove."""

    cell_size: float = DEFAULT_CELL_SIZE
    region_width: float = DEFAULT_REGION_SIZE
    region_height: float = DEFAULT_REGION_SIZE
    slope_threshold: float | None = DEFAULT_SLOPE_THRESHOLD
    adjacency_penalty: float = 0.0
    _clock: Any = field(default=time.time)
    _cells: dict[Cell, str] = field(default_factory=dict, init=False)
    _heights: dict[Cell, float] = field(default_factory=dict, init=False)
    _walked: list[WalkedRecord] = field(default_factory=list, init=False)
    _proven: list[ProvenWalkRecord] = field(default_factory=list, init=False)
    _hint: list[Point] = field(default_factory=list, init=False)
    # Fail-closed audit: (reason, detail) for every refused ingest.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.cell_size = _num(self.cell_size, "cell size")
        self.region_width = _num(self.region_width, "region width")
        self.region_height = _num(self.region_height, "region height")
        if self.cell_size <= 0:
            raise ValueError("a cell size must be positive")
        if self.region_width <= 0 or self.region_height <= 0:
            raise ValueError("a region size must be positive")
        if self.slope_threshold is not None:
            self.slope_threshold = _num(
                self.slope_threshold, "slope threshold"
            )
            if self.slope_threshold < 0:
                raise ValueError("a slope threshold must be non-negative")
        self.adjacency_penalty = _num(
            self.adjacency_penalty, "adjacency penalty"
        )
        if self.adjacency_penalty < 0:
            raise ValueError("an adjacency penalty must be non-negative")

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

    # -- geometry ---------------------------------------------------------
    @property
    def columns(self) -> int:
        return max(1, math.ceil(self.region_width / self.cell_size))

    @property
    def rows(self) -> int:
        return max(1, math.ceil(self.region_height / self.cell_size))

    def in_region(self, x: Any, y: Any) -> bool:
        """True when (x, y) is a valid point inside this region."""
        try:
            px, py = _num(x, "x"), _num(y, "y")
        except (ValueError, TypeError):
            return False
        return 0.0 <= px < self.region_width and 0.0 <= py < self.region_height

    def cell_for(self, x: Any, y: Any) -> Cell:
        """The raster cell containing (x, y). Raises ValueError
        for a point outside this region — region-local only."""
        px, py = _num(x, "x"), _num(y, "y")
        if not (0.0 <= px < self.region_width and 0.0 <= py < self.region_height):
            raise ValueError(f"point ({px}, {py}) is outside the region")
        return (
            min(int(px // self.cell_size), self.columns - 1),
            min(int(py // self.cell_size), self.rows - 1),
        )

    def cell_center(self, cell: Cell) -> Point:
        """The region-local centre point of one cell."""
        cx, cy = cell
        return (
            (cx + 0.5) * self.cell_size,
            (cy + 0.5) * self.cell_size,
        )

    def _cell_in_grid(self, cell: Cell) -> bool:
        return 0 <= cell[0] < self.columns and 0 <= cell[1] < self.rows

    # -- cell state ---------------------------------------------------------
    def _state_of_cell(self, cell: Cell) -> str:
        return self._cells.get(cell, CELL_UNKNOWN)

    def cell_state(self, x: Any, y: Any) -> str:
        """The state of the cell containing (x, y): one of
        ``unknown`` (the default — most of a fresh region),
        ``free``, ``blocked``, or ``hard_blocked``."""
        return self._state_of_cell(self.cell_for(x, y))

    def is_traversable(self, x: Any, y: Any) -> bool:
        """True only when the cell containing (x, y) is FREE.

        Unknown is never traversable, blocked and hard-blocked
        never are, and a point outside the region never is."""
        try:
            cell = self.cell_for(x, y)
        except (ValueError, TypeError):
            return False
        return self._state_of_cell(cell) == CELL_FREE

    def _set_cell(self, cell: Cell, state: str) -> str:
        """Set one cell's state, honouring hard-block law: a
        HARD_BLOCKED cell is changed only by an explicit hard
        mark or an explicit reset (mark_unknown)."""
        current = self._state_of_cell(cell)
        if current == CELL_HARD_BLOCKED and state in (CELL_FREE, CELL_BLOCKED):
            return current
        if state == CELL_UNKNOWN:
            self._cells.pop(cell, None)
        else:
            self._cells[cell] = state
        return self._state_of_cell(cell)

    def mark_free(self, x: Any, y: Any) -> str:
        """Mark the cell containing (x, y) FREE (a caller who
        knows it is traversable). Never overrides a hard
        block. Returns the cell's resulting state."""
        return self._set_cell(self.cell_for(x, y), CELL_FREE)

    def mark_blocked(self, x: Any, y: Any) -> str:
        """Mark the cell containing (x, y) BLOCKED. Never
        overrides a hard block. Returns the resulting state."""
        return self._set_cell(self.cell_for(x, y), CELL_BLOCKED)

    def mark_hard_blocked(self, x: Any, y: Any) -> str:
        """Mark the cell containing (x, y) HARD_BLOCKED (a
        no-entry / ban cell). Returns the resulting state."""
        cell = self.cell_for(x, y)
        self._cells[cell] = CELL_HARD_BLOCKED
        return CELL_HARD_BLOCKED

    def mark_unknown(self, x: Any, y: Any) -> str:
        """Reset the cell containing (x, y) to UNKNOWN — the
        explicit caller-directed reset, and the only mark that
        clears a hard block. Returns the resulting state."""
        cell = self.cell_for(x, y)
        self._cells.pop(cell, None)
        return CELL_UNKNOWN

    # -- counts / honest state ---------------------------------------------
    def counts_by_state(self) -> dict[str, int]:
        """Exact cell counts for every state, unknown included."""
        counts = {
            CELL_UNKNOWN: self.columns * self.rows,
            CELL_FREE: 0,
            CELL_BLOCKED: 0,
            CELL_HARD_BLOCKED: 0,
        }
        for state in self._cells.values():
            counts[state] += 1
            counts[CELL_UNKNOWN] -= 1
        return counts

    def snapshot(self) -> dict[str, int]:
        """Cell counts by state, plus the raster's total."""
        counts = self.counts_by_state()
        counts["total"] = self.columns * self.rows
        return counts

    def current_state(self) -> NavigationState:
        counts = self.counts_by_state()
        total = self.columns * self.rows
        unknown = counts[CELL_UNKNOWN]
        return NavigationState(
            cell_size=self.cell_size,
            region_width=self.region_width,
            region_height=self.region_height,
            columns=self.columns,
            rows=self.rows,
            total_cells=total,
            unknown_cells=unknown,
            free_cells=counts[CELL_FREE],
            blocked_cells=counts[CELL_BLOCKED],
            hard_blocked_cells=counts[CELL_HARD_BLOCKED],
            known_cells=total - unknown,
            unknown_percent=(100.0 * unknown / total) if total else 100.0,
            coverage_percent=(
                100.0 * (total - unknown) / total
            ) if total else 0.0,
            walked_positions=len(self._walked),
            proven_walks=len(self._proven),
            hint_points=len(self._hint),
            heights_known=len(self._heights),
        )

    def describe(self) -> str:
        """Answer "what ground do you know?" — honestly.

        Coverage is spoken as a percentage unknown: a fresh
        raster is 100% unknown and says so, never implying a
        walkable region it has not sensed."""
        state = self.current_state()
        return (
            f"navigation: {state.known_cells} of {state.total_cells} "
            f"cells known ({state.unknown_percent:.1f}% unknown) — "
            f"{state.free_cells} free, {state.blocked_cells} blocked, "
            f"{state.hard_blocked_cells} hard-blocked (no-entry/ban). "
            "Unknown cells are never treated as free."
        )

    # -- object footprints (M2 ingest source) --------------------------------
    def mark_object_footprint(
        self,
        x: Any,
        y: Any,
        scale_x: Any = None,
        scale_y: Any = None,
    ) -> list[Cell]:
        """Block the cells one object's footprint covers.

        The footprint is the axis-aligned rectangle centred on
        (x, y) with the supplied scale/size. With NO scale
        carried, only the object's own cell is blocked — a
        larger footprint is never invented. A scale axis that
        was not carried spans one cell on that axis, no more.
        Returns the cells marked."""
        centre = self.cell_for(x, y)
        px, py = _num(x, "x"), _num(y, "y")

        def _axis_range(
            pos: float, scale: Any, centre_index: int, limit: int
        ) -> tuple[int, int]:
            # An axis with NO scale carried spans exactly the
            # object's own cell on that axis — never a rectangle
            # (a cell-sized box centred on a cell boundary would
            # spill into neighbours the event never claimed).
            if scale is None:
                return centre_index, centre_index
            size = _num(scale, "scale")
            if size <= 0:
                raise ValueError("an object scale must be positive")
            lo, hi = pos - size / 2.0, pos + size / 2.0
            first = max(0, math.floor(lo / self.cell_size))
            last = min(
                limit - 1,
                math.floor((hi - 1e-9) / self.cell_size),
            )
            return first, last

        first_cx, last_cx = _axis_range(px, scale_x, centre[0], self.columns)
        first_cy, last_cy = _axis_range(py, scale_y, centre[1], self.rows)
        marked: list[Cell] = []
        for cx in range(first_cx, last_cx + 1):
            for cy in range(first_cy, last_cy + 1):
                cell = (cx, cy)
                self._set_cell(cell, CELL_BLOCKED)
                marked.append(cell)
        if centre not in marked:  # degenerate scales still block her cell
            self._set_cell(centre, CELL_BLOCKED)
            marked.append(centre)
        return marked

    # -- parcels (M2 ingest source) -------------------------------------------
    def mark_parcel_no_entry(
        self,
        x_min: Any,
        y_min: Any,
        x_max: Any,
        y_max: Any,
        *,
        no_entry: bool = True,
    ) -> int:
        """Hard-block every cell of a rectangular parcel area.

        Acts only when the caller-supplied flag says the parcel
        is no-entry / ban (``no_entry`` truthy): a parcel
        without that flag marks nothing and returns 0 — its
        traversability is simply not this method's claim.
        Bounds are used exactly as supplied; no parcel is
        invented or extended. Returns the cells hard-blocked."""
        lo_x, lo_y = _num(x_min, "x_min"), _num(y_min, "y_min")
        hi_x, hi_y = _num(x_max, "x_max"), _num(y_max, "y_max")
        if hi_x < lo_x or hi_y < lo_y:
            raise ValueError("parcel bounds are inverted")
        if not no_entry:
            return 0
        first_cx = max(0, math.floor(lo_x / self.cell_size))
        last_cx = min(
            self.columns - 1,
            math.floor((hi_x - 1e-9) / self.cell_size)
            if hi_x > lo_x
            else first_cx,
        )
        first_cy = max(0, math.floor(lo_y / self.cell_size))
        last_cy = min(
            self.rows - 1,
            math.floor((hi_y - 1e-9) / self.cell_size)
            if hi_y > lo_y
            else first_cy,
        )
        count = 0
        for cx in range(first_cx, last_cx + 1):
            for cy in range(first_cy, last_cy + 1):
                self._cells[(cx, cy)] = CELL_HARD_BLOCKED
                count += 1
        return count

    # Kept for callers that prefer the shorter spelling.
    mark_parcel = mark_parcel_no_entry

    # -- walked ground (M2 ingest source) ---------------------------------------
    def record_walked(
        self, x: Any, y: Any, *, now: float | None = None
    ) -> WalkedRecord:
        """Record one position she actually stood at, marking
        its cell FREE (proven free — she was there). A
        HARD_BLOCKED cell stays hard-blocked: parcel law
        outranks a position report. Raises for a point outside
        the region at the direct API."""
        px, py = _num(x, "x"), _num(y, "y")
        cell = self.cell_for(px, py)
        self._set_cell(cell, CELL_FREE)
        record = WalkedRecord(x=px, y=py, cell=cell, at=self._now(now))
        self._walked.append(record)
        return record

    def walked_positions(self) -> list[WalkedRecord]:
        """Every recorded stood-at position, in arrival order."""
        return list(self._walked)

    # -- terrain heights (M2 ingest source) -------------------------------------
    def set_terrain_height(
        self, x: Any, y: Any, height: Any
    ) -> float:
        """Store the terrain height a caller supplies for the
        cell containing (x, y). Only that cell gets a height —
        heights are never interpolated to neighbours or
        invented for unsupplied cells. Returns the height."""
        value = _num(height, "terrain height")
        self._heights[self.cell_for(x, y)] = value
        return value

    # Aliases in sibling naming.
    record_terrain_height = set_terrain_height
    set_height = set_terrain_height

    def terrain_height(self, x: Any, y: Any) -> float | None:
        """The supplied height of the cell containing (x, y),
        or None when no height was ever supplied for it."""
        return self._heights.get(self.cell_for(x, y))

    def _step_slope_blocked(self, a: Cell, b: Cell) -> bool:
        """True when the step a->b is too steep to plan: only
        when a threshold is configured AND both cells carry a
        supplied height AND their difference exceeds it. An
        unknown height never blocks a step — it is unknown,
        not steep."""
        if self.slope_threshold is None:
            return False
        ha = self._heights.get(a)
        hb = self._heights.get(b)
        if ha is None or hb is None:
            return False
        return abs(hb - ha) > self.slope_threshold

    # -- traversability for planning --------------------------------------------
    def _cell_traversable(self, cell: Cell) -> bool:
        return (
            self._cell_in_grid(cell)
            and self._state_of_cell(cell) == CELL_FREE
        )

    def _step_allowed(self, a: Cell, b: Cell) -> bool:
        """True when planning may step a->b: b is traversable,
        a diagonal step does not corner-cut (both orthogonal
        neighbours of the diagonal must be traversable), and
        the slope between the cells is not proven too steep."""
        if not self._cell_traversable(b):
            return False
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx != 0 and dy != 0:
            if not self._cell_traversable((a[0] + dx, a[1])):
                return False
            if not self._cell_traversable((a[0], a[1] + dy)):
                return False
        return not self._step_slope_blocked(a, b)

    def _cells_along_segment(self, p: Point, q: Point) -> list[Cell]:
        """The distinct raster cells the segment p->q passes
        through, in order (dense sampling at a quarter cell —
        raster traversal, so no crossed cell is skipped)."""
        dist = math.hypot(q[0] - p[0], q[1] - p[1])
        steps = max(1, math.ceil(dist / (self.cell_size * 0.25)))
        cells: list[Cell] = []
        for i in range(steps + 1):
            t = i / steps
            sx = p[0] + (q[0] - p[0]) * t
            sy = p[1] + (q[1] - p[1]) * t
            if not (0.0 <= sx < self.region_width and 0.0 <= sy < self.region_height):
                cell = (
                    math.floor(sx / self.cell_size),
                    math.floor(sy / self.cell_size),
                )
            else:
                cell = self.cell_for(sx, sy)
            if not cells or cells[-1] != cell:
                cells.append(cell)
        return cells

    def _line_clear(self, a: Cell, b: Cell) -> bool:
        """True when the straight line between two cell centres
        crosses only traversable cells, with no corner-cutting
        diagonal step and no proven-too-steep step."""
        cells = self._cells_along_segment(
            self.cell_center(a), self.cell_center(b)
        )
        for cell in cells:
            if not self._cell_traversable(cell):
                return False
        for prev, nxt in itertools.pairwise(cells):
            dx, dy = nxt[0] - prev[0], nxt[1] - prev[1]
            if abs(dx) > 1 or abs(dy) > 1:
                return False
            if dx != 0 and dy != 0:
                if not self._cell_traversable((prev[0] + dx, prev[1])):
                    return False
                if not self._cell_traversable((prev[0], prev[1] + dy)):
                    return False
            if self._step_slope_blocked(prev, nxt):
                return False
        return True

    # -- M3: the planner ----------------------------------------------------------
    def _failure(self, start: Point, goal: Point, reason: str) -> PlanResult:
        return PlanResult(success=False, start=start, goal=goal, reason=reason)

    def _endpoint_failure(
        self, point: Point, label: str
    ) -> str | None:
        """The honest failure reason for one endpoint, or None
        when the endpoint is a FREE in-region cell."""
        if not self.in_region(point[0], point[1]):
            return "out_of_region"
        state = self._state_of_cell(self.cell_for(point[0], point[1]))
        if state == CELL_UNKNOWN:
            return f"{label}_unknown"
        if state == CELL_BLOCKED:
            return f"{label}_blocked"
        if state == CELL_HARD_BLOCKED:
            return f"{label}_hard_blocked"
        return None

    def plan(self, start: Any, goal: Any) -> PlanResult:
        """Plan a path from start to goal over the raster.

        Pure over the current raster — replanning after new
        obstacle ingest naturally produces a different (or
        honestly failed) plan. On success the path is the
        smoothed waypoint list: the exact start, intermediate
        cell centres after line-of-sight shortcutting, and the
        exact goal. On failure the result carries a distinct
        reason and an empty path — never a fabricated route."""
        try:
            start_point = _point(start, "start")
        except (ValueError, TypeError):
            return PlanResult(
                success=False, start=(0.0, 0.0), goal=(0.0, 0.0),
                reason="invalid_start",
            )
        try:
            goal_point = _point(goal, "goal")
        except (ValueError, TypeError):
            return PlanResult(
                success=False, start=start_point, goal=start_point,
                reason="invalid_goal",
            )
        for point, label in ((start_point, "start"), (goal_point, "goal")):
            reason = self._endpoint_failure(point, label)
            if reason is not None:
                return self._failure(start_point, goal_point, reason)
        start_cell = self.cell_for(*start_point)
        goal_cell = self.cell_for(*goal_point)
        if start_cell == goal_cell:
            return PlanResult(
                success=True,
                start=start_point,
                goal=goal_point,
                path=[start_point, goal_point]
                if start_point != goal_point
                else [start_point],
                cost=math.hypot(
                    goal_point[0] - start_point[0],
                    goal_point[1] - start_point[1],
                ),
                cells=[start_cell],
            )
        cells, cost = self._astar(start_cell, goal_cell)
        if cells is None:
            return self._failure(start_point, goal_point, "unreachable")
        return PlanResult(
            success=True,
            start=start_point,
            goal=goal_point,
            path=self._smooth(cells, start_point, goal_point),
            cost=cost,
            cells=cells,
        )

    def _astar(
        self, start: Cell, goal: Cell
    ) -> tuple[list[Cell] | None, float | None]:
        """A* over FREE cells: distance cost, Euclidean
        heuristic, 8-directional with corner-cut prevention,
        plus the configured penalty for stepping onto a cell
        adjacent to a blocked/hard-blocked cell."""
        goal_centre = self.cell_center(goal)

        def heuristic(cell: Cell) -> float:
            centre = self.cell_center(cell)
            return math.hypot(
                centre[0] - goal_centre[0], centre[1] - goal_centre[1]
            )

        def step_penalty(cell: Cell) -> float:
            if self.adjacency_penalty <= 0 or cell == goal:
                return 0.0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    neighbour = (cell[0] + dx, cell[1] + dy)
                    if self._cell_in_grid(neighbour) and self._state_of_cell(
                        neighbour
                    ) in (CELL_BLOCKED, CELL_HARD_BLOCKED):
                        return self.adjacency_penalty
            return 0.0

        open_heap: list[tuple[float, float, Cell]] = []
        heapq.heappush(open_heap, (heuristic(start), 0.0, start))
        came_from: dict[Cell, Cell] = {}
        g_score: dict[Cell, float] = {start: 0.0}
        closed: set[Cell] = set()
        directions = [
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1),
        ]
        while open_heap:
            _, g, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            closed.add(current)
            if current == goal:
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                return path, g
            for dx, dy in directions:
                neighbour = (current[0] + dx, current[1] + dy)
                if not self._cell_in_grid(neighbour):
                    continue
                if not self._step_allowed(current, neighbour):
                    continue
                step = self.cell_size * (
                    math.sqrt(2.0) if dx != 0 and dy != 0 else 1.0
                )
                tentative = g + step + step_penalty(neighbour)
                if tentative < g_score.get(neighbour, math.inf):
                    g_score[neighbour] = tentative
                    came_from[neighbour] = current
                    heapq.heappush(
                        open_heap,
                        (tentative + heuristic(neighbour), tentative, neighbour),
                    )
        return None, None

    def _smooth(
        self, cells: list[Cell], start: Point, goal: Point
    ) -> list[Point]:
        """Line-of-sight shortcutting over the A* cell path:
        greedily jump to the farthest cell visible from the
        current anchor, so the smoothed path only ever crosses
        traversable cells."""
        if len(cells) == 1:
            return [start, goal] if start != goal else [start]
        waypoints: list[Point] = [start]
        anchor = 0
        while anchor < len(cells) - 1:
            farthest = anchor + 1
            for j in range(len(cells) - 1, anchor + 1, -1):
                if self._line_clear(cells[anchor], cells[j]):
                    farthest = j
                    break
            if farthest == len(cells) - 1:
                waypoints.append(goal)
            else:
                waypoints.append(self.cell_center(cells[farthest]))
            anchor = farthest
        return waypoints

    def path_still_valid(self, path: Any) -> PathValidity:
        """Re-check a previously planned path against the
        CURRENT raster: False with a reason (and the offending
        point/cell) when any cell the path crosses has become
        blocked, hard-blocked, or unknown since planning."""
        try:
            points = [_point(p, "path point") for p in path]
        except (ValueError, TypeError) as exc:
            return PathValidity(valid=False, reason=f"invalid_path: {exc}")
        if not points:
            return PathValidity(valid=False, reason="empty_path")
        segments: list[tuple[Point, Point]] = [
            (points[i], points[i + 1]) for i in range(len(points) - 1)
        ] or [(points[0], points[0])]
        for seg_start, seg_end in segments:
            for cell in self._cells_along_segment(seg_start, seg_end):
                if not self._cell_in_grid(cell):
                    return PathValidity(
                        valid=False, reason="out_of_region",
                        offending_point=seg_start, offending_cell=cell,
                    )
                state = self._state_of_cell(cell)
                if state != CELL_FREE:
                    reason = {
                        CELL_UNKNOWN: "path_cell_unknown",
                        CELL_BLOCKED: "path_cell_blocked",
                        CELL_HARD_BLOCKED: "path_cell_hard_blocked",
                    }[state]
                    return PathValidity(
                        valid=False, reason=reason,
                        offending_point=seg_start, offending_cell=cell,
                    )
        return PathValidity(valid=True)

    # -- static-path hint (cross-check only) ---------------------------------------
    def accept_hint(self, points: Any) -> list[Point]:
        """Store an llGetStaticPath-style static-path hint.

        The hint is held exactly as supplied for cross-checking
        only: storing it changes NO cell state, creates NO free
        cell, and the planner never routes along it. Raises
        ValueError/TypeError for malformed or out-of-region
        points at the direct API."""
        if not isinstance(points, (list, tuple)) or not points:
            raise ValueError("a hint needs at least one point")
        hint = [_point(p, "hint point") for p in points]
        for point in hint:
            if not self.in_region(point[0], point[1]):
                raise ValueError(f"hint point {point} is outside the region")
        self._hint = hint
        return list(self._hint)

    def hint(self) -> list[Point]:
        """The stored hint points, or an empty list."""
        return list(self._hint)

    def validate_hint(self) -> HintValidation:
        """Cross-check every stored hint segment against the
        raster, reporting which segments cross unknown,
        blocked, or hard-blocked cells. An empty hint validates
        as no segments (clear, and meaningless — see
        :meth:`hint_agreement` for the has-hint flag)."""
        results: list[HintSegmentResult] = []
        for i in range(len(self._hint) - 1):
            seg_start, seg_end = self._hint[i], self._hint[i + 1]
            offending: list[Cell] = []
            status = "clear"
            for cell in self._cells_along_segment(seg_start, seg_end):
                state = (
                    self._state_of_cell(cell)
                    if self._cell_in_grid(cell)
                    else CELL_UNKNOWN
                )
                if state != CELL_FREE:
                    offending.append(cell)
                    if state == CELL_HARD_BLOCKED:
                        status = "crosses_hard_blocked"
                    elif state == CELL_BLOCKED and status != "crosses_hard_blocked":
                        status = "crosses_blocked"
                    elif status == "clear":
                        status = "crosses_unknown"
            results.append(
                HintSegmentResult(
                    index=i, start=seg_start, end=seg_end,
                    status=status, offending_cells=offending,
                )
            )
        return HintValidation(segments=results)

    def hint_agreement(self, path: Any = None) -> HintAgreement:
        """Report how a planned path agrees with the hint: the
        hint validates clear AND (when a path is given) the
        path passes through every hint point's cell. The hint
        is evidence to cross-check, never authority — agreement
        or disagreement changes no cell and no plan."""
        validation = self.validate_hint()
        follows = False
        if path is not None and self._hint:
            try:
                points = [_point(p, "path point") for p in path]
            except (ValueError, TypeError):
                points = []
            path_cells: set[Cell] = set()
            for seg_start, seg_end in itertools.pairwise(points) or []:
                path_cells.update(
                    self._cells_along_segment(seg_start, seg_end)
                )
            if len(points) == 1 and self.in_region(*points[0]):
                path_cells.add(self.cell_for(*points[0]))
            follows = all(
                self.cell_for(*hp) in path_cells for hp in self._hint
            )
        return HintAgreement(
            has_hint=bool(self._hint),
            validation=validation,
            path_follows_hint=follows,
            agrees=bool(self._hint) and validation.clear and (
                path is None or follows
            ),
        )

    # -- proven-path memory (attributed experience) ----------------------------------
    def record_walk_result(
        self,
        start: Any,
        goal: Any = None,
        success: bool = False,
        *,
        blocked_at: Any = None,
        at: float | None = None,
        now: float | None = None,
    ) -> ProvenWalkRecord:
        """Record one walked segment as attributed experience.

        ``goal`` may be a single endpoint or a list of segment
        points (the walked polyline). A successful walk marks
        the segment's cells FREE — she actually walked them —
        except HARD_BLOCKED cells, which stay hard. A blocked
        walk marks nothing free; when ``blocked_at`` is
        supplied, that cell is recorded BLOCKED (unless hard).
        Either way the record itself is experience, held in
        :meth:`proven_walks`, never a canonical world fact."""
        start_point = _point(start, "walk start")
        if isinstance(goal, (list, tuple)) and goal and isinstance(
            goal[0], (list, tuple, dict)
        ):
            points = [start_point] + [_point(p, "walk point") for p in goal]
        elif goal is None:
            points = [start_point]
        else:
            points = [start_point, _point(goal, "walk goal")]
        moment = self._now(at if at is not None else now)
        cells: list[Cell] = []
        if success:
            for seg_start, seg_end in itertools.pairwise(points):
                for cell in self._cells_along_segment(seg_start, seg_end):
                    if self._cell_in_grid(cell):
                        self._set_cell(cell, CELL_FREE)
                        if cell not in cells:
                            cells.append(cell)
        blocked_point: Point | None = None
        if blocked_at is not None:
            blocked_point = _point(blocked_at, "blocked_at")
            if self.in_region(*blocked_point):
                cell = self.cell_for(*blocked_point)
                self._set_cell(cell, CELL_BLOCKED)
                if cell not in cells:
                    cells.append(cell)
        record = ProvenWalkRecord(
            start=points[0],
            goal=points[-1],
            success=bool(success),
            at=moment,
            blocked_at=blocked_point,
            cells=tuple(cells),
        )
        self._proven.append(record)
        return record

    def proven_walks(self) -> list[ProvenWalkRecord]:
        """All proven-path records, in recording order —
        re-readable attributed experience."""
        return list(self._proven)

    # Aliases in doc 14's "proven edges" naming.
    proven_edges = proven_walks

    # -- the one build-only command: move_to (M1 seam contract) ----------------------
    def build_move_to_command(
        self,
        command_id: str,
        x: Any,
        y: Any,
        z: Any = None,
        *,
        mode: str = "walk",
    ) -> Command:
        """Build (do not send, do not record) a ``move_to``
        command for the M1 Core leg.

        Governor-ready: tier ``"mind"``, the destination as
        x/y (and z only when supplied — a height is never
        invented), and the mode explicitly ``"walk"`` or
        ``"fly"`` — any other mode fails closed, and the mode
        is never silently switched. A destination outside the
        region fails closed. Building records NOTHING: no
        walked position, no proven edge, no cell change — the
        walk exists only when the Core executes it and position
        events come back. The Core currently refuses
        ``move_to`` as ``not_implemented_in_minimal_client``;
        this builder is the seam contract for that M1 leg."""
        mode_key = str(mode or "").strip().lower()
        if mode_key not in MOVE_MODES:
            raise ValueError(
                f"a move_to mode must be one of {sorted(MOVE_MODES)}, "
                f"not {mode!r}"
            )
        px, py = _num(x, "x"), _num(y, "y")
        if not self.in_region(px, py):
            raise ValueError(
                f"move_to destination ({px}, {py}) is outside the region"
            )
        params: dict[str, Any] = {"x": px, "y": py, "mode": mode_key}
        if z is not None:
            params["z"] = _num(z, "z")
        return Command(
            id=str(command_id), cmd="move_to", params=params, tier="mind"
        )

    # -- ingest -----------------------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: file what a seam event carries.

        Never raises — a malformed or unknown entry is recorded
        in ``rejected`` and returns None, so one bad event cannot
        stop the perception stream (the dispatcher's own
        isolation rule)."""
        if event.name not in NAVIGATION_EVENTS:
            self.rejected.append(("not_a_navigation_event", event.name))
            return None
        try:
            handler = {
                "object_info": self._ingest_object_info,
                "parcel_changed": self._ingest_parcel_changed,
                "self_position_updated": self._ingest_self_position,
            }[event.name]
            return handler(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    @staticmethod
    def _position_xy(source: dict[str, Any]) -> Point:
        raw = source.get("position") or source.get("pos")
        if raw is not None:
            return _point(raw, "position")
        if "x" in source and "y" in source:
            return (_num(source["x"], "x"), _num(source["y"], "y"))
        raise ValueError("an entry without a position cannot be filed")

    @staticmethod
    def _scale_xy(source: dict[str, Any]) -> tuple[Any, Any]:
        raw = source.get("scale") or source.get("size") or source.get(
            "dimensions"
        )
        if isinstance(raw, dict):
            sx = raw.get("x", raw.get("width"))
            sy = raw.get("y", raw.get("depth"))
            return sx, sy
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            return raw[0], raw[1]
        sx = source.get("scale_x", source.get("size_x"))
        sy = source.get("scale_y", source.get("size_y"))
        return sx, sy

    def _record_object_entry(self, entry: Any) -> list[Cell]:
        if not isinstance(entry, dict):
            raise TypeError("object entry is not an object")
        px, py = self._position_xy(entry)
        scale_x, scale_y = self._scale_xy(entry)
        return self.mark_object_footprint(px, py, scale_x, scale_y)

    def _ingest_object_info(self, event: SeamEvent) -> Any:
        payload = event.payload
        entries = payload.get("objects")
        if isinstance(entries, list):
            filed = []
            for entry in entries:
                try:
                    filed.append(self._record_object_entry(entry))
                except (ValueError, TypeError) as exc:
                    self.rejected.append((str(exc), event.name))
            return filed
        return self._record_object_entry(payload)

    @staticmethod
    def _parcel_flag(entry: dict[str, Any]) -> bool:
        for key in (
            "no_entry", "ban", "banned", "ban_line", "hard_blocked",
            "entry_denied", "no_entry_parcel",
        ):
            if entry.get(key) is True:
                return True
        restriction = entry.get("restriction") or entry.get("access")
        return str(restriction).strip().lower() in (
            "no_entry", "no-entry", "ban", "banned",
        )

    def _record_parcel_entry(self, entry: Any) -> int:
        if not isinstance(entry, dict):
            raise TypeError("parcel entry is not an object")
        bounds = entry.get("bounds") or entry.get("rect") or entry
        if not isinstance(bounds, dict):
            raise TypeError("parcel bounds are not an object")

        def _bound(*keys: str) -> Any:
            for source in (bounds, entry):
                for key in keys:
                    if key in source and source[key] is not None:
                        return source[key]
            return None

        x_min = _bound("x_min", "min_x", "x0", "west")
        y_min = _bound("y_min", "min_y", "y0", "south")
        x_max = _bound("x_max", "max_x", "x1", "east")
        y_max = _bound("y_max", "max_y", "y1", "north")
        if None in (x_min, y_min, x_max, y_max):
            raise ValueError("a parcel without full bounds cannot be filed")
        return self.mark_parcel_no_entry(
            x_min, y_min, x_max, y_max,
            no_entry=self._parcel_flag(entry),
        )

    def _ingest_parcel_changed(self, event: SeamEvent) -> Any:
        payload = event.payload
        entries = payload.get("parcels")
        if isinstance(entries, list):
            filed = []
            for entry in entries:
                try:
                    filed.append(self._record_parcel_entry(entry))
                except (ValueError, TypeError) as exc:
                    self.rejected.append((str(exc), event.name))
            return filed
        nested = payload.get("parcel")
        if isinstance(nested, dict):
            merged = dict(nested)
            merged.update(
                {k: v for k, v in payload.items() if k != "parcel"}
            )
            return self._record_parcel_entry(merged)
        return self._record_parcel_entry(payload)

    def _ingest_self_position(self, event: SeamEvent) -> WalkedRecord:
        payload = event.payload
        px, py = self._position_xy(payload)
        moment = (
            self._payload_timestamp(payload, self._now(None))
            if "timestamp" in payload
            else None
        )
        return self.record_walked(px, py, now=moment)

    # -- purge ---------------------------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident's records; always removes nothing.

        Navigation data is place/self experience: the raster
        describes ground, the walked records and proven edges
        are Yrsa's own experience of that ground, and no record
        here carries any resident's identity — there is no
        resident id on a cell, a walked position, a hint, or a
        proven edge — so there is nothing of a resident's to
        purge. The method exists so uniform per-resident purge
        loops can call it and get the honest answer."""
        return 0


__all__ = [
    "CELL_BLOCKED",
    "CELL_FREE",
    "CELL_HARD_BLOCKED",
    "CELL_STATES",
    "CELL_UNKNOWN",
    "DEFAULT_CELL_SIZE",
    "DEFAULT_REGION_SIZE",
    "DEFAULT_SLOPE_THRESHOLD",
    "MOVE_MODES",
    "NAVIGATION_EVENTS",
    "HintAgreement",
    "HintSegmentResult",
    "HintValidation",
    "NavigationState",
    "NavigationStore",
    "PathValidity",
    "PlanResult",
    "ProvenWalkRecord",
    "WalkedRecord",
]
