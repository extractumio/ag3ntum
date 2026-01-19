"""
Scanner Configuration Loader for Ag3ntum.

Loads sensitive data scanner configuration from YAML file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# Default config file path (relative to project root)
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "security" / "sensitive-data-scanner.yaml"

# Global config instance (lazy-loaded)
_config_instance: Optional["ScannerConfig"] = None


@dataclass
class SessionScanConfig:
    """Configuration for session file scanning."""

    enabled: bool = True
    max_depth: int = 5
    max_files: int = 100
    max_file_size_bytes: int = 1048576  # 1MB
    scan_extensions: list[str] = field(default_factory=lambda: [
        ".txt", ".json", ".jsonl", ".yaml", ".yml", ".env",
        ".config", ".conf", ".ini", ".properties", ".xml",
        ".py", ".js", ".ts", ".sh", ".bash", ".log", ".md", ".csv"
    ])
    skip_patterns: list[str] = field(default_factory=lambda: [
        "*.pyc", "*.pyo", "__pycache__/*", ".git/*", "node_modules/*",
        "*.bin", "*.exe", "*.dll", "*.so", "*.dylib",
        "*.png", "*.jpg", "*.jpeg", "*.gif", "*.pdf"
    ])
    readonly_mount_paths: list[str] = field(default_factory=lambda: ["external/ro"])
    recent_files_window_seconds: int = 3600


@dataclass
class AlertConfig:
    """Configuration for security alerts."""

    sse_enabled: bool = True
    log_enabled: bool = True
    single_file_message: str = "Sensitive data detected in {filename}: {types}. Content has been redacted."
    multiple_files_message: str = "Sensitive data detected in {count} files: {types}. Content has been redacted."
    type_labels: dict[str, str] = field(default_factory=dict)


@dataclass
class ScannerConfig:
    """Complete scanner configuration."""

    enabled: bool = True
    replacement_format: str = "asterisk"

    # Detection settings
    detect_secrets_plugins: list[str] = field(default_factory=list)
    entropy_base64_limit: float = 4.5
    entropy_hex_limit: float = 3.0
    custom_patterns: dict[str, list[str]] = field(default_factory=dict)

    # Allowlist settings
    excluded_paths: list[str] = field(default_factory=list)
    false_positive_strings: list[str] = field(default_factory=list)
    false_positive_patterns: list[str] = field(default_factory=list)

    # Session scan settings
    session_scan: SessionScanConfig = field(default_factory=SessionScanConfig)

    # Alert settings
    alerts: AlertConfig = field(default_factory=AlertConfig)


def load_scanner_config(config_path: Optional[Path | str] = None) -> ScannerConfig:
    """
    Load scanner configuration from YAML file.

    Args:
        config_path: Path to config file (uses default if not specified)

    Returns:
        ScannerConfig instance
    """
    config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        logger.warning(
            f"Scanner config not found at {config_path}. Using defaults."
        )
        return ScannerConfig()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load scanner config from {config_path}: {e}")
        return ScannerConfig()

    return _parse_config(data)


def _parse_config(data: dict[str, Any]) -> ScannerConfig:
    """Parse config dictionary into ScannerConfig object."""

    # Parse detection settings
    detection = data.get("detection", {})
    entropy = detection.get("entropy", {})

    # Parse session scan settings
    session_scan_data = data.get("session_scan", {})
    session_scan = SessionScanConfig(
        enabled=session_scan_data.get("enabled", True),
        max_depth=session_scan_data.get("max_depth", 5),
        max_files=session_scan_data.get("max_files", 100),
        max_file_size_bytes=session_scan_data.get("max_file_size_bytes", 1048576),
        scan_extensions=session_scan_data.get("scan_extensions", SessionScanConfig().scan_extensions),
        skip_patterns=session_scan_data.get("skip_patterns", SessionScanConfig().skip_patterns),
        readonly_mount_paths=session_scan_data.get("readonly_mount_paths", ["external/ro"]),
        recent_files_window_seconds=session_scan_data.get("recent_files_window_seconds", 3600),
    )

    # Parse alert settings
    alerts_data = data.get("alerts", {})
    messages = alerts_data.get("messages", {})
    alerts = AlertConfig(
        sse_enabled=alerts_data.get("sse_enabled", True),
        log_enabled=alerts_data.get("log_enabled", True),
        single_file_message=messages.get(
            "single_file",
            "Sensitive data detected in {filename}: {types}. Content has been redacted."
        ),
        multiple_files_message=messages.get(
            "multiple_files",
            "Sensitive data detected in {count} files: {types}. Content has been redacted."
        ),
        type_labels=alerts_data.get("type_labels", {}),
    )

    # Parse allowlist
    allowlist = data.get("allowlist", {})

    return ScannerConfig(
        enabled=data.get("enabled", True),
        replacement_format=data.get("replacement_format", "asterisk"),
        detect_secrets_plugins=detection.get("detect_secrets_plugins", []),
        entropy_base64_limit=entropy.get("base64_limit", 4.5),
        entropy_hex_limit=entropy.get("hex_limit", 3.0),
        custom_patterns=detection.get("custom_patterns", {}),
        excluded_paths=allowlist.get("excluded_paths", []),
        false_positive_strings=allowlist.get("false_positive_strings", []),
        false_positive_patterns=allowlist.get("false_positive_patterns", []),
        session_scan=session_scan,
        alerts=alerts,
    )


def get_scanner_config() -> ScannerConfig:
    """
    Get the global scanner config instance (lazy-loaded).

    Returns:
        ScannerConfig instance
    """
    global _config_instance

    if _config_instance is None:
        _config_instance = load_scanner_config()

    return _config_instance


def reset_scanner_config() -> None:
    """Reset the global config instance (for testing or config reload)."""
    global _config_instance
    _config_instance = None


def is_scanner_enabled() -> bool:
    """Check if the scanner is enabled in config."""
    return get_scanner_config().enabled


def get_type_label(secret_type: str) -> str:
    """
    Get human-readable label for a secret type.

    Args:
        secret_type: The internal secret type name

    Returns:
        Human-readable label
    """
    config = get_scanner_config()
    return config.alerts.type_labels.get(
        secret_type,
        secret_type.replace("_", " ").title()
    )
