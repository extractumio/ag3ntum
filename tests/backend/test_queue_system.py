"""
Tests for the task queue system.

Tests for:
- TaskQueue: Redis-backed priority queue operations
- QuotaManager: Quota enforcement logic
- AutoResumeService: Startup recovery for interrupted sessions
- QueueConfig: Configuration loading
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Optional

import pytest
import pytest_asyncio

# Add project root to path before importing project modules
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.queue_config import (
    AutoResumeConfig,
    QueueConfig,
    QuotaConfig,
    TaskQueueConfig,
    load_queue_config,
)
from src.services.task_queue import TaskQueue, QueuedTask
from src.services.quota_manager import QuotaManager


# =============================================================================
# Queue Config Tests
# =============================================================================

class TestQueueConfig:
    """Tests for queue configuration loading."""

    def test_load_default_config(self):
        """Test loading with empty config uses defaults."""
        config = load_queue_config({})

        assert config.auto_resume.enabled is True
        assert config.auto_resume.max_session_age_hours == 6
        assert config.auto_resume.max_resume_attempts == 3

        assert config.queue.enabled is True
        assert config.queue.processing_interval_ms == 500

        assert config.quotas.global_max_concurrent == 4
        assert config.quotas.per_user_max_concurrent == 2

    def test_load_custom_config(self):
        """Test loading with custom values."""
        raw = {
            "auto_resume": {
                "enabled": False,
                "max_session_age_hours": 12,
            },
            "queue": {
                "processing_interval_ms": 1000,
            },
            "quotas": {
                "global_max_concurrent": 8,
                "per_user_daily_limit": 100,
            },
        }
        config = load_queue_config(raw)

        assert config.auto_resume.enabled is False
        assert config.auto_resume.max_session_age_hours == 12
        assert config.queue.processing_interval_ms == 1000
        assert config.quotas.global_max_concurrent == 8
        assert config.quotas.per_user_daily_limit == 100


# =============================================================================
# TaskQueue Tests (with mocked Redis)
# =============================================================================

class TestTaskQueue:
    """Tests for TaskQueue Redis operations."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        mock = AsyncMock()
        # Set up return values for common operations
        mock.zadd = AsyncMock(return_value=1)
        mock.zpopmin = AsyncMock(return_value=[])
        mock.zrem = AsyncMock(return_value=1)
        mock.zrank = AsyncMock(return_value=None)
        mock.zcard = AsyncMock(return_value=0)
        mock.zrange = AsyncMock(return_value=[])
        mock.set = AsyncMock(return_value=True)
        mock.get = AsyncMock(return_value=None)
        mock.delete = AsyncMock(return_value=1)
        mock.sadd = AsyncMock(return_value=1)
        mock.srem = AsyncMock(return_value=1)
        mock.scard = AsyncMock(return_value=0)
        mock.smembers = AsyncMock(return_value=set())
        # Support async context manager
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        return mock

    @pytest.fixture
    def task_queue(self, mock_redis):
        """Create a TaskQueue with mocked Redis."""
        queue = TaskQueue.__new__(TaskQueue)
        # Initialize required attributes that __init__ would set
        queue._pool = MagicMock()  # Pretend pool already exists
        queue._lock = asyncio.Lock()
        queue._task_ttl_seconds = 86400
        queue._max_queue_size = 1000
        return queue

    @pytest.fixture
    def patched_redis(self, mock_redis):
        """Patch redis.Redis to return our mock."""
        with patch('src.services.task_queue.redis.Redis', return_value=mock_redis):
            yield mock_redis

    @pytest.fixture
    def sample_task(self):
        """Create a sample queued task."""
        return QueuedTask(
            session_id="test-session-123",
            user_id="user-456",
            task="Test task description",
            priority=0,
            queued_at=datetime.now(timezone.utc),
            is_auto_resume=False,
            resume_from=None,
        )

    @pytest.mark.asyncio
    async def test_enqueue_task(self, task_queue, patched_redis, sample_task):
        """Test enqueueing a task."""
        # Setup
        patched_redis.zrank.return_value = 0  # Position 0 = first in queue

        # Execute
        position = await task_queue.enqueue(sample_task)

        # Verify
        assert position == 1  # 0-indexed rank + 1
        patched_redis.zadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_dequeue_empty_queue(self, task_queue, patched_redis):
        """Test dequeue from empty queue returns None."""
        patched_redis.zpopmin.return_value = []

        result = await task_queue.dequeue()

        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_with_task(self, task_queue, patched_redis, sample_task):
        """Test dequeue returns task when queue has items."""
        import json
        task_data = json.dumps({
            "session_id": sample_task.session_id,
            "user_id": sample_task.user_id,
            "task": sample_task.task,
            "priority": sample_task.priority,
            "queued_at": sample_task.queued_at.isoformat(),
            "is_auto_resume": sample_task.is_auto_resume,
            "resume_from": sample_task.resume_from,
        })
        patched_redis.zpopmin.return_value = [(sample_task.session_id, 12345.0)]
        patched_redis.get.return_value = task_data

        result = await task_queue.dequeue()

        assert result is not None
        assert result.session_id == sample_task.session_id
        assert result.user_id == sample_task.user_id

    @pytest.mark.asyncio
    async def test_get_position(self, task_queue, patched_redis):
        """Test getting position of a task in queue."""
        patched_redis.zrank.return_value = 2

        position = await task_queue.get_position("session-123")

        assert position == 3  # zrank is 0-indexed, position is 1-indexed

    @pytest.mark.asyncio
    async def test_get_position_not_found(self, task_queue, patched_redis):
        """Test getting position of non-existent task."""
        patched_redis.zrank.return_value = None

        position = await task_queue.get_position("non-existent")

        assert position is None

    @pytest.mark.asyncio
    async def test_remove_task(self, task_queue, patched_redis):
        """Test removing a task from queue."""
        patched_redis.zrem.return_value = 1

        result = await task_queue.remove("session-123")

        assert result is True
        patched_redis.zrem.assert_called()

    @pytest.mark.asyncio
    async def test_mark_user_active(self, task_queue, patched_redis):
        """Test marking a user as having an active task."""
        await task_queue.mark_user_active("user-123", "session-456")

        patched_redis.sadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_user_inactive(self, task_queue, patched_redis):
        """Test marking a user's task as inactive."""
        await task_queue.mark_user_inactive("user-123", "session-456")

        patched_redis.srem.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_user_active_count(self, task_queue, patched_redis):
        """Test getting count of user's active tasks."""
        patched_redis.scard.return_value = 2

        count = await task_queue.get_user_active_count("user-123")

        assert count == 2

    @pytest.mark.asyncio
    async def test_get_queue_length(self, task_queue, patched_redis):
        """Test getting total queue length."""
        patched_redis.zcard.return_value = 5

        length = await task_queue.get_queue_length()

        assert length == 5


