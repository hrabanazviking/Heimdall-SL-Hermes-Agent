"""Phase 3 slice 1 — presence tiers + scene digest (doc 03 §§2, 7.4, 7.7).

The load-bearing invariants under test:

- the coarse census NEVER yields a name in any digest output;
- the two tiers are never merged (coarse entries have no identity
  fields at all, and full-tier expiry/census stay independent);
- TTL expiry removes avatars;
- range bands are exact at the 10 / 20 / 100 m boundaries;
- budget truncation drops objects before people, never PLACE/SELF;
- HEARD renders as attributed utterances, never as facts.
"""
from heimdall.awareness import (
    CoarseEntry,
    PresenceTracker,
    SceneDigestBuilder,
    band_for_distance,
    build_scene_digest,
)

SELF = (100.0, 100.0, 24.0)
ERIK_UUID = "c3333333-3333-4333-8333-333333333333"
MARA_UUID = "b2222222-2222-4222-8222-222222222222"


def _tracker(now=1000.0):
    tracker = PresenceTracker(ttl_s=30.0)
    tracker.set_self_position(SELF, now=now)
    return tracker


# -- range bands -------------------------------------------------------

def test_range_bands_at_boundaries():
    assert band_for_distance(0.0) == "whisper"
    assert band_for_distance(10.0) == "whisper"
    assert band_for_distance(10.01) == "say"
    assert band_for_distance(20.0) == "say"
    assert band_for_distance(20.01) == "shout"
    assert band_for_distance(100.0) == "shout"
    assert band_for_distance(100.01) == "region"
    assert band_for_distance(1000.0) == "region"


def test_band_of_avatar_uses_distance_from_self():
    tracker = _tracker()
    near = tracker.update_avatar(ERIK_UUID, "Erik Viking", (102.0, 100.0, 24.0), now=1000.0)
    far = tracker.update_avatar(MARA_UUID, "Mara Viking", (100.0, 250.0, 24.0), now=1000.0)
    assert tracker.band_of(near) == "whisper"
    assert tracker.band_of(far) == "region"
    assert tracker.distance_to(near) == 2.0


# -- TTL expiry ----------------------------------------------------------

def test_ttl_expiry_removes_avatars():
    tracker = _tracker()
    tracker.update_avatar(ERIK_UUID, "Erik Viking", (101.0, 100.0, 24.0), now=1000.0)
    assert [a.name for a in tracker.avatars(now=1029.0)] == ["Erik Viking"]
    assert tracker.avatars(now=1031.0) == []
    assert tracker.avatar(ERIK_UUID, now=1031.0) is None


def test_ttl_expiry_removes_coarse_dots_too():
    tracker = _tracker()
    tracker.update_coarse([(0.0, 0.0, 0.0), (10.0, 10.0, 0.0)], now=1000.0)
    assert tracker.coarse_count(now=1020.0) == 2
    assert tracker.coarse_count(now=1031.0) == 0


def test_fresh_update_refreshes_last_seen():
    tracker = _tracker()
    tracker.update_avatar(ERIK_UUID, "Erik Viking", (101.0, 100.0, 24.0), now=1000.0)
    tracker.update_avatar(ERIK_UUID, "Erik Viking", (102.0, 100.0, 24.0), now=1025.0)
    assert len(tracker.avatars(now=1050.0)) == 1


# -- the two tiers never conflate -----------------------------------------

def test_coarse_entry_has_no_identity_fields():
    entry = CoarseEntry((1.0, 2.0, 3.0), 1000.0)
    assert not hasattr(entry, "name")
    assert not hasattr(entry, "uuid")
    assert not hasattr(entry, "display_name")
    assert not hasattr(entry, "__dict__")  # slots: nowhere to smuggle one in


def test_coarse_census_never_yields_a_name_in_digest():
    tracker = _tracker()
    tracker.update_avatar(ERIK_UUID, "Erik Viking", (102.0, 100.0, 24.0), now=1000.0)
    # Three anonymous dots elsewhere in the region — no names exist
    # for them anywhere in the system.
    tracker.update_coarse([(0.0, 0.0, 20.0), (250.0, 250.0, 20.0), (50.0, 200.0, 20.0)], now=1000.0)

    digest = build_scene_digest(
        tracker,
        place={"region": "Viking Village", "parcel": "Longhouse"},
        now=1000.0,
    )
    assert "coarse census: 3 unidentified agents in region" in digest
    # Erik (full tier, say-range) is named once, in PEOPLE (say-range);
    # the census contributes a count and nothing else.
    assert digest.count("Erik Viking") == 1
    region_line = next(l for l in digest.splitlines() if l.startswith("PEOPLE (region)"))
    assert "Erik" not in region_line and "Mara" not in region_line


def test_full_tier_requires_uuid_and_name():
    tracker = _tracker()
    for bad in (("", "Name"), (ERIK_UUID, "")):
        try:
            tracker.update_avatar(bad[0], bad[1], (0.0, 0.0, 0.0), now=1000.0)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("anonymous full-tier entry was accepted")


# -- digest format (doc 03 §7.4) -------------------------------------------

