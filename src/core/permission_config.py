"""
Centralized Permission Configuration for Ag3ntum.

Provides tool definitions and permission schema structures.

=============================================================================
DEPRECATION NOTICE
=============================================================================
This module is LEGACY and should not be used for new security logic.

The permission system has been replaced by:
1. config/permissions.yaml - Runtime permission configuration
2. config/security/command_filtering.yaml - Command security rules  
3. src/core/command_security.py - CommandSecurityFilter for dangerous commands
4. src/core/permission_profiles.py - PermissionManager that loads YAML

What's still used from this module:
- AVAILABLE_TOOLS: Tool metadata for CLI display
- ToolDefinition: Pydantic model for tool info
- PermissionConfig/PermissionRules: Schema structures

What's DEPRECATED:
- DEFAULT_PERMISSION_CONFIG: Legacy native tool rules (native tools are blocked)
- PermissionConfigManager: Legacy JSON-based config (use YAML profiles instead)

Security is now enforced by:
1. CommandSecurityFilter: Pre-execution regex filtering of dangerous commands
2. Ag3ntumBash's bwrap sandbox: OS-level filesystem isolation
3. Ag3ntumPathValidator: For Python file tools like Ag3ntumRead/Write
4. tools.disabled in permissions.yaml: Native Claude Code tools are blocked
=============================================================================
"""
import fnmatch
import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

# Import paths from central config
from ..config import AGENT_DIR, CONFIG_DIR
from .tool_utils import extract_patterns_for_tool

logger = logging.getLogger(__name__)


class PermissionMode(StrEnum):
    """
    Permission modes that control tool usage behavior.

    - default: Standard behavior with prompts for permission on first use.
    - acceptEdits: Automatically accepts file edit permissions for the session.
    - plan: Read-only analysis mode, no file modifications or command execution.
    - bypassPermissions: Skips all permission prompts (use with caution).
    """
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"


class ToolCategory(StrEnum):
    """Categories for grouping tools by functionality."""
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SHELL = "shell"
    SEARCH = "search"
    AGENT = "agent"
    WEB = "web"
    NOTEBOOK = "notebook"
    MISC = "misc"


class ToolDefinition(BaseModel):
    """
    Definition of a Claude Code tool.

    Contains metadata about the tool including its name, description,
    category, and whether it's considered safe or dangerous.
    """
    name: str = Field(
        description="Tool identifier used in allow/deny rules"
    )
    description: str = Field(
        description="""
Detailed description of what the tool does
and when it should be used"""
    )
    category: ToolCategory = Field(
        description="Category for grouping similar tools"
    )
    is_safe: bool = Field(
        default=False,
        description="""
Whether the tool is considered safe (read-only, no side effects).
Safe tools can be auto-approved in certain permission modes."""
    )
    supports_patterns: bool = Field(
        default=False,
        description="""
Whether the tool supports glob patterns in its arguments.
Used for pattern-based allow/deny rules like Bash(git:*)."""
    )
    example_patterns: list[str] = Field(
        default_factory=list,
        description="Example patterns for allow/deny rules"
    )


class HookConfig(BaseModel):
    """Configuration for a permission hook."""
    type: str = Field(
        default="command",
        description="Type of hook (command, script, etc.)"
    )
    command: str = Field(
        description="Command or script to execute for the hook"
    )
    timeout_ms: int = Field(
        default=5000,
        description="Timeout for hook execution in milliseconds"
    )


class HookMatcher(BaseModel):
    """Matcher configuration for hooks."""
    matcher: str = Field(
        default="*",
        description="""
Pattern to match tool names.
Use * for all tools or specific tool names."""
    )
    hooks: list[HookConfig] = Field(
        default_factory=list,
        description="List of hooks to execute when matcher matches"
    )


class HooksConfig(BaseModel):
    """Configuration for all permission hooks."""
    PreToolUse: list[HookMatcher] = Field(
        default_factory=list,
        description="""
Hooks executed before a tool is used.
Can modify inputs or enforce policies."""
    )
    PostToolUse: list[HookMatcher] = Field(
        default_factory=list,
        description="""
Hooks executed after a tool is used.
Useful for logging or cleanup."""
    )
    PermissionRequest: list[HookMatcher] = Field(
        default_factory=list,
        description="""
Hooks executed when Claude requests permission.
Can auto-approve or deny based on custom logic."""
    )


