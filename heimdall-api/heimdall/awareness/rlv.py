"""RLV restriction store and gate — commission (4), both directions.

RLV (doc 06) is the in-world control channel into her body:
scripts drive and restrict the avatar through commands of the
form ``@behaviour[:option]=add/rem``. Doc 05 §6.4 is the build
law for this slice, and it is enforced here, in the body, not
by convention:

- **Restrictions are source-attributed and allowlisted.**
  Every restriction carries its source (an id plus a kind:
  ``volmarr`` / ``own_worn`` / ``venue_opt_in`` / ``other``).
  A restriction is APPLIED only from an allowlisted source:
  Volmarr's objects, her own worn items, and venues explicitly
  opted-in per place (see :meth:`RlvStore.opt_in_place`). A
  restriction from any other source is HELD — not applied, not
  silently dropped: it is recorded in the held audit, with its
  source and the reason, and is reportable through
  :meth:`RlvStore.held_restrictions`, :meth:`RlvStore.current_state`
  and :meth:`RlvStore.describe`.
- **Safety invariants win over everything.** A restriction
  that would disable the kill switch, block her receiving
  Volmarr's commands, or block logout is REFUSED even from an
  allowlisted source — fail closed, and audited in
  ``rejected``. Concretely: the logout / kill-switch behaviours
  themselves can never be restricted; a plain receive-family
  restriction (``recvchat`` / ``recvemote`` / ``recvim`` and
  their ``_sec`` twins) is refused unless it is the exception
  form naming Volmarr (``@recvim:<Volmarr>=add`` — the form
  that *keeps* his access, doc 06 Pattern 1); and detaching a
  configured protected control attachment is refused.
- **Stack semantics (doc 06 §2.1).** Rules are keyed by the
  emitting source: the same behaviour restricted by several
  sources stays active until ALL of them lift it, and a source
  can only lift its own restrictions — there is no cross-source
  clear (``@clear`` is per-emitter in the protocol too).
- **The behaviour gate.** :meth:`RlvStore.check` answers for
  an outbound action: allowed, or blocked with
  ``blocked_reason == "rlv_blocked"`` — the Core refusal
  language the command router already passes through verbatim —
  naming the behaviour and the source(s) holding it. Logout
  and the kill switch are never gate-blocked (defence in
  depth: such restrictions are refused at apply time, and the
  gate hard-allows those actions regardless).
- **Felt visibility.** Active restrictions are always listed
  WITH their sources — a restriction can never hide its
  source (doc 05 §6.4; the protocol's own ``@notify`` stream
  hides sources from scripts, but the body's own stack does
  not hide them from her).

Both directions (commission 4):

- **Inbound** is the allowlist gate above: Volmarr (and her
  own worn items, and opted-in venues) controlling her avatar.
- **Outbound / self-use** is build-only: this store builds
  governor-ready commands (tier ``"mind"``) for her applying
  or lifting RLV on her OWN avatar — ``rlv_apply`` /
  ``rlv_lift`` over the Core's RLV channel, plus the read-only
  ``rlv_status`` request the Core already answers — and a
  self restriction is recorded only after Core confirmation
  (:meth:`RlvStore.confirm_applied` /
  :meth:`RlvStore.confirm_lifted`, or the matching
  confirmation events), the record-after-confirmation pattern
  of F3/F6/F7. No command is ever executed by the store
  itself; building a command changes no state.

Parsing: ``@behaviour[:option]=add/rem`` (``=n`` is a synonym
of ``=add``, ``=y`` of ``=rem``, per doc 06 §2.1), comma-chains
in one string, behaviour names case-insensitive. Anything
malformed or empty — no ``@``, no ``=``, an unknown parameter
such as ``force`` or a channel number (one-shots are not
rules and are not stored here), an empty behaviour — fails
closed into the audit; ingest never raises.

Scope honesty: this is the API/seam side only. The C# Core
answers ``rlv_status`` today with an honest
``engine: not_in_this_slice`` and does not yet emit
restriction applied/held/lifted events, does not yet answer
``rlv_apply`` / ``rlv_lift``, and has no relay listener or
LibreMetaverse.RLV engine attached — that Core leg is a
purplelongship slice. The event names added to the seam
vocabulary in ``core/models.py`` are the contract that leg
will speak. Until it lands, restrictions, holds, and self-use
records can be held, gated, and queried entirely on this
side, and every self-use command is ready to route the moment
Core can confirm it.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.models import Command, SeamEvent

# Canonical seam event names this store ingests — the RLV
# names added to core/models.py with this slice. (``rlv_status``
# already exists as a Core *command*, not an event, and is not
# duplicated in the event vocabulary.)
RLV_EVENTS = frozenset(
    {
        "rlv_restriction_applied",
        "rlv_restriction_held",
        "rlv_restriction_lifted",
    }
)

# Source kinds a restriction may be attributed to.
SOURCE_KINDS = frozenset({"volmarr", "own_worn", "venue_opt_in", "other"})
# Kinds trusted by default. ``venue_opt_in`` is trusted only
# for a place that has actually been opted into (per place).
_TRUSTED_KINDS = frozenset({"volmarr", "own_worn"})

# Doc 06 §2.1 parameter synonyms: =n is =add, =y is =rem.
_ADD_PARAMS = frozenset({"add", "n"})
_LIFT_PARAMS = frozenset({"rem", "y"})

_BEHAVIOUR_RE = re.compile(r"^[a-z0-9_]+$")

# Behaviours that can never be restricted at all: restricting
# them would disable logout or the kill switch (doc 05 §6.4).
_NEVER_RESTRICT = frozenset(
    {"logout", "logoff", "disconnect", "killswitch", "kill_switch"}
)
# Receive-family behaviours: a plain restriction here blocks
# her receiving chat/IMs — including Volmarr's commands — and
# is refused unless it is the exception form naming Volmarr.
_RECEIVE_BEHAVIOURS = frozenset(
    {
        "recvchat",
        "recvemote",
        "recvim",
        "recvchat_sec",
        "recvemote_sec",
        "recvim_sec",
    }
)
# Actions the gate hard-allows regardless of the stack.
_NEVER_GATED = frozenset({"logout", "logoff", "kill_switch", "killswitch"})

# Outbound action -> RLV behaviours whose plain (unoptioned)
# restriction blocks it. An action that is itself a behaviour
# name is also checked directly (see RlvStore.check).
ACTION_BEHAVIOURS: dict[str, tuple[str, ...]] = {
    "say": ("sendchat",),
    "chat": ("sendchat",),
    "im": ("sendim", "startim"),
    "send_im": ("sendim", "startim"),
    "group_im": ("sendim",),
    "group_chat": ("sendim",),
    "teleport": ("tploc", "tplm", "tplure", "tplocal"),
    "sit": ("sit", "interact"),
    "sit_on_object": ("sit", "interact"),
    "stand": ("unsit",),
    "fly": ("fly",),
    "detach": ("detach",),
    "detach_item": ("detach",),
    "attach": ("addattach",),
    "attach_item": ("addattach",),
    "touch": ("touchall", "interact"),
    "touch_object": ("touchall", "interact"),
    "edit": ("edit", "interact"),
    "rez": ("rez", "interact"),
}


@dataclass
class ParsedRlvRule:
    """One parsed ``@behaviour[:option]=add/rem`` rule."""

    behaviour: str
    option: str | None
    action: str  # "add" (restrict) | "lift" (the =rem/=y form)
    raw: str

    @property
    def key(self) -> str:
        """The stack key: behaviour, or behaviour:option."""
        if self.option is None:
            return self.behaviour
        return f"{self.behaviour}:{self.option}"

    @property
    def canonical(self) -> str:
        """The rule in canonical @-form, e.g. ``@sendchat=add``."""
        param = "add" if self.action == "add" else "rem"
        return f"@{self.key}={param}"


def parse_rlv_rules(text: Any) -> list[ParsedRlvRule]:
    """Parse an RLV command string into rules. Raises ValueError.

    Accepts one rule or a comma-separated chain under a single
    leading ``@`` (doc 06 §2.1). Malformed or empty input —
    missing ``@``, missing ``=``, an unknown parameter
    (``force`` / a channel number: one-shots are not rules), an
    empty or invalid behaviour, an empty option — raises, so
    callers can fail closed into their audit.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("an RLV command string must be non-empty text")
    stripped = text.strip()
    if not stripped.startswith("@"):
        raise ValueError("an RLV command string must start with @")
    body = stripped[1:]
    if not body.strip():
        raise ValueError("an RLV command string carries no rule")
    rules: list[ParsedRlvRule] = []
    for part in body.split(","):
        token = part.strip()
        if not token or "=" not in token:
            raise ValueError(f"malformed RLV rule {part!r}")
        lhs, _, param_raw = token.partition("=")
        param = param_raw.strip().lower()
        if param in _ADD_PARAMS:
            action = "add"
        elif param in _LIFT_PARAMS:
            action = "lift"
        else:
            raise ValueError(
                f"RLV rule {token!r} has a non-rule parameter "
                f"{param_raw!r} (only add/rem rules are stored)"
            )
        behaviour_raw, colon, option_raw = lhs.partition(":")
        behaviour = behaviour_raw.strip().lower()
        if not _BEHAVIOUR_RE.match(behaviour):
            raise ValueError(f"malformed RLV behaviour {behaviour_raw!r}")
        option: str | None = None
        if colon:
            option = option_raw.strip()
            if not option:
                raise ValueError(f"RLV rule {token!r} has an empty option")
        rules.append(
            ParsedRlvRule(
                behaviour=behaviour, option=option, action=action, raw=token
            )
        )
    return rules


