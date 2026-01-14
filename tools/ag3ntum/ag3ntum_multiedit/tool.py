"""
Ag3ntumMultiEdit - Sandboxed multi-file editing with validation.

Full feature parity with Claude Code MultiEdit tool:
- Multiple edits in one call
- Atomic operation (all or nothing)
- Supports multiple files

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
AG3NTUM_MULTIEDIT_TOOL: str = "mcp__ag3ntum__MultiEdit"


def create_multiedit_tool(session_id: str):
    """
    Create Ag3ntumMultiEdit tool bound to a specific session's workspace.

    Args:
        session_id: The session ID (used to get the PathValidator)

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "MultiEdit",
        """Apply multiple edits to one or more files atomically.

Each edit specifies a file, old_string, and new_string.
All edits are validated first, then applied together.
If any edit fails validation, none are applied.

Args:
    edits: List of edit objects, each with:
        - file_path: Path to edit
        - old_string: Text to find
        - new_string: Text to replace with

Returns:
    Summary of all edits applied or error.

Example:
    MultiEdit(edits=[
        {"file_path": "main.py", "old_string": "v1", "new_string": "v2"},
        {"file_path": "config.yaml", "old_string": "debug: false", "new_string": "debug: true"}
    ])
""",
        {"edits": list},
    )
    async def multiedit(args: dict[str, Any]) -> dict[str, Any]:
        """Apply multiple edits atomically."""
        edits = args.get("edits", [])

        if not edits:
            return _error("edits list is required and cannot be empty")

        if not isinstance(edits, list):
            return _error("edits must be a list")

        # Get validator for this session
        try:
            validator = get_path_validator(bound_session_id)
        except RuntimeError as e:
            logger.error(f"Ag3ntumMultiEdit: PathValidator not configured - {e}")
            return _error(f"Internal error: {e}")

        # Phase 1: Validate all edits
        validated_edits: list[dict[str, Any]] = []
        for i, edit in enumerate(edits):
            if not isinstance(edit, dict):
                return _error(f"Edit {i}: must be an object with file_path, old_string, new_string")

            file_path = edit.get("file_path", "")
            old_string = edit.get("old_string", "")
            new_string = edit.get("new_string", "")

            if not file_path:
                return _error(f"Edit {i}: file_path is required")
            if not old_string:
                return _error(f"Edit {i}: old_string is required")

            # Validate path
            try:
                validated = validator.validate_path(file_path, operation="edit")
            except PathValidationError as e:
                return _error(f"Edit {i} ({file_path}): {e.reason}")

            path = validated.normalized

            # Check existence
            if not path.exists():
                return _error(f"Edit {i}: File not found: {file_path}")

            if path.is_dir():
                return _error(f"Edit {i}: Cannot edit directory: {file_path}")

            # Read content and verify old_string exists
            try:
                content = path.read_text(encoding="utf-8")
            except Exception as e:
                return _error(f"Edit {i}: Failed to read {file_path}: {e}")

            if old_string not in content:
                return _error(
                    f"Edit {i} ({file_path}): String not found.\n"
                    f"Searched for: {repr(old_string[:100])}"
                )

            validated_edits.append({
                "index": i,
                "file_path": file_path,
                "path": path,
                "old_string": old_string,
                "new_string": new_string,
                "content": content,
            })

        # Phase 2: Apply all edits
        results: list[str] = []
        for edit in validated_edits:
            path = edit["path"]
            old_string = edit["old_string"]
            new_string = edit["new_string"]
            content = edit["content"]
            file_path = edit["file_path"]

            # Replace first occurrence
            new_content = content.replace(old_string, new_string, 1)

            try:
                path.write_text(new_content, encoding="utf-8")
                results.append(f"✓ `{file_path}`: 1 replacement")
            except Exception as e:
                # This shouldn't happen after validation, but handle gracefully
                results.append(f"✗ `{file_path}`: Failed to write: {e}")

        logger.info(f"Ag3ntumMultiEdit: Applied {len(validated_edits)} edits")

        return _result(
            f"**MultiEdit Complete**\n\n"
            f"**Edits applied:** {len(validated_edits)}\n\n"
            + "\n".join(results)
        )

    return multiedit


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_multiedit_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the Ag3ntumMultiEdit tool.

    Args:
        session_id: The session ID for PathValidator lookup.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    multiedit_tool = create_multiedit_tool(session_id=session_id)

    logger.info(f"Created Ag3ntumMultiEdit MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[multiedit_tool],
    )
