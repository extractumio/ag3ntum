"""
Permission Profile Manager for Ag3ntum.

Implements a single permission profile loaded once at startup and used
throughout the agent execution. No profile switching between system and user.

Usage:
    from permission_profiles import PermissionManager

    # Load profile from default location
    manager = PermissionManager()

    # Set session context for workspace sandboxing
    manager.set_session_context(session_id, workspace_path)

    # Check permissions
    if manager.is_allowed("Read(/path/to/file)"):
        ...
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from ..config import (
    AGENT_DIR,
    CONFIG_DIR,
    DATA_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
    SKILLS_DIR,
)
from .tool_utils import extract_patterns_for_tool
from .permission_config import (
    PermissionConfig,
    PermissionConfigManager,
    PermissionMode,
    PermissionRules,
    ToolsConfig,
)
from .sandbox import SandboxConfig

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .tracer import TracerBase

logger = logging.getLogger(__name__)


class ExtendedToolsConfig(BaseModel):
    """
    Extended tool configuration including permission checking settings.
    """
    enabled: list[str] = Field(
        default_factory=list,
        description="List of enabled tools"
    )
    disabled: list[str] = Field(
        default_factory=list,
        description="List of disabled tools"
    )
    permission_checked: list[str] = Field(
        default_factory=list,
        description="""
