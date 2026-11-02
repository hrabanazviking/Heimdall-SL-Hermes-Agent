"""F5 — places & discovery (census doc 15 slice F5).

The load-bearing invariants under test:

- SLURLs parse in all accepted forms (map URL, secondlife://,
  app teleport link), region names URL-decoded, coordinates
  optional; unrecognisable text fails closed to None;
- teleport commands build without recording anything; arrivals
  enter history only on confirmation (direct or via a
  teleport_completed event);
- home: building set_home changes nothing, confirmation sets
  it, going home with no home known fails closed;
- region records hold exactly what an event carried — absent
  fields stay absent, sparse re-records erase nothing, lookup
  works by name and by id;
- search is rate-aware: kind/query validated, build only checks
  the pacing, confirmation consumes a slot and enforces the
  minimum interval and the rolling-hour cap (this seam's own
  conservative defaults — Linden publishes no numeric limit);
- search results are attributed held records, filterable,
  malformed entries fail closed into ``rejected``;
- ``purge_resident`` removes only that resident's people
  results.
"""
import pytest
from heimdall.awareness import PlaceStore, parse_slurl
from heimdall.core.models import SeamEvent, canonical_event_name

ERIK = "c3333333-3333-4333-8333-333333333333"
VILLAGE = "e5555555-5555-4555-8555-555555555555"


def _store(**kw):
    return PlaceStore(_clock=lambda: 1000.0, **kw)


def _ingest(store, event_name, raw, **payload):
    return store.ingest_event(
        SeamEvent(name=event_name, raw_name=raw, payload=payload)
    )


# -- vocabulary ---------------------------------------------------------

def test_places_event_names_are_canonical():
    assert canonical_event_name("RegionInfoReceived") == "region_info"
    assert canonical_event_name("MapRegionReceived") == "region_info"
    assert canonical_event_name("SearchResultsReceived") == "search_results"
    assert canonical_event_name("HomeLocationSet") == "home_set"
    for api_form in ("region_info", "search_results", "home_set"):
        assert canonical_event_name(api_form) == api_form
    # Arrivals already rode the base vocabulary — not duplicated.
    assert canonical_event_name("TeleportCompleted") == "teleport_completed"


def test_seam_event_from_wire_canonicalises_new_names():
    for raw, canonical in (
        ("RegionInfoReceived", "region_info"),
        ("SearchResultsReceived", "search_results"),
        ("HomeLocationSet", "home_set"),
    ):
        event = SeamEvent.from_wire({"event": raw, "region": "X"})
        assert event is not None and event.name == canonical


# -- SLURL parsing --------------------------------------------------------

def test_parse_map_slurl_with_coords():
    dest = parse_slurl(
        "https://maps.secondlife.com/secondlife/Viking%20Village/128/64/25"
    )
    assert dest is not None
    assert dest.region == "Viking Village"
    assert (dest.x, dest.y, dest.z) == (128.0, 64.0, 25.0)


def test_parse_secondlife_scheme_and_app_form():
    dest = parse_slurl("secondlife://Viking%20Village/10/20")
    assert dest is not None and dest.region == "Viking Village"
    assert (dest.x, dest.y, dest.z) == (10.0, 20.0, None)
    app = parse_slurl("secondlife:///app/teleport/Viking%20Village/1/2/3")
    assert app is not None and app.region == "Viking Village"
    assert (app.x, app.y, app.z) == (1.0, 2.0, 3.0)


def test_parse_region_only_slurl():
    dest = parse_slurl("https://maps.secondlife.com/secondlife/Ahern")
    assert dest is not None and dest.region == "Ahern"
    assert dest.x is None and dest.y is None and dest.z is None


