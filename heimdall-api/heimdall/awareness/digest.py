"""Scene digest — design doc 03 §7.4: what the mind actually "sees".

Renders the presence tracker's state plus caller-supplied place /
self / objects / heard inputs into the compact §7.4 format::

    PLACE: Region "…" · Parcel "…" — <description, 1 line>
    SELF: standing at <128,64,24>, facing N · …
    PEOPLE (say-range): A (2.1 m), B (7 m, …)
    PEOPLE (region): C (45 m) · coarse census: N unidentified agents in region
    OBJECTS OF NOTE: "Campfire (click to sit)" 3 m ahead · …
    HEARD (last 60 s): A: "…" · [object] Greeter: "Welcome!"

Laws carried from doc 03, enforced here, not by convention:

- **§7.7 — the tiers never conflate.** Full-tier avatars are rendered
  by name with their distance/band. Coarse-census dots are rendered
  *only* as a count of unidentified agents — no name, no identity,
  ever. There is no code path that attaches an identity to a coarse
  entry (the coarse tier has no identity fields to attach).
- **Heard is attribution, never fact.** HEARD entries render as
  ``Speaker: "text"`` — an attributed utterance in quotes, exactly as
  doc 03 and the build law require for all inbound SL text. Nothing
  heard is ever rendered as a statement about the world.
- **Ignorance is explicit (§8.10).** Absent data says so: no vision
  input renders ``vision: none (object-data only)``; unknown place /
  self state renders as unknown rather than being invented.
- **Budget.** The digest is built under a configurable budget
  (characters, or approximate tokens at ~4 chars/token). When over
  budget, whole sections drop lowest-priority first — OBJECTS OF
  NOTE before PEOPLE (region), then HEARD, then PEOPLE (say-range)
  last — and PLACE / SELF are never dropped, even if they alone
  exceed the budget (a digest that has lost where she is and who she
  is has lost the point of existing).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .presence import PresenceTracker

DEFAULT_BUDGET_CHARS = 1200
CHARS_PER_TOKEN = 4
HEARD_WINDOW_S = 60.0


@dataclass
class HeardEntry:
    """One heard utterance: speaker + text, attributed, never a fact."""

    speaker: str
    text: str
    is_object: bool = False
    at: float | None = None  # timestamp on the tracker's clock basis

    @classmethod
    def coerce(cls, value: Any) -> HeardEntry:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                speaker=str(value.get("speaker", "unknown")),
                text=str(value.get("text", "")),
                is_object=bool(value.get("is_object", False)),
                at=value.get("at", value.get("timestamp")),
            )
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            return cls(speaker=str(value[0]), text=str(value[1]))
        raise TypeError(f"cannot coerce heard entry from {type(value).__name__}")


def _fmt_pos(position: Any) -> str:
    if position is None:
        return "position unknown"
    try:
        x, y, z = (float(position[0]), float(position[1]), float(position[2]))
    except (TypeError, IndexError, KeyError, ValueError):
        if isinstance(position, dict):
            return _fmt_pos((position["x"], position["y"], position["z"]))
        return "position unknown"
    def _n(v: float) -> str:
        return str(int(v)) if v == int(v) else f"{v:.1f}"
    return f"<{_n(x)},{_n(y)},{_n(z)}>"


def _place_line(place: Any) -> str:
    if place is None:
        return "PLACE: unknown — vision: none (object-data only)"
    if isinstance(place, str):
        return f"PLACE: {place}"
    if isinstance(place, dict):
        region = place.get("region") or place.get("region_name") or "unknown region"
        parcel = place.get("parcel") or place.get("parcel_name")
        head = f'PLACE: Region "{region}"'
        if parcel:
            head += f' · Parcel "{parcel}"'
        description = place.get("description")
        if description:
            head += f" — {description}"
        extras = []
        if place.get("rules"):
            extras.append(f"Rules: {place['rules']}")
        if place.get("virtual_time"):
            extras.append(f"Virtual time: {place['virtual_time']}")
        if place.get("sim"):
            extras.append(f"Sim: {place['sim']}")
        if extras:
            head += "\n  " + " · ".join(str(e) for e in extras)
        return head
    return f"PLACE: {place}"


def _self_line(self_info: Any, tracker: PresenceTracker) -> str:
    if isinstance(self_info, str) and self_info:
        return f"SELF: {self_info}"
    parts: list[str] = []
    if isinstance(self_info, dict):
        if self_info.get("state"):
            parts.append(str(self_info["state"]))
        position = self_info.get("position", tracker.self_position)
        parts.append(f"at {_fmt_pos(position)}")
        if self_info.get("facing"):
            parts.append(f"facing {self_info['facing']}")
        if self_info.get("wearing"):
            parts.append(f"wearing: {self_info['wearing']}")
        if self_info.get("pending"):
            parts.append(f"pending: {self_info['pending']}")
    else:
        parts.append(f"at {_fmt_pos(tracker.self_position)}")
    return "SELF: " + " · ".join(parts)


def _object_text(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        name = obj.get("name") or obj.get("text") or "unnamed object"
        text = f'"{name}"' if obj.get("name") else str(name)
        if obj.get("description"):
            text = f"{text} — {obj['description']}"
        if obj.get("distance_text"):
            text += f" {obj['distance_text']}"
        elif obj.get("distance_m") is not None:
            text += f" {float(obj['distance_m']):.1f} m"
        return text
    return str(obj)


class SceneDigestBuilder:
    """Build §7.4 scene digests from a :class:`PresenceTracker`."""

    def __init__(
        self,
        tracker: PresenceTracker,
        *,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
    ) -> None:
        self.tracker = tracker
        self.budget_chars = budget_chars

    def build(
        self,
        *,
        place: Any = None,
        self_info: Any = None,
        objects: Any = None,
        heard: Any = None,
        vision: str | None = None,
        now: float | None = None,
        budget_chars: int | None = None,
        budget_tokens: int | None = None,
    ) -> str:
        if budget_tokens is not None:
            budget = int(budget_tokens) * CHARS_PER_TOKEN
        elif budget_chars is not None:
            budget = int(budget_chars)
        else:
            budget = self.budget_chars

        tracker = self.tracker
        avatars = tracker.avatars(now=now)

        # PEOPLE sections — full tier only, split by band. Without a
        # self position no band can be computed; everyone then sits
        # in say-range listing distance-less, which is honest (we know
        # who, not where-relative) — document via SELF ignorance line.
        say_people: list[str] = []
        region_people: list[str] = []
        for avatar in avatars:
            distance = tracker.distance_to(avatar)
            band = tracker.band_of(avatar)
            if distance is None or band is None:
                say_people.append(avatar.name)
            elif band in ("whisper", "say"):
                say_people.append(f"{avatar.name} ({distance:.1f} m)")
            else:
                region_people.append(f"{avatar.name} ({distance:.1f} m)")

        coarse_n = tracker.coarse_count(now=now)
        senses = f"vision: {vision}" if vision else "vision: none (object-data only)"

        # Sections in render order, each with a drop priority:
        # higher number drops earlier. PLACE/SELF/SENSES: never drop.
        sections: list[tuple[str, int, str]] = [
            ("place", -1, _place_line(place)),
            ("self", -1, _self_line(self_info, tracker)),
            (
                "people_say",
                3,
                "PEOPLE (say-range): "
                + (" · ".join(say_people) if say_people else "none known"),
            ),
            (
                "people_region",
                2,
                self._people_region_line(region_people, coarse_n),
            ),
            (
                "objects",
                4,
                "OBJECTS OF NOTE: "
                + (
                    " · ".join(_object_text(o) for o in objects)
                    if objects
                    else "none known"
                ),
            ),
            (
                "heard",
                1,
                self._heard_line(heard, now),
            ),
            ("senses", -1, f"SENSES: {senses}"),
        ]

        lines = [text for _key, _prio, text in sections]
        text = "\n".join(lines)
        if len(text) <= budget:
            return text
        # Drop lowest-priority (highest number) sections first, one at
        # a time, re-measuring — objects before people, never PLACE/SELF.
        remaining = list(sections)
        while len("\n".join(t for _k, _p, t in remaining)) > budget:
            droppable = [(p, i) for i, (_k, p, _t) in enumerate(remaining) if p >= 0]
            if not droppable:
                break
            _prio, idx = max(droppable)  # highest priority number, latest such
            del remaining[idx]
        return "\n".join(t for _k, _p, t in remaining)

    @staticmethod
    def _people_region_line(region_people: list[str], coarse_n: int) -> str:
        parts: list[str] = []
        if region_people:
            parts.append(" · ".join(region_people))
        if coarse_n:
            # Count ONLY. No names exist for coarse dots and none are
            # invented (§7.7).
            parts.append(
                f"coarse census: {coarse_n} unidentified agents in region"
            )
        if not parts:
            return "PEOPLE (region): none known · coarse census: 0 unidentified agents in region"
        return "PEOPLE (region): " + " · ".join(parts)

    @staticmethod
    def _heard_line(heard: Any, now: float | None) -> str:
        entries: list[HeardEntry] = []
        for raw in heard or []:
            entry = HeardEntry.coerce(raw)
            if (
                entry.at is not None
                and now is not None
                and now - float(entry.at) > HEARD_WINDOW_S
            ):
                continue  # outside the last-60s window
            entries.append(entry)
        if not entries:
            return "HEARD (last 60 s): nothing heard"
        rendered = []
        for entry in entries:
            speaker = f"[object] {entry.speaker}" if entry.is_object else entry.speaker
            rendered.append(f'{speaker}: "{entry.text}"')
        return "HEARD (last 60 s): " + " · ".join(rendered)


def build_scene_digest(tracker: PresenceTracker, **kwargs: Any) -> str:
    """One-shot convenience: build a digest with a default builder."""
    return SceneDigestBuilder(tracker).build(**kwargs)
