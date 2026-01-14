"""
SystemWriteOutput tool implementation.

Deprecated: output.yaml persistence has been removed in favor of
streamed events. This tool now returns a warning without writing files.
"""
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

logger = logging.getLogger(__name__)


# System tools that cannot be disabled via permissions.
# These tools are critical for agent operation and output reporting.
# Add new system tools to this list as needed.
SYSTEM_TOOLS: list[str] = [
    "mcp__ag3ntum__WriteOutput",
    "mcp__ag3ntum__Bash",
]


def create_write_output_tool(workspace_path: Path, session_id: str = ""):
    """
    Create the write_output tool function with workspace binding.

    Args:
        workspace_path: Absolute path to the workspace directory.
        session_id: Session ID to include in output (optional).

    Returns:
        Tool function decorated with @tool.
    """
    _ = workspace_path
    bound_session_id = session_id

    @tool(
        "WriteOutput",
        """Deprecated. Output persistence is handled via streamed events.""",
        {
            "status": str,
            "error": str,
            "comments": str,
            "output": str,
            "result_files": list,
        }
    )
    async def write_output(args: dict[str, Any]) -> dict[str, Any]:
        """
        Deprecated: returns a warning without writing output.
        """
        logger.warning(
            "SystemWriteOutput is deprecated; session_id=%s args=%s",
            bound_session_id,
            args,
        )
        return {
            "content": [{
                "type": "text",
                "text": "WriteOutput is deprecated. Stream results directly in assistant messages."
            }]
        }

    return write_output


def create_ag3ntum_mcp_server(
    workspace_path: Path,
    session_id: str = "",
    server_name: str = "ag3ntum",
    version: str = "1.0.0"
):
    """
    Create an in-process MCP server for the SystemWriteOutput tool.

    Args:
        workspace_path: Absolute path to session workspace.
        session_id: Session ID to embed in output.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    # Create the bound tool
    write_output_tool = create_write_output_tool(workspace_path, session_id)

    logger.info(
        f"Created SystemWriteOutput MCP server: "
        f"workspace={workspace_path}, session_id={session_id}"
    )

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[write_output_tool]
    )


def is_system_tool(tool_name: str) -> bool:
    """
    Check if a tool is a system tool that bypasses permissions.

    System tools are critical for agent operation and cannot be
    disabled by the permission system.

    Args:
        tool_name: Full tool name (may include mcp__ prefix).

    Returns:
        True if the tool is a system tool.
    """
    return tool_name in SYSTEM_TOOLS