# =============================================================================
# QuotaManager Tests
# =============================================================================

class TestQuotaManager:
    """Tests for QuotaManager quota enforcement."""

    @pytest.fixture
    def quota_config(self):
        """Create quota configuration."""
        return QuotaConfig(
            global_max_concurrent=4,
            per_user_max_concurrent=2,
            per_user_daily_limit=50,
        )

    @pytest.fixture
    def mock_task_queue(self):
        """Create a mock TaskQueue."""
        mock = AsyncMock(spec=TaskQueue)
        mock.get_user_active_count = AsyncMock(return_value=0)
        return mock

    @pytest.fixture
    def mock_db(self):
        """Create a properly mocked database session."""
        mock = AsyncMock()
        # scalar_one_or_none is a sync method, so use MagicMock for result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock.execute.return_value = mock_result
        return mock

    @pytest.fixture
    def quota_manager(self, mock_task_queue, quota_config):
        """Create a QuotaManager with mocks."""
        return QuotaManager(mock_task_queue, quota_config)

    @pytest.mark.asyncio
    async def test_can_start_when_under_limits(self, quota_manager, mock_task_queue, mock_db):
        """Test task can start when all limits are satisfied."""
        mock_task_queue.get_user_active_count.return_value = 0

        can_start, reason = await quota_manager.can_start_task("user-123", mock_db)

        assert can_start is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_cannot_start_when_global_limit_reached(self, quota_manager, mock_task_queue, mock_db):
        """Test task cannot start when global limit is reached."""
        # Simulate 4 active tasks (at global limit)
        quota_manager._global_active_count = 4

        can_start, reason = await quota_manager.can_start_task("user-123", mock_db)

        assert can_start is False
        assert "global" in reason.lower()

    @pytest.mark.asyncio
    async def test_cannot_start_when_user_limit_reached(self, quota_manager, mock_task_queue, mock_db):
        """Test task cannot start when per-user limit is reached."""
        mock_task_queue.get_user_active_count.return_value = 2

        can_start, reason = await quota_manager.can_start_task("user-123", mock_db)

        assert can_start is False
        assert "user" in reason.lower() or "concurrent" in reason.lower()

    def test_increment_global(self, quota_manager):
        """Test incrementing global active count."""
        initial = quota_manager.get_global_active()
        quota_manager.increment_global()

        assert quota_manager.get_global_active() == initial + 1

    def test_decrement_global(self, quota_manager):
        """Test decrementing global active count."""
        quota_manager.increment_global()
        quota_manager.increment_global()
        initial = quota_manager.get_global_active()
        quota_manager.decrement_global()

        assert quota_manager.get_global_active() == initial - 1

    def test_decrement_global_cannot_go_negative(self, quota_manager):
        """Test global count cannot go below zero."""
        quota_manager.decrement_global()

        assert quota_manager.get_global_active() == 0


