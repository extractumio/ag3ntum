"""
Prompt builder service for Ag3ntum.

Renders system prompts from Jinja2 templates with configurable context.
Used by the API to expose system prompts to admins for debugging/inspection.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import PROMPTS_DIR

logger = logging.getLogger(__name__)


def _filter_startswith(items: list[str], prefix: str) -> list[str]:
    """Jinja2 filter to select strings starting with a prefix."""
    return [item for item in items if item.startswith(prefix)]


def _filter_contains(items: list[str], value: str) -> bool:
    """Jinja2 filter to check if a list contains a value."""
    return value in items


class PromptBuilder:
    """
    Service for building system prompts from Jinja2 templates.

    This extracts the prompt rendering logic from agent_core.py
    to make it reusable for admin debugging/inspection.
    """

    def __init__(self, prompts_dir: Optional[Path] = None):
        """
        Initialize the prompt builder.

        Args:
            prompts_dir: Directory containing prompt templates.
                        Defaults to PROMPTS_DIR from config.
        """
        self._prompts_dir = prompts_dir or PROMPTS_DIR

        # Create Jinja2 environment
        self._jinja_env = Environment(
            loader=FileSystemLoader(self._prompts_dir),
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=select_autoescape(),
        )

        # Register custom filters
        self._jinja_env.filters["select_startswith"] = _filter_startswith
        self._jinja_env.filters["contains"] = _filter_contains

    def build_system_prompt(
        self,
        role: str = "default",
        model: str = "claude-sonnet-4-20250514",
        session_id: Optional[str] = None,
        workspace_path: str = "/workspace",
        permissions: Optional[dict[str, Any]] = None,
        enable_skills: bool = True,
        external_mounts: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Build the system prompt from templates.

        Args:
            role: Role template name (loads prompts/roles/<role>.md).
            model: Model name to include in prompt.
            session_id: Optional session ID for context.
            workspace_path: Workspace path to include in prompt.
            permissions: Permission profile data for template.
            enable_skills: Whether skills are enabled.
            external_mounts: External mounts configuration.

        Returns:
            Rendered system prompt string.

        Raises:
            FileNotFoundError: If role file or system template not found.
            ValueError: If template rendering fails.
        """
        # Load role content
        role_file = self._prompts_dir / "roles" / f"{role}.md"
        if not role_file.exists():
            raise FileNotFoundError(
                f"Role file not found: {role_file}. "
                f"Create the role file in prompts/roles/{role}.md"
            )

        try:
            role_content = role_file.read_text(encoding="utf-8").strip()
        except IOError as e:
            raise ValueError(f"Failed to read role file {role_file}: {e}") from e

        # Build template context
        template_context = {
            # Environment info
            "current_date": datetime.now().strftime("%A, %B %d, %Y"),
            "model": model,
            "session_id": session_id or "preview",
            "workspace_path": workspace_path,
            "working_dir": workspace_path,
            # Role
            "role_content": role_content,
            # Permissions
            "permissions": permissions,
            # Skills
            "enable_skills": enable_skills,
            # External mounts
            "external_mounts": external_mounts or {},
        }

        # Render system prompt
        try:
            system_prompt = self._jinja_env.get_template("system.j2").render(
                **template_context
            )
        except Exception as e:
            raise ValueError(f"Failed to render system prompt template: {e}") from e

        return system_prompt

    def get_available_roles(self) -> list[str]:
        """
        Get list of available role templates.

        Returns:
            List of role names (without .md extension).
        """
        roles_dir = self._prompts_dir / "roles"
        if not roles_dir.exists():
            return []

        return sorted([
            f.stem for f in roles_dir.glob("*.md")
            if f.is_file()
        ])

    def get_template_modules(self) -> list[str]:
        """
        Get list of template modules included in system.j2.

        Returns:
            List of module names.
        """
        modules_dir = self._prompts_dir / "modules"
        if not modules_dir.exists():
            return []

        return sorted([
            f.stem for f in modules_dir.glob("*.j2")
            if f.is_file()
        ])


# Singleton instance
_prompt_builder: Optional[PromptBuilder] = None


def get_prompt_builder() -> PromptBuilder:
    """Get the singleton PromptBuilder instance."""
    global _prompt_builder
    if _prompt_builder is None:
        _prompt_builder = PromptBuilder()
    return _prompt_builder
