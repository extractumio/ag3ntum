"""
Combined Ag3ntum tools MCP server.

Creates a single MCP server named 'ag3ntum' containing ALL Ag3ntum tools.
This ensures consistent tool naming: mcp__ag3ntum__Read, mcp__ag3ntum__Write,
mcp__ag3ntum__Bash, etc.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml
from claude_agent_sdk import create_sdk_mcp_server

from .ag3ntum_read import create_read_tool
from .ag3ntum_read_document import create_read_document_tool
from .ag3ntum_write import create_write_tool
from .ag3ntum_edit import create_edit_tool
from .ag3ntum_multiedit import create_multiedit_tool
from .ag3ntum_glob import create_glob_tool
from .ag3ntum_grep import create_grep_tool
from .ag3ntum_ls import create_ls_tool
from .ag3ntum_webfetch import create_webfetch_tool
from .ag3ntum_bash import (
    create_bash_tool,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_KILL_AFTER_SECONDS,
    DEFAULT_PREVIEW_MODE,
    DEFAULT_PREVIEW_LINES,
    MAX_PREVIEW_LINES,
    OUTPUT_DIR,
)
from .ag3ntum_ask import create_ask_user_question_tool

logger = logging.getLogger(__name__)

# Default config path relative to this file's location
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "security" / "tools-security.yaml"


@dataclass
class BashToolConfig:
    """Configuration for Bash tool loaded from tools-security.yaml."""

    timeout: int = DEFAULT_TIMEOUT_SECONDS  # 300 seconds (5 minutes)
    kill_after: int = DEFAULT_KILL_AFTER_SECONDS  # 10 seconds grace period
    preview_mode: str = DEFAULT_PREVIEW_MODE  # "tail"
    preview_lines: int = DEFAULT_PREVIEW_LINES  # 20
    max_preview_lines: int = MAX_PREVIEW_LINES  # 100
    output_dir: str = OUTPUT_DIR  # ".tmp/cmd"


def load_bash_config(config_path: Path | None = None) -> BashToolConfig:
    """
    Load Bash tool configuration from tools-security.yaml.

    Args:
        config_path: Path to tools-security.yaml. If None, uses default.

    Returns:
        BashToolConfig with values from YAML or defaults.
    """
    path = config_path or DEFAULT_CONFIG_PATH

    if not path.exists():
        logger.warning(f"Bash config file not found: {path}, using defaults")
        return BashToolConfig()

    try:
        with open(path) as f:
            yaml_data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load bash config from {path}: {e}")
        return BashToolConfig()

    # Navigate to tools.bash section
    bash_config = yaml_data.get("tools", {}).get("bash", {})

    if not bash_config:
        logger.info("No bash config in YAML, using defaults")
        return BashToolConfig()

    config = BashToolConfig(
        timeout=bash_config.get("timeout", DEFAULT_TIMEOUT_SECONDS),
        kill_after=bash_config.get("kill_after", DEFAULT_KILL_AFTER_SECONDS),
        preview_mode=bash_config.get("preview_mode", DEFAULT_PREVIEW_MODE),
        preview_lines=bash_config.get("preview_lines", DEFAULT_PREVIEW_LINES),
        max_preview_lines=bash_config.get("max_preview_lines", MAX_PREVIEW_LINES),
        output_dir=bash_config.get("output_dir", OUTPUT_DIR),
    )

    logger.info(
        f"Loaded Bash config from {path}: timeout={config.timeout}s, "
        f"kill_after={config.kill_after}s, preview_mode={config.preview_mode}"
    )
    return config


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
        - mcp__ag3ntum__ReadDocument
        - mcp__ag3ntum__Write
        - mcp__ag3ntum__Edit
        - mcp__ag3ntum__MultiEdit
        - mcp__ag3ntum__Glob
        - mcp__ag3ntum__Grep
        - mcp__ag3ntum__LS
        - mcp__ag3ntum__WebFetch
        - mcp__ag3ntum__AskUserQuestion
    """
    tools = []
    
    # Add Bash tool if requested and workspace_path provided
    if include_bash:
        if workspace_path is None:
            logger.warning("Cannot create Bash tool: workspace_path is required")
        else:
            # Load bash configuration from tools-security.yaml
            bash_config = load_bash_config()
            bash_tool = create_bash_tool(
                workspace_path=workspace_path,
                timeout_seconds=bash_config.timeout,
                kill_after_seconds=bash_config.kill_after,
                default_preview_mode=bash_config.preview_mode,  # type: ignore[arg-type]
                default_preview_lines=bash_config.preview_lines,
                max_preview_lines=bash_config.max_preview_lines,
                output_dir=bash_config.output_dir,
                sandbox_executor=sandbox_executor,
            )
            tools.append(bash_tool)
            logger.debug(
                f"Added Bash tool to unified MCP server (timeout={bash_config.timeout}s, "
                f"kill_after={bash_config.kill_after}s)"
            )
    
    # Add all file tools bound to this session
    # Note: WebFetch doesn't need session_id as it doesn't access filesystem
    tools.extend([
        create_read_tool(session_id=session_id),
        create_read_document_tool(session_id=session_id),
        create_write_tool(session_id=session_id),
        create_edit_tool(session_id=session_id),
        create_multiedit_tool(session_id=session_id),
        create_glob_tool(session_id=session_id),
        create_grep_tool(session_id=session_id),
        create_ls_tool(session_id=session_id),
        create_webfetch_tool(),  # No session_id needed
        create_ask_user_question_tool(session_id=session_id),
    ])

    # Log each tool for debugging
    tool_names = [getattr(t, '__name__', str(t)) for t in tools]
    logger.info(
        f"Created unified Ag3ntum MCP server for session {session_id} "
        f"with {len(tools)} tools (Bash: {include_bash}): {tool_names}"
    )

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=tools,
    )