def test_parse_slurl_fails_closed():
    for bad in (
        "",
        "Viking Village",
        "meet me at the village",
        "https://example.com/secondlife/Ahern/1/2/3",
        "https://maps.secondlife.com/secondlife/Ahern/1/2/3/4",
        "https://maps.secondlife.com/secondlife/Ahern/north/2/3",
        "secondlife:///app/dance/Ahern/1/2/3",
    ):
        assert parse_slurl(bad) is None, bad


# -- teleport: build, then confirm ----------------------------------------

def test_build_teleport_from_slurl_records_nothing():
    store = _store()
    cmd = store.build_teleport_command(
        "c1", slurl="https://maps.secondlife.com/secondlife/Ahern/128/64/25"
    )
    assert cmd.cmd == "teleport" and cmd.tier == "mind"
    assert cmd.params["region"] == "Ahern"
    assert cmd.params["x"] == 128.0 and cmd.params["z"] == 25.0
    assert store.teleport_history() == []
    assert store.last_teleport() is None


def test_build_teleport_requires_a_destination():
    store = _store()
    with pytest.raises(ValueError):
        store.build_teleport_command("c1")
    with pytest.raises(ValueError):
        store.build_teleport_command("c2", slurl="not a slurl")


def test_confirm_teleport_records_history():
    store = _store()
    dest = parse_slurl("secondlife://Ahern/1/2/3")
    record = store.confirm_teleport(dest, source="slurl", now=1010.0)
    assert record.destination.region == "Ahern"
    assert record.timestamp == 1010.0 and record.source == "slurl"
    assert store.last_teleport() is record
    assert len(store.teleport_history()) == 1


def test_teleport_completed_event_records_arrival():
    store = _store()
    record = _ingest(
        store, "teleport_completed", "TeleportCompleted",
        region="Viking Village", x=10, y=20, z=30,
    )
    assert record is not None and record.destination.region == "Viking Village"
    assert (record.destination.x, record.destination.z) == (10.0, 30.0)
    assert len(store.teleport_history()) == 1


def test_teleport_completed_without_destination_fails_closed():
    store = _store()
    assert _ingest(store, "teleport_completed", "TeleportCompleted") is None
    assert store.teleport_history() == []
    assert store.rejected


# -- home -------------------------------------------------------------------

def test_home_lifecycle_build_confirm_go():
    store = _store()
    with pytest.raises(ValueError):
        store.build_go_home_command("c0")  # no home known
    cmd = store.build_set_home_command("c1")
    assert cmd.cmd == "set_home" and cmd.tier == "mind"
    assert store.home() is None  # building changed nothing
    dest = parse_slurl("secondlife://Ahern/5/5/5")
    store.confirm_home_set(dest)
    assert store.home() is dest
    go = store.build_go_home_command("c2")
    assert go.cmd == "teleport" and go.params["region"] == "Ahern"
    assert go.params["source"] == "home"


def test_home_set_event_sets_home():
    store = _store()
    dest = _ingest(store, "home_set", "HomeLocationSet", region="Ahern", x=1)
    assert dest is not None and store.home().region == "Ahern"


# -- regions ------------------------------------------------------------------

def test_region_record_invents_nothing():
    store = _store()
    region = store.record_region("Ahern")
    assert region.id is None and region.grid_x is None
    assert region.maturity is None and region.status is None


def test_region_sparse_rerecord_erases_nothing():
    store = _store()
    store.record_region("Ahern", region_id=VILLAGE, grid_x=1000.0, grid_y=1000.0)
    store.record_region("Ahern", status="online")
    region = store.get_region("Ahern")
    assert region.id == VILLAGE and region.grid_x == 1000.0
    assert region.status == "online"
    assert store.get_region(VILLAGE) is region  # lookup by id
    assert store.get_region("Nowhere") is None


