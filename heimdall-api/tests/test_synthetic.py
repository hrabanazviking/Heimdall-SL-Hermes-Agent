"""V3 — synthetic view (visual capture doc 16, Route C).

The load-bearing invariants under test:

- meshing is exactly what events carried: a box of 8 vertices
  / 12 triangles / 6 outward faces centred at the carried
  position with the carried dimensions, from any of the
  F6/M2 scale field forms — and an object without a carried
  scale (or with a partial one, or without a position) is
  held known-but-unmeshable with its reason, never given an
  invented size and never rendered;
- rotation policy is pinned: no sibling module parses a
  rotation, so a non-identity rotation holds the object
  unmeshable as ``rotated_non_identity`` — never silently
  rendered axis-aligned — while an identity rotation meshes;
- occlusion is by construction: a true per-pixel depth
  buffer hides a farther box entirely behind a nearer one,
  an object behind the camera never appears, and a camera
  looking away renders background only;
- the PNG is real (magic + IHDR) and byte-deterministic for
  an identical scene + camera; the provenance record and the
  ``visual_observation`` expression carry
  source/label ``synthetic_render``, the camera, the
  object-set timestamp and the visible ids — an attributed
  computed observation, never a photograph;
- staleness is computable: the object-set timestamp is the
  latest ingest/update time on the injectable clock;
- spool + purge: a spooled render's file exists, and
  ``purge_resident`` removes exactly the objects tied to the
  resident plus the spooled renders whose included-object
  set names a removed object, leaving untied ones;
- malformed ingest fails closed into ``rejected`` and never
  raises, applying nothing; avatar events are rejected,
  never drawn; no new seam vocabulary was added.
"""
import math
import struct

import pytest

from heimdall.awareness import (
    SYNTHETIC_EVENTS,
    SyntheticStore,
    base_colour_for,
    build_box_mesh,
)
from heimdall.awareness.synthetic import (
    REASON_INCOMPLETE_SCALE,
    REASON_MISSING_POSITION,
    REASON_MISSING_SCALE,
    REASON_ROTATED,
)
from heimdall.core.models import SeamEvent, canonical_event_name


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def _store(tmp_path=None, clock=None, spool=False):
    return SyntheticStore(
        spool_dir=tmp_path if spool else None,
        _clock=FakeClock() if clock is None else clock,
    )


def _ingest(store, payload, raw="ObjectInfo"):
    return store.ingest_event(
        SeamEvent(name="object_info", raw_name=raw, payload=payload)
    )


def _box(store, object_id, position, scale, **extra):
    payload = {
        "object_id": object_id,
        "name": object_id,
        "position": list(position),
        "scale": list(scale),
    }
    payload.update(extra)
    return _ingest(store, payload)


# -- meshing exactness ---------------------------------------------------


def test_box_meshing_exactness():
    store = _store()
    record = _box(store, "crate", (1.0, 2.0, 3.0), (2.0, 4.0, 6.0))
    assert record is not None and record.meshable
    mesh = store.mesh_for("crate")
    assert mesh is not None
    assert mesh.vertex_count == 8
    assert mesh.triangle_count == 12
    assert len(mesh.face_normals) == 6
    assert mesh.centre == (1.0, 2.0, 3.0)
    assert mesh.dimensions == (2.0, 4.0, 6.0)
    xs = sorted({v[0] for v in mesh.vertices})
    ys = sorted({v[1] for v in mesh.vertices})
    zs = sorted({v[2] for v in mesh.vertices})
    assert xs == [0.0, 2.0] and ys == [0.0, 4.0] and zs == [0.0, 6.0]


