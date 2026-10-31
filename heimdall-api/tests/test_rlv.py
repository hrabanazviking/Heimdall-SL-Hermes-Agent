"""RLV seam — commission (4), both directions (doc 05 §6.4, doc 06).

The load-bearing invariants under test:

- restrictions are source-attributed and applied ONLY from
  allowlisted sources (Volmarr's objects, her own worn items,
  venues opted-in per place); every other source's restriction
  is HELD — recorded with its source and reason, reportable,
  never applied and never silently dropped;
- safety invariants win over everything: a restriction that
  would disable the kill switch, block receiving Volmarr's
  commands, or block logout is refused even from an
  allowlisted (Volmarr) source, fail-closed and audited;
- stack semantics: the same behaviour restricted by multiple
  sources stays active until ALL sources lift it, and a source
  can only lift its own restrictions;
- add/rem parsing fails closed: malformed or empty input is
  audited, changes nothing, and ingest never raises;
- the behaviour gate answers allowed / ``rlv_blocked`` naming
  the behaviour and its source(s);
- felt state always lists active restrictions WITH sources;
- self-use is build-only governor-ready commands (tier
  ``"mind"``), recorded only after Core confirmation;
- ``purge_resident`` removes a source's active AND held
  restrictions.
"""
import pytest

from heimdall.awareness import RlvStore, parse_rlv_rules
from heimdall.core.governor import MessageGovernor
from heimdall.core.models import (
    CORE_EVENT_ALIASES,
    Command,
    SeamEvent,
    canonical_event_name,
)

VOLMARR = "a1111111-1111-4111-8111-111111111111"  # his avatar id
VOLMARR_OBJ = "b2222222-2222-4222-8222-222222222222"  # his object
HUD = "c3333333-3333-4333-8333-333333333333"  # her own worn item
VENUE_OBJ = "d4444444-4444-4444-8444-444444444444"
STRANGER_OBJ = "e5555555-5555-4555-8555-555555555555"
PLACE = "venue-place-1"
OTHER_PLACE = "venue-place-2"


def _store(**kwargs):
    kwargs.setdefault("volmarr_id", VOLMARR)
    kwargs.setdefault("_clock", lambda: 1000.0)
    return RlvStore(**kwargs)


def _ingest(store, event_name, raw, **payload):
    return store.ingest_event(
        SeamEvent(name=event_name, raw_name=raw, payload=payload)
    )


def _applied(store, **payload):
    return _ingest(
        store, "rlv_restriction_applied", "RlvRestrictionApplied", **payload
    )


# -- vocabulary ---------------------------------------------------------

def test_rlv_event_names_are_canonical():
    assert (
        canonical_event_name("RlvRestrictionApplied")
        == "rlv_restriction_applied"
    )
    assert (
        canonical_event_name("RlvRestrictionHeld") == "rlv_restriction_held"
    )
    assert (
        canonical_event_name("RlvRestrictionLifted")
        == "rlv_restriction_lifted"
    )
    for api_form in (
        "rlv_restriction_applied",
        "rlv_restriction_held",
        "rlv_restriction_lifted",
    ):
        assert canonical_event_name(api_form) == api_form


def test_no_duplicate_alias_added_for_rlv():
    for canonical in (
        "rlv_restriction_applied",
        "rlv_restriction_held",
        "rlv_restriction_lifted",
    ):
        forms = [k for k, v in CORE_EVENT_ALIASES.items() if v == canonical]
        assert len(forms) == 2  # Core name + API identity form only


def test_rlv_status_is_a_command_not_an_event():
    # It already exists in the Core's command vocabulary; the
    # event vocabulary must not grow a duplicate meaning for it.
    assert canonical_event_name("rlv_status") is None


def test_seam_event_from_wire_canonicalises_new_names():
    event = SeamEvent.from_wire(
        {"event": "RlvRestrictionApplied", "source_id": VOLMARR_OBJ}
    )
    assert event is not None and event.name == "rlv_restriction_applied"


# -- parsing --------------------------------------------------------------

def test_parse_add_rem_synonyms_and_options():
    rules = parse_rlv_rules("@sendchat=n,tploc=add")
    assert [(r.behaviour, r.action) for r in rules] == [
        ("sendchat", "add"),
        ("tploc", "add"),
    ]
    lift = parse_rlv_rules("@sendchat=y")[0]
    assert lift.action == "lift"
    lift2 = parse_rlv_rules("@sendchat=rem")[0]
    assert lift2.action == "lift"
    optioned = parse_rlv_rules(f"@sendim:{VOLMARR}=add")[0]
    assert optioned.option == VOLMARR and optioned.key == f"sendim:{VOLMARR}"
    assert optioned.canonical == f"@sendim:{VOLMARR}=add"
    # Behaviour names are case-insensitive (doc 06 §2.1).
    assert parse_rlv_rules("@SendChat=n")[0].behaviour == "sendchat"


