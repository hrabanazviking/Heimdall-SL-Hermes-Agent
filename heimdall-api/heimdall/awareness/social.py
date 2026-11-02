"""Friends & groups surface — census doc 15 slice F4.

Every viewer keeps a Friends & Groups surface: who her friends
are and what she has let them do, which groups she belongs to and
under what title, what those groups have told their members, and
who she has muted or blocked. Until this slice the seam had only
the bare FriendOnline / FriendOffline events: no friends list, no
friendship permissions, no group memberships, no notices inbox —
and, hardest of all, no mute/block honouring, which doc 01 names
a privacy *duty*, not a nicety.

This module is the seam-side store: friends, groups, and notices
arrive as seam events, are held queryable, and are acted on
through governor-ready commands that are *built* here and never
executed here.

Bounds, enforced here in the body, not by convention:

- **Online status is never invented.** A friend's ``online`` is
  ``None`` (unknown) until a friend online/offline event actually
  arrives for them. A list refresh updates names and rights and
  leaves the known presence exactly as it was.
- **Friendship and group changes record only after confirmation.**
  Accepting or declining a friendship offer, offering friendship,
  accepting a group invite, and joining or leaving a group each
  build a governor-ready command (tier="mind", the F1 offers
  shape) and record *nothing*; the caller routes the command and
  calls the matching ``confirm_*`` method only on a confirmed
  Core reply (the record-after-confirmation pattern, F1/F2/F3).
- **Exactly one active group.** At most one membership is flagged
  active at any time: marking one active demotes every other.
- **Mute/block is honoured at ingest.** :meth:`admission_for`
  answers ``"blocked"`` / ``"muted"`` / ``"allow"`` for a sender.
  A blocked resident's group notice is refused into the
  ``rejected`` audit and never stored; a muted resident's notice
  is stored flagged ``muted`` rather than surfaced normally. The
  same helper is the seam's one documented predicate for the
  offers/conversation paths: a blocked sender's offer or message
  is to be refused/flagged, a muted sender's contributions marked
  muted — callers ask this store, never guess.
- **Unknown and malformed entries fail closed.** An event this
  store does not understand, or an entry missing its UUID or
  name, goes into the ``rejected`` audit list and changes
  nothing.
- **Notices and records are not facts.** A group notice is an
  attributed held record — group, sender, subject, time — not a
  claim about the world, and nothing here promotes one into one.

Scope honesty: this is the API/seam side of F4. The C# Core does
not yet emit friend/group list events, group notices, or mute
lists, nor answer ``offer_friendship`` / ``join_group`` /
``leave_group`` — that is the named Core leg (a purplelongship
slice, like F1–F3's). The event names added to the seam
vocabulary in ``core/models.py`` are the contract that leg will
speak; friendship offers themselves already ride F1's vocabulary
(``FriendshipOfferReceived`` / ``friendship_offer``), so this
slice adds no duplicate name for them. Until the Core leg lands,
friends, groups, notices, and mutes can be recorded and queried
entirely on this side, and every action command is ready to route
the moment Core can confirm it.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..core.models import Command, SeamEvent

# Canonical seam event names this store ingests. Friendship offers
# are NOT here: they are held by the F1 offers queue, and this
# store only builds the resolution commands for them.
SOCIAL_EVENTS = frozenset(
    {
        "friend_list",
        "friend_online",
        "friend_offline",
        "group_list",
        "group_joined",
        "group_left",
        "group_notice",
        "mute_list",
    }
)

# Friend-rights alias keys, flat or under a nested rights dict.
# The canonical seam names come first; the LibreMetaverse-style
# short forms follow.
_ONLINE_KEYS = (
    "can_see_my_online_status",
    "can_see_online_status",
    "can_see_online",
)
_MAP_KEYS = ("can_see_me_on_map", "can_see_on_map")
_MODIFY_KEYS = ("can_modify_my_objects", "can_modify_objects")


def _rights_from(source: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Read the three friend rights from a payload/entry dict."""
    nested = source.get("rights") or source.get("permissions") or {}
    if not isinstance(nested, dict):
        nested = {}

    def pick(keys: tuple[str, ...]) -> bool:
        for key in keys:
            if key in source:
                return bool(source[key])
            if key in nested:
                return bool(nested[key])
        return False

    return pick(_ONLINE_KEYS), pick(_MAP_KEYS), pick(_MODIFY_KEYS)


