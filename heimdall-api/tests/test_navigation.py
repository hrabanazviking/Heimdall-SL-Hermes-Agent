"""M2 + M3 — traversability raster + A* planner (doc 14).

The load-bearing invariants under test:

- UNKNOWN is the default for every cell and is never planned
  through, never traversable, and honestly reported as
  coverage (a fresh raster is 100% unknown);
- object footprints block exactly what position + carried
  scale say (no scale -> only the object's own cell, never a
  larger invented footprint); no-entry/ban parcels hard-block
  exactly their supplied bounds; walked positions become FREE
  only where she actually stood, and never override a hard
  block;
- terrain heights exist only where supplied; a slope blocks a
  step only when both heights are known and exceed the
  configured threshold;
- A* finds the way around obstacles, diagonals never
  corner-cut through blocked/unknown orthogonal neighbours,
  smoothing never crosses a non-free cell, and every failure
  is a distinct honest reason — unreachable / goal unknown /
  goal blocked / goal hard-blocked / start unknown /
  out of region — never a fabricated path;
- an llGetStaticPath-style hint never creates FREE cells,
  never changes a plan, and validates by naming the segments
  that cross unknown/blocked ground;
- a planned path reports itself invalid with a reason once a
  cell on it is newly blocked;
- proven walks are attributed experience records (success
  marks its cells FREE, a blocked walk records the blocked
  cell), re-readable and never facts;
- malformed ingest fails closed into ``rejected`` and never
  raises; the build-only move_to command validates its mode
  (walk/fly), is tier="mind", and records nothing.
"""
import pytest

from heimdall.awareness import (
    CELL_BLOCKED,
    CELL_FREE,
    CELL_HARD_BLOCKED,
    CELL_UNKNOWN,
    NAVIGATION_EVENTS,
    NavigationStore,
)
from heimdall.core.models import CORE_EVENT_ALIASES, SeamEvent, canonical_event_name


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now


def _store(**kwargs):
    kwargs.setdefault("cell_size", 2.0)
    kwargs.setdefault("region_width", 20.0)
    kwargs.setdefault("region_height", 20.0)
    kwargs.setdefault("_clock", FakeClock())
    return NavigationStore(**kwargs)


def _centre(cx, cy, cell_size=2.0):
    return ((cx + 0.5) * cell_size, (cy + 0.5) * cell_size)


def _free_all(store):
    for cx in range(store.columns):
        for cy in range(store.rows):
            store.mark_free(*_centre(cx, cy, store.cell_size))


def _free_cell(store, cx, cy):
    return store.mark_free(*_centre(cx, cy, store.cell_size))


def _ingest(store, event_name, raw, **payload):
    return store.ingest_event(
        SeamEvent(name=event_name, raw_name=raw, payload=payload)
    )


# -- vocabulary: nothing new, nothing duplicated -------------------------

def test_navigation_events_ride_base_vocabulary():
    assert canonical_event_name("ObjectInfo") == "object_info"
    assert canonical_event_name("ParcelEntered") == "parcel_changed"
    assert canonical_event_name("SelfPositionUpdated") == "self_position_updated"
    assert NAVIGATION_EVENTS == {
        "object_info", "parcel_changed", "self_position_updated"
    }
    # Planning is a local computation: no new wire event was
    # invented for it.
    assert canonical_event_name("PathPlanned") is None
    assert "PathPlanned" not in CORE_EVENT_ALIASES
    assert "WalkCompleted" not in CORE_EVENT_ALIASES


# -- honest UNKNOWN raster -------------------------------------------------

def test_fresh_raster_is_entirely_unknown():
    store = NavigationStore(_clock=FakeClock())
    state = store.current_state()
    assert state.columns == 128 and state.rows == 128
    assert state.total_cells == 128 * 128
    assert state.unknown_cells == state.total_cells
    assert state.unknown_percent == 100.0
    assert state.coverage_percent == 0.0
    assert store.snapshot()["unknown"] == state.total_cells
    assert store.cell_state(10, 10) == CELL_UNKNOWN
    assert store.is_traversable(10, 10) is False
    assert "100.0% unknown" in store.describe()


