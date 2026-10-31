"""Object interaction — census doc 15 slice F6.

Touch, object-targeted sit, dialog/menu answering, and playing
animations and gestures: "this is the slice that makes venues,
games, and furniture real" (doc 15 §F6). Until this slice the seam
could *see* objects (``object_info`` has ridden the base vocabulary
since Phase 2/3) but could not act on one: no registry of what is
nearby, no touch, no way to sit on a *specific* chair rather than
wherever the generic sit lands, no way to answer the menu a
scripted object presents, and no way to start or stop an animation
or fire a gesture.

This module is the seam-side store: objects arrive as seam events,
are held queryable with exactly the properties an event carried,
and are acted on only through commands that are *built* here and
never executed here.

Bounds, enforced here in the body, not by convention:

- **Object data is never invented.** A registry record holds
  exactly what an object event carried — id, name, and only the
  owner / position / touch / sit affordances actually reported.
  Absent fields stay absent (``None``), never defaulted from the
  name or a guess. An affordance explicitly reported *absent*
  (touchable/sittable ``False``) refuses the matching build;
  an unreported affordance is unknown, not denied.
- **Build-only, record-after-confirmation.** Building a touch,
  sit, animation, or gesture command records nothing and acts on
  nothing; the interaction enters the history only via the
  matching ``confirm_*`` step after Core confirms it happened
  (directly, or via a confirmation event — the Core's own word).
  Touching or sitting on an object this registry does not know
  fails closed.
- **Dialogs are F1 offers, answered through F1.** A script dialog
  is an offer of kind ``script_dialog`` in the pending-offers
  queue (doc 03 §6) — this module does not duplicate that queue.
  It supplies the F6 half: take a dialog offer and a chosen button
  (by index or by label), validate the choice against the buttons
  the dialog actually offered — a button that was not offered
  fails closed — and build the resolution through the queue's own
  ``build_resolution_command`` (decision ``answer``, tier
  ``"mind"``). The dialog is marked answered only by
  :meth:`confirm_dialog_answered`, after Core confirmation.
- **Animations and gestures are named, never invented.** A
  play/stop command names exactly the identifier the caller (or a
  confirming event) supplied. The known-animations/gestures
  registry holds only what an event, a confirmation, or an
  explicit ``note_*`` call supplied — nothing is seeded, guessed,
  or listed from any built-in catalogue.
- **Unknown and malformed entries fail closed.** An event this
  store does not understand, an object with no id or no name, a
  confirmation for something unknown, go into the ``rejected``
  audit list (or raise at the direct API) and change nothing.

Scope honesty: this is the API/seam side of F6. The C# Core
already answers the generic ``sit`` / ``stand`` commands (its
``sit`` takes a bare ``target_uuid`` with no registry, no
affordance check, and no record) but does not yet emit
touch/sit/animation/gesture confirmation events and does not yet
answer ``touch_object`` / ``sit_on_object`` / ``play_animation`` /
``stop_animation`` / ``play_gesture`` — that is the named Core leg
(a purplelongship slice, like F1–F5's). The distinct
``sit_on_object`` name is deliberate: it is the registry-checked,
recorded act, not the generic sit. The event names added to the
seam vocabulary in ``core/models.py`` are the contract that leg
will speak; ``object_info`` and F1's ``script_dialog`` already
rode earlier vocabulary and are not duplicated. Until the Core
leg lands, objects, interactions, and dialog answers can be
recorded and queried entirely on this side, and every action
command is ready to route the moment Core can confirm it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.models import Command, SeamEvent
from .offers import OffersQueue, PendingOffer

# Canonical seam event names this store ingests. Object data
# already rides the base vocabulary (ObjectInfo / object_info)
# and script dialogs already ride F1's (ScriptDialogReceived /
# script_dialog, held by the offers queue — not ingested here);
# neither is duplicated. The rest are the F6 confirmation events
# added to core/models.py with this slice.
OBJECTS_EVENTS = frozenset(
    {
        "object_info",
        "object_touched",
        "object_sat_on",
        "animation_started",
        "animation_stopped",
        "gesture_played",
    }
)


@dataclass
class ObjectRecord:
    """One known in-world object — exactly what events carried."""

    id: str
    name: str
    owner_id: str | None = None
    owner_name: str | None = None
    position: tuple[float, float, float] | None = None
    touchable: bool | None = None
    sittable: bool | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class InteractionRecord:
    """One confirmed interaction, in confirmation order."""

    kind: str  # touch | sit | animation_play | animation_stop | gesture_play
    timestamp: float
    object_id: str | None = None
    object_name: str | None = None
    target: str | None = None  # animation / gesture identifier


@dataclass
class DialogRecord:
    """One confirmed dialog answer: which offer, which button."""

    offer_id: str
    object_id: str | None
    sender_id: str
    button_index: int
    button_label: str
    timestamp: float


@dataclass
class KnownPlayable:
    """One animation or gesture this seam actually knows of."""

    identifier: str
    kind: str  # animation | gesture
    name: str | None = None


def _position_from(raw: Any) -> tuple[float, float, float] | None:
    """A position exactly as carried, or None when not carried."""
    if isinstance(raw, dict):
        values = [raw.get(axis) for axis in ("x", "y", "z")]
    elif isinstance(raw, (list, tuple)):
        values = list(raw[:3])
    else:
        return None
    if len(values) != 3 or not all(
        isinstance(v, (int, float)) for v in values
    ):
        return None
    return (float(values[0]), float(values[1]), float(values[2]))


def dialog_buttons(offer: PendingOffer) -> list[str]:
    """The buttons a script-dialog offer actually presented.

    Read from the offer payload's ``buttons`` list, exactly as the
    dialog carried them; anything else (absent, not a list) means
    the dialog offered no buttons this seam can see — an empty
    list, so every choice against it fails closed.
    """
    raw = offer.payload.get("buttons")
    if not isinstance(raw, list):
        return []
    return [str(b) for b in raw]


@dataclass
class ObjectStore:
    """The object-interaction store: ingest, query, act, confirm."""

    _clock: Any = field(default=time.time)
    _objects: dict[str, ObjectRecord] = field(default_factory=dict, init=False)
    _interactions: list[InteractionRecord] = field(
        default_factory=list, init=False
    )
    _dialogs: list[DialogRecord] = field(default_factory=list, init=False)
    _animations: dict[str, KnownPlayable] = field(
        default_factory=dict, init=False
    )
    _gestures: dict[str, KnownPlayable] = field(default_factory=dict, init=False)
    _playing: set[str] = field(default_factory=set, init=False)
    _seated_on: str | None = field(default=None, init=False)
    # Fail-closed audit: (reason, detail) for every refused ingest.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else float(now)

    # -- registry -------------------------------------------------------
    def record_object(
        self,
        object_id: str,
        name: str,
        *,
        owner_id: str | None = None,
        owner_name: str | None = None,
        position: tuple[float, float, float] | None = None,
        touchable: bool | None = None,
        sittable: bool | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ObjectRecord:
        """File one object. Raises ValueError without id or name.

        Re-recording updates only the fields actually supplied —
        a later sparse event must not erase known data, and no
        field is ever filled by inference.
        """
        if not object_id or not str(object_id).strip():
            raise ValueError("an object without an id cannot be filed")
        if not name or not str(name).strip():
            raise ValueError("an object without a name cannot be filed")
        key = str(object_id)
        existing = self._objects.get(key)
        if existing is not None:
            existing.name = str(name)
            if owner_id is not None:
                existing.owner_id = str(owner_id)
            if owner_name is not None:
                existing.owner_name = str(owner_name)
            if position is not None:
                existing.position = position
            if touchable is not None:
                existing.touchable = bool(touchable)
            if sittable is not None:
                existing.sittable = bool(sittable)
            if payload is not None:
                existing.payload = dict(payload)
            return existing
        record = ObjectRecord(
            id=key,
            name=str(name),
            owner_id=None if owner_id is None else str(owner_id),
            owner_name=None if owner_name is None else str(owner_name),
            position=position,
            touchable=touchable,
            sittable=sittable,
            payload=dict(payload or {}),
        )
        self._objects[record.id] = record
        return record

    def _record_object_entry(self, entry: Any) -> ObjectRecord:
        if not isinstance(entry, dict):
            raise TypeError("object entry is not an object")

        def _bool(*keys: str) -> bool | None:
            for key in keys:
                raw = entry.get(key)
                if isinstance(raw, bool):
                    return raw
            return None

        known = {
            "object_id", "id", "uuid", "name", "object_name",
            "owner_id", "owner", "owner_uuid", "owner_name",
            "position", "pos", "touchable", "can_touch",
            "sittable", "can_sit", "sit_target",
        }
        return self.record_object(
            str(
                entry.get("object_id")
                or entry.get("id")
                or entry.get("uuid")
                or ""
            ),
            str(entry.get("name") or entry.get("object_name") or ""),
            owner_id=(
                entry.get("owner_id")
                or entry.get("owner")
                or entry.get("owner_uuid")
            ),
            owner_name=entry.get("owner_name"),
            position=_position_from(
                entry.get("position") or entry.get("pos")
            ),
            touchable=_bool("touchable", "can_touch"),
            sittable=_bool("sittable", "can_sit", "sit_target"),
            payload={k: v for k, v in entry.items() if k not in known},
        )

    def get_object(self, object_id: str) -> ObjectRecord | None:
        return self._objects.get(str(object_id))

    def list_objects(self) -> list[ObjectRecord]:
        """Known objects, in the order they were first recorded."""
        return list(self._objects.values())

    def _require_object(self, object_id: str) -> ObjectRecord:
        record = self.get_object(object_id)
        if record is None:
            raise KeyError(f"no known object with id {object_id!r}")
        return record

    # -- touch: build, then confirm --------------------------------------
    def build_touch_command(self, object_id: str, command_id: str) -> Command:
        """Build (do not send, do not record) a ``touch_object``
        command. Raises KeyError for an unknown object and
        ValueError when the object was explicitly reported
        untouchable — building records no interaction; the touch
        is recorded only by :meth:`confirm_touch` after Core
        confirms it."""
        record = self._require_object(object_id)
        if record.touchable is False:
            raise ValueError(
                f"object {record.id!r} was reported as not touchable"
            )
        return Command(
            id=str(command_id),
            cmd="touch_object",
            params={"object_id": record.id, "name": record.name},
            tier="mind",
        )

    def confirm_touch(
        self, object_id: str, *, now: float | None = None
    ) -> InteractionRecord:
        """Record a touch ONLY after Core confirms it happened."""
        record = self._require_object(object_id)
        entry = InteractionRecord(
            kind="touch",
            timestamp=self._now(now),
            object_id=record.id,
            object_name=record.name,
        )
        self._interactions.append(entry)
        return entry

    # -- object-targeted sit: build, then confirm -------------------------
    def build_sit_command(self, object_id: str, command_id: str) -> Command:
        """Build (do not send, do not record) a ``sit_on_object``
        command for one specific known object — distinct from the
        Core's generic ``sit``/``stand``: this one is
        registry-checked, affordance-checked, and recorded on
        confirmation. ``target_uuid`` rides along in the Core's
        existing sit parameter form. Raises KeyError for an
        unknown object and ValueError when the object was
        explicitly reported unsittable."""
        record = self._require_object(object_id)
        if record.sittable is False:
            raise ValueError(
                f"object {record.id!r} was reported as not sittable"
            )
        return Command(
            id=str(command_id),
            cmd="sit_on_object",
            params={"object_id": record.id, "target_uuid": record.id},
            tier="mind",
        )

    def confirm_sit(
        self, object_id: str, *, now: float | None = None
    ) -> InteractionRecord:
        """Record a sit ONLY after Core confirms it happened."""
        record = self._require_object(object_id)
        entry = InteractionRecord(
            kind="sit",
            timestamp=self._now(now),
            object_id=record.id,
            object_name=record.name,
        )
        self._interactions.append(entry)
        self._seated_on = record.id
        return entry

    def seated_on(self) -> str | None:
        """The object id a confirmed sit put her on, or None."""
        return self._seated_on

    # -- script dialogs: F1 offers, answered through F1 -------------------
    @staticmethod
    def _dialog_offer(
        queue: OffersQueue, offer_id: str, now: float | None = None
    ) -> PendingOffer:
        offer = queue.get(offer_id, now=now)
        if offer is None:
            raise KeyError(f"no pending offer with id {offer_id!r}")
        if offer.kind != "script_dialog":
            raise ValueError(
                f"offer {offer_id!r} is a {offer.kind}, not a script dialog"
            )
        return offer

    @staticmethod
    def _choose_button(
        offer: PendingOffer,
        button_index: int | None,
        button_label: str | None,
    ) -> tuple[int, str]:
        """Validate a dialog choice against the offered buttons.

        Exactly one of index / label must be given; the choice
        must be one the dialog actually offered. Anything else
        fails closed — an unoffered button is never answered.
        """
        buttons = dialog_buttons(offer)
        if (button_index is None) == (button_label is None):
            raise ValueError("choose a dialog button by index or by label")
        if button_index is not None:
            if not isinstance(button_index, int) or isinstance(
                button_index, bool
            ):
                raise ValueError("a dialog button index must be an integer")
            if not 0 <= button_index < len(buttons):
                raise ValueError(
                    f"button index {button_index} was not offered "
                    f"(dialog offered {len(buttons)} button(s))"
                )
            return button_index, buttons[button_index]
        label = str(button_label)
        if label not in buttons:
            raise ValueError(f"button {label!r} was not offered by this dialog")
        return buttons.index(label), label

    def build_dialog_answer_command(
        self,
        queue: OffersQueue,
        offer_id: str,
        *,
        button_index: int | None = None,
        button_label: str | None = None,
        now: float | None = None,
    ) -> Command:
        """Build (do not send, do not resolve) the answer to a
        script dialog, through F1's own resolution builder.

        The choice is validated against the buttons the dialog
        actually offered; the returned command is the queue's
        ``offer_respond`` (decision ``answer``, tier ``"mind"``)
        carrying ``{"index": …, "label": …}`` as its answer. The
        offer stays pending and nothing is recorded here — the
        dialog is marked answered only by
        :meth:`confirm_dialog_answered` after Core confirms.
        """
        offer = self._dialog_offer(queue, offer_id, now)
        index, label = self._choose_button(offer, button_index, button_label)
        return queue.build_resolution_command(
            offer.id,
            "answer",
            answer={"index": index, "label": label},
            now=now,
        )

    def confirm_dialog_answered(
        self,
        queue: OffersQueue,
        offer_id: str,
        *,
        button_index: int | None = None,
        button_label: str | None = None,
        now: float | None = None,
    ) -> DialogRecord:
        """Mark a dialog answered ONLY after Core confirms the
        answer was sent: resolves the offer in the F1 queue (the
        queue's own ``resolve``) and files the dialog record
        here. The choice is re-validated — a confirmation for a
        button that was never offered fails closed and resolves
        nothing."""
        offer = self._dialog_offer(queue, offer_id, now)
        index, label = self._choose_button(offer, button_index, button_label)
        moment = self._now(now)
        queue.resolve(
            offer.id,
            "answer",
            answer={"index": index, "label": label},
            now=now,
        )
        record = DialogRecord(
            offer_id=offer.id,
            object_id=(
                None
                if offer.payload.get("object_id") is None
                else str(offer.payload["object_id"])
            ),
            sender_id=offer.sender_id,
            button_index=index,
            button_label=label,
            timestamp=moment,
        )
        self._dialogs.append(record)
        return record

    def dialog_history(self) -> list[DialogRecord]:
        """Confirmed dialog answers, in the order they closed."""
        return list(self._dialogs)

    # -- animations & gestures: build, then confirm ------------------------
    @staticmethod
    def _playable_id(identifier: str) -> str:
        text = str(identifier or "").strip()
        if not text:
            raise ValueError("an animation or gesture needs an identifier")
        return text

    def note_animation(
        self, identifier: str, *, name: str | None = None
    ) -> KnownPlayable:
        """Register one animation a caller actually supplied."""
        key = self._playable_id(identifier)
        entry = KnownPlayable(
            identifier=key,
            kind="animation",
            name=None if name is None else str(name),
        )
        self._animations[key] = entry
        return entry

    def note_gesture(
        self, identifier: str, *, name: str | None = None
    ) -> KnownPlayable:
        """Register one gesture a caller actually supplied."""
        key = self._playable_id(identifier)
        entry = KnownPlayable(
            identifier=key,
            kind="gesture",
            name=None if name is None else str(name),
        )
        self._gestures[key] = entry
        return entry

    def known_animations(self) -> list[KnownPlayable]:
        return list(self._animations.values())

    def known_gestures(self) -> list[KnownPlayable]:
        return list(self._gestures.values())

    def build_play_animation_command(
        self, identifier: str, command_id: str
    ) -> Command:
        """Build (do not send, do not record) ``play_animation``
        for the exact identifier supplied. Records nothing; the
        play is recorded only by :meth:`confirm_animation_started`
        after Core confirms."""
        return Command(
            id=str(command_id),
            cmd="play_animation",
            params={"animation": self._playable_id(identifier)},
            tier="mind",
        )

    def build_stop_animation_command(
        self, identifier: str, command_id: str
    ) -> Command:
        """Build (do not send, do not record) ``stop_animation``
        for the exact identifier supplied."""
        return Command(
            id=str(command_id),
            cmd="stop_animation",
            params={"animation": self._playable_id(identifier)},
            tier="mind",
        )

    def build_play_gesture_command(
        self, identifier: str, command_id: str
    ) -> Command:
        """Build (do not send, do not record) ``play_gesture`` for
        the exact identifier supplied."""
        return Command(
            id=str(command_id),
            cmd="play_gesture",
            params={"gesture": self._playable_id(identifier)},
            tier="mind",
        )

    def confirm_animation_started(
        self, identifier: str, *, now: float | None = None
    ) -> InteractionRecord:
        """Record a started animation ONLY after Core confirms.

        A confirmation is itself a supply: the animation joins
        the known registry (under exactly the confirmed
        identifier) and the playing set."""
        key = self._playable_id(identifier)
        if key not in self._animations:
            self.note_animation(key)
        self._playing.add(key)
        entry = InteractionRecord(
            kind="animation_play", timestamp=self._now(now), target=key
        )
        self._interactions.append(entry)
        return entry

    def confirm_animation_stopped(
        self, identifier: str, *, now: float | None = None
    ) -> InteractionRecord:
        """Record a stopped animation ONLY after Core confirms."""
        key = self._playable_id(identifier)
        self._playing.discard(key)
        entry = InteractionRecord(
            kind="animation_stop", timestamp=self._now(now), target=key
        )
        self._interactions.append(entry)
        return entry

    def confirm_gesture_played(
        self, identifier: str, *, now: float | None = None
    ) -> InteractionRecord:
        """Record a played gesture ONLY after Core confirms; the
        confirmed gesture joins the known registry."""
        key = self._playable_id(identifier)
        if key not in self._gestures:
            self.note_gesture(key)
        entry = InteractionRecord(
            kind="gesture_play", timestamp=self._now(now), target=key
        )
        self._interactions.append(entry)
        return entry

    def playing_animations(self) -> list[str]:
        """Identifiers confirmed started and not yet stopped."""
        return sorted(self._playing)

    def interaction_history(self) -> list[InteractionRecord]:
        """Confirmed interactions, in confirmation order."""
        return list(self._interactions)

    # -- ingest -------------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: file what a seam event carries.

        Never raises — a malformed or unknown entry is recorded
        in ``rejected`` and returns None, so one bad event cannot
        stop the perception stream (the dispatcher's own
        isolation rule). Confirmation events ARE the Core's
        confirmation: recording the interaction here is the
        confirm step, not a guess.
        """
        if event.name not in OBJECTS_EVENTS:
            self.rejected.append(("not_an_objects_event", event.name))
            return None
        try:
            handler = {
                "object_info": self._ingest_object_info,
                "object_touched": self._ingest_confirmed_object,
                "object_sat_on": self._ingest_confirmed_object,
                "animation_started": self._ingest_animation_started,
                "animation_stopped": self._ingest_animation_stopped,
                "gesture_played": self._ingest_gesture_played,
            }[event.name]
            return handler(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    def _ingest_object_info(self, event: SeamEvent) -> Any:
        payload = event.payload
        entries = payload.get("objects")
        if isinstance(entries, list):
            filed = []
            for entry in entries:
                try:
                    filed.append(self._record_object_entry(entry))
                except (ValueError, TypeError) as exc:
                    self.rejected.append((str(exc), event.name))
            return filed
        return self._record_object_entry(payload)

    def _ingest_confirmed_object(self, event: SeamEvent) -> InteractionRecord:
        object_id = (
            event.payload.get("object_id")
            or event.payload.get("id")
            or event.payload.get("uuid")
            or ""
        )
        if event.name == "object_touched":
            return self.confirm_touch(str(object_id))
        return self.confirm_sit(str(object_id))

    @staticmethod
    def _playable_from_payload(payload: dict[str, Any], *keys: str) -> str:
        for key in keys:
            raw = payload.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw
        raise ValueError("confirmation event carries no identifier")

    def _ingest_animation_started(self, event: SeamEvent) -> InteractionRecord:
        return self.confirm_animation_started(
            self._playable_from_payload(
                event.payload, "animation", "animation_id", "name"
            )
        )

    def _ingest_animation_stopped(self, event: SeamEvent) -> InteractionRecord:
        return self.confirm_animation_stopped(
            self._playable_from_payload(
                event.payload, "animation", "animation_id", "name"
            )
        )

    def _ingest_gesture_played(self, event: SeamEvent) -> InteractionRecord:
        return self.confirm_gesture_played(
            self._playable_from_payload(
                event.payload, "gesture", "gesture_id", "name"
            )
        )

    # -- purge ---------------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident from every record here; count removals.

        Objects owned by that resident are removed, along with
        every interaction record tied to those objects and every
        dialog record tied to those objects or sent by that
        resident. Objects owned by anyone else, unowned objects,
        and animation/gesture records (not a resident's records)
        stay.
        """
        if not resident_id:
            return 0
        key = str(resident_id)
        removed_ids = {
            oid for oid, obj in self._objects.items() if obj.owner_id == key
        }
        removed = 0
        for oid in removed_ids:
            del self._objects[oid]
            removed += 1
        kept = [r for r in self._interactions if r.object_id not in removed_ids]
        removed += len(self._interactions) - len(kept)
        self._interactions = kept
        kept_dialogs = [
            d
            for d in self._dialogs
            if d.object_id not in removed_ids and d.sender_id != key
        ]
        removed += len(self._dialogs) - len(kept_dialogs)
        self._dialogs = kept_dialogs
        if self._seated_on in removed_ids:
            self._seated_on = None
        return removed


__all__ = [
    "OBJECTS_EVENTS",
    "DialogRecord",
    "InteractionRecord",
    "KnownPlayable",
    "ObjectRecord",
    "ObjectStore",
    "dialog_buttons",
]
