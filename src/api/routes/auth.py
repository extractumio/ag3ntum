"""
Authentication endpoints for Ag3ntum API.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import Session
from ...services.auth_service import auth_service, UserEnvironmentError
from ...services.agent_runner import agent_runner
from ..deps import get_current_user_id
from ..models import TokenResponse, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    email: str = Body(...),
    password: str = Body(...),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Login with email and password.

    Returns a JWT token valid for 7 days.

    Returns 403 Forbidden if user account is misconfigured (missing home/venv).
    """
    try:
        user, token, expires_in = await auth_service.authenticate(db, email, password)
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            user_id=user.id,
            expires_in=expires_in,
        )
    except UserEnvironmentError as e:
        # User exists but filesystem is misconfigured - must be recreated
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )


@router.post("/logout")
async def logout(
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """
    Logout (client-side token deletion).

    The server does not track tokens, so logout is handled client-side
    by deleting the token from storage.
    """
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Get current user info.

    Returns information about the authenticated user.
    """
    user = await auth_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
    )


@router.get("/me/events")
async def stream_user_events(
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    Stream real-time events for all user sessions (SSE).

    This endpoint provides cross-session updates for:
    - Session status changes (running, completed, failed, queued)
    - Queue position updates
    - New sessions created

    Used by the SessionListTab to show real-time updates with badges.
    """
    # Extract token from query or header
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing access token",
        )

    user_id = await auth_service.validate_token(token, db)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    async def event_generator():
        """
        Generate SSE events for all user sessions.

        Subscribes to Redis pub/sub for user-level events and
        aggregates events from all active sessions.
        """
        # Track active session subscriptions
        active_sessions: set[str] = set()
        last_heartbeat = datetime.now(timezone.utc)

        # Track session statuses to detect changes
        session_statuses: dict[str, str] = {}

        try:
            while True:
                # Get ALL recent sessions for this user (not just running/queued)
                # This allows us to detect status changes (running -> complete/failed)
                async with get_db_session() as session_db:
                    result = await session_db.execute(
                        select(Session).where(Session.user_id == user_id)
                        .order_by(Session.updated_at.desc())
                        .limit(50)  # Limit to most recent sessions
                    )
                    sessions = result.scalars().all()
                    current_statuses = {s.id: s.status for s in sessions}

                # Check if any session status changed
                changed_sessions = []
                for session in sessions:
                    old_status = session_statuses.get(session.id)
                    if old_status is not None and old_status != session.status:
                        # Status changed - send specific event
                        changed_sessions.append({
                            "id": session.id,
                            "old_status": old_status,
                            "new_status": session.status,
                            "queue_position": session.queue_position,
                        })

                # Send status change events for any changed sessions
                for change in changed_sessions:
                    event = {
                        "type": "session_status_change",
                        "data": change,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    yield f"data: {json.dumps(event, default=str)}\n\n"

                # Check if the set of running/queued sessions changed (for active_sessions tracking)
                current_active_ids = {s.id for s in sessions if s.status in ("running", "queued")}
                if current_active_ids != active_sessions:
                    # Build session list with all statuses (for initial sync and badge handling)
                    session_list = [
                        {
                            "id": s.id,
                            "status": s.status,
                            "queue_position": s.queue_position,
                            "is_auto_resume": s.is_auto_resume,
                        }
                        for s in sessions
                    ]
                    event = {
                        "type": "session_list_update",
                        "data": {"sessions": session_list},
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    payload = json.dumps(event, default=str)
                    yield f"data: {payload}\n\n"
                    active_sessions = current_active_ids

                # Update tracked statuses
                session_statuses = current_statuses

                # Send heartbeat every 30 seconds
                now = datetime.now(timezone.utc)
                if (now - last_heartbeat).total_seconds() >= 30:
                    heartbeat = {
                        "type": "heartbeat",
                        "timestamp": now.isoformat(),
                    }
                    yield f"data: {json.dumps(heartbeat)}\n\n"
                    last_heartbeat = now

                # Poll for updates (could be replaced with Redis pub/sub for better performance)
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            logger.debug(f"User events stream cancelled for user {user_id}")
            raise

        except Exception as e:
            logger.exception(f"User events stream error for user {user_id}: {e}")
            error_event = {
                "type": "error",
                "data": {"message": f"Stream error: {str(e)}"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    # Helper to get fresh DB session in generator
    from ...db.database import AsyncSessionLocal
    async def get_db_session():
        return AsyncSessionLocal()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )

