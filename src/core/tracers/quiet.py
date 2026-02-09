"""
Quiet Tracer for Ag3ntum.

Minimal tracer that only logs errors and completion.
"""
from typing import Any, Optional

from ..output import format_duration
from .base import TracerBase


class QuietTracer(TracerBase):
    """
    Minimal tracer that only logs errors and completion.

    Use this when you want minimal console output.
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
        """Silent start."""
        pass

    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        """Silent tool start."""
        pass

    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Only report errors."""
        if is_error:
            print(f"[ERROR] {tool_name}: {result}")

    def on_thinking(self, thinking_text: str, is_partial: bool = False) -> None:
        """Silent thinking."""
        pass

    def on_message(self, text: str, is_partial: bool = False) -> None:
        """Silent message."""
        pass

    def on_error(self, error_message: str, error_type: str = "error") -> None:
        """Report errors."""
        print(f"[{error_type.upper()}] {error_message}")

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
        """Report completion with token usage."""
        cost_str = f" (${total_cost_usd:.4f})" if total_cost_usd else ""
        tokens_str = ""
        if usage:
            total_tokens = (
                usage.get("input_tokens", 0) +
                usage.get("cache_creation_input_tokens", 0) +
                usage.get("cache_read_input_tokens", 0) +
                usage.get("output_tokens", 0)
            )
            tokens_str = f", {total_tokens:,} tokens"
        print(f"[{status}] Completed in {duration_ms}ms, {num_turns} turns{cost_str}{tokens_str}")

        # Show cumulative stats if this was a resumed session
        if cumulative_turns and cumulative_turns > num_turns:
            cumul_cost = f" ${cumulative_cost_usd:.4f}" if cumulative_cost_usd else ""
            cumul_tokens = f", {cumulative_tokens:,} tokens" if cumulative_tokens else ""
            print(f"[SESSION TOTAL] {cumulative_turns} turns{cumul_cost}{cumul_tokens}")

    def on_output_display(
        self,
        output: Optional[str] = None,
        error: Optional[str] = None,
        comments: Optional[str] = None,
        result_files: Optional[list[str]] = None,
        status: Optional[str] = None
    ) -> None:
        """Display output summary in quiet mode."""
        if error and error.strip():
            print(f"[ERROR] {error.strip()[:100]}")
        if output and output.strip():
            first_line = output.strip().split("\n")[0][:80]
            print(f"[OUTPUT] {first_line}")
        if result_files:
            print(f"[FILES] {len(result_files)} file(s): {', '.join(result_files[:3])}")

    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        """Report profile switch."""
        path_str = f" from {profile_path}" if profile_path else ""
        print(f"[PROFILE] {profile_type.upper()}: {profile_name} ({len(tools)} tools){path_str}")

    def on_hook_triggered(
        self,
        hook_event: str,
        tool_name: Optional[str] = None,
        decision: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        """Report hook trigger."""
        if decision in ("deny", "block"):
            parts = [f"[HOOK] {hook_event}"]
            if tool_name:
                parts.append(tool_name)
            if decision:
                parts.append(f"-> {decision}")
            print(" ".join(parts))

    def on_conversation_turn(
        self,
        turn_number: int,
        prompt_preview: str,
        response_preview: str,
        duration_ms: int,
        tools_used: list[str]
    ) -> None:
        """Report turn summary."""
        pass  # Quiet mode doesn't show turn details

    def on_session_connect(self, session_id: Optional[str] = None) -> None:
        """Report session connect."""
        pass

    def on_session_disconnect(
        self,
        session_id: Optional[str] = None,
        total_turns: int = 0,
        total_duration_ms: int = 0
    ) -> None:
        """Report session disconnect."""
        print(f"[SESSION] Ended: {total_turns} turns, {total_duration_ms}ms")

    def on_subagent_start(
        self,
        task_id: str,
        subagent_name: str,
        prompt: str
    ) -> None:
        """Report subagent start."""
        print(f"[SUBAGENT] {subagent_name}")

    def on_subagent_message(
        self,
        task_id: str,
        text: str,
        is_partial: bool = False
    ) -> None:
        """Silent — quiet mode skips subagent messages."""
        pass

    def on_subagent_stop(
        self,
        task_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Report subagent completion."""
        duration_str = format_duration(duration_ms)
        status = "failed" if is_error else "completed"
        print(f"[SUBAGENT] {status} ({duration_str})")
