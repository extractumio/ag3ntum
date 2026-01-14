"""
Core modules for Ag3ntum.

This package contains all the core functionality:
- agent.py: CLI entry point
- agent_core.py: Core agent implementation
- cli_common.py: Shared CLI argument parsing utilities
- constants.py: Centralized constants (colors, log formats, etc.)
- conversation.py: Multi-turn conversation sessions
- exceptions.py: Custom exceptions
- hooks.py: SDK hooks implementation
- logging_config.py: Unified logging configuration
- output.py: Output formatting (result boxes, session tables)
- permissions.py: Permission management
- permission_config.py: Centralized permission configuration
- schemas.py: Pydantic data models
- sessions.py: Session management
- skills.py: Skills loading and execution
- skill_tools.py: MCP tool wrappers for script-based skills
- tasks.py: Task loading and management
- tracer.py: Execution tracing with console output
- trace_processor.py: SDK message to tracer bridge
"""
from .agent_core import ClaudeAgent, run_agent
from .cli_common import (
    add_task_arguments,
    add_directory_arguments,
    add_session_arguments,
    add_config_override_arguments,
    add_permission_arguments,
    add_output_arguments,
    add_logging_arguments,
    add_http_arguments,
    add_cli_arguments,
    create_common_parser,
    parse_set_overrides,
)
from .constants import (
    AnsiColors,
    BoxChars,
    StatusIcons,
    LOG_FORMAT_FILE,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
)
from .conversation import (
    ConversationSession,
    ConversationTurn,
    ConversationMetrics,
)
from .exceptions import AgentError, TaskError, SkillError
from .logging_config import (
    setup_file_logging,
    setup_dual_logging,
    setup_cli_logging,
    setup_http_logging,
    setup_backend_logging,
)
from .output import (
    format_result,
    print_output_box,
    print_result_box,
    print_sessions_table,
    print_status,
    get_terminal_width,
    wrap_text,
)
from .hooks import (
    HooksManager,
    HookResult,
    ToolUsageRecord,
    create_permission_hook,
    create_audit_hook,
    create_prompt_enhancement_hook,
    create_stop_hook,
    create_subagent_stop_hook,
)
from .permissions import (
    PermissionManager,
    PermissionDenial,
    PermissionDenialTracker,
    load_permissions_from_config,
    create_permission_callback,
    create_permission_hooks,
)
from .command_security import (
    CommandSecurityFilter,
    SecurityRule,
    SecurityCheckResult,
    get_command_security_filter,
    check_command_security,
)
# Security is enforced by multiple layers:
# - CommandSecurityFilter: Pre-execution regex filtering (dangerous commands)
# - Ag3ntumBash's bwrap sandbox (OS-level filesystem isolation)
# - Ag3ntumPathValidator (for Python file tools)
from .schemas import (
    AgentConfig,
    AgentResult,
    Checkpoint,
    CheckpointType,
    TaskStatus,
    SessionInfo,
    TokenUsage,
    LLMMetrics,
    get_model_context_size,
    MODEL_CONTEXT_SIZES,
)
from .sessions import SessionManager
from .skills import (
    SkillManager,
    SkillType,
    SkillIntegration,
    categorize_skills,
    get_instruction_skills_prompt,
)
from .skill_tools import (
    SkillToolsManager,
    SkillToolDefinition,
    SkillExecutionResult,
    create_skills_mcp_server,
    execute_skill_sync,
)
from .tasks import load_task
from .tracer import (
    ExecutionTracer,
    TracerBase,
    QuietTracer,
    NullTracer,
    Color,
    Symbol,
)
from .trace_processor import (
    TraceProcessor,
    create_trace_hooks,
    create_stderr_callback,
)

__all__ = [
    # Agent
    "ClaudeAgent",
    "run_agent",
    # CLI Common
    "add_task_arguments",
    "add_directory_arguments",
    "add_session_arguments",
    "add_config_override_arguments",
    "add_permission_arguments",
    "add_output_arguments",
    "add_logging_arguments",
    "add_http_arguments",
    "add_cli_arguments",
    "create_common_parser",
    "parse_set_overrides",
    # Constants
    "AnsiColors",
    "BoxChars",
    "StatusIcons",
    "LOG_FORMAT_FILE",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
    # Conversation
    "ConversationSession",
    "ConversationTurn",
    "ConversationMetrics",
    # Exceptions
    "AgentError",
    "TaskError",
    "SkillError",
    # Logging
    "setup_file_logging",
    "setup_dual_logging",
    "setup_cli_logging",
    "setup_http_logging",
    "setup_backend_logging",
    # Output
    "format_result",
    "print_output_box",
    "print_result_box",
    "print_sessions_table",
    "print_status",
    "get_terminal_width",
    "wrap_text",
    # Hooks
    "HooksManager",
    "HookResult",
    "ToolUsageRecord",
    "create_permission_hook",
    "create_audit_hook",
    "create_prompt_enhancement_hook",
    "create_stop_hook",
    "create_subagent_stop_hook",
    # Permissions
    "PermissionManager",
    "PermissionDenial",
    "PermissionDenialTracker",
    "load_permissions_from_config",
    "create_permission_callback",
    "create_permission_hooks",
    # Command Security
    "CommandSecurityFilter",
    "SecurityRule",
    "SecurityCheckResult",
    "get_command_security_filter",
    "check_command_security",
    # Schemas
    "AgentConfig",
    "AgentResult",
    "Checkpoint",
    "CheckpointType",
    "TaskStatus",
    "SessionInfo",
    "TokenUsage",
    "LLMMetrics",
    "get_model_context_size",
    "MODEL_CONTEXT_SIZES",
    # Managers
    "SessionManager",
    "SkillManager",
    # Skills
    "SkillType",
    "SkillIntegration",
    "categorize_skills",
    "get_instruction_skills_prompt",
    # Skill Tools
    "SkillToolsManager",
    "SkillToolDefinition",
    "SkillExecutionResult",
    "create_skills_mcp_server",
    "execute_skill_sync",
    # Tasks
    "load_task",
    # Tracing
    "ExecutionTracer",
    "TracerBase",
    "QuietTracer",
    "NullTracer",
    "Color",
    "Symbol",
    "TraceProcessor",
    "create_trace_hooks",
    "create_stderr_callback",
]
