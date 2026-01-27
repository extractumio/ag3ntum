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

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape

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
from .sandbox import SandboxConfig, SandboxExecutor
from .subagent_manager import get_subagent_manager

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
)

logger = logging.getLogger(__name__)


class CheckpointTracker:
    """
    Tracks checkpoints during agent execution.

    Captures UUIDs from tool result messages and creates checkpoints
    for file-modifying tools (Write, Edit). Checkpoints are collected
    in memory and can be retrieved for database persistence.
    """

    def __init__(
        self,
        session_id: str,
        auto_checkpoint_tools: list[str],
        enabled: bool = True,
        initial_turn_count: int = 0
    ) -> None:
        """
        Initialize the checkpoint tracker.

        Args:
            session_id: The session ID.
            auto_checkpoint_tools: Tools that trigger auto-checkpoints.
            enabled: Whether checkpoint tracking is enabled.
            initial_turn_count: Starting turn number (cumulative from previous runs).
        """
        self._session_id = session_id
        self._auto_checkpoint_tools = auto_checkpoint_tools
        self._enabled = enabled
        self._pending_tool_calls: dict[str, dict[str, Any]] = {}
        self._turn_counter = 0
        self._initial_turn_count = initial_turn_count
        self._checkpoints: list[Checkpoint] = []

    @property
    def checkpoints(self) -> list[Checkpoint]:
        """Get all checkpoints created during this execution."""
        return self._checkpoints.copy()

    def track_tool_use(self, tool_use_id: str, tool_name: str, tool_input: dict) -> None:
        """
        Track a tool use request for later checkpoint creation.

        Args:
            tool_use_id: The tool use ID from the SDK.
            tool_name: Name of the tool being used.
            tool_input: The tool input parameters.
        """
        if not self._enabled:
            return

        self._pending_tool_calls[tool_use_id] = {
            "tool_name": tool_name,
            "file_path": tool_input.get("file_path"),
        }

    def process_tool_result(self, tool_use_id: str, uuid: Optional[str]) -> Optional[Checkpoint]:
        """
        Process a tool result and create a checkpoint if applicable.

        Args:
            tool_use_id: The tool use ID from the original request.
            uuid: The UUID from the tool result message.

        Returns:
            Created Checkpoint if one was created, None otherwise.
        """
        if not self._enabled or not uuid:
            return None

        tool_info = self._pending_tool_calls.pop(tool_use_id, None)
        if not tool_info:
            return None

        tool_name = tool_info.get("tool_name")
        if tool_name not in self._auto_checkpoint_tools:
            return None

        # Create checkpoint for file-modifying tool
        self._turn_counter += 1
        from datetime import datetime
        checkpoint = Checkpoint(
            uuid=uuid,
            created_at=datetime.now(),
            checkpoint_type=CheckpointType.AUTO,
            turn_number=self._initial_turn_count + self._turn_counter,
            tool_name=tool_name,
            file_path=tool_info.get("file_path"),
        )
        self._checkpoints.append(checkpoint)
        logger.debug(f"Created auto checkpoint: {checkpoint.to_summary()}")
        return checkpoint

    def process_message(self, message: Any) -> Optional[Checkpoint]:
        """
        Process a message and create checkpoints as needed.

        This method extracts tool use and tool result information from
        SDK messages and creates checkpoints for file-modifying tools.

        Args:
            message: SDK message to process.

        Returns:
            Created Checkpoint if one was created, None otherwise.
        """
        if not self._enabled:
            return None

        # Check for AssistantMessage with tool use blocks
        if hasattr(message, 'content') and isinstance(message.content, list):
            for block in message.content:
                # Tool use block - track for later
                if hasattr(block, 'name') and hasattr(block, 'id'):
                    tool_input = getattr(block, 'input', {}) or {}
                    self.track_tool_use(block.id, block.name, tool_input)

                # Tool result block - create checkpoint
                if hasattr(block, 'tool_use_id') and hasattr(message, 'uuid'):
                    uuid = getattr(message, 'uuid', None)
                    if uuid:
                        return self.process_tool_result(block.tool_use_id, uuid)

        return None


# Jinja environment for templates
# Note: select_autoescape only enables autoescape for HTML/XML extensions,
# which is appropriate for our text-based prompt templates (.j2, .md)
_jinja_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=select_autoescape(),
)


def _filter_startswith(items: list[str], prefix: str) -> list[str]:
    """Jinja2 filter to select strings starting with a prefix."""
    return [item for item in items if item.startswith(prefix)]


def _filter_contains(items: list[str], value: str) -> bool:
    """Jinja2 filter to check if a list contains a value."""
    return value in items


