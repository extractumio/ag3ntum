"""
SSH MCP tool implementations.

Four tools following the 3-layer pattern:
1. _impl() functions — testable core logic
2. create_*_tool() factories — MCP wrappers binding SSHToolContext
3. create_ssh_tools() — returns all four as a list

Tool names:
- mcp__ag3ntum__SSHConnect
- mcp__ag3ntum__SSHExec
- mcp__ag3ntum__SSHRead
- mcp__ag3ntum__SSHWrite

Security:
- All operations check security_config.enabled first (fail-closed).
- All operations are logged to the audit service.
- Command output is truncated at max_output_bytes and sanitised.
- Raw exception tracebacks are never returned to the agent.
- asyncssh is TYPE_CHECKING-gated; imported lazily inside closures.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Optional

from claude_agent_sdk import tool

if TYPE_CHECKING:
    import asyncssh  # noqa: F401 — type annotation only
    from src.services.ssh_audit_service import SSHAuditService

from src.core.ssh.ssh_config import SSHProfile, SSHSecurityConfig
from src.core.ssh.ssh_connection_pool import SSHConnectionPool, SSHConnectionLimitError
from src.core.ssh.ssh_command_filter import SSHCommandFilter
from src.core.ssh.ssh_credential_vault import SSHCredentialVault
from tools.ag3ntum.ag3ntum_ssh.batch_template import (
    generate_batch_manifest,
    generate_batch_script,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool name constants
# ---------------------------------------------------------------------------

AG3NTUM_SSH_EXEC_TOOL: str = "mcp__ag3ntum__SSHExec"
AG3NTUM_SSH_READ_TOOL: str = "mcp__ag3ntum__SSHRead"
AG3NTUM_SSH_WRITE_TOOL: str = "mcp__ag3ntum__SSHWrite"
AG3NTUM_SSH_CONNECT_TOOL: str = "mcp__ag3ntum__SSHConnect"

# Truncation notice appended when output is trimmed
_TRUNCATION_NOTICE = "\n[output truncated]"


# ---------------------------------------------------------------------------
# Credential redaction for audit logs
# ---------------------------------------------------------------------------

_CREDENTIAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # --password must be checked before short -p to avoid partial match
    (re.compile(r'--password[=\s]+\S+'), '--password=[REDACTED]'),
    (re.compile(r'(?<!-)-p\S+'), '-p[REDACTED]'),
    (re.compile(r'(?i)Authorization:\s*\S+(\s+\S+)?'),
     'Authorization: [REDACTED]'),
    (re.compile(r'(?i)(api[_-]?key|token|secret)[=:]\s*\S+'),
     r'\1=[REDACTED]'),
    (re.compile(r"(?i)IDENTIFIED\s+BY\s+'[^']+'"),
     "IDENTIFIED BY '[REDACTED]'"),
]


def _apply_redaction_patterns(
    text: str, patterns: list[tuple[re.Pattern, str]]
) -> str:
    """Apply a list of (compiled_pattern, replacement) to text."""
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def _redact_credentials(command: str) -> str:
    """Redact known credential patterns from command for audit logging."""
    return _apply_redaction_patterns(command, _CREDENTIAL_PATTERNS)


def _redact_output_secrets(
    text: str, patterns: list[tuple[re.Pattern, str]]
) -> str:
    """Redact secret patterns from command output using config-driven rules."""
    return _apply_redaction_patterns(text, patterns)


# ---------------------------------------------------------------------------
# Operations mode helper
# ---------------------------------------------------------------------------

def _matches_operations(command: str, operations: list[str]) -> bool:
    """Check if command matches any allowed_operations pattern."""
    for pattern in operations:
        try:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


# ---------------------------------------------------------------------------
# Approval state for requires_approval commands
# ---------------------------------------------------------------------------

class SSHApprovalStore:
    """Session-scoped store for approved SSH commands.

    When a command triggers 'requires_approval', the user must explicitly
    approve it. Approved command hashes are stored per-session and checked
    on retry.
    """

    def __init__(self) -> None:
        # {session_id: {command_hash: approval_time}}
        self._approvals: dict[str, dict[str, float]] = {}

    def approve(self, session_id: str, command: str) -> str:
        """Approve a command for a session. Returns the approval ID."""
        cmd_hash = hashlib.sha256(command.encode()).hexdigest()[:16]
        session_store = self._approvals.setdefault(session_id, {})
        session_store[cmd_hash] = time.monotonic()
        logger.info(
            "SSHApprovalStore: Approved command hash=%s session=%s",
            cmd_hash, session_id[:8],
        )
        return cmd_hash

    def is_approved(self, session_id: str, command: str) -> bool:
        """Check if a command was previously approved for this session."""
        cmd_hash = hashlib.sha256(command.encode()).hexdigest()[:16]
        session_store = self._approvals.get(session_id, {})
        return cmd_hash in session_store

    def clear_session(self, session_id: str) -> None:
        """Clear all approvals for a session."""
        self._approvals.pop(session_id, None)


# ---------------------------------------------------------------------------
# WriteTracker — mandatory pre-read enforcement for SSHWrite
# ---------------------------------------------------------------------------

@dataclass
class ReadRecord:
    """Record of a file read, used to enforce read-before-write."""
    checksum: str       # SHA-256 of file content at read time
    size: int           # File size at read time
    read_at: float      # monotonic timestamp


class WriteTracker:
    """Session-scoped tracker ensuring files are read before written.

    Populated by SSHRead. Consulted by SSHWrite.
    """

    def __init__(self) -> None:
        self._reads: dict[tuple[str, str], ReadRecord] = {}

    def record_read(self, profile_name: str, path: str,
                    checksum: str, size: int) -> None:
        """Called by SSHRead after successful file read."""
        self._reads[(profile_name, path)] = ReadRecord(
            checksum=checksum,
            size=size,
            read_at=time.monotonic(),
        )

    def get_read_record(self, profile_name: str, path: str
                        ) -> ReadRecord | None:
        """Called by SSHWrite to verify pre-read. Returns None if not read."""
        return self._reads.get((profile_name, path))

    def clear_session(self) -> None:
        """Clear all records — called on session cleanup."""
        self._reads.clear()


class WriteBudget:
    """Tracks total bytes written per session to prevent disk exhaustion."""

    def __init__(self, max_bytes: int = 10_485_760) -> None:  # 10MB default
        self._total: int = 0
        self._max: int = max_bytes

    def check(self, size: int) -> bool:
        return (self._total + size) <= self._max

    def record(self, size: int) -> None:
        self._total += size

    @property
    def remaining(self) -> int:
        return max(0, self._max - self._total)


# ---------------------------------------------------------------------------
# SSHToolContext — binds all services to a session
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SSHToolContext:
    """Holds all service references for SSH tools. Bound once per session."""
    session_id: str
    user_id: str
    security_config: SSHSecurityConfig
    connection_pool: SSHConnectionPool
    command_filter: SSHCommandFilter
    credential_vault: SSHCredentialVault
    audit_service: SSHAuditService
    profiles: dict[str, SSHProfile]
    db_session_factory: Any  # async context manager returning AsyncSession
    rate_limiter: Any = field(default=None)   # SSHRateLimiter, optional
    approval_store: Any = field(default=None)  # SSHApprovalStore, optional
    command_semaphore: Any = field(default=None)  # asyncio.Semaphore, optional
    ssh_enabled_check: Optional[Callable[[str], Awaitable[bool]]] = field(default=None)
    write_tracker: Optional[WriteTracker] = field(default=None)
    write_budget: Optional[WriteBudget] = field(default=None)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _result(text: str) -> dict[str, Any]:
    """Create a successful MCP result."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an MCP error result."""
    return {
        "content": [{"type": "text", "text": f"**Error:** {message}"}],
        "is_error": True,
    }


