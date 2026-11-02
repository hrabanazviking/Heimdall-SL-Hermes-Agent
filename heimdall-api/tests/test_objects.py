"""F6 — object interaction (census doc 15 slice F6).

The load-bearing invariants under test:

- the object registry holds exactly what an event carried —
  absent owner/position/affordances stay absent, sparse
  re-records erase nothing, unknown/malformed records fail
  closed into ``rejected`` and ingest never raises;
- touch and object-targeted sit build governor-ready commands
  (tier="mind") without recording anything; the interaction is
  recorded only on confirmation, touching/sitting an unknown
  object fails closed, and an explicitly reported absent
  affordance refuses the build;
- script dialogs are F1 offers: the F6 side validates the chosen
  button against the buttons the dialog actually offered (an
  unoffered button fails closed), builds the answer through the
  queue's own resolution builder without resolving, and marks
  the dialog answered only on confirmation;
- animations and gestures are build-only commands naming exactly
  the identifier supplied; the known registry holds only what an
  event, a confirmation, or an explicit note supplied;
- ``purge_resident`` removes that resident's objects and the
  interaction/dialog records tied to them.
"""
import pytest
from heimdall.awareness import ObjectStore, OffersQueue, dialog_buttons
from heimdall.core.models import SeamEvent, canonical_event_name

ERIK = "c3333333-3333-4333-8333-333333333333"
OTHER = "d4444444-4444-4444-8444-444444444444"
JUKEBOX = "a1111111-1111-4111-8111-111111111111"
CHAIR = "b2222222-2222-4222-8222-222222222222"


def _store():
    return ObjectStore(_clock=lambda: 1000.0)


def _ingest(store, event_name, raw, **payload):
    return store.ingest_event(
        SeamEvent(name=event_name, raw_name=raw, payload=payload)
    )


def _jukebox(store, **kw):
    return store.record_object(
        JUKEBOX, "Jukebox", owner_id=ERIK, touchable=True, **kw
    )


def _dialog(queue, **payload):
    body = {
        "buttons": ["Dance", "Sit", "Leave"],
        "channel": -42,
        "object_id": JUKEBOX,
        "message": "Pick a track",
    }
    body.update(payload)
    return queue.record(
        "script_dialog", sender_id=JUKEBOX, sender_name="Jukebox",
        payload=body, now=100.0,
    )


# -- vocabulary ---------------------------------------------------------

def test_objects_event_names_are_canonical():
    assert canonical_event_name("ObjectTouched") == "object_touched"
    assert canonical_event_name("ObjectSatOn") == "object_sat_on"
    assert canonical_event_name("AnimationStarted") == "animation_started"
    assert canonical_event_name("AnimationStopped") == "animation_stopped"
    assert canonical_event_name("GesturePlayed") == "gesture_played"
    for api_form in (
        "object_touched", "object_sat_on", "animation_started",
        "animation_stopped", "gesture_played",
    ):
        assert canonical_event_name(api_form) == api_form
    # Already in the base / F1 vocabulary — not duplicated by F6.
    assert canonical_event_name("ObjectInfo") == "object_info"
    assert canonical_event_name("ScriptDialogReceived") == "script_dialog"


def test_seam_event_from_wire_canonicalises_new_names():
    for raw, canonical in (
        ("ObjectTouched", "object_touched"),
        ("ObjectSatOn", "object_sat_on"),
        ("GesturePlayed", "gesture_played"),
    ):
        event = SeamEvent.from_wire({"event": raw, "object_id": JUKEBOX})
        assert event is not None and event.name == canonical


# -- registry: ingest, fail-closed, nothing invented ----------------------

def test_ingest_object_info_holds_exactly_what_arrived():
    store = _store()
    record = _ingest(
        store, "object_info", "ObjectInfo",
        object_id=JUKEBOX, name="Jukebox", owner_id=ERIK,
        position=[128.0, 64.0, 25.0], touchable=True,
    )
    assert record is not None
    assert record.id == JUKEBOX and record.name == "Jukebox"
    assert record.owner_id == ERIK and record.owner_name is None
    assert record.position == (128.0, 64.0, 25.0)
    assert record.touchable is True
    assert record.sittable is None  # not reported — not invented


