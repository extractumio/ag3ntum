"""
Security validators and output sanitizers for ReadDocument tool.

Handles:
- Archive security (zip bomb detection, banned extensions)
- Content sanitization (LLM context protection)
- Path sanitization for archive internal paths
"""
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import ArchiveConfig, OutputConfig, get_config
from .exceptions import (
    ArchiveFileCountError,
    ArchiveNestingError,
    BannedExtensionError,
    ZipBombDetectedError,
)

logger = logging.getLogger(__name__)


@dataclass
class ArchiveMemberInfo:
    """Information about a file within an archive."""

    name: str
    size: int  # Uncompressed size
    compressed_size: int
    is_dir: bool
    is_symlink: bool = False  # Security: track symlinks for traversal prevention


@dataclass
class SanitizedContent:
    """Result of content sanitization."""

    content: str
    was_truncated: bool
    original_length: int
    sanitized_length: int
    removed_null_bytes: int
    removed_control_chars: int


def sanitize_archive_path(internal_path: str) -> str:
    """
    Sanitize an internal archive path to prevent traversal attacks.

    - Removes leading slashes (absolute paths)
    - Removes .. components
    - Normalizes path separators

    Args:
        internal_path: Path within the archive

    Returns:
        Sanitized relative path
    """
    # Normalize to forward slashes
    path = internal_path.replace("\\", "/")

    # Parse and rebuild without dangerous components
    parts = PurePosixPath(path).parts

    safe_parts = []
    for part in parts:
        # Skip empty, dot, and double-dot components
        if part in ("", ".", ".."):
            continue
        # Skip if it looks like a drive letter (C:)
        if len(part) == 2 and part[1] == ":":
            continue
        safe_parts.append(part)

    return "/".join(safe_parts)


def check_banned_extension(filename: str, config: ArchiveConfig | None = None) -> None:
    """
    Check if a file has a banned extension.

    Args:
        filename: Filename to check
        config: Archive config (uses global if not provided)

    Raises:
        BannedExtensionError: If extension is banned
    """
    if config is None:
        config = get_config().archive

    # Get extension (lowercase)
    ext = PurePosixPath(filename).suffix.lower()

    if ext in config.banned_extensions:
        logger.warning(f"Blocked banned extension: {filename}")
        raise BannedExtensionError(filename, ext)


def validate_archive_security(
    members: list[ArchiveMemberInfo],
    compressed_total: int,
    config: ArchiveConfig | None = None,
) -> None:
    """
    Validate archive security constraints.

    Checks:
    - File count limit
    - Compression ratio (zip bomb detection)
    - Individual file sizes
    - Total uncompressed size

    Args:
        members: List of archive member info
        compressed_total: Total compressed size of archive
        config: Archive config (uses global if not provided)

    Raises:
        ArchiveSecurityError subclasses on violations
    """
    if config is None:
        config = get_config().archive

    # Check file count
    file_count = len([m for m in members if not m.is_dir])
    if file_count > config.max_file_count:
        logger.warning(f"Archive file count {file_count} exceeds limit {config.max_file_count}")
        raise ArchiveFileCountError(file_count, config.max_file_count)

    # Calculate total uncompressed size
    total_uncompressed = sum(m.size for m in members if not m.is_dir)

    # Check compression ratio
    if compressed_total > 0:
        ratio = total_uncompressed / compressed_total
        if ratio > config.max_compression_ratio:
            logger.warning(
                f"Archive compression ratio {ratio:.1f}:1 exceeds limit {config.max_compression_ratio}:1"
            )
            raise ZipBombDetectedError(
                compressed_size=compressed_total,
                uncompressed_size=total_uncompressed,
                ratio=ratio,
                max_ratio=config.max_compression_ratio,
            )

    # Check total uncompressed size
    if total_uncompressed > config.max_total_size:
        logger.warning(
            f"Archive uncompressed size {total_uncompressed} exceeds limit {config.max_total_size}"
        )
        raise ZipBombDetectedError(
            compressed_size=compressed_total,
            uncompressed_size=total_uncompressed,
            ratio=total_uncompressed / max(compressed_total, 1),
            max_ratio=config.max_compression_ratio,
        )

    logger.debug(
        f"Archive security check passed: {file_count} files, "
        f"{total_uncompressed} bytes uncompressed, ratio {total_uncompressed / max(compressed_total, 1):.1f}:1"
    )


def check_archive_nesting(current_depth: int, config: ArchiveConfig | None = None) -> None:
    """
    Check if archive nesting depth is within limits.

    Args:
        current_depth: Current nesting level (0 = top level)
        config: Archive config (uses global if not provided)

    Raises:
        ArchiveNestingError: If depth exceeds limit
    """
    if config is None:
        config = get_config().archive

    if current_depth >= config.max_nesting_depth:
        raise ArchiveNestingError(current_depth + 1, config.max_nesting_depth)


