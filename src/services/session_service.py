"""
Session service for Ag3ntum API.

Handles session CRUD operations and synchronization between
the database and file-based SessionManager.

Implements robustness features:
- Error handling with proper rollback
- Retry logic for transient database failures
- Atomic session creation (database + file system)
- Session ID format validation
- Cancellation flag persistence
"""
import asyncio
import logging
import re
import shutil
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import SESSIONS_DIR
from ..core.sessions import SessionManager, generate_session_id
from ..db.models import Session

logger = logging.getLogger(__name__)

# Type variable for retry decorator
T = TypeVar("T")

# Session ID validation pattern: YYYYMMDD_HHMMSS_hexchars
SESSION_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[a-f0-9]{8}$")

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 0.1
RETRY_BACKOFF_MULTIPLIER = 2.0


class SessionServiceError(Exception):
    """Base exception for session service errors."""
    pass


class SessionCreationError(SessionServiceError):
    """Error during session creation."""
    pass


class SessionNotFoundError(SessionServiceError):
    """Session not found."""
    pass


class InvalidSessionIdError(SessionServiceError):
    """Invalid session ID format."""
    pass


def validate_session_id(session_id: str) -> None:
    """
    Validate session ID format to prevent path traversal attacks.

    Args:
        session_id: The session ID to validate.

    Raises:
        InvalidSessionIdError: If the session ID format is invalid.
    """
    if not session_id:
        raise InvalidSessionIdError("Session ID cannot be empty")

    if not SESSION_ID_PATTERN.match(session_id):
        raise InvalidSessionIdError(
            f"Invalid session ID format: {session_id}. "
            "Expected format: YYYYMMDD_HHMMSS_hexchars"
        )

    # Additional path traversal check
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        raise InvalidSessionIdError(
            f"Invalid characters in session ID: {session_id}"
        )


