"""
Agent runner service for Ag3ntum API.

Manages background agent execution tasks with cancellation support.
Uses the unified task_runner for execution (shared with CLI).
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from sqlalchemy import select

from ..config import CONFIG_DIR
from ..core.schemas import SessionContext, TaskExecutionParams
from ..core.sessions import SessionManager
from ..core.task_runner import execute_agent_task
from ..core.tracer import BackendConsoleTracer, EventingTracer
from ..db.database import AsyncSessionLocal
from ..db.models import Session, Token, User
from ..services import event_service
from ..services.encryption_service import encryption_service
from ..services.redis_event_hub import RedisEventHub, EventSinkQueue

logger = logging.getLogger(__name__)


@dataclass
class TaskParams:
    """
    Parameters for agent task execution.

    Matches CLI arguments and agent.yaml configuration options.
    All fields are optional - if not provided, values from agent.yaml are used.
    """
    # Task
    task: str

    # Session
    session_id: str
    user_id: str  # User ID for isolation
    sessions_dir: str  # User-specific sessions base directory (e.g., /users/username/sessions)
    resume_session_id: Optional[str] = None
    fork_session: bool = False

    # Additional directories (CLI: --add-dir)
    additional_dirs: Optional[list[str]] = None

    # Agent config overrides (CLI: --model, --max-turns, --timeout, etc.)
    model: Optional[str] = None
    max_turns: Optional[int] = None
    timeout_seconds: Optional[int] = None
    enable_skills: Optional[bool] = None
    enable_file_checkpointing: Optional[bool] = None
    permission_mode: Optional[str] = None
    role: Optional[str] = None
    max_buffer_size: Optional[int] = None
    output_format: Optional[str] = None
    include_partial_messages: Optional[bool] = None

    # Extended thinking configuration
    thinking_tokens: Optional[int] = None

    # Permission profile (CLI: --profile)
    profile: Optional[str] = None

    # Dynamic mounts for this session (API only, not available via CLI)
    dynamic_mounts: Optional[list] = None  # List of DynamicMountRequest objects

    def __post_init__(self):
        if self.additional_dirs is None:
            self.additional_dirs = []
        if self.dynamic_mounts is None:
            self.dynamic_mounts = []


class AgentRunner:
    """
    Manages background agent execution.

    Provides methods to start, cancel, and track agent tasks.
    Each task runs in a background asyncio Task.
    """

    def __init__(self) -> None:
        """Initialize the agent runner.

        Requires Redis for SSE event streaming. Will fail with a clear error
        if Redis is unavailable.
        """
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._results: dict[str, dict[str, Any]] = {}
        self._completion_callbacks: list[Callable[[str, str], None]] = []

        # Load Redis URL from config (required)
        api_config_path = CONFIG_DIR / "api.yaml"
        try:
            with open(api_config_path) as f:
                api_config = yaml.safe_load(f)
        except FileNotFoundError:
            raise RuntimeError(
                f"Configuration file not found: {api_config_path}\n"
                f"Please ensure config/api.yaml exists with Redis configuration."
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to load configuration: {api_config_path}\n"
                f"Error: {e}"
            ) from e

        redis_config = api_config.get("redis", {})
        redis_url = redis_config.get("url")
        if not redis_url:
            raise RuntimeError(
                f"Redis URL not configured in {api_config_path}\n"
                f"Please add 'redis.url' to config/api.yaml, e.g.:\n"
                f"  redis:\n"
                f"    url: \"redis://redis:6379/0\""
            )

        # Initialize RedisEventHub with Redis Streams (required)
        self._event_hub = RedisEventHub(
            redis_url=redis_url,
            stream_maxlen=10000,  # Keep last 10k events per session
            block_ms=30000,  # 30 second XREAD block timeout
            stream_ttl_seconds=86400,  # 24 hour TTL for streams
        )
        self._redis_url = redis_url
        self._redis_verified = False

    async def _ensure_redis_connection(self) -> None:
        """Verify Redis connection on first use (lazy initialization)."""
        if self._redis_verified:
            return

        import redis.asyncio as redis_client
        try:
            pool = await self._event_hub._ensure_pool()
            async with redis_client.Redis(connection_pool=pool) as conn:
                await conn.ping()
            logger.info("Redis connection verified - SSE streaming ready")
            self._redis_verified = True
        except Exception as e:
            raise RuntimeError(
                f"Redis is required for SSE event streaming but connection failed: {e}\n"
                f"Please ensure Redis is running and accessible at: {self._redis_url}\n"
                f"Check config/api.yaml for Redis configuration."
            ) from e

    async def _update_session_status(
        self,
        session_id: str,
        status: str,
        model: Optional[str] = None,
        num_turns: Optional[int] = None,
        duration_ms: Optional[int] = None,
        total_cost_usd: Optional[float] = None,
        usage: Optional[dict] = None,
    ) -> None:
        """
        Update session status in database using a fresh session.

        This method creates its own database session to avoid issues
        with closed sessions from request handlers.

        Also updates cumulative stats when completion metrics are provided.
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Session).where(Session.id == session_id)
            )
            session = result.scalar_one_or_none()

            if session:
                session.status = status
                session.updated_at = datetime.now(timezone.utc)

                if model is not None:
                    session.model = model
                if num_turns is not None:
                    session.num_turns = num_turns
                    # Also accumulate to cumulative
                    session.cumulative_turns += num_turns
                if duration_ms is not None:
                    session.duration_ms = duration_ms
                    session.cumulative_duration_ms += duration_ms
                if total_cost_usd is not None:
                    session.total_cost_usd = total_cost_usd
                    session.cumulative_cost_usd += total_cost_usd
                if status in ("completed", "complete", "partial", "failed", "cancelled"):
                    session.completed_at = datetime.now(timezone.utc)

                # Update cumulative token stats if usage provided
                if usage:
                    session.cumulative_input_tokens += usage.get("input_tokens", 0)
                    session.cumulative_output_tokens += usage.get("output_tokens", 0)
                    session.cumulative_cache_creation_tokens += usage.get("cache_creation_input_tokens", 0)
                    session.cumulative_cache_read_tokens += usage.get("cache_read_input_tokens", 0)

                await db.commit()
                logger.debug(f"Updated session {session_id} status to {status}")

    async def _record_usage(
        self,
        session_id: str,
        user_id: str,
        model: str,
        metrics: Optional[Any] = None,
        usage: Optional[dict] = None,
    ) -> None:
        """Record session usage for billing/reporting (fire-and-forget).

        Called after session completion. Failures are logged but never
        propagated — session completion must not be affected.
        """
        from .usage_service import usage_service
        from .reseller_quota_service import reseller_quota_service

        async with AsyncSessionLocal() as db:
            # Look up user to get reseller_id
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            reseller_id = user.reseller_id if user else None

            input_tokens = usage.get("input_tokens", 0) if usage else 0
            output_tokens = usage.get("output_tokens", 0) if usage else 0
            cache_creation = usage.get("cache_creation_input_tokens", 0) if usage else 0
            cache_read = usage.get("cache_read_input_tokens", 0) if usage else 0
            cost_usd = metrics.total_cost_usd if metrics and metrics.total_cost_usd else 0.0
            duration_ms = metrics.duration_ms if metrics and metrics.duration_ms else 0
            num_turns = metrics.num_turns if metrics and metrics.num_turns else 0

            await usage_service.record_session_usage(
                db=db,
                session_id=session_id,
                user_id=user_id,
                reseller_id=reseller_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                num_turns=num_turns,
                cache_creation_tokens=cache_creation,
                cache_read_tokens=cache_read,
            )

            # Update reseller quota counters if applicable
            if reseller_id:
                await reseller_quota_service.record_cost(
                    db, reseller_id, cost_usd, input_tokens, output_tokens
                )

            # Fire webhook for session completion
            if reseller_id:
                from .webhook_service import webhook_service
                await webhook_service.fire_best_effort(db, reseller_id, "session.completed", {
                    "session_id": session_id,
                    "user_id": user_id,
                    "model": model,
                    "cost_usd": cost_usd,
                    "duration_ms": duration_ms,
                    "num_turns": num_turns,
                })

            logger.debug(f"Usage recorded for session {session_id} (cost=${cost_usd:.4f})")

    async def start_task(self, params: TaskParams) -> None:
        """
        Start agent execution in background.

        Args:
            params: TaskParams with all execution parameters.

        Raises:
            RuntimeError: If task is already running for this session.
        """
        session_id = params.session_id

        # Verify Redis connection on first use
        await self._ensure_redis_connection()

        if session_id in self._running_tasks:
            raise RuntimeError(f"Task already running for session: {session_id}")

        # Initialize cancel flag and event queue
        self._cancel_flags[session_id] = False

        # Start the background task
        task_coro = self._run_agent(params)
        self._running_tasks[session_id] = asyncio.create_task(task_coro)

        logger.info(f"Started background task for session: {session_id}")

    async def _run_agent(self, params: TaskParams) -> None:
        """
        Run the agent in background using the unified task runner.

        Uses execute_agent_task() for consistent behavior with CLI.

        Error Handling Strategy:
        - Creates tracer early to ensure error events can be sent to frontend
        - Catches exceptions at all levels and emits proper error events
        - Always emits a completion/error event so frontend knows session ended
        - Updates database status on completion or failure
        """
        session_id = params.session_id
        tracer: Optional[EventingTracer] = None

        event_queue = EventSinkQueue(self._event_hub, session_id)
        last_sequence = await event_service.get_last_sequence(session_id)

        def emit_event(event_type: str, data: dict[str, Any]) -> None:
            """Helper to emit an event even if tracer creation failed."""
            event = {
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sequence": last_sequence + 1,
                "session_id": session_id,
            }
            if tracer is not None:
                tracer.emit_event(event_type, data)
                return

            # Fallback when tracer is not available (e.g., tracer creation failed)
            # Use persist-then-publish pattern to prevent race conditions
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return

            async def persist_then_publish():
                """Persist event to DB, then publish to EventHub."""
                await event_service.record_event(event)
                await self._event_hub.publish(session_id, event)

            loop.create_task(persist_then_publish())

        def emit_error_event(message: str, error_type: str = "server_error") -> None:
            """Helper to emit error event even if tracer creation failed."""
            emit_event("error", {
                "message": message,
                "error_type": error_type,
                "session_id": session_id,
            })

        def emit_cancelled_event(
            message: str = "Task was cancelled",
            resumable: bool = False
        ) -> None:
            """Emit a cancelled event to properly signal task cancellation."""
            emit_event("cancelled", {
                "message": message,
                "session_id": session_id,
                "resumable": resumable,
            })

        try:
            # Create tracer early for error reporting
            base_tracer = BackendConsoleTracer(session_id=session_id)

            async def persist_event(event: dict[str, Any]) -> None:
                """Persist event to database. Returns when persistence is complete."""
                await event_service.record_event(event)

            tracer = EventingTracer(
                base_tracer,
                event_queue=event_queue,
                event_sink=persist_event,
                session_id=session_id,
                initial_sequence=last_sequence,
            )

            # Resolve user context and API key
            user: Optional[User] = None
            api_key: Optional[str] = None
            linux_uid: Optional[int] = None
            linux_gid: Optional[int] = None
            session_context: Optional[SessionContext] = None

            async with AsyncSessionLocal() as db:
                # Fetch user
                result = await db.execute(select(User).where(User.id == params.user_id))
                user = result.scalar_one_or_none()

                if not user or user.linux_uid is None:
                    emit_error_event(
                        f"User not found or Linux UID not set for user_id: {params.user_id}",
                        "configuration_error"
                    )
                    await self._update_session_status(session_id, "failed")
                    return

                linux_uid = user.linux_uid
                linux_gid = user.linux_uid  # Use UID as GID (common pattern)

                # Resolve Anthropic API key (user's encrypted token or system fallback)
                token_result = await db.execute(
                    select(Token).where(
                        Token.user_id == user.id,
                        Token.token_type == "anthropic_api_key"
                    ).order_by(Token.created_at.desc()).limit(1)
                )
                token = token_result.scalar_one_or_none()

                if token:
                    # Decrypt user's API key
                    api_key = encryption_service.decrypt(token.encrypted_value)
                    # Update last_used_at
                    token.last_used_at = datetime.now(timezone.utc)
                    await db.commit()
                else:
                    # Fall back to system API key from secrets.yaml
                    secrets_path = CONFIG_DIR / "secrets.yaml"
                    try:
                        with open(secrets_path) as f:
                            secrets_data = yaml.safe_load(f)
                            api_key = secrets_data.get("anthropic_api_key")
                    except Exception as e:
                        logger.error(f"Failed to load system API key: {e}")

                if not api_key:
                    emit_error_event(
                        "No Anthropic API key available (neither user nor system)",
                        "configuration_error"
                    )
                    await self._update_session_status(session_id, "failed")
                    return

                # Fetch Session from database and create SessionContext
                session_result = await db.execute(
                    select(Session).where(Session.id == session_id)
                )
                db_session = session_result.scalar_one_or_none()
                if db_session:
                    session_context = SessionContext.from_db_session(db_session)
                    logger.debug(f"Created SessionContext for {session_id}")
                else:
                    logger.warning(f"Session {session_id} not found in database")

            # Use sessions_dir passed from API endpoint (already determined once)
            sessions_dir = Path(params.sessions_dir)
            working_dir = sessions_dir / session_id

            # Set up dynamic mounts if any were requested
            dynamic_mount_info = []
            if params.dynamic_mounts:
                try:
                    session_manager = SessionManager(sessions_dir)
                    dynamic_mount_info = session_manager.setup_dynamic_mounts(
                        session_id=session_id,
                        username=user.username,
                        mount_requests=params.dynamic_mounts,
                        owner_uid=linux_uid,
                    )
                    logger.info(
                        f"Set up {len(dynamic_mount_info)} dynamic mounts for session {session_id}"
                    )
                except Exception as mount_error:
                    logger.error(f"Failed to set up dynamic mounts: {mount_error}")
                    emit_error_event(
                        f"Failed to set up dynamic mounts: {mount_error}",
                        "configuration_error"
                    )
                    await self._update_session_status(session_id, "failed")
                    return
            else:
                # Resume: reload persisted mount info (symlinks already exist)
                try:
                    session_manager = SessionManager(sessions_dir)
                    dynamic_mount_info = session_manager.load_dynamic_mount_info(
                        session_id
                    )
                    if dynamic_mount_info:
                        logger.info(
                            f"Reloaded {len(dynamic_mount_info)} dynamic mounts "
                            f"for resumed session {session_id}"
                        )
                except Exception as mount_error:
                    logger.warning(
                        f"Failed to reload dynamic mount info for session "
                        f"{session_id}: {mount_error}"
                    )

            # Build SSH context if SSH is enabled and user has active profiles
            ssh_context = None
            try:
                from .ssh_service_manager import ssh_service_manager as ssh_mgr
                if ssh_mgr and ssh_mgr.enabled:
                    async with AsyncSessionLocal() as ssh_db:
                        from .ssh_profile_service import (
                            get_profiles,
                            profile_to_ssh_profile,
                        )
                        records = await get_profiles(ssh_db, params.user_id)
                        active_records = [r for r in records if r.is_active]
                        if active_records:
                            from .vault_encryption import VaultEncryption
                            from .vault_service import VaultService
                            from ..config import CONFIG_DIR
                            import yaml as _yaml
                            secrets_path = CONFIG_DIR / "secrets.yaml"
                            secrets_data = {}
                            if secrets_path.exists():
                                with secrets_path.open("r", encoding="utf-8") as f:
                                    secrets_data = _yaml.safe_load(f) or {}
                            master_key = (secrets_data.get("fernet_key", "") or "").encode()
                            if master_key:
                                encryption = VaultEncryption(master_key=master_key)
                                vault_svc = VaultService(vault_encryption=encryption)
                                profiles = {
                                    r.name: profile_to_ssh_profile(r)
                                    for r in active_records
                                }
                                ssh_context = await ssh_mgr.build_session_context(
                                    session_id=session_id,
                                    user_id=params.user_id,
                                    profiles=profiles,
                                    db_session_factory=AsyncSessionLocal,
                                    vault_service=vault_svc,
                                )
                                if ssh_context:
                                    logger.info(
                                        "SSH context built for session %s (%d profiles)",
                                        session_id, len(profiles),
                                    )
            except Exception as ssh_err:
                logger.warning("Failed to build SSH context: %s", ssh_err)

            logger.info(f"Task: {params.task[:100]}{'...' if len(params.task) > 100 else ''}")

            exec_params = TaskExecutionParams(
                task=params.task,
                working_dir=working_dir,
                session_id=session_id,
                resume_session_id=params.resume_session_id,
                fork_session=params.fork_session,
                model=params.model,
                max_turns=params.max_turns,
                timeout_seconds=params.timeout_seconds,
                permission_mode=params.permission_mode,
                role=params.role,
                profile_path=Path(params.profile) if params.profile else None,
                additional_dirs=params.additional_dirs or [],
                enable_skills=params.enable_skills,
                enable_file_checkpointing=params.enable_file_checkpointing,
                max_buffer_size=params.max_buffer_size,
                output_format=params.output_format,
                include_partial_messages=params.include_partial_messages,
                thinking_tokens=params.thinking_tokens,
                # User isolation
                user_id=params.user_id,
                username=user.username,
                linux_uid=linux_uid,
                linux_gid=linux_gid,
                anthropic_api_key=api_key,
                sessions_dir=sessions_dir,
                tracer=tracer,
                # Session context from database
                session_context=session_context,
                # Dynamic mounts set up for this session
                dynamic_mounts=dynamic_mount_info,
                # SSH tool context (if user has active profiles)
                ssh_context=ssh_context,
            )

            # Execute using unified task runner
            result = await execute_agent_task(exec_params)

            # Store result
            self._results[session_id] = {
                "status": result.status.value,
                "output": result.output,
                "error": result.error,
                "comments": result.comments,
                "result_files": result.result_files,
                "metrics": result.metrics.model_dump() if result.metrics else None,
            }

            # Update database with final status and metrics
            metrics = result.metrics
            final_status = result.status.value.lower()
            if final_status == "error":
                final_status = "failed"

            # Check if agent stopped due to AskUserQuestion (human-in-the-loop)
            # If a question_pending event was emitted without a matching question_answered,
            # the session should be in "waiting_for_input" status
            try:
                from tools.ag3ntum.ag3ntum_ask.tool import get_pending_question_from_events
                pending_question = await get_pending_question_from_events(session_id)
                if pending_question:
                    final_status = "waiting_for_input"
                    logger.info(
                        f"Session {session_id} waiting for user input "
                        f"(question_id: {pending_question.get('question_id')})"
                    )
                    # Emit status_update event so frontend updates immediately
                    # This corrects the status from agent_complete which was emitted
                    # before we knew about the pending question
                    emit_event("status_update", {
                        "session_id": session_id,
                        "status": "waiting_for_input",
                        "question_id": pending_question.get("question_id"),
                    })
            except Exception as e:
                logger.warning(f"Failed to check pending questions for {session_id}: {e}")

            # Extract usage data for cumulative token tracking
            usage_dict = None
            if metrics and metrics.usage:
                usage_dict = {
                    "input_tokens": metrics.usage.input_tokens,
                    "output_tokens": metrics.usage.output_tokens,
                    "cache_creation_input_tokens": metrics.usage.cache_creation_input_tokens,
                    "cache_read_input_tokens": metrics.usage.cache_read_input_tokens,
                }

            await self._update_session_status(
                session_id=session_id,
                status=final_status,
                model=metrics.model if metrics else params.model,
                num_turns=metrics.num_turns if metrics else None,
                duration_ms=metrics.duration_ms if metrics else None,
                total_cost_usd=metrics.total_cost_usd if metrics else None,
                usage=usage_dict,
            )

            logger.info(f"Agent completed for session: {session_id} (status: {final_status})")

            # Fire-and-forget: record usage for billing/reporting
            # Failure must NOT affect session completion (spec 1.6.3)
            try:
                await self._record_usage(
                    session_id=session_id,
                    user_id=params.user_id,
                    model=metrics.model if metrics else (params.model or "unknown"),
                    metrics=metrics,
                    usage=usage_dict,
                )
            except Exception as e:
                logger.warning(f"Usage recording failed for {session_id} (non-fatal): {e}")

        except asyncio.CancelledError:
            logger.info(f"Agent cancelled for session: {session_id}")

            # Check if session has claude_session_id (Claude session was established)
            # This determines if the session can be resumed.
            # We check the database Session.claude_session_id directly.
            has_resume_id = False
            try:
                # Primary: Check database for claude_session_id
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(Session).where(Session.id == session_id)
                    )
                    db_session = result.scalar_one_or_none()
                    if db_session and db_session.claude_session_id:
                        has_resume_id = True
                    else:
                        # Fallback: Check events for agent_start with session_id
                        events = await event_service.list_events(session_id, limit=50)
                        has_resume_id = any(
                            e.get("type") == "agent_start" and e.get("data", {}).get("session_id")
                            for e in events
                        )
            except Exception as e:
                logger.warning(f"Failed to check resumability for {session_id}: {e}")

            self._results[session_id] = {
                "status": "cancelled",
                "error": "Task was cancelled",
                "resumable": has_resume_id,
            }
            emit_cancelled_event("Task was cancelled", resumable=has_resume_id)
            await self._update_session_status(session_id, "cancelled")
            raise

        except Exception as e:
            error_message = str(e)
            logger.exception(f"Agent failed for session: {session_id}")

            # Provide user-friendly error message
            if "Can't find source path" in error_message:
                user_message = (
                    f"Internal sandbox configuration error: {error_message}. "
                    "Check backend logs for details."
                )
            elif "bwrap" in error_message.lower():
                user_message = (
                    f"Sandbox execution error: {error_message}. "
                    "The sandboxed command failed to execute."
                )
            else:
                user_message = f"Internal error: {error_message}"

            self._results[session_id] = {
                "status": "failed",
                "error": user_message,
            }
            emit_error_event(user_message, "execution_error")
            await self._update_session_status(session_id, "failed")

        finally:
            # Close SSH connections for this session
            if ssh_context is not None:
                try:
                    from .ssh_service_manager import ssh_service_manager as _ssh_mgr
                    await _ssh_mgr.cleanup_session(session_id)
                except Exception as ssh_err:
                    logger.warning("SSH cleanup failed for %s: %s", session_id, ssh_err)

            # Drain pending tracer tasks to ensure all events are persisted
            if tracer is not None:
                try:
                    await tracer.drain()
                except Exception as e:
                    logger.warning(f"Failed to drain tracer tasks for {session_id}: {e}")

            # Cleanup
            self._running_tasks.pop(session_id, None)
            self._cancel_flags.pop(session_id, None)
            # Subscribers handle their own cleanup

            # Notify completion callbacks (for queue processor)
            for callback in self._completion_callbacks:
                try:
                    callback(session_id, params.user_id)
                except Exception as cb_error:
                    logger.warning(f"Completion callback error: {cb_error}")

    def register_completion_callback(
        self, callback: Callable[[str, str], None]
    ) -> None:
        """
        Register a callback to be called when any task completes.

        Callbacks are invoked with (session_id, user_id) arguments after
        task completion (success, failure, or cancellation).

        Args:
            callback: Function to call on completion.
        """
        self._completion_callbacks.append(callback)
        logger.debug(f"Registered completion callback: {callback.__name__}")

    async def cancel_task(self, session_id: str) -> bool:
        """
        Cancel a running task.

        Args:
            session_id: The session ID.

        Returns:
            True if cancelled, False if not running.
        """
        if session_id not in self._running_tasks:
            return False

        # Set cancel flag (for graceful cancellation)
        self._cancel_flags[session_id] = True

        # Cancel the asyncio task
        task = self._running_tasks[session_id]
        task.cancel()

        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        logger.info(f"Cancelled task for session: {session_id}")
        return True

    def is_running(self, session_id: str) -> bool:
        """
        Check if a task is currently running.

        Args:
            session_id: The session ID.

        Returns:
            True if running.
        """
        return session_id in self._running_tasks

    def is_cancellation_requested(self, session_id: str) -> bool:
        """
        Check if cancellation was requested.

        Args:
            session_id: The session ID.

        Returns:
            True if cancellation was requested.
        """
        return self._cancel_flags.get(session_id, False)

    def get_event_queue(self, session_id: str) -> Optional[asyncio.Queue]:
        """Deprecated: event queues are managed per-subscriber."""
        return None

    def subscribe(
        self,
        session_id: str,
        from_sequence: Optional[int] = None,
    ):
        """
        Subscribe to events for a session via Redis Streams.

        Returns an async generator that yields events from the stream.
        With Redis Streams, events are persisted so consumers can join
        at any time and read from any point - no race conditions.

        Args:
            session_id: The session ID to subscribe to.
            from_sequence: Start from events after this sequence (optional).

        Returns:
            Async generator yielding event dictionaries.
        """
        return self._event_hub.subscribe(session_id, from_sequence=from_sequence)

    async def stop_subscriber(self, session_id: str) -> None:
        """Signal subscribers to stop for a session."""
        await self._event_hub.stop_subscriber(session_id)

    async def publish_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Publish an event to all subscribers for a session."""
        await self._event_hub.publish(session_id, event)

    def get_result(self, session_id: str) -> Optional[dict]:
        """
        Get the result of a completed task.

        Args:
            session_id: The session ID.

        Returns:
            Result dictionary, or None if not found.
        """
        return self._results.get(session_id)

    def cleanup_session(self, session_id: str) -> None:
        """
        Cleanup resources for a session.

        Args:
            session_id: The session ID.
        """
        self._results.pop(session_id, None)


# Global agent runner instance
agent_runner = AgentRunner()