def test_counts_and_describe_report_coverage_honestly():
    store = _store()
    _free_cell(store, 0, 0)
    store.mark_blocked(3, 3)
    store.mark_hard_blocked(5, 5)
    counts = store.counts_by_state()
    assert counts[CELL_FREE] == 1
    assert counts[CELL_BLOCKED] == 1
    assert counts[CELL_HARD_BLOCKED] == 1
    assert counts[CELL_UNKNOWN] == 100 - 3
    assert store.snapshot()["total"] == 100
    text = store.describe()
    assert "97.0% unknown" in text
    assert "never treated as free" in text


def test_unknown_never_planned_through():
    store = _store()
    _free_cell(store, 0, 0)
    _free_cell(store, 9, 9)
    result = store.plan((1, 1), (19, 19))
    assert result.success is False
    assert result.reason == "unreachable"
    assert result.path == []


# -- object footprints -------------------------------------------------------

def test_object_without_scale_blocks_only_its_own_cell():
    store = _store()
    _ingest(store, "object_info", "ObjectInfo",
            position={"x": 10, "y": 10, "z": 1})
    assert store.cell_state(10, 10) == CELL_BLOCKED
    assert store.snapshot()[CELL_BLOCKED] == 1
    # No larger footprint was invented around it.
    assert store.cell_state(8, 10) == CELL_UNKNOWN
    assert store.cell_state(12, 12) == CELL_UNKNOWN


def test_object_footprint_uses_carried_scale():
    store = _store()
    _ingest(store, "object_info", "ObjectInfo",
            position={"x": 10, "y": 10},
            scale={"x": 6, "y": 4})
    # Rect x in [7,13] -> cells 3..6; y in [8,12] -> cells 4..5.
    assert store.snapshot()[CELL_BLOCKED] == 8
    assert store.cell_state(7, 9) == CELL_BLOCKED
    assert store.cell_state(13, 11) == CELL_BLOCKED
    assert store.cell_state(5, 9) == CELL_UNKNOWN


def test_object_list_ingest_files_each_and_rejects_bad_entries():
    store = _store()
    filed = _ingest(store, "object_info", "ObjectInfo", objects=[
        {"position": {"x": 2, "y": 2}},
        {"name": "no position here"},
        {"position": {"x": 6, "y": 6}, "size": [4, 4]},
    ])
    assert len(filed) == 2
    assert len(store.rejected) == 1
    assert store.cell_state(2, 2) == CELL_BLOCKED
    assert store.cell_state(6, 6) == CELL_BLOCKED


# -- parcels: hard blocks -------------------------------------------------------

def test_parcel_no_entry_hard_blocks_exact_bounds():
    store = _store()
    marked = store.mark_parcel_no_entry(4, 4, 12, 12, no_entry=True)
    assert marked == 16
    assert store.cell_state(5, 5) == CELL_HARD_BLOCKED
    assert store.cell_state(11, 11) == CELL_HARD_BLOCKED
    assert store.cell_state(13, 13) == CELL_UNKNOWN
    assert store.is_traversable(5, 5) is False


def test_parcel_without_no_entry_flag_marks_nothing():
    store = _store()
    assert store.mark_parcel_no_entry(4, 4, 12, 12, no_entry=False) == 0
    assert store.snapshot()[CELL_HARD_BLOCKED] == 0


def test_parcel_ingest_hard_blocks_only_flagged_parcels():
    store = _store()
    _ingest(store, "parcel_changed", "ParcelChanged",
            bounds={"x_min": 0, "y_min": 0, "x_max": 8, "y_max": 8},
            no_entry=True)
    assert store.cell_state(1, 1) == CELL_HARD_BLOCKED
    # An unflagged parcel change is valid data that hard-blocks
    # nothing — traversability is not claimed from it.
    _ingest(store, "parcel_changed", "ParcelChanged",
            bounds={"x_min": 10, "y_min": 10, "x_max": 18, "y_max": 18})
    assert store.cell_state(11, 11) == CELL_UNKNOWN