class PermissionRules(BaseModel):
    """
    Permission rules for tool access.

    Supports glob patterns for matching tool calls:
    - Bash(git:*) - Allow all git commands
    - Read(./secrets/**) - Deny reading secrets folder
    - Edit(*) - Allow editing any file
    """
    allow: list[str] = Field(
        default_factory=list,
        description="""
List of allowed tool patterns.
Tools matching these patterns are permitted without prompts.
Supports glob patterns like Bash(npm run lint), Read(~/.zshrc)."""
    )
    deny: list[str] = Field(
        default_factory=list,
        description="""
List of denied tool patterns.
Tools matching these patterns are explicitly prohibited.
Deny rules take precedence over allow rules."""
    )
    ask: list[str] = Field(
        default_factory=list,
        description="""
List of tool patterns that require confirmation.
User will be prompted before these tools execute."""
    )


class ToolsConfig(BaseModel):
    """Configuration for enabled/disabled tools."""
    enabled: list[str] = Field(
        default_factory=list,
        description="""
List of tools that are enabled for the agent.
Empty list means all tools are enabled by default."""
    )
    disabled: list[str] = Field(
        default_factory=list,
        description="""
List of tools that are disabled for the agent.
Disabled tools cannot be used even if allowed."""
    )


class PermissionConfig(BaseModel):
    """
    Complete permission configuration for Ag3ntum.

    This is the main configuration model that combines all permission
    settings into a single, comprehensive structure.
    """
    defaultMode: PermissionMode = Field(
        default=PermissionMode.DEFAULT,
        description="Default permission mode for the agent"
    )
    permissions: PermissionRules = Field(
        default_factory=PermissionRules,
        description="Allow/deny rules for tool access"
    )
    tools: ToolsConfig = Field(
        default_factory=ToolsConfig,
        description="Configuration for enabled/disabled tools"
    )
    hooks: HooksConfig = Field(
        default_factory=HooksConfig,
        description="Hook configurations for dynamic permission management"
    )
    allowedTools: list[str] = Field(
        default_factory=list,
        description="""
Legacy: List of tools passed to Claude SDK.
Prefer using tools.enabled instead."""
    )

    @field_validator("allowedTools", mode="before")
    @classmethod
    def default_allowed_tools(cls, v: list[str]) -> list[str]:
        """Set default allowed tools if empty."""
        if not v:
            return [
                "Bash", "Read", "Write", "Edit", "MultiEdit",
                "Grep", "Glob", "LS", "Task", "Skill"
            ]
        return v


