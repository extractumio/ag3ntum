"""
Prompt Context Builder for Ag3ntum.

Builds PromptContext with all variables, functions, and tool names
required for prompt template rendering.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .prompt_engine import PromptContext

logger = logging.getLogger(__name__)

# Tool name registry - maps variable names to full MCP tool names
TOOL_NAME_REGISTRY: dict[str, str] = {
    "AG3NTUM_BASH_TOOL": "mcp__ag3ntum__Bash",
    "AG3NTUM_READ_TOOL": "mcp__ag3ntum__Read",
    "AG3NTUM_WRITE_TOOL": "mcp__ag3ntum__Write",
    "AG3NTUM_EDIT_TOOL": "mcp__ag3ntum__Edit",
    "AG3NTUM_GLOB_TOOL": "mcp__ag3ntum__Glob",
    "AG3NTUM_GREP_TOOL": "mcp__ag3ntum__Grep",
    "AG3NTUM_LS_TOOL": "mcp__ag3ntum__LS",
    "AG3NTUM_READDOCUMENT_TOOL": "mcp__ag3ntum__ReadDocument",
    "AG3NTUM_ASKUSER_TOOL": "mcp__ag3ntum__AskUserQuestion",
    # Native SDK tools (not prefixed)
    "TODOWRITE_TOOL": "TodoWrite",
    "TASK_TOOL": "Task",
    "SKILL_TOOL": "Skill",
}

# Security strings - never expose implementation details
SECURITY_STRINGS: dict[str, str] = {
    "SECURITY_DISCLOSURE_RESPONSE": (
        "Sorry, cannot do that. Ask something else."
    ),
    "SECURITY_DENIAL_RESPONSE": (
        "Sorry, cannot do that. Try a different approach."
    ),
}


def build_prompt_context(
    docker_workspace_path: str = "",
    session_id: Optional[str] = None,
    model: str = "claude-sonnet-4-20250514",
    role_content: str = "",
    permissions: Optional[dict[str, Any]] = None,
    enable_skills: bool = True,
    enable_external_mounts: bool = False,
    external_mounts: Optional[dict[str, Any]] = None,
    dynamic_mounts: Optional[list] = None,
    original_path_mounts: Optional[list] = None,
) -> PromptContext:
    """
    Build a PromptContext for template rendering.

    Args:
        docker_workspace_path: Internal Docker path for path translation.
                               The agent never sees this - it sees / as root.
        session_id: Session identifier
        model: Claude model name
        role_content: Role definition content
        permissions: Permission profile dict
        enable_skills: Whether skills are enabled
        enable_external_mounts: Whether external mounts are available
        external_mounts: External mount configuration
        dynamic_mounts: Dynamic mount list for this session
        original_path_mounts: Original-path mount list

    Returns:
        PromptContext ready for template rendering
    """
    context = PromptContext()

    # Tool names
    context.tool_names = TOOL_NAME_REGISTRY.copy()

    # Environment - Agent sees /workspace as root (matches bwrap CWD/HOME)
    now = datetime.now(timezone.utc)
    context.environment = {
        "WORKSPACE_PATH": "/workspace",
        "CURRENT_WORKING_DIR": "/workspace",
        "SESSION_ID": session_id or "preview",
        "MODEL_NAME": model,
        "CURRENT_DATETIME": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "CURRENT_YEAR": str(now.year),
        "ROLE_CONTENT": role_content,
    }

    # Flags
    context.flags = {
        "ENABLE_SKILLS": enable_skills,
        "ENABLE_EXTERNAL_MOUNTS": enable_external_mounts or bool(external_mounts),
        "HAS_PERMISSIONS": permissions is not None,
        "HAS_EXTERNAL_MOUNTS": bool(
            external_mounts and (
                external_mounts.get("ro")
                or external_mounts.get("rw")
                or external_mounts.get("persistent")
                or external_mounts.get("user_ro")
                or external_mounts.get("user_rw")
            )
        ),
        "HAS_DYNAMIC_MOUNTS": bool(dynamic_mounts),
        "HAS_ORIGINAL_PATH_MOUNTS": bool(original_path_mounts),
    }

    # Strings (security)
    context.strings = SECURITY_STRINGS.copy()

    # Add permission-related strings if available
    if permissions:
        context.strings["PERMISSION_PROFILE_NAME"] = permissions.get("name", "default")
        context.strings["PERMISSION_DESCRIPTION"] = permissions.get("description", "")
        context.strings["ENABLED_TOOLS"] = ", ".join(
            permissions.get("enabled_tools", [])
        )

    # Configuration functions
    context.functions = {
        "MAX_OUTPUT_CHARS": lambda: 30000,
        "MAX_TIMEOUT_MS": lambda: 120000,
        "CUSTOM_TIMEOUT_MS": lambda: 600000,
        "MAX_FILE_LINES": lambda: 2000,
    }

    # Objects
    context.objects = {
        "EXPLORE_AGENT": {
            "agentType": "Explore",
            "description": "Codebase exploration agent",
        },
        "PLAN_AGENT": {
            "agentType": "Plan",
            "description": "Planning and design agent",
        },
    }

    # Store complex data in objects for template access
    if permissions:
        context.objects["PERMISSIONS"] = permissions
    if external_mounts:
        context.objects["EXTERNAL_MOUNTS"] = external_mounts

    # Arrays
    context.arrays = {
        "TOOL_USAGE_HINTS_ARRAY": [],
        "AVAILABLE_TOOLS_SET": [],
    }

    if dynamic_mounts:
        context.arrays["DYNAMIC_MOUNTS"] = dynamic_mounts
    if original_path_mounts:
        context.arrays["ORIGINAL_PATH_MOUNTS"] = original_path_mounts

    return context
