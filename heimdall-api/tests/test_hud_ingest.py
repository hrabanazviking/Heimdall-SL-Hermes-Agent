"""LSL companion HUD ingest — Phase 3 slice 3.

The HUD1 payloads here are written against ``heimdall-lsl/README-hud.md``
(the wire-format spec both ends share). No socket, no grid: ingest is
transport-agnostic by design, so these tests drive ``HudIngest``
directly with payload strings, exactly as any future HTTP mount would.
"""
from pathlib import Path

import pytest
from heimdall.awareness.hud_ingest import (
    FORMAT_TAG,
    HudIngest,
    HudPayloadError,
    parse_hud_payload,
)
from heimdall.awareness.presence import PresenceTracker

REPO_ROOT = Path(__file__).resolve().parents[2]

PARCEL_ID = "123e4567-e89b-12d3-a456-426614174000"


def make_payload(**overrides):
    fields = [
        ("v", "1"),
        ("ts", "1730000000"),
        ("region", "Viking%20Village"),
        ("fps", "45.0"),
        ("dil", "0.99"),
        ("agents", "3"),
        ("apos", "<10.0, 20.0, 30.0>;<40.0, 50.0, 60.0>"),
        ("self_pos", "<128.0, 128.0, 25.0>"),
        ("self_flags", "0"),
        ("env_day_len", "14400"),
        ("env_day_off", "0"),
        ("env_sec", "3600.5"),
        ("parcel", "Longhouse"),
        ("parcel_area", "4096"),
        ("parcel_flags", "0"),
        ("parcel_id", PARCEL_ID),
        ("sensor_count", "1"),
        ("sensor_pos", "<130.0, 128.0, 25.0>"),
        ("free_mem", "32000"),
    ]
    merged = dict(fields)
    merged.update(overrides)
    body = "|".join(f"{k}={v}" for k, v in merged.items() if v is not None)
    return f"{FORMAT_TAG}|{body}"


# -- parsing ------------------------------------------------------------


def test_parse_valid_payload_decodes_every_field():
    digest = parse_hud_payload(make_payload())
    assert digest.source == "hud"
    assert digest.timestamp == 1730000000
    assert digest.region == "Viking Village"  # URL-escapes decoded
    assert digest.region_fps == 45.0
    assert digest.time_dilation == 0.99
    assert digest.census_reported_count == 3
    assert digest.census_positions == ((10.0, 20.0, 30.0), (40.0, 50.0, 60.0))
    assert digest.self_position == (128.0, 128.0, 25.0)
    assert digest.parcel_name == "Longhouse"
    assert digest.parcel_area == 4096
    assert digest.parcel_id == PARCEL_ID
    assert digest.sensor_count == 1
    assert digest.sensor_positions == ((130.0, 128.0, 25.0),)
    assert digest.free_memory == 32000


def test_parse_accepts_bytes_and_empty_position_lists():
    digest = parse_hud_payload(
        make_payload(agents="0", apos="", sensor_count="0", sensor_pos="").encode()
    )
    assert digest.census_positions == ()
    assert digest.sensor_positions == ()


@pytest.mark.parametrize(
    "payload",
    [
        "HUD9|" + make_payload().split("|", 1)[1],  # wrong format tag
        make_payload(v="2"),  # unsupported version
        make_payload(ts="0"),  # non-positive timestamp
        make_payload(ts="soon"),  # non-integer timestamp
        make_payload(dil="1.5"),  # dilation is 0.0-1.0 by definition
        make_payload(fps="nan"),  # non-finite float
        make_payload(agents="101"),  # beyond llGetAgentList ceiling
        make_payload(agents="1"),  # fewer agents than census positions
        make_payload(sensor_count="2"),  # sensor count/positions mismatch
        make_payload(parcel_id="not-a-uuid"),
        make_payload(region="Bad%01Name"),  # control character after decode
        make_payload(extra="1"),  # unknown field
        make_payload() + "\n" + make_payload(),  # payload is exactly one line
        "",
    ],
)
def test_parse_rejects_malformed_payloads_whole(payload):
    with pytest.raises(HudPayloadError):
        parse_hud_payload(payload)


def test_parse_rejects_missing_and_duplicate_fields():
    missing = make_payload().replace("|free_mem=32000", "")
    with pytest.raises(HudPayloadError):
        parse_hud_payload(missing)
    with pytest.raises(HudPayloadError):
        parse_hud_payload(make_payload() + "|fps=44.0")


