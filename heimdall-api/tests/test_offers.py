"""F1 — pending-offers queue (doc 03 §6, census doc 15 slice F1).

The load-bearing invariants under test:

- offers are held hold-and-show: nothing auto-resolves, ever;
- unknown kinds and senderless offers fail closed, with an audit
  trail in ``rejected`` when ingested as events;
- expiry is loud: expired offers land in history as ``expired`` and
  can no longer be resolved;
- accepting a script *debit* permission without operator approval
  is refused (doc 05 payments default-deny), declining never is;
- building a resolution command does NOT resolve the offer — only
  an explicit resolve after Core confirmation does (the governor's
  record-after-confirmation pattern).
"""
import pytest
from heimdall.api.event_dispatcher import EventDispatcher
from heimdall.awareness import OFFER_KINDS, OffersQueue
from heimdall.core.models import SeamEvent, canonical_event_name

ERIK = "c3333333-3333-4333-8333-333333333333"
OBJ = "a1111111-1111-4111-8111-111111111111"


def _queue():
    return OffersQueue(default_ttl_s=900.0)


# -- vocabulary ---------------------------------------------------------

def test_offer_event_names_are_canonical():
    assert canonical_event_name("OfferReceived") == "offer_received"
    assert canonical_event_name("TeleportLureReceived") == "teleport_lure"
    assert canonical_event_name("FriendshipOfferReceived") == "friendship_offer"
    assert canonical_event_name("GroupInvitationReceived") == "group_invite"
    assert canonical_event_name("InventoryOfferReceived") == "inventory_offer"
    assert canonical_event_name("CallingCardOfferReceived") == "calling_card_offer"
    assert canonical_event_name("ScriptDialogReceived") == "script_dialog"
    assert canonical_event_name("ScriptPermissionRequest") == "script_permission_request"
    assert canonical_event_name("offer_received") == "offer_received"


def test_all_kinds_recordable():
    q = _queue()
    for kind in sorted(OFFER_KINDS):
        offer = q.record(kind, sender_id=ERIK, sender_name="Erik", now=100.0)
        assert offer.status == "pending"
    assert len(q.pending(now=101.0)) == len(OFFER_KINDS)


# -- hold-and-show --------------------------------------------------------

def test_pending_is_oldest_first_and_nothing_auto_resolves():
    q = _queue()
    q.record("teleport_lure", sender_id=ERIK, now=100.0)
    q.record("friendship", sender_id=ERIK, now=200.0)
    held = q.pending(now=250.0)
    assert [o.kind for o in held] == ["teleport_lure", "friendship"]
    assert all(o.status == "pending" for o in held)
    assert q.history() == []


def test_unknown_kind_fails_closed():
    q = _queue()
    with pytest.raises(ValueError):
        q.record("partner_proposal", sender_id=ERIK, now=100.0)
    with pytest.raises(ValueError):
        q.record("friendship", sender_id="", now=100.0)


# -- event ingest -----------------------------------------------------------

def test_ingest_per_kind_and_generic_events():
    q = _queue()
    lure = q.ingest_event(
        SeamEvent(name="teleport_lure", raw_name="TeleportLureReceived",
                  payload={"sender_id": ERIK, "sender_name": "Erik"})
    )
    generic = q.ingest_event(
        SeamEvent(name="offer_received", raw_name="OfferReceived",
                  payload={"kind": "group_invite", "sender_id": ERIK})
    )
    assert lure is not None and lure.kind == "teleport_lure"
    assert generic is not None and generic.kind == "group_invite"
    assert len(q.pending()) == 2


def test_ingest_bad_offer_is_rejected_not_queued():
    q = _queue()
    bad_kind = q.ingest_event(
        SeamEvent(name="offer_received", raw_name="OfferReceived",
                  payload={"kind": "mystery", "sender_id": ERIK})
    )
    no_sender = q.ingest_event(
        SeamEvent(name="teleport_lure", raw_name="TeleportLureReceived", payload={})
    )
    assert bad_kind is None and no_sender is None
    assert q.pending() == []
    assert len(q.rejected) == 2


