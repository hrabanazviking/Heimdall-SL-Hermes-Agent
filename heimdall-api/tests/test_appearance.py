"""F7 — appearance (census doc 15 slice F7).

The load-bearing invariants under test:

- before any snapshot the outfit is UNKNOWN, never invented
  as empty or naked; an explicitly-empty snapshot is known
  and distinguishable from unknown;
- a snapshot holds exactly what it carried — sparse slot /
  attach-point fields stay None — replaces the outfit
  wholesale, and clears staleness; malformed entries fail
  closed into ``rejected`` and ingest never raises;
- only clothing / bodypart / object (a subset of F3's kinds)
  are wearable; every other kind fails closed;
- wear / attach / change-outfit build governor-ready commands
  (tier="mind") validated against a bound inventory (unknown
  item / non-wearable kind fail closed) or, unbound, against
  explicit caller details only — building records nothing;
- remove / detach require the item to be currently worn /
  attached; changes enter history only on confirmation;
- ``appearance_changed`` (already in the base vocabulary, not
  duplicated by F7) alters no outfit entry: it marks a known
  outfit stale and records a notice, cleared by the next
  snapshot;
- ``purge_resident`` removes that resident's worn/attached
  items (snapshot-carried or inventory-carried ties) and
  their history entries.
"""
import pytest

from heimdall.awareness import AppearanceStore, InventoryStore
from heimdall.core.models import CORE_EVENT_ALIASES, SeamEvent, canonical_event_name

ERIK = "c3333333-3333-4333-8333-333333333333"
OTHER = "d4444444-4444-4444-8444-444444444444"
DRESS = "a1111111-1111-4111-8111-111111111111"
HAIR = "b2222222-2222-4222-8222-222222222222"
SWORD = "e5555555-5555-4555-8555-555555555555"
NOTE = "f6666666-6666-4666-8666-666666666666"


def _store(inventory=None):
    return AppearanceStore(inventory=inventory, _clock=lambda: 1000.0)


def _ingest(store, event_name, raw, **payload):
    return store.ingest_event(
        SeamEvent(name=event_name, raw_name=raw, payload=payload)
    )


def _inventory():
    inv = InventoryStore()
    inv.record("clothing", name="Black Dress", item_id=DRESS, creator_id=ERIK)
    inv.record("bodypart", name="Long Hair", item_id=HAIR)
    inv.record("object", name="Rune Sword", item_id=SWORD)
    inv.record("notecard", name="A Note", item_id=NOTE)
    return inv


def _snapshot(store, entries=None):
    if entries is None:
        entries = [
            {"item_id": DRESS, "name": "Black Dress",
             "kind": "clothing", "slot": "dress"},
            {"item_id": HAIR, "name": "Long Hair", "kind": "bodypart"},
            {"item_id": SWORD, "name": "Rune Sword", "kind": "object",
             "attach_point": "right hand"},
        ]
    return _ingest(store, "outfit_received", "OutfitReceived", outfit=entries)


# -- vocabulary ---------------------------------------------------------

def test_appearance_event_names_are_canonical():
    assert canonical_event_name("OutfitReceived") == "outfit_received"
    assert canonical_event_name("ItemWorn") == "item_worn"
    assert canonical_event_name("ItemRemoved") == "item_removed"
    assert canonical_event_name("AttachmentAttached") == "attachment_attached"
    assert canonical_event_name("AttachmentDetached") == "attachment_detached"
    for api_form in (
        "outfit_received", "item_worn", "item_removed",
        "attachment_attached", "attachment_detached",
    ):
        assert canonical_event_name(api_form) == api_form


def test_appearance_changed_not_duplicated_by_f7():
    # It rode the base vocabulary before F7: exactly the two
    # pre-existing entries (Core name + API identity form).
    forms = [k for k, v in CORE_EVENT_ALIASES.items()
             if v == "appearance_changed"]
    assert sorted(forms) == ["AppearanceChanged", "appearance_changed"]
    event = SeamEvent.from_wire({"event": "AppearanceChanged"})
    assert event is not None and event.name == "appearance_changed"


def test_seam_event_from_wire_canonicalises_new_names():
    for raw, canonical in (
        ("OutfitReceived", "outfit_received"),
        ("ItemWorn", "item_worn"),
        ("AttachmentDetached", "attachment_detached"),
    ):
        event = SeamEvent.from_wire({"event": raw, "item_id": DRESS})
        assert event is not None and event.name == canonical


# -- unknown vs explicitly empty -------------------------------------------

