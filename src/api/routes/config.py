"""
Configuration endpoints for Ag3ntum API.

Provides admin-only endpoints for inspecting and managing system configuration.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..deps import require_admin
from ...core.prompt_manager import get_prompt_manager

router = APIRouter(prefix="/config", tags=["config"])


class SystemPromptResponse(BaseModel):
    """Response from GET /config/system_prompt."""

    prompt: str = Field(description="The rendered system prompt")
    role: str = Field(description="Role template used")
    model: str = Field(description="Model name in prompt context")
    available_roles: list[str] = Field(description="Available role templates")
    prompt_modules: list[str] = Field(description="Prompt modules included")


class ReloadResponse(BaseModel):
    """Response from POST /config/prompts/reload."""

    status: str = Field(description="Reload status")
    cache_entries_cleared: int = Field(description="Number of cache entries cleared")


@router.get("/system_prompt", response_model=SystemPromptResponse)
async def get_system_prompt(
    role: str = Query(
        default="default",
        description="Role template to use (from prompts/roles/<role>.md)"
    ),
    model: str = Query(
        default="claude-sonnet-4-20250514",
        description="Model name to include in prompt context"
    ),
    enable_skills: bool = Query(
        default=True,
        description="Whether to enable skills section in prompt"
    ),
    _admin=Depends(require_admin),  # Require admin access
) -> SystemPromptResponse:
    """
    Get the current system prompt as it would be sent to Claude.

    **Admin only** - requires admin role.

    This endpoint renders the system prompt with the specified parameters,
    allowing admins to inspect what the agent sees as its instructions.

    The response includes:
    - The fully rendered system prompt
    - Metadata about the role and model used
    - Available roles and prompt modules for reference
    """
    manager = get_prompt_manager()

    # Build the system prompt
    prompt = manager.build_system_prompt(
        role=role,
        model=model,
        session_id=None,  # Preview mode
        docker_workspace_path="/workspace",
        permissions=None,  # No specific permissions for preview
        enable_skills=enable_skills,
        external_mounts=None,  # No mounts for preview
    )

    return SystemPromptResponse(
        prompt=prompt,
        role=role,
        model=model,
        available_roles=manager.get_available_roles(),
        prompt_modules=manager.get_prompt_modules(),
    )


@router.post("/prompts/reload", response_model=ReloadResponse)
async def reload_prompts(
    _admin=Depends(require_admin),  # Require admin access
) -> ReloadResponse:
    """
    Hot-reload prompt templates and override configuration.

    **Admin only** - requires admin role.

    Clears the prompt template cache and reloads the override allowlist
    from config/prompt-overrides.yaml. New sessions will use the updated
    prompts. Existing sessions are not affected.
    """
    manager = get_prompt_manager()
    cleared = manager.reload()

    return ReloadResponse(
        status="ok",
        cache_entries_cleared=cleared,
    )
