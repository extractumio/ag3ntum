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

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
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