def test_region_info_event_single_and_list():
    store = _store()
    one = _ingest(
        store, "region_info", "RegionInfoReceived",
        region="Ahern", region_id=VILLAGE, grid_x=1000, maturity="Moderate",
    )
    assert one is not None and one.name == "Ahern" and one.grid_x == 1000.0
    filed = _ingest(
        store, "region_info", "RegionInfoReceived",
        regions=[{"name": "Dore"}, {"no_name": True}, "junk"],
    )
    assert len(filed) == 1 and filed[0].name == "Dore"
    assert len(store.rejected) == 2
    assert [r.name for r in store.list_regions()] == ["Ahern", "Dore"]


# -- search -------------------------------------------------------------------

def test_search_validation_fails_closed():
    store = _store()
    with pytest.raises(ValueError):
        store.build_search_command("things", "viking", "c1")
    with pytest.raises(ValueError):
        store.build_search_command("people", "  ", "c2")
    for kind in ("people", "places", "events", "groups"):
        cmd = store.build_search_command(kind, "viking", f"c-{kind}")
        assert cmd.cmd == "search" and cmd.tier == "mind"
        assert cmd.params == {"kind": kind, "query": "viking"}


def test_search_build_checks_but_does_not_consume():
    store = _store()
    store.build_search_command("people", "erik", "c1", now=1000.0)
    store.build_search_command("people", "mara", "c2", now=1000.0)
    # Neither build consumed a slot: a confirm at the same moment works.
    filed = store.confirm_search("people", "erik", [], now=1000.0)
    assert filed == []


def test_search_rate_limit_interval_and_hour_cap():
    store = _store(min_search_interval_seconds=5.0, max_searches_per_hour=2)
    store.confirm_search("people", "erik", [], now=1000.0)
    with pytest.raises(PermissionError):
        store.confirm_search("people", "mara", [], now=1002.0)  # too soon
    with pytest.raises(PermissionError):
        store.build_search_command("people", "mara", "c1", now=1002.0)
    store.confirm_search("people", "mara", [], now=1006.0)
    with pytest.raises(PermissionError):
        store.confirm_search("places", "village", [], now=1012.0)  # hour cap
    # An hour later the window has rolled: searching works again.
    store.confirm_search("places", "village", [], now=1000.0 + 3601.0)


def test_search_results_are_attributed_records():
    store = _store()
    filed = store.confirm_search(
        "people", "erik",
        [
            {"id": ERIK, "name": "Erik Viking", "summary": "Blacksmith"},
            {"name": "No Id"},
            "junk",
        ],
        now=1000.0,
    )
    assert len(filed) == 1
    result = filed[0]
    assert result.kind == "people" and result.query == "erik"
    assert result.name == "Erik Viking" and result.summary == "Blacksmith"
    assert len(store.rejected) == 2
    assert store.search_results(kind="people") == filed
    assert store.search_results(kind="places") == []
    assert store.search_results(query="mara") == []


def test_search_results_event_ingest():
    store = _store()
    filed = _ingest(
        store, "search_results", "SearchResultsReceived",
        kind="places", query="village",
        results=[{"id": VILLAGE, "name": "Viking Village"}],
    )
    assert len(filed) == 1 and filed[0].kind == "places"
    # Ingest of a bad-kind event files nothing and audits.
    assert _ingest(
        store, "search_results", "SearchResultsReceived",
        kind="things", query="x", results=[],
    ) is None
    assert store.rejected


def test_unknown_event_fails_closed():
    store = _store()
    assert _ingest(store, "chat", "ChatReceived", text="hi") is None
    assert store.rejected == [("not_a_places_event", "chat")]


def test_purge_resident_removes_only_people_results():
    store = _store()
    store.confirm_search(
        "people", "erik", [{"id": ERIK, "name": "Erik Viking"}], now=1000.0
    )
    store.confirm_search(
        "places", "village", [{"id": VILLAGE, "name": "Viking Village"}],
        now=1010.0,
    )
    assert store.purge_resident(ERIK) == 1
    assert store.search_results(kind="people") == []
    assert len(store.search_results(kind="places")) == 1
    assert store.purge_resident("") == 0
