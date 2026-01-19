"""
Permission management for Ag3ntum.

Provides a unified interface for permission management that bridges
the new centralized PermissionConfig system with legacy code.

SECURITY NOTE: Dangerous command pattern filtering has been REMOVED.
Security is now enforced by:
1. Ag3ntumBash's bwrap sandbox (OS-level filesystem isolation)
2. Ag3ntumPathValidator (for Python file tools)
3. Native Claude Code tools are blocked via disallowed_tools

For full permission configuration, see permission_config.py.
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from .hooks import HooksManager, create_permission_hook, create_header_reminder_hook
from .tool_utils import build_actionable_denial_message, build_tool_call_string

from .permission_config import (
    AVAILABLE_TOOLS,
    DEFAULT_PERMISSION_CONFIG,
    DANGEROUS_TOOLS,
    SAFE_TOOLS,
    PermissionConfig,
    PermissionConfigManager,
    PermissionMode,
    PermissionRules,
    ToolDefinition,
    ToolsConfig,
    get_all_tool_definitions,
    get_dangerous_tools,
    get_safe_tools,
)

logger = logging.getLogger(__name__)


@dataclass
class PermissionDenial:
    """
    Record of a permission denial.
    """
    tool_name: str
    tool_call: str
    message: str
    is_security_violation: bool = False


@dataclass
class PermissionDenialTracker:
    """
    Tracks permission denials during agent execution.
    """
    denials: list[PermissionDenial] = field(default_factory=list)
    _interrupted: bool = False

    def record_denial(
        self,
        tool_name: str,
        tool_call: str,
        message: str,
        is_security_violation: bool = False,
        interrupt: bool = False
    ) -> None:
        """
        Record a permission denial.

        Args:
            tool_name: Name of the denied tool.
            tool_call: Full tool call string.
            message: Denial message.
            is_security_violation: Whether this is a security violation.
            interrupt: Whether this denial interrupts agent execution.
                      Only interrupting denials should override agent output.
        """
        self.denials.append(PermissionDenial(
            tool_name=tool_name,
            tool_call=tool_call,
            message=message,
            is_security_violation=is_security_violation,
        ))
        # Only mark as interrupted if this denial actually stops the agent
        if interrupt:
            self._interrupted = True

    @property
    def was_interrupted(self) -> bool:
        """Check if the agent was interrupted due to permission denial."""
        return self._interrupted

    @property
    def last_denial(self) -> Optional[PermissionDenial]:
        """Get the most recent denial, if any."""
        return self.denials[-1] if self.denials else None

    def clear(self) -> None:
        """Clear all recorded denials."""
        self.denials.clear()
        self._interrupted = False


class PermissionManager:
    """
    Manages tool permissions for the agent.

    This class provides a unified interface for permission management,
    integrating with the centralized PermissionConfig system while
    maintaining backward compatibility with existing code.

    Usage:
        # Load from default location (AGENT/config/permissions.json)
        manager = PermissionManager()

        # Load from specific config file
        manager = PermissionManager(config_path=Path("./my-permissions.json"))

        # Load from project directory (.claude/settings.local.json)
        manager = PermissionManager(config_dir=Path("/path/to/project"))

        # Check permissions
        if manager.is_allowed("Bash(git commit -m 'test')"):
            # Execute the tool
            pass

        # Get allowed tools for SDK
        tools = manager.get_allowed_tools_for_sdk()
    """

    DEFAULT_PERMISSIONS: dict[str, Any] = DEFAULT_PERMISSION_CONFIG.model_dump(
        mode="json"
    )

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        config_path: Optional[Path] = None
    ) -> None:
        """
        Initialize the permission manager.

        Args:
            config_dir: Directory containing .claude/settings.local.json.
                       If None, uses AGENT/config/permissions.json.
            config_path: Direct path to a permissions.json config file.
                        Takes precedence over config_dir.
        """
        self._config_dir = config_dir
        self._config_path = config_path
        self._config_manager = PermissionConfigManager(
            config_path=config_path,
            project_dir=config_dir
        )
        self._config: Optional[PermissionConfig] = None

    def _load_config(self) -> PermissionConfig:
        """Load configuration, using cached version if available."""
        if self._config is None:
            self._config = self._config_manager.load()
        return self._config

    def reload(self) -> PermissionConfig:
        """Force reload configuration from file."""
        self._config = self._config_manager.reload()
        return self._config

    @property
    def config(self) -> PermissionConfig:
        """Get the current permission configuration."""
        return self._load_config()

    @property
    def allow_rules(self) -> list[str]:
        """Get the list of allowed patterns."""
        return self._load_config().permissions.allow

    @property
    def deny_rules(self) -> list[str]:
        """Get the list of denied patterns."""
        return self._load_config().permissions.deny

    @property
    def ask_rules(self) -> list[str]:
        """Get the list of patterns requiring confirmation."""
        return self._load_config().permissions.ask

    @property
    def permission_mode(self) -> PermissionMode:
        """Get the current permission mode."""
        return self._load_config().defaultMode

    @property
    def enabled_tools(self) -> list[str]:
        """Get list of enabled tools."""
        return self._config_manager.get_enabled_tools()

    def is_allowed(self, tool_call: str) -> bool:
        """
        Check if a tool call is allowed.

        Args:
            tool_call: Tool call string, e.g., "Bash(git status)"

        Returns:
            True if allowed, False if denied.
        """
        return self._config_manager.is_tool_allowed(tool_call)

    def needs_confirmation(self, tool_call: str) -> bool:
        """
        Check if a tool call requires user confirmation.

        Args:
            tool_call: Tool call string.

        Returns:
            True if confirmation needed.
        """
        return self._config_manager.needs_confirmation(tool_call)

    def get_allowed_tools_for_sdk(self) -> list[str]:
        """
        Get list of allowed tools in SDK format.

        Returns:
            List of tool names for ClaudeAgentOptions.allowed_tools.
        """
        return self._config_manager.get_allowed_tools_for_sdk()

    def get_tool_info(self, tool_name: str) -> Optional[ToolDefinition]:
        """
        Get information about a specific tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            ToolDefinition or None if not found.
        """
        return self._config_manager.get_tool_info(tool_name)

    def to_dict(self) -> dict[str, Any]:
        """Return permissions as a dictionary."""
        return self._load_config().model_dump(mode="json")

    def to_claude_settings(self) -> dict[str, Any]:
        """
        Convert to .claude/settings.local.json format.

        Returns:
            Dictionary suitable for Claude settings file.
        """
        return self._config_manager.to_claude_settings()

    def save(self, target_dir: Path) -> None:
        """
        Save permissions to .claude/settings.local.json.

        Args:
            target_dir: Directory to save the settings file in.
        """
        claude_dir = target_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        settings_file = claude_dir / "settings.local.json"
        settings_data = self.to_claude_settings()

        settings_file.write_text(
            json.dumps(settings_data, indent=2)
        )
        logger.info(f"Saved permissions to {settings_file}")

    def save_config(self, target_path: Optional[Path] = None) -> Path:
        """
        Save full configuration to permissions.json.

        Args:
            target_path: Path to save to. Defaults to AGENT/config/permissions.json.

        Returns:
            Path where config was saved.
        """
        return self._config_manager.save(target_path)

    @staticmethod
    def get_available_tools() -> dict[str, ToolDefinition]:
        """Get all available tool definitions."""
        return get_all_tool_definitions()

    @staticmethod
    def get_safe_tools() -> list[str]:
        """Get list of safe (read-only) tools."""
        return get_safe_tools()

    @staticmethod
    def get_dangerous_tools() -> list[str]:
        """Get list of dangerous tools."""
        return get_dangerous_tools()


def create_default_settings(target_dir: Path) -> None:
    """
    Create default .claude/settings.local.json in target directory.

    Args:
        target_dir: Directory to create settings in.
    """
    manager = PermissionManager()
    manager.save(target_dir)


def load_permissions_from_config(
    config_path: Optional[Path] = None,
    project_dir: Optional[Path] = None
) -> PermissionManager:
    """
    Load permissions from configuration file.

    Args:
        config_path: Direct path to permissions.json file.
        project_dir: Project directory for .claude/settings.local.json.

    Returns:
        Configured PermissionManager instance.
    """
    return PermissionManager(
        config_dir=project_dir,
        config_path=config_path
    )


def create_permission_callback(
    permission_manager: Any,
    on_permission_check: Optional[Any] = None,
    denial_tracker: Optional[PermissionDenialTracker] = None,
    trace_processor: Optional[Any] = None,
    max_denials_before_interrupt: int = 3,
    system_message_builder: Optional[Any] = None,
):
    """
    Create a permission callback that enforces permission rules.

    This is a CORE security feature that:
    1. Provides actionable guidance on denial (what IS allowed)
    2. Uses smart interrupt logic: stops only after repeated failures
    3. Immediately interrupts on security violations
    4. Blocks dangerous command patterns

    NOTE: Sandbox wrapping is now handled by Ag3ntumBash tool itself.
    Raw Bash is disabled in permissions.yaml. Security is enforced by:
    - Ag3ntumBash's internal bwrap sandbox (for subprocesses)
    - Ag3ntumPathValidator (for Python file tools like Ag3ntumRead/Write)
    - Dangerous command pattern blocking (in this callback)

    Args:
        permission_manager: Permission manager instance (PermissionManager or
                           PermissionManager) with is_allowed() method.
        on_permission_check: Optional callback for tracing permission decisions.
                            Called with (tool_name: str, decision: str).
        denial_tracker: Optional tracker to record denials for output generation.
        trace_processor: Optional trace processor to mark as permission denied.
        max_denials_before_interrupt: Number of denials before interrupting.
                                      Default is 3 to allow Claude to learn.

    Returns:
        Async permission callback for ClaudeAgentOptions.can_use_tool.
    """
    # Track denial counts per tool to enable smart interrupt
    denial_counts: dict[str, int] = {}

    async def can_use_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        """
        Permission callback with rule enforcement and actionable guidance.

        Uses smart interrupt logic:
        - Security violations: immediate interrupt
        - Regular denials: allow learning, interrupt after max_denials_before_interrupt
        """
        logger.info(f"PERMISSION CHECK: {tool_name} with input: {tool_input}")

        # SECURITY: Always deny attempts to bypass sandbox - immediate interrupt
        if tool_input.get("dangerouslyDisableSandbox"):
            security_msg = "Security violation: sandbox bypass attempted. Agent stopped."
            logger.warning(
                f"SECURITY: Model attempted to use dangerouslyDisableSandbox! "
                f"Tool: {tool_name}, Input: {tool_input}"
            )
            if on_permission_check:
                on_permission_check(tool_name, "deny")
            if denial_tracker:
                denial_tracker.record_denial(
                    tool_name=tool_name,
                    tool_call=f"{tool_name}(dangerouslyDisableSandbox=True)",
                    message=security_msg,
                    is_security_violation=True,
                    interrupt=True,  # Security violations always interrupt
                )
            if trace_processor and hasattr(trace_processor, 'set_permission_denied'):
                trace_processor.set_permission_denied(True)
            return PermissionResultDeny(
                behavior="deny",
                message=security_msg,
                interrupt=True
            )

        # NOTE: Dangerous command pattern filtering has been REMOVED.
        # Security is now enforced by Ag3ntumBash's bwrap sandbox which:
        # - Creates a mount namespace where only /workspace is visible
        # - Prevents access to system files even with dangerous commands
        # - Runs commands with reduced privileges (UID switching)
        # 
        # Raw Bash is disabled in permissions.yaml, so only Ag3ntumBash is used.

        # Check permission rules
        tool_call = build_tool_call_string(tool_name, tool_input)
        logger.info(f"PERMISSION CHECK: tool_call={tool_call}")
        allowed = permission_manager.is_allowed(tool_call)
        decision = "allow" if allowed else "deny"
        logger.info(f"PERMISSION CHECK: decision={decision}")

        if on_permission_check:
            on_permission_check(tool_name, decision)

        if allowed:
            # NOTE: Raw Bash is disabled in permissions.yaml (tools.disabled).
            # All command execution goes through mcp__ag3ntum__Bash which has
            # built-in bwrap sandbox. No need for permission callback to wrap.
            # 
            # Security is now enforced by:
            # 1. Ag3ntumBash tool's internal bwrap sandbox (for subprocesses)
            # 2. Ag3ntumPathValidator (for Python file tools like Ag3ntumRead/Write)
            
            allow_result = PermissionResultAllow(behavior="allow")
            if system_message_builder is not None:
                message = system_message_builder(tool_name, tool_input)
                if message and hasattr(allow_result, "system_message"):
                    allow_result.system_message = message
            return allow_result

        # Denied - track denial count for smart interrupt
        denial_counts[tool_name] = denial_counts.get(tool_name, 0) + 1
        current_count = denial_counts[tool_name]
        total_denials = sum(denial_counts.values())

        # Determine if we should interrupt
        should_interrupt = (
            current_count >= max_denials_before_interrupt or
            total_denials >= max_denials_before_interrupt * 2
        )
        is_penultimate = current_count == max_denials_before_interrupt - 1

        # Build actionable denial message
        denial_msg = build_actionable_denial_message(
            tool_name=tool_name,
            tool_input=tool_input,
            is_final_denial=is_penultimate,
            permission_manager=permission_manager,
        )

        # Append interrupt reason if this is the final denial
        if should_interrupt:
            denial_msg = (
                f"TASK STOPPED: Maximum permission failures exceeded "
                f"({current_count}/{max_denials_before_interrupt} denials for {tool_name}). "
                f"Last error: {denial_msg}"
            )

        logger.info(
            f"PERMISSION DENIAL: {tool_name} denied "
            f"(count={current_count}/{max_denials_before_interrupt}, "
            f"interrupt={should_interrupt})"
        )

        # Record denial for output generation
        if denial_tracker:
            denial_tracker.record_denial(
                tool_name=tool_name,
                tool_call=tool_call,
                message=denial_msg,
                is_security_violation=False,
                interrupt=should_interrupt,  # Only mark interrupted if actually stopping
            )

        # Mark trace processor if we're interrupting
        if should_interrupt:
            if trace_processor and hasattr(trace_processor, 'set_permission_denied'):
                trace_processor.set_permission_denied(True)

        deny_result = PermissionResultDeny(
            behavior="deny",
            message=denial_msg,
            interrupt=should_interrupt
        )
        if system_message_builder is not None:
            message = system_message_builder(tool_name, tool_input)
            if message and hasattr(deny_result, "system_message"):
                deny_result.system_message = message
        return deny_result

    return can_use_tool


def create_permission_hooks(
    permission_manager: Any,
    on_permission_check: Optional[Any] = None,
    denial_tracker: Optional[PermissionDenialTracker] = None,
    trace_processor: Optional[Any] = None,
    system_message_builder: Optional[Any] = None,
    workspace_dir: Optional[str] = None,
) -> dict:
    """
    Create SDK hooks configuration for permission management.

    This is the preferred way to integrate permissions with the new hooks system.
    Returns a hooks config dict that can be passed to ClaudeAgentOptions.

    Args:
        permission_manager: Permission manager instance with is_allowed() method.
        on_permission_check: Optional callback for tracing permission decisions.
        denial_tracker: Optional tracker to record denials.
        trace_processor: Optional trace processor for status updates.
        workspace_dir: Optional workspace directory for path normalization.

    Returns:
        Dictionary for ClaudeAgentOptions.hooks parameter.
        
    NOTE: Security hooks have been simplified. Dangerous command blocking
    and absolute path blocking are now handled by:
    - Ag3ntumBash's bwrap sandbox (OS-level isolation)
    - Ag3ntumPathValidator (for Python file tools)
    """
    manager = HooksManager()
    
    # NOTE: Absolute path blocking hook REMOVED - handled by Ag3ntumPathValidator
    # NOTE: Dangerous command hook REMOVED - handled by bwrap sandbox

    # Add permission checking hook (for legacy pattern matching - will be simplified further)
    permission_hook = create_permission_hook(
        permission_manager=permission_manager,
        on_permission_check=on_permission_check,
        denial_tracker=denial_tracker,
        trace_processor=trace_processor,
        system_message_builder=system_message_builder,
    )
    manager.add_pre_tool_hook(permission_hook)

    # Add header reminder hook to reinforce structured output requirements
    # This injects a system reminder after tool completions to ensure the agent
    # starts its next response with the required header block
    header_reminder_hook = create_header_reminder_hook(enable=True)
    manager.add_post_tool_hook(header_reminder_hook)

    return manager.build_hooks_config()


# Re-export for convenience
__all__ = [
    "PermissionManager",
    "PermissionConfig",
    "PermissionConfigManager",
    "PermissionMode",
    "PermissionRules",
    "ToolsConfig",
    "ToolDefinition",
    "PermissionDenial",
    "PermissionDenialTracker",
    "AVAILABLE_TOOLS",
    "SAFE_TOOLS",
    "DANGEROUS_TOOLS",
    "create_default_settings",
    "load_permissions_from_config",
    "get_all_tool_definitions",
    "get_safe_tools",
    "get_dangerous_tools",
    "create_permission_callback",
    "create_permission_hooks",
]