def test_digest_renders_place_self_people_objects_heard():
    tracker = _tracker()
    tracker.update_avatar(ERIK_UUID, "Erik Viking", (102.0, 100.0, 24.0), now=1000.0)
    tracker.update_avatar(MARA_UUID, "Mara Viking", (100.0, 100.0, 74.0), now=1000.0)  # 50 m: shout band → region line
    tracker.update_coarse([(0.0, 0.0, 20.0)], now=1000.0)

    digest = build_scene_digest(
        tracker,
        place={
            "region": "Viking Village",
            "parcel": "Longhouse Green",
            "description": "quiet Heathen talk, all welcome",
            "rules": "no-fly, scripts-on",
            "virtual_time": "23:40 (night, clear)",
        },
        self_info={"state": "standing", "facing": "N"},
        objects=[{"name": "Campfire (click to sit)", "distance_text": "3 m ahead"}],
        heard=[
            {"speaker": "Erik Viking", "text": "the bridge is closed"},
            {"speaker": "Greeter", "text": "Welcome!", "is_object": True},
        ],
        now=1000.0,
    )
    lines = digest.splitlines()
    assert lines[0].startswith('PLACE: Region "Viking Village" · Parcel "Longhouse Green"')
    assert any(l.startswith("SELF: standing") for l in lines)
    assert any("Erik Viking (2.0 m)" in l for l in lines if l.startswith("PEOPLE (say-range)"))
    region_line = next(l for l in lines if l.startswith("PEOPLE (region)"))
    assert "Mara Viking (50.0 m)" in region_line
    assert "coarse census: 1 unidentified agents in region" in region_line
    assert any('"Campfire (click to sit)"' in l for l in lines if l.startswith("OBJECTS OF NOTE"))
    heard_line = next(l for l in lines if l.startswith("HEARD"))
    assert 'Erik Viking: "the bridge is closed"' in heard_line
    assert '[object] Greeter: "Welcome!"' in heard_line
    assert "vision: none (object-data only)" in digest


def test_heard_is_attributed_utterance_never_a_bare_fact():
    tracker = _tracker()
    digest = build_scene_digest(
        tracker,
        place="Somewhere",
        heard=[("Mara Viking", "the bridge is closed")],
        now=1000.0,
    )
    # The claim exists ONLY inside its attribution, in quotes.
    assert digest.count("the bridge is closed") == 1
    assert 'Mara Viking: "the bridge is closed"' in digest


def test_heard_outside_60s_window_is_dropped():
    tracker = _tracker()
    digest = build_scene_digest(
        tracker,
        place="Somewhere",
        heard=[
            {"speaker": "Old", "text": "long ago", "at": 900.0},
            {"speaker": "New", "text": "just now", "at": 990.0},
        ],
        now=1000.0,
    )
    assert "long ago" not in digest
    assert 'New: "just now"' in digest


def test_empty_world_digest_still_renders_place_self_and_ignorance():
    tracker = PresenceTracker(ttl_s=30.0)  # no self position, nobody, nothing
    digest = build_scene_digest(tracker, now=1000.0)
    lines = digest.splitlines()
    assert any(l.startswith("PLACE:") for l in lines)
    assert any(l.startswith("SELF:") for l in lines)
    assert "position unknown" in digest
    assert "vision: none (object-data only)" in digest
    assert "coarse census: 0 unidentified agents in region" in digest
    assert "nothing heard" in digest


# -- budget truncation ------------------------------------------------------

def test_budget_truncation_drops_objects_before_people_never_place_self():
    tracker = _tracker()
    tracker.update_avatar(ERIK_UUID, "Erik Viking", (102.0, 100.0, 24.0), now=1000.0)
    builder = SceneDigestBuilder(tracker)
    kwargs = {
        "place": {"region": "Viking Village", "parcel": "Longhouse Green"},
        "objects": [{"name": "Campfire"}, {"name": "Tip Jar"}, {"name": "Dance Ball"}],
        "heard": [{"speaker": "Erik Viking", "text": "hello there"}],
        "now": 1000.0,
    }
    full = builder.build(**kwargs)
    small_budget = len(full) - 10  # force at least one section to drop
    trimmed = builder.build(budget_chars=small_budget, **kwargs)
    assert len(trimmed) <= small_budget or "OBJECTS OF NOTE" not in trimmed
    assert "OBJECTS OF NOTE" not in trimmed  # lowest priority drops first
    assert "PLACE:" in trimmed and "SELF:" in trimmed
    assert "Erik Viking" in trimmed  # people survive while objects drop


def test_budget_truncation_extreme_keeps_only_place_self_senses():
    tracker = _tracker()
    tracker.update_avatar(ERIK_UUID, "Erik Viking", (102.0, 100.0, 24.0), now=1000.0)
    digest = build_scene_digest(
        tracker,
        place={"region": "Viking Village"},
        objects=[{"name": "Campfire"}],
        heard=[{"speaker": "Erik Viking", "text": "hello"}],
        now=1000.0,
        budget_chars=40,  # smaller than PLACE+SELF alone: never dropped anyway
    )
    assert "PLACE:" in digest and "SELF:" in digest
    assert "OBJECTS OF NOTE" not in digest
    assert "PEOPLE" not in digest
    assert "HEARD" not in digest