def test_ingest_object_info_sparse_record_invents_nothing():
    store = _store()
    record = _ingest(store, "object_info", "ObjectInfo",
                     object_id=CHAIR, name="Chair")
    assert record is not None
    assert record.owner_id is None and record.position is None
    assert record.touchable is None and record.sittable is None


def test_sparse_rerecord_erases_nothing():
    store = _store()
    _jukebox(store, position=(1.0, 2.0, 3.0))
    again = store.record_object(JUKEBOX, "Jukebox")
    assert again.owner_id == ERIK
    assert again.position == (1.0, 2.0, 3.0)
    assert again.touchable is True


def test_ingest_bulk_objects_with_one_malformed():
    store = _store()
    filed = _ingest(
        store, "object_info", "ObjectInfo",
        objects=[
            {"object_id": JUKEBOX, "name": "Jukebox"},
            {"name": "No id here"},
            {"object_id": CHAIR, "name": "Chair", "sittable": True},
        ],
    )
    assert [r.id for r in filed] == [JUKEBOX, CHAIR]
    assert store.get_object(CHAIR).sittable is True
    assert len(store.rejected) == 1


def test_ingest_malformed_fails_closed_and_never_raises():
    store = _store()
    assert _ingest(store, "object_info", "ObjectInfo", name="Nameless") is None
    assert _ingest(store, "object_info", "ObjectInfo",
                   object_id=JUKEBOX) is None
    assert store.list_objects() == []
    assert len(store.rejected) == 2
    with pytest.raises(ValueError):
        store.record_object("", "Jukebox")
    with pytest.raises(ValueError):
        store.record_object(JUKEBOX, "")


def test_ingest_unknown_and_dialog_events_are_not_objects_events():
    store = _store()
    assert _ingest(store, "chat", "ChatReceived", msg="hi") is None
    # Script dialogs are held by the F1 offers queue, not here.
    assert _ingest(store, "script_dialog", "ScriptDialogReceived",
                   sender_id=JUKEBOX) is None
    assert [r[0] for r in store.rejected] == [
        "not_an_objects_event", "not_an_objects_event",
    ]


# -- touch: build-only, confirm-after --------------------------------------

def test_build_touch_records_nothing_then_confirm_records():
    store = _store()
    _jukebox(store)
    cmd = store.build_touch_command(JUKEBOX, "c1")
    assert cmd.cmd == "touch_object" and cmd.tier == "mind"
    assert cmd.params["object_id"] == JUKEBOX
    assert store.interaction_history() == []
    record = store.confirm_touch(JUKEBOX)
    assert record.kind == "touch" and record.object_id == JUKEBOX
    assert record.object_name == "Jukebox"
    assert [r.kind for r in store.interaction_history()] == ["touch"]


def test_touch_unknown_object_fails_closed():
    store = _store()
    with pytest.raises(KeyError):
        store.build_touch_command(JUKEBOX, "c1")
    with pytest.raises(KeyError):
        store.confirm_touch(JUKEBOX)


def test_touch_explicitly_untouchable_refused_unreported_allowed():
    store = _store()
    store.record_object(JUKEBOX, "Statue", touchable=False)
    with pytest.raises(ValueError):
        store.build_touch_command(JUKEBOX, "c1")
    store.record_object(CHAIR, "Chair")  # affordance unreported
    assert store.build_touch_command(CHAIR, "c2").cmd == "touch_object"


def test_object_touched_event_is_the_confirmation():
    store = _store()
    _jukebox(store)
    record = _ingest(store, "object_touched", "ObjectTouched",
                     object_id=JUKEBOX)
    assert record is not None and record.kind == "touch"
    # A confirmation for an unknown object fails closed.
    assert _ingest(store, "object_touched", "ObjectTouched",
                   object_id=CHAIR) is None
    assert len(store.interaction_history()) == 1
    assert store.rejected[-1][1] == "object_touched"


# -- object-targeted sit -----------------------------------------------------

