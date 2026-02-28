"""
SSH MCP tool implementations.

Three tools following the 3-layer pattern:
1. _impl() functions — testable core logic
2. create_*_tool() factories — MCP wrappers binding SSHToolContext
3. create_ssh_tools() — returns all three as a list

Tool names:
- mcp__ag3ntum__SSHConnect
- mcp__ag3ntum__SSHExec
- mcp__ag3ntum__SSHRead

Security:
- All operations check security_config.enabled first (fail-closed).
- All operations are logged to the audit service.
- Command output is truncated at max_output_bytes and sanitised.
- Raw exception tracebacks are never returned to the agent.
- asyncssh is TYPE_CHECKING-gated; imported lazily inside closures.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import tool

if TYPE_CHECKING:
    import asyncssh  # noqa: F401 — type annotation only
    from src.services.ssh_audit_service import SSHAuditService

from src.core.ssh.ssh_config import SSHProfile, SSHSecurityConfig
from src.core.ssh.ssh_connection_pool import SSHConnectionPool, SSHConnectionLimitError
from src.core.ssh.ssh_command_filter import SSHCommandFilter
from src.core.ssh.ssh_credential_vault import SSHCredentialVault

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool name constants
# ---------------------------------------------------------------------------

AG3NTUM_SSH_EXEC_TOOL: str = "mcp__ag3ntum__SSHExec"
AG3NTUM_SSH_READ_TOOL: str = "mcp__ag3ntum__SSHRead"
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


def _redact_credentials(command: str) -> str:
    """Redact known credential patterns from command for audit logging."""
    result = command
    for pattern, replacement in _CREDENTIAL_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


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

async def _ssh_exec_impl(
    args: dict[str, Any],
    *,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Core SSHExec logic — testable without MCP wrapper."""
    if not ctx.security_config.enabled:
        return _error("SSH is disabled. Enable it in the security configuration.")

    profile_name: str = args.get("profile_name", "")
    command: str = args.get("command", "").strip()

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
            return _error(
                f"Command requires human approval before execution. "
                f"Reason: {filter_result.reason}. "
                f"Approval ID: {cmd_hash}. "
                "Use SSHConnect with action='approve' and the approval_id "
                "to approve this command, then retry."
            )

    # Get or establish connection
    start_ms = int(time.monotonic() * 1000)
    try:
        async with ctx.db_session_factory() as db:
            connect_fn = await ctx.credential_vault.get_connect_fn(
                db, ctx.user_id, ctx.session_id, profile
            )
        conn = await ctx.connection_pool.get_connection(
            ctx.session_id, profile_name, ctx.user_id, connect_fn
        )
    except SSHConnectionLimitError as exc:
        return _error(f"SSH connection limit reached: {exc}")
    except ValueError as exc:
        return _error(f"SSH credential error: {exc}")
    except Exception as exc:
        err_msg = _classify_host_key_error(exc, profile_name)
        if err_msg:
            await _audit_host_key_failure(ctx, profile_name, profile, exc)
            return _error(err_msg)
        logger.error("SSHExec: Connection failed for profile %s: %s", profile_name, exc)
        return _error(f"SSH connection failed: {_sanitise_error(exc)}")

    # Execute command
    try:
        result = await conn.run(
            command,
            timeout=ctx.security_config.limits.command_timeout_seconds,
        )
    except Exception as exc:
        logger.warning(
            "SSHExec: Command execution failed (profile=%s cmd=%s): %s",
            profile_name, command[:80], exc,
        )
        return _error(f"SSH command execution failed: {_sanitise_error(exc)}")

    duration_ms = int(time.monotonic() * 1000) - start_ms

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    exit_code = result.exit_status if result.exit_status is not None else -1

    max_bytes = ctx.security_config.limits.max_output_bytes
    stdout = _truncate_output(stdout, max_bytes)
    stderr = _truncate_output(stderr, max_bytes)

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

Returns:
    Exit code, stdout, stderr, and execution duration.

Notes:
    - Commands are filtered by the privilege level set in the profile.
    - Blocked or unapproved commands return an error — never execute.
    - Output is truncated at the configured max_output_bytes limit.

