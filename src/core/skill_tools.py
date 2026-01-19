"""
Skill tools for Ag3ntum.

Provides @tool decorated wrappers for script-based skills, enabling them
to be exposed as MCP tools to the Claude Agent SDK.

This module handles:
- Loading skills and detecting which have associated scripts
- Creating @tool decorated async handlers for script-based skills
- Generating MCP server configurations for skill tools
- Sandboxed execution of skill scripts with environment variable injection

SECURITY: All script-based skills MUST run inside the Bubblewrap sandbox.
A SandboxExecutor is REQUIRED. Without it, skill execution will fail with
an explicit error - there is no fallback to insecure direct execution.

Environment variables (sandboxed_envs) are injected via the SandboxExecutor's
config (custom_env field), which is set up by the PermissionManager. This
ensures a single source of truth for environment variables.

Usage:
    from skill_tools import SkillToolsManager, create_skills_mcp_server
    from sandbox import SandboxExecutor, SandboxConfig

    # SandboxExecutor must be configured with sandboxed_envs via custom_env
    # (handled by PermissionManager.get_sandbox_config(sandboxed_envs=...))
    manager = SkillToolsManager(
        skills_dir,
        workspace_dir=workspace_dir,
        sandbox_executor=executor,  # REQUIRED for skill execution
    )
    mcp_server = manager.create_mcp_server()

    options = ClaudeAgentOptions(
        mcp_servers={"skills": mcp_server},
        allowed_tools=manager.get_allowed_tool_names()
    )
"""
import asyncio
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .tool_utils import build_script_command

from claude_agent_sdk import create_sdk_mcp_server, tool

from .skills import Skill, SkillManager

if TYPE_CHECKING:
    from .sandbox import SandboxExecutor

logger = logging.getLogger(__name__)


@dataclass
class SkillToolDefinition:
    """
    Definition of a skill tool.

    Represents a script-based skill that can be invoked as an MCP tool.
    """
    name: str
    description: str
    skill: Skill
    script_path: Path
    mcp_tool_name: str = ""

    def __post_init__(self) -> None:
        """Generate MCP tool name if not provided."""
        if not self.mcp_tool_name:
            # Normalize skill name for MCP tool naming
            safe_name = self.name.replace("-", "_").replace(".", "_").lower()
            self.mcp_tool_name = f"skill_{safe_name}"


@dataclass
class SkillExecutionResult:
    """
    Result of executing a skill script.
    """
    success: bool
    output: str
    error: Optional[str] = None
    exit_code: int = 0
    duration_ms: int = 0


