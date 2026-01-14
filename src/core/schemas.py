"""
Data models for Ag3ntum.

Contains Pydantic models for agent configuration, results, metrics, and checkpoints.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .tracer import TracerBase


class CheckpointType(StrEnum):
    """Type of checkpoint for categorization."""
    AUTO = "AUTO"       # Automatically created after tool results
    MANUAL = "MANUAL"   # Manually created via create_checkpoint()
    TURN = "TURN"       # Created at turn boundaries


class Checkpoint(BaseModel):
    """
    Represents a point in the conversation that can be rewound to.

    Checkpoints track file system state at specific user message UUIDs,
    enabling rollback of file changes made by the agent.
    """
    uuid: str = Field(
        description="User message UUID from the SDK (used for rewind_files)"
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="Timestamp when the checkpoint was created"
    )
    checkpoint_type: CheckpointType = Field(
        default=CheckpointType.AUTO,
        description="Type of checkpoint (AUTO, MANUAL, TURN)"
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional description of what was done before this checkpoint"
    )
    turn_number: Optional[int] = Field(
        default=None,
        description="Turn number when this checkpoint was created"
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="Name of the tool that triggered this checkpoint (for AUTO type)"
    )
    file_path: Optional[str] = Field(
        default=None,
        description="File path that was modified (for file-related checkpoints)"
    )

    def to_summary(self) -> str:
        """
        Generate a human-readable summary of this checkpoint.

        Returns:
            Summary string describing the checkpoint.
        """
        parts = [f"[{self.checkpoint_type}]"]
        if self.turn_number is not None:
            parts.append(f"Turn {self.turn_number}")
        if self.tool_name:
            parts.append(f"{self.tool_name}")
        if self.file_path:
            parts.append(f"-> {self.file_path}")
        if self.description:
            parts.append(f": {self.description}")
        return " ".join(parts)


class TaskStatus(StrEnum):
    """Status of task execution."""
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    ERROR = "ERROR"


# Model context window sizes (in tokens)
# These are the maximum context window sizes for each model
MODEL_CONTEXT_SIZES: dict[str, int] = {
    # Claude 4.5 models (latest)
    "claude-haiku-4-5-20251001": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-opus-4-5-20251101": 200_000,
    # Claude 4.x models (legacy)
    "claude-opus-4-1-20250805": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4-20250514": 200_000,
    # Default fallback
    "default": 200_000,
}


def get_model_context_size(model: str) -> int:
    """
    Get the context window size for a model.

    Args:
        model: The model name/identifier.

    Returns:
        The context window size in tokens.
    """
    return MODEL_CONTEXT_SIZES.get(model, MODEL_CONTEXT_SIZES["default"])


class TokenUsage(BaseModel):
    """
    Token usage statistics.

    Tracks input and output token counts, including cached tokens.
    """
    input_tokens: int = Field(
        default=0,
        description="Number of input tokens processed"
    )
    output_tokens: int = Field(
        default=0,
        description="Number of output tokens generated"
    )
    cache_creation_input_tokens: int = Field(
        default=0,
        description="Number of tokens used to create cache"
    )
    cache_read_input_tokens: int = Field(
        default=0,
        description="Number of tokens read from cache"
    )

    @property
    def total_input_tokens(self) -> int:
        """
        Total input tokens including cache operations.
        """
        return (
            self.input_tokens +
            self.cache_creation_input_tokens +
            self.cache_read_input_tokens
        )

    @property
    def total_tokens(self) -> int:
        """
        Total tokens (input + output).
        """
        return self.total_input_tokens + self.output_tokens

    def add(self, other: "TokenUsage") -> "TokenUsage":
        """
        Add another TokenUsage to this one (for accumulating stats).

        Args:
            other: Another TokenUsage instance to add.

        Returns:
            A new TokenUsage with combined values.
        """
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens +
                other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens +
                other.cache_read_input_tokens
            ),
        )

    @classmethod
    def from_sdk_usage(cls, usage: Optional[dict]) -> "TokenUsage":
        """
        Create TokenUsage from SDK usage dictionary.

        Args:
            usage: The usage dict from ResultMessage.

        Returns:
            TokenUsage instance.
        """
        if not usage:
            return cls()

        return cls(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_input_tokens=usage.get(
                "cache_creation_input_tokens", 0
            ),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        )


class LLMMetrics(BaseModel):
    """
    Metrics from LLM execution.

    Contains information about the model used, execution time,
    number of turns, cost, and token usage.
    """
    model: str
    duration_ms: int
    num_turns: int
    session_id: str
    total_cost_usd: Optional[float] = None
    usage: Optional[TokenUsage] = Field(
        default=None,
        description="Token usage statistics"
    )


class AgentConfig(BaseModel):
    """
    Agent configuration.

    Defines the model, execution limits, paths, and permissions.
    Required fields are loaded from agent.yaml.
    Tool-related fields come from permission profiles.

    Usage:
        from config import AgentConfigLoader
        loader = AgentConfigLoader()
        config_data = loader.get_config()
        config = AgentConfig(**config_data)
    """
    # Required fields (loaded from agent.yaml)
    model: str = Field(
        ...,
        description="The Claude model to use"
    )
    max_turns: int = Field(
        ...,
        description="Maximum number of turns for the agent"
    )
    timeout_seconds: int = Field(
        ...,
        description="Timeout in seconds"
    )
    enable_skills: bool = Field(
        ...,
        description="Enable custom skills from the skills/ folder"
    )
    enable_file_checkpointing: bool = Field(
        ...,
        description="Enable file change tracking for checkpoint/rewind"
    )
    permission_mode: Optional[str] = Field(
        None,
        description="Permission mode: null (use can_use_tool), default, acceptEdits, plan, bypassPermissions. MUST be null for security."
    )
    role: str = Field(
        ...,
        description="Role template name (file in prompts/roles/<role>.md)"
    )

    # Fields from permission profiles (optional, set at runtime)
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="List of tools the agent can use (from permission profile)"
    )
    auto_checkpoint_tools: list[str] = Field(
        default_factory=list,
        description="Tools that trigger automatic checkpoint creation (from permission profile)"
    )

    # Optional fields (runtime or CLI overrides)
    skills_dir: Optional[str] = Field(
        default=None,
        description="Custom skills directory (defaults to AGENT/skills)"
    )
    working_dir: Optional[str] = Field(
        default=None,
        description="Working directory for the agent"
    )
    additional_dirs: list[str] = Field(
        default_factory=list,
        description="Additional directories the agent can access"
    )
    permissions_config: Optional[str] = Field(
        default=None,
        description="Path to legacy permissions.json configuration file"
    )

    # SDK-specific optional fields
    max_buffer_size: Optional[int] = Field(
        default=None,
        description="Maximum buffer size for streaming"
    )
    output_format: Optional[str] = Field(
        default=None,
        description="Output format: text, json, stream-json"
    )
    include_partial_messages: bool = Field(
        default=False,
        description="Include partial/incomplete messages in output"
    )


class SessionInfo(BaseModel):
    """
    Session information.

    Contains session ID, timestamps, state, metrics, cumulative
    token/cost statistics, and checkpoint history.
    """
    session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    working_dir: str
    status: TaskStatus = TaskStatus.PARTIAL
    resume_id: Optional[str] = Field(
        default=None,
        description="Claude session ID for resuming"
    )
    num_turns: Optional[int] = Field(
        default=None,
        description="Number of turns in the session (current run only)"
    )
    duration_ms: Optional[int] = Field(
        default=None,
        description="Duration of the session in milliseconds (current run only)"
    )
    total_cost_usd: Optional[float] = Field(
        default=None,
        description="Total cost of the session in USD (current run only)"
    )
    # Cumulative statistics (persist across session resumptions)
    cumulative_turns: int = Field(
        default=0,
        description="Cumulative number of turns across all runs"
    )
    cumulative_duration_ms: int = Field(
        default=0,
        description="Cumulative duration in milliseconds across all runs"
    )
    cumulative_cost_usd: float = Field(
        default=0.0,
        description="Cumulative cost in USD across all runs"
    )
    cumulative_usage: Optional[TokenUsage] = Field(
        default=None,
        description="Cumulative token usage across all runs"
    )
    # Model information for context load tracking
    model: Optional[str] = Field(
        default=None,
        description="The model used in this session"
    )
    # Fork tracking
    parent_session_id: Optional[str] = Field(
        default=None,
        description="Parent session ID if this session was forked"
    )
    # File checkpointing
    checkpoints: list[Checkpoint] = Field(
        default_factory=list,
        description="""
