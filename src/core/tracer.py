"""
Execution Tracer for Ag3ntum.

Provides real-time, fashionable console output for agent execution tracing
with spinners, colors, and dynamic content updates.

Uses shared constants and utilities from constants.py and output.py
for consistent styling across all terminal output.

Usage:
    tracer = ExecutionTracer(verbose=True)

    # Hook into agent execution
    tracer.on_agent_start(session_id, model, tools)
    tracer.on_tool_start(tool_name, tool_input)
    tracer.on_tool_complete(tool_name, result, duration_ms, is_error)
    tracer.on_thinking(thinking_text)
    tracer.on_message(text)
    tracer.on_error(error_message)
    tracer.on_agent_complete(result_message)
"""
import asyncio
import json
import logging
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .constants import (
    AnsiColors,
    BoxChars,
    JSON_PREVIEW_MAX_LINE_LENGTH,
    JSON_PREVIEW_MAX_LINES,
    MESSAGE_PREVIEW_LENGTH,
    PATH_TRUNCATE_LENGTH,
    StatusIcons,
    TerminalControl,
    TODO_CONTENT_MAX_LENGTH,
    TODO_PLAN_INDENT,
    TOOL_GRID_COLUMN_WIDTH,
    TOOL_GRID_COLUMNS,
)
from .output import (
    format_cost,
    format_duration,
    get_terminal_width,
    is_tty,
    print_output_box,
    truncate_path,
    truncate_text,
    wrap_text,
)
from .schemas import get_model_context_size
from .structured_output import normalize_error_value, parse_structured_output

# Import security scanner (lazy import to avoid circular dependencies)
# Used by EventingTracer for post-completion security scanning


# Type aliases for backward compatibility
# Use AnsiColors from constants as the canonical Color type
Color = AnsiColors


class Symbol:
    """
    Symbol constants for terminal decoration.

    Wraps BoxChars and StatusIcons from constants.py for backward compatibility.
    New code should import directly from constants.py.
    """
    # Box drawing (from BoxChars)
    BOX_H = BoxChars.HORIZONTAL
    BOX_V = BoxChars.VERTICAL
    BOX_TL = BoxChars.TOP_LEFT
    BOX_TR = BoxChars.TOP_RIGHT
    BOX_BL = BoxChars.BOTTOM_LEFT
    BOX_BR = BoxChars.BOTTOM_RIGHT
    BOX_T = BoxChars.TOP_T
    BOX_B = BoxChars.BOTTOM_T
    BOX_L = BoxChars.LEFT_T
    BOX_R = BoxChars.RIGHT_T
    BOX_X = BoxChars.CROSS

    # Double box drawing
    DBOX_H = BoxChars.DOUBLE_HORIZONTAL
    DBOX_V = BoxChars.DOUBLE_VERTICAL
    DBOX_TL = BoxChars.DOUBLE_TOP_LEFT
    DBOX_TR = BoxChars.DOUBLE_TOP_RIGHT
    DBOX_BL = BoxChars.DOUBLE_BOTTOM_LEFT
    DBOX_BR = BoxChars.DOUBLE_BOTTOM_RIGHT

    # Status indicators (from StatusIcons)
    CHECK = StatusIcons.SUCCESS
    CROSS = StatusIcons.FAILURE
    WARN = StatusIcons.WARNING
    INFO = StatusIcons.INFO
    BULLET = StatusIcons.BULLET
    CIRCLE = StatusIcons.CIRCLE
    CIRCLE_FILLED = StatusIcons.CIRCLE_FILLED
    STAR = StatusIcons.STAR
    LIGHTNING = StatusIcons.LIGHTNING
    POINTER = StatusIcons.POINTER
    GEAR = StatusIcons.GEAR
    FOLDER = StatusIcons.FOLDER
    FILE = StatusIcons.FILE
    CLOCK = StatusIcons.CLOCK
    BRAIN = StatusIcons.BRAIN
    ARROW_RIGHT = StatusIcons.ARROW_RIGHT
    ARROW_LEFT = StatusIcons.ARROW_LEFT
    ARROW_UP = StatusIcons.ARROW_UP
    ARROW_DOWN = StatusIcons.ARROW_DOWN
    TRIANGLE_RIGHT = StatusIcons.TRIANGLE_RIGHT
    TRIANGLE_DOWN = StatusIcons.TRIANGLE_DOWN

    # Spinners
    SPINNER_DOTS = list(StatusIcons.SPINNER)
    SPINNER_LINE = list(StatusIcons.SPINNER_LINE)
    SPINNER_ARROW = list(StatusIcons.SPINNER_ARROW)
    SPINNER_PULSE_CIRCLE = list(StatusIcons.SPINNER_PULSE_CIRCLE)

    # Additional symbols not in constants (tracer-specific)
    TOOL = StatusIcons.GEAR
    ROCKET = "»"
    MONEY = "◈"
    SPINNER_BOUNCE = ["⠁", "⠂", "⠄", "⠂"]
    SPINNER_PULSE = ["◐", "◓", "◑", "◒"]


@dataclass
class SpinnerState:
    """State for spinner animation."""
    active: bool = False
    message: str = ""
    frames: list[str] = field(default_factory=lambda: Symbol.SPINNER_DOTS)
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

    # ═══════════════════════════════════════════════════════════════
    # Hooks-aware tracing methods (new for ConversationSession)
    # ═══════════════════════════════════════════════════════════════

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

    # ═══════════════════════════════════════════════════════════════
    # Subagent tracing methods (for Task tool)
    # ═══════════════════════════════════════════════════════════════

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


