"""
Background processor for task queue.

Runs as a background asyncio task, continuously checking the queue
and starting tasks when quotas allow.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import USERS_DIR
from ..db.database import AsyncSessionLocal
from ..db.models import Session, User
from .task_queue import TaskQueue, QueuedTask
from .quota_manager import QuotaManager
from . import event_service

if TYPE_CHECKING:
    from .agent_runner import AgentRunner, TaskParams

logger = logging.getLogger(__name__)


class QueueProcessor:
    """
    Background task queue processor.

    Continuously monitors the queue and starts tasks when:
    1. Queue has pending tasks
    2. Quotas allow (global and per-user)

    Emits SSE events for queue status updates.
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        quota_manager: QuotaManager,
        processing_interval_ms: int = 500,
        redis_url: Optional[str] = None,
        task_timeout_minutes: int = 30,
    ) -> None:
        """
        Initialize queue processor.

        Args:
            task_queue: The TaskQueue instance.
            quota_manager: The QuotaManager instance.
            processing_interval_ms: How often to check queue (milliseconds).
            redis_url: Redis URL for event publishing.
            task_timeout_minutes: Timeout for queued tasks (0 = no timeout).
        """
        self._queue = task_queue
        self._quota_manager = quota_manager
        self._interval_s = processing_interval_ms / 1000
        self._redis_url = redis_url
        self._task_timeout_minutes = task_timeout_minutes
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._timeout_check_task: Optional[asyncio.Task] = None
        self._last_timeout_check = datetime.now(timezone.utc)
        # Check for timed-out tasks every 60 seconds
        self._timeout_check_interval_s = 60

        logger.info(
            f"QueueProcessor initialized: interval={processing_interval_ms}ms, "
            f"task_timeout={task_timeout_minutes}min"
        )

    async def start(self) -> None:
        """Start the background processor."""
        if self._running:
            logger.warning("QueueProcessor already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("QueueProcessor started")

    async def stop(self) -> None:
        """Stop the background processor gracefully."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("QueueProcessor stopped")

    async def _process_loop(self) -> None:
        """Main processing loop."""
        logger.debug("QueueProcessor loop started")
        while self._running:
            try:
                await self._process_next()

                # Periodically check for timed-out tasks
                now = datetime.now(timezone.utc)
                if (now - self._last_timeout_check).total_seconds() >= self._timeout_check_interval_s:
                    await self._cleanup_timed_out_tasks()
                    self._last_timeout_check = now

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Queue processing error: {e}")

            await asyncio.sleep(self._interval_s)

    async def _process_next(self) -> None:
        """Process the next task in queue if quotas allow."""
        # Peek at next task without dequeuing
        task = await self._queue.peek()
        if task is None:
            return

        # Check quotas for this user
        async with AsyncSessionLocal() as db:
            can_start, reason = await self._quota_manager.can_start_task(
                task.user_id, db
            )

        if not can_start:
            logger.debug(
                f"Task {task.session_id} waiting in queue: {reason}"
            )
            return

        # Quotas allow - dequeue and start
        task = await self._queue.dequeue()
        if task is None:
            # Race condition - another processor grabbed it
            return

        await self._start_task(task)

        # Emit queue_position_update events for remaining queued tasks
        await self._emit_position_updates()

    async def _start_task(self, queued_task: QueuedTask) -> None:
        """
        Start a queued task.

        Args:
            queued_task: The task to start.
        """
        session_id = queued_task.session_id
        user_id = queued_task.user_id

        logger.info(
            f"Starting {'auto-resume' if queued_task.is_auto_resume else 'queued'} "
            f"task for session {session_id}"
        )

        try:
            async with AsyncSessionLocal() as db:
                # Get session
                result = await db.execute(
                    select(Session).where(Session.id == session_id)
                )
                session = result.scalar_one_or_none()

                if not session:
                    logger.error(f"Session {session_id} not found")
                    return

                # Get user
                user_result = await db.execute(
                    select(User).where(User.id == user_id)
                )
                user = user_result.scalar_one_or_none()

                if not user:
                    logger.error(f"User {user_id} not found")
                    return

                # Update session status to running
                session.status = "running"
                session.queue_position = None
                session.updated_at = datetime.now(timezone.utc)
                await db.commit()

                # Increment quotas
                self._quota_manager.increment_global()
                await self._queue.mark_user_active(user_id, session_id)
                await self._quota_manager.increment_daily_count(user_id, db)

            # Emit queue_started event
            await self._emit_queue_event(session_id, "queue_started", {
                "session_id": session_id,
                "message": "Task started after queuing",
                "was_auto_resume": queued_task.is_auto_resume,
            })

            # Build task parameters
            user_sessions_dir = USERS_DIR / user.username / "sessions"

            # Build resume context if auto-resume
            task_text = queued_task.task
            if queued_task.is_auto_resume:
                task_text = (
                    "<resume-context>\n"
                    "Previous execution was interrupted by system restart.\n"
                    "Resume from the last known and stable checkpoint.\n"
                    "</resume-context>\n\n"
                    f"{task_text}"
                )

            # Import here to avoid circular imports
            from .agent_runner import agent_runner, TaskParams

            params = TaskParams(
                task=task_text,
                session_id=session_id,
                user_id=user_id,
                sessions_dir=str(user_sessions_dir),
                resume_session_id=queued_task.resume_from or session_id,
                fork_session=False,
            )

            # Start agent (this returns immediately, runs in background)
            await agent_runner.start_task(params)

        except Exception as e:
            logger.exception(f"Failed to start task {session_id}: {e}")
            # Decrement quotas on failure
            self._quota_manager.decrement_global()
            await self._queue.mark_user_inactive(user_id, session_id)

            # Update session to failed
            try:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(Session).where(Session.id == session_id)
                    )
                    session = result.scalar_one_or_none()
                    if session:
                        session.status = "failed"
                        session.updated_at = datetime.now(timezone.utc)
                        session.completed_at = datetime.now(timezone.utc)
                        await db.commit()
            except Exception as db_error:
                logger.error(f"Failed to update session status: {db_error}")

    async def _emit_position_updates(self) -> None:
        """
        Emit queue_position_update events to all remaining queued sessions.

        Called after a task is dequeued to notify other sessions that their
        position in the queue has changed.
        """
        try:
            # Get all queued sessions with their positions
            queued_sessions = await self._queue.get_queued_sessions(limit=100)

            for position, (session_id, score) in enumerate(queued_sessions, start=1):
                await self._emit_queue_event(session_id, "queue_position_update", {
                    "session_id": session_id,
                    "position": position,
                    "queue_length": len(queued_sessions),
                })

                # Also update the session's queue_position in the database
                try:
                    async with AsyncSessionLocal() as db:
                        result = await db.execute(
                            select(Session).where(Session.id == session_id)
                        )
                        session = result.scalar_one_or_none()
                        if session and session.queue_position != position:
                            session.queue_position = position
                            await db.commit()
                except Exception as db_error:
                    logger.debug(f"Failed to update queue position for {session_id}: {db_error}")

        except Exception as e:
            logger.warning(f"Failed to emit position updates: {e}")

    async def _emit_queue_event(
        self,
        session_id: str,
        event_type: str,
        data: dict,
    ) -> None:
        """
        Emit a queue-related SSE event.

        Args:
            session_id: The session ID.
            event_type: The event type (e.g., "queue_started").
            data: The event data.
        """
        try:
            # Get next sequence number
            last_seq = await event_service.get_last_sequence(session_id)
            sequence = last_seq + 1

            event = {
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sequence": sequence,
                "session_id": session_id,
            }

            # Persist event
            await event_service.record_event(event)

            # Publish to Redis for real-time delivery
            from .agent_runner import agent_runner
            await agent_runner._event_hub.publish(session_id, event)

            logger.debug(f"Emitted {event_type} event for {session_id}")
        except Exception as e:
            logger.warning(f"Failed to emit queue event: {e}")

    async def _cleanup_timed_out_tasks(self) -> None:
        """
        Clean up tasks that have been queued too long.

        Tasks queued longer than task_timeout_minutes are marked as failed
        and removed from the queue.
        """
        if self._task_timeout_minutes <= 0:
            return  # Timeout disabled

        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(
                minutes=self._task_timeout_minutes
            )

            # Get queued sessions from database that have timed out
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Session).where(
                        Session.status == "queued",
                        Session.queued_at < cutoff_time,
                    )
                )
                timed_out_sessions = list(result.scalars().all())

                if not timed_out_sessions:
                    return

                logger.info(
                    f"Found {len(timed_out_sessions)} timed-out queued tasks"
                )

                for session in timed_out_sessions:
                    # Remove from Redis queue
                    await self._queue.remove(session.id)

                    # Update session status
                    session.status = "failed"
                    session.completed_at = datetime.now(timezone.utc)
                    session.queue_position = None

                    # Emit error event
                    await self._emit_queue_event(session.id, "error", {
                        "message": f"Task timed out after waiting {self._task_timeout_minutes} minutes in queue",
                        "error_type": "queue_timeout",
                    })

                    logger.info(
                        f"Timed out session {session.id} "
                        f"(queued at {session.queued_at.isoformat()})"
                    )

                await db.commit()

        except Exception as e:
            logger.exception(f"Error cleaning up timed-out tasks: {e}")

    def on_task_complete(self, session_id: str, user_id: str) -> None:
        """
        Called when a task completes (success, failure, or cancel).

        This is registered as a callback with AgentRunner.

        Args:
            session_id: The completed session ID.
            user_id: The user ID.
        """
        logger.debug(f"Task {session_id} completed for user {user_id}")
        self._quota_manager.decrement_global()
        # Schedule async cleanup
        asyncio.create_task(
            self._queue.mark_user_inactive(user_id, session_id)
        )

    async def get_queue_stats(self) -> dict:
        """
        Get current queue statistics.

        Returns:
            Dictionary with queue stats.
        """
        queue_length = await self._queue.get_queue_length()
        return {
            "queue_length": queue_length,
            "global_active": self._quota_manager.get_global_active(),
            "processor_running": self._running,
            "max_concurrent": self._quota_manager.config.global_max_concurrent,
        }