def test_mesh_triangle_winding_faces_outward():
    mesh = build_box_mesh("m", (0.0, 0.0, 0.0), (2.0, 2.0, 2.0))
    for index, tri in enumerate(mesh.triangles):
        a, b, c = (mesh.vertices[i] for i in tri)
        ux, uy, uz = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        vx, vy, vz = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        face = mesh.face_normals[index // 2]
        dot = sum(n * f for n, f in zip(normal, face))
        assert dot > 0  # winding agrees with the outward normal


def test_scale_field_variants_all_mesh():
    store = _store()
    _ingest(store, {
        "id": "via-size-dict", "position": [0, 5, 0],
        "size": {"width": 2, "depth": 2, "height": 2},
    })
    _ingest(store, {
        "uuid": "via-dimensions", "position": [5, 5, 0],
        "dimensions": [2, 2, 2],
    })
    _ingest(store, {
        "object_id": "via-axes", "position": [-5, 5, 0],
        "scale_x": 2, "scale_y": 2, "scale_z": 2,
    })
    _ingest(store, {
        "object_id": "via-pos-dict", "pos": {"x": 0, "y": 9, "z": 0},
        "scale": {"x": 1, "y": 1, "z": 1},
    })
    state = store.current_state()
    assert state.meshed == 4 and state.unmeshable == 0


# -- unmeshable honesty ----------------------------------------------------


def test_no_scale_object_unmeshable_and_never_rendered():
    store = _store()
    record = _ingest(store, {
        "object_id": "ghost", "name": "ghost", "position": [0, 5, 0],
    })
    assert record is not None and not record.meshable
    assert record.unmeshable_reason == REASON_MISSING_SCALE
    assert store.mesh_for("ghost") is None
    state = store.current_state()
    assert state.known_objects == 1 and state.meshed == 0
    assert state.unmeshable_reasons == {REASON_MISSING_SCALE: 1}
    result = store.render((0, 0, 0), (0, 10, 0), width=32, height=24)
    assert result.is_empty and result.visible == []


def test_partial_scale_unmeshable():
    store = _store()
    record = _ingest(store, {
        "object_id": "flat", "position": [0, 5, 0], "scale": [2, 2],
    })
    assert record is not None and not record.meshable
    assert record.unmeshable_reason == REASON_INCOMPLETE_SCALE


def test_missing_position_unmeshable_not_rejected():
    store = _store()
    record = _ingest(store, {"object_id": "lost", "scale": [1, 1, 1]})
    assert record is not None and not record.meshable
    assert record.unmeshable_reason == REASON_MISSING_POSITION
    assert store.rejected == []


def test_rotation_non_identity_held_unmeshable():
    store = _store()
    record = _ingest(store, {
        "object_id": "tilted", "position": [0, 5, 0], "scale": [2, 2, 2],
        "rotation": [0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)],
    })
    assert record is not None and not record.meshable
    assert record.unmeshable_reason == REASON_ROTATED
    assert store.mesh_for("tilted") is None
    result = store.render((0, 0, 0), (0, 10, 0), width=32, height=24)
    assert result.is_empty  # never silently rendered axis-aligned


def test_rotation_identity_meshes():
    store = _store()
    record = _ingest(store, {
        "object_id": "level", "position": [0, 5, 0], "scale": [2, 2, 2],
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    })
    assert record is not None and record.meshable
    assert store.mesh_for("level") is not None


def test_object_update_replaces_geometry():
    clock = FakeClock()
    store = _store(clock=clock)
    _box(store, "mover", (0, 5, 0), (2, 2, 2))
    clock.advance(10)
    _box(store, "mover", (0, 9, 0), (4, 4, 4))
    assert len(store.objects()) == 1
    mesh = store.mesh_for("mover")
    assert mesh is not None and mesh.centre == (0.0, 9.0, 0.0)
    assert mesh.dimensions == (4.0, 4.0, 4.0)
    assert store.object_set_timestamp == 1010.0


# -- rendering: occlusion, framing, visible list ----------------------------


def _occlusion_store():
    store = _store()
    _box(store, "near-box", (0, 5, 0), (2, 2, 2))
    _box(store, "far-box", (0, 10, 0), (2, 2, 2))
    return store


def test_occlusion_near_hides_far():
    store = _occlusion_store()
    result = store.render((0, 0, 0), (0, 10, 0), width=48, height=36)
    visible_ids = [v.object_id for v in result.visible]
    assert visible_ids == ["near-box"]  # the far box owns 0 pixels
    centre = result.object_id_buffer[18][24]
    assert centre == "near-box"
    near = result.visible[0]
    assert near.pixel_count > 0
    assert near.nearest_depth == pytest.approx(4.0)
    total_owned = sum(
        1 for row in result.object_id_buffer for cell in row if cell
    )
    assert total_owned == near.pixel_count


def test_object_behind_camera_renders_empty():
    store = _store()
    _box(store, "behind", (0, -5, 0), (2, 2, 2))
    result = store.render((0, 0, 0), (0, 10, 0), width=32, height=24)
    assert result.is_empty
    assert all(
        cell is None for row in result.object_id_buffer for cell in row
    )
    assert all(
        cell is None for row in result.depth_buffer for cell in row
    )


def test_camera_looking_away_renders_empty():
    store = _occlusion_store()
    result = store.render((0, 0, 0), (0, -10, 0), width=32, height=24)
    assert result.is_empty and result.visible == []


