"""
Skills API endpoints for Ag3ntum.

Provides endpoints for:
- GET /skills - List available skills with descriptions for the current user

Skills are discovered dynamically using the shared discover_merged_skills()
function, which merges:
1. Global skills: SKILLS_DIR/.claude/skills/
2. User skills: USERS_DIR/<username>/.claude/skills/

User skills with the same name override global skills, matching the behavior
used during agent execution in agent_core._setup_workspace_skills().
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.skills import SkillManager, discover_merged_skills
from ...db.database import get_db
from ...db.models import User
from ..deps import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillInfo(BaseModel):
    """Information about a skill."""
    id: str = Field(description="Skill identifier (folder name)")
    name: str = Field(description="Display name of the skill")
    description: str = Field(description="Brief description of what the skill does")


class SkillsListResponse(BaseModel):
    """Response from GET /skills."""
    skills: list[SkillInfo] = Field(
        default_factory=list,
        description="List of available skills"
    )


@router.get("", response_model=SkillsListResponse)
async def list_skills(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SkillsListResponse:
    """
    List all available skills with their descriptions for the current user.

    Returns merged skills from global and user-specific directories.
    User skills with the same name override global skills.
    """
    try:
        # Look up the username from user_id
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Discover merged skills using shared function (global + user, with user overriding)
        skill_sources = discover_merged_skills(username=user.username)

        # Load skill metadata for each discovered skill
        skills_info = []
        for skill_name, skill_path in sorted(skill_sources.items()):
            try:
                # Create a SkillManager pointing to the skill's parent directory
                # so it can load the specific skill
                skill_manager = SkillManager(skill_path.parent)
                skill = skill_manager.load_skill(skill_name)
                if skill:
                    skills_info.append(SkillInfo(
                        id=skill_name,
                        name=skill.name or skill_name,
                        description=skill.description or "",
                    ))
                else:
                    # Skill directory exists but couldn't load metadata
                    skills_info.append(SkillInfo(
                        id=skill_name,
                        name=skill_name,
                        description="",
                    ))
            except Exception as e:
                logger.warning(f"Failed to load skill {skill_name} from {skill_path}: {e}")
                # Include skill with just the name if we can't load it
                skills_info.append(SkillInfo(
                    id=skill_name,
                    name=skill_name,
                    description="",
                ))

        logger.debug(
            f"Listed {len(skills_info)} skills for user {user.username}: "
            f"{[s.id for s in skills_info]}"
        )

        return SkillsListResponse(skills=skills_info)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list skills for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list skills: {str(e)}"
        )
