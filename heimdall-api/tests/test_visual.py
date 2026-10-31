"""V1 — map sense (visual capture doc 16, Route A).

The load-bearing invariants under test:

- a tile URL is built only from region + grid coordinates
  actually supplied — missing/invalid region or coordinates
  refuse loudly (recorded, fetcher never called), never
  guessed;
- a successful fetch spools the exact bytes under a
  deterministic filename and files provenance
  (source="map_tile", region, grid coords, fetch time, spool
  path, byte count) plus a ``visual_observation`` labelled a
  map image — never "what Yrsa sees", never a fact;
- non-JPEG / empty / fetcher-error payloads fail closed:
  nothing spooled, rejection recorded, fetch never raises;
- fetches are rate-limited: a second immediate fetch is
  refused loudly and allowed again after the window (fake
  clock), and the rolling-hour cap holds;
- the vision state is honest: UNKNOWN /
  ``vision: none (object-data only)`` before any tile, and
  exactly ``vision: map image only`` once one lands —
  including through the scene digest's visual-store input;
- region-change events record the region and trigger a fetch
  only when they carry coordinates and a fetcher exists;
- place purge removes a region's spool files + records;
  map tiles carry no resident identity, so the resident
  purge honestly removes nothing;
- no network anywhere: every fetch rides a fake fetcher.
"""
from pathlib import Path

import pytest
from heimdall.awareness import (
    PresenceTracker,
    SceneDigestBuilder,
    VisualStore,
    build_map_tile_url,
)
from heimdall.core.models import CORE_EVENT_ALIASES, SeamEvent, canonical_event_name

JPEG = b"\xff\xd8\xff\xe0" + b"a-fake-but-plausible-tile" + b"\xff\xd9"
NOT_JPEG = b"<html><body>404 Not Found</body></html>"


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class FakeFetcher:
    """Records every URL it was handed; returns a fixed payload
    or raises a fixed error. No network exists behind it."""

    def __init__(self, payload=JPEG, error=None):
        self.payload = payload
        self.error = error
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.payload


def _store(tmp_path, fetcher=None, clock=None, **kwargs):
    kwargs.setdefault("min_fetch_interval_seconds", 0.0)
    return VisualStore(
        spool_dir=tmp_path,
        fetcher=FakeFetcher() if fetcher is None else fetcher,
        _clock=FakeClock() if clock is None else clock,
        **kwargs,
    )


def _ingest(store, event_name, raw, **payload):
    return store.ingest_event(
        SeamEvent(name=event_name, raw_name=raw, payload=payload)
    )


# -- vocabulary ---------------------------------------------------------

def test_visual_event_name_is_canonical():
    assert canonical_event_name("VisualObservation") == "visual_observation"
    assert canonical_event_name("visual_observation") == "visual_observation"


def test_seam_event_from_wire_canonicalises_visual_observation():
    event = SeamEvent.from_wire(
        {"event": "VisualObservation", "region": "Ahern"}
    )
    assert event is not None and event.name == "visual_observation"


def test_vocabulary_not_duplicated():
    forms = [k for k, v in CORE_EVENT_ALIASES.items()
             if v == "visual_observation"]
    assert len(forms) == 2  # Core name + API identity form only
    # The region-change trigger rides the base vocabulary and
    # was NOT duplicated by this slice.
    region_forms = [k for k, v in CORE_EVENT_ALIASES.items()
                    if v == "region_changed"]
    assert len(region_forms) == 2
    # Nor was the existing object vocabulary disturbed.
    assert canonical_event_name("ObjectInfo") == "object_info"


# -- URL construction -----------------------------------------------------

def test_map_tile_url_from_supplied_coords():
    assert build_map_tile_url(1000, 1000) == (
        "http://map.secondlife.com/map-1-1000-1000-objects.jpg"
    )
    assert build_map_tile_url(997, 1003) == (
        "http://map.secondlife.com/map-1-997-1003-objects.jpg"
    )
    # Integral floats ride region events elsewhere in the seam.
    assert build_map_tile_url(1000.0, 1000.0) == (
        "http://map.secondlife.com/map-1-1000-1000-objects.jpg"
    )


