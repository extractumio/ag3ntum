"""
Shared utilities for ReadDocument tool.

Common functions used across extractors and the main tool.
"""
import logging
import re
import shutil
import subprocess
from pathlib import Path

from .exceptions import DependencyMissingError, PageRangeError, RowRangeError

logger = logging.getLogger(__name__)


def check_dependency(name: str, purpose: str) -> str:
    """
    Check if a system dependency is available.

    Args:
        name: Command name to check (e.g., "pandoc", "tesseract")
        purpose: Human-readable purpose for error messages

    Returns:
        Path to the executable

    Raises:
        DependencyMissingError: If dependency is not found
    """
    path = shutil.which(name)
    if path is None:
        logger.error(f"Required dependency '{name}' not found. Needed for: {purpose}")
        raise DependencyMissingError(name, purpose)
    logger.debug(f"Found dependency {name} at {path}")
    return path


def run_command(
    cmd: list[str],
    timeout: float,
    input_data: bytes | None = None,
    cwd: Path | None = None,
) -> tuple[bytes, bytes, int]:
    """
    Run a command with timeout.

    Args:
        cmd: Command and arguments
        timeout: Timeout in seconds
        input_data: Optional stdin data
        cwd: Working directory

    Returns:
        Tuple of (stdout, stderr, return_code)
    """
    try:
        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        logger.warning(f"Command timed out after {timeout}s: {cmd[0]}")
        raise
    except Exception as e:
        logger.error(f"Command failed: {cmd[0]} - {e}")
        raise


def parse_page_range(spec: str | None, total_pages: int) -> list[int]:
    """
    Parse a page range specification.

    Formats:
    - "5" -> [4] (single page, 0-indexed)
    - "1-10" -> [0,1,2,...,9]
    - "1,3,5-7" -> [0,2,4,5,6]
    - None -> all pages

    Args:
        spec: Page range string
        total_pages: Total number of pages in document

    Returns:
        List of 0-indexed page numbers

    Raises:
        PageRangeError: If specification is invalid
    """
    if spec is None:
        return list(range(total_pages))

    pages = set()
    spec = spec.strip()

    try:
        for part in spec.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                start_idx = int(start) - 1
                end_idx = int(end) - 1
                if start_idx < 0 or end_idx >= total_pages or start_idx > end_idx:
                    raise PageRangeError(spec, total_pages)
                pages.update(range(start_idx, end_idx + 1))
            else:
                page_idx = int(part) - 1
                if page_idx < 0 or page_idx >= total_pages:
                    raise PageRangeError(spec, total_pages)
                pages.add(page_idx)

        return sorted(pages)

    except ValueError:
        raise PageRangeError(spec, total_pages)


def parse_row_range(spec: str | None, total_rows: int) -> tuple[int, int]:
    """
    Parse a row range specification.

    Formats:
    - "1-100" -> (0, 100) (start_idx, end_idx exclusive)
    - "head:50" -> (0, 50)
    - "tail:20" -> (total_rows-20, total_rows)
    - None -> (0, total_rows)

    Args:
        spec: Row range string
        total_rows: Total number of rows

    Returns:
        Tuple of (start_idx, end_idx) - 0-indexed, end exclusive

    Raises:
        RowRangeError: If specification is invalid
    """
    if spec is None:
        return (0, total_rows)

    spec = spec.strip().lower()

    try:
        if spec.startswith("head:"):
            n = int(spec[5:])
            return (0, min(n, total_rows))

        elif spec.startswith("tail:"):
            n = int(spec[5:])
            return (max(0, total_rows - n), total_rows)

        elif "-" in spec:
            start, end = spec.split("-", 1)
            start_idx = int(start) - 1
            end_idx = int(end)
            if start_idx < 0 or end_idx > total_rows or start_idx >= end_idx:
                raise RowRangeError(spec, total_rows)
            return (start_idx, end_idx)

        else:
            # Single row
            idx = int(spec) - 1
            if idx < 0 or idx >= total_rows:
                raise RowRangeError(spec, total_rows)
            return (idx, idx + 1)

    except ValueError:
        raise RowRangeError(spec, total_rows)


def parse_column_selection(spec: str | None, available_columns: list[str]) -> list[str]:
    """
    Parse a column selection specification.

    Formats:
    - "A,B,C" -> column letters (for Excel)
    - "name,age,salary" -> column names
    - None -> all columns

    Args:
        spec: Column selection string
        available_columns: List of available column names

    Returns:
        List of selected column names
    """
    if spec is None:
        return available_columns

    requested = [c.strip() for c in spec.split(",")]

    # Check if using Excel-style letters (A, B, C, ...)
    if all(re.match(r"^[A-Z]+$", c) for c in requested):
        # Convert letters to indices
        indices = []
        for letter in requested:
            idx = 0
            for char in letter:
                idx = idx * 26 + (ord(char) - ord("A") + 1)
            indices.append(idx - 1)

        return [available_columns[i] for i in indices if i < len(available_columns)]

    # Otherwise, match by column names
    result = []
    available_lower = {c.lower(): c for c in available_columns}

    for col in requested:
        col_lower = col.lower()
        if col_lower in available_lower:
            result.append(available_lower[col_lower])
        elif col in available_columns:
            result.append(col)

    return result if result else available_columns


def format_bytes(size: int) -> str:
    """Format byte size as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if size != int(size) else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_duration(seconds: float) -> str:
    """
    Format duration as human-readable string.

    Note: This function takes seconds (float) as input, used for audio/video durations.
    The core/output.py format_duration() takes milliseconds (int) for task timing.
    These are intentionally separate due to different input units and use cases.
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


def truncate_string(s: str, max_len: int, suffix: str = "...") -> str:
    """Truncate string with suffix if too long."""
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


def safe_filename(name: str) -> str:
    """Convert string to safe filename."""
    # Replace unsafe characters
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Limit length
    if len(safe) > 200:
        safe = safe[:200]
    return safe
