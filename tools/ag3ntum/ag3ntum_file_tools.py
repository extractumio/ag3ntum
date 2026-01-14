"""
Combined Ag3ntum tools MCP server.

Creates a single MCP server named 'ag3ntum' containing ALL Ag3ntum tools.
This ensures consistent tool naming: mcp__ag3ntum__Read, mcp__ag3ntum__Write, 
mcp__ag3ntum__Bash, etc.
"""
import logging
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import create_sdk_mcp_server

from .ag3ntum_read import create_read_tool
from .ag3ntum_write import create_write_tool
from .ag3ntum_edit import create_edit_tool
from .ag3ntum_multiedit import create_multiedit_tool
from .ag3ntum_glob import create_glob_tool
from .ag3ntum_grep import create_grep_tool
from .ag3ntum_ls import create_ls_tool
from .ag3ntum_webfetch import create_webfetch_tool
from .ag3ntum_bash import create_bash_tool

logger = logging.getLogger(__name__)


def create_ag3ntum_tools_mcp_server(
    session_id: str,
    workspace_path: Optional[Path] = None,
    sandbox_executor: Optional[Any] = None,
    include_bash: bool = True,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
) -> Any:
    """
    Create a unified MCP server containing ALL Ag3ntum tools.

    This creates a single MCP server with all file manipulation and command execution tools,
    ensuring consistent naming: mcp__ag3ntum__Read, mcp__ag3ntum__Write, mcp__ag3ntum__Bash, etc.

    Args:
        session_id: The session ID for PathValidator lookup.
        workspace_path: Workspace path for Bash tool (required if include_bash=True).
        sandbox_executor: Sandbox executor for Bash tool security.
        include_bash: Whether to include the Bash tool (default: True).
        server_name: MCP server name (default: "ag3ntum").
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.

    Tool Names Generated:
        - mcp__ag3ntum__Bash (if include_bash=True)
        - mcp__ag3ntum__Read
        - mcp__ag3ntum__Write
        - mcp__ag3ntum__Edit
        - mcp__ag3ntum__MultiEdit
        - mcp__ag3ntum__Glob
        - mcp__ag3ntum__Grep
        - mcp__ag3ntum__LS
        - mcp__ag3ntum__WebFetch
    """
    tools = []
    
    # Add Bash tool if requested and workspace_path provided
    if include_bash:
        if workspace_path is None:
            logger.warning("Cannot create Bash tool: workspace_path is required")
        else:
            bash_tool = create_bash_tool(
                workspace_path=workspace_path,
                timeout_seconds=300,
                default_preview_lines=30,
                sandbox_executor=sandbox_executor,
            )
            tools.append(bash_tool)
            logger.debug("Added Bash tool to unified MCP server")
    
    # Add all file tools bound to this session
    # Note: WebFetch doesn't need session_id as it doesn't access filesystem
    tools.extend([
        create_read_tool(session_id=session_id),
        create_write_tool(session_id=session_id),
        create_edit_tool(session_id=session_id),
        create_multiedit_tool(session_id=session_id),
        create_glob_tool(session_id=session_id),
        create_grep_tool(session_id=session_id),
        create_ls_tool(session_id=session_id),
        create_webfetch_tool(),  # No session_id needed
    ])

    logger.info(
        f"Created unified Ag3ntum MCP server for session {session_id} "
        f"with {len(tools)} tools (Bash: {include_bash})"
    )

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=tools,
    )
