"""
Skill tools for Ag3ntum.

Provides @tool decorated wrappers for script-based skills, enabling them
to be exposed as MCP tools to the Claude Agent SDK.

This module handles:
- Loading skills and detecting which have associated scripts
- Creating @tool decorated async handlers for script-based skills
- Generating MCP server configurations for skill tools

Usage:
    from skill_tools import SkillToolsManager, create_skills_mcp_server

    manager = SkillToolsManager(skills_dir)
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
from typing import Any, Optional

from .tool_utils import build_script_command

from claude_agent_sdk import create_sdk_mcp_server, tool

from .skills import Skill, SkillManager

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
    """

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        workspace_dir: Optional[Path] = None,
        timeout_seconds: int = 300,
    ) -> None:
        """
        Initialize the skill tools manager.

        Args:
            skills_dir: Directory containing skills.
            workspace_dir: Working directory for skill execution.
            timeout_seconds: Timeout for skill script execution.
        """
        self._skill_manager = SkillManager(skills_dir)
        self._workspace_dir = workspace_dir
        self._timeout = timeout_seconds
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
        workspace_dir = self._workspace_dir

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
            _workspace_dir: Optional[Path] = workspace_dir,
            _script_path: Path = script_path,
        ) -> dict[str, Any]:
            """Execute the skill script with provided arguments."""
            cmd_args = args.get("args", [])
            input_data = args.get("input_data", "")

            cmd = _base_cmd + ([str(a) for a in cmd_args] if cmd_args else [])

            logger.info(f"Executing skill {_skill_name}: {' '.join(cmd)}")

            try:
                # Run script asynchronously
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=_workspace_dir or _script_path.parent,
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
) -> tuple[Any, list[str]]:
    """
    Convenience function to create an MCP server for skills.

    Args:
        skills_dir: Directory containing skills.
        workspace_dir: Working directory for skill execution.

    Returns:
        Tuple of (McpSdkServerConfig, list of allowed tool names).
    """
    manager = SkillToolsManager(skills_dir, workspace_dir)
    manager.initialize()

    mcp_server = manager.create_mcp_server()
    tool_names = manager.get_allowed_tool_names()

    return mcp_server, tool_names


def execute_skill_sync(
    skill: Skill,
    args: Optional[list[str]] = None,
    input_data: Optional[str] = None,
    cwd: Optional[Path] = None,
    timeout: int = 300,
) -> SkillExecutionResult:
    """
    Execute a skill script synchronously.

    Args:
        skill: The skill to execute.
        args: Command-line arguments for the script.
        input_data: Optional stdin data.
        cwd: Working directory.
        timeout: Timeout in seconds.

    Returns:
        SkillExecutionResult with output and status.
    """
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
        result = subprocess.run(
            cmd,
            cwd=cwd or script_path.parent,
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
        return SkillExecutionResult(
            success=False,
            output="",
            error=str(e),
            exit_code=1,
        )
