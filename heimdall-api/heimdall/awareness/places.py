"""Places & discovery — census doc 15 slice F5.

Every viewer can *go places* and *find things*: follow a landmark
or a SLURL, go home, look a region up on the map, and search for
people, places, events, and groups. Until this slice the seam had
only the bare ``teleport`` command (region/coords, from Phase 2)
and F3's landmark-to-teleport builder: no SLURL parsing of its
own, no teleport history, no home, no region record, and no
search surface at all.

This module is the seam-side store: destinations, regions, and
search results arrive as seam events or are built as
governor-ready commands, are held queryable, and are acted on
only through commands that are *built* here and never executed
here.

Bounds, enforced here in the body, not by convention:

- **Search is rate-aware from the first slice, never a scrape.**
  Doc 01 records the scripted-agent policy verbatim: "Scripted
  Agents causing unreasonable load on search or the regions in
  which they operate" is a named violation, and scraping
  profiles/search at rates that load the services is a named
  abuse vector to design against. Linden publishes no numeric
  search limit, so the limits here are this seam's own
  conservative defaults (configurable, never presented as
  Linden's numbers): a minimum interval between executed
  searches and a cap per rolling hour, enforced at the
  confirmation step where a search actually goes out.
  :meth:`build_search_command` only *checks* the limiter;
  :meth:`confirm_search` is what consumes a slot.
- **History and home record only after confirmation.** Building
  a teleport command records nothing and moves nothing; a
  teleport enters the history only via :meth:`confirm_teleport`
  or a ``teleport_completed`` event (the Core's own
  confirmation). Home likewise: building ``set_home`` changes
  nothing locally; :meth:`confirm_home_set` (or a ``home_set``
  event) is what makes a destination home, and going home with
  no home known fails closed — no guessed destination.
- **Region data is never invented.** A region's record holds
  exactly what a region/map event carried — name, id, grid
  coordinates, maturity, status — and absent fields stay absent
  (``None`` / empty), never defaulted from the name or a guess.
- **Search results are held records, not facts.** A result is
  what a search answered at a moment in time — kind, id, name,
  and the summary exactly as supplied — attributed to its query,
  re-readable, and never promoted into a claim about the world.
- **Unknown and malformed entries fail closed.** An event this
  store does not understand, a destination with nowhere in it, a
  search of an unknown kind or with an empty query, go into the
  ``rejected`` audit list (or raise at the direct API) and change
  nothing.

Scope honesty: this is the API/seam side of F5. The C# Core
already answers the region/coords ``teleport`` command (Phase 2)
but does not yet emit region-info, search-result, or home-set
events, does not yet resolve a bare SLURL, and does not yet
answer ``search`` / ``set_home`` — that is the named Core leg (a
purplelongship slice, like F1–F4's). The event names added to
the seam vocabulary in ``core/models.py`` are the contract that
leg will speak; ``teleport_completed`` already rode the base
vocabulary and is not duplicated. Until the Core leg lands,
destinations, history, home, regions, and results can be
recorded and queried entirely on this side, and every action
command is ready to route the moment Core can confirm it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlparse

from ..core.models import Command, SeamEvent

# Canonical seam event names this store ingests. Teleport
# completion already rides the base vocabulary
# (TeleportCompleted / teleport_completed) and is ingested here
# without adding a duplicate name in models.py.
PLACES_EVENTS = frozenset(
    {
        "region_info",
        "teleport_completed",
        "search_results",
        "home_set",
    }
)

# The four search kinds doc 15 names for F5.
SEARCH_KINDS = frozenset({"people", "places", "events", "groups"})

# This seam's own conservative search defaults (see the module
# docstring: Linden publishes no numeric limit; these are ours).
DEFAULT_MIN_SEARCH_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_SEARCHES_PER_HOUR = 20

_SLURL_HOSTS = {"maps.secondlife.com", "slurl.com"}


@dataclass
class Destination:
    """One place she can be sent to: region and, when known, a
    position inside it. Absent coordinates stay ``None`` — a
    region-only destination is honest and common (a SLURL with
    no coordinates, a region name spoken in chat)."""

    region: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    slurl: str | None = None

    def to_params(self) -> dict[str, Any]:
        """The Core's existing teleport parameter form."""
        params: dict[str, Any] = {"region": self.region}
        if self.x is not None:
            params["x"] = self.x
        if self.y is not None:
            params["y"] = self.y
        if self.z is not None:
            params["z"] = self.z
        if self.slurl:
            params["slurl"] = self.slurl
        return params


