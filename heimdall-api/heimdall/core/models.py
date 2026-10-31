"""Wire models for the Heimdall seam.

Formats are taken verbatim from SL_Heimdall_Full_Architecture_Proposal.md:

- §7.1 WebSocket API: command / response / error / unsolicited-event JSON.
- §7.2 IPC protocol: newline-delimited JSON over Unix sockets
  (command socket ``/tmp/heimdall.sock``, event socket
  ``/tmp/heimdall.sock.events``).
- §7.3 LibreMetaverse -> Heimdall.Core event mapping: the Core-side
  event names the dispatcher normalises for downstream consumers.

Standard library only — the seam must run anywhere Python 3.11+ runs,
including purplelongship, with no install step beyond this package.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# §7.3 Core event name -> canonical seam event name.
# Downstream consumers (the WYRD bridge, the mind) see only canonical
# names; Core keeps its own vocabulary and needs zero knowledge of them.
CORE_EVENT_ALIASES: dict[str, str] = {
    "ChatReceived": "chat",
    "ChatSent": "chat_sent",
    "IMReceived": "im",
    "IMSent": "im_sent",
    "GroupChatReceived": "group_chat",
    "RegionChanged": "region_changed",
    "ParcelEntered": "parcel_changed",
    "ParcelChanged": "parcel_changed",
    "AvatarAppeared": "avatar_appeared",
    "AvatarUpdated": "avatar_appeared",
    "AvatarLeft": "avatar_left",
    "SelfPositionUpdated": "self_position_updated",
    "FriendOnline": "friend_online",
    "FriendOffline": "friend_offline",
    "AppearanceChanged": "appearance_changed",
    "ObjectInfo": "object_info",
    "ObjectAppeared": "object_info",
    "Disconnected": "disconnected",
    "LoginSuccess": "login_success",
    "LoginFailed": "login_failed",
    "TeleportProgress": "teleport_progress",
    "TeleportCompleted": "teleport_completed",
    # F1 offers vocabulary (doc 03 §6, census doc 15): the per-kind
    # offer events and the generic OfferReceived form. The C# Core
    # does not emit these yet — these names are the contract its F1
    # leg will speak, settled here so the seam side can be built and
    # proven ahead of it (see awareness/offers.py).
    "OfferReceived": "offer_received",
    "TeleportLureReceived": "teleport_lure",
    "FriendshipOfferReceived": "friendship_offer",
    "GroupInvitationReceived": "group_invite",
    "InventoryOfferReceived": "inventory_offer",
    "CallingCardOfferReceived": "calling_card_offer",
    "ScriptDialogReceived": "script_dialog",
    "ScriptPermissionRequest": "script_permission_request",
    # F3 inventory vocabulary (census doc 15): an item arriving or
    # changing, and a notecard arriving. The C# Core does not emit
    # these yet — these names are the contract its F3 leg will
    # speak, settled here so the seam side can be built and proven
    # ahead of it (see awareness/inventory.py).
    "InventoryItemReceived": "inventory_item",
    "InventoryUpdated": "inventory_updated",
    "NotecardReceived": "notecard_received",
    # F4 friends & groups vocabulary (census doc 15): the friends
    # and group lists, group join/leave confirmations, group
    # notices, and the mute/block list. FriendOnline / FriendOffline
    # above already carry presence, and friendship offers already
    # ride the F1 vocabulary (FriendshipOfferReceived) — neither is
    # duplicated here. The C# Core does not emit these yet — these
    # names are the contract its F4 leg will speak, settled here so
    # the seam side can be built and proven ahead of it (see
    # awareness/social.py).
    "FriendListReceived": "friend_list",
    "GroupListReceived": "group_list",
    "GroupJoined": "group_joined",
    "GroupLeft": "group_left",
    "GroupNoticeReceived": "group_notice",
    "MuteListReceived": "mute_list",
    # F5 places & discovery vocabulary (census doc 15): region /
    # map data, search results, and the home-set confirmation.
    # TeleportCompleted above already carries arrivals — it is
    # not duplicated here. The C# Core does not emit these yet —
    # these names are the contract its F5 leg will speak, settled
    # here so the seam side can be built and proven ahead of it
    # (see awareness/places.py).
    "RegionInfoReceived": "region_info",
    "MapRegionReceived": "region_info",
    "SearchResultsReceived": "search_results",
    "HomeLocationSet": "home_set",
    # F6 object-interaction vocabulary (census doc 15): touch and
    # object-targeted-sit confirmations, and animation/gesture
    # confirmations. ObjectInfo / ObjectAppeared above already
    # carry object data, and ScriptDialogReceived above already
    # carries script dialogs (F1 offers) — neither is duplicated
    # here. The C# Core does not emit these yet — these names are
    # the contract its F6 leg will speak, settled here so the seam
    # side can be built and proven ahead of it (see
    # awareness/objects.py).
    "ObjectTouched": "object_touched",
    "ObjectSatOn": "object_sat_on",
    "AnimationStarted": "animation_started",
    "AnimationStopped": "animation_stopped",
    "GesturePlayed": "gesture_played",
    # F7 appearance vocabulary (census doc 15): the outfit
    # snapshot, single-item worn/removed and attached/detached
    # confirmations. AppearanceChanged above already carries
    # appearance-change notices — it is not duplicated here.
    # The C# Core does not emit these yet — these names are the
    # contract its F7 leg will speak, settled here so the seam
    # side can be built and proven ahead of it (see
    # awareness/appearance.py).
    "OutfitReceived": "outfit_received",
    "ItemWorn": "item_worn",
    "ItemRemoved": "item_removed",
    "AttachmentAttached": "attachment_attached",
    "AttachmentDetached": "attachment_detached",
    # F8 money-as-a-sense vocabulary (census doc 15): the balance
    # event and the transaction event — sense-half only. There is
    # deliberately no spend vocabulary here: no pay/buy event or
    # confirmation exists in this slice, and the governor's
    # money-command default-deny stands untouched. The C# Core
    # does not emit these yet — these names are the contract its
    # F8 leg will speak, settled here so the seam side can be
    # built and proven ahead of it (see awareness/money.py).
    "BalanceReceived": "balance_received",
    "TransactionReceived": "transaction_received",
    # API-form names (the §7.1 unsolicited format and the Phase I0
    # replay fixture both use these) converge on the same canonical
    # vocabulary. This map mirrors the alias set the WYRD bridge's
    # translator accepts, verified 2026-10-29 — every event the
    # bridge can translate must survive this seam, never dead-letter.
    "chat": "chat",
    "im": "im",
    "group_chat": "group_chat",
    "login": "login_success",
    "login_success": "login_success",
    "logout": "disconnected",
    "region_changed": "region_changed",
    "parcel_changed": "parcel_changed",
    "parcel_entered": "parcel_changed",
    "avatar_appeared": "avatar_appeared",
    "avatar_updated": "avatar_appeared",
    "avatar_left": "avatar_left",
    "self_position_updated": "self_position_updated",
    "position_updated": "self_position_updated",
    "position_update": "self_position_updated",
    "object_info": "object_info",
    "object_appeared": "object_info",
    "friend_online": "friend_online",
    "friend_offline": "friend_offline",
    "appearance_changed": "appearance_changed",
    "teleport_completed": "teleport_completed",
    "teleport_progress": "teleport_progress",
    "disconnected": "disconnected",
    "offer_received": "offer_received",
    "teleport_lure": "teleport_lure",
    "friendship_offer": "friendship_offer",
    "group_invite": "group_invite",
    "inventory_offer": "inventory_offer",
    "calling_card_offer": "calling_card_offer",
    "script_dialog": "script_dialog",
    "script_permission_request": "script_permission_request",
    "inventory_item": "inventory_item",
    "inventory_updated": "inventory_updated",
    "notecard_received": "notecard_received",
    "friend_list": "friend_list",
    "group_list": "group_list",
    "group_joined": "group_joined",
    "group_left": "group_left",
    "group_notice": "group_notice",
    "mute_list": "mute_list",
    "region_info": "region_info",
    "search_results": "search_results",
    "home_set": "home_set",
    "object_touched": "object_touched",
    "object_sat_on": "object_sat_on",
    "animation_started": "animation_started",
    "animation_stopped": "animation_stopped",
    "gesture_played": "gesture_played",
    "outfit_received": "outfit_received",
    "item_worn": "item_worn",
    "item_removed": "item_removed",
    "attachment_attached": "attachment_attached",
    "attachment_detached": "attachment_detached",
    "balance_received": "balance_received",
    "transaction_received": "transaction_received",
}


def canonical_event_name(raw_name: str) -> str | None:
    """Return the canonical seam name, or None for an unknown event.

    Unknown events fail closed: the dispatcher dead-letters them
    instead of guessing a meaning for data it does not understand.
    """
    return CORE_EVENT_ALIASES.get(raw_name)


@dataclass
class SeamEvent:
    """One inbound event, normalised for downstream consumers."""

    name: str  # canonical name
    raw_name: str  # name exactly as it arrived from Core
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, wire: dict[str, Any]) -> "SeamEvent | None":
        raw = wire.get("event") or wire.get("type_event") or ""
        if not isinstance(raw, str) or not raw:
            return None
        canonical = canonical_event_name(raw)
        if canonical is None:
            return None
        payload = {k: v for k, v in wire.items() if k not in ("event", "type_event")}
        return cls(name=canonical, raw_name=raw, payload=payload)


@dataclass
class Command:
    """One outbound command in §7.1 form.

    ``tier`` is the doc-05 latency tier that produced the command:
    "mind" for the inhabitant herself, "reflex" for body-level
    reflexes. Reflex tiers NEVER produce words — the governor
    enforces that, not convention.
    """

    id: str
    cmd: str
    params: dict[str, Any] = field(default_factory=dict)
    tier: str = "mind"

    @classmethod
    def from_wire(cls, wire: dict[str, Any]) -> "Command":
        params = {
            k: v for k, v in wire.items() if k not in ("id", "cmd", "tier")
        }
        return cls(
            id=str(wire.get("id", "")),
            cmd=str(wire.get("cmd", "")),
            params=params,
            tier=str(wire.get("tier", "mind")),
        )

    def to_ipc_line(self) -> bytes:
        """Serialise as one §7.2 NDJSON command line."""
        wire = {"id": self.id, "cmd": self.cmd, **self.params}
        return (json.dumps(wire, separators=(",", ":")) + "\n").encode("utf-8")


def parse_ndjson_line(line: bytes) -> dict[str, Any] | None:
    """Parse one NDJSON line; None for blank/malformed lines."""
    text = line.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None
