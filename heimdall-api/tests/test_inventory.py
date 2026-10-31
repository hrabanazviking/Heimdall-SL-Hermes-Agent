"""F3 — inventory as a sense and a hand (census doc 15 slice F3).

The load-bearing invariants under test:

- unknown kinds and nameless items fail closed, with an audit
  trail in ``rejected`` when ingested as events;
- browsing is honest: folder/kind filters, sorted folders, and
  case-insensitive name search over what is actually held;
- notecard content is never invented — reading returns only text
  that arrived, and missing/unavailable content is an honest error;
- landmarks and gives build governor-ready commands without
  executing anything; a give is recorded only when the caller
  marks it after Core confirmation, and giving keeps the item;
- there is NO delete/trash/remove method at all — destruction is
  a pause-line; the only removal is the per-resident purge.
"""
import pytest
from heimdall.api.event_dispatcher import EventDispatcher
from heimdall.awareness import ITEM_KINDS, InventoryStore
from heimdall.core.models import SeamEvent, canonical_event_name

ERIK = "c3333333-3333-4333-8333-333333333333"
MARA = "d4444444-4444-4444-8444-444444444444"


def _store():
    return InventoryStore(_clock=lambda: 1000.0)


def _ingest(store, event_name="inventory_item", raw="InventoryItemReceived", **payload):
    return store.ingest_event(SeamEvent(name=event_name, raw_name=raw, payload=payload))


# -- vocabulary ---------------------------------------------------------

def test_inventory_event_names_are_canonical():
    assert canonical_event_name("InventoryItemReceived") == "inventory_item"
    assert canonical_event_name("InventoryUpdated") == "inventory_updated"
    assert canonical_event_name("NotecardReceived") == "notecard_received"
    assert canonical_event_name("inventory_item") == "inventory_item"
    assert canonical_event_name("inventory_updated") == "inventory_updated"
    assert canonical_event_name("notecard_received") == "notecard_received"


def test_seam_event_from_wire_canonicalises_new_names():
    for raw, canonical in (
        ("InventoryItemReceived", "inventory_item"),
        ("InventoryUpdated", "inventory_updated"),
        ("NotecardReceived", "notecard_received"),
        ("inventory_item", "inventory_item"),
        ("inventory_updated", "inventory_updated"),
        ("notecard_received", "notecard_received"),
    ):
        event = SeamEvent.from_wire({"event": raw, "name": "x", "kind": "object"})
        assert event is not None and event.name == canonical


def test_all_kinds_are_fileable():
    store = _store()
    for kind in sorted(ITEM_KINDS):
        item = _ingest(store, kind=kind, name=f"a {kind}")
        assert item is not None and item.kind == kind
    assert len(store.list_items()) == len(ITEM_KINDS)


# -- ingest: fail closed ---------------------------------------------------

def test_ingest_valid_item_carries_its_fields():
    store = _store()
    item = _ingest(
        store,
        kind="object",
        item_id="obj-1",
        name="Carved Chair",
        folder="Furniture",
        creator_id=MARA,
        creator_name="Mara",
        sender_id=ERIK,
        sender_name="Erik",
    )
    assert item is not None
    assert item.id == "obj-1" and item.name == "Carved Chair"
    assert item.folder == "Furniture"
    assert item.creator_id == MARA and item.creator_name == "Mara"
    assert item.received_from_id == ERIK and item.received_from_name == "Erik"
    assert item.received_at == 1000.0
    assert store.get("obj-1") is item


def test_ingest_generates_id_and_default_folder():
    store = _store()
    item = _ingest(store, kind="texture", name="Bark")
    assert item is not None and item.id == "item-1"
    assert item.folder == "Objects"
    second = _ingest(store, kind="sound", name="Wind")
    assert second is not None and second.id == "item-2"


def test_unknown_kind_fails_closed():
    store = _store()
    bad = _ingest(store, kind="vehicle", name="Longship")
    assert bad is None
    assert store.list_items() == []
    assert len(store.rejected) == 1
    with pytest.raises(ValueError):
        store.record("vehicle", name="Longship")


def test_missing_name_fails_closed():
    store = _store()
    bad = _ingest(store, kind="object")
    assert bad is None
    assert store.list_items() == []
    assert len(store.rejected) == 1
    with pytest.raises(ValueError):
        store.record("object", name="")


def test_non_inventory_event_is_rejected_not_filed():
    store = _store()
    result = store.ingest_event(
        SeamEvent(name="chat", raw_name="ChatReceived", payload={"msg": "hi"})
    )
    assert result is None
    assert store.list_items() == []
    assert store.rejected == [("not_an_inventory_event", "chat")]


