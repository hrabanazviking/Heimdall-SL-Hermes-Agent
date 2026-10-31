"""V4 — attended photo handoff (visual capture doc 16,
Route D, per the doc 17 protocol).

The load-bearing invariants under test:

- a handover files only as what it is: PNG bytes (magic
  checked) plus a sidecar carrying exactly
  ``source="attended_snapshot"`` and ``label="photograph"``,
  a sanctioned place kind, a parseable capture moment, a
  named photographer, and a resident-visibility value that
  is a non-empty list or exactly ``"unknown"`` — every other
  shape fails closed into the ``rejected`` audit, files
  nothing, spools nothing, and never raises;
- unknowns are held as None — region / grid / camera are
  never guessed or borrowed from a previous photo;
- filing spools PNG + sidecar JSON together under the
  protocol filename (capture timestamp as reported,
  region slug or ``region-unknown``, per-store sequence so
  same-second shots never collide) with full provenance —
  source, label, photographer, region, grid, camera,
  residents, place kind, capture moment, spool path, byte
  count, SHA-256;
- a duplicate SHA-256 is not filed twice: duplicate
  outcome, nothing new spooled or recorded;
- every filed photo is a ``visual_observation`` labelled a
  photograph whose text attributes the image in words to
  the photographer's snapshot — perception, never a fact,
  never "what Yrsa sees";
- the vision state is honest: ``none (object-data only)``
  before any photo, ``attended photograph only`` once one
  is filed — including through the scene digest, where an
  attended store with a photo outranks a map store, an
  empty attended store falls through, and an explicit
  vision still wins;
- purge deletes exactly the photos listing the resident
  (files + records), reports partial honestly while an
  ``"unknown"``-visibility photo remains, and removes 0
  for a resident tied to nothing;
- no login, no network, no photograph taken: filing is a
  local act over bytes + sidecar a caller already holds.
"""
import hashlib
import json
from pathlib import Path

import pytest