def test_build_sit_is_object_targeted_and_build_only():
    store = _store()
    store.record_object(CHAIR, "Chair", sittable=True)
    cmd = store.build_sit_command(CHAIR, "c1")
    assert cmd.cmd == "sit_on_object" and cmd.tier == "mind"
    assert cmd.params["object_id"] == CHAIR
    assert cmd.params["target_uuid"] == CHAIR
    assert store.interaction_history() == []
    assert store.seated_on() is None
    record = store.confirm_sit(CHAIR)
    assert record.kind == "sit"
    assert store.seated_on() == CHAIR


def test_sit_unknown_and_unsittable_fail_closed():
    store = _store()
    with pytest.raises(KeyError):
        store.build_sit_command(CHAIR, "c1")
    store.record_object(CHAIR, "Chair", sittable=False)
    with pytest.raises(ValueError):
        store.build_sit_command(CHAIR, "c1")
    assert _ingest(store, "object_sat_on", "ObjectSatOn",
                   object_id=JUKEBOX) is None
    assert store.interaction_history() == []


# -- script dialogs via F1 ---------------------------------------------------

def test_dialog_buttons_reads_only_what_was_offered():
    queue = OffersQueue()
    offer = _dialog(queue)
    assert dialog_buttons(offer) == ["Dance", "Sit", "Leave"]
    bare = queue.record("script_dialog", sender_id=JUKEBOX,
                        payload={}, now=100.0)
    assert dialog_buttons(bare) == []


def test_build_dialog_answer_by_label_and_index_resolves_nothing():
    store = _store()
    queue = OffersQueue()
    offer = _dialog(queue)
    by_label = store.build_dialog_answer_command(
        queue, offer.id, button_label="Dance", now=110.0
    )
    assert by_label.cmd == "offer_respond" and by_label.tier == "mind"
    assert by_label.params["decision"] == "answer"
    assert by_label.params["answer"] == {"index": 0, "label": "Dance"}
    assert by_label.params["channel"] == -42  # carried back for Core
    by_index = store.build_dialog_answer_command(
        queue, offer.id, button_index=2, now=110.0
    )
    assert by_index.params["answer"] == {"index": 2, "label": "Leave"}
    # Building resolved nothing and recorded nothing.
    assert queue.get(offer.id, now=111.0) is not None
    assert queue.history() == []
    assert store.dialog_history() == []


def test_dialog_unoffered_button_fails_closed():
    store = _store()
    queue = OffersQueue()
    offer = _dialog(queue)
    with pytest.raises(ValueError):
        store.build_dialog_answer_command(
            queue, offer.id, button_label="Explode", now=110.0
        )
    with pytest.raises(ValueError):
        store.build_dialog_answer_command(
            queue, offer.id, button_index=3, now=110.0
        )
    with pytest.raises(ValueError):
        store.build_dialog_answer_command(
            queue, offer.id, button_index=-1, now=110.0
        )
    with pytest.raises(ValueError):
        store.build_dialog_answer_command(queue, offer.id, now=110.0)
    with pytest.raises(ValueError):
        store.build_dialog_answer_command(
            queue, offer.id, button_index=0, button_label="Dance", now=110.0
        )
    assert queue.get(offer.id, now=111.0) is not None


def test_dialog_answer_requires_a_dialog_offer():
    store = _store()
    queue = OffersQueue()
    lure = queue.record("teleport_lure", sender_id=ERIK, now=100.0)
    with pytest.raises(ValueError):
        store.build_dialog_answer_command(
            queue, lure.id, button_index=0, now=110.0
        )
    with pytest.raises(KeyError):
        store.build_dialog_answer_command(
            queue, "offer-999", button_index=0, now=110.0
        )


def test_confirm_dialog_answered_resolves_and_records():
    store = _store()
    queue = OffersQueue()
    offer = _dialog(queue)
    record = store.confirm_dialog_answered(
        queue, offer.id, button_label="Sit", now=120.0
    )
    assert record.offer_id == offer.id
    assert record.object_id == JUKEBOX and record.sender_id == JUKEBOX
    assert (record.button_index, record.button_label) == (1, "Sit")
    closed = queue.history()
    assert len(closed) == 1 and closed[0].status == "answered"
    assert closed[0].resolution_answer == {"index": 1, "label": "Sit"}
    assert [d.offer_id for d in store.dialog_history()] == [offer.id]


