"""
Queue management endpoints for Ag3ntum API.

Provides endpoints for:
- GET /queue/status - Get current queue status
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import Session
from ..deps import get_current_user_id
from ..models import QueueStatusResponse, QueuedSessionInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("/status", response_model=QueueStatusResponse)
async def get_queue_status(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> QueueStatusResponse:
    """
    Get current queue status.

    Returns global queue statistics and user's queued tasks.
    """
    # Get queue components from app state
    task_queue = getattr(request.app.state, "task_queue", None)
    quota_manager = getattr(request.app.state, "quota_manager", None)

    if task_queue is None or quota_manager is None:
        # Queue system not enabled - return zeros
        return QueueStatusResponse(
            global_queue_length=0,
            global_active_tasks=0,
            user_active_tasks=0,
            user_queued_tasks=[],
            max_concurrent_global=4,  # Default
            max_concurrent_user=2,  # Default
        )

    # Get global stats
    queue_length = await task_queue.get_queue_length()
    global_active = quota_manager.get_global_active()
    user_active = await task_queue.get_user_active_count(user_id)

    # Get user's queued sessions from database
    result = await db.execute(
        select(Session).where(
            Session.user_id == user_id,
            Session.status == "queued",
        ).order_by(Session.queue_position)
    )
    queued_sessions = result.scalars().all()

    user_queued_tasks = [
        QueuedSessionInfo(
            session_id=s.id,
            queue_position=s.queue_position,
            queued_at=s.queued_at,
            is_auto_resume=s.is_auto_resume,
        )
        for s in queued_sessions
    ]

    return QueueStatusResponse(
        global_queue_length=queue_length,
        global_active_tasks=global_active,
        user_active_tasks=user_active,
        user_queued_tasks=user_queued_tasks,
        max_concurrent_global=quota_manager.config.global_max_concurrent,
        max_concurrent_user=quota_manager.config.per_user_max_concurrent,
    )