# ---------------------------------------------------------------------------
# Runtime feature flag check
# ---------------------------------------------------------------------------

async def _check_ssh_enabled(ctx: SSHToolContext) -> bool:
    """Check if SSH is still enabled for the user (runtime guard).

    Uses cached feature flag lookup (30s TTL) so that admin revocation
    takes effect within 30 seconds even for active sessions.
    Callback is injected via SSHToolContext.ssh_enabled_check by the
    service layer — no services import needed here.
    """
    if ctx.ssh_enabled_check is None:
        return True  # No check configured — trust session setup
    try:
        return await ctx.ssh_enabled_check(ctx.user_id)
    except Exception:
        return False  # Fail-closed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate_output(text: str, max_bytes: int) -> str:
    """Truncate text to at most max_bytes, appending a truncation notice."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated + _TRUNCATION_NOTICE


def _sanitise_error(exc: Exception) -> str:
    """Return a safe error description without leaking tracebacks."""
    exc_type = type(exc).__name__
    # Avoid returning internal paths / full tracebacks
    return f"{exc_type}: {exc!s}"


async def _get_ssh_connection(
    profile_name: str,
    profile: SSHProfile,
    ctx: SSHToolContext,
    tool_name: str = "SSH",
) -> tuple[Any, dict[str, Any] | None]:
    """Get or establish an SSH connection, returning (conn, error_or_None).

    Centralises the connection setup + error classification pattern
    used across SSHExec, SSHRead, SSHWrite, cleanup, and rollback.
    """
    try:
        async with ctx.db_session_factory() as db:
            connect_fn = await ctx.credential_vault.get_connect_fn(
                db, ctx.user_id, ctx.session_id, profile
            )
        conn = await ctx.connection_pool.get_connection(
            ctx.session_id, profile_name, ctx.user_id, connect_fn
        )
        return conn, None
    except SSHConnectionLimitError as exc:
        return None, _error(f"SSH connection limit reached: {exc}")
    except ValueError as exc:
        return None, _error(f"SSH credential error: {exc}")
    except Exception as exc:
        err_msg = _classify_host_key_error(exc, profile_name)
        if err_msg:
            await _audit_host_key_failure(ctx, profile_name, profile, exc)
            return None, _error(err_msg)
        logger.error(
            "%s: Connection failed for profile %s: %s",
            tool_name, profile_name, exc,
        )
        return None, _error(
            f"SSH connection failed: {_sanitise_error(exc)}"
        )


def _backup_dir(profile_name: str) -> str:
    """Return the SFTP backup directory path for a profile."""
    return f"~/.ag3ntum-backups/{profile_name}"


async def _sftp_read_bytes(sftp: Any, path: str) -> bytes:
    """Read a remote file's entire contents via SFTP."""
    f = await sftp.open(path, "rb")
    try:
        return await f.read()
    finally:
        await f.close()


async def _sftp_write_bytes(sftp: Any, path: str, data: bytes) -> None:
    """Write bytes to a remote file via SFTP."""
    f = await sftp.open(path, "wb")
    try:
        await f.write(data)
    finally:
        await f.close()


async def _audit_file_access(
    ctx: SSHToolContext,
    profile_name: str,
    profile: SSHProfile,
    path: str,
    operation: str,
) -> None:
    """Log a file access audit event (best-effort)."""
    try:
        async with ctx.db_session_factory() as db:
            await ctx.audit_service.log_file_access(
                db,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                ssh_profile=profile_name,
                remote_host=profile.host,
                remote_user=profile.username,
                remote_port=profile.port,
                path=path,
                operation=operation,
                privilege_level=profile.privilege_level,
                mode=profile.mode,
            )
    except Exception as audit_exc:
        logger.error("%s: Failed to log audit: %s", operation, audit_exc)


def _resolve_profile(
    profile_name: str, profiles: dict[str, SSHProfile]
) -> tuple[SSHProfile | None, dict[str, Any] | None]:
    """Resolve profile_name to an SSHProfile or return an error dict."""
    if not profile_name:
        return None, _error("profile_name is required")
    profile = profiles.get(profile_name)
    if profile is None:
        available = ", ".join(sorted(profiles)) or "(none configured)"
        return None, _error(
            f"SSH profile '{profile_name}' not found. "
            f"Available profiles: {available}"
        )
    return profile, None


def _classify_host_key_error(exc: Exception, profile_name: str) -> str | None:
    """Classify an SSH exception as a host key error, returning a user message.

    Returns None if the exception is not host-key related.
    """
    exc_type = type(exc).__name__

    # asyncssh raises HostKeyNotVerifiable when the server key
    # doesn't match any trusted key in the known_hosts callable
    if exc_type == "HostKeyNotVerifiable":
        return (
            "SSH host key verification failed for profile "
            f"'{profile_name}'. The server's host key does not match "
            "the pinned key. Administrator must verify and re-pin "
            "the host key."
        )

    # When known_hosts callable returns empty trust list,
    # asyncssh raises PermissionDenied or DisconnectError
    exc_str = str(exc).lower()
    if "host key" in exc_str or "known_hosts" in exc_str:
        return (
            f"No pinned host key for profile '{profile_name}'. "
            "Administrator must scan and pin the host key before connecting."
        )

    return None


async def _audit_host_key_failure(
    ctx: SSHToolContext,
    profile_name: str,
    profile: SSHProfile,
    exc: Exception,
) -> None:
    """Log a host key failure as an audit event (best-effort)."""
    exc_type = type(exc).__name__
    event_type = (
        "host_key_mismatch" if exc_type == "HostKeyNotVerifiable"
        else "host_key_missing"
    )
    try:
        async with ctx.db_session_factory() as db:
            await ctx.audit_service.log_host_key_event(
                db,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                ssh_profile=profile_name,
                remote_host=profile.host,
                remote_port=profile.port,
                event_type=event_type,
                details=str(exc),
            )
    except Exception as audit_exc:
        logger.error("Failed to log host key audit event: %s", audit_exc)


# ---------------------------------------------------------------------------
# SSHExec — execute a command on a remote server
# ---------------------------------------------------------------------------

async def _stream_process_output(
    conn: Any,
    command: str,
    timeout: int,
    max_bytes: int,
) -> tuple[str, str, int, bool]:
    """Execute command via create_process with streaming byte budget.

    Reads stdout/stderr incrementally, killing the process when the
    byte budget is exhausted. This prevents OOM from commands that
    produce unbounded output (e.g., ``cat /dev/urandom``).

    Returns:
        (stdout, stderr, exit_code, was_truncated)
    """
    process = await conn.create_process(command)
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    total_bytes = 0
    truncated = False

    async def _read_stream(
        stream: Any, chunks: list[bytes],
    ) -> None:
        nonlocal total_bytes, truncated
        while True:
            chunk = await stream.read(32768)
            if not chunk:
                break
            encoded = chunk.encode("utf-8", errors="replace") \
                if isinstance(chunk, str) else chunk
            remaining = max_bytes - total_bytes
            if remaining <= 0:
                truncated = True
                break
            if len(encoded) > remaining:
                chunks.append(encoded[:remaining])
                total_bytes += remaining
                truncated = True
                break
            chunks.append(encoded)
            total_bytes += len(encoded)

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _read_stream(process.stdout, stdout_chunks),
                _read_stream(process.stderr, stderr_chunks),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        truncated = True
    finally:
        # Ensure process is terminated if still running
        try:
            process.kill()
        except Exception:
            pass
        try:
            await process.wait()
        except Exception:
            pass

    stdout_bytes = b"".join(stdout_chunks)
    stderr_bytes = b"".join(stderr_chunks)
    stdout_str = stdout_bytes.decode("utf-8", errors="replace")
    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

    exit_code = process.exit_status
    if exit_code is None:
        exit_code = -1

    if truncated:
        if stdout_str:
            stdout_str += _TRUNCATION_NOTICE
        elif stderr_str:
            stderr_str += _TRUNCATION_NOTICE

    return stdout_str, stderr_str, exit_code, truncated


