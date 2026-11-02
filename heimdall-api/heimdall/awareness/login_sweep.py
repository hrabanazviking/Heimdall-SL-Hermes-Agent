"""Astrid-Law login sweep — no silent shut-out (Phase 3 slice 2).

The Astrid Law comes from Samtal (design doc 05 §5, row 3): after
Astrid's seat was silently wounded — seated but unable to speak, with
nobody told — the rule became that presence must heal itself *out
loud*. A silent shut-out / silent login failure must never happen to
anyone again. Every login failure is loud, surfaced, and retried or
explicitly blocked — never silently swallowed.

This module is the Python-side owner of that law for the Second Life
body. :class:`LoginSupervisor`:

1. Attempts login; on success → ``LOGGED_IN``, and the success is
   recorded.
2. On ``credentials_unavailable`` → ``BLOCKED_CREDENTIALS``
   immediately, with **no retries** (retrying cannot fix a missing
   credential) and a loud surfaced record.
3. On transient failures (a ``LoginFailed`` event, a transport error,
   a timeout, a ``Disconnected`` while a session was expected) →
   retries with exponential backoff (configurable base / cap /
   max-attempts; injectable clock and sleeper so tests never really
   sleep), and **every** failure produces a surfaced record carrying
   the attempt number, the reason, and the next-retry delay. There is
   no code path in this module where a failure is swallowed silently.
4. After max attempts → ``FAILED_LOUD`` (not silent idle): a final
   surfaced record stating the attempts are exhausted and why. The
   supervisor stays queryable — :meth:`LoginSupervisor.status`
   returns the state, the attempt count, the last error, and the full
   failure history.
5. An unexpected ``Disconnected`` after a successful login is a
   shut-out: surfaced immediately, and re-login is attempted under the
   same backoff policy. An intentional :meth:`logout` suppresses
   re-login (deliberate silence is respected, per doc 05 §5 row 3c).
6. Kill switch: if the message governor's kill switch is engaged,
   login attempts pause (state ``HELD``) rather than hammering the
   Core. The governor is the same object the command router gates
   through, when it is reachable; otherwise it is an optional
   dependency.

Placement: this lives in ``heimdall.awareness`` (not ``heimdall.core``)
because it is Phase 3 *awareness hardening* — the slice sits beside
presence tiers and the scene digest, it consumes Core events
(``login_success`` / ``login_failed`` / ``disconnected``) the way the
rest of awareness consumes the world, and it owns no transport of its
own.

Transport: the supervisor drives the Core **only** through the
existing seam interfaces — a :class:`CommandRouter` if one is given,
or a ``CommandTransport`` (the :class:`IpcClient` protocol) wrapped in
a ``CommandRouter`` with the governor. No parallel transport is
invented here. The style is synchronous, matching ``IpcClient``,
``CommandRouter`` and the runtime's explicit event pump: events from
the Core event socket are fed in through :meth:`handle_event` (or
:meth:`attach` to an :class:`EventDispatcher`), exactly as the runtime
pump feeds the dispatcher.

Standard library only, Python ≥3.11, like the rest of the seam.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..api.command_router import CommandRouter, CommandTransport
from ..core.governor import MessageGovernor
from ..core.models import SeamEvent

logger = logging.getLogger(__name__)

# -- states ---------------------------------------------------------------
IDLE = "idle"
ATTEMPTING = "attempting"
LOGGED_IN = "logged_in"
BLOCKED_CREDENTIALS = "blocked_credentials"
FAILED_LOUD = "failed_loud"
HELD = "held"
LOGGED_OUT = "logged_out"

CREDENTIALS_UNAVAILABLE = "credentials_unavailable"
KILL_SWITCH_ENGAGED = "kill_switch_engaged"

__all__ = [
    "ATTEMPTING",
    "BLOCKED_CREDENTIALS",
    "FAILED_LOUD",
    "HELD",
    "IDLE",
    "LOGGED_IN",
    "LOGGED_OUT",
    "LoginRecord",
    "LoginSupervisor",
    "LoginSweepConfig",
]


@dataclass(frozen=True)
class LoginSweepConfig:
    """Backoff / retry policy for the login sweep."""

    base_delay_s: float = 2.0
    max_delay_s: float = 60.0
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise ValueError("backoff delays must be >= 0")

    def delay_for_attempt(self, attempt: int) -> float:
        """Exponential backoff after the 1-based ``attempt`` fails."""
        return min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))


@dataclass(frozen=True)
class LoginRecord:
    """One surfaced record — the loud part of the Astrid Law.

    Every outcome of the supervisor (success, failure, blocked, held,
    shut-out, exhaustion, logout) produces at least one of these,
    appended to the history, handed to the ``on_record`` callback, and
    written to the module log. A failure with no record does not exist
    in this module.
    """

    kind: str
    attempt: int
    reason: str = ""
    next_retry_delay_s: float | None = None
    state: str = IDLE
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "attempt": self.attempt,
            "reason": self.reason,
            "next_retry_delay_s": self.next_retry_delay_s,
            "state": self.state,
            "timestamp": self.timestamp,
        }


@dataclass
class LoginSupervisor:
    """Owns the login lifecycle on the Python side of the seam.

    Construct with exactly one of ``router`` / ``transport``. With a
    bare transport, a :class:`CommandRouter` is built over it with the
    given (or a fresh) governor, so login commands pass the same
    governor gate as every other command.
    """

    router: CommandRouter | None = None
    transport: CommandTransport | None = None
    governor: MessageGovernor | None = None
    config: LoginSweepConfig = field(default_factory=LoginSweepConfig)
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    on_record: Callable[[LoginRecord], None] | None = None

    _state: str = field(default=IDLE, init=False)
    _attempts: int = field(default=0, init=False)
    _last_error: str = field(default="", init=False)
    _session_expected: bool = field(default=False, init=False)
    _intentional_logout: bool = field(default=False, init=False)
    _in_login_loop: bool = field(default=False, init=False)
    _records: list[LoginRecord] = field(default_factory=list, init=False)
    _failures: list[LoginRecord] = field(default_factory=list, init=False)
    _command_seq: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.router is None and self.transport is None:
            raise ValueError("LoginSupervisor needs a router or a transport")
        if self.router is not None and self.transport is not None:
            raise ValueError("pass a router or a transport, not both")
        if self.governor is None and self.router is not None:
            # Reuse the governor the router already gates through, if
            # it is reachable (it is a private attribute of the slice-1
            # router; reaching for it is optional, never required).
            self.governor = getattr(self.router, "_governor", None)
        if self.router is None:
            governor = self.governor or MessageGovernor()
            self.governor = governor
            self.router = CommandRouter(self.transport, governor)  # type: ignore[arg-type]

    # -- surfacing --------------------------------------------------------
    def _surface(
        self,
        kind: str,
        *,
        attempt: int,
        reason: str = "",
        next_retry_delay_s: float | None = None,
        is_failure: bool = False,
    ) -> LoginRecord:
        record = LoginRecord(
            kind=kind,
            attempt=attempt,
            reason=reason,
            next_retry_delay_s=next_retry_delay_s,
            state=self._state,
            timestamp=float(self.clock()),
        )
        self._records.append(record)
        if is_failure:
            self._failures.append(record)
            self._last_error = reason
        if kind in ("login_success",):
            self._last_error = ""
        logger.info("login sweep: %s", record.to_dict())
        if self.on_record is not None:
            try:
                self.on_record(record)
            except Exception:  # a broken callback must not silence the record; it is already in the history and the log
                logger.exception("login sweep on_record callback failed")
        return record

    @property
    def records(self) -> list[LoginRecord]:
        return list(self._records)

    @property
    def failure_history(self) -> list[LoginRecord]:
        return list(self._failures)

    @property
    def state(self) -> str:
        return self._state

    def status(self) -> dict[str, Any]:
        """Queryable at all times, including after FAILED_LOUD."""
        return {
            "state": self._state,
            "logged_in": self._state == LOGGED_IN,
            "attempts": self._attempts,
            "attempt_count": self._attempts,
            "last_error": self._last_error,
            "session_expected": self._session_expected,
            "failures": [r.to_dict() for r in self._failures],
            "failure_history": [r.to_dict() for r in self._failures],
            "history": [r.to_dict() for r in self._records],
            "records": [r.to_dict() for r in self._records],
        }

    # -- kill switch --------------------------------------------------------
    def _kill_switch_engaged(self) -> bool:
        return bool(self.governor is not None and self.governor.kill_switch_engaged)

    # -- the login loop -----------------------------------------------------
    def login(self) -> dict[str, Any]:
        """Drive login to success, blocked, held, or FAILED_LOUD.

        Synchronous, per the seam's style. Every iteration surfaces at
        least one record; the method never returns silently after a
        failure.
        """
        if self._state == LOGGED_IN and self._session_expected:
            return self.status()
        if self._in_login_loop:
            # Re-entrant call (e.g. an event arriving mid-attempt):
            # the outer loop owns retrying; do not start a nested one.
            return self.status()
        self._intentional_logout = False
        self._in_login_loop = True
        try:
            return self._login_loop()
        finally:
            self._in_login_loop = False

    # Alias with the vocabulary callers naturally reach for.
    ensure_logged_in = login

    def _login_loop(self) -> dict[str, Any]:
        assert self.router is not None
        attempt_in_cycle = 0
        while attempt_in_cycle < self.config.max_attempts:
            if self._kill_switch_engaged():
                self._state = HELD
                self._surface(
                    "held",
                    attempt=self._attempts,
                    reason=KILL_SWITCH_ENGAGED,
                    is_failure=True,
                )
                return self.status()

            attempt_in_cycle += 1
            self._attempts += 1
            self._state = ATTEMPTING
            attempt = self._attempts
            self._command_seq += 1
            try:
                reply = self.router.route_wire(
                    {"id": f"login-{self._command_seq}", "cmd": "login"}
                )
            except Exception as exc:  # noqa: BLE001 — transport error / timeout: transient, loud (surfaced as a record, never swallowed)
                reason = f"{type(exc).__name__}: {exc}"
                if self._handle_transient(attempt, attempt_in_cycle, reason):
                    return self.status()
                continue

            outcome, reason = self._classify_reply(reply)

            if outcome == "held":
                # The router's governor stopped the command before it
                # reached the Core: that was not a login attempt.
                self._attempts -= 1
                self._state = HELD
                self._surface(
                    "held",
                    attempt=self._attempts,
                    reason=reason or KILL_SWITCH_ENGAGED,
                    is_failure=True,
                )
                return self.status()

            if outcome == "success":
                self._state = LOGGED_IN
                self._session_expected = True
                self._surface("login_success", attempt=attempt)
                return self.status()

            if outcome == "credentials":
                self._state = BLOCKED_CREDENTIALS
                self._session_expected = False
                self._surface(
                    "blocked_credentials",
                    attempt=attempt,
                    reason=CREDENTIALS_UNAVAILABLE,
                    is_failure=True,
                )
                return self.status()

            # Transient failure.
            if self._handle_transient(attempt, attempt_in_cycle, reason):
                return self.status()
        return self.status()  # pragma: no cover - loop always returns

    def _handle_transient(
        self, attempt: int, attempt_in_cycle: int, reason: str
    ) -> bool:
        """Surface one transient failure; True when the cycle is over."""
        exhausted = attempt_in_cycle >= self.config.max_attempts
        delay = None if exhausted else self.config.delay_for_attempt(attempt_in_cycle)
        self._surface(
            "login_failed",
            attempt=attempt,
            reason=reason,
            next_retry_delay_s=delay,
            is_failure=True,
        )
        if exhausted:
            self._state = FAILED_LOUD
            self._surface(
                "failed_loud",
                attempt=attempt,
                reason=(
                    f"login attempts exhausted after {attempt_in_cycle} "
                    f"attempt(s); last error: {reason}"
                ),
                is_failure=True,
            )
            return True
        self.sleeper(delay)  # type: ignore[arg-type]
        return False

    @staticmethod
    def _classify_reply(reply: Any) -> tuple[str, str]:
        """Map a router/Core reply to success/credentials/held/transient."""
        if not isinstance(reply, dict):
            return ("transient", "malformed_login_reply")
        error = str(reply.get("error") or "")
        blocked = str(reply.get("blocked_reason") or "")
        combined = f"{error} {blocked}"
        if CREDENTIALS_UNAVAILABLE in combined:
            return ("credentials", CREDENTIALS_UNAVAILABLE)
        if KILL_SWITCH_ENGAGED in combined:
            return ("held", KILL_SWITCH_ENGAGED)
        if reply.get("status") == "ok":
            result = reply.get("result")
            if isinstance(result, dict) and result.get("logged_in") is False:
                return ("transient", "login_not_confirmed")
            return ("success", "")
        reason = blocked or error or "login_failed"
        if CREDENTIALS_UNAVAILABLE in reason:
            return ("credentials", CREDENTIALS_UNAVAILABLE)
        return ("transient", reason)

    # -- logout -------------------------------------------------------------
    def logout(self) -> dict[str, Any]:
        """Intentional logout: deliberate silence, no re-login."""
        assert self.router is not None
        self._intentional_logout = True
        self._session_expected = False
        was = self._state
        self._state = LOGGED_OUT
        self._command_seq += 1
        try:
            self.router.route_wire(
                {"id": f"logout-{self._command_seq}", "cmd": "logout"}
            )
        except Exception as exc:  # noqa: BLE001 — logout failure is surfaced, never swallowed, but the intent stands
            self._surface(
                "logout_transport_error",
                attempt=self._attempts,
                reason=f"{type(exc).__name__}: {exc}",
                is_failure=True,
            )
        else:
            self._surface("logout", attempt=self._attempts, reason=f"was_{was}")
        return self.status()

    # -- Core events ----------------------------------------------------------
    def attach(self, dispatcher: Any) -> None:
        """Subscribe to login/disconnect events on an EventDispatcher."""
        for name in ("login_success", "login_failed", "disconnected"):
            dispatcher.subscribe(name, self.handle_event)

    def handle_event(self, event: SeamEvent | dict[str, Any]) -> None:
        """Feed one Core event (SeamEvent or raw wire dict) in.

        This is the event-socket side of the sweep, fed by the runtime
        pump / dispatcher. Every branch surfaces a record.
        """
        seam: SeamEvent | None
        if isinstance(event, dict):
            seam = SeamEvent.from_wire(event)
            if seam is None:
                self._surface(
                    "unknown_event",
                    attempt=self._attempts,
                    reason=f"unrecognised event: {event.get('event')!r}",
                    is_failure=True,
                )
                return
        else:
            seam = event

        payload = seam.payload or {}
        reason = str(payload.get("reason") or payload.get("error") or "")

        if seam.name == "login_success":
            self._state = LOGGED_IN
            self._session_expected = True
            self._intentional_logout = False
            self._surface("login_success", attempt=self._attempts)
            return

        if seam.name == "login_failed":
            if CREDENTIALS_UNAVAILABLE in reason:
                self._state = BLOCKED_CREDENTIALS
                self._session_expected = False
                self._surface(
                    "blocked_credentials",
                    attempt=self._attempts,
                    reason=CREDENTIALS_UNAVAILABLE,
                    is_failure=True,
                )
                return
            self._surface(
                "login_failed",
                attempt=self._attempts,
                reason=reason or "login_failed",
                is_failure=True,
            )
            return

        if seam.name == "disconnected":
            if self._intentional_logout or not self._session_expected:
                self._surface(
                    "disconnected_expected",
                    attempt=self._attempts,
                    reason=reason or "logout",
                )
                return
            # A shut-out. Loud first, then re-login under the policy.
            self._state = ATTEMPTING
            self._surface(
                "shut_out",
                attempt=self._attempts,
                reason=reason or "disconnected",
                is_failure=True,
            )
            if self._in_login_loop:
                # The outer login loop will see the failure reply /
                # exception and retry; do not nest a second loop.
                return
            self.login()
            return

        # Any other event is not the sweep's business — but say so in
        # the record stream rather than dropping it on the floor here.
        self._surface(
            "event_ignored",
            attempt=self._attempts,
            reason=f"not a login event: {seam.name}",
        )
