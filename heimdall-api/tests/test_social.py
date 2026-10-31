"""F4 — friends & groups surface (census doc 15 slice F4).

The load-bearing invariants under test:

- friends are listed with their permissions preserved, and
  online status is unknown until a presence event arrives —
  never invented by a list refresh;
- friendship accept/decline and group join/leave/invite build
  governor-ready commands (F1 offers shape, tier="mind") without
  mutating local state; the local record lands only on the
  confirm step after Core confirmation;
- group memberships keep exactly one active group at a time;
- group notices are an attributed, per-group re-readable inbox;
- mute vs block: blocked is refused, muted is flagged, normal is
  allowed — through the single ``admission_for`` helper, wired
  into the notice ingest path;
- unknown/malformed entries fail closed into ``rejected``;
- ``purge_resident`` removes the resident from every record.
"""
import pytest

from heimdall.api.event_dispatcher import EventDispatcher
from heimdall.awareness import OffersQueue, SocialStore
from heimdall.core.models import SeamEvent, canonical_event_name

ERIK = "c3333333-3333-4333-8333-333333333333"
MARA = "d4444444-4444-4444-8444-444444444444"
TROLL = "f6666666-6666-4666-8666-666666666666"
VILLAGE = "e5555555-5555-4555-8555-555555555555"
MARKET = "a7777777-7777-4777-8777-777777777777"


def _store():
    return SocialStore(_clock=lambda: 1000.0)


def _ingest(store, event_name, raw, **payload):
    return store.ingest_event(
        SeamEvent(name=event_name, raw_name=raw, payload=payload)
    )


def _friend_list(store, friends):
    return _ingest(
        store, "friend_list", "FriendListReceived", friends=friends
    )


# -- vocabulary ---------------------------------------------------------

def test_social_event_names_are_canonical():
    assert canonical_event_name("FriendListReceived") == "friend_list"
    assert canonical_event_name("GroupListReceived") == "group_list"
    assert canonical_event_name("GroupJoined") == "group_joined"
    assert canonical_event_name("GroupLeft") == "group_left"
    assert canonical_event_name("GroupNoticeReceived") == "group_notice"
    assert canonical_event_name("MuteListReceived") == "mute_list"
    for api_form in (
        "friend_list", "group_list", "group_joined",
        "group_left", "group_notice", "mute_list",
    ):
        assert canonical_event_name(api_form) == api_form
    # Presence and friendship offers were already in the vocabulary
    # (F1 / base seam) — F4 must not duplicate them.
    assert canonical_event_name("FriendOnline") == "friend_online"
    assert canonical_event_name("FriendOffline") == "friend_offline"
    assert canonical_event_name("FriendshipOfferReceived") == "friendship_offer"


def test_seam_event_from_wire_canonicalises_new_names():
    for raw, canonical in (
        ("FriendListReceived", "friend_list"),
        ("GroupListReceived", "group_list"),
        ("GroupJoined", "group_joined"),
        ("GroupLeft", "group_left"),
        ("GroupNoticeReceived", "group_notice"),
        ("MuteListReceived", "mute_list"),
    ):
        event = SeamEvent.from_wire({"event": raw, "friends": []})
        assert event is not None and event.name == canonical


# -- friends: list, permissions, presence --------------------------------

def test_friend_list_preserves_permissions():
    store = _store()
    filed = _friend_list(store, [
        {"id": ERIK, "name": "Erik Viking",
         "can_see_my_online_status": True, "can_see_me_on_map": True,
         "can_modify_my_objects": False},
        {"id": MARA, "name": "Mara", "rights": {"can_see_online": True}},
    ])
    assert len(filed) == 2
    erik = store.get_friend(ERIK)
    assert erik is not None and erik.name == "Erik Viking"
    assert erik.can_see_my_online_status is True
    assert erik.can_see_me_on_map is True
    assert erik.can_modify_my_objects is False
    mara = store.get_friend(MARA)
    assert mara is not None and mara.can_see_my_online_status is True
    assert mara.can_see_me_on_map is False  # unspecified stays denied
    assert [f.id for f in store.list_friends()] == [ERIK, MARA]


def test_online_status_only_from_events_never_invented():
    store = _store()
    _friend_list(store, [{"id": ERIK, "name": "Erik"}])
    assert store.get_friend(ERIK).online is None  # unknown
    _ingest(store, "friend_online", "FriendOnline", friend_id=ERIK)
    assert store.get_friend(ERIK).online is True
    _ingest(store, "friend_offline", "FriendOffline", friend_id=ERIK)
    assert store.get_friend(ERIK).online is False
    # A list refresh updates the record but not the presence.
    _friend_list(store, [{"id": ERIK, "name": "Erik Viking"}])
    assert store.get_friend(ERIK).name == "Erik Viking"
    assert store.get_friend(ERIK).online is False


