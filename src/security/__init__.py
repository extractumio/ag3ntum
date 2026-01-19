"""
Ag3ntum Security Module.

Provides security utilities for sensitive data detection, redaction, and alerting.
"""

from .sensitive_data_scanner import (
    SensitiveDataScanner,
    ScanResult,
    DetectedSecret,
    get_scanner,
    scan_text,
    scan_and_redact,
)
from .scanner_config import (
    ScannerConfig,
    load_scanner_config,
    get_scanner_config,
    is_scanner_enabled,
    get_type_label,
)
from .session_scanner import (
    SessionScanResult,
    FileScanResult,
    scan_session_files,
    emit_security_alert,
)

__all__ = [
    # Scanner
    "SensitiveDataScanner",
    "ScanResult",
    "DetectedSecret",
    "get_scanner",
    "scan_text",
    "scan_and_redact",
    # Config
    "ScannerConfig",
    "load_scanner_config",
    "get_scanner_config",
    "is_scanner_enabled",
    "get_type_label",
    # Session scanner
    "SessionScanResult",
    "FileScanResult",
    "scan_session_files",
    "emit_security_alert",
]
