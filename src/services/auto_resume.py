"""
Auto-resume service for recovering interrupted sessions on startup.

When the container restarts, sessions that were actively running get stuck
in "running" state. This service finds those sessions and queues them for
automatic resumption.

All session metadata (including claude_session_id for resumption) is stored
in the SQLite database - no file-based storage.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Session
from .task_queue import TaskQueue, QueuedTask
from .queue_config import AutoResumeConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AutoResumeService:
    """
    Handles automatic resumption of interrupted sessions.

    On startup:
    1. Find sessions in 'running' state from last N hours
    2. Find sessions in 'queued' state
    3. Queue them for resume/restart in order

    Sessions are queued with high priority (100) so they're processed
    before normal tasks.
    """

    # Priority levels
    PRIORITY_AUTO_RESUME = 100  # Interrupted running tasks
    PRIORITY_QUEUED_RECOVERY = 50  # Previously queued tasks

    def __init__(
        self,
        task_queue: TaskQueue,
        config: AutoResumeConfig,
    ) -> None:
        """
        Initialize auto-resume service.

        Args:
            task_queue: The TaskQueue to enqueue recovered sessions.
            config: Auto-resume configuration.
        """
        self._queue = task_queue
        self._config = config

        logger.info(
            f"AutoResumeService initialized: enabled={config.enabled}, "
            f"max_age_hours={config.max_session_age_hours}, "
            f"max_attempts={config.max_resume_attempts}"
        )

    async def recover_on_startup(self, db: AsyncSession) -> dict:
        """
        Recover interrupted sessions on startup.

        This should be called during application lifespan startup,
        BEFORE starting the QueueProcessor.

        Args:
            db: Database session.

        Returns:
            Statistics about recovered sessions.
        """
        if not self._config.enabled:
            logger.info("Auto-resume is disabled")
            return {"enabled": False}

        stats = {
            "enabled": True,
            "running_found": 0,
            "queued_found": 0,
            "recovered": 0,
            "skipped_too_old": 0,
            "skipped_max_attempts": 0,
            "skipped_no_resume_id": 0,
            "marked_failed": 0,
        }

        # Calculate cutoff time
        cutoff_time = datetime.now(timezone.utc) - timedelta(
            hours=self._config.max_session_age_hours
        )

        # Find sessions that need recovery
        # - Status is "running" (was interrupted) or "queued" (was waiting)
        # - Updated within the cutoff window
        query = select(Session).where(
            and_(
                Session.status.in_(["running", "queued"]),
                Session.updated_at >= cutoff_time,
            )
        ).order_by(Session.updated_at.asc())  # Oldest first

        result = await db.execute(query)
        sessions = list(result.scalars().all())

        logger.info(
            f"Auto-resume: found {len(sessions)} sessions to check "
            f"(cutoff: {cutoff_time.isoformat()})"
        )

        for session in sessions:
            if session.status == "running":
                stats["running_found"] += 1
            else:
                stats["queued_found"] += 1

            # Check resume attempts limit
            resume_attempts = session.resume_attempts or 0
            if resume_attempts >= self._config.max_resume_attempts:
                logger.warning(
                    f"Session {session.id} exceeded max resume attempts "
                    f"({resume_attempts}/{self._config.max_resume_attempts})"
                )
                session.status = "failed"
                session.completed_at = datetime.now(timezone.utc)
                stats["skipped_max_attempts"] += 1
                stats["marked_failed"] += 1
                continue

            # Check if session has claude_session_id (can be resumed)
            # This is now stored in the database, captured in real-time during execution
            has_resume_id = bool(session.claude_session_id)

            # If running but no claude_session_id, the agent never connected to Claude properly
            if not has_resume_id and session.status == "running":
                logger.info(
                    f"Session {session.id} has no claude_session_id and was running, "
                    f"marking as failed"
                )
                session.status = "failed"
                session.completed_at = datetime.now(timezone.utc)
                stats["skipped_no_resume_id"] += 1
                stats["marked_failed"] += 1
                continue

            # Queue for resume/restart
            priority = (
                self.PRIORITY_AUTO_RESUME
                if session.status == "running"
                else self.PRIORITY_QUEUED_RECOVERY
            )

            queued_task = QueuedTask(
                session_id=session.id,
                user_id=session.user_id,
                task=session.task or "Resume interrupted task",
                priority=priority,
                queued_at=datetime.now(timezone.utc),
                is_auto_resume=True,
                # Only set resume_from if we have a valid claude_session_id
                resume_from=session.id if has_resume_id else None,
            )

            position = await self._queue.enqueue(queued_task)

            # Update session in database
            session.status = "queued"
            session.queue_position = position
            session.queued_at = datetime.now(timezone.utc)
            session.resume_attempts = resume_attempts + 1
            session.is_auto_resume = True

            stats["recovered"] += 1
            logger.info(
                f"Queued session {session.id} for auto-resume "
                f"(position: {position}, attempts: {session.resume_attempts})"
            )

        # Commit all changes
        await db.commit()

        # Log summary
        logger.info(
            f"Auto-resume recovery complete: "
            f"{stats['recovered']} sessions queued, "
            f"{stats['skipped_max_attempts']} skipped (max attempts), "
            f"{stats['skipped_no_resume_id']} skipped (no claude_session_id), "
            f"{stats['marked_failed']} marked as failed"
        )

        return stats

    async def cleanup_old_sessions(self, db: AsyncSession) -> int:
        """
        Mark very old "running" sessions as failed.

        Sessions older than max_session_age_hours that are still in
        running/queued state are considered abandoned and marked as failed.

        This is called after recovery to clean up any stragglers that
        didn't get processed.

        Args:
            db: Database session.

        Returns:
            Number of sessions cleaned up.
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(
            hours=self._config.max_session_age_hours
        )

        # Find old sessions still in non-terminal state
        query = select(Session).where(
            and_(
                Session.status.in_(["running", "queued", "pending"]),
                Session.updated_at < cutoff_time,
            )
        )

        result = await db.execute(query)
        sessions = list(result.scalars().all())

        count = 0
        for session in sessions:
            session.status = "failed"
            session.completed_at = datetime.now(timezone.utc)
            count += 1
            logger.info(
                f"Marked old session {session.id} as failed "
                f"(last updated: {session.updated_at.isoformat()})"
            )

        if count > 0:
            await db.commit()
            logger.info(f"Cleaned up {count} old abandoned sessions")

        return count