def test_presence_for_a_stranger_invents_no_friendship():
    store = _store()
    result = _ingest(store, "friend_online", "FriendOnline", friend_id=ERIK)
    assert result is None
    assert store.list_friends() == []
    assert len(store.rejected) == 1


# -- friendship actions: build without mutate, record on confirm ---------

def test_friendship_accept_builds_command_without_local_mutation():
    store = _store()
    queue = OffersQueue()
    offer = queue.record("friendship", sender_id=ERIK,
                         sender_name="Erik", now=100.0)
    cmd = store.build_accept_friendship_command(offer.id, ERIK, "cmd-1")
    assert cmd.cmd == "offer_respond" and cmd.tier == "mind"
    assert cmd.params == {"offer_id": offer.id, "kind": "friendship",
                          "decision": "accept", "sender_id": ERIK}
    # Nothing resolved, nothing recorded: confirmation is separate.
    assert queue.get(offer.id, now=101.0) is offer
    assert store.list_friends() == []
    friend = store.confirm_friendship_accepted(
        ERIK, name="Erik", can_see_me_on_map=True)
    assert friend.id == ERIK and friend.can_see_me_on_map is True
    assert store.list_friends() == [friend]
    assert friend.online is None  # accepting invents no presence


def test_friendship_decline_builds_command_and_records_nothing():
    store = _store()
    cmd = store.build_decline_friendship_command("offer-9", MARA, "cmd-2")
    assert cmd.cmd == "offer_respond" and cmd.tier == "mind"
    assert cmd.params["decision"] == "decline"
    assert cmd.params["kind"] == "friendship"
    assert store.list_friends() == []
    with pytest.raises(ValueError):
        store.build_decline_friendship_command("", MARA, "cmd-3")
    with pytest.raises(ValueError):
        store.build_accept_friendship_command("offer-9", "", "cmd-4")


def test_offering_friendship_builds_without_recording():
    store = _store()
    cmd = store.build_offer_friendship_command(ERIK, "cmd-5")
    assert cmd.cmd == "offer_friendship" and cmd.tier == "mind"
    assert cmd.params == {"resident_id": ERIK}
    assert store.get_friend(ERIK) is None
    with pytest.raises(ValueError):
        store.build_offer_friendship_command("", "cmd-6")


# -- groups: memberships and the exactly-one-active invariant -------------

def _group_list(store):
    return _ingest(store, "group_list", "GroupListReceived", groups=[
        {"id": VILLAGE, "name": "Village", "title": "Völva",
         "permissions": {"can_chat": True}, "active": True},
        {"id": MARKET, "name": "Market Guild", "my_title": "Trader"},
    ])


def test_group_list_memberships_titles_and_permissions():
    store = _store()
    filed = _group_list(store)
    assert len(filed) == 2
    village = store.get_group(VILLAGE)
    assert village is not None and village.title == "Völva"
    assert village.permissions == {"can_chat": True}
    market = store.get_group(MARKET)
    assert market is not None and market.title == "Trader"
    assert market.permissions == {}  # not supplied, not invented
    assert [g.id for g in store.list_groups()] == [VILLAGE, MARKET]


def test_exactly_one_active_group_at_a_time():
    store = _store()
    _group_list(store)
    assert store.active_group().id == VILLAGE
    store.set_active_group(MARKET)
    assert store.active_group().id == MARKET
    assert store.get_group(VILLAGE).active is False
    # Recording another group as active demotes the previous one.
    store.record_group(VILLAGE, name="Village", active=True)
    assert store.active_group().id == VILLAGE
    assert store.get_group(MARKET).active is False
    assert sum(1 for g in store.list_groups() if g.active) == 1
    with pytest.raises(KeyError):
        store.set_active_group(TROLL)


def test_join_group_builds_without_mutating_then_confirms():
    store = _store()
    cmd = store.build_join_group_command(VILLAGE, "cmd-7")
    assert cmd.cmd == "join_group" and cmd.tier == "mind"
    assert cmd.params == {"group_id": VILLAGE}
    assert store.list_groups() == []  # built, not yet a member
    group = store.confirm_group_joined(VILLAGE, name="Village",
                                       title="Völva")
    assert store.list_groups() == [group]
    with pytest.raises(ValueError):
        store.build_join_group_command("", "cmd-8")


