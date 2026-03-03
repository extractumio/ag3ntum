"""
SQLAlchemy ORM models for Ag3ntum API.

Defines User and Session tables for the SQLite database.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    """User model for authenticated access."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(20), default="user")
    jwt_secret: Mapped[str] = mapped_column(String(64))
    linux_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    queue_priority: Mapped[int] = mapped_column(Integer, default=0)  # Higher = higher priority
    token_version: Mapped[int] = mapped_column(Integer, default=0)

    # Reseller association (null = admin-managed direct user)
    reseller_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("resellers.id"), nullable=True, index=True
    )

    # Per-user configuration (reseller-managed)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    features_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    settings_mode: Mapped[str] = mapped_column(String(20), default="readonly", server_default="readonly")
    allowed_overrides: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    security_overrides_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    spending_limit_monthly_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spending_limit_daily_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spending_limit_per_session_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    reseller: Mapped[Optional["Reseller"]] = relationship(
        "Reseller", back_populates="users", foreign_keys=[reseller_id]
    )
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )
    tokens: Mapped[list["Token"]] = relationship(
        "Token", back_populates="user", cascade="all, delete-orphan"
    )
    vault_secrets: Mapped[list["VaultSecret"]] = relationship(
        "VaultSecret", back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    """
    Session model for agent execution tracking.

    This is the authoritative source for all session metadata.
    Session directories only contain agent.jsonl (SDK log) and workspace/.
    """
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    task: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    working_dir: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    num_turns: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_cost_usd: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )

    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    # Queue management
    queue_position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)

    # Auto-resume tracking
    is_auto_resume: Mapped[bool] = mapped_column(Boolean, default=False)
    resume_attempts: Mapped[int] = mapped_column(Integer, default=0)

    # Claude SDK session ID for resumption (captured from init event)
    # This is different from `id` which is Ag3ntum's internal session ID
    claude_session_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Cumulative statistics across all resumptions
    cumulative_turns: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Token usage (separate columns for query efficiency)
    cumulative_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cumulative_cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Session forking
    parent_session_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Checkpointing (JSON array of Checkpoint objects)
    file_checkpointing_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    checkpoints_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationship to user
    user: Mapped["User"] = relationship("User", back_populates="sessions")

    # Relationship to events
    events: Mapped[list["Event"]] = relationship(
        "Event", back_populates="session", cascade="all, delete-orphan"
    )


