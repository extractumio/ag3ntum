"""
Session management endpoints for Ag3ntum API.

Provides endpoints for:
- POST /sessions/run - Unified endpoint to create session and start task
- POST /sessions - Create session without starting
- GET /sessions - List sessions
- GET /sessions/{id} - Get session details
- POST /sessions/{id}/task - Start task on existing session
- POST /sessions/{id}/cancel - Cancel running task
- GET /sessions/{id}/result - Get task result
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

logger = logging.getLogger(__name__)
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import USERS_DIR, get_config_loader
from ...db.database import get_db
from ...db.models import User
from ...services.agent_runner import agent_runner, TaskParams
from ...services import event_service
from ...services.auth_service import auth_service
from ...services.session_service import session_service
from ..deps import get_current_user_id
from ..models import (
    AgentConfigOverrides,
    CancelResponse,
    CreateSessionRequest,
    ResultMetrics,
    ResultResponse,
    RunTaskRequest,
    SessionListResponse,
    SessionResponse,
    StartTaskRequest,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
    TaskStartedResponse,
    TokenUsageResponse,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Default values for large input handling (overridden by agent.yaml)
_LARGE_INPUT_DEFAULTS = {
    "threshold_bytes": 200 * 1024,  # 200KB
    "filename": "huge_user_input.txt",
    "message_template": "Run the user request from the file ./{filename} ({size})",
}


def _get_large_input_config() -> dict:
    """Get large input configuration from agent.yaml with defaults."""
    loader = get_config_loader()
    return loader.get_section("large_input", _LARGE_INPUT_DEFAULTS)


def process_large_user_input(task: str, workspace_dir: Path) -> str:
    """
    Process user input and store to file if it exceeds the configured size threshold.

    If the task content exceeds the threshold configured in agent.yaml (large_input.threshold_bytes),
    saves it to a file in the workspace and returns a transformed task instructing the agent
    to read from that file.

    Each large input gets a unique filename with timestamp to prevent collisions when
    multiple large inputs are submitted to the same session.

    Args:
        task: The original user task/input text.
        workspace_dir: Path to the session's workspace directory.

    Returns:
        Either the original task (if small) or a transformed task pointing to the file.
    """
    config = _get_large_input_config()
    threshold_bytes = config.get("threshold_bytes", _LARGE_INPUT_DEFAULTS["threshold_bytes"])
    base_filename = config.get("filename", _LARGE_INPUT_DEFAULTS["filename"])
    message_template = config.get("message_template", _LARGE_INPUT_DEFAULTS["message_template"])

    task_bytes = task.encode('utf-8')
    task_size = len(task_bytes)

    if task_size <= threshold_bytes:
        return task

    # Ensure workspace directory exists
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename with timestamp to avoid collisions
    # Format: huge_user_input_20260115_123456.txt
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name_parts = base_filename.rsplit('.', 1)
    if len(name_parts) == 2:
        filename = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
    else:
        filename = f"{base_filename}_{timestamp}"

    # Write the large input to file
    input_file_path = workspace_dir / filename
    input_file_path.write_text(task, encoding='utf-8')

    # Format size for display
    if task_size >= 1024 * 1024:
        size_str = f"{task_size / (1024 * 1024):.1f}MB"
    else:
        size_str = f"{task_size / 1024:.0f}KB"

    logger.info(
        f"Large user input ({size_str}) stored to {input_file_path}"
    )

    # Return transformed task using configured template
    return message_template.format(filename=filename, size=size_str)


def session_to_response(session, resumable: bool | None = None) -> SessionResponse:
    """
    Convert a database Session to SessionResponse.

    Args:
        session: Database session object.
        resumable: Optional override for resumability. If None, determined from session_info.
    """
    # Determine resumability if not explicitly provided
    if resumable is None:
        if session.status == "waiting_for_input":
            # Sessions waiting for user input are always resumable
            resumable = True
        elif session.status == "cancelled":
            session_info = session_service.get_session_info(session.id)
            resumable = bool(session_info.get("resume_id"))

    return SessionResponse(
        id=session.id,
        status=session.status,
        task=session.task,
        model=session.model,
        created_at=session.created_at,
        updated_at=session.updated_at,
        completed_at=session.completed_at,
        num_turns=session.num_turns,
        duration_ms=session.duration_ms,
        total_cost_usd=session.total_cost_usd,
        cancel_requested=session.cancel_requested,
        resumable=resumable,
    )

async def record_user_message_event(session_id: str, text: str, processed_text: str | None = None) -> None:
    """
    Record a user message event.

    Args:
        session_id: The session ID.
        text: The original user message (for display, may be truncated).
        processed_text: The processed message sent to LLM (if different from text).
                       When large input is stored to file, this contains the redirect message.
    """
    last_sequence = await event_service.get_last_sequence(session_id)

    # Get large input config for threshold
    config = _get_large_input_config()
    threshold_bytes = config.get("threshold_bytes", _LARGE_INPUT_DEFAULTS["threshold_bytes"])

    # Check if text exceeds threshold
    text_bytes = text.encode('utf-8')
    text_size = len(text_bytes)

    # Build event data
    event_data: dict[str, Any] = {"text": text, "session_id": session_id}

    # If text is large, add truncation info for frontend
    if text_size > threshold_bytes:
        # Format size for display
        if text_size >= 1024 * 1024:
            size_str = f"{text_size / (1024 * 1024):.1f}MB"
        else:
            size_str = f"{text_size / 1024:.0f}KB"

        event_data["is_large"] = True
        event_data["size_display"] = size_str
        event_data["size_bytes"] = text_size

        # If processed_text is provided (file redirect), include it
        if processed_text and processed_text != text:
            event_data["processed_text"] = processed_text

    event = {
        "type": "user_message",
        "data": event_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sequence": last_sequence + 1,
        "session_id": session_id,
    }
    await event_service.record_event(event)
    await agent_runner.publish_event(session_id, event)


async def build_resume_context(session_id: str, is_waiting_for_input: bool = False) -> tuple[str | None, bool]:
    """
    Build resume context for a cancelled or waiting_for_input session.

    Analyzes previous events to determine:
    - Whether session is resumable (has agent_start event)
    - What todo state was at cancellation
    - User answers to pending questions (human-in-the-loop)

    Args:
        session_id: The session ID.
        is_waiting_for_input: True if session was waiting for user input.

    Returns:
        Tuple of (context_string, is_resumable).
        context_string is None if session is not resumable.
    """
    events = await event_service.list_events(session_id)
    if not events:
        return None, False

    # Check for agent_start event (indicates Claude session was established)
    has_agent_start = any(e.get("type") == "agent_start" for e in events)
    if not has_agent_start:
        return None, False

    # Find latest todo_update events to extract todo state
    todos: list[dict] = []
    for event in reversed(events):
        if event.get("type") == "todo_update":
            data = event.get("data", {})
            if "todos" in data:
                todos = data["todos"]
                break

    # Build context wrapped in resume-context tags so it's not shown in UI
    context_lines = ["<resume-context>"]

    if is_waiting_for_input:
        context_lines.append("Previous execution paused waiting for user input.")
    else:
        context_lines.append("Previous execution was cancelled by user.")

    # Get answered questions from events (human-in-the-loop)
    try:
        from tools.ag3ntum.ag3ntum_ask.tool import get_answered_questions_from_events
        answered_questions = await get_answered_questions_from_events(session_id)

        if answered_questions:
            context_lines.append("")
            context_lines.append("User answered the following questions:")
            for aq in answered_questions:
                questions = aq.get("questions", [])
                answer = aq.get("answer", "")
                for q in questions:
                    question_text = q.get("question", "Unknown question")
                    context_lines.append(f"  Q: {question_text}")
                context_lines.append(f"  A: {answer}")
                context_lines.append("")
    except Exception as e:
        logger.warning(f"Failed to get answered questions from events: {e}")

    if todos:
        context_lines.append("")
        context_lines.append("Todo state at pause:")
        for todo in todos:
            status_icon = {
                "completed": "✓",
                "in_progress": "→",
                "pending": "○",
                "cancelled": "✗",
            }.get(todo.get("status", "pending"), "○")
            content = todo.get("content", "Unknown")
            context_lines.append(f"  {status_icon} {content} [{todo.get('status', 'pending')}]")

        # Identify interrupted task
        in_progress = [t for t in todos if t.get("status") == "in_progress"]
        if in_progress:
            context_lines.append("")
            context_lines.append("Note: Task(s) marked in_progress were interrupted and may be incomplete.")

    context_lines.append("</resume-context>")
    context_lines.append("")

    return "\n".join(context_lines), True


def build_task_params(
    session_id: str,
    user_id: str,
    task: str,
    additional_dirs: list[str],
    resume_session_id: str | None,
    fork_session: bool,
    config: AgentConfigOverrides,
    sessions_dir: Path,
) -> TaskParams:
    """
    Build TaskParams from request data.

    Converts API request fields to TaskParams dataclass.
    
    Args:
        sessions_dir: User-specific sessions base directory (e.g., /users/username/sessions)
    """
    return TaskParams(
        task=task,
        session_id=session_id,
        user_id=user_id,
        sessions_dir=str(sessions_dir),
        resume_session_id=resume_session_id,
        fork_session=fork_session,
        additional_dirs=additional_dirs,
        model=config.model,
        max_turns=config.max_turns,
        timeout_seconds=config.timeout_seconds,
        enable_skills=config.enable_skills,
        enable_file_checkpointing=config.enable_file_checkpointing,
        permission_mode=config.permission_mode,
        role=config.role,
        max_buffer_size=config.max_buffer_size,
        output_format=config.output_format,
        include_partial_messages=config.include_partial_messages,
        profile=config.profile,
    )


# =============================================================================
# POST /sessions/run - Unified endpoint (recommended)
# =============================================================================

@router.post("/run", response_model=TaskStartedResponse, status_code=status.HTTP_201_CREATED)
async def run_task(
    request: RunTaskRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TaskStartedResponse:
    """
    Create a new session and start task execution immediately.

    This is the primary endpoint for running agent tasks. It:
    1. Creates a new session (or resumes an existing one if resume_session_id is provided)
    2. Starts task execution in the background
    3. Returns immediately with session ID

    Use GET /sessions/{id} to check status and GET /sessions/{id}/result for output.

    Matches CLI capabilities:
        python agent.py --task "..." --model "..." --max-turns 50
        python agent.py --resume SESSION_ID --task "Continue..."
    """
    # Get user and determine sessions directory ONCE
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {user_id}",
        )

    # Determine user's sessions base directory (created once here)
    user_sessions_dir = USERS_DIR / user.username / "sessions"
    user_sessions_dir.mkdir(parents=True, exist_ok=True)
    
    # Create session in database (SessionService will use this base directory)
    session = await session_service.create_session(
        db=db,
        user_id=user_id,
        task=request.task,
        model=request.config.model,
        sessions_dir=user_sessions_dir,
    )

    # Process large user input: if task exceeds 200KB, store to file
    workspace_dir = user_sessions_dir / session.id / "workspace"
    task_for_agent = process_large_user_input(request.task, workspace_dir)

    # Build task parameters (pass same sessions_dir through the chain)
    params = build_task_params(
        session_id=session.id,
        user_id=user_id,
        task=task_for_agent,
        additional_dirs=request.additional_dirs,
        resume_session_id=request.resume_session_id,
        fork_session=request.fork_session,
        config=request.config,
        sessions_dir=user_sessions_dir,
    )

    # Record user message event with processed text for LLM reference
    await record_user_message_event(
        session.id,
        request.task,
        processed_text=task_for_agent if task_for_agent != request.task else None
    )

    # Start the agent in background
    await agent_runner.start_task(params)

    # Update session to running status
    await session_service.update_session(db=db, session=session, status="running")

    return TaskStartedResponse(
        session_id=session.id,
        status="running",
        message="Task execution started",
        resumed_from=request.resume_session_id,
    )


# =============================================================================
# POST /sessions - Create session without starting
# =============================================================================

@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: CreateSessionRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """
    Create a new session without starting execution.

    Use POST /sessions/{id}/task to start the task later.
    For most use cases, prefer POST /sessions/run which creates and starts in one call.
    """
    # Get user and determine sessions directory
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {user_id}",
        )

    # Determine user's sessions base directory
    user_sessions_dir = USERS_DIR / user.username / "sessions"
    user_sessions_dir.mkdir(parents=True, exist_ok=True)

    session = await session_service.create_session(
        db=db,
        user_id=user_id,
        task=request.task,
        sessions_dir=user_sessions_dir,
        model=request.model,
    )

    return session_to_response(session)


# =============================================================================
# GET /sessions - List sessions
# =============================================================================

@router.get("", response_model=SessionListResponse)
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    """
    List sessions for the current user.

    Returns a paginated list of sessions ordered by creation date (newest first).
    """
    sessions, total = await session_service.list_sessions(
        db=db,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )

    for session in sessions:
        if session.status == "running" and not agent_runner.is_running(session.id):
            terminal_status = await event_service.get_latest_terminal_status(session.id)
            if terminal_status:
                session.status = terminal_status
                session.completed_at = datetime.now(timezone.utc)
    await db.commit()

    return SessionListResponse(
        sessions=[session_to_response(s) for s in sessions],
        total=total,
    )


# =============================================================================
# GET /sessions/{id} - Get session details
# =============================================================================

@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """
    Get session details.

    Returns the current state of a session including status and metrics.
    """
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    return session_to_response(session)


# =============================================================================
# POST /sessions/{id}/task - Start task on existing session
# =============================================================================

@router.post("/{session_id}/task", response_model=TaskStartedResponse)
async def start_task(
    session_id: str,
    request: StartTaskRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TaskStartedResponse:
    """
    Start or continue task execution on an existing session.

    Starts the agent in the background. Use GET /sessions/{id} to check status
    and GET /sessions/{id}/events (SSE) to stream real-time events.

    If no task is provided, uses the session's stored task.
    Supports resuming from a different session via resume_session_id.
    """
    # Get user and determine sessions directory
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {user_id}",
        )
    
    user_sessions_dir = USERS_DIR / user.username / "sessions"
    
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    if agent_runner.is_running(session_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task is already running for this session",
        )

    # Use request.task if provided, otherwise fall back to session.task
    original_task = request.task or session.task
    if not original_task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No task specified. Provide task in request or create session with task.",
        )

    # Process large user input: if task exceeds 200KB, store to file
    workspace_dir = user_sessions_dir / session_id / "workspace"
    processed_task = process_large_user_input(original_task, workspace_dir)
    task_to_run = processed_task

    # Determine resume session:
    # - If request.resume_session_id is set, resume from that session
    # - Otherwise, if this session was already run before (has turns), resume it
    resume_from = request.resume_session_id
    if not resume_from:
        session_info = session_service.get_session_info(session_id)
        if session.num_turns > 0 or session_info.get("resume_id"):
            # This session has history, resume from itself
            resume_from = session_id

    # Check if resuming a cancelled or waiting_for_input session and build resume context
    is_resumable = True
    if session.status in ("cancelled", "waiting_for_input") and resume_from:
        is_waiting_for_input = session.status == "waiting_for_input"
        resume_context, is_resumable = await build_resume_context(
            session_id,
            is_waiting_for_input=is_waiting_for_input
        )

        if not is_resumable:
            # Session was cancelled before agent_start - not resumable
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session cannot be resumed. It was cancelled before the agent could start. "
                       "Please create a new session with the same task.",
            )

        if resume_context:
            # Prepend resume context to help agent understand the state
            task_to_run = f"{resume_context}\n{task_to_run}"

    # Build task parameters (pass sessions_dir through)
    params = build_task_params(
        session_id=session_id,
        user_id=user_id,
        task=task_to_run,
        additional_dirs=request.additional_dirs,
        resume_session_id=resume_from,
        fork_session=request.fork_session,
        config=request.config,
        sessions_dir=user_sessions_dir,
    )

    # Record user message event with processed text for LLM reference
    await record_user_message_event(
        session_id,
        original_task,
        processed_text=processed_task if processed_task != original_task else None
    )

    # Start the agent in background
    await agent_runner.start_task(params)

    # Update session to running status
    session = await session_service.update_session(
        db=db,
        session=session,
        status="running",
    )

    return TaskStartedResponse(
        session_id=session_id,
        status="running",
        message="Task execution started",
        resumed_from=resume_from if resume_from != session_id else None,
    )


# =============================================================================
# GET /sessions/{id}/events - SSE event stream
# =============================================================================

@router.get("/{session_id}/events")
async def stream_events(
    session_id: str,
    token: str | None = Query(default=None),
    after: int | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    Stream real-time execution events for a session (SSE).

    Note: token is passed via query parameter to support EventSource.
    """
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

    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    queue = await agent_runner.subscribe(session_id)

    async def event_generator():
        """
        Generate SSE events for a session.

        Handles:
        - Normal event streaming from the agent
        - Heartbeats during idle periods
        - Error events if the agent fails before sending events
        - Graceful completion when agent finishes

        Event Delivery Guarantee (Redis-first architecture):
        Events are published to Redis first (~1ms), then persisted to SQLite (~5-50ms).
        To handle the race condition where SSE subscribes after Redis publish but
        before SQLite persist, we use a 10-event overlap buffer:
        1. Subscribe to Redis Pub/Sub first (catches future events)
        2. Replay from DB starting at sequence-10 (overlap buffer)
        3. Deduplicate by sequence number (prevents duplicates from overlap)
        This ensures no events are lost while maintaining low latency.
        """
        try:
            last_sequence = after or 0
            if last_event_id:
                try:
                    last_sequence = int(last_event_id)
                except ValueError:
                    last_sequence = after or 0

            # Store original for deduplication
            original_last_sequence = last_sequence

            # Add 10-event overlap buffer to catch Redis-published but not-yet-persisted events
            replay_start_sequence = max(0, last_sequence - 10)

            # Replay missed events from persistence with overlap buffer
            replay_events = await event_service.list_events(
                session_id=session_id,
                after_sequence=replay_start_sequence,
                limit=2000,
            )

            # Deduplicate events in overlap window
            seen_sequences = set()
            for event in replay_events:
                seq = event.get('sequence')

                # Skip if already sent or duplicate
                if seq in seen_sequences or seq <= original_last_sequence:
                    continue

                seen_sequences.add(seq)
                payload = json.dumps(event, default=str)
                yield f"id: {seq}\n"
                yield f"data: {payload}\n\n"
                last_sequence = seq

                if event.get("type") in ("agent_complete", "error", "cancelled"):
                    return

            # Stream live events from Redis
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"

                    # Check if task finished while waiting
                    if not agent_runner.is_running(session_id) and queue.empty():
                        break
                    continue

                seq = event.get("sequence", 0)

                # Deduplicate (might overlap with replay)
                if seq in seen_sequences or seq <= last_sequence:
                    continue

                seen_sequences.add(seq)
                payload = json.dumps(event, default=str)

                yield f"id: {seq}\n"
                yield f"data: {payload}\n\n"

                event_type = event.get("type")
                if event_type in ("agent_complete", "error", "cancelled"):
                    break
                last_sequence = seq

        except Exception as e:
            # Send error event if SSE streaming fails
            logger.exception(f"SSE streaming error for session {session_id}")
            error_event = {
                "type": "error",
                "data": {
                    "message": f"Streaming error: {str(e)}",
                    "error_type": "streaming_error",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sequence": 9998,
            }
            payload = json.dumps(error_event, default=str)
            yield f"id: 9998\n"
            yield f"data: {payload}\n\n"

        finally:
            await agent_runner.unsubscribe(session_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


@router.get("/{session_id}/events/history")
async def list_events(
    session_id: str,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    after: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """
    List persisted events for a session.

    This endpoint powers polling fallback and session replay.
    """
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]

    user_id = await auth_service.validate_token(token, db) if token else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    return await event_service.list_events(session_id=session_id, after_sequence=after)


# =============================================================================
# POST /sessions/{id}/cancel - Cancel running task
# =============================================================================

@router.post("/{session_id}/cancel", response_model=CancelResponse)
async def cancel_task(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> CancelResponse:
    """
    Cancel a running task.

    Requests cancellation of the running agent. The agent will stop
    at the next opportunity (typically after the current tool completes).
    """
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    if not agent_runner.is_running(session_id):
        # Already stopped, just update status
        if session.status == "running":
            session = await session_service.update_session(
                db=db,
                session=session,
                status="cancelled",
                completed_at=datetime.now(timezone.utc),
            )

        return CancelResponse(
            session_id=session_id,
            status=session.status,
            message="Task is not running",
        )

    # Cancel the running task
    cancelled = await agent_runner.cancel_task(session_id)

    if cancelled:
        session = await session_service.update_session(
            db=db,
            session=session,
            status="cancelled",
            cancel_requested=True,
            completed_at=datetime.now(timezone.utc),
        )

    return CancelResponse(
        session_id=session_id,
        status="cancelled" if cancelled else session.status,
        message="Cancellation requested" if cancelled else "Failed to cancel",
    )


# =============================================================================
# GET /sessions/{id}/result - Get task result
# =============================================================================

@router.get("/{session_id}/result", response_model=ResultResponse)
async def get_result(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> ResultResponse:
    """
    Get task result.

    Returns a synthesized summary from persisted events and execution metrics.
    Includes token usage from the file-based session info.
    """
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    events = await event_service.list_events(session_id=session_id, after_sequence=None, limit=5000)

    message_buffer: list[str] = []
    final_message: str = ""
    result_files: set[str] = set()
    status_value = "FAILED"
    error_message = ""
    structured_status: Optional[str] = None

    for event in events:
        if event.get("type") == "message":
            data = event.get("data", {})
            text = str(data.get("text", ""))
            if data.get("is_partial"):
                message_buffer.append(text)
            else:
                final_message = "".join(message_buffer) + text
                message_buffer = []
            if structured_status is None:
                structured_status = data.get("structured_status")
                if structured_status:
                    structured_status = str(structured_status).upper()
            if not error_message:
                structured_error = data.get("structured_error")
                if structured_error:
                    error_message = str(structured_error)

        if event.get("type") == "error":
            error_message = str(event.get("data", {}).get("message", ""))

        if event.get("type") == "agent_complete":
            status_value = str(event.get("data", {}).get("status", status_value))

        if event.get("type") in ("tool_start", "tool_complete"):
            data = event.get("data", {})
            tool_input = data.get("tool_input", {})
            if isinstance(tool_input, dict):
                for key in ("file_path", "path", "target_path", "dest_path"):
                    path_value = tool_input.get(key)
                    if isinstance(path_value, str) and not path_value.startswith(("/", "~")):
                        result_files.add(path_value)

    # Get session info for token usage data
    session_info = session_service.get_session_info(session_id)
    cumulative_usage = session_info.get("cumulative_usage")

    # Build token usage from cumulative stats
    usage = None
    if cumulative_usage:
        usage = TokenUsageResponse(
            input_tokens=cumulative_usage.get("input_tokens", 0),
            output_tokens=cumulative_usage.get("output_tokens", 0),
            cache_creation_input_tokens=cumulative_usage.get(
                "cache_creation_input_tokens", 0
            ),
            cache_read_input_tokens=cumulative_usage.get(
                "cache_read_input_tokens", 0
            ),
        )

    # Build metrics from session data + file-based info
    metrics = ResultMetrics(
        duration_ms=session.duration_ms,
        num_turns=session.num_turns or 0,
        total_cost_usd=session.total_cost_usd,
        model=session.model or session_info.get("model"),
        usage=usage,
    )

    return ResultResponse(
        session_id=session_id,
        status=structured_status or status_value,
        error=error_message,
        comments="",
        output=final_message,
        result_files=sorted(result_files),
        metrics=metrics,
    )


# =============================================================================
# GET /sessions/{id}/files - Download/view a session result file
# =============================================================================

@router.get("/{session_id}/files")
async def get_session_file(
    session_id: str,
    path: str = Query(..., description="Relative path to a result file"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """
    Fetch a file from a session workspace.
    """
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    try:
        file_path = session_service.get_session_file(session_id, path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path",
        )

    return FileResponse(file_path, filename=file_path.name)


# =============================================================================
# POST /sessions/{id}/answer - Submit answer to AskUserQuestion
# =============================================================================

@router.post("/{session_id}/answer", response_model=SubmitAnswerResponse)
async def submit_answer(
    session_id: str,
    request: SubmitAnswerRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SubmitAnswerResponse:
    """
    Submit an answer to a pending AskUserQuestion tool call.

    This endpoint is called by the frontend when the user selects options
    and clicks submit on an AskUserQuestion interactive UI.

    The answer is stored as a question_answered event in the session's
    event stream. After submitting, the session can be resumed to continue
    agent execution with the user's answer in context.
    """
    # Verify session exists and belongs to user
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Use the event-based submit function
    from tools.ag3ntum.ag3ntum_ask.tool import submit_answer_as_event

    success = await submit_answer_as_event(
        session_id=session_id,
        question_id=request.question_id,
        answer=request.answer,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No pending question found with ID: {request.question_id}",
        )

    logger.info(
        f"Answer submitted for session {session_id}, question {request.question_id}"
    )

    # Session can be resumed now that the answer is submitted
    return SubmitAnswerResponse(
        success=True,
        message="Answer submitted successfully. You can now resume the session.",
        can_resume=True,
    )


# =============================================================================
# GET /sessions/{id}/pending-question - Get pending question for session
# =============================================================================

@router.get("/{session_id}/pending-question")
async def get_pending_question(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get the current pending question for a session.

    Returns the pending question if one exists and is waiting for an answer.
    Reads from the session's event stream (question_pending events).
    """
    # Verify session exists and belongs to user
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Use the event-based query function
    from tools.ag3ntum.ag3ntum_ask.tool import get_pending_question_from_events

    pending = await get_pending_question_from_events(session_id)

    if not pending:
        return {"has_pending_question": False}

    return {
        "has_pending_question": True,
        "question_id": pending["question_id"],
        "questions": pending["questions"],
        "created_at": pending.get("timestamp"),
    }