@pytest.mark.parametrize("bad", [
    "", "   ", "@", "sendchat", "@sendchat", "@sendchat=",
    "@sendchat=force", "@sendchat=4242", "@=n", "@:x=n",
    "@send chat=n", "@sendchat:=n", "@sendchat=n,",
    "@sendchat=n,,@tploc=n", None, 42,
])
def test_parse_malformed_raises(bad):
    with pytest.raises((ValueError, TypeError)):
        parse_rlv_rules(bad)


@pytest.mark.parametrize("bad", [
    "", "@", "@sendchat", "@sendchat=force", "@sendchat=",
])
def test_malformed_apply_fails_closed_into_audit(bad):
    store = _store()
    results = store.apply_restriction(VOLMARR_OBJ, "volmarr", bad)
    assert results[0].outcome == "refused"
    assert store.active_restrictions() == []
    assert len(store.rejected) == 1


def test_malformed_ingest_fails_closed_and_never_raises():
    store = _store()
    assert _applied(store, source_id=VOLMARR_OBJ, source_kind="volmarr",
                    restriction="@sendchat=force") is None
    # The force one-shot is not a rule: nothing applied, audited.
    assert store.active_restrictions() == []
    assert len(store.rejected) >= 1
    # An event with no restriction at all also fails closed.
    assert _applied(store, source_id=VOLMARR_OBJ,
                    source_kind="volmarr") is None
    assert store.active_restrictions() == []
    # An event with no source cannot be filed.
    assert _applied(store, restriction="@sendchat=n",
                    source_kind="volmarr") is None
    assert store.active_restrictions() == []


def test_unknown_event_fails_closed_and_never_raises():
    store = _store()
    assert _ingest(store, "chat", "ChatReceived", msg="hi") is None
    assert store.rejected[-1] == ("not_an_rlv_event", "chat")
    assert store.active_restrictions() == []


# -- allowlist: apply vs hold ----------------------------------------------

def test_volmarr_and_own_worn_sources_apply():
    store = _store()
    r1 = store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n")
    assert r1[0].outcome == "applied"
    r2 = store.apply_restriction(HUD, "own_worn", "@tploc=n")
    assert r2[0].outcome == "applied"
    keys = {r.key for r in store.active_restrictions()}
    assert keys == {"sendchat", "tploc"}
    assert store.held_restrictions() == []


def test_non_allowlisted_source_is_held_not_applied():
    store = _store()
    results = store.apply_restriction(STRANGER_OBJ, "other", "@sendchat=n")
    assert results[0].outcome == "held"
    assert results[0].reason == "source_not_allowlisted"
    # Not applied: the gate is untouched.
    assert store.active_restrictions() == []
    assert store.check("say").allowed is True
    # Held, not dropped: recorded with source and reason.
    held = store.held_restrictions()
    assert len(held) == 1
    assert held[0].source_id == STRANGER_OBJ
    assert held[0].behaviour == "sendchat"
    assert held[0].reason == "source_not_allowlisted"
    assert STRANGER_OBJ in store.describe()


def test_unknown_source_kind_is_held():
    store = _store()
    results = store.apply_restriction(STRANGER_OBJ, "mystery", "@fly=n")
    assert results[0].outcome == "held"
    assert results[0].reason == "unknown_source_kind"
    assert store.active_restrictions() == []


def test_venue_requires_per_place_opt_in():
    store = _store()
    # No opt-in anywhere: held.
    r = store.apply_restriction(
        VENUE_OBJ, "venue_opt_in", "@tploc=n", place_id=PLACE
    )
    assert r[0].outcome == "held" and r[0].reason == "venue_not_opted_in"
    # Opt-in for a DIFFERENT place does not cover this one.
    store.opt_in_place(OTHER_PLACE)
    r = store.apply_restriction(
        VENUE_OBJ, "venue_opt_in", "@tploc=n", place_id=PLACE
    )
    assert r[0].outcome == "held"
    # Opt-in for this place: applied.
    store.opt_in_place(PLACE)
    r = store.apply_restriction(
        VENUE_OBJ, "venue_opt_in", "@tploc=n", place_id=PLACE
    )
    assert r[0].outcome == "applied"
    assert store.is_restricted("tploc") is True
    # A venue claim with no place at all is held.
    r = store.apply_restriction(VENUE_OBJ, "venue_opt_in", "@fly=n")
    assert r[0].outcome == "held"
    # Opting out stops new restrictions from that place landing.
    store.opt_out_place(PLACE)
    r = store.apply_restriction(
        VENUE_OBJ, "venue_opt_in", "@fly=n", place_id=PLACE
    )
    assert r[0].outcome == "held"


