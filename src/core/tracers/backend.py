"""
Backend Console Tracer for Ag3ntum.

Non-interactive, timestamped logging for backend/API execution.
"""
import logging
import time
from datetime import datetime
from typing import Any, Optional

from .base import TracerBase


class BackendConsoleTracer(TracerBase):
    """
    Backend tracer with linear, timestamped output.

    Designed for backend/API execution with:
    - Non-interactive output (no colors, no spinners)
    - Timestamped log lines with session context
    - Dual output: console (print) + Python logging
    - Major events only: agent start/complete, tool start/complete, errors

    Example output:
        [2026-01-04 10:15:32] [abc123] Agent started: claude-sonnet-4-20250514
        [2026-01-04 10:15:33] [abc123] Tool: read_file -> OK (45ms)
        [2026-01-04 10:15:40] [abc123] Agent completed: COMPLETE (8.2s, 3 turns, $0.0142)
    """

    def __init__(
        self,
        session_id: str = "",
        logger_name: str = "ag3ntum.agent"
    ) -> None:
        self.session_id = session_id or "unknown"
        self.logger = logging.getLogger(logger_name)
        self._start_time: Optional[float] = None
        self._tool_start_times: dict[str, float] = {}

    def _timestamp(self) -> str:
        """Get formatted timestamp."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _format_duration(self, ms: int) -> str:
        """Format duration in human-readable form."""
        if ms < 1000:
            return f"{ms}ms"
        seconds = ms / 1000
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.1f}s"

    def _format_cost(self, cost: float) -> str:
        """Format cost in USD."""
        if cost < 0.01:
            return f"${cost:.4f}"
        return f"${cost:.2f}"

    def _log(self, message: str, level: int = logging.INFO) -> None:
        """Output to both console and Python logging."""
        timestamp = self._timestamp()
        formatted = f"[{timestamp}] [{self.session_id}] {message}"
        print(formatted)
        self.logger.log(
            level,
            message,
            extra={"session_id": self.session_id}
        )

    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        """Log agent start."""
        self._start_time = time.time()
        if session_id:
            self.session_id = session_id
        self._log(f"Agent started: {model} ({len(tools)} tools)")

    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        """Log tool start."""
        self._tool_start_times[tool_id] = time.time()
        self._log(f"Tool: {tool_name} ...", level=logging.DEBUG)

    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Log tool completion."""
        if tool_id in self._tool_start_times:
            actual_ms = int((time.time() - self._tool_start_times[tool_id]) * 1000)
            duration_ms = actual_ms
            del self._tool_start_times[tool_id]

        duration_str = self._format_duration(duration_ms)
        status = "FAIL" if is_error else "OK"
        level = logging.ERROR if is_error else logging.INFO
        self._log(f"Tool: {tool_name} -> {status} ({duration_str})", level=level)

    def on_thinking(self, thinking_text: str, is_partial: bool = False) -> None:
        """Silent - thinking not logged in backend mode."""
        pass

    def on_message(self, text: str, is_partial: bool = False) -> None:
        """Silent - messages not logged in backend mode."""
        pass

    def on_error(self, error_message: str, error_type: str = "error") -> None:
        """Log errors."""
        self._log(f"{error_type.upper()}: {error_message}", level=logging.ERROR)

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
        """Log agent completion with metrics."""
        duration_str = self._format_duration(duration_ms)
        parts = [f"Agent completed: {status} ({duration_str}, {num_turns} turns"]

        if total_cost_usd is not None:
            parts.append(f", {self._format_cost(total_cost_usd)}")

        if usage:
            total_tokens = (
                usage.get("input_tokens", 0) +
                usage.get("cache_creation_input_tokens", 0) +
                usage.get("cache_read_input_tokens", 0) +
                usage.get("output_tokens", 0)
            )
            parts.append(f", {total_tokens:,} tokens")

        parts.append(")")
        self._log("".join(parts))

        if cumulative_turns and cumulative_turns > num_turns:
            cumul_parts = [f"Session total: {cumulative_turns} turns"]
            if cumulative_cost_usd is not None:
                cumul_parts.append(self._format_cost(cumulative_cost_usd))
            if cumulative_tokens is not None:
                cumul_parts.append(f"{cumulative_tokens:,} tokens")
            self._log(" | ".join(cumul_parts))

    def on_output_display(
        self,
        output: Optional[str] = None,
        error: Optional[str] = None,
        comments: Optional[str] = None,
        result_files: Optional[list[str]] = None,
        status: Optional[str] = None
    ) -> None:
        """Log output summary."""
        if error and error.strip():
            self._log(f"Output error: {error.strip()[:100]}", level=logging.ERROR)
        if output and output.strip():
            first_line = output.strip().split("\n")[0][:80]
            self._log(f"Output: {first_line}")
        if result_files:
            self._log(f"Result files: {len(result_files)} file(s)")

    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        """Log profile switch."""
        self._log(
            f"Profile: {profile_type.upper()} ({profile_name}, {len(tools)} tools)"
        )

    def on_hook_triggered(
        self,
        hook_event: str,
        tool_name: Optional[str] = None,
        decision: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        """Log hook triggers (only denials)."""
        if decision in ("deny", "block"):
            parts = [f"Hook: {hook_event}"]
            if tool_name:
                parts.append(f"[{tool_name}]")
            if decision:
                parts.append(f"-> {decision}")
            self._log(" ".join(parts), level=logging.WARNING)

    def on_conversation_turn(
        self,
        turn_number: int,
        prompt_preview: str,
        response_preview: str,
        duration_ms: int,
        tools_used: list[str]
    ) -> None:
        """Silent - turn details not logged in backend mode."""
        pass

    def on_session_connect(self, session_id: Optional[str] = None) -> None:
        """Log session connect."""
        if session_id:
            self.session_id = session_id
        self._log("Session connected")

    def on_session_disconnect(
        self,
        session_id: Optional[str] = None,
        total_turns: int = 0,
        total_duration_ms: int = 0
    ) -> None:
        """Log session disconnect."""
        duration_str = self._format_duration(total_duration_ms)
        self._log(f"Session ended: {total_turns} turns, {duration_str}")

    def on_subagent_start(
        self,
        task_id: str,
        subagent_name: str,
        prompt: str
    ) -> None:
        """Log subagent start."""
        self._log(f"Subagent: {subagent_name} started")

    def on_subagent_message(
        self,
        task_id: str,
        text: str,
        is_partial: bool = False
    ) -> None:
        """Silent - subagent messages not logged in backend mode."""
        pass

    def on_subagent_stop(
        self,
        task_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Log subagent completion."""
        duration_str = self._format_duration(duration_ms)
        status = "FAIL" if is_error else "OK"
        level = logging.ERROR if is_error else logging.INFO
        self._log(f"Subagent: completed -> {status} ({duration_str})", level=level)
