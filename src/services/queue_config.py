"""
Configuration dataclasses for task queue system.

Loads and validates task_queue settings from api.yaml with sensible defaults.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class AutoResumeConfig:
    """Configuration for auto-resume of interrupted sessions."""
    enabled: bool = True
    max_session_age_hours: int = 6
    max_resume_attempts: int = 3
    resume_delay_seconds: int = 5


@dataclass
class QueueConfig:
    """Configuration for task queue processing."""
    enabled: bool = True
    processing_interval_ms: int = 500
    max_queue_size: int = 1000
    task_timeout_minutes: int = 30


@dataclass
class QuotaConfig:
    """Configuration for task execution quotas."""
    global_max_concurrent: int = 4
    per_user_max_concurrent: int = 2
    per_user_daily_limit: int = 50  # 0 = unlimited


@dataclass
class TaskQueueConfig:
    """Combined configuration for entire task queue system."""
    auto_resume: AutoResumeConfig
    queue: QueueConfig
    quotas: QuotaConfig


def load_queue_config(task_queue_config: dict[str, Any]) -> TaskQueueConfig:
    """
    Load task queue configuration from api.yaml task_queue section.

    Args:
        task_queue_config: The 'task_queue' section from api.yaml config.

    Returns:
        TaskQueueConfig with all settings loaded, using defaults for missing values.
    """
    auto_resume_dict = task_queue_config.get("auto_resume", {})
    queue_dict = task_queue_config.get("queue", {})
    quotas_dict = task_queue_config.get("quotas", {})

    return TaskQueueConfig(
        auto_resume=AutoResumeConfig(
            enabled=auto_resume_dict.get("enabled", True),
            max_session_age_hours=auto_resume_dict.get("max_session_age_hours", 6),
            max_resume_attempts=auto_resume_dict.get("max_resume_attempts", 3),
            resume_delay_seconds=auto_resume_dict.get("resume_delay_seconds", 5),
        ),
        queue=QueueConfig(
            enabled=queue_dict.get("enabled", True),
            processing_interval_ms=queue_dict.get("processing_interval_ms", 500),
            max_queue_size=queue_dict.get("max_queue_size", 1000),
            task_timeout_minutes=queue_dict.get("task_timeout_minutes", 30),
        ),
        quotas=QuotaConfig(
            global_max_concurrent=quotas_dict.get("global_max_concurrent", 4),
            per_user_max_concurrent=quotas_dict.get("per_user_max_concurrent", 2),
            per_user_daily_limit=quotas_dict.get("per_user_daily_limit", 50),
        ),
    )
