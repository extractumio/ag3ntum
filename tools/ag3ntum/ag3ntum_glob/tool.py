"""
Ag3ntumGlob - Sandboxed glob pattern matching with validation.

Full feature parity with Claude Code Glob tool:
- Pattern matching for file discovery
- Recursive search
- Results limited to workspace

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
AG3NTUM_GLOB_TOOL: str = "mcp__ag3ntum__Glob"

# Maximum results to return
MAX_RESULTS: int = 10000


def create_glob_tool(session_id: str):
    """
    Create Ag3ntumGlob tool bound to a specific session's workspace.

    Args:
        session_id: The session ID (used to get the PathValidator)

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "Glob",
        """Find files matching a glob pattern within the workspace.

Args:
    pattern: Glob pattern (e.g., "**/*.py", "src/*.txt")
    path: Base directory for search (default: workspace root)

Returns:
    List of matching file paths, or error.

Examples:
    Glob(pattern="**/*.py")
    Glob(pattern="*.yaml", path="./config")
    Glob(pattern="test_*.py", path="/workspace/tests")
""",
        {"pattern": str, "path": str},
    )
    async def glob(args: dict[str, Any]) -> dict[str, Any]:
        """Find files matching a glob pattern."""
        pattern = args.get("pattern", "")
        base_path = args.get("path", ".")

        if not pattern:
            return _error("pattern is required")

        # Get validator for this session
        try:
            validator = get_path_validator(bound_session_id)
        except RuntimeError as e:
            logger.error(f"Ag3ntumGlob: PathValidator not configured - {e}")
            return _error(f"Internal error: {e}")

        # Validate base path
        try:
            validated = validator.validate_path(base_path, operation="glob", allow_directory=True)
        except PathValidationError as e:
            logger.warning(f"Ag3ntumGlob: Path validation failed - {e.reason}")
            return _error(f"Path validation failed: {e.reason}")

        search_path = validated.normalized

        # Check if directory exists
        if not search_path.exists():
            return _error(f"Directory not found: {base_path}")

        if not search_path.is_dir():
            return _error(f"Not a directory: {base_path}")

        # Execute glob
        try:
            matches = list(search_path.glob(pattern))

            # Filter to only files (not directories) and limit results
            files = [m for m in matches if m.is_file()][:MAX_RESULTS]

            # Convert to relative paths for display
            workspace = validator.workspace
            relative_paths = []
            for f in files:
                try:
                    rel = f.relative_to(workspace)
                    relative_paths.append(str(rel))
                except ValueError:
                    # Should not happen after validation, but be safe
                    relative_paths.append(str(f))

            # Sort for consistent output
            relative_paths.sort()

            total_matches = len(matches)
            truncated = total_matches > MAX_RESULTS

            logger.info(
                f"Ag3ntumGlob: Found {len(relative_paths)} files matching '{pattern}'"
            )

            if not relative_paths:
                return _result(f"No files found matching pattern: `{pattern}`")

            output = f"**Found {len(relative_paths)} files**"
            if truncated:
                output += f" (showing first {MAX_RESULTS} of {total_matches})"
            output += f"\n\n```\n{chr(10).join(relative_paths)}\n```"

            return _result(output)

        except Exception as e:
            return _error(f"Glob failed: {e}")

    return glob


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_glob_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the Ag3ntumGlob tool.

    Args:
        session_id: The session ID for PathValidator lookup.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    glob_tool = create_glob_tool(session_id=session_id)

    logger.info(f"Created Ag3ntumGlob MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[glob_tool],
    )