def test_visible_list_correctness_and_ordering():
    store = _store()
    _box(store, "left-far", (-3, 10, 0), (2, 2, 2))
    _box(store, "right-near", (3, 8, 0), (2, 2, 2))
    result = store.render((0, 0, 0), (0, 10, 0), width=64, height=48)
    assert [v.object_id for v in result.visible] == [
        "right-near", "left-far",
    ]
    for entry in result.visible:
        assert entry.pixel_count > 0
    depths = [v.nearest_depth for v in result.visible]
    assert depths == sorted(depths)
    assert result.visible[0].nearest_depth == pytest.approx(7.0)
    assert result.visible[1].nearest_depth == pytest.approx(9.0)


def test_render_size_and_direction_configurable():
    store = _occlusion_store()
    result = store.render(
        (0, 0, 0), direction=(0, 1, 0), width=20, height=10,
        fov_degrees=45.0,
    )
    assert len(result.object_id_buffer) == 10
    assert all(len(row) == 20 for row in result.object_id_buffer)
    assert result.visible  # still sees the near box


def test_render_facing_validation():
    store = _occlusion_store()
    with pytest.raises(ValueError):
        store.render((0, 0, 0))
    with pytest.raises(ValueError):
        store.render((0, 0, 0), (0, 10, 0), direction=(0, 1, 0))
    with pytest.raises(ValueError):
        store.render((0, 0, 0), (0, 0, 0))  # zero facing


# -- PNG + determinism -------------------------------------------------------


def test_png_validity_and_determinism():
    store = _occlusion_store()
    first = store.render((0, 0, 0), (0, 10, 0), width=48, height=36)
    second = store.render((0, 0, 0), (0, 10, 0), width=48, height=36)
    png = first.png_bytes
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[12:16] == b"IHDR"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (48, 36)
    assert second.png_bytes == png  # identical scene+camera, identical bytes
    assert second.depth_buffer == first.depth_buffer


def test_base_colour_deterministic_synthetic_palette():
    colour = base_colour_for("near-box")
    assert colour == base_colour_for("near-box")
    assert all(64 <= channel <= 223 for channel in colour)


# -- provenance, observation, staleness ---------------------------------------


def test_provenance_record_and_observation(tmp_path):
    clock = FakeClock()
    store = _store(tmp_path, clock=clock, spool=True)
    _box(store, "crate", (0, 5, 0), (2, 2, 2))
    clock.advance(30)
    result = store.render((0, 0, 0), (0, 10, 0), width=32, height=24)
    record = result.record
    assert record.source == "synthetic_render"
    assert record.label == "synthetic_render"
    assert record.camera_position == (0.0, 0.0, 0.0)
    assert (record.width, record.height) == (32, 24)
    assert record.object_set_timestamp == 1000.0
    assert record.rendered_at == 1030.0
    assert record.byte_count == len(result.png_bytes)
    assert record.visible_object_ids == ("crate",)
    assert record.spool_path is not None
    observation = result.to_observation()
    assert observation.event_name == "visual_observation"
    assert observation.source == "synthetic_render"
    assert observation.label == "synthetic_render"
    assert observation.object_set_timestamp == 1000.0
    assert observation.visible_object_ids == ("crate",)


def test_object_set_timestamp_and_staleness():
    clock = FakeClock()
    store = _store(clock=clock)
    assert store.object_set_timestamp is None
    _box(store, "a", (0, 5, 0), (2, 2, 2))
    assert store.object_set_timestamp == 1000.0
    clock.advance(50)
    _box(store, "b", (4, 5, 0), (2, 2, 2))
    assert store.object_set_timestamp == 1050.0
    clock.advance(50)
    result = store.render((0, 0, 0), (0, 10, 0), width=32, height=24)
    assert result.object_set_timestamp == 1050.0
    assert result.rendered_at - result.object_set_timestamp == 50.0


# -- spool + purge --------------------------------------------------------------


def test_spool_file_written_and_purge_resident(tmp_path):
    store = _store(tmp_path, spool=True)
    _box(store, "alice-box", (-6, 10, 0), (2, 2, 2), owner_id="res-1")
    _box(store, "bob-box", (3, 10, 0), (2, 2, 2), owner_id="res-2")
    both = store.render((0, 0, 0), (0, 10, 0), width=64, height=48)
    assert set(both.record.visible_object_ids) == {"alice-box", "bob-box"}
    bob_only = store.render(
        (3, 0, 0), (3, 10, 0), width=64, height=48, fov_degrees=40.0
    )
    assert bob_only.record.visible_object_ids == ("bob-box",)
    assert len(store.records()) == 2
    from pathlib import Path

    assert Path(both.record.spool_path).read_bytes() == both.png_bytes
    assert Path(bob_only.record.spool_path).exists()

    removed = store.purge_resident("res-1")
    assert removed == 2  # alice's object + the render that included it
    assert store.get_object("alice-box") is None
    assert store.get_object("bob-box") is not None
    assert not Path(both.record.spool_path).exists()
    assert Path(bob_only.record.spool_path).exists()
    assert store.records() == [bob_only.record]