# =============================================================================
# QueuedTask Tests
# =============================================================================

class TestQueuedTask:
    """Tests for QueuedTask dataclass."""

    def test_create_basic_task(self):
        """Test creating a basic queued task."""
        now = datetime.now(timezone.utc)
        task = QueuedTask(
            session_id="sess-123",
            user_id="user-456",
            task="Do something",
            priority=0,
            queued_at=now,
        )

        assert task.session_id == "sess-123"
        assert task.user_id == "user-456"
        assert task.task == "Do something"
        assert task.priority == 0
        assert task.queued_at == now
        assert task.is_auto_resume is False
        assert task.resume_from is None

    def test_create_auto_resume_task(self):
        """Test creating an auto-resume task."""
        task = QueuedTask(
            session_id="sess-123",
            user_id="user-456",
            task="Resume interrupted",
            priority=100,
            queued_at=datetime.now(timezone.utc),
            is_auto_resume=True,
            resume_from="sess-123",
        )

        assert task.is_auto_resume is True
        assert task.resume_from == "sess-123"
        assert task.priority == 100

    def test_priority_affects_score(self):
        """Test that higher priority results in lower score (earlier processing)."""
        now = datetime.now(timezone.utc)
        low_priority = QueuedTask(
            session_id="low",
            user_id="user",
            task="Low priority",
            priority=0,
            queued_at=now,
        )
        high_priority = QueuedTask(
            session_id="high",
            user_id="user",
            task="High priority",
            priority=100,
            queued_at=now,
        )

        # Higher priority should come before lower priority when queued at same time
        low_score = now.timestamp() - (low_priority.priority * 1_000_000)
        high_score = now.timestamp() - (high_priority.priority * 1_000_000)

        assert high_score < low_score  # Lower score = higher priority


# =============================================================================
# Integration-style Tests (mocking database)
# =============================================================================