# All available tools in Claude Code with their definitions
AVAILABLE_TOOLS: dict[str, ToolDefinition] = {
    "Bash": ToolDefinition(
        name="Bash",
        description="""
Execute shell commands in a bash environment.
Supports running any shell command with arguments.
Use with caution as it can modify the system.""",
        category=ToolCategory.SHELL,
        is_safe=False,
        supports_patterns=True,
        example_patterns=[
            "Bash(git:*)",
            "Bash(npm run:*)",
            "Bash(python:*)",
            "Bash(ls:*)",
            "Bash(cat:*)",
            "Bash(grep:*)",
        ]
    ),
    "Read": ToolDefinition(
        name="Read",
        description="""
Read contents of files from the filesystem.
Supports reading text files with optional line ranges.
Safe operation with no side effects.""",
        category=ToolCategory.FILE_READ,
        is_safe=True,
        supports_patterns=True,
        example_patterns=[
            "Read(*)",
            "Read(./src/**)",
            "Read(~/.zshrc)",
        ]
    ),
    "Write": ToolDefinition(
        name="Write",
        description="""
Write content to files on the filesystem.
Creates new files or overwrites existing ones.
Can modify or create any file in accessible directories.""",
        category=ToolCategory.FILE_WRITE,
        is_safe=False,
        supports_patterns=True,
        example_patterns=[
            "Write(*)",
            "Write(./sessions/**)",
        ]
    ),
    "Edit": ToolDefinition(
        name="Edit",
        description="""
Edit existing files using search and replace.
Finds specific text and replaces it with new content.
Useful for making targeted modifications to code.""",
        category=ToolCategory.FILE_WRITE,
        is_safe=False,
        supports_patterns=True,
        example_patterns=[
            "Edit(*)",
            "Edit(./src/**/*.py)",
        ]
    ),
    "MultiEdit": ToolDefinition(
        name="MultiEdit",
        description="""
Make multiple edits to a single file in one operation.
More efficient than multiple Edit calls for batch changes.
Atomic operation - all edits apply or none do.""",
        category=ToolCategory.FILE_WRITE,
        is_safe=False,
        supports_patterns=True,
        example_patterns=[
            "MultiEdit(*)",
        ]
    ),
    "Grep": ToolDefinition(
        name="Grep",
        description="""
Search for patterns in files using regex.
Returns matching lines with context.
Safe read-only operation.""",
        category=ToolCategory.SEARCH,
        is_safe=True,
        supports_patterns=False,
        example_patterns=[]
    ),
    "Glob": ToolDefinition(
        name="Glob",
        description="""
Find files matching glob patterns.
Returns list of matching file paths.
Safe read-only operation.""",
        category=ToolCategory.SEARCH,
        is_safe=True,
        supports_patterns=False,
        example_patterns=[]
    ),
    "LS": ToolDefinition(
        name="LS",
        description="""
List directory contents with optional recursion.
Returns file and directory names.
Safe read-only operation.""",
        category=ToolCategory.SEARCH,
        is_safe=True,
        supports_patterns=False,
        example_patterns=[]
    ),
    "Task": ToolDefinition(
        name="Task",
        description="""
Spawn a sub-agent to handle a specific task.
Creates a new Claude instance for parallel work.
Useful for breaking down complex tasks.""",
        category=ToolCategory.AGENT,
        is_safe=False,
        supports_patterns=False,
        example_patterns=[]
    ),
    "TodoRead": ToolDefinition(
        name="TodoRead",
        description="""
Read todo items from the task list.
Returns current todos with status.
Safe read-only operation.""",
        category=ToolCategory.MISC,
        is_safe=True,
        supports_patterns=False,
        example_patterns=[]
    ),
    "TodoWrite": ToolDefinition(
        name="TodoWrite",
        description="""
Write or update todo items in the task list.
Can create, update, or mark todos as complete.
Modifies task state.""",
        category=ToolCategory.MISC,
        is_safe=False,
        supports_patterns=False,
        example_patterns=[]
    ),
    "NotebookEdit": ToolDefinition(
        name="NotebookEdit",
        description="""
Edit Jupyter notebook cells.
Can modify, add, or remove notebook cells.
Supports code, markdown, and raw cells.""",
        category=ToolCategory.NOTEBOOK,
        is_safe=False,
        supports_patterns=True,
        example_patterns=[
            "NotebookEdit(*.ipynb)",
        ]
    ),
    "WebFetch": ToolDefinition(
        name="WebFetch",
        description="""
Fetch content from web URLs.
Downloads and returns web page content.
Requires network access.""",
        category=ToolCategory.WEB,
        is_safe=True,
        supports_patterns=True,
        example_patterns=[
            "WebFetch(*)",
        ]
    ),
    "WebSearch": ToolDefinition(
        name="WebSearch",
        description="""
Search the web for information.
Returns search results with snippets.
Requires network access.""",
        category=ToolCategory.WEB,
        is_safe=True,
        supports_patterns=False,
        example_patterns=[]
    ),
    "Skill": ToolDefinition(
        name="Skill",
        description="""
Execute custom skills defined in the skills directory.
Skills are predefined workflows or capabilities.
Can be safe or dangerous depending on skill.""",
        category=ToolCategory.AGENT,
        is_safe=False,
        supports_patterns=True,
        example_patterns=[
            "Skill(*)",
            "Skill(test:*)",
        ]
    ),
    "Agent": ToolDefinition(
        name="Agent",
        description="""
Invoke another agent for specialized tasks.
Enables delegation to specialized sub-agents.
May have its own permission set.""",
        category=ToolCategory.AGENT,
        is_safe=False,
        supports_patterns=True,
        example_patterns=[
            "Agent(*)",
        ]
    ),
    "AskUserQuestion": ToolDefinition(
        name="AskUserQuestion",
        description="""
Ask the user multiple choice questions to gather information or clarify ambiguity.
Presents options to the user and collects their response.
Safe read-only operation - does not modify any state.
NOTE: This native tool is BLOCKED - use mcp__ag3ntum__AskUserQuestion instead.""",
        category=ToolCategory.MISC,
        is_safe=True,
        supports_patterns=False,
        example_patterns=[]
    ),
    "mcp__ag3ntum__AskUserQuestion": ToolDefinition(
        name="mcp__ag3ntum__AskUserQuestion",
        description="""
Ag3ntum interactive question tool. Asks users multiple choice questions via the web UI.
Pauses execution until user responds through the frontend.
Safe read-only operation - waits for user input.""",
        category=ToolCategory.MISC,
        is_safe=True,
        supports_patterns=False,
        example_patterns=[]
    ),
}

# Safe tools that can be auto-approved in certain modes
SAFE_TOOLS: list[str] = [
    tool.name for tool in AVAILABLE_TOOLS.values() if tool.is_safe
]

# Dangerous tools that should require explicit approval
DANGEROUS_TOOLS: list[str] = [
    tool.name for tool in AVAILABLE_TOOLS.values() if not tool.is_safe
]

