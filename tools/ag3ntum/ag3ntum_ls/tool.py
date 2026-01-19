"""
Ag3ntumLS - Sandboxed directory listing with validation.

Full feature parity with Claude Code LS tool:
- Directory listing
- Recursive option
- Hidden files option

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
AG3NTUM_LS_TOOL: str = "mcp__ag3ntum__LS"

# Maximum entries to return
MAX_ENTRIES: int = 1000


def create_ls_tool(session_id: str):
    """
    Create Ag3ntumLS tool bound to a specific session's workspace.

    Args:
        session_id: The session ID (used to get the PathValidator)

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "LS",
        """List directory contents within the workspace.

Args:
    path: Directory to list (default: workspace root)
    recursive: List recursively (default: False)
    include_hidden: Include hidden files (default: False)

Returns:
    Directory listing with file types and sizes.

Examples:
    LS()
    LS(path="./src", recursive=True)
    LS(path="/workspace/config", include_hidden=True)
""",
        {"path": str, "recursive": bool, "include_hidden": bool},
    )
    async def ls(args: dict[str, Any]) -> dict[str, Any]:
        """List directory contents."""
        dir_path = args.get("path", ".")
        recursive = args.get("recursive", False)
        include_hidden = args.get("include_hidden", False)

        # Get validator for this session
        try:
            validator = get_path_validator(bound_session_id)
        except RuntimeError as e:
            logger.error(f"Ag3ntumLS: PathValidator not configured - {e}")
            return _error(f"Internal error: {e}")

        # Validate path
        try:
            validated = validator.validate_path(dir_path, operation="list", allow_directory=True)
        except PathValidationError as e:
            logger.warning(f"Ag3ntumLS: Path validation failed - {e.reason}")
            return _error(f"Path validation failed: {e.reason}")

        path = validated.normalized

        # Check if directory exists
        if not path.exists():
            return _error(f"Directory not found: {dir_path}")

        if not path.is_dir():
            return _error(f"Not a directory: {dir_path}")

        # List contents
        try:
            entries: list[str] = []
            workspace = validator.workspace

            if recursive:
                items = list(path.rglob("*"))
            else:
                items = list(path.iterdir())

            # Filter and format
            for item in items[:MAX_ENTRIES]:
                # Skip hidden files unless requested
                if not include_hidden and item.name.startswith("."):
                    continue

                # Get relative path
                try:
                    rel = item.relative_to(workspace)
                except ValueError:
                    rel = item

                # Format entry
                if item.is_dir():
                    entries.append(f"📁 {rel}/")
                else:
                    size = item.stat().st_size
                    size_str = _format_size(size)
                    entries.append(f"📄 {rel} ({size_str})")

            # Sort: directories first, then files
            entries.sort(key=lambda x: (not x.startswith("📁"), x.lower()))

            total_items = len(items)
            truncated = total_items > MAX_ENTRIES

            logger.info(f"Ag3ntumLS: Listed {len(entries)} entries in {dir_path}")

            if not entries:
                return _result(f"Directory is empty: `{dir_path}`")

            header = f"**Contents of `{dir_path}`**"
            if truncated:
                header += f" (showing first {MAX_ENTRIES} of {total_items})"

            return _result(header + f"\n\n```\n{chr(10).join(entries)}\n```")

        except Exception as e:
            return _error(f"Failed to list directory: {e}")

    return ls


def _format_size(size: int) -> str:
    """Format file size in human-readable format."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_ls_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the Ag3ntumLS tool.

    Args:
        session_id: The session ID for PathValidator lookup.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    ls_tool = create_ls_tool(session_id=session_id)

    logger.info(f"Created Ag3ntumLS MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[ls_tool],
    )
