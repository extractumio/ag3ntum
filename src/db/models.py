"""
SQLAlchemy ORM models for Ag3ntum API.

Defines User and Session tables for the SQLite database.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
