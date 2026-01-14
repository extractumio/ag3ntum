"""
Session management for Ag3ntum.

Handles session creation, persistence, and resumption.
Each session has an isolated workspace with:
- skills/ - Symlinked from global skills library

Implements robustness features:
- Atomic file writes for session info
- Proper error handling and logging
"""
import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .exceptions import SessionError
from .schemas import (
    Checkpoint,
    CheckpointType,
    SessionInfo,
    TaskStatus,
    TokenUsage,
)

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Manages agent sessions.

    Sessions are stored in the sessions directory and include:
    - Session metadata (session_info.json)
    - Agent logs (agent.jsonl)
    - Output files created by the agent in workspace

    Thread-safety:
    - Uses atomic writes for session info updates
    """

    def __init__(self, sessions_dir: Path) -> None:
        """
        Initialize the session manager.

        Args:
            sessions_dir: Directory to store sessions.
        """
        self._sessions_dir = sessions_dir
        # Note: Directory is created on-demand in create_session(), not here.
        # This allows lazy initialization for per-user session directories.

    def create_session(
        self,
        working_dir: str,
        session_id: Optional[str] = None
    ) -> SessionInfo:
        """
        Create a new session.

        Args:
            working_dir: Working directory for the session.
            session_id: Optional session ID. If None, generates one.

        Returns:
            SessionInfo for the new session.
        """
        if session_id is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            uid = uuid.uuid4().hex[:8]
            session_id = f"{ts}_{uid}"

        session_info = SessionInfo(
            session_id=session_id,
            working_dir=working_dir,
            status=TaskStatus.PARTIAL
        )

        session_dir = self.get_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        self._save_session_info(session_info)
        logger.info(f"Created session: {session_id}")

        return session_info

    def get_session_dir(self, session_id: str) -> Path:
        """
        Get the directory for a session.

        Args:
            session_id: The session ID.

        Returns:
            Path to the session directory.
        """
        return self._sessions_dir / session_id

    def get_log_file(self, session_id: str) -> Path:
        """
        Get the log file path for a session.

        Args:
            session_id: The session ID.

        Returns:
            Path to the agent.jsonl file.
        """
        return self.get_session_dir(session_id) / "agent.jsonl"

    def get_workspace_dir(self, session_id: str) -> Path:
        """
        Get the workspace directory for a session.

        The workspace is a sandboxed subdirectory where the agent can
        write output. This is separate from the session directory to
        prevent the agent from reading logs and other sensitive files.

        Args:
            session_id: The session ID.

        Returns:
            Path to the workspace directory.
        """
        workspace = self.get_session_dir(session_id) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def cleanup_workspace_skills(self, session_id: str) -> None:
        """
        Remove the skills folder from a session's workspace.

        Called after agent run completes to clean up merged skills symlinks.
        The .claude/skills/ directory contains symlinks to actual skill sources.
        Workspace files are preserved.

        Args:
            session_id: The session ID.
        """
        # New structure: .claude/skills/ contains symlinks
        claude_skills_dir = (
            self.get_session_dir(session_id) / "workspace" / ".claude" / "skills"
        )

        if claude_skills_dir.exists():
            try:
                # Remove the directory with all its symlinks
                shutil.rmtree(claude_skills_dir)
                logger.info(
                    f"Cleaned up workspace/.claude/skills/ for session {session_id}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to cleanup workspace skills for session {session_id}: {e}"
                )

        # Also clean old-style skills/ symlink for backward compatibility
        old_skills_link = self.get_session_dir(session_id) / "workspace" / "skills"
        if old_skills_link.is_symlink():
            try:
                old_skills_link.unlink()
                logger.debug(f"Removed legacy skills symlink for session {session_id}")
            except Exception as e:
                logger.warning(f"Failed to remove legacy skills symlink: {e}")

    def _save_session_info(self, session_info: SessionInfo) -> None:
        """
        Save session info to disk atomically.

        Uses a temporary file and atomic rename to prevent corruption
        if the process crashes mid-write.
        """
        session_dir = self.get_session_dir(session_info.session_id)
        info_file = session_dir / "session_info.json"

        # Write to temporary file first
        temp_fd = None
        temp_path = None
        try:
            temp_fd, temp_path = tempfile.mkstemp(
                dir=session_dir,
                prefix=".session_info_",
                suffix=".tmp"
            )
            # Write JSON content
            content = session_info.model_dump_json(indent=2)
            os.write(temp_fd, content.encode('utf-8'))
            os.fsync(temp_fd)  # Ensure data is flushed to disk
            os.close(temp_fd)
            temp_fd = None

            # Atomic rename
            os.rename(temp_path, info_file)
            temp_path = None  # Mark as successfully moved

        except Exception as e:
            logger.error(f"Failed to save session info: {e}")
            raise
        finally:
            # Clean up on failure
            if temp_fd is not None:
                try:
                    os.close(temp_fd)
                except Exception:
                    pass
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    def load_session(self, session_id: str) -> SessionInfo:
        """
        Load an existing session.

        Args:
            session_id: The session ID to load.

        Returns:
            SessionInfo for the session.

        Raises:
            SessionError: If the session cannot be loaded.
        """
        session_dir = self.get_session_dir(session_id)
        info_file = session_dir / "session_info.json"

        if not info_file.exists():
            raise SessionError(f"Session not found: {session_id}")

        try:
            data = json.loads(info_file.read_text())
            return SessionInfo(**data)
        except (json.JSONDecodeError, ValueError) as e:
            raise SessionError(f"Failed to load session {session_id}: {e}")

    def update_session(
        self,
        session_info: SessionInfo,
        status: Optional[TaskStatus] = None,
        resume_id: Optional[str] = None,
        num_turns: Optional[int] = None,
        duration_ms: Optional[int] = None,
        total_cost_usd: Optional[float] = None,
        usage: Optional[TokenUsage] = None,
        model: Optional[str] = None
    ) -> SessionInfo:
        """
        Update an existing session with cumulative statistics.

        Stats from the current run are stored and also added to cumulative
        totals, enabling tracking across session resumptions.

        Args:
            session_info: The session to update.
            status: New status (optional).
            resume_id: Claude session ID for resuming (optional).
            num_turns: Number of turns in this run (optional).
            duration_ms: Duration of this run in milliseconds (optional).
            total_cost_usd: Cost of this run in USD (optional).
            usage: Token usage for this run (optional).
            model: The model used in this session (optional).

        Returns:
            Updated SessionInfo with cumulative stats.
        """
        if status is not None:
            session_info.status = status
        if resume_id is not None:
            session_info.resume_id = resume_id
        if model is not None:
            session_info.model = model

        # Update current run stats
        if num_turns is not None:
            session_info.num_turns = num_turns
            # Add to cumulative
            session_info.cumulative_turns += num_turns

        if duration_ms is not None:
            session_info.duration_ms = duration_ms
            # Add to cumulative
            session_info.cumulative_duration_ms += duration_ms

        if total_cost_usd is not None:
            session_info.total_cost_usd = total_cost_usd
            # Add to cumulative
            session_info.cumulative_cost_usd += total_cost_usd

        if usage is not None:
            # Add to cumulative usage
            if session_info.cumulative_usage is None:
                session_info.cumulative_usage = usage
            else:
                session_info.cumulative_usage = (
                    session_info.cumulative_usage.add(usage)
                )

        self._save_session_info(session_info)
        return session_info

    # -------------------------------------------------------------------------
    # Checkpoint Management
    # -------------------------------------------------------------------------

    def add_checkpoint(
        self,
        session_info: SessionInfo,
        uuid: str,
        checkpoint_type: CheckpointType = CheckpointType.AUTO,
        description: Optional[str] = None,
        turn_number: Optional[int] = None,
        tool_name: Optional[str] = None,
        file_path: Optional[str] = None
    ) -> Checkpoint:
        """
        Add a checkpoint to the session.

        Checkpoints track file system state at specific points, enabling
        rollback of file changes via rewind_files().

        Args:
            session_info: The session to add the checkpoint to.
            uuid: User message UUID from the SDK.
            checkpoint_type: Type of checkpoint (AUTO, MANUAL, TURN).
            description: Optional description of the checkpoint.
            turn_number: Turn number when checkpoint was created.
            tool_name: Name of the tool that triggered this checkpoint.
            file_path: File path that was modified.

        Returns:
            The created Checkpoint object.
        """
        checkpoint = Checkpoint(
            uuid=uuid,
            checkpoint_type=checkpoint_type,
            description=description,
            turn_number=turn_number,
            tool_name=tool_name,
            file_path=file_path,
        )
        session_info.checkpoints.append(checkpoint)
        self._save_session_info(session_info)
        logger.debug(f"Added checkpoint: {checkpoint.to_summary()}")
        return checkpoint

    def list_checkpoints(self, session_id: str) -> list[Checkpoint]:
        """
        List all checkpoints for a session.

        Args:
            session_id: The session ID.

        Returns:
            List of Checkpoint objects, ordered by creation time.
        """
        session_info = self.load_session(session_id)
        return session_info.checkpoints

    def get_checkpoint(
        self,
        session_id: str,
        checkpoint_id: Optional[str] = None,
        index: Optional[int] = None
    ) -> Optional[Checkpoint]:
        """
        Get a specific checkpoint by UUID or index.

        Args:
            session_id: The session ID.
            checkpoint_id: The checkpoint UUID to find.
            index: The checkpoint index (0 = first, -1 = last).

        Returns:
            The Checkpoint if found, None otherwise.

        Raises:
            ValueError: If neither checkpoint_id nor index is provided.
        """
        if checkpoint_id is None and index is None:
            raise ValueError("Either checkpoint_id or index must be provided")

        checkpoints = self.list_checkpoints(session_id)

        if not checkpoints:
            return None

        if index is not None:
            try:
                return checkpoints[index]
            except IndexError:
                return None

        for checkpoint in checkpoints:
            if checkpoint.uuid == checkpoint_id:
                return checkpoint

        return None

    def get_latest_checkpoint(self, session_id: str) -> Optional[Checkpoint]:
        """
        Get the most recent checkpoint for a session.

        Args:
            session_id: The session ID.

        Returns:
            The latest Checkpoint if any exist, None otherwise.
        """
        return self.get_checkpoint(session_id, index=-1)

    def clear_checkpoints_after(
        self,
        session_info: SessionInfo,
        checkpoint_uuid: str
    ) -> int:
        """
        Remove all checkpoints after a specific checkpoint.

        Used when rewinding to a checkpoint - subsequent checkpoints
        become invalid as the file state has changed.

        Args:
            session_info: The session to modify.
            checkpoint_uuid: The UUID of the checkpoint to keep.

        Returns:
            Number of checkpoints removed.
        """
        original_count = len(session_info.checkpoints)

        keep_checkpoints = []
        found_target = False
        for checkpoint in session_info.checkpoints:
            keep_checkpoints.append(checkpoint)
            if checkpoint.uuid == checkpoint_uuid:
                found_target = True
                break

        if found_target:
            session_info.checkpoints = keep_checkpoints
            self._save_session_info(session_info)
            removed = original_count - len(keep_checkpoints)
            if removed > 0:
                logger.info(
                    f"Cleared {removed} checkpoints after {checkpoint_uuid}"
                )
            return removed

        return 0

    def clear_all_checkpoints(self, session_info: SessionInfo) -> int:
        """
        Remove all checkpoints from a session.

        Args:
            session_info: The session to clear checkpoints from.

        Returns:
            Number of checkpoints removed.
        """
        count = len(session_info.checkpoints)
        session_info.checkpoints = []
        self._save_session_info(session_info)
        if count > 0:
            logger.info(f"Cleared all {count} checkpoints from session")
        return count

    def get_checkpoints_by_type(
        self,
        session_id: str,
        checkpoint_type: CheckpointType
    ) -> list[Checkpoint]:
        """
        Get checkpoints of a specific type.

        Args:
            session_id: The session ID.
            checkpoint_type: The type of checkpoints to retrieve.

        Returns:
            List of matching Checkpoint objects.
        """
        checkpoints = self.list_checkpoints(session_id)
        return [cp for cp in checkpoints if cp.checkpoint_type == checkpoint_type]

    def get_checkpoints_for_file(
        self,
        session_id: str,
        file_path: str
    ) -> list[Checkpoint]:
        """
        Get checkpoints related to a specific file.

        Args:
            session_id: The session ID.
            file_path: The file path to filter by.

        Returns:
            List of Checkpoint objects for the specified file.
        """
        checkpoints = self.list_checkpoints(session_id)
        return [cp for cp in checkpoints if cp.file_path == file_path]

    def list_sessions(self) -> list[SessionInfo]:
        """
        List all sessions.

        Returns:
            List of SessionInfo objects.
        """
        sessions = []
        if not self._sessions_dir.exists():
            return sessions

        for session_dir in self._sessions_dir.iterdir():
            if session_dir.is_dir():
                try:
                    sessions.append(self.load_session(session_dir.name))
                except SessionError:
                    continue

        return sorted(sessions, key=lambda s: s.created_at, reverse=True)


def generate_session_id() -> str:
    """Generate a unique session ID."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{ts}_{uid}"