# -- ingest behaviour -----------------------------------------------------


def test_ingest_feeds_coarse_tier_only_and_returns_attributed_records():
    tracker = PresenceTracker()
    ingest = HudIngest(tracker=tracker)
    result = ingest.ingest(make_payload(), now=500.0)
    assert result is not None
    assert ingest.accepted_count == 1
    assert ingest.rejected_count == 0
    # Coarse tier received exactly the anonymous census positions...
    assert tracker.coarse_count(now=500.0) == 2
    assert tracker.coarse_positions(now=500.0) == [
        (10.0, 20.0, 30.0),
        (40.0, 50.0, 60.0),
    ]
    # ...and the full-fidelity tier was never touched: HUD data has no
    # names or keys, so it can never fabricate a person (doc 03 §7.7).
    assert tracker.avatars(now=500.0) == []
    # Records are attributed sensor data, not canonical facts.
    assert result.conditions.source == "hud"
    assert result.place.source == "hud"
    assert result.place.parcel_name == "Longhouse"
    assert [e.name for e in result.events] == [
        "hud_census",
        "hud_conditions",
        "hud_place",
    ]
    assert all(e.payload["source"] == "hud" for e in result.events)


def test_ingest_rejection_applies_nothing_and_is_counted():
    tracker = PresenceTracker()
    ingest = HudIngest(tracker=tracker)
    assert ingest.ingest(make_payload(dil="9.9"), now=500.0) is None
    assert ingest.rejected_count == 1
    assert ingest.accepted_count == 0
    assert tracker.coarse_count(now=500.0) == 0
    assert ingest.last_digest is None
    assert ingest.last_conditions is None
    assert ingest.last_place is None


def test_ingest_without_tracker_still_parses_and_reports():
    ingest = HudIngest()
    result = ingest.ingest(make_payload(), now=1.0)
    assert result is not None
    assert result.digest.region == "Viking Village"


def test_ingest_emits_events_to_callback():
    seen = []
    ingest = HudIngest(on_events=seen.extend)
    ingest.ingest(make_payload(), now=1.0)
    assert [e.name for e in seen] == ["hud_census", "hud_conditions", "hud_place"]


# -- auth: checked when configured, never stored ---------------------------


def test_ingest_with_expected_secret_requires_matching_auth():
    ingest = HudIngest(expected_secret="s3cret")
    assert ingest.ingest(make_payload(), now=1.0) is None  # no auth field
    assert ingest.ingest(make_payload() + "|auth=wrong", now=1.0) is None
    assert ingest.rejected_count == 2
    result = ingest.ingest(make_payload() + "|auth=s3cret", now=1.0)
    assert result is not None
    # The secret is proof, not data: it appears on no record or event.
    assert "s3cret" not in repr(result)


def test_ingest_without_expected_secret_drops_auth_field():
    ingest = HudIngest()
    result = ingest.ingest(make_payload() + "|auth=whatever", now=1.0)
    assert result is not None
    assert "whatever" not in repr(result)


# -- purge ------------------------------------------------------------------


def test_purge_clears_coarse_census_and_stored_records():
    tracker = PresenceTracker()
    ingest = HudIngest(tracker=tracker)
    ingest.ingest(make_payload(), now=500.0)
    assert tracker.coarse_count(now=500.0) == 2
    ingest.purge()
    assert tracker.coarse_count(now=500.0) == 0
    assert ingest.last_digest is None
    assert ingest.last_conditions is None
    assert ingest.last_place is None


# -- one format, both ends ---------------------------------------------------


def test_format_tag_matches_lsl_emitter_and_spec():
    lsl = (REPO_ROOT / "heimdall-lsl" / "heimdall_companion_hud.lsl").read_text(
        encoding="utf-8"
    )
    spec = (REPO_ROOT / "heimdall-lsl" / "README-hud.md").read_text(encoding="utf-8")
    assert FORMAT_TAG == "HUD1"
    assert f'"{FORMAT_TAG}"' in lsl  # the emitter's FORMAT_TAG string
    assert FORMAT_TAG in spec
    # Disclosure is build law (doc 05): the HUD carries it as hover text.
    assert "Scripted Agent" in lsl
    assert "llSetText" in lsl