class TestQueueIntegration:
    """Integration tests for the queue system components working together."""

    @pytest.fixture
    def mock_db(self):
        """Create a properly mocked database session."""
        mock = AsyncMock()
        # scalar_one_or_none is a sync method, so use MagicMock for result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock.execute.return_value = mock_result
        return mock

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        mock = AsyncMock()
        mock.zadd = AsyncMock(return_value=1)
        mock.zpopmin = AsyncMock(return_value=[])
        mock.zrem = AsyncMock(return_value=1)
        mock.zrank = AsyncMock(return_value=0)
        mock.zcard = AsyncMock(return_value=0)
        mock.set = AsyncMock(return_value=True)
        mock.get = AsyncMock(return_value=None)
        mock.delete = AsyncMock(return_value=1)
        mock.sadd = AsyncMock(return_value=1)
        mock.srem = AsyncMock(return_value=1)
        mock.scard = AsyncMock(return_value=0)
        # Support async context manager
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        return mock

    @pytest.mark.asyncio
    async def test_queue_and_dequeue_flow(self, mock_redis, mock_db):
        """Test the full flow of queuing and dequeuing a task."""
        import json

        # Setup TaskQueue with mock by patching redis.Redis
        queue = TaskQueue.__new__(TaskQueue)
        queue._pool = MagicMock()  # Pretend pool exists
        queue._lock = asyncio.Lock()
        queue._task_ttl_seconds = 86400
        queue._max_queue_size = 1000

        # Create task
        task = QueuedTask(
            session_id="test-123",
            user_id="user-456",
            task="Test task",
            priority=50,
            queued_at=datetime.now(timezone.utc),
        )

        with patch('src.services.task_queue.redis.Redis', return_value=mock_redis):
            # Enqueue
            mock_redis.zrank.return_value = 0
            position = await queue.enqueue(task)
            assert position == 1  # rank 0 + 1

            # Verify enqueue was called
            assert mock_redis.zadd.called

            # Prepare for dequeue
            task_data = json.dumps({
                "session_id": task.session_id,
                "user_id": task.user_id,
                "task": task.task,
                "priority": task.priority,
                "queued_at": task.queued_at.isoformat(),
                "is_auto_resume": False,
                "resume_from": None,
            })
            mock_redis.zpopmin.return_value = [(task.session_id, 12345.0)]
            mock_redis.get.return_value = task_data

            # Dequeue
            dequeued = await queue.dequeue()
            assert dequeued is not None
            assert dequeued.session_id == task.session_id

    @pytest.mark.asyncio
    async def test_quota_blocks_when_full(self, mock_redis, mock_db):
        """Test that quota manager blocks tasks when limits are reached."""
        # Setup TaskQueue with mock
        queue = TaskQueue.__new__(TaskQueue)
        queue._pool = MagicMock()
        queue._lock = asyncio.Lock()
        queue._task_ttl_seconds = 86400

        config = QuotaConfig(
            global_max_concurrent=2,
            per_user_max_concurrent=1,
            per_user_daily_limit=50,
        )
        manager = QuotaManager(queue, config)

        # Simulate global limit reached
        manager.increment_global()
        manager.increment_global()

        can_start, reason = await manager.can_start_task("user-123", mock_db)
        assert can_start is False
        assert "global" in reason.lower()


# =============================================================================
# Auto-Resume Service Tests
# =============================================================================

class TestAutoResumeService:
    """Tests for AutoResumeService startup recovery."""

    @pytest.fixture
    def auto_resume_config(self):
        """Create auto-resume configuration."""
        return AutoResumeConfig(
            enabled=True,
            max_session_age_hours=6,
            max_resume_attempts=3,
            resume_delay_seconds=5,
        )

    @pytest.fixture
    def disabled_config(self):
        """Create disabled auto-resume configuration."""
        return AutoResumeConfig(
            enabled=False,
            max_session_age_hours=6,
            max_resume_attempts=3,
            resume_delay_seconds=5,
        )

    @pytest.fixture
    def mock_task_queue(self):
        """Create a mock TaskQueue."""
        mock = AsyncMock()
        mock.enqueue = AsyncMock(return_value=1)
        return mock

    @pytest.mark.asyncio
    async def test_disabled_auto_resume(self, mock_task_queue, disabled_config):
        """Test that disabled auto-resume returns early."""
        from src.services.auto_resume import AutoResumeService

        service = AutoResumeService(mock_task_queue, disabled_config)
        mock_db = AsyncMock()

        stats = await service.recover_on_startup(mock_db)

        assert stats["enabled"] is False
        mock_task_queue.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_recover_running_sessions(self, mock_task_queue, auto_resume_config):
        """Test recovery of running sessions."""
        from src.services.auto_resume import AutoResumeService
        from unittest.mock import MagicMock

        service = AutoResumeService(mock_task_queue, auto_resume_config)

        # Create mock user
        mock_user = MagicMock()
        mock_user.username = "testuser"

        # Create mock session with claude_session_id (database field for resumption)
        mock_session = MagicMock()
        mock_session.id = "test-session-123"
        mock_session.user_id = "user-456"
        mock_session.user = mock_user
        mock_session.task = "Running task"
        mock_session.status = "running"
        mock_session.resume_attempts = 0
        mock_session.updated_at = datetime.now(timezone.utc)
        mock_session.claude_session_id = "valid-claude-session-id"  # Resume ID from database

        # Setup mock DB - the code uses scalars().all()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_session]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        # claude_session_id is now read directly from session object (database field)
        stats = await service.recover_on_startup(mock_db)

        assert stats["enabled"] is True
        assert stats["running_found"] == 1
        assert stats["recovered"] == 1

    @pytest.mark.asyncio
    async def test_skip_sessions_exceeding_max_attempts(self, mock_task_queue, auto_resume_config):
        """Test that sessions exceeding max resume attempts are skipped."""
        from src.services.auto_resume import AutoResumeService
        from unittest.mock import MagicMock

        service = AutoResumeService(mock_task_queue, auto_resume_config)

        # Create mock user
        mock_user = MagicMock()
        mock_user.username = "testuser"

        # Create mock session with max attempts reached
        mock_session = MagicMock()
        mock_session.id = "test-session-123"
        mock_session.user_id = "user-456"
        mock_session.user = mock_user
        mock_session.task = "Running task"
        mock_session.status = "running"
        mock_session.resume_attempts = 3  # At max attempts
        mock_session.updated_at = datetime.now(timezone.utc)

        # Setup mock DB - the code uses scalars().all()
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_session]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        stats = await service.recover_on_startup(mock_db)

        assert stats["skipped_max_attempts"] == 1
        assert stats["marked_failed"] == 1
        mock_task_queue.enqueue.assert_not_called()