def test_outfit_is_unknown_before_any_snapshot():
    store = _store()
    assert store.outfit_known is False
    assert store.outfit_stale is False
    assert store.current_outfit() == []
    assert store.current_attachments() == []
    assert store.is_wearing(DRESS) is False
    state = store.current_state()
    assert state.known is False and state.outfit == []
    assert "unknown" in store.describe().lower()
    assert "not known" in store.describe()


def test_explicitly_empty_snapshot_is_known_and_not_unknown():
    store = _store()
    record = _snapshot(store, entries=[])
    assert record.kind == "snapshot" and record.item_ids == ()
    assert store.outfit_known is True
    assert store.current_outfit() == []
    assert "unknown" not in store.describe().lower().split("wearing")[0]


def test_single_worn_confirmation_does_not_invent_a_known_outfit():
    store = _store()
    record = _ingest(store, "item_worn", "ItemWorn", item_id=DRESS,
                     name="Black Dress", kind="clothing")
    assert record.kind == "worn"
    assert store.is_wearing(DRESS) is True
    # One confirmed item is not the whole outfit.
    assert store.outfit_known is False
    assert "not known" in store.describe()


# -- snapshot: exactly what arrived -----------------------------------------

def test_snapshot_holds_exactly_what_arrived():
    store = _store()
    _snapshot(store)
    assert store.outfit_known is True
    dress = store.get_worn(DRESS)
    assert dress.name == "Black Dress" and dress.kind == "clothing"
    assert dress.slot == "dress" and dress.attach_point is None
    hair = store.get_worn(HAIR)
    assert hair.slot is None and hair.attach_point is None
    sword = store.get_worn(SWORD)
    assert sword.attach_point == "right hand" and sword.slot is None
    assert [i.id for i in store.current_outfit()] == [DRESS, HAIR]
    assert [i.id for i in store.current_attachments()] == [SWORD]
    assert store.is_wearing(DRESS) is True
    assert store.is_wearing(SWORD) is False  # attached, not worn
    assert store.is_attached(SWORD) is True


def test_snapshot_replaces_outfit_wholesale():
    store = _store()
    _snapshot(store)
    _snapshot(store, entries=[
        {"item_id": HAIR, "name": "Long Hair", "kind": "bodypart"},
    ])
    assert [i.id for i in store.current_outfit()] == [HAIR]
    assert store.current_attachments() == []
    assert store.is_wearing(DRESS) is False
    assert [r.kind for r in store.outfit_history()] == [
        "snapshot", "snapshot",
    ]


def test_snapshot_malformed_entries_fail_closed_and_never_raise():
    store = _store()
    record = _snapshot(store, entries=[
        {"item_id": DRESS, "name": "Black Dress", "kind": "clothing"},
        {"name": "No id here", "kind": "clothing"},
        {"item_id": NOTE, "name": "A Note", "kind": "notecard"},
        {"item_id": HAIR, "kind": "bodypart"},  # no name, no inventory
        "not-a-dict",
    ])
    assert [i.id for i in store.current_outfit()] == [DRESS]
    assert len(store.rejected) == 4
    assert record.item_ids == (DRESS,)
    assert _ingest(store, "chat", "ChatReceived", msg="hi") is None
    assert store.rejected[-1][0] == "not_an_appearance_event"


def test_nonwearable_kind_fails_closed_everywhere():
    store = _store()
    with pytest.raises(ValueError):
        store.confirm_worn(NOTE, name="A Note", kind="notecard")
    with pytest.raises(ValueError):
        store.build_wear_command(NOTE, "c1", name="A Note", kind="notecard")
    assert store.current_outfit() == []
    assert store.outfit_history() == []


# -- wear: build-only, inventory-validated -----------------------------------

def test_build_wear_validates_via_bound_inventory_and_records_nothing():
    store = _store(inventory=_inventory())
    cmd = store.build_wear_command(DRESS, "c1")
    assert cmd.cmd == "wear_item" and cmd.tier == "mind"
    assert cmd.params["item_id"] == DRESS
    assert cmd.params["name"] == "Black Dress"  # from inventory, not a guess
    assert cmd.params["kind"] == "clothing"
    assert store.current_outfit() == []
    assert store.outfit_history() == []


def test_build_wear_unknown_or_nonwearable_inventory_item_fails_closed():
    store = _store(inventory=_inventory())
    with pytest.raises(KeyError):
        store.build_wear_command("no-such-item", "c1")
    with pytest.raises(ValueError):
        store.build_wear_command(NOTE, "c2")  # a notecard is not wearable
    assert store.outfit_history() == []