def parse_slurl(text: str) -> Destination | None:
    """Parse a SLURL / map URL / app teleport link, or return None.

    Accepted forms (region names URL-decoded):

    - ``https://maps.secondlife.com/secondlife/<Region>/<x>/<y>/<z>``
    - ``http://slurl.com/secondlife/<Region>/<x>/<y>/<z>``
    - ``secondlife://<Region>/<x>/<y>/<z>``
    - ``secondlife:///app/teleport/<Region>/<x>/<y>/<z>``

    Coordinates are optional and may be partial from the right
    (region-only and region/x/y forms are both honest). Anything
    that is not recognisably one of these forms — a bare region
    name, prose, a foreign URL — returns None: the caller fails
    closed rather than guessing a destination out of text.
    """
    if not text or not isinstance(text, str):
        return None
    candidate = text.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    scheme = parsed.scheme.lower()
    parts = [unquote(p) for p in parsed.path.split("/") if p != ""]
    region: str | None = None
    coords: list[str] = []
    if scheme in ("http", "https") and parsed.netloc.lower() in _SLURL_HOSTS:
        if len(parts) < 2 or parts[0].lower() != "secondlife":
            return None
        region, coords = parts[1], parts[2:]
    elif scheme == "secondlife":
        if parts and parts[0].lower() == "app":
            if len(parts) < 3 or parts[1].lower() != "teleport":
                return None
            region, coords = parts[2], parts[3:]
        elif parsed.netloc:
            region, coords = unquote(parsed.netloc), parts
        elif parts:
            region, coords = parts[0], parts[1:]
        else:
            return None
    else:
        return None
    if not region or not region.strip():
        return None
    if len(coords) > 3:
        return None
    values: list[float | None] = []
    for raw in coords:
        try:
            values.append(float(raw))
        except ValueError:
            return None
    while len(values) < 3:
        values.append(None)
    return Destination(
        region=region.strip(),
        x=values[0],
        y=values[1],
        z=values[2],
        slurl=candidate,
    )


def destination_from_payload(payload: dict[str, Any]) -> Destination | None:
    """Build a Destination from a payload dict, or None.

    A ``slurl`` field is parsed first when it carries a region;
    explicit region/coords fields win for the position. Returns
    None when the payload names no region and carries no
    parseable SLURL — nowhere is nowhere, never a guess.
    """
    if not isinstance(payload, dict):
        return None
    from_slurl: Destination | None = None
    slurl = payload.get("slurl") or payload.get("SLURL")
    if isinstance(slurl, str) and slurl:
        from_slurl = parse_slurl(slurl)
    region = (
        payload.get("region")
        or payload.get("region_name")
        or payload.get("sim_name")
    )

    def _coord(key: str) -> float | None:
        raw = payload.get(key)
        if isinstance(raw, (int, float)):
            return float(raw)
        return None

    if region:
        x = _coord("x")
        y = _coord("y")
        z = _coord("z")
        if from_slurl is not None and from_slurl.region == str(region):
            if x is None:
                x = from_slurl.x
            if y is None:
                y = from_slurl.y
            if z is None:
                z = from_slurl.z
        return Destination(
            region=str(region),
            x=x,
            y=y,
            z=z,
            slurl=str(slurl) if slurl else None,
        )
    return from_slurl


