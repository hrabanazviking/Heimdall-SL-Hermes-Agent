"""Command router — the action side of the seam.

Every outbound command (§7.1 form) passes the safety governor BEFORE
it reaches the IPC client. A blocked command never touches the Core;
the caller gets a §7.1 error response naming the reason. A command the
Core refuses (e.g. an RLV restriction) has its response passed through
verbatim, so ``rlv_blocked`` and the restriction name survive to the
mind exactly as Core reported them.

Only a Core-confirmed *sent* message is counted by the governor — a
blocked or failed send never burns the day's message budget.
"""
from __future__ import annotations

from typing import Any, Protocol

from ..core.governor import MessageGovernor
from ..core.models import Command


class CommandTransport(Protocol):
    def send_command(self, command: Command) -> dict[str, Any]: ...


class CommandRouter:
    def __init__(self, transport: CommandTransport, governor: MessageGovernor) -> None:
        self._transport = transport
        self._governor = governor

    def route_wire(self, wire: dict[str, Any]) -> dict[str, Any]:
        """Route one §7.1 command dict; return a §7.1 response dict."""
        command = Command.from_wire(wire)
        decision = self._governor.check(command)
        if not decision.allowed:
            return {
                "id": command.id,
                "status": "error",
                "cmd": command.cmd,
                "error": f"blocked by safety governor: {decision.reason}",
                "blocked_reason": decision.reason,
            }
        reply = self._transport.send_command(command)
        if reply.get("status") == "ok":
            self._governor.record_sent(command)
        return {"id": command.id, "cmd": command.cmd, **reply}