def test_dispatcher_wires_offer_events_into_the_queue():
    q = _queue()
    dispatcher = EventDispatcher()
    for name in ("offer_received", "teleport_lure", "friendship_offer",
                 "group_invite", "inventory_offer", "calling_card_offer",
                 "script_dialog", "script_permission_request"):
        dispatcher.subscribe(name, q.ingest_event)
    event = dispatcher.dispatch_wire(
        {"event": "InventoryOfferReceived", "sender_id": ERIK,
         "sender_name": "Erik", "item_id": "item-1"}
    )
    assert event is not None and event.name == "inventory_offer"
    held = q.pending()
    assert len(held) == 1 and held[0].payload["item_id"] == "item-1"
    assert dispatcher.dead_letters == [] and dispatcher.handler_errors == []


# -- expiry -------------------------------------------------------------------

def test_expiry_moves_to_history_and_blocks_resolution():
    q = _queue()
    offer = q.record("teleport_lure", sender_id=ERIK, now=100.0)  # kind TTL 300 s
    assert q.pending(now=399.0) == [offer]
    assert q.pending(now=401.0) == []
    hist = q.history()
    assert len(hist) == 1 and hist[0].status == "expired"
    with pytest.raises(KeyError):
        q.resolve(offer.id, "accept", now=402.0)


def test_payload_expires_in_overrides_kind_ttl():
    q = _queue()
    q.record("friendship", sender_id=ERIK, now=100.0,
             payload={"expires_in_s": 10})
    assert q.pending(now=109.0) != []
    assert q.pending(now=111.0) == []


# -- resolving ------------------------------------------------------------------

def test_accept_and_decline_resolve_once():
    q = _queue()
    a = q.record("friendship", sender_id=ERIK, now=100.0)
    done = q.resolve(a.id, "accept", now=110.0)
    assert done.status == "accepted"
    assert q.pending(now=111.0) == []
    with pytest.raises(KeyError):
        q.resolve(a.id, "decline", now=112.0)
    b = q.record("group_invite", sender_id=ERIK, now=120.0)
    assert q.resolve(b.id, "decline", now=121.0).status == "declined"
    assert [o.status for o in q.history()] == ["accepted", "declined"]


def test_script_dialog_is_answered_not_accepted():
    q = _queue()
    d = q.record("script_dialog", sender_id=OBJ, now=100.0,
                 payload={"channel": -42, "buttons": ["Sit", "Leave"]})
    with pytest.raises(ValueError):
        q.resolve(d.id, "accept", now=101.0)
    with pytest.raises(ValueError):
        q.resolve(d.id, "answer", now=101.0)  # no answer given
    done = q.resolve(d.id, "answer", answer="Sit", now=102.0)
    assert done.status == "answered" and done.resolution_answer == "Sit"
    plain = q.record("friendship", sender_id=ERIK, now=103.0)
    with pytest.raises(ValueError):
        q.resolve(plain.id, "answer", answer="yes", now=104.0)


def test_debit_permission_accept_needs_operator_approval():
    q = _queue()
    p = q.record("script_permission", sender_id=OBJ, now=100.0,
                 payload={"permissions": ["animate", "debit"]})
    with pytest.raises(PermissionError):
        q.resolve(p.id, "accept", now=101.0)
    assert q.get(p.id, now=102.0) is p  # refusal leaves it held, not resolved
    assert q.resolve(p.id, "decline", now=103.0).status == "declined"

    q2 = _queue()
    p2 = q2.record("script_permission", sender_id=OBJ, now=100.0,
                   payload={"permissions": ["debit"]})
    ok = q2.resolve(p2.id, "accept", operator_approved=True, now=101.0)
    assert ok.status == "accepted"

    q3 = _queue()
    p3 = q3.record("script_permission", sender_id=OBJ, now=100.0,
                   payload={"permissions": ["animate", "attach"]})
    assert q3.resolve(p3.id, "accept", now=101.0).status == "accepted"


# -- resolution commands ---------------------------------------------------------

def test_build_resolution_command_does_not_resolve():
    q = _queue()
    offer = q.record("inventory", sender_id=ERIK, now=100.0,
                     payload={"item_id": "item-9"})
    cmd = q.build_resolution_command(offer.id, "accept", now=101.0)
    assert cmd.cmd == "offer_respond" and cmd.tier == "mind"
    assert cmd.params["offer_id"] == offer.id
    assert cmd.params["kind"] == "inventory"
    assert cmd.params["decision"] == "accept"
    assert cmd.params["sender_id"] == ERIK
    assert cmd.params["item_id"] == "item-9"
    # Still held: resolution happens only after Core confirms.
    assert q.get(offer.id, now=102.0) is offer
    with pytest.raises(KeyError):
        q.build_resolution_command("offer-999", "accept", now=102.0)
