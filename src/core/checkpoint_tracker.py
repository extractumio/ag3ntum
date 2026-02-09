"""
Checkpoint tracking during agent execution.

Captures UUIDs from tool result messages and creates checkpoints
for file-modifying tools (Write, Edit). Checkpoints are collected
in memory and can be retrieved for database persistence.
"""
import logging
from datetime import datetime
from typing import Any, Optional

from .schemas import Checkpoint, CheckpointType

logger = logging.getLogger(__name__)


class CheckpointTracker:
    """
    Tracks checkpoints during agent execution.

    Captures UUIDs from tool result messages and creates checkpoints
    for file-modifying tools (Write, Edit). Checkpoints are collected
    in memory and can be retrieved for database persistence.
    """

    def __init__(
        self,
        session_id: str,
        auto_checkpoint_tools: list[str],
        enabled: bool = True,
        initial_turn_count: int = 0
    ) -> None:
        """
        Initialize the checkpoint tracker.

        Args:
            session_id: The session ID.
            auto_checkpoint_tools: Tools that trigger auto-checkpoints.
            enabled: Whether checkpoint tracking is enabled.
            initial_turn_count: Starting turn number (cumulative from previous runs).
        """
        self._session_id = session_id
        self._auto_checkpoint_tools = auto_checkpoint_tools
        self._enabled = enabled
        self._pending_tool_calls: dict[str, dict[str, Any]] = {}
        self._turn_counter = 0
        self._initial_turn_count = initial_turn_count
        self._checkpoints: list[Checkpoint] = []

    @property
    def checkpoints(self) -> list[Checkpoint]:
        """Get all checkpoints created during this execution."""
        return self._checkpoints.copy()

    def track_tool_use(self, tool_use_id: str, tool_name: str, tool_input: dict) -> None:
        """
        Track a tool use request for later checkpoint creation.

        Args:
            tool_use_id: The tool use ID from the SDK.
            tool_name: Name of the tool being used.
            tool_input: The tool input parameters.
        """
        if not self._enabled:
            return

        self._pending_tool_calls[tool_use_id] = {
            "tool_name": tool_name,
            "file_path": tool_input.get("file_path"),
        }

    def process_tool_result(self, tool_use_id: str, uuid: Optional[str]) -> Optional[Checkpoint]:
        """
        Process a tool result and create a checkpoint if applicable.

        Args:
            tool_use_id: The tool use ID from the original request.
            uuid: The UUID from the tool result message.

        Returns:
            Created Checkpoint if one was created, None otherwise.
        """
        if not self._enabled or not uuid:
            return None

        tool_info = self._pending_tool_calls.pop(tool_use_id, None)
        if not tool_info:
            return None

        tool_name = tool_info.get("tool_name")
        if tool_name not in self._auto_checkpoint_tools:
            return None

        # Create checkpoint for file-modifying tool
        self._turn_counter += 1
        checkpoint = Checkpoint(
            uuid=uuid,
            created_at=datetime.now(),
            checkpoint_type=CheckpointType.AUTO,
            turn_number=self._initial_turn_count + self._turn_counter,
            tool_name=tool_name,
            file_path=tool_info.get("file_path"),
        )
        self._checkpoints.append(checkpoint)
        logger.debug(f"Created auto checkpoint: {checkpoint.to_summary()}")
        return checkpoint

    def process_message(self, message: Any) -> Optional[Checkpoint]:
        """
        Process a message and create checkpoints as needed.

        This method extracts tool use and tool result information from
        SDK messages and creates checkpoints for file-modifying tools.

        Args:
            message: SDK message to process.

        Returns:
            Created Checkpoint if one was created, None otherwise.
        """
        if not self._enabled:
            return None

        # Check for AssistantMessage with tool use blocks
        if hasattr(message, 'content') and isinstance(message.content, list):
            for block in message.content:
                # Tool use block - track for later
                if hasattr(block, 'name') and hasattr(block, 'id'):
                    tool_input = getattr(block, 'input', {}) or {}
                    self.track_tool_use(block.id, block.name, tool_input)

                # Tool result block - create checkpoint
                if hasattr(block, 'tool_use_id') and hasattr(message, 'uuid'):
                    uuid = getattr(message, 'uuid', None)
                    if uuid:
                        return self.process_tool_result(block.tool_use_id, uuid)

        return None