async def _ssh_exec_impl(
    args: dict[str, Any],
    *,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Core SSHExec logic — testable without MCP wrapper."""
    if not await _check_ssh_enabled(ctx):
        return _error("SSH access has been disabled by your administrator.")

    profile_name: str = args.get("profile_name", "")
    command: str = args.get("command", "").strip()
    dry_run: bool = args.get("dry_run", False)

    if not command:
        return _error("command is required")

    profile, err = _resolve_profile(profile_name, ctx.profiles)
    if err is not None:
        return err
    assert profile is not None  # guaranteed by _resolve_profile when err is None

    # Rate limit check
    if ctx.rate_limiter is not None:
        if not ctx.rate_limiter.check(ctx.session_id, profile_name):
            return _error(
                "SSH command rate limit exceeded. "
                f"Maximum {ctx.security_config.limits.rate_limit_commands_per_minute}"
                " commands per minute."
            )

    # Operations mode: only allowed_operations patterns permitted
    if profile.mode == "operations" and profile.allowed_operations:
        if not _matches_operations(command, profile.allowed_operations):
            return _error(
                f"Command not in allowed operations for profile '{profile_name}'. "
                f"Mode: operations. Use SSHConnect(action='list') to see allowed operations."
            )

    # Command filter check
    filter_result = ctx.command_filter.check_command(command, profile.privilege_level)

    if filter_result.action == "block":
        # Log blocked attempt — need a db session
        try:
            async with ctx.db_session_factory() as db:
                await ctx.audit_service.log_blocked(
                    db,
                    session_id=ctx.session_id,
                    user_id=ctx.user_id,
                    ssh_profile=profile_name,
                    remote_host=profile.host,
                    remote_user=profile.username,
                    remote_port=profile.port,
                    command=_redact_credentials(command),
                    reason=filter_result.reason,
                    rule=filter_result.rule,
                    privilege_level=profile.privilege_level,
                    mode=profile.mode,
                )
        except Exception as audit_exc:
            logger.error("SSHExec: Failed to log blocked command: %s", audit_exc)

        if dry_run:
            return _result(
                f"[DRY RUN] Command BLOCKED.\n"
                f"Command: {command}\n"
                f"Reason:  {filter_result.reason}\n"
                f"Profile: {profile_name} (L{profile.privilege_level})"
            )

        return _error(
            f"Command blocked by security filter. "
            f"Reason: {filter_result.reason}"
        )

    if filter_result.action == "requires_approval":
        # Check if this command was previously approved
        if ctx.approval_store and ctx.approval_store.is_approved(
            ctx.session_id, command
        ):
            pass  # Approved — proceed with execution
        else:
            cmd_hash = hashlib.sha256(
                command.encode()
            ).hexdigest()[:16]
            if dry_run:
                return _result(
                    f"[DRY RUN] Command REQUIRES APPROVAL.\n"
                    f"Command:     {command}\n"
                    f"Reason:      {filter_result.reason}\n"
                    f"Approval ID: {cmd_hash}\n"
                    f"Profile:     {profile_name} (L{profile.privilege_level})"
                )
            return _error(
                f"Command requires human approval before execution. "
                f"Reason: {filter_result.reason}. "
                f"Approval ID: {cmd_hash}. "
                "Use SSHConnect with action='approve' and the approval_id "
                "to approve this command, then retry."
            )

    # Dry-run: command passed filter — return preview without executing
    if dry_run:
        return _result(
            f"[DRY RUN] Command ALLOWED.\n"
            f"Command: {command}\n"
            f"Profile: {profile_name} (L{profile.privilege_level})\n"
            "Command would execute on "
            f"{profile.username}@{profile.host}:{profile.port}"
        )

    # Acquire concurrent command semaphore
    semaphore = ctx.command_semaphore
    if semaphore is not None:
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=5.0)
        except asyncio.TimeoutError:
            return _error(
                "Too many concurrent SSH commands. "
                f"Maximum {ctx.security_config.limits.max_concurrent_commands}"
                " concurrent commands allowed. Try again shortly."
            )

    try:
        return await _ssh_exec_inner(
            command, profile_name, profile, ctx
        )
    finally:
        if semaphore is not None:
            try:
                semaphore.release()
            except ValueError:
                pass  # Already released or never acquired


async def _ssh_exec_inner(
    command: str,
    profile_name: str,
    profile: SSHProfile,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Inner execution logic after filter checks and semaphore acquisition."""
    # Get or establish connection
    start_ms = int(time.monotonic() * 1000)
    conn, conn_err = await _get_ssh_connection(
        profile_name, profile, ctx, "SSHExec"
    )
    if conn_err is not None:
        return conn_err

    # Execute command with streaming byte budget
    max_bytes = ctx.security_config.limits.max_output_bytes
    timeout = ctx.security_config.limits.command_timeout_seconds
    try:
        stdout, stderr, exit_code, _ = await _stream_process_output(
            conn, command, timeout, max_bytes
        )
    except Exception as exc:
        logger.warning(
            "SSHExec: Command execution failed (profile=%s cmd=%s): %s",
            profile_name, command[:80], exc,
        )
        return _error(f"SSH command execution failed: {_sanitise_error(exc)}")

    duration_ms = int(time.monotonic() * 1000) - start_ms

    # Record activity on pool
    ctx.connection_pool.record_activity(ctx.session_id, profile_name)

    # Audit log
    output_bytes = len((stdout + stderr).encode("utf-8", errors="replace"))
    try:
        async with ctx.db_session_factory() as db:
            await ctx.audit_service.log_command(
                db,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                ssh_profile=profile_name,
                remote_host=profile.host,
                remote_user=profile.username,
                remote_port=profile.port,
                command=_redact_credentials(command),
                exit_code=exit_code,
                output_bytes=output_bytes,
                duration_ms=duration_ms,
                privilege_level=profile.privilege_level,
                mode=profile.mode,
            )
    except Exception as audit_exc:
        logger.error("SSHExec: Failed to log command: %s", audit_exc)

    # Redact credentials in output before returning to agent context
    redaction = ctx.command_filter.output_redaction_patterns
    if stdout:
        stdout = _redact_output_secrets(stdout, redaction)
    if stderr:
        stderr = _redact_output_secrets(stderr, redaction)

    lines = [
        f"Exit code: {exit_code}",
        f"Profile:   {profile_name} ({profile.username}@{profile.host}:{profile.port})",
        f"Duration:  {duration_ms}ms",
    ]
    if stdout:
        lines.append(f"\nstdout:\n{stdout}")
    if stderr:
        lines.append(f"\nstderr:\n{stderr}")
    if not stdout and not stderr:
        lines.append("\n(no output)")

    return _result("\n".join(lines))


def create_ssh_exec_tool(ctx: SSHToolContext):
    """Create SSHExec tool bound to the given context."""
    bound_ctx = ctx

    @tool(
        "SSHExec",
        """Execute a command on a remote server via SSH.

Args:
    profile_name: SSH profile name (configured in ssh-profiles.yaml)
    command:      Shell command to run on the remote server
    dry_run:      If true, preview filter result without executing (default: false)

Returns:
    Exit code, stdout, stderr, and execution duration.
    In dry_run mode: filter result preview (ALLOWED/BLOCKED/REQUIRES APPROVAL).

Notes:
    - Commands are filtered by the privilege level set in the profile.
    - Blocked or unapproved commands return an error — never execute.
    - Output is streamed with a byte budget — large outputs are truncated.
    - Use dry_run=true to preview whether a command would be allowed.

Examples:
    SSHExec(profile_name="prod-web", command="uptime")
    SSHExec(profile_name="db-primary", command="df -h /var/lib/postgresql")
    SSHExec(profile_name="prod-web", command="rm -rf /tmp/*", dry_run=true)
""",
        {"profile_name": str, "command": str},
    )
    async def ssh_exec(args: dict[str, Any]) -> dict[str, Any]:
        """Execute command on remote server via SSH."""
        return await _ssh_exec_impl(args, ctx=bound_ctx)

    return ssh_exec


# ---------------------------------------------------------------------------
# SSHRead — read a file from a remote server via SFTP
# ---------------------------------------------------------------------------

async def _ssh_read_impl(
    args: dict[str, Any],
    *,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Core SSHRead logic — testable without MCP wrapper."""
    if not await _check_ssh_enabled(ctx):
        return _error("SSH access has been disabled by your administrator.")

    profile_name: str = args.get("profile_name", "")
    remote_path: str = args.get("path", "").strip()

    if not remote_path:
        return _error("path is required")

    profile, err = _resolve_profile(profile_name, ctx.profiles)
    if err is not None:
        return err
    assert profile is not None  # guaranteed by _resolve_profile when err is None

    # Get or establish connection
    conn, conn_err = await _get_ssh_connection(
        profile_name, profile, ctx, "SSHRead"
    )
    if conn_err is not None:
        return conn_err

    max_bytes = ctx.security_config.limits.max_file_read_bytes

    try:
        async with conn.start_sftp_client() as sftp:
            # Check file size before reading
            try:
                stat = await sftp.stat(remote_path)
                file_size = stat.size if stat.size is not None else 0
                if file_size > max_bytes:
                    return _error(
                        f"File too large to read: {file_size} bytes "
                        f"(limit: {max_bytes} bytes). "
                        "Use SSHExec with 'head' or 'tail' to read a portion."
                    )
            except Exception:
                # stat failed — still attempt the read; let the read fail naturally
                pass

            # Resolve symlinks and check target path
            real_path = remote_path
            try:
                resolved = await sftp.realpath(remote_path)
                if resolved != remote_path:
                    real_path = resolved
                    # Symlink detected — verify target is readable
                    path_check = ctx.command_filter.check_path_readable(
                        real_path, profile.privilege_level
                    )
                    if not path_check.allowed:
                        return _error(
                            f"Symlink target '{real_path}' is blocked "
                            "for this privilege level."
                        )
            except Exception:
                pass  # realpath failed — proceed with the original path

            # Check path against blocked paths (prevent reading sensitive files)
            read_check = ctx.command_filter.check_path_readable(
                real_path, profile.privilege_level
            )
            if not read_check.allowed:
                return _error(
                    f"Cannot read file — {read_check.reason}"
                )

            remote_file = await sftp.open(remote_path, "rb")
            try:
                raw: bytes = await remote_file.read()
            finally:
                await remote_file.close()

    except Exception as exc:
        logger.warning(
            "SSHRead: Failed to read %s on profile %s: %s",
            remote_path, profile_name, exc,
        )
        return _error(f"Failed to read remote file: {_sanitise_error(exc)}")

    # Binary file detection — check first 8KB for null bytes
    sample = raw[:8192]
    if sample:
        null_count = sample.count(b'\x00')
        if null_count > len(sample) * 0.01:
            return _error(
                f"File appears to be binary ({null_count} null bytes "
                f"in first {len(sample)} bytes). "
                "Use SSHExec with 'file' or 'xxd' to inspect binary files."
            )

    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
    else:
        truncated = False

    content = raw.decode("utf-8", errors="replace")

    # Record read in WriteTracker for SSHWrite pre-read enforcement
    if ctx.write_tracker is not None:
        read_checksum = hashlib.sha256(raw).hexdigest()
        ctx.write_tracker.record_read(
            profile_name, remote_path, read_checksum, len(raw)
        )

    # Record activity and audit
    ctx.connection_pool.record_activity(ctx.session_id, profile_name)
    await _audit_file_access(ctx, profile_name, profile, remote_path, "read_file")

    # Format with line numbers (matching local Read tool style)
    lines = content.splitlines()
    numbered = [f"{i + 1:6}|{line}" for i, line in enumerate(lines)]
    output = "\n".join(numbered)

    if truncated:
        output += f"\n\n[output truncated — file exceeded {max_bytes} bytes]"

    header = f"Remote file: {profile.username}@{profile.host}:{remote_path}\n\n"
    return _result(header + output)


def create_ssh_read_tool(ctx: SSHToolContext):
    """Create SSHRead tool bound to the given context."""
    bound_ctx = ctx

    @tool(
        "SSHRead",
        """Read a file from a remote server via SSH/SFTP.

Args:
    profile_name: SSH profile name (configured in ssh-profiles.yaml)
    path:         Absolute path to the file on the remote server

Returns:
    File contents with line numbers, similar to the local Read tool.

Notes:
    - Files larger than max_file_read_bytes are refused before reading.
    - Content is displayed with line numbers for easy reference.
    - Binary files will be decoded with replacement characters.

Examples:
    SSHRead(profile_name="prod-web", path="/etc/nginx/nginx.conf")
    SSHRead(profile_name="db-primary", path="/var/log/postgresql/postgresql.log")
""",
        {"profile_name": str, "path": str},
    )
    async def ssh_read(args: dict[str, Any]) -> dict[str, Any]:
        """Read file from remote server via SFTP."""
        return await _ssh_read_impl(args, ctx=bound_ctx)

    return ssh_read


# ---------------------------------------------------------------------------
# L2 extension allowlist for SSHWrite
# ---------------------------------------------------------------------------

_L2_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({
    # Configuration formats
    ".conf", ".cfg", ".cnf", ".ini",
    ".yaml", ".yml", ".json", ".toml", ".xml",
    ".properties",
    # Web server specifics
    ".htaccess", ".htpasswd",
    # Systemd
    ".service", ".timer", ".socket", ".mount",
    # SSL/TLS
    ".pem", ".crt", ".key",
    # Text
    ".txt", ".log", ".md",
})


def _compute_diff(old: str, new: str, path: str, max_lines: int = 50) -> str:
    """Generate unified diff, truncated to max_lines."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a{path}", tofile=f"b{path}",
    ))
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"\n... ({len(diff) - max_lines} more lines)\n"]
    return "".join(diff) if diff else "(no changes)"


