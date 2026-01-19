"""
Ag3ntumBash tool implementation.

Executes bash commands and automatically captures output to ./.tmp/cmd/
directory. Returns metadata (exit code, filesize, line count) and
configurable preview lines (head or tail) for efficient context management.

Security Layers:
1. CommandSecurityFilter - Pre-execution regex filtering of dangerous commands
2. Bubblewrap (bwrap) - OS-level filesystem and process isolation

NOTE: Ag3ntumBash does NOT use Ag3ntumPathValidator because:
1. Bwrap handles filesystem isolation at the OS level
2. Commands run in a sandboxed subprocess, not the main Python process
3. The subprocess can only see /workspace due to mount namespace
"""
import asyncio
import hashlib
import logging
import os
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from claude_agent_sdk import create_sdk_mcp_server, tool

from src.core.command_security import (
    CommandSecurityFilter,
    SecurityCheckResult,
    get_command_security_filter,
)

if TYPE_CHECKING:
    from src.core.sandbox import SandboxExecutor

logger = logging.getLogger(__name__)

# =============================================================================
# Constants - Configurable defaults for Ag3ntumBash tool
# =============================================================================

# System tool name - bypasses permissions
AG3NTUM_BASH_TOOL: str = "mcp__ag3ntum__Bash"

# Command execution timeout in seconds (5 minutes)
DEFAULT_TIMEOUT_SECONDS: int = 300

# Grace period before SIGKILL after SIGTERM (seconds)
DEFAULT_KILL_AFTER_SECONDS: int = 10

# Preview configuration
DEFAULT_PREVIEW_MODE: Literal["head", "tail"] = "tail"
DEFAULT_PREVIEW_LINES: int = 20
MAX_PREVIEW_LINES: int = 100

# Output directory (relative to workspace)
OUTPUT_DIR: str = ".tmp/cmd"


