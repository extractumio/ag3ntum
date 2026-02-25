"""
SSH configuration models and loaders.

Loads SSH security settings from config/security/ssh-security.yaml
and per-user connection profiles from users/{user}/ag3ntum/ssh-profiles.yaml.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Default config path
SSH_SECURITY_CONFIG_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "config" / "security" / "ssh-security.yaml"
)
SSH_PRIVILEGE_LEVELS_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "config" / "security" / "ssh-privilege-levels.yaml"
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
    always_blocked: list[str] = field(default_factory=lambda: [
        "127.0.0.1", "localhost", "::1",
        "169.254.0.0/16",
    ])
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


def load_ssh_security_config(
    config_path: Optional[Path] = None,
) -> SSHSecurityConfig:
    """
    Load global SSH security configuration.

    Returns default (SSH disabled) if config file doesn't exist.
    Fail-closed: any parse error returns SSH disabled.
    """
    path = config_path or SSH_SECURITY_CONFIG_PATH

    if not path.exists():
        logger.info(
            f"SSH security config not found at {path}, SSH disabled"
        )
        return SSHSecurityConfig(enabled=False)

    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(
            f"Failed to load SSH security config from {path}: {e}. "
            "SSH disabled (fail-closed)."
        )
        return SSHSecurityConfig(enabled=False)

    ssh_data = data.get("ssh", {})
    if not ssh_data:
        return SSHSecurityConfig(enabled=False)

    limits_data = ssh_data.get("limits", {})
    hosts_data = ssh_data.get("hosts", {})
    creds_data = ssh_data.get("credentials", {})
    ctx_data = ssh_data.get("context_isolation", {})
    audit_data = ssh_data.get("audit", {})
    monitor_data = ssh_data.get("behavior_monitor", {})

    return SSHSecurityConfig(
        enabled=ssh_data.get("enabled", False),
        default_mode=ssh_data.get("default_mode", "readonly"),
        limits=SSHConnectionLimits(
            max_connections_per_user=limits_data.get(
                "max_connections_per_user", 3
            ),
            max_concurrent_commands=limits_data.get(
                "max_concurrent_commands", 5
            ),
            session_timeout_seconds=limits_data.get(
                "session_timeout_seconds", 1800
            ),
            command_timeout_seconds=limits_data.get(
                "command_timeout_seconds", 300
            ),
            max_output_bytes=limits_data.get(
                "max_output_bytes", 1_048_576
            ),
            max_file_read_bytes=limits_data.get(
                "max_file_read_bytes", 5_242_880
            ),
            max_file_write_bytes=limits_data.get(
                "max_file_write_bytes", 1_048_576
            ),
            rate_limit_commands_per_minute=limits_data.get(
                "rate_limit_commands_per_minute", 30
            ),
        ),
        hosts=SSHHostConfig(
            mode=hosts_data.get("mode", "allowlist"),
            always_blocked=hosts_data.get("always_blocked", [
                "127.0.0.1", "localhost", "::1", "169.254.0.0/16",
            ]),
            private_network_exceptions=hosts_data.get(
                "private_network_exceptions", []
            ),
        ),
        credentials=SSHCredentialConfig(
            key_storage_encryption=creds_data.get(
                "key_storage_encryption", "fernet"
            ),
            allowed_key_types=creds_data.get(
                "allowed_key_types", ["ed25519", "rsa-4096"]
            ),
            prohibited_key_types=creds_data.get(
                "prohibited_key_types", ["dsa", "rsa-1024", "rsa-2048"]
            ),
            certificate_support=creds_data.get("certificate_support", True),
            certificate_ca_url=creds_data.get("certificate_ca_url"),
            certificate_ttl_seconds=creds_data.get(
                "certificate_ttl_seconds", 3600
            ),
            password_auth_allowed=creds_data.get(
                "password_auth_allowed", False
            ),
            known_hosts_path=creds_data.get("known_hosts_path"),
        ),
        context_isolation=SSHContextIsolationConfig(
            enabled=ctx_data.get("enabled", True),
            auto_detect_sensitive=ctx_data.get(
                "auto_detect_sensitive", True
            ),
            always_redact_secrets=ctx_data.get(
                "always_redact_secrets", True
            ),
            max_context_file_size_bytes=ctx_data.get(
                "max_context_file_size_bytes", 102_400
            ),
        ),
        audit=SSHAuditConfig(
            enabled=audit_data.get("enabled", True),
            log_commands=audit_data.get("log_commands", True),
            log_file_access=audit_data.get("log_file_access", True),
            log_connection_events=audit_data.get(
                "log_connection_events", True
            ),
            sensitive_command_alert=audit_data.get(
                "sensitive_command_alert", True
            ),
            retention_days=audit_data.get("retention_days", 90),
        ),
        behavior_monitor=SSHBehaviorMonitorConfig(
            enabled=monitor_data.get("enabled", True),
            anomaly_detection=monitor_data.get("anomaly_detection", True),
            circuit_breaker_threshold=monitor_data.get(
                "circuit_breaker_threshold", 10
            ),
            command_pattern_window_seconds=monitor_data.get(
                "command_pattern_window_seconds", 60
            ),
        ),
    )


def load_ssh_profiles(
    user_config_dir: Path,
) -> dict[str, SSHProfile]:
    """
    Load per-user SSH connection profiles.

    Args:
        user_config_dir: Path to user's ag3ntum config directory
                         (e.g., /users/{username}/ag3ntum/)

    Returns:
        Dict mapping profile name to SSHProfile.
        Empty dict if no profiles configured.
    """
    profiles_path = user_config_dir / "ssh-profiles.yaml"

    if not profiles_path.exists():
        return {}

    try:
        with open(profiles_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(
            f"Failed to load SSH profiles from {profiles_path}: {e}"
        )
        return {}

    profiles_data = data.get("profiles", {})
    result: dict[str, SSHProfile] = {}

    for name, profile_data in profiles_data.items():
        if not isinstance(profile_data, dict):
            logger.warning(f"Invalid SSH profile '{name}': not a dict")
            continue

        host = profile_data.get("host")
        if not host:
            logger.warning(f"SSH profile '{name}' missing 'host'")
            continue

        result[name] = SSHProfile(
            name=name,
            host=host,
            port=profile_data.get("port", 22),
            username=profile_data.get("username", ""),
            auth_method=profile_data.get("auth_method", "key"),
            key_ref=profile_data.get("key_ref"),
            certificate_ref=profile_data.get("certificate_ref"),
            password_secret_id=profile_data.get("password_secret_id"),
            mode=profile_data.get("mode", "readonly"),
            privilege_level=profile_data.get("privilege_level", 0),
            allowed_operations=profile_data.get(
                "allowed_operations", []
            ),
            description=profile_data.get("description", ""),
            file_handling=profile_data.get("file_handling", {}),
        )

    logger.info(
        f"Loaded {len(result)} SSH profiles from {profiles_path}"
    )
    return result