Examples:
    SSHExec(profile_name="prod-web", command="uptime")
    SSHExec(profile_name="db-primary", command="df -h /var/lib/postgresql")
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
    if not ctx.security_config.enabled:
        return _error("SSH is disabled. Enable it in the security configuration.")

    profile_name: str = args.get("profile_name", "")
    remote_path: str = args.get("path", "").strip()

    if not remote_path:
        return _error("path is required")

    profile, err = _resolve_profile(profile_name, ctx.profiles)
    if err is not None:
        return err
    assert profile is not None  # guaranteed by _resolve_profile when err is None

    # Get or establish connection
    try:
        async with ctx.db_session_factory() as db:
            connect_fn = await ctx.credential_vault.get_connect_fn(
                db, ctx.user_id, ctx.session_id, profile
            )
        conn = await ctx.connection_pool.get_connection(
            ctx.session_id, profile_name, ctx.user_id, connect_fn
        )
    except SSHConnectionLimitError as exc:
        return _error(f"SSH connection limit reached: {exc}")
    except ValueError as exc:
        return _error(f"SSH credential error: {exc}")
    except Exception as exc:
        err_msg = _classify_host_key_error(exc, profile_name)
        if err_msg:
            await _audit_host_key_failure(ctx, profile_name, profile, exc)
            return _error(err_msg)
        logger.error("SSHRead: Connection failed for profile %s: %s", profile_name, exc)
        return _error(f"SSH connection failed: {_sanitise_error(exc)}")

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

            # Resolve symlinks and check target path for L2
            try:
                real_path = await sftp.realpath(remote_path)
                if real_path != remote_path:
                    # Symlink detected — verify target is allowed
                    path_check = ctx.command_filter.check_path_writable(
                        real_path, profile.privilege_level
                    )
                    if not path_check.allowed and profile.privilege_level <= 2:
                        return _error(
                            f"Symlink target '{real_path}' is outside "
                            "allowed paths for this privilege level."
                        )
            except Exception:
                pass  # realpath failed — proceed with the original path

            remote_file = await sftp.open(remote_path, "r")
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

    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
    else:
        truncated = False

    content = raw.decode("utf-8", errors="replace")

    # Record activity and audit
    ctx.connection_pool.record_activity(ctx.session_id, profile_name)
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
                path=remote_path,
                operation="read_file",
                privilege_level=profile.privilege_level,
                mode=profile.mode,
            )
    except Exception as audit_exc:
        logger.error("SSHRead: Failed to log file access: %s", audit_exc)

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
# SSHConnect — connection lifecycle management
# ---------------------------------------------------------------------------

async def _ssh_connect_impl(
    args: dict[str, Any],
    *,
    ctx: SSHToolContext,
) -> dict[str, Any]:
    """Core SSHConnect logic — testable without MCP wrapper."""
    if not ctx.security_config.enabled:
        return _error("SSH is disabled. Enable it in the security configuration.")

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

    if action not in ("connect", "disconnect", "status"):
        return _error(
            f"Invalid action '{action}'. "
            "Valid actions: connect, disconnect, status, list, approve"
        )

    # All other actions need a profile
    profile, err = _resolve_profile(profile_name, ctx.profiles)
    if err is not None:
        return err
    assert profile is not None  # guaranteed by _resolve_profile when err is None

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
    try:
        async with ctx.db_session_factory() as db:
            connect_fn = await ctx.credential_vault.get_connect_fn(
                db, ctx.user_id, ctx.session_id, profile
            )
        await ctx.connection_pool.get_connection(
            ctx.session_id, profile_name, ctx.user_id, connect_fn
        )
    except SSHConnectionLimitError as exc:
        return _error(f"SSH connection limit reached: {exc}")
    except ValueError as exc:
        return _error(f"SSH credential error: {exc}")
    except Exception as exc:
        err_msg = _classify_host_key_error(exc, profile_name)
        if err_msg:
            await _audit_host_key_failure(ctx, profile_name, profile, exc)
            return _error(err_msg)
        logger.error(
            "SSHConnect: Connection failed for profile %s: %s", profile_name, exc
        )
        return _error(f"SSH connection failed: {_sanitise_error(exc)}")

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


def create_ssh_connect_tool(ctx: SSHToolContext):
    """Create SSHConnect tool bound to the given context."""
    bound_ctx = ctx

    @tool(
        "SSHConnect",
        """Manage SSH connection lifecycle for a profile.

Args:
    profile_name: SSH profile name (required for connect, disconnect, status)
    action:       One of: connect | disconnect | status | list | approve
    command:      Exact command to approve (required for approve action)

Actions:
    connect:    Establish an SSH connection for the profile
    disconnect: Close an active SSH connection
    status:     Check connection state for a specific profile
    list:       List all configured profiles and active connections
    approve:    Approve a command requiring human approval (requires 'command' parameter)

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
""",
        {"profile_name": str, "action": str},
    )
    async def ssh_connect(args: dict[str, Any]) -> dict[str, Any]:
        """Manage SSH connection lifecycle."""
        return await _ssh_connect_impl(args, ctx=bound_ctx)

    return ssh_connect


# ---------------------------------------------------------------------------
# Public factory — returns all three tools
# ---------------------------------------------------------------------------

def create_ssh_tools(ctx: SSHToolContext) -> list:
    """Create all SSH tool functions bound to context.

    Returns:
        List of @tool-decorated functions: [SSHExec, SSHRead, SSHConnect]
    """
    return [
        create_ssh_exec_tool(ctx),
        create_ssh_read_tool(ctx),
        create_ssh_connect_tool(ctx),
    ]
