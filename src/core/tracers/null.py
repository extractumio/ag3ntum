"""
Null Tracer for Ag3ntum.

No-op tracer that does nothing. Use when tracing is completely disabled.
"""
from typing import Any, Optional

from .base import TracerBase


class NullTracer(TracerBase):
    """
    No-op tracer that does nothing.

    Use this when you want to completely disable tracing.
    """

    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        pass

    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        pass

    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        pass

    def on_thinking(self, thinking_text: str, is_partial: bool = False) -> None:
        pass

    def on_message(self, text: str, is_partial: bool = False) -> None:
        pass

    def on_error(self, error_message: str, error_type: str = "error") -> None:
        pass

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
        pass

    def on_output_display(
        self,
        output: Optional[str] = None,
        error: Optional[str] = None,
        comments: Optional[str] = None,
        result_files: Optional[list[str]] = None,
        status: Optional[str] = None
    ) -> None:
        pass

    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        pass

    def on_hook_triggered(
        self,
        hook_event: str,
        tool_name: Optional[str] = None,
        decision: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        pass

    def on_conversation_turn(
        self,
        turn_number: int,
        prompt_preview: str,
        response_preview: str,
        duration_ms: int,
        tools_used: list[str]
    ) -> None:
        pass

    def on_session_connect(self, session_id: Optional[str] = None) -> None:
        pass

    def on_session_disconnect(
        self,
        session_id: Optional[str] = None,
        total_turns: int = 0,
        total_duration_ms: int = 0
    ) -> None:
        pass

    def on_subagent_start(
        self,
        task_id: str,
        subagent_name: str,
        prompt: str
    ) -> None:
        pass

    def on_subagent_message(
        self,
        task_id: str,
        text: str,
        is_partial: bool = False
    ) -> None:
        pass

    def on_subagent_stop(
        self,
        task_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        pass
