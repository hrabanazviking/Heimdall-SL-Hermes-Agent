"""Attended photo handoff — visual capture doc 16 slice V4
(Route D), wired per the doc 17 protocol, seam/API side only.

Doc 16's Route D is the one road to a *true photograph*: a
real graphical viewer renders the scene and its Snapshot
function saves a PNG to disk. In an attended session that
viewer is Volmarr's own viewer, standing in-world beside
Yrsa; a snapshot he saves is a file that can be handed to
Yrsa out-of-world. Doc 17 turned that into a protocol —
capture, spool + naming, a required sidecar, filing as an
attributed visual observation, privacy/purge, and fail-closed
rejection — and named the one code slice it still needed:
this store, plus the digest wiring that lets a state whose
newest image is a V4 photo say so honestly. This module is
that store. No Core (C#) change, no login, and no network
anywhere: a handover arrives here as PNG bytes plus a sidecar
dict a caller already holds, and filing is a local act.

Bounds, enforced here in the body, not by convention:

- **The photograph is Volmarr's snapshot, never "what Yrsa
  sees".** Every record and observation carries
  ``source="attended_snapshot"`` and the label
  ``photograph``, and the observation text attributes the
  image in words to the photographer ("Volmarr's snapshot
  of …", from the sidecar's photographer value). It is
  perception — an attributed observation about a place and
  moment, Phase I0 discipline — never a canonical fact
  merely because it is in a picture, and never a product of
  Yrsa's own session. This route exists only while a human
  viewer is present: it is not ambient vision, it has no
  cadence, and nothing here fetches, captures, or requests
  an image.
- **Handovers fail closed, filing never raises.** A handover
  is rejected — recorded in the ``rejected`` audit with its
  reason, nothing spooled, nothing recorded — when: the
  bytes are not a PNG (the 8-byte PNG magic is checked, as
  V1 checks JPEG magic); the sidecar is missing or not a
  dict; ``source`` is not exactly ``attended_snapshot`` or
  ``label`` is not exactly ``photograph``; ``place_kind`` is
  outside the three sanctioned kinds (``public`` /
  ``attended`` / ``explicitly_requested`` — doc 16's bounds:
  public places, attended moments, or explicitly requested
  captures only); ``captured_at`` is missing or not
  parseable ISO-8601; ``photographer`` is missing or empty;
  or ``residents_visible`` is neither a non-empty list of
  non-empty strings nor exactly the string ``"unknown"``
  (an empty list is rejected — the protocol forbids an
  empty list asserted from a glance). Malformed input types
  (None, wrong types) reject the same way; filing never
  raises and never partially files.
- **Unknowns are stated, never filled.** ``region`` /
  ``grid_x`` / ``grid_y`` / ``camera`` may be null in the
  sidecar; they are held as None — never guessed, never
  defaulted from a previous photo, never inferred from the
  image. An unknown region slugs to ``region-unknown`` in
  the spool filename, stated as unknown.
- **Deduplication is by SHA-256.** The filed record carries
  the photo's SHA-256; a handover whose hash already exists
  is NOT filed twice — it returns a duplicate outcome,
  spools nothing new, and is counted in the store's
  ``duplicates`` audit.
- **Spool naming is the protocol's.** When a spool directory
  is configured, the PNG and its sidecar JSON are written
  together under ``<YYYYMMDD>-<HHMMSS>_<region-slug>_<seq>.png``
  (sidecar: same basename plus ``.json``). The timestamp is
  the sidecar's ``captured_at``, local-naive as reported —
  the wall-clock components exactly as the operator stated
  them, never timezone-converted. The region slug is the
  region name lower-cased with non-alphanumerics collapsed
  to ``-`` (V1's slugging), or ``region-unknown`` when the
  region is null. ``seq`` is a zero-padded per-store counter
  starting at ``001`` that only ever advances on a filed
  photo, so two shots in the same second never collide. A
  photo never files without its sidecar: if either write
  fails, both files are removed and the handover rejects.
- **The vision state names itself honestly.** Before any
  photo is filed the felt vision label is exactly
  ``"none (object-data only)"``; once at least one photo is
  filed it is exactly ``"attended photograph only"`` (doc
  17's wording, shipped by this slice). The scene digest
  takes this label only for a state whose newest image is a
  V4 photo — the caller-supplied condition documented in
  ``digest.py``; an empty store never claims it.
- **Purge is per-resident, and partial is spoken.** A V4
  photo can carry other residents, so the
  ``residents_visible`` list is the purge index:
  :meth:`AttendedPhotoStore.purge_resident` deletes exactly
  the filed photos whose list contains the named resident
  (spool PNG + sidecar files deleted, records removed) and
  returns a report. Photos whose ``residents_visible`` is
  ``"unknown"`` cannot be cleared by a name search, so
  while any such photo remains, the report flags the purge
  as partial with the unknown count — completeness is
  never claimed for them. A resident tied to nothing
  removes 0. Photos are never published, posted, or sent
  anywhere by this module; filing is into Yrsa's own
  perception on Volmarr's own machine only.
- **No spend, no messaging, no governor change.** Disk
  snapshots are free and snapshot-to-inventory (L$10/image)
  is outside this protocol entirely; nothing here builds a
  command, and ``core/governor.py`` is untouched.

Scope honesty: this is V4's code slice, seam side only. No
live handover has been performed through it, no photograph
was taken by building it, and the protocol's first real
handover still happens in an attended session with Volmarr
present, under doc 17's steps. There is no Core leg (the
Core emits nothing this store needs — a handover arrives
from the operator side as bytes + sidecar), no texture sight
(doc 16 V2), and no own-session rendering (V5, gated on
Volmarr's ruling alone). No new seam vocabulary: the
``visual_observation`` vocabulary V1 settled already carries
this store's observations, so ``core/models.py`` is
untouched.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Provenance source + image label for everything V4 holds.
# The label is what the observation IS: a photograph — a true
# photograph, but Volmarr's snapshot through Volmarr's
# viewer, never a product of Yrsa's own session.
ATTENDED_SOURCE = "attended_snapshot"
ATTENDED_LABEL = "photograph"

# The digest vision wordings (doc 17): exactly these strings,
# before and after the first filed photo. The "none" wording
# is the digest's existing posture, shared with V1/V3.
VISION_NONE_LABEL = "none (object-data only)"
VISION_ATTENDED_LABEL = "attended photograph only"

# Doc 16's capture bounds, as doc 17's sidecar states them:
# public places, attended moments, or explicitly requested
# captures only. No wider ambient bound exists until Volmarr
# rules one.
ATTENDED_PLACE_KINDS = frozenset(
    {"public", "attended", "explicitly_requested"}
)

# The sidecar's resident-visibility unknown marker: the exact
# string "unknown" — never an empty list asserted from a
# glance.
RESIDENTS_UNKNOWN = "unknown"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _region_slug(region: str | None) -> str:
    """The spool-filename slug for a region name: lower-cased,
    non-alphanumerics collapsed to ``-`` (V1's slugging). An
    unknown region slugs to ``region-unknown`` — stated,
    never guessed from a previous region."""
    if not region:
        return "region-unknown"
    slug = re.sub(r"[^a-z0-9]+", "-", str(region).strip().lower()).strip("-")
    return slug or "region-unknown"


def _parse_captured_at(raw: Any) -> datetime | None:
    """Parse a sidecar ``captured_at`` strictly as ISO-8601.

    Returns the datetime — its wall-clock components exactly
    as reported, which is what the spool filename carries —
    or None when the value is missing, not a string, or not
    parseable. A trailing ``Z`` is normalised for parsers
    that predate its support in ``fromisoformat``."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _grid_coord(value: Any, label: str) -> int | None:
    """Validate one optional grid coordinate; None stays None.

    A supplied coordinate is used exactly as supplied: a real
    ``int`` passes, an integral ``float`` is accepted (region
    data elsewhere in the seam carries floats), and booleans,
    strings, and non-integral floats fail closed. A missing
    coordinate is None — held unknown, never borrowed from
    another region's record."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"a grid {label} must be an integer or null")
    if isinstance(value, float):
        if not value.is_integer():
            raise TypeError(f"a grid {label} must be an integer or null")
        return int(value)
    if isinstance(value, int):
        return int(value)
    raise TypeError(f"a grid {label} must be an integer or null")


@dataclass
class AttendedPhotoRecord:
    """Provenance for one filed attended photograph — the full
    sidecar as reported, plus where its exact bytes live, its
    byte count, and its SHA-256 (the deduplication key and
    the purge index ride the record, not the filename)."""

    source: str
    label: str
    photographer: str
    region: str | None
    grid_x: int | None
    grid_y: int | None
    camera: Any
    residents_visible: list[str] | str
    place_kind: str
    captured_at: str
    filed_at: float
    spool_path: str | None
    sidecar_path: str | None
    byte_count: int
    sha256: str
    notes: str | None = None

    def to_observation(self) -> AttendedObservation:
        """This photo expressed as a ``visual_observation``:
        an attributed observation whose text names the image
        as the photographer's snapshot — perception about a
        place and moment, never a canonical fact, never
        "what Yrsa sees"."""
        place = (
            f'region "{self.region}"' if self.region else "an unrecorded region"
        )
        text = (
            f"{self.photographer}'s snapshot of {place} "
            f"(place kind: {self.place_kind}), captured at "
            f"{self.captured_at} — an attended photograph taken "
            "through the photographer's viewer."
        )
        return AttendedObservation(
            source=self.source,
            label=self.label,
            photographer=self.photographer,
            region=self.region,
            grid_x=self.grid_x,
            grid_y=self.grid_y,
            camera=self.camera,
            residents_visible=self.residents_visible,
            place_kind=self.place_kind,
            captured_at=self.captured_at,
            spool_path=self.spool_path,
            byte_count=self.byte_count,
            sha256=self.sha256,
            text=text,
        )


@dataclass
class AttendedObservation:
    """One attributed photograph observation, in the existing
    ``visual_observation`` seam vocabulary.

    Perception data about a place and moment, labelled a
    photograph and attributed in words to the photographer's
    snapshot. It is never what Yrsa sees through her own
    session and never a canonical fact."""

    source: str
    label: str
    photographer: str
    region: str | None
    grid_x: int | None
    grid_y: int | None
    camera: Any
    residents_visible: list[str] | str
    place_kind: str
    captured_at: str
    spool_path: str | None
    byte_count: int
    sha256: str
    text: str
    event_name: str = "visual_observation"


@dataclass
class AttendedFiling:
    """The outcome of one filing attempt, exactly one of:

    - ``"filed"`` — the photo was filed; ``record`` is its
      provenance record.
    - ``"duplicate"`` — the photo's SHA-256 already exists;
      nothing was filed or spooled twice, and ``record`` is
      the existing record it duplicates.
    - ``"rejected"`` — the handover failed closed; ``reason``
      names why and ``record`` is None."""

    outcome: str
    record: AttendedPhotoRecord | None
    reason: str | None

    @property
    def is_filed(self) -> bool:
        return self.outcome == "filed"

    @property
    def is_duplicate(self) -> bool:
        return self.outcome == "duplicate"

    @property
    def is_rejected(self) -> bool:
        return self.outcome == "rejected"


@dataclass
class AttendedState:
    """A snapshot of the attended-photo state, honest about
    its gaps: what is filed, what was refused, and how many
    filed photos carry unresolvable resident visibility."""

    known: bool
    vision_label: str
    filed: int
    duplicates_refused: int
    rejected: int
    unknown_photos: int


@dataclass
class AttendedPurgeReport:
    """The honest result of one per-resident purge.

    ``removed`` counts exactly the filed photos deleted.
    ``partial`` is True while any filed photo whose
    ``residents_visible`` is ``"unknown"`` remains — such a
    photo cannot be cleared by a name search, so a purge
    that leaves one is reported partial, never complete;
    ``unknown_photos`` is how many remain."""

    resident: str
    removed: int
    partial: bool
    unknown_photos: int


@dataclass
class AttendedPhotoStore:
    """The attended-photo store: file, observe, purge.

    Handed PNG bytes plus a sidecar dict by a caller (the
    operator side of doc 17's protocol), it validates the
    handover fail-closed, spools the pair with provenance,
    and holds every filed photo as an attributed
    ``visual_observation`` labelled a photograph."""

    spool_dir: Any = None
    on_observation: Any = None
    _clock: Any = field(default=time.time)
    _records: list[AttendedPhotoRecord] = field(
        default_factory=list, init=False
    )
    _seq: int = field(default=0, init=False)
    # Duplicate audit: (sha256, existing spool path) for every
    # handover refused as a duplicate — noted, never re-filed.
    duplicates: list[tuple[str, Any]] = field(default_factory=list, init=False)
    # Fail-closed audit: (reason, detail) for every rejected
    # handover, in the sibling stores' shape.
    rejected: list[tuple[str, Any]] = field(default_factory=list, init=False)

    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else float(now)

    def _reject(self, reason: str, detail: Any) -> AttendedFiling:
        """Record a rejection loudly and return its outcome."""
        self.rejected.append((reason, detail))
        return AttendedFiling(outcome="rejected", record=None, reason=reason)

    # -- honesty: vision state ---------------------------------------------
    @property
    def has_photos(self) -> bool:
        """True only once a photograph has actually been
        filed. Before that there is no attended photograph —
        the vision state is the digest's none posture."""
        return bool(self._records)

    def vision_label(self) -> str:
        """The felt vision label, in doc 17's exact wording:
        ``"attended photograph only"`` once at least one
        photo is filed, else ``"none (object-data only)"``.
        This is the value a digest is handed — via
        ``SceneDigestBuilder.build(..., attended_store=...)``
        for a state whose newest image is a V4 photo."""
        return VISION_ATTENDED_LABEL if self._records else VISION_NONE_LABEL

    def current_state(self) -> AttendedState:
        return AttendedState(
            known=self.has_photos,
            vision_label=self.vision_label(),
            filed=len(self._records),
            duplicates_refused=len(self.duplicates),
            rejected=len(self.rejected),
            unknown_photos=sum(
                1
                for record in self._records
                if record.residents_visible == RESIDENTS_UNKNOWN
            ),
        )

    def describe(self) -> str:
        """Answer "what can you see?" — honestly.

        Before any photo: vision is none (object-data only),
        and that is spoken, never dressed up. After one: a
        photograph exists and is named as what it is — an
        attended photograph, the photographer's snapshot —
        with the filed / duplicate / rejected counts."""
        state = self.current_state()
        if not self._records:
            return (
                f"vision: {state.vision_label} — no attended "
                "photograph has been filed, so there is no "
                f"photograph of anywhere yet. {state.rejected} "
                "handover(s) rejected, "
                f"{state.duplicates_refused} duplicate(s) refused."
            )
        last = self._records[-1]
        place = (
            f'region "{last.region}"' if last.region else "an unrecorded region"
        )
        return (
            f"vision: {state.vision_label} — the latest attended "
            f"photograph is {last.photographer}'s snapshot of "
            f"{place}, {state.filed} photograph(s) on record, "
            f"{state.duplicates_refused} duplicate(s) refused, "
            f"{state.rejected} handover(s) rejected, "
            f"{state.unknown_photos} photograph(s) with unknown "
            "resident visibility. An attended photograph is the "
            "photographer's snapshot, not what I see."
        )

    # -- queries -------------------------------------------------------------
    def records(self) -> list[AttendedPhotoRecord]:
        """All filed photo provenance records, in filing order."""
        return list(self._records)

    def latest_record(self) -> AttendedPhotoRecord | None:
        return self._records[-1] if self._records else None

    def observations(self) -> list[AttendedObservation]:
        """All filed photos as visual observations, in filing
        order — derived from the records, so a purge removes
        a photo's observation with its record by construction."""
        return [record.to_observation() for record in self._records]

    # -- filing ----------------------------------------------------------------
    def file_photo(
        self,
        photo: Any,
        sidecar: Any,
        *,
        now: float | None = None,
    ) -> AttendedFiling:
        """File one attended handover: PNG bytes + sidecar dict.

        Returns an :class:`AttendedFiling` — filed, duplicate,
        or rejected with its reason. Never raises: a malformed
        handover (non-PNG bytes, a missing/invalid sidecar,
        any protocol field failing its rule, malformed input
        types) is recorded in ``rejected`` and files nothing.
        A duplicate (SHA-256 already filed) spools nothing new
        and is recorded in ``duplicates``."""
        # The bytes first: only a real PNG is a photograph
        # under this protocol (magic checked, as V1 checks
        # JPEG magic). Anything else — JPEG bytes from another
        # route, an empty payload, a non-bytes value — rejects.
        if isinstance(photo, (bytearray, memoryview)):
            photo = bytes(photo)
        if not isinstance(photo, bytes):
            return self._reject("invalid_photo_bytes", type(photo).__name__)
        if not photo.startswith(_PNG_MAGIC):
            return self._reject("not_a_png", len(photo))
        # The sidecar: a photo never files without it.
        if sidecar is None:
            return self._reject("missing_sidecar", None)
        if not isinstance(sidecar, dict):
            return self._reject("invalid_sidecar", type(sidecar).__name__)
        # The exact Route D values — no borrowed labels, ever.
        source = sidecar.get("source")
        if source != ATTENDED_SOURCE:
            return self._reject("invalid_source", source)
        label = sidecar.get("label")
        if label != ATTENDED_LABEL:
            return self._reject("invalid_label", label)
        place_kind = sidecar.get("place_kind")
        if not isinstance(place_kind, str) or place_kind not in ATTENDED_PLACE_KINDS:
            return self._reject("invalid_place_kind", place_kind)
        captured_raw = sidecar.get("captured_at")
        captured = _parse_captured_at(captured_raw)
        if captured is None:
            return self._reject("invalid_captured_at", captured_raw)
        photographer_raw = sidecar.get("photographer")
        if not isinstance(photographer_raw, str) or not photographer_raw.strip():
            return self._reject("invalid_photographer", photographer_raw)
        photographer = photographer_raw.strip()
        residents_raw = sidecar.get("residents_visible")
        residents: list[str] | str
        if isinstance(residents_raw, str):
            if residents_raw != RESIDENTS_UNKNOWN:
                return self._reject(
                    "invalid_residents_visible", residents_raw
                )
            residents = RESIDENTS_UNKNOWN
        elif isinstance(residents_raw, list):
            if not residents_raw:
                # Never an empty list asserted from a glance.
                return self._reject("invalid_residents_visible", residents_raw)
            if any(
                not isinstance(name, str) or not name.strip()
                for name in residents_raw
            ):
                return self._reject(
                    "invalid_residents_visible", residents_raw
                )
            residents = list(residents_raw)
        else:
            return self._reject("invalid_residents_visible", residents_raw)
        # Unknowns are held as None — never guessed, never
        # borrowed from a previous photo.
        region_raw = sidecar.get("region")
        if region_raw is None:
            region: str | None = None
        elif isinstance(region_raw, str):
            region = region_raw.strip() or None
        else:
            return self._reject("invalid_region", region_raw)
        try:
            grid_x = _grid_coord(sidecar.get("grid_x"), "x")
            grid_y = _grid_coord(sidecar.get("grid_y"), "y")
        except (ValueError, TypeError) as exc:
            return self._reject(str(exc), None)
        camera = sidecar.get("camera")
        notes_raw = sidecar.get("notes")
        notes = notes_raw if isinstance(notes_raw, str) else None
        # Deduplication by SHA-256: a hash already filed is
        # not filed twice and spools nothing new.
        digest = hashlib.sha256(photo).hexdigest()
        for existing in self._records:
            if existing.sha256 == digest:
                self.duplicates.append((digest, existing.spool_path))
                return AttendedFiling(
                    outcome="duplicate", record=existing, reason="duplicate"
                )
        moment = self._now(now)
        seq = self._seq + 1
        filename = (
            f"{captured:%Y%m%d}-{captured:%H%M%S}_"
            f"{_region_slug(region)}_{seq:03d}.png"
        )
        spool_path: str | None = None
        sidecar_path: str | None = None
        if self.spool_dir is not None:
            png_target = Path(self.spool_dir) / filename
            sidecar_target = png_target.with_suffix(".json")
            try:
                png_target.parent.mkdir(parents=True, exist_ok=True)
                png_target.write_bytes(photo)
                sidecar_target.write_text(
                    json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            except (OSError, TypeError, ValueError) as exc:
                # A photo never files without its sidecar:
                # remove any partial pair and reject whole.
                for target in (png_target, sidecar_target):
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                return self._reject(f"spool_error: {exc}", filename)
            spool_path = str(png_target)
            sidecar_path = str(sidecar_target)
        self._seq = seq
        record = AttendedPhotoRecord(
            source=ATTENDED_SOURCE,
            label=ATTENDED_LABEL,
            photographer=photographer,
            region=region,
            grid_x=grid_x,
            grid_y=grid_y,
            camera=camera,
            residents_visible=residents,
            place_kind=place_kind,
            captured_at=str(captured_raw).strip(),
            filed_at=moment,
            spool_path=spool_path,
            sidecar_path=sidecar_path,
            byte_count=len(photo),
            sha256=digest,
            notes=notes,
        )
        self._records.append(record)
        if self.on_observation is not None:
            self.on_observation(record.to_observation())
        return AttendedFiling(outcome="filed", record=record, reason=None)

    # -- purge -------------------------------------------------------------------
    def purge_resident(self, resident: Any) -> AttendedPurgeReport:
        """Remove one resident's filed photographs; report honestly.

        Exactly the filed photos whose ``residents_visible``
        LIST contains the resident (string match, as listed)
        are removed — spool PNG + sidecar files deleted (a
        file already gone is not an error), records dropped,
        and their observations with them (observations derive
        from records). Photos whose ``residents_visible`` is
        ``"unknown"`` cannot be cleared by a name search and
        are never deleted by one; while any remain, the
        report is partial with their count. A resident tied
        to nothing removes 0."""
        key = "" if resident is None else str(resident)
        removed = 0
        if key:
            kept: list[AttendedPhotoRecord] = []
            for record in self._records:
                if (
                    isinstance(record.residents_visible, list)
                    and key in record.residents_visible
                ):
                    for path in (record.spool_path, record.sidecar_path):
                        if path is not None:
                            try:
                                Path(path).unlink(missing_ok=True)
                            except OSError:
                                pass  # record dropped regardless
                    removed += 1
                else:
                    kept.append(record)
            self._records = kept
        unknown_remaining = sum(
            1
            for record in self._records
            if record.residents_visible == RESIDENTS_UNKNOWN
        )
        return AttendedPurgeReport(
            resident=key,
            removed=removed,
            partial=unknown_remaining > 0,
            unknown_photos=unknown_remaining,
        )


__all__ = [
    "ATTENDED_LABEL",
    "ATTENDED_PLACE_KINDS",
    "ATTENDED_SOURCE",
    "RESIDENTS_UNKNOWN",
    "VISION_ATTENDED_LABEL",
    "VISION_NONE_LABEL",
    "AttendedFiling",
    "AttendedObservation",
    "AttendedPhotoRecord",
    "AttendedPhotoStore",
    "AttendedPurgeReport",
    "AttendedState",
]