def test_planner_refuses_hard_blocked_goal_and_never_crosses():
    store = _store()
    _free_all(store)
    store.mark_parcel_no_entry(8, 0, 12, 20, no_entry=True)
    result = store.plan((1, 1), (19, 1))
    assert result.success is False and result.reason == "unreachable"
    goal_in_parcel = store.plan((1, 1), (9, 1))
    assert goal_in_parcel.reason == "goal_hard_blocked"


# -- walked ground -----------------------------------------------------------------

def test_walked_positions_become_free_with_timestamps():
    store = _store()
    record = store.record_walked(3, 3, now=42.0)
    assert record.cell == (1, 1) and record.at == 42.0
    assert store.cell_state(3, 3) == CELL_FREE
    assert store.is_traversable(3, 3) is True
    # Only where she stood: the neighbouring cell stays unknown.
    assert store.cell_state(5, 3) == CELL_UNKNOWN


def test_self_position_ingest_marks_walked_cell():
    store = _store()
    record = _ingest(store, "self_position_updated", "SelfPositionUpdated",
                     position=[5, 5, 22.0], timestamp=77.0)
    assert record is not None and record.cell == (2, 2)
    assert record.at == 77.0
    assert store.cell_state(5, 5) == CELL_FREE
    assert store.walked_positions() == [record]


def test_walked_never_overrides_hard_block():
    store = _store()
    store.mark_hard_blocked(3, 3)
    store.record_walked(3, 3)
    assert store.cell_state(3, 3) == CELL_HARD_BLOCKED


# -- terrain heights & slope -----------------------------------------------------------

def test_terrain_height_only_where_supplied():
    store = _store()
    assert store.terrain_height(1, 1) is None
    store.set_terrain_height(1, 1, 22.5)
    assert store.terrain_height(1, 1) == 22.5
    # Never interpolated to a neighbour.
    assert store.terrain_height(3, 1) is None
    assert store.current_state().heights_known == 1


def test_steep_slope_blocks_step_only_when_both_heights_known():
    def _row_store(**kwargs):
        store = _store(**kwargs)
        for cx in range(3):
            _free_cell(store, cx, 0)
        return store

    steep = _row_store(slope_threshold=2.0)
    steep.set_terrain_height(1, 1, 0.0)
    steep.set_terrain_height(3, 1, 0.0)
    steep.set_terrain_height(5, 1, 10.0)
    result = steep.plan((1, 1), (5, 1))
    assert result.success is False and result.reason == "unreachable"

    unmeasured = _row_store(slope_threshold=2.0)
    assert unmeasured.plan((1, 1), (5, 1)).success is True

    no_threshold = _row_store(slope_threshold=None)
    no_threshold.set_terrain_height(1, 1, 0.0)
    no_threshold.set_terrain_height(5, 1, 10.0)
    assert no_threshold.plan((1, 1), (5, 1)).success is True


# -- A*: around obstacles, distinct failures ----------------------------------------------

def test_astar_finds_path_around_obstacle_wall():
    store = _store()
    _free_all(store)
    for cy in range(9):  # wall column cx=5, gap only at cy=9
        store.mark_blocked(*_centre(5, cy))
    result = store.plan((1, 1), (19, 1))
    assert result.success is True
    assert result.path[0] == (1.0, 1.0)
    assert result.path[-1] == (19.0, 1.0)
    assert (5, 9) in result.cells  # through the one gap
    assert store.path_still_valid(result.path).valid is True


def test_plan_failures_are_distinct_and_honest():
    store = _store()
    _free_cell(store, 0, 0)
    reasons = {}
    reasons["goal_unknown"] = store.plan((1, 1), (19, 19)).reason
    store.mark_blocked(19, 19)
    reasons["goal_blocked"] = store.plan((1, 1), (19, 19)).reason
    store.mark_hard_blocked(19, 19)
    reasons["goal_hard"] = store.plan((1, 1), (19, 19)).reason
    _free_cell(store, 9, 9)
    reasons["start_unknown"] = store.plan((5, 5), (19, 19)).reason
    reasons["out"] = store.plan((1, 1), (300, 10)).reason
    assert reasons == {
        "goal_unknown": "goal_unknown",
        "goal_blocked": "goal_blocked",
        "goal_hard": "goal_hard_blocked",
        "start_unknown": "start_unknown",
        "out": "out_of_region",
    }
    for result in (store.plan((1, 1), (19, 19)),):
        assert result.path == []  # never a fabricated path