# =============================================================================
# Queue Error Handling Tests
# =============================================================================

class TestQueueErrorHandling:
    """Tests for queue error handling and edge cases."""

    @pytest.fixture
    def mock_redis_failing(self):
        """Create a mock Redis client that raises connection errors for all operations."""
        from redis.exceptions import ConnectionError
        error = ConnectionError("Connection refused")
        mock = AsyncMock()
        # All Redis methods used by TaskQueue should raise ConnectionError
        mock.zadd = AsyncMock(side_effect=error)
        mock.zcard = AsyncMock(side_effect=error)
        mock.zpopmin = AsyncMock(side_effect=error)
        mock.zrange = AsyncMock(side_effect=error)
        mock.zrank = AsyncMock(side_effect=error)
        mock.zrem = AsyncMock(side_effect=error)
        mock.get = AsyncMock(side_effect=error)
        mock.set = AsyncMock(side_effect=error)
        mock.delete = AsyncMock(side_effect=error)
        mock.ping = AsyncMock(side_effect=error)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        return mock

    @pytest.mark.asyncio
    async def test_enqueue_raises_unavailable_on_connection_error(self, mock_redis_failing):
        """Test that enqueue raises QueueUnavailableError on Redis connection failure."""
        from src.services.task_queue import TaskQueue, QueuedTask, QueueUnavailableError

        queue = TaskQueue.__new__(TaskQueue)
        queue._pool = MagicMock()
        queue._lock = asyncio.Lock()
        queue._task_ttl_seconds = 86400
        queue._max_queue_size = 1000

        task = QueuedTask(
            session_id="test-123",
            user_id="user-456",
            task="Test task",
            priority=0,
            queued_at=datetime.now(timezone.utc),
        )

        with patch('src.services.task_queue.redis.Redis', return_value=mock_redis_failing):
            with pytest.raises(QueueUnavailableError) as exc_info:
                await queue.enqueue(task)

            assert "unavailable" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_enqueue_raises_overflow_when_full(self):
        """Test that enqueue raises QueueOverflowError when queue is at capacity."""
        from src.services.task_queue import TaskQueue, QueuedTask, QueueOverflowError

        mock_redis = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=100)  # Queue is at max
        mock_redis.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.__aexit__ = AsyncMock(return_value=None)

        queue = TaskQueue.__new__(TaskQueue)
        queue._pool = MagicMock()
        queue._lock = asyncio.Lock()
        queue._task_ttl_seconds = 86400
        queue._max_queue_size = 100  # Max is 100

        task = QueuedTask(
            session_id="test-123",
            user_id="user-456",
            task="Test task",
            priority=0,
            queued_at=datetime.now(timezone.utc),
        )

        with patch('src.services.task_queue.redis.Redis', return_value=mock_redis):
            with pytest.raises(QueueOverflowError) as exc_info:
                await queue.enqueue(task)

            assert exc_info.value.current_size == 100
            assert exc_info.value.max_size == 100

    @pytest.mark.asyncio
    async def test_dequeue_returns_none_on_connection_error(self, mock_redis_failing):
        """Test that dequeue gracefully returns None on Redis connection failure."""
        from src.services.task_queue import TaskQueue

        queue = TaskQueue.__new__(TaskQueue)
        queue._pool = MagicMock()
        queue._lock = asyncio.Lock()
        queue._task_ttl_seconds = 86400

        with patch('src.services.task_queue.redis.Redis', return_value=mock_redis_failing):
            result = await queue.dequeue()

            # Should return None instead of raising
            assert result is None

    @pytest.mark.asyncio
    async def test_peek_returns_none_on_connection_error(self, mock_redis_failing):
        """Test that peek gracefully returns None on Redis connection failure."""
        from src.services.task_queue import TaskQueue

        queue = TaskQueue.__new__(TaskQueue)
        queue._pool = MagicMock()
        queue._lock = asyncio.Lock()
        queue._task_ttl_seconds = 86400

        with patch('src.services.task_queue.redis.Redis', return_value=mock_redis_failing):
            result = await queue.peek()

            # Should return None instead of raising
            assert result is None

    @pytest.mark.asyncio
    async def test_health_check_returns_unhealthy_on_connection_error(self, mock_redis_failing):
        """Test that health_check returns unhealthy status on Redis failure."""
        from src.services.task_queue import TaskQueue

        queue = TaskQueue.__new__(TaskQueue)
        queue._pool = MagicMock()
        queue._lock = asyncio.Lock()
        queue._task_ttl_seconds = 86400

        with patch('src.services.task_queue.redis.Redis', return_value=mock_redis_failing):
            is_healthy, message = await queue.health_check()

            assert is_healthy is False
            assert "connection" in message.lower() or "failed" in message.lower()


