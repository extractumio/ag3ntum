"""
Core agent implementation for Ag3ntum.

This module contains the main agent execution logic using the Claude Agent SDK.
"""
import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

import uuid as uuid_mod

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
)
from .prompt_manager import get_prompt_manager
from .prompt_engine import PromptTemplateEngine, PromptContext

# Import paths from central config
from ..config import (
    AGENT_DIR,
    PROMPTS_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
    SKILLS_DIR,
    USERS_DIR,
    load_sandboxed_envs,
)
import shutil
from .exceptions import (
    AgentError,
    MaxTurnsExceededError,
    ServerError,
    SessionIncompleteError,
)
from .schemas import (
    AgentConfig,
    AgentResult,
    Checkpoint,
    CheckpointType,
    LLMMetrics,
    SessionContext,
    TaskStatus,
    TokenUsage,
)
from .sessions import SessionManager
from .skills import SkillManager, discover_merged_skills
from .skill_tools import SkillToolsManager
from .tracer import ExecutionTracer, TracerBase, NullTracer
from .trace_processor import TraceProcessor
from .permissions import (
    create_permission_callback,
    PermissionDenialTracker,
)
from .permission_profiles import PermissionManager
from .sandbox import SandboxConfig, SandboxExecutor, SandboxMount
from .subagent_manager import get_subagent_manager
from .checkpoint_tracker import CheckpointTracker
from .hooks import create_pre_compact_hook
from .structured_output import parse_structured_output

# Ensure tools directory is in sys.path for ag3ntum imports
import sys
_tools_dir = str(AGENT_DIR / "tools")
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

# Import Ag3ntum MCP tools - these are REQUIRED for Ag3ntum to function
# If these imports fail, the application should fail fast with a clear error
from tools.ag3ntum import (
    create_ag3ntum_tools_mcp_server,
    AG3NTUM_BASH_TOOL,
)

# Import PathValidator configuration functions
from .path_validator import (
    configure_path_validator,
    cleanup_path_validator,
    set_session_linux_uid,
)

# Import LLM proxy config for non-Anthropic model routing
from ..api.llm_proxy.config import load_llm_proxy_config, ProxyConfigError

logger = logging.getLogger(__name__)


def determine_session_status(
    result_text: Optional[str],
    had_tool_errors: bool,
    tool_error_count: int = 0,
) -> str:
    """
    Determine the final session status using a priority chain.

    Priority:
    1. Agent's self-assessment from structured header (request_status) — primary
    2. Fallback: default to COMPLETE (agent ran to completion without crash)

    Args:
        result_text: The agent's final message text (may contain structured header).
        had_tool_errors: Whether any tool errors occurred during execution.
        tool_error_count: Number of tool errors (for logging).

    Returns:
        Status string: "COMPLETE", "PARTIAL", or "FAILED".
    """
    if result_text:
        header_fields, _ = parse_structured_output(result_text)
        agent_status = header_fields.get("request_status", "").upper()
        if agent_status in ("COMPLETE", "PARTIAL", "FAILED"):
            if had_tool_errors:
                logger.info(
                    f"Agent self-assessed as {agent_status} despite "
                    f"{tool_error_count} tool error(s)"
                )
            return agent_status

    # No structured header status — fall back to heuristic.
    # An agent that ran to completion without crash/circuit-breaker/permission-denial
    # likely completed its task. Default to COMPLETE.
    if had_tool_errors:
        logger.warning(
            f"No agent status header found; "
            f"{tool_error_count} tool error(s) during execution"
        )
    return "COMPLETE"


def _get_proxy_base_url_for_model(
    model: str,
    api_port: int = 40080,
    session_id: str | None = None,
) -> Optional[str]:
    """
    Check if a model requires routing through the LLM proxy.

    Models defined in config/llm-api-proxy.yaml are routed through the proxy
    endpoint, which handles format translation for non-Anthropic providers.

    Args:
        model: The model name (e.g., 'openrouter:openai/gpt-5.2').
        api_port: The API server port (default 40080).
        session_id: Optional session ID for debug file organization.

    Returns:
        The proxy base URL if the model needs proxy routing, None otherwise.
    """
    try:
        config = load_llm_proxy_config()
    except ProxyConfigError as e:
        logger.debug(f"LLM proxy config not available: {e}")
        return None

    # Check if model is defined in proxy config
    if model not in config.models:
        logger.debug(f"Model '{model}' not in proxy config, using direct Anthropic API")
        return None

    # Model is defined in proxy config - route through proxy
    mapping = config.models[model]
    provider = config.providers.get(mapping.provider)

    if provider is None:
        logger.warning(f"Model '{model}' references undefined provider '{mapping.provider}'")
        return None

    # All proxy-defined models go through the proxy, regardless of provider type
    # The proxy handles routing to the appropriate endpoint
    # NOTE: SDK appends "/v1/messages" to base_url, so we use /api/llm-proxy (not /api/llm-proxy/v1)
    # When session_id is provided, embed it in the URL for session-scoped debug output
    if session_id:
        proxy_url = f"http://127.0.0.1:{api_port}/api/llm-proxy/s/{session_id}"
    else:
        proxy_url = f"http://127.0.0.1:{api_port}/api/llm-proxy"
    logger.info(
        f"LLM_PROXY: Model '{model}' → provider '{mapping.provider}' "
        f"(type={provider.type}) → {proxy_url}"
    )
    return proxy_url


# User prompt template engine (lightweight, for user.md only)
_user_prompt_engine = PromptTemplateEngine()


