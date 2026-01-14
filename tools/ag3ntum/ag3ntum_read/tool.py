"""
Ag3ntumRead - Sandboxed file reading with validation.

Full feature parity with Claude Code Read tool:
- Read entire files or specific line ranges
- Binary file detection
- Large file handling with preview

Security: Uses Ag3ntumPathValidator to ensure all paths are within
the session workspace. The validator translates agent-provided paths
(like /workspace/foo.txt) to real Docker filesystem paths.
"""
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from src.core.path_validator import get_path_validator, PathValidationError

logger = logging.getLogger(__name__)

# Tool name constant
AG3NTUM_READ_TOOL: str = "mcp__ag3ntum__Read"


def create_read_tool(session_id: str):
    """
    Create Ag3ntumRead tool bound to a specific session's workspace.

    Args:
        session_id: The session ID (used to get the PathValidator)

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "Read",
        """Read file contents from the workspace.

Args:
    file_path: Path to the file (relative to workspace or /workspace/...)
    offset: Starting line number (1-indexed, optional)
    limit: Maximum lines to read (optional)

Returns:
    File contents with line numbers, or error message.

Examples:
    Read(file_path="./src/main.py")
    Read(file_path="config.yaml", offset=10, limit=20)
    Read(file_path="/workspace/output.txt")
""",
        {"file_path": str, "offset": int, "limit": int},
    )
    async def read(args: dict[str, Any]) -> dict[str, Any]:
        """Read file contents with line numbers."""
        file_path = args.get("file_path", "")
        offset = args.get("offset", 1)
        limit = args.get("limit")

        if not file_path:
            return _error("file_path is required")

        # Get validator for this session
        try:
            validator = get_path_validator(bound_session_id)
        except RuntimeError as e:
            logger.error(f"Ag3ntumRead: PathValidator not configured - {e}")
            return _error(f"Internal error: {e}")

        # Validate path
        try:
            validated = validator.validate_path(file_path, operation="read")
        except PathValidationError as e:
            logger.warning(f"Ag3ntumRead: Path validation failed - {e.reason}")
            return _error(f"Path validation failed: {e.reason}")

        path = validated.normalized

        # Check existence
        if not path.exists():
            return _error(f"File not found: {file_path}")

        if path.is_dir():
            return _error(f"Cannot read directory: {file_path}. Use LS tool instead.")

        # Check if binary
        try:
            with open(path, "rb") as f:
                chunk = f.read(8192)
                if b"\x00" in chunk:
                    size = path.stat().st_size
                    return _result(
                        f"Binary file detected ({size} bytes). Cannot display contents."
                    )
        except Exception as e:
            return _error(f"Failed to read file: {e}")

        # Read content
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            total_lines = len(lines)

            # Apply offset/limit
            start_idx = max(0, offset - 1)
            end_idx = start_idx + limit if limit else len(lines)
            selected_lines = lines[start_idx:end_idx]

            # Format with line numbers
            numbered_lines = []
            for i, line in enumerate(selected_lines, start=start_idx + 1):
                numbered_lines.append(f"{i:6}|{line}")

            output = "\n".join(numbered_lines)

            # Add truncation notice
            if limit and end_idx < total_lines:
                output += f"\n\n... ({total_lines - end_idx} more lines)"

            logger.info(
                f"Ag3ntumRead: Read {len(selected_lines)} lines from {file_path}"
            )
            return _result(output)

        except Exception as e:
            return _error(f"Failed to read file: {e}")

    return read


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_read_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the Ag3ntumRead tool.

    Args:
        session_id: The session ID for PathValidator lookup.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    read_tool = create_read_tool(session_id=session_id)

    logger.info(f"Created Ag3ntumRead MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[read_tool],
    )