from heimdall.awareness import (
    AttendedPhotoStore,
    PresenceTracker,
    SceneDigestBuilder,
    VisualStore,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG1 = PNG_MAGIC + b"first-fake-but-plausible-photograph"
PNG2 = PNG_MAGIC + b"second-fake-but-plausible-photograph"
PNG3 = PNG_MAGIC + b"third-fake-but-plausible-photograph"
JPEG = b"\xff\xd8\xff\xe0" + b"a-map-tile-is-not-a-photograph" + b"\xff\xd9"


class FakeClock:
    def __init__(self, start=2000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def _sidecar(**overrides):
    sidecar = {
        "captured_at": "2026-10-31T01:15:00-04:00",
        "photographer": "Volmarr",
        "source": "attended_snapshot",
        "label": "photograph",
        "region": "Viking Village",
        "grid_x": 1000,
        "grid_y": 1001,
        "camera": "harbour pier, facing north",
        "residents_visible": ["Yrsa Freydisdottir"],
        "place_kind": "attended",
        "notes": "from the pier",
    }
    sidecar.update(overrides)
    return sidecar


def _store(tmp_path, **kwargs):
    kwargs.setdefault("_clock", FakeClock())
    return AttendedPhotoStore(spool_dir=tmp_path, **kwargs)


# -- honest empty state -------------------------------------------------

def test_vision_is_none_before_any_photo(tmp_path):
    store = _store(tmp_path)
    assert store.has_photos is False
    assert store.vision_label() == "none (object-data only)"
    state = store.current_state()
    assert state.known is False and state.filed == 0
    assert state.duplicates_refused == 0 and state.rejected == 0
    assert state.unknown_photos == 0
    assert "vision: none (object-data only)" in store.describe()
    assert "attended photograph only" not in store.describe()
    assert store.records() == [] and store.observations() == []
    assert store.latest_record() is None


# -- successful filing ----------------------------------------------------

def test_successful_filing_spools_pair_with_provenance(tmp_path):
    store = _store(tmp_path)
    filing = store.file_photo(PNG1, _sidecar())
    assert filing.outcome == "filed" and filing.is_filed
    assert filing.reason is None
    record = filing.record
    assert record is not None
    assert record.source == "attended_snapshot"
    assert record.label == "photograph"
    assert record.photographer == "Volmarr"
    assert record.region == "Viking Village"
    assert record.grid_x == 1000 and record.grid_y == 1001
    assert record.camera == "harbour pier, facing north"
    assert record.residents_visible == ["Yrsa Freydisdottir"]
    assert record.place_kind == "attended"
    assert record.captured_at == "2026-10-31T01:15:00-04:00"
    assert record.notes == "from the pier"
    assert record.byte_count == len(PNG1)
    assert record.sha256 == hashlib.sha256(PNG1).hexdigest()
    # Protocol filename: capture timestamp as reported,
    # region slug, sequence 001.
    assert Path(record.spool_path).name == (
        "20261031-011500_viking-village_001.png"
    )
    assert Path(record.sidecar_path).name == (
        "20261031-011500_viking-village_001.json"
    )
    with open(record.spool_path, "rb") as handle:
        assert handle.read() == PNG1
    # The sidecar on disk is the operator's sidecar, verbatim.
    with open(record.sidecar_path, encoding="utf-8") as handle:
        assert json.load(handle) == _sidecar()
    assert store.records() == [record]
    assert store.latest_record() == record


def test_filed_photo_is_an_attributed_visual_observation(tmp_path):
    seen = []
    store = _store(tmp_path, on_observation=seen.append)
    store.file_photo(PNG1, _sidecar())
    observations = store.observations()
    assert len(observations) == 1 and seen == observations
    obs = observations[0]
    assert obs.event_name == "visual_observation"
    assert obs.source == "attended_snapshot" and obs.label == "photograph"
    assert obs.photographer == "Volmarr"
    assert obs.region == "Viking Village"
    assert obs.byte_count == len(PNG1)
    assert obs.sha256 == hashlib.sha256(PNG1).hexdigest()
    # The text attributes the image in words to the
    # photographer's snapshot — perception, never a claim
    # that this is what Yrsa sees.
    assert obs.text.startswith("Volmarr's snapshot of")
    assert 'region "Viking Village"' in obs.text
    assert "what Yrsa sees" not in obs.text


def test_vision_label_transitions_to_attended_photograph_only(tmp_path):
    store = _store(tmp_path)
    assert store.vision_label() == "none (object-data only)"
    store.file_photo(PNG1, _sidecar())
    assert store.has_photos is True
    assert store.vision_label() == "attended photograph only"
    assert "vision: attended photograph only" in store.describe()
    state = store.current_state()
    assert state.known is True and state.filed == 1
    assert state.vision_label == "attended photograph only"


def test_filing_without_spool_dir_records_in_memory_only(tmp_path):
    store = AttendedPhotoStore(spool_dir=None, _clock=FakeClock())
    filing = store.file_photo(PNG1, _sidecar())
    assert filing.is_filed
    assert filing.record.spool_path is None
    assert filing.record.sidecar_path is None
    assert store.vision_label() == "attended photograph only"
    assert list(tmp_path.iterdir()) == []


def test_bytearray_bytes_are_accepted(tmp_path):
    store = _store(tmp_path)
    filing = store.file_photo(bytearray(PNG1), _sidecar())
    assert filing.is_filed
    assert filing.record.byte_count == len(PNG1)


# -- PNG magic: fail closed -------------------------------------------------

@pytest.mark.parametrize("payload", [JPEG, b"", b"random-bytes-not-an-image",
                                     b"\x89PNG", PNG_MAGIC[:-1] + b"X"])
def test_non_png_bytes_fail_closed(tmp_path, payload):
    store = _store(tmp_path)
    filing = store.file_photo(payload, _sidecar())
    assert filing.outcome == "rejected" and filing.is_rejected
    assert filing.reason == "not_a_png"
    assert filing.record is None
    assert store.records() == [] and store.observations() == []
    assert list(tmp_path.iterdir()) == []  # nothing spooled
    assert store.vision_label() == "none (object-data only)"
    assert [r for r, _d in store.rejected] == ["not_a_png"]


# -- sidecar rules: fail closed ----------------------------------------------

def test_missing_or_non_dict_sidecar_fails_closed(tmp_path):
    store = _store(tmp_path)
    assert store.file_photo(PNG1, None).reason == "missing_sidecar"
    assert store.file_photo(PNG1, "sidecar").reason == "invalid_sidecar"
    assert store.file_photo(PNG1, ["not", "a", "dict"]).reason == (
        "invalid_sidecar"
    )
    assert store.records() == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", ["source", "label", "place_kind",
                                 "captured_at", "photographer",
                                 "residents_visible"])