def test_chain_from_allowlisted_source_applies_each_rule():
    store = _store()
    results = store.apply_restriction(
        VOLMARR_OBJ, "volmarr", "@tploc=n,tplm=n,unsit=n"
    )
    assert [r.outcome for r in results] == ["applied"] * 3
    assert {r.key for r in store.active_restrictions()} == {
        "tploc", "tplm", "unsit",
    }


# -- safety invariants --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "@logout=n", "@logoff=n", "@disconnect=n", "@killswitch=n",
    "@kill_switch=n",
    "@recvim=n", "@recvchat=n", "@recvemote=n",
    "@recvim_sec=n", "@recvchat_sec=n",
])
def test_safety_invariants_refused_even_from_volmarr(text):
    store = _store()
    results = store.apply_restriction(VOLMARR_OBJ, "volmarr", text)
    assert results[0].outcome == "refused"
    assert results[0].reason.startswith("safety_invariant")
    assert store.active_restrictions() == []
    assert len(store.rejected) == 1
    # And from her own worn items too — the invariant is source-blind.
    results = store.apply_restriction(HUD, "own_worn", text)
    assert results[0].outcome == "refused"


def test_volmarr_exception_form_on_receive_behaviour_is_allowed():
    # Doc 06 Pattern 1: @recvim:<Volmarr>=add KEEPS his access;
    # it is the one receive-family form the gate allows.
    store = _store()
    results = store.apply_restriction(
        HUD, "own_worn", f"@recvim:{VOLMARR}=add"
    )
    assert results[0].outcome == "applied"
    # It is an exception record, not a gag: IMs are not blocked.
    assert store.check("im").allowed is True


def test_receive_exception_for_a_third_party_is_refused():
    store = _store()
    results = store.apply_restriction(
        VOLMARR_OBJ, "volmarr", f"@recvim:{STRANGER_OBJ}=add"
    )
    assert results[0].outcome == "refused"
    assert results[0].reason == "safety_invariant_volmarr_reachability"


def test_detach_of_a_protected_control_attachment_is_refused():
    store = _store(protected_ids=("control-hud-9",))
    results = store.apply_restriction(
        VOLMARR_OBJ, "volmarr", "@detach:control-hud-9=n"
    )
    assert results[0].outcome == "refused"
    assert results[0].reason == "safety_invariant_protected_attachment"
    # Ordinary detach locks from Volmarr are still his channel.
    ok = store.apply_restriction(VOLMARR_OBJ, "volmarr", "@detach=n")
    assert ok[0].outcome == "applied"


# -- stack semantics ------------------------------------------------------------

def test_stack_stays_active_until_all_sources_lift():
    store = _store()
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n")
    store.apply_restriction(HUD, "own_worn", "@sendchat=n")
    assert store.check("say").allowed is False
    # One source lifts: the other still holds it.
    lifted = store.lift_restriction(VOLMARR_OBJ, "@sendchat=rem")
    assert lifted[0].outcome == "lifted"
    assert store.is_restricted("sendchat") is True
    gate = store.check("say")
    assert gate.allowed is False and gate.source_ids == (HUD,)
    # The last source lifts: free.
    store.lift_restriction(HUD, "@sendchat=rem")
    assert store.is_restricted("sendchat") is False
    assert store.check("say").allowed is True


def test_a_source_can_only_lift_its_own():
    store = _store()
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n")
    results = store.lift_restriction(HUD, "@sendchat=rem")
    assert results[0].outcome == "not_lifted"
    assert results[0].reason == "source_does_not_hold_this_restriction"
    assert store.is_restricted("sendchat") is True
    # Lifting something nobody holds is also a clean no-op audit.
    results = store.lift_restriction(VOLMARR_OBJ, "@fly=rem")
    assert results[0].outcome == "not_lifted"
    assert results[0].reason == "restriction_not_active"


def test_lift_form_inside_apply_string_lifts():
    store = _store()
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@tploc=n")
    results = store.apply_restriction(VOLMARR_OBJ, "volmarr", "@tploc=y")
    assert results[0].outcome == "lifted"
    assert store.active_restrictions() == []


