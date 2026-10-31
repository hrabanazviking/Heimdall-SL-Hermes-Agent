"""Appearance — census doc 15 slice F7.

"What are you wearing?" is a question she should be able to
answer from state, and changing an outfit should be something
she can *do* — wear an item from inventory, take one off, attach
or detach an object, swap the whole outfit (doc 15 §F7). Until
this slice, ``appearance_changed`` events fired into the seam
and meant nothing: there was no outfit state behind them, no
wear command, no honest answer.

This module is the seam-side store: outfit snapshots and
single-item confirmations arrive as seam events, are held with
exactly the fields they carried, and are acted on only through
commands that are *built* here and never executed here.

Bounds, enforced here in the body, not by convention:

- **Outfit state is never invented.** Before any outfit
  snapshot arrives the outfit is UNKNOWN — not empty, not
  naked. :attr:`AppearanceStore.outfit_known` distinguishes an
  unknown outfit from a snapshot that explicitly carried zero
  items. A held entry holds exactly what arrived — id, name,
  kind, and slot / attach point only when carried; sparse
  fields stay ``None``, never filled from a guess.
- **Wearable kinds are a subset of F3's.** Only ``clothing``,
  ``bodypart``, and ``object`` (objects are attachments) can be
  worn or attached. Any other kind fails closed.
- **Appearance-changed is a staleness notice, not data.**
  ``appearance_changed`` already rides the base vocabulary; it
  never alters outfit entries here. It marks a known outfit
  stale — appearance may have changed, and the new outfit is
  unknown until the next snapshot — and records a change
  notice. Only an ``outfit_received`` snapshot clears the
  staleness.
- **Build-only, record-after-confirmation.** Building a wear /
  remove / attach / detach / change-outfit command records
  nothing and changes nothing; the change enters the history
  only via the matching ``confirm_*`` step after Core confirms
  it happened (directly, or via a confirmation event — the
  Core's own word). Removing or detaching an item that is not
  currently worn / attached fails closed.
- **Wear validates against inventory when one is bound.** With
  an :class:`~heimdall.awareness.inventory.InventoryStore`
  bound, a worn / attached / changed-to item must exist there
  and be a wearable kind, failing closed otherwise. Without a
  bound inventory, validation falls back to explicit item
  details supplied by the caller — a name and kind are never
  guessed.

Scope honesty: this is the API/seam side of F7. The C# Core
already emits ``appearance_changed`` (mapped from its
AppearanceSet signal since Phase 2 slice 2) but does not yet
emit outfit snapshots or worn/attached confirmations, and does
not yet answer ``wear_item`` / ``remove_item`` / ``attach_item``
/ ``detach_item`` / ``change_outfit`` — that is the named Core
leg (a purplelongship slice, like F1–F6's). The event names
added to the seam vocabulary in ``core/models.py`` are the
contract that leg will speak; ``appearance_changed`` already
rode the base vocabulary and is not duplicated. Until the Core
leg lands, outfit state, history, and the honest "what are you
wearing?" answer can be held and queried entirely on this
side, and every action command is ready to route the moment
Core can confirm it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.models import Command, SeamEvent
from .inventory import ITEM_KINDS, InventoryStore

# The wearable subset of F3's ITEM_KINDS: clothing and body
# parts are worn; objects are attachments. Anything else —
# notecards, landmarks, scripts, … — can be held in inventory
# but never worn, and fails closed here.
WEARABLE_KINDS = frozenset({"clothing", "bodypart", "object"})
assert WEARABLE_KINDS <= ITEM_KINDS

# Canonical seam event names this store ingests.
# ``appearance_changed`` already rides the base vocabulary
# (AppearanceChanged / appearance_changed) and is ingested
# here without being re-added to core/models.py; the rest are
# the F7 names added there with this slice.
APPEARANCE_EVENTS = frozenset(
    {
        "outfit_received",
        "item_worn",
        "item_removed",
        "attachment_attached",
        "attachment_detached",
        "appearance_changed",
    }
)


@dataclass
class WornItem:
    """One worn or attached item — exactly what arrived."""

    id: str
    name: str
    kind: str  # clothing | bodypart | object
    slot: str | None = None
    attach_point: str | None = None
    creator_id: str | None = None
    creator_name: str | None = None
    received_from_id: str | None = None
    received_from_name: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutfitChangeRecord:
    """One confirmed outfit change, in confirmation order."""

    kind: str  # worn | removed | attached | detached | outfit | snapshot
    timestamp: float
    item_id: str | None = None
    item_name: str | None = None
    item_ids: tuple[str, ...] = ()


@dataclass
class AppearanceNotice:
    """One appearance-changed notice: the outfit may have moved."""

    timestamp: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppearanceState:
    """A snapshot of the outfit state, honest about its gaps."""

    known: bool
    stale: bool
    outfit: list[WornItem]
    attachments: list[WornItem]
    notices: int


@dataclass
class AppearanceStore:
    """The appearance store: ingest, query, act, confirm."""

    inventory: InventoryStore | None = field(default=None)
    _clock: Any = field(default=time.time)
    _outfit: dict[str, WornItem] = field(default_factory=dict, init=False)
    _attachments: set[str] = field(default_factory=set, init=False)
    _known: bool = field(default=False, init=False)
    _stale: bool = field(default=False, init=False)
    _history: list[OutfitChangeRecord] = field(default_factory=list, init=False)
    _notices: list[AppearanceNotice] = field(default_factory=list, init=False)
    # Fail-closed audit: (reason, detail) for every refused ingest.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else float(now)

    # -- honesty flags ----------------------------------------------------
    @property
    def outfit_known(self) -> bool:
        """True only once a full outfit snapshot (or a confirmed
        wholesale outfit change) has arrived. Before that the
        outfit is UNKNOWN — individual worn items may be known,
        but the outfit as a whole is not, and is never presented
        as empty."""
        return self._known

    @property
    def outfit_stale(self) -> bool:
        """True when appearance changed after the last snapshot."""
        return self._stale

    # -- entry resolution: never guess a name or kind ----------------------
    def _from_inventory(self, item_id: str) -> Any:
        if self.inventory is None:
            return None
        return self.inventory.get(str(item_id))

    def _entry_from_mapping(self, entry: Any) -> WornItem:
        if not isinstance(entry, dict):
            raise TypeError("outfit entry is not an object")
        item_id = str(
            entry.get("item_id") or entry.get("id") or entry.get("uuid") or ""
        )
        if not item_id:
            raise ValueError("an outfit entry without an id cannot be filed")
        name = entry.get("name") or entry.get("item_name")
        kind = entry.get("kind") or entry.get("item_kind")
        inv = self._from_inventory(item_id)
        if name is None and inv is not None:
            name = inv.name
        if kind is None and inv is not None:
            kind = inv.kind
        if not name or not str(name).strip():
            raise ValueError("an outfit entry without a name cannot be filed")
        if not kind:
            raise ValueError("an outfit entry without a kind cannot be filed")
        kind = str(kind)
        if kind not in WEARABLE_KINDS:
            raise ValueError(f"kind {kind!r} is not wearable")
        known = {
            "item_id", "id", "uuid", "name", "item_name", "kind",
            "item_kind", "slot", "attach_point", "attachment_point",
            "creator_id", "creator_name", "received_from_id",
            "received_from_name", "sender_id", "sender_name",
        }
        return WornItem(
            id=item_id,
            name=str(name),
            kind=kind,
            slot=None if entry.get("slot") is None else str(entry["slot"]),
            attach_point=(
                None
                if (entry.get("attach_point") or entry.get("attachment_point"))
                is None
                else str(
                    entry.get("attach_point") or entry.get("attachment_point")
                )
            ),
            creator_id=(
                entry.get("creator_id")
                or (inv.creator_id if inv is not None else None)
            ),
            creator_name=(
                entry.get("creator_name")
                or (inv.creator_name if inv is not None else None)
            ),
            received_from_id=(
                entry.get("received_from_id")
                or entry.get("sender_id")
                or (inv.received_from_id if inv is not None else None)
            ),
            received_from_name=(
                entry.get("received_from_name")
                or entry.get("sender_name")
                or (inv.received_from_name if inv is not None else None)
            ),
            payload={k: v for k, v in entry.items() if k not in known},
        )

    def _coerce_entry(
        self,
        item: Any,
        *,
        name: str | None = None,
        kind: str | None = None,
        slot: str | None = None,
        attach_point: str | None = None,
    ) -> WornItem:
        """Coerce a WornItem / mapping / bare id into a WornItem.

        A bare id resolves only through the bound inventory;
        explicit details win where supplied. Anything that
        cannot be resolved to a name + wearable kind fails
        closed — nothing is guessed."""
        if isinstance(item, WornItem):
            if item.kind not in WEARABLE_KINDS:
                raise ValueError(f"kind {item.kind!r} is not wearable")
            return item
        if isinstance(item, dict):
            return self._entry_from_mapping(item)
        item_id = str(item or "")
        if not item_id:
            raise ValueError("an outfit entry without an id cannot be filed")
        mapping: dict[str, Any] = {"item_id": item_id}
        if name is not None:
            mapping["name"] = name
        if kind is not None:
            mapping["kind"] = kind
        if slot is not None:
            mapping["slot"] = slot
        if attach_point is not None:
            mapping["attach_point"] = attach_point
        return self._entry_from_mapping(mapping)

    def _coerce_entries(self, items: Any) -> list[WornItem]:
        if items is None:
            return []
        if isinstance(items, (WornItem, dict, str)):
            items = [items]
        return [self._coerce_entry(item) for item in items]

    def _validate_wearable(self, item_id: str, *, name=None, kind=None,
                           slot=None, attach_point=None) -> WornItem:
        """Validate one item for a wear/attach/change build.

        With a bound inventory the item must exist there and be
        wearable — the inventory is the authority. Without one,
        explicit caller-supplied details are the only source."""
        if self.inventory is not None:
            inv = self.inventory.get(str(item_id))
            if inv is None:
                raise KeyError(f"no inventory item with id {item_id!r}")
            if inv.kind not in WEARABLE_KINDS:
                raise ValueError(
                    f"inventory item {item_id!r} is a {inv.kind}, not wearable"
                )
            return WornItem(
                id=inv.id,
                name=inv.name,
                kind=inv.kind,
                slot=slot,
                attach_point=attach_point,
                creator_id=inv.creator_id,
                creator_name=inv.creator_name,
                received_from_id=inv.received_from_id,
                received_from_name=inv.received_from_name,
            )
        return self._coerce_entry(
            item_id, name=name, kind=kind, slot=slot,
            attach_point=attach_point,
        )

    # -- queries -------------------------------------------------------------
    def current_outfit(self) -> list[WornItem]:
        """Worn (non-attachment) items currently held. Pair with
        :attr:`outfit_known`: before any snapshot this list is
        only the individually-confirmed items, not the outfit."""
        return [
            item for key, item in self._outfit.items()
            if key not in self._attachments
        ]

    def current_attachments(self) -> list[WornItem]:
        """Attached items (objects) currently held."""
        return [
            item for key, item in self._outfit.items()
            if key in self._attachments
        ]

    def get_worn(self, item_id: str) -> WornItem | None:
        return self._outfit.get(str(item_id))

    def is_wearing(self, item_id: str) -> bool:
        return str(item_id) in self._outfit and (
            str(item_id) not in self._attachments
        )

    def is_attached(self, item_id: str) -> bool:
        return str(item_id) in self._attachments

    def current_state(self) -> AppearanceState:
        return AppearanceState(
            known=self._known,
            stale=self._stale,
            outfit=self.current_outfit(),
            attachments=self.current_attachments(),
            notices=len(self._notices),
        )

    def describe(self) -> str:
        """Answer "what are you wearing?" — honestly, with the
        unknown and stale flags spoken, never smoothed over."""
        if not self._known and not self._outfit:
            return (
                "Outfit unknown — no outfit snapshot has arrived, "
                "so what I am wearing is not known (not empty, "
                "not naked: unknown)."
            )
        worn = ", ".join(
            f"{i.name} ({i.kind}"
            + (f", slot {i.slot}" if i.slot else "")
            + ")"
            for i in self.current_outfit()
        ) or "nothing recorded worn"
        attached = ", ".join(
            f"{i.name} ({i.kind}"
            + (f", attached at {i.attach_point}" if i.attach_point else "")
            + ")"
            for i in self.current_attachments()
        )
        text = f"Wearing: {worn}."
        if attached:
            text += f" Attached: {attached}."
        if not self._known:
            text += (
                " The full outfit is not known — no snapshot has "
                "arrived; this is only what has been individually "
                "confirmed."
            )
        if self._stale:
            text += (
                " This outfit may be stale — appearance changed "
                "since the last snapshot, and the new outfit is "
                "unknown until the next snapshot."
            )
        return text

    def outfit_history(self) -> list[OutfitChangeRecord]:
        """Confirmed outfit changes, in confirmation order."""
        return list(self._history)

    def appearance_notices(self) -> list[AppearanceNotice]:
        """Appearance-changed notices, in arrival order."""
        return list(self._notices)

    # -- build-only commands ---------------------------------------------------
    @staticmethod
    def _params_for(entry: WornItem) -> dict[str, Any]:
        params: dict[str, Any] = {
            "item_id": entry.id,
            "name": entry.name,
            "kind": entry.kind,
        }
        if entry.slot is not None:
            params["slot"] = entry.slot
        if entry.attach_point is not None:
            params["attach_point"] = entry.attach_point
        return params

    def build_wear_command(self, item_id: str, command_id: str, *,
                           name: str | None = None,
                           kind: str | None = None,
                           slot: str | None = None) -> Command:
        """Build (do not send, do not record) a ``wear_item``
        command. With a bound inventory the item must exist
        there and be wearable; without one, explicit ``name``
        and ``kind`` must be supplied — never guessed. Building
        changes no outfit state; the wear is recorded only by
        :meth:`confirm_worn` after Core confirms."""
        entry = self._validate_wearable(
            item_id, name=name, kind=kind, slot=slot
        )
        return Command(
            id=str(command_id), cmd="wear_item",
            params=self._params_for(entry), tier="mind",
        )

    def build_remove_command(self, item_id: str, command_id: str) -> Command:
        """Build (do not send, do not record) a ``remove_item``
        command. The item must be currently worn — removing
        something she is not wearing fails closed."""
        key = str(item_id)
        if not self.is_wearing(key):
            raise KeyError(f"item {item_id!r} is not currently worn")
        entry = self._outfit[key]
        return Command(
            id=str(command_id), cmd="remove_item",
            params=self._params_for(entry), tier="mind",
        )

    def build_attach_command(self, item_id: str, command_id: str, *,
                             attach_point: str | None = None,
                             name: str | None = None,
                             kind: str | None = None) -> Command:
        """Build (do not send, do not record) an ``attach_item``
        command. An attach point is required — attaching nowhere
        in particular fails closed. Validation is the wear
        validation: bound inventory first, explicit details
        otherwise."""
        if not attach_point or not str(attach_point).strip():
            raise ValueError("attaching an item requires an attach point")
        entry = self._validate_wearable(
            item_id, name=name, kind=kind,
            attach_point=str(attach_point),
        )
        return Command(
            id=str(command_id), cmd="attach_item",
            params=self._params_for(entry), tier="mind",
        )

    def build_detach_command(self, item_id: str, command_id: str) -> Command:
        """Build (do not send, do not record) a ``detach_item``
        command. The item must be currently attached."""
        key = str(item_id)
        if not self.is_attached(key):
            raise KeyError(f"item {item_id!r} is not currently attached")
        entry = self._outfit[key]
        return Command(
            id=str(command_id), cmd="detach_item",
            params=self._params_for(entry), tier="mind",
        )

    def build_change_outfit_command(self, items: Any, command_id: str, *,
                                    item_details: dict[str, Any] | None = None
                                    ) -> Command:
        """Build (do not send, do not record) a wholesale
        ``change_outfit`` command for an item list. Every item
        is validated like a wear (bound inventory, or explicit
        details via ``item_details`` keyed by item id); any
        unresolvable or non-wearable item fails the whole build
        closed. Building changes no outfit state."""
        entries = self._validate_outfit_list(items, item_details)
        return Command(
            id=str(command_id), cmd="change_outfit",
            params={
                "item_ids": [e.id for e in entries],
                "items": [self._params_for(e) for e in entries],
            },
            tier="mind",
        )

    def _validate_outfit_list(self, items: Any,
                              item_details: dict[str, Any] | None
                              ) -> list[WornItem]:
        if items is None:
            return []
        if isinstance(items, (WornItem, dict, str)):
            items = [items]
        entries = []
        for item in items:
            if isinstance(item, (WornItem, dict)):
                entry = self._coerce_entry(item)
                if self.inventory is not None:
                    # Re-validate dict/WornItem forms against the
                    # bound inventory as the authority.
                    entry = self._validate_wearable(entry.id)
                    # Keep caller-carried placement the inventory
                    # cannot know.
                    coerced = self._coerce_entry(item)
                    entry.slot = coerced.slot
                    entry.attach_point = coerced.attach_point
                entries.append(entry)
            else:
                details = (item_details or {}).get(str(item), {})
                if not isinstance(details, dict):
                    raise TypeError(
                        f"item details for {item!r} are not an object"
                    )
                entries.append(self._validate_wearable(
                    str(item),
                    name=details.get("name"),
                    kind=details.get("kind"),
                    slot=details.get("slot"),
                    attach_point=details.get("attach_point"),
                ))
        return entries

    # -- confirmations: record ONLY after Core confirms -------------------------
    def _file(self, entry: WornItem, *, attachment: bool) -> WornItem:
        self._outfit[entry.id] = entry
        if attachment or entry.kind == "object" or entry.attach_point is not None:
            self._attachments.add(entry.id)
        else:
            self._attachments.discard(entry.id)
        return entry

    def confirm_worn(self, item: Any, *, name: str | None = None,
                     kind: str | None = None, slot: str | None = None,
                     attach_point: str | None = None,
                     now: float | None = None) -> OutfitChangeRecord:
        """Record a wear ONLY after Core confirms it happened."""
        entry = self._coerce_entry(
            item, name=name, kind=kind, slot=slot,
            attach_point=attach_point,
        )
        self._file(entry, attachment=False)
        record = OutfitChangeRecord(
            kind="worn", timestamp=self._now(now),
            item_id=entry.id, item_name=entry.name,
        )
        self._history.append(record)
        return record

    def confirm_removed(self, item_id: str, *,
                        now: float | None = None) -> OutfitChangeRecord:
        """Record a removal ONLY after Core confirms it. The
        item must be currently worn — confirming a removal of
        something not worn fails closed."""
        key = str(item_id)
        if not self.is_wearing(key):
            raise KeyError(f"item {item_id!r} is not currently worn")
        entry = self._outfit.pop(key)
        self._attachments.discard(key)
        record = OutfitChangeRecord(
            kind="removed", timestamp=self._now(now),
            item_id=entry.id, item_name=entry.name,
        )
        self._history.append(record)
        return record

    def confirm_attached(self, item: Any, *, name: str | None = None,
                         kind: str | None = None,
                         attach_point: str | None = None,
                         now: float | None = None) -> OutfitChangeRecord:
        """Record an attach ONLY after Core confirms it."""
        entry = self._coerce_entry(
            item, name=name, kind=kind, attach_point=attach_point
        )
        self._file(entry, attachment=True)
        record = OutfitChangeRecord(
            kind="attached", timestamp=self._now(now),
            item_id=entry.id, item_name=entry.name,
        )
        self._history.append(record)
        return record

    def confirm_detached(self, item_id: str, *,
                         now: float | None = None) -> OutfitChangeRecord:
        """Record a detach ONLY after Core confirms it. The
        item must be currently attached."""
        key = str(item_id)
        if not self.is_attached(key):
            raise KeyError(f"item {item_id!r} is not currently attached")
        entry = self._outfit.pop(key)
        self._attachments.discard(key)
        record = OutfitChangeRecord(
            kind="detached", timestamp=self._now(now),
            item_id=entry.id, item_name=entry.name,
        )
        self._history.append(record)
        return record

    def _confirm_wholesale(self, items: Any, record_kind: str,
                           now: float | None) -> OutfitChangeRecord:
        entries = self._coerce_entries(items)
        self._outfit = {}
        self._attachments = set()
        for entry in entries:
            self._file(entry, attachment=False)
        self._known = True
        self._stale = False
        record = OutfitChangeRecord(
            kind=record_kind, timestamp=self._now(now),
            item_ids=tuple(e.id for e in entries),
        )
        self._history.append(record)
        return record

    def confirm_outfit(self, items: Any, *,
                       now: float | None = None) -> OutfitChangeRecord:
        """Record a wholesale outfit change ONLY after Core
        confirms it: replaces the outfit wholesale, marks it
        known, and clears staleness."""
        return self._confirm_wholesale(items, "outfit", now)

    def confirm_snapshot(self, items: Any, *,
                         now: float | None = None) -> OutfitChangeRecord:
        """Record an outfit snapshot ONLY after it arrives from
        Core: replaces the outfit wholesale, marks it known,
        and clears staleness."""
        return self._confirm_wholesale(items, "snapshot", now)

    # -- ingest -------------------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: file what a seam event carries.

        Never raises — a malformed or unknown entry is recorded
        in ``rejected`` and returns None, so one bad event cannot
        stop the perception stream (the dispatcher's own
        isolation rule). Confirmation events ARE the Core's
        confirmation: recording the change here is the confirm
        step, not a guess."""
        if event.name not in APPEARANCE_EVENTS:
            self.rejected.append(("not_an_appearance_event", event.name))
            return None
        try:
            handler = {
                "outfit_received": self._ingest_snapshot,
                "item_worn": self._ingest_worn,
                "item_removed": self._ingest_removed,
                "attachment_attached": self._ingest_attached,
                "attachment_detached": self._ingest_detached,
                "appearance_changed": self._ingest_appearance_changed,
            }[event.name]
            return handler(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    def _ingest_snapshot(self, event: SeamEvent) -> OutfitChangeRecord:
        payload = event.payload
        raw = None
        for key in ("outfit", "items", "entries"):
            if isinstance(payload.get(key), list):
                raw = payload[key]
                break
        if raw is None:
            raw = []
        entries = []
        for entry in raw:
            try:
                entries.append(self._entry_from_mapping(entry))
            except (ValueError, TypeError) as exc:
                self.rejected.append((str(exc), event.name))
        return self.confirm_snapshot(entries)

    def _single_entry(self, event: SeamEvent) -> WornItem:
        payload = dict(event.payload)
        for key in ("item", "entry"):
            if isinstance(payload.get(key), dict):
                merged = dict(payload[key])
                merged.update(
                    {k: v for k, v in payload.items() if k not in (key,)}
                )
                return self._entry_from_mapping(merged)
        return self._entry_from_mapping(payload)

    def _ingest_worn(self, event: SeamEvent) -> OutfitChangeRecord:
        return self.confirm_worn(self._single_entry(event))

    def _ingest_attached(self, event: SeamEvent) -> OutfitChangeRecord:
        return self.confirm_attached(self._single_entry(event))

    @staticmethod
    def _payload_item_id(event: SeamEvent) -> str:
        return str(
            event.payload.get("item_id")
            or event.payload.get("id")
            or event.payload.get("uuid")
            or ""
        )

    def _ingest_removed(self, event: SeamEvent) -> OutfitChangeRecord:
        return self.confirm_removed(self._payload_item_id(event))

    def _ingest_detached(self, event: SeamEvent) -> OutfitChangeRecord:
        return self.confirm_detached(self._payload_item_id(event))

    def _ingest_appearance_changed(self, event: SeamEvent) -> AppearanceNotice:
        """An appearance change notice: entries do NOT move.

        The outfit is marked stale (only a known outfit can be
        stale — an unknown one is simply still unknown) and a
        notice is recorded; the next snapshot clears it."""
        notice = AppearanceNotice(
            timestamp=self._now(None), payload=dict(event.payload)
        )
        self._notices.append(notice)
        if self._known:
            self._stale = True
        return notice

    # -- purge ---------------------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident from every record here; count removals.

        Worn/attached items whose creator or received-from
        matches that resident — via fields the snapshot itself
        carried, or via the bound inventory's data for that
        item — are removed, along with every outfit-history
        entry tied to those items. Items tied to anyone else,
        and items with no resident tie carried anywhere, stay."""
        if not resident_id:
            return 0
        key = str(resident_id)

        def _matches(item: WornItem) -> bool:
            if key in (item.creator_id, item.received_from_id):
                return True
            inv = self._from_inventory(item.id)
            return inv is not None and key in (
                inv.creator_id, inv.received_from_id
            )

        removed_ids = {
            item_id for item_id, item in self._outfit.items()
            if _matches(item)
        }
        removed = 0
        for item_id in removed_ids:
            del self._outfit[item_id]
            self._attachments.discard(item_id)
            removed += 1
        kept = [
            r for r in self._history
            if r.item_id not in removed_ids
            and not (set(r.item_ids) & removed_ids)
        ]
        removed += len(self._history) - len(kept)
        self._history = kept
        return removed


__all__ = [
    "APPEARANCE_EVENTS",
    "WEARABLE_KINDS",
    "AppearanceNotice",
    "AppearanceState",
    "AppearanceStore",
    "OutfitChangeRecord",
    "WornItem",
]