def test_leave_group_builds_without_mutating_then_confirms():
    store = _store()
    _group_list(store)
    cmd = store.build_leave_group_command(VILLAGE, "cmd-9")
    assert cmd.cmd == "leave_group" and cmd.tier == "mind"
    assert cmd.params == {"group_id": VILLAGE}
    assert store.get_group(VILLAGE) is not None  # still a member
    store.confirm_group_left(VILLAGE)
    assert store.get_group(VILLAGE) is None
    # The active group left with her; no successor is guessed.
    assert store.active_group() is None
    with pytest.raises(KeyError):
        store.build_leave_group_command(VILLAGE, "cmd-10")
    with pytest.raises(KeyError):
        store.confirm_group_left(VILLAGE)


def test_group_joined_and_left_events_are_confirmations():
    store = _store()
    joined = _ingest(store, "group_joined", "GroupJoined",
                     group_id=VILLAGE, name="Village", title="Völva")
    assert joined is not None and store.get_group(VILLAGE) is joined
    left = _ingest(store, "group_left", "GroupLeft", group_id=VILLAGE)
    assert left is not None and store.list_groups() == []


def test_group_invite_via_offers_shape():
    store = _store()
    queue = OffersQueue()
    offer = queue.record("group_invite", sender_id=ERIK, now=100.0,
                         payload={"group_id": VILLAGE})
    cmd = store.build_accept_group_invite_command(
        offer.id, ERIK, "cmd-11", group_id=VILLAGE)
    assert cmd.cmd == "offer_respond" and cmd.tier == "mind"
    assert cmd.params["kind"] == "group_invite"
    assert cmd.params["decision"] == "accept"
    assert cmd.params["group_id"] == VILLAGE
    # Neither resolved in the queue nor a membership yet.
    assert queue.get(offer.id, now=101.0) is offer
    assert store.list_groups() == []
    store.confirm_group_joined(VILLAGE, name="Village")
    assert store.get_group(VILLAGE) is not None

    decline = store.build_decline_group_invite_command(
        "offer-2", ERIK, "cmd-12", group_id=MARKET)
    assert decline.params["decision"] == "decline"
    assert store.get_group(MARKET) is None


# -- group notices inbox -----------------------------------------------------

def test_notices_inbox_attributed_and_rereadable_per_group():
    store = _store()
    _group_list(store)
    first = _ingest(
        store, "group_notice", "GroupNoticeReceived",
        group_id=VILLAGE, sender_id=ERIK, sender_name="Erik",
        subject="Hall tonight", body="Bring mead.",
        attachment_id="asset-1", timestamp=1700.0)
    _ingest(store, "group_notice", "GroupNoticeReceived",
            group_id=MARKET, sender_id=MARA, subject="Prices",
            body="New stock.")
    assert first is not None
    assert first.group_id == VILLAGE and first.sender_id == ERIK
    assert first.sender_name == "Erik" and first.subject == "Hall tonight"
    assert first.body == "Bring mead."
    assert first.attachment_ref == "asset-1"
    assert first.timestamp == 1700.0 and first.muted is False
    village = store.notices_for(VILLAGE)
    assert [n.subject for n in village] == ["Hall tonight"]
    assert [n.subject for n in store.notices_for(MARKET)] == ["Prices"]
    # Re-readable: a second read returns the same held records.
    assert store.notices_for(VILLAGE) == village
    assert len(store.list_notices()) == 2
    # A notice is a record, not a promoted fact.
    assert not hasattr(first, "is_fact")


# -- mute / block honouring -----------------------------------------------------

def test_mute_and_block_predicates_and_admission_helper():
    store = _store()
    assert store.admission_for(ERIK) == "allow"
    assert not store.is_muted(ERIK) and not store.is_blocked(ERIK)
    store.record_mute(MARA, name="Mara", reason="spam in local chat")
    assert store.is_muted(MARA) and store.admission_for(MARA) == "muted"
    store.record_block(TROLL, name="Troll")
    assert store.is_blocked(TROLL)
    assert store.admission_for(TROLL) == "blocked"
    # Blocked is the stronger refusal when both lists hold someone.
    store.record_mute(TROLL)
    assert store.admission_for(TROLL) == "blocked"
    assert [e.resident_id for e in store.muted_residents()] == [MARA, TROLL]
    assert [e.resident_id for e in store.blocked_residents()] == [TROLL]
    with pytest.raises(ValueError):
        store.record_mute("")
    with pytest.raises(ValueError):
        store.record_block("")


def test_mute_list_event_snapshots_both_categories():
    store = _store()
    filed = _ingest(store, "mute_list", "MuteListReceived",
                    muted=[{"id": MARA, "name": "Mara"}],
                    blocked=[TROLL])
    assert len(filed) == 2
    assert store.is_muted(MARA) and store.is_blocked(TROLL)
    # A later snapshot replaces its category rather than merging.
    _ingest(store, "mute_list", "MuteListReceived", muted=[])
    assert not store.is_muted(MARA) and store.is_blocked(TROLL)