@dataclass
class RegionInfo:
    """One region as a region/map event described it — nothing more."""

    name: str
    id: str | None = None
    grid_x: float | None = None
    grid_y: float | None = None
    maturity: str | None = None
    status: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TeleportRecord:
    """One confirmed arrival: where, when, and by what route."""

    destination: Destination
    timestamp: float
    source: str = "teleport"


@dataclass
class SearchResult:
    """One search answer, held as a record of that search."""

    kind: str
    id: str
    name: str
    query: str
    timestamp: float
    summary: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlaceStore:
    """The places & discovery store: ingest, query, act, pace."""

    _clock: Any = field(default=time.time)
    min_search_interval_seconds: float = DEFAULT_MIN_SEARCH_INTERVAL_SECONDS
    max_searches_per_hour: int = DEFAULT_MAX_SEARCHES_PER_HOUR
    _regions: dict[str, RegionInfo] = field(default_factory=dict, init=False)
    _history: list[TeleportRecord] = field(default_factory=list, init=False)
    _results: list[SearchResult] = field(default_factory=list, init=False)
    _search_times: list[float] = field(default_factory=list, init=False)
    _home: Destination | None = field(default=None, init=False)
    # Fail-closed audit: (reason, detail) for every refused ingest.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else float(now)

    @staticmethod
    def _payload_timestamp(payload: dict[str, Any], fallback: float) -> float:
        raw = payload.get("timestamp")
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw).timestamp()
            except ValueError:
                return fallback
        return fallback

    # -- regions ------------------------------------------------------
    def record_region(
        self,
        name: str,
        *,
        region_id: str | None = None,
        grid_x: float | None = None,
        grid_y: float | None = None,
        maturity: str | None = None,
        status: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RegionInfo:
        """File one region. Raises ValueError without a name.

        Re-recording updates only the fields actually supplied —
        a later sparse event must not erase known data, and no
        field is ever filled by inference.
        """
        if not name or not str(name).strip():
            raise ValueError("a region without a name cannot be filed")
        key = str(name)
        existing = self._regions.get(key)
        if existing is not None:
            if region_id is not None:
                existing.id = str(region_id)
            if grid_x is not None:
                existing.grid_x = float(grid_x)
            if grid_y is not None:
                existing.grid_y = float(grid_y)
            if maturity is not None:
                existing.maturity = str(maturity)
            if status is not None:
                existing.status = str(status)
            if payload is not None:
                existing.payload = dict(payload)
            return existing
        region = RegionInfo(
            name=key,
            id=None if region_id is None else str(region_id),
            grid_x=None if grid_x is None else float(grid_x),
            grid_y=None if grid_y is None else float(grid_y),
            maturity=None if maturity is None else str(maturity),
            status=None if status is None else str(status),
            payload=dict(payload or {}),
        )
        self._regions[region.name] = region
        return region

    def _record_region_entry(self, entry: Any) -> RegionInfo:
        if not isinstance(entry, dict):
            raise TypeError("region entry is not an object")
        grid = entry.get("grid") or entry.get("grid_coords") or {}
        if not isinstance(grid, dict):
            grid = {}

        def _num(*keys: str) -> float | None:
            for source in (entry, grid):
                for key in keys:
                    raw = source.get(key)
                    if isinstance(raw, (int, float)):
                        return float(raw)
            return None

        return self.record_region(
            str(
                entry.get("region")
                or entry.get("region_name")
                or entry.get("sim_name")
                or entry.get("name")
                or ""
            ),
            region_id=(
                entry.get("region_id")
                or entry.get("id")
                or entry.get("uuid")
            ),
            grid_x=_num("grid_x", "region_x", "x"),
            grid_y=_num("grid_y", "region_y", "y"),
            maturity=entry.get("maturity") or entry.get("access"),
            status=entry.get("status"),
            payload={
                k: v
                for k, v in entry.items()
                if k
                not in (
                    "region", "region_name", "sim_name", "name",
                    "region_id", "id", "uuid", "grid", "grid_coords",
                    "grid_x", "grid_y", "region_x", "region_y",
                    "maturity", "access", "status",
                )
            },
        )

    def get_region(self, name_or_id: str) -> RegionInfo | None:
        """Look a region up by exact name, or by its held id."""
        if name_or_id in self._regions:
            return self._regions[name_or_id]
        for region in self._regions.values():
            if region.id is not None and region.id == str(name_or_id):
                return region
        return None

    def list_regions(self) -> list[RegionInfo]:
        """Held regions, in the order they were first recorded."""
        return list(self._regions.values())

    # -- teleport: build, then confirm ---------------------------------
    def build_teleport_command(
        self,
        command_id: str,
        *,
        destination: Destination | None = None,
        region: str | None = None,
        slurl: str | None = None,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
    ) -> Command:
        """Build (do not send, do not record) a ``teleport`` command.

        The destination may be given as a Destination, as
        region/coords, or as a SLURL (parsed here; an unparseable
        SLURL raises ValueError rather than teleporting nowhere).
        Building records no history — the arrival is recorded
        only by :meth:`confirm_teleport` after Core confirms.
        """
        target = destination
        if target is None and slurl:
            target = parse_slurl(slurl)
            if target is None:
                raise ValueError(f"unparseable SLURL: {slurl!r}")
        if target is None:
            if not region or not str(region).strip():
                raise ValueError("teleporting requires a destination")
            target = Destination(
                region=str(region),
                x=None if x is None else float(x),
                y=None if y is None else float(y),
                z=None if z is None else float(z),
            )
        return Command(
            id=str(command_id),
            cmd="teleport",
            params=target.to_params(),
            tier="mind",
        )

    def confirm_teleport(
        self,
        destination: Destination,
        *,
        source: str = "teleport",
        now: float | None = None,
    ) -> TeleportRecord:
        """Record an arrival ONLY after Core confirms it."""
        if destination is None or not destination.region:
            raise ValueError("a confirmed teleport needs a destination")
        record = TeleportRecord(
            destination=destination,
            timestamp=self._now(now),
            source=str(source),
        )
        self._history.append(record)
        return record

    def teleport_history(self) -> list[TeleportRecord]:
        """Confirmed arrivals, in arrival order — re-readable."""
        return list(self._history)

    def last_teleport(self) -> TeleportRecord | None:
        return self._history[-1] if self._history else None

    # -- home: build, then confirm --------------------------------------
    def build_set_home_command(self, command_id: str) -> Command:
        """Build (do not send, do not record) the ``set_home``
        command. Home changes locally only via
        :meth:`confirm_home_set` after Core confirms."""
        return Command(
            id=str(command_id), cmd="set_home", params={}, tier="mind"
        )

    def confirm_home_set(self, destination: Destination) -> Destination:
        """Record home ONLY after Core confirms the set."""
        if destination is None or not destination.region:
            raise ValueError("home needs a destination")
        self._home = destination
        return self._home

    def home(self) -> Destination | None:
        """The known home destination, or None when none is known."""
        return self._home

    def build_go_home_command(self, command_id: str) -> Command:
        """Build the teleport home. Fails closed with ValueError
        when no home is known — never a guessed destination."""
        if self._home is None:
            raise ValueError("no home is known — cannot go home")
        params = self._home.to_params()
        params["source"] = "home"
        return Command(
            id=str(command_id), cmd="teleport", params=params, tier="mind"
        )

    # -- search: rate-aware, build checks, confirm consumes -------------
    def _recent_search_times(self, now: float) -> list[float]:
        cutoff = now - 3600.0
        self._search_times = [t for t in self._search_times if t > cutoff]
        return self._search_times

    def check_search_allowed(self, *, now: float | None = None) -> None:
        """Raise PermissionError when an executed search right now
        would break this seam's conservative pacing: inside the
        minimum interval since the last executed search, or at
        the rolling-hour cap. Checking consumes nothing."""
        moment = self._now(now)
        recent = self._recent_search_times(moment)
        if recent and moment - recent[-1] < float(
            self.min_search_interval_seconds
        ):
            raise PermissionError(
                "search too soon after the previous search "
                f"(< {self.min_search_interval_seconds:g}s interval)"
            )
        if len(recent) >= int(self.max_searches_per_hour):
            raise PermissionError(
                "search cap for the rolling hour reached "
                f"({self.max_searches_per_hour})"
            )

    @staticmethod
    def _validate_search(kind: str, query: str) -> tuple[str, str]:
        kind_key = str(kind or "").strip().lower()
        if kind_key not in SEARCH_KINDS:
            raise ValueError(f"unknown search kind: {kind!r}")
        query_text = str(query or "").strip()
        if not query_text:
            raise ValueError("a search requires a query")
        return kind_key, query_text

    def build_search_command(
        self,
        kind: str,
        query: str,
        command_id: str,
        *,
        now: float | None = None,
    ) -> Command:
        """Build (do not send, do not record) a ``search`` command.

        Validates kind/query and *checks* the rate limiter, but
        consumes no slot and stores no results: the search exists
        only when :meth:`confirm_search` records its answers
        after Core confirms it ran.
        """
        kind_key, query_text = self._validate_search(kind, query)
        self.check_search_allowed(now=now)
        return Command(
            id=str(command_id),
            cmd="search",
            params={"kind": kind_key, "query": query_text},
            tier="mind",
        )

    def confirm_search(
        self,
        kind: str,
        query: str,
        results: list[dict[str, Any]],
        *,
        now: float | None = None,
    ) -> list[SearchResult]:
        """Record one executed search and its answers.

        Consumes a rate-limiter slot (PermissionError when the
        pacing would be broken — an executed search that broke
        the pacing is refused here, not silently filed) and files
        each well-formed result; malformed results go to the
        ``rejected`` audit and file nothing.
        """
        kind_key, query_text = self._validate_search(kind, query)
        moment = self._now(now)
        self.check_search_allowed(now=moment)
        self._search_times.append(moment)
        filed: list[SearchResult] = []
        for entry in results or []:
            try:
                filed.append(
                    self._record_result_entry(entry, kind_key, query_text, moment)
                )
            except (ValueError, TypeError) as exc:
                self.rejected.append((str(exc), "search_results"))
        return filed

    def _record_result_entry(
        self,
        entry: Any,
        kind: str,
        query: str,
        moment: float,
    ) -> SearchResult:
        if not isinstance(entry, dict):
            raise TypeError("search result is not an object")
        result_id = entry.get("id") or entry.get("uuid")
        name = entry.get("name") or entry.get("title")
        if not result_id:
            raise ValueError("a search result without an id cannot be filed")
        if not name or not str(name).strip():
            raise ValueError("a search result without a name cannot be filed")
        result = SearchResult(
            kind=kind,
            id=str(result_id),
            name=str(name),
            query=query,
            timestamp=moment,
            summary=(
                None
                if entry.get("summary") is None and entry.get("description") is None
                else str(entry.get("summary") or entry.get("description"))
            ),
            payload={
                k: v
                for k, v in entry.items()
                if k not in ("id", "uuid", "name", "title", "summary", "description")
            },
        )
        self._results.append(result)
        return result

    def search_results(
        self, kind: str | None = None, query: str | None = None
    ) -> list[SearchResult]:
        """Held results, optionally filtered by kind and/or query,
        in the order their searches confirmed them."""
        out = self._results
        if kind is not None:
            out = [r for r in out if r.kind == str(kind).strip().lower()]
        if query is not None:
            out = [r for r in out if r.query == str(query)]
        return list(out)

    # -- ingest ---------------------------------------------------------
    def ingest_event(self, event: SeamEvent) -> Any:
        """Dispatcher handler: file what a seam event carries.

        Never raises — a malformed or unknown entry is recorded
        in ``rejected`` and returns None, so one bad event cannot
        stop the perception stream (the dispatcher's own
        isolation rule).
        """
        if event.name not in PLACES_EVENTS:
            self.rejected.append(("not_a_places_event", event.name))
            return None
        try:
            handler = {
                "region_info": self._ingest_region_info,
                "teleport_completed": self._ingest_teleport_completed,
                "search_results": self._ingest_search_results,
                "home_set": self._ingest_home_set,
            }[event.name]
            return handler(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.rejected.append((str(exc), event.name))
            return None

    def _ingest_region_info(self, event: SeamEvent) -> Any:
        payload = event.payload
        entries = payload.get("regions")
        if isinstance(entries, list):
            filed = []
            for entry in entries:
                try:
                    filed.append(self._record_region_entry(entry))
                except (ValueError, TypeError) as exc:
                    self.rejected.append((str(exc), event.name))
            return filed
        return self._record_region_entry(payload)

    def _ingest_teleport_completed(
        self, event: SeamEvent
    ) -> TeleportRecord:
        # A completed event IS the Core's confirmation — recording
        # the arrival here is the confirm step, not a guess.
        destination = destination_from_payload(event.payload)
        if destination is None:
            raise ValueError("teleport completed without a destination")
        return self.confirm_teleport(
            destination,
            source=str(event.payload.get("source") or "teleport"),
            now=self._payload_timestamp(
                event.payload, self._now(None)
            )
            if "timestamp" in event.payload
            else None,
        )

    def _ingest_search_results(self, event: SeamEvent) -> list[SearchResult]:
        payload = event.payload
        kind_key, query_text = self._validate_search(
            str(payload.get("kind") or ""), str(payload.get("query") or "")
        )
        entries = payload.get("results")
        if not isinstance(entries, list):
            self.rejected.append(("search results without entries", event.name))
            return []
        # Arriving results are the Core's report of a search that
        # already ran — they are filed without consuming a slot
        # here (pacing governs outbound builds/confirms), each
        # malformed entry audited individually.
        moment = self._payload_timestamp(payload, self._now(None))
        filed: list[SearchResult] = []
        for entry in entries:
            try:
                filed.append(
                    self._record_result_entry(entry, kind_key, query_text, moment)
                )
            except (ValueError, TypeError) as exc:
                self.rejected.append((str(exc), event.name))
        return filed

    def _ingest_home_set(self, event: SeamEvent) -> Destination:
        destination = destination_from_payload(event.payload)
        if destination is None:
            raise ValueError("home set without a destination")
        return self.confirm_home_set(destination)

    # -- purge ------------------------------------------------------------
    def purge_resident(self, resident_id: str) -> int:
        """Remove a resident from every record here; count removals.

        People-search results naming that resident are removed.
        Places, events, and groups are not a resident's records
        and stay; teleport history and home are her own movement
        record, not data about the resident, and also stay.
        """
        if not resident_id:
            return 0
        kept = [
            r
            for r in self._results
            if not (r.kind == "people" and r.id == str(resident_id))
        ]
        removed = len(self._results) - len(kept)
        self._results = kept
        return removed


# Kept for callers that prefer the explicit SLURL spelling.
parse_destination = parse_slurl

__all__ = [
    "DEFAULT_MAX_SEARCHES_PER_HOUR",
    "DEFAULT_MIN_SEARCH_INTERVAL_SECONDS",
    "PLACES_EVENTS",
    "SEARCH_KINDS",
    "Destination",
    "PlaceStore",
    "RegionInfo",
    "SearchResult",
    "TeleportRecord",
    "destination_from_payload",
    "parse_destination",
    "parse_slurl",
]