def create_bash_tool(
    workspace_path: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    kill_after_seconds: int = DEFAULT_KILL_AFTER_SECONDS,
    default_preview_mode: Literal["head", "tail"] = DEFAULT_PREVIEW_MODE,
    default_preview_lines: int = DEFAULT_PREVIEW_LINES,
    max_preview_lines: int = MAX_PREVIEW_LINES,
    output_dir: str = OUTPUT_DIR,
    sandbox_executor: Optional["SandboxExecutor"] = None,
    security_filter: Optional[CommandSecurityFilter] = None,
):
    """
    Create the Ag3ntumBash tool function with workspace binding.

    Args:
        workspace_path: Absolute path to the workspace directory.
        timeout_seconds: Maximum execution time for commands (enforced via
                        Linux `timeout` command which sends SIGTERM).
        kill_after_seconds: Grace period after SIGTERM before sending SIGKILL
                           to forcibly terminate stuck processes.
        default_preview_mode: Default preview mode ("head" or "tail").
        default_preview_lines: Default number of preview lines.
        max_preview_lines: Maximum allowed preview lines.
        output_dir: Output directory relative to workspace.
        sandbox_executor: Optional SandboxExecutor for bubblewrap isolation.
                         When provided, all commands are wrapped in bwrap
                         for filesystem and process isolation.
        security_filter: Optional CommandSecurityFilter for pre-execution
                        command validation. If not provided, uses the
                        default global filter.

    Returns:
        Tool function decorated with @tool.
    """
    bound_workspace = workspace_path
    bound_timeout = timeout_seconds
    bound_kill_after = kill_after_seconds
    bound_default_preview_mode = default_preview_mode
    bound_default_preview_lines = default_preview_lines
    bound_max_preview_lines = max_preview_lines
    bound_output_dir = output_dir
    bound_sandbox_executor = sandbox_executor
    bound_security_filter = security_filter or get_command_security_filter()

    @tool(
        "Bash",
        f"""Execute a bash command and capture output to a file.

Output is automatically saved to ./{output_dir}/<id>.txt with metadata.
Returns a preview (head or tail lines) plus total size and line count.

Use this tool instead of raw bash to prevent large outputs from bloating context.

Args:
    command: The bash command to execute.
    preview_mode: "head" for first N lines, "tail" for last N lines (default: "{default_preview_mode}").
    preview_lines: Number of lines to return in preview (default: {default_preview_lines}, max: {max_preview_lines}).
    
Returns:
    output_file: Path to the full output file.
    exit_code: Command exit code (0 = success).
    filesize_bytes: Total size of output in bytes.
    total_lines: Total number of lines in output.
    preview_mode: Which preview mode was used.
    preview: The requested preview lines.
    
Example:
    Bash(command="find . -name '*.py'", preview_mode="head", preview_lines=10)
""",
        {
            "command": str,
            "preview_mode": str,  # "head" or "tail"
            "preview_lines": int,
        }
    )
    async def bash(args: dict[str, Any]) -> dict[str, Any]:
        """
        Execute bash command and return captured output with metadata.
        """
        command: str = args.get("command", "").strip()
        preview_mode: Literal["head", "tail"] = args.get(
            "preview_mode", bound_default_preview_mode
        )
        preview_lines: int = min(
            args.get("preview_lines", bound_default_preview_lines),
            bound_max_preview_lines
        )

        if not command:
            return _error_response("Command cannot be empty")

        # SECURITY: Pre-execution command filtering
        # This is Layer 1 - regex-based blocking of dangerous commands
        security_result: SecurityCheckResult = bound_security_filter.check_command(command)
        if security_result.should_block:
            logger.warning(
                f"Ag3ntumBash: SECURITY BLOCKED - {security_result.message} "
                f"command={command[:100]}..."
            )
            return _error_response(
                f"Command blocked by security policy: {security_result.message}"
            )

        if preview_mode not in ("head", "tail"):
            preview_mode = bound_default_preview_mode

        # Ensure output directory exists
        output_path = bound_workspace / bound_output_dir
        try:
            output_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return _error_response(f"Failed to create output directory: {e}")

        # Generate unique output file: YYYYMMDD-HHMMSS-<hash12>.txt
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d-%H%M%S")
        # Create 12-char hash from high-precision time + process ID for uniqueness
        unique_seed = f"{time.time_ns()}-{os.getpid()}-{id(args)}"
        hash_suffix = hashlib.sha256(unique_seed.encode()).hexdigest()[:12]
        filename = f"{timestamp}-{hash_suffix}.txt"
        output_file = output_path / filename

        logger.info(f"Ag3ntumBash: Executing command, output={output_file}")

        try:
            # Build execution command - wrap in sandbox if executor is available
            exec_command: str | list[str]
            use_shell: bool
            exec_cwd: str | None
            exec_env: dict[str, str] | None

            if bound_sandbox_executor is not None and bound_sandbox_executor.config.enabled:
                # SECURITY: Wrap command in bubblewrap for filesystem isolation
                # This is the PRIMARY security layer for Ag3ntumBash
                try:
                    allow_network = bool(
                        getattr(bound_sandbox_executor.config, "network", None)
                        and bound_sandbox_executor.config.network.enabled
                    )
                    # Build bwrap command list (not shell string)
                    bwrap_cmd = bound_sandbox_executor.build_bwrap_command(
                        ["bash", "-c", command],
                        allow_network=allow_network,
                    )
                    # TIMEOUT: Wrap bwrap command with Linux timeout for forcible termination
                    # timeout --kill-after=KILL sends SIGTERM first, then SIGKILL after grace period
                    exec_command = [
                        "timeout",
                        f"--kill-after={bound_kill_after}",
                        str(bound_timeout),
                    ] + bwrap_cmd
                    use_shell = False
                    exec_cwd = None  # bwrap sets --chdir internally
                    exec_env = {"TERM": "dumb"}  # Minimal env, bwrap clears the rest
                    logger.info(
                        f"Ag3ntumBash: SANDBOX ENABLED - wrapping in timeout({bound_timeout}s, "
                        f"kill-after={bound_kill_after}s) + bwrap: {command[:50]}..."
                    )
                except Exception as e:
                    # SECURITY: FAIL-CLOSED - if sandbox fails, DENY the command
                    logger.error(f"Ag3ntumBash: SANDBOX FAIL-CLOSED - bwrap error: {e}")
                    return _error_response(
                        f"Sandbox unavailable (bwrap error: {e}). "
                        "Commands are blocked for security."
                    )
            else:
                # No sandbox - execute directly (SHOULD NOT happen in production)
                # Log warning for visibility
                logger.warning(
                    "Ag3ntumBash: SANDBOX DISABLED - executing without isolation! "
                    "This is a security risk."
                )
                # TIMEOUT: Wrap command with Linux timeout for forcible termination
                # timeout --kill-after=KILL sends SIGTERM first, then SIGKILL after grace period
                exec_command = f"timeout --kill-after={bound_kill_after} {bound_timeout} bash -c {shlex.quote(command)}"
                use_shell = True
                exec_cwd = str(bound_workspace)
                exec_env = {**os.environ, "TERM": "dumb"}

            # Execute command with timeout
            # Note: Linux `timeout` handles the primary timeout via SIGTERM/SIGKILL
            # asyncio timeout is a fallback safety net (timeout + kill_after + 30s buffer)
            asyncio_timeout = bound_timeout + bound_kill_after + 30

            if use_shell:
                process = await asyncio.create_subprocess_shell(
                    exec_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=exec_cwd,
                    env=exec_env,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *exec_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=exec_cwd,
                    env=exec_env,
                )

            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=asyncio_timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error(
                    f"Ag3ntumBash: ASYNCIO TIMEOUT (fallback) after {asyncio_timeout}s - "
                    f"Linux timeout failed to terminate the process"
                )
                return _error_response(
                    f"Command timed out after {bound_timeout} seconds "
                    f"(forcibly killed after {bound_kill_after}s grace period)"
                )

            exit_code = process.returncode or 0

            # Check for Linux timeout exit codes
            # 124 = SIGTERM sent (command timed out)
            # 137 = SIGKILL sent (128 + 9, process killed after grace period)
            if exit_code == 124:
                logger.warning(
                    f"Ag3ntumBash: Command terminated by timeout after {bound_timeout}s"
                )
                return _error_response(
                    f"Command timed out after {bound_timeout} seconds (SIGTERM sent)"
                )
            elif exit_code == 137:
                logger.warning(
                    f"Ag3ntumBash: Command force-killed after {bound_timeout}+{bound_kill_after}s"
                )
                return _error_response(
                    f"Command timed out and was force-killed after {bound_timeout}s + "
                    f"{bound_kill_after}s grace period (SIGKILL sent)"
                )

            # Write command output to file
            output_file.write_bytes(stdout)

            # Calculate content metadata (before appending metadata lines)
            content_size = output_file.stat().st_size
            output_text = stdout.decode("utf-8", errors="replace")
            lines = output_text.splitlines()
            total_lines = len(lines)

            # Append EXIT_CODE and FILESIZE metadata to file
            # This allows `tail` to reveal metadata without reading full output
            metadata_lines = f"\nEXIT_CODE:{exit_code}\nFILESIZE:{content_size}\n"
            with output_file.open("a") as f:
                f.write(metadata_lines)

            # Final filesize includes metadata
            filesize_bytes = output_file.stat().st_size

            # Get preview lines
            if preview_mode == "head":
                preview_content = "\n".join(lines[:preview_lines])
                truncated = total_lines > preview_lines
            else:  # tail
                preview_content = "\n".join(lines[-preview_lines:])
                truncated = total_lines > preview_lines

            # Build result
            relative_path = f"{bound_output_dir}/{filename}"

            result_text = f"""**Command executed successfully**

**Output file:** `{relative_path}`
**Exit code:** {exit_code}
**Content size:** {content_size:,} bytes
**Total lines:** {total_lines:,}

**Preview ({preview_mode} {min(preview_lines, total_lines)} lines):**
```
{preview_content}
```
{"[... output truncated ...]" if truncated else ""}

**To read more:**
- Full file: Use Read tool on `{relative_path}`
- Metadata only: `tail -n 3 {relative_path}` → shows EXIT_CODE and FILESIZE
"""

            logger.info(
                f"Ag3ntumBash: Complete - exit={exit_code}, "
                f"size={filesize_bytes}, lines={total_lines}"
            )

            return {
                "content": [{
                    "type": "text",
                    "text": result_text
                }]
            }

        except Exception as e:
            logger.exception(f"Ag3ntumBash: Execution failed - {e}")
            return _error_response(f"Command execution failed: {e}")

    return bash


