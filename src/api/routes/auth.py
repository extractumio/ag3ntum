"""
Authentication endpoints for Ag3ntum API.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import Session
from ...services.auth_service import auth_service, UserEnvironmentError
from ...services.agent_runner import agent_runner
from ...services.connection_token import create_connection_token, validate_connection_token
from ...services.rate_limiter import check_rate_limit, reset_rate_limit
from ..deps import get_current_user_id, validate_sse_token
from ..models import TokenResponse, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    email: str = Body(...),
    password: str = Body(...),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Login with email and password.

    Returns a JWT token valid for 7 days.

    Rate-limited: 5 failed attempts per account per minute,
    20 failed attempts per IP per minute.

    Returns 403 Forbidden if user account is misconfigured (missing home/venv).
    Returns 429 Too Many Requests when rate limit is exceeded.
    """
    # Rate limit checks
    client_ip = request.client.host if request.client else "unknown"
    account_key = f"rate:auth:account:{email}"
    ip_key = f"rate:auth:ip:{client_ip}"

    if not await check_rate_limit(account_key, max_attempts=5, window_seconds=60):
        logger.warning("Auth rate limit exceeded for account: %s", email)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
        )

    if not await check_rate_limit(ip_key, max_attempts=20, window_seconds=60):
        logger.warning("Auth rate limit exceeded for IP: %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again later.",
        )

    try:
        user, token, expires_in = await auth_service.authenticate(db, email, password)
        # Successful login — reset the per-account counter
        await reset_rate_limit(account_key)
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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Logout and revoke all active tokens for the current user.

    Increments the user's token_version so all previously issued
    JWT tokens become invalid.
    """
    await auth_service.revoke_tokens(db, user_id)
    return {"status": "logged_out"}


@router.post("/change-password")
async def change_password(
    current_password: str = Body(...),
    new_password: str = Body(...),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Change password and revoke all existing tokens.

    Returns a new JWT token valid for 7 days.
    All previously issued tokens are invalidated.
    """
    try:
        token, expires_in = await auth_service.change_password(
            db, user_id, current_password, new_password
        )
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            user_id=user_id,
            expires_in=expires_in,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/connection-token")
async def issue_connection_token(
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """
    Exchange a JWT for a short-lived, single-use connection token.

    Used by SSE endpoints (EventSource) which cannot set custom headers.
    The connection token is valid for 30 seconds and can only be used once.

    Returns:
        {"connection_token": "<token>"}
    """
    try:
        token = await create_connection_token(user_id)
        return {"connection_token": token}
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not create connection token. Try again later.",
        )


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
        reseller_id=getattr(user, "reseller_id", None),
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
    Accepts either a short-lived connection token or a JWT.
    """
    user_id = await validate_sse_token(token, authorization, db)

    # Import here to avoid circular imports
    from ...db.database import AsyncSessionLocal

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
        consecutive_errors = 0

        while True:
            try:
                # Get ALL recent sessions for this user (not just running/queued)
                # This allows us to detect status changes (running -> complete/failed)
                async with AsyncSessionLocal() as session_db:
                    result = await session_db.execute(
                        select(Session).where(Session.user_id == user_id)
                        .order_by(Session.updated_at.desc())
                        .limit(50)  # Limit to most recent sessions
                    )
                    sessions = result.scalars().all()
                    current_statuses = {s.id: s.status for s in sessions}

                # Reset error counter on success
                consecutive_errors = 0

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
                consecutive_errors += 1
                logger.warning(f"User events stream error for user {user_id} (attempt {consecutive_errors}): {e}")

                # After too many consecutive errors, terminate the stream
                if consecutive_errors >= 5:
                    logger.error(f"User events stream for {user_id} terminated after {consecutive_errors} consecutive errors")
                    error_event = {
                        "type": "error",
                        "data": {"message": "Stream terminated due to repeated errors"},
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    yield f"data: {json.dumps(error_event)}\n\n"
                    return

                # Wait before retrying (with backoff)
                await asyncio.sleep(min(2 ** consecutive_errors, 30))

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