def test_inventory_updated_updates_the_held_item():
    store = _store()
    _ingest(store, kind="object", item_id="obj-1", name="Chair", folder="Old")
    updated = _ingest(
        store,
        event_name="inventory_updated",
        raw="InventoryUpdated",
        kind="object",
        item_id="obj-1",
        name="Carved Chair",
        folder="Furniture",
    )
    assert updated is not None and updated.name == "Carved Chair"
    assert updated.folder == "Furniture"
    assert len(store.list_items()) == 1


def test_dispatcher_wires_inventory_events_into_the_store():
    store = _store()
    dispatcher = EventDispatcher()
    for name in ("inventory_item", "inventory_updated", "notecard_received"):
        dispatcher.subscribe(name, store.ingest_event)
    event = dispatcher.dispatch_wire(
        {"event": "NotecardReceived", "name": "Welcome", "text": "hall rules"}
    )
    assert event is not None and event.name == "notecard_received"
    held = store.list_items()
    assert len(held) == 1 and held[0].kind == "notecard"
    assert dispatcher.dead_letters == [] and dispatcher.handler_errors == []


# -- browsing ---------------------------------------------------------------

def _stocked():
    store = _store()
    _ingest(store, kind="object", name="Carved Chair", folder="Furniture")
    _ingest(store, kind="clothing", name="Wool Cloak", folder="Clothing")
    _ingest(store, kind="object", name="Oak Table", folder="Furniture")
    _ingest(store, kind="landmark", name="Home", folder="Landmarks")
    return store


def test_folder_and_kind_filtering():
    store = _stocked()
    assert [i.name for i in store.list_items(folder="Furniture")] == [
        "Carved Chair",
        "Oak Table",
    ]
    assert [i.name for i in store.list_items(kind="object")] == [
        "Carved Chair",
        "Oak Table",
    ]
    assert [i.name for i in store.list_items(folder="Furniture", kind="clothing")] == []
    assert len(store.list_items()) == 4


def test_folders_are_sorted_and_only_populated():
    store = _stocked()
    assert store.folders() == ["Clothing", "Furniture", "Landmarks"]
    assert _store().folders() == []


def test_search_is_case_insensitive_name_substring():
    store = _stocked()
    assert [i.name for i in store.search("chair")] == ["Carved Chair"]
    assert [i.name for i in store.search("CHAIR")] == ["Carved Chair"]
    assert [i.name for i in store.search("oak t")] == ["Oak Table"]
    assert store.search("longship") == []


# -- notecards -----------------------------------------------------------------

def test_notecard_read_returns_arrived_text_and_lines():
    store = _store()
    item = _ingest(
        store,
        event_name="notecard_received",
        raw="NotecardReceived",
        name="Hall Rules",
        notecard_text="be kind\nno shoes\n",
        sender_id=ERIK,
    )
    assert item is not None and item.kind == "notecard"
    content = store.read_notecard(item.id)
    assert content.item_id == item.id
    assert content.text == "be kind\nno shoes\n"
    assert content.lines == ["be kind", "no shoes"]


def test_notecard_text_is_never_stored_for_other_kinds():
    store = _store()
    item = _ingest(store, kind="object", name="Box", notecard_text="secret")
    assert item is not None and item.notecard_text is None


def test_read_notecard_wrong_kind_and_missing_item():
    store = _stocked()
    chair = store.search("chair")[0]
    with pytest.raises(ValueError):
        store.read_notecard(chair.id)
    with pytest.raises(KeyError):
        store.read_notecard("item-999")


def test_read_notecard_content_unavailable_is_an_honest_error():
    store = _store()
    bare = _ingest(store, event_name="notecard_received", raw="NotecardReceived",
                   name="Sealed Note")
    assert bare is not None
    with pytest.raises(ValueError):
        store.read_notecard(bare.id)
    flagged = _ingest(
        store,
        event_name="notecard_received",
        raw="NotecardReceived",
        name="Flagged Note",
        notecard_text="you should not see this",
        content_available=False,
    )
    assert flagged is not None
    with pytest.raises(ValueError):
        store.read_notecard(flagged.id)


# -- landmarks -------------------------------------------------------------------

def _landmark(store, **payload):
    base = {"kind": "landmark", "name": "Home", "folder": "Landmarks"}
    base.update(payload)
    return _ingest(store, **base)


def test_list_landmarks_only_landmarks():
    store = _stocked()
    _landmark(store, name="Market", region="Daikoku")
    assert [i.name for i in store.list_landmarks()] == ["Home", "Market"]


def test_teleport_command_from_region_and_coords():
    store = _store()
    lm = _landmark(store, region="Viking Village", x=10, y=20, z=30)
    cmd = store.build_teleport_command(lm.id, "cmd-1")
    assert cmd.id == "cmd-1" and cmd.cmd == "teleport" and cmd.tier == "mind"
    assert cmd.params["item_id"] == lm.id
    assert cmd.params["landmark_id"] == lm.id
    assert cmd.params["region"] == "Viking Village"
    assert cmd.params["x"] == 10.0 and cmd.params["y"] == 20.0
    assert cmd.params["z"] == 30.0


def test_teleport_command_from_coords_dict_and_slurl_only():
    store = _store()
    lm = _landmark(store, coords={"x": 1, "y": 2, "z": 3})
    cmd = store.build_teleport_command(lm.id, "cmd-2")
    assert (cmd.params["x"], cmd.params["y"], cmd.params["z"]) == (1.0, 2.0, 3.0)
    slurl_only = _landmark(
        store, name="Elsewhere", slurl="http://maps.secondlife.com/secondlife/X/1/2/3"
    )
    cmd2 = store.build_teleport_command(slurl_only.id, "cmd-3")
    assert cmd2.params["slurl"].startswith("http://maps.secondlife.com/")


def test_teleport_command_no_destination_and_wrong_kind_fail():
    store = _stocked()
    bare = _landmark(store, name="Nowhere")
    with pytest.raises(ValueError):
        store.build_teleport_command(bare.id, "cmd-4")
    chair = store.search("chair")[0]
    with pytest.raises(ValueError):
        store.build_teleport_command(chair.id, "cmd-5")
    with pytest.raises(KeyError):
        store.build_teleport_command("item-999", "cmd-6")


# -- giving ------------------------------------------------------------------------

def test_give_command_builds_without_recording():
    store = _stocked()
    chair = store.search("chair")[0]
    cmd = store.build_give_command(chair.id, ERIK, "cmd-7")
    assert cmd.cmd == "give_inventory" and cmd.tier == "mind"
    assert cmd.params == {"item_id": chair.id, "recipient_id": ERIK}
    # Building records nothing: only a confirmed mark_given does.
    assert store.given_history() == []
    assert store.get(chair.id) is chair


def test_mark_given_records_only_after_confirmation_and_keeps_item():
    store = _stocked()
    chair = store.search("chair")[0]
    store.build_give_command(chair.id, ERIK, "cmd-8")
    assert store.given_history() == []  # built, not yet confirmed
    record = store.mark_given(chair.id, ERIK, now=1100.0)
    assert record.item_id == chair.id and record.item_name == "Carved Chair"
    assert record.recipient_id == ERIK and record.given_at == 1100.0
    assert store.given_history() == [record]
    # Giving does not remove the item (no copy/transfer data here).
    assert store.get(chair.id) is chair


def test_give_fails_closed_on_missing_recipient_or_item():
    store = _stocked()
    chair = store.search("chair")[0]
    with pytest.raises(ValueError):
        store.build_give_command(chair.id, "", "cmd-9")
    with pytest.raises(KeyError):
        store.build_give_command("item-999", ERIK, "cmd-10")
    with pytest.raises(ValueError):
        store.mark_given(chair.id, "")
    with pytest.raises(KeyError):
        store.mark_given("item-999", ERIK)
    assert store.given_history() == []


# -- purge & the missing delete -------------------------------------------------------

def test_purge_resident_removes_their_items_and_given_entries():
    store = _store()
    _ingest(store, kind="object", name="From Erik", sender_id=ERIK)
    _ingest(store, kind="object", name="By Erik", creator_id=ERIK)
    kept = _ingest(store, kind="object", name="By Mara", creator_id=MARA)
    store.mark_given(kept.id, ERIK, now=1100.0)
    store.mark_given(kept.id, MARA, now=1200.0)
    removed = store.purge_resident(ERIK)
    assert removed == 3  # two items + one given-history entry
    assert [i.name for i in store.list_items()] == ["By Mara"]
    assert [g.recipient_id for g in store.given_history()] == [MARA]
    assert store.purge_resident(ERIK) == 0
    assert store.purge_resident("") == 0


def test_no_delete_trash_or_remove_method_exists():
    store = _stocked()
    for name in ("delete", "trash", "remove", "destroy",
                 "delete_item", "trash_item", "remove_item"):
        assert not hasattr(store, name), name
    public = [m for m in dir(store) if not m.startswith("_")]
    assert not [
        m for m in public
        if any(w in m for w in ("delete", "trash", "remove", "destroy"))
    ]


# -- items are records, not facts -------------------------------------------------------

def test_items_are_records_not_facts_and_payload_is_a_copy():
    store = _store()
    payload = {"kind": "object", "name": "Runestone", "weight": 3}
    item = store.ingest_event(
        SeamEvent(name="inventory_item", raw_name="InventoryItemReceived",
                  payload=payload)
    )
    assert item is not None
    # The stored payload is a copy: mutating the caller's dict after
    # ingest does not rewrite what the store holds.
    payload["weight"] = 999
    assert store.get(item.id).payload["weight"] == 3
    # Nothing on the record promotes it into a claim about the world.
    assert not hasattr(item, "is_fact")
    assert not hasattr(item, "claim")