# Register custom filters
_jinja_env.filters["select_startswith"] = _filter_startswith
_jinja_env.filters["contains"] = _filter_contains


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

        Returns a dict suitable for the mounts.j2 template with structure:
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
        mounts_file = Path("/data/auto-generated/auto-generated-mounts.yaml")
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
        username: Optional[str] = None
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

        # Inject per-user mounts from external-mounts.yaml config
        # These are user-specific mounts that are configured at Docker level
        # via docker-compose.override.yml (generated by run.sh)
        # The mounts appear at /mounts/user-ro/{name} and /mounts/user-rw/{name} in Docker
        if sandbox_config and sandbox_config.enabled and username:
            from ..services.mount_service import get_user_mounts
            from .sandbox import SandboxMount
            added_count = 0

            try:
                user_mounts = get_user_mounts(username)

                # Per-user RO mounts
                # IMPORTANT: Mount to SAME PATH as source so workspace symlinks resolve correctly!
                # Symlinks: ./external/user-ro/{name} -> /mounts/user-ro/{name}
                # Bwrap must mount /mounts/user-ro/{name} -> /mounts/user-ro/{name}
                for mount_info in user_mounts.get("ro", []):
                    name = mount_info["name"]
                    docker_path = f"/mounts/user-ro/{name}"
                    # Only add if the Docker mount exists
                    if Path(docker_path).exists():
                        sandbox_config.dynamic_mounts.append(SandboxMount(
                            source=docker_path,
                            target=docker_path,  # Same path so symlinks work!
                            mode="ro",
                            optional=mount_info.get("optional", True),
                        ))
                        added_count += 1

                # Per-user RW mounts
                # IMPORTANT: Mount to SAME PATH as source so workspace symlinks resolve correctly!
                # Symlinks: ./external/user-rw/{name} -> /mounts/user-rw/{name}
                # Bwrap must mount /mounts/user-rw/{name} -> /mounts/user-rw/{name}
                for mount_info in user_mounts.get("rw", []):
                    name = mount_info["name"]
                    docker_path = f"/mounts/user-rw/{name}"
                    # Only add if the Docker mount exists
                    if Path(docker_path).exists():
                        sandbox_config.dynamic_mounts.append(SandboxMount(
                            source=docker_path,
                            target=docker_path,  # Same path so symlinks work!
                            mode="rw",
                            optional=mount_info.get("optional", True),
                        ))
                        added_count += 1

                if added_count > 0:
                    logger.info(f"SANDBOX: Added {added_count} per-user dynamic mounts")

            except Exception as e:
                logger.warning(f"Failed to load per-user mounts for '{username}': {e}")

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
            # Agent sees: /workspace/external/ro/* -> Real path: /mounts/ro/*
            # Agent sees: /workspace/external/rw/* -> Real path: /mounts/rw/*
            # Agent sees: /workspace/external/persistent/* -> Real path: /users/{username}/ag3ntum/persistent/*
            external_ro_base = Path("/mounts/ro")
            external_rw_base = Path("/mounts/rw")
            persistent_path = Path(f"/users/{username}/ag3ntum/persistent") if username else None

            # Load per-user mounts for PathValidator
            # These are configured via external-mounts.yaml per_user section
            # Mounts appear at /mounts/user-ro/{name} and /mounts/user-rw/{name} in Docker
            user_mounts_ro_paths: dict[str, Path] = {}
            user_mounts_rw_paths: dict[str, Path] = {}

            if username:
                from ..services.mount_service import get_user_mounts
                try:
                    user_mounts_data = get_user_mounts(username)
                    for mount_info in user_mounts_data.get("ro", []):
                        name = mount_info["name"]
                        mount_path = Path(f"/mounts/user-ro/{name}")
                        if mount_path.exists() or mount_info.get("optional", True):
                            user_mounts_ro_paths[name] = mount_path
                    for mount_info in user_mounts_data.get("rw", []):
                        name = mount_info["name"]
                        mount_path = Path(f"/mounts/user-rw/{name}")
                        if mount_path.exists() or mount_info.get("optional", True):
                            user_mounts_rw_paths[name] = mount_path
                except Exception as e:
                    logger.warning(f"Failed to load per-user mounts for PathValidator: {e}")

            configure_path_validator(
                session_id=session_context.session_id,
                workspace_path=workspace_dir,
                username=username,  # Pass username to configure SandboxPathResolver
                skills_path=self._skills_dir if self._config.enable_skills else None,
                global_skills_path=global_skills,
                user_skills_path=user_skills,
                external_ro_base=external_ro_base if external_ro_base.exists() else None,
                external_rw_base=external_rw_base if external_rw_base.exists() else None,
                persistent_path=persistent_path if persistent_path and persistent_path.exists() else None,
                user_mounts_ro=user_mounts_ro_paths if user_mounts_ro_paths else None,
                user_mounts_rw=user_mounts_rw_paths if user_mounts_rw_paths else None,
            )
            logger.info(
                f"PathValidator configured for session {session_context.session_id}, "
                f"workspace={workspace_dir}, global_skills={global_skills}, user_skills={user_skills}, "
                f"external_ro={external_ro_base if external_ro_base.exists() else None}, "
                f"external_rw={external_rw_base if external_rw_base.exists() else None}, "
                f"persistent={persistent_path if persistent_path and persistent_path.exists() else None}, "
                f"user_mounts_ro={len(user_mounts_ro_paths)}, user_mounts_rw={len(user_mounts_rw_paths)}"
            )
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
        user_template_path = PROMPTS_DIR / "user.j2"
        if not user_template_path.exists():
            raise AgentError(
                f"User prompt template not found: {user_template_path}\n"
                f"Create the template file in AGENT/prompts/user.j2"
            )

        params = parameters or {}
        workspace_dir = self._session_manager.get_workspace_dir(
            session_context.session_id
        )
        try:
            user_prompt = _jinja_env.get_template("user.j2").render(
                task=task,
                working_dir=self._config.working_dir or str(workspace_dir),
                **params,
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
            raise ServerError(f"API error: {response.subtype}")
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
        session_context: Optional[SessionContext] = None
    ) -> AgentResult:
        """
        Execute the agent with a task.

        Timeout is always enforced. Uses config.timeout_seconds (default 1800s = 30 min)
        unless overridden via timeout_seconds parameter.

        Args:
            task: The task description.
            system_prompt: Custom system prompt. If None, loads from prompts/system.j2.
            parameters: Additional template parameters (optional).
            resume_session_id: Session ID to resume (optional, for logging - use session_context.claude_session_id).
            fork_session: If True, fork to new session when resuming (optional).
            timeout_seconds: Override timeout (uses config.timeout_seconds if None).
            session_id: Session ID to use for new session (optional, use session_context.session_id instead).
            username: Optional username for user-specific features.
            session_context: Session context from database. If provided, contains session_id and
                            claude_session_id for resumption. Caller is responsible for persisting
                            updates from AgentResult back to database.

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
                session_id=session_id, username=username, session_context=session_context
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
        session_context: Optional[SessionContext] = None
    ) -> AgentResult:
        """
        Internal execution logic (called by run() with timeout wrapper).

        Args:
            task: The task description.
            system_prompt: Custom system prompt. If None, loads from prompts/system.j2.
            parameters: Additional template parameters (optional).
            resume_session_id: Session ID to resume (optional, for logging only - use session_context.claude_session_id).
            fork_session: If True, fork to new session when resuming (optional).
            session_id: Session ID to use for new session (optional, use session_context.session_id instead).
            username: Optional username for user-specific features.
            session_context: Session context from database. If not provided, a minimal one is created.

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
            # Ensure session directory exists
            self._session_manager.create_session_directory(session_context.session_id)

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
            system_template_path = PROMPTS_DIR / "system.j2"
            if not system_template_path.exists():
                raise AgentError(
                    f"System prompt template not found: {system_template_path}\n"
                    f"Create the template file in AGENT/prompts/system.j2"
                )

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

            # Load role content from role template file (fail-fast if missing)
            # Custom role can be specified via parameters["role"] to override config
            params = parameters or {}
            role_name = params.get("role", self._config.role)
            role_file = PROMPTS_DIR / "roles" / f"{role_name}.md"
            if not role_file.exists():
                raise AgentError(
                    f"Role file not found: {role_file}\n"
                    f"Create the role file in AGENT/prompts/roles/{role_name}.md"
                )
            try:
                role_content = role_file.read_text(encoding="utf-8").strip()
            except IOError as e:
                raise AgentError(f"Failed to read role file {role_file}: {e}") from e

            # Build template context with all dynamic values
            template_context = {
                # Environment info
                "current_date": datetime.now().strftime("%A, %B %d, %Y"),
                "model": self._config.model,
                "session_id": session_context.session_id,
                "workspace_path": str(workspace_dir),
                "working_dir": self._config.working_dir or str(workspace_dir),
                # Role
                "role_content": role_content,
                # Permissions
                "permissions": permissions_data,
                # Skills (SDK handles discovery via setting_sources)
                "enable_skills": self._config.enable_skills,
                # External mounts configuration
                "external_mounts": self._load_external_mounts_config(username),
            }

            try:
                system_prompt = _jinja_env.get_template("system.j2").render(
                    **template_context
                )
            except Exception as e:
                raise AgentError(f"Failed to render system prompt template: {e}") from e

        # Validate system prompt is not empty
        if not system_prompt or not system_prompt.strip():
            raise AgentError("System prompt is empty after loading/rendering")

        # Create trace processor BEFORE options so it can be passed to
        # permission callback for correct failure status display
        trace_processor = TraceProcessor(self._tracer)
        trace_processor.set_task(task)
        trace_processor.set_model(self._config.model)

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
            username=username
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

                        # Track checkpoints for file-modifying tools
                        checkpoint_tracker.process_message(message)

                        if isinstance(message, ResultMessage):
                            result = message

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
                        num_turns=result.num_turns,
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
                        num_turns=result.num_turns if result else 0,
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

            # Determine status based on tool errors during execution
            # If any tool returned is_error=True, mark the session as FAILED
            # (even though the agent completed, tool failures mean the task wasn't fully accomplished)
            if trace_processor.had_tool_errors():
                raw_status = "FAILED"
            else:
                raw_status = "COMPLETE"

            # Finalize any orphaned subagents before emitting completion
            trace_processor.finalize_orphaned_subagents()

            # Emit completion so the UI can close the stream cleanly.
            if result:
                self._tracer.on_agent_complete(
                    status=raw_status,
                    num_turns=result.num_turns,
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
                    num_turns=result.num_turns,
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