def _error_response(message: str) -> dict[str, Any]:
    """Create a standardized error response."""
    return {
        "content": [{
            "type": "text",
            "text": f"**Error:** {message}"
        }],
        "isError": True
    }


def create_ag3ntum_bash_mcp_server(
    workspace_path: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    kill_after_seconds: int = DEFAULT_KILL_AFTER_SECONDS,
    default_preview_mode: Literal["head", "tail"] = DEFAULT_PREVIEW_MODE,
    default_preview_lines: int = DEFAULT_PREVIEW_LINES,
    max_preview_lines: int = MAX_PREVIEW_LINES,
    output_dir: str = OUTPUT_DIR,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
    sandbox_executor: Optional["SandboxExecutor"] = None,
    security_filter: Optional[CommandSecurityFilter] = None,
):
    """
    Create an in-process MCP server for the Ag3ntumBash tool.

    Args:
        workspace_path: Absolute path to workspace directory.
        timeout_seconds: Maximum command execution time (enforced via Linux
                        `timeout` command which sends SIGTERM).
        kill_after_seconds: Grace period after SIGTERM before sending SIGKILL
                           to forcibly terminate stuck processes.
        default_preview_mode: Default preview mode ("head" or "tail").
        default_preview_lines: Default number of preview lines.
        max_preview_lines: Maximum allowed preview lines.
        output_dir: Output directory relative to workspace.
        server_name: MCP server name.
        version: Server version.
        sandbox_executor: Optional SandboxExecutor for bubblewrap isolation.
                         STRONGLY RECOMMENDED for production use.
        security_filter: Optional CommandSecurityFilter for command validation.
                        If not provided, uses default global filter.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    bash_tool = create_bash_tool(
        workspace_path=workspace_path,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        default_preview_mode=default_preview_mode,
        default_preview_lines=default_preview_lines,
        max_preview_lines=max_preview_lines,
        output_dir=output_dir,
        sandbox_executor=sandbox_executor,
        security_filter=security_filter,
    )

    sandbox_status = "ENABLED" if sandbox_executor and sandbox_executor.config.enabled else "DISABLED"
    logger.info(
        f"Created Ag3ntumBash MCP server: "
        f"workspace={workspace_path}, timeout={timeout_seconds}s, "
        f"preview={default_preview_mode}/{default_preview_lines}, output_dir={output_dir}, "
        f"sandbox={sandbox_status}"
    )

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[bash_tool]
    )


def is_ag3ntum_bash_tool(tool_name: str) -> bool:
    """
    Check if a tool name is the Ag3ntumBash tool.

    Args:
        tool_name: Full tool name (may include mcp__ prefix).

    Returns:
        True if the tool is Ag3ntumBash.
    """
    return tool_name == AG3NTUM_BASH_TOOL