def find_archive_member(
    members: list[ArchiveMemberInfo],
    requested_path: str,
) -> ArchiveMemberInfo | None:
    """
    Find an archive member by sanitized path comparison.

    This function properly handles path traversal by sanitizing both the
    requested path AND each member's name before comparison.

    Args:
        members: List of archive member info
        requested_path: User-requested path (will be sanitized)

    Returns:
        Matching ArchiveMemberInfo or None if not found
    """
    safe_requested = sanitize_archive_path(requested_path)

    for member in members:
        # Sanitize the member's name for comparison
        safe_member_name = sanitize_archive_path(member.name)

        # Compare sanitized versions (handles both with/without trailing slash)
        if safe_member_name == safe_requested or safe_member_name.rstrip("/") == safe_requested:
            return member

    return None


def check_symlink_safety(member: ArchiveMemberInfo) -> None:
    """
    Verify archive member is not a symlink (security measure).

    Symlinks in archives can be used for path traversal attacks.

    Args:
        member: Archive member to check

    Raises:
        ArchiveSecurityError: If member is a symlink
    """
    from .exceptions import ArchiveSecurityError

    if member.is_symlink:
        logger.warning(f"Blocked symlink in archive: {member.name}")
        raise ArchiveSecurityError(
            f"Symlinks not allowed in archives: {member.name}",
            "symlink_blocked"
        )


# Control character pattern (excluding common whitespace)
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_output(content: str, config: OutputConfig | None = None) -> SanitizedContent:
    """
    Sanitize content for safe output to LLM context.

    Performs:
    - Null byte removal
    - Control character removal
    - Length truncation

    Args:
        content: Raw content string
        config: Output config (uses global if not provided)

    Returns:
        SanitizedContent with sanitization stats
    """
    if config is None:
        config = get_config().output

    original_length = len(content)
    removed_null = 0
    removed_control = 0

    # Remove null bytes
    if config.strip_null_bytes:
        null_count = content.count("\x00")
        if null_count > 0:
            content = content.replace("\x00", "")
            removed_null = null_count
            logger.debug(f"Removed {null_count} null bytes from content")

    # Remove control characters
    if config.strip_control_chars:

        def count_and_remove(match):
            nonlocal removed_control
            removed_control += 1
            return ""

        content = CONTROL_CHAR_PATTERN.sub(count_and_remove, content)
        if removed_control > 0:
            logger.debug(f"Removed {removed_control} control characters from content")

    # Truncate by characters
    was_truncated = False
    if len(content) > config.max_chars:
        content = content[: config.max_chars] + config.truncation_marker
        was_truncated = True
        logger.info(f"Truncated content from {original_length} to {config.max_chars} chars")

    # Truncate by lines
    lines = content.split("\n")
    if len(lines) > config.max_lines:
        lines = lines[: config.max_lines]
        lines.append(config.truncation_marker)
        content = "\n".join(lines)
        was_truncated = True
        logger.info(f"Truncated content to {config.max_lines} lines")

    return SanitizedContent(
        content=content,
        was_truncated=was_truncated,
        original_length=original_length,
        sanitized_length=len(content),
        removed_null_bytes=removed_null,
        removed_control_chars=removed_control,
    )


def sanitize_metadata(
    metadata: dict,
    config: OutputConfig | None = None,
) -> dict:
    """
    Sanitize metadata dict for safe output.

    - Limits number of fields
    - Truncates long values
    - Removes null bytes and control chars from values

    Args:
        metadata: Raw metadata dict
        config: Output config (uses global if not provided)

    Returns:
        Sanitized metadata dict
    """
    if config is None:
        config = get_config().output

    result = {}
    field_count = 0

    for key, value in metadata.items():
        if field_count >= config.max_metadata_fields:
            logger.debug(f"Metadata field limit reached, dropping remaining fields")
            break

        # Sanitize the value
        if isinstance(value, str):
            # Remove null bytes and control chars
            if config.strip_null_bytes:
                value = value.replace("\x00", "")
            if config.strip_control_chars:
                value = CONTROL_CHAR_PATTERN.sub("", value)
            # Truncate
            if len(value) > config.max_metadata_value_len:
                value = value[: config.max_metadata_value_len] + "..."
        elif isinstance(value, (list, tuple)):
            # Convert to string and truncate
            value = str(value)
            if len(value) > config.max_metadata_value_len:
                value = value[: config.max_metadata_value_len] + "..."
        elif isinstance(value, dict):
            # Recursively sanitize nested dicts (with reduced field limit)
            nested_config = OutputConfig(
                max_metadata_fields=min(10, config.max_metadata_fields - field_count),
                max_metadata_value_len=config.max_metadata_value_len,
                strip_null_bytes=config.strip_null_bytes,
                strip_control_chars=config.strip_control_chars,
            )
            value = sanitize_metadata(value, nested_config)

        result[key] = value
        field_count += 1

    return result


def sanitize_cell_content(content: str, config: OutputConfig | None = None) -> str:
    """
    Sanitize a single cell content for tabular data.

    Args:
        content: Cell content string
        config: Output config (uses global if not provided)

    Returns:
        Sanitized cell content
    """
    if config is None:
        config = get_config().output

    # Remove null bytes
    if config.strip_null_bytes:
        content = content.replace("\x00", "")

    # Remove control characters
    if config.strip_control_chars:
        content = CONTROL_CHAR_PATTERN.sub("", content)

    # Truncate
    if len(content) > config.max_cell_content:
        content = content[: config.max_cell_content] + "..."

    return content