@pytest.mark.parametrize("bad", [-1, -100, "1000", "east", True, 1.5, None])
def test_map_tile_url_refuses_invalid_coords(bad):
    with pytest.raises((ValueError, TypeError)):
        build_map_tile_url(bad, 1000)
    with pytest.raises((ValueError, TypeError)):
        build_map_tile_url(1000, bad)


# -- honest UNKNOWN state ---------------------------------------------------

def test_vision_is_unknown_before_any_fetch(tmp_path):
    store = _store(tmp_path)
    assert store.vision_known is False
    assert store.vision_label() == "none (object-data only)"
    state = store.current_state()
    assert state.known is False and state.tiles == 0
    assert state.region is None and state.last_fetched_at is None
    text = store.describe()
    assert "vision: none (object-data only)" in text
    assert "map image only" not in text
    assert store.records() == [] and store.observations() == []


# -- successful fetch ---------------------------------------------------------

def test_successful_fetch_spools_exact_bytes_with_provenance(tmp_path):
    clock = FakeClock(1000.0)
    fetcher = FakeFetcher()
    seen = []
    store = _store(tmp_path, fetcher, clock, on_observation=seen.append)
    record = store.fetch_map_tile("Viking Village", 1000, 1001)
    assert record is not None
    assert fetcher.urls == [
        "http://map.secondlife.com/map-1-1000-1001-objects.jpg"
    ]
    assert record.source == "map_tile"
    assert record.region == "Viking Village"
    assert record.grid_x == 1000 and record.grid_y == 1001
    assert record.fetched_at == 1000.0
    assert record.byte_count == len(JPEG)
    assert record.url == fetcher.urls[0]
    # Deterministic filename: coords + region slug + fetch time.
    assert record.spool_path.endswith(
        "map-1-1000-1001-viking-village-1000.jpg"
    )
    with open(record.spool_path, "rb") as handle:
        assert handle.read() == JPEG
    assert store.records() == [record]
    assert store.latest_record() == record
    assert store.current_region == "Viking Village"


def test_successful_fetch_emits_map_image_observation(tmp_path):
    seen = []
    store = _store(tmp_path, on_observation=seen.append)
    store.fetch_map_tile("Ahern", 1000, 1000)
    observations = store.observations()
    assert len(observations) == 1 and seen == observations
    obs = observations[0]
    assert obs.event_name == "visual_observation"
    assert obs.source == "map_tile" and obs.label == "map_image"
    assert obs.region == "Ahern"
    assert obs.grid_x == 1000 and obs.grid_y == 1000
    assert obs.byte_count == len(JPEG)
    # An attributed observation about a place — the record
    # carries no claim that this is what she sees.
    assert "see" not in obs.label and "photo" not in obs.label


def test_vision_label_transitions_to_map_image_only(tmp_path):
    store = _store(tmp_path)
    assert store.vision_label() == "none (object-data only)"
    store.fetch_map_tile("Ahern", 1000, 1000)
    assert store.vision_known is True
    assert store.vision_label() == "map image only"
    assert "vision: map image only" in store.describe()
    state = store.current_state()
    assert state.known is True and state.tiles == 1
    assert state.vision_label == "map image only"
    assert state.last_fetched_at == 1000.0


# -- refusals: nothing supplied, nothing fetched -------------------------------

def test_fetch_refused_when_coords_missing_and_fetcher_not_called(tmp_path):
    fetcher = FakeFetcher()
    store = _store(tmp_path, fetcher)
    assert store.fetch_map_tile("Ahern") is None
    assert store.fetch_map_tile("Ahern", 1000) is None
    assert store.fetch_map_tile("Ahern", None, 1000) is None
    assert fetcher.urls == []
    assert [r.reason for r in store.refusals] == [
        "missing_grid_coordinates"
    ] * 3
    assert store.records() == []
    assert list(tmp_path.iterdir()) == []