# DEPRECATED: Default permission configuration for native tools
# Native tools (Bash, Read, Write, etc.) are now BLOCKED via permissions.yaml
# This configuration is kept for backward compatibility but is NOT used
# for actual security enforcement. See command_security.py for active security.
#
# WARNING: Do not rely on these rules for security. They apply to native
# Claude Code tools which are disabled in production.
DEFAULT_PERMISSION_CONFIG = PermissionConfig(
    defaultMode=PermissionMode.DEFAULT,
    permissions=PermissionRules(
        allow=[
            # Safe read-only operations
            "Read(*)",
            "Grep",
            "Glob",
            "LS",
            "TodoRead",
            # Common safe bash commands
            "Bash(git:*)",
            "Bash(find:*)",
            "Bash(grep:*)",
            "Bash(ls:*)",
            "Bash(cat:*)",
            "Bash(head:*)",
            "Bash(tail:*)",
            "Bash(wc:*)",
            "Bash(python:*)",
            "Bash(echo:*)",
            "Bash(mkdir:*)",
            "Bash(cp:*)",
            "Bash(mv:*)",
        ],
        deny=[
            # Dangerous bash commands
            "Bash(rm -rf:*)",
            "Bash(sudo:*)",
            "Bash(chmod 777:*)",
            "Bash(curl|wget -O:*)",
            # Sensitive file patterns
            "Read(./.env)",
            "Read(./secrets/**)",
            "Read(**/.env*)",
            "Read(**/secrets/**)",
            "Write(./.env)",
            "Write(./secrets/**)",
            "Edit(./.env)",
            "Edit(./secrets/**)",
        ],
        ask=[
            # Potentially impactful operations that need confirmation
            "Bash(git push:*)",
            "Bash(git reset:*)",
            "Bash(npm publish:*)",
            "Bash(docker:*)",
        ]
    ),
    tools=ToolsConfig(
        enabled=[
            "Bash", "Read", "Write", "Edit", "MultiEdit",
            "Grep", "Glob", "LS", "Task", "Skill",
            "TodoRead", "TodoWrite"
        ],
        disabled=[
            # Disabled by default, enable as needed
            # "WebFetch",
            # "WebSearch",
            # "NotebookEdit",
            # "Agent",
        ]
    ),
    allowedTools=[
        "Bash", "Read", "Write", "Edit", "MultiEdit",
        "Grep", "Glob", "LS", "Task", "Skill"
    ]
)


