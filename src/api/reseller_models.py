"""
Pydantic request/response models for reseller and admin API endpoints.

Defines the API contract for reseller management, user provisioning,
usage reporting, API key management, and admin operations.
"""
import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Shared / Common
# =============================================================================

class PaginationInfo(BaseModel):
    """Pagination metadata for list responses."""
    page: int = Field(description="Current page number")
    per_page: int = Field(description="Items per page")
    total: int = Field(description="Total number of items")
    total_pages: int = Field(description="Total number of pages")


class ResellerErrorDetail(BaseModel):
    """Structured error detail with machine-readable code."""
    code: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable error message")
    details: Optional[dict[str, Any]] = Field(
        default=None, description="Additional error context"
    )


class ResellerErrorResponse(BaseModel):
    """Standard error response for reseller/admin endpoints."""
    error: ResellerErrorDetail


# =============================================================================
# Reseller User Management — Requests
# =============================================================================

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


class CreateResellerUserRequest(BaseModel):
    """Request body for POST /reseller/users."""
    username: str = Field(
        min_length=3, max_length=32,
        description="Username (3-32 chars, lowercase alphanumeric + underscore)"
    )
    email: str = Field(description="Email address")
    password: str = Field(min_length=8, description="Password (min 8 chars)")
    quota_overrides: Optional[dict[str, Any]] = Field(
        default=None, description="Quota overrides within reseller limits"
    )
    feature_overrides: Optional[dict[str, Any]] = Field(
        default=None, description="Feature overrides (cannot exceed reseller features)"
    )
    metadata: Optional[dict[str, Any]] = Field(
        default=None, description="Opaque metadata (e.g., whmcs_service_id)"
    )

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not re.match(r'^[a-z][a-z0-9_]{2,31}$', v):
            raise ValueError(
                "Username must start with a letter, contain only "
                "lowercase letters, digits, and underscores"
            )
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email format")
        return v.lower()


class UpdateResellerUserRequest(BaseModel):
    """Request body for PUT /reseller/users/{user_id}."""
    email: Optional[str] = Field(default=None, description="New email")
    quota_overrides: Optional[dict[str, Any]] = Field(default=None)
    feature_overrides: Optional[dict[str, Any]] = Field(default=None)
    metadata: Optional[dict[str, Any]] = Field(default=None)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _EMAIL_RE.match(v):
            raise ValueError("Invalid email format")
        return v.lower() if v else v


class SuspendRequest(BaseModel):
    """Request for suspend operations."""
    reason: Optional[str] = Field(default=None, description="Suspension reason")


class ChangePasswordRequest(BaseModel):
    """Request for password change."""
    new_password: str = Field(min_length=8, description="New password")


# =============================================================================
# Reseller User Management — Responses
# =============================================================================

class ResellerUserQuota(BaseModel):
    """User quota summary."""
    max_concurrent_tasks: int = 2
    max_daily_tasks: int = 50
    tasks_today: int = 0


class ResellerUserUsageSummary(BaseModel):
    """User usage summary for the current period."""
    sessions_total: int = 0
    sessions_this_month: int = 0
    cost_this_month_usd: float = 0.0
    tokens_this_month: int = 0


class ResellerUserResponse(BaseModel):
    """Response representing a user managed by a reseller."""
    id: str
    username: str
    email: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_session_at: Optional[datetime] = None
    sessions_total: int = 0
    quota: Optional[ResellerUserQuota] = None
    features: Optional[dict[str, Any]] = None
    usage_summary: Optional[ResellerUserUsageSummary] = None
    metadata: Optional[dict[str, Any]] = None


class ResellerUserListResponse(BaseModel):
    """Paginated list of users."""
    users: list[ResellerUserResponse] = Field(default_factory=list)
    pagination: PaginationInfo


class SuspendUserResponse(BaseModel):
    """Response from suspending a user."""
    id: str
    username: str
    is_active: bool
    suspended_at: Optional[datetime] = None
    active_sessions_cancelled: int = 0


class DeleteUserResponse(BaseModel):
    """Response from deleting a user."""
    status: str = "deleted"
    id: str
    username: str
    sessions_deleted: int = 0
    files_cleaned: bool = True


