"""
Session File Scanner for Ag3ntum.

Scans recently modified files in session workspace after agent request completion
to detect any sensitive data that may have leaked.

This module is triggered:
1. When agent request completes (agent_complete event)
2. Files modified within the configured time window are scanned
3. Alerts are sent via SSE if sensitive data is found

Key features:
- Configurable depth and file limits
- Skips read-only mounted folders
- Only scans recently modified files
- Sends SSE alerts for detection
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional

from .scanner_config import get_scanner_config, get_type_label, is_scanner_enabled
from .sensitive_data_scanner import ScanResult, get_scanner

logger = logging.getLogger(__name__)


@dataclass
class FileScanResult:
    """Result of scanning a single file."""

    file_path: Path
    relative_path: str
    scan_result: ScanResult
    redacted: bool = False
    error: Optional[str] = None


@dataclass
class SessionScanResult:
    """Result of scanning an entire session."""

    session_id: str
    files_scanned: int
    files_with_secrets: int
    total_secrets: int
    secret_types: set[str] = field(default_factory=set)
    file_results: list[FileScanResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def has_secrets(self) -> bool:
        """Whether any secrets were found."""
        return self.files_with_secrets > 0

    def get_alert_message(self) -> str:
        """Generate alert message for UI notification."""
        config = get_scanner_config()
        type_labels = [get_type_label(t) for t in self.secret_types]
        types_str = ", ".join(sorted(type_labels))

        if self.files_with_secrets == 1:
            # Single file
            file_result = self.file_results[0] if self.file_results else None
            filename = file_result.relative_path if file_result else "unknown"
            return config.alerts.single_file_message.format(
                filename=filename,
                types=types_str,
            )
        else:
            # Multiple files
            return config.alerts.multiple_files_message.format(
                count=self.files_with_secrets,
                types=types_str,
            )

    def to_alert_data(self) -> dict[str, Any]:
        """Convert to alert event data for SSE."""
        return {
            "session_id": self.session_id,
            "files_scanned": self.files_scanned,
            "files_with_secrets": self.files_with_secrets,
            "total_secrets": self.total_secrets,
            "secret_types": list(self.secret_types),
            "type_labels": [get_type_label(t) for t in self.secret_types],
            "message": self.get_alert_message(),
            "files": [
                {
                    "path": fr.relative_path,
                    "secrets_count": fr.scan_result.secret_count,
                    "redacted": fr.redacted,
                }
                for fr in self.file_results
                if fr.scan_result.has_secrets
            ],
        }


def _should_scan_file(
    file_path: Path,
    relative_path: str,
    config: Any,
    reference_time: float,
) -> bool:
    """
    Determine if a file should be scanned.

    Args:
        file_path: Absolute path to the file
        relative_path: Path relative to workspace root
        config: Scanner configuration
        reference_time: Reference timestamp (request completion time)

    Returns:
        True if file should be scanned
    """
    session_config = config.session_scan

    # Check if file exists and is regular file
    if not file_path.exists() or not file_path.is_file():
        return False

    # Check if in read-only mount path
    for ro_path in session_config.readonly_mount_paths:
        if relative_path.startswith(ro_path):
            return False

    # Check skip patterns
    for pattern in session_config.skip_patterns:
        if fnmatch(relative_path, pattern) or fnmatch(file_path.name, pattern):
            return False

    # Check file extension
    if session_config.scan_extensions:
        ext = file_path.suffix.lower()
        if ext not in session_config.scan_extensions:
            return False

    # Check file size
    try:
        file_size = file_path.stat().st_size
        if file_size > session_config.max_file_size_bytes:
            return False
        if file_size == 0:
            return False
    except OSError:
        return False

    # Check modification time (only scan recent files)
    try:
        mtime = file_path.stat().st_mtime
        age_seconds = reference_time - mtime
        if age_seconds > session_config.recent_files_window_seconds:
            return False
    except OSError:
        return False

    return True


def _is_text_file(file_path: Path) -> bool:
    """Check if file is likely a text file (not binary)."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(8192)
            # Check for null bytes (common in binary files)
            if b"\x00" in chunk:
                return False
            return True
    except Exception:
        return False