def test_purge_untied_resident_removes_nothing(tmp_path):
    store = _store(tmp_path, spool=True)
    _box(store, "bob-box", (0, 5, 0), (2, 2, 2), owner_id="res-2")
    store.render((0, 0, 0), (0, 10, 0), width=32, height=24)
    assert store.purge_resident("res-nobody") == 0
    assert store.purge_resident("") == 0
    assert len(store.records()) == 1
    assert store.get_object("bob-box") is not None


# -- fail-closed ingest ----------------------------------------------------------


@pytest.mark.parametrize("entry", [
    {},  # missing id
    {"object_id": "x", "position": "nowhere", "scale": [1, 1, 1]},
    {"object_id": "x", "position": [0, 5], "scale": [1, 1, 1]},
    {"object_id": "x", "position": [0, float("nan"), 0], "scale": [1, 1, 1]},
    {"object_id": "x", "position": [0, 5, 0], "scale": [-1, 2, 2]},
    {"object_id": "x", "position": [0, 5, 0],
     "scale": {"x": "wide", "y": 1, "z": 1}},
    {"object_id": "x", "position": [0, 5, 0], "scale": 5},
    {"object_id": "x", "position": [0, 5, 0], "scale": [1, 1, 1],
     "rotation": "tilted"},
])
def test_malformed_entries_rejected_without_applying(entry):
    store = _store()
    result = _ingest(store, entry)
    assert result is None
    assert store.objects() == []
    assert store.current_state().meshed == 0
    assert len(store.rejected) == 1


def test_unknown_and_avatar_events_rejected():
    store = _store()
    for name in ("avatar_appeared", "chat", "self_position_updated"):
        outcome = store.ingest_event(
            SeamEvent(name=name, raw_name=name, payload={})
        )
        assert outcome is None
    assert len(store.rejected) == 3
    assert store.objects() == []


def test_list_payload_partial_failure():
    store = _store()
    filed = _ingest(store, {"objects": [
        {"object_id": "good", "position": [0, 5, 0], "scale": [2, 2, 2]},
        {"position": [0, 5, 0], "scale": [2, 2, 2]},  # no id
        {"object_id": "also-good", "position": [5, 5, 0],
         "scale": [1, 1, 1]},
    ]})
    assert [r.id for r in filed] == ["good", "also-good"]
    assert len(store.rejected) == 1
    assert store.current_state().meshed == 2


# -- honesty surfaces --------------------------------------------------------------


def test_empty_scene_render_and_honest_describe():
    store = _store()
    before = store.describe()
    assert "no synthetic view has been rendered" in before
    assert store.current_state().vision_label == "none (object-data only)"
    result = store.render((0, 0, 0), (0, 10, 0), width=32, height=24)
    assert result.is_empty and result.visible == []
    assert result.object_set_timestamp is None
    after = store.describe()
    assert "vision: synthetic view only" in after
    assert "background only" in after
    state = store.current_state()
    assert state.renders == 1 and state.last_render_visible == 0


def test_describe_counts_meshed_and_unmeshable():
    store = _store()
    _box(store, "crate", (0, 5, 0), (2, 2, 2))
    _ingest(store, {"object_id": "ghost", "position": [3, 5, 0]})
    store.render((0, 0, 0), (0, 10, 0), width=32, height=24)
    text = store.describe()
    assert "1 object(s) meshed" in text
    assert "1 held unmeshable" in text
    state = store.current_state()
    assert state.known_objects == 2 and state.meshed == 1
    assert state.unmeshable == 1


def test_no_new_vocabulary():
    assert SYNTHETIC_EVENTS == frozenset({"object_info"})
    assert canonical_event_name("ObjectInfo") == "object_info"
    assert canonical_event_name("VisualObservation") == "visual_observation"
    from heimdall.core.models import CORE_EVENT_ALIASES

    object_forms = [k for k, v in CORE_EVENT_ALIASES.items()
                    if v == "object_info"]
    # Exactly the pre-existing base forms (ObjectInfo /
    # ObjectAppeared + their API spellings) — this slice added
    # no name for the vocabulary it ingests.
    assert sorted(object_forms) == [
        "ObjectAppeared", "ObjectInfo", "object_appeared", "object_info",
    ]