class ClaudeAgent:
    """
    Ag3ntum - Self-Improving Agent.

    Executes tasks using the Claude Agent SDK with configurable
    tools, prompts, and execution limits.
    """

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        sessions_dir: Optional[Path] = None,
        logs_dir: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
        tracer: Optional[Union[TracerBase, bool]] = True,
        permission_manager: Optional[PermissionManager] = None,
        linux_uid: Optional[int] = None,
        linux_gid: Optional[int] = None,
    ) -> None:
        """
        Initialize the Claude Agent.

        Args:
            config: Agent configuration. Uses defaults if not provided.
            sessions_dir: Directory for sessions. Defaults to AGENT/sessions.
            logs_dir: Directory for logs. Defaults to AGENT/logs.
            skills_dir: Directory for skills. Defaults to AGENT/skills.
            tracer: Execution tracer for console output.
                - True (default): Use ExecutionTracer with default settings.
                - False/None: Disable tracing (NullTracer).
                - TracerBase instance: Use custom tracer.
            permission_manager: PermissionManager for permission checking.
                Required - agent will fail without permission profile.
            linux_uid: Linux UID for privilege dropping during command execution.
                When set, sandboxed commands will run as this UID instead of the API user.
            linux_gid: Linux GID for privilege dropping during command execution.
                When set, sandboxed commands will run with this GID.
        """
        self._config = config or AgentConfig()
        self._sessions_dir = sessions_dir or SESSIONS_DIR
        self._logs_dir = logs_dir or LOGS_DIR
        self._permission_manager = permission_manager
        self._linux_uid = linux_uid
        self._linux_gid = linux_gid

        # SECURITY: Validate that permission_mode is None or empty
        # Setting permission_mode to any value causes SDK to use --permission-prompt-tool stdio
        # which bypasses can_use_tool callback and all permission checks
        if self._config.permission_mode not in (None, "", "null"):
            logger.warning(
                f"SECURITY WARNING: permission_mode='{self._config.permission_mode}' is set. "
                f"This will bypass can_use_tool callback and disable permission checks! "
                f"Set permission_mode to null in config/agent.yaml to enable security."
            )
            raise AgentError(
                "permission_mode must be null (not 'default', 'acceptEdits', etc). "
                "Set it to null in agent.yaml to enable proper permission checking via can_use_tool callback."
            )

        # Determine skills directory from parameter, config, or default
        if skills_dir:
            self._skills_dir = skills_dir
        elif self._config.skills_dir:
            self._skills_dir = Path(self._config.skills_dir)
        else:
            self._skills_dir = SKILLS_DIR

        self._session_manager = SessionManager(self._sessions_dir)
        self._skill_manager = SkillManager(self._skills_dir)
        self._logs_dir.mkdir(parents=True, exist_ok=True)

        # Setup tracer
        if tracer is True:
            self._tracer: TracerBase = ExecutionTracer(verbose=True)
        elif tracer is False or tracer is None:
            self._tracer = NullTracer()
        else:
            self._tracer = tracer

        # Track permission denials for interruption handling
        self._denial_tracker = PermissionDenialTracker()
        self._sandbox_system_message: Optional[str] = None

        # Wire tracer to permission manager for profile notifications
        if self._permission_manager is not None:
            self._permission_manager.set_tracer(self._tracer)

    @property
    def config(self) -> AgentConfig:
        """Get the agent configuration."""
        return self._config

    @property
    def skill_manager(self) -> SkillManager:
        """Get the skill manager."""
        return self._skill_manager

    @property
    def tracer(self) -> TracerBase:
        """Get the execution tracer."""
        return self._tracer

    def _load_external_mounts_config(self, username: Optional[str] = None) -> dict:
        """
        Load external mounts configuration for template rendering.

        Returns a dict suitable for the mounts template with structure:
        {
            "ro": [{"name": "downloads", "description": "..."}],
            "rw": [{"name": "projects", "description": "..."}],
            "persistent": True/False
        }

        Args:
            username: Optional username for persistent storage check.

        Returns:
            External mounts configuration dict.
        """
        import yaml

        mounts_config = {
            "ro": [],
            "rw": [],
            "persistent": False,
        }

        # Load mounts manifest if it exists (auto-generated by run.sh)
        mounts_file = Path("/auto-generated/auto-generated-mounts.yaml")
        if mounts_file.exists():
            try:
                with open(mounts_file, "r", encoding="utf-8") as f:
                    manifest = yaml.safe_load(f) or {}

                mounts_data = manifest.get("mounts", {})

                # Read-only mounts
                if isinstance(mounts_data.get("ro"), list):
                    for mount in mounts_data["ro"]:
                        if isinstance(mount, dict) and mount.get("name"):
                            mounts_config["ro"].append({
                                "name": mount["name"],
                                "description": mount.get("description", ""),
                            })

                # Read-write mounts
                if isinstance(mounts_data.get("rw"), list):
                    for mount in mounts_data["rw"]:
                        if isinstance(mount, dict) and mount.get("name"):
                            mounts_config["rw"].append({
                                "name": mount["name"],
                                "description": mount.get("description", ""),
                            })

                # Log successful mount config loading
                ro_count = len(mounts_config["ro"])
                rw_count = len(mounts_config["rw"])
                if ro_count > 0 or rw_count > 0:
                    logger.info(
                        f"Loaded external mounts config: {ro_count} RO, {rw_count} RW"
                    )
                else:
                    logger.debug("External mounts manifest exists but contains no mounts")

            except Exception as e:
                logger.warning(f"Failed to load mounts config: {e}")
        else:
            logger.debug(f"No external mounts manifest at {mounts_file}")

        # Check if persistent storage exists for user
        if username:
            persistent_dir = Path(f"/users/{username}/ag3ntum/persistent")
            mounts_config["persistent"] = persistent_dir.exists()

            # Load per-user mounts from external-mounts.yaml
            try:
                from ..services.mount_service import get_user_mounts
                user_mounts = get_user_mounts(username)

                # Add user-specific RO mounts
                mounts_config["user_ro"] = [
                    {"name": m["name"], "description": m.get("description", "")}
                    for m in user_mounts.get("ro", [])
                ]

                # Add user-specific RW mounts
                mounts_config["user_rw"] = [
                    {"name": m["name"], "description": m.get("description", "")}
                    for m in user_mounts.get("rw", [])
                ]

                if mounts_config["user_ro"] or mounts_config["user_rw"]:
                    logger.debug(
                        f"Loaded per-user mounts for '{username}': "
                        f"{len(mounts_config['user_ro'])} RO, {len(mounts_config['user_rw'])} RW"
                    )
            except Exception as e:
                logger.debug(f"No per-user mounts for '{username}': {e}")
                mounts_config["user_ro"] = []
                mounts_config["user_rw"] = []

        return mounts_config

    def _load_original_path_mounts_for_prompt(
        self,
        username: Optional[str] = None,
        dynamic_mounts: Optional[list] = None,
    ) -> list[dict]:
        """
        Collect all original-path mount info for the mounts template.

        Gathers host paths from:
        1. The original_paths config section
        2. All standard external mounts (which auto-get original-path support)
        3. Dynamic mounts (which also auto-get original-path support)

        Returns list of {"path": "/var/log", "mode": "ro", "description": "..."}.
        """
        result: list[dict] = []
        seen_paths: set[str] = set()

        # 1. Original-path mounts from config
        if username:
            try:
                from ..services.mount_service import get_original_path_mount_service
                orig_service = get_original_path_mount_service()
                for mount in orig_service.get_mounts_for_user(username):
                    if mount.path not in seen_paths:
                        seen_paths.add(mount.path)
                        result.append({
                            "path": mount.path,
                            "mode": mount.mode,
                            "description": mount.description or "",
                        })
            except Exception as e:
                logger.debug(f"No original-path mounts from config: {e}")

        # 2. Standard external mounts (global + per-user) — all have host paths
        try:
            from ..services.mount_service import get_all_mounts_with_host_paths
            ext_mounts = get_all_mounts_with_host_paths(username)
            for mode in ("ro", "rw"):
                for m in ext_mounts.get(mode, []):
                    hp = m.get("host_path", "")
                    if hp and hp not in seen_paths:
                        seen_paths.add(hp)
                        result.append({
                            "path": hp,
                            "mode": mode,
                            "description": m.get("description", ""),
                        })
        except Exception as e:
            logger.debug(f"No external mount host paths for prompt: {e}")

        # 3. Dynamic mounts
        if dynamic_mounts:
            for dm in dynamic_mounts:
                hp = getattr(dm, "host_path", None) or ""
                if hp and hp not in seen_paths:
                    seen_paths.add(hp)
                    result.append({
                        "path": hp,
                        "mode": getattr(dm, "mode", "ro"),
                        "description": getattr(dm, "description", ""),
                    })

        return result

    def _setup_workspace_skills(
        self,
        session_id: str,
        username: Optional[str] = None
    ) -> None:
        """
        Create merged skills directory with symlinks in workspace.

        Skills are discovered from:
        1. Global skills: SKILLS_DIR/.claude/skills/
        2. User skills: USERS_DIR/<username>/.claude/skills/

        User skills with the same name override global skills.
        The SDK discovers skills via setting_sources=["project"] from
        workspace/.claude/skills/.

        IMPORTANT ARCHITECTURE NOTE:
        Symlinks must point to paths that work in BOTH Docker and bwrap environments:
        - MCP tools (Read, Write, Glob, etc.) run in Docker container OUTSIDE bwrap
        - Bash tool runs INSIDE bwrap sandbox
        - Both environments now have CONSISTENT mounts (see permissions.yaml):
          - /skills = ./skills (same in both Docker and bwrap)
          - /user-skills = per-user skills mount (same in both Docker and bwrap)

        Symlink paths: /skills/.claude/skills/foo, /user-skills/foo
        These work in both MCP tools and Bash.
        SECURITY: User skills are per-user mounts to prevent cross-user access.

        Args:
            session_id: The session ID for workspace access.
            username: Optional username for user-specific skills.
        """
        if not self._config.enable_skills:
            return

        workspace_dir = self._session_manager.get_workspace_dir(session_id)
        skills_target = workspace_dir / ".claude" / "skills"

        # Clean existing and recreate
        if skills_target.exists():
            shutil.rmtree(skills_target)
        skills_target.mkdir(parents=True, exist_ok=True)

        # NOTE: .claude/ is owned by the API process (UID 45045) with default
        # umask permissions. It is intentionally NOT world-readable inside bwrap.
        # The SDK reads skills from the Docker context (outside bwrap), so the
        # sandbox user does not need access to .claude/ via Bash.

        # Discover merged skills using shared function (global + user, with user overriding)
        skill_sources = discover_merged_skills(username=username)

        # Paths used to determine skill source type
        global_skills_base = SKILLS_DIR / ".claude" / "skills"
        user_skills_base = USERS_DIR / username / ".claude" / "skills" if username else None

        # Create symlinks pointing to DOCKER paths (not bwrap sandbox paths)
        # MCP tools run outside bwrap and see Docker's filesystem:
        #   - Global skills: /skills/.claude/skills/<skill_name>
        #   - User skills: /user-skills/<skill_name> (mounted from /users/<username>/.claude/skills)
        for skill_name, source_path in skill_sources.items():
            link_path = skills_target / skill_name

            # User skills override global, so check user first
            if user_skills_base and str(source_path).startswith(str(user_skills_base)):
                docker_path = Path("/user-skills") / skill_name
            else:
                docker_path = Path("/skills") / ".claude" / "skills" / skill_name

            try:
                link_path.symlink_to(docker_path)
                logger.debug(f"Linked skill: {skill_name} -> {docker_path} (source: {source_path})")
            except Exception as e:
                logger.warning(f"Failed to create skill symlink {skill_name}: {e}")

        skill_names = sorted(skill_sources.keys())
        logger.info(
            f"Refreshed skills ({len(skill_sources)}): {', '.join(skill_names) if skill_names else 'none'} "
            f"-> {skills_target}"
        )

    def _cleanup_session(self, session_id: str, owner_uid: Optional[int] = None) -> None:
        """
        Clean up session resources after agent run completes.

        Removes copied skills from workspace to save disk space.
        Session metadata is preserved. Also hardens file permissions
        to ensure session isolation.

        Args:
            session_id: The session ID to clean up.
            owner_uid: Optional owner UID for permission hardening.
                       If not provided, gets owner from directory ownership.
        """
        # Remove skills folder from workspace
        self._session_manager.cleanup_workspace_skills(session_id)

        # Clear session context from permission manager
        if self._permission_manager is not None:
            self._permission_manager.clear_session_context()

        # Clean up PathValidator for this session
        cleanup_path_validator(session_id)

        # SECURITY: Harden session file permissions after agent run
        # This ensures all files created during execution have proper 700/600 permissions
        # with owner-only access (true session isolation)
        try:
            from .sessions import ensure_secure_session_files
            session_dir = self._session_manager.get_session_dir(session_id)

            # Get owner_uid from directory if not provided
            if owner_uid is None:
                try:
                    stat = session_dir.stat()
                    owner_uid = stat.st_uid
                except OSError:
                    pass

            ensure_secure_session_files(session_dir, owner_uid)
        except Exception as e:
            # Don't fail cleanup on permission hardening failure
            logger.warning(f"Failed to harden session permissions for {session_id}: {e}")

    def _build_options(
        self,
        session_context: SessionContext,
        system_prompt: str,
        trace_processor: Optional[Any] = None,
        resume_id: Optional[str] = None,
        fork_session: bool = False,
        username: Optional[str] = None,
        dynamic_mounts: Optional[list] = None,
        ssh_context: Optional[Any] = None,
    ) -> ClaudeAgentOptions:
        """
        Build ClaudeAgentOptions for the SDK.

        Args:
            session_context: Session context with session_id and related data.
            system_prompt: System prompt (required, must not be empty).
            trace_processor: Optional trace processor for permission denial tracking.
            resume_id: Claude's session ID for resuming conversations (optional).
            fork_session: If True, fork to new session when resuming (optional).
            username: Optional username for loading user-specific sandboxed environment variables.
            dynamic_mounts: List of DynamicMountInfo objects for this session (optional).

        Returns:
            ClaudeAgentOptions configured for execution.

        Raises:
            AgentError: If required parameters are missing or invalid.
        """
        # Validate required inputs - fail fast
        if not system_prompt or not system_prompt.strip():
            raise AgentError(
                "system_prompt is required and must not be empty. "
                "Load prompts from AGENT/prompts/ before calling _build_options."
            )
        all_tools = list(self._config.allowed_tools)
        if self._config.enable_skills and "Skill" not in all_tools:
            all_tools.append("Skill")

        # Permission management: permission manager is required
        if self._permission_manager is None:
            raise AgentError(
                "PermissionManager is required. "
                "Agent cannot run without permission profile."
            )

        # Activate permission profile
        self._permission_manager.activate()

        # Get tool configuration from active profile
        permission_checked_tools = self._permission_manager.get_permission_checked_tools()
        sandbox_disabled_tools = self._permission_manager.get_disabled_tools()

        # Pre-approved tools (no permission check needed)
        allowed_tools = [
            t for t in all_tools
            if t not in permission_checked_tools and t not in sandbox_disabled_tools
        ]

        # Available tools (excluding completely disabled ones)
        available_tools = [
            t for t in all_tools
            if t not in sandbox_disabled_tools
        ]

        # Disabled tools list for SDK
        disallowed_tools = list(sandbox_disabled_tools)

        active_profile = self._permission_manager.active_profile
        logger.info(
            f"SANDBOX: Using profile '{active_profile.name}' for task execution"
        )
        logger.info(f"SANDBOX: permission_checked_tools={permission_checked_tools}")
        logger.info(f"SANDBOX: available_tools={available_tools}")
        logger.info(f"SANDBOX: allowed_tools (pre-approved)={allowed_tools}")
        logger.info(f"SANDBOX: disallowed_tools (blocked)={disallowed_tools}")

        # Build list of accessible directories from the active profile
        working_dir = Path(self._config.working_dir) if self._config.working_dir else AGENT_DIR
        profile_dirs = self._permission_manager.get_allowed_dirs()
        add_dirs = []
        for dir_path in profile_dirs:
            # Resolve relative paths (e.g., "./input") to absolute paths
            if dir_path.startswith("./"):
                add_dirs.append(str(working_dir / dir_path[2:]))
            elif dir_path.startswith("/"):
                add_dirs.append(dir_path)
            else:
                add_dirs.append(str(working_dir / dir_path))
        logger.info(f"SANDBOX: Profile allowed_dirs={add_dirs}")

        # Use workspace subdirectory as cwd to prevent reading session logs
        # The workspace only contains files the agent should access
        workspace_dir = self._session_manager.get_workspace_dir(
            session_context.session_id
        )

        # Load sandboxed environment variables (global + user-specific overrides)
        # These will be available inside the bubblewrap sandbox for Ag3ntumBash commands
        sandboxed_envs = load_sandboxed_envs(username=username)
        if sandboxed_envs:
            logger.info(
                f"SANDBOX: Loaded {len(sandboxed_envs)} sandboxed env vars for user '{username}': "
                f"{list(sandboxed_envs.keys())}"
            )

        sandbox_config = self._permission_manager.get_sandbox_config(
            sandboxed_envs=sandboxed_envs
        )

        # Per-user mounts and external mounts are now added after PathValidator
        # mount loading (see "SECURITY: Mount only authorized mounts" block below)
        # to avoid loading mount data twice and to ensure consistent path formats.

        # Add bwrap mount for persistent Docker path so workspace symlink resolves
        # The workspace symlink: ./persistent -> /users/{username}/ag3ntum/persistent
        # Bwrap must mount the Docker path at the SAME path inside the sandbox,
        # following the same pattern as external mount symlinks.
        # (The /persistent mount from permissions.yaml still exists as an alias.)
        if sandbox_config and sandbox_config.enabled and username:
            persistent_docker_path = f"/users/{username}/ag3ntum/persistent"
            if Path(persistent_docker_path).exists():
                sandbox_config.dynamic_mounts.append(SandboxMount(
                    source=persistent_docker_path,
                    target=persistent_docker_path,  # Same path so symlink works!
                    mode="rw",
                    optional=True,
                ))
                logger.debug(f"SANDBOX: Added persistent Docker path mount: {persistent_docker_path}")

        self._sandbox_system_message = self._format_sandbox_system_message(
            sandbox_config=sandbox_config,
            workspace_dir=workspace_dir,
        )

        # Build custom sandbox executor for bubblewrap isolation
        # SDK's built-in sandbox doesn't work reliably in Docker environments,
        # so we use our own bubblewrap wrapper via the permission callback
        sandbox_executor = self._build_sandbox_executor(sandbox_config, workspace_dir)

        # Create permission callback using the permission manager
        # Pass tracer's on_permission_check for tracing (if available)
        # Pass denial tracker to record denials
        # Pass trace_processor so permission denial shows FAILED status
        # Pass sandbox_executor to wrap Bash commands in bubblewrap
        on_permission_check = (
            self._tracer.on_permission_check
            if hasattr(self._tracer, 'on_permission_check')
            else None
        )
        # Clear any previous denials before starting new run
        self._denial_tracker.clear()
        can_use_tool = create_permission_callback(
            permission_manager=self._permission_manager,
            on_permission_check=on_permission_check,
            denial_tracker=self._denial_tracker,
            trace_processor=trace_processor,
            system_message_builder=self._sandbox_system_message_builder,
        )

        all_tools = available_tools

        # Get session directory for isolated Claude storage (CLAUDE_CONFIG_DIR)
        session_dir = self._session_manager.get_session_dir(session_context.session_id)

        # Set up MCP servers for additional tools
        mcp_servers: dict[str, Any] = {}

        # Configure PathValidator for this session BEFORE creating MCP tools
        # The validator runs in the main Python process (outside bwrap) and
        # translates agent paths (/workspace/...) to real Docker paths
        #
        # IMPORTANT: Skills paths must be DOCKER paths (not bwrap paths) because
        # MCP tools (Read, Write, etc.) run outside bwrap and see the Docker filesystem.
        # Docker mounts: ./skills:/skills, /users/{username}/.claude/skills:/user-skills
        # So global skills are at /skills/.claude/skills/ and user skills at /user-skills/
        try:
            global_skills = None
            user_skills = None
            if self._config.enable_skills:
                global_skills = Path("/skills/.claude/skills")
                if username:
                    user_skills = Path("/user-skills")

            # External mount paths (Docker container paths)
            # With flattened mount structure, all mounts are at /mounts/{name}
            # Agent sees: /workspace/external/ro/* -> Real path: /mounts/{name}
            # Agent sees: /workspace/external/rw/* -> Real path: /mounts/{name}
            # Agent sees: /workspace/persistent/* -> Real path: /users/{username}/ag3ntum/persistent/*
            persistent_path = Path(f"/users/{username}/ag3ntum/persistent") if username else None

            # Load global mounts from manifest for PathValidator
            # These are configured via external-mounts.yaml global section
            # All mounts now appear at /mounts/{name} (flattened structure)
            from ..services.mount_service import get_global_mounts_for_path_validator
            global_mounts = get_global_mounts_for_path_validator()
            global_mounts_ro_paths = global_mounts.get("ro", {})
            global_mounts_rw_paths = global_mounts.get("rw", {})

            # Load per-user mounts for PathValidator
            # These are configured via external-mounts.yaml per_user section
            # Mounts appear at /mounts/{name} in Docker (flattened structure)
            user_mounts_ro_paths: dict[str, Path] = {}
            user_mounts_rw_paths: dict[str, Path] = {}

            if username:
                from ..services.mount_service import get_user_mounts
                try:
                    user_mounts_data = get_user_mounts(username)
                    for mount_info in user_mounts_data.get("ro", []):
                        name = mount_info["name"]
                        mount_path = Path(f"/mounts/{name}")
                        if mount_path.exists() or mount_info.get("optional", True):
                            user_mounts_ro_paths[name] = mount_path
                    for mount_info in user_mounts_data.get("rw", []):
                        name = mount_info["name"]
                        mount_path = Path(f"/mounts/{name}")
                        if mount_path.exists() or mount_info.get("optional", True):
                            user_mounts_rw_paths[name] = mount_path
                except Exception as e:
                    logger.warning(f"Failed to load per-user mounts for PathValidator: {e}")

            # SECURITY: Mount only authorized mounts individually into bwrap.
            # Instead of mounting the entire /mounts tree (which exposes ALL
            # mounts to every user), each authorized mount is added individually.
            # This prevents cross-user mount visibility via Bash.
            #
            # In Docker (nested container mode), the host filesystem is the base
            # for bwrap. Without --tmpfs /mounts, all Docker volumes at /mounts/
            # would be visible. The tmpfs creates a clean empty /mounts directory,
            # then only authorized mounts are bind-mounted into it.
            if sandbox_config and sandbox_config.enabled:
                # Create tmpfs at /mounts to hide all Docker mount volumes,
                # then add only authorized mounts individually below.
                sandbox_config.tmpfs_paths.append("/mounts")

                bwrap_mount_count = 0
                all_bwrap_mounts = [
                    (global_mounts_ro_paths, "ro"),
                    (global_mounts_rw_paths, "rw"),
                    (user_mounts_ro_paths, "ro"),
                    (user_mounts_rw_paths, "rw"),
                ]
                for mount_dict, mode in all_bwrap_mounts:
                    for _name, _mount_path in mount_dict.items():
                        source = str(_mount_path)
                        if Path(source).exists():
                            sandbox_config.dynamic_mounts.append(SandboxMount(
                                source=source,
                                target=source,
                                mode=mode,
                                optional=True,
                            ))
                            bwrap_mount_count += 1
                        else:
                            logger.warning(
                                f"SANDBOX: Skipping mount '{_name}': path {source} does not exist"
                            )
                if bwrap_mount_count > 0:
                    logger.info(f"SANDBOX: Added {bwrap_mount_count} authorized external mounts to bwrap")

            # Build dynamic mount paths for PathValidator
            dynamic_mounts_ro_paths: dict[str, Path] = {}
            dynamic_mounts_rw_paths: dict[str, Path] = {}
            # Also track host paths for original-path support
            dynamic_host_paths: list[tuple[str, str, str]] = []  # (host_path, container_path, mode)
            if dynamic_mounts:
                for mount_info in dynamic_mounts:
                    # Build container path: /mounts/{base}/{subpath} (flattened structure)
                    container_path = f"/mounts/{mount_info.source_base}"
                    if mount_info.source_subpath:
                        container_path = f"{container_path}/{mount_info.source_subpath}"
                    mount_path = Path(container_path)

                    if mount_info.mode == "ro":
                        dynamic_mounts_ro_paths[mount_info.alias] = mount_path
                    else:
                        dynamic_mounts_rw_paths[mount_info.alias] = mount_path

                    # Also add to sandbox config for Bubblewrap binding
                    sandbox_config.dynamic_mounts.append(SandboxMount(
                        source=container_path,
                        target=container_path,  # Same path so symlinks work
                        mode=mount_info.mode,
                        optional=True,
                    ))

                    # Track host_path for original-path support (e.g., /var/log)
                    if mount_info.host_path:
                        dynamic_host_paths.append((mount_info.host_path, container_path, mount_info.mode))

                logger.info(
                    f"Added {len(dynamic_mounts)} dynamic mounts: "
                    f"{len(dynamic_mounts_ro_paths)} RO, {len(dynamic_mounts_rw_paths)} RW"
                )

            # Load original-path mounts for this user
            # These allow accessing paths like /var/log at their original locations
            original_path_mounts_ro: dict[str, Path] = {}
            original_path_mounts_rw: dict[str, Path] = {}
            if username:
                from ..services.mount_service import get_original_path_mount_service
                try:
                    orig_mount_service = get_original_path_mount_service()
                    orig_mounts = orig_mount_service.get_mounts_for_user(username)
                    for mount in orig_mounts:
                        docker_path = Path(mount.container_path)
                        if docker_path.exists() or mount.optional:
                            if mount.mode == "ro":
                                original_path_mounts_ro[mount.path] = docker_path
                            else:
                                original_path_mounts_rw[mount.path] = docker_path

                            # Add to sandbox config for Bubblewrap binding
                            # Bind Docker path to original location inside sandbox
                            sandbox_config.original_path_mounts.append(SandboxMount(
                                source=mount.container_path,  # Docker path
                                target=mount.path,  # Original path (bind target)
                                mode=mount.mode,
                                optional=mount.optional,
                            ))
                    if orig_mounts:
                        logger.info(
                            f"Added {len(orig_mounts)} original-path mounts: "
                            f"{len(original_path_mounts_ro)} RO, {len(original_path_mounts_rw)} RW"
                        )
                except Exception as e:
                    logger.warning(f"Failed to load original-path mounts: {e}")

            # Also add external mounts (global and user) to original_path_mounts
            # This enables agents to use host paths like /var/log directly,
            # not just internal paths like ./external/ro/global_var_log
            try:
                from ..services.mount_service import get_all_mounts_with_host_paths
                external_mounts_with_host_paths = get_all_mounts_with_host_paths(username)
                external_original_count = 0

                for mode in ("ro", "rw"):
                    target_dict = original_path_mounts_ro if mode == "ro" else original_path_mounts_rw
                    for mount_info in external_mounts_with_host_paths.get(mode, []):
                        host_path = mount_info["host_path"]
                        container_path = mount_info["container_path"]
                        docker_path = Path(container_path)

                        # Skip if already added (from original_paths config)
                        if host_path in target_dict:
                            continue

                        target_dict[host_path] = docker_path
                        external_original_count += 1

                        # Add to sandbox config for Bubblewrap binding
                        sandbox_config.original_path_mounts.append(SandboxMount(
                            source=container_path,  # Docker path
                            target=host_path,  # Original host path (bind target)
                            mode=mode,
                            optional=True,
                        ))

                if external_original_count > 0:
                    logger.info(
                        f"Added {external_original_count} external mounts to original-path support "
                        f"(total: {len(original_path_mounts_ro)} RO, {len(original_path_mounts_rw)} RW)"
                    )
            except Exception as e:
                logger.warning(f"Failed to add external mounts to original-path support: {e}")

            # Add dynamic mount host paths to original-path support
            # This enables agents to use /var/log directly when /var/log is a dynamic mount
            if dynamic_host_paths:
                dynamic_original_count = 0
                for host_path, container_path, mode in dynamic_host_paths:
                    target_dict = original_path_mounts_ro if mode == "ro" else original_path_mounts_rw
                    # Skip if already added
                    if host_path in target_dict:
                        continue
                    target_dict[host_path] = Path(container_path)
                    dynamic_original_count += 1
                    # Add to sandbox config for Bubblewrap binding
                    sandbox_config.original_path_mounts.append(SandboxMount(
                        source=container_path,
                        target=host_path,
                        mode=mode,
                        optional=True,
                    ))
                if dynamic_original_count > 0:
                    logger.info(
                        f"Added {dynamic_original_count} dynamic mounts to original-path support "
                        f"(total: {len(original_path_mounts_ro)} RO, {len(original_path_mounts_rw)} RW)"
                    )

            configure_path_validator(
                session_id=session_context.session_id,
                workspace_path=workspace_dir,
                username=username,  # Pass username to configure SandboxPathResolver
                skills_path=self._skills_dir if self._config.enable_skills else None,
                global_skills_path=global_skills,
                user_skills_path=user_skills,
                global_mounts_ro=global_mounts_ro_paths if global_mounts_ro_paths else None,
                global_mounts_rw=global_mounts_rw_paths if global_mounts_rw_paths else None,
                persistent_path=persistent_path if persistent_path and persistent_path.exists() else None,
                user_mounts_ro=user_mounts_ro_paths if user_mounts_ro_paths else None,
                user_mounts_rw=user_mounts_rw_paths if user_mounts_rw_paths else None,
                dynamic_mounts_ro=dynamic_mounts_ro_paths if dynamic_mounts_ro_paths else None,
                dynamic_mounts_rw=dynamic_mounts_rw_paths if dynamic_mounts_rw_paths else None,
                original_path_mounts_ro=original_path_mounts_ro if original_path_mounts_ro else None,
                original_path_mounts_rw=original_path_mounts_rw if original_path_mounts_rw else None,
            )
            logger.info(
                f"PathValidator configured for session {session_context.session_id}, "
                f"workspace={workspace_dir}, global_skills={global_skills}, user_skills={user_skills}, "
                f"global_mounts={len(global_mounts_ro_paths)} RO/{len(global_mounts_rw_paths)} RW, "
                f"persistent={persistent_path if persistent_path and persistent_path.exists() else None}, "
                f"user_mounts={len(user_mounts_ro_paths)} RO/{len(user_mounts_rw_paths)} RW, "
                f"original_paths={len(original_path_mounts_ro)} RO/{len(original_path_mounts_rw)} RW"
            )
            # Register sandbox UID so file tools can chown files to the session user
            if self._linux_uid is not None:
                set_session_linux_uid(session_context.session_id, self._linux_uid)

        except Exception as e:
            logger.error(f"Failed to configure PathValidator: {e}")
            raise AgentError(f"PathValidator configuration failed: {e}")

        # Add unified Ag3ntum MCP server containing ALL tools (Bash + file tools)
        # All tools share the same server name "ag3ntum" for consistent naming:
        # mcp__ag3ntum__Bash, mcp__ag3ntum__Read, mcp__ag3ntum__Write, etc.
        # SECURITY: Bash uses bwrap sandbox, file tools use PathValidator
        # NOTE: MCP tools are REQUIRED - fail fast if creation fails
        session_id = session_context.session_id
        include_bash = AG3NTUM_BASH_TOOL in all_tools
        try:
            # Create unified MCP server with ALL Ag3ntum tools
            # Tool names: mcp__ag3ntum__Bash, mcp__ag3ntum__Read, mcp__ag3ntum__Write,
            #            mcp__ag3ntum__Edit, mcp__ag3ntum__MultiEdit, mcp__ag3ntum__Glob,
            #            mcp__ag3ntum__Grep, mcp__ag3ntum__LS, mcp__ag3ntum__WebFetch,
            #            mcp__ag3ntum__AskUserQuestion
            ag3ntum_server = create_ag3ntum_tools_mcp_server(
                session_id=session_id,
                workspace_path=workspace_dir,
                sandbox_executor=sandbox_executor,  # SECURITY: Enable bwrap for Bash
                include_bash=include_bash,
                ssh_context=ssh_context,
                server_name="ag3ntum"
            )
            mcp_servers["ag3ntum"] = ag3ntum_server

            # CRITICAL: Add MCP tool names to all_tools list for subagent access
            # The SDK's AgentDefinition.tools filters from the parent's available tools.
            # Without this, subagents can't use MCP tools even if specified in their config.
            # Tool names follow the mcp__{server}__{tool} convention.
            ag3ntum_tool_names = [
                "mcp__ag3ntum__Read",
                "mcp__ag3ntum__ReadDocument",
                "mcp__ag3ntum__Write",
                "mcp__ag3ntum__Edit",
                "mcp__ag3ntum__MultiEdit",
                "mcp__ag3ntum__Glob",
                "mcp__ag3ntum__Grep",
                "mcp__ag3ntum__LS",
                "mcp__ag3ntum__WebFetch",
                "mcp__ag3ntum__AskUserQuestion",
            ]
            if include_bash:
                ag3ntum_tool_names.append("mcp__ag3ntum__Bash")

            # Add SSH tool names if SSH context is available
            # SSH tools are pre-approved (added to allowed_tools) because
            # access is already gated by feature flags + per-user enablement,
            # and the tools enforce their own security (command filter, host
            # blocking, rate limiting, credential vault).
            if ssh_context is not None:
                from tools.ag3ntum.ag3ntum_ssh.tool import (
                    AG3NTUM_SSH_EXEC_TOOL,
                    AG3NTUM_SSH_READ_TOOL,
                    AG3NTUM_SSH_CONNECT_TOOL,
                )
                ssh_tool_names = [
                    AG3NTUM_SSH_EXEC_TOOL,
                    AG3NTUM_SSH_READ_TOOL,
                    AG3NTUM_SSH_CONNECT_TOOL,
                ]
                ag3ntum_tool_names.extend(ssh_tool_names)
                # Pre-approve SSH tools so the SDK permission system doesn't
                # block them — SSH security is enforced by the tools themselves
                allowed_tools.extend(ssh_tool_names)

            # Add to all_tools so they're available for subagent tool filtering
            all_tools.extend(ag3ntum_tool_names)

            tool_count = len(ag3ntum_tool_names)
            logger.info(
                f"Ag3ntum unified MCP server configured ({tool_count} tools, "
                f"Bash: {include_bash}, sandbox: {'ENABLED' if sandbox_executor else 'DISABLED'})"
            )
            logger.debug(f"MCP tools added to all_tools: {ag3ntum_tool_names}")
        except Exception as e:
            # MCP tools are critical - fail fast with clear error
            raise AgentError(
                f"CRITICAL: Failed to create Ag3ntum MCP server. "
                f"MCP tools (mcp__ag3ntum__*) are required for Ag3ntum to function. "
                f"Error: {e}"
            )

        # Add skills MCP server for script-based skills
        # SECURITY: Script skills MUST run inside the Bubblewrap sandbox
        # Environment variables (sandboxed_envs) are injected via sandbox config's custom_env
        if self._config.enable_skills and sandbox_executor is not None:
            try:
                skill_tools_manager = SkillToolsManager(
                    skills_dir=self._skills_dir,
                    workspace_dir=workspace_dir,
                    sandbox_executor=sandbox_executor,
                )
                skill_tools_manager.initialize()

                skill_tool_names = skill_tools_manager.get_tool_definitions()
                if skill_tool_names:
                    skills_server = skill_tools_manager.create_mcp_server(
                        name="skills",
                        version="1.0.0"
                    )
                    mcp_servers["skills"] = skills_server
                    logger.info(
                        f"Skills MCP server configured ({len(skill_tool_names)} script skills, "
                        f"sandbox: ENABLED, envs via sandbox config)"
                    )
            except Exception as e:
                logger.warning(f"Failed to create skills MCP server: {e}")
        elif self._config.enable_skills and sandbox_executor is None:
            logger.warning(
                "Skills MCP server NOT created: SandboxExecutor is required for script-based skills. "
                "Instruction-based skills (SKILL.md) can still use mcp__ag3ntum__Bash."
            )

        # Get subagent overrides from the global SubagentManager singleton
        # These override Claude Code's built-in subagents (general-purpose, etc.)
        # and disable unwanted ones (claude-code-guide, statusline-setup)
        subagent_manager = get_subagent_manager()
        agents = subagent_manager.get_agents_dict()
        if agents:
            logger.info(
                f"SUBAGENTS: Using {len(agents)} custom subagent definitions "
                f"(enabled: {subagent_manager.list_enabled_agents()}, "
                f"disabled: {subagent_manager.list_disabled_agents()})"
            )

        # Build environment variables
        # Use base_model (without :mode=thinking suffix) for API calls
        # Set MAX_THINKING_TOKENS when thinking mode is enabled
        env_vars = {"CLAUDE_CONFIG_DIR": str(session_dir)}
        thinking_tokens = self._config.effective_thinking_tokens
        if thinking_tokens:
            env_vars["MAX_THINKING_TOKENS"] = str(thinking_tokens)
            logger.info(
                f"THINKING: Extended thinking enabled with {thinking_tokens} token budget"
            )

        # Check if model needs LLM proxy routing (non-Anthropic models)
        # This sets ANTHROPIC_BASE_URL to route requests through our proxy
        # session_id is embedded in the URL for session-scoped debug output
        proxy_base_url = _get_proxy_base_url_for_model(
            self._config.base_model,
            session_id=session_context.session_id,
        )
        if proxy_base_url:
            env_vars["ANTHROPIC_BASE_URL"] = proxy_base_url
            logger.info(f"LLM_PROXY: Routing model '{self._config.base_model}' via {proxy_base_url}")

        # Build hooks configuration
        # PreCompact: logs when context compaction is triggered for diagnostics
        hooks_config: dict[str, list] = {}
        pre_compact_hook = create_pre_compact_hook(hook_logger=logger)
        hooks_config["PreCompact"] = [HookMatcher(hooks=[pre_compact_hook])]

        logger.info(
            f"SANDBOX: Final ClaudeAgentOptions - "
            f"tools={all_tools}, allowed_tools={allowed_tools}, "
            f"disallowed_tools={disallowed_tools}, "
            f"can_use_tool={'SET' if can_use_tool else 'NONE'}, "
            f"cwd={workspace_dir}, "
            f"CLAUDE_CONFIG_DIR={session_dir}, "
            f"mcp_servers={list(mcp_servers.keys())}, "
            f"bwrap_sandbox={'ENABLED' if sandbox_executor else 'DISABLED'}, "
            f"resume={resume_id}, fork_session={fork_session}, "
            f"agents={list(agents.keys()) if agents else 'none'}, "
            f"thinking={'ENABLED (' + str(thinking_tokens) + ' tokens)' if thinking_tokens else 'DISABLED'}"
        )

        # Capture SDK stderr for debugging (especially exit code 1 on resume)
        def _sdk_stderr_callback(line: str) -> None:
            logger.debug("SDK_STDERR: %s", line.rstrip())

        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self._config.base_model,  # Use base model without :mode=thinking suffix
            max_turns=self._config.max_turns,
            permission_mode=None,  # CRITICAL: Explicitly set to None to use can_use_tool callback
            tools=all_tools,  # Available tools (excluding disabled)
            allowed_tools=allowed_tools,  # Pre-approved (no permission check)
            disallowed_tools=disallowed_tools,  # Completely blocked tools
            mcp_servers=mcp_servers if mcp_servers else None,
            cwd=str(workspace_dir),  # Sandboxed workspace, not session dir
            add_dirs=add_dirs,
            setting_sources=["project"] if self._config.enable_skills else [],
            can_use_tool=can_use_tool,  # Includes bwrap sandboxing for Bash
            env=env_vars,  # Per-session storage + thinking config
            resume=resume_id,  # Claude's session ID for resumption
            fork_session=fork_session,  # Fork instead of continue when resuming
            enable_file_checkpointing=self._config.enable_file_checkpointing,
            max_buffer_size=self._config.max_buffer_size,
            output_format=self._config.output_format,
            include_partial_messages=self._config.include_partial_messages,
            agents=agents if agents else None,  # Subagent overrides (global singleton)
            hooks=hooks_config if hooks_config else None,  # SDK hooks (PreCompact, etc.)
            stderr=_sdk_stderr_callback,  # Capture SDK process stderr for debugging
        )

    def _build_user_prompt(
        self,
        task: str,
        session_context: SessionContext,
        parameters: Optional[dict] = None
    ) -> str:
        """
        Build the user prompt from template.

        Args:
            task: The task description.
            session_context: Session context with session_id.
            parameters: Additional template parameters.

        Returns:
            Rendered user prompt.

        Raises:
            AgentError: If user prompt template is missing or invalid.
        """
        # Validate task is provided
        if not task or not task.strip():
            raise AgentError("Task is required and must not be empty")

        # Validate user prompt template exists
        user_template_path = PROMPTS_DIR / "user.md"
        if not user_template_path.exists():
            raise AgentError(
                f"User prompt template not found in: {PROMPTS_DIR}\n"
                f"Create the template file in AGENT/prompts/user.md"
            )

        params = parameters or {}
        workspace_dir = self._session_manager.get_workspace_dir(
            session_context.session_id
        )
        try:
            # Build context for user prompt rendering
            user_context = PromptContext()
            user_context.strings["TASK"] = task
            user_context.strings["WORKING_DIR"] = self._config.working_dir or str(workspace_dir)
            if params.get("context"):
                user_context.strings["CONTEXT"] = params["context"]
            user_context.flags["HAS_CONTEXT"] = bool(params.get("context"))
            user_prompt = _user_prompt_engine.load_and_render(
                user_template_path, user_context
            )
        except Exception as e:
            raise AgentError(f"Failed to render user prompt template: {e}") from e

        if not user_prompt or not user_prompt.strip():
            raise AgentError("User prompt is empty after rendering")

        return user_prompt

    def _build_sandbox_executor(
        self,
        sandbox_config: Optional[SandboxConfig],
        workspace_dir: Path,
    ) -> Optional[SandboxExecutor]:
        """
        Build a SandboxExecutor with resolved mounts for bubblewrap isolation.

        This creates the executor that will wrap Bash commands in bubblewrap
        to provide proper filesystem isolation within Docker containers.

        When linux_uid/linux_gid are set on the agent, sandboxed commands will
        drop privileges to run as that user instead of the API user (45045).
        This ensures files created by the agent are owned by the session user.

        Args:
            sandbox_config: Sandbox configuration from permissions.yaml.
            workspace_dir: Absolute path to the session workspace directory.

        Returns:
            SandboxExecutor if sandbox is enabled, None otherwise.
        """
        if sandbox_config is None or not sandbox_config.enabled:
            logger.info("BWRAP SANDBOX: Disabled in config")
            return None

        if not sandbox_config.file_sandboxing:
            logger.info("BWRAP SANDBOX: File sandboxing disabled")
            return None

        # Pass linux_uid/linux_gid to executor for privilege dropping
        executor = SandboxExecutor(
            sandbox_config,
            linux_uid=self._linux_uid,
            linux_gid=self._linux_gid,
        )

        if self._linux_uid is not None:
            logger.info(f"BWRAP SANDBOX: Will drop privileges to UID={self._linux_uid}, GID={self._linux_gid}")

        # Validate mount sources exist
        missing = executor.validate_mount_sources()
        if missing:
            logger.warning(
                f"BWRAP SANDBOX: Some mount sources don't exist: {missing}. "
                "Sandbox may fail at runtime."
            )

        logger.info(
            f"BWRAP SANDBOX: Enabled with {len(sandbox_config.static_mounts)} static mounts, "
            f"{len(sandbox_config.session_mounts)} session mounts, "
            f"workspace={workspace_dir}"
        )

        return executor

    def _sandbox_system_message_builder(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> Optional[str]:
        if not self._sandbox_system_message:
            return None
        if tool_name in {
            "Bash",
            "Read",
            "Write",
            "Edit",
            "MultiEdit",
            "Glob",
            "Grep",
            "LS",
            "WebFetch",
            "WebSearch",
        }:
            return self._sandbox_system_message
        return None

    def _format_sandbox_system_message(
        self,
        sandbox_config: Optional[SandboxConfig],
        workspace_dir: Path,
    ) -> Optional[str]:
        if sandbox_config is None:
            return None

        writable_paths = sandbox_config.writable_paths or [str(workspace_dir)]
        readonly_paths = sandbox_config.readonly_paths or []
        network_mode = "enabled" if sandbox_config.network_sandboxing and sandbox_config.enabled else "disabled"
        file_mode = "enabled" if sandbox_config.file_sandboxing and sandbox_config.enabled else "disabled"

        return (
            "Sandbox policy: "
            f"file sandboxing {file_mode}, network sandboxing {network_mode}. "
            f"Writable: {', '.join(writable_paths) or 'none'}. "
            f"Read-only: {', '.join(readonly_paths) or 'none'}. "
            "Do not access paths outside the allowed list or attempt to bypass sandboxing."
        )

    def _validate_response(self, response: Optional[ResultMessage]) -> None:
        """
        Validate the agent response.

        Args:
            response: The ResultMessage from the SDK.

        Raises:
            SessionIncompleteError: If session did not complete.
            ServerError: If an API error occurred.
            MaxTurnsExceededError: If max turns was exceeded.
        """
        if response is None:
            raise SessionIncompleteError("Session did not complete")
        if response.is_error:
            # Use result field for actual error message, fall back to subtype
            error_msg = response.result or response.subtype or "Unknown error"
            raise ServerError(f"API error: {error_msg}")
        if response.subtype == "error_max_turns":
            raise MaxTurnsExceededError(
                f"Exceeded {self._config.max_turns} turns"
            )

    async def run(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        parameters: Optional[dict] = None,
        resume_session_id: Optional[str] = None,
        fork_session: bool = False,
        timeout_seconds: Optional[int] = None,
        session_id: Optional[str] = None,
        username: Optional[str] = None,
        session_context: Optional[SessionContext] = None,
        dynamic_mounts: Optional[list] = None,
        ssh_context: Optional[Any] = None,
    ) -> AgentResult:
        """
        Execute the agent with a task.

        Timeout is always enforced. Uses config.timeout_seconds (default 1800s = 30 min)
        unless overridden via timeout_seconds parameter.

        Args:
            task: The task description.
            system_prompt: Custom system prompt. If None, loads from prompts/system-prompts/.
            parameters: Additional template parameters (optional).
            resume_session_id: Session ID to resume (optional, for logging - use session_context.claude_session_id).
            fork_session: If True, fork to new session when resuming (optional).
            timeout_seconds: Override timeout (uses config.timeout_seconds if None).
            session_id: Session ID to use for new session (optional, use session_context.session_id instead).
            username: Optional username for user-specific features.
            session_context: Session context from database. If provided, contains session_id and
                            claude_session_id for resumption. Caller is responsible for persisting
                            updates from AgentResult back to database.
            dynamic_mounts: List of DynamicMountInfo objects for this session (optional).

        Returns:
            AgentResult with execution outcome.

        Raises:
            AgentError: If prompts cannot be loaded or are invalid.
        """
        # Determine effective timeout (parameter overrides config)
        effective_timeout = timeout_seconds or self._config.timeout_seconds

        # Wrap execution with timeout to ensure every run is time-bounded
        return await asyncio.wait_for(
            self._execute(
                task, system_prompt, parameters, resume_session_id, fork_session,
                session_id=session_id, username=username, session_context=session_context,
                dynamic_mounts=dynamic_mounts,
                ssh_context=ssh_context,
            ),
            timeout=effective_timeout,
        )

    async def _execute(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        parameters: Optional[dict] = None,
        resume_session_id: Optional[str] = None,
        fork_session: bool = False,
        session_id: Optional[str] = None,
        username: Optional[str] = None,
        session_context: Optional[SessionContext] = None,
        dynamic_mounts: Optional[list] = None,
        ssh_context: Optional[Any] = None,
    ) -> AgentResult:
        """
        Internal execution logic (called by run() with timeout wrapper).

        Args:
            task: The task description.
            system_prompt: Custom system prompt. If None, loads from prompts/system-prompts/.
            parameters: Additional template parameters (optional).
            resume_session_id: Session ID to resume (optional, for logging only - use session_context.claude_session_id).
            fork_session: If True, fork to new session when resuming (optional).
            session_id: Session ID to use for new session (optional, use session_context.session_id instead).
            username: Optional username for user-specific features.
            session_context: Session context from database. If not provided, a minimal one is created.
            dynamic_mounts: List of DynamicMountInfo objects for this session.

        Returns:
            AgentResult with execution outcome.

        Raises:
            AgentError: If prompts cannot be loaded or are invalid.
        """
        # Session context should be provided by caller (from database)
        # If not provided, create a minimal one (for backward compat during transition)
        if session_context is None:
            # Generate session ID if not provided
            if session_id is None:
                from .sessions import generate_session_id
                session_id = generate_session_id()
            session_context = SessionContext(
                session_id=session_id,
                working_dir=self._config.working_dir or str(AGENT_DIR),
                file_checkpointing_enabled=self._config.enable_file_checkpointing,
            )
            # Create session directory
            self._session_manager.create_session_directory(session_context.session_id)
        else:
            # Session directory was already created by session_service (backend path).
            # After chown to sandbox user, API process may lack group access (770 perms)
            # if the container hasn't restarted since user creation (Gotcha #12).
            # The agent subprocess runs as sandbox user via bwrap and has full access.
            try:
                self._session_manager.create_session_directory(session_context.session_id)
            except PermissionError:
                session_dir = self._session_manager.get_session_dir(session_context.session_id)
                if session_dir.exists():
                    logger.debug(
                        f"Session directory {session_dir} exists but API process "
                        f"lacks group access (expected after user creation without restart)"
                    )
                else:
                    raise

        # Extract resume_id from session_context for SDK resumption
        resume_id: Optional[str] = None
        if session_context.claude_session_id:
            resume_id = session_context.claude_session_id
            logger.info(
                f"Resuming session: {session_context.session_id} "
                f"(Claude session: {resume_id})"
            )

        # Set session context for session-specific permissions
        # This sandboxes the agent to only its own workspace folder
        # Inside the sandbox, the workspace is mounted at /workspace and cwd is /workspace
        # So relative paths are relative to /workspace (the session workspace directory)
        if self._permission_manager is not None:
            # Agent's perspective inside sandbox: cwd is /workspace
            workspace_path = "."
            workspace_absolute = self._session_manager.get_workspace_dir(
                session_context.session_id
            )
            self._permission_manager.set_session_context(
                session_id=session_context.session_id,
                workspace_path=workspace_path,
                workspace_absolute_path=workspace_absolute,
                username=username
            )

        # Setup skills access in workspace
        # Creates merged .claude/skills/ directory with symlinks to global and user skills
        self._setup_workspace_skills(session_context.session_id, username=username)

        # Load system prompt from template if not provided
        # Done after session creation so permissions reflect session-specific rules
        if system_prompt is None:
            # Build permission profile data for the template
            # Now includes session-specific paths after set_session_context()
            permissions_data = None
            if self._permission_manager is not None:
                active_profile = self._permission_manager.active_profile
                # Get allow/deny/allowed_dirs from permissions if available
                allow_rules: list[str] = []
                deny_rules: list[str] = []
                allowed_dirs: list[str] = []
                if active_profile.permissions is not None:
                    allow_rules = active_profile.permissions.allow
                    deny_rules = active_profile.permissions.deny
                    allowed_dirs = active_profile.permissions.allowed_dirs

                permissions_data = {
                    "name": active_profile.name,
                    "description": active_profile.description,
                    "allow": allow_rules,
                    "deny": deny_rules,
                    "enabled_tools": active_profile.tools.enabled,
                    "disabled_tools": active_profile.tools.disabled,
                    "allowed_dirs": allowed_dirs,
                }
                sandbox_config = self._permission_manager.get_sandbox_config()
                if sandbox_config is not None:
                    permissions_data["sandbox"] = {
                        "enabled": sandbox_config.enabled,
                        "file_sandboxing": sandbox_config.file_sandboxing,
                        "network_sandboxing": sandbox_config.network_sandboxing,
                        "writable_paths": sandbox_config.writable_paths,
                        "readonly_paths": sandbox_config.readonly_paths,
                        "network": {
                            "enabled": sandbox_config.network.enabled,
                            "allowed_domains": sandbox_config.network.allowed_domains,
                            "allow_localhost": sandbox_config.network.allow_localhost,
                        },
                    }

            # Get workspace directory for template
            workspace_dir = self._session_manager.get_workspace_dir(
                session_context.session_id
            )

            # Custom role can be specified via parameters["role"] to override config
            params = parameters or {}
            role_name = params.get("role", self._config.role)

            try:
                system_prompt = get_prompt_manager().build_system_prompt(
                    username=username,
                    role=role_name,
                    model=self._config.model,
                    session_id=session_context.session_id,
                    docker_workspace_path=str(workspace_dir),
                    permissions=permissions_data,
                    enable_skills=self._config.enable_skills,
                    external_mounts=self._load_external_mounts_config(username),
                    dynamic_mounts=dynamic_mounts or [],
                    original_path_mounts=self._load_original_path_mounts_for_prompt(
                        username, dynamic_mounts
                    ),
                    ssh_profiles=getattr(ssh_context, "profiles", None) if ssh_context else None,
                )
            except FileNotFoundError as e:
                raise AgentError(str(e)) from e
            except Exception as e:
                raise AgentError(f"Failed to render system prompt: {e}") from e

        # Validate system prompt is not empty
        if not system_prompt or not system_prompt.strip():
            raise AgentError("System prompt is empty after loading/rendering")

        # Build path display mapping for transforming internal paths to host paths in output
        # This makes agent output more user-friendly (e.g., "/var/log" instead of "./external/ro/global_var_log")
        path_display_mapping: dict[str, str] = {}
        try:
            from ..services.mount_service import get_path_display_mapping
            path_display_mapping = get_path_display_mapping(username)

            # Also add dynamic mount aliases if they have host paths
            if dynamic_mounts:
                for mount_info in dynamic_mounts:
                    if mount_info.host_path and mount_info.alias:
                        # Dynamic mounts appear at workspace root as {alias}
                        path_display_mapping[mount_info.alias] = mount_info.host_path

            if path_display_mapping:
                logger.debug(f"Built path display mapping with {len(path_display_mapping)} entries")
        except Exception as e:
            logger.warning(f"Failed to build path display mapping: {e}")

        # Create trace processor BEFORE options so it can be passed to
        # permission callback for correct failure status display
        trace_processor = TraceProcessor(self._tracer, path_display_mapping=path_display_mapping)
        trace_processor.set_task(task)
        trace_processor.set_model(self._config.model)

        # Apply model-specific guardrail profile
        from src.config import get_model_profile
        model_profile = get_model_profile(self._config.model)
        trace_processor.configure_guardrails(
            max_consecutive_failures=model_profile.get("max_consecutive_failures"),
            max_repetitive_calls=model_profile.get("max_repetitive_calls"),
            max_silent_turns=model_profile.get("max_silent_turns"),
            max_todowrite_only_turns=model_profile.get("max_todowrite_only_turns"),
        )

        # Set cumulative stats if resuming a session (for display during execution)
        if session_context.cumulative_turns > 0 or session_context.cumulative_cost_usd > 0:
            trace_processor.set_cumulative_stats(
                cost_usd=session_context.cumulative_cost_usd,
                turns=session_context.cumulative_turns,
                tokens=session_context.cumulative_total_tokens,
            )

        options = self._build_options(
            session_context, system_prompt, trace_processor,
            resume_id=resume_id,
            fork_session=fork_session,
            username=username,
            dynamic_mounts=dynamic_mounts,
            ssh_context=ssh_context,
        )
        user_prompt = self._build_user_prompt(task, session_context, parameters)

        log_file = self._session_manager.get_log_file(session_context.session_id)
        result: Optional[ResultMessage] = None

        # Create checkpoint tracker for file change tracking
        checkpoint_tracker = CheckpointTracker(
            session_id=session_context.session_id,
            auto_checkpoint_tools=self._config.auto_checkpoint_tools,
            enabled=session_context.file_checkpointing_enabled,
            initial_turn_count=session_context.cumulative_turns,
        )

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(user_prompt)

                with log_file.open("w", encoding="utf-8") as f:
                    async for message in client.receive_response():
                        # Write to log file
                        f.write(json.dumps(asdict(message)) + "\n")

                        # Process for console tracing
                        trace_processor.process_message(message)

                        # Check circuit breaker for repeated tool failures
                        if trace_processor.circuit_breaker_tripped:
                            logger.error(
                                "Circuit breaker tripped for session %s: %s",
                                session_context.session_id,
                                trace_processor.circuit_breaker_message,
                            )
                            self._tracer.on_error(
                                trace_processor.circuit_breaker_message,
                                error_type="circuit_breaker"
                            )
                            # Force a result if we have a partial one
                            if isinstance(message, ResultMessage):
                                result = message
                            break

                        # Track checkpoints for file-modifying tools
                        checkpoint_tracker.process_message(message)

                        if isinstance(message, ResultMessage):
                            result = message

            # Persist conversation data to project JSONL for --resume support.
            # In SDK mode, the binary doesn't write conversations to project files,
            # so we must write them ourselves for resume to find them.
            if result and getattr(result, "session_id", None):
                _session_dir = self._session_manager.get_session_dir(
                    session_context.session_id
                )
                _workspace_dir = self._session_manager.get_workspace_dir(
                    session_context.session_id
                )
                _persist_conversation_for_resume(
                    session_dir=_session_dir,
                    workspace_dir=_workspace_dir,
                    claude_session_id=result.session_id,
                    user_prompt=user_prompt,
                    result_text=result.result,
                    model=self._config.model,
                )

            # Compute adjusted turn count: exclude TodoWrite/TodoRead from metrics
            # These are planning tools that shouldn't count against the turn budget
            _todo_count = trace_processor.todo_tool_count
            def _adjusted_turns(raw_turns: int) -> int:
                return max(0, raw_turns - _todo_count)

            # Check if circuit breaker was tripped
            if trace_processor.circuit_breaker_tripped:
                error_msg = trace_processor.circuit_breaker_message
                self._cleanup_session(session_context.session_id)

                # Extract metrics even for failed runs
                usage = None
                if result:
                    usage = TokenUsage.from_sdk_usage(result.usage)

                # Finalize any orphaned subagents before emitting completion
                trace_processor.finalize_orphaned_subagents()

                # Emit completion so the UI can close the stream deterministically
                self._tracer.on_agent_complete(
                    status="FAILED",
                    num_turns=_adjusted_turns(result.num_turns) if result else 0,
                    duration_ms=result.duration_ms if result else 0,
                    total_cost_usd=result.total_cost_usd if result else None,
                    result=error_msg,
                    session_id=getattr(result, "session_id", None) if result else None,
                    usage=getattr(result, "usage", None) if result else None,
                    model=self._config.model,
                    cumulative_cost_usd=session_context.cumulative_cost_usd,
                    cumulative_turns=session_context.cumulative_turns,
                    cumulative_tokens=session_context.cumulative_total_tokens,
                )

                return AgentResult(
                    status=TaskStatus.FAILED,
                    error=error_msg,
                    metrics=LLMMetrics(
                        model=self._config.model,
                        duration_ms=result.duration_ms if result else 0,
                        num_turns=_adjusted_turns(result.num_turns) if result else 0,
                        session_id=result.session_id if result else None,
                        total_cost_usd=result.total_cost_usd if result else None,
                        usage=usage,
                    ) if result else None,
                    session_id=session_context.session_id,
                )

            self._validate_response(result)

            # Check if agent was interrupted due to permission denial
            # This happens when interrupt=True is returned from permission callback
            if self._denial_tracker.was_interrupted:
                denial = self._denial_tracker.last_denial
                error_msg = denial.message if denial else "Permission denied"
                self._tracer.on_error(error_msg, error_type="permission_denied")
                self._cleanup_session(session_context.session_id)

                # Extract metrics even for failed runs
                usage = None
                if result:
                    usage = TokenUsage.from_sdk_usage(result.usage)
                    # Note: Session update is now handled by caller via AgentResult.metrics

                # Finalize any orphaned subagents before emitting completion
                trace_processor.finalize_orphaned_subagents()

                # Emit completion so the UI can close the stream deterministically.
                if result:
                    self._tracer.on_agent_complete(
                        status="FAILED",
                        num_turns=_adjusted_turns(result.num_turns),
                        duration_ms=result.duration_ms,
                        total_cost_usd=result.total_cost_usd,
                        result=result.result,
                        session_id=getattr(result, "session_id", None),
                        usage=getattr(result, "usage", None),
                        model=self._config.model,
                        cumulative_cost_usd=session_context.cumulative_cost_usd,
                        cumulative_turns=session_context.cumulative_turns,
                        cumulative_tokens=session_context.cumulative_total_tokens,
                    )

                return AgentResult(
                    status=TaskStatus.FAILED,
                    error=error_msg,
                    metrics=LLMMetrics(
                        model=self._config.model,
                        duration_ms=result.duration_ms if result else 0,
                        num_turns=_adjusted_turns(result.num_turns) if result else 0,
                        session_id=result.session_id if result else None,
                        total_cost_usd=result.total_cost_usd if result else None,
                        usage=usage,
                    ) if result else None,
                    session_id=session_context.session_id,
                )

            # Normal successful completion
            # Clean up session (remove skills, switch to system profile)
            self._cleanup_session(session_context.session_id)

            # Extract token usage from result (for metrics in AgentResult)
            usage = None
            if result:
                usage = TokenUsage.from_sdk_usage(result.usage)
                # Note: Session update is now handled by caller via AgentResult.metrics

            # Determine session status using priority chain:
            # agent self-assessment > fallback heuristic
            raw_status = determine_session_status(
                result_text=result.result if result else None,
                had_tool_errors=trace_processor.had_tool_errors(),
                tool_error_count=trace_processor.tool_error_count,
            )

            # Finalize any orphaned subagents before emitting completion
            trace_processor.finalize_orphaned_subagents()

            # Emit completion so the UI can close the stream cleanly.
            if result:
                self._tracer.on_agent_complete(
                    status=raw_status,
                    num_turns=_adjusted_turns(result.num_turns),
                    duration_ms=result.duration_ms,
                    total_cost_usd=result.total_cost_usd,
                    result=result.result,
                    session_id=getattr(result, "session_id", None),
                    usage=getattr(result, "usage", None),
                    model=self._config.model,
                    cumulative_cost_usd=session_context.cumulative_cost_usd,
                    cumulative_turns=session_context.cumulative_turns,
                    cumulative_tokens=session_context.cumulative_total_tokens,
                )

            return AgentResult(
                status=TaskStatus(raw_status),
                output=result.result if result else None,
                metrics=LLMMetrics(
                    model=self._config.model,
                    duration_ms=result.duration_ms,
                    num_turns=_adjusted_turns(result.num_turns),
                    session_id=result.session_id,
                    total_cost_usd=result.total_cost_usd,
                    usage=usage,
                ) if result else None,
                session_id=session_context.session_id,
            )

        except AgentError as e:
            self._tracer.on_error(str(e), error_type="agent_error")
            self._cleanup_session(session_context.session_id)
            # Note: Session status update is now handled by caller
            raise
        except asyncio.TimeoutError:
            error_msg = f"Timed out after {self._config.timeout_seconds}s"
            self._tracer.on_error(error_msg, error_type="timeout")
            self._cleanup_session(session_context.session_id)
            # Note: Session status update is now handled by caller
            raise AgentError(error_msg)
        except Exception as e:
            self._tracer.on_error(str(e), error_type="error")
            self._cleanup_session(session_context.session_id)
            # Note: Session status update is now handled by caller
            return AgentResult(
                status=TaskStatus.ERROR,
                error=str(e),
                session_id=session_context.session_id,
            )

    async def run_with_timeout(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        parameters: Optional[dict] = None,
        resume_session_id: Optional[str] = None,
        fork_session: bool = False,
        timeout_seconds: Optional[int] = None,
        session_id: Optional[str] = None
    ) -> AgentResult:
        """
        Execute agent with timeout (alias for run(), kept for backward compatibility).

        All runs now enforce timeout by default (30 minutes).

        Args:
            task: The task description.
            system_prompt: Custom system prompt (optional).
            parameters: Additional template parameters (optional).
            resume_session_id: Session ID to resume (optional).
            fork_session: If True, fork to new session when resuming (optional).
            timeout_seconds: Override timeout (uses config.timeout_seconds if None).
            session_id: Session ID to use for new session (optional).

        Returns:
            AgentResult with execution outcome.
        """
        return await self.run(
            task, system_prompt, parameters, resume_session_id, fork_session,
            timeout_seconds=timeout_seconds, session_id=session_id
        )

    async def compact(
        self,
        session_id: str,
        claude_session_id: str
    ) -> dict[str, Any]:
        """
        Compact conversation history for a session.

        Reduces context size by summarizing older messages while
        preserving important context. Uses the SDK's /compact command.

        Args:
            session_id: The Ag3ntum session ID (for logging).
            claude_session_id: The Claude SDK session ID for resumption.

        Returns:
            Dict with compaction metadata:
            - pre_tokens: Token count before compaction
            - post_tokens: Token count after compaction (if available)
            - trigger: What triggered the compaction

        Raises:
            AgentError: If claude_session_id is not provided.
        """
        if not claude_session_id:
            raise AgentError(
                f"Session {session_id} has no Claude session ID to resume"
            )

        compact_metadata: dict[str, Any] = {}

        async with ClaudeSDKClient(
            options=ClaudeAgentOptions(
                resume=claude_session_id,
                max_turns=1
            )
        ) as client:
            await client.query("/compact")

            async for message in client.receive_response():
                if isinstance(message, SystemMessage):
                    if message.subtype == "compact_boundary":
                        compact_metadata = message.data.get("compact_metadata", {})

        logger.info(
            f"Compacted session {session_id}: "
            f"pre_tokens={compact_metadata.get('pre_tokens')}"
        )

        return compact_metadata

    # -------------------------------------------------------------------------
    # Checkpoint Management
    #
    # NOTE: Checkpoint data is now stored in the database (Session.checkpoints_json).
    # Callers should use session_service to manage checkpoints.
    # These methods are provided for convenience and work with passed-in data.
    # -------------------------------------------------------------------------

    def create_checkpoint(
        self,
        session_id: str,
        uuid: str,
        turn_number: int,
        description: Optional[str] = None
    ) -> Checkpoint:
        """
        Create a manual checkpoint object.

        This creates a Checkpoint object that the caller should persist to the database.

        Args:
            session_id: The session ID.
            uuid: The user message UUID from the SDK.
            turn_number: Current cumulative turn number.
            description: Optional description of the checkpoint.

        Returns:
            The created Checkpoint object. Caller must persist to database.
        """
        from datetime import datetime
        checkpoint = Checkpoint(
            uuid=uuid,
            created_at=datetime.now(),
            checkpoint_type=CheckpointType.MANUAL,
            description=description,
            turn_number=turn_number,
        )
        logger.debug(f"Created manual checkpoint: {checkpoint.to_summary()}")
        return checkpoint

    async def rewind_to_checkpoint(
        self,
        session_id: str,
        claude_session_id: str,
        checkpoint: Checkpoint,
        file_checkpointing_enabled: bool = True
    ) -> dict[str, Any]:
        """
        Rewind files to a specific checkpoint.

        This restores all files to their state at the specified checkpoint,
        reverting any changes made after that point.

        Args:
            session_id: The Ag3ntum session ID (for logging).
            claude_session_id: The Claude SDK session ID for resumption.
            checkpoint: The Checkpoint object to rewind to.
            file_checkpointing_enabled: Whether file checkpointing is enabled.

        Returns:
            Dict with rewind metadata:
            - checkpoint: The checkpoint that was rewound to
            - success: Whether the rewind succeeded

        Raises:
            AgentError: If session data is invalid or checkpointing not enabled.

        Note:
            The caller is responsible for clearing checkpoints after this one
            from the database using session_service.clear_checkpoints_after().
        """
        # Validate file checkpointing is enabled
        if not file_checkpointing_enabled:
            raise AgentError(
                f"File checkpointing is not enabled for session {session_id}. "
                "Set enable_file_checkpointing=True in session config."
            )

        # Validate session has a resume ID
        if not claude_session_id:
            raise AgentError(
                f"Session {session_id} has no Claude session ID to resume"
            )

        # Use SDK to rewind files
        async with ClaudeSDKClient(
            options=ClaudeAgentOptions(
                resume=claude_session_id,
                max_turns=1,
                enable_file_checkpointing=True,
            )
        ) as client:
            await client.rewind_files(checkpoint.uuid)

        logger.info(
            f"Rewound session {session_id} to checkpoint {checkpoint.uuid}"
        )

        # Notify tracer if available
        if hasattr(self._tracer, 'on_checkpoint_rewind'):
            self._tracer.on_checkpoint_rewind(checkpoint, 0)

        return {
            "checkpoint": checkpoint,
            "success": True,
        }

    async def rewind_to_latest_checkpoint(
        self,
        session_id: str,
        claude_session_id: str,
        checkpoints: list[Checkpoint],
        file_checkpointing_enabled: bool = True
    ) -> dict[str, Any]:
        """
        Rewind to the most recent checkpoint.

        Convenience method for undoing the last file-modifying operation.

        Args:
            session_id: The Ag3ntum session ID.
            claude_session_id: The Claude SDK session ID for resumption.
            checkpoints: List of checkpoints from database (Session.checkpoints_json).
            file_checkpointing_enabled: Whether file checkpointing is enabled.

        Returns:
            Dict with rewind metadata (same as rewind_to_checkpoint).

        Raises:
            AgentError: If no checkpoints exist or rewind fails.
        """
        if len(checkpoints) < 2:
            raise AgentError(
                f"Session {session_id} needs at least 2 checkpoints to rewind"
            )

        # Rewind to the checkpoint before the last one
        return await self.rewind_to_checkpoint(
            session_id=session_id,
            claude_session_id=claude_session_id,
            checkpoint=checkpoints[-2],
            file_checkpointing_enabled=file_checkpointing_enabled
        )

    @staticmethod
    def get_checkpoint_summary(checkpoints: list[Checkpoint]) -> list[str]:
        """
        Get a human-readable summary of checkpoints.

        Args:
            checkpoints: List of Checkpoint objects.

        Returns:
            List of checkpoint summary strings.
        """
        return [
            f"[{i}] {cp.to_summary()}"
            for i, cp in enumerate(checkpoints)
        ]


def _persist_conversation_for_resume(
    session_dir: Path,
    workspace_dir: Path,
    claude_session_id: str,
    user_prompt: str,
    result_text: Optional[str],
    model: str,
) -> None:
    """
    Write conversation data to the Claude Code project JSONL file.

    In SDK mode (--output-format stream-json), the Claude Code binary does NOT
    persist conversation messages to its project JSONL files — only metadata
    (queue-operation, file-history-snapshot). When --resume is used for a
    subsequent request, the binary searches these files for conversation data
    and fails with "No conversation found" if none exists.

    This function writes the user prompt and assistant response to the project
    file in the format the binary expects, enabling successful resume.

    Args:
        session_dir: The session directory (CLAUDE_CONFIG_DIR).
        workspace_dir: The workspace directory (cwd for the agent).
        claude_session_id: The Claude session ID from the SDK.
        user_prompt: The user's prompt text.
        result_text: The assistant's response text (may be None on error).
        model: The model name used.
    """
    try:
        # Compute project slug from workspace path (same algorithm as Claude Code binary)
        # Binary replaces both "/" and "_" with "-" and keeps leading "-"
        # e.g., /users/greg/sessions/20260212_213024_c37241ab/workspace
        #     → -users-greg-sessions-20260212-213024-c37241ab-workspace
        slug = str(workspace_dir).replace("/", "-").replace("_", "-")
        project_dir = session_dir / "projects" / slug
        project_dir.mkdir(parents=True, exist_ok=True)

        project_file = project_dir / f"{claude_session_id}.jsonl"

        from datetime import timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        user_uuid = str(uuid_mod.uuid4())
        assistant_uuid = str(uuid_mod.uuid4())
        cwd = str(workspace_dir)

        # Find parentUuid for the user message by reading the last assistant entry
        parent_uuid = None
        if project_file.exists():
            try:
                with project_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if entry.get("type") == "assistant" and entry.get("uuid"):
                                parent_uuid = entry["uuid"]
                        except json.JSONDecodeError:
                            continue
            except Exception:
                parent_uuid = None

        entries = []

        # User message
        entries.append({
            "parentUuid": parent_uuid,
            "isSidechain": False,
            "userType": "external",
            "cwd": cwd,
            "sessionId": claude_session_id,
            "type": "user",
            "message": {"role": "user", "content": user_prompt},
            "uuid": user_uuid,
            "timestamp": now,
            "permissionMode": "default",
        })

        # Assistant message
        content_blocks = []
        if result_text:
            content_blocks.append({"type": "text", "text": result_text})

        entries.append({
            "parentUuid": user_uuid,
            "isSidechain": False,
            "userType": "external",
            "cwd": cwd,
            "sessionId": claude_session_id,
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": content_blocks,
                "model": model,
                "stop_reason": "end_turn",
                "stop_sequence": None,
            },
            "uuid": assistant_uuid,
            "timestamp": now,
        })

        # Append to project file (preserves binary's queue-operation entries)
        with project_file.open("a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        logger.debug(
            "Persisted conversation for resume: session=%s, file=%s, entries=%d",
            claude_session_id, project_file, len(entries),
        )
    except Exception as e:
        # Non-fatal: resume may fail but current request succeeded
        logger.warning(
            "Failed to persist conversation for resume (session=%s): %s",
            claude_session_id, e,
        )


