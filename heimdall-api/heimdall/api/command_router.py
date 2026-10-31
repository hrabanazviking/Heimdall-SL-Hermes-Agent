"""Command router — the action side of the seam.

Every outbound command (§7.1 form) passes the safety governor BEFORE
it reaches the IPC client. A blocked command never touches the Core;
the caller gets a §7.1 error response naming the reason. A command the
Core refuses (e.g. an RLV restriction) has its response passed through
verbatim, so ``rlv_blocked`` and the restriction name survive to the
mind exactly as Core reported them.

Only a Core-confirmed *sent* message is counted by the governor — a
blocked or failed send never burns the day's message budget.

Speaking modes (census doc 15, slice F2): the ``say``/``chat`` path
carries an explicit ``mode`` — ``whisper`` / ``say`` / ``shout``,
default ``say``. The mode is validated here, at the seam: an invalid
mode fails closed with an error reply and never reaches the Core
(whose own chat-type mapping would otherwise silently coerce it to
normal chat). A valid mode is passed through as the Core's ``type``
parameter (``say`` maps to the Core's ``normal``).

When a conversation log is wired in, a Core-confirmed speech send is
also recorded there as an outbound utterance — only after
confirmation, the same record-after-confirmation pattern the
governor's counting uses (F2, awareness/conversations.py).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ..core.governor import MessageGovernor
from ..core.models import Command

if TYPE_CHECKING:
    # Type-only: awareness.login_sweep imports this module, so a
    # runtime import of the awareness package would cycle. The log
    # is a duck-typed collaborator at runtime (record_outbound).
    from ..awareness.conversations import ConversationLog

# The validated speaking modes and the Core chat ``type`` each maps
# to (Heimdall.Core's SendChat switch: whisper/shout, else normal).
SPEAKING_MODES = frozenset({"whisper", "say", "shout"})
CORE_CHAT_TYPES = {"whisper": "whisper", "say": "normal", "shout": "shout"}
SPEECH_COMMANDS = frozenset({"say", "chat"})


class CommandTransport(Protocol):
    def send_command(self, command: Command) -> dict[str, Any]: ...


def _speech_text(params: dict[str, Any]) -> str:
    text = params.get("msg") or params.get("message") or params.get("text")
    return "" if text is None else str(text)


class CommandRouter:
    def __init__(
        self,
        transport: CommandTransport,
        governor: MessageGovernor,
        *,
        conversation_log: ConversationLog | None = None,
    ) -> None:
        self._transport = transport
        self._governor = governor
        self._log = conversation_log

    def _prepare_speech(self, command: Command) -> dict[str, Any] | None:
        """Validate/normalise a say/chat command's speaking mode.

        Returns an error reply for an invalid mode (fail closed —
        the command never reaches the Core), else None with the
        Core ``type`` parameter set from the validated mode.
        """
        if command.cmd not in SPEECH_COMMANDS:
            return None
        mode = command.params.get("mode", "say")
        if not isinstance(mode, str) or mode not in SPEAKING_MODES:
            return {
                "id": command.id,
                "status": "error",
                "cmd": command.cmd,
                "error": f"invalid speaking mode: {mode!r}",
                "blocked_reason": "invalid_speaking_mode",
            }
        command.params["mode"] = mode
        command.params["type"] = CORE_CHAT_TYPES[mode]
        return None

    def _record_outbound(self, command: Command) -> None:
        """Log a Core-confirmed speech send as an outbound utterance."""
        if self._log is None:
            return
        text = _speech_text(command.params)
        if command.cmd in SPEECH_COMMANDS:
            self._log.record_outbound(
                "local_chat", peer_id="local", text=text
            )
        elif command.cmd == "im":
            peer = command.params.get("to_uuid") or command.params.get("to")
            self._log.record_outbound(
                "im",
                peer_id="" if peer is None else str(peer),
                peer_name=command.params.get("to_name"),
                text=text,
            )
        elif command.cmd == "group_im":
            peer = command.params.get("group_uuid") or command.params.get(
                "group_id"
            )
            self._log.record_outbound(
                "group_chat",
                peer_id="" if peer is None else str(peer),
                peer_name=command.params.get("group_name"),
                text=text,
            )

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
        speech_error = self._prepare_speech(command)
        if speech_error is not None:
            return speech_error
        reply = self._transport.send_command(command)
        if reply.get("status") == "ok":
            self._governor.record_sent(command)
            self._record_outbound(command)
        return {"id": command.id, "cmd": command.cmd, **reply}
