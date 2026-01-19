"""
Subagent Manager for Ag3ntum.

This module provides the SubagentManager singleton that loads and manages
subagent configurations, providing override definitions for Claude Code's
built-in subagents.

SINGLETON ARCHITECTURE
======================
SubagentManager is implemented as a singleton that is initialized ONCE at
API startup. The same instance is shared across ALL users and ALL sessions.

Why Singleton?
--------------
1. **Performance**: Jinja2 templates are rendered once at startup, not on
   every request. This avoids repeated file I/O and template compilation.

2. **Consistency**: All users receive the same security-hardened subagent
   definitions. There's no per-user variation in security constraints.

3. **Memory Efficiency**: Rendered prompts are cached in memory and reused
   across all sessions rather than being regenerated.

4. **Atomic Configuration**: Changes to subagents.yaml require a service
   restart, ensuring all sessions see the same configuration.

Global Accessibility
--------------------
The singleton is accessible via:
    from src.core.subagent_manager import get_subagent_manager
    manager = get_subagent_manager()

Or directly:
    from src.core.subagent_manager import SubagentManager
    manager = SubagentManager.get_instance()

FUTURE EXTENSION: Per-User Subagents
------------------------------------
The current implementation provides GLOBAL subagent definitions only.
A future enhancement may add per-user customization by:

1. Loading user-specific overrides from /users/{username}/.claude/agents/
2. Merging user overrides with global definitions at session creation
3. Caching merged definitions per-user (with TTL or invalidation)

When this is implemented, the SubagentManager would expose:
    manager.get_agents_dict(user_id=None)  # Global (current behavior)
    manager.get_agents_dict(user_id="alice")  # User-specific merge

For now, all users share the same subagent definitions loaded from
config/subagents.yaml.

Usage Example
-------------
    # At startup (in main.py lifespan)
    manager = get_subagent_manager()
    logger.info(f"Loaded {manager.agent_count} subagents")

    # In agent_core.py _build_options()
    agents = get_subagent_manager().get_agents_dict()
    options = ClaudeAgentOptions(..., agents=agents)
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Import SDK's AgentDefinition for proper typing
from claude_agent_sdk.types import AgentDefinition as SDKAgentDefinition

from ..config import CONFIG_DIR, PROMPTS_DIR

logger = logging.getLogger(__name__)


@dataclass
class SubagentDefinition:
    """
    Definition for a subagent override.

    Represents a single subagent configuration that will be passed to the
    Claude Agent SDK to override built-in subagents.

    Attributes:
        name: Unique identifier for the subagent (e.g., "general-purpose")
        enabled: Whether this subagent is active. Disabled subagents return
                 a "not available" message when invoked.
        description: Short description shown to the main agent to help it
                     decide when to use this subagent.
        prompt: The full system prompt for this subagent (rendered from template).
        tools: List of tool names this subagent can use.
    """
    name: str
    enabled: bool
    description: str
    prompt: str
    tools: list[str]

    def to_sdk_definition(self) -> SDKAgentDefinition:
        """
        Convert to SDK-compatible AgentDefinition dataclass.

        Returns:
            SDKAgentDefinition instance suitable for passing to
            ClaudeAgentOptions.agents parameter.
        """
        if not self.enabled:
            # Disabled agent: minimal prompt that reports unavailability
            return SDKAgentDefinition(
                description=self.description,
                prompt=self.prompt,
                tools=[],
            )
        return SDKAgentDefinition(
            description=self.description,
            prompt=self.prompt,
            tools=self.tools,
        )


class SubagentManager:
    """
    Singleton manager for subagent configurations and prompt rendering.

    SINGLETON PATTERN
    =================
    This class uses the singleton pattern to ensure only one instance exists
    throughout the application lifecycle. The instance is created on first
    access via get_instance() and reused for all subsequent calls.

    THREAD SAFETY NOTE
    ------------------
    The singleton is initialized at startup before any requests are processed.
    After initialization, the instance is read-only (no mutations), making it
    inherently thread-safe for concurrent access during request handling.

    LIFECYCLE
    ---------
    1. First call to get_instance() creates the singleton
    2. load() is called automatically to read config and render templates
    3. All subsequent get_instance() calls return the same instance
    4. Instance persists until process termination

    Attributes:
        _instance: Class-level singleton instance (None until first access)
        _config_path: Path to subagents.yaml configuration file
        _jinja_env: Jinja2 environment for template rendering
        _definitions: Dict mapping subagent names to their definitions
        _loaded: Flag indicating whether configuration has been loaded
    """

    # Class-level singleton instance
    _instance: Optional["SubagentManager"] = None

    def __init__(self) -> None:
        """
        Initialize the SubagentManager.

        NOTE: Do not call this directly. Use get_instance() or
        get_subagent_manager() to access the singleton.
        """
        self._config_path = CONFIG_DIR / "subagents.yaml"
        self._jinja_env = Environment(
            loader=FileSystemLoader(PROMPTS_DIR),
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=select_autoescape(),
        )
        self._definitions: dict[str, SubagentDefinition] = {}
        self._loaded = False

    @classmethod
    def get_instance(cls) -> "SubagentManager":
        """
        Get or create the singleton instance.

        This is the primary way to access the SubagentManager. The first call
        creates the instance and loads configuration; subsequent calls return
        the cached instance.

        Returns:
            The global SubagentManager singleton instance.

        Example:
            manager = SubagentManager.get_instance()
            agents = manager.get_agents_dict()
        """
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.load()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """
        Reset the singleton instance (for testing only).

        WARNING: This should only be used in test fixtures to reset state
        between tests. Do not call in production code.
        """
        cls._instance = None

    @property
    def agent_count(self) -> int:
        """Get the number of loaded subagent definitions."""
        return len(self._definitions)

    @property
    def enabled_count(self) -> int:
        """Get the number of enabled subagents."""
        return sum(1 for d in self._definitions.values() if d.enabled)

    @property
    def disabled_count(self) -> int:
        """Get the number of disabled subagents."""
        return sum(1 for d in self._definitions.values() if not d.enabled)

    def load(self) -> None:
        """
        Load subagent configurations from YAML and render prompt templates.

        This method:
        1. Reads config/subagents.yaml
        2. For each subagent, renders its Jinja2 prompt template
        3. Stores the rendered SubagentDefinition for later use

        Called automatically by get_instance() on first access. Can be called
        manually to reload configuration (requires restart for changes).

        Raises:
            No exceptions are raised. Errors are logged and the manager
            continues with whatever agents could be loaded successfully.
        """
        if not self._config_path.exists():
            logger.info(
                f"No subagents.yaml found at {self._config_path}, "
                "using Claude Code defaults"
            )
            self._loaded = True
            return

        try:
            with self._config_path.open(encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to load subagents.yaml: {e}")
            self._loaded = True
            return

        subagents_config = config.get("subagents", {})

        for name, agent_config in subagents_config.items():
            self._load_subagent(name, agent_config)

        logger.info(
            f"SubagentManager: Loaded {self.agent_count} subagents "
            f"({self.enabled_count} enabled, {self.disabled_count} disabled)"
        )
        self._loaded = True

    def _load_subagent(self, name: str, config: dict[str, Any]) -> None:
        """
        Load a single subagent definition from configuration.

        Args:
            name: The subagent identifier (e.g., "general-purpose")
            config: Dict with keys: enabled, description, prompt_template/prompt, tools
        """
        enabled = config.get("enabled", True)
        description = config.get("description", f"Subagent: {name}")
        tools = config.get("tools", [])

        # Render prompt from template or use direct prompt string
        prompt_template = config.get("prompt_template")
        if prompt_template:
            try:
                template = self._jinja_env.get_template(prompt_template)
                # Render with minimal context (subagents don't get full session context)
                prompt = template.render(
                    enable_skills=True,  # Enable skills section in template
                )
            except Exception as e:
                logger.error(
                    f"Failed to render template '{prompt_template}' for "
                    f"subagent '{name}': {e}"
                )
                # Fall back to direct prompt or error message
                prompt = config.get(
                    "prompt",
                    f"Error loading subagent template: {e}"
                )
        else:
            prompt = config.get(
                "prompt",
                "This agent is not available in this environment."
            )

        self._definitions[name] = SubagentDefinition(
            name=name,
            enabled=enabled,
            description=description,
            prompt=prompt,
            tools=tools,
        )

        status = "enabled" if enabled else "DISABLED"
        logger.debug(f"Subagent '{name}': {status}, tools={tools}")

    def get_agents_dict(self) -> dict[str, SDKAgentDefinition]:
        """
        Get all subagent definitions as SDK-compatible AgentDefinition objects.

        This is the primary method used by agent_core.py to get subagent
        overrides for ClaudeAgentOptions.agents parameter.

        Returns:
            Dict mapping agent names to SDKAgentDefinition instances.
            Empty dict if no subagents are configured.

        Example:
            agents = manager.get_agents_dict()
            # Returns:
            # {
            #     "general-purpose": SDKAgentDefinition(
            #         description="...",
            #         prompt="...",
            #         tools=["Read", "Glob", ...]
            #     ),
            #     "claude-code-guide": SDKAgentDefinition(
            #         description="Disabled - ...",
            #         prompt="This agent is not available...",
            #         tools=[]
            #     )
            # }
        """
        return {
            name: defn.to_sdk_definition()
            for name, defn in self._definitions.items()
        }

    def get_agent(self, name: str) -> Optional[SubagentDefinition]:
        """
        Get a specific subagent definition by name.

        Args:
            name: The subagent identifier (e.g., "general-purpose")

        Returns:
            SubagentDefinition if found, None otherwise.
        """
        return self._definitions.get(name)

    def is_enabled(self, name: str) -> bool:
        """
        Check if a subagent is enabled.

        Args:
            name: The subagent identifier

        Returns:
            True if the subagent is enabled or not defined (defaults to enabled).
            False if explicitly disabled in configuration.
        """
        defn = self._definitions.get(name)
        return defn.enabled if defn else True  # Default to enabled if not defined

    def list_agents(self) -> list[str]:
        """
        List all configured subagent names.

        Returns:
            List of subagent identifiers.
        """
        return list(self._definitions.keys())

    def list_enabled_agents(self) -> list[str]:
        """
        List names of enabled subagents only.

        Returns:
            List of enabled subagent identifiers.
        """
        return [name for name, defn in self._definitions.items() if defn.enabled]

    def list_disabled_agents(self) -> list[str]:
        """
        List names of disabled subagents only.

        Returns:
            List of disabled subagent identifiers.
        """
        return [name for name, defn in self._definitions.items() if not defn.enabled]


def get_subagent_manager() -> SubagentManager:
    """
    Get the global SubagentManager singleton instance.

    Convenience function that wraps SubagentManager.get_instance().
    This is the recommended way to access the subagent manager throughout
    the codebase.

    Returns:
        The global SubagentManager singleton instance.

    Example:
        from src.core.subagent_manager import get_subagent_manager

        # In agent_core.py
        agents = get_subagent_manager().get_agents_dict()

        # In main.py startup
        manager = get_subagent_manager()
        logger.info(f"Loaded {manager.agent_count} subagents")
    """
    return SubagentManager.get_instance()
