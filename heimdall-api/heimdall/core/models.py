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