def test_fetch_refused_when_region_missing_or_invalid_coords(tmp_path):
    fetcher = FakeFetcher()
    store = _store(tmp_path, fetcher)
    assert store.fetch_map_tile(None, 1000, 1000) is None
    assert store.fetch_map_tile("  ", 1000, 1000) is None
    assert store.fetch_map_tile("Ahern", -1, 1000) is None
    assert store.fetch_map_tile("Ahern", "1000", 1000) is None
    assert fetcher.urls == []
    assert store.refusals[0].reason == "missing_region"
    assert store.refusals[1].reason == "missing_region"
    assert len(store.refusals) == 4
    assert store.vision_known is False


def test_fetch_refused_without_fetcher_or_spool_dir(tmp_path):
    no_fetcher = VisualStore(spool_dir=tmp_path, fetcher=None,
                             _clock=FakeClock())
    assert no_fetcher.fetch_map_tile("Ahern", 1000, 1000) is None
    assert no_fetcher.refusals[-1].reason == "no_fetcher_configured"
    no_spool = VisualStore(spool_dir=None, fetcher=FakeFetcher(),
                           _clock=FakeClock())
    assert no_spool.fetch_map_tile("Ahern", 1000, 1000) is None
    assert no_spool.refusals[-1].reason == "no_spool_directory_configured"


# -- fail-closed payloads --------------------------------------------------------

@pytest.mark.parametrize("payload", [NOT_JPEG, b"", None, 12345])
def test_non_jpeg_and_empty_payloads_fail_closed(tmp_path, payload):
    fetcher = FakeFetcher(payload=payload)
    store = _store(tmp_path, fetcher)
    assert store.fetch_map_tile("Ahern", 1000, 1000) is None
    assert store.records() == [] and store.observations() == []
    assert list(tmp_path.iterdir()) == []  # nothing spooled
    assert store.vision_known is False
    assert len(store.refusals) == 1 and len(store.rejected) == 1


def test_fetcher_error_fails_closed_and_never_raises(tmp_path):
    fetcher = FakeFetcher(error=OSError("network is down"))
    store = _store(tmp_path, fetcher)
    assert store.fetch_map_tile("Ahern", 1000, 1000) is None
    assert store.refusals[-1].reason.startswith("fetch_error")
    assert store.records() == [] and list(tmp_path.iterdir()) == []


# -- rate limiting -----------------------------------------------------------------

def test_rate_limit_blocks_second_immediate_fetch_loudly(tmp_path):
    clock = FakeClock(1000.0)
    fetcher = FakeFetcher()
    store = _store(tmp_path, fetcher, clock,
                   min_fetch_interval_seconds=60.0)
    assert store.fetch_map_tile("Ahern", 1000, 1000) is not None
    assert store.fetch_map_tile("Da Boom", 1001, 1000) is None
    assert len(fetcher.urls) == 1  # refused, NOT fetched anyway
    assert store.refusals[-1].reason.startswith("rate_limited")
    assert len(store.records()) == 1
    clock.advance(61.0)
    assert store.fetch_map_tile("Da Boom", 1001, 1000) is not None
    assert len(fetcher.urls) == 2


def test_hourly_cap_blocks_and_window_reopens(tmp_path):
    clock = FakeClock(1000.0)
    fetcher = FakeFetcher()
    store = _store(tmp_path, fetcher, clock, max_fetches_per_hour=2)
    assert store.fetch_map_tile("Ahern", 1000, 1000) is not None
    assert store.fetch_map_tile("Da Boom", 1001, 1000) is not None
    assert store.fetch_map_tile("Sandbox", 1002, 1000) is None
    assert store.refusals[-1].reason.startswith("rate_limited")
    assert len(fetcher.urls) == 2
    clock.advance(3601.0)
    assert store.fetch_map_tile("Sandbox", 1002, 1000) is not None


