"""
Prompt builder service for Ag3ntum.

Thin wrapper around PromptManager for backward-compatible API signature.
Uses the new ${VAR} prompt engine exclusively (no Jinja2).
"""
import logging
from pathlib import Path
from typing import Any, Optional

from ..core.prompt_manager import get_prompt_manager

logger = logging.getLogger(__name__)


class PromptBuilder:
    """
    Service for building system prompts.

    Delegates to PromptManager. Maintains backward-compatible API signature
    for callers that import PromptBuilder directly.
    """

    def __init__(self, prompts_dir: Optional[Path] = None):
        """
        Initialize the prompt builder.

        Args:
            prompts_dir: Ignored - PromptManager uses config.
                         Kept for backward compatibility.
        """
        # prompts_dir is ignored; PromptManager uses PROMPTS_DIR from config
        pass

    def build_system_prompt(
        self,
        role: str = "default",
        model: str = "claude-sonnet-4-20250514",
        session_id: Optional[str] = None,
        workspace_path: str = "/",
        permissions: Optional[dict[str, Any]] = None,
        enable_skills: bool = True,
        external_mounts: Optional[dict[str, Any]] = None,
        username: Optional[str] = None,
        dynamic_mounts: Optional[list] = None,
        original_path_mounts: Optional[list] = None,
    ) -> str:
        """
        Build the system prompt using the new template engine.

        Args:
            role: Role template name
            model: Model name
            session_id: Session identifier
            workspace_path: Workspace path (agent sees / as root)
            permissions: Permission profile data
            enable_skills: Whether skills are enabled
            external_mounts: External mount configuration
            username: Username for override lookup
            dynamic_mounts: Dynamic mount list for this session
            original_path_mounts: Original-path mount list

        Returns:
            Rendered system prompt string
        """
        return get_prompt_manager().build_system_prompt(
            username=username,
            role=role,
            model=model,
            session_id=session_id,
            docker_workspace_path=workspace_path,
            permissions=permissions,
            enable_skills=enable_skills,
            external_mounts=external_mounts,
            dynamic_mounts=dynamic_mounts,
            original_path_mounts=original_path_mounts,
        )

    def get_available_roles(self) -> list[str]:
        """Get list of available role templates."""
        return get_prompt_manager().get_available_roles()

    def get_template_modules(self) -> list[str]:
        """Get list of system prompt modules."""
        return get_prompt_manager().get_prompt_modules()


# Singleton instance
_prompt_builder: Optional[PromptBuilder] = None


def get_prompt_builder() -> PromptBuilder:
    """Get the singleton PromptBuilder instance."""
    global _prompt_builder
    if _prompt_builder is None:
        _prompt_builder = PromptBuilder()
    return _prompt_builder