List of checkpoints for file state tracking.
Each checkpoint represents a point that can be rewound to."""
    )
    file_checkpointing_enabled: bool = Field(
        default=False,
        description="Whether file checkpointing is enabled for this session"
    )


class AgentResult(BaseModel):
    """
    Agent execution result.

    Contains the status, output, error, comments, result_files, and metrics.
    """
    status: TaskStatus
    output: Optional[str] = None
    error: Optional[str] = None
    comments: Optional[str] = None
    result_files: list[str] = Field(default_factory=list)
    metrics: Optional[LLMMetrics] = None
    session_info: Optional[SessionInfo] = None


@dataclass
class TaskExecutionParams:
    """
    Unified parameters for agent task execution.

    Used by both CLI and HTTP entry points to invoke execute_agent_task().
    This provides a single interface for running agent tasks regardless of
    the entry point, ensuring consistent behavior across CLI and API.

    Attributes:
        task: The task description/prompt for the agent.
        working_dir: Working directory for the agent (defaults to cwd).
        session_id: Pre-generated session ID (for HTTP, allows client to know ID upfront).
        resume_session_id: Session ID to resume from.
        fork_session: Whether to fork the resumed session instead of continuing it.
        model: Model override (from agent.yaml if not specified).
        max_turns: Max turns override.
        timeout_seconds: Timeout override.
        permission_mode: Permission mode override.
        profile_path: Path to permission profile YAML.
        additional_dirs: Additional directories the agent can access.
        tracer: Tracer instance for execution output (CLI: ExecutionTracer, HTTP: BackendConsoleTracer).
    """
    task: str
    working_dir: Optional[Path] = None
    session_id: Optional[str] = None
    resume_session_id: Optional[str] = None
    fork_session: bool = False

    # Config overrides (from agent.yaml if not specified)
    model: Optional[str] = None
    max_turns: Optional[int] = None
    timeout_seconds: Optional[int] = None
    permission_mode: Optional[str] = None
    profile_path: Optional[Path] = None
    role: Optional[str] = None
    additional_dirs: list[str] = field(default_factory=list)
    enable_skills: Optional[bool] = None
    enable_file_checkpointing: Optional[bool] = None
    max_buffer_size: Optional[int] = None
    output_format: Optional[str] = None
    include_partial_messages: Optional[bool] = None

    # User isolation (for OS-level process isolation)
    user_id: Optional[str] = None
    username: Optional[str] = None  # Username for sandbox mount paths
    linux_uid: Optional[int] = None
    linux_gid: Optional[int] = None
    anthropic_api_key: Optional[str] = None
    sessions_dir: Optional[Path] = None  # Per-user sessions directory

    # Tracer (CLI: ExecutionTracer, HTTP: BackendConsoleTracer)
    tracer: Optional[Any] = None  # Type: TracerBase (Any to avoid circular import)