def test_blocked_notice_refused_muted_notice_flagged():
    store = _store()
    store.record_block(TROLL)
    store.record_mute(MARA)
    refused = _ingest(store, "group_notice", "GroupNoticeReceived",
                      group_id=VILLAGE, sender_id=TROLL,
                      subject="You won!")
    assert refused is None
    assert store.list_notices() == []
    assert len(store.rejected) == 1
    flagged = _ingest(store, "group_notice", "GroupNoticeReceived",
                      group_id=VILLAGE, sender_id=MARA,
                      subject="Hall tonight", body="muted voice")
    assert flagged is not None and flagged.muted is True
    normal = _ingest(store, "group_notice", "GroupNoticeReceived",
                     group_id=VILLAGE, sender_id=ERIK,
                     subject="Hall tonight", body="clear voice")
    assert normal is not None and normal.muted is False


# -- fail closed ------------------------------------------------------------------

def test_malformed_entries_fail_closed_into_rejected():
    store = _store()
    filed = _friend_list(store, [
        {"id": ERIK, "name": "Erik"},
        {"name": "No Id"},
        {"id": MARA},
        "not-a-dict",
    ])
    assert [f.id for f in filed] == [ERIK]
    assert [f.id for f in store.list_friends()] == [ERIK]
    assert len(store.rejected) == 3
    with pytest.raises(ValueError):
        store.record_friend("", name="Erik")
    with pytest.raises(ValueError):
        store.record_friend(ERIK, name="")
    with pytest.raises(ValueError):
        store.record_group("", name="Village")
    with pytest.raises(ValueError):
        store.record_group(VILLAGE, name="")


def test_non_social_event_and_bad_lists_are_rejected():
    store = _store()
    result = store.ingest_event(
        SeamEvent(name="chat", raw_name="ChatReceived",
                  payload={"msg": "hi"}))
    assert result is None
    assert store.rejected == [("not_a_social_event", "chat")]
    assert _ingest(store, "friend_list", "FriendListReceived") == []
    assert _ingest(store, "group_list", "GroupListReceived") == []
    bad_notice = _ingest(store, "group_notice", "GroupNoticeReceived",
                         group_id=VILLAGE, sender_id=ERIK)
    assert bad_notice is None  # no subject: nothing honest to file
    assert store.list_notices() == []
    assert len(store.rejected) == 4


def test_dispatcher_wires_social_events_into_the_store():
    store = _store()
    dispatcher = EventDispatcher()
    for name in ("friend_list", "friend_online", "friend_offline",
                 "group_list", "group_joined", "group_left",
                 "group_notice", "mute_list"):
        dispatcher.subscribe(name, store.ingest_event)
    dispatcher.dispatch_wire(
        {"event": "FriendListReceived",
         "friends": [{"id": ERIK, "name": "Erik"}]})
    dispatcher.dispatch_wire({"event": "FriendOnline", "friend_id": ERIK})
    assert store.get_friend(ERIK).online is True
    assert dispatcher.dead_letters == [] and dispatcher.handler_errors == []


# -- purge ---------------------------------------------------------------------------

def test_purge_resident_removes_them_from_every_record():
    store = _store()
    _friend_list(store, [{"id": ERIK, "name": "Erik"},
                         {"id": MARA, "name": "Mara"}])
    store.record_mute(ERIK)
    store.record_block(ERIK)
    store.record_mute(MARA)
    _ingest(store, "group_notice", "GroupNoticeReceived",
            group_id=VILLAGE, sender_id=MARA, subject="From Mara")
    # Erik's notice lands only while he is neither muted nor
    # blocked on a fresh store path — file it before blocking in
    # a second store to count his notices honestly.
    other = _store()
    _ingest(other, "group_notice", "GroupNoticeReceived",
            group_id=VILLAGE, sender_id=ERIK, subject="From Erik")
    _ingest(other, "group_notice", "GroupNoticeReceived",
            group_id=VILLAGE, sender_id=MARA, subject="Also Mara")
    removed_other = other.purge_resident(ERIK)
    assert removed_other == 1
    assert [n.subject for n in other.list_notices()] == ["Also Mara"]

    removed = store.purge_resident(ERIK)
    assert removed == 3  # friend + mute entry + block entry
    assert store.get_friend(ERIK) is None
    assert not store.is_muted(ERIK) and not store.is_blocked(ERIK)
    assert store.admission_for(ERIK) == "allow"
    # Everyone else's records are untouched.
    assert store.get_friend(MARA) is not None
    assert store.is_muted(MARA)
    assert [n.subject for n in store.list_notices()] == ["From Mara"]
    assert store.purge_resident(ERIK) == 0
    assert store.purge_resident("") == 0
