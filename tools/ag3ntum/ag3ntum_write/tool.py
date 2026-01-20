"""
Ag3ntumWrite - Sandboxed file writing with validation.

Full feature parity with Claude Code Write tool:
- Create new files
- Overwrite existing files (requires explicit flag)
- Create parent directories

Security:
- Uses Ag3ntumPathValidator to ensure all paths are within allowed mounts
- The validator translates agent-provided paths (like /workspace/foo.txt)
  to real Docker filesystem paths
- Verifies path is writable before attempting write
- Verifies file was actually written after operation

Sensitive Data:
- Scans content for API keys, tokens, passwords before writing
- Detected secrets are redacted with same-length placeholders to preserve formatting
"""
import logging
import os
import stat
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import create_sdk_mcp_server, tool

from src.core.path_validator import get_path_validator, PathValidationError, get_resolver_for_session
from src.security import scan_and_redact, is_scanner_enabled

logger = logging.getLogger(__name__)

# Tool name constant
AG3NTUM_WRITE_TOOL: str = "mcp__ag3ntum__Write"


def _is_path_writable(path: Path) -> tuple[bool, str]:
    """
    Check if a path is writable.

    For existing files: checks if file is writable.
    For non-existing files: checks if parent directory is writable.

    Args:
        path: The path to check

    Returns:
        Tuple of (is_writable, reason_if_not)
    """
    if path.exists():
        # File exists - check if it's writable
        if path.is_dir():
            return False, "Path is a directory, not a file"

        # Check file permissions
        try:
            file_stat = path.stat()
            # Check if file is read-only (no write permission for owner)
            if not (file_stat.st_mode & stat.S_IWUSR):
                return False, "File is read-only (no write permission)"

            # Also try to open for writing to verify actual access
            # This catches cases like immutable files or ACLs
            with open(path, 'a') as f:
                pass
            return True, ""
        except PermissionError:
            return False, "Permission denied - cannot write to this file"
        except OSError as e:
            return False, f"Cannot write to file: {e}"
    else:
        # File doesn't exist - check if parent is writable
        parent = path.parent
        if not parent.exists():
            # Parent doesn't exist yet - check grandparent
            # Find the first existing ancestor
            existing_ancestor = parent
            while not existing_ancestor.exists() and existing_ancestor != existing_ancestor.parent:
                existing_ancestor = existing_ancestor.parent

            if not existing_ancestor.exists():
                return False, "Cannot determine if path is writable - no existing ancestor"

            parent = existing_ancestor

        # Check if parent directory is writable
        try:
            if not os.access(parent, os.W_OK):
                return False, f"Parent directory is not writable: {parent.name}"
            return True, ""
        except Exception as e:
            return False, f"Cannot verify write access to parent directory: {e}"


def _verify_file_written(path: Path, expected_content: str) -> tuple[bool, str]:
    """
    Verify that a file was written correctly.

    Args:
        path: Path to the file
        expected_content: The content that should have been written

    Returns:
        Tuple of (success, error_message_if_failed)
    """
    # Check file exists
    if not path.exists():
        return False, "File was not created - write operation may have failed silently"

    # Check file size
    expected_size = len(expected_content.encode("utf-8"))
    try:
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            return False, (
                f"File size mismatch: expected {expected_size} bytes, "
                f"got {actual_size} bytes. Write may have been interrupted or truncated."
            )
    except OSError as e:
        return False, f"Cannot verify file size: {e}"

    # Optionally verify content hash for critical files
    # (not implemented to avoid performance overhead for large files)

    return True, ""