@dataclass
class Friend:
    """One friend: identity, the rights she holds over me, presence.

    ``online`` is None (unknown) until a presence event arrives —
    never defaulted, never guessed from the list itself.
    """

    id: str
    name: str
    can_see_my_online_status: bool = False
    can_see_me_on_map: bool = False
    can_modify_my_objects: bool = False
    online: bool | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupMembership:
    """One group I belong to: identity, my title, my standing."""

    id: str
    name: str
    title: str | None = None
    # My permissions/abilities summary, exactly as supplied (a
    # copy) — absent data stays an empty dict, never invented.
    permissions: dict[str, Any] = field(default_factory=dict)
    active: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupNotice:
    """One group notice, attributed — a held record, never a fact."""

    id: str
    group_id: str
    sender_id: str
    sender_name: str | None
    subject: str
    timestamp: float
    body: str | None = None
    attachment_ref: str | None = None
    muted: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class MuteEntry:
    """One muted or blocked resident, with the reason if given."""

    resident_id: str
    name: str | None = None
    reason: str | None = None


@dataclass
class SocialStore:
    """The friends & groups store: ingest, query, act, honour."""

    _clock: Callable[[], float] = field(default=time.time)
    _friends: dict[str, Friend] = field(default_factory=dict, init=False)
    _groups: dict[str, GroupMembership] = field(
        default_factory=dict, init=False
    )
    _notices: list[GroupNotice] = field(default_factory=list, init=False)
    _muted: dict[str, MuteEntry] = field(default_factory=dict, init=False)
    _blocked: dict[str, MuteEntry] = field(default_factory=dict, init=False)
    _counter: int = field(default=0, init=False)
    # Fail-closed audit: (reason, detail) for every refused ingest.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    # -- time ---------------------------------------------------------
    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else float(now)

    @staticmethod
    def _payload_timestamp(
        payload: dict[str, Any], fallback: float
    ) -> float:
        raw = payload.get("timestamp")
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw).timestamp()
            except ValueError:
                return fallback
        return fallback

    # -- mute / block: the honouring predicates -------------------------
    def record_mute(
        self,
        resident_id: str,
        *,
        name: str | None = None,
        reason: str | None = None,
    ) -> MuteEntry:
        """Record one muted resident. Raises ValueError without an id."""
        if not resident_id:
            raise ValueError("muting requires a resident id")
        entry = MuteEntry(
            resident_id=str(resident_id),
            name=None if name is None else str(name),
            reason=None if reason is None else str(reason),
        )
        self._muted[entry.resident_id] = entry
        return entry

    def record_block(
        self,
        resident_id: str,
        *,
        name: str | None = None,
        reason: str | None = None,
    ) -> MuteEntry:
        """Record one blocked resident. Raises ValueError without an id."""
        if not resident_id:
            raise ValueError("blocking requires a resident id")
        entry = MuteEntry(
            resident_id=str(resident_id),
            name=None if name is None else str(name),
            reason=None if reason is None else str(reason),
        )
        self._blocked[entry.resident_id] = entry
        return entry

    def is_muted(self, resident_id: str) -> bool:
        return str(resident_id) in self._muted

    def is_blocked(self, resident_id: str) -> bool:
        return str(resident_id) in self._blocked

    def admission_for(self, sender_id: str) -> str:
        """How the seam treats one sender: allow / muted / blocked.

        The single documented helper the offers and conversation
        paths ask: a ``"blocked"`` sender's offer or message is
        refused/flagged, a ``"muted"`` sender's contributions are
        marked muted rather than surfaced normally, and everyone
        else is ``"allow"``. Blocked wins when both lists hold a
        resident — the stronger refusal governs.
        """
        if self.is_blocked(sender_id):
            return "blocked"
        if self.is_muted(sender_id):
            return "muted"
        return "allow"

    def muted_residents(self) -> list[MuteEntry]:
        return list(self._muted.values())

    def blocked_residents(self) -> list[MuteEntry]:
        return list(self._blocked.values())

    # -- friends: recording ------------------------------------------------
    def record_friend(
        self,
        friend_id: str,
        *,
        name: str,
        can_see_my_online_status: bool = False,
        can_see_me_on_map: bool = False,
        can_modify_my_objects: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> Friend:
        """File one friend. Raises ValueError on a missing id/name.

        Re-recording a held friend updates name and rights in
        place and keeps the known presence: a list refresh is not
        a presence event and must not invent one.
        """
        if not friend_id:
            raise ValueError("a friend without an id cannot be filed")
        if not name or not str(name).strip():
            raise ValueError("a friend without a name cannot be filed")
        body = dict(payload or {})
        existing = self._friends.get(str(friend_id))
        if existing is not None:
            existing.name = str(name)
            existing.can_see_my_online_status = bool(
                can_see_my_online_status
            )
            existing.can_see_me_on_map = bool(can_see_me_on_map)
            existing.can_modify_my_objects = bool(can_modify_my_objects)
            existing.payload = body
            return existing
        friend = Friend(
            id=str(friend_id),
            name=str(name),
            can_see_my_online_status=bool(can_see_my_online_status),
            can_see_me_on_map=bool(can_see_me_on_map),
            can_modify_my_objects=bool(can_modify_my_objects),
            online=None,
            payload=body,
        )
        self._friends[friend.id] = friend
        return friend

    def _record_friend_entry(self, entry: Any) -> Friend:
        if not isinstance(entry, dict):
            raise TypeError("friend entry is not an object")
        online, on_map, modify = _rights_from(entry)
        return self.record_friend(
            str(
                entry.get("friend_id")
                or entry.get("id")
                or entry.get("uuid")
                or ""
            ),
            name=str(entry.get("name") or entry.get("friend_name") or ""),
            can_see_my_online_status=online,
            can_see_me_on_map=on_map,
            can_modify_my_objects=modify,
            payload={
                k: v
                for k, v in entry.items()
                if k
                not in (
                    "friend_id",
                    "id",
                    "uuid",
                    "name",
                    "friend_name",
                    "rights",
                    "permissions",
                )
                and k not in _ONLINE_KEYS + _MAP_KEYS + _MODIFY_KEYS
            },
        )

    def get_friend(self, friend_id: str) -> Friend | None:
        return self._friends.get(str(friend_id))

    def list_friends(self) -> list[Friend]:
        """Held friends, in the order they were first recorded."""
        return list(self._friends.values())

    def apply_friend_presence(self, friend_id: str, online: bool) -> Friend:
        """Record one presence event for a held friend.

        Raises KeyError for a resident who is not a held friend —
        presence for a stranger invents no friendship here.
        """
        friend = self.get_friend(friend_id)
        if friend is None:
            raise KeyError(f"no friend with id {friend_id!r}")
        friend.online = bool(online)
        return friend

    # -- friendship actions: build, then confirm -------------------------
    def build_offer_friendship_command(
        self, resident_id: str, command_id: str
    ) -> Command:
        """Build (do not send, do not record) the
        ``offer_friendship`` command for the governor-gated router.

        Offering friendship records no friend locally: the
        friendship exists on this side only when Core confirms it
        (a friend list / :meth:`confirm_friendship_accepted`).
        Raises ValueError for a missing resident.
        """
        if not resident_id:
            raise ValueError("offering friendship requires a resident")
        return Command(
            id=str(command_id),
            cmd="offer_friendship",
            params={"resident_id": str(resident_id)},
            tier="mind",
        )

    def build_accept_friendship_command(
        self, offer_id: str, sender_id: str, command_id: str
    ) -> Command:
        """Build the F1-shaped ``offer_respond`` accept command for
        a held friendship offer — without resolving or recording
        anything. The caller routes it, then calls
        :meth:`confirm_friendship_accepted` on a confirmed reply.
        """
        return self._build_offer_response(
            offer_id, sender_id, command_id, kind="friendship",
            decision="accept",
        )

    def build_decline_friendship_command(
        self, offer_id: str, sender_id: str, command_id: str
    ) -> Command:
        """Build the F1-shaped ``offer_respond`` decline command
        for a held friendship offer — without resolving or
        recording anything. Declining needs no confirmation record:
        no friendship ever existed on this side.
        """
        return self._build_offer_response(
            offer_id, sender_id, command_id, kind="friendship",
            decision="decline",
        )

    def _build_offer_response(
        self,
        offer_id: str,
        sender_id: str,
        command_id: str,
        *,
        kind: str,
        decision: str,
        group_id: str | None = None,
    ) -> Command:
        if not offer_id:
            raise ValueError("answering an offer requires its id")
        if not sender_id:
            raise ValueError("answering an offer requires its sender")
        params: dict[str, Any] = {
            "offer_id": str(offer_id),
            "kind": kind,
            "decision": decision,
            "sender_id": str(sender_id),
        }
        if group_id:
            params["group_id"] = str(group_id)
        return Command(
            id=str(command_id), cmd="offer_respond", params=params,
            tier="mind",
        )

    def confirm_friendship_accepted(
        self,
        friend_id: str,
        *,
        name: str,
        can_see_my_online_status: bool = False,
        can_see_me_on_map: bool = False,
        can_modify_my_objects: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> Friend:
        """Record a friendship ONLY after Core confirms the accept.

        Called by the caller once the ``offer_respond`` accept (or
        an ``offer_friendship``) is confirmed — the seam side of
        the record-after-confirmation pattern.
        """
        return self.record_friend(
            friend_id,
            name=name,
            can_see_my_online_status=can_see_my_online_status,
            can_see_me_on_map=can_see_me_on_map,
            can_modify_my_objects=can_modify_my_objects,
            payload=payload,
        )

    # -- groups: recording ---------------------------------------------------
    def record_group(
        self,
        group_id: str,
        *,
        name: str,
        title: str | None = None,
        permissions: dict[str, Any] | None = None,
        active: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> GroupMembership:
        """File one membership. Raises ValueError on missing id/name.

        ``active=True`` demotes every other membership first: the
        exactly-one-active invariant is kept here, in the body.
        Re-recording updates in place; omitting ``active`` keeps
        the current flag.
        """
        if not group_id:
            raise ValueError("a group without an id cannot be filed")
        if not name or not str(name).strip():
            raise ValueError("a group without a name cannot be filed")
        body = dict(payload or {})
        perms = dict(permissions or {})
        existing = self._groups.get(str(group_id))
        if existing is not None:
            existing.name = str(name)
            if title is not None:
                existing.title = str(title)
            if permissions is not None:
                existing.permissions = perms
            existing.payload = body
            if active:
                self._activate(existing)
            return existing
        group = GroupMembership(
            id=str(group_id),
            name=str(name),
            title=None if title is None else str(title),
            permissions=perms,
            active=False,
            payload=body,
        )
        self._groups[group.id] = group
        if active:
            self._activate(group)
        return group

    def _activate(self, group: GroupMembership) -> None:
        for other in self._groups.values():
            other.active = other is group

    def _record_group_entry(self, entry: Any) -> GroupMembership:
        if not isinstance(entry, dict):
            raise TypeError("group entry is not an object")
        perms = entry.get("permissions") or entry.get("my_permissions")
        if perms is not None and not isinstance(perms, dict):
            perms = {"summary": perms}
        return self.record_group(
            str(
                entry.get("group_id")
                or entry.get("id")
                or entry.get("uuid")
                or ""
            ),
            name=str(entry.get("name") or entry.get("group_name") or ""),
            title=entry.get("title") or entry.get("my_title"),
            permissions=perms,
            active=bool(entry.get("active")),
            payload={
                k: v
                for k, v in entry.items()
                if k
                not in (
                    "group_id",
                    "id",
                    "uuid",
                    "name",
                    "group_name",
                    "title",
                    "my_title",
                    "permissions",
                    "my_permissions",
                    "active",
                )
            },
        )

    def get_group(self, group_id: str) -> GroupMembership | None:
        return self._groups.get(str(group_id))

    def list_groups(self) -> list[GroupMembership]:
        """Held memberships, in the order first recorded."""
        return list(self._groups.values())

    def active_group(self) -> GroupMembership | None:
        """The one active membership, or None when none is active."""
        for group in self._groups.values():
            if group.active:
                return group
        return None

    def set_active_group(self, group_id: str) -> GroupMembership:
        """Mark one held membership active, demoting all others.

        A local standing change only (which title she currently
        shows) — raises KeyError for a group she does not belong
        to; it builds no command and invents no membership.
        """
        group = self.get_group(group_id)
        if group is None:
            raise KeyError(f"no group membership with id {group_id!r}")
        self._activate(group)
        return group

    # -- group actions: build, then confirm --------------------------------
    def build_join_group_command(
        self, group_id: str, command_id: str
    ) -> Command:
        """Build (do not send, do not record) the ``join_group``
        command. Membership is recorded only by
        :meth:`confirm_group_joined` after Core confirms.
        """
        if not group_id:
            raise ValueError("joining a group requires its id")
        return Command(
            id=str(command_id),
            cmd="join_group",
            params={"group_id": str(group_id)},
            tier="mind",
        )

    def confirm_group_joined(
        self,
        group_id: str,
        *,
        name: str,
        title: str | None = None,
        permissions: dict[str, Any] | None = None,
        active: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> GroupMembership:
        """Record a membership ONLY after Core confirms the join."""
        return self.record_group(
            group_id,
            name=name,
            title=title,
            permissions=permissions,
            active=active,
            payload=payload,
        )

    def build_leave_group_command(
        self, group_id: str, command_id: str
    ) -> Command:
        """Build (do not send, do not mutate) the ``leave_group``
        command for a held membership. The membership is removed
        only by :meth:`confirm_group_left` after Core confirms.
        Raises KeyError for a group she does not belong to.
        """
        if self.get_group(group_id) is None:
            raise KeyError(f"no group membership with id {group_id!r}")
        return Command(
            id=str(command_id),
            cmd="leave_group",
            params={"group_id": str(group_id)},
            tier="mind",
        )

    def confirm_group_left(self, group_id: str) -> GroupMembership:
        """Remove a membership ONLY after Core confirms the leave.

        If the left group was the active one, no group is active
        afterwards — a successor is never guessed. Raises
        KeyError for a group she does not belong to.
        """
        group = self.get_group(group_id)
        if group is None:
            raise KeyError(f"no group membership with id {group_id!r}")
        del self._groups[group.id]
        return group

    def build_accept_group_invite_command(
        self,
        offer_id: str,
        sender_id: str,
        command_id: str,
        *,
        group_id: str | None = None,
    ) -> Command:
        """Build the F1-shaped ``offer_respond`` accept command
        for a held group invitation — without resolving or
        recording anything. Membership lands only via
        :meth:`confirm_group_joined` after Core confirms.
        """
        return self._build_offer_response(
            offer_id, sender_id, command_id, kind="group_invite",
            decision="accept", group_id=group_id,
        )

    def build_decline_group_invite_command(
        self,
        offer_id: str,
        sender_id: str,
        command_id: str,
        *,
        group_id: str | None = None,
    ) -> Command:
        """Build the F1-shaped ``offer_respond`` decline command
        for a held group invitation — recording nothing.
        """
        return self._build_offer_response(
            offer_id, sender_id, command_id, kind="group_invite",
            decision="decline", group_id=group_id,
        )

    # -- group notices inbox ---------------------------------------------------
    def record_group_notice(
        self,
        group_id: str,
        *,
        sender_id: str,
        subject: str,
        sender_name: str | None = None,
        body: str | None = None,
        attachment_ref: str | None = None,
        notice_id: str | None = None,
        payload: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> GroupNotice:
        """File one group notice, attributed to its group/sender.

        Raises ValueError on a missing group, sender, or subject,
        and ValueError for a *blocked* sender — a blocked
        resident's notice is refused here, never filed (the
        admission helper, enforced in the body). A *muted*
        sender's notice is filed flagged ``muted``.
        """
        if not group_id:
            raise ValueError("a group notice without a group cannot be filed")
        if not sender_id:
            raise ValueError("a group notice without a sender cannot be filed")
        if not subject or not str(subject).strip():
            raise ValueError("a group notice without a subject cannot be filed")
        admission = self.admission_for(str(sender_id))
        if admission == "blocked":
            raise ValueError(
                f"group notice from blocked resident {sender_id!r} refused"
            )
        raw = dict(payload or {})
        if notice_id:
            new_id = str(notice_id)
        else:
            self._counter += 1
            new_id = f"notice-{self._counter}"
        notice = GroupNotice(
            id=new_id,
            group_id=str(group_id),
            sender_id=str(sender_id),
            sender_name=None if sender_name is None else str(sender_name),
            subject=str(subject),
            timestamp=self._payload_timestamp(raw, self._now(now)),
            body=None if body is None else str(body),
            attachment_ref=(
                None if attachment_ref is None else str(attachment_ref)
            ),
            muted=admission == "muted",
            payload=raw,
        )
        self._notices.append(notice)
        return notice

    def notices_for(self, group_id: str) -> list[GroupNotice]:
        """One group's notices, in arrival order — re-readable."""
        return [n for n in self._notices if n.group_id == str(group_id)]

    def list_notices(self) -> list[GroupNotice]:
        """Every held notice, in arrival order."""
        return list(self._notices)

    # -- ingest ------------------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: file what a seam event carries.

        Never raises — a malformed, unknown, or blocked entry is
        recorded in ``rejected`` and returns None, so one bad
        event cannot stop the perception stream (the dispatcher's
        own isolation rule). List events file each valid entry and
        audit each bad one, returning the entries filed.
        """
        if event.name not in SOCIAL_EVENTS:
            self.rejected.append(("not_a_social_event", event.name))
            return None
        try:
            handler = {
                "friend_list": self._ingest_friend_list,
                "friend_online": self._ingest_presence,
                "friend_offline": self._ingest_presence,
                "group_list": self._ingest_group_list,
                "group_joined": self._ingest_group_joined,
                "group_left": self._ingest_group_left,
                "group_notice": self._ingest_group_notice,
                "mute_list": self._ingest_mute_list,
            }[event.name]
            return handler(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    def _ingest_friend_list(self, event: SeamEvent) -> list[Friend]:
        entries = event.payload.get("friends")
        if not isinstance(entries, list):
            self.rejected.append(("friend list without entries", event.name))
            return []
        filed = []
        for entry in entries:
            try:
                filed.append(self._record_friend_entry(entry))
            except (ValueError, TypeError) as exc:
                self.rejected.append((str(exc), event.name))
        return filed

    def _ingest_presence(self, event: SeamEvent) -> Friend:
        payload = event.payload
        friend_id = (
            payload.get("friend_id")
            or payload.get("friend_uuid")
            or payload.get("sender_id")
            or payload.get("from_uuid")
            or payload.get("id")
            or ""
        )
        return self.apply_friend_presence(
            str(friend_id), online=event.name == "friend_online"
        )

    def _ingest_group_list(self, event: SeamEvent) -> list[GroupMembership]:
        entries = event.payload.get("groups")
        if not isinstance(entries, list):
            self.rejected.append(("group list without entries", event.name))
            return []
        filed = []
        for entry in entries:
            try:
                filed.append(self._record_group_entry(entry))
            except (ValueError, TypeError) as exc:
                self.rejected.append((str(exc), event.name))
        return filed

    def _ingest_group_joined(self, event: SeamEvent) -> GroupMembership:
        # A joined event IS the Core's confirmation — recording
        # the membership here is the confirm step, not a guess.
        return self._record_group_entry(event.payload)

    def _ingest_group_left(self, event: SeamEvent) -> GroupMembership:
        payload = event.payload
        group_id = (
            payload.get("group_id")
            or payload.get("id")
            or payload.get("uuid")
            or ""
        )
        return self.confirm_group_left(str(group_id))

    def _ingest_group_notice(self, event: SeamEvent) -> GroupNotice:
        payload = event.payload
        body = {
            k: v
            for k, v in payload.items()
            if k
            not in (
                "group_id",
                "group_uuid",
                "sender_id",
                "sender_name",
                "from_uuid",
                "from",
                "subject",
                "body",
                "message",
                "text",
                "attachment_ref",
                "attachment_id",
                "attachment",
                "notice_id",
                "id",
            )
        }
        body_with_stamp = dict(body)
        if "timestamp" in payload:
            body_with_stamp["timestamp"] = payload["timestamp"]
        return self.record_group_notice(
            str(
                payload.get("group_id") or payload.get("group_uuid") or ""
            ),
            sender_id=str(
                payload.get("sender_id") or payload.get("from_uuid") or ""
            ),
            sender_name=payload.get("sender_name") or payload.get("from"),
            subject=str(payload.get("subject") or ""),
            body=(
                payload.get("body")
                or payload.get("message")
                or payload.get("text")
            ),
            attachment_ref=(
                payload.get("attachment_ref")
                or payload.get("attachment_id")
                or payload.get("attachment")
            ),
            notice_id=payload.get("notice_id") or payload.get("id"),
            payload=body_with_stamp,
        )

    def _ingest_mute_list(self, event: SeamEvent) -> list[MuteEntry]:
        payload = event.payload
        filed: list[MuteEntry] = []
        handled = False
        for key, recorder in (
            ("muted", self.record_mute),
            ("blocked", self.record_block),
        ):
            entries = payload.get(key)
            if entries is None:
                continue
            handled = True
            if not isinstance(entries, list):
                self.rejected.append(
                    (f"mute list {key!r} is not a list", event.name)
                )
                continue
            # A list event is a snapshot for its category: it
            # replaces what this side held for that category.
            target = self._muted if key == "muted" else self._blocked
            target.clear()
            for entry in entries:
                try:
                    filed.append(self._record_mute_entry(entry, recorder))
                except (ValueError, TypeError) as exc:
                    self.rejected.append((str(exc), event.name))
        raw_entries = payload.get("entries")
        if isinstance(raw_entries, list):
            handled = True
            for entry in raw_entries:
                try:
                    if not isinstance(entry, dict):
                        raise TypeError("mute entry is not an object")
                    kind = str(entry.get("kind") or entry.get("type") or "")
                    if kind in ("muted", "mute"):
                        recorder = self.record_mute
                    elif kind in ("blocked", "block"):
                        recorder = self.record_block
                    else:
                        raise ValueError(
                            f"unknown mute entry kind: {kind!r}"
                        )
                    filed.append(self._record_mute_entry(entry, recorder))
                except (ValueError, TypeError) as exc:
                    self.rejected.append((str(exc), event.name))
        if not handled:
            self.rejected.append(("mute list without entries", event.name))
        return filed

    @staticmethod
    def _record_mute_entry(
        entry: Any, recorder: Callable[..., MuteEntry]
    ) -> MuteEntry:
        if isinstance(entry, str):
            return recorder(entry)
        if not isinstance(entry, dict):
            raise TypeError("mute entry is not an object")
        return recorder(
            str(
                entry.get("resident_id")
                or entry.get("id")
                or entry.get("uuid")
                or ""
            ),
            name=entry.get("name") or entry.get("resident_name"),
            reason=entry.get("reason"),
        )

    # -- purge ---------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident from every record here; count removals.

        The friend entry, the mute entry, the block entry, and
        every group notice that resident sent are removed. This
        is the seam side of the per-resident purge duty (doc 01
        privacy) — by person, across the whole surface.
        """
        if not resident_id:
            return 0
        removed = 0
        if self._friends.pop(str(resident_id), None) is not None:
            removed += 1
        if self._muted.pop(str(resident_id), None) is not None:
            removed += 1
        if self._blocked.pop(str(resident_id), None) is not None:
            removed += 1
        kept = [n for n in self._notices if n.sender_id != str(resident_id)]
        removed += len(self._notices) - len(kept)
        self._notices = kept
        return removed
