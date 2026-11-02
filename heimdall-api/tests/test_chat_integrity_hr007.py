"""HR-007 — interim chain hardening (responsiveness Part 0).

The laws under test, all offline (tmp dirs only — the live hook
state and the laptop evtcap log are never touched):

- checksummed record store: append+verify round-trip; chain
  continuity when an existing file is reopened; a mid-file
  byte-flip is detected at the exact line; a torn final line is
  detected; appending onto a corrupt file is refused
  (fail-closed) and leaves the file untouched; reading returns
  only the verified prefix plus a corruption report;
- atomic state store: save/load round-trip; a corrupted current
  copy falls back to the previous-good copy with the visible
  ``used_fallback`` flag;
- dead-listener detector: fires only when the Core is logged in
  AND the log is stale beyond the named threshold — fresh log
  and stale-but-logged-out never fire.
"""
import pytest

from heimdall.chat import (
    DEFAULT_DEAD_LISTENER_THRESHOLD_S,
    AtomicStateStore,
    ChecksummedRecordStore,
    DeadListenerMonitor,
    IntegrityError,
    is_listener_stale,
)
from heimdall.chat.integrity import _chain_checksum, GENESIS_CHECKSUM


# -----------------------------------------------------------------
# Checksummed record store
# -----------------------------------------------------------------
def _store(tmp_path, name="events.log"):
    return ChecksummedRecordStore(tmp_path / name)


def test_append_verify_round_trip(tmp_path):
    store = _store(tmp_path)
    store.append("im|hello")
    store.append("chat|second line")
    store.append("")  # empty payload is a legal record
    result = store.read_verified()
    assert result.report.ok is True
    assert result.report.total_lines == 3
    assert result.report.verified_count == 3
    assert result.records == ["im|hello", "chat|second line", ""]
    assert store.verify().ok is True


def test_chain_continuity_when_reopening_existing_file(tmp_path):
    path = tmp_path / "events.log"
    first = ChecksummedRecordStore(path)
    c1 = first.append("one")
    c2 = first.append("two")
    # A second store object over the same file must continue the
    # chain, not restart it.
    second = ChecksummedRecordStore(path)
    c3 = second.append("three")
    expected_c3 = _chain_checksum(c2, 3, "three")
    assert c3 == expected_c3
    assert c1 == _chain_checksum(GENESIS_CHECKSUM, 1, "one")
    assert c2 == _chain_checksum(c1, 2, "two")
    result = second.read_verified()
    assert result.report.ok is True
    assert result.records == ["one", "two", "three"]


def test_mid_file_byte_flip_detected_at_exact_line(tmp_path):
    store = _store(tmp_path)
    for payload in ("alpha", "bravo", "charlie", "delta"):
        store.append(payload)
    raw = store.path.read_bytes()
    lines = raw.split(b"\n")
    # Flip one byte inside the payload of line 2 (index 1).
    line2 = bytearray(lines[1])
    pos = line2.index(b"bravo")
    line2[pos] = ord("B")
    lines[1] = bytes(line2)
    store.path.write_bytes(b"\n".join(lines))
    report = store.verify()
    assert report.ok is False
    assert report.bad_line == 2
    assert report.reason == "checksum_mismatch"
    assert report.verified_count == 1
    # Reading returns only the verified prefix.
    result = store.read_verified()
    assert result.records == ["alpha"]
    assert result.report.bad_line == 2


def test_torn_final_line_detected(tmp_path):
    store = _store(tmp_path)
    store.append("complete-one")
    store.append("complete-two")
    # Simulate a torn write: a partial third line, no newline.
    with open(store.path, "ab") as fh:
        fh.write(b'{"seq":3,"checksum":"abc123","payl')
    report = store.verify()
    assert report.ok is False
    assert report.bad_line == 3
    assert report.reason == "torn_tail"
    result = store.read_verified()
    assert result.records == ["complete-one", "complete-two"]


def test_truncated_record_line_detected(tmp_path):
    store = _store(tmp_path)
    store.append("kept")
    store.append("will-be-cut")
    raw = store.path.read_bytes()
    # Cut the file mid-second-line but keep a newline so the
    # remainder parses as a malformed record line.
    first_nl = raw.index(b"\n")
    store.path.write_bytes(raw[: first_nl + 8] + b"\n")
    report = store.verify()
    assert report.ok is False
    assert report.bad_line == 2
    assert report.reason in ("malformed", "torn_tail", "checksum_mismatch")


def test_append_onto_corrupt_file_refused(tmp_path):
    store = _store(tmp_path)
    store.append("good")
    before = store.path.read_bytes()
    corrupted = before.replace(b"good", b"GOOD")
    store.path.write_bytes(corrupted)
    with pytest.raises(IntegrityError):
        store.append("must-not-land")
    # Fail-closed means untouched: the corrupt bytes are exactly
    # what the refusal found, with nothing appended.
    assert store.path.read_bytes() == corrupted