def test_build_wear_without_inventory_needs_explicit_details():
    store = _store()
    with pytest.raises(ValueError):
        store.build_wear_command(DRESS, "c1")  # nothing to validate against
    with pytest.raises(ValueError):
        store.build_wear_command(DRESS, "c1", name="Black Dress")
    cmd = store.build_wear_command(
        DRESS, "c1", name="Black Dress", kind="clothing", slot="dress"
    )
    assert cmd.params["name"] == "Black Dress"
    assert cmd.params["slot"] == "dress"
    assert store.current_outfit() == []


def test_confirm_worn_records_history_and_updates_outfit():
    store = _store(inventory=_inventory())
    store.build_wear_command(DRESS, "c1")
    record = store.confirm_worn(DRESS)  # resolved through the inventory
    assert record.kind == "worn" and record.item_id == DRESS
    assert record.item_name == "Black Dress"
    assert store.is_wearing(DRESS) is True
    assert [r.kind for r in store.outfit_history()] == ["worn"]


# -- remove / detach: must be currently worn / attached ------------------------

def test_remove_requires_currently_worn_then_confirm_removes():
    store = _store()
    _snapshot(store)
    cmd = store.build_remove_command(DRESS, "c1")
    assert cmd.cmd == "remove_item" and cmd.tier == "mind"
    assert store.is_wearing(DRESS) is True  # building removed nothing
    record = store.confirm_removed(DRESS)
    assert record.kind == "removed"
    assert store.is_wearing(DRESS) is False
    with pytest.raises(KeyError):
        store.build_remove_command(DRESS, "c2")
    with pytest.raises(KeyError):
        store.confirm_removed(DRESS)
    # An attachment is detached, not removed.
    with pytest.raises(KeyError):
        store.build_remove_command(SWORD, "c3")


def test_item_removed_event_is_the_confirmation_and_unknown_fails_closed():
    store = _store()
    _snapshot(store)
    record = _ingest(store, "item_removed", "ItemRemoved", item_id=DRESS)
    assert record is not None and record.kind == "removed"
    assert _ingest(store, "item_removed", "ItemRemoved",
                   item_id=DRESS) is None
    assert store.rejected[-1][1] == "item_removed"
    assert [r.kind for r in store.outfit_history()] == [
        "snapshot", "removed",
    ]


# -- attach / detach -----------------------------------------------------------

def test_attach_requires_attach_point_and_validates():
    store = _store(inventory=_inventory())
    with pytest.raises(ValueError):
        store.build_attach_command(SWORD, "c1")
    with pytest.raises(ValueError):
        store.build_attach_command(SWORD, "c1", attach_point="  ")
    cmd = store.build_attach_command(SWORD, "c1", attach_point="right hand")
    assert cmd.cmd == "attach_item" and cmd.tier == "mind"
    assert cmd.params["attach_point"] == "right hand"
    assert store.current_attachments() == []  # building recorded nothing
    record = store.confirm_attached(SWORD, attach_point="right hand")
    assert record.kind == "attached"
    assert store.is_attached(SWORD) is True


def test_detach_requires_currently_attached_then_confirm_detaches():
    store = _store()
    _snapshot(store)
    cmd = store.build_detach_command(SWORD, "c1")
    assert cmd.cmd == "detach_item"
    assert store.is_attached(SWORD) is True  # building detached nothing
    record = _ingest(store, "attachment_detached", "AttachmentDetached",
                     item_id=SWORD)
    assert record is not None and record.kind == "detached"
    assert store.is_attached(SWORD) is False
    with pytest.raises(KeyError):
        store.build_detach_command(SWORD, "c2")
    with pytest.raises(KeyError):
        store.confirm_detached(SWORD)


def test_attachment_attached_event_files_the_item():
    store = _store()
    record = _ingest(store, "attachment_attached", "AttachmentAttached",
                     item_id=SWORD, name="Rune Sword", kind="object",
                     attach_point="right hand")
    assert record.kind == "attached"
    assert [i.id for i in store.current_attachments()] == [SWORD]
    assert store.get_worn(SWORD).attach_point == "right hand"


# -- change outfit wholesale -----------------------------------------------------

def test_change_outfit_build_validates_all_and_records_nothing():
    store = _store(inventory=_inventory())
    cmd = store.build_change_outfit_command([DRESS, SWORD], "c1")
    assert cmd.cmd == "change_outfit" and cmd.tier == "mind"
    assert cmd.params["item_ids"] == [DRESS, SWORD]
    assert store.outfit_known is False
    assert store.outfit_history() == []
    with pytest.raises(ValueError):
        store.build_change_outfit_command([DRESS, NOTE], "c2")
    with pytest.raises(KeyError):
        store.build_change_outfit_command([DRESS, "no-such-item"], "c3")


