"""HMAC authentication for the §7.1 WebSocket API.

The Full Architecture Proposal (§7.1) specifies the shape of the
authentication exchange — the client's first message is
``{"type": "auth", "token": "<hmac-sha256-token>", "agent": "hermes"}``
— but leaves the HMAC's message undefined. This module settles that
implementation detail (Forge Protocol: Yrsa decides technical
implementation) as follows:

- The shared secret is supplied **out of band** by the operator, via
  the ``HEIMDALL_WS_SECRET`` environment variable at deployment time
  (see slice 4 wiring). It is never written to code, config files in
  this repo, logs, or a command the in-world path can send.
- The expected token for an agent is
  ``HMAC-SHA256(secret, agent-name)`` as a lowercase hex digest,
  compared with :func:`hmac.compare_digest` (constant time).
- A server with no secret **refuses to start**. There is no
  unauthenticated mode and no fallback token: an API that can move a
  body in-world does not run open, even on localhost.

Authentication happens exactly once, as the first message on a
connection. Until it succeeds the connection can reach nothing — no
commands are routed and no events are broadcast to it.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

SECRET_ENV_VAR = "HEIMDALL_WS_SECRET"


class AuthConfigError(RuntimeError):
    """The server cannot authenticate clients as configured."""


class HmacAuthenticator:
    def __init__(self, secret: bytes | str) -> None:
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if not secret:
            raise AuthConfigError(
                "no shared secret configured: the WebSocket API "
                "refuses to run unauthenticated"
            )
        self._secret = secret

    @classmethod
    def from_env(cls, env_var: str = SECRET_ENV_VAR) -> HmacAuthenticator:
        secret = os.environ.get(env_var, "")
        if not secret:
            raise AuthConfigError(
                f"{env_var} is not set: the WebSocket API refuses to "
                "run unauthenticated"
            )
        return cls(secret)

    def token_for(self, agent: str) -> str:
        """The expected §7.1 token for one agent name."""
        return hmac.new(
            self._secret, agent.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def verify_message(self, message: Any) -> str | None:
        """Verify a first-message auth dict.

        Returns the authenticated agent name, or None for anything
        that is not a well-formed auth message with a valid token.
        Fails closed on every malformation.
        """
        if not isinstance(message, dict):
            return None
        if message.get("type") != "auth":
            return None
        agent = message.get("agent")
        token = message.get("token")
        if not isinstance(agent, str) or not agent:
            return None
        if not isinstance(token, str) or not token:
            return None
        if not hmac.compare_digest(token, self.token_for(agent)):
            return None
        return agent