def test_plan_within_one_free_cell_succeeds():
    store = _store()
    _free_cell(store, 2, 2)
    result = store.plan((5, 5), (5.5, 5.5))
    assert result.success is True
    assert result.path[-1] == (5.5, 5.5)


def test_diagonal_never_corner_cuts():
    store = _store()
    _free_cell(store, 0, 0)
    _free_cell(store, 1, 1)
    # Both orthogonal neighbours of the diagonal are unknown:
    # the direct diagonal step is forbidden and no other route
    # exists.
    result = store.plan((1, 1), (3, 3))
    assert result.success is False and result.reason == "unreachable"
    # Freeing ONE orthogonal neighbour opens an orthogonal
    # detour — legitimate — but the plan must still never take
    # the diagonal step itself.
    _free_cell(store, 1, 0)
    detour = store.plan((1, 1), (3, 3))
    assert detour.success is True
    for a, b in zip(detour.cells, detour.cells[1:]):
        assert a[0] == b[0] or a[1] == b[1]  # no diagonal step


def test_smoothing_shortcuts_clear_ground_only():
    store = _store()
    _free_all(store)
    straight = store.plan((1, 1), (19, 1))
    assert straight.path == [(1.0, 1.0), (19.0, 1.0)]

    elbow = _store()
    for cx in range(10):
        _free_cell(elbow, cx, 0)
    for cy in range(10):
        _free_cell(elbow, 9, cy)
    result = elbow.plan((1, 1), (19, 19))
    assert result.success is True
    # The shortcut must not cut across the unknown interior:
    # every crossed cell of the smoothed path is FREE.
    assert elbow.path_still_valid(result.path).valid is True
    assert len(result.path) <= 4


def test_adjacency_penalty_still_plans():
    store = _store(adjacency_penalty=1.5)
    _free_all(store)
    store.mark_blocked(10, 10)
    assert store.plan((1, 1), (19, 19)).success is True


# -- hint: cross-check only -----------------------------------------------------------------

def test_hint_never_creates_free_cells_and_flags_unknown_crossing():
    store = _store()
    before = store.snapshot()
    store.accept_hint([(1, 1), (19, 19)])
    assert store.snapshot() == before  # no cell changed at all
    validation = store.validate_hint()
    assert validation.clear is False
    assert validation.segments[0].status == "crosses_unknown"
    assert validation.segments[0].offending_cells


def test_hint_validation_flags_blocked_crossing():
    store = _store()
    _free_all(store)
    store.mark_blocked(10, 10)
    store.accept_hint([(1, 1), (19, 19)])
    segment = store.validate_hint().segments[0]
    assert segment.status == "crosses_blocked"
    assert (5, 5) in segment.offending_cells


def test_hint_does_not_change_plans_and_agreement_is_reported():
    store = _store()
    _free_all(store)
    store.accept_hint([(1, 1), (19, 1)])
    result = store.plan((1, 1), (19, 1))
    assert result.success is True and result.path == [(1.0, 1.0), (19.0, 1.0)]
    agreement = store.hint_agreement(result.path)
    assert agreement.has_hint is True
    assert agreement.validation.clear is True
    assert agreement.path_follows_hint is True
    assert agreement.agrees is True
    with pytest.raises(ValueError):
        store.accept_hint([(1, 1), (999, 1)])


# -- replan / path validity ---------------------------------------------------------------------

def test_path_still_valid_until_new_obstacle_lands():
    store = _store()
    _free_all(store)
    result = store.plan((1, 1), (19, 1))
    assert store.path_still_valid(result.path).valid is True
    store.mark_blocked(10, 1)
    validity = store.path_still_valid(result.path)
    assert validity.valid is False
    assert bool(validity) is False
    assert validity.reason == "path_cell_blocked"
    assert validity.offending_cell == (5, 0)
    # Replanning over the current raster routes around it.
    replanned = store.plan((1, 1), (19, 1))
    assert replanned.success is True
    assert store.path_still_valid(replanned.path).valid is True