Tools that require permission callback checks.
These tools are available but each use is validated against permission rules."""
    )

    @field_validator("enabled", "disabled", "permission_checked", mode="before")
    @classmethod
    def convert_none_to_list(cls, v: Any) -> list[str]:
        """Convert None (from empty YAML values) to empty list."""
        if v is None:
            return []
        return v


class ExtendedPermissionRules(BaseModel):
    """
    Extended permission rules including allowed directories.
    """
    allow: list[str] = Field(
        default_factory=list,
        description="List of allowed tool patterns"
    )
    deny: list[str] = Field(
        default_factory=list,
        description="List of denied tool patterns"
    )
    ask: list[str] = Field(
        default_factory=list,
        description="List of patterns requiring confirmation"
    )
    allowed_dirs: list[str] = Field(
        default_factory=list,
        description="Directories accessible in this profile"
    )

    @field_validator("allow", "deny", "ask", "allowed_dirs", mode="before")
    @classmethod
    def convert_none_to_list(cls, v: Any) -> list[str]:
        """Convert None (from empty YAML values) to empty list."""
        if v is None:
            return []
        return v


class SessionWorkspaceConfig(BaseModel):
    """
    Session-specific workspace permissions.

    Defines patterns for dynamically generating session-specific
    permission rules. Use {workspace} placeholder for the workspace path.
    """
    description: str = Field(
        default="",
        description="Description of the session workspace configuration"
    )
    allow: list[str] = Field(
        default_factory=list,
        description="Allow patterns. Supports {workspace} placeholder."
    )
    deny: list[str] = Field(
        default_factory=list,
        description="Deny patterns."
    )
    allowed_dirs: list[str] = Field(
        default_factory=list,
        description="Allowed directories. Supports {workspace} placeholder."
    )

    @field_validator("allow", "deny", "allowed_dirs", mode="before")
    @classmethod
    def convert_none_to_list(cls, v: Any) -> list[str]:
        """Convert None (from empty YAML values) to empty list."""
        if v is None:
            return []
        return v


class CheckpointingConfig(BaseModel):
    """
    File checkpointing configuration.

    Defines which tools trigger automatic checkpoint creation
    for file change tracking and rewind functionality.
    """
    auto_checkpoint_tools: list[str] = Field(
        default_factory=lambda: ["Write", "Edit"],
        description="Tools that trigger automatic checkpoint creation after execution"
    )

    @field_validator("auto_checkpoint_tools", mode="before")
    @classmethod
    def convert_none_to_list(cls, v: Any) -> list[str]:
        """Convert None (from empty YAML values) to empty list."""
        if v is None:
            return ["Write", "Edit"]
        return v


class PermissionProfile(BaseModel):
    """
    A complete permission profile with all settings.

    Uses 'session_workspace' for dynamic session-specific rules.
    """
    name: str = Field(
        description="Profile name (e.g., 'user')"
    )
    description: str = Field(
        default="",
        description="Human-readable description of the profile"
    )
    defaultMode: PermissionMode = Field(
        default=PermissionMode.DEFAULT,
        description="Permission mode for this profile"
    )
    tools: ExtendedToolsConfig = Field(
        default_factory=ExtendedToolsConfig,
        description="Tool configuration including enabled/disabled/permission_checked"
    )
    permissions: Optional[ExtendedPermissionRules] = Field(
        default=None,
        description="Static permission rules"
    )
    session_workspace: Optional[SessionWorkspaceConfig] = Field(
        default=None,
        description="Dynamic session permissions"
    )
    checkpointing: Optional[CheckpointingConfig] = Field(
        default=None,
        description="File checkpointing configuration"
    )
    sandbox: Optional[SandboxConfig] = Field(
        default=None,
        description="Sandbox configuration for tool execution"
    )


class ProfileNotFoundError(Exception):
    """Raised when a required permission profile file is not found."""
    pass


class PermissionManager:
    """
    Manages permission profile for agent operations.

    A single profile is loaded once at startup and remains active
    throughout the execution.

    Usage:
        manager = PermissionManager()

        # Set session context for sandboxing
        manager.set_session_context(session_id, workspace_path)

        # Check permissions
        if manager.is_allowed("Read(/path/to/file)"):
            ...
    """

    # Default filename (YAML preferred)
    PROFILE_BASE = "permissions"
    SUPPORTED_EXTENSIONS = [".yaml", ".yml", ".json"]

    def __init__(
        self,
        profile_path: Optional[Path] = None
    ) -> None:
        """
        Initialize the permission manager.

        Args:
            profile_path: Path to permission profile file.
                         Defaults to AGENT/config/permissions.yaml
                         (falls back to .json if .yaml not found).
        """
        self._profile_path = (
            profile_path or self._find_profile_file(self.PROFILE_BASE)
        )

        self._profile: Optional[PermissionProfile] = None
        self._profile_base: Optional[PermissionProfile] = None  # Template
        self._active_profile: Optional[PermissionProfile] = None

        # Session context for dynamic permission generation
        self._session_id: Optional[str] = None
        self._workspace_path: Optional[str] = None
        self._workspace_absolute_path: Optional[Path] = None
        self._username: Optional[str] = None

        # Internal config manager for permission checking
        self._config_manager = PermissionConfigManager()

        # Optional tracer for notifications
        self._tracer: Optional["TracerBase"] = None

    def set_tracer(self, tracer: "TracerBase") -> None:
        """
        Set the tracer for notifications.

        Args:
            tracer: TracerBase instance to notify.
        """
        self._tracer = tracer

    def _find_profile_file(self, base_name: str) -> Path:
        """
        Find a profile file by base name, trying extensions in order.

        Tries .yaml first, then .yml, then .json.

        Args:
            base_name: Base filename without extension (e.g., "permissions").

        Returns:
            Path to the first existing file, or path with .yaml extension
            if no file exists (for error reporting).
        """
        for ext in self.SUPPORTED_EXTENSIONS:
            path = CONFIG_DIR / f"{base_name}{ext}"
            if path.exists():
                return path
        # Return default .yaml path if nothing exists
        return CONFIG_DIR / f"{base_name}.yaml"

    def _notify_profile_loaded(self) -> None:
        """Notify tracer about profile load if tracer is set."""
        if self._tracer is None or self._active_profile is None:
            return

        tools = self.get_enabled_tools()
        allow_count = 0
        deny_count = 0
        if self._active_profile.permissions is not None:
            allow_count = len(self._active_profile.permissions.allow)
            deny_count = len(self._active_profile.permissions.deny)

        profile_path = str(self._profile_path)

        # Call on_profile_switch with 'user' type for compatibility
        self._tracer.on_profile_switch(
            profile_type="user",
            profile_name=self._active_profile.name,
            tools=tools,
            allow_rules_count=allow_count,
            deny_rules_count=deny_count,
            profile_path=profile_path
        )

    def _load_profile(self, path: Path) -> PermissionProfile:
        """
        Load a permission profile from file (YAML or JSON).

        Supports both YAML (.yaml, .yml) and JSON (.json) formats.
        Format is determined by file extension.

        Args:
            path: Path to profile file.

        Returns:
            Loaded PermissionProfile.

        Raises:
            ProfileNotFoundError: If profile file does not exist.
            ValueError: If profile file is invalid.
        """
        if not path.exists():
            raise ProfileNotFoundError(
                f"Permission profile not found: {path}\n"
                f"Create the profile file manually or copy from templates.\n"
                f"Supported formats: .yaml, .yml, .json"
            )

        try:
            with path.open("r", encoding="utf-8") as f:
                # Determine format by extension
                suffix = path.suffix.lower()
                if suffix in (".yaml", ".yml"):
                    data = yaml.safe_load(f)
                elif suffix == ".json":
                    data = json.load(f)
                else:
                    # Try YAML first (more permissive parser)
                    data = yaml.safe_load(f)

            profile = PermissionProfile.model_validate(data)
            logger.info(f"Loaded permission profile from {path}")
            return profile
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse YAML profile {path}: {e}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON profile {path}: {e}")
        except Exception as e:
            raise ValueError(f"Failed to load profile {path}: {e}")

    def _ensure_profile_loaded(self) -> None:
        """Ensure profile is loaded from config file."""
        if self._profile_base is None:
            self._profile_base = self._load_profile(self._profile_path)
        if self._profile is None:
            # Use base profile if no session context is set
            self._profile = self._profile_base
            self._active_profile = self._profile
            self._update_config_manager()

    def reload_profile(self) -> None:
        """Force reload profile from file."""
        self._profile = None
        self._profile_base = None
        self._ensure_profile_loaded()
        # Re-apply session context if set
        if self._session_id and self._workspace_path:
            self._build_session_profile()

    def set_session_context(
        self,
        session_id: str,
        workspace_path: str,
        workspace_absolute_path: Optional[Path] = None,
        username: Optional[str] = None
    ) -> None:
        """
        Set the session context for dynamic permission generation.

        This creates a session-specific profile that restricts
        the agent to only its own workspace folder.

        Args:
            session_id: The session ID (e.g., "20250130_123456_abc12345").
            workspace_path: Path to the session workspace, relative to AGENT_DIR
                           (e.g., "./sessions/20250130_123456_abc12345/workspace").
            workspace_absolute_path: Absolute path to the workspace directory.
                           Used for resolving relative paths in permission matching.
            username: Username for sandbox mount path substitution.

        Example:
            manager.set_session_context(
                session_id="20250130_123456_abc12345",
                workspace_path="./sessions/20250130_123456_abc12345/workspace",
                workspace_absolute_path=Path("/full/path/to/workspace"),
                username="john"
            )
            # Now agent can only access this specific workspace
        """
        self._session_id = session_id
        self._workspace_path = workspace_path
        self._workspace_absolute_path = workspace_absolute_path
        self._username = username
        self._ensure_profile_loaded()
        self._build_session_profile()

        # Update config manager with workspace context for path resolution
        if workspace_absolute_path is not None:
            self._config_manager.set_working_directory(workspace_absolute_path)

        if self._profile_base and self._profile_base.sandbox:
            logger.info(
                "Sandbox configuration loaded: enabled=%s file=%s network=%s",
                self._profile_base.sandbox.enabled,
                self._profile_base.sandbox.file_sandboxing,
                self._profile_base.sandbox.network_sandboxing,
            )

        logger.info(
            f"Session context set: session_id={session_id}, "
            f"workspace={workspace_path}"
        )

    def _build_session_profile(self) -> None:
        """
        Build a session-specific profile.

        Creates a modified copy of the base profile with
        session-specific permission rules from the session_workspace config.
        Uses patterns defined in permissions.yaml, replacing {workspace}
        placeholder with the actual workspace path.
        """
        if self._profile_base is None or self._workspace_path is None:
            return

        base = self._profile_base
        workspace = self._workspace_path

        # Check if session_workspace config exists
        if base.session_workspace is None:
            logger.warning(
                "No session_workspace config in profile, "
                "using base profile without session-specific rules"
            )
            self._profile = base
            self._active_profile = self._profile
            self._update_config_manager()
            return

        ws_config = base.session_workspace

        # Build session-specific allow rules from config
        # Replace {workspace} placeholder with actual workspace path
        session_allow: list[str] = []
        for pattern in ws_config.allow:
            session_allow.append(pattern.replace("{workspace}", workspace))

        # Build session-specific deny rules from config
        session_deny: list[str] = []
        for pattern in ws_config.deny:
            session_deny.append(pattern.replace("{workspace}", workspace))

        # Build session-specific allowed_dirs from config
        session_allowed_dirs: list[str] = []
        for pattern in ws_config.allowed_dirs:
            session_allowed_dirs.append(pattern.replace("{workspace}", workspace))

        # Create session-specific profile with permissions for the config manager
        self._profile = PermissionProfile(
            name=f"user:{self._session_id}",
            description=(
                f"Session-specific profile. "
                f"Sandboxed to workspace: {workspace}"
            ),
            defaultMode=base.defaultMode,
            tools=base.tools.model_copy(),
            permissions=ExtendedPermissionRules(
                allow=session_allow,
                deny=session_deny,
                ask=[],
                allowed_dirs=session_allowed_dirs,
            ),
        )
        self._active_profile = self._profile
        self._update_config_manager()

        logger.info(
            f"Built session-specific profile: {self._profile.name}"
        )

    def clear_session_context(self) -> None:
        """
        Clear the session context and revert to base profile.

        Call this when the session is complete to reset permissions.
        """
        self._session_id = None
        self._workspace_path = None
        self._workspace_absolute_path = None
        self._username = None
        if self._profile_base is not None:
            self._profile = self._profile_base
            self._active_profile = self._profile
            self._update_config_manager()
        # Clear working directory from config manager
        self._config_manager.clear_working_directory()
        logger.info("Session context cleared, reverted to base profile")

    @property
    def profile(self) -> PermissionProfile:
        """Get the current profile."""
        self._ensure_profile_loaded()
        return self._profile  # type: ignore

    @property
    def active_profile(self) -> PermissionProfile:
        """Get the currently active profile."""
        if self._active_profile is None:
            self._ensure_profile_loaded()
        return self._active_profile  # type: ignore

    @property
    def sandbox_config(self) -> Optional[SandboxConfig]:
        """Get sandbox configuration from the base profile, if any."""
        self._ensure_profile_loaded()
        if self._profile_base is None:
            return None
        return self.get_sandbox_config()

    def get_sandbox_config(
        self,
        sandboxed_envs: Optional[dict[str, str]] = None,
    ) -> Optional[SandboxConfig]:
        """
        Resolve sandbox config placeholders and inject sandboxed environment variables.

        Args:
            sandboxed_envs: Optional dictionary of environment variables to inject
                           into the sandbox. These will be available via --setenv
                           in the bubblewrap command.

        Returns:
            Resolved SandboxConfig with custom_env populated, or None if sandbox is disabled.
        """
        self._ensure_profile_loaded()
        if self._profile_base is None or self._profile_base.sandbox is None:
            return None

        session_dir = ""
        workspace_dir = ""
        if self._session_id:
            session_dir = str(SESSIONS_DIR / self._session_id)
        if self._workspace_absolute_path is not None:
            workspace_dir = str(self._workspace_absolute_path)

        placeholders = {
            "agent_dir": str(AGENT_DIR),
            "sessions_dir": str(SESSIONS_DIR),
            "session_dir": session_dir,
            "workspace_dir": workspace_dir,
            "config_dir": str(CONFIG_DIR),
            "skills_dir": str(SKILLS_DIR),
            "logs_dir": str(LOGS_DIR),
            "data_dir": str(DATA_DIR),
            # User-specific placeholders for sandbox mounts
            "username": self._username or "",
            "session_id": self._session_id or "",
        }
        resolved = self._profile_base.sandbox.resolve(placeholders)

        # SECURITY: Inject sandboxed environment variables for THIS session only
        # The resolved config has a fresh SandboxEnvConfig with empty custom_env
        # (created in SandboxConfig.resolve() to prevent cross-session leakage)
        if sandboxed_envs and resolved.environment:
            resolved.environment.custom_env = dict(sandboxed_envs)
            logger.info(
                f"Injected {len(sandboxed_envs)} sandboxed environment variables "
                f"into sandbox config for session {self._session_id}"
            )
        elif resolved.environment:
            # Ensure custom_env is empty if no sandboxed_envs provided
            # This is defensive - resolve() already sets it to empty
            resolved.environment.custom_env = {}

        return resolved

    def activate(self) -> PermissionProfile:
        """
        Activate the profile (ensure it's loaded and ready).

        Returns:
            The activated profile.
        """
        self._ensure_profile_loaded()

        # Log profile activation with details
        profile_name = self._active_profile.name if self._active_profile else "user"
        allow_count = 0
        deny_count = 0
        if self._active_profile and self._active_profile.permissions:
            allow_count = len(self._active_profile.permissions.allow)
            deny_count = len(self._active_profile.permissions.deny)
        logger.info(
            f"PROFILE: Activated '{profile_name}' "
            f"(allow={allow_count}, deny={deny_count})"
        )

        # Notify tracer about profile load
        self._notify_profile_loaded()

        return self._active_profile  # type: ignore

    def _update_config_manager(self) -> None:
        """Update the internal config manager with active profile settings."""
        if self._active_profile is None:
            return

        # Convert profile to PermissionConfig for the config manager
        # Map ExtendedPermissionRules to PermissionRules
        perm_rules = PermissionRules()
        if self._active_profile.permissions is not None:
            perm_rules = PermissionRules(
                allow=self._active_profile.permissions.allow,
                deny=self._active_profile.permissions.deny,
                ask=self._active_profile.permissions.ask,
            )

        # Map ExtendedToolsConfig to ToolsConfig
        tools_config = ToolsConfig(
            enabled=self._active_profile.tools.enabled,
            disabled=self._active_profile.tools.disabled,
        )

        config = PermissionConfig(
            defaultMode=self._active_profile.defaultMode,
            permissions=perm_rules,
            tools=tools_config,
        )
        self._config_manager._config = config
        # Set _last_modified to prevent reload from file
        self._config_manager._last_modified = time.time()
        # Also clear _config_path to prevent file-based reload
        self._config_manager._config_path = None

    def is_allowed(self, tool_call: str) -> bool:
        """
        Check if a tool call is allowed by the current profile.

        Args:
            tool_call: Tool call string, e.g., "Read(/path/to/file)"

        Returns:
            True if allowed, False if denied.
        """
        if self._active_profile is None:
            self._ensure_profile_loaded()
        return self._config_manager.is_tool_allowed(tool_call)

    def needs_confirmation(self, tool_call: str) -> bool:
        """
        Check if a tool call requires confirmation.

        Args:
            tool_call: Tool call string.

        Returns:
            True if confirmation needed.
        """
        return self._config_manager.needs_confirmation(tool_call)

    def get_enabled_tools(self) -> list[str]:
        """Get list of enabled tools for current profile."""
        profile = self.active_profile
        enabled = set(profile.tools.enabled)
        disabled = set(profile.tools.disabled)
        return list(enabled - disabled)

    def get_permission_checked_tools(self) -> set[str]:
        """
        Get tools that require permission callback checks.

        These tools are available but each use is validated.

        Returns:
            Set of tool names that need permission checks.
        """
        return set(self.active_profile.tools.permission_checked)

    def get_disabled_tools(self) -> set[str]:
        """
        Get tools that are completely disabled.

        These tools cannot be used at all.

        Returns:
            Set of tool names that are disabled.
        """
        return set(self.active_profile.tools.disabled)

    def get_pre_approved_tools(self) -> list[str]:
        """
        Get tools that are pre-approved (no permission check needed).

        Returns:
            List of tool names that don't require permission checks.
        """
        enabled = set(self.get_enabled_tools())
        permission_checked = self.get_permission_checked_tools()
        disabled = self.get_disabled_tools()
        return list(enabled - permission_checked - disabled)

    def get_allowed_dirs(self) -> list[str]:
        """
        Get directories accessible in the current profile.

        Returns:
            List of directory paths (relative to working dir).
        """
        profile = self.active_profile
        if profile.permissions is not None:
            return list(profile.permissions.allowed_dirs)
        return []

    def get_allowed_patterns_for_tool(self, tool_name: str) -> list[str]:
        """
        Get allowed patterns for a specific tool from the active profile.

        Useful for providing actionable guidance when a command is denied.

        Args:
            tool_name: Name of the tool (e.g., "Bash", "Read", "Write").

        Returns:
            List of allowed patterns for the tool.
        """
        profile = self.active_profile
        if profile.permissions is None:
            return []
        return extract_patterns_for_tool(tool_name, profile.permissions.allow)

    def get_denied_patterns_for_tool(self, tool_name: str) -> list[str]:
        """
        Get denied patterns for a specific tool from the active profile.

        Args:
            tool_name: Name of the tool.

        Returns:
            List of denied patterns for the tool.
        """
        profile = self.active_profile
        if profile.permissions is None:
            return []
        return extract_patterns_for_tool(tool_name, profile.permissions.deny)

    def save_profile(
        self,
        profile: PermissionProfile,
        target_path: Optional[Path] = None
    ) -> Path:
        """
        Save a permission profile to file (YAML or JSON).

        Format is determined by file extension. Defaults to YAML.

        Args:
            profile: Profile to save.
            target_path: Path to save to. If None, uses default path.

        Returns:
            Path where profile was saved.
        """
        if target_path is None:
            target_path = self._profile_path

        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = profile.model_dump(mode="json")
        suffix = target_path.suffix.lower()

        with target_path.open("w", encoding="utf-8") as f:
            if suffix in (".yaml", ".yml"):
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            else:
                json.dump(data, f, indent=2)

        logger.info(f"Saved profile '{profile.name}' to {target_path}")
        return target_path

    def validate_profile_exists(self) -> Path:
        """
        Validate that profile file exists.

        Returns:
            Path to profile file.

        Raises:
            ProfileNotFoundError: If profile file is missing.
        """
        if not self._profile_path.exists():
            raise ProfileNotFoundError(
                f"Permission profile not found: {self._profile_path}\n"
                "Create the file manually or copy from templates."
            )
        return self._profile_path


def validate_profile_file(config_dir: Optional[Path] = None) -> Path:
    """
    Validate that permission profile file exists.

    Args:
        config_dir: Directory containing profile.
                   Defaults to AGENT/config/.

    Returns:
        Path to profile file.

    Raises:
        ProfileNotFoundError: If profile file is missing.
    """
    if config_dir is None:
        config_dir = CONFIG_DIR

    manager = PermissionManager(
        profile_path=config_dir / f"{PermissionManager.PROFILE_BASE}.yaml"
    )
    return manager.validate_profile_exists()