class PermissionConfigManager:
    """
    Manages permission configuration loading and validation.

    Supports loading from multiple sources with precedence:
    1. Runtime configuration (passed as dict or PermissionConfig)
    2. Local project settings (.claude/settings.local.json)
    3. Project settings (.claude/settings.json)
    4. User settings (~/.claude/settings.json)
    5. Default configuration
    """

    CONFIG_FILENAME = "permissions.json"
    CLAUDE_SETTINGS_FILENAME = "settings.local.json"

    def __init__(
        self,
        config_path: Optional[Path] = None,
        project_dir: Optional[Path] = None
    ) -> None:
        """
        Initialize the permission configuration manager.

        Args:
            config_path: Direct path to permissions.json config file.
            project_dir: Project directory for .claude/settings.local.json.
        """
        self._config_path = config_path
        self._project_dir = project_dir
        self._config: Optional[PermissionConfig] = None
        self._last_modified: Optional[float] = None
        # Working directory for resolving relative paths in permission matching
        self._working_directory: Optional[Path] = None

    def set_working_directory(self, working_dir: Path) -> None:
        """
        Set the working directory for path resolution.

        When set, relative paths in tool calls (e.g., ./skills/meow/meow.py)
        are resolved against this directory instead of AGENT_DIR.

        Args:
            working_dir: Absolute path to the working directory.
        """
        self._working_directory = working_dir.resolve()
        logger.info(f"PERMISSION_SETUP: Working directory set to: {self._working_directory}")

    def clear_working_directory(self) -> None:
        """Clear the working directory, reverting to AGENT_DIR for path resolution."""
        self._working_directory = None
        logger.debug("Permission working directory cleared")

    def _find_config_file(self) -> Optional[Path]:
        """
        Find the configuration file to load.

        Search order:
        1. Explicit config_path if provided
        2. AGENT/config/permissions.json
        3. Project .claude/settings.local.json
        4. User ~/.claude/settings.json

        Returns:
            Path to config file or None if not found.
        """
        # Check explicit path first
        if self._config_path and self._config_path.exists():
            return self._config_path

        # Check AGENT/config/permissions.json
        agent_config = CONFIG_DIR / self.CONFIG_FILENAME
        if agent_config.exists():
            return agent_config

        # Check project .claude/settings.local.json
        if self._project_dir:
            project_settings = (
                self._project_dir / ".claude" / self.CLAUDE_SETTINGS_FILENAME
            )
            if project_settings.exists():
                return project_settings

        # Check user settings
        user_settings = Path.home() / ".claude" / "settings.json"
        if user_settings.exists():
            return user_settings

        return None

    def _needs_reload(self) -> bool:
        """Check if configuration file has been modified since last load."""
        config_file = self._find_config_file()
        if config_file is None:
            return self._config is None

        current_mtime = config_file.stat().st_mtime
        if self._last_modified is None:
            return True

        return current_mtime > self._last_modified

    def load(self, force: bool = False) -> PermissionConfig:
        """
        Load permission configuration from file.

        Supports hot-reloading: if the config file has been modified,
        it will be reloaded automatically.

        Args:
            force: Force reload even if file hasn't changed.

        Returns:
            Loaded PermissionConfig.
        """
        if not force and self._config is not None and not self._needs_reload():
            return self._config

        config_file = self._find_config_file()

        if config_file is None:
            logger.debug("No config file found, using defaults")
            self._config = DEFAULT_PERMISSION_CONFIG.model_copy(deep=True)
            return self._config

        try:
            with config_file.open("r", encoding="utf-8") as f:
                config_data = json.load(f)

            self._config = PermissionConfig.model_validate(config_data)
            self._last_modified = config_file.stat().st_mtime
            logger.info(f"Loaded permission config from {config_file}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse {config_file}: {e}")
            self._config = DEFAULT_PERMISSION_CONFIG.model_copy(deep=True)

        except Exception as e:
            logger.error(f"Failed to load {config_file}: {e}")
            self._config = DEFAULT_PERMISSION_CONFIG.model_copy(deep=True)

        return self._config

    def reload(self) -> PermissionConfig:
        """Force reload configuration from file."""
        return self.load(force=True)

    def save(self, target_path: Optional[Path] = None) -> Path:
        """
        Save current configuration to file.

        Args:
            target_path: Path to save to. Defaults to AGENT/config/permissions.json.

        Returns:
            Path where config was saved.
        """
        if target_path is None:
            target_path = CONFIG_DIR / self.CONFIG_FILENAME

        target_path.parent.mkdir(parents=True, exist_ok=True)

        config = self._config or DEFAULT_PERMISSION_CONFIG
        with target_path.open("w", encoding="utf-8") as f:
            json.dump(
                config.model_dump(mode="json"),
                f,
                indent=2
            )

        logger.info(f"Saved permission config to {target_path}")
        return target_path

    def get_enabled_tools(self) -> list[str]:
        """Get list of enabled tools based on configuration."""
        config = self.load()
        enabled = set(config.tools.enabled or list(AVAILABLE_TOOLS.keys()))
        disabled = set(config.tools.disabled)
        return list(enabled - disabled)

    def get_allowed_tools_for_sdk(self) -> list[str]:
        """Get list of allowed tools in SDK format."""
        config = self.load()
        if config.allowedTools:
            return config.allowedTools
        return self.get_enabled_tools()

    def is_tool_allowed(self, tool_call: str) -> bool:
        """
        Check if a tool call is allowed based on permissions.

        Logic (SECURITY-FIRST ORDER):
        1. Check deny rules FIRST - explicit denies always block
        2. Check allow rules - if not denied, check if explicitly allowed
        3. Default to denied for security

        This order ensures that deny rules like Bash(ps *) take precedence
        over broad allow rules like Bash(*), preventing security bypasses.

        Args:
            tool_call: Tool call string, e.g., "Bash(git commit -m 'test')"

        Returns:
            True if allowed, False if denied.
        """
        config = self.load()

        # Log the tool call being checked
        logger.info(f"PERMISSION_CHECK: Checking tool_call='{tool_call}'")

        # SECURITY: Check deny rules FIRST - explicit denies always win
        # This prevents broad allow patterns like Bash(*) from defeating
        # specific deny patterns like Bash(ps *) or Bash(kill *)
        for pattern in config.permissions.deny:
            if self._matches_pattern(tool_call, pattern):
                logger.info(f"PERMISSION_CHECK: Tool '{tool_call}' DENIED by pattern '{pattern}'")
                return False

        # Check allow rules - if not denied, check if explicitly allowed
        for pattern in config.permissions.allow:
            if self._matches_pattern(tool_call, pattern):
                logger.info(f"PERMISSION_CHECK: Tool '{tool_call}' ALLOWED by pattern '{pattern}'")
                return True

        # Default behavior based on mode
        if config.defaultMode == PermissionMode.BYPASS:
            logger.info(f"PERMISSION_CHECK: Tool '{tool_call}' ALLOWED (bypass mode)")
            return True

        # Default to denied for security
        logger.info(f"PERMISSION_CHECK: Tool '{tool_call}' DENIED (no matching allow rule, default deny)")
        return False

    def needs_confirmation(self, tool_call: str) -> bool:
        """
        Check if a tool call requires user confirmation.

        Args:
            tool_call: Tool call string.

        Returns:
            True if confirmation needed.
        """
        config = self.load()

        for pattern in config.permissions.ask:
            if self._matches_pattern(tool_call, pattern):
                return True

        return False

    def _matches_pattern(self, tool_call: str, pattern: str) -> bool:
        """
        Check if a tool call matches a permission pattern.

        Patterns support:
        - Exact match: "Bash" matches "Bash"
        - Glob patterns: "Bash(git:*)" matches "Bash(git commit -m 'test')"
        - File patterns: "Read(./src/**)" matches "Read(./src/foo/bar.py)"
        - Relative patterns are resolved against AGENT_DIR

        Args:
            tool_call: The actual tool call string.
            pattern: The permission pattern.

        Returns:
            True if tool_call matches pattern.
        """
        # Handle case where pattern has no parentheses (tool name only)
        if "(" not in pattern:
            # Match just the tool name
            tool_name = tool_call.split("(")[0] if "(" in tool_call else tool_call
            return fnmatch.fnmatch(tool_name, pattern)

        # Extract tool name and argument from both
        tool_name = tool_call.split("(")[0]
        pattern_name = pattern.split("(")[0]

        # Tool names must match
        if tool_name != pattern_name:
            return False

        # Extract the path/argument from the tool call and pattern
        tool_arg = tool_call[len(tool_name) + 1:-1]  # Remove "ToolName(" and ")"
        pattern_arg = pattern[len(pattern_name) + 1:-1]  # Remove "ToolName(" and ")"

        # For file operations and search, normalize paths for matching
        if tool_name in ("Read", "Write", "Edit", "MultiEdit", "Glob", "Grep"):
            return self._matches_path_pattern(tool_arg, pattern_arg, tool_name=tool_name)

        # For Bash commands with file paths (e.g., "python ./skills/**")
        # Extract the path from the command and match against pattern
        if tool_name == "Bash":
            return self._matches_bash_pattern(tool_arg, pattern_arg)

        # For other tools, use standard glob matching
        fnmatch_pattern = pattern_arg.replace(":*", "*").replace("**", "*")
        return fnmatch.fnmatch(tool_arg, fnmatch_pattern)

    def _matches_bash_pattern(self, command: str, pattern: str) -> bool:
        """
        Check if a Bash command matches a permission pattern.

        Handles commands with file paths like "python ./skills/**" by
        normalizing paths against AGENT_DIR.

        Pattern format: "python ./skills/**" or "python3 ./skills/**"
        Command examples:
          - "python ./skills/meow/meow.py"
          - "python3 /absolute/path/to/skills/meow/meow.py"
          - "cd /path && python3 ./skills/meow/meow.py" (compound command)

        Args:
            command: The actual Bash command string.
            pattern: The permission pattern.

        Returns:
            True if command matches pattern.
        """
        # Deny compound commands entirely for security
        # Commands with &&, ||, ;, |, $(), ``, etc. are not allowed
        if any(op in command for op in [" && ", " || ", " ; ", " | ", "$(", "`"]):
            logger.debug(f"Compound/piped command denied: '{command[:50]}...'")
            return False

        return self._matches_simple_bash_pattern(command, pattern)

    def _matches_simple_bash_pattern(self, command: str, pattern: str) -> bool:
        """
        Check if a simple (non-compound) Bash command matches a pattern.

        Args:
            command: Simple Bash command string.
            pattern: Permission pattern.

        Returns:
            True if command matches pattern.
        """
        # Split command and pattern into parts
        command_parts = command.split()
        pattern_parts = pattern.split()

        if not command_parts or not pattern_parts:
            return False

        # First part is the executable (python, python3, etc.)
        cmd_executable = command_parts[0]
        pattern_executable = pattern_parts[0]

        # Executable must match (or pattern can use * wildcard)
        if not fnmatch.fnmatch(cmd_executable, pattern_executable):
            return False

        # If pattern has a path component, match it
        if len(pattern_parts) > 1 and len(command_parts) > 1:
            pattern_path = pattern_parts[1]
            cmd_path = command_parts[1]

            # Check if pattern contains a path glob (like ./skills/**)
            if "./" in pattern_path or "/" in pattern_path or "**" in pattern_path:
                return self._matches_path_pattern(cmd_path, pattern_path, tool_name="Bash")

        # Handle wildcard pattern like "python *" or bare "python"
        if len(pattern_parts) == 1 and pattern_parts[0].endswith("*"):
            return True

        # If pattern is just the executable and command has more args
        if len(pattern_parts) == 1 and len(command_parts) >= 1:
            return fnmatch.fnmatch(cmd_executable, pattern_executable)

        # Fall back to standard glob matching for non-path patterns
        fnmatch_pattern = pattern.replace(":*", "*").replace("**", "*")
        return fnmatch.fnmatch(command, fnmatch_pattern)

    def _matches_path_pattern(
        self,
        file_path: str,
        pattern: str,
        tool_name: Optional[str] = None
    ) -> bool:
        """
        Check if a file path matches a permission pattern.

        Handles both absolute and relative paths by resolving
        relative paths against the working directory (if set) or AGENT_DIR.

        IMPORTANT: Paths within the working directory are normalized to
        relative paths (./filename) before pattern matching. This ensures
        that deny patterns like /** only match truly external absolute paths,
        not workspace-relative paths that happen to be resolved to absolute.

        Supports glob patterns like:
        - ./skills/** (all files under skills/)
        - ./skills/**/*.py (all .py files under skills/)
        - ./src/*.txt (all .txt files directly in src/)

        Args:
            file_path: The actual file path (may be absolute or relative).
            pattern: The permission pattern (may use ./ for relative).
            tool_name: Optional tool name for debugging context (e.g., "Write", "Read").

        Returns:
            True if path matches pattern.
        """
        # Determine the base directory for resolving relative paths
        # Use working directory if set, otherwise AGENT_DIR
        resolve_base = self._working_directory or AGENT_DIR

        # DEBUG: Log what we're matching with tool context
        tool_context = f"{tool_name}(" if tool_name else ""
        logger.info(
            f"PERMISSION_CHECK [{tool_name or 'UNKNOWN'}]: "
            f"Matching file_path='{file_path}' against pattern='{pattern}'"
        )
        logger.info(
            f"PERMISSION_CHECK [{tool_name or 'UNKNOWN'}]: "
            f"_working_directory={self._working_directory}, "
            f"resolve_base={resolve_base}"
        )

        # Track if original path was absolute (starts with /)
        original_was_absolute = file_path.startswith("/")

        # Normalize the file path to absolute for resolution
        if not original_was_absolute:
            file_path = str(resolve_base / file_path)
        file_path_obj = Path(file_path).resolve()

        # IMPORTANT: Normalize paths within the working directory back to relative
        # This ensures bare filenames like "print_uid.py" match patterns like "./**"
        # and don't get blocked by deny patterns like "/**"
        try:
            rel_to_workspace = file_path_obj.relative_to(resolve_base.resolve())
            # Path is under workspace - use relative path for matching
            normalized_path = f"./{rel_to_workspace}"
            is_workspace_path = True
        except ValueError:
            # Path is outside workspace - keep absolute path
            normalized_path = str(file_path_obj)
            is_workspace_path = False

        # Handle special case: ** alone means "match everything"
        if pattern == "**" or pattern == "*":
            return True

        # Extract directory portion and file pattern from the pattern
        # e.g., "./skills/**/*.py" -> base="./skills", file_glob="*.py"
        # e.g., "./skills/**" -> base="./skills", file_glob=None
        dir_pattern, file_glob = self._split_glob_pattern(pattern)

        # IMPORTANT: Handle absolute deny patterns (like /**) vs relative allow patterns
        # Pattern starting with "/" should ONLY match files OUTSIDE the workspace
        # This prevents deny patterns like Write(/**) from blocking workspace writes
        if dir_pattern.startswith("/"):
            # Absolute pattern - should only match files outside workspace
            if is_workspace_path:
                # File is in workspace, absolute patterns should not match it
                # (e.g., deny pattern "/**" should not block "./print_uid.py")
                return False
            # File is outside workspace - continue with normal matching
            base_dir = Path(dir_pattern)
        elif dir_pattern.startswith("./"):
            # Relative pattern - match against workspace
            base_dir = resolve_base / dir_pattern[2:]
        elif dir_pattern.startswith("../"):
            base_dir = (resolve_base / dir_pattern).resolve()
        else:
            # Relative path without ./ (e.g., just ".") - treat as relative to resolve_base
            base_dir = resolve_base / dir_pattern

        # Check if file is under the allowed directory
        try:
            rel = file_path_obj.relative_to(base_dir.resolve())
            logger.info(
                f"PERMISSION_CHECK [{tool_name or 'UNKNOWN'}]: "
                f"File IS under base_dir. rel={rel}, "
                f"file_path_obj={file_path_obj}, base_dir={base_dir.resolve()}"
            )
        except ValueError as e:
            # file_path is not under base_dir
            logger.info(
                f"PERMISSION_CHECK [{tool_name or 'UNKNOWN'}]: "
                f"File NOT under base_dir - NO MATCH. "
                f"file_path_obj={file_path_obj}, base_dir={base_dir.resolve()}, error={e}"
            )
            return False

        # If there's a file glob pattern (like *.py), check if file matches
        if file_glob:
            result = fnmatch.fnmatch(file_path_obj.name, file_glob)
            logger.info(
                f"PERMISSION_CHECK [{tool_name or 'UNKNOWN'}]: "
                f"File glob check: {file_path_obj.name} vs {file_glob} = {result}"
            )
            return result

        logger.info(f"PERMISSION_CHECK [{tool_name or 'UNKNOWN'}]: MATCH! Returning True")
        return True

    def _split_glob_pattern(self, pattern: str) -> tuple[str, Optional[str]]:
        """
        Split a glob pattern into directory portion and file glob.

        Examples:
            "./skills/**/*.py" -> ("./skills", "*.py")
            "./skills/**" -> ("./skills", None)
            "./src/*.txt" -> ("./src", "*.txt")
            "./data" -> ("./data", None)

        Args:
            pattern: The glob pattern to split.

        Returns:
            Tuple of (directory_path, file_glob or None).
        """
        # Check if pattern ends with a file glob (e.g., *.py, *.txt)
        # Look for patterns like **/*.py or *.py at the end
        parts = pattern.split("/")

        # Check the last part for file glob
        last_part = parts[-1] if parts else ""

        # If last part is a file glob (starts with * and has extension)
        # Examples: *.py, *.txt, *.json
        if last_part.startswith("*") and "." in last_part and last_part != "**":
            file_glob = last_part
            # Remove the file glob from the path
            remaining_parts = parts[:-1]

            # Also remove trailing ** if present
            if remaining_parts and remaining_parts[-1] == "**":
                remaining_parts = remaining_parts[:-1]

            # Handle absolute paths like /**/*.py -> remaining_parts = ['']
            # Should return "/" not "" to preserve absolute path indicator
            if remaining_parts == [""]:
                dir_pattern = "/"
            else:
                dir_pattern = "/".join(remaining_parts) if remaining_parts else "."
            return dir_pattern, file_glob

        # If last part is just **, it matches everything under the directory
        if last_part == "**":
            remaining_parts = parts[:-1]
            # Handle absolute paths like /** -> remaining_parts = ['']
            # Should return "/" not "" to preserve absolute path indicator
            if remaining_parts == [""]:
                dir_pattern = "/"
            else:
                dir_pattern = "/".join(remaining_parts) if remaining_parts else "."
            return dir_pattern, None

        # No glob pattern - the whole thing is a directory path
        # Strip any trailing * that might be there
        clean_pattern = pattern.rstrip("*").rstrip("/")
        return clean_pattern if clean_pattern else ".", None

    def to_claude_settings(self) -> dict[str, Any]:
        """
        Convert configuration to Claude .claude/settings.local.json format.

        Returns:
            Dictionary suitable for .claude/settings.local.json.
        """
        config = self.load()
        return {
            "permissions": config.permissions.model_dump(mode="json"),
            "defaultMode": config.defaultMode.value,
            "hooks": config.hooks.model_dump(mode="json"),
        }

    def get_tools_by_category(
        self,
        category: ToolCategory
    ) -> list[ToolDefinition]:
        """Get all tools in a specific category."""
        return [
            tool for tool in AVAILABLE_TOOLS.values()
            if tool.category == category
        ]

    def get_tool_info(self, tool_name: str) -> Optional[ToolDefinition]:
        """Get information about a specific tool."""
        return AVAILABLE_TOOLS.get(tool_name)

    def get_allowed_patterns_for_tool(self, tool_name: str) -> list[str]:
        """
        Get all allowed patterns for a specific tool.

        Returns patterns from the allow list that match the given tool name.
        Useful for providing actionable guidance when a command is denied.

        Args:
            tool_name: Name of the tool (e.g., "Bash", "Read", "Write").

        Returns:
            List of allowed patterns for the tool (e.g., ["python ./skills/**/*.py"]).
        """
        config = self.load()
        return extract_patterns_for_tool(tool_name, config.permissions.allow)

    def get_denied_patterns_for_tool(self, tool_name: str) -> list[str]:
        """
        Get all denied patterns for a specific tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            List of denied patterns for the tool.
        """
        config = self.load()
        return extract_patterns_for_tool(tool_name, config.permissions.deny)


def create_default_permissions_file(
    target_dir: Optional[Path] = None
) -> Path:
    """
    Create a default permissions.json configuration file.

    Args:
        target_dir: Directory to create the config in.
                   Defaults to AGENT/config/.

    Returns:
        Path to created file.
    """
    if target_dir is None:
        target_dir = CONFIG_DIR

    manager = PermissionConfigManager()
    manager._config = DEFAULT_PERMISSION_CONFIG
    return manager.save(target_dir / "permissions.json")


def get_all_tool_definitions() -> dict[str, ToolDefinition]:
    """Get all available tool definitions."""
    return AVAILABLE_TOOLS.copy()


def get_safe_tools() -> list[str]:
    """Get list of safe (read-only) tools."""
    return SAFE_TOOLS.copy()


def get_dangerous_tools() -> list[str]:
    """Get list of dangerous tools that modify state."""
    return DANGEROUS_TOOLS.copy()