# -- proven-path memory ---------------------------------------------------------------------------

def test_proven_walk_success_marks_segment_free_and_is_rereadable():
    store = _store()
    record = store.record_walk_result((1, 1), (9, 1), True, at=123.0)
    assert record.success is True and record.at == 123.0
    assert record.start == (1.0, 1.0) and record.goal == (9.0, 1.0)
    for x in (1, 3, 5, 7, 9):
        assert store.cell_state(x, 1) == CELL_FREE
    assert store.proven_walks() == [record]
    assert store.proven_edges() == [record]


def test_proven_walk_blocked_records_blocked_cell_and_no_free():
    store = _store()
    record = store.record_walk_result(
        (1, 5), (9, 5), False, blocked_at=(5, 5), at=9.0
    )
    assert record.success is False
    assert record.blocked_at == (5.0, 5.0)
    assert store.cell_state(5, 5) == CELL_BLOCKED
    assert store.cell_state(1, 5) == CELL_UNKNOWN
    assert store.cell_state(9, 5) == CELL_UNKNOWN


def test_proven_walk_segment_list_form():
    store = _store()
    record = store.record_walk_result(
        (1, 1), [(5, 1), (5, 5)], True, at=1.0
    )
    assert record.goal == (5.0, 5.0)
    assert store.cell_state(5, 5) == CELL_FREE
    assert store.cell_state(5, 1) == CELL_FREE


# -- fail-closed ingest --------------------------------------------------------------------------------

def test_malformed_ingest_fails_closed_and_never_raises():
    store = _store()
    before = store.snapshot()
    assert _ingest(store, "object_info", "ObjectInfo",
                   name="positionless") is None
    assert _ingest(store, "parcel_changed", "ParcelChanged",
                   no_entry=True) is None  # no bounds carried
    assert _ingest(store, "self_position_updated", "SelfPositionUpdated",
                   position={"x": "east", "y": 5}) is None
    assert _ingest(store, "chat", "ChatReceived", msg="hi") is None
    assert store.snapshot() == before
    assert len(store.rejected) == 4
    assert store.rejected[-1] == ("not_a_navigation_event", "chat")
    assert store.walked_positions() == []


# -- move_to: build-only -----------------------------------------------------------------------------------

def test_move_to_builder_is_governor_ready_and_records_nothing():
    store = _store()
    before = store.snapshot()
    command = store.build_move_to_command("cmd-1", 10, 12, 22.0, mode="fly")
    assert command.cmd == "move_to"
    assert command.tier == "mind"
    assert command.params == {"x": 10.0, "y": 12.0, "mode": "fly", "z": 22.0}
    walking = store.build_move_to_command("cmd-2", 3, 4)
    assert walking.params == {"x": 3.0, "y": 4.0, "mode": "walk"}
    assert "z" not in walking.params  # no invented height
    # Building recorded nothing at all.
    assert store.snapshot() == before
    assert store.walked_positions() == []
    assert store.proven_walks() == []


@pytest.mark.parametrize("mode", ["teleport", "run", "", "FLYING", None])
def test_move_to_builder_rejects_invalid_modes(mode):
    store = _store()
    with pytest.raises(ValueError):
        store.build_move_to_command("cmd-x", 10, 10, mode=mode)


def test_move_to_builder_rejects_out_of_region_destination():
    store = _store()
    with pytest.raises(ValueError):
        store.build_move_to_command("cmd-x", 300, 10)
    with pytest.raises(ValueError):
        store.build_move_to_command("cmd-x", -1, 10)


# -- purge --------------------------------------------------------------------------------------------------------

def test_purge_resident_removes_nothing_because_no_resident_identity():
    store = _store()
    store.record_walked(3, 3)
    store.record_walk_result((1, 1), (9, 1), True)
    assert store.purge_resident("c3333333-3333-4333-8333-333333333333") == 0
    assert store.purge_resident("") == 0
    # Place/self experience is untouched by a resident purge.
    assert store.cell_state(3, 3) == CELL_FREE
    assert len(store.proven_walks()) == 1
