"""
Ag3ntumWrite - Sandboxed file writing with validation.

Full feature parity with Claude Code Write tool:
- Create new files
- Overwrite existing files
- Create parent directories

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
AG3NTUM_WRITE_TOOL: str = "mcp__ag3ntum__Write"


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

Creates the file if it doesn't exist, overwrites if it does.
Parent directories are created automatically.

Args:
    file_path: Path to write (relative to workspace or /workspace/...)
    content: Content to write to the file

Returns:
    Confirmation message or error.

Examples:
    Write(file_path="./output.txt", content="Hello, World!")
    Write(file_path="src/new_module.py", content="def hello(): pass")
    Write(file_path="/workspace/data.json", content='{"key": "value"}')
""",
        {"file_path": str, "content": str},
    )
    async def write(args: dict[str, Any]) -> dict[str, Any]:
        """Write content to a file."""
        file_path = args.get("file_path", "")
        content = args.get("content", "")

        if not file_path:
            return _error("file_path is required")

        # Get validator for this session
        try:
            validator = get_path_validator(bound_session_id)
        except RuntimeError as e:
            logger.error(f"Ag3ntumWrite: PathValidator not configured - {e}")
            return _error(f"Internal error: {e}")

        # Validate path
        try:
            validated = validator.validate_path(file_path, operation="write")
        except PathValidationError as e:
            logger.warning(f"Ag3ntumWrite: Path validation failed - {e.reason}")
            return _error(f"Path validation failed: {e.reason}")

        path = validated.normalized

        # Create parent directories
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return _error(f"Failed to create directories: {e}")

        # Write content
        try:
            existed = path.exists()
            path.write_text(content, encoding="utf-8")

            action = "Updated" if existed else "Created"
            size = len(content.encode("utf-8"))
            lines = len(content.splitlines())

            logger.info(
                f"Ag3ntumWrite: {action} {file_path} ({size} bytes, {lines} lines)"
            )

            return _result(
                f"**{action} file:** `{file_path}`\n"
                f"**Size:** {size} bytes\n"
                f"**Lines:** {lines}"
            )

        except Exception as e:
            return _error(f"Failed to write file: {e}")

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