class SkillToolsManager:
    """
    Manages skill tools for MCP integration.

    Discovers script-based skills and creates @tool decorated handlers
    for them that can be registered with the Claude Agent SDK.

    SECURITY: All script-based skills run inside the Bubblewrap sandbox.
    A SandboxExecutor is REQUIRED for skill execution. If not provided,
    skill execution will fail with an explicit error.
    """

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        workspace_dir: Optional[Path] = None,
        timeout_seconds: int = 300,
        sandbox_executor: Optional["SandboxExecutor"] = None,
    ) -> None:
        """
        Initialize the skill tools manager.

        Args:
            skills_dir: Directory containing skills.
            workspace_dir: Working directory for skill execution.
            timeout_seconds: Timeout for skill script execution.
            sandbox_executor: SandboxExecutor for secure execution.
                REQUIRED for skill script execution. Environment variables
                from sandboxed_envs are injected via the sandbox config.

        Note:
            sandboxed_envs are NOT passed directly here. They are already
            injected into the SandboxExecutor's config via custom_env field.
            This ensures a single source of truth for environment variables.
        """
        self._skill_manager = SkillManager(skills_dir)
        self._workspace_dir = workspace_dir
        self._timeout = timeout_seconds
        self._sandbox_executor = sandbox_executor
        self._tool_definitions: dict[str, SkillToolDefinition] = {}
        self._mcp_tools: list[Any] = []
        self._initialized = False

    def discover_script_skills(self) -> list[SkillToolDefinition]:
        """
        Discover all skills that have associated scripts.

        Returns:
            List of SkillToolDefinition for script-based skills.
        """
        definitions: list[SkillToolDefinition] = []

        for skill_name in self._skill_manager.list_skills():
            try:
                skill = self._skill_manager.load_skill(skill_name)

                if skill.script_file and skill.script_file.exists():
                    definition = SkillToolDefinition(
                        name=skill.name,
                        description=skill.description or f"Execute {skill.name} skill",
                        skill=skill,
                        script_path=skill.script_file,
                    )
                    definitions.append(definition)
                    self._tool_definitions[definition.mcp_tool_name] = definition
                    logger.info(
                        f"Discovered script skill: {skill.name} -> "
                        f"{definition.mcp_tool_name}"
                    )
            except Exception as e:
                logger.warning(f"Failed to load skill {skill_name}: {e}")

        return definitions

    def _create_tool_handler(
        self,
        definition: SkillToolDefinition
    ):
        """
        Create an async tool handler for a skill.

        SECURITY: Skills MUST run inside the Bubblewrap sandbox.
        If sandbox_executor is not available, execution fails with an error.

        Args:
            definition: The skill tool definition.

        Returns:
            Decorated async tool handler function.
        """
        # Capture values by binding to local variables in closure scope
        # to avoid closure issues when creating multiple handlers in a loop
        skill_name = definition.skill.name
        script_path = definition.script_path
        timeout = self._timeout
        sandbox_executor = self._sandbox_executor

        # Determine execution command
        if script_path.suffix == ".py":
            base_cmd = [sys.executable, str(script_path)]
        elif script_path.suffix in [".sh", ".bash"]:
            base_cmd = ["bash", str(script_path)]
        else:
            base_cmd = [str(script_path)]

        # Use default arguments to capture values at definition time
        # This ensures each handler has its own copy of these values
        @tool(
            definition.mcp_tool_name,
            definition.description,
            {
                "args": list,
                "input_data": str,
            }
        )
        async def skill_handler(
            args: dict[str, Any],
            _skill_name: str = skill_name,
            _base_cmd: list = base_cmd,
            _timeout: int = timeout,
            _script_path: Path = script_path,
            _sandbox_executor: Optional["SandboxExecutor"] = sandbox_executor,
        ) -> dict[str, Any]:
            """Execute the skill script inside the Bubblewrap sandbox."""
            # SECURITY: Require sandbox for skill execution
            if _sandbox_executor is None:
                error_msg = (
                    f"SECURITY ERROR: Cannot execute skill '{_skill_name}' - "
                    f"SandboxExecutor is not configured. "
                    f"Script-based skills MUST run inside the Bubblewrap sandbox."
                )
                logger.error(error_msg)
                return {
                    "content": [{
                        "type": "text",
                        "text": error_msg
                    }],
                    "is_error": True
                }

            cmd_args = args.get("args", [])
            input_data = args.get("input_data", "")

            cmd = _base_cmd + ([str(a) for a in cmd_args] if cmd_args else [])

            try:
                # Build Bubblewrap command with sandboxed env vars
                # Environment variables are in sandbox config via custom_env
                bwrap_cmd = _sandbox_executor.build_bwrap_command(
                    cmd,
                    allow_network=True,  # Skills may need network access
                )
                logger.info(
                    f"Executing skill {_skill_name} in sandbox: "
                    f"{' '.join(bwrap_cmd[:8])}..."
                )

                process = await asyncio.create_subprocess_exec(
                    *bwrap_cmd,
                    stdin=asyncio.subprocess.PIPE if input_data else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                stdin_bytes = input_data.encode() if input_data else None
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=stdin_bytes),
                    timeout=_timeout
                )

                exit_code = process.returncode or 0
                output = stdout.decode("utf-8", errors="replace")
                error_output = stderr.decode("utf-8", errors="replace")

                if exit_code != 0:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Skill {_skill_name} failed (exit {exit_code}):\n"
                                    f"stdout: {output}\nstderr: {error_output}"
                        }],
                        "is_error": True
                    }

                return {
                    "content": [{
                        "type": "text",
                        "text": output if output else f"Skill {_skill_name} completed."
                    }]
                }

            except asyncio.TimeoutError:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Skill {_skill_name} timed out after {_timeout}s"
                    }],
                    "is_error": True
                }

            except Exception as e:
                logger.error(f"Skill {_skill_name} execution error: {e}")
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Skill {_skill_name} error: {str(e)}"
                    }],
                    "is_error": True
                }

        return skill_handler

    def initialize(self) -> None:
        """
        Initialize the manager by discovering skills and creating tools.
        """
        if self._initialized:
            return

        definitions = self.discover_script_skills()

        for definition in definitions:
            handler = self._create_tool_handler(definition)
            self._mcp_tools.append(handler)

        self._initialized = True
        logger.info(f"Initialized {len(self._mcp_tools)} skill tools")

    def get_tool_definitions(self) -> list[SkillToolDefinition]:
        """Get all discovered skill tool definitions."""
        if not self._initialized:
            self.initialize()
        return list(self._tool_definitions.values())

    def get_allowed_tool_names(self) -> list[str]:
        """
        Get list of tool names for allowed_tools config.

        Returns:
            List of MCP tool names in "mcp__server__tool" format.
        """
        if not self._initialized:
            self.initialize()

        return [
            f"mcp__skills__{defn.mcp_tool_name}"
            for defn in self._tool_definitions.values()
        ]

    def create_mcp_server(self, name: str = "skills", version: str = "1.0.0"):
        """
        Create an MCP server configuration for skill tools.

        Args:
            name: Server name for MCP registration.
            version: Server version string.

        Returns:
            McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
        """
        if not self._initialized:
            self.initialize()

        if not self._mcp_tools:
            logger.warning("No script-based skills found, MCP server will be empty")

        return create_sdk_mcp_server(
            name=name,
            version=version,
            tools=self._mcp_tools
        )