def test_change_outfit_unbound_needs_details_for_every_item():
    store = _store()
    details = {
        DRESS: {"name": "Black Dress", "kind": "clothing"},
        HAIR: {"name": "Long Hair", "kind": "bodypart"},
    }
    cmd = store.build_change_outfit_command(
        [DRESS, HAIR], "c1", item_details=details
    )
    assert cmd.params["item_ids"] == [DRESS, HAIR]
    with pytest.raises(ValueError):
        store.build_change_outfit_command([DRESS, HAIR], "c2")


def test_confirm_outfit_replaces_wholesale_and_marks_known():
    store = _store()
    _snapshot(store)
    record = store.confirm_outfit([
        {"item_id": HAIR, "name": "Long Hair", "kind": "bodypart"},
    ])
    assert record.kind == "outfit" and record.item_ids == (HAIR,)
    assert store.outfit_known is True
    assert [i.id for i in store.current_outfit()] == [HAIR]
    assert store.current_attachments() == []


def test_confirm_snapshot_direct_call_marks_known():
    store = _store()
    record = store.confirm_snapshot([])
    assert record.kind == "snapshot"
    assert store.outfit_known is True
    assert store.current_outfit() == []


# -- appearance_changed: stale, never data ---------------------------------------

def test_appearance_changed_marks_stale_without_changing_entries():
    store = _store()
    _snapshot(store)
    before = [(i.id, i.name, i.slot) for i in store.current_outfit()]
    notice = _ingest(store, "appearance_changed", "AppearanceChanged",
                     reason="appearance_set")
    assert notice is not None
    assert store.outfit_stale is True
    assert [(i.id, i.name, i.slot) for i in store.current_outfit()] == before
    assert [i.id for i in store.current_attachments()] == [SWORD]
    assert len(store.appearance_notices()) == 1
    assert "stale" in store.describe()
    # History gained no outfit change from a mere notice.
    assert [r.kind for r in store.outfit_history()] == ["snapshot"]


def test_next_snapshot_clears_staleness():
    store = _store()
    _snapshot(store)
    _ingest(store, "appearance_changed", "AppearanceChanged")
    assert store.outfit_stale is True
    _snapshot(store, entries=[])
    assert store.outfit_stale is False
    assert store.outfit_known is True


def test_appearance_changed_while_unknown_stays_unknown():
    store = _store()
    _ingest(store, "appearance_changed", "AppearanceChanged")
    assert store.outfit_known is False
    assert len(store.appearance_notices()) == 1
    assert "unknown" in store.describe().lower()


def test_describe_answers_what_are_you_wearing_from_state():
    store = _store()
    _snapshot(store)
    text = store.describe()
    assert "Black Dress" in text and "Rune Sword" in text
    assert "right hand" in text
    state = store.current_state()
    assert state.known is True and state.stale is False
    assert state.notices == 0


# -- purge -------------------------------------------------------------------------

def test_purge_resident_via_snapshot_carried_fields():
    store = _store()
    _snapshot(store, entries=[
        {"item_id": DRESS, "name": "Black Dress", "kind": "clothing",
         "creator_id": ERIK},
        {"item_id": HAIR, "name": "Long Hair", "kind": "bodypart",
         "creator_id": OTHER},
        {"item_id": SWORD, "name": "Rune Sword", "kind": "object",
         "attach_point": "right hand", "received_from_id": ERIK},
    ])
    store.confirm_worn(HAIR, name="Long Hair", kind="bodypart")
    removed = store.purge_resident(ERIK)
    # Dress + sword; the snapshot history entry named both, so
    # it goes too. Hair (OTHER's) and its worn record stay.
    assert removed == 3
    assert [i.id for i in store.current_outfit()] == [HAIR]
    assert store.current_attachments() == []
    assert [r.kind for r in store.outfit_history()] == ["worn"]
    assert store.purge_resident("") == 0
    assert store.purge_resident(ERIK) == 0


def test_purge_resident_via_bound_inventory_data():
    inv = _inventory()  # DRESS was created by ERIK in inventory
    store = _store(inventory=inv)
    _snapshot(store, entries=[
        {"item_id": DRESS, "name": "Black Dress", "kind": "clothing"},
        {"item_id": HAIR, "name": "Long Hair", "kind": "bodypart"},
    ])
    removed = store.purge_resident(ERIK)
    # The snapshot carried no creator for the dress — the bound
    # inventory's creator data supplies the tie. Plus the
    # snapshot history entry that named it.
    assert removed == 2
    assert [i.id for i in store.current_outfit()] == [HAIR]
