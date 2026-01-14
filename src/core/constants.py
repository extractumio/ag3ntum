"""
Centralized constants for Ag3ntum.

All magic numbers, strings, and configuration values are defined here.
This ensures consistency across modules and makes maintenance easier.

Usage:
    from .constants import (
        AnsiColors,
        BoxChars,
        StatusIcons,
        TerminalControl,
        LOG_FORMAT_FILE,
        LOG_PREVIEW_LENGTH,
    )
"""
from enum import StrEnum


# =============================================================================
# Logging Constants
# =============================================================================

# Log format for file-based logging
LOG_FORMAT_FILE: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Log format for console (basic, no colors)
LOG_FORMAT_CONSOLE: str = "%(levelname)-8s %(name)s: %(message)s"

# Log format for colored console (using colorlog)
LOG_FORMAT_COLORED: str = (
    "%(log_color)s%(levelname)-8s%(reset)s %(cyan)s%(name)s%(reset)s: %(message)s"
)

# Rotating file handler settings
LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT: int = 5

# Log file names
LOG_FILE_CLI: str = "agent_cli.log"
LOG_FILE_HTTP: str = "agent_http.log"
LOG_FILE_BACKEND: str = "backend.log"


# =============================================================================
# ANSI Terminal Colors
# =============================================================================

class AnsiColors(StrEnum):
    """
    ANSI escape codes for terminal colors.

    Single source of truth for all color codes used in terminal output.
    Used by both output.py and tracer.py for consistent styling.
    """
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    # Standard foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright foreground colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Background colors
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"

    # Semantic aliases for common use cases
    GRAY = "\033[90m"
    INFO = "\033[94m"
    SUCCESS = "\033[92m"
    WARNING = "\033[93m"
    ERROR = "\033[91m"


# =============================================================================
# Terminal Control Sequences
# =============================================================================

class TerminalControl:
    """ANSI escape sequences for terminal cursor and line control."""
    CLEAR_LINE: str = "\033[2K"
    CURSOR_UP: str = "\033[1A"
    CURSOR_DOWN: str = "\033[1B"
    CURSOR_START: str = "\033[0G"
    CURSOR_HIDE: str = "\033[?25l"
    CURSOR_SHOW: str = "\033[?25h"
    SAVE_CURSOR: str = "\033[s"
    RESTORE_CURSOR: str = "\033[u"

    @staticmethod
    def move_up(n: int = 1) -> str:
        """Move cursor up n lines."""
        return f"\033[{n}A"

    @staticmethod
    def move_down(n: int = 1) -> str:
        """Move cursor down n lines."""
        return f"\033[{n}B"

    @staticmethod
    def move_to_column(col: int) -> str:
        """Move cursor to column position."""
        return f"\033[{col}G"


# =============================================================================
# Color Configuration for colorlog
# =============================================================================

COLORLOG_COLORS: dict[str, str] = {
    "DEBUG": "white",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "red,bg_white",
}


# =============================================================================
# Display Constants
# =============================================================================

# Preview lengths for logging and display
LOG_PREVIEW_LENGTH: int = 100
TASK_PREVIEW_LENGTH: int = 60
ERROR_PREVIEW_LENGTH: int = 40
WORKING_DIR_TRUNCATE_LENGTH: int = 40
MESSAGE_PREVIEW_LENGTH: int = 80
TOOL_PREVIEW_LENGTH: int = 80
PATH_TRUNCATE_LENGTH: int = 80

# Terminal output
DEFAULT_TERMINAL_WIDTH: int = 80
MIN_TERMINAL_WIDTH: int = 50
MAX_TERMINAL_WIDTH: int = 120
RESULT_BOX_MAX_WIDTH: int = 55

# Session table column widths
SESSION_ID_WIDTH: int = 30
SESSION_STATUS_WIDTH: int = 12
SESSION_CREATED_WIDTH: int = 20


# =============================================================================
# Box Drawing Characters (Unicode)
# =============================================================================

class BoxChars:
    """
    Unicode box drawing characters for terminal output.

    Single source of truth for all box/border characters used in CLI display.
    Provides both single-line and double-line variants.
    """
    # Single-line box
    TOP_LEFT: str = "┌"
    TOP_RIGHT: str = "┐"
    BOTTOM_LEFT: str = "└"
    BOTTOM_RIGHT: str = "┘"
    HORIZONTAL: str = "─"
    VERTICAL: str = "│"
    LEFT_T: str = "├"
    RIGHT_T: str = "┤"
    TOP_T: str = "┬"
    BOTTOM_T: str = "┴"
    CROSS: str = "┼"

    # Double-line box
    DOUBLE_TOP_LEFT: str = "╔"
    DOUBLE_TOP_RIGHT: str = "╗"
    DOUBLE_BOTTOM_LEFT: str = "╚"
    DOUBLE_BOTTOM_RIGHT: str = "╝"
    DOUBLE_HORIZONTAL: str = "═"
    DOUBLE_VERTICAL: str = "║"

    # ASCII fallbacks for non-Unicode terminals
    ASCII_TOP_LEFT: str = "+"
    ASCII_TOP_RIGHT: str = "+"
    ASCII_BOTTOM_LEFT: str = "+"
    ASCII_BOTTOM_RIGHT: str = "+"
    ASCII_HORIZONTAL: str = "-"
    ASCII_VERTICAL: str = "|"


# =============================================================================
# Status and Decoration Icons
# =============================================================================

class StatusIcons:
    """
    Unicode icons for status display.

    Single source of truth for all status indicators used in terminal output.
    """
    # Status indicators
    SUCCESS: str = "✓"
    FAILURE: str = "✗"
    WARNING: str = "⚠"
    INFO: str = "ℹ"
    RUNNING: str = "⟳"
    PENDING: str = "○"

    # Additional decorative symbols
    BULLET: str = "•"
    CIRCLE: str = "○"
    CIRCLE_FILLED: str = "●"
    STAR: str = "★"
    LIGHTNING: str = "⚡"
    GEAR: str = "⚙"
    POINTER: str = "❯"
    ARROW_RIGHT: str = "→"
    ARROW_LEFT: str = "←"
    ARROW_UP: str = "↑"
    ARROW_DOWN: str = "↓"
    TRIANGLE_RIGHT: str = "▶"
    TRIANGLE_DOWN: str = "▼"
    FOLDER: str = "▣"
    FILE: str = "▫"
    CLOCK: str = "◔"
    BRAIN: str = "◆"

    # Spinner characters for progress indication
    SPINNER: tuple[str, ...] = (
        "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"
    )
    SPINNER_LINE: tuple[str, ...] = ("—", "\\", "|", "/")
    SPINNER_ARROW: tuple[str, ...] = ("←", "↖", "↑", "↗", "→", "↘", "↓", "↙")


# =============================================================================
# API Constants
# =============================================================================

# Default polling interval for HTTP client
DEFAULT_POLL_INTERVAL: float = 2.0

# HTTP request timeout
DEFAULT_HTTP_TIMEOUT: int = 30


# =============================================================================
# Tool Display Formatting
# =============================================================================

# Grid layout settings for tool display
TOOL_GRID_COLUMNS: int = 4
TOOL_GRID_COLUMN_WIDTH: int = 18

# JSON preview settings
JSON_PREVIEW_MAX_LINES: int = 10
JSON_PREVIEW_MAX_LINE_LENGTH: int = 80

# Todo plan display settings
TODO_PLAN_INDENT: int = 6
TODO_CONTENT_MAX_LENGTH: int = 55
