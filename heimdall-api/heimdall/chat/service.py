"""Outbound text chat — three send paths, kept separate on purpose.

Volmarr's order (2026-10-31, attended session): she must have text
chat for IMs *and* for main (local) chat, must be aware of which is
which, and must send to each separately. So this service has exactly
three public send methods — :meth:`ChatService.send_local_chat`,
:meth:`ChatService.send_im`, :meth:`ChatService.send_group_im` —
each hard-wired to its own Core command and its own channel stamp.
There is deliberately **no** generic ``send(channel, ...)``: a caller
cannot pass the wrong channel by accident, and an IM that is missing
its recipient raises before anything is routed — it is never
silently rerouted into public local chat, which is exactly the
failure that channel separation exists to prevent.

Every send goes through the seam's command router, which means the
doc-05 safety laws are enforced in the body, not by the caller:

- the :class:`~heimdall.core.governor.MessageGovernor` gate runs
  first (5,000 messages / UTC day cap, kill switch, reflex tiers
  never speak — this service defaults to the ``mind`` tier, the
  only tier that may produce words);
- the router validates the local-chat speaking mode
  (whisper / say / shout) and fails closed on an invalid one;
- a send is counted by the governor and written to the F2
  conversation log only after the Core confirms it.

A blocked or refused send comes back as a :class:`SendResult` with
``ok`` False and the router's ``blocked_reason`` — the caller always
learns that she did *not* speak, and why.
"""
from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .channels import GROUP_IM, IM, LOCAL_CHAT


class ChatRouter(Protocol):
    """The seam's command router surface (api/command_router.py)."""

    def route_wire(self, wire: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SendResult:
    """The outcome of one channelled send."""

    channel: str  # the channel the caller asked for — always stamped
    ok: bool  # True only when the Core confirmed the send
    reply: dict[str, Any]  # the router's §7.1 response, verbatim

    @property
    def blocked_reason(self) -> str | None:
        """Why the governor/router refused, when it did."""
        reason = self.reply.get("blocked_reason")
        return None if reason is None else str(reason)


class ChatService:
    """The mind's three hands for text chat — one per channel."""

    def __init__(
        self,
        router: ChatRouter,
        *,
        tier: str = "mind",
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._router = router
        self._tier = tier
        self._counter = itertools.count(1)
        self._id_factory = id_factory or (
            lambda: f"chat-{next(self._counter)}"
        )

    # -- the three send paths (one per channel, by design) --------
    def send_local_chat(
        self, text: str, *, mode: str = "say", tier: str | None = None
    ) -> SendResult:
        """Speak in local (main) chat — public, heard by the place."""
        return self._send(
            LOCAL_CHAT,
            "say",
            {"msg": self._require_text(text), "mode": mode},
            tier,
        )

    def send_im(
        self, avatar_uuid: str, text: str, *, tier: str | None = None
    ) -> SendResult:
        """Send a private instant message to one resident, by UUID.

        Fails closed without a recipient: an IM is never rerouted
        to local chat, so a missing UUID raises before routing.
        """
        if not avatar_uuid or not str(avatar_uuid).strip():
            raise ValueError(
                "send_im requires the recipient's avatar_uuid; an "
                "IM without a recipient is refused, never rerouted "
                "to local chat"
            )
        return self._send(
            IM,
            "im",
            {
                "to_uuid": str(avatar_uuid),
                "msg": self._require_text(text),
            },
            tier,
        )

    def send_group_im(
        self, group_uuid: str, text: str, *, tier: str | None = None
    ) -> SendResult:
        """Send a message to one group chat, by group UUID."""
        if not group_uuid or not str(group_uuid).strip():
            raise ValueError(
                "send_group_im requires the group's group_uuid; a "
                "group IM without a group is refused, never "
                "rerouted to local chat"
            )
        return self._send(
            GROUP_IM,
            "group_im",
            {
                "group_uuid": str(group_uuid),
                "msg": self._require_text(text),
            },
            tier,
        )

    # -- internals --------------------------------------------------
    @staticmethod
    def _require_text(text: str) -> str:
        if text is None or not str(text).strip():
            raise ValueError("chat text must be non-empty")
        return str(text)

    def _send(
        self,
        channel: str,
        cmd: str,
        params: dict[str, Any],
        tier: str | None,
    ) -> SendResult:
        wire: dict[str, Any] = {
            "id": self._id_factory(),
            "cmd": cmd,
            "tier": self._tier if tier is None else tier,
            **params,
        }
        reply = self._router.route_wire(wire)
        return SendResult(
            channel=channel, ok=reply.get("status") == "ok", reply=reply
        )
