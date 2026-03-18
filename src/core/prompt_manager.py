"""
Prompt Manager for Ag3ntum.

Manages prompt loading, caching, user overrides, and rendering.
Provides the main interface for prompt operations.
"""
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from ..config import CONFIG_DIR, PROMPTS_DIR, USERS_DIR
from .prompt_engine import PromptTemplateEngine, PromptMetadata
from .prompt_context import build_prompt_context, PromptContext

logger = logging.getLogger(__name__)


class PromptManager:
    """
    Singleton manager for prompt loading and rendering.

    Handles:
    - Global prompt loading from prompts/
    - User override merging from users/{user}/.prompts/
    - Override allowlist enforcement
    - Hot reload via clear_cache()
    """

    _instance: Optional["PromptManager"] = None

    def __init__(self) -> None:
        self._prompts_dir = PROMPTS_DIR
        self._engine = PromptTemplateEngine(base_dir=self._prompts_dir)
        self._overrides_config = self._load_overrides_config()

    @classmethod
    def get_instance(cls) -> "PromptManager":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    def _load_overrides_config(self) -> dict[str, Any]:
        """Load prompt override allowlist configuration."""
        config_path = CONFIG_DIR / "prompt-overrides.yaml"
        if not config_path.exists():
            return {"allowed_overrides": {}}

        try:
            with config_path.open(encoding="utf-8") as f:
                return yaml.safe_load(f) or {"allowed_overrides": {}}
        except Exception as e:
            logger.error(f"Failed to load prompt-overrides.yaml: {e}")
            return {"allowed_overrides": {}}

    def _is_override_allowed(self, category: str, filename: str) -> bool:
        """Check if a user override is allowed for this prompt."""
        allowed = self._overrides_config.get("allowed_overrides", {})
        category_rules = allowed.get(category, [])

        for rule in category_rules:
            if rule == "*.md":
                return True
            if rule == filename:
                return True

        return False

    def _get_user_override_path(
        self,
        username: str,
        category: str,
        filename: str,
    ) -> Optional[Path]:
        """Get user override path if it exists and is allowed."""
        if not self._is_override_allowed(category, filename):
            return None

        user_prompts_dir = USERS_DIR / username / ".prompts"
        override_path = user_prompts_dir / category / filename

        if override_path.exists():
            return override_path

        return None

    def build_system_prompt(
        self,
        username: Optional[str] = None,
        role: str = "default",
        model: str = "claude-sonnet-4-20250514",
        session_id: Optional[str] = None,
        docker_workspace_path: str = "",
        permissions: Optional[dict[str, Any]] = None,
        enable_skills: bool = True,
        external_mounts: Optional[dict[str, Any]] = None,
        dynamic_mounts: Optional[list] = None,
        original_path_mounts: Optional[list] = None,
        ssh_profiles: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Build the complete system prompt.

        Loads and renders all prompt components, applying user overrides
        where allowed.

        Args:
            username: User for override lookup (None for global only)
            role: Role template name
            model: Model name for context
            session_id: Session identifier
            docker_workspace_path: Internal Docker path (agent sees / as root)
            permissions: Permission profile
            enable_skills: Skills enabled flag
            external_mounts: External mounts config
            dynamic_mounts: Dynamic mount list for this session
            original_path_mounts: Original-path mount list

        Returns:
            Complete rendered system prompt

        Raises:
            FileNotFoundError: If role file not found
        """
        # Load role content
        role_path = self._prompts_dir / "roles" / f"{role}.md"
        if username:
            user_role = self._get_user_override_path(username, "roles", f"{role}.md")
            if user_role:
                role_path = user_role

        if not role_path.exists():
            raise FileNotFoundError(
                f"Role file not found: {role_path}. "
                f"Create the role file in prompts/roles/{role}.md"
            )

        role_content = role_path.read_text(encoding="utf-8").strip()

        # Build context
        context = build_prompt_context(
            docker_workspace_path=docker_workspace_path,
            session_id=session_id,
            model=model,
            role_content=role_content,
            permissions=permissions,
            enable_skills=enable_skills,
            enable_external_mounts=bool(external_mounts),
            external_mounts=external_mounts,
            dynamic_mounts=dynamic_mounts,
            original_path_mounts=original_path_mounts,
            ssh_profiles=ssh_profiles,
        )

        # Load and render each system prompt component
        prompt_parts = []

        system_prompts_dir = self._prompts_dir / "system-prompts"
        if system_prompts_dir.exists():
            for prompt_file in sorted(system_prompts_dir.glob("*.md")):
                # Check for user override
                if username:
                    override = self._get_user_override_path(
                        username, "system-prompts", prompt_file.name
                    )
                    if override:
                        prompt_file = override

                rendered = self._engine.load_and_render(prompt_file, context)
                if rendered.strip():
                    prompt_parts.append(rendered)

        return "\n\n".join(prompt_parts)

    def render_subagent_prompt(
        self,
        template_path: str,
        context: Optional[PromptContext] = None,
    ) -> str:
        """
        Render a subagent prompt template.

        Args:
            template_path: Relative path within prompts dir (e.g., "subagents/general-purpose/prompt.md")
            context: Optional context; if None, builds a minimal context

        Returns:
            Rendered prompt string
        """
        full_path = self._prompts_dir / template_path
        if not full_path.exists():
            raise FileNotFoundError(f"Subagent prompt not found: {full_path}")

        if context is None:
            context = build_prompt_context(enable_skills=True)

        return self._engine.load_and_render(full_path, context)

    def get_system_reminder(
        self,
        reminder_name: str,
        context: PromptContext,
    ) -> Optional[str]:
        """
        Get a rendered system reminder.

        Args:
            reminder_name: Name of the reminder (without .md extension)
            context: PromptContext for rendering

        Returns:
            Rendered reminder or None if not found
        """
        reminder_path = self._prompts_dir / "system-reminders" / f"{reminder_name}.md"
        if not reminder_path.exists():
            return None

        return self._engine.load_and_render(reminder_path, context)

    def get_available_roles(self) -> list[str]:
        """Get list of available role templates."""
        roles_dir = self._prompts_dir / "roles"
        if not roles_dir.exists():
            return []
        return sorted([
            f.stem for f in roles_dir.glob("*.md")
            if f.is_file()
        ])

    def get_prompt_modules(self) -> list[str]:
        """Get list of system prompt modules."""
        system_dir = self._prompts_dir / "system-prompts"
        if not system_dir.exists():
            return []
        return sorted([
            f.stem for f in system_dir.glob("*.md")
            if f.is_file()
        ])

    def reload(self) -> int:
        """
        Reload all prompts by clearing cache.

        Returns:
            Number of cache entries cleared
        """
        self._overrides_config = self._load_overrides_config()
        return self._engine.clear_cache()


def get_prompt_manager() -> PromptManager:
    """Get the global PromptManager singleton."""
    return PromptManager.get_instance()