def test_missing_required_sidecar_field_fails_closed(tmp_path, key):
    store = _store(tmp_path)
    sidecar = _sidecar()
    del sidecar[key]
    filing = store.file_photo(PNG1, sidecar)
    assert filing.is_rejected and filing.reason is not None
    assert store.records() == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("overrides,reason", [
    ({"source": "map_tile"}, "invalid_source"),
    ({"source": "synthetic_render"}, "invalid_source"),
    ({"source": None}, "invalid_source"),
    ({"label": "map_image"}, "invalid_label"),
    ({"label": "synthetic_render"}, "invalid_label"),
    ({"label": None}, "invalid_label"),
    ({"place_kind": "private"}, "invalid_place_kind"),
    ({"place_kind": "ambient"}, "invalid_place_kind"),
    ({"place_kind": None}, "invalid_place_kind"),
    ({"place_kind": ["public"]}, "invalid_place_kind"),
    ({"captured_at": "not-a-date"}, "invalid_captured_at"),
    ({"captured_at": "31/10/2026 01:15"}, "invalid_captured_at"),
    ({"captured_at": ""}, "invalid_captured_at"),
    ({"captured_at": None}, "invalid_captured_at"),
    ({"captured_at": 20261031}, "invalid_captured_at"),
    ({"photographer": ""}, "invalid_photographer"),
    ({"photographer": "   "}, "invalid_photographer"),
    ({"photographer": None}, "invalid_photographer"),
    ({"photographer": 42}, "invalid_photographer"),
    # Never an empty list asserted from a glance.
    ({"residents_visible": []}, "invalid_residents_visible"),
    # A bare string is only ever exactly "unknown".
    ({"residents_visible": "Yrsa Freydisdottir"}, "invalid_residents_visible"),
    ({"residents_visible": "Unknown"}, "invalid_residents_visible"),
    ({"residents_visible": [""]}, "invalid_residents_visible"),
    ({"residents_visible": ["  "]}, "invalid_residents_visible"),
    ({"residents_visible": [123]}, "invalid_residents_visible"),
    ({"residents_visible": None}, "invalid_residents_visible"),
    ({"residents_visible": 42}, "invalid_residents_visible"),
    ({"region": 123}, "invalid_region"),
])
def test_sidecar_rule_violations_fail_closed(tmp_path, overrides, reason):
    store = _store(tmp_path)
    filing = store.file_photo(PNG1, _sidecar(**overrides))
    assert filing.is_rejected and filing.reason == reason
    assert filing.record is None
    assert store.records() == [] and store.observations() == []
    assert list(tmp_path.iterdir()) == []
    assert store.rejected[-1][0] == reason


def test_unknown_residents_string_is_accepted(tmp_path):
    store = _store(tmp_path)
    filing = store.file_photo(PNG1, _sidecar(residents_visible="unknown"))
    assert filing.is_filed
    assert filing.record.residents_visible == "unknown"
    assert store.current_state().unknown_photos == 1


@pytest.mark.parametrize("place_kind", ["public", "attended",
                                        "explicitly_requested"])
def test_all_sanctioned_place_kinds_file(tmp_path, place_kind):
    store = _store(tmp_path)
    filing = store.file_photo(PNG1, _sidecar(place_kind=place_kind))
    assert filing.is_filed
    assert filing.record.place_kind == place_kind


# -- unknowns held as None ------------------------------------------------------

def test_null_region_grid_camera_held_as_none(tmp_path):
    store = _store(tmp_path)
    filing = store.file_photo(
        PNG1,
        _sidecar(region=None, grid_x=None, grid_y=None, camera=None),
    )
    assert filing.is_filed
    record = filing.record
    assert record.region is None
    assert record.grid_x is None and record.grid_y is None
    assert record.camera is None
    # The unknown region is stated in the filename, not guessed.
    assert Path(record.spool_path).name == (
        "20261031-011500_region-unknown_001.png"
    )
    obs = store.observations()[0]
    assert obs.region is None and obs.grid_x is None
    assert "unrecorded region" in obs.text


def test_region_is_never_borrowed_from_a_previous_photo(tmp_path):
    store = _store(tmp_path)
    store.file_photo(PNG1, _sidecar(region="Viking Village"))
    filing = store.file_photo(PNG2, _sidecar(region=None))
    assert filing.is_filed and filing.record.region is None
    assert "region-unknown" in Path(filing.record.spool_path).name


def test_integral_float_grid_coords_are_accepted(tmp_path):
    store = _store(tmp_path)
    filing = store.file_photo(PNG1, _sidecar(grid_x=1000.0, grid_y=1001.0))
    assert filing.is_filed
    assert filing.record.grid_x == 1000 and filing.record.grid_y == 1001