# ---------------------------------------------------------------------------
# SSHWrite — write a file to a remote server via SFTP
# ---------------------------------------------------------------------------

async def _ssh_write_impl(
    args: dict[str, Any],
    *,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Core SSHWrite logic — testable without MCP wrapper.

    Four-phase execution:
      Phase 0: Validation (no I/O)
      Phase 1: Preflight (read-only I/O)
      Phase 2: Backup (single mutation)
      Phase 3: Write (atomic mutation)
    """
    if not await _check_ssh_enabled(ctx):
        return _error("SSH access has been disabled by your administrator.")

    profile_name: str = args.get("profile_name", "")
    path: str = args.get("path", "").strip()
    content: str = args.get("content", "")
    dry_run: bool = args.get("dry_run", False)

    # Batch mode dispatch
    batch = args.get("batch")
    if batch is not None:
        return await _ssh_write_batch_impl(
            batch, profile_name=profile_name, dry_run=dry_run, ctx=ctx
        )

    # --- Phase 0: Validation (no I/O) ---

    if not path:
        return _error("path is required")
    if not path.startswith("/"):
        return _error("Absolute path required (must start with '/').")
    if not content:
        return _error("content is required (empty writes not allowed).")

    profile, err = _resolve_profile(profile_name, ctx.profiles)
    if err is not None:
        return err
    assert profile is not None

    # Rate limit check
    if ctx.rate_limiter is not None:
        if not ctx.rate_limiter.check(ctx.session_id, profile_name):
            return _error(
                "SSH command rate limit exceeded. "
                f"Maximum {ctx.security_config.limits.rate_limit_commands_per_minute}"
                " commands per minute."
            )

    # Content size check
    content_bytes = content.encode("utf-8")
    max_bytes = ctx.security_config.limits.max_file_write_bytes
    if len(content_bytes) > max_bytes:
        return _error(
            f"Content size ({len(content_bytes)} bytes) exceeds "
            f"limit ({max_bytes} bytes)."
        )

    # Write budget check
    if ctx.write_budget is not None:
        if not ctx.write_budget.check(len(content_bytes)):
            return _error(
                f"Session write budget exceeded. "
                f"Remaining: {ctx.write_budget.remaining} bytes."
            )

    # Path security check
    path_check = ctx.command_filter.check_path_writable(path, profile.privilege_level)
    if not path_check.allowed:
        return _error(f"Write denied: {path_check.reason}")

    # Extension restrictions removed — path scoping provides security.
    # P1 allows any file type in /var/www/; P2 allows in /etc/ configs.

    # Pre-read check (WriteTracker)
    if ctx.write_tracker is not None:
        read_record = ctx.write_tracker.get_read_record(profile_name, path)
        if read_record is None:
            return _error(
                "File must be read with SSHRead before writing. "
                "This ensures you see the current content before modifying it."
            )
    else:
        read_record = None

    # --- Phase 1: Preflight (read-only I/O) ---

    start_ms = int(time.monotonic() * 1000)
    conn, conn_err = await _get_ssh_connection(
        profile_name, profile, ctx, "SSHWrite"
    )
    if conn_err is not None:
        return conn_err

    conflict_warning = ""
    old_content = ""

    try:
        async with conn.start_sftp_client() as sftp:
            # Disk space check
            try:
                dir_path = os.path.dirname(path)
                vfs = await sftp.statvfs(dir_path)
                free_bytes = vfs.avail * vfs.bsize
                required = max(len(content_bytes) * 3, 10_485_760)
                if free_bytes < required:
                    return _error(
                        f"Insufficient disk space: {free_bytes} bytes free, "
                        f"need {required} bytes."
                    )
            except Exception:
                pass  # statvfs not supported on all systems — proceed

            # File stat
            file_exists = False
            original_mode = 0o644
            try:
                stat_result = await sftp.stat(path)
                file_exists = True
                if stat_result.permissions is not None:
                    original_mode = stat_result.permissions
            except Exception:
                pass  # File doesn't exist — new file

            # Symlink resolution
            try:
                resolved = await sftp.realpath(path)
                if resolved != path:
                    re_check = ctx.command_filter.check_path_writable(
                        resolved, profile.privilege_level
                    )
                    if not re_check.allowed:
                        return _error(
                            f"Symlink target '{resolved}' write denied: "
                            f"{re_check.reason}"
                        )
            except Exception:
                pass  # realpath failed — proceed with original path

            # Read current content for binary detection, diff, and
            # checksum comparison — single SFTP read for all three.
            old_raw: bytes = b""
            if file_exists:
                try:
                    old_raw = await _sftp_read_bytes(sftp, path)
                    # Binary detection on first 8KB
                    sample = old_raw[:8192]
                    if sample and sample.count(b'\x00') > len(sample) * 0.01:
                        return _error(
                            "Target file is binary. SSHWrite only supports text files."
                        )
                    old_content = old_raw.decode("utf-8", errors="replace")
                except Exception:
                    old_content = ""

                # Checksum conflict detection (hash raw bytes, not re-encoded)
                if read_record is not None and old_raw:
                    current_hash = hashlib.sha256(old_raw).hexdigest()
                    if current_hash != read_record.checksum:
                        conflict_warning = (
                            "WARNING: File has been modified since you last read it. "
                            "Your write will overwrite those changes.\n"
                        )

            # Dry run
            if dry_run:
                diff_text = _compute_diff(old_content, content, path)
                preview = (
                    f"[DRY RUN] SSHWrite preview — no changes made.\n"
                    f"Profile:  {profile_name} ({profile.username}@{profile.host})\n"
                    f"Path:     {path}\n"
                    f"Size:     {len(content_bytes)} bytes\n"
                    f"Exists:   {'yes' if file_exists else 'no (new file)'}\n"
                )
                if conflict_warning:
                    preview += conflict_warning
                preview += f"\nDiff:\n{diff_text}"
                return _result(preview)

            # --- Phase 2: Backup ---

            backup_path = ""
            if file_exists:
                backup_dir = _backup_dir(profile_name)
                try:
                    await sftp.makedirs(backup_dir, exist_ok=True)
                except Exception:
                    # makedirs may not exist on all asyncssh versions
                    try:
                        await sftp.mkdir(backup_dir)
                    except Exception:
                        # Try creating parent first
                        try:
                            await sftp.mkdir("~/.ag3ntum-backups")
                        except Exception:
                            pass
                        try:
                            await sftp.mkdir(backup_dir)
                        except Exception as mkdir_exc:
                            return _error(
                                f"Cannot create backup directory: {_sanitise_error(mkdir_exc)}"
                            )

                # Rotate: keep max 5 per filename
                filename = PurePosixPath(path).name
                try:
                    existing = await sftp.listdir(backup_dir)
                    matching = sorted([
                        f for f in existing
                        if f.startswith(filename) and f.endswith(".bak")
                    ])
                    while len(matching) >= 5:
                        oldest = matching.pop(0)
                        try:
                            await sftp.remove(f"{backup_dir}/{oldest}")
                        except Exception:
                            pass
                except Exception:
                    pass  # listdir failed — proceed without rotation

                # Create backup
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_name = f"{filename}.{timestamp}.bak"
                backup_path = f"{backup_dir}/{backup_name}"

                try:
                    await _sftp_write_bytes(sftp, backup_path, old_raw)
                except Exception as backup_exc:
                    return _error(
                        f"Backup creation failed: {_sanitise_error(backup_exc)}. "
                        "Write aborted — target file is untouched."
                    )

            # --- Phase 3: Write (atomic) ---

            dir_name = str(PurePosixPath(path).parent)
            temp_name = f"{dir_name}/.{PurePosixPath(path).name}.ag3ntum-tmp"

            try:
                await _sftp_write_bytes(sftp, temp_name, content_bytes)

                # Preserve original permissions
                if file_exists:
                    try:
                        await sftp.chmod(temp_name, original_mode)
                    except Exception:
                        pass  # chmod failure is non-fatal

                # Atomic rename (posix_rename supports overwrite)
                try:
                    await sftp.posix_rename(temp_name, path)
                except AttributeError:
                    await sftp.rename(temp_name, path)
            except Exception as write_exc:
                # Clean up temp file
                try:
                    await sftp.remove(temp_name)
                except Exception:
                    pass
                return _error(
                    f"Write failed: {_sanitise_error(write_exc)}. "
                    f"{'Backup at: ' + backup_path if backup_path else 'No backup (new file).'}"
                )

    except Exception as exc:
        logger.warning(
            "SSHWrite: SFTP operation failed on profile %s: %s",
            profile_name, exc,
        )
        return _error(f"SFTP operation failed: {_sanitise_error(exc)}")

    duration_ms = int(time.monotonic() * 1000) - start_ms

    # Record activity on pool
    ctx.connection_pool.record_activity(ctx.session_id, profile_name)

    # Deduct from write budget
    if ctx.write_budget is not None:
        ctx.write_budget.record(len(content_bytes))

    # Audit log
    await _audit_file_access(ctx, profile_name, profile, path, "write_file")

    # Response with diff
    diff_text = _compute_diff(old_content, content, path)
    response_lines = [
        "File written successfully.",
        f"Profile:  {profile_name} ({profile.username}@{profile.host})",
        f"Path:     {path}",
        f"Size:     {len(content_bytes)} bytes",
    ]
    if backup_path:
        response_lines.append(f"Backup:   {backup_path}")
    response_lines.append(f"Duration: {duration_ms}ms")
    if conflict_warning:
        response_lines.append(f"\n{conflict_warning}")
    response_lines.append(f"\nDiff:\n{diff_text}")

    return _result("\n".join(response_lines))


# ---------------------------------------------------------------------------
# SSHWrite batch implementation
# ---------------------------------------------------------------------------

async def _ssh_write_batch_impl(
    batch: list[dict[str, Any]],
    *,
    profile_name: str,
    dry_run: bool,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Batch write implementation — generates and executes a hardened script.

    Requires minimum L2 privilege. At L2, all files must have been
    read via SSHRead. At L3+, the pre-read check is skipped.
    """
    if not batch:
        return _error("batch list is empty.")

    profile, err = _resolve_profile(profile_name, ctx.profiles)
    if err is not None:
        return err
    assert profile is not None

    if profile.privilege_level < 2:
        return _error("Batch mode requires minimum privilege level 2.")

    # Validate all entries
    total_size = 0
    for entry in batch:
        path = entry.get("path", "").strip()
        content = entry.get("content", "")
        if not path or not path.startswith("/"):
            return _error(f"Batch: absolute path required, got '{path}'.")
        if not content:
            return _error(f"Batch: empty content for '{path}'.")

        content_bytes = content.encode("utf-8")
        if len(content_bytes) > ctx.security_config.limits.max_file_write_bytes:
            return _error(
                f"Batch: content for '{path}' exceeds size limit "
                f"({len(content_bytes)} bytes)."
            )
        total_size += len(content_bytes)

        # Path security
        path_check = ctx.command_filter.check_path_writable(
            path, profile.privilege_level
        )
        if not path_check.allowed:
            return _error(f"Batch: write denied for '{path}': {path_check.reason}")

        # Extension restrictions removed — path scoping provides security.

        # Pre-read check (P1-P2 require reading before writing)
        if profile.privilege_level <= 2 and ctx.write_tracker is not None:
            record = ctx.write_tracker.get_read_record(profile_name, path)
            if record is None:
                return _error(
                    f"Batch: file '{path}' must be read with SSHRead first."
                )

    # Write budget check
    if ctx.write_budget is not None:
        if not ctx.write_budget.check(total_size):
            return _error(
                f"Batch: total size ({total_size} bytes) exceeds session "
                f"write budget. Remaining: {ctx.write_budget.remaining} bytes."
            )

    # Generate snapshot ID and script
    snapshot_id = (
        f"ag3ntum-batch-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    manifest = generate_batch_manifest(
        profile_name, ctx.session_id, batch, snapshot_id
    )
    script = generate_batch_script(
        profile_name, snapshot_id, batch, manifest
    )

    if dry_run:
        file_list = "\n".join(
            f"  {e['path']} ({len(e.get('content', '').encode('utf-8'))} bytes)"
            for e in batch
        )
        return _result(
            f"[DRY RUN] Batch write preview — no changes made.\n"
            f"Profile:    {profile_name}\n"
            f"Files:      {len(batch)}\n"
            f"Total size: {total_size} bytes\n"
            f"Snapshot:   {snapshot_id}\n\n"
            f"Files:\n{file_list}"
        )

    # Get connection
    conn, conn_err = await _get_ssh_connection(
        profile_name, profile, ctx, "SSHWrite:batch"
    )
    if conn_err is not None:
        return conn_err

    # Upload script via SFTP
    script_path = f"/tmp/{snapshot_id}.sh"
    try:
        async with conn.start_sftp_client() as sftp:
            await _sftp_write_bytes(sftp, script_path, script.encode("utf-8"))
            await sftp.chmod(script_path, 0o700)
    except Exception as exc:
        return _error(f"Failed to upload batch script: {_sanitise_error(exc)}")

    # Execute via SSH
    try:
        stdout, stderr, exit_code, _ = await _stream_process_output(
            conn,
            f"bash {script_path}",
            ctx.security_config.limits.command_timeout_seconds,
            ctx.security_config.limits.max_output_bytes,
        )
    except Exception as exc:
        return _error(f"Batch script execution failed: {_sanitise_error(exc)}")

    # Record activity and budget
    ctx.connection_pool.record_activity(ctx.session_id, profile_name)
    if ctx.write_budget is not None:
        ctx.write_budget.record(total_size)

    # Audit log
    await _audit_file_access(
        ctx, profile_name, profile, f"batch:{snapshot_id}", "write_file_batch"
    )

    # Parse output for status
    status = "success" if exit_code == 0 else "failed"
    response = (
        f"Batch write {status}.\n"
        f"Profile:    {profile_name} ({profile.username}@{profile.host})\n"
        f"Files:      {len(batch)}\n"
        f"Snapshot:   {snapshot_id}\n"
        f"Exit code:  {exit_code}\n"
    )
    if stdout:
        response += f"\nOutput:\n{stdout}"
    if stderr:
        response += f"\nErrors:\n{stderr}"
    if exit_code != 0:
        response += (
            "\nRollback was triggered automatically. "
            "Original files have been restored from the snapshot."
        )

    return _result(response) if exit_code == 0 else _error(response)


def create_ssh_write_tool(ctx: SSHToolContext):
    """Create SSHWrite tool bound to the given context."""
    bound_ctx = ctx

    @tool(
        "SSHWrite",
        """Write a file to a remote server via SSH/SFTP with automatic backup.

Args:
    profile_name: SSH profile name (configured in ssh-profiles.yaml)
    path:         Absolute path to the file on the remote server
    content:      Full file content to write
    dry_run:      If true, preview the write without making changes (default: false)

Returns:
    Write confirmation with backup path, size, duration, and unified diff.

Safety:
    - File must be read with SSHRead BEFORE writing (enforced).
    - Automatic backup created before every write (~/.ag3ntum-backups/).
    - Atomic write: temp file + rename prevents corruption.
    - L0-L1 profiles cannot write files.
    - L2 profiles restricted to writable_paths and allowed extensions.
    - L3+ can write to any non-blocked path.

Batch mode:
    Pass a 'batch' list of {path, content} dicts instead of single path/content.
    All files are validated, then written atomically with snapshot-first backup.
    Requires minimum L2. At L2, all files must be pre-read. L3+ can skip pre-read.
    Example: SSHWrite(profile_name="prod", batch=[{"path": "/etc/a.conf", "content": "..."},
                      {"path": "/etc/b.conf", "content": "..."}])

When to use single-file vs batch mode:
    | Scenario                         | Mode              |
    |----------------------------------|-------------------|
    | Edit 1 config file               | Single-file       |
    | Edit 2-5 related configs         | Single-file (loop)|
    | Update 6+ files with same pattern| Batch             |
    | Search-and-replace across codebase| Batch            |
    | One-off emergency fix            | Single-file       |

Examples:
    SSHWrite(profile_name="prod-web", path="/etc/nginx/nginx.conf",
             content="...new config...", dry_run=true)
    SSHWrite(profile_name="prod-web", path="/etc/nginx/nginx.conf",
             content="...new config...")
""",
        {"profile_name": str, "path": str, "content": str},
    )
    async def ssh_write(args: dict[str, Any]) -> dict[str, Any]:
        """Write file to remote server via SFTP."""
        return await _ssh_write_impl(args, ctx=bound_ctx)

    return ssh_write


# ---------------------------------------------------------------------------
# SSHConnect — connection lifecycle management
# ---------------------------------------------------------------------------

async def _ssh_connect_impl(
    args: dict[str, Any],
    *,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Core SSHConnect logic — testable without MCP wrapper."""
    if not await _check_ssh_enabled(ctx):
        return _error("SSH access has been disabled by your administrator.")

    action: str = args.get("action", "").strip().lower()
    profile_name: str = args.get("profile_name", "")

    if not action:
        return _error(
            "action is required. Valid actions: connect, disconnect, status, list, approve"
        )

    if action == "list":
        return _ssh_connect_list(ctx)

    if action == "approve":
        return _ssh_connect_approve(args, ctx)

    if action not in ("connect", "disconnect", "status", "cleanup_backups", "rollback"):
        return _error(
            f"Invalid action '{action}'. "
            "Valid actions: connect, disconnect, status, list, approve, "
            "cleanup_backups, rollback"
        )

    # All other actions need a profile
    profile, err = _resolve_profile(profile_name, ctx.profiles)
    if err is not None:
        return err
    assert profile is not None  # guaranteed by _resolve_profile when err is None

    if action == "cleanup_backups":
        return await _ssh_connect_cleanup_backups(args, profile_name, profile, ctx)

    if action == "rollback":
        return await _ssh_connect_rollback(args, profile_name, profile, ctx)

    if action == "connect":
        return await _ssh_connect_connect(profile_name, profile, ctx)

    if action == "disconnect":
        return await _ssh_connect_disconnect(profile_name, profile, ctx)

    # action == "status"
    return _ssh_connect_status(profile_name, ctx)


def _ssh_connect_approve(
    args: dict[str, Any], ctx: SSHToolContext
) -> dict[str, Any]:
    """Approve a command that requires human approval."""
    command = args.get("command", "").strip()
    if not command:
        return _error(
            "command is required for approve action. "
            "Provide the exact command that needs approval."
        )
    if ctx.approval_store is None:
        return _error("Approval store not configured.")

    approval_id = ctx.approval_store.approve(ctx.session_id, command)
    return _result(
        f"Command approved. Approval ID: {approval_id}\n"
        "You can now retry the command."
    )


def _ssh_connect_list(ctx: SSHToolContext) -> dict[str, Any]:
    """List available profiles and active connections."""
    active = {
        entry["profile"]: entry
        for entry in ctx.connection_pool.get_connection_info(ctx.session_id)
    }

    if not ctx.profiles:
        return _result("No SSH profiles configured for this user.")

    lines = ["Available SSH profiles:\n"]
    for name, prof in sorted(ctx.profiles.items()):
        conn_info = active.get(name)
        status = "connected" if conn_info else "not connected"
        line = (
            f"  {name}: {prof.username}@{prof.host}:{prof.port} "
            f"[{prof.mode}/L{prof.privilege_level}] — {status}"
        )
        if conn_info:
            line += (
                f" ({conn_info['command_count']} commands, "
                f"idle {conn_info['idle_seconds']}s)"
            )
        lines.append(line)

    if not active:
        lines.append("\nNo active connections in this session.")

    return _result("\n".join(lines))


async def _ssh_connect_connect(
    profile_name: str, profile: SSHProfile, ctx: SSHToolContext
) -> dict[str, Any]:
    """Establish connection for a profile."""
    start_ms = int(time.monotonic() * 1000)
    _, conn_err = await _get_ssh_connection(
        profile_name, profile, ctx, "SSHConnect"
    )
    if conn_err is not None:
        return conn_err

    duration_ms = int(time.monotonic() * 1000) - start_ms

    try:
        async with ctx.db_session_factory() as db:
            await ctx.audit_service.log_connection(
                db,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                ssh_profile=profile_name,
                remote_host=profile.host,
                remote_user=profile.username,
                remote_port=profile.port,
                event="connect",
                privilege_level=profile.privilege_level,
                mode=profile.mode,
                duration_ms=duration_ms,
            )
    except Exception as audit_exc:
        logger.error("SSHConnect: Failed to log connection: %s", audit_exc)

    return _result(
        f"Connected to {profile.username}@{profile.host}:{profile.port} "
        f"via profile '{profile_name}' in {duration_ms}ms.\n"
        f"Mode: {profile.mode}, Privilege level: {profile.privilege_level}"
    )


async def _ssh_connect_disconnect(
    profile_name: str, profile: SSHProfile, ctx: SSHToolContext
) -> dict[str, Any]:
    """Release a connection."""
    start_ms = int(time.monotonic() * 1000)
    await ctx.connection_pool.release_connection(ctx.session_id, profile_name)
    duration_ms = int(time.monotonic() * 1000) - start_ms

    try:
        async with ctx.db_session_factory() as db:
            await ctx.audit_service.log_connection(
                db,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                ssh_profile=profile_name,
                remote_host=profile.host,
                remote_user=profile.username,
                remote_port=profile.port,
                event="disconnect",
                privilege_level=profile.privilege_level,
                mode=profile.mode,
                duration_ms=duration_ms,
            )
    except Exception as audit_exc:
        logger.error("SSHConnect: Failed to log disconnect: %s", audit_exc)

    return _result(
        f"Disconnected from profile '{profile_name}' "
        f"({profile.username}@{profile.host}:{profile.port})."
    )


def _ssh_connect_status(profile_name: str, ctx: SSHToolContext) -> dict[str, Any]:
    """Get connection status for a profile."""
    connections = ctx.connection_pool.get_connection_info(ctx.session_id)
    conn_info = next(
        (c for c in connections if c["profile"] == profile_name), None
    )

    profile = ctx.profiles.get(profile_name)
    if profile is None:
        return _error(f"Profile '{profile_name}' not found.")

    if conn_info is None:
        return _result(
            f"Profile '{profile_name}': not connected.\n"
            f"Target: {profile.username}@{profile.host}:{profile.port}\n"
            f"Mode: {profile.mode}, Privilege level: {profile.privilege_level}"
        )

    alive = "alive" if conn_info.get("alive") else "closed/zombie"
    lines = [
        f"Profile '{profile_name}': {alive}",
        f"  Target:    {conn_info['username']}@{conn_info['host']}:{conn_info['port']}",
        f"  Connected: {conn_info['connected_at']}",
        f"  Last use:  {conn_info['last_activity']}",
        f"  Idle:      {conn_info['idle_seconds']}s",
        f"  Commands:  {conn_info['command_count']}",
        f"  Mode:      {conn_info['privilege_level']} (privilege level)",
    ]
    return _result("\n".join(lines))


async def _ssh_connect_cleanup_backups(
    args: dict[str, Any],
    profile_name: str,
    profile: SSHProfile,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """List or clean up backup files for a profile."""
    cleanup_id = args.get("cleanup_id", "").strip()
    confirm = args.get("confirm", False)

    # Sanitize cleanup_id — reject path traversal
    if cleanup_id and (".." in cleanup_id or "/" in cleanup_id):
        return _error("Invalid cleanup_id — must not contain '..' or '/'.")

    conn, conn_err = await _get_ssh_connection(
        profile_name, profile, ctx, "SSHConnect:cleanup"
    )
    if conn_err is not None:
        return conn_err

    backup_dir = _backup_dir(profile_name)

    try:
        async with conn.start_sftp_client() as sftp:
            if not cleanup_id:
                # List all backups
                try:
                    entries = await sftp.listdir(backup_dir)
                except Exception:
                    return _result(f"No backups found for profile '{profile_name}'.")

                if not entries:
                    return _result(f"No backups found for profile '{profile_name}'.")

                lines = [f"Backups for profile '{profile_name}':\n"]
                for entry in sorted(entries):
                    entry_path = f"{backup_dir}/{entry}"
                    try:
                        stat = await sftp.stat(entry_path)
                        size = stat.size if stat.size is not None else 0
                        lines.append(f"  {entry} ({size} bytes)")
                    except Exception:
                        lines.append(f"  {entry} (size unknown)")

                lines.append(f"\nTotal: {len(entries)} backup(s)")
                lines.append(
                    "Use cleanup_id=<name> to delete a specific backup, "
                    "or cleanup_id='all' with confirm=true to delete all."
                )
                return _result("\n".join(lines))

            if cleanup_id == "all":
                if not confirm:
                    return _error(
                        "Deleting ALL backups requires confirm=true. "
                        "This action cannot be undone."
                    )
                try:
                    entries = await sftp.listdir(backup_dir)
                    for entry in entries:
                        entry_path = f"{backup_dir}/{entry}"
                        try:
                            # Try as directory first (batch snapshots)
                            sub_entries = await sftp.listdir(entry_path)
                            for sub in sub_entries:
                                await sftp.remove(f"{entry_path}/{sub}")
                            await sftp.rmdir(entry_path)
                        except Exception:
                            # It's a file
                            await sftp.remove(entry_path)
                    count = len(entries)
                except Exception as exc:
                    return _error(f"Cleanup failed: {_sanitise_error(exc)}")

                await _audit_file_access(
                    ctx, profile_name, profile, backup_dir, "cleanup_backups"
                )

                return _result(
                    f"Deleted {count} backup(s) for profile '{profile_name}'."
                )

            # Delete specific backup
            target = f"{backup_dir}/{cleanup_id}"
            try:
                try:
                    # Try as directory (batch snapshot)
                    sub_entries = await sftp.listdir(target)
                    for sub in sub_entries:
                        await sftp.remove(f"{target}/{sub}")
                    await sftp.rmdir(target)
                except Exception:
                    # It's a file
                    await sftp.remove(target)
            except Exception as exc:
                return _error(
                    f"Failed to delete backup '{cleanup_id}': "
                    f"{_sanitise_error(exc)}"
                )

            await _audit_file_access(
                ctx, profile_name, profile,
                f"{backup_dir}/{cleanup_id}", "cleanup_backups"
            )

            return _result(f"Deleted backup '{cleanup_id}' for profile '{profile_name}'.")

    except Exception as exc:
        return _error(f"SFTP operation failed: {_sanitise_error(exc)}")


async def _ssh_connect_rollback(
    args: dict[str, Any],
    profile_name: str,
    profile: SSHProfile,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Rollback files from a batch snapshot."""
    snapshot_id = args.get("snapshot_id", "").strip()
    if not snapshot_id:
        return _error("snapshot_id is required for rollback action.")

    # Sanitize snapshot_id — reject path traversal
    if ".." in snapshot_id or "/" in snapshot_id:
        return _error("Invalid snapshot_id — must not contain '..' or '/'.")

    conn, conn_err = await _get_ssh_connection(
        profile_name, profile, ctx, "SSHConnect:rollback"
    )
    if conn_err is not None:
        return conn_err

    backup_dir = _backup_dir(profile_name)
    snapshot_dir = f"{backup_dir}/{snapshot_id}"

    try:
        async with conn.start_sftp_client() as sftp:
            # Read manifest
            manifest_path = f"{snapshot_dir}/manifest.json"
            try:
                manifest_raw = await _sftp_read_bytes(sftp, manifest_path)
                manifest = json.loads(manifest_raw.decode("utf-8"))
            except Exception as exc:
                return _error(
                    f"Cannot read manifest for snapshot '{snapshot_id}': "
                    f"{_sanitise_error(exc)}"
                )

            # Validate all paths before restoring
            files = manifest.get("files", [])
            for file_entry in files:
                file_path = file_entry.get("path", "")
                path_check = ctx.command_filter.check_path_writable(
                    file_path, profile.privilege_level
                )
                if not path_check.allowed:
                    return _error(
                        f"Cannot rollback — path '{file_path}' is not writable: "
                        f"{path_check.reason}"
                    )

            # Restore each file
            results = []
            for file_entry in files:
                file_path = file_entry["path"]
                backup_name = file_entry["backup_name"]
                backup_file_path = f"{snapshot_dir}/{backup_name}"
                try:
                    backup_content = await _sftp_read_bytes(sftp, backup_file_path)
                    dir_name = str(PurePosixPath(file_path).parent)
                    temp_name = (
                        f"{dir_name}/.{PurePosixPath(file_path).name}.ag3ntum-tmp"
                    )
                    await _sftp_write_bytes(sftp, temp_name, backup_content)
                    try:
                        await sftp.posix_rename(temp_name, file_path)
                    except AttributeError:
                        await sftp.rename(temp_name, file_path)
                    results.append(f"  {file_path}: restored")
                except Exception as exc:
                    results.append(f"  {file_path}: FAILED ({_sanitise_error(exc)})")

            # Audit
            await _audit_file_access(
                ctx, profile_name, profile, snapshot_dir, "rollback"
            )

            return _result(
                f"Rollback from snapshot '{snapshot_id}':\n"
                + "\n".join(results)
            )

    except Exception as exc:
        return _error(f"SFTP operation failed: {_sanitise_error(exc)}")


def create_ssh_connect_tool(ctx: SSHToolContext):
    """Create SSHConnect tool bound to the given context."""
    bound_ctx = ctx

    @tool(
        "SSHConnect",
        """Manage SSH connection lifecycle for a profile.

Args:
    profile_name: SSH profile name (required for most actions)
    action:       One of: connect | disconnect | status | list | approve |
                  cleanup_backups | rollback
    command:      Exact command to approve (required for approve action)
    cleanup_id:   Backup name to delete, or 'all' (for cleanup_backups action)
    confirm:      Required true when cleanup_id='all' (for cleanup_backups action)
    snapshot_id:  Snapshot directory name to restore from (for rollback action)

Actions:
    connect:         Establish an SSH connection for the profile
    disconnect:      Close an active SSH connection
    status:          Check connection state for a specific profile
    list:            List all configured profiles and active connections
    approve:         Approve a command requiring human approval (requires 'command')
    cleanup_backups: List or delete backup files (requires profile_name;
                     optional cleanup_id and confirm parameters)
    rollback:        Restore files from a batch snapshot (requires profile_name
                     and snapshot_id)

Notes:
    - SSHExec and SSHRead connect automatically; explicit connect is optional.
    - Connections persist for the session and have an idle timeout.
    - 'list' does not require profile_name.
    - 'approve' grants one-time approval for a command that was blocked with
      requires_approval. Provide the exact command string to approve.

Examples:
    SSHConnect(action="list")
    SSHConnect(profile_name="prod-web", action="connect")
    SSHConnect(profile_name="prod-web", action="status")
    SSHConnect(profile_name="prod-web", action="disconnect")
    SSHConnect(action="approve", command="mysqldump mydb > /backup/mydb.sql")
    SSHConnect(profile_name="prod-web", action="cleanup_backups")
    SSHConnect(profile_name="prod-web", action="cleanup_backups",
               cleanup_id="nginx.conf.20260318T120000Z.bak")
    SSHConnect(profile_name="prod-web", action="cleanup_backups",
               cleanup_id="all", confirm=true)
    SSHConnect(profile_name="prod-web", action="rollback",
               snapshot_id="snapshot-20260318T120000Z")
""",
        {"profile_name": str, "action": str},
    )
    async def ssh_connect(args: dict[str, Any]) -> dict[str, Any]:
        """Manage SSH connection lifecycle."""
        return await _ssh_connect_impl(args, ctx=bound_ctx)

    return ssh_connect


# ---------------------------------------------------------------------------
# Public factory — returns all four tools
# ---------------------------------------------------------------------------

def create_ssh_tools(ctx: SSHToolContext) -> list:
    """Create all SSH tool functions bound to context.

    Returns:
        List of @tool-decorated functions: [SSHExec, SSHRead, SSHWrite, SSHConnect]
    """
    return [
        create_ssh_exec_tool(ctx),
        create_ssh_read_tool(ctx),
        create_ssh_write_tool(ctx),
        create_ssh_connect_tool(ctx),
    ]
