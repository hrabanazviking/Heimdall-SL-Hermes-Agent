"""Outbound safety governor — doc 05 invariants, enforced in the body.

These rules live in the seam (the body side), so no mind — however it
is prompted, and whoever is talking to it in-world — can waive them:

- **Message governor:** at most 5,000 outbound messages per UTC day
  (Linden Lab's scripted-agent operator cap, doc 01). Chat, IM and
  group chat all count. The counter resets on the UTC day boundary.
- **Kill switch:** when engaged, every outbound command is blocked.
  Engaging/disengaging is an operator act (Volmarr), never a command
  the in-world path can send itself.
- **Reflex tiers never speak:** a command tagged tier="reflex" may
  keep the body alive (movement, heartbeat) but may never produce
  words — say/IM/group-chat commands from a reflex tier are blocked.
  All speech is the inhabitant's own.
- **Payments default-deny:** money commands are blocked unless the
  command carries an explicit operator approval marker.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Command

DAILY_MESSAGE_CAP = 5000

MESSAGE_COMMANDS = frozenset({"chat", "say", "im", "group_chat", "shout", "whisper"})
MONEY_COMMANDS = frozenset({"pay", "give_money", "give_linden"})


@dataclass
class Decision:
    allowed: bool
    reason: str = ""


@dataclass
class MessageGovernor:
    cap: int = DAILY_MESSAGE_CAP
    _day: str = ""
    _count: int = 0
    _kill_switch: bool = False

    # -- operator controls (never reachable from in-world commands) --
    def engage_kill_switch(self) -> None:
        self._kill_switch = True

    def release_kill_switch(self) -> None:
        self._kill_switch = False

    @property
    def kill_switch_engaged(self) -> bool:
        return self._kill_switch

    # -- accounting --
    def _roll_day(self, now: datetime) -> None:
        day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if day != self._day:
            self._day = day
            self._count = 0

    def count_today(self, now: datetime | None = None) -> int:
        self._roll_day(now or datetime.now(timezone.utc))
        return self._count

    # -- the gate every outbound command passes through --
    def check(self, command: Command, *, now: datetime | None = None) -> Decision:
        now = now or datetime.now(timezone.utc)
        self._roll_day(now)

        if self._kill_switch:
            return Decision(False, "kill_switch_engaged")

        if command.cmd in MESSAGE_COMMANDS and command.tier == "reflex":
            # Anti-impersonation rule (doc 05): reflexes keep the body
            # alive; they never produce words or commitments.
            return Decision(False, "reflex_tier_may_not_speak")

        if command.cmd in MONEY_COMMANDS and not command.params.get(
            "operator_approved"
        ):
            return Decision(False, "payment_requires_operator_approval")

        if command.cmd in MESSAGE_COMMANDS and self._count >= self.cap:
            return Decision(False, "daily_message_cap_reached")

        return Decision(True)

    def record_sent(self, command: Command, *, now: datetime | None = None) -> None:
        """Count a message command after Core confirms it was sent."""
        now = now or datetime.now(timezone.utc)
        self._roll_day(now)
        if command.cmd in MESSAGE_COMMANDS:
            self._count += 1