@pytest.mark.parametrize("bad", ["1000", 1.5, True, [1000]])
def test_invalid_grid_coords_fail_closed(tmp_path, bad):
    store = _store(tmp_path)
    filing = store.file_photo(PNG1, _sidecar(grid_x=bad))
    assert filing.is_rejected
    assert store.records() == []


# -- spool naming ------------------------------------------------------------------

def test_same_second_shots_never_collide(tmp_path):
    store = _store(tmp_path)
    first = store.file_photo(PNG1, _sidecar())
    second = store.file_photo(PNG2, _sidecar())
    assert first.is_filed and second.is_filed
    assert Path(first.record.spool_path).name == (
        "20261031-011500_viking-village_001.png"
    )
    assert Path(second.record.spool_path).name == (
        "20261031-011500_viking-village_002.png"
    )
    assert len(list(tmp_path.glob("*.png"))) == 2
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_region_slug_collapses_non_alphanumerics(tmp_path):
    store = _store(tmp_path)
    filing = store.file_photo(PNG1, _sidecar(region="Da  Boom!! Bay"))
    assert filing.is_filed
    assert Path(filing.record.spool_path).name == (
        "20261031-011500_da-boom-bay_001.png"
    )


def test_captured_at_timestamp_is_local_naive_as_reported(tmp_path):
    store = _store(tmp_path)
    # A Z-suffixed moment and a naive moment both stamp their
    # own wall-clock components — never timezone-converted.
    filing = store.file_photo(
        PNG1, _sidecar(captured_at="2026-10-31T01:15:00Z")
    )
    assert Path(filing.record.spool_path).name.startswith("20261031-011500_")
    filing = store.file_photo(
        PNG2, _sidecar(captured_at="2026-11-01T23:59:58")
    )
    assert Path(filing.record.spool_path).name.startswith("20261101-235958_")


def test_sequence_is_never_reused_after_a_purge(tmp_path):
    store = _store(tmp_path)
    store.file_photo(PNG1, _sidecar())
    store.purge_resident("Yrsa Freydisdottir")
    filing = store.file_photo(PNG2, _sidecar())
    assert Path(filing.record.spool_path).name.endswith("_002.png")


# -- deduplication ---------------------------------------------------------------------

def test_duplicate_hash_is_not_filed_twice(tmp_path):
    store = _store(tmp_path)
    first = store.file_photo(PNG1, _sidecar())
    # The same bytes under a different sidecar are still the
    # same photograph: the hash rules, not the metadata.
    second = store.file_photo(PNG1, _sidecar(region="Elsewhere",
                                             place_kind="public"))
    assert second.outcome == "duplicate" and second.is_duplicate
    assert second.record is first.record
    assert store.records() == [first.record]
    assert len(store.observations()) == 1
    assert len(list(tmp_path.glob("*.png"))) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len(store.duplicates) == 1
    assert store.duplicates[0][0] == hashlib.sha256(PNG1).hexdigest()
    state = store.current_state()
    assert state.filed == 1 and state.duplicates_refused == 1


def test_distinct_bytes_are_not_duplicates(tmp_path):
    store = _store(tmp_path)
    assert store.file_photo(PNG1, _sidecar()).is_filed
    assert store.file_photo(PNG2, _sidecar()).is_filed
    assert store.duplicates == []
    assert len(store.records()) == 2


# -- digest integration -------------------------------------------------------------------

class _FakeFetcher:
    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return JPEG


def _builder():
    return SceneDigestBuilder(PresenceTracker(ttl_s=30.0))


def _visual_store_with_tile(tmp_path):
    store = VisualStore(
        spool_dir=tmp_path, fetcher=_FakeFetcher(),
        min_fetch_interval_seconds=0.0, _clock=FakeClock(),
    )
    assert store.fetch_map_tile("Ahern", 1000, 1000) is not None
    return store


def test_digest_without_stores_is_unchanged():
    digest = _builder().build()
    assert "vision: none (object-data only)" in digest


def test_digest_empty_attended_store_falls_through(tmp_path):
    builder = _builder()
    attended = _store(tmp_path)
    # An empty attended store never claims the V4 wording.
    digest = builder.build(attended_store=attended)
    assert "vision: none (object-data only)" in digest
    assert "attended photograph only" not in digest
    # And it falls through to the visual store when one holds
    # an image.
    visual = _visual_store_with_tile(tmp_path)
    digest = builder.build(attended_store=attended, visual_store=visual)
    assert "vision: map image only" in digest


def test_digest_attended_photo_wording(tmp_path):
    builder = _builder()
    attended = _store(tmp_path)
    attended.file_photo(PNG1, _sidecar())
    digest = builder.build(attended_store=attended)
    assert "vision: attended photograph only" in digest