def test_clear_source_removes_only_that_sources_stack():
    store = _store()
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n,tploc=n")
    store.apply_restriction(HUD, "own_worn", "@sendchat=n")
    assert store.clear_source(VOLMARR_OBJ) == 2
    remaining = store.active_restrictions()
    assert len(remaining) == 1 and remaining[0].source_id == HUD
    assert store.clear_source(VOLMARR_OBJ) == 0
    assert store.clear_source("") == 0


# -- the behaviour gate -------------------------------------------------------------

def test_gate_blocks_and_allows_with_source_named():
    store = _store()
    assert store.check("say").allowed is True
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n")
    gate = store.check("say")
    assert gate.allowed is False
    assert gate.blocked_reason == "rlv_blocked"
    assert gate.behaviour == "sendchat"
    assert gate.source_id == VOLMARR_OBJ
    assert gate.source_ids == (VOLMARR_OBJ,)
    assert gate.source_kinds == ("volmarr",)
    # The behaviour name itself gates too.
    assert store.check("sendchat").allowed is False
    # Unrelated actions stay allowed.
    assert store.check("im").allowed is True
    assert store.check("teleport").allowed is True


def test_gate_maps_actions_to_behaviours():
    store = _store()
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@tploc=n")
    gate = store.check("teleport")
    assert gate.allowed is False and gate.behaviour == "tploc"
    store.apply_restriction(HUD, "own_worn", "@unsit=n")
    assert store.check("stand").allowed is False
    store.apply_restriction(HUD, "own_worn", "@sendim=n")
    assert store.check("im").allowed is False
    assert store.check("group_im").allowed is False


def test_gate_never_blocks_logout_or_kill_switch():
    store = _store()
    assert store.check("logout").allowed is True
    assert store.check("kill_switch").allowed is True
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n")
    assert store.check("logout").allowed is True


def test_held_restriction_does_not_gate():
    store = _store()
    store.apply_restriction(STRANGER_OBJ, "other", "@sendchat=n")
    assert store.check("say").allowed is True


# -- felt visibility -------------------------------------------------------------------

def test_describe_and_state_always_name_sources():
    store = _store()
    assert store.describe() == "No RLV restrictions are active."
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n")
    store.apply_restriction(HUD, "own_worn", "@tploc=n")
    store.apply_restriction(STRANGER_OBJ, "other", "@fly=n")
    text = store.describe()
    assert "sendchat" in text and VOLMARR_OBJ in text
    assert "tploc" in text and HUD in text
    assert "fly" in text and STRANGER_OBJ in text  # held, reported
    state = store.current_state()
    assert state.active_count == 2 and state.held_count == 1
    by_key = {r.key: r for r in state.restrictions}
    assert by_key["sendchat"].source_id == VOLMARR_OBJ
    assert by_key["sendchat"].source_kind == "volmarr"
    assert by_key["tploc"].source_id == HUD
    assert state.held[0].source_id == STRANGER_OBJ


# -- self-use: build-only, confirm-only ---------------------------------------------------

def test_self_use_build_is_governor_ready_and_records_nothing():
    store = _store()
    cmd = store.build_apply_command("@sendchat=n", "c1")
    assert cmd.cmd == "rlv_apply" and cmd.tier == "mind"
    assert cmd.params["restriction"] == "@sendchat=add"
    assert cmd.params["behaviour"] == "sendchat"
    # Building changed nothing.
    assert store.active_restrictions() == []
    assert store.self_history() == []
    # The governor lets the self-use commands through.
    governor = MessageGovernor()
    assert governor.check(cmd).allowed is True
    lift = store.build_lift_command("sendchat", "c2")
    assert lift.cmd == "rlv_lift" and lift.tier == "mind"
    assert lift.params["restriction"] == "@sendchat=rem"
    assert governor.check(lift).allowed is True
    status = store.build_status_command("c3")
    assert status.cmd == "rlv_status" and status.params == {}
    assert governor.check(status).allowed is True
    assert store.active_restrictions() == []


def test_self_use_recorded_only_after_confirmation():
    store = _store()
    store.build_apply_command("@sendchat=n", "c1")
    assert store.is_restricted("sendchat") is False
    record = store.confirm_applied("@sendchat=n")
    assert record.source_kind == "own_worn"
    assert record.source_id == store.self_source_id
    assert store.is_restricted("sendchat") is True
    assert store.check("say").allowed is False
    assert [h.kind for h in store.self_history()] == ["apply"]
    lifted = store.confirm_lifted("sendchat")
    assert lifted.kind == "lift" and lifted.key == "sendchat"
    assert store.is_restricted("sendchat") is False
    assert [h.kind for h in store.self_history()] == ["apply", "lift"]
    # A confirmation for a lift that never landed fails closed.
    with pytest.raises(ValueError):
        store.confirm_lifted("sendchat")