async def _write_impl(
    session_id: str,
    file_path: str,
    content: str,
    overwrite_existing: Any = None,
) -> dict[str, Any]:
    """
    Core write implementation - testable without MCP tool wrapper.

    Args:
        session_id: The session ID (used to get the PathValidator)
        file_path: Path to write (relative to workspace or /workspace/...)
        content: Content to write to the file
        overwrite_existing: Set to true to overwrite existing files (default: false)

    Returns:
        Dict with result or error
    """
    # Normalize overwrite flag - default to False
    # Handle various falsy values: None, False, 0, "0", "false", ""
    if overwrite_existing is None:
        overwrite = False
    elif isinstance(overwrite_existing, bool):
        overwrite = overwrite_existing
    elif isinstance(overwrite_existing, (int, float)):
        overwrite = bool(overwrite_existing)
    elif isinstance(overwrite_existing, str):
        overwrite = overwrite_existing.lower() in ("true", "1", "yes")
    else:
        overwrite = bool(overwrite_existing)

    if not file_path:
        return _error("file_path is required")

    # Get validator for this session
    try:
        validator = get_path_validator(session_id)
    except RuntimeError as e:
        logger.error(f"Ag3ntumWrite: PathValidator not configured - {e}")
        return _error("Internal error: session not properly configured")

    # Validate path (security checks)
    try:
        validated = validator.validate_path(file_path, operation="write")
    except PathValidationError as e:
        logger.warning(f"Ag3ntumWrite: Path validation failed for '{file_path}' - {e.reason}")
        return _error(f"Path validation failed: {e.reason}")

    path = validated.normalized

    # Get display path - prefer sandbox path format for user display
    # This shows the path in the format the agent understands
    try:
        resolver = get_resolver_for_session(session_id)
        if resolver:
            display_path = resolver.normalize(file_path)
        else:
            display_path = str(path)
    except Exception:
        display_path = str(path)

    # Check if path is writable (fail fast)
    is_writable, write_error = _is_path_writable(path)
    if not is_writable:
        logger.warning(f"Ag3ntumWrite: Path not writable '{display_path}' - {write_error}")
        return _error(f"Cannot write to path: {write_error}")

    # Check if file already exists
    file_existed = path.exists()
    if file_existed and not overwrite:
        logger.info(f"Ag3ntumWrite: File exists, overwrite not requested: {display_path}")
        return _error(
            f"File already exists: `{display_path}`. "
            "To overwrite, set overwrite_existing=true."
        )

    # Create parent directories if needed
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        return _error("Permission denied: cannot create parent directories")
    except OSError as e:
        return _error(f"Failed to create directories: {e}")

    # Scan content for sensitive data before writing
    secrets_redacted = 0
    secret_types: list[str] = []
    content_to_write = content

    if is_scanner_enabled():
        try:
            scan_result = scan_and_redact(content)
            if scan_result.has_secrets:
                content_to_write = scan_result.redacted_text
                secrets_redacted = scan_result.secret_count
                secret_types = list(scan_result.secret_types)
                logger.warning(
                    f"Ag3ntumWrite: Redacted {secrets_redacted} secrets "
                    f"({', '.join(secret_types)}) in {display_path}"
                )
        except Exception as e:
            logger.warning(f"Ag3ntumWrite: Failed to scan content - {e}")

    # Write content
    try:
        path.write_text(content_to_write, encoding="utf-8")
    except PermissionError:
        return _error("Permission denied: cannot write to file")
    except OSError as e:
        return _error(f"Failed to write file: {e}")

    # Verify file was actually written
    verified, verify_error = _verify_file_written(path, content_to_write)
    if not verified:
        logger.error(f"Ag3ntumWrite: Write verification failed for {display_path} - {verify_error}")
        return _error(f"Write verification failed: {verify_error}")

    # Calculate stats
    action = "Overwrote" if file_existed else "Created"
    size = len(content_to_write.encode("utf-8"))
    lines = len(content_to_write.splitlines())

    logger.info(
        f"Ag3ntumWrite: {action} {display_path} ({size} bytes, {lines} lines)"
    )

    # Build result message with actual (display) path
    result_msg = (
        f"**{action} file:** `{display_path}`\n"
        f"**Size:** {size} bytes\n"
        f"**Lines:** {lines}"
    )

    # Add security notice if secrets were redacted
    if secrets_redacted > 0:
        result_msg += (
            f"\n\n**Security Notice:** {secrets_redacted} sensitive value(s) "
            f"({', '.join(secret_types)}) were automatically redacted."
        )

    return _result(result_msg)


def create_write_tool(session_id: str):
    """
    Create Ag3ntumWrite tool bound to a specific session's workspace.

    Args:
        session_id: The session ID (used to get the PathValidator)

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "Write",
        """Write content to a file in the workspace.

Creates the file if it doesn't exist. To overwrite an existing file,
you must explicitly set overwrite_existing=true.

Parent directories are created automatically if they don't exist.

Args:
    file_path: Path to write (relative to workspace or /workspace/...)
    content: Content to write to the file
    overwrite_existing: Set to true to overwrite existing files (default: false)

Returns:
    Confirmation message with actual path and size, or error.

Examples:
    Write(file_path="./output.txt", content="Hello, World!")
    Write(file_path="src/new_module.py", content="def hello(): pass")
    Write(file_path="/workspace/data.json", content='{"key": "value"}', overwrite_existing=true)
""",
        {"file_path": str, "content": str, "overwrite_existing": Optional[bool]},
    )
    async def write(args: dict[str, Any]) -> dict[str, Any]:
        """Write content to a file."""
        return await _write_impl(
            session_id=bound_session_id,
            file_path=args.get("file_path", ""),
            content=args.get("content", ""),
            overwrite_existing=args.get("overwrite_existing"),
        )

    return write


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_write_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the Ag3ntumWrite tool.

    Args:
        session_id: The session ID for PathValidator lookup.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    write_tool = create_write_tool(session_id=session_id)

    logger.info(f"Created Ag3ntumWrite MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[write_tool],
    )
