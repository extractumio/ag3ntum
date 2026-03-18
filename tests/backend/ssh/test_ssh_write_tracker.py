"""
Unit tests for WriteTracker and WriteBudget.

Tests the data classes and session-scoped state management
directly — no SSH connections or async required.
"""
import pytest

from tools.ag3ntum.ag3ntum_ssh.tool import ReadRecord, WriteBudget, WriteTracker


# ---------------------------------------------------------------------------
# TestWriteTracker
# ---------------------------------------------------------------------------

class TestWriteTracker:
    """WriteTracker unit tests."""

    @pytest.mark.unit
    def test_record_read_stores_checksum(self):
        tracker = WriteTracker()
        tracker.record_read("prof", "/etc/test.conf", "abc123", 100)
        record = tracker.get_read_record("prof", "/etc/test.conf")
        assert record is not None
        assert record.checksum == "abc123"
        assert record.size == 100

    @pytest.mark.unit
    def test_get_read_record_returns_none_for_unread(self):
        tracker = WriteTracker()
        assert tracker.get_read_record("prof", "/etc/test.conf") is None

    @pytest.mark.unit
    def test_get_read_record_returns_record_for_read_file(self):
        tracker = WriteTracker()
        tracker.record_read("prof", "/etc/test.conf", "hash1", 50)
        record = tracker.get_read_record("prof", "/etc/test.conf")
        assert record is not None
        assert record.checksum == "hash1"

    @pytest.mark.unit
    def test_clear_session_removes_all_records(self):
        tracker = WriteTracker()
        tracker.record_read("p1", "/a", "h1", 10)
        tracker.record_read("p2", "/b", "h2", 20)
        tracker.clear_session()
        assert tracker.get_read_record("p1", "/a") is None
        assert tracker.get_read_record("p2", "/b") is None

    @pytest.mark.unit
    def test_multiple_reads_of_same_file_updates_record(self):
        tracker = WriteTracker()
        tracker.record_read("prof", "/etc/test.conf", "old_hash", 100)
        tracker.record_read("prof", "/etc/test.conf", "new_hash", 200)
        record = tracker.get_read_record("prof", "/etc/test.conf")
        assert record is not None
        assert record.checksum == "new_hash"
        assert record.size == 200

    @pytest.mark.unit
    def test_different_profiles_same_path_independent(self):
        tracker = WriteTracker()
        tracker.record_read("p1", "/etc/test.conf", "h1", 10)
        tracker.record_read("p2", "/etc/test.conf", "h2", 20)
        r1 = tracker.get_read_record("p1", "/etc/test.conf")
        r2 = tracker.get_read_record("p2", "/etc/test.conf")
        assert r1 is not None and r1.checksum == "h1"
        assert r2 is not None and r2.checksum == "h2"

    @pytest.mark.unit
    def test_read_at_is_monotonic(self):
        tracker = WriteTracker()
        tracker.record_read("p", "/a", "h", 10)
        r = tracker.get_read_record("p", "/a")
        assert r is not None
        assert r.read_at > 0

    @pytest.mark.unit
    def test_empty_tracker_clear_session_is_safe(self):
        tracker = WriteTracker()
        # Should not raise on an empty tracker
        tracker.clear_session()
        assert tracker.get_read_record("any", "/any") is None

    @pytest.mark.unit
    def test_path_isolation_between_sessions(self):
        """Two trackers do not share state."""
        t1 = WriteTracker()
        t2 = WriteTracker()
        t1.record_read("prof", "/etc/foo", "h1", 10)
        assert t2.get_read_record("prof", "/etc/foo") is None

    @pytest.mark.unit
    def test_record_multiple_paths_same_profile(self):
        tracker = WriteTracker()
        tracker.record_read("srv", "/etc/a.conf", "ha", 10)
        tracker.record_read("srv", "/etc/b.conf", "hb", 20)
        ra = tracker.get_read_record("srv", "/etc/a.conf")
        rb = tracker.get_read_record("srv", "/etc/b.conf")
        assert ra is not None and ra.checksum == "ha"
        assert rb is not None and rb.checksum == "hb"


# ---------------------------------------------------------------------------
# TestWriteBudget
# ---------------------------------------------------------------------------

class TestWriteBudget:
    """WriteBudget unit tests."""

    @pytest.mark.unit
    def test_check_within_budget(self):
        budget = WriteBudget(max_bytes=1000)
        assert budget.check(500) is True

    @pytest.mark.unit
    def test_check_exceeds_budget(self):
        budget = WriteBudget(max_bytes=1000)
        assert budget.check(1001) is False

    @pytest.mark.unit
    def test_record_accumulates(self):
        budget = WriteBudget(max_bytes=1000)
        budget.record(400)
        assert budget.check(600) is True
        assert budget.check(601) is False

    @pytest.mark.unit
    def test_remaining_decreases(self):
        budget = WriteBudget(max_bytes=1000)
        assert budget.remaining == 1000
        budget.record(300)
        assert budget.remaining == 700

    @pytest.mark.unit
    def test_exact_budget_allowed(self):
        budget = WriteBudget(max_bytes=1000)
        assert budget.check(1000) is True

    @pytest.mark.unit
    def test_zero_remaining_blocks(self):
        budget = WriteBudget(max_bytes=100)
        budget.record(100)
        assert budget.check(1) is False
        assert budget.remaining == 0

    @pytest.mark.unit
    def test_default_budget_is_10mb(self):
        budget = WriteBudget()
        assert budget.remaining == 10_485_760

    @pytest.mark.unit
    def test_remaining_never_goes_negative(self):
        """remaining property floors at zero even if over-recorded."""
        budget = WriteBudget(max_bytes=100)
        budget.record(200)  # over-records intentionally
        assert budget.remaining == 0

    @pytest.mark.unit
    def test_record_then_check_boundary(self):
        budget = WriteBudget(max_bytes=500)
        budget.record(250)
        assert budget.check(250) is True
        assert budget.check(251) is False

    @pytest.mark.unit
    def test_zero_size_always_passes(self):
        budget = WriteBudget(max_bytes=0)
        # A budget of 0 means nothing can be written
        assert budget.check(0) is True
        assert budget.check(1) is False


# ---------------------------------------------------------------------------
# TestReadRecord
# ---------------------------------------------------------------------------

class TestReadRecord:
    """ReadRecord dataclass tests."""

    @pytest.mark.unit
    def test_fields(self):
        record = ReadRecord(checksum="abc", size=42, read_at=1.0)
        assert record.checksum == "abc"
        assert record.size == 42
        assert record.read_at == 1.0

    @pytest.mark.unit
    def test_zero_size(self):
        record = ReadRecord(checksum="empty", size=0, read_at=0.0)
        assert record.size == 0

    @pytest.mark.unit
    def test_checksum_preserved_exactly(self):
        sha = "a" * 64  # Simulate a SHA-256 hex digest
        record = ReadRecord(checksum=sha, size=100, read_at=1.5)
        assert record.checksum == sha
