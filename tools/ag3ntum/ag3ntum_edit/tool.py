"""
Ag3ntumEdit - Sandboxed file editing with search/replace.

Full feature parity with Claude Code Edit tool:
- Search and replace within files
- Exact match requirement
- Multiple occurrence handling

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
AG3NTUM_EDIT_TOOL: str = "mcp__ag3ntum__Edit"


def create_edit_tool(session_id: str):
    """
    Create Ag3ntumEdit tool bound to a specific session's workspace.

    Args:
        session_id: The session ID (used to get the PathValidator)

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "Edit",
        """Edit a file by replacing specific text.

The old_string must match exactly (including whitespace and indentation).
Only the first occurrence is replaced unless replace_all is True.

Args:
    file_path: Path to edit (relative to workspace or /workspace/...)
    old_string: Exact text to find and replace
    new_string: Text to replace with
    replace_all: If True, replace all occurrences (default: False)

Returns:
    Confirmation with diff preview or error.

Examples:
    Edit(file_path="./main.py", old_string="def old_name():", new_string="def new_name():")
    Edit(file_path="config.yaml", old_string="debug: false", new_string="debug: true", replace_all=True)
""",
        {"file_path": str, "old_string": str, "new_string": str, "replace_all": bool},
    )
    async def edit(args: dict[str, Any]) -> dict[str, Any]:
        """Edit a file by replacing text."""
        file_path = args.get("file_path", "")
        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        replace_all = args.get("replace_all", False)

        if not file_path:
            return _error("file_path is required")
        if not old_string:
            return _error("old_string is required")

        # Get validator for this session
        try:
            validator = get_path_validator(bound_session_id)
        except RuntimeError as e:
            logger.error(f"Ag3ntumEdit: PathValidator not configured - {e}")
            return _error(f"Internal error: {e}")

        # Validate path
        try:
            validated = validator.validate_path(file_path, operation="edit")
        except PathValidationError as e:
            logger.warning(f"Ag3ntumEdit: Path validation failed - {e.reason}")
            return _error(f"Path validation failed: {e.reason}")

        path = validated.normalized

        # Check existence
        if not path.exists():
            return _error(f"File not found: {file_path}")

        if path.is_dir():
            return _error(f"Cannot edit directory: {file_path}")

        # Read current content
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return _error(f"Failed to read file: {e}")

        # Check if old_string exists
        count = content.count(old_string)
        if count == 0:
            return _error(
                f"String not found in file.\n"
                f"Make sure old_string matches exactly (including whitespace).\n"
                f"Searched for: {repr(old_string[:100])}"
            )

        if count > 1 and not replace_all:
            return _error(
                f"Found {count} occurrences of the string.\n"
                f"Use replace_all=True to replace all, or provide more context to make the match unique."
            )

        # Perform replacement
        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced = 1

        # Write back
        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return _error(f"Failed to write file: {e}")

        # Truncate for display
        old_display = old_string[:200] + ("..." if len(old_string) > 200 else "")
        new_display = new_string[:200] + ("..." if len(new_string) > 200 else "")

        logger.info(f"Ag3ntumEdit: Edited {file_path} ({replaced} replacements)")

        return _result(
            f"**Edited:** `{file_path}`\n"
            f"**Replacements:** {replaced}\n\n"
            f"**Changed:**\n```diff\n- {old_display}\n+ {new_display}\n```"
        )

    return edit


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_edit_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the Ag3ntumEdit tool.

    Args:
        session_id: The session ID for PathValidator lookup.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    edit_tool = create_edit_tool(session_id=session_id)

    logger.info(f"Created Ag3ntumEdit MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[edit_tool],
    )