@dataclass
class RlvRestriction:
    """One active restriction, with the source that set it."""

    behaviour: str
    option: str | None
    source_id: str
    source_kind: str
    applied_at: float
    place_id: str | None = None
    raw: str = ""

    @property
    def key(self) -> str:
        if self.option is None:
            return self.behaviour
        return f"{self.behaviour}:{self.option}"


@dataclass
class HeldRestriction:
    """One held (not applied) restriction, with source + reason."""

    behaviour: str
    option: str | None
    source_id: str
    source_kind: str
    reason: str
    held_at: float
    place_id: str | None = None
    raw: str = ""

    @property
    def key(self) -> str:
        if self.option is None:
            return self.behaviour
        return f"{self.behaviour}:{self.option}"


@dataclass
class RlvApplyResult:
    """The gate's verdict for one rule from one source."""

    outcome: str  # applied | held | refused | lifted | not_lifted
    behaviour: str
    key: str
    source_id: str
    reason: str = ""


@dataclass
class GateResult:
    """The behaviour gate's answer for one outbound action."""

    allowed: bool
    action: str
    blocked_reason: str = ""  # "rlv_blocked" when blocked
    behaviour: str | None = None
    key: str | None = None
    source_ids: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ()

    @property
    def source_id(self) -> str | None:
        """The first (primary) blocking source, or None."""
        return self.source_ids[0] if self.source_ids else None


