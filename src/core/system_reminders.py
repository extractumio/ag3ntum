"""
System Reminders for Ag3ntum.

Provides contextual reminders that are injected into conversations
based on runtime state (file changes, todo updates, etc.).
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from .prompt_manager import get_prompt_manager
from .prompt_context import build_prompt_context

logger = logging.getLogger(__name__)


class ReminderType(Enum):
    """Types of system reminders (42 total - Claude Code v2.1.39 compatible)."""

    # File Operations (6)
    FILE_MODIFIED_BY_USER_OR_LINTER = "file-modified-by-user-or-linter"
    FILE_EXISTS_BUT_EMPTY = "file-exists-but-empty"
    FILE_TRUNCATED = "file-truncated"
    FILE_SHORTER_THAN_OFFSET = "file-shorter-than-offset"
    FILE_OPENED_IN_IDE = "file-opened-in-ide"
    LINES_SELECTED_IN_IDE = "lines-selected-in-ide"

    # Task/Todo Management (5)
    TODO_LIST_CHANGED = "todo-list-changed"
    TODO_LIST_EMPTY = "todo-list-empty"
    TODOWRITE_REMINDER = "todowrite-reminder"
    TASK_STATUS = "task-status"
    TASK_TOOLS_REMINDER = "task-tools-reminder"

    # Plan Mode (7)
    PLAN_MODE_IS_ACTIVE_5_PHASE = "plan-mode-is-active-5-phase"
    PLAN_MODE_IS_ACTIVE_ITERATIVE = "plan-mode-is-active-iterative"
    PLAN_MODE_IS_ACTIVE_SUBAGENT = "plan-mode-is-active-subagent"
    PLAN_MODE_RE_ENTRY = "plan-mode-re-entry"
    EXITED_PLAN_MODE = "exited-plan-mode"
    PLAN_FILE_REFERENCE = "plan-file-reference"
    VERIFY_PLAN_REMINDER = "verify-plan-reminder"

    # Hooks (5)
    HOOK_SUCCESS = "hook-success"
    HOOK_BLOCKING_ERROR = "hook-blocking-error"
    HOOK_ADDITIONAL_CONTEXT = "hook-additional-context"
    HOOK_STOPPED_CONTINUATION = "hook-stopped-continuation"
    HOOK_STOPPED_CONTINUATION_PREFIX = "hook-stopped-continuation-prefix"

    # Token/Resource Limits (3)
    TOKEN_USAGE = "token-usage"
    OUTPUT_TOKEN_LIMIT_EXCEEDED = "output-token-limit-exceeded"
    USD_BUDGET = "usd-budget"

    # Team/Swarm (4)
    TEAM_COORDINATION = "team-coordination"
    TEAM_SHUTDOWN = "team-shutdown"
    DELEGATE_MODE_PROMPT = "delegate-mode-prompt"
    EXITED_DELEGATE_MODE = "exited-delegate-mode"

    # Session (1)
    SESSION_CONTINUATION = "session-continuation"

    # MCP Resources (2)
    MCP_RESOURCE_NO_CONTENT = "mcp-resource-no-content"
    MCP_RESOURCE_NO_DISPLAYABLE_CONTENT = "mcp-resource-no-displayable-content"

    # Memory (2)
    MEMORY_FILE_CONTENTS = "memory-file-contents"
    NESTED_MEMORY_CONTENTS = "nested-memory-contents"

    # Other (7)
    COMPACT_FILE_REFERENCE = "compact-file-reference"
    BTW_SIDE_QUESTION = "btw-side-question"
    AGENT_MENTION = "agent-mention"
    INVOKED_SKILLS = "invoked-skills"
    OUTPUT_STYLE_ACTIVE = "output-style-active"
    NEW_DIAGNOSTICS_DETECTED = "new-diagnostics-detected"
    MALWARE_ANALYSIS_AFTER_READ = "malware-analysis-after-read-tool-call"


@dataclass
class ReminderContext:
    """Context data for rendering reminders."""

    # File-related
    file_path: Optional[str] = None
    file_snippet: Optional[str] = None
    truncated_lines: Optional[int] = None
    selected_lines: Optional[str] = None

    # Todo-related
    todo_content: Optional[Any] = None

    # Token-related
    tokens_used: Optional[int] = None
    tokens_total: Optional[int] = None
    tokens_remaining: Optional[int] = None
    usd_used: Optional[float] = None
    usd_total: Optional[float] = None

    # Hook-related
    hook_name: Optional[str] = None
    hook_output: Optional[str] = None
    hook_error: Optional[str] = None
    hook_context: Optional[str] = None

    # Plan mode
    plan_file_path: Optional[str] = None

    # Team/Swarm
    team_config_path: Optional[str] = None
    task_list_path: Optional[str] = None
    restricted_tools: Optional[list[str]] = None

    # MCP Resources
    resource_uri: Optional[str] = None

    # Memory
    memory_path: Optional[str] = None
    memory_content: Optional[str] = None

    # Agent/Skills
    agent_name: Optional[str] = None
    skills_list: Optional[list[str]] = None
    style_name: Optional[str] = None
    diagnostics: Optional[list[str]] = None


def get_reminder(
    reminder_type: ReminderType,
    reminder_context: Optional[ReminderContext] = None,
    docker_workspace_path: str = "",
) -> Optional[str]:
    """
    Get a rendered system reminder.

    Args:
        reminder_type: Type of reminder to get
        reminder_context: Context data for the reminder
        docker_workspace_path: Docker path for internal translation

    Returns:
        Rendered reminder string wrapped in <system-reminder> tags,
        or None if reminder not found
    """
    manager = get_prompt_manager()

    # Build base context
    context = build_prompt_context(docker_workspace_path=docker_workspace_path)

    # Add reminder-specific context variables
    if reminder_context:
        if reminder_context.file_path:
            context.strings["FILE_PATH"] = reminder_context.file_path
        if reminder_context.file_snippet:
            context.strings["FILE_SNIPPET"] = reminder_context.file_snippet
        if reminder_context.truncated_lines is not None:
            context.strings["TRUNCATED_LINES"] = str(reminder_context.truncated_lines)
        if reminder_context.selected_lines:
            context.strings["SELECTED_LINES"] = reminder_context.selected_lines
        if reminder_context.tokens_used is not None:
            context.strings["TOKENS_USED"] = str(reminder_context.tokens_used)
        if reminder_context.tokens_total is not None:
            context.strings["TOKENS_TOTAL"] = str(reminder_context.tokens_total)
        if reminder_context.tokens_remaining is not None:
            context.strings["TOKENS_REMAINING"] = str(reminder_context.tokens_remaining)
        if reminder_context.usd_used is not None:
            context.strings["USD_USED"] = str(reminder_context.usd_used)
        if reminder_context.usd_total is not None:
            context.strings["USD_TOTAL"] = str(reminder_context.usd_total)
        if reminder_context.hook_name:
            context.strings["HOOK_NAME"] = reminder_context.hook_name
        if reminder_context.hook_output:
            context.strings["HOOK_OUTPUT"] = reminder_context.hook_output
        if reminder_context.hook_error:
            context.strings["HOOK_ERROR"] = reminder_context.hook_error
        if reminder_context.hook_context:
            context.strings["HOOK_CONTEXT"] = reminder_context.hook_context
        if reminder_context.plan_file_path:
            context.strings["PLAN_FILE_PATH"] = reminder_context.plan_file_path
        if reminder_context.team_config_path:
            context.strings["TEAM_CONFIG_PATH"] = reminder_context.team_config_path
        if reminder_context.task_list_path:
            context.strings["TASK_LIST_PATH"] = reminder_context.task_list_path
        if reminder_context.resource_uri:
            context.strings["RESOURCE_URI"] = reminder_context.resource_uri
        if reminder_context.memory_path:
            context.strings["MEMORY_PATH"] = reminder_context.memory_path
        if reminder_context.memory_content:
            context.strings["MEMORY_CONTENT"] = reminder_context.memory_content
        if reminder_context.agent_name:
            context.strings["AGENT_NAME"] = reminder_context.agent_name
        if reminder_context.style_name:
            context.strings["STYLE_NAME"] = reminder_context.style_name
        if reminder_context.restricted_tools:
            context.arrays["RESTRICTED_TOOLS"] = reminder_context.restricted_tools
        if reminder_context.skills_list:
            context.arrays["SKILLS_LIST"] = reminder_context.skills_list
        if reminder_context.diagnostics:
            context.arrays["DIAGNOSTICS"] = reminder_context.diagnostics

    # Get and render reminder
    reminder_content = manager.get_system_reminder(reminder_type.value, context)

    if not reminder_content:
        return None

    # Wrap in system-reminder tags
    return f"<system-reminder>\n{reminder_content.strip()}\n</system-reminder>"
