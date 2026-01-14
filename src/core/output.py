"""
Output formatting utilities for Ag3ntum.

Provides consistent output formatting for CLI and HTTP client, including:
- Result formatting (text and box display)
- Session table formatting
- Status messages with colors
- Shared formatting utilities (duration, cost, text wrapping)

This module is the single source of truth for all display formatting logic.
Both output.py and tracer.py use these utilities for consistent styling.

Usage:
    from .output import (
        format_result,
        print_result_box,
        print_sessions_table,
        format_duration,
        format_cost,
        wrap_text,
        truncate_text,
        truncate_path,
        get_terminal_width,
    )
"""
import shutil
import sys
from typing import Any, Optional

from .constants import (
    AnsiColors,
    BoxChars,
    DEFAULT_TERMINAL_WIDTH,
    MAX_TERMINAL_WIDTH,
    MESSAGE_PREVIEW_LENGTH,
    MIN_TERMINAL_WIDTH,
    PATH_TRUNCATE_LENGTH,
    SESSION_CREATED_WIDTH,
    SESSION_ID_WIDTH,
    SESSION_STATUS_WIDTH,
    StatusIcons,
    WORKING_DIR_TRUNCATE_LENGTH,
)


# =============================================================================
# Terminal Utilities
# =============================================================================

def get_terminal_width() -> int:
    """
    Get terminal width with sensible bounds.

    Returns:
        Terminal width clamped between MIN_TERMINAL_WIDTH and MAX_TERMINAL_WIDTH.
    """
    try:
        width = shutil.get_terminal_size().columns
        return max(MIN_TERMINAL_WIDTH, min(width, MAX_TERMINAL_WIDTH))
    except Exception:
        return DEFAULT_TERMINAL_WIDTH


def is_tty() -> bool:
    """
    Check if stdout is a TTY (terminal).

    Returns:
        True if stdout is a terminal, False otherwise.
    """
    return sys.stdout.isatty()


# =============================================================================
# Text Formatting Utilities
# =============================================================================

def wrap_text(text: str, width: int = 70) -> list[str]:
    """
    Wrap text to specified width, preserving words.

    Args:
        text: Text to wrap.
        width: Maximum line width.

    Returns:
        List of wrapped lines.
    """
    if len(text) <= width:
        return [text]

    words = text.split()
    lines: list[str] = []
    current_line: list[str] = []
    current_len = 0

    for word in words:
        word_len = len(word)
        separator_len = 1 if current_line else 0

        if current_len + word_len + separator_len <= width:
            current_line.append(word)
            current_len += word_len + separator_len
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_len = word_len

    if current_line:
        lines.append(" ".join(current_line))

    return lines


