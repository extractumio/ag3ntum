"""
Quota management for task execution limits.

Tracks and enforces:
1. Global concurrent task limit (across all users)
2. Per-user concurrent task limit
3. Per-user daily task limit (optional, uses database persistence)

Uses in-memory tracking for fast checks with Redis as backing store
for per-user active counts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .queue_config import QuotaConfig

if TYPE_CHECKING:
    from .task_queue import TaskQueue
    from ..db.models import UserQuota

logger = logging.getLogger(__name__)


class QuotaManager:
    """
    Manages execution quotas for task scheduling.

    Checks three quota types before allowing a task to start:
    1. Global concurrent limit - total tasks across all users
    2. Per-user concurrent limit - tasks per individual user
    3. Per-user daily limit - tasks per user per day (optional)

    The global counter is in-memory for speed. Per-user counters use Redis
    (via TaskQueue) for cross-process coordination.
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        config: QuotaConfig,
    ) -> None:
        """
        Initialize quota manager.

        Args:
            task_queue: TaskQueue instance for user active counts.
            config: Quota configuration with limits.
        """
        self._queue = task_queue
        self._config = config
        self._global_active_count = 0

        logger.info(
            f"QuotaManager initialized: "
            f"global_max={config.global_max_concurrent}, "
            f"per_user_max={config.per_user_max_concurrent}, "
            f"daily_limit={config.per_user_daily_limit}"
        )

    async def can_start_task(
        self,
        user_id: str,
        db: Optional[AsyncSession] = None,
    ) -> tuple[bool, str]:
        """
        Check if a new task can start for the given user.

        Args:
            user_id: The user requesting to start a task.
            db: Optional database session for daily limit check.

        Returns:
            Tuple of (can_start, reason_if_not).
            If can_start is True, reason is empty string.
            If can_start is False, reason explains why.
        """
        # Check 1: Global concurrent limit
        if self._global_active_count >= self._config.global_max_concurrent:
            return (
                False,
                f"Global limit reached ({self._config.global_max_concurrent} concurrent tasks)",
            )

        # Check 2: Per-user concurrent limit
        user_active = await self._queue.get_user_active_count(user_id)
        if user_active >= self._config.per_user_max_concurrent:
            return (
                False,
                f"User concurrent limit reached ({self._config.per_user_max_concurrent} tasks)",
            )

        # Check 3: Per-user daily limit (if enabled and db provided)
        if self._config.per_user_daily_limit > 0 and db is not None:
            can_start, reason = await self._check_daily_limit(user_id, db)
            if not can_start:
                return (False, reason)

        return (True, "")

    async def _check_daily_limit(
        self,
        user_id: str,
        db: AsyncSession,
    ) -> tuple[bool, str]:
        """
        Check per-user daily task limit using database.

        Args:
            user_id: The user ID to check.
            db: Database session.

        Returns:
            Tuple of (can_start, reason_if_not).
        """
        from ..db.models import UserQuota

        # Get or create user quota record
        result = await db.execute(
            select(UserQuota).where(UserQuota.user_id == user_id)
        )
        quota = result.scalar_one_or_none()

        if quota is None:
            # No quota record - user hasn't hit limit yet, allow
            return (True, "")

        # Check if we need to reset daily count (new day)
        quota.reset_if_needed()

        # Check against daily limit
        if quota.tasks_today >= quota.max_daily_tasks:
            return (
                False,
                f"Daily limit reached ({quota.max_daily_tasks} tasks/day)",
            )

        return (True, "")

    async def increment_daily_count(
        self,
        user_id: str,
        db: AsyncSession,
    ) -> None:
        """
        Increment the daily task count for a user.

        Called when a task actually starts (not just queued).

        Args:
            user_id: The user ID.
            db: Database session.
        """
        if self._config.per_user_daily_limit <= 0:
            # Daily limit disabled
            return

        from ..db.models import UserQuota

        result = await db.execute(
            select(UserQuota).where(UserQuota.user_id == user_id)
        )
        quota = result.scalar_one_or_none()

        if quota is None:
            # Create new quota record
            quota = UserQuota(
                user_id=user_id,
                max_concurrent_tasks=self._config.per_user_max_concurrent,
                max_daily_tasks=self._config.per_user_daily_limit,
                tasks_today=1,
                last_reset=datetime.now(timezone.utc),
            )
            db.add(quota)
        else:
            # Reset if new day, then increment
            quota.reset_if_needed()
            quota.tasks_today += 1

        await db.commit()
        logger.debug(f"User {user_id} daily count: {quota.tasks_today}")

    def increment_global(self) -> None:
        """Increment global active count when task starts."""
        self._global_active_count += 1
        logger.debug(f"Global active count: {self._global_active_count}")

    def decrement_global(self) -> None:
        """Decrement global active count when task completes."""
        self._global_active_count = max(0, self._global_active_count - 1)
        logger.debug(f"Global active count: {self._global_active_count}")

    def get_global_active(self) -> int:
        """Get current global active task count."""
        return self._global_active_count

    def reset_global_count(self) -> None:
        """Reset global count (e.g., on startup after determining actual running tasks)."""
        self._global_active_count = 0
        logger.info("Global active count reset to 0")

    def set_global_count(self, count: int) -> None:
        """Set global count to specific value (e.g., after counting running tasks on startup)."""
        self._global_active_count = count
        logger.info(f"Global active count set to {count}")

    @property
    def config(self) -> QuotaConfig:
        """Get the quota configuration."""
        return self._config