class PasswordChangedResponse(BaseModel):
    """Response from changing a password."""
    status: str = "password_changed"
    tokens_revoked: bool = True


# =============================================================================
# API Key Management
# =============================================================================

VALID_SCOPES = {
    "users:create", "users:read", "users:update", "users:suspend",
    "users:delete", "users:password", "sessions:read", "usage:read",
    "keys:manage", "config:read", "config:update", "skills:manage",
    "security:manage",
}


class CreateAPIKeyRequest(BaseModel):
    """Request to create an API key."""
    name: str = Field(min_length=1, max_length=100, description="Key name")
    scopes: list[str] = Field(description="Permitted scopes")
    ip_allowlist: Optional[list[str]] = Field(
        default=None, description="Allowed IPs/CIDRs (null = any)"
    )
    rate_limit_per_minute: int = Field(default=60, ge=1, le=1000)
    expires_at: Optional[datetime] = Field(default=None)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, v: list[str]) -> list[str]:
        invalid = set(v) - VALID_SCOPES
        if invalid:
            raise ValueError(f"Invalid scopes: {invalid}")
        return v


class APIKeyResponse(BaseModel):
    """Response for API key (without full key)."""
    id: str
    key_prefix: str
    name: str
    scopes: list[str]
    ip_allowlist: Optional[list[str]] = None
    rate_limit_per_minute: int = 60
    is_active: bool = True
    last_used_at: Optional[datetime] = None
    last_used_ip: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime


class APIKeyCreatedResponse(APIKeyResponse):
    """Response from creating an API key (includes full key, shown once)."""
    key: str = Field(description="Full API key — shown only once")


class APIKeyRotatedResponse(APIKeyCreatedResponse):
    """Response from rotating an API key."""
    old_key_valid_until: datetime


class APIKeyListResponse(BaseModel):
    """List of API keys."""
    api_keys: list[APIKeyResponse] = Field(default_factory=list)


# =============================================================================
# Usage & Reporting
# =============================================================================

class UsagePeriod(BaseModel):
    """Time period for usage queries."""
    start: datetime
    end: datetime


class UsageTotals(BaseModel):
    """Aggregate usage totals."""
    sessions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    active_users: int = 0
    ssh_commands: int = 0


class UserUsageBreakdown(BaseModel):
    """Per-user usage breakdown."""
    user_id: str
    username: str
    sessions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    ssh_commands: int = 0


class DayUsageBreakdown(BaseModel):
    """Per-day usage breakdown."""
    date: str
    sessions: int = 0
    cost_usd: float = 0.0
    active_users: int = 0


class UsageResponse(BaseModel):
    """Aggregate usage report response."""
    period: UsagePeriod
    totals: UsageTotals
    by_user: Optional[list[UserUsageBreakdown]] = None
    by_day: Optional[list[DayUsageBreakdown]] = None


class UserUsageResponse(BaseModel):
    """Per-user usage detail response."""
    user_id: str
    username: str
    period: UsagePeriod
    totals: UsageTotals
    sessions: list[dict[str, Any]] = Field(default_factory=list)


# =============================================================================
# Spending
# =============================================================================

class SpendingLimits(BaseModel):
    """Spending limit configuration."""
    monthly_usd: Optional[float] = None
    daily_usd: Optional[float] = None
    per_session_usd: Optional[float] = None


class SpendingCurrent(BaseModel):
    """Current spending amounts."""
    monthly_usd: float = 0.0
    daily_usd: float = 0.0


class SpendingStatusResponse(BaseModel):
    """Spending status for a user or reseller."""
    limits: SpendingLimits
    current: SpendingCurrent
    alert_threshold_pct: int = 80
    status: str = "ok"  # "ok", "warning", "exceeded"


class SetSpendingLimitsRequest(BaseModel):
    """Request to set spending limits."""
    max_monthly_usd: Optional[float] = Field(default=None, ge=0)
    max_daily_usd: Optional[float] = Field(default=None, ge=0)
    max_per_session_usd: Optional[float] = Field(default=None, ge=0)


# =============================================================================
# User Configuration
# =============================================================================