def with_db_retry(
    max_retries: int = MAX_RETRIES,
    retry_delay: float = RETRY_DELAY_SECONDS,
    backoff_multiplier: float = RETRY_BACKOFF_MULTIPLIER,
) -> Callable:
    """
    Decorator for retrying database operations on transient failures.

    Retries on OperationalError (connection issues, locks, etc.)
    but not on IntegrityError (constraint violations).
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_error: Optional[Exception] = None
            delay = retry_delay

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except OperationalError as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Database operation failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_multiplier
                    else:
                        logger.error(
                            f"Database operation failed after {max_retries + 1} attempts: {e}"
                        )
                except IntegrityError:
                    # Don't retry integrity errors - they won't succeed
                    raise

            raise last_error  # type: ignore
        return wrapper
    return decorator


class SessionService:
    """
    Service for session management.

    Provides methods for creating, querying, and updating sessions.
    Synchronizes between SQLite database and file-based session storage.

    Features:
    - Atomic session creation with rollback on failure
    - Retry logic for transient database failures
    - Session ID validation
    - Proper error handling and logging
    """

    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        """
        Initialize the session service.

        Args:
            sessions_dir: Directory for file-based session storage.
        """
        self._sessions_dir = sessions_dir or SESSIONS_DIR
        self._session_manager = SessionManager(self._sessions_dir)
        # In-memory cancellation flags for quick checks without DB
        self._cancellation_flags: dict[str, bool] = {}

    @with_db_retry()
    async def create_session(
        self,
        db: AsyncSession,
        user_id: str,
        task: str,
        sessions_dir: Path,
        model: Optional[str] = None,
    ) -> Session:
        """
        Create a new session atomically.

        Creates both a database record and the file-based session folder.
        If either operation fails, both are rolled back.

        Args:
            db: Database session.
            user_id: Owner user ID.
            task: Task description.
            sessions_dir: User-specific sessions base directory (e.g., /users/username/sessions).
            model: Claude model to use.

        Returns:
            The created Session database object.

        Raises:
            SessionCreationError: If session creation fails.
        """
        session_id = generate_session_id()
        validate_session_id(session_id)  # Validate our own generated ID

        file_session_created = False

        try:
            # Create SessionManager for this user's sessions directory
            session_manager = SessionManager(sessions_dir)
            
            # Create file-based session first (creates folder structure)
            session_manager.create_session(
                working_dir=str(sessions_dir),
                session_id=session_id
            )
            file_session_created = True

            # Create database record
            db_session = Session(
                id=session_id,
                user_id=user_id,
                status="pending",
                task=task,
                model=model,
                working_dir=str(sessions_dir / session_id),
            )
            db.add(db_session)

            try:
                await db.commit()
                await db.refresh(db_session)
            except Exception as db_error:
                # Database commit failed - rollback file-based session
                logger.error(
                    f"Database commit failed for session {session_id}, "
                    f"rolling back file-based session: {db_error}"
                )
                await db.rollback()
                raise SessionCreationError(
                    f"Failed to create session in database: {db_error}"
                ) from db_error

            logger.info(f"Created session: {session_id} for user: {user_id}")
            return db_session

        except SessionCreationError:
            # Clean up file-based session on database failure
            if file_session_created:
                self._cleanup_file_session(session_id, sessions_dir)
            raise
        except Exception as e:
            # Clean up file-based session on any other failure
            if file_session_created:
                self._cleanup_file_session(session_id, sessions_dir)
            logger.error(f"Session creation failed: {e}")
            raise SessionCreationError(f"Failed to create session: {e}") from e

    def _cleanup_file_session(self, session_id: str, sessions_dir: Path) -> None:
        """
        Clean up a file-based session on creation failure.

        Args:
            session_id: The session ID to clean up.
            sessions_dir: Base directory where session was created.
        """
        try:
            session_dir = sessions_dir / session_id
            
            if session_dir.exists():
                shutil.rmtree(session_dir)
                logger.info(f"Cleaned up orphaned session directory: {session_id}")
        except Exception as cleanup_error:
            logger.warning(
                f"Failed to cleanup orphaned session {session_id}: {cleanup_error}"
            )

    @with_db_retry()
    async def get_session(
        self,
        db: AsyncSession,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Session]:
        """
        Get a session by ID.

        Args:
            db: Database session.
            session_id: The session ID.
            user_id: Optional user ID to verify ownership.

        Returns:
            Session if found and authorized, None otherwise.

        Raises:
            InvalidSessionIdError: If session ID format is invalid.
        """
        validate_session_id(session_id)

        query = select(Session).where(Session.id == session_id)

        if user_id:
            query = query.where(Session.user_id == user_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    @with_db_retry()
    async def list_sessions(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Session], int]:
        """
        List sessions for a user.

        Args:
            db: Database session.
            user_id: The user ID.
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.

        Returns:
            Tuple of (sessions list, total count).
        """
        # Count query
        from sqlalchemy import func
        count_query = select(func.count()).select_from(Session).where(
            Session.user_id == user_id
        )
        count_result = await db.execute(count_query)
        total = count_result.scalar_one()

        # List query
        query = (
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(query)
        sessions = list(result.scalars().all())

        return sessions, total

    @with_db_retry()
    async def update_session(
        self,
        db: AsyncSession,
        session: Session,
        status: Optional[str] = None,
        num_turns: Optional[int] = None,
        duration_ms: Optional[int] = None,
        total_cost_usd: Optional[float] = None,
        cancel_requested: Optional[bool] = None,
        completed_at: Optional[datetime] = None,
    ) -> Session:
        """
        Update a session with proper error handling.

        Args:
            db: Database session.
            session: The session to update.
            status: New status.
            num_turns: Number of turns.
            duration_ms: Duration in milliseconds.
            total_cost_usd: Total cost in USD.
            cancel_requested: Whether cancellation was requested.
            completed_at: Completion timestamp.

        Returns:
            The updated session.
        """
        if status is not None:
            session.status = status
        if num_turns is not None:
            session.num_turns = num_turns
        if duration_ms is not None:
            session.duration_ms = duration_ms
        if total_cost_usd is not None:
            session.total_cost_usd = total_cost_usd
        if cancel_requested is not None:
            session.cancel_requested = cancel_requested
            # Update in-memory flag for quick checks
            self._cancellation_flags[session.id] = cancel_requested
        if completed_at is not None:
            session.completed_at = completed_at

        session.updated_at = datetime.now(timezone.utc)

        try:
            await db.commit()
            await db.refresh(session)
        except Exception as e:
            logger.error(f"Failed to update session {session.id}: {e}")
            await db.rollback()
            raise

        return session

    @with_db_retry()
    async def request_cancellation(
        self,
        db: AsyncSession,
        session: Session,
    ) -> Session:
        """
        Request cancellation for a running session.

        Args:
            db: Database session.
            session: The session to cancel.

        Returns:
            The updated session.
        """
        # Set in-memory flag immediately for quick checks
        self._cancellation_flags[session.id] = True

        return await self.update_session(
            db,
            session,
            cancel_requested=True,
        )

    def get_session_file(self, session_id: str, file_path: str) -> Path:
        """
        Resolve a workspace file path for a session.

        Args:
            session_id: The session ID.
            file_path: Relative file path within the workspace.

        Returns:
            Path to the file within the session workspace.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the path escapes the workspace.
            InvalidSessionIdError: If session ID format is invalid.
        """
        validate_session_id(session_id)

        workspace = self._session_manager.get_workspace_dir(session_id).resolve()
        normalized = file_path.strip().lstrip('/')
        if normalized.startswith('./'):
            normalized = normalized[2:]

        candidate = (workspace / normalized).resolve()

        # Security check: ensure resolved path is within workspace
        # This catches symlink attacks by using resolved paths
        if not str(candidate).startswith(str(workspace)):
            raise ValueError("Invalid file path: escapes workspace boundary")

        # Additional check for symlinks pointing outside workspace
        if candidate.is_symlink():
            real_path = candidate.resolve()
            if not str(real_path).startswith(str(workspace)):
                raise ValueError("Invalid file path: symlink points outside workspace")

        if not candidate.exists():
            raise FileNotFoundError("File not found")

        return candidate

    def get_session_info(self, session_id: str) -> dict:
        """
        Get the file-based session info (session_info.json).

        This contains token usage, cumulative stats, and other metadata
        not stored in the database.

        Args:
            session_id: The session ID.

        Returns:
            Session info as a dictionary, or empty dict if not found.
        """
        try:
            validate_session_id(session_id)
            session_info = self._session_manager.load_session(session_id)
            return session_info.model_dump()
        except InvalidSessionIdError:
            logger.debug(f"Invalid session ID format: {session_id}")
            return {}
        except Exception as e:
            # Lower to debug - file-based session may be in user-specific directory
            logger.debug(f"Failed to load session info for {session_id}: {e}")
            return {}

    def update_resume_id(
        self, session_id: str, resume_id: str, sessions_base_dir: Optional[Path] = None
    ) -> None:
        """
        Persist a Claude resume_id for a session.

        Args:
            session_id: The local session ID.
            resume_id: Claude session ID to store.
            sessions_base_dir: Optional user-specific sessions directory.
        """
        try:
            validate_session_id(session_id)
        except InvalidSessionIdError as e:
            logger.warning(f"Invalid session ID in update_resume_id: {e}")
            return

        if not resume_id or not isinstance(resume_id, str):
            logger.warning(f"Invalid resume_id for {session_id}: {resume_id}")
            return

        try:
            # Use provided sessions_base_dir or default
            manager = (
                SessionManager(sessions_base_dir) if sessions_base_dir
                else self._session_manager
            )
            session_info = manager.load_session(session_id)
            if session_info.resume_id == resume_id:
                return
            manager.update_session(
                session_info,
                resume_id=resume_id,
            )
            logger.debug(f"Updated resume_id for {session_id}")
        except Exception as e:
            # Lower to debug level - file-based update is not critical when DB is primary
            logger.debug(f"Failed to update resume_id for {session_id}: {e}")

    def is_cancellation_requested(self, session_id: str) -> bool:
        """
        Check if cancellation was requested for a session.

        Uses in-memory flag for fast checks during agent execution.
        Falls back to database check if flag not set.

        Args:
            session_id: The session ID.

        Returns:
            True if cancellation was requested.
        """
        # Check in-memory flag first (fast path)
        if session_id in self._cancellation_flags:
            return self._cancellation_flags[session_id]

        # Flag not in memory - default to False
        # The agent_runner will use its own cancel tracking
        return False

    async def check_cancellation_from_db(
        self,
        db: AsyncSession,
        session_id: str
    ) -> bool:
        """
        Check cancellation status from database.

        Use this for authoritative check, especially after server restart.

        Args:
            db: Database session.
            session_id: The session ID.

        Returns:
            True if cancellation was requested.
        """
        try:
            validate_session_id(session_id)
            session = await self.get_session(db, session_id)
            if session and session.cancel_requested:
                # Update in-memory flag
                self._cancellation_flags[session_id] = True
                return True
        except Exception as e:
            logger.warning(f"Failed to check cancellation for {session_id}: {e}")
        return False

    def clear_cancellation_flag(self, session_id: str) -> None:
        """
        Clear the in-memory cancellation flag for a session.

        Called when a session is cleaned up or resumed.

        Args:
            session_id: The session ID.
        """
        self._cancellation_flags.pop(session_id, None)

    async def cleanup_stale_sessions(self, db: AsyncSession) -> int:
        """
        Clean up sessions that are stuck in 'running' state.

        Called on server startup to fix sessions that were interrupted
        by server restart.

        Args:
            db: Database session.

        Returns:
            Number of sessions cleaned up.
        """
        from ..services import event_service

        try:
            # Find all sessions marked as running
            query = select(Session).where(Session.status == "running")
            result = await db.execute(query)
            stale_sessions = list(result.scalars().all())

            cleaned_count = 0
            for session in stale_sessions:
                try:
                    # Check if there's a terminal event in the database
                    terminal_status = await event_service.get_latest_terminal_status(
                        session.id
                    )

                    if terminal_status:
                        # Session actually completed, update status
                        session.status = terminal_status
                        session.completed_at = datetime.now(timezone.utc)
                        logger.info(
                            f"Fixed stale session {session.id}: "
                            f"running -> {terminal_status}"
                        )
                    else:
                        # No terminal event - mark as failed due to server restart
                        session.status = "failed"
                        session.completed_at = datetime.now(timezone.utc)
                        logger.warning(
                            f"Marked interrupted session {session.id} as failed "
                            "(server restart)"
                        )

                    cleaned_count += 1

                except Exception as e:
                    logger.error(
                        f"Failed to clean up stale session {session.id}: {e}"
                    )

            if cleaned_count > 0:
                await db.commit()
                logger.info(f"Cleaned up {cleaned_count} stale sessions")

            return cleaned_count

        except Exception as e:
            logger.error(f"Failed to cleanup stale sessions: {e}")
            await db.rollback()
            return 0


# Global session service instance
session_service = SessionService()