def test_self_use_build_and_confirm_refuse_safety_violations():
    store = _store()
    with pytest.raises(ValueError):
        store.build_apply_command("@logout=n", "c1")
    with pytest.raises(ValueError):
        store.build_apply_command("@recvim=n", "c2")
    with pytest.raises(ValueError):
        store.confirm_applied("@killswitch=n")
    assert store.active_restrictions() == []
    # Wrong-form commands fail closed too.
    with pytest.raises(ValueError):
        store.build_apply_command("@sendchat=rem", "c3")
    with pytest.raises(ValueError):
        store.build_lift_command("@sendchat=n", "c4")


def test_self_use_commands_are_not_governor_money_or_speech():
    store = _store()
    governor = MessageGovernor()
    for cmd in (
        store.build_apply_command("tploc", "a"),
        store.build_lift_command("tploc", "b"),
        store.build_status_command("c"),
    ):
        decision = governor.check(cmd)
        assert decision.allowed is True, (cmd.cmd, decision.reason)
    # Reflex tiers may not speak — and self-use is never reflex.
    reflex = Command(id="r", cmd="rlv_apply", params={}, tier="reflex")
    assert governor.check(reflex).allowed is True  # not a speech cmd


# -- ingest --------------------------------------------------------------------------------

def test_ingest_applied_gates_like_the_direct_api():
    store = _store()
    results = _applied(
        store, source_id=VENUE_OBJ, source_kind="venue_opt_in",
        restriction="@sit=n", place_id=PLACE,
    )
    assert results[0].outcome == "held"  # venue not opted in yet
    store.opt_in_place(PLACE)
    results = _applied(
        store, source_id=VENUE_OBJ, source_kind="venue_opt_in",
        restriction="@sit=n", place_id=PLACE,
    )
    assert results[0].outcome == "applied"
    assert store.check("sit").allowed is False
    results = _applied(
        store, source_id=STRANGER_OBJ, source_kind="other",
        restriction="@fly=n",
    )
    assert results[0].outcome == "held"
    assert store.is_restricted("fly") is False


def test_ingest_held_event_records_the_hold():
    store = _store()
    held = _ingest(
        store, "rlv_restriction_held", "RlvRestrictionHeld",
        source_id=STRANGER_OBJ, source_kind="other",
        restriction="@sendchat=n", reason="source_not_allowlisted",
    )
    assert len(held) == 1 and held[0].source_id == STRANGER_OBJ
    assert store.active_restrictions() == []
    assert store.held_restrictions()[0].reason == "source_not_allowlisted"


def test_ingest_lifted_lifts_only_that_source():
    store = _store()
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n")
    store.apply_restriction(HUD, "own_worn", "@sendchat=n")
    results = _ingest(
        store, "rlv_restriction_lifted", "RlvRestrictionLifted",
        source_id=HUD, restriction="@sendchat=rem",
    )
    assert results[0].outcome == "lifted"
    gate = store.check("say")
    assert gate.allowed is False and gate.source_ids == (VOLMARR_OBJ,)


def test_ingest_behaviour_form_and_chain_forms():
    store = _store()
    results = _applied(
        store, source_id=VOLMARR_OBJ, source_kind="volmarr",
        behaviour="tploc",
    )
    assert results[0].outcome == "applied"
    results = _applied(
        store, source_id=VOLMARR_OBJ, source_kind="volmarr",
        restrictions=["@tplm=n", {"behaviour": "fly"}],
    )
    assert [r.outcome for r in results] == ["applied", "applied"]
    assert {r.key for r in store.active_restrictions()} == {
        "tploc", "tplm", "fly",
    }


# -- purge -----------------------------------------------------------------------------------

def test_purge_resident_removes_active_and_held():
    store = _store()
    store.apply_restriction(VENUE_OBJ, "other", "@fly=n")  # held
    store.opt_in_place(PLACE)
    store.apply_restriction(
        VENUE_OBJ, "venue_opt_in", "@tploc=n", place_id=PLACE
    )  # applied
    store.apply_restriction(VOLMARR_OBJ, "volmarr", "@sendchat=n")
    removed = store.purge_resident(VENUE_OBJ)
    assert removed == 2  # one active + one held
    assert store.held_restrictions() == []
    assert {r.key for r in store.active_restrictions()} == {"sendchat"}
    assert store.purge_resident(VENUE_OBJ) == 0
    assert store.purge_resident("") == 0