@dataclass
class SelfUseRecord:
    """One Core-confirmed self-use apply/lift, in order."""

    kind: str  # apply | lift
    key: str
    timestamp: float


@dataclass
class RlvState:
    """A snapshot of the RLV state, honest about holds too."""

    restrictions: list[RlvRestriction]
    held: list[HeldRestriction]
    opted_in_places: list[str]

    @property
    def active_count(self) -> int:
        return len(self.restrictions)

    @property
    def held_count(self) -> int:
        return len(self.held)


@dataclass
class RlvStore:
    """The RLV store/gate: ingest, allowlist, check, self-use."""

    volmarr_id: str | None = None
    protected_ids: tuple[str, ...] = ()
    self_source_id: str = "self"
    _clock: Any = field(default=time.time)
    # key -> {source_id: restriction}: the doc 06 stack.
    _active: dict[str, dict[str, RlvRestriction]] = field(
        default_factory=dict, init=False
    )
    _held: list[HeldRestriction] = field(default_factory=list, init=False)
    _opted_in: set[str] = field(default_factory=set, init=False)
    _self_history: list[SelfUseRecord] = field(
        default_factory=list, init=False
    )
    # Fail-closed audit: (reason, detail) for every refusal.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else float(now)

    # -- venue opt-in -------------------------------------------------
    def opt_in_place(self, place_id: str) -> str:
        """Opt in, per place, to a venue's restriction source.

        Only after this may a ``venue_opt_in`` source naming
        this place have restrictions applied; the opt-in is
        per place, never global."""
        key = str(place_id or "").strip()
        if not key:
            raise ValueError("a venue opt-in needs a place id")
        self._opted_in.add(key)
        return key

    def opt_out_place(self, place_id: str) -> bool:
        """Withdraw a place's opt-in. Restrictions that venue
        already applied stay until lifted or cleared — opting
        out stops new ones landing, it does not rewrite the
        stack."""
        key = str(place_id or "").strip()
        if key in self._opted_in:
            self._opted_in.discard(key)
            return True
        return False

    def opted_in_places(self) -> list[str]:
        return sorted(self._opted_in)

    # -- classification ------------------------------------------------
    def _effective_kind(self, source_id: str, source_kind: str) -> str:
        """The kind the gate acts on. A configured Volmarr id or
        the self id classifies by identity, whatever label the
        caller attached — identity wins over labels."""
        if self.volmarr_id is not None and source_id == str(self.volmarr_id):
            return "volmarr"
        if source_id == str(self.self_source_id):
            return "own_worn"
        return source_kind

    def _allowlist_reason(
        self, source_id: str, source_kind: str, place_id: str | None
    ) -> str | None:
        """None when the source is allowlisted for this place,
        else the reason the restriction must be held."""
        kind = self._effective_kind(source_id, source_kind)
        if kind in _TRUSTED_KINDS:
            return None
        if kind == "venue_opt_in":
            if place_id is not None and str(place_id) in self._opted_in:
                return None
            return "venue_not_opted_in"
        if kind not in SOURCE_KINDS:
            return "unknown_source_kind"
        return "source_not_allowlisted"

    def _safety_refusal(self, rule: ParsedRlvRule) -> str | None:
        """The doc 05 §6.4 invariants, checked before the
        allowlist: a reason string when the rule must be refused
        from EVERY source, allowlisted or not."""
        if rule.behaviour in _NEVER_RESTRICT:
            return "safety_invariant_protected_behaviour"
        if rule.behaviour in _RECEIVE_BEHAVIOURS:
            # The exception form naming Volmarr keeps his access
            # (doc 06 Pattern 1) and is the only receive rule
            # allowed; every other receive rule could block her
            # receiving Volmarr's commands.
            if (
                self.volmarr_id is not None
                and rule.option is not None
                and rule.option == str(self.volmarr_id)
            ):
                return None
            return "safety_invariant_volmarr_reachability"
        if (
            rule.behaviour == "detach"
            and rule.option is not None
            and rule.option in {str(p) for p in self.protected_ids}
        ):
            return "safety_invariant_protected_attachment"
        return None

    # -- the inbound gate: apply / lift --------------------------------
    def apply_restriction(
        self,
        source_id: str,
        source_kind: str,
        restriction: Any,
        *,
        param: str = "add",
        place_id: str | None = None,
        now: float | None = None,
    ) -> list[RlvApplyResult]:
        """Gate one restriction (or @-chain) from one source.

        ``restriction`` is an RLV string (``"@sendchat=n"``, a
        comma-chain, or a lift form ``"@sendchat=rem"``) or a
        bare behaviour / ``behaviour:option`` name, in which
        case ``param`` (``add``/``rem``) says which. Never
        raises: a malformed string fails closed into
        ``rejected`` and returns a ``refused`` result; a
        safety-violating rule is refused and audited; a
        non-allowlisted source's rule is held and audited in
        the held list; only an allowlisted, safe ``add`` lands
        on the stack.
        """
        sid = "" if source_id is None else str(source_id)
        if not sid.strip():
            self.rejected.append(("restriction_without_a_source", restriction))
            return [
                RlvApplyResult(
                    outcome="refused",
                    behaviour="",
                    key="",
                    source_id=sid,
                    reason="restriction_without_a_source",
                )
            ]
        try:
            rules = self._coerce_rules(restriction, param)
        except ValueError as exc:
            self.rejected.append((str(exc), restriction))
            return [
                RlvApplyResult(
                    outcome="refused",
                    behaviour="",
                    key="",
                    source_id=sid,
                    reason=str(exc),
                )
            ]
        moment = self._now(now)
        return [
            self._gate_one(sid, source_kind, rule, place_id, moment)
            for rule in rules
        ]

    @staticmethod
    def _coerce_rules(restriction: Any, param: str) -> list[ParsedRlvRule]:
        if isinstance(restriction, ParsedRlvRule):
            return [restriction]
        if not isinstance(restriction, str) or not restriction.strip():
            raise ValueError("a restriction must be non-empty text")
        text = restriction.strip()
        if text.startswith("@") or "=" in text:
            return parse_rlv_rules(text if text.startswith("@") else f"@{text}")
        lowered = param.strip().lower()
        if lowered in _ADD_PARAMS:
            action = "add"
        elif lowered in _LIFT_PARAMS:
            action = "lift"
        else:
            raise ValueError(f"unknown restriction parameter {param!r}")
        behaviour_raw, colon, option_raw = text.partition(":")
        behaviour = behaviour_raw.strip().lower()
        if not _BEHAVIOUR_RE.match(behaviour):
            raise ValueError(f"malformed RLV behaviour {behaviour_raw!r}")
        option = option_raw.strip() if colon else None
        if colon and not option:
            raise ValueError("a restriction option must be non-empty")
        return [
            ParsedRlvRule(
                behaviour=behaviour, option=option, action=action, raw=text
            )
        ]

    def _gate_one(
        self,
        source_id: str,
        source_kind: str,
        rule: ParsedRlvRule,
        place_id: str | None,
        moment: float,
    ) -> RlvApplyResult:
        kind = self._effective_kind(source_id, source_kind)
        if rule.action == "lift":
            return self._lift_one(source_id, rule)
        refusal = self._safety_refusal(rule)
        if refusal is not None:
            self.rejected.append((refusal, rule.key))
            return RlvApplyResult(
                outcome="refused",
                behaviour=rule.behaviour,
                key=rule.key,
                source_id=source_id,
                reason=refusal,
            )
        hold_reason = self._allowlist_reason(source_id, source_kind, place_id)
        if hold_reason is not None:
            self._held.append(
                HeldRestriction(
                    behaviour=rule.behaviour,
                    option=rule.option,
                    source_id=source_id,
                    source_kind=kind,
                    reason=hold_reason,
                    held_at=moment,
                    place_id=None if place_id is None else str(place_id),
                    raw=rule.raw,
                )
            )
            return RlvApplyResult(
                outcome="held",
                behaviour=rule.behaviour,
                key=rule.key,
                source_id=source_id,
                reason=hold_reason,
            )
        record = RlvRestriction(
            behaviour=rule.behaviour,
            option=rule.option,
            source_id=source_id,
            source_kind=kind,
            applied_at=moment,
            place_id=None if place_id is None else str(place_id),
            raw=rule.raw,
        )
        self._active.setdefault(rule.key, {})[source_id] = record
        return RlvApplyResult(
            outcome="applied",
            behaviour=rule.behaviour,
            key=rule.key,
            source_id=source_id,
        )

    def _lift_one(self, source_id: str, rule: ParsedRlvRule) -> RlvApplyResult:
        """Lift one of a source's OWN restrictions (doc 06:
        a source can only lift its own; there is no
        cross-source clear)."""
        holders = self._active.get(rule.key)
        if holders and source_id in holders:
            del holders[source_id]
            if not holders:
                del self._active[rule.key]
            return RlvApplyResult(
                outcome="lifted",
                behaviour=rule.behaviour,
                key=rule.key,
                source_id=source_id,
            )
        if holders:
            reason = "source_does_not_hold_this_restriction"
        else:
            reason = "restriction_not_active"
        self.rejected.append((reason, rule.key))
        return RlvApplyResult(
            outcome="not_lifted",
            behaviour=rule.behaviour,
            key=rule.key,
            source_id=source_id,
            reason=reason,
        )

    def lift_restriction(
        self,
        source_id: str,
        restriction: Any,
        *,
        param: str = "rem",
        now: float | None = None,
    ) -> list[RlvApplyResult]:
        """Lift a source's own restriction(s). Same input forms
        as :meth:`apply_restriction`; the rules are forced to
        the lift action. Never raises — malformed input fails
        closed into ``rejected``."""
        sid = "" if source_id is None else str(source_id)
        try:
            rules = self._coerce_rules(restriction, param)
        except ValueError as exc:
            self.rejected.append((str(exc), restriction))
            return [
                RlvApplyResult(
                    outcome="refused",
                    behaviour="",
                    key="",
                    source_id=sid,
                    reason=str(exc),
                )
            ]
        return [self._lift_one(sid, rule) for rule in rules]

    def clear_source(self, source_id: str) -> int:
        """Remove every ACTIVE restriction one source holds
        (the per-emitter ``@clear`` of doc 06); count removals.
        Held records are not the stack and are not touched."""
        if not source_id:
            return 0
        sid = str(source_id)
        removed = 0
        for key in list(self._active):
            holders = self._active[key]
            if sid in holders:
                del holders[sid]
                removed += 1
            if not holders:
                del self._active[key]
        return removed

    # -- queries: felt state, always with sources -----------------------
    def active_restrictions(self) -> list[RlvRestriction]:
        """Every active restriction, WITH its source, in
        key/source order. A restriction never hides its source."""
        out: list[RlvRestriction] = []
        for key in sorted(self._active):
            for sid in sorted(self._active[key]):
                out.append(self._active[key][sid])
        return out

    def restrictions_for(self, behaviour: str) -> list[RlvRestriction]:
        """Active restrictions whose key or behaviour matches."""
        wanted = str(behaviour or "").strip().lower()
        return [
            r
            for r in self.active_restrictions()
            if r.key == wanted or r.behaviour == wanted
        ]

    def is_restricted(self, behaviour: str) -> bool:
        return bool(self.restrictions_for(behaviour))

    def held_restrictions(self) -> list[HeldRestriction]:
        """Held (not applied) restrictions, with sources and
        reasons — reportable, never silently dropped."""
        return list(self._held)

    def current_state(self) -> RlvState:
        return RlvState(
            restrictions=self.active_restrictions(),
            held=self.held_restrictions(),
            opted_in_places=self.opted_in_places(),
        )

    def describe(self) -> str:
        """Answer "what binds me, and whose is it?" — every
        active restriction named WITH its source, and held
        attempts reported with theirs."""
        active = self.active_restrictions()
        lines: list[str] = []
        if not active:
            lines.append("No RLV restrictions are active.")
        else:
            lines.append(f"RLV restrictions active ({len(active)}):")
            for r in active:
                lines.append(
                    f"- {r.key} (source: {r.source_id}, "
                    f"kind: {r.source_kind})"
                )
        if self._held:
            lines.append(f"Held, not applied ({len(self._held)}):")
            for h in self._held:
                lines.append(
                    f"- {h.key} (source: {h.source_id}, "
                    f"kind: {h.source_kind}, reason: {h.reason})"
                )
        return "\n".join(lines)

    # -- the behaviour gate ----------------------------------------------
    def check(self, action: str) -> GateResult:
        """Gate one outbound action against the stack.

        Allowed, or blocked with ``blocked_reason`` =
        ``"rlv_blocked"`` (the Core refusal language the
        command router passes through verbatim), naming the
        behaviour and every source holding it. Only plain
        (unoptioned) restrictions block a mapped action — an
        optioned rule (``@sendim:<uuid>=add``) is an exception
        form and blocks only an exact ``behaviour:option``
        check. Logout / kill-switch actions are never blocked.
        """
        wanted = str(action or "").strip().lower()
        if not wanted or wanted in _NEVER_GATED:
            return GateResult(allowed=True, action=wanted)
        candidates: list[str] = []
        if ":" in wanted:
            candidates.append(wanted)
        for behaviour in ACTION_BEHAVIOURS.get(wanted, ()):
            if behaviour not in candidates:
                candidates.append(behaviour)
        if wanted not in candidates:
            candidates.append(wanted)
        for candidate in candidates:
            if ":" in candidate:
                holders = self._active.get(candidate, {})
                entries = [holders[s] for s in sorted(holders)]
            else:
                entries = [
                    r
                    for r in self.active_restrictions()
                    if r.behaviour == candidate and r.option is None
                ]
            if entries:
                return GateResult(
                    allowed=False,
                    action=wanted,
                    blocked_reason="rlv_blocked",
                    behaviour=entries[0].behaviour,
                    key=entries[0].key,
                    source_ids=tuple(r.source_id for r in entries),
                    source_kinds=tuple(r.source_kind for r in entries),
                )
        return GateResult(allowed=True, action=wanted)

    # -- self-use: build-only, record-after-confirmation ------------------
    def _self_rule(self, restriction: Any, param: str) -> ParsedRlvRule:
        rules = self._coerce_rules(restriction, param)
        if len(rules) != 1:
            raise ValueError(
                "a self-use command carries exactly one RLV rule"
            )
        rule = rules[0]
        refusal = self._safety_refusal(rule) if rule.action == "add" else None
        if refusal is not None:
            self.rejected.append((refusal, rule.key))
            raise ValueError(
                f"self-use restriction {rule.key!r} is refused: {refusal}"
            )
        return rule

    def build_apply_command(
        self, restriction: Any, command_id: str, *, param: str = "add"
    ) -> Command:
        """Build (do not send, do not record) an ``rlv_apply``
        command — the Core's RLV channel (its existing
        ``rlv_status`` vocabulary) — for her own avatar.
        Raises ValueError for a malformed or safety-violating
        rule; building changes no state."""
        rule = self._self_rule(restriction, param)
        if rule.action != "add":
            raise ValueError(
                "an apply command needs an add-form rule, "
                f"not {rule.raw!r}"
            )
        return Command(
            id=str(command_id),
            cmd="rlv_apply",
            params={
                "restriction": rule.canonical,
                "behaviour": rule.behaviour,
                "option": rule.option,
            },
            tier="mind",
        )

    def build_lift_command(
        self, restriction: Any, command_id: str, *, param: str = "rem"
    ) -> Command:
        """Build (do not send, do not record) an ``rlv_lift``
        command for her own avatar, the matching lift of a
        rule she set. Raises ValueError for a malformed rule
        or a lift form on the apply path's behalf."""
        rule = self._self_rule(restriction, param)
        if rule.action != "lift":
            raise ValueError(
                f"a lift command needs a lift-form rule, not {rule.raw!r}"
            )
        return Command(
            id=str(command_id),
            cmd="rlv_lift",
            params={
                "restriction": rule.canonical,
                "behaviour": rule.behaviour,
                "option": rule.option,
            },
            tier="mind",
        )

    def build_status_command(self, command_id: str) -> Command:
        """Build (do not send) the read-only ``rlv_status``
        request — the Core command that already exists, asking
        what restrictions the engine actually holds."""
        return Command(
            id=str(command_id), cmd="rlv_status", params={}, tier="mind"
        )

    def confirm_applied(
        self, restriction: Any, *, param: str = "add", now: float | None = None
    ) -> RlvRestriction:
        """Record a self-use application ONLY after Core
        confirms it happened. Raises ValueError for a malformed
        or safety-violating rule, and when the Core's
        confirmation does not produce an applied record."""
        rule = self._self_rule(restriction, param)
        if rule.action != "add":
            raise ValueError(
                f"a confirmed application needs an add-form rule, "
                f"not {rule.raw!r}"
            )
        results = self.apply_restriction(
            self.self_source_id, "own_worn", rule, now=now
        )
        result = results[0]
        if result.outcome != "applied":
            raise ValueError(
                f"self-use restriction {rule.key!r} was not applied: "
                f"{result.reason}"
            )
        self._self_history.append(
            SelfUseRecord(kind="apply", key=rule.key, timestamp=self._now(now))
        )
        holders = self._active[rule.key]
        return holders[self.self_source_id]

    def confirm_lifted(
        self, restriction: Any, *, param: str = "rem", now: float | None = None
    ) -> SelfUseRecord:
        """Record a self-use lift ONLY after Core confirms it.
        Raises ValueError when the self source held no such
        restriction (a source lifts only its own)."""
        rule = self._self_rule(restriction, param)
        if rule.action != "lift":
            raise ValueError(
                f"a confirmed lift needs a lift-form rule, not {rule.raw!r}"
            )
        results = self.lift_restriction(
            self.self_source_id, rule, now=now
        )
        if results[0].outcome != "lifted":
            raise ValueError(
                f"self-use lift of {rule.key!r} was not recorded: "
                f"{results[0].reason}"
            )
        record = SelfUseRecord(
            kind="lift", key=rule.key, timestamp=self._now(now)
        )
        self._self_history.append(record)
        return record

    def self_history(self) -> list[SelfUseRecord]:
        """Core-confirmed self-use applies and lifts, in order."""
        return list(self._self_history)

    # -- ingest -------------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: gate what a seam event carries.

        Never raises — a malformed or unknown entry is recorded
        in ``rejected`` (or the held list) and returns None, so
        one bad event cannot stop the perception stream (the
        dispatcher's own isolation rule). A confirmation event
        IS the Core's confirmation: applied events gate through
        the allowlist (held if the source is not allowlisted,
        refused if safety-violating), lifted events lift only
        that source's own stack entry.
        """
        if event.name not in RLV_EVENTS:
            self.rejected.append(("not_an_rlv_event", event.name))
            return None
        try:
            handler = {
                "rlv_restriction_applied": self._ingest_applied,
                "rlv_restriction_held": self._ingest_held,
                "rlv_restriction_lifted": self._ingest_lifted,
            }[event.name]
            return handler(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    @staticmethod
    def _first_present(
        source: dict[str, Any], keys: tuple[str, ...]
    ) -> Any:
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
        return None

    def _rules_from_payload(
        self, payload: dict[str, Any], default_param: str
    ) -> list[ParsedRlvRule]:
        entries: list[Any] = []
        for key in ("restrictions", "rules", "commands"):
            raw = payload.get(key)
            if isinstance(raw, list):
                entries.extend(raw)
        if not entries:
            for key in (
                "restriction", "command", "rlv", "text", "rule",
            ):
                if payload.get(key) is not None:
                    entries.append(payload[key])
                    break
        if entries:
            rules: list[ParsedRlvRule] = []
            for entry in entries:
                if isinstance(entry, dict):
                    behaviour = entry.get("behaviour") or entry.get("behavior")
                    if behaviour is None:
                        raise ValueError(
                            "a restriction entry carries no behaviour"
                        )
                    raw = str(behaviour)
                    if entry.get("option"):
                        raw += f":{entry['option']}"
                    param = entry.get("param") or entry.get(
                        "parameter"
                    ) or default_param
                    rules.extend(self._coerce_rules(raw, str(param)))
                else:
                    rules.extend(self._coerce_rules(str(entry), default_param))
            return rules
        behaviour = payload.get("behaviour") or payload.get("behavior")
        if behaviour is None:
            raise ValueError("a restriction event carries no restriction")
        raw = str(behaviour)
        if payload.get("option"):
            raw += f":{payload['option']}"
        param = payload.get("param") or payload.get("parameter") or default_param
        return self._coerce_rules(raw, str(param))

    def _payload_meta(self, payload: dict[str, Any]) -> tuple[str, str, str | None]:
        source_id = self._first_present(
            payload,
            ("source_id", "source", "object_id", "sender_id", "uuid", "id"),
        )
        kind = self._first_present(
            payload, ("source_kind", "kind", "source_type")
        )
        place_id = self._first_present(
            payload, ("place_id", "venue_id", "region_id", "parcel_id", "place")
        )
        return (
            "" if source_id is None else str(source_id),
            "other" if kind is None else str(kind),
            None if place_id is None else str(place_id),
        )

    @staticmethod
    def _require_source(source_id: str) -> str:
        if not source_id.strip():
            raise ValueError(
                "a restriction event without a source cannot be filed"
            )
        return source_id

    def _ingest_applied(self, event: SeamEvent) -> list[RlvApplyResult]:
        payload = event.payload
        source_id, kind, place_id = self._payload_meta(payload)
        source_id = self._require_source(source_id)
        results: list[RlvApplyResult] = []
        for rule in self._rules_from_payload(payload, "add"):
            results.append(
                self._gate_one(source_id, kind, rule, place_id, self._now(None))
            )
        return results

    def _ingest_held(self, event: SeamEvent) -> list[HeldRestriction]:
        payload = event.payload
        source_id, kind, place_id = self._payload_meta(payload)
        source_id = self._require_source(source_id)
        reason = payload.get("reason") or "reported_held"
        held: list[HeldRestriction] = []
        for rule in self._rules_from_payload(payload, "add"):
            entry = HeldRestriction(
                behaviour=rule.behaviour,
                option=rule.option,
                source_id=source_id,
                source_kind=self._effective_kind(source_id, kind),
                reason=str(reason),
                held_at=self._now(None),
                place_id=place_id,
                raw=rule.raw,
            )
            self._held.append(entry)
            held.append(entry)
        return held

    def _ingest_lifted(self, event: SeamEvent) -> list[RlvApplyResult]:
        payload = event.payload
        source_id, _, _ = self._payload_meta(payload)
        source_id = self._require_source(source_id)
        results: list[RlvApplyResult] = []
        for rule in self._rules_from_payload(payload, "rem"):
            results.append(self._lift_one(source_id, rule))
        return results

    # -- purge ----------------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a source's ACTIVE and HELD restrictions; count
        removals. (A resident id and a source id are the same
        key here: sources are identified by their object/resident
        id, and a purge must not leave held attempts behind.)"""
        if not resident_id:
            return 0
        sid = str(resident_id)
        removed = self.clear_source(sid)
        kept = [h for h in self._held if h.source_id != sid]
        removed += len(self._held) - len(kept)
        self._held = kept
        return removed


__all__ = [
    "ACTION_BEHAVIOURS",
    "RLV_EVENTS",
    "SOURCE_KINDS",
    "GateResult",
    "HeldRestriction",
    "ParsedRlvRule",
    "RlvApplyResult",
    "RlvRestriction",
    "RlvState",
    "RlvStore",
    "SelfUseRecord",
    "parse_rlv_rules",
]
