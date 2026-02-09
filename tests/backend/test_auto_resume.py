"""
Tests for auto_resume.py.

Tests the auto-resume service:
- Recovery of interrupted "running" sessions on startup
- Recovery of "queued" sessions
- Max resume attempts limit (skip + mark failed)
- Missing claude_session_id handling
- Age cutoff filtering
- Priority assignment (100 for running, 50 for queued)
- Cleanup of old abandoned sessions
- Disabled mode (early return)
- Session state mutations (status, queue_position, resume_attempts)
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.auto_resume import AutoResumeService
from src.services.queue_config import AutoResumeConfig
from src.services.task_queue import QueuedTask


def _make_session(
    session_id="sess-1",
    user_id="user-1",
    status="running",
    task="Do something",
    claude_session_id="claude-sdk-123",
    resume_attempts=0,
    updated_at=None,
    completed_at=None,
):
    """Create a mock session object mimicking the Session model."""
    session = MagicMock()
    session.id = session_id
    session.user_id = user_id
    session.status = status
    session.task = task
    session.claude_session_id = claude_session_id
    session.resume_attempts = resume_attempts
    session.updated_at = updated_at or datetime.now(timezone.utc)
    session.completed_at = completed_at
    session.queue_position = None
    session.queued_at = None
    session.is_auto_resume = False
    return session


def _make_config(enabled=True, max_age_hours=6, max_attempts=3):
    """Create an AutoResumeConfig."""
    return AutoResumeConfig(
        enabled=enabled,
        max_session_age_hours=max_age_hours,
        max_resume_attempts=max_attempts,
    )


def _make_mock_db(sessions=None):
    """Create a mock async database session."""
    db = AsyncMock()
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = sessions or []
    result.scalars.return_value = scalars
    db.execute.return_value = result
    return db


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
class TestAutoResumeConstants:
    """Tests for AutoResumeService priority constants."""

    @pytest.mark.unit
    def test_auto_resume_priority(self):
        """Test auto-resume priority is 100."""
        assert AutoResumeService.PRIORITY_AUTO_RESUME == 100

    @pytest.mark.unit
    def test_queued_recovery_priority(self):
        """Test queued recovery priority is 50."""
        assert AutoResumeService.PRIORITY_QUEUED_RECOVERY == 50

    @pytest.mark.unit
    def test_resume_higher_than_queued(self):
        """Test running recovery gets higher priority than queued recovery."""
        assert (
            AutoResumeService.PRIORITY_AUTO_RESUME
            > AutoResumeService.PRIORITY_QUEUED_RECOVERY
        )


# ---------------------------------------------------------------------------
# Disabled Mode
# ---------------------------------------------------------------------------
class TestAutoResumeDisabled:
    """Tests for auto-resume when disabled."""

    @pytest.mark.asyncio
    async def test_disabled_returns_early(self):
        """Test that disabled config skips recovery."""
        config = _make_config(enabled=False)
        queue = AsyncMock()
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db()
        stats = await service.recover_on_startup(db)

        assert stats == {"enabled": False}
        db.execute.assert_not_called()
        queue.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# recover_on_startup
# ---------------------------------------------------------------------------
class TestRecoverOnStartup:
    """Tests for AutoResumeService.recover_on_startup."""

    @pytest.mark.asyncio
    async def test_no_sessions_found(self):
        """Test recovery with no interrupted sessions."""
        config = _make_config()
        queue = AsyncMock()
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[])
        stats = await service.recover_on_startup(db)

        assert stats["enabled"] is True
        assert stats["running_found"] == 0
        assert stats["queued_found"] == 0
        assert stats["recovered"] == 0

    @pytest.mark.asyncio
    async def test_running_session_recovered(self):
        """Test that a running session with claude_session_id is recovered."""
        config = _make_config()
        queue = AsyncMock()
        queue.enqueue.return_value = 1  # position

        session = _make_session(
            status="running",
            claude_session_id="claude-sdk-abc",
        )
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        stats = await service.recover_on_startup(db)

        assert stats["running_found"] == 1
        assert stats["recovered"] == 1
        queue.enqueue.assert_called_once()

        # Verify the queued task
        queued_task = queue.enqueue.call_args[0][0]
        assert queued_task.session_id == "sess-1"
        assert queued_task.priority == 100  # PRIORITY_AUTO_RESUME
        assert queued_task.is_auto_resume is True
        assert queued_task.resume_from == "sess-1"

    @pytest.mark.asyncio
    async def test_queued_session_recovered(self):
        """Test that a queued session is recovered with lower priority."""
        config = _make_config()
        queue = AsyncMock()
        queue.enqueue.return_value = 1

        session = _make_session(
            status="queued",
            claude_session_id="claude-sdk-abc",
        )
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        stats = await service.recover_on_startup(db)

        assert stats["queued_found"] == 1
        assert stats["recovered"] == 1

        queued_task = queue.enqueue.call_args[0][0]
        assert queued_task.priority == 50  # PRIORITY_QUEUED_RECOVERY

    @pytest.mark.asyncio
    async def test_session_state_updated_after_recovery(self):
        """Test that session state is updated after queuing."""
        config = _make_config()
        queue = AsyncMock()
        queue.enqueue.return_value = 3  # position = 3

        session = _make_session(
            status="running",
            claude_session_id="sdk-id",
            resume_attempts=1,
        )
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        await service.recover_on_startup(db)

        assert session.status == "queued"
        assert session.queue_position == 3
        assert session.resume_attempts == 2  # incremented
        assert session.is_auto_resume is True
        assert session.queued_at is not None

    @pytest.mark.asyncio
    async def test_max_attempts_exceeded_marks_failed(self):
        """Test that exceeding max resume attempts marks session as failed."""
        config = _make_config(max_attempts=3)
        queue = AsyncMock()

        session = _make_session(
            status="running",
            claude_session_id="sdk-id",
            resume_attempts=3,  # Already at max
        )
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        stats = await service.recover_on_startup(db)

        assert stats["skipped_max_attempts"] == 1
        assert stats["marked_failed"] == 1
        assert stats["recovered"] == 0
        assert session.status == "failed"
        assert session.completed_at is not None
        queue.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_running_no_resume_id_marks_failed(self):
        """Test that running session without claude_session_id is marked failed."""
        config = _make_config()
        queue = AsyncMock()

        session = _make_session(
            status="running",
            claude_session_id=None,  # No resume ID
        )
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        stats = await service.recover_on_startup(db)

        assert stats["skipped_no_resume_id"] == 1
        assert stats["marked_failed"] == 1
        assert stats["recovered"] == 0
        assert session.status == "failed"
        queue.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_queued_no_resume_id_still_recovered(self):
        """Test that queued session without resume_id is still recovered."""
        config = _make_config()
        queue = AsyncMock()
        queue.enqueue.return_value = 1

        session = _make_session(
            status="queued",
            claude_session_id=None,  # No resume ID, but queued is OK
        )
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        stats = await service.recover_on_startup(db)

        assert stats["recovered"] == 1
        queued_task = queue.enqueue.call_args[0][0]
        assert queued_task.resume_from is None  # No resume ID

    @pytest.mark.asyncio
    async def test_multiple_sessions_mixed(self):
        """Test recovery with a mix of recoverable and non-recoverable sessions."""
        config = _make_config(max_attempts=2)
        queue = AsyncMock()
        queue.enqueue.return_value = 1

        sessions = [
            _make_session(
                session_id="s1",
                status="running",
                claude_session_id="sdk-1",
                resume_attempts=0,
            ),
            _make_session(
                session_id="s2",
                status="running",
                claude_session_id=None,  # No resume ID
                resume_attempts=0,
            ),
            _make_session(
                session_id="s3",
                status="running",
                claude_session_id="sdk-3",
                resume_attempts=2,  # At max
            ),
            _make_session(
                session_id="s4",
                status="queued",
                claude_session_id="sdk-4",
                resume_attempts=0,
            ),
        ]

        service = AutoResumeService(task_queue=queue, config=config)
        db = _make_mock_db(sessions=sessions)
        stats = await service.recover_on_startup(db)

        assert stats["running_found"] == 3
        assert stats["queued_found"] == 1
        assert stats["recovered"] == 2  # s1 + s4
        assert stats["skipped_no_resume_id"] == 1  # s2
        assert stats["skipped_max_attempts"] == 1  # s3
        assert stats["marked_failed"] == 2  # s2 + s3
        assert queue.enqueue.call_count == 2

    @pytest.mark.asyncio
    async def test_db_commit_called(self):
        """Test that database changes are committed."""
        config = _make_config()
        queue = AsyncMock()
        queue.enqueue.return_value = 1

        session = _make_session(status="running", claude_session_id="sdk-1")
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        await service.recover_on_startup(db)

        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_task_fallback(self):
        """Test that missing task gets a default value in QueuedTask."""
        config = _make_config()
        queue = AsyncMock()
        queue.enqueue.return_value = 1

        session = _make_session(
            status="running",
            claude_session_id="sdk-1",
            task=None,
        )
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        await service.recover_on_startup(db)

        queued_task = queue.enqueue.call_args[0][0]
        assert queued_task.task == "Resume interrupted task"

    @pytest.mark.asyncio
    async def test_resume_attempts_none_treated_as_zero(self):
        """Test that None resume_attempts is treated as 0."""
        config = _make_config(max_attempts=3)
        queue = AsyncMock()
        queue.enqueue.return_value = 1

        session = _make_session(
            status="running",
            claude_session_id="sdk-1",
            resume_attempts=None,
        )
        # Override the mock return for resume_attempts
        session.resume_attempts = None
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[session])
        stats = await service.recover_on_startup(db)

        # Should be recovered (None -> 0, which is < 3)
        assert stats["recovered"] == 1
        assert session.resume_attempts == 1  # 0 + 1


# ---------------------------------------------------------------------------
# cleanup_old_sessions
# ---------------------------------------------------------------------------
class TestCleanupOldSessions:
    """Tests for AutoResumeService.cleanup_old_sessions."""

    @pytest.mark.asyncio
    async def test_no_old_sessions(self):
        """Test cleanup with no old sessions."""
        config = _make_config()
        queue = AsyncMock()
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[])
        count = await service.cleanup_old_sessions(db)

        assert count == 0
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_old_sessions_marked_failed(self):
        """Test that old sessions are marked as failed."""
        config = _make_config(max_age_hours=6)
        queue = AsyncMock()
        service = AutoResumeService(task_queue=queue, config=config)

        old_session = _make_session(
            session_id="old-1",
            status="running",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=12),
        )

        db = _make_mock_db(sessions=[old_session])
        count = await service.cleanup_old_sessions(db)

        assert count == 1
        assert old_session.status == "failed"
        assert old_session.completed_at is not None
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_old_sessions(self):
        """Test cleaning up multiple old sessions."""
        config = _make_config(max_age_hours=6)
        queue = AsyncMock()
        service = AutoResumeService(task_queue=queue, config=config)

        sessions = [
            _make_session(session_id="old-1", status="running"),
            _make_session(session_id="old-2", status="queued"),
            _make_session(session_id="old-3", status="pending"),
        ]

        db = _make_mock_db(sessions=sessions)
        count = await service.cleanup_old_sessions(db)

        assert count == 3
        for s in sessions:
            assert s.status == "failed"
            assert s.completed_at is not None

    @pytest.mark.asyncio
    async def test_no_commit_when_none_cleaned(self):
        """Test that commit is not called when no sessions cleaned."""
        config = _make_config()
        queue = AsyncMock()
        service = AutoResumeService(task_queue=queue, config=config)

        db = _make_mock_db(sessions=[])
        await service.cleanup_old_sessions(db)

        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# AutoResumeConfig
# ---------------------------------------------------------------------------
class TestAutoResumeConfig:
    """Tests for AutoResumeConfig dataclass."""

    @pytest.mark.unit
    def test_defaults(self):
        """Test default configuration values."""
        config = AutoResumeConfig()
        assert config.enabled is True
        assert config.max_session_age_hours == 6
        assert config.max_resume_attempts == 3
        assert config.resume_delay_seconds == 5

    @pytest.mark.unit
    def test_custom_values(self):
        """Test custom configuration values."""
        config = AutoResumeConfig(
            enabled=False,
            max_session_age_hours=12,
            max_resume_attempts=5,
            resume_delay_seconds=10,
        )
        assert config.enabled is False
        assert config.max_session_age_hours == 12
        assert config.max_resume_attempts == 5
        assert config.resume_delay_seconds == 10
