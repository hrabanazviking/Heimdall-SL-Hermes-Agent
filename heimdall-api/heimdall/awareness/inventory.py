"""Inventory as a sense and a hand — census doc 15 slice F3.

Every viewer keeps an Inventory surface: what she has, where it is
filed, and what she can do with it — read a notecard someone sent,
keep a landmark and teleport from it later, give an item to someone
else. Until this slice the seam had none of that: the Core's
``Inventory`` manager is never touched, and doc 03 §5's line that
"a notecard received and read is perception" had no code behind it.

This module is the seam-side store: items arrive as seam events,
are held browsable by folder and kind, searchable by name, and
usable through governor-ready commands that are *built* here and
never executed here.

Bounds, enforced here in the body, not by convention:

- **Unknown kinds and nameless items fail closed.** An item whose
  kind this seam does not understand, or that carries no name, is
  rejected and recorded in ``rejected`` — never filed under a
  guessed meaning.
- **Notecard content is never invented.** :meth:`read_notecard`
  returns only text that actually arrived with the item. If the
  content did not arrive — or the payload says it is not available
  (``content_available`` is false) — it raises an honest error.
- **Giving is recorded only after confirmation.** Building a
  ``give_inventory`` command records nothing; the caller routes it
  through the governor-gated router and calls :meth:`mark_given`
  only on a confirmed Core reply (the record-after-confirmation
  pattern, F1/F2). Giving also does not remove the item from this
  store: Second Life give semantics depend on the item's copy /
  transfer permissions, which this seam does not carry — absent
  that data, deleting locally would be inventing a transfer.
- **There is no delete path at all.** No delete / trash / remove
  method exists on this store — destruction is a pause-line
  (census doc 15, F3: "No delete/trash path in this slice").
  The one removal that does exist is :meth:`purge_resident`, the
  doc-01 per-resident privacy purge, and it removes by *person*,
  never by item.
- **Items are not facts.** An inventory entry is a held object
  record — an id, a name, a kind, a folder — not a claim about
  the world, and nothing here promotes one into one.

Receiving through the offers queue (F1) is by ingest only: an
accepted inventory offer's item arrives here as an
``inventory_item`` event in its own right. This module does not
watch or resolve offers itself.

Scope honesty: this is the API/seam side of F3. The C# Core does
not yet emit inventory events, answer ``give_inventory``, or
teleport by landmark — that is the named Core leg. The event names
added to the seam vocabulary in ``core/models.py`` are the contract
that leg will speak; until it lands, items can be recorded,
browsed, searched, and read entirely on this side, and the give /
teleport commands are ready to route the moment Core can confirm
them. The ``teleport`` command built from a landmark uses the
Core's existing region/coords form when the landmark payload
carries it; a landmark carrying only a SLURL builds a command
carrying that SLURL for the Core leg to resolve.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.models import Command, SeamEvent

# The item kinds this seam files. Anything else fails closed.
ITEM_KINDS = frozenset(
    {
        "object",
        "notecard",
        "landmark",
        "texture",
        "gesture",
        "animation",
        "clothing",
        "bodypart",
        "script",
        "sound",
        "calling_card",
    }
)

# Canonical seam event names this store ingests.
INVENTORY_EVENTS = frozenset(
    {"inventory_item", "inventory_updated", "notecard_received"}
)

DEFAULT_FOLDER = "Objects"


@dataclass
class InventoryItem:
    """One held inventory item — a record, never a fact."""

    id: str
    name: str
    kind: str
    folder: str = DEFAULT_FOLDER
    creator_id: str | None = None
    creator_name: str | None = None
    received_from_id: str | None = None
    received_from_name: str | None = None
    received_at: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    # Notecard text, only for notecards and only when it arrived.
    notecard_text: str | None = None
    # False when the payload says the content did not arrive;
    # for notecards, text presence is what makes content readable.
    content_available: bool = True


@dataclass
class NotecardContent:
    """The text of one notecard, exactly as it arrived."""

    item_id: str
    text: str
    lines: list[str]


@dataclass
class GivenRecord:
    """One confirmed give: item, recipient, when — after the fact."""

    item_id: str
    item_name: str
    recipient_id: str
    given_at: float


@dataclass
class InventoryStore:
    """The inventory store: ingest, browse, read, give — no delete."""

    _clock: Callable[[], float] = field(default=time.time)
    _items: dict[str, InventoryItem] = field(default_factory=dict, init=False)
    _given: list[GivenRecord] = field(default_factory=list, init=False)
    _counter: int = field(default=0, init=False)
    # Fail-closed audit: (reason, detail) for every refused ingest.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    # -- time ---------------------------------------------------------
    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    # -- recording ------------------------------------------------------
    def record(
        self,
        kind: str,
        *,
        name: str,
        item_id: str | None = None,
        folder: str | None = None,
        creator_id: str | None = None,
        creator_name: str | None = None,
        received_from_id: str | None = None,
        received_from_name: str | None = None,
        payload: dict[str, Any] | None = None,
        notecard_text: str | None = None,
        now: float | None = None,
    ) -> InventoryItem:
        """File one item. Raises ValueError on an unknown kind or a
        missing name — callers who cannot raise use ingest_event.

        Re-recording an id that is already held updates that item
        in place (the ``inventory_updated`` shape): fields carried
        by the new record win, fields it omits are kept.
        """
        if kind not in ITEM_KINDS:
            raise ValueError(f"unknown inventory kind: {kind!r}")
        if not name or not str(name).strip():
            raise ValueError("an inventory item without a name cannot be filed")
        body = dict(payload or {})
        text = notecard_text
        if text is None and kind == "notecard":
            raw_text = body.get("notecard_text") or body.get("text") or body.get("content")
            text = None if raw_text is None else str(raw_text)
        if kind != "notecard":
            text = None
        content_available = body.get("content_available")
        if content_available is None:
            content_available = True
        existing = self._items.get(str(item_id)) if item_id else None
        if existing is not None:
            existing.name = str(name)
            existing.kind = kind
            if folder:
                existing.folder = str(folder)
            if creator_id is not None:
                existing.creator_id = str(creator_id)
            if creator_name is not None:
                existing.creator_name = str(creator_name)
            if received_from_id is not None:
                existing.received_from_id = str(received_from_id)
            if received_from_name is not None:
                existing.received_from_name = str(received_from_name)
            existing.payload = body
            if text is not None:
                existing.notecard_text = text
            existing.content_available = bool(content_available)
            return existing
        if item_id:
            new_id = str(item_id)
        else:
            self._counter += 1
            new_id = f"item-{self._counter}"
        item = InventoryItem(
            id=new_id,
            name=str(name),
            kind=kind,
            folder=str(folder) if folder else DEFAULT_FOLDER,
            creator_id=None if creator_id is None else str(creator_id),
            creator_name=None if creator_name is None else str(creator_name),
            received_from_id=(
                None if received_from_id is None else str(received_from_id)
            ),
            received_from_name=(
                None if received_from_name is None else str(received_from_name)
            ),
            received_at=self._now(now),
            payload=body,
            notecard_text=text,
            content_available=bool(content_available),
        )
        self._items[item.id] = item
        return item

    def ingest_event(self, event: SeamEvent) -> InventoryItem | None:
        """Dispatcher handler: file the item a seam event carries.

        Never raises — a malformed, unknown-kind, or nameless item
        is recorded in ``rejected`` and returns None, so one bad
        event cannot stop the perception stream (the dispatcher's
        own isolation rule).
        """
        if event.name not in INVENTORY_EVENTS:
            self.rejected.append(("not_an_inventory_event", event.name))
            return None
        try:
            payload = event.payload
            kind = payload.get("kind") or payload.get("item_kind")
            if event.name == "notecard_received" and not kind:
                kind = "notecard"
            if kind is None:
                self.rejected.append(("inventory item without a kind", event.name))
                return None
            body = {
                k: v
                for k, v in payload.items()
                if k
                not in (
                    "kind",
                    "item_kind",
                    "item_id",
                    "id",
                    "name",
                    "item_name",
                    "folder",
                    "folder_name",
                    "creator_id",
                    "creator_name",
                    "received_from_id",
                    "received_from_name",
                    "sender_id",
                    "sender_name",
                    "from_uuid",
                    "from",
                )
            }
            return self.record(
                str(kind),
                name=str(payload.get("name") or payload.get("item_name") or ""),
                item_id=payload.get("item_id") or payload.get("id"),
                folder=payload.get("folder") or payload.get("folder_name"),
                creator_id=payload.get("creator_id"),
                creator_name=payload.get("creator_name"),
                received_from_id=(
                    payload.get("received_from_id")
                    or payload.get("sender_id")
                    or payload.get("from_uuid")
                ),
                received_from_name=(
                    payload.get("received_from_name")
                    or payload.get("sender_name")
                    or payload.get("from")
                ),
                payload=body,
            )
        except ValueError as exc:
            self.rejected.append((str(exc), event.name))
            return None

    # -- browsing ---------------------------------------------------------
    def get(self, item_id: str) -> InventoryItem | None:
        return self._items.get(str(item_id))

    def list_items(
        self, folder: str | None = None, kind: str | None = None
    ) -> list[InventoryItem]:
        """Held items, in received order, optionally filtered."""
        items = list(self._items.values())
        if folder is not None:
            items = [i for i in items if i.folder == folder]
        if kind is not None:
            items = [i for i in items if i.kind == kind]
        return items

    def folders(self) -> list[str]:
        """Every folder holding at least one item, sorted."""
        return sorted({i.folder for i in self._items.values()})

    def search(self, query: str) -> list[InventoryItem]:
        """Items whose name contains ``query``, case-insensitive."""
        needle = str(query).lower()
        if not needle:
            return []
        return [i for i in self._items.values() if needle in i.name.lower()]

    def list_landmarks(self) -> list[InventoryItem]:
        return self.list_items(kind="landmark")

    # -- notecards ---------------------------------------------------------
    def read_notecard(self, item_id: str) -> NotecardContent:
        """Read one notecard's text, exactly as it arrived.

        Raises KeyError if no such item is held, ValueError if the
        item is not a notecard, and ValueError if its content did
        not arrive (or arrived flagged unavailable) — content is
        never invented to fill the gap.
        """
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"no inventory item with id {item_id!r}")
        if item.kind != "notecard":
            raise ValueError(f"item {item_id!r} is a {item.kind}, not a notecard")
        if not item.content_available or item.notecard_text is None:
            raise ValueError(
                f"notecard {item_id!r} content is not available on this side"
            )
        return NotecardContent(
            item_id=item.id,
            text=item.notecard_text,
            lines=item.notecard_text.splitlines(),
        )

    # -- landmarks: teleport commands ---------------------------------------------------------
    def build_teleport_command(
        self, item_id: str, command_id: str
    ) -> Command:
        """Build (do not execute) the ``teleport`` command for a
        landmark, in the Core's region/coords form when the payload
        carries it, plus the SLURL when it carries one.

        Raises KeyError for a missing item, ValueError if the item
        is not a landmark, and ValueError if the landmark carries
        no destination data at all — there is nowhere honest to
        teleport to.
        """
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"no inventory item with id {item_id!r}")
        if item.kind != "landmark":
            raise ValueError(f"item {item_id!r} is a {item.kind}, not a landmark")
        payload = item.payload
        params: dict[str, Any] = {"item_id": item.id, "landmark_id": item.id}
        region = payload.get("region") or payload.get("region_name")
        if region:
            params["region"] = str(region)
        coords: dict[str, Any] = {}
        raw_coords = payload.get("coords") or payload.get("position")
        if isinstance(raw_coords, dict):
            coords = {
                axis: raw_coords[axis]
                for axis in ("x", "y", "z")
                if axis in raw_coords
            }
        elif isinstance(raw_coords, (list, tuple)) and len(raw_coords) >= 3:
            coords = {"x": raw_coords[0], "y": raw_coords[1], "z": raw_coords[2]}
        for axis in ("x", "y", "z"):
            if axis in payload:
                coords[axis] = payload[axis]
        for axis, value in coords.items():
            if isinstance(value, (int, float)):
                params[axis] = float(value)
        slurl = payload.get("slurl") or payload.get("SLURL")
        if slurl:
            params["slurl"] = str(slurl)
        if not region and not slurl and not coords:
            raise ValueError(
                f"landmark {item_id!r} carries no destination data"
            )
        return Command(id=str(command_id), cmd="teleport", params=params, tier="mind")

    # -- giving ---------------------------------------------------------
    def build_give_command(
        self, item_id: str, recipient_id: str, command_id: str
    ) -> Command:
        """Build (do not send, do not record) the ``give_inventory``
        command for the governor-gated router.

        The give is only recorded once Core confirms it — the
        caller routes this command, then calls :meth:`mark_given`
        on a confirmed reply. Raises KeyError for a missing item
        and ValueError for a missing recipient.
        """
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"no inventory item with id {item_id!r}")
        if not recipient_id:
            raise ValueError("giving an item requires a recipient")
        return Command(
            id=str(command_id),
            cmd="give_inventory",
            params={"item_id": item.id, "recipient_id": str(recipient_id)},
            tier="mind",
        )

    def mark_given(
        self,
        item_id: str,
        recipient_id: str,
        *,
        now: float | None = None,
    ) -> GivenRecord:
        """Record a confirmed give in the given-history.

        Called by the caller ONLY after the Core confirms the give
        was sent. The item itself stays held: without copy/transfer
        permission data this seam cannot honestly say the original
        left (see the module docstring). Raises KeyError for a
        missing item and ValueError for a missing recipient.
        """
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"no inventory item with id {item_id!r}")
        if not recipient_id:
            raise ValueError("giving an item requires a recipient")
        record = GivenRecord(
            item_id=item.id,
            item_name=item.name,
            recipient_id=str(recipient_id),
            given_at=self._now(now),
        )
        self._given.append(record)
        return record

    def given_history(self) -> list[GivenRecord]:
        """Confirmed gives, in the order they were recorded."""
        return list(self._given)

    # -- purge ---------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident's items; return how many entries went.

        Every item received from or created by that resident is
        removed, along with every given-history entry addressed to
        that resident; the return counts both. This is the seam
        side of the per-resident purge duty (doc 01 privacy) — the
        only removal this store performs, and it is by person,
        never a per-item delete.
        """
        if not resident_id:
            return 0
        removed = 0
        for item_id in list(self._items.keys()):
            item = self._items[item_id]
            if resident_id in (item.received_from_id, item.creator_id):
                del self._items[item_id]
                removed += 1
        kept = [g for g in self._given if g.recipient_id != resident_id]
        removed += len(self._given) - len(kept)
        self._given = kept
        return removed