class UserConfigResponse(BaseModel):
    """Full user configuration response."""
    user_id: str
    settings_mode: str = "readonly"
    allowed_overrides: list[str] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    security: dict[str, Any] = Field(default_factory=dict)
    spending: SpendingStatusResponse
    skills: dict[str, Any] = Field(default_factory=dict)
    ssh_filters: dict[str, Any] = Field(default_factory=dict)


class UpdateUserConfigRequest(BaseModel):
    """Request to update user configuration."""
    settings_mode: Optional[str] = None
    allowed_overrides: Optional[list[str]] = None
    feature_overrides: Optional[dict[str, Any]] = None


class UpdateSecurityConfigRequest(BaseModel):
    """Request to update user security configuration."""
    allowed_tools: Optional[list[str]] = None
    disabled_tools: Optional[list[str]] = None
    command_block_patterns: Optional[list[str]] = None
    network_allowed_domains: Optional[list[str]] = None
    network_blocked_domains: Optional[list[str]] = None
    path_blocklist_additions: Optional[list[str]] = None


class UpdateSSHFiltersRequest(BaseModel):
    """Request to update user SSH filters."""
    blocked_hosts: Optional[list[str]] = None
    allowed_hosts: Optional[list[str]] = None
    command_block_patterns: Optional[list[str]] = None
    max_connections: Optional[int] = Field(default=None, ge=1, le=20)
    session_timeout_seconds: Optional[int] = Field(default=None, ge=60, le=86400)


class SetSettingsModeRequest(BaseModel):
    """Request to set user settings mode."""
    mode: str = Field(description="'readonly' or 'configurable'")
    allowed_overrides: list[str] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("readonly", "configurable"):
            raise ValueError("mode must be 'readonly' or 'configurable'")
        return v


class SetEnvVarsRequest(BaseModel):
    """Request to set environment variables."""
    env_vars: dict[str, str] = Field(description="Environment variable name-value pairs")


# =============================================================================
# Skills Management
# =============================================================================

class SkillResponse(BaseModel):
    """Skill information."""
    name: str
    source: str = "library"
    is_enabled: bool = True
    content_hash: str = ""
    created_at: Optional[datetime] = None


class UserSkillsResponse(BaseModel):
    """List of user skills with limits."""
    skills: list[SkillResponse] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)


class AssignSkillRequest(BaseModel):
    """Request to assign a skill to a user."""
    name: str = Field(min_length=1, max_length=100)
    source: str = Field(default="library")


class UploadSkillRequest(BaseModel):
    """Request to upload a skill to the reseller library."""
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    content: str = Field(min_length=1, max_length=51200)  # 50KB max


# =============================================================================
# Reseller Self-Service
# =============================================================================

class ResellerProfileResponse(BaseModel):
    """Reseller's own profile."""
    id: str
    name: str
    company: Optional[str] = None
    contact_email: str
    is_active: bool
    limits: dict[str, Any]
    llm_provider: Optional[str] = None
    features: dict[str, Any] = Field(default_factory=dict)
    spending: Optional[SpendingStatusResponse] = None
    created_at: datetime


class ConnectionTestResponse(BaseModel):
    """Response for test-connection endpoint."""
    status: str = "ok"
    authenticated_as: str
    reseller_id: str
    server_version: str
    timestamp: datetime


# =============================================================================
# Admin — Reseller Management
# =============================================================================

class CreateResellerRequest(BaseModel):
    """Request to create a new reseller."""
    name: str = Field(min_length=1, max_length=100)
    company: Optional[str] = Field(default=None, max_length=255)
    contact_email: str = Field(description="Primary contact email")
    password: str = Field(min_length=8, description="Reseller login password")
    max_users: int = Field(default=50, ge=1, le=10000)
    max_concurrent_tasks: int = Field(default=10, ge=1, le=100)
    max_daily_tasks: int = Field(default=500, ge=1, le=100000)
    max_monthly_spending_usd: Optional[float] = Field(default=None, ge=0)
    max_daily_spending_usd: Optional[float] = Field(default=None, ge=0)
    spending_alert_threshold_pct: int = Field(default=80, ge=1, le=100)
    llm_provider: Optional[str] = Field(default=None)
    features: Optional[dict[str, Any]] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=5000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_ -]{0,99}$', v):
            raise ValueError(
                "Name must start with a letter and contain only "
                "letters, digits, spaces, hyphens, underscores"
            )
        return v


