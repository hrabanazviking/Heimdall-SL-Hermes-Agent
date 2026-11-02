"""Wire-model normalisation across both event vocabularies."""
import pytest

from heimdall.core.models import SeamEvent, canonical_event_name

# Every event form the Phase I0 replay fixture / WYRD translator use,
# in both Core (§7.3) and API (§7.1) spelling, must have a canonical
# name in the seam — an event the bridge can translate must never
# dead-letter here.
BRIDGE_FORMS = [
    ("LoginSuccess", "login_success"),
    ("login_success", "login_success"),
    ("Disconnected", "disconnected"),
    ("RegionChanged", "region_changed"),
    ("ParcelChanged", "parcel_changed"),
    ("parcel_changed", "parcel_changed"),
    ("AvatarAppeared", "avatar_appeared"),
    ("avatar_appeared", "avatar_appeared"),
    ("AvatarLeft", "avatar_left"),
    ("ChatReceived", "chat"),
    ("chat", "chat"),
    ("GroupChatReceived", "group_chat"),
    ("IMReceived", "im"),
    ("SelfPositionUpdated", "self_position_updated"),
    ("position_update", "self_position_updated"),
    ("ObjectInfo", "object_info"),
    ("ObjectAppeared", "object_info"),
    ("TeleportCompleted", "teleport_completed"),
    ("FriendOnline", "friend_online"),
    ("AppearanceChanged", "appearance_changed"),
]


@pytest.mark.parametrize("raw,canonical", BRIDGE_FORMS)
def test_bridge_event_forms_all_canonicalise(raw, canonical):
    assert canonical_event_name(raw) == canonical
    event = SeamEvent.from_wire({"event": raw, "msg": "x"})
    assert event is not None and event.name == canonical


def test_unknown_event_has_no_canonical_name():
    assert canonical_event_name("SomethingInvented") is None
    assert SeamEvent.from_wire({"event": "SomethingInvented"}) is None
    assert SeamEvent.from_wire({"msg": "no event key at all"}) is None