def test_check_fetch_allowed_raises_and_consumes_nothing(tmp_path):
    clock = FakeClock(1000.0)
    store = _store(tmp_path, clock=clock,
                   min_fetch_interval_seconds=60.0)
    store.fetch_map_tile("Ahern", 1000, 1000)
    with pytest.raises(PermissionError):
        store.check_fetch_allowed()
    # Checking is free: a later real fetch after the window is
    # judged on the one actual fetch, not on the checks.
    clock.advance(61.0)
    store.check_fetch_allowed()
    assert store.fetch_map_tile("Da Boom", 1001, 1000) is not None


def test_failed_fetch_still_consumes_its_pacing_slot(tmp_path):
    # The GET was the load, even when its payload was junk.
    clock = FakeClock(1000.0)
    fetcher = FakeFetcher(payload=NOT_JPEG)
    store = _store(tmp_path, fetcher, clock,
                   min_fetch_interval_seconds=60.0)
    assert store.fetch_map_tile("Ahern", 1000, 1000) is None
    fetcher.payload = JPEG
    assert store.fetch_map_tile("Ahern", 1000, 1000) is None
    assert store.refusals[-1].reason.startswith("rate_limited")
    assert len(fetcher.urls) == 1


# -- region-change trigger ------------------------------------------------------------

def test_region_changed_ingest_records_region_and_fetches(tmp_path):
    fetcher = FakeFetcher()
    store = _store(tmp_path, fetcher)
    record = _ingest(store, "region_changed", "RegionChanged",
                     region="Ahern", grid_x=1000, grid_y=1000)
    assert record is not None and record.region == "Ahern"
    assert fetcher.urls == [
        "http://map.secondlife.com/map-1-1000-1000-objects.jpg"
    ]
    assert store.current_region == "Ahern"
    assert store.vision_label() == "map image only"


def test_region_changed_without_coords_records_but_never_guesses(tmp_path):
    fetcher = FakeFetcher()
    store = _store(tmp_path, fetcher)
    assert _ingest(store, "region_changed", "RegionChanged",
                   region="Ahern") is None
    assert store.current_region == "Ahern"  # the region is recorded
    assert fetcher.urls == []  # but no coordinates were invented
    assert store.vision_known is False
    # A later region's coordinates are never borrowed either.
    _ingest(store, "region_changed", "RegionChanged",
            region="Da Boom", grid_x=1001, grid_y=1000)
    assert _ingest(store, "region_changed", "RegionChanged",
                   region="Ahern") is None
    assert len(fetcher.urls) == 1


def test_region_changed_without_fetcher_records_region_only(tmp_path):
    store = VisualStore(spool_dir=tmp_path, fetcher=None,
                        _clock=FakeClock())
    assert _ingest(store, "region_changed", "RegionChanged",
                   region="Ahern", grid_x=1000, grid_y=1000) is None
    assert store.current_region == "Ahern"
    assert store.records() == [] and store.refusals == []


def test_region_changed_ingest_without_region_fails_closed(tmp_path):
    store = _store(tmp_path)
    assert _ingest(store, "region_changed", "RegionChanged",
                   grid_x=1000, grid_y=1000) is None
    assert len(store.rejected) == 1
    assert store.rejected[0][1] == "region_changed"
    assert store.current_region is None


def test_handle_region_changed_direct_api(tmp_path):
    store = _store(tmp_path)
    record = store.handle_region_changed("Ahern", 1000, 1000)
    assert record is not None
    with pytest.raises(ValueError):
        store.handle_region_changed("")
    with pytest.raises((ValueError, TypeError)):
        store.handle_region_changed("Ahern", -1, 1000)


# -- visual_observation ingest -----------------------------------------------------------

def test_visual_observation_ingest_files_reported_tile(tmp_path):
    store = _store(tmp_path)
    obs = _ingest(store, "visual_observation", "VisualObservation",
                  source="map_tile", region="Ahern", grid_x=1000,
                  grid_y=1000, spool_path="/spool/map-1-1000-1000.jpg",
                  byte_count=4242, timestamp=900.0)
    assert obs is not None and obs.label == "map_image"
    assert obs.region == "Ahern" and obs.byte_count == 4242
    assert obs.fetched_at == 900.0
    assert store.vision_label() == "map image only"
    assert store.records()[0].spool_path == "/spool/map-1-1000-1000.jpg"