class Event(Base):
    """
    Persisted SSE events for session replay and recovery.

    Stores structured events emitted by the tracer so clients can
    resume streams and load full history.
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("sessions.id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    data: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime)

    session: Mapped["Session"] = relationship("Session", back_populates="events")


class Token(Base):
    """Token storage for encrypted user credentials."""
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    token_type: Mapped[str] = mapped_column(String(50))
    encrypted_value: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="tokens")


class UserQuota(Base):
    """
    Tracks user quota usage with persistence across restarts.

    Stores per-user task limits and daily usage counters that survive
    container restarts. The daily counter resets automatically when
    a new day begins (based on last_reset timestamp).
    """
    __tablename__ = "user_quotas"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )

    # Configurable limits (can override global defaults per user)
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, default=2)
    max_daily_tasks: Mapped[int] = mapped_column(Integer, default=50)

    # Usage tracking (persists across restarts)
    tasks_today: Mapped[int] = mapped_column(Integer, default=0)
    last_reset: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    user: Mapped["User"] = relationship("User")

    def should_reset_daily_count(self) -> bool:
        """Check if daily counter should be reset (new day)."""
        now = datetime.now(timezone.utc)
        return self.last_reset.date() < now.date()

    def reset_if_needed(self) -> bool:
        """Reset daily count if new day. Returns True if reset occurred."""
        if self.should_reset_daily_count():
            self.tasks_today = 0
            self.last_reset = datetime.now(timezone.utc)
            return True
        return False


class VaultSecret(Base):
    """Encrypted credential storage for users (API keys, SSH keys, etc.)."""
    __tablename__ = "vault_secrets"

    __table_args__ = (
        UniqueConstraint("user_id", "secret_type", "name", name="uq_vault_user_type_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    secret_type: Mapped[str] = mapped_column(String(50), index=True)
    # Types: api_key, ssh_private_key, ssh_certificate, ftp_credentials,
    #        database_url, bearer_token, http_basic_auth, generic
    name: Mapped[str] = mapped_column(String(255))
    encrypted_value: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    env_var_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ssh_key_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rotated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_accessed_session_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="vault_secrets")


class VaultAuditLog(Base):
    """Immutable audit trail for all vault secret operations."""
    __tablename__ = "vault_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vault_secret_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("vault_secrets.id"), nullable=True, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50))
    # Actions: CREATE, READ, DECRYPT, UPDATE, DELETE, ROTATE, INJECT, FAILED_ACCESS
    status: Mapped[str] = mapped_column(String(20))
    # Statuses: SUCCESS, FAILED, DENIED
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, index=True, default=lambda: datetime.now(timezone.utc)
    )


class SSHAuditEvent(Base):
    """Audit log for all SSH tool operations."""
    __tablename__ = "ssh_audit_events"

    __table_args__ = (
        Index("ix_ssh_audit_user_host", "user_id", "remote_host"),
        Index("ix_ssh_audit_user_timestamp", "user_id", "timestamp"),
        Index("ix_ssh_audit_session_timestamp", "session_id", "timestamp"),
        Index("ix_ssh_audit_blocked_timestamp", "blocked", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    ssh_profile: Mapped[str] = mapped_column(String(255))
    remote_host: Mapped[str] = mapped_column(String(255), index=True)
    remote_user: Mapped[str] = mapped_column(String(255))
    remote_port: Mapped[int] = mapped_column(Integer, default=22)
    operation: Mapped[str] = mapped_column(String(50), index=True)
    # Operations: exec, read_file, write_file, connect, disconnect
    privilege_level: Mapped[int] = mapped_column(Integer, default=0)
    command: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remote_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str] = mapped_column(String(50))
    # Modes: operations, readonly, filtered_shell
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    block_rule: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    human_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    context_isolated: Mapped[bool] = mapped_column(Boolean, default=False)
    anomaly_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    anomaly_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    relay_used: Mapped[bool] = mapped_column(Boolean, default=False)
    relay_audit_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, index=True, default=lambda: datetime.now(timezone.utc)
    )


# =============================================================================
# Reseller Models
# =============================================================================

class Reseller(Base):
    """Reseller organization that manages end-users."""
    __tablename__ = "resellers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str] = mapped_column(String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # The user account that acts as reseller (role="reseller")
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), unique=True
    )

    # Quota limits
    max_users: Mapped[int] = mapped_column(Integer, default=50)
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, default=10)
    max_daily_tasks: Mapped[int] = mapped_column(Integer, default=500)

    # Spending caps
    max_monthly_spending_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_daily_spending_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spending_alert_threshold_pct: Mapped[int] = mapped_column(Integer, default=80)

    # LLM provider (from llm-api-proxy.yaml); null = platform default
    llm_provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Feature flags (JSON blob)
    features_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Admin notes
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Suspension tracking
    suspended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    suspended_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pre_suspend_user_states: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    owner: Mapped["User"] = relationship(
        "User", foreign_keys=[owner_user_id]
    )
    users: Mapped[list["User"]] = relationship(
        "User", back_populates="reseller", foreign_keys="User.reseller_id"
    )
    api_keys: Mapped[list["APIKey"]] = relationship(
        "APIKey", back_populates="reseller", cascade="all, delete-orphan"
    )
    quota: Mapped[Optional["ResellerQuota"]] = relationship(
        "ResellerQuota", back_populates="reseller", uselist=False,
        cascade="all, delete-orphan"
    )


class APIKey(Base):
    """API key for machine-to-machine authentication."""
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reseller_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("resellers.id"), nullable=True, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(20), index=True)
    key_hash: Mapped[str] = mapped_column(String(128))
    scopes: Mapped[str] = mapped_column(Text)  # JSON array
    ip_allowlist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    reseller: Mapped[Optional["Reseller"]] = relationship(
        "Reseller", back_populates="api_keys"
    )


class APIKeyAuditLog(Base):
    """Immutable audit log for API key usage."""
    __tablename__ = "api_key_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("api_keys.id"), nullable=True, index=True
    )
    reseller_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("resellers.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(50))
    target_user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    ip_address: Mapped[str] = mapped_column(String(45))
    status_code: Mapped[int] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, index=True, default=lambda: datetime.now(timezone.utc)
    )


class UsageRecord(Base):
    """Per-session usage record for billing and reporting."""
    __tablename__ = "usage_records"

    __table_args__ = (
        Index("ix_usage_reseller_period", "reseller_id", "period_start"),
        Index("ix_usage_user_period", "user_id", "period_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    reseller_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("resellers.id"), nullable=True, index=True
    )
    session_id: Mapped[str] = mapped_column(String(50), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    model: Mapped[str] = mapped_column(String(100))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    num_turns: Mapped[int] = mapped_column(Integer, default=0)
    ssh_commands_executed: Mapped[int] = mapped_column(Integer, default=0)
    files_uploaded: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ResellerQuota(Base):
    """Aggregate quota tracking for a reseller across all their users."""
    __tablename__ = "reseller_quotas"

    reseller_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resellers.id"), primary_key=True
    )
    current_user_count: Mapped[int] = mapped_column(Integer, default=0)
    tasks_today: Mapped[int] = mapped_column(Integer, default=0)
    last_reset: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    monthly_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    monthly_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    monthly_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    monthly_reset: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    daily_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    daily_cost_reset: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    reseller: Mapped["Reseller"] = relationship(
        "Reseller", back_populates="quota"
    )

    def should_reset_daily(self) -> bool:
        """Check if daily counter should be reset."""
        now = datetime.now(timezone.utc)
        return self.daily_cost_reset.date() < now.date()

    def should_reset_monthly(self) -> bool:
        """Check if monthly counters should be reset."""
        now = datetime.now(timezone.utc)
        return (self.monthly_reset.year < now.year or
                self.monthly_reset.month < now.month)

    def reset_if_needed(self) -> None:
        """Reset counters if new period."""
        now = datetime.now(timezone.utc)
        if self.should_reset_daily():
            self.tasks_today = 0
            self.daily_cost_usd = 0.0
            self.daily_cost_reset = now
        if self.should_reset_monthly():
            self.monthly_input_tokens = 0
            self.monthly_output_tokens = 0
            self.monthly_cost_usd = 0.0
            self.monthly_reset = now


class UserSkill(Base):
    """Tracks skills assigned to a user."""
    __tablename__ = "user_skills"

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_user_skill_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    reseller_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("resellers.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(20))  # "library", "uploaded", "custom"
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class PlatformConfig(Base):
    """Key-value store for mutable platform-level defaults.

    Admins update via PUT /admin/config. FeatureFlagService reads these
    on startup and caches them in memory.
    """
    __tablename__ = "platform_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)


class WebhookEndpoint(Base):
    """Reseller webhook endpoint for event notifications."""
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reseller_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resellers.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    events: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    deliveries: Mapped[list["WebhookDeliveryLog"]] = relationship(
        "WebhookDeliveryLog", back_populates="endpoint",
        cascade="all, delete-orphan",
    )


class WebhookDeliveryLog(Base):
    """Delivery log entry for a webhook notification attempt."""
    __tablename__ = "webhook_delivery_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("webhook_endpoints.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    endpoint: Mapped["WebhookEndpoint"] = relationship(
        "WebhookEndpoint", back_populates="deliveries"
    )


class ResellerSkillLibrary(Base):
    """Reseller-curated skill library for assignment to users."""
    __tablename__ = "reseller_skill_library"

    __table_args__ = (
        UniqueConstraint("reseller_id", "name", name="uq_reseller_skill_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reseller_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resellers.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