class ExecutionTracer(TracerBase):
    """
    Fashionable console tracer for Ag3ntum execution.

    Provides rich terminal output with:
    - Colored status indicators
    - Animated spinners for ongoing operations
    - Box-drawn sections for clarity
    - Dynamic line updates
    - Timing and cost information

    Uses shared formatting utilities from output.py for consistent styling.

    Args:
        verbose: Show detailed output including tool parameters.
        show_thinking: Display thinking block content.
        max_preview_length: Maximum characters for text previews.
        use_colors: Enable ANSI colors (auto-detect TTY).
        use_unicode: Enable Unicode symbols (fallback to ASCII).
    """

    def __init__(
        self,
        verbose: bool = True,
        show_thinking: bool = True,
        max_preview_length: int = MESSAGE_PREVIEW_LENGTH,
        use_colors: bool = True,
        use_unicode: bool = True
    ) -> None:
        self.verbose = verbose
        self.show_thinking = show_thinking
        self.max_preview_length = max_preview_length
        self.use_colors = use_colors and is_tty()
        self.use_unicode = use_unicode

        self._spinner = SpinnerState()
        self._start_time: Optional[float] = None
        self._tool_start_times: dict[str, float] = {}
        self._turn_count = 0
        self._lock = threading.Lock()
        self._current_model: str = ""

        # Use shared terminal width detection from output.py
        self._console_width = get_terminal_width()

        # Track agent start state and stored profile info
        self._agent_started: bool = False
        self._pending_profile: Optional[dict[str, Any]] = None

    def _get_short_model_id(self, model: str) -> str:
        """
        Extract a short model identifier for display.
        """
        # Fallback: return last segment or truncated model name
        parts = model.split("/")[-1].split(":")
        short = parts[0]
        if len(short) > 20:
            short = short[:17] + "..."
        return short

    # ═══════════════════════════════════════════════════════════════
    # Formatting Helpers
    # ═══════════════════════════════════════════════════════════════

    def _color(self, text: str, *colors: Color) -> str:
        """Apply color codes to text if colors are enabled."""
        if not self.use_colors:
            return text
        color_codes = "".join(str(c) for c in colors)
        return f"{color_codes}{text}{Color.RESET}"

    def _symbol(self, unicode_sym: str, ascii_fallback: str = "") -> str:
        """Return Unicode symbol or ASCII fallback."""
        if self.use_unicode:
            return unicode_sym
        return ascii_fallback or unicode_sym[0] if unicode_sym else ""

    def _timestamp(self) -> str:
        """Get formatted timestamp."""
        return datetime.now().strftime("%H:%M:%S")

    def _elapsed(self) -> str:
        """Get elapsed time since start."""
        if self._start_time is None:
            return "0.0s"
        elapsed = time.time() - self._start_time
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        minutes = int(elapsed // 60)
        seconds = elapsed % 60
        return f"{minutes}m {seconds:.1f}s"

    def _truncate(self, text: str, max_len: Optional[int] = None) -> str:
        """
        Truncate text with ellipsis using shared utility.

        Args:
            text: Text to truncate.
            max_len: Maximum length (defaults to self.max_preview_length).

        Returns:
            Truncated text.
        """
        max_len = max_len or self.max_preview_length
        return truncate_text(text, max_len)

    def _truncate_path(self, path: str, max_len: int = PATH_TRUNCATE_LENGTH) -> str:
        """
        Truncate a path intelligently using shared utility.

        Args:
            path: File path to truncate.
            max_len: Maximum display length.

        Returns:
            Truncated path string.
        """
        return truncate_path(path, max_len)

    def _format_tool_name(self, tool_name: str) -> str:
        """
        Format tool name for display.

        Converts MCP tool prefixes to readable format:
        - mcp__ag3ntum__Bash -> Ag3ntumBash

        Args:
            tool_name: Raw tool name from SDK.

        Returns:
            Formatted display name.
        """
        prefix = "mcp__ag3ntum__"
        if tool_name.startswith(prefix):
            suffix = tool_name[len(prefix):]
            return f"Ag3ntum{suffix}"
        return tool_name

    def _is_path_like(self, key: str, value: str) -> bool:
        """Check if a key-value pair looks like a file path."""
        path_keys = {"file_path", "path", "filepath", "directory", "dir", "folder", "cwd"}
        if key.lower() in path_keys:
            return True
        # Also check if value looks like a path
        if isinstance(value, str) and (value.startswith("/") or value.startswith("~/")):
            return True
        return False

    def _char_width(self, char: str) -> int:
        """Get visual width of a single character."""
        code = ord(char)
        # Wide character ranges (emojis, CJK, etc.) - 2 char width
        wide_ranges = [
            (0x1100, 0x115F),    # Hangul Jamo
            (0x231A, 0x231B),    # Watch, Hourglass
            (0x23E9, 0x23F3),    # Various symbols
            (0x23F8, 0x23FA),    # Various symbols
            # Note: 0x25AA-0x25AB (▪▫) are single-width geometric shapes
            (0x25B6, 0x25B6),    # Play button
            (0x25C0, 0x25C0),    # Reverse button
            (0x25FB, 0x25FE),    # Squares
            (0x2614, 0x2615),    # Umbrella, Hot beverage
            (0x2648, 0x2653),    # Zodiac
            (0x267F, 0x267F),    # Wheelchair
            (0x2693, 0x2693),    # Anchor
            (0x26A1, 0x26A1),    # High voltage
            (0x26AA, 0x26AB),    # Circles
            (0x26BD, 0x26BE),    # Soccer, Baseball
            (0x26C4, 0x26C5),    # Snowman, Sun
            (0x26CE, 0x26CE),    # Ophiuchus
            (0x26D4, 0x26D4),    # No entry
            (0x26EA, 0x26EA),    # Church
            (0x26F2, 0x26F3),    # Fountain, Golf
            (0x26F5, 0x26F5),    # Sailboat
            (0x26FA, 0x26FA),    # Tent
            (0x26FD, 0x26FD),    # Fuel pump
            (0x2702, 0x2702),    # Scissors
            (0x2705, 0x2705),    # White check mark ✅
            (0x2708, 0x270D),    # Airplane to Writing hand
            (0x270F, 0x270F),    # Pencil
            (0x2712, 0x2712),    # Black nib
            (0x2714, 0x2714),    # Check mark ✔
            (0x2716, 0x2716),    # X mark ✖
            (0x271D, 0x271D),    # Latin cross
            (0x2721, 0x2721),    # Star of David
            (0x2728, 0x2728),    # Sparkles
            (0x2733, 0x2734),    # Eight spoked asterisk
            (0x2744, 0x2744),    # Snowflake
            (0x2747, 0x2747),    # Sparkle
            (0x274C, 0x274C),    # Cross mark ❌
            (0x274E, 0x274E),    # Cross mark
            (0x2753, 0x2755),    # Question marks
            (0x2757, 0x2757),    # Exclamation mark
            (0x2763, 0x2764),    # Heart exclamation, Heart
            (0x2795, 0x2797),    # Plus, Minus, Division
            (0x27A1, 0x27A1),    # Right arrow
            (0x27B0, 0x27B0),    # Curly loop
            (0x27BF, 0x27BF),    # Double curly loop
            (0x2934, 0x2935),    # Arrows
            (0x2E80, 0x9FFF),    # CJK
            (0xF900, 0xFAFF),    # CJK Compatibility
            (0x1F000, 0x1F02F),  # Mahjong
            (0x1F0A0, 0x1F0FF),  # Playing Cards
            (0x1F100, 0x1F1FF),  # Enclosed Alphanumeric Supplement (flags)
            (0x1F200, 0x1F2FF),  # Enclosed Ideographic Supplement
            (0x1F300, 0x1F5FF),  # Misc Symbols and Pictographs (🌀-🗿)
            (0x1F600, 0x1F64F),  # Emoticons (😀-🙏)
            (0x1F680, 0x1F6FF),  # Transport and Map Symbols (🚀 etc)
            (0x1F700, 0x1F77F),  # Alchemical Symbols
            (0x1F780, 0x1F7FF),  # Geometric Shapes Extended
            (0x1F800, 0x1F8FF),  # Supplemental Arrows-C
            (0x1F900, 0x1F9FF),  # Supplemental Symbols (🤖 etc)
            (0x1FA00, 0x1FA6F),  # Chess Symbols
            (0x1FA70, 0x1FAFF),  # Symbols and Pictographs Extended-A
        ]

        for start, end in wide_ranges:
            if start <= code <= end:
                return 2

        # Variation selectors (invisible, zero width)
        if 0xFE00 <= code <= 0xFE0F:
            return 0

        return 1

    def _visual_width(self, text: str) -> int:
        """
        Calculate visual width of text, accounting for wide characters (emojis).
        """
        return sum(self._char_width(char) for char in text)

    def _truncate_to_visual_width(self, text: str, max_width: int) -> str:
        """Truncate text to fit within a visual width, accounting for wide chars."""
        if self._visual_width(text) <= max_width:
            return text

        result = []
        current_width = 0
        for char in text:
            char_width = self._char_width(char)
            if current_width + char_width > max_width - 3:  # Leave room for "..."
                break
            result.append(char)
            current_width += char_width

        return "".join(result) + "..."

    def _format_json_preview(
        self,
        value: Any,
        max_lines: int = JSON_PREVIEW_MAX_LINES,
        max_line_length: int = JSON_PREVIEW_MAX_LINE_LENGTH,
        indent: int = 2
    ) -> list[str]:
        """
        Format a JSON-like object as pretty-printed preview lines.

        Args:
            value: The value to format (dict, list, or other).
            max_lines: Maximum number of lines to return.
            max_line_length: Maximum length per line.
            indent: Indentation spaces for JSON.

        Returns:
            List of formatted lines.
        """
        try:
            if isinstance(value, (dict, list)):
                formatted = json.dumps(value, indent=indent, ensure_ascii=False)
            else:
                formatted = str(value)
        except (TypeError, ValueError):
            formatted = str(value)

        lines = formatted.split("\n")
        result = []

        for i, line in enumerate(lines):
            if i >= max_lines:
                remaining = len(lines) - max_lines
                result.append(f"... +{remaining} more lines")
                break

            if len(line) > max_line_length:
                line = line[:max_line_length - 3] + "..."
            result.append(line)

        return result

    def _format_todo_plan(
        self,
        todos: list[dict[str, Any]],
        indent: int = TODO_PLAN_INDENT
    ) -> list[str]:
        """
        Format todos as a plan-like tree with:
        - Completed items dimmed
        - In-progress items bold
        - Pending items normal
        - Shows: previous completed, current, next 2, then <...>

        Args:
            todos: List of todo items with 'content' and 'status' fields.
            indent: Number of spaces for base indentation.

        Returns:
            List of formatted lines to print.
        """
        if not todos:
            return []

        lines: list[str] = []
        bar = self._symbol(Symbol.BOX_V, "|")
        branch = self._symbol(Symbol.BOX_L, "|-")
        last_branch = self._symbol(Symbol.BOX_BL, "`-")

        # Find current in-progress item index
        current_idx = -1
        for i, todo in enumerate(todos):
            status = todo.get("status", "pending")
            if status == "in_progress":
                current_idx = i
                break

        # Determine which items to show:
        # - One previous completed (if exists)
        # - Current in_progress
        # - Next 2 pending
        # - <...> if more exist

        if current_idx == -1:
            # No in_progress - show first few items
            start_idx = 0
            end_idx = min(4, len(todos))
            show_ellipsis = len(todos) > 4
        else:
            # Find the last completed item before current
            prev_completed_idx = -1
            for i in range(current_idx - 1, -1, -1):
                if todos[i].get("status") == "completed":
                    prev_completed_idx = i
                    break

            # Calculate visible range
            start_idx = prev_completed_idx if prev_completed_idx >= 0 else current_idx
            end_idx = min(current_idx + 3, len(todos))  # current + next 2

            # Check if there are hidden items before
            hidden_before = start_idx > 0
            show_ellipsis = end_idx < len(todos)

            if hidden_before:
                lines.append(
                    f"{' ' * indent}{self._color(bar, Color.DIM)} "
                    f"{self._color('<...>', Color.DIM)}"
                )

        # Status symbols
        status_symbols = {
            "completed": self._symbol(Symbol.CHECK, "v"),
            "in_progress": self._symbol(Symbol.POINTER, ">"),
            "pending": self._symbol(Symbol.CIRCLE, "o"),
            "cancelled": self._symbol(Symbol.CROSS, "x"),
        }

        for i in range(start_idx, end_idx):
            todo = todos[i]
            is_last = (i == end_idx - 1) and not show_ellipsis
            connector = last_branch if is_last else branch

            status = todo.get("status", "pending")
            content = todo.get("content", "")

            # Truncate long content using constant
            if len(content) > TODO_CONTENT_MAX_LENGTH:
                content = content[:TODO_CONTENT_MAX_LENGTH - 3] + "..."

            # Format based on status
            sym = status_symbols.get(status, self._symbol(Symbol.CIRCLE, "o"))

            if status == "completed":
                # Dimmed for completed
                line = (
                    f"{' ' * indent}{self._color(bar, Color.DIM)} "
                    f"{self._color(connector, Color.DIM)} "
                    f"{self._color(sym, Color.DIM)} "
                    f"{self._color(content, Color.DIM)}"
                )
            elif status == "in_progress":
                # Bold for in-progress
                line = (
                    f"{' ' * indent}{self._color(bar, Color.DIM)} "
                    f"{self._color(connector, Color.BRIGHT_CYAN)} "
                    f"{self._color(sym, Color.BRIGHT_CYAN, Color.BOLD)} "
                    f"{self._color(content, Color.WHITE, Color.BOLD)}"
                )
            else:
                # Normal for pending
                line = (
                    f"{' ' * indent}{self._color(bar, Color.DIM)} "
                    f"{self._color(connector, Color.DIM)} "
                    f"{self._color(sym, Color.WHITE)} "
                    f"{self._color(content, Color.WHITE)}"
                )

            lines.append(line)

        # Add ellipsis if more items exist
        if show_ellipsis:
            remaining = len(todos) - end_idx
            lines.append(
                f"{' ' * indent}{self._color(bar, Color.DIM)} "
                f"{self._color(last_branch, Color.DIM)} "
                f"{self._color(f'<... {remaining} more>', Color.DIM)}"
            )

        return lines

    def _format_duration(self, ms: int) -> str:
        """Format duration using shared utility."""
        return format_duration(ms)

    def _format_cost(self, cost: float) -> str:
        """Format cost using shared utility."""
        return format_cost(cost)

    # ═══════════════════════════════════════════════════════════════
    # Output Methods
    # ═══════════════════════════════════════════════════════════════

    def _write(self, text: str, end: str = "\n") -> None:
        """Thread-safe write to stdout."""
        with self._lock:
            sys.stdout.write(text + end)
            sys.stdout.flush()

    def _clear_line(self) -> None:
        """Clear current line."""
        if self.use_colors:
            self._write(TerminalControl.CLEAR_LINE + TerminalControl.CURSOR_START, end="")

    def _print_box(
        self,
        lines: list[str],
        title: Optional[str] = None,
        color: Color = Color.DIM,
        title_color: Optional[Color] = None,
        border_color: Optional[Color] = None,
        width: Optional[int] = None,
        center_title: bool = False
    ) -> None:
        """
        Print content inside a box with single-line borders.

        Args:
            lines: List of content lines to display inside the box.
            title: Optional title for the box header.
            color: Color for default content.
            title_color: Color for the title (defaults to color).
            border_color: Color for all borders (defaults to color).
            width: Box width (defaults to console width - 4 for margins).
            center_title: Whether to center the title text.
        """
        width = width or (self._console_width - 4)
        title_color = title_color or color
        border_color = border_color or color
        inner_width = width - 2  # Subtract 2 for border chars

        # Box drawing chars (single line)
        tl = self._symbol(Symbol.BOX_TL, "+")
        tr = self._symbol(Symbol.BOX_TR, "+")
        bl = self._symbol(Symbol.BOX_BL, "+")
        br = self._symbol(Symbol.BOX_BR, "+")
        h = self._symbol(Symbol.BOX_H, "-")
        v = self._symbol(Symbol.BOX_V, "|")
        lt = self._symbol(Symbol.BOX_L, "+")
        rt = self._symbol(Symbol.BOX_R, "+")

        # Top border
        self._write(self._color(f"{tl}{h * inner_width}{tr}", border_color))

        # Title line (if provided)
        if title:
            title_visual_width = self._visual_width(title)
            if center_title:
                # Center the title, accounting for visual width
                left_pad = (inner_width - title_visual_width) // 2
                right_pad = inner_width - title_visual_width - left_pad
                title_padded = " " * left_pad + title + " " * right_pad
            else:
                title_content = f" {title}"
                title_visual = self._visual_width(title_content)
                title_padded = title_content + " " * (inner_width - title_visual)

            self._write(
                self._color(v, border_color) +
                self._color(title_padded, title_color, Color.BOLD) +
                self._color(v, border_color)
            )
            # Separator after title
            self._write(self._color(f"{lt}{h * inner_width}{rt}", border_color))

        # Content lines
        for line in lines:
            # Truncate if too long (accounting for wide characters)
            visual_len = self._visual_width(line)
            if visual_len > inner_width:
                line = self._truncate_to_visual_width(line, inner_width)
                visual_len = self._visual_width(line)

            # Pad to inner_width (accounting for visual width)
            padding_needed = inner_width - visual_len
            line_padded = line + " " * max(0, padding_needed)

            self._write(
                self._color(v, border_color) +
                line_padded +
                self._color(v, border_color)
            )

        # Bottom border
        self._write(self._color(f"{bl}{h * inner_width}{br}", border_color))

    def print_task(self, task: str) -> None:
        """
        Print the task description in a box (first 5 lines).

        Args:
            task: The task description text.
        """
        # Use full console width
        box_width = self._console_width - 2
        inner_width = box_width - 4  # Account for box borders and padding

        # Split into lines and wrap long lines
        task_lines: list[str] = []

        for line in task.strip().split("\n"):
            if len(line) <= inner_width:
                task_lines.append(f" {line}")
            else:
                # Wrap long lines
                wrapped = self._wrap_text(line, inner_width)
                task_lines.extend(f" {w}" for w in wrapped)

        # Limit to 5 lines
        display_lines = task_lines[:5]
        if len(task_lines) > 5:
            display_lines.append(f" ... +{len(task_lines) - 5} more lines")

        self._print_box(
            lines=display_lines,
            title="TASK",
            color=Color.WHITE,
            title_color=Color.BRIGHT_WHITE,
            border_color=Color.DIM,
            width=box_width,
            center_title=False
        )
        self._write("")

    def _print_header(self, title: str, color: Color = Color.BRIGHT_CYAN) -> None:
        """Print a decorated header with single-line box drawing."""
        width = self._console_width - 2  # Full console width with small margin
        inner_width = width - 2

        tl = self._symbol(Symbol.BOX_TL, "+")
        tr = self._symbol(Symbol.BOX_TR, "+")
        bl = self._symbol(Symbol.BOX_BL, "+")
        br = self._symbol(Symbol.BOX_BR, "+")
        h = self._symbol(Symbol.BOX_H, "-")
        v = self._symbol(Symbol.BOX_V, "|")

        # Build title content
        star = self._symbol(Symbol.STAR, "*")
        title_content = f" {star} {title}"
        title_padded = title_content.ljust(inner_width)

        self._write("")
        self._write(self._color(f"{tl}{h * inner_width}{tr}", color))
        self._write(
            self._color(v, color) +
            self._color(title_padded, color, Color.BOLD) +
            self._color(v, color)
        )
        self._write(self._color(f"{bl}{h * inner_width}{br}", color))

    def _print_footer(self, color: Color = Color.BRIGHT_CYAN) -> None:
        """Print a decorated footer."""
        width = 60
        border = self._symbol(Symbol.DBOX_H, "=") * width
        self._write(self._color(border, color))
        self._write("")

    def _print_line(
        self,
        prefix: str,
        message: str,
        color: Color = Color.WHITE,
        prefix_color: Optional[Color] = None,
        indent: int = 0
    ) -> None:
        """Print a formatted line with prefix."""
        prefix_color = prefix_color or color
        indent_str = "  " * indent
        bar = self._symbol(Symbol.BOX_V, "|")

        formatted = (
            f"{indent_str}"
            f"{self._color(bar, Color.DIM)} "
            f"{self._color(prefix, prefix_color, Color.BOLD)} "
            f"{self._color(message, color)}"
        )
        self._write(formatted)

    def _print_key_value(
        self,
        key: str,
        value: str,
        color: Color = Color.WHITE,
        indent: int = 1
    ) -> None:
        """Print a key-value pair."""
        indent_str = "  " * indent
        bar = self._symbol(Symbol.BOX_V, "|")
        bullet = self._symbol(Symbol.BULLET, "-")

        formatted = (
            f"{indent_str}"
            f"{self._color(bar, Color.DIM)} "
            f"{self._color(bullet, Color.DIM)} "
            f"{self._color(key + ':', Color.DIM)} "
            f"{self._color(value, color)}"
        )
        self._write(formatted)

    # ═══════════════════════════════════════════════════════════════
    # Spinner Control
    # ═══════════════════════════════════════════════════════════════

    def _start_spinner(
        self,
        message: str,
        frames: Optional[list[str]] = None,
        pre_colored: bool = False
    ) -> None:
        """Start an animated spinner.

        Args:
            message: Text to display next to the spinner
            frames: List of frame characters for animation
            pre_colored: If True, frames already contain ANSI color codes
        """
        if not self.use_colors or not sys.stdout.isatty():
            self._write(f"  {self._symbol(Symbol.GEAR, '*')} {message}...")
            return

        self._stop_spinner()

        self._spinner.active = True
        self._spinner.message = message
        self._spinner.frames = frames or Symbol.SPINNER_DOTS
        self._spinner.frame_index = 0
        self._spinner.stop_event = threading.Event()
        stop_event = self._spinner.stop_event  # Capture for thread safety
        is_pre_colored = pre_colored  # Capture for thread safety

        def spin() -> None:
            while not stop_event.is_set():
                frame = self._spinner.frames[self._spinner.frame_index]
                self._spinner.frame_index = (
                    (self._spinner.frame_index + 1) % len(self._spinner.frames)
                )

                with self._lock:
                    sys.stdout.write(TerminalControl.CLEAR_LINE)
                    sys.stdout.write(TerminalControl.CURSOR_START)
                    # Use frame as-is if pre-colored, otherwise wrap with color
                    frame_display = frame if is_pre_colored else self._color(frame, Color.CYAN)
                    sys.stdout.write(
                        f"  {frame_display} "
                        f"{self._color(self._spinner.message, Color.DIM)}"
                    )
                    sys.stdout.flush()

                time.sleep(0.08)

        self._spinner.thread = threading.Thread(target=spin, daemon=True)
        self._spinner.thread.start()

    def _stop_spinner(self, final_message: str = "", success: bool = True) -> None:
        """Stop the spinner with optional final message."""
        if not self._spinner.active:
            return

        if self._spinner.stop_event:
            self._spinner.stop_event.set()
        if self._spinner.thread:
            self._spinner.thread.join(timeout=0.5)

        self._spinner.active = False

        if self.use_colors and sys.stdout.isatty():
            with self._lock:
                sys.stdout.write(TerminalControl.CLEAR_LINE)
                sys.stdout.write(TerminalControl.CURSOR_START)

                if final_message:
                    symbol = Symbol.CHECK if success else Symbol.CROSS
                    color = Color.GREEN if success else Color.RED
                    sys.stdout.write(
                        f"  {self._color(symbol, color)} "
                        f"{self._color(final_message, color)}\n"
                    )
                sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════════
    # TracerBase Implementation
    # ═══════════════════════════════════════════════════════════════

    def _format_tools_grid(
        self,
        tools: list[str],
        columns: int = TOOL_GRID_COLUMNS,
        col_width: int = TOOL_GRID_COLUMN_WIDTH
    ) -> list[str]:
        """Format tools into a neat grid layout."""
        lines = []
        for i in range(0, len(tools), columns):
            row_tools = tools[i:i + columns]
            row = "  ".join(tool.ljust(col_width)[:col_width] for tool in row_tools)
            lines.append(row)
        return lines

    def _wrap_text(self, text: str, width: int = 70) -> list[str]:
        """Wrap text using shared utility."""
        return wrap_text(text, width)

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
        self._start_time = time.time()
        self._turn_count = 0
        self._current_model = model
        self._agent_started = True

        self._print_header(
            "Ag3ntum | Self-Improving Agent",
            Color.BRIGHT_CYAN
        )

        bar = self._symbol(Symbol.BOX_V, "|")

        # Session info with consistent indentation
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.LIGHTNING, '*'), Color.BRIGHT_CYAN)} "
            f"{self._color('SESSION', Color.BRIGHT_CYAN, Color.BOLD)} "
            f"{self._color(session_id, Color.CYAN)}"
        )

        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
            f"{self._color('Model:', Color.DIM)} "
            f"{self._color(model, Color.BRIGHT_WHITE)}"
        )

        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
            f"{self._color('Working Dir:', Color.DIM)} "
            f"{self._color(self._truncate_path(working_dir, 60), Color.DIM)}"
        )

        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
            f"{self._color('Started:', Color.DIM)} "
            f"{self._color(self._timestamp(), Color.DIM)}"
        )

        self._write(f"  {self._color(bar, Color.DIM)}")

        # Display permission profile info if available (from pending profile switch)
        if self._pending_profile:
            profile_type = self._pending_profile["profile_type"]
            profile_name = self._pending_profile["profile_name"]
            profile_tools = self._pending_profile["tools"]
            allow_count = self._pending_profile["allow_rules_count"]
            deny_count = self._pending_profile["deny_rules_count"]
            profile_path = self._pending_profile.get("profile_path")

            # Choose color based on profile type
            if profile_type.lower() == "system":
                profile_color = Color.BRIGHT_MAGENTA
            else:
                profile_color = Color.BRIGHT_GREEN

            # Profile line
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.STAR, '*'), profile_color)} "
                f"{self._color('Profile:', Color.DIM)} "
                f"{self._color(profile_type.upper(), profile_color, Color.BOLD)} "
                f"{self._color(f'({profile_name})', Color.DIM)} "
                f"{self._color(f'[allow={allow_count}, deny={deny_count}]', Color.DIM)}"
            )

            # Profile path
            if profile_path:
                # Shorten the path for display
                path_display = profile_path
                if len(path_display) > 50:
                    path_display = "..." + path_display[-47:]
                self._write(
                    f"  {self._color(bar, Color.DIM)} "
                    f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
                    f"{self._color('Loaded:', Color.DIM)} "
                    f"{self._color(path_display, Color.DIM)}"
                )

            self._write(f"  {self._color(bar, Color.DIM)}")

            # Tools section with grid layout - use profile tools
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.GEAR, '*'), Color.BRIGHT_WHITE)} "
                f"{self._color('Tools:', Color.DIM)} "
                f"{self._color(str(len(profile_tools)), Color.BRIGHT_WHITE)} "
                f"{self._color('available', Color.DIM)}"
            )

            tool_lines = self._format_tools_grid(profile_tools, columns=4, col_width=18)
            for line in tool_lines:
                self._write(
                    f"  {self._color(bar, Color.DIM)}   "
                    f"{self._color(line, Color.DIM)}"
                )

            # Clear pending profile
            self._pending_profile = None
        else:
            # No profile info - display tools from on_agent_start args
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.GEAR, '*'), Color.BRIGHT_WHITE)} "
                f"{self._color('Tools:', Color.DIM)} "
                f"{self._color(str(len(tools)), Color.BRIGHT_WHITE)} "
                f"{self._color('available', Color.DIM)}"
            )

            tool_lines = self._format_tools_grid(tools, columns=4, col_width=18)
            for line in tool_lines:
                self._write(
                    f"  {self._color(bar, Color.DIM)}   "
                    f"{self._color(line, Color.DIM)}"
                )

        # Print loaded skills if any
        if skills:
            self._write(f"  {self._color(bar, Color.DIM)}")
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.STAR, '*'), Color.BRIGHT_MAGENTA)} "
                f"{self._color('Skills:', Color.DIM)} "
                f"{self._color(str(len(skills)), Color.BRIGHT_MAGENTA)} "
                f"{self._color('loaded', Color.DIM)}"
            )
            skill_lines = self._format_tools_grid(skills, columns=4, col_width=16)
            for line in skill_lines:
                self._write(
                    f"  {self._color(bar, Color.DIM)}   "
                    f"{self._color(line, Color.MAGENTA)}"
                )

        # Bottom border
        bl = self._symbol(Symbol.BOX_BL, "+")
        h = self._symbol(Symbol.BOX_H, "-")
        self._write(self._color(f"  {bl}{h * 57}", Color.DIM))
        self._write("")

        # Print task if provided
        if task:
            self.print_task(task)

    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        """Called before a tool/skill is executed."""
        # Stop any existing spinner BEFORE writing new output to prevent
        # the old spinner thread from overwriting the new tool header
        self._stop_spinner()

        self._turn_count += 1
        self._tool_start_times[tool_id] = time.time()

        # Build tool display
        tool_icon = self._symbol(Symbol.TOOL, ">")
        turn_badge = self._color(f"[{self._turn_count}]", Color.DIM)

        display_name = self._format_tool_name(tool_name)
        self._write(
            f"  {self._color(tool_icon, Color.CYAN)} "
            f"{turn_badge} "
            f"{self._color(display_name, Color.BRIGHT_CYAN, Color.BOLD)}"
        )

        if self.verbose and tool_input:
            bar = self._color(Symbol.BOX_V, Color.DIM)

            # Special handling for TodoWrite - display as plan tree
            if tool_name == "TodoWrite" and "todos" in tool_input:
                todos = tool_input.get("todos", [])
                if isinstance(todos, str):
                    try:
                        todos = json.loads(todos)
                    except (json.JSONDecodeError, TypeError):
                        todos = []

                if isinstance(todos, list) and todos:
                    plan_lines = self._format_todo_plan(todos, indent=4)
                    for line in plan_lines:
                        self._write(line)
                # Skip the regular tool_input display for TodoWrite
            else:
                for key, value in tool_input.items():
                    # Check if value is a complex object (dict/list) or JSON-like string
                    is_complex = isinstance(value, (dict, list))
                    is_json_string = (
                        isinstance(value, str) and
                        len(value) > 50 and
                        (value.strip().startswith("{") or value.strip().startswith("["))
                    )

                    if is_complex or is_json_string:
                        # Pretty print JSON objects
                        self._write(
                            f"      {bar} {self._color(Symbol.BULLET, Color.DIM)} "
                            f"{self._color(key + ':', Color.DIM)}"
                        )

                        # Parse JSON string if needed
                        if is_json_string:
                            try:
                                value = json.loads(value)
                            except (json.JSONDecodeError, TypeError):
                                pass

                        # Format and print each line
                        json_lines = self._format_json_preview(
                            value, max_lines=10, max_line_length=80
                        )
                        for json_line in json_lines:
                            self._write(
                                f"      {bar}     "
                                f"{self._color(json_line, Color.WHITE)}"
                            )
                    else:
                        # Simple value - single line
                        value_str = str(value)
                        max_display_len = 80
                        if len(value_str) > max_display_len:
                            # Use smart path truncation for path-like values
                            if self._is_path_like(key, value_str):
                                value_str = self._truncate_path(value_str, max_display_len)
                            else:
                                value_str = value_str[:max_display_len - 3] + "..."
                        value_str = value_str.replace("\n", "\\n")
                        self._print_key_value(
                            key,
                            self._color(value_str, Color.WHITE),
                            Color.DIM,
                            indent=2
                        )

        self._start_spinner(
            f"Executing {display_name}...",
            frames=Symbol.SPINNER_PULSE_CIRCLE,
            pre_colored=True
        )

    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Called after a tool/skill completes."""
        # Calculate actual duration if we have start time
        if tool_id in self._tool_start_times:
            actual_ms = int((time.time() - self._tool_start_times[tool_id]) * 1000)
            duration_ms = actual_ms
            del self._tool_start_times[tool_id]

        duration_str = self._format_duration(duration_ms)

        if is_error:
            status_icon = self._symbol(Symbol.CROSS, "X")
            status_text = "FAILED"
        else:
            status_icon = self._symbol(Symbol.CHECK, "V")
            status_text = "OK"

        display_name = self._format_tool_name(tool_name)
        self._stop_spinner(
            f"{display_name} {status_icon} {status_text} "
            f"{self._color(f'({duration_str})', Color.DIM)}",
            success=not is_error
        )

        # Show result preview for verbose mode
        if self.verbose and result:
            result_str = str(result)
            if len(result_str) > 100:
                result_str = result_str[:97] + "..."
            result_str = result_str.replace("\n", " ")

            output_color = Color.RED if is_error else Color.DIM
            self._write(
                f"      {self._color(Symbol.BOX_L, Color.DIM)}"
                f"{self._color(Symbol.BOX_H, Color.DIM)} "
                f"{self._color(result_str, output_color)}"
            )

        self._write("")

    def on_thinking(self, thinking_text: str, is_partial: bool = False) -> None:
        """Called when the agent is in thinking mode."""
        if not self.show_thinking:
            return

        brain = self._symbol(Symbol.BRAIN, "*")
        preview = self._truncate(thinking_text, 70)
        length = len(thinking_text)

        self._write(
            f"  {self._color(brain, Color.YELLOW)} "
            f"{self._color('Thinking:', Color.YELLOW, Color.BOLD)} "
            f"{self._color(preview, Color.DIM)} "
            f"{self._color(f'({length} chars)', Color.BRIGHT_BLACK)}"
        )

    def on_message(self, text: str, is_partial: bool = False) -> None:
        """Called when the agent generates a message."""
        if not text.strip():
            return

        preview = self._truncate(text, self.max_preview_length)
        length = len(text)

        pointer = self._symbol(Symbol.POINTER, ">")

        if is_partial:
            # Partial message - update in place
            self._clear_line()
            self._write(
                f"  {self._color(pointer, Color.GREEN)} "
                f"{self._color(preview, Color.WHITE)} "
                f"{self._color(f'[{length}]', Color.DIM)}",
                end=""
            )
        else:
            # Complete message
            self._write(
                f"  {self._color(pointer, Color.BRIGHT_GREEN)} "
                f"{self._color(preview, Color.WHITE)} "
                f"{self._color(f'({length} chars)', Color.DIM)}"
            )

    def on_error(self, error_message: str, error_type: str = "error") -> None:
        """Called when an error occurs."""
        self._stop_spinner()

        error_icon = self._symbol(Symbol.CROSS, "X")
        warn_icon = self._symbol(Symbol.WARN, "!")

        icon = error_icon if error_type == "error" else warn_icon
        color = Color.BRIGHT_RED if error_type == "error" else Color.BRIGHT_YELLOW

        self._write("")
        self._write(
            f"  {self._color(icon, color)} "
            f"{self._color(error_type.upper() + ':', color, Color.BOLD)} "
            f"{self._color(error_message, color)}"
        )
        self._write("")

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
        """Called when the agent completes execution."""
        self._stop_spinner()

        status_upper = status.upper()
        is_complete = status_upper in ("COMPLETE", "OK", "COMPLETED")
        is_partial = status_upper == "PARTIAL"

        self._write("")

        if is_complete:
            header_color = Color.BRIGHT_GREEN
            status_icon = self._symbol(Symbol.CHECK, "OK")
            header_text = "COMPLETE"
        elif is_partial:
            header_color = Color.BRIGHT_YELLOW
            status_icon = self._symbol(Symbol.WARN, "!")
            header_text = "PARTIAL"
        else:
            header_color = Color.BRIGHT_RED
            status_icon = self._symbol(Symbol.CROSS, "X")
            header_text = "FAILED"

        width = self._console_width - 2  # Use full console width with small margin

        # Build content lines
        content_lines: list[str] = []

        # Metrics line (current run)
        duration_str = self._format_duration(duration_ms)
        metrics_parts = [f"Duration: {duration_str}", f"Turns: {num_turns}"]
        if total_cost_usd is not None:
            cost_str = self._format_cost(total_cost_usd)
            metrics_parts.append(f"Cost: {cost_str}")
        content_lines.append(" " + " | ".join(metrics_parts))

        # Token usage and context load (this run)
        if usage:
            input_tokens = usage.get("input_tokens", 0)
            cache_creation = usage.get("cache_creation_input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            total_input = input_tokens + cache_creation + cache_read
            total_tokens = total_input + output_tokens

            # Build token usage line
            token_parts = [f"Tokens: {total_tokens:,}"]
            token_parts.append(f"(in: {total_input:,}, out: {output_tokens:,})")

            # Add context load if model is known
            if model:
                context_size = get_model_context_size(model)
                context_percent = (total_input / context_size) * 100
                token_parts.append(
                    f"Context: {total_input:,}/{context_size:,} ({context_percent:.1f}%)"
                )

            content_lines.append(" " + " | ".join(token_parts))

            # Cache info if relevant
            if cache_creation > 0 or cache_read > 0:
                cache_parts = []
                if cache_creation > 0:
                    cache_parts.append(f"cache_write: {cache_creation:,}")
                if cache_read > 0:
                    cache_parts.append(f"cache_read: {cache_read:,}")
                content_lines.append(
                    f" {self._symbol(Symbol.BULLET, '-')} " + " | ".join(cache_parts)
                )

        # Cumulative stats (if this is a resumed session)
        has_cumulative = (
            cumulative_cost_usd is not None and
            cumulative_turns is not None and
            (cumulative_turns > num_turns or cumulative_cost_usd > (total_cost_usd or 0))
        )
        if has_cumulative:
            content_lines.append("")  # Empty line separator
            cumul_parts = [
                f"Session Total: {cumulative_turns} turns"
            ]
            if cumulative_cost_usd is not None:
                cumul_parts.append(self._format_cost(cumulative_cost_usd))
            if cumulative_tokens is not None:
                cumul_parts.append(f"{cumulative_tokens:,} tokens")
            content_lines.append(
                f" {self._symbol(Symbol.STAR, '*')} " + " | ".join(cumul_parts)
            )

        # Session ID
        if session_id:
            content_lines.append(f" Session: {session_id}")

        # Use _print_box for the completion summary - entire box in status color
        title = f"{status_icon} {header_text}"
        self._print_box(
            lines=content_lines,
            title=title,
            color=Color.WHITE,
            title_color=header_color,
            border_color=header_color,
            width=width,
            center_title=True
        )
        self._write("")

    def on_output_display(
        self,
        output: Optional[str] = None,
        error: Optional[str] = None,
        comments: Optional[str] = None,
        result_files: Optional[list[str]] = None,
        status: Optional[str] = None
    ) -> None:
        """
        Display a structured output summary in a styled box.

        Uses the shared print_output_box function for consistent formatting
        across CLI and HTTP client output.
        """
        # Use shared output box function for consistent formatting
        print_output_box(
            output=output,
            error=error,
            comments=comments,
            result_files=result_files,
            status=status or "COMPLETE",
            terminal_width=self._console_width,
        )

    # ═══════════════════════════════════════════════════════════════
    # Additional Utility Methods
    # ═══════════════════════════════════════════════════════════════

    def on_system_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Handle system events (init, status changes, etc.)."""
        if event_type == "init":
            self.on_agent_start(
                session_id=data.get("session_id", "unknown"),
                model=data.get("model", "unknown"),
                tools=data.get("tools", []),
                working_dir=data.get("cwd", ".")
            )
        else:
            info_icon = self._symbol(Symbol.INFO, "i")
            self._write(
                f"  {self._color(info_icon, Color.BLUE)} "
                f"{self._color(event_type, Color.BLUE)}: "
                f"{self._color(str(data)[:80], Color.DIM)}"
            )

    def on_permission_check(
        self,
        tool_name: str,
        decision: str,
        reason: Optional[str] = None
    ) -> None:
        """Called when a permission check is made."""
        if decision == "allow":
            icon = self._symbol(Symbol.CHECK, "V")
            color = Color.GREEN
        elif decision == "deny":
            icon = self._symbol(Symbol.CROSS, "X")
            color = Color.RED
        else:
            icon = self._symbol(Symbol.WARN, "?")
            color = Color.YELLOW

        display_name = self._format_tool_name(tool_name)
        msg = f"{display_name} {self._symbol(Symbol.ARROW_RIGHT, '->')} {decision}"
        if reason:
            msg += f" ({reason})"

        self._write(
            f"    {self._color(icon, color)} "
            f"{self._color('Permission:', Color.DIM)} "
            f"{self._color(msg, color)}"
        )

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
        Display profile switch notification with tools info.

        If called before on_agent_start, stores the profile info to be
        included in the agent start header. If called after, prints
        a standalone profile switch notification.

        Args:
            profile_type: Type of profile ("system" or "user").
            profile_name: Name of the profile.
            tools: List of available tools in this profile.
            allow_rules_count: Number of allow rules in the profile.
            deny_rules_count: Number of deny rules in the profile.
            profile_path: Path to the loaded profile file.
        """
        profile_info = {
            "profile_type": profile_type,
            "profile_name": profile_name,
            "tools": tools,
            "allow_rules_count": allow_rules_count,
            "deny_rules_count": deny_rules_count,
            "profile_path": profile_path,
        }

        # If agent hasn't started yet, store profile info for header
        if not self._agent_started:
            self._pending_profile = profile_info
            return

        # Agent has started, print standalone profile switch notification
        self._print_profile_switch(profile_info)

    def _print_profile_switch(self, profile_info: dict[str, Any]) -> None:
        """
        Print a standalone profile switch notification.

        Args:
            profile_info: Dictionary with profile details.
        """
        profile_type = profile_info["profile_type"]
        profile_name = profile_info["profile_name"]
        tools = profile_info["tools"]
        allow_rules_count = profile_info["allow_rules_count"]
        deny_rules_count = profile_info["deny_rules_count"]
        profile_path = profile_info.get("profile_path")

        bar = self._symbol(Symbol.BOX_V, "|")

        # Choose color based on profile type
        if profile_type.lower() == "system":
            profile_color = Color.BRIGHT_MAGENTA
            icon = self._symbol(Symbol.GEAR, "*")
        else:
            profile_color = Color.BRIGHT_GREEN
            icon = self._symbol(Symbol.STAR, "*")

        # Print separator before profile switch
        self._write("")

        # Profile header
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(icon, profile_color)} "
            f"{self._color('PROFILE:', Color.DIM)} "
            f"{self._color(profile_type.upper(), profile_color, Color.BOLD)} "
            f"{self._color(f'({profile_name})', Color.DIM)}"
        )

        # Profile path
        if profile_path:
            # Shorten the path for display
            path_display = profile_path
            if len(path_display) > 50:
                path_display = "..." + path_display[-47:]
            self._write(
                f"  {self._color(bar, Color.DIM)} "
                f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
                f"{self._color('Loaded:', Color.DIM)} "
                f"{self._color(path_display, Color.DIM)}"
            )

        # Rules count
        rules_info = f"allow={allow_rules_count}, deny={deny_rules_count}"
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.BULLET, '-'), Color.DIM)} "
            f"{self._color('Rules:', Color.DIM)} "
            f"{self._color(rules_info, Color.DIM)}"
        )

        # Tools section
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(self._symbol(Symbol.TOOL, '*'), Color.BRIGHT_WHITE)} "
            f"{self._color('Tools:', Color.DIM)} "
            f"{self._color(str(len(tools)), Color.BRIGHT_WHITE)} "
            f"{self._color('available', Color.DIM)}"
        )

        # Display tools in grid
        tool_lines = self._format_tools_grid(tools, columns=4, col_width=18)
        for line in tool_lines:
            self._write(
                f"  {self._color(bar, Color.DIM)}   "
                f"{self._color(line, Color.DIM)}"
            )

        # Bottom separator
        bl = self._symbol(Symbol.BOX_BL, "+")
        h = self._symbol(Symbol.BOX_H, "-")
        self._write(self._color(f"  {bl}{h * 57}", Color.DIM))
        self._write("")

    def print_separator(self, char: str = "─", width: int = 60) -> None:
        """Print a separator line."""
        sep_char = self._symbol(char, "-")
        self._write(self._color(f"  {sep_char * width}", Color.DIM))

    def print_status(self, message: str, status: str = "info") -> None:
        """Print a status message."""
        icons = {
            "info": (Symbol.INFO, Color.BLUE),
            "success": (Symbol.CHECK, Color.GREEN),
            "warning": (Symbol.WARN, Color.YELLOW),
            "error": (Symbol.CROSS, Color.RED),
        }

        icon, color = icons.get(status, icons["info"])
        self._write(
            f"  {self._color(self._symbol(icon, '*'), color)} "
            f"{self._color(message, color)}"
        )

    # ═══════════════════════════════════════════════════════════════
    # Hooks-aware tracing implementations
    # ═══════════════════════════════════════════════════════════════

    def on_hook_triggered(
        self,
        hook_event: str,
        tool_name: Optional[str] = None,
        decision: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        """Display hook trigger notification."""
        if not self.verbose:
            return

        bar = self._symbol(Symbol.BOX_V, "|")

        # Color based on decision
        if decision == "allow":
            color = Color.GREEN
            icon = self._symbol(Symbol.CHECK, "v")
        elif decision == "deny" or decision == "block":
            color = Color.RED
            icon = self._symbol(Symbol.CROSS, "x")
        else:
            color = Color.CYAN
            icon = self._symbol(Symbol.GEAR, "*")

        parts = [hook_event]
        if tool_name:
            display_name = self._format_tool_name(tool_name)
            parts.append(f"[{display_name}]")
        if decision:
            parts.append(f"-> {decision}")

        hook_text = " ".join(parts)

        self._write(
            f"    {self._color(bar, Color.DIM)} "
            f"{self._color(icon, color)} "
            f"{self._color('Hook:', Color.DIM)} "
            f"{self._color(hook_text, color)}"
        )

        if message:
            self._write(
                f"    {self._color(bar, Color.DIM)}   "
                f"{self._color(message[:60], Color.DIM)}"
            )

    def on_conversation_turn(
        self,
        turn_number: int,
        prompt_preview: str,
        response_preview: str,
        duration_ms: int,
        tools_used: list[str]
    ) -> None:
        """Display conversation turn summary."""
        bar = self._symbol(Symbol.BOX_V, "|")
        arrow = self._symbol(Symbol.ARROW_RIGHT, "->")

        duration_str = self._format_duration(duration_ms)
        tools_str = f" [{', '.join(tools_used)}]" if tools_used else ""

        self._write("")
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(f'Turn {turn_number}', Color.BRIGHT_CYAN, Color.BOLD)} "
            f"{self._color(f'({duration_str})', Color.DIM)}"
            f"{self._color(tools_str, Color.DIM)}"
        )

        # Prompt preview
        prompt_truncated = self._truncate(prompt_preview, 50)
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color('You:', Color.WHITE)} "
            f"{self._color(prompt_truncated, Color.DIM)}"
        )

        # Response preview
        response_truncated = self._truncate(response_preview, 50)
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(f'{arrow}', Color.GREEN)} "
            f"{self._color(response_truncated, Color.WHITE)}"
        )

    def on_session_connect(self, session_id: Optional[str] = None) -> None:
        """Display session connect notification."""
        bar = self._symbol(Symbol.BOX_V, "|")
        icon = self._symbol(Symbol.LIGHTNING, "*")

        session_str = session_id or "connecting..."

        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(icon, Color.BRIGHT_GREEN)} "
            f"{self._color('Session connected:', Color.DIM)} "
            f"{self._color(session_str, Color.BRIGHT_GREEN)}"
        )

    def on_session_disconnect(
        self,
        session_id: Optional[str] = None,
        total_turns: int = 0,
        total_duration_ms: int = 0
    ) -> None:
        """Display session disconnect summary."""
        bar = self._symbol(Symbol.BOX_V, "|")
        icon = self._symbol(Symbol.CHECK, "v")

        duration_str = self._format_duration(total_duration_ms)

        self._write("")
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(icon, Color.BRIGHT_CYAN)} "
            f"{self._color('Session ended:', Color.DIM)} "
            f"{self._color(f'{total_turns} turns, {duration_str}', Color.WHITE)}"
        )

    def on_subagent_start(
        self,
        task_id: str,
        subagent_name: str,
        prompt: str
    ) -> None:
        """Display subagent start notification."""
        bar = self._symbol(Symbol.BOX_V, "|")
        icon = self._symbol(Symbol.ARROW_RIGHT, ">")

        prompt_preview = self._truncate(prompt.strip(), 60) if prompt else ""
        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(icon, Color.BRIGHT_BLUE)} "
            f"{self._color(f'Subagent: {subagent_name}', Color.BRIGHT_BLUE)}"
        )
        if prompt_preview:
            self._write(
                f"  {self._color(bar, Color.DIM)}   "
                f"{self._color(prompt_preview, Color.DIM)}"
            )

    def on_subagent_message(
        self,
        task_id: str,
        text: str,
        is_partial: bool = False
    ) -> None:
        """Display subagent message (only non-partial messages in verbose mode)."""
        if is_partial or not self.verbose:
            return
        bar = self._symbol(Symbol.BOX_V, "|")
        preview = self._truncate(text.strip(), 60)
        self._write(
            f"  {self._color(bar, Color.DIM)}   "
            f"{self._color(f'[subagent] {preview}', Color.DIM)}"
        )

    def on_subagent_stop(
        self,
        task_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Display subagent completion."""
        bar = self._symbol(Symbol.BOX_V, "|")
        icon = self._symbol(Symbol.CHECK, "v") if not is_error else self._symbol(Symbol.CROSS, "x")
        color = Color.GREEN if not is_error else Color.RED
        duration_str = self._format_duration(duration_ms)

        self._write(
            f"  {self._color(bar, Color.DIM)} "
            f"{self._color(icon, color)} "
            f"{self._color(f'Subagent complete ({duration_str})', Color.DIM)}"
        )


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
        """Display subagent start with spinner."""
        self._stop_spinner()
        prompt_preview = truncate_text(prompt, 80) if prompt else ""
        color = Color.CYAN if self.use_colors else ""
        reset = Color.RESET if self.use_colors else ""
        icon = Symbol.GEAR if self.use_unicode else ">"
        print(f"  {color}{icon} Subagent: {subagent_name}{reset}")
        if prompt_preview:
            dim = Color.DIM if self.use_colors else ""
            print(f"    {dim}{prompt_preview}{reset}")
        self._start_spinner(
            f"Running subagent: {subagent_name}",
            frames=Symbol.SPINNER_PULSE_CIRCLE,
            pre_colored=True
        )

    def on_subagent_message(
        self,
        task_id: str,
        text: str,
        is_partial: bool = False
    ) -> None:
        """Display subagent messages (streaming or final)."""
        if is_partial:
            # For partial messages, just update spinner
            return
        # For final messages, show brief output
        if text and text.strip():
            self._stop_spinner()
            preview = truncate_text(text.strip(), 60)
            dim = Color.DIM if self.use_colors else ""
            reset = Color.RESET if self.use_colors else ""
            print(f"    {dim}[subagent] {preview}{reset}")

    def on_subagent_stop(
        self,
        task_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        """Display subagent completion."""
        self._stop_spinner()
        duration_str = format_duration(duration_ms)
        if is_error:
            color = Color.RED if self.use_colors else ""
            icon = Symbol.CROSS if self.use_unicode else "X"
        else:
            color = Color.GREEN if self.use_colors else ""
            icon = Symbol.CHECK if self.use_unicode else "+"
        reset = Color.RESET if self.use_colors else ""
        print(f"    {color}{icon} Subagent completed ({duration_str}){reset}")


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
        # Console output
        print(formatted)
        # Python logging (with session context)
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
        # Update session_id if provided
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
        # Calculate actual duration if available
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

        # Log cumulative stats if resumed session
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


class EventingTracer(TracerBase):
    """
    Tracer wrapper that emits structured events to an asyncio queue.

    This allows real-time streaming (SSE/Web) while preserving the original
    tracer output for CLI or backend logging.

    Event Delivery Guarantee:
    The event_sink is an async function that persists events to the database.
    Events are awaited for persistence BEFORE being published to the EventHub.
    This ensures that SSE subscribers will always find events either:
    1. In the queue (if subscribed before publish), or
    2. In the database (if subscribed after publish)
    """

    def __init__(
        self,
        tracer: TracerBase,
        event_queue: Optional[asyncio.Queue] = None,
        event_sink: Optional[Any] = None,  # Async callable: (event: dict) -> None
        session_id: Optional[str] = None,
        initial_sequence: int = 0,
    ) -> None:
        self._tracer = tracer
        self._event_queue = event_queue
        self._event_sink = event_sink
        self._session_id = session_id
        self._sequence = initial_sequence
        self._stream_header_buffer = ""
        self._stream_header_expected: Optional[bool] = None
        self._stream_header_wrapped = False
        self._stream_structured_fields: Optional[dict[str, str]] = None
        self._stream_structured_status: Optional[str] = None
        self._stream_structured_error: Optional[str] = None
        self._stream_full_text = ""
        self._stream_active = False
        self._last_stream_full_text = ""
        self._suppress_next_message = False
        # Track working directory for post-completion security scanning
        self._working_dir: Optional[str] = None

    def emit_event(
        self,
        event_type: str,
        data: dict[str, Any],
        persist_event: bool = True
    ) -> None:
        """
        Emit a structured event to the queue.

        Events are persisted to DB and published to EventHub subscribers.
        To prevent race conditions where events are published before SSE
        subscribers connect, we await persistence completion before publishing.
        This guarantees that any event published to EventHub is already in the
        database, so SSE replay will always find it.
        """
        if self._event_queue is None:
            return

        self._sequence += 1
        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": self._sequence,
            "session_id": self._session_id or data.get("session_id"),
        }

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def publish_and_persist():
            """
            Publish to Redis Stream first, then persist to SQLite.

            With Redis Streams (not Pub/Sub), events are durably stored in the
            stream. This eliminates the race condition that existed with Pub/Sub:

            Redis Streams Architecture:
            1. Publish to Redis Stream (~1ms) - events persist in stream
            2. Persist to SQLite (~5-50ms) - backup for long-term storage/analytics

            Why this order works:
            - Redis Streams persist events (unlike Pub/Sub which is fire-and-forget)
            - SSE consumers read from the stream starting at position "0" (beginning)
            - Even if consumer connects after publish, events are still in stream
            - No race conditions - consumers always get all events from stream

            SQLite persistence is now optional backup for:
            - Long-term analytics and reporting
            - Disaster recovery if Redis data is lost
            - Historical queries that span beyond Redis TTL
            """
            # Publish to Redis Stream FIRST for low latency real-time delivery
            # Events persist in stream - no race conditions with late subscribers
            try:
                await self._event_queue.put(event)
            except Exception as e:
                logger.error(f"Failed to publish event to Redis Stream: {e}")
                # Continue to SQLite persistence as fallback

            # Then persist to SQLite as backup/long-term storage
            if self._event_sink is not None and persist_event:
                try:
                    await self._event_sink(event)
                except Exception as e:
                    logger.warning(f"SQLite persistence failed (event is in Redis Stream): {e}")

        if loop and loop.is_running():
            loop.create_task(publish_and_persist())
        else:
            # No event loop available - this shouldn't happen in normal operation
            # but can occur during shutdown or in edge cases
            logger.error(
                f"emit_event called outside async context for {event_type}. "
                "Event will be published to Redis Stream but NOT persisted to SQLite."
            )
            # Best effort: publish to Redis Stream (SQLite persistence requires async context)
            try:
                self._event_queue.put_nowait(event)
            except Exception as e:
                logger.error(f"Failed to publish event {event_type}: {e}")

    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        # Store working_dir for post-completion security scanning
        self._working_dir = working_dir

        # Save claude_session_id to database immediately when we receive it
        # The session_id parameter here is the Claude SDK session ID
        if session_id and session_id != "unknown" and self._session_id:
            self._save_claude_session_id(session_id)

        self._tracer.on_agent_start(
            session_id=session_id,
            model=model,
            tools=tools,
            working_dir=working_dir,
            skills=skills,
            task=task,
        )
        self.emit_event(
            "agent_start",
            {
                "session_id": session_id,
                "model": model,
                "tools": tools,
                "skills": skills,
                "task": task,
            },
        )

    def _save_claude_session_id(self, claude_session_id: str) -> None:
        """
        Persist claude_session_id to database immediately (fire-and-forget).

        This captures the Claude SDK's session ID as soon as we receive the init event,
        enabling auto-resume even if execution is interrupted before completion.
        """
        async def _save():
            try:
                from ..db.database import AsyncSessionLocal
                from ..db.models import Session
                from sqlalchemy import select

                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(Session).where(Session.id == self._session_id)
                    )
                    session = result.scalar_one_or_none()
                    if session:
                        session.claude_session_id = claude_session_id
                        await db.commit()
                        logger.debug(
                            f"Saved claude_session_id for {self._session_id}: {claude_session_id}"
                        )
            except Exception as e:
                logger.warning(f"Failed to save claude_session_id: {e}")

        # Schedule in event loop (non-blocking)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_save())
        except RuntimeError:
            # No running loop - skip (will be saved on completion anyway)
            pass

    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        self._tracer.on_tool_start(tool_name, tool_input, tool_id)
        self.emit_event(
            "tool_start",
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_id": tool_id,
            },
        )

    def on_tool_input_ready(
        self,
        tool_name: str,
        tool_id: str,
        tool_input: dict[str, Any],
    ) -> None:
        """Emit event when complete tool input is available (after streaming completes)."""
        self.emit_event(
            "tool_input_ready",
            {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "tool_input": tool_input,
            },
        )

    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        self._tracer.on_tool_complete(
            tool_name=tool_name,
            tool_id=tool_id,
            result=result,
            duration_ms=duration_ms,
            is_error=is_error,
        )
        self.emit_event(
            "tool_complete",
            {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "result": result,
                "duration_ms": duration_ms,
                "is_error": is_error,
            },
        )

    def on_thinking(self, thinking_text: str, is_partial: bool = False) -> None:
        self._tracer.on_thinking(thinking_text, is_partial=is_partial)
        self.emit_event(
            "thinking",
            {"text": thinking_text, "is_partial": is_partial},
            persist_event=not is_partial,  # Only persist complete thinking blocks
        )

    def on_message(self, text: str, is_partial: bool = False) -> None:
        if is_partial:
            self._stream_active = True
            body_text = self._consume_stream_text(text)
            if not body_text:
                return
            self._stream_full_text += body_text
            self._tracer.on_message(body_text, is_partial=True)
            self.emit_event(
                "message",
                {
                    "text": body_text,
                    "is_partial": True,
                    "structured_fields": None,
                    "structured_status": None,
                    "structured_error": None,
                },
                persist_event=False,
            )
            return

        if self._stream_active:
            body_text = ""
            if text and not self._stream_full_text:
                body_text = self._consume_stream_text(text)
            if not body_text and self._stream_header_expected is None and self._stream_header_buffer:
                body_text = self._stream_header_buffer
                self._stream_header_buffer = ""
                self._stream_header_expected = False
            if body_text:
                self._stream_full_text += body_text

            structured_fields = self._stream_structured_fields
            structured_status = self._stream_structured_status
            structured_error = self._stream_structured_error
            full_text = self._stream_full_text
            if not body_text and not full_text:
                self._reset_stream_state()
                return
            self._tracer.on_message(full_text, is_partial=False)
            self.emit_event(
                "message",
                {
                    "text": body_text,
                    "full_text": full_text,
                    "is_partial": False,
                    "structured_fields": structured_fields,
                    "structured_status": structured_status,
                    "structured_error": structured_error,
                },
            )
            self._last_stream_full_text = full_text
            self._suppress_next_message = bool(full_text.strip())
            self._reset_stream_state()
            return

        if not text.strip():
            return

        if self._suppress_next_message:
            if text.strip() == self._last_stream_full_text.strip():
                self._suppress_next_message = False
                self._last_stream_full_text = ""
                return
            self._suppress_next_message = False
            self._last_stream_full_text = ""

        self._tracer.on_message(text, is_partial=False)
        structured_fields = None
        structured_status = None
        structured_error = None

        fields, body = parse_structured_output(text)
        if fields:
            structured_fields = fields
            structured_status = fields.get("status")
            structured_error = fields.get("error")
            text = body

        self.emit_event(
            "message",
            {
                "text": text,
                "is_partial": False,
                "structured_fields": structured_fields,
                "structured_status": structured_status,
                "structured_error": structured_error,
            },
        )

    def _reset_stream_state(self) -> None:
        self._stream_header_buffer = ""
        self._stream_header_expected = None
        self._stream_header_wrapped = False
        self._stream_structured_fields = None
        self._stream_structured_status = None
        self._stream_structured_error = None
        self._stream_full_text = ""
        self._stream_active = False

    def _consume_stream_text(self, text: str) -> str:
        if self._stream_header_expected is None:
            self._stream_header_buffer += text
            if len(self._stream_header_buffer) < 3:
                return ""
            if self._stream_header_buffer.startswith("```"):
                fence_end = self._stream_header_buffer.find("\n")
                if fence_end == -1:
                    return ""
                self._stream_header_wrapped = True
                self._stream_header_buffer = self._stream_header_buffer[fence_end + 1 :]
            trimmed = self._stream_header_buffer.lstrip()
            if not trimmed.startswith("---"):
                output = self._stream_header_buffer
                self._stream_header_buffer = ""
                self._stream_header_expected = False
                return output
            self._stream_header_buffer = trimmed
            self._stream_header_expected = True
            return self._extract_header_body()

        if self._stream_header_expected:
            self._stream_header_buffer += text
            return self._extract_header_body()

        return text

    def _extract_header_body(self) -> str:
        lines = self._stream_header_buffer.splitlines(keepends=True)
        if not lines:
            return ""

        header_end_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                header_end_index = index
                break

        if header_end_index is None:
            return ""

        header_lines = lines[1:header_end_index]
        fields: dict[str, str] = {}
        for line in header_lines:
            line_value = line.strip()
            if not line_value or ":" not in line_value:
                continue
            key, value = line_value.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key:
                # Normalize error field to filter out placeholder values
                if key == "error":
                    value = normalize_error_value(value)
                fields[key] = value

        self._stream_structured_fields = fields or None
        self._stream_structured_status = fields.get("status") if fields else None
        self._stream_structured_error = fields.get("error") if fields else None
        self._stream_header_expected = False

        body_lines = lines[header_end_index + 1 :]
        if self._stream_header_wrapped and body_lines:
            if body_lines[0].strip().startswith("```"):
                body_lines = body_lines[1:]
        body = "".join(body_lines)
        self._stream_header_buffer = ""
        return body

    def on_metrics_update(self, metrics: dict[str, Any]) -> None:
        if hasattr(self._tracer, "on_metrics_update"):
            self._tracer.on_metrics_update(metrics)
        self.emit_event("metrics_update", metrics)

    def on_error(self, error_message: str, error_type: str = "error") -> None:
        self._tracer.on_error(error_message, error_type=error_type)
        self.emit_event(
            "error",
            {
                "message": error_message,
                "error_type": error_type,
            },
        )

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
        self._tracer.on_agent_complete(
            status=status,
            num_turns=num_turns,
            duration_ms=duration_ms,
            total_cost_usd=total_cost_usd,
            result=result,
            session_id=session_id,
            usage=usage,
            model=model,
            cumulative_cost_usd=cumulative_cost_usd,
            cumulative_turns=cumulative_turns,
            cumulative_tokens=cumulative_tokens,
        )
        self.emit_event(
            "agent_complete",
            {
                "status": status,
                "num_turns": num_turns,
                "duration_ms": duration_ms,
                "total_cost_usd": total_cost_usd,
                "result": result,
                "session_id": session_id,
                "usage": usage,
                "model": model,
                "cumulative_cost_usd": cumulative_cost_usd,
                "cumulative_turns": cumulative_turns,
                "cumulative_tokens": cumulative_tokens,
            },
        )

        # Trigger post-completion security scan of session files
        self._trigger_security_scan(session_id or self._session_id)

    def _trigger_security_scan(self, session_id: Optional[str]) -> None:
        """
        Trigger async security scan of session workspace files.

        Scans recently modified files for sensitive data and emits
        security_alert event if any secrets are found.
        """
        if not session_id or not self._working_dir:
            return

        from pathlib import Path

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop available

        async def run_scan():
            try:
                # Import here to avoid circular dependencies
                from ..security import (
                    scan_session_files,
                    is_scanner_enabled,
                )

                if not is_scanner_enabled():
                    return

                # Determine workspace path
                # working_dir typically ends with /workspace
                workspace_path = Path(self._working_dir)
                if not workspace_path.name == "workspace":
                    # Try to find workspace subdirectory
                    possible_workspace = workspace_path / "workspace"
                    if possible_workspace.exists():
                        workspace_path = possible_workspace

                if not workspace_path.exists():
                    logger.debug(f"Workspace not found for security scan: {workspace_path}")
                    return

                # Run the scan
                scan_result = await scan_session_files(
                    session_id=session_id,
                    workspace_path=workspace_path,
                    redact_files=True,
                )

                # Emit security alert if secrets were found
                if scan_result.has_secrets:
                    alert_data = scan_result.to_alert_data()
                    self.emit_event(
                        "security_alert",
                        alert_data,
                        persist_event=True,  # Persist to DB for audit trail
                    )
                    logger.warning(
                        f"Security scan completed for session {session_id}: "
                        f"Found {scan_result.total_secrets} secrets in "
                        f"{scan_result.files_with_secrets} files"
                    )
                else:
                    logger.debug(
                        f"Security scan completed for session {session_id}: "
                        f"No secrets found in {scan_result.files_scanned} files"
                    )

            except Exception as e:
                logger.error(f"Security scan failed for session {session_id}: {e}")

        # Schedule the scan to run in the event loop
        loop.create_task(run_scan())

    def on_output_display(
        self,
        output: Optional[str] = None,
        error: Optional[str] = None,
        comments: Optional[str] = None,
        result_files: Optional[list[str]] = None,
        status: Optional[str] = None
    ) -> None:
        self._tracer.on_output_display(
            output=output,
            error=error,
            comments=comments,
            result_files=result_files,
            status=status,
        )

    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        self._tracer.on_profile_switch(
            profile_type=profile_type,
            profile_name=profile_name,
            tools=tools,
            allow_rules_count=allow_rules_count,
            deny_rules_count=deny_rules_count,
            profile_path=profile_path,
        )
        self.emit_event(
            "profile_switch",
            {
                "profile_type": profile_type,
                "profile_name": profile_name,
                "tools": tools,
                "allow_rules_count": allow_rules_count,
                "deny_rules_count": deny_rules_count,
            },
        )

    def on_hook_triggered(
        self,
        hook_event: str,
        tool_name: Optional[str] = None,
        decision: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        self._tracer.on_hook_triggered(
            hook_event=hook_event,
            tool_name=tool_name,
            decision=decision,
            message=message,
        )
        self.emit_event(
            "hook_triggered",
            {
                "hook_event": hook_event,
                "tool_name": tool_name,
                "decision": decision,
                "message": message,
            },
        )

    def on_conversation_turn(
        self,
        turn_number: int,
        prompt_preview: str,
        response_preview: str,
        duration_ms: int,
        tools_used: list[str]
    ) -> None:
        self._tracer.on_conversation_turn(
            turn_number=turn_number,
            prompt_preview=prompt_preview,
            response_preview=response_preview,
            duration_ms=duration_ms,
            tools_used=tools_used,
        )
        self.emit_event(
            "conversation_turn",
            {
                "turn_number": turn_number,
                "prompt_preview": prompt_preview,
                "response_preview": response_preview,
                "duration_ms": duration_ms,
                "tools_used": tools_used,
            },
        )

    def on_session_connect(self, session_id: Optional[str] = None) -> None:
        self._tracer.on_session_connect(session_id=session_id)
        self.emit_event("session_connect", {"session_id": session_id})

    def on_session_disconnect(
        self,
        session_id: Optional[str] = None,
        total_turns: int = 0,
        total_duration_ms: int = 0
    ) -> None:
        self._tracer.on_session_disconnect(
            session_id=session_id,
            total_turns=total_turns,
            total_duration_ms=total_duration_ms,
        )
        self.emit_event(
            "session_disconnect",
            {
                "session_id": session_id,
                "total_turns": total_turns,
                "total_duration_ms": total_duration_ms,
            },
        )

    def on_subagent_start(
        self,
        task_id: str,
        subagent_name: str,
        prompt: str
    ) -> None:
        self._tracer.on_subagent_start(task_id, subagent_name, prompt)
        self.emit_event(
            "subagent_start",
            {
                "task_id": task_id,
                "subagent_name": subagent_name,
                "prompt_preview": prompt[:200] if prompt else "",
            },
        )

    def on_subagent_message(
        self,
        task_id: str,
        text: str,
        is_partial: bool = False
    ) -> None:
        self._tracer.on_subagent_message(task_id, text, is_partial)
        self.emit_event(
            "subagent_message",
            {
                "task_id": task_id,
                "text": text,
                "is_partial": is_partial,
            },
            persist_event=not is_partial,
        )

    def on_subagent_stop(
        self,
        task_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        self._tracer.on_subagent_stop(task_id, result, duration_ms, is_error)
        self.emit_event(
            "subagent_stop",
            {
                "task_id": task_id,
                "result_preview": str(result)[:500] if result else "",
                "duration_ms": duration_ms,
                "is_error": is_error,
            },
        )


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