# =============================================================================
# Queue Processor Tests
# =============================================================================

class TestQueueProcessor:
    """Tests for QueueProcessor functionality."""

    @pytest.fixture
    def mock_task_queue(self):
        """Create a mock TaskQueue."""
        mock = AsyncMock()
        mock.peek = AsyncMock(return_value=None)
        mock.dequeue = AsyncMock(return_value=None)
        mock.get_queue_length = AsyncMock(return_value=0)
        mock.remove = AsyncMock(return_value=True)
        return mock

    @pytest.fixture
    def mock_quota_manager(self):
        """Create a mock QuotaManager."""
        mock = MagicMock()
        mock.can_start_task = AsyncMock(return_value=(True, ""))
        mock.get_global_active.return_value = 0
        mock.config.global_max_concurrent = 4
        return mock

    def test_processor_initialization(self, mock_task_queue, mock_quota_manager):
        """Test QueueProcessor initialization with all parameters."""
        from src.services.queue_processor import QueueProcessor

        processor = QueueProcessor(
            task_queue=mock_task_queue,
            quota_manager=mock_quota_manager,
            processing_interval_ms=1000,
            redis_url="redis://localhost:6379",
            task_timeout_minutes=60,
        )

        assert processor._interval_s == 1.0
        assert processor._task_timeout_minutes == 60
        assert processor._running is False

    @pytest.mark.asyncio
    async def test_processor_start_and_stop(self, mock_task_queue, mock_quota_manager):
        """Test starting and stopping the processor."""
        from src.services.queue_processor import QueueProcessor

        processor = QueueProcessor(
            task_queue=mock_task_queue,
            quota_manager=mock_quota_manager,
        )

        await processor.start()
        assert processor._running is True

        await processor.stop()
        assert processor._running is False

    @pytest.mark.asyncio
    async def test_get_queue_stats(self, mock_task_queue, mock_quota_manager):
        """Test getting queue statistics."""
        from src.services.queue_processor import QueueProcessor

        mock_task_queue.get_queue_length = AsyncMock(return_value=5)
        mock_quota_manager.get_global_active.return_value = 2

        processor = QueueProcessor(
            task_queue=mock_task_queue,
            quota_manager=mock_quota_manager,
        )

        stats = await processor.get_queue_stats()

        assert stats["queue_length"] == 5
        assert stats["global_active"] == 2
        assert stats["processor_running"] is False
        assert stats["max_concurrent"] == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
