"""
Base class for execution tracers.

Provides the TracerBase ABC and SpinnerState dataclass.
"""
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..constants import StatusIcons


@dataclass
class SpinnerState:
    """State for spinner animation."""
    active: bool = False
    message: str = ""
    frames: list[str] = field(default_factory=lambda: list(StatusIcons.SPINNER))
    frame_index: int = 0
    thread: Optional[threading.Thread] = None
    stop_event: Optional[threading.Event] = None


class TracerBase(ABC):
    """
    Abstract base class for execution tracing.

    Override these methods to customize tracing behavior.
    """

    @abstractmethod
    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        """Called when the agent starts execution."""
        pass

    @abstractmethod
    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        """Called before a tool/skill is executed."""
        pass

    @abstractmethod
    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Called after a tool/skill completes."""
        pass

    @abstractmethod
    def on_thinking(self, thinking_text: str, is_partial: bool = False) -> None:
        """
        Called when the agent is in thinking mode.

        Args:
            thinking_text: The thinking text (delta for streaming, full for non-streaming).
            is_partial: True if this is a streaming delta, False if complete.
        """
        pass

    @abstractmethod
    def on_message(self, text: str, is_partial: bool = False) -> None:
        """Called when the agent generates a message."""
        pass

    @abstractmethod
    def on_error(self, error_message: str, error_type: str = "error") -> None:
        """Called when an error occurs."""
        pass

    def on_metrics_update(self, metrics: dict[str, Any]) -> None:
        """Called when execution metrics are updated."""
        _ = metrics

    @abstractmethod
    def on_agent_complete(
        self,
        status: str,
        num_turns: int,
        duration_ms: int,
        total_cost_usd: Optional[float],
        result: Optional[str],
        session_id: Optional[str] = None,
        usage: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        cumulative_cost_usd: Optional[float] = None,
        cumulative_turns: Optional[int] = None,
        cumulative_tokens: Optional[int] = None
    ) -> None:
        """
        Called when the agent completes execution.

        Args:
            status: Final status (COMPLETE, PARTIAL, FAILED, etc.).
            num_turns: Number of turns in this run.
            duration_ms: Duration of this run in milliseconds.
            total_cost_usd: Cost of this run in USD.
            result: The result text/JSON.
            session_id: Claude session ID for resuming.
            usage: Token usage dictionary for this run.
            model: The model used.
            cumulative_cost_usd: Total cost across all runs (for resumed sessions).
            cumulative_turns: Total turns across all runs (for resumed sessions).
            cumulative_tokens: Total tokens across all runs (for resumed sessions).
        """
        pass

    @abstractmethod
    def on_output_display(
        self,
        output: Optional[str] = None,
        error: Optional[str] = None,
        comments: Optional[str] = None,
        result_files: Optional[list[str]] = None,
        status: Optional[str] = None
    ) -> None:
        """
        Called after a structured result is available for display.

        Args:
            output: The output text.
            error: Error message if any.
            comments: Additional comments.
            result_files: List of result file paths.
            status: The status for the result.
        """
        pass

    @abstractmethod
    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        """
        Called when permission profile is switched.

        Args:
            profile_type: Type of profile ("system" or "user").
            profile_name: Name of the profile.
            tools: List of available tools in this profile.
            allow_rules_count: Number of allow rules in the profile.
            deny_rules_count: Number of deny rules in the profile.
            profile_path: Path to the loaded profile file.
        """
        pass

    # ===================================================================
    # Hooks-aware tracing methods (new for ConversationSession)
    # ===================================================================

    @abstractmethod
    def on_hook_triggered(
        self,
        hook_event: str,
        tool_name: Optional[str] = None,
        decision: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        """
        Called when a hook is triggered.

        Args:
            hook_event: Hook event name (PreToolUse, PostToolUse, etc.).
            tool_name: The tool involved, if any.
            decision: Hook decision (allow, deny, block, etc.).
            message: Optional message from the hook.
        """
        pass

    @abstractmethod
    def on_conversation_turn(
        self,
        turn_number: int,
        prompt_preview: str,
        response_preview: str,
        duration_ms: int,
        tools_used: list[str]
    ) -> None:
        """
        Called when a conversation turn completes (multi-turn sessions).

        Args:
            turn_number: The turn number in the conversation.
            prompt_preview: Preview of the user prompt.
            response_preview: Preview of the assistant response.
            duration_ms: Duration of the turn in milliseconds.
            tools_used: List of tools used in this turn.
        """
        pass

    @abstractmethod
    def on_session_connect(self, session_id: Optional[str] = None) -> None:
        """Called when a conversation session connects."""
        pass

    @abstractmethod
    def on_session_disconnect(
        self,
        session_id: Optional[str] = None,
        total_turns: int = 0,
        total_duration_ms: int = 0
    ) -> None:
        """Called when a conversation session disconnects."""
        pass

    # ===================================================================
    # Subagent tracing methods (for Task tool)
    # ===================================================================

    @abstractmethod
    def on_subagent_start(
        self,
        task_id: str,
        subagent_name: str,
        prompt: str
    ) -> None:
        """
        Called when a Task tool invocation starts a subagent.

        Args:
            task_id: The tool_use_id of the Task tool call.
            subagent_name: The subagent type (from Task tool input).
            prompt: The task prompt given to the subagent.
        """
        pass

    @abstractmethod
    def on_subagent_message(
        self,
        task_id: str,
        text: str,
        is_partial: bool = False
    ) -> None:
        """
        Called for messages from within a subagent context.

        Args:
            task_id: The tool_use_id of the parent Task tool call.
            text: The message text from the subagent.
            is_partial: Whether this is a partial streaming message.
        """
        pass

    @abstractmethod
    def on_subagent_stop(
        self,
        task_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """
        Called when a subagent completes.

        Args:
            task_id: The tool_use_id of the Task tool call.
            result: The result returned by the subagent.
            duration_ms: Duration of subagent execution.
            is_error: Whether the subagent completed with an error.
        """
        pass