async def scan_session_files(
    session_id: str,
    workspace_path: Path,
    reference_time: Optional[float] = None,
    redact_files: bool = True,
) -> SessionScanResult:
    """
    Scan session workspace files for sensitive data.

    This is the main entry point for post-request session scanning.

    Args:
        session_id: The session ID
        workspace_path: Path to session workspace directory
        reference_time: Reference timestamp for "recent" files (default: now)
        redact_files: Whether to redact detected secrets in files

    Returns:
        SessionScanResult with scan details
    """
    start_time = time.time()
    result = SessionScanResult(
        session_id=session_id,
        files_scanned=0,
        files_with_secrets=0,
        total_secrets=0,
    )

    # Check if scanner is enabled
    if not is_scanner_enabled():
        return result

    config = get_scanner_config()
    session_config = config.session_scan

    if not session_config.enabled:
        return result

    # Ensure workspace exists
    if not workspace_path.exists():
        result.errors.append(f"Workspace not found: {workspace_path}")
        return result

    reference_time = reference_time or time.time()
    scanner = get_scanner()

    # Collect files to scan with depth and count limits
    files_to_scan: list[tuple[Path, str]] = []

    def collect_files(dir_path: Path, current_depth: int = 0):
        """Recursively collect files to scan."""
        if current_depth > session_config.max_depth:
            return

        if len(files_to_scan) >= session_config.max_files:
            return

        try:
            for entry in dir_path.iterdir():
                if len(files_to_scan) >= session_config.max_files:
                    return

                try:
                    relative_path = str(entry.relative_to(workspace_path))
                except ValueError:
                    continue

                if entry.is_dir():
                    # Skip read-only mounts
                    skip = False
                    for ro_path in session_config.readonly_mount_paths:
                        if relative_path.startswith(ro_path):
                            skip = True
                            break
                    if not skip:
                        collect_files(entry, current_depth + 1)
                elif entry.is_file():
                    if _should_scan_file(entry, relative_path, config, reference_time):
                        files_to_scan.append((entry, relative_path))
        except PermissionError:
            pass
        except OSError as e:
            logger.debug(f"Error iterating directory {dir_path}: {e}")

    # Collect files
    collect_files(workspace_path)

    logger.info(
        f"Session {session_id}: Found {len(files_to_scan)} files to scan "
        f"(max: {session_config.max_files}, depth: {session_config.max_depth})"
    )

    # Scan each file
    for file_path, relative_path in files_to_scan:
        try:
            # Skip binary files
            if not _is_text_file(file_path):
                continue

            # Read file content
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug(f"Failed to read {file_path}: {e}")
                continue

            # Scan content
            scan_result = scanner.scan(content)
            result.files_scanned += 1

            if scan_result.has_secrets:
                result.files_with_secrets += 1
                result.total_secrets += scan_result.secret_count
                result.secret_types.update(scan_result.secret_types)

                file_result = FileScanResult(
                    file_path=file_path,
                    relative_path=relative_path,
                    scan_result=scan_result,
                )

                # Redact if configured
                if redact_files:
                    try:
                        file_path.write_text(
                            scan_result.redacted_text, encoding="utf-8"
                        )
                        file_result.redacted = True
                        logger.info(
                            f"Session {session_id}: Redacted {scan_result.secret_count} "
                            f"secrets in {relative_path}"
                        )
                    except Exception as e:
                        file_result.error = str(e)
                        logger.error(f"Failed to redact {file_path}: {e}")

                result.file_results.append(file_result)

        except Exception as e:
            error_msg = f"Error scanning {relative_path}: {e}"
            result.errors.append(error_msg)
            logger.warning(error_msg)

    result.duration_ms = (time.time() - start_time) * 1000

    if result.has_secrets:
        logger.warning(
            f"Session {session_id}: Found {result.total_secrets} secrets "
            f"in {result.files_with_secrets} files (scan took {result.duration_ms:.1f}ms)"
        )
    else:
        logger.debug(
            f"Session {session_id}: Scanned {result.files_scanned} files, "
            f"no secrets found (took {result.duration_ms:.1f}ms)"
        )

    return result


async def emit_security_alert(
    session_id: str,
    scan_result: SessionScanResult,
    event_queue: Optional[asyncio.Queue] = None,
) -> None:
    """
    Emit security alert event via SSE.

    Args:
        session_id: The session ID
        scan_result: The scan result containing detection details
        event_queue: Optional queue to emit events to (for tracer integration)
    """
    if not scan_result.has_secrets:
        return

    config = get_scanner_config()
    if not config.alerts.sse_enabled:
        return

    alert_data = scan_result.to_alert_data()

    # Log alert if configured
    if config.alerts.log_enabled:
        logger.warning(
            f"SECURITY ALERT - Session {session_id}: {alert_data['message']}"
        )

    # Create SSE event
    event = {
        "type": "security_alert",
        "session_id": session_id,
        "data": alert_data,
    }

    # Emit via event queue if provided
    if event_queue:
        try:
            await event_queue.put(event)
        except Exception as e:
            logger.error(f"Failed to emit security alert: {e}")
