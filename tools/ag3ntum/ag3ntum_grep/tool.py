"""
Ag3ntumGrep - Sandboxed text search with validation.

Full feature parity with Claude Code Grep tool:
- Regex pattern search
- Context lines before/after
- Case-insensitive option

Security: Uses Ag3ntumPathValidator to ensure all paths are within
the session workspace. The validator translates agent-provided paths
(like /workspace/foo.txt) to real Docker filesystem paths.
"""
import logging
import re
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from src.core.path_validator import get_path_validator, PathValidationError

logger = logging.getLogger(__name__)

# Tool name constant
AG3NTUM_GREP_TOOL: str = "mcp__ag3ntum__Grep"

# Maximum results to return
MAX_RESULTS: int = 1000
DEFAULT_CONTEXT_LINES: int = 3


def create_grep_tool(session_id: str):
    """
    Create Ag3ntumGrep tool bound to a specific session's workspace.

    Args:
        session_id: The session ID (used to get the PathValidator)

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "Grep",
        """Search for a pattern in files within the workspace.

Args:
    pattern: Regex pattern to search for
    path: File or directory to search (default: workspace root)
    include: Glob pattern for files to include (e.g., "*.py")
    ignore_case: Case-insensitive search (default: False)
    context: Number of context lines before/after match (default: 3)

Returns:
    Matching lines with context, or error.

Examples:
    Grep(pattern="def main", path="./src")
    Grep(pattern="TODO", include="*.py", ignore_case=True)
    Grep(pattern="error", path="/workspace/logs", context=5)
""",
        {"pattern": str, "path": str, "include": str, "ignore_case": bool, "context": int},
    )
    async def grep(args: dict[str, Any]) -> dict[str, Any]:
        """Search for a pattern in files."""
        pattern = args.get("pattern", "")
        base_path = args.get("path", ".")
        include = args.get("include", "**/*")
        ignore_case = args.get("ignore_case", False)
        context = args.get("context", DEFAULT_CONTEXT_LINES)

        if not pattern:
            return _error("pattern is required")

        # Compile regex
        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return _error(f"Invalid regex pattern: {e}")

        # Get validator for this session
        try:
            validator = get_path_validator(bound_session_id)
        except RuntimeError as e:
            logger.error(f"Ag3ntumGrep: PathValidator not configured - {e}")
            return _error(f"Internal error: {e}")

        # Validate base path
        try:
            validated = validator.validate_path(base_path, operation="grep", allow_directory=True)
        except PathValidationError as e:
            logger.warning(f"Ag3ntumGrep: Path validation failed - {e.reason}")
            return _error(f"Path validation failed: {e.reason}")

        search_path = validated.normalized

        # Check if path exists
        if not search_path.exists():
            return _error(f"Path not found: {base_path}")

        # Collect files to search
        if search_path.is_file():
            files_to_search = [search_path]
        else:
            files_to_search = [f for f in search_path.glob(include) if f.is_file()]

        # Search files
        results: list[str] = []
        total_matches = 0
        workspace = validator.workspace

        for file_path in files_to_search:
            if total_matches >= MAX_RESULTS:
                break

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
            except Exception:
                continue  # Skip unreadable files

            # Get relative path for display
            try:
                rel_path = file_path.relative_to(workspace)
            except ValueError:
                rel_path = file_path

            file_matches: list[tuple[int, str]] = []
            for i, line in enumerate(lines):
                if regex.search(line):
                    file_matches.append((i, line))
                    total_matches += 1
                    if total_matches >= MAX_RESULTS:
                        break

            if file_matches:
                results.append(f"\n**{rel_path}**")
                for line_num, line in file_matches:
                    # Add context
                    start = max(0, line_num - context)
                    end = min(len(lines), line_num + context + 1)

                    for ctx_num in range(start, end):
                        prefix = ">" if ctx_num == line_num else " "
                        results.append(f"{prefix} {ctx_num + 1}: {lines[ctx_num]}")
                    results.append("")  # Blank line between matches

        logger.info(f"Ag3ntumGrep: Found {total_matches} matches for '{pattern}'")

        if not results:
            return _result(f"No matches found for pattern: `{pattern}`")

        truncated = total_matches >= MAX_RESULTS
        header = f"**Found {total_matches} matches**"
        if truncated:
            header += f" (showing first {MAX_RESULTS})"

        return _result(header + "\n" + "\n".join(results))

    return grep


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_grep_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the Ag3ntumGrep tool.

    Args:
        session_id: The session ID for PathValidator lookup.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    grep_tool = create_grep_tool(session_id=session_id)

    logger.info(f"Created Ag3ntumGrep MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[grep_tool],
    )