def create_skills_mcp_server(
    skills_dir: Optional[Path] = None,
    workspace_dir: Optional[Path] = None,
    sandbox_executor: Optional["SandboxExecutor"] = None,
) -> tuple[Any, list[str]]:
    """
    Convenience function to create an MCP server for skills.

    SECURITY: sandbox_executor is REQUIRED for skill execution.
    If not provided, skill execution will fail with an explicit error.

    Args:
        skills_dir: Directory containing skills.
        workspace_dir: Working directory for skill execution.
        sandbox_executor: SandboxExecutor for secure execution.
            Environment variables are injected via sandbox config's custom_env.

    Returns:
        Tuple of (McpSdkServerConfig, list of allowed tool names).
    """
    manager = SkillToolsManager(
        skills_dir,
        workspace_dir,
        sandbox_executor=sandbox_executor,
    )
    manager.initialize()

    mcp_server = manager.create_mcp_server()
    tool_names = manager.get_allowed_tool_names()

    return mcp_server, tool_names


def execute_skill_sync(
    skill: Skill,
    args: Optional[list[str]] = None,
    input_data: Optional[str] = None,
    timeout: int = 300,
    sandbox_executor: Optional["SandboxExecutor"] = None,
) -> SkillExecutionResult:
    """
    Execute a skill script synchronously inside the Bubblewrap sandbox.

    SECURITY: sandbox_executor is REQUIRED. If not provided, execution fails.

    Args:
        skill: The skill to execute.
        args: Command-line arguments for the script.
        input_data: Optional stdin data.
        timeout: Timeout in seconds.
        sandbox_executor: SandboxExecutor for secure execution.
            Environment variables are injected via sandbox config's custom_env.

    Returns:
        SkillExecutionResult with output and status.
    """
    # SECURITY: Require sandbox for skill execution
    if sandbox_executor is None:
        error_msg = (
            f"SECURITY ERROR: Cannot execute skill '{skill.name}' - "
            f"SandboxExecutor is not configured. "
            f"Script-based skills MUST run inside the Bubblewrap sandbox."
        )
        logger.error(error_msg)
        return SkillExecutionResult(
            success=False,
            output="",
            error=error_msg,
            exit_code=1,
        )

    if not skill.script_file or not skill.script_file.exists():
        return SkillExecutionResult(
            success=False,
            output="",
            error=f"Skill {skill.name} has no script file",
            exit_code=1,
        )

    script_path = skill.script_file
    cmd = build_script_command(script_path, args)

    start = time.time()

    try:
        # Build Bubblewrap command with sandboxed env vars
        bwrap_cmd = sandbox_executor.build_bwrap_command(
            cmd,
            allow_network=True,
        )
        logger.info(f"Executing skill {skill.name} in sandbox (sync)")

        result = subprocess.run(
            bwrap_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
        )

        duration_ms = int((time.time() - start) * 1000)

        return SkillExecutionResult(
            success=result.returncode == 0,
            output=result.stdout,
            error=result.stderr if result.returncode != 0 else None,
            exit_code=result.returncode,
            duration_ms=duration_ms,
        )

    except subprocess.TimeoutExpired:
        return SkillExecutionResult(
            success=False,
            output="",
            error=f"Script timed out after {timeout}s",
            exit_code=124,
        )

    except Exception as e:
        logger.error(f"Skill {skill.name} sync execution error: {e}")
        return SkillExecutionResult(
            success=False,
            output="",
            error=str(e),
            exit_code=1,
        )