def test_digest_attended_takes_precedence_over_visual_store(tmp_path):
    builder = _builder()
    attended = _store(tmp_path)
    attended.file_photo(PNG1, _sidecar())
    visual = _visual_store_with_tile(tmp_path)
    digest = builder.build(attended_store=attended, visual_store=visual)
    assert "vision: attended photograph only" in digest
    assert "map image only" not in digest


def test_digest_explicit_vision_wins_over_attended(tmp_path):
    builder = _builder()
    attended = _store(tmp_path)
    attended.file_photo(PNG1, _sidecar())
    digest = builder.build(attended_store=attended,
                           vision="synthetic view only")
    assert "vision: synthetic view only" in digest
    assert "attended photograph only" not in digest


# -- purge ---------------------------------------------------------------------------------

def test_purge_by_listed_resident_deletes_files_and_records(tmp_path):
    store = _store(tmp_path)
    first = store.file_photo(
        PNG1, _sidecar(residents_visible=["Alice Resident", "Bob Resident"])
    )
    second = store.file_photo(
        PNG2, _sidecar(residents_visible=["Bob Resident"])
    )
    assert first.is_filed and second.is_filed
    report = store.purge_resident("Alice Resident")
    assert report.removed == 1
    assert report.partial is False and report.unknown_photos == 0
    assert report.resident == "Alice Resident"
    assert not Path(first.record.spool_path).exists()
    assert not Path(first.record.sidecar_path).exists()
    assert Path(second.record.spool_path).exists()
    assert store.records() == [second.record]
    assert [o.sha256 for o in store.observations()] == [second.record.sha256]


def test_purge_with_unknown_residents_reports_partial(tmp_path):
    store = _store(tmp_path)
    unknown = store.file_photo(PNG1, _sidecar(residents_visible="unknown"))
    listed = store.file_photo(
        PNG2, _sidecar(residents_visible=["Alice Resident"])
    )
    assert unknown.is_filed and listed.is_filed
    report = store.purge_resident("Alice Resident")
    assert report.removed == 1
    # The unknown-visibility photo cannot be cleared by a
    # name search: it stays, and the purge says so.
    assert report.partial is True and report.unknown_photos == 1
    assert store.records() == [unknown.record]
    assert Path(unknown.record.spool_path).exists()
    assert store.vision_label() == "attended photograph only"


def test_purge_of_unlisted_resident_removes_zero(tmp_path):
    store = _store(tmp_path)
    store.file_photo(PNG1, _sidecar(residents_visible=["Alice Resident"]))
    report = store.purge_resident("Nobody Here")
    assert report.removed == 0
    assert report.partial is False and report.unknown_photos == 0
    assert len(store.records()) == 1
    assert store.purge_resident("").removed == 0
    assert store.purge_resident(None).removed == 0


def test_purge_of_everything_restores_the_none_label(tmp_path):
    store = _store(tmp_path)
    store.file_photo(PNG1, _sidecar(residents_visible=["Alice Resident"]))
    store.file_photo(PNG2, _sidecar(residents_visible=["Alice Resident"]))
    assert store.purge_resident("Alice Resident").removed == 2
    assert store.records() == [] and store.observations() == []
    assert store.vision_label() == "none (object-data only)"
    assert list(tmp_path.iterdir()) == []


# -- never raises ------------------------------------------------------------------------------

@pytest.mark.parametrize("photo,sidecar", [
    (None, None),
    (None, _sidecar()),
    (12345, _sidecar()),
    ("a string is not bytes", _sidecar()),
    ({"not": "bytes"}, _sidecar()),
    (PNG1, None),
    (PNG1, 42),
    (PNG1, "sidecar"),
    (PNG1, []),
    (PNG1, {"source": "attended_snapshot"}),
])
def test_filing_never_raises_on_malformed_input(tmp_path, photo, sidecar):
    store = _store(tmp_path)
    filing = store.file_photo(photo, sidecar)
    assert filing.is_rejected
    assert store.records() == []


# -- no other surface ------------------------------------------------------------------------------

def test_store_builds_no_commands_and_spends_nothing(tmp_path):
    store = _store(tmp_path)
    for name in ("pay", "give_money", "give_linden", "buy", "send",
                 "message", "teleport", "fetch", "capture", "snapshot"):
        assert not hasattr(store, name), name
    builders = [a for a in dir(store) if a.startswith("build_")]
    assert builders == []
