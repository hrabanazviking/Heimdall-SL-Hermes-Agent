"""Interim chain hardening — HR-007 (responsiveness Part 0).

HR-007 buys safety for the interim live chain *while the hook
path still serves*: the ``galdr-evtcap`` event log and the nerve
hook's small state (watermark + line-keyed receipts) are plain
files today, so a torn write, a flipped byte, or a silent
listener death is invisible until a message is lost. This module
builds the tested integrity machinery a later attended/device
step will wire into those files. **No live wiring lives here**:
nothing in this module touches ``~/hooks``, any device, or any
running service, and the detector below only *decides* — it
sends nothing anywhere.

Three primitives, stdlib only:

1. :class:`ChecksummedRecordStore` — an append-only record store
   where each stored line carries a running SHA-256 chain
   checksum over (previous checksum + sequence + payload).
   Appending first verifies the existing chain and refuses
   (fail-closed, :class:`IntegrityError`) on a corrupt file;
   :meth:`ChecksummedRecordStore.verify` walks the whole file
   and reports the exact first bad line (tamper, truncation /
   torn tail) instead of silently accepting it; reading returns
   only verified records plus a corruption report.

2. :class:`AtomicStateStore` — a small-state store for
   watermark/receipts-style state: write-temp + fsync + atomic
   rename, a checksum embedded in the stored envelope, read-back
   verification on every save, and a previous-good copy retained
   and used (with a visible ``used_fallback`` flag) when the
   current copy fails verification.

3. Dead-listener detection — pure decision logic
   (:func:`is_listener_stale`): stale when
   ``now - last_append_time`` exceeds the named configurable
   threshold (:data:`DEFAULT_DEAD_LISTENER_THRESHOLD_S`) **and**
   the Core reports logged in. A fresh log or a logged-out Core
   is never stale. :class:`DeadListenerMonitor` wraps that logic
   and produces an :class:`DeadListenerAlarm` record when stale
   — a record only; delivering it is a later slice's job.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# The chain starts from this fixed genesis checksum so the first
# record's checksum is reproducible from the payload alone.
GENESIS_CHECKSUM = "0" * 64

# Default staleness threshold for the dead-listener detector, in
# seconds. The interim evtcap appends on every Core event and the
# nerve cadence is 5 s, so a log untouched for a full minute while
# the Core claims to be logged in means the listener is dead, not
# merely quiet. Configurable per monitor / per call.
DEFAULT_DEAD_LISTENER_THRESHOLD_S = 60.0


class IntegrityError(Exception):
    """A stored file failed verification (fail-closed signal)."""


def _chain_checksum(previous: str, seq: int, payload: str) -> str:
    h = hashlib.sha256()
    h.update(previous.encode("utf-8"))
    h.update(b"\x00")
    h.update(str(seq).encode("utf-8"))
    h.update(b"\x00")
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@dataclass(frozen=True)
class CorruptionReport:
    """The verdict of walking a checksummed record file.

    ``ok`` is True only when every line verified. Otherwise
    ``bad_line`` is the exact 1-based line number of the first
    bad line and ``reason`` names the failure class
    (``"checksum_mismatch"``, ``"torn_tail"``, ``"malformed"``,
    ``"sequence_gap"``). ``verified_count`` is how many records
    verified *before* the bad line.
    """

    ok: bool
    total_lines: int = 0
    verified_count: int = 0
    bad_line: int | None = None
    reason: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class VerifiedRead:
    """Records read from a store plus their corruption report."""

    records: list[str] = field(default_factory=list)
    report: CorruptionReport = field(
        default_factory=lambda: CorruptionReport(ok=True)
    )


class ChecksummedRecordStore:
    """Append-only, chain-checksummed record file.

    On-disk format: one JSON object per line,
    ``{"seq": int, "checksum": hex, "payload": str}`` terminated
    by ``"\\n"``. ``seq`` starts at 1 and increments by one;
    ``checksum`` chains over the previous line's checksum.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _scan(self) -> tuple[list[str], CorruptionReport]:
        if not self.path.exists():
            return [], CorruptionReport(ok=True, total_lines=0)
        raw = self.path.read_bytes()
        if raw == b"":
            return [], CorruptionReport(ok=True, total_lines=0)
        torn_tail = not raw.endswith(b"\n")
        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # A trailing newline yields a final empty element that is
        # not a line; without one, the final element is the torn
        # tail itself and is checked (and rejected) below.
        if not torn_tail:
            lines = lines[:-1]
        records: list[str] = []
        previous = GENESIS_CHECKSUM
        for idx, line in enumerate(lines):
            lineno = idx + 1
            if torn_tail and idx == len(lines) - 1:
                return records, CorruptionReport(
                    ok=False,
                    total_lines=len(lines),
                    verified_count=len(records),
                    bad_line=lineno,
                    reason="torn_tail",
                    detail="final line is not newline-terminated "
                    "(truncated / torn write)",
                )
            try:
                obj = json.loads(line)
                seq = obj["seq"]
                checksum = obj["checksum"]
                payload = obj["payload"]
                if not (
                    isinstance(seq, int)
                    and isinstance(checksum, str)
                    and isinstance(payload, str)
                ):
                    raise ValueError("field types are wrong")
            except (ValueError, KeyError, TypeError) as exc:
                return records, CorruptionReport(
                    ok=False,
                    total_lines=len(lines),
                    verified_count=len(records),
                    bad_line=lineno,
                    reason="malformed",
                    detail=f"line does not parse as a record: {exc}",
                )
            expected_seq = len(records) + 1
            if seq != expected_seq:
                return records, CorruptionReport(
                    ok=False,
                    total_lines=len(lines),
                    verified_count=len(records),
                    bad_line=lineno,
                    reason="sequence_gap",
                    detail=f"expected seq {expected_seq}, found {seq}",
                )
            expected = _chain_checksum(previous, seq, payload)
            if checksum != expected:
                return records, CorruptionReport(
                    ok=False,
                    total_lines=len(lines),
                    verified_count=len(records),
                    bad_line=lineno,
                    reason="checksum_mismatch",
                    detail="stored checksum does not match the "
                    "recomputed chain checksum",
                )
            records.append(payload)
            previous = checksum
        return records, CorruptionReport(
            ok=True, total_lines=len(lines), verified_count=len(records)
        )

    def verify(self) -> CorruptionReport:
        """Walk the whole file; report the exact first bad line."""
        _, report = self._scan()
        return report

    def read_verified(self) -> VerifiedRead:
        """Only the verified prefix, plus the corruption report."""
        records, report = self._scan()
        return VerifiedRead(records=records, report=report)

    def _tail(self) -> tuple[int, str]:
        records, report = self._scan()
        if not report.ok:
            raise IntegrityError(
                f"refusing to append to corrupt record file "
                f"{self.path}: line {report.bad_line} "
                f"({report.reason}: {report.detail})"
            )
        if not records:
            return 0, GENESIS_CHECKSUM
        # Recompute the tail checksum from the verified records.
        previous = GENESIS_CHECKSUM
        for seq, payload in enumerate(records, start=1):
            previous = _chain_checksum(previous, seq, payload)
        return len(records), previous

    def append(self, payload: str) -> str:
        """Append one record; returns its chain checksum.

        Fail-closed: a corrupt existing file is refused with
        :class:`IntegrityError` and left byte-for-byte untouched.
        """
        if not isinstance(payload, str):
            raise TypeError("payload must be str")
        seq_base, previous = self._tail()
        seq = seq_base + 1
        checksum = _chain_checksum(previous, seq, payload)
        line = (
            json.dumps(
                {"seq": seq, "checksum": checksum, "payload": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        return checksum


@dataclass(frozen=True)
class StateLoadResult:
    """State read back from an :class:`AtomicStateStore`.

    ``used_fallback`` is the visible flag: True when ``data``
    came from the retained previous-good copy because the
    current copy was missing or failed verification.
    """

    data: bytes
    used_fallback: bool = False
    detail: str | None = None


def _encode_envelope(data: bytes) -> bytes:
    envelope = {
        "version": 1,
        "checksum": hashlib.sha256(data).hexdigest(),
        "payload_b64": base64.b64encode(data).decode("ascii"),
    }
    return (json.dumps(envelope, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _decode_envelope(raw: bytes) -> bytes:
    obj = json.loads(raw.decode("utf-8"))
    payload = base64.b64decode(obj["payload_b64"], validate=True)
    if hashlib.sha256(payload).hexdigest() != obj["checksum"]:
        raise IntegrityError("state checksum mismatch")
    return payload


class AtomicStateStore:
    """Checksummed small-state store with previous-good fallback.

    The current state lives at ``path``; the previous good copy
    at ``path`` with a ``".prev"`` suffix. Every save verifies
    the outgoing current copy before retaining it as previous,
    writes via temp-file + fsync + atomic rename, and reads the
    renamed file back to prove the bytes on disk are the bytes
    intended (the HR-075 write-fault discipline).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.prev_path = Path(str(path) + ".prev")

    def _read_verified_file(self, candidate: Path) -> bytes:
        return _decode_envelope(candidate.read_bytes())

    def save(self, data: bytes | str) -> None:
        if isinstance(data, str):
            raw_data = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            raw_data = bytes(data)
        else:
            raise TypeError("state must be bytes or str")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Retain the outgoing copy as previous-good, but only if
        # it itself verifies — never preserve corruption.
        if self.path.exists():
            try:
                current = self._read_verified_file(self.path)
            except Exception:
                current = None
            if current is not None:
                self._atomic_write(self.prev_path, _encode_envelope(current))
        self._atomic_write(self.path, _encode_envelope(raw_data))
        # Read-back verification: what is on disk must be what
        # was saved, or the save did not happen.
        try:
            back = self._read_verified_file(self.path)
        except Exception as exc:
            raise IntegrityError(
                f"state save read-back failed for {self.path}: {exc}"
            ) from exc
        if back != raw_data:
            raise IntegrityError(
                f"state save read-back mismatch for {self.path}"
            )

    def _atomic_write(self, target: Path, content: bytes) -> None:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            _fsync_dir(target.parent)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def load(self) -> StateLoadResult:
        """Load state, falling back to previous-good when needed.

        Raises :class:`FileNotFoundError` when neither copy
        exists, and :class:`IntegrityError` when copies exist
        but none verify.
        """
        current_error: Exception | None = None
        if self.path.exists():
            try:
                return StateLoadResult(
                    data=self._read_verified_file(self.path),
                    used_fallback=False,
                )
            except Exception as exc:  # any failure -> try fallback
                current_error = exc
        if self.prev_path.exists():
            try:
                return StateLoadResult(
                    data=self._read_verified_file(self.prev_path),
                    used_fallback=True,
                    detail=(
                        "current state copy missing or failed "
                        f"verification ({current_error}); served "
                        "the retained previous-good copy"
                    ),
                )
            except Exception as exc:
                raise IntegrityError(
                    f"state at {self.path} and its previous-good "
                    f"copy both fail verification: {exc}"
                ) from exc
        if current_error is not None:
            raise IntegrityError(
                f"state at {self.path} fails verification and no "
                f"previous-good copy exists: {current_error}"
            ) from current_error
        raise FileNotFoundError(
            f"no state file at {self.path} (nor previous-good copy)"
        )


def is_listener_stale(
    *,
    now: float,
    last_append_time: float | None,
    core_logged_in: bool,
    threshold_s: float = DEFAULT_DEAD_LISTENER_THRESHOLD_S,
) -> bool:
    """Pure dead-listener decision.

    Stale only when the Core reports logged in AND the log's
    last append is older than ``threshold_s``. A logged-out Core
    is never stale (silence is expected), a fresh log is never
    stale, and a log that has never appended while logged in
    counts as stale (no append time at all).
    """
    if threshold_s < 0:
        raise ValueError("threshold_s must be >= 0")
    if not core_logged_in:
        return False
    if last_append_time is None:
        return True
    return (now - last_append_time) > threshold_s


@dataclass(frozen=True)
class DeadListenerAlarm:
    """An alarm *record* — produced, never sent, by HR-007."""

    kind: str = "dead_listener"
    now: float = 0.0
    last_append_time: float | None = None
    stale_for_s: float | None = None
    threshold_s: float = DEFAULT_DEAD_LISTENER_THRESHOLD_S
    core_logged_in: bool = True
    detail: str = ""


class DeadListenerMonitor:
    """Small wrapper turning staleness inputs into alarm records."""

    def __init__(
        self, threshold_s: float = DEFAULT_DEAD_LISTENER_THRESHOLD_S
    ):
        if threshold_s < 0:
            raise ValueError("threshold_s must be >= 0")
        self.threshold_s = float(threshold_s)

    def check(
        self,
        *,
        now: float,
        last_append_time: float | None,
        core_logged_in: bool,
    ) -> DeadListenerAlarm | None:
        """An alarm record when stale, else None. Sends nothing."""
        if not is_listener_stale(
            now=now,
            last_append_time=last_append_time,
            core_logged_in=core_logged_in,
            threshold_s=self.threshold_s,
        ):
            return None
        stale_for = (
            None if last_append_time is None else now - last_append_time
        )
        if last_append_time is None:
            detail = (
                "Core reports logged in but the event log has "
                "never recorded an append"
            )
        else:
            detail = (
                f"Core reports logged in but the event log last "
                f"appended {stale_for:.1f}s ago, beyond the "
                f"{self.threshold_s:.1f}s threshold"
            )
        return DeadListenerAlarm(
            now=now,
            last_append_time=last_append_time,
            stale_for_s=stale_for,
            threshold_s=self.threshold_s,
            core_logged_in=core_logged_in,
            detail=detail,
        )