async def run_agent(
    task: str,
    config: AgentConfig,
    permission_manager: PermissionManager,
    system_prompt: Optional[str] = None,
    parameters: Optional[dict] = None,
    resume_session_id: Optional[str] = None,
    fork_session: bool = False,
    tracer: Optional[Union[TracerBase, bool]] = True
) -> AgentResult:
    """
    Convenience function to run the agent.

    Args:
        task: The task description.
        config: AgentConfig loaded from agent.yaml (required).
        permission_manager: PermissionManager (required).
        system_prompt: Custom system prompt.
        parameters: Additional template parameters.
        resume_session_id: Session ID to resume.
        fork_session: If True, fork to new session when resuming.
        tracer: Execution tracer for console output.
            - True (default): Use ExecutionTracer with default settings.
            - False/None: Disable tracing (NullTracer).
            - TracerBase instance: Use custom tracer.

    Returns:
        AgentResult with execution outcome.

    Raises:
        AgentError: If permission manager is not provided or prompts are missing.

    Example:
        from config import AgentConfigLoader
        from schemas import AgentConfig

        loader = AgentConfigLoader()
        yaml_config = loader.get_config()
        config = AgentConfig(**yaml_config, working_dir="/path/to/project")

        result = await run_agent(
            task="List all files",
            config=config,
            permission_manager=manager
        )
    """
    agent = ClaudeAgent(
        config,
        tracer=tracer,
        permission_manager=permission_manager
    )
    return await agent.run_with_timeout(
        task, system_prompt, parameters, resume_session_id, fork_session
    )