@pytest.mark.parametrize("payload", [
    {"region": "Ahern", "spool_path": "/s/x.jpg"},                     # no coords
    {"grid_x": 1000, "grid_y": 1000, "spool_path": "/s/x.jpg"},        # no region
    {"region": "Ahern", "grid_x": 1000, "grid_y": 1000},               # no spool
    {"region": "Ahern", "grid_x": 1000, "grid_y": 1000,
     "spool_path": "/s/x.jpg", "source": "texture_image"},             # not V1
    {"region": "Ahern", "grid_x": -1, "grid_y": 1000,
     "spool_path": "/s/x.jpg"},                                        # bad coord
])
def test_visual_observation_ingest_fails_closed(tmp_path, payload):
    store = _store(tmp_path)
    assert _ingest(store, "visual_observation", "VisualObservation",
                   **payload) is None
    assert store.records() == [] and store.observations() == []
    assert len(store.rejected) == 1


def test_unknown_event_fails_closed_and_never_raises(tmp_path):
    store = _store(tmp_path)
    assert _ingest(store, "chat", "ChatReceived", msg="hi") is None
    assert store.rejected[-1] == ("not_a_visual_event", "chat")


# -- digest integration ----------------------------------------------------------------------

def test_digest_uses_visual_store_label_honestly(tmp_path):
    tracker = PresenceTracker(ttl_s=30.0)
    builder = SceneDigestBuilder(tracker)
    store = _store(tmp_path)
    before = builder.build(visual_store=store)
    assert "vision: none (object-data only)" in before
    store.fetch_map_tile("Ahern", 1000, 1000)
    after = builder.build(visual_store=store)
    assert "vision: map image only" in after
    # An explicit vision string still wins over the store, and
    # a digest built with neither is byte-for-byte the old one.
    explicit = builder.build(visual_store=store, vision="map image only")
    assert "vision: map image only" in explicit
    plain = builder.build()
    assert "vision: none (object-data only)" in plain


# -- purge ---------------------------------------------------------------------------------------

def test_purge_region_removes_spool_files_and_records(tmp_path):
    store = _store(tmp_path)
    first = store.fetch_map_tile("Ahern", 1000, 1000)
    second = store.fetch_map_tile("Da Boom", 1001, 1000)
    assert first is not None and second is not None
    removed = store.purge_region("Ahern")
    assert removed == 1
    assert not Path(first.spool_path).exists()
    assert Path(second.spool_path).exists()
    assert [r.region for r in store.records()] == ["Da Boom"]
    assert [o.region for o in store.observations()] == ["Da Boom"]
    # Another region's map remains, so the label honestly stays.
    assert store.vision_label() == "map image only"
    assert store.purge_region("Ahern") == 0
    assert store.purge_region("Nowhere") == 0
    assert store.purge_region("Da Boom") == 1
    assert store.vision_label() == "none (object-data only)"
    assert store.current_region is None


def test_purge_resident_removes_nothing_because_tiles_name_no_resident(tmp_path):
    store = _store(tmp_path)
    store.fetch_map_tile("Ahern", 1000, 1000)
    assert store.purge_resident("c3333333-3333-4333-8333-333333333333") == 0
    assert store.purge_resident("") == 0
    assert len(store.records()) == 1  # untouched: place data, not resident data


# -- no other surface ------------------------------------------------------------------------------

def test_store_builds_no_commands_and_spends_nothing(tmp_path):
    store = _store(tmp_path)
    for name in ("pay", "give_money", "give_linden", "buy", "send",
                 "message", "teleport", "fetch_texture"):
        assert not hasattr(store, name), name
    builders = [a for a in dir(store) if a.startswith("build_")]
    assert builders == []
