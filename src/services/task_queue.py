"""
Task queue service using Redis sorted sets for priority-based queuing.

Uses Redis ZADD/ZPOPMIN for O(log N) queue operations with priority support.
Higher priority tasks are processed first (lower score = higher priority).

Key structure:
- task_queue:pending - Sorted set of pending tasks (session_id -> score)
- task_queue:task:{session_id} - JSON string with full task details
- task_queue:user:{user_id}:active - Set of active session IDs for user

Error Handling:
- Redis connection failures raise QueueUnavailableError
- Callers should handle this gracefully (fail-closed: reject new tasks)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError

logger = logging.getLogger(__name__)

# Default TTL for task data (24 hours)
DEFAULT_TASK_TTL_SECONDS = 86400


class QueueUnavailableError(Exception):
    """
    Raised when the task queue is unavailable (Redis connection failure).

    Callers should handle this by:
    - Rejecting new task submissions (fail-closed)
    - Returning appropriate HTTP 503 Service Unavailable
    """

    def __init__(self, message: str = "Task queue unavailable", cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class QueueOverflowError(Exception):
    """
    Raised when the task queue is full (max_queue_size reached).

    Callers should handle this by:
    - Returning HTTP 429 Too Many Requests or 503 Service Unavailable
    - Suggesting the user retry later
    """

    def __init__(self, message: str = "Task queue is full", current_size: int = 0, max_size: int = 0):
        super().__init__(message)
        self.current_size = current_size
        self.max_size = max_size


@dataclass
class QueuedTask:
    """Task waiting in queue."""
    session_id: str
    user_id: str
    task: str
    priority: int  # Higher = more priority
    queued_at: datetime
    is_auto_resume: bool = False
    resume_from: Optional[str] = None  # Session ID to resume from

    def to_json(self) -> str:
        """Serialize to JSON string."""
        data = asdict(self)
        data["queued_at"] = self.queued_at.isoformat()
        return json.dumps(data)

    @classmethod
    def from_json(cls, data: str) -> QueuedTask:
        """Deserialize from JSON string."""
        parsed = json.loads(data)
        parsed["queued_at"] = datetime.fromisoformat(parsed["queued_at"])
        return cls(**parsed)


class TaskQueue:
    """
    Redis-backed priority task queue.

    Uses sorted sets where score = timestamp - (priority * 1_000_000)
    Lower score = higher priority = processed first.

    Key structure:
    - task_queue:pending - Sorted set of pending tasks
    - task_queue:task:{session_id} - Hash with full task details
    - task_queue:user:{user_id}:active - Set of active session IDs for user
    """

    QUEUE_KEY = "task_queue:pending"
    TASK_KEY_PREFIX = "task_queue:task:"
    USER_ACTIVE_PREFIX = "task_queue:user:"

    def __init__(
        self,
        redis_url: str,
        task_ttl_seconds: int = DEFAULT_TASK_TTL_SECONDS,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
        max_queue_size: int = 1000,
    ) -> None:
        """
        Initialize task queue.

        Args:
            redis_url: Redis connection URL.
            task_ttl_seconds: TTL for task data keys.
            socket_timeout: Redis socket timeout in seconds.
            socket_connect_timeout: Redis connection timeout in seconds.
            max_queue_size: Maximum number of tasks allowed in queue (0 = unlimited).
        """
        self._redis_url = redis_url
        self._task_ttl_seconds = task_ttl_seconds
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._max_queue_size = max_queue_size
        self._pool: Optional[redis.ConnectionPool] = None
        self._lock = asyncio.Lock()

        logger.info(f"TaskQueue initialized: url={redis_url}, max_queue_size={max_queue_size}")

    async def _ensure_pool(self) -> redis.ConnectionPool:
        """Lazy-initialize Redis connection pool."""
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = redis.ConnectionPool.from_url(
                        self._redis_url,
                        socket_timeout=self._socket_timeout,
                        socket_connect_timeout=self._socket_connect_timeout,
                        decode_responses=True,
                    )
                    logger.debug("TaskQueue Redis connection pool created")
        return self._pool

    async def enqueue(self, task: QueuedTask) -> int:
        """
        Add task to queue.

        Args:
            task: The task to queue.

        Returns:
            Current queue position (1-based).

        Raises:
            QueueUnavailableError: If Redis is unavailable.
            QueueOverflowError: If queue has reached max_queue_size.
        """
        try:
            pool = await self._ensure_pool()
            async with redis.Redis(connection_pool=pool) as conn:
                # Check queue size limit before adding
                if self._max_queue_size > 0:
                    current_size = await conn.zcard(self.QUEUE_KEY)
                    if current_size >= self._max_queue_size:
                        logger.warning(
                            f"Queue overflow: {current_size}/{self._max_queue_size} "
                            f"- rejecting task {task.session_id}"
                        )
                        raise QueueOverflowError(
                            f"Queue is full ({current_size}/{self._max_queue_size} tasks)",
                            current_size=current_size,
                            max_size=self._max_queue_size,
                        )

                # Calculate score: lower = higher priority
                # timestamp ensures FIFO within same priority
                timestamp = task.queued_at.timestamp()
                score = timestamp - (task.priority * 1_000_000)

                # Store task details with TTL
                task_key = f"{self.TASK_KEY_PREFIX}{task.session_id}"
                await conn.set(task_key, task.to_json(), ex=self._task_ttl_seconds)

                # Add to sorted set
                await conn.zadd(self.QUEUE_KEY, {task.session_id: score})

                # Get position (rank + 1 for 1-based)
                rank = await conn.zrank(self.QUEUE_KEY, task.session_id)
                position = (rank or 0) + 1

                logger.info(
                    f"Task {task.session_id} enqueued at position {position} "
                    f"(priority={task.priority}, auto_resume={task.is_auto_resume})"
                )
                return position
        except QueueOverflowError:
            raise  # Re-raise overflow error without wrapping
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Redis unavailable for enqueue: {e}")
            raise QueueUnavailableError("Cannot enqueue task - queue unavailable", cause=e) from e
        except RedisError as e:
            logger.error(f"Redis error during enqueue: {e}")
            raise QueueUnavailableError(f"Queue error: {e}", cause=e) from e

    async def dequeue(self) -> Optional[QueuedTask]:
        """
        Remove and return highest priority task.

        Returns:
            QueuedTask or None if queue empty.

        Note: Does not raise QueueUnavailableError - returns None on Redis failure
        to allow graceful degradation of queue processing.
        """
        try:
            pool = await self._ensure_pool()
            async with redis.Redis(connection_pool=pool) as conn:
                # ZPOPMIN returns lowest score (highest priority)
                result = await conn.zpopmin(self.QUEUE_KEY, count=1)
                if not result:
                    return None

                session_id, score = result[0]
                task_key = f"{self.TASK_KEY_PREFIX}{session_id}"
                task_json = await conn.get(task_key)

                if not task_json:
                    logger.warning(f"Task data missing for session {session_id}")
                    return None

                await conn.delete(task_key)
                task = QueuedTask.from_json(task_json)
                logger.info(f"Task {session_id} dequeued")
                return task
        except (ConnectionError, TimeoutError, RedisError) as e:
            logger.error(f"Redis error during dequeue: {e}")
            return None

    async def peek(self) -> Optional[QueuedTask]:
        """
        Get highest priority task without removing it.

        Returns:
            QueuedTask or None if queue empty.

        Note: Does not raise QueueUnavailableError - returns None on Redis failure.
        """
        try:
            pool = await self._ensure_pool()
            async with redis.Redis(connection_pool=pool) as conn:
                # Get first item without removing
                result = await conn.zrange(self.QUEUE_KEY, 0, 0)
                if not result:
                    return None

                session_id = result[0]
                task_key = f"{self.TASK_KEY_PREFIX}{session_id}"
                task_json = await conn.get(task_key)

                if not task_json:
                    # Orphaned queue entry, remove it
                    await conn.zrem(self.QUEUE_KEY, session_id)
                    logger.warning(f"Removed orphaned queue entry for {session_id}")
                    return None

                return QueuedTask.from_json(task_json)
        except (ConnectionError, TimeoutError, RedisError) as e:
            logger.error(f"Redis error during peek: {e}")
            return None

    async def get_position(self, session_id: str) -> Optional[int]:
        """
        Get current queue position (1-based) or None if not queued.

        Args:
            session_id: The session ID to check.

        Returns:
            Position (1-based) or None if not in queue.
        """
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            rank = await conn.zrank(self.QUEUE_KEY, session_id)
            return (rank + 1) if rank is not None else None

    async def get_queue_length(self) -> int:
        """Get total number of queued tasks."""
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            return await conn.zcard(self.QUEUE_KEY)

    async def remove(self, session_id: str) -> bool:
        """
        Remove task from queue (e.g., on cancel).

        Args:
            session_id: The session ID to remove.

        Returns:
            True if removed, False if not found.
        """
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            removed = await conn.zrem(self.QUEUE_KEY, session_id)
            await conn.delete(f"{self.TASK_KEY_PREFIX}{session_id}")
            if removed:
                logger.info(f"Task {session_id} removed from queue")
            return removed > 0

    async def get_user_active_count(self, user_id: str) -> int:
        """
        Get number of active (running) tasks for user.

        Args:
            user_id: The user ID to check.

        Returns:
            Number of active tasks.
        """
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            return await conn.scard(f"{self.USER_ACTIVE_PREFIX}{user_id}:active")

    async def mark_user_active(self, user_id: str, session_id: str) -> None:
        """
        Mark task as active for user quota tracking.

        Args:
            user_id: The user ID.
            session_id: The session ID that is now active.
        """
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            await conn.sadd(f"{self.USER_ACTIVE_PREFIX}{user_id}:active", session_id)
            logger.debug(f"Marked {session_id} as active for user {user_id}")

    async def mark_user_inactive(self, user_id: str, session_id: str) -> None:
        """
        Mark task as inactive (completed/failed/cancelled).

        Args:
            user_id: The user ID.
            session_id: The session ID that is no longer active.
        """
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            await conn.srem(f"{self.USER_ACTIVE_PREFIX}{user_id}:active", session_id)
            logger.debug(f"Marked {session_id} as inactive for user {user_id}")

    async def get_all_user_active(self, user_id: str) -> list[str]:
        """
        Get all active session IDs for a user.

        Args:
            user_id: The user ID.

        Returns:
            List of active session IDs.
        """
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            return list(await conn.smembers(f"{self.USER_ACTIVE_PREFIX}{user_id}:active"))

    async def clear_user_active(self, user_id: str) -> int:
        """
        Clear all active sessions for a user (e.g., on startup cleanup).

        Args:
            user_id: The user ID.

        Returns:
            Number of entries cleared.
        """
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            key = f"{self.USER_ACTIVE_PREFIX}{user_id}:active"
            count = await conn.scard(key)
            if count > 0:
                await conn.delete(key)
                logger.info(f"Cleared {count} active sessions for user {user_id}")
            return count

    async def get_queued_sessions(self, limit: int = 100) -> list[tuple[str, float]]:
        """
        Get list of queued session IDs with their scores.

        Args:
            limit: Maximum number to return.

        Returns:
            List of (session_id, score) tuples.
        """
        pool = await self._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            return await conn.zrange(
                self.QUEUE_KEY, 0, limit - 1, withscores=True
            )

    async def health_check(self) -> tuple[bool, str]:
        """
        Check if the queue is healthy (Redis is available).

        Returns:
            Tuple of (is_healthy, message).
        """
        try:
            pool = await self._ensure_pool()
            async with redis.Redis(connection_pool=pool) as conn:
                await conn.ping()
                queue_len = await conn.zcard(self.QUEUE_KEY)
                return (True, f"Queue operational, {queue_len} tasks pending")
        except (ConnectionError, TimeoutError) as e:
            return (False, f"Redis connection failed: {e}")
        except RedisError as e:
            return (False, f"Redis error: {e}")

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
            logger.info("TaskQueue connection pool closed")
