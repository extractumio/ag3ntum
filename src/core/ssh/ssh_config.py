"""
SSH configuration models and factory.

Security defaults are hardcoded — no YAML files required.
Per-user SSH enablement is controlled via feature flags (admin toggle).
Command filter policy is loaded from ssh-privilege-levels.yaml (operational config).
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Hosts that can NEVER be connected to — hardcoded, not configurable
ALWAYS_BLOCKED_HOSTS: list[str] = [
    "127.0.0.1", "localhost", "::1",
    "169.254.0.0/16",  # Cloud metadata endpoint
    "0.0.0.0",
]

# Command filter policy (operational config, not user-facing)
SSH_PRIVILEGE_LEVELS_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "config" / "security" / "ssh-privilege-levels.yaml"
)

# Legacy path — only used for deprecation warning
_LEGACY_SSH_SECURITY_CONFIG_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "config" / "security" / "ssh-security.yaml"
)


@dataclass
class SSHConnectionLimits:
    """Connection and execution limits."""
    max_connections_per_user: int = 3
    max_concurrent_commands: int = 5
    session_timeout_seconds: int = 1800  # 30 minutes
    command_timeout_seconds: int = 300  # 5 minutes
    max_output_bytes: int = 1_048_576  # 1MB
    max_file_read_bytes: int = 5_242_880  # 5MB
    max_file_write_bytes: int = 1_048_576  # 1MB
    rate_limit_commands_per_minute: int = 30


@dataclass
class SSHHostConfig:
    """Host access control configuration."""
    mode: str = "allowlist"  # allowlist | blocklist
    always_blocked: list[str] = field(
        default_factory=lambda: list(ALWAYS_BLOCKED_HOSTS)
    )
    private_network_exceptions: list[str] = field(default_factory=list)


@dataclass
class SSHCredentialConfig:
    """Credential security settings."""
    key_storage_encryption: str = "fernet"
    allowed_key_types: list[str] = field(
        default_factory=lambda: ["ed25519", "rsa-4096"]
    )
    prohibited_key_types: list[str] = field(
        default_factory=lambda: ["dsa", "rsa-1024", "rsa-2048"]
    )
    certificate_support: bool = True
    certificate_ca_url: Optional[str] = None
    certificate_ttl_seconds: int = 3600
    password_auth_allowed: bool = False
    known_hosts_path: Optional[str] = None  # Path to known_hosts file; None = use system default


@dataclass
class SSHHostKeyVerificationConfig:
    """Host key verification settings.

    TOFU (trust-on-first-use): first connection pins the host key;
    subsequent connections verify against pinned key.
    """
    mode: str = "tofu"  # tofu | strict


@dataclass
class SSHContextIsolationConfig:
    """Context isolation settings for sensitive file handling."""
    enabled: bool = True
    auto_detect_sensitive: bool = True
    always_redact_secrets: bool = True
    max_context_file_size_bytes: int = 102_400  # 100KB


@dataclass
class SSHAuditConfig:
    """Audit logging configuration."""
    enabled: bool = True
    log_commands: bool = True
    log_file_access: bool = True
    log_connection_events: bool = True
    sensitive_command_alert: bool = True
    retention_days: int = 90


@dataclass
class SSHBehaviorMonitorConfig:
    """Behavior monitoring configuration."""
    enabled: bool = True
    anomaly_detection: bool = True
    circuit_breaker_threshold: int = 10
    command_pattern_window_seconds: int = 60


@dataclass
class SSHSecurityConfig:
    """Global SSH security configuration."""
    enabled: bool = False  # Disabled by default — admin must opt-in
    default_mode: str = "readonly"  # operations | readonly | filtered_shell
    limits: SSHConnectionLimits = field(default_factory=SSHConnectionLimits)
    hosts: SSHHostConfig = field(default_factory=SSHHostConfig)
    credentials: SSHCredentialConfig = field(
        default_factory=SSHCredentialConfig
    )
    context_isolation: SSHContextIsolationConfig = field(
        default_factory=SSHContextIsolationConfig
    )
    audit: SSHAuditConfig = field(default_factory=SSHAuditConfig)
    behavior_monitor: SSHBehaviorMonitorConfig = field(
        default_factory=SSHBehaviorMonitorConfig
    )
    host_key_verification: SSHHostKeyVerificationConfig = field(
        default_factory=SSHHostKeyVerificationConfig
    )


@dataclass
class SSHProfile:
    """Per-user SSH connection profile."""
    name: str
    host: str
    port: int = 22
    username: str = ""
    auth_method: str = "key"  # key | certificate | password
    key_ref: Optional[str] = None
    certificate_ref: Optional[str] = None
    password_secret_id: Optional[int] = None
    mode: str = "readonly"  # operations | readonly | filtered_shell
    privilege_level: int = 0  # 0-4
    allowed_operations: list[str] = field(default_factory=list)
    description: str = ""
    file_handling: dict = field(default_factory=dict)


def get_default_ssh_security_config() -> SSHSecurityConfig:
    """Return SSH security config with hardcoded sensible defaults.

    No YAML file needed. SSH infrastructure is always ready at platform
    level; per-user enablement is controlled by feature flags.
    """
    return SSHSecurityConfig(
        enabled=True,  # Platform-level always ready
    )