def test_verify_missing_and_empty_files_are_clean(tmp_path):
    missing = _store(tmp_path, "nope.log")
    assert missing.verify().ok is True
    assert missing.read_verified().records == []
    empty = _store(tmp_path, "empty.log")
    empty.path.write_bytes(b"")
    assert empty.verify().ok is True


# -----------------------------------------------------------------
# Atomic checksummed state store
# -----------------------------------------------------------------
def test_atomic_state_save_load_round_trip(tmp_path):
    store = AtomicStateStore(tmp_path / "nerve.state")
    store.save(b"im@line12|uuid-1|test")
    result = store.load()
    assert result.data == b"im@line12|uuid-1|test"
    assert result.used_fallback is False
    store.save("watermark=42")
    result2 = store.load()
    assert result2.data == b"watermark=42"
    assert result2.used_fallback is False


def test_corrupt_current_state_falls_back_to_previous_good(tmp_path):
    store = AtomicStateStore(tmp_path / "nerve.state")
    store.save(b"state-v1")
    store.save(b"state-v2")
    # Corrupt the current copy: flip payload bytes so the
    # embedded checksum no longer matches.
    raw = bytearray(store.path.read_bytes())
    pos = raw.index(b"c3RhdGUtdjI")  # base64 of "state-v2" prefix
    raw[pos] = ord("A") if raw[pos] != ord("A") else ord("B")
    store.path.write_bytes(bytes(raw))
    result = store.load()
    assert result.used_fallback is True
    assert result.data == b"state-v1"
    assert result.detail  # the fallback is visible, with a reason


def test_state_with_no_copies_raises_file_not_found(tmp_path):
    store = AtomicStateStore(tmp_path / "absent.state")
    with pytest.raises(FileNotFoundError):
        store.load()


def test_state_both_copies_corrupt_raises_integrity_error(tmp_path):
    store = AtomicStateStore(tmp_path / "nerve.state")
    store.save(b"v1")
    store.save(b"v2")
    store.path.write_bytes(b"not json at all\n")
    store.prev_path.write_bytes(b"also not json\n")
    with pytest.raises(IntegrityError):
        store.load()


# -----------------------------------------------------------------
# Dead-listener detector
# -----------------------------------------------------------------
NOW = 10_000.0


def test_detector_fresh_log_never_stale():
    assert (
        is_listener_stale(
            now=NOW,
            last_append_time=NOW - 5.0,
            core_logged_in=True,
        )
        is False
    )
    monitor = DeadListenerMonitor()
    assert (
        monitor.check(
            now=NOW, last_append_time=NOW - 5.0, core_logged_in=True
        )
        is None
    )


def test_detector_stale_but_logged_out_never_stale():
    old = NOW - (DEFAULT_DEAD_LISTENER_THRESHOLD_S + 3600.0)
    assert (
        is_listener_stale(
            now=NOW, last_append_time=old, core_logged_in=False
        )
        is False
    )
    monitor = DeadListenerMonitor()
    assert (
        monitor.check(now=NOW, last_append_time=old, core_logged_in=False)
        is None
    )


def test_detector_stale_and_logged_in_fires_alarm_record():
    old = NOW - (DEFAULT_DEAD_LISTENER_THRESHOLD_S + 30.0)
    assert (
        is_listener_stale(
            now=NOW, last_append_time=old, core_logged_in=True
        )
        is True
    )
    monitor = DeadListenerMonitor()
    alarm = monitor.check(
        now=NOW, last_append_time=old, core_logged_in=True
    )
    assert alarm is not None
    assert alarm.kind == "dead_listener"
    assert alarm.core_logged_in is True
    assert alarm.threshold_s == DEFAULT_DEAD_LISTENER_THRESHOLD_S
    assert alarm.stale_for_s == pytest.approx(
        DEFAULT_DEAD_LISTENER_THRESHOLD_S + 30.0
    )
    assert alarm.detail


def test_detector_threshold_is_configurable_and_exact_boundary():
    monitor = DeadListenerMonitor(threshold_s=10.0)
    # Exactly at the threshold is not beyond it: no alarm.
    assert (
        monitor.check(
            now=NOW, last_append_time=NOW - 10.0, core_logged_in=True
        )
        is None
    )
    alarm = monitor.check(
        now=NOW, last_append_time=NOW - 10.5, core_logged_in=True
    )
    assert alarm is not None
    assert alarm.threshold_s == 10.0


def test_detector_never_appended_while_logged_in_is_stale():
    assert (
        is_listener_stale(
            now=NOW, last_append_time=None, core_logged_in=True
        )
        is True
    )
    assert (
        is_listener_stale(
            now=NOW, last_append_time=None, core_logged_in=False
        )
        is False
    )
