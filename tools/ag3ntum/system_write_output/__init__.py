"""
Ag3ntum System Tools - Tools that cannot be disabled via permissions.

These tools are critical for agent operation and output reporting.
They are always available regardless of permission settings.

Usage:
    from ag3ntum.system_write_output import (
        create_ag3ntum_mcp_server,
        SYSTEM_TOOLS,
    )

    # Get MCP server for ClaudeAgentOptions
    mcp_server = create_ag3ntum_mcp_server(workspace_path)

    # Add to mcp_servers in ClaudeAgentOptions
    options = ClaudeAgentOptions(
        mcp_servers={"ag3ntum": mcp_server},
        allowed_tools=list(SYSTEM_TOOLS),  # Pre-approved
    )
"""
from .tool import (
    SYSTEM_TOOLS,
    create_ag3ntum_mcp_server,
    create_write_output_tool,
    is_system_tool,
)

__all__ = [
    "SYSTEM_TOOLS",
    "create_ag3ntum_mcp_server",
    "create_write_output_tool",
    "is_system_tool",
]