def test_confirm_dialog_with_unoffered_button_resolves_nothing():
    store = _store()
    queue = OffersQueue()
    offer = _dialog(queue)
    with pytest.raises(ValueError):
        store.confirm_dialog_answered(
            queue, offer.id, button_label="Explode", now=120.0
        )
    assert queue.get(offer.id, now=121.0) is not None
    assert queue.history() == []
    assert store.dialog_history() == []


# -- animations & gestures -----------------------------------------------------

def test_animation_commands_are_build_only():
    store = _store()
    play = store.build_play_animation_command("anim-wave", "c1")
    assert play.cmd == "play_animation" and play.tier == "mind"
    assert play.params == {"animation": "anim-wave"}
    stop = store.build_stop_animation_command("anim-wave", "c2")
    assert stop.cmd == "stop_animation"
    assert stop.params == {"animation": "anim-wave"}
    assert store.interaction_history() == []
    assert store.playing_animations() == []
    with pytest.raises(ValueError):
        store.build_play_animation_command("  ", "c3")


def test_animation_confirm_records_and_tracks_playing():
    store = _store()
    store.build_play_animation_command("anim-wave", "c1")
    record = store.confirm_animation_started("anim-wave")
    assert record.kind == "animation_play" and record.target == "anim-wave"
    assert store.playing_animations() == ["anim-wave"]
    # A confirmation supplies the animation to the known registry.
    assert [a.identifier for a in store.known_animations()] == ["anim-wave"]
    stopped = store.confirm_animation_stopped("anim-wave")
    assert stopped.kind == "animation_stop"
    assert store.playing_animations() == []
    assert [r.kind for r in store.interaction_history()] == [
        "animation_play", "animation_stop",
    ]


def test_gesture_build_and_confirm():
    store = _store()
    cmd = store.build_play_gesture_command("gesture-bow", "c1")
    assert cmd.cmd == "play_gesture" and cmd.tier == "mind"
    assert cmd.params == {"gesture": "gesture-bow"}
    assert store.interaction_history() == []
    record = store.confirm_gesture_played("gesture-bow")
    assert record.kind == "gesture_play"
    assert [g.identifier for g in store.known_gestures()] == ["gesture-bow"]


def test_playable_registry_holds_only_what_was_supplied():
    store = _store()
    assert store.known_animations() == []
    assert store.known_gestures() == []
    store.note_animation("anim-wave", name="Wave")
    store.note_gesture("gesture-bow")
    assert store.known_animations()[0].name == "Wave"
    assert store.known_gestures()[0].name is None
    with pytest.raises(ValueError):
        store.note_animation("")


def test_animation_and_gesture_events_confirm():
    store = _store()
    started = _ingest(store, "animation_started", "AnimationStarted",
                      animation="anim-spin")
    assert started is not None and started.kind == "animation_play"
    played = _ingest(store, "gesture_played", "GesturePlayed",
                     gesture_id="gesture-bow")
    assert played is not None and played.kind == "gesture_play"
    # A confirmation carrying no identifier fails closed.
    assert _ingest(store, "animation_started", "AnimationStarted") is None
    assert len(store.interaction_history()) == 2


# -- purge ---------------------------------------------------------------------

def test_purge_resident_removes_their_objects_and_records():
    store = _store()
    queue = OffersQueue()
    _jukebox(store)
    store.record_object(CHAIR, "Chair", owner_id=OTHER, sittable=True)
    store.confirm_touch(JUKEBOX)
    store.confirm_touch(CHAIR)
    store.confirm_sit(CHAIR)
    offer = _dialog(queue)
    store.confirm_dialog_answered(queue, offer.id, button_index=0, now=120.0)
    removed = store.purge_resident(ERIK)
    # Jukebox + its touch record + its dialog record.
    assert removed == 3
    assert [o.id for o in store.list_objects()] == [CHAIR]
    assert [(r.kind, r.object_id) for r in store.interaction_history()] == [
        ("touch", CHAIR), ("sit", CHAIR),
    ]
    assert store.dialog_history() == []
    assert store.seated_on() == CHAIR  # not the purged resident's object
    assert store.purge_resident("") == 0
    assert store.purge_resident(ERIK) == 0
