"""
Configuration endpoints for Ag3ntum API.

Provides admin-only endpoints for inspecting system configuration.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..deps import require_admin
from ...services.prompt_builder import get_prompt_builder

router = APIRouter(prefix="/config", tags=["config"])


class SystemPromptResponse(BaseModel):
    """Response from GET /config/system_prompt."""

    prompt: str = Field(description="The rendered system prompt")
    role: str = Field(description="Role template used")
    model: str = Field(description="Model name in prompt context")
    available_roles: list[str] = Field(description="Available role templates")
    template_modules: list[str] = Field(description="Template modules included")


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

    This endpoint renders the system prompt from Jinja2 templates with
    the specified parameters, allowing admins to inspect what the agent
    sees as its instructions.

    The response includes:
    - The fully rendered system prompt
    - Metadata about the role and model used
    - Available roles and template modules for reference
    """
    prompt_builder = get_prompt_builder()

    # Build the system prompt
    prompt = prompt_builder.build_system_prompt(
        role=role,
        model=model,
        session_id=None,  # Preview mode
        workspace_path="/workspace",
        permissions=None,  # No specific permissions for preview
        enable_skills=enable_skills,
        external_mounts=None,  # No mounts for preview
    )

    return SystemPromptResponse(
        prompt=prompt,
        role=role,
        model=model,
        available_roles=prompt_builder.get_available_roles(),
        template_modules=prompt_builder.get_template_modules(),
    )
