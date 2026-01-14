"""
Ag3ntumBash Tool - Execute bash commands with automatic output capture.

This tool executes bash commands and automatically captures output to 
./.tmp/cmd/ directory, returning metadata and preview lines instead of
full output to prevent context bloat.

SECURITY: When a SandboxExecutor is provided, commands are wrapped in
bubblewrap for filesystem isolation. This is REQUIRED for production use.

Usage:
    from ag3ntum.ag3ntum_bash import (
        create_ag3ntum_bash_mcp_server,
        AG3NTUM_BASH_TOOL,
        DEFAULT_TIMEOUT_SECONDS,
        DEFAULT_PREVIEW_LINES,
    )
    from src.core.sandbox import SandboxExecutor

    # Get MCP server with sandbox for security
    mcp_server = create_ag3ntum_bash_mcp_server(
        workspace_path,
        timeout_seconds=600,  # 10 minutes
        default_preview_lines=50,
        sandbox_executor=sandbox_executor,  # REQUIRED for production
    )

    # Add to mcp_servers in ClaudeAgentOptions
    # NOTE: Do NOT add to allowed_tools - let it go through permission callback
    options = ClaudeAgentOptions(
        mcp_servers={"ag3ntum": mcp_server},
        # Ag3ntumBash goes through can_use_tool for dangerous command checking
    )
"""
from .tool import (
    # Constants
    AG3NTUM_BASH_TOOL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_PREVIEW_MODE,
    DEFAULT_PREVIEW_LINES,
    MAX_PREVIEW_LINES,
    OUTPUT_DIR,
    # Functions
    create_ag3ntum_bash_mcp_server,
    create_bash_tool,
    is_ag3ntum_bash_tool,
)

__all__ = [
    # Constants
    "AG3NTUM_BASH_TOOL",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_PREVIEW_MODE",
    "DEFAULT_PREVIEW_LINES",
    "MAX_PREVIEW_LINES",
    "OUTPUT_DIR",
    # Functions
    "create_ag3ntum_bash_mcp_server",
    "create_bash_tool",
    "is_ag3ntum_bash_tool",
]
