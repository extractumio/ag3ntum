"""
Unified Task Runner for Ag3ntum.

Provides a single entry point for agent task execution that is used by both
CLI (src/core/agent.py) and HTTP API (src/services/agent_runner.py).

This module extracts the common execution logic to ensure consistent behavior
across all entry points and reduces code duplication.

Usage:
    from src.core.task_runner import execute_agent_task
    from src.core.schemas import TaskExecutionParams

    params = TaskExecutionParams(
        task="Your task description",
        working_dir=Path("/path/to/dir"),
        tracer=ExecutionTracer(),  # or BackendConsoleTracer()
    )
    result = await execute_agent_task(params)
"""
import logging
from pathlib import Path
from typing import Optional

from ..config import (
    AGENT_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
    AgentConfigLoader,
)
from .agent_core import ClaudeAgent
from .permission_profiles import PermissionManager
from .schemas import AgentConfig, AgentResult, TaskExecutionParams
from .tracer import NullTracer, TracerBase

logger = logging.getLogger(__name__)


async def execute_agent_task(
    params: TaskExecutionParams,
    config_loader: Optional[AgentConfigLoader] = None,
) -> AgentResult:
    """
    Execute an agent task with unified logic for CLI and HTTP.

    This is the single entry point for running agent tasks. Both CLI and HTTP
    entry points should call this function with their respective parameters.

    Args:
        params: TaskExecutionParams with all execution parameters.
        config_loader: Optional pre-loaded AgentConfigLoader. If not provided,
                       a new one will be created.

    Returns:
        AgentResult with status, output, metrics, and session info.

    Raises:
        AgentError: If the agent encounters an error during execution.
        Exception: For unexpected errors.
    """
    # 1. Resolve working directory
    if params.working_dir:
        working_dir = params.working_dir.resolve()
    else:
        # Default: AGENT_DIR for API context, cwd for CLI
        # The caller should set working_dir explicitly for their context
        working_dir = AGENT_DIR

    logger.info(f"Working directory: {working_dir}")

    # 2. Load configuration from agent.yaml
    if config_loader is None:
        config_loader = AgentConfigLoader()
    config_data = config_loader.get_config()

    # 3. Apply parameter overrides (params take precedence over config)
    model = params.model or config_data["model"]
    max_turns = (
        params.max_turns if params.max_turns is not None
        else config_data["max_turns"]
    )
    timeout_seconds = (
        params.timeout_seconds if params.timeout_seconds is not None
        else config_data["timeout_seconds"]
    )
    # permission_mode is optional and managed via permissions.yaml
    permission_mode = params.permission_mode or config_data.get("permission_mode")
    role = params.role or config_data["role"]

    # Skills and checkpointing (with override support)
    enable_skills = (
        params.enable_skills if params.enable_skills is not None
        else config_data["enable_skills"]
    )
    enable_file_checkpointing = (
        params.enable_file_checkpointing if params.enable_file_checkpointing is not None
        else config_data["enable_file_checkpointing"]
    )
    max_buffer_size = (
        params.max_buffer_size if params.max_buffer_size is not None
        else config_data.get("max_buffer_size")
    )
    output_format = params.output_format or config_data.get("output_format")
    include_partial_messages = (
        params.include_partial_messages if params.include_partial_messages is not None
        else config_data.get("include_partial_messages", False)
    )

    # Extended thinking configuration
    thinking_tokens = (
        params.thinking_tokens if params.thinking_tokens is not None
        else config_data.get("thinking_tokens")
    )

    logger.info(f"Config: model={model}, max_turns={max_turns}, timeout={timeout_seconds}s")

    # 4. Load permission manager from profile
    permission_manager = PermissionManager(profile_path=params.profile_path)

    if params.profile_path:
        logger.info(f"Using profile: {params.profile_path}")

    # 5. Get allowed tools from permission profile
    profile = permission_manager.profile
    profile_tools = profile.tools
    allowed_tools = list(set(profile_tools.enabled) - set(profile_tools.disabled))

    # Get auto_checkpoint_tools from profile (with fallback)
    if profile.checkpointing:
        auto_checkpoint_tools = profile.checkpointing.auto_checkpoint_tools
    else:
        auto_checkpoint_tools = ["Write", "Edit"]  # Reasonable default

    logger.info(f"Allowed tools: {', '.join(allowed_tools)}")
    logger.info(f"Auto checkpoint tools: {', '.join(auto_checkpoint_tools)}")

    # 6. Build AgentConfig
    agent_config = AgentConfig(
        model=model,
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        enable_skills=enable_skills,
        enable_file_checkpointing=enable_file_checkpointing,
        permission_mode=permission_mode,
        role=role,
        auto_checkpoint_tools=auto_checkpoint_tools,
        working_dir=str(working_dir),
        additional_dirs=params.additional_dirs,
        allowed_tools=allowed_tools,
        permissions_config=None,  # Not used with permission profiles
        max_buffer_size=max_buffer_size,
        output_format=output_format,
        include_partial_messages=include_partial_messages,
        thinking_tokens=thinking_tokens,
    )

    # 7. Determine tracer
    tracer: TracerBase = params.tracer if params.tracer else NullTracer()

    logger.info(
        f"Starting agent: model={agent_config.model}, max_turns={agent_config.max_turns}"
    )
    if params.resume_session_id:
        logger.info(
            f"Resuming from session: {params.resume_session_id} "
            f"(fork={params.fork_session})"
        )

    # 8. Create and run agent
    # Use sessions_dir from params (determined once at entry point)
    # For HTTP: user-specific /users/{username}/sessions
    # For CLI: global SESSIONS_DIR (fallback for CLI use)
    sessions_dir = params.sessions_dir if params.sessions_dir else SESSIONS_DIR
    
    agent = ClaudeAgent(
        config=agent_config,
        sessions_dir=sessions_dir,
        logs_dir=LOGS_DIR,
        tracer=tracer,
        permission_manager=permission_manager,
        linux_uid=params.linux_uid,
        linux_gid=params.linux_gid,
    )

    if params.linux_uid is not None:
        logger.info(f"User isolation: UID={params.linux_uid}, GID={params.linux_gid}")

    # Execute the agent
    result = await agent.run(
        task=params.task,
        resume_session_id=params.resume_session_id,
        fork_session=params.fork_session,
        session_id=params.session_id,  # Use pre-generated session ID if provided
        username=params.username,  # For sandbox mount path substitution
    )

    return result