def truncate_text(
    text: str,
    max_len: int = MESSAGE_PREVIEW_LENGTH,
    suffix: str = "..."
) -> str:
    """
    Truncate text with ellipsis, collapsing whitespace.

    Args:
        text: Text to truncate.
        max_len: Maximum length.
        suffix: Suffix to append when truncating.

    Returns:
        Truncated text.
    """
    text = text.replace("\n", " ").replace("\r", "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix


def truncate_path(path: str, max_len: int = PATH_TRUNCATE_LENGTH) -> str:
    """
    Truncate a path intelligently, keeping both start and end visible.

    Shows the beginning of the path and the filename/end portion,
    with '...' in the middle.

    Args:
        path: File path to truncate.
        max_len: Maximum display length.

    Returns:
        Truncated path string.

    Example:
        '/home/user/projects/myapp/src/components/Button.tsx'
        -> '/home/user/pro.../components/Button.tsx'
    """
    if len(path) <= max_len:
        return path

    ellipsis = "..."
    available = max_len - len(ellipsis)

    # Give ~40% to start, ~60% to end (keep more of filename)
    start_len = max(available * 2 // 5, 10)
    end_len = max(available - start_len, 15)

    if start_len + end_len > available:
        start_len = available // 2
        end_len = available - start_len

    return path[:start_len] + ellipsis + path[-end_len:]


# =============================================================================
# Duration and Cost Formatting
# =============================================================================

def format_duration(duration_ms: int) -> str:
    """
    Format duration in human-readable form.

    Args:
        duration_ms: Duration in milliseconds.

    Returns:
        Human-readable duration string (e.g., "1.5s", "2m 30.5s").
    """
    if not duration_ms:
        return "0ms"
    if duration_ms < 1000:
        return f"{duration_ms}ms"
    elif duration_ms < 60000:
        return f"{duration_ms / 1000:.1f}s"
    else:
        minutes = duration_ms // 60000
        seconds = (duration_ms % 60000) / 1000
        return f"{minutes}m {seconds:.1f}s"


def format_cost(cost: float) -> str:
    """
    Format cost in human-readable form.

    Args:
        cost: Cost in USD.

    Returns:
        Formatted cost string (e.g., "$0.0012", "$1.50").
    """
    if cost >= 0.01:
        return f"${cost:.2f}"
    else:
        return f"${cost:.4f}"


# =============================================================================
# Status Message Printing
# =============================================================================

def print_status(message: str, status: str = "info") -> None:
    """
    Print a status message with color.

    Args:
        message: Message to print.
        status: Status type (info, success, warning, error, dim).
    """
    colors = {
        "info": AnsiColors.INFO,
        "success": AnsiColors.SUCCESS,
        "warning": AnsiColors.WARNING,
        "error": AnsiColors.ERROR,
        "dim": AnsiColors.GRAY,
    }
    color = colors.get(status, "")
    print(f"{color}{message}{AnsiColors.RESET}")


# =============================================================================
# Result Attribute Helper
# =============================================================================

def _get_result_attr(result: Any, attr: str, default: Any = None) -> Any:
    """
    Get attribute from result object or dict.

    Supports both AgentResult objects and dict responses from API.

    Args:
        result: AgentResult object or dict.
        attr: Attribute name to retrieve.
        default: Default value if attribute not found.

    Returns:
        Attribute value or default.
    """
    if isinstance(result, dict):
        return result.get(attr, default)
    return getattr(result, attr, default)


# =============================================================================
# Result Formatting
# =============================================================================

def format_result(result: Any, session_id: Optional[str] = None) -> str:
    """
    Format agent result for human-readable output.

    Args:
        result: AgentResult object or dict.
        session_id: Optional session ID override.

    Returns:
        Formatted string suitable for file output or display.
    """
    if isinstance(result, dict):
        status = result.get("status", "UNKNOWN")
        error = result.get("error", "")
        comments = result.get("comments", "")
        output = result.get("output", "")
        result_files = result.get("result_files", [])
        sid = session_id or result.get("session_id", "")
    else:
        status = result.status
        error = result.error
        comments = result.comments
        output = result.output
        result_files = result.result_files or []
        sid = session_id
        if result.session_info:
            sid = result.session_info.session_id

    lines = [f"Status: {status}"]

    if error:
        lines.append(f"Error: {error}")

    if comments:
        lines.append(f"Comments: {comments}")

    if output:
        lines.append(f"Output: {output}")

    if result_files:
        lines.append("Files:")
        for filepath in result_files:
            lines.append(f"  - {filepath}")

    if sid:
        lines.append(f"Session: {sid}")

    return "\n".join(lines)


def _print_box_line(
    text: str,
    inner_width: int,
    border_color: str,
    text_color: str = ""
) -> None:
    """Print a single line within a box."""
    C = AnsiColors
    B = BoxChars
    padding = inner_width - len(text)
    color = text_color or C.WHITE
    print(
        f"{border_color}{B.VERTICAL}{C.RESET}"
        f"{color}{text}{C.RESET}"
        f"{' ' * max(0, padding)}"
        f"{border_color}{B.VERTICAL}{C.RESET}"
    )


def print_output_box(
    output: Optional[str] = None,
    error: Optional[str] = None,
    comments: Optional[str] = None,
    result_files: Optional[list[str]] = None,
    status: str = "COMPLETE",
    terminal_width: Optional[int] = None,
) -> None:
    """
    Print a styled output box with inline title format.

    This is the canonical function for displaying agent output results.
    Used by both CLI (agent_cli.py) and HTTP client (agent_http.py).

    Args:
        output: Output text to display.
        error: Error message if any.
        comments: Additional comments.
        result_files: List of result file paths.
        status: Status string (COMPLETE, PARTIAL, FAILED).
        terminal_width: Terminal width (auto-detected if None).
    """
    C = AnsiColors
    B = BoxChars

    width = terminal_width or get_terminal_width()
    inner_width = width - 2

    # Determine box color and title based on status
    status_upper = status.upper()
    if status_upper in ("COMPLETE", "OK", "COMPLETED", "SUCCESS"):
        box_color = C.SUCCESS
        title = f"{StatusIcons.FILE} Output"
    elif status_upper == "PARTIAL":
        box_color = C.WARNING
        title = f"{StatusIcons.WARNING} Partial Output"
    else:
        box_color = C.ERROR
        title = f"{StatusIcons.FAILURE} Output"

    # Check if we have any content to display
    has_content = output or error or comments or result_files
    if not has_content:
        return

    # Top border with inline title: ┌─ Title ─────────────┐
    title_segment = f" {title} "
    left_border_len = 1  # One horizontal char before title
    right_border_len = inner_width - left_border_len - len(title_segment)
    print(
        f"{box_color}{B.TOP_LEFT}{B.HORIZONTAL}{C.RESET}"
        f"{box_color}{title_segment}{C.RESET}"
        f"{box_color}{B.HORIZONTAL * right_border_len}{B.TOP_RIGHT}{C.RESET}"
    )

    # Error line
    if error and error.strip():
        error_text = str(error).strip()
        max_len = inner_width - 12
        if len(error_text) > max_len:
            error_text = error_text[:max_len - 3] + "..."
        error_line = f" {StatusIcons.FAILURE} Error: {error_text}"
        _print_box_line(error_line, inner_width, box_color, C.ERROR)

    # Comments line
    if comments and comments.strip():
        comments_text = str(comments).strip()
        max_len = inner_width - 12
        if len(comments_text) > max_len:
            comments_text = comments_text[:max_len - 3] + "..."
        comments_line = f" Comments: {comments_text}"
        _print_box_line(comments_line, inner_width, box_color, C.GRAY)

    # Output text
    if output and output.strip():
        output_text = output.strip()
        max_len = inner_width - 5
        if len(output_text) > max_len:
            output_text = output_text[:max_len - 3] + "..."
        output_line = f" > {output_text}"
        _print_box_line(output_line, inner_width, box_color, C.WHITE)

    # Result files
    if result_files:
        files_line = f" {StatusIcons.FOLDER} Result Files: {len(result_files)}"
        _print_box_line(files_line, inner_width, box_color, C.WHITE)
        for filepath in result_files[:5]:
            display_path = str(filepath)
            if len(display_path) > inner_width - 8:
                display_path = "..." + display_path[-(inner_width - 11):]
            file_line = f"    - {display_path}"
            _print_box_line(file_line, inner_width, box_color, C.DIM)
        if len(result_files) > 5:
            more_line = f"    ... +{len(result_files) - 5} more files"
            _print_box_line(more_line, inner_width, box_color, C.DIM)

    # Bottom border
    print(f"{box_color}{B.BOTTOM_LEFT}{B.HORIZONTAL * inner_width}{B.BOTTOM_RIGHT}{C.RESET}")
    print()


def print_result_box(result: Any, session_id: Optional[str] = None) -> None:
    """
    Print a detailed result summary box to stdout.

    Displays status, metrics, token usage, session, and output in styled boxes
    matching the CLI tracer output format.

    Args:
        result: AgentResult object or dict with status, output, error, metrics, etc.
        session_id: Optional session ID override (for API responses).
    """
    C = AnsiColors
    B = BoxChars

    terminal_width = get_terminal_width()
    width = terminal_width - 2
    inner_width = width - 2

    # Determine status and styling
    status_raw = _get_result_attr(result, "status", "FAILED")
    status_upper = str(status_raw).upper()
    is_complete = status_upper in ("COMPLETE", "TASKSTATUS.COMPLETE")
    is_partial = status_upper in ("PARTIAL", "TASKSTATUS.PARTIAL")

    if is_complete:
        status_color = C.SUCCESS
        status_icon = StatusIcons.SUCCESS
        status_text = "COMPLETE"
    elif is_partial:
        status_color = C.WARNING
        status_icon = StatusIcons.WARNING
        status_text = "PARTIAL"
    else:
        status_color = C.ERROR
        status_icon = StatusIcons.FAILURE
        status_text = "FAILED"

    print()

    # =========================================================================
    # Status Box
    # =========================================================================

    # Top border
    print(f"{status_color}{B.TOP_LEFT}{B.HORIZONTAL * inner_width}{B.TOP_RIGHT}{C.RESET}")

    # Status line (centered)
    status_content = f"{status_icon} {status_text}"
    status_padding = (inner_width - len(status_content)) // 2
    print(
        f"{status_color}{B.VERTICAL}{C.RESET}"
        f"{' ' * status_padding}"
        f"{status_color}{C.BOLD}{status_content}{C.RESET}"
        f"{' ' * (inner_width - status_padding - len(status_content))}"
        f"{status_color}{B.VERTICAL}{C.RESET}"
    )

    # Separator
    print(f"{status_color}{B.LEFT_T}{B.HORIZONTAL * inner_width}{B.RIGHT_T}{C.RESET}")

    # Metrics
    metrics = _get_result_attr(result, "metrics")
    if metrics:
        if isinstance(metrics, dict):
            duration_ms = metrics.get("duration_ms") or 0
            turns = metrics.get("num_turns", 0)
            cost = metrics.get("total_cost_usd") or 0
            model = metrics.get("model")
            usage = metrics.get("usage")
        else:
            duration_ms = getattr(metrics, "duration_ms", 0) or 0
            turns = getattr(metrics, "num_turns", 0)
            cost = getattr(metrics, "total_cost_usd", 0) or 0
            model = getattr(metrics, "model", None)
            usage = getattr(metrics, "usage", None)

        # Line 1: Duration | Turns | Cost
        duration_str = format_duration(duration_ms)
        cost_str = format_cost(cost)
        metrics_line = f" Duration: {duration_str} | Turns: {turns} | Cost: {cost_str}"
        _print_box_line(metrics_line, inner_width, status_color, C.WHITE)

        # Line 2: Token usage (if available)
        if usage:
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                cache_creation = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
            else:
                input_tokens = getattr(usage, "input_tokens", 0)
                output_tokens = getattr(usage, "output_tokens", 0)
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
                cache_read = getattr(usage, "cache_read_input_tokens", 0)

            total_input = input_tokens + cache_creation + cache_read
            total_tokens = total_input + output_tokens

            # Token line with context percentage
            token_parts = [f"Tokens: {total_tokens:,}"]
            token_parts.append(f"(in: {total_input:,}, out: {output_tokens:,})")

            # Add context load if model is known
            if model:
                # Import here to avoid circular import
                from .schemas import get_model_context_size
                context_size = get_model_context_size(model)
                context_percent = (total_input / context_size) * 100 if context_size else 0
                token_parts.append(
                    f"Context: {total_input:,}/{context_size:,} ({context_percent:.1f}%)"
                )

            tokens_line = " " + " | ".join(token_parts)
            _print_box_line(tokens_line, inner_width, status_color, C.WHITE)

            # Cache info (if relevant)
            if cache_creation > 0 or cache_read > 0:
                cache_parts = []
                if cache_creation > 0:
                    cache_parts.append(f"cache_write: {cache_creation:,}")
                if cache_read > 0:
                    cache_parts.append(f"cache_read: {cache_read:,}")
                cache_line = " • " + " | ".join(cache_parts)
                _print_box_line(cache_line, inner_width, status_color, C.GRAY)

    # Session ID
    session_info = _get_result_attr(result, "session_info")
    if session_info:
        sid = (
            session_info.session_id
            if hasattr(session_info, "session_id")
            else session_info.get("session_id")
        )
    else:
        sid = session_id or _get_result_attr(result, "session_id")

    if sid:
        session_line = f" Session: {sid}"
        _print_box_line(session_line, inner_width, status_color, C.GRAY)

    # Bottom border
    print(f"{status_color}{B.BOTTOM_LEFT}{B.HORIZONTAL * inner_width}{B.BOTTOM_RIGHT}{C.RESET}")
    print()

    # =========================================================================
    # Output Box (if there's output, error, comments, or files)
    # =========================================================================

    output_text = _get_result_attr(result, "output", "")
    error = _get_result_attr(result, "error", "")
    comments = _get_result_attr(result, "comments", "")
    result_files = _get_result_attr(result, "result_files", [])

    # Use shared output box function for consistent formatting
    print_output_box(
        output=output_text,
        error=error,
        comments=comments,
        result_files=result_files,
        status=status_text,
        terminal_width=terminal_width,
    )


# =============================================================================
# Session Table Formatting
# =============================================================================

def print_sessions_table(sessions: list[Any]) -> None:
    """
    Print a formatted table of sessions.

    Handles both SessionInfo objects and dictionaries from API responses.

    Args:
        sessions: List of session objects or dicts with id, status, created_at, working_dir.
    """
    if not sessions:
        print("No sessions found.")
        return

    header = (
        f"\n{'Session ID':<{SESSION_ID_WIDTH}} "
        f"{'Status':<{SESSION_STATUS_WIDTH}} "
        f"{'Created':<{SESSION_CREATED_WIDTH}} "
        f"{'Working Dir'}"
    )
    print(header)
    print("-" * 100)

    for session in sessions:
        # Handle both SessionInfo objects and dicts
        if isinstance(session, dict):
            session_id = session.get("id", session.get("session_id", ""))
            status = session.get("status", "")
            created = session.get("created_at", "")
            if isinstance(created, str):
                created = created[:19].replace("T", " ")
            working_dir = session.get("working_dir", "") or ""
        else:
            session_id = session.session_id
            status = str(session.status)
            created = session.created_at.strftime("%Y-%m-%d %H:%M:%S")
            working_dir = session.working_dir

        # Truncate long working directory paths
        if len(working_dir) > WORKING_DIR_TRUNCATE_LENGTH:
            working_dir = "..." + working_dir[-(WORKING_DIR_TRUNCATE_LENGTH - 3):]

        print(
            f"{session_id:<{SESSION_ID_WIDTH}} "
            f"{status:<{SESSION_STATUS_WIDTH}} "
            f"{created:<{SESSION_CREATED_WIDTH}} "
            f"{working_dir}"
        )

    print()