class UpdateResellerRequest(BaseModel):
    """Request to update a reseller."""
    name: Optional[str] = Field(default=None, max_length=100)
    company: Optional[str] = Field(default=None, max_length=255)
    contact_email: Optional[str] = None
    max_users: Optional[int] = Field(default=None, ge=1, le=10000)
    max_concurrent_tasks: Optional[int] = Field(default=None, ge=1, le=100)
    max_daily_tasks: Optional[int] = Field(default=None, ge=1, le=100000)
    max_monthly_spending_usd: Optional[float] = Field(default=None, ge=0)
    max_daily_spending_usd: Optional[float] = Field(default=None, ge=0)
    spending_alert_threshold_pct: Optional[int] = Field(default=None, ge=1, le=100)
    llm_provider: Optional[str] = None
    features: Optional[dict[str, Any]] = None
    notes: Optional[str] = Field(default=None, max_length=5000)


class ResellerLimits(BaseModel):
    """Reseller quota limits."""
    max_users: int = 50
    current_users: int = 0
    max_concurrent_tasks: int = 10
    max_daily_tasks: int = 500


class ResellerSpending(BaseModel):
    """Reseller spending status."""
    limits: SpendingLimits
    current: SpendingCurrent
    alert_threshold_pct: int = 80


class ResellerStats(BaseModel):
    """Reseller statistics."""
    user_count: int = 0
    active_users_30d: int = 0
    total_sessions: int = 0
    total_cost_usd: float = 0.0
    api_keys_active: int = 0
    sessions_this_month: int = 0
    cost_this_month_usd: float = 0.0


class ResellerResponse(BaseModel):
    """Response for a reseller."""
    id: str
    name: str
    company: Optional[str] = None
    contact_email: str
    owner_user_id: str
    owner_username: Optional[str] = None
    is_active: bool
    suspended_at: Optional[datetime] = None
    limits: ResellerLimits
    llm_provider: Optional[str] = None
    features: dict[str, Any] = Field(default_factory=dict)
    spending: Optional[ResellerSpending] = None
    stats: Optional[ResellerStats] = None
    notes: Optional[str] = None
    api_keys: Optional[list[APIKeyResponse]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class ResellerListResponse(BaseModel):
    """Paginated list of resellers."""
    resellers: list[ResellerResponse] = Field(default_factory=list)
    pagination: PaginationInfo


class SuspendResellerResponse(BaseModel):
    """Response from suspending a reseller."""
    id: str
    name: str
    is_active: bool
    suspended_at: datetime
    users_suspended: int = 0
    sessions_cancelled: int = 0
    api_keys_deactivated: int = 0


class UnsuspendResellerResponse(BaseModel):
    """Response from unsuspending a reseller."""
    id: str
    name: str
    is_active: bool
    users_restored: int = 0
    api_keys_reactivated: int = 0


class DeleteResellerResponse(BaseModel):
    """Response from deleting a reseller."""
    status: str = "deleted"
    name: str
    users_deleted: int = 0
    sessions_deleted: int = 0


# =============================================================================
# Admin — Platform Stats
# =============================================================================

class PlatformStats(BaseModel):
    """Platform-wide statistics."""
    platform: dict[str, Any]
    resellers: dict[str, Any]
    users: dict[str, Any]
    sessions: dict[str, Any]
    usage_this_month: dict[str, Any]
    capacity: dict[str, Any]


class AuditLogEntry(BaseModel):
    """Single audit log entry."""
    id: int
    timestamp: datetime
    api_key_name: Optional[str] = None
    reseller_name: Optional[str] = None
    action: str
    target_user: Optional[str] = None
    ip_address: str
    status_code: int
    error: Optional[str] = None


class AuditLogResponse(BaseModel):
    """Paginated audit log."""
    entries: list[AuditLogEntry] = Field(default_factory=list)
    pagination: PaginationInfo
