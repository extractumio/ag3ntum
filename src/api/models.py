"""
Pydantic request/response models for Ag3ntum API.

Defines the API contract for all endpoints.
All parameters from CLI are available via HTTP request.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .waf_filter import truncate_text_content


class TokenResponse(BaseModel):
    """Response from POST /auth/login."""
    access_token: str = Field(description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type")
    user_id: str = Field(description="User ID associated with the token")
    expires_in: int = Field(description="Token expiry in seconds")


class UserResponse(BaseModel):
    """Response from GET /auth/me."""
    id: str = Field(description="User ID")
    username: str = Field(description="Username")
    email: str = Field(description="Email address")
    role: str = Field(description="User role (admin/user)")
    created_at: datetime = Field(description="Account creation timestamp")


class HealthResponse(BaseModel):
    """Response from GET /health."""
    status: str = Field(default="ok", description="Health status")
    version: str = Field(description="API version")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Current server time"
    )


class ConfigResponse(BaseModel):
    """Response from GET /config."""
    models_available: list[str] = Field(
        description="List of available Claude models"
    )
    default_model: str = Field(
        description="Default model to use"
    )


# =============================================================================
# Agent Configuration Overrides (matches agent.yaml + CLI args)
# =============================================================================

class AgentConfigOverrides(BaseModel):
    """
    Configuration overrides for agent execution.

    All fields are optional - if not provided, values from agent.yaml are used.
    These match the CLI arguments: --model, --max-turns, --timeout, etc.
    """
    # Model override (CLI: --model)
    model: Optional[str] = Field(
        default=None,
        description="Claude model to use (overrides agent.yaml)"
    )

    # Execution limits (CLI: --max-turns, --timeout)
    max_turns: Optional[int] = Field(
        default=None,
        description="Maximum conversation turns (overrides agent.yaml)"
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        description="Execution timeout in seconds (overrides agent.yaml)"
    )

    # Feature toggles (CLI: --no-skills maps to enable_skills=false)
    enable_skills: Optional[bool] = Field(
        default=None,
        description="Enable custom skills (overrides agent.yaml)"
    )
    enable_file_checkpointing: Optional[bool] = Field(
        default=None,
        description="Enable file change tracking (overrides agent.yaml)"
    )

    # Permission settings (CLI: --permission-mode, --profile)
    permission_mode: Optional[str] = Field(
        default=None,
        description="Permission mode: default, acceptEdits, plan, bypassPermissions"
    )
    profile: Optional[str] = Field(
        default=None,
        description="Permission profile file path"
    )

    # Role template (from agent.yaml)
    role: Optional[str] = Field(
        default=None,
        description="Role template name (loads prompts/roles/<role>.md)"
    )

    # SDK options (from agent.yaml)
    max_buffer_size: Optional[int] = Field(
        default=None,
        description="Maximum buffer size for streaming"
    )
    output_format: Optional[str] = Field(
        default=None,
        description="Output format: text, json, stream-json"
    )
    include_partial_messages: Optional[bool] = Field(
        default=None,
        description="Include partial/incomplete messages in output"
    )


# =============================================================================
# Session Requests
# =============================================================================

class RunTaskRequest(BaseModel):
    """
    Request body for POST /sessions/run - unified endpoint to run agent tasks.

    This is the primary endpoint that combines session creation and task execution.
    Supports both new sessions and resuming existing sessions.

    Matches CLI capabilities:
      python agent.py --task "..." --model "..." --max-turns 50
      python agent.py --resume SESSION_ID --task "Continue..."
    """
    # Task (required)
    task: str = Field(
        description="Task description to execute"
    )

    # Additional directories (CLI: --add-dir)
    additional_dirs: list[str] = Field(
        default_factory=list,
        description="Additional directories the agent can access"
    )

    # Session resumption (CLI: --resume, --fork-session)
    resume_session_id: Optional[str] = Field(
        default=None,
        description="Session ID to resume (e.g., 20260103_210631_ecc41d66)"
    )
    fork_session: bool = Field(
        default=False,
        description="Fork to new session when resuming instead of continuing"
    )

    # All agent config overrides
    config: AgentConfigOverrides = Field(
        default_factory=AgentConfigOverrides,
        description="Agent configuration overrides (model, max_turns, etc.)"
    )
    
    @field_validator("task")
    @classmethod
    def truncate_task(cls, v: str) -> str:
        """Apply WAF filter to task field."""
        return truncate_text_content(v, "task") or ""


class CreateSessionRequest(BaseModel):
    """
    Request body for POST /sessions - creates a session without starting.

    Use this if you want to create a session first and start later.
    For most cases, use POST /sessions/run instead.
    """
    task: str = Field(description="Task description for the agent")
    model: Optional[str] = Field(
        default=None,
        description="Claude model to use (overrides config)"
    )
    
    @field_validator("task")
    @classmethod
    def truncate_task(cls, v: str) -> str:
        """Apply WAF filter to task field."""
        return truncate_text_content(v, "task") or ""


class StartTaskRequest(BaseModel):
    """
    Request body for POST /sessions/{id}/task - starts task on existing session.

    All fields are optional - uses session's stored values if not provided.
    Matches CLI capabilities for task continuation.
    """
    # Task override (optional - uses session's task if not provided)
    task: Optional[str] = Field(
        default=None,
        description="Task to execute (optional, uses session task if not provided)"
    )

    # Additional directories (CLI: --add-dir)
    additional_dirs: list[str] = Field(
        default_factory=list,
        description="Additional directories the agent can access"
    )

    # Session resumption
    resume_session_id: Optional[str] = Field(
        default=None,
        description="Resume from a different session (optional)"
    )
    fork_session: bool = Field(
        default=False,
        description="Fork to new session when resuming"
    )

    # All agent config overrides
    config: AgentConfigOverrides = Field(
        default_factory=AgentConfigOverrides,
        description="Agent configuration overrides"
    )
    
    @field_validator("task")
    @classmethod
    def truncate_task(cls, v: Optional[str]) -> Optional[str]:
        """Apply WAF filter to task field."""
        if v is None:
            return None
        return truncate_text_content(v, "task")


# =============================================================================
# Session Responses
# =============================================================================

class SessionResponse(BaseModel):
    """Response representing a session."""
    id: str = Field(description="Session ID")
    status: str = Field(description="Session status")
    task: Optional[str] = Field(default=None, description="Task description")
    model: Optional[str] = Field(default=None, description="Model used")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")
    completed_at: Optional[datetime] = Field(
        default=None,
        description="Completion timestamp"
    )
    num_turns: int = Field(default=0, description="Number of conversation turns")
    duration_ms: Optional[int] = Field(
        default=None,
        description="Duration in milliseconds"
    )
    total_cost_usd: Optional[float] = Field(
        default=None,
        description="Total cost in USD"
    )
    cancel_requested: bool = Field(
        default=False,
        description="Whether cancellation was requested"
    )
    resumable: Optional[bool] = Field(
        default=None,
        description="Whether session can be resumed (has established Claude session)"
    )


class SessionListResponse(BaseModel):
    """Response for GET /sessions."""
    sessions: list[SessionResponse] = Field(
        default_factory=list,
        description="List of sessions"
    )
    total: int = Field(description="Total number of sessions")


class TaskStartedResponse(BaseModel):
    """Response from POST /sessions/run or POST /sessions/{id}/task."""
    session_id: str = Field(description="Session ID")
    status: str = Field(description="Session status (running)")
    message: str = Field(description="Status message")
    resumed_from: Optional[str] = Field(
        default=None,
        description="Session ID that was resumed (if applicable)"
    )


class CancelResponse(BaseModel):
    """Response from POST /sessions/{id}/cancel."""
    session_id: str = Field(description="Session ID")
    status: str = Field(description="Session status after cancellation")
    message: str = Field(description="Cancellation message")


class TokenUsageResponse(BaseModel):
    """Token usage breakdown for a completed task."""
    input_tokens: int = Field(default=0, description="Input tokens (non-cached)")
    output_tokens: int = Field(default=0, description="Output tokens generated")
    cache_creation_input_tokens: int = Field(
        default=0,
        description="Tokens used to create cache"
    )
    cache_read_input_tokens: int = Field(
        default=0,
        description="Tokens read from cache"
    )

    @property
    def total_input(self) -> int:
        """Total input tokens including cache."""
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def total(self) -> int:
        """Total tokens (input + output)."""
        return self.total_input + self.output_tokens


class ResultMetrics(BaseModel):
    """Execution metrics for a completed task."""
    duration_ms: Optional[int] = Field(
        default=None,
        description="Duration in milliseconds"
    )
    num_turns: int = Field(default=0, description="Number of conversation turns")
    total_cost_usd: Optional[float] = Field(
        default=None,
        description="Total cost in USD"
    )
    model: Optional[str] = Field(
        default=None,
        description="Model used for execution"
    )
    usage: Optional[TokenUsageResponse] = Field(
        default=None,
        description="Token usage breakdown"
    )


class ResultResponse(BaseModel):
    """Response from GET /sessions/{id}/result (event summary + metrics)."""
    session_id: str = Field(description="Session ID")
    status: str = Field(description="Task status: COMPLETE, PARTIAL, FAILED")
    error: str = Field(default="", description="Error message if any")
    comments: str = Field(default="", description="Additional comments")
    output: str = Field(default="", description="Task output")
    result_files: list[str] = Field(
        default_factory=list,
        description="Generated file paths"
    )
    metrics: Optional[ResultMetrics] = Field(
        default=None,
        description="Execution metrics (duration, turns, cost)"
    )


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str = Field(description="Error message")
    code: Optional[str] = Field(default=None, description="Error code")


class SubmitAnswerRequest(BaseModel):
    """Request body for POST /sessions/{id}/answer."""
    question_id: str = Field(description="ID of the question being answered")
    answer: str = Field(description="User's answer to the question")


class SubmitAnswerResponse(BaseModel):
    """Response from POST /sessions/{id}/answer."""
    success: bool = Field(description="Whether the answer was submitted successfully")
    message: str = Field(description="Status message")
    can_resume: bool = Field(
        default=False,
        description="Whether the session can be resumed now that the answer is submitted"
    )
