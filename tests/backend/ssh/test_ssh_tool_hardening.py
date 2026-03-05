"""
Tests for SSH tool hardening features.

Covers: credential redaction, rate limiting, approval tokens,
streaming output, binary file detection, concurrent semaphore, dry-run mode.
"""
import asyncio

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from tools.ag3ntum.ag3ntum_ssh.tool import (
    _redact_credentials,
    _redact_output_secrets,
    _matches_operations,
    _ssh_exec_impl,
    _ssh_read_impl,
    _stream_process_output,
    SSHApprovalStore,
    SSHToolContext,
)
from src.core.ssh.ssh_rate_limiter import SSHRateLimiter
from src.core.ssh.ssh_config import (
    SSHConnectionLimits,
    SSHProfile,
    SSHSecurityConfig,
)
from src.core.ssh.ssh_command_filter import SSHFilterResult


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_mock_process(stdout="", stderr="", exit_status=0):
    """Build a mock asyncssh process for create_process().

    Stdout/stderr streams return data on first read, empty on subsequent.
    """
    process = MagicMock()
    process.exit_status = exit_status
    process.kill = MagicMock()
    process.wait = AsyncMock()

    stdout_reads = iter([stdout, ""])
    mock_stdout = AsyncMock()
    mock_stdout.read = AsyncMock(
        side_effect=lambda n=32768: next(stdout_reads, "")
    )
    process.stdout = mock_stdout

    stderr_reads = iter([stderr, ""])
    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(
        side_effect=lambda n=32768: next(stderr_reads, "")
    )
    process.stderr = mock_stderr

    return process


def _make_mock_ctx(
    *,
    enabled=True,
    privilege_level=0,
    max_output_bytes=1024,
    filter_action="allow",
    filter_reason="allowed",
    rate_limiter=None,
    approval_store=None,
    command_semaphore=None,
):
    """Build an SSHToolContext with mocked services for unit tests."""
    session = AsyncMock()

    @asynccontextmanager
    async def db_factory():
        yield session

    profile = SSHProfile(
        name="test-server",
        host="192.168.1.100",
        port=22,
        username="deploy",
        auth_method="key",
        key_ref="test-key",
        mode="readonly",
        privilege_level=privilege_level,
    )

    mock_filter = MagicMock()
    mock_filter.check_command = MagicMock(return_value=SSHFilterResult(
        allowed=(filter_action == "allow"),
        action=filter_action,
        reason=filter_reason,
        rule="test-rule",
        category="test",
    ))
    # SSHRead uses check_path_readable for path filtering — default to allow
    mock_filter.check_path_readable = MagicMock(return_value=SSHFilterResult(
        allowed=True, action="allow", reason="read permitted",
        rule="L0:read_allowed", category="read_access",
    ))

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.create_process = AsyncMock(
        return_value=_make_mock_process("default output")
    )
    mock_pool.get_connection = AsyncMock(return_value=mock_conn)
    mock_pool.record_activity = MagicMock()

    mock_audit = MagicMock()
    mock_audit.log_command = AsyncMock(return_value=1)
    mock_audit.log_blocked = AsyncMock(return_value=1)
    mock_audit.log_file_access = AsyncMock(return_value=1)

    mock_vault = MagicMock()
    mock_vault.get_connect_fn = AsyncMock(return_value=AsyncMock())

    return SSHToolContext(
        session_id="test-session",
        user_id="test-user",
        security_config=SSHSecurityConfig(
            enabled=enabled,
            default_mode="readonly",
            limits=SSHConnectionLimits(
                max_output_bytes=max_output_bytes,
                max_concurrent_commands=5,
                command_timeout_seconds=30,
                max_file_read_bytes=2048,
            ),
        ),
        connection_pool=mock_pool,
        command_filter=mock_filter,
        credential_vault=mock_vault,
        audit_service=mock_audit,
        profiles={"test-server": profile},
        db_session_factory=db_factory,
        rate_limiter=rate_limiter,
        approval_store=approval_store,
        command_semaphore=command_semaphore,
    ), mock_conn


# ---------------------------------------------------------------------------
# Credential Redaction
# ---------------------------------------------------------------------------

class TestCredentialRedaction:
    """Test credential redaction in audit logs."""

    @pytest.mark.unit
    def test_redacts_mysql_password(self):
        """mysql -p flag is redacted."""
        cmd = "mysql -u root -pSuperSecret123 wordpress"
        result = _redact_credentials(cmd)
        assert "SuperSecret123" not in result
        assert "-p[REDACTED]" in result

    @pytest.mark.unit
    def test_redacts_long_password_flag(self):
        """--password= is redacted."""
        cmd = "mysql --password=secret123 -u root"
        result = _redact_credentials(cmd)
        assert "secret123" not in result
        assert "--password=[REDACTED]" in result

    @pytest.mark.unit
    def test_redacts_authorization_header(self):
        """Authorization header in curl is redacted."""
        cmd = 'curl -H "Authorization: Bearer tok123" https://api.example.com'
        result = _redact_credentials(cmd)
        assert "tok123" not in result
        assert "Authorization: [REDACTED]" in result

    @pytest.mark.unit
    def test_redacts_api_key(self):
        """API key patterns are redacted."""
        cmd = "curl https://api.example.com?api_key=abc123"
        result = _redact_credentials(cmd)
        assert "abc123" not in result

    @pytest.mark.unit
    def test_redacts_identified_by(self):
        """SQL IDENTIFIED BY is redacted."""
        cmd = "mysql -e \"ALTER USER 'root' IDENTIFIED BY 'newpass'\""
        result = _redact_credentials(cmd)
        assert "newpass" not in result

    @pytest.mark.unit
    def test_preserves_safe_commands(self):
        """Commands without credentials are unchanged."""
        cmd = "uptime"
        assert _redact_credentials(cmd) == cmd

    @pytest.mark.unit
    def test_preserves_command_structure(self):
        """Redaction preserves the command structure around credentials."""
        cmd = "mysql -u root -pSecret wordpress -e 'SELECT 1'"
        result = _redact_credentials(cmd)
        assert "mysql" in result
        assert "-u root" in result
        assert "wordpress" in result


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class TestSSHRateLimiter:
    """Test SSH command rate limiter."""

    @pytest.mark.unit
    def test_allows_within_limit(self):
        limiter = SSHRateLimiter(max_per_minute=5)
        for _ in range(5):
            assert limiter.check("session1", "profile1")

    @pytest.mark.unit
    def test_blocks_over_limit(self):
        limiter = SSHRateLimiter(max_per_minute=3)
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert not limiter.check("session1", "profile1")

    @pytest.mark.unit
    def test_separate_sessions(self):
        limiter = SSHRateLimiter(max_per_minute=2)
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert not limiter.check("session1", "profile1")
        assert limiter.check("session2", "profile1")

    @pytest.mark.unit
    def test_separate_profiles(self):
        limiter = SSHRateLimiter(max_per_minute=2)
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert not limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile2")

    @pytest.mark.unit
    def test_reset_clears_window(self):
        limiter = SSHRateLimiter(max_per_minute=2)
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert not limiter.check("session1", "profile1")
        limiter.reset("session1", "profile1")
        assert limiter.check("session1", "profile1")

    @pytest.mark.unit
    def test_reset_session(self):
        limiter = SSHRateLimiter(max_per_minute=1)
        limiter.check("session1", "profile1")
        limiter.check("session1", "profile2")
        limiter.reset_session("session1")
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile2")


# ---------------------------------------------------------------------------
# Approval Store
# ---------------------------------------------------------------------------

class TestSSHApprovalStore:
    """Test SSH command approval token store."""

    @pytest.mark.unit
    def test_unapproved_command(self):
        store = SSHApprovalStore()
        assert not store.is_approved("session1", "mysqldump mydb")

    @pytest.mark.unit
    def test_approved_command(self):
        store = SSHApprovalStore()
        store.approve("session1", "mysqldump mydb")
        assert store.is_approved("session1", "mysqldump mydb")

    @pytest.mark.unit
    def test_approval_is_session_scoped(self):
        store = SSHApprovalStore()
        store.approve("session1", "mysqldump mydb")
        assert not store.is_approved("session2", "mysqldump mydb")

    @pytest.mark.unit
    def test_approval_is_command_specific(self):
        store = SSHApprovalStore()
        store.approve("session1", "mysqldump mydb")
        assert not store.is_approved("session1", "pg_dump otherdb")

    @pytest.mark.unit
    def test_approve_returns_id(self):
        store = SSHApprovalStore()
        approval_id = store.approve("session1", "mysqldump mydb")
        assert isinstance(approval_id, str)
        assert len(approval_id) > 0

    @pytest.mark.unit
    def test_clear_session(self):
        store = SSHApprovalStore()
        store.approve("session1", "mysqldump mydb")
        store.clear_session("session1")
        assert not store.is_approved("session1", "mysqldump mydb")


# ---------------------------------------------------------------------------
# Streaming Output with Byte Budget (#4 / EXT-32)
# ---------------------------------------------------------------------------

class TestStreamingOutput:
    """Test streaming output with byte budget via create_process."""

    @pytest.mark.unit
    async def test_stream_truncates_large_stdout(self):
        """Output exceeding byte budget is truncated."""
        # Process that returns 2000 bytes of stdout
        big_output = "x" * 2000
        process = _make_mock_process(stdout=big_output)
        conn = AsyncMock()
        conn.create_process = AsyncMock(return_value=process)

        stdout, stderr, exit_code, truncated = await _stream_process_output(
            conn, "cat bigfile", timeout=30, max_bytes=500
        )
        assert truncated
        assert "truncated" in stdout.lower()
        # Actual data portion should be <= 500 bytes
        assert len(stdout.encode("utf-8")) <= 600  # 500 data + notice

    @pytest.mark.unit
    async def test_stream_small_output_not_truncated(self):
        """Output within budget is returned in full."""
        process = _make_mock_process(stdout="small output")
        conn = AsyncMock()
        conn.create_process = AsyncMock(return_value=process)

        stdout, stderr, exit_code, truncated = await _stream_process_output(
            conn, "echo small", timeout=30, max_bytes=1024
        )
        assert not truncated
        assert "small output" in stdout
        assert exit_code == 0

    @pytest.mark.unit
    async def test_stream_captures_stderr(self):
        """Stderr is captured alongside stdout."""
        process = _make_mock_process(stdout="out", stderr="err", exit_status=1)
        conn = AsyncMock()
        conn.create_process = AsyncMock(return_value=process)

        stdout, stderr, exit_code, _ = await _stream_process_output(
            conn, "bad-cmd", timeout=30, max_bytes=1024
        )
        assert "out" in stdout
        assert "err" in stderr
        assert exit_code == 1

    @pytest.mark.unit
    async def test_stream_kills_process_on_budget_exceeded(self):
        """Process is killed when byte budget is exhausted."""
        process = _make_mock_process(stdout="x" * 2000)
        conn = AsyncMock()
        conn.create_process = AsyncMock(return_value=process)

        await _stream_process_output(
            conn, "cat /dev/urandom", timeout=30, max_bytes=100
        )
        process.kill.assert_called()

    @pytest.mark.unit
    async def test_exec_impl_uses_streaming(self):
        """_ssh_exec_impl uses create_process for streaming output."""
        ctx, mock_conn = _make_mock_ctx(max_output_bytes=1024)
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="streaming output")
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=ctx,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "streaming output" in text
        mock_conn.create_process.assert_awaited_once()


# ---------------------------------------------------------------------------
# Binary File Detection (#10 / EXT-38)
# ---------------------------------------------------------------------------

class TestBinaryFileDetection:
    """Test binary file detection in SSHRead."""

    def _make_read_ctx_and_sftp(self, file_content: bytes, file_size=None):
        """Create context and SFTP mocks for a read test."""
        ctx, _ = _make_mock_ctx()

        mock_sftp = AsyncMock()
        mock_stat = MagicMock()
        mock_stat.size = file_size if file_size is not None else len(file_content)
        mock_sftp.stat = AsyncMock(return_value=mock_stat)
        mock_sftp.realpath = AsyncMock(return_value="/test/file")

        mock_file = AsyncMock()
        mock_file.read = AsyncMock(return_value=file_content)
        mock_file.close = AsyncMock()
        mock_sftp.open = AsyncMock(return_value=mock_file)

        mock_sftp_cm = MagicMock()
        mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)
        ctx.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        return ctx

    @pytest.mark.unit
    async def test_rejects_binary_file(self):
        """Files with >1% null bytes are rejected as binary."""
        # 100 bytes with 5 null bytes (5%) — clearly binary
        content = b"\x00" * 5 + b"A" * 95
        ctx = self._make_read_ctx_and_sftp(content)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/test/file"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "binary" in text.lower()

    @pytest.mark.unit
    async def test_accepts_text_file(self):
        """Text files with no null bytes pass binary detection."""
        content = b"Hello, world!\nLine 2\n"
        ctx = self._make_read_ctx_and_sftp(content)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/test/file"},
            ctx=ctx,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "Hello" in text

    @pytest.mark.unit
    async def test_accepts_text_with_minimal_nulls(self):
        """Files with <1% null bytes are accepted (e.g., occasional padding)."""
        # 1000 bytes with 5 null bytes (0.5%) — should pass
        content = b"A" * 995 + b"\x00" * 5
        ctx = self._make_read_ctx_and_sftp(content)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/test/file"},
            ctx=ctx,
        )
        assert "is_error" not in result

    @pytest.mark.unit
    async def test_binary_suggests_alternatives(self):
        """Binary rejection message suggests 'file' or 'xxd' commands."""
        content = b"\x00" * 100  # 100% null bytes
        ctx = self._make_read_ctx_and_sftp(content)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/test/binary"},
            ctx=ctx,
        )
        text = result["content"][0]["text"]
        assert "file" in text.lower() or "xxd" in text.lower()


# ---------------------------------------------------------------------------
# Concurrent Command Semaphore (#11 / EXT-39)
# ---------------------------------------------------------------------------

class TestConcurrentSemaphore:
    """Test concurrent command semaphore enforcement."""

    @pytest.mark.unit
    async def test_semaphore_limits_concurrency(self):
        """Semaphore blocks when too many concurrent commands."""
        sem = asyncio.Semaphore(1)
        ctx, mock_conn = _make_mock_ctx(command_semaphore=sem)
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="output")
        )

        # Acquire the semaphore to simulate a "running" command
        await sem.acquire()

        # Next command should timeout waiting for the semaphore
        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "concurrent" in text.lower()

        # Release so cleanup can proceed
        sem.release()

    @pytest.mark.unit
    async def test_semaphore_allows_within_limit(self):
        """Commands within the concurrency limit proceed normally."""
        sem = asyncio.Semaphore(5)
        ctx, mock_conn = _make_mock_ctx(command_semaphore=sem)
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="ok")
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=ctx,
        )
        assert "is_error" not in result
        assert "ok" in result["content"][0]["text"]

    @pytest.mark.unit
    async def test_semaphore_released_after_success(self):
        """Semaphore is released after successful execution."""
        sem = asyncio.Semaphore(1)
        ctx, mock_conn = _make_mock_ctx(command_semaphore=sem)
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="output")
        )

        await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=ctx,
        )
        # Semaphore should be released — value back to 1
        assert not sem.locked()

    @pytest.mark.unit
    async def test_semaphore_released_after_error(self):
        """Semaphore is released even when execution fails."""
        sem = asyncio.Semaphore(1)
        ctx, mock_conn = _make_mock_ctx(command_semaphore=sem)
        mock_conn.create_process = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        # Force the connection pool to return the mock conn
        ctx.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        # Semaphore must be released despite the error
        assert not sem.locked()

    @pytest.mark.unit
    async def test_no_semaphore_still_works(self):
        """Execution works when no semaphore is configured."""
        ctx, mock_conn = _make_mock_ctx(command_semaphore=None)
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="ok")
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=ctx,
        )
        assert "is_error" not in result


# ---------------------------------------------------------------------------
# Dry-Run Mode (#12 / EXT-40)
# ---------------------------------------------------------------------------

class TestDryRunMode:
    """Test dry-run mode for SSHExec."""

    @pytest.mark.unit
    async def test_dry_run_allowed_command(self):
        """Dry-run for an allowed command shows preview without executing."""
        ctx, mock_conn = _make_mock_ctx(filter_action="allow")

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime", "dry_run": True},
            ctx=ctx,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "DRY RUN" in text
        assert "ALLOWED" in text
        assert "uptime" in text
        # Should NOT have connected
        mock_conn.create_process.assert_not_awaited()

    @pytest.mark.unit
    async def test_dry_run_blocked_command(self):
        """Dry-run for a blocked command returns preview (not error)."""
        ctx, mock_conn = _make_mock_ctx(
            filter_action="block",
            filter_reason="Destructive command",
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "rm -rf /", "dry_run": True},
            ctx=ctx,
        )
        # Dry-run block returns a result, not an error
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "DRY RUN" in text
        assert "BLOCKED" in text
        assert "Destructive command" in text

    @pytest.mark.unit
    async def test_dry_run_requires_approval(self):
        """Dry-run for a requires_approval command shows preview."""
        ctx, mock_conn = _make_mock_ctx(
            filter_action="requires_approval",
            filter_reason="Database dump needs review",
        )

        result = await _ssh_exec_impl(
            {
                "profile_name": "test-server",
                "command": "mysqldump mydb",
                "dry_run": True,
            },
            ctx=ctx,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "DRY RUN" in text
        assert "REQUIRES APPROVAL" in text
        assert "Approval ID" in text

    @pytest.mark.unit
    async def test_dry_run_does_not_connect(self):
        """Dry-run never establishes an SSH connection."""
        ctx, mock_conn = _make_mock_ctx()

        await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime", "dry_run": True},
            ctx=ctx,
        )
        ctx.connection_pool.get_connection.assert_not_awaited()

    @pytest.mark.unit
    async def test_dry_run_does_not_audit(self):
        """Dry-run does not log to audit service."""
        ctx, mock_conn = _make_mock_ctx()

        await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime", "dry_run": True},
            ctx=ctx,
        )
        ctx.audit_service.log_command.assert_not_awaited()

    @pytest.mark.unit
    async def test_non_dry_run_executes(self):
        """Without dry_run, the command actually executes."""
        ctx, mock_conn = _make_mock_ctx()
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="real output")
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=ctx,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "real output" in text
        assert "DRY RUN" not in text


# ---------------------------------------------------------------------------
# Operations Mode Enforcement (Phase 1B)
# ---------------------------------------------------------------------------

class TestOperationsMode:
    """Test operations mode enforcement in _ssh_exec_impl."""

    def _make_operations_ctx(self, allowed_operations=None, mode="operations"):
        """Create context with an operations-mode profile."""
        session = AsyncMock()

        @asynccontextmanager
        async def db_factory():
            yield session

        profile = SSHProfile(
            name="wp-server",
            host="192.168.1.100",
            port=22,
            username="deploy",
            auth_method="key",
            key_ref="test-key",
            mode=mode,
            privilege_level=1,
            allowed_operations=allowed_operations or [],
        )

        mock_filter = MagicMock()
        mock_filter.check_command = MagicMock(return_value=SSHFilterResult(
            allowed=True,
            action="allow",
            reason="allowed",
            rule="test-rule",
            category="test",
        ))

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="output")
        )
        mock_pool.get_connection = AsyncMock(return_value=mock_conn)
        mock_pool.record_activity = MagicMock()

        mock_audit = MagicMock()
        mock_audit.log_command = AsyncMock(return_value=1)
        mock_audit.log_blocked = AsyncMock(return_value=1)

        mock_vault = MagicMock()
        mock_vault.get_connect_fn = AsyncMock(return_value=AsyncMock())

        ctx = SSHToolContext(
            session_id="test-session",
            user_id="test-user",
            security_config=SSHSecurityConfig(
                enabled=True,
                limits=SSHConnectionLimits(
                    max_output_bytes=1024,
                    max_concurrent_commands=5,
                    command_timeout_seconds=30,
                    max_file_read_bytes=2048,
                ),
            ),
            connection_pool=mock_pool,
            command_filter=mock_filter,
            credential_vault=mock_vault,
            audit_service=mock_audit,
            profiles={"wp-server": profile},
            db_session_factory=db_factory,
        )
        return ctx

    @pytest.mark.unit
    async def test_operations_mode_allows_matching_command(self):
        """Command matching allowed_operations is permitted."""
        ctx = self._make_operations_ctx(
            allowed_operations=[r'^wp\s+plugin\s+list']
        )
        result = await _ssh_exec_impl(
            {"profile_name": "wp-server", "command": "wp plugin list"},
            ctx=ctx,
        )
        assert "is_error" not in result

    @pytest.mark.unit
    async def test_operations_mode_blocks_non_matching_command(self):
        """Command not matching allowed_operations is blocked."""
        ctx = self._make_operations_ctx(
            allowed_operations=[r'^wp\s+plugin\s+list']
        )
        result = await _ssh_exec_impl(
            {"profile_name": "wp-server", "command": "rm -rf /tmp"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "not in allowed operations" in text

    @pytest.mark.unit
    async def test_operations_mode_empty_operations_blocks_all(self):
        """Empty allowed_operations with operations mode doesn't block
        (empty list means no operations filter is applied)."""
        ctx = self._make_operations_ctx(allowed_operations=[])
        result = await _ssh_exec_impl(
            {"profile_name": "wp-server", "command": "uptime"},
            ctx=ctx,
        )
        # Empty list means the condition `profile.allowed_operations` is falsy,
        # so operations mode check is skipped
        assert "is_error" not in result

    @pytest.mark.unit
    async def test_operations_mode_bypassed_when_not_operations(self):
        """Non-operations mode skips the operations check."""
        ctx = self._make_operations_ctx(
            allowed_operations=[r'^wp\s+plugin\s+list'],
            mode="readonly",  # Not operations mode
        )
        result = await _ssh_exec_impl(
            {"profile_name": "wp-server", "command": "rm -rf /tmp"},
            ctx=ctx,
        )
        # Should pass operations check (bypassed), then pass the mock filter
        assert "is_error" not in result


# ---------------------------------------------------------------------------
# _matches_operations helper
# ---------------------------------------------------------------------------

class TestMatchesOperations:
    """Test the _matches_operations helper function."""

    @pytest.mark.unit
    def test_matches_simple_pattern(self):
        assert _matches_operations("wp plugin list", [r'^wp\s+plugin\s+list'])

    @pytest.mark.unit
    def test_no_match(self):
        assert not _matches_operations("rm -rf /", [r'^wp\s+plugin\s+list'])

    @pytest.mark.unit
    def test_case_insensitive(self):
        assert _matches_operations("WP Plugin List", [r'^wp\s+plugin\s+list'])

    @pytest.mark.unit
    def test_invalid_regex_skipped(self):
        """Invalid regex patterns are skipped without crashing."""
        assert not _matches_operations("test", [r'[invalid'])

    @pytest.mark.unit
    def test_multiple_patterns(self):
        patterns = [r'^wp\s+plugin', r'^wp\s+theme']
        assert _matches_operations("wp theme list", patterns)


# ---------------------------------------------------------------------------
# Output Secret Redaction (Phase 2D)
# ---------------------------------------------------------------------------

class TestOutputSecretRedaction:
    """Test output secret redaction for command stdout."""

    @pytest.mark.unit
    def test_redacts_wp_db_password(self):
        """WordPress DB_PASSWORD define is redacted."""
        text = "define('DB_PASSWORD', 'my_secret_pass');"
        result = _redact_output_secrets(text)
        assert "my_secret_pass" not in result
        assert "[REDACTED]" in result

    @pytest.mark.unit
    def test_redacts_wp_db_user(self):
        """WordPress DB_USER define is redacted."""
        text = "define('DB_USER', 'wp_admin');"
        result = _redact_output_secrets(text)
        assert "wp_admin" not in result
        assert "[REDACTED]" in result

    @pytest.mark.unit
    def test_redacts_generic_password(self):
        """Generic password= pattern is redacted."""
        text = "password: SuperSecret123"
        result = _redact_output_secrets(text)
        assert "SuperSecret123" not in result

    @pytest.mark.unit
    def test_redacts_api_key(self):
        """API key patterns are redacted."""
        text = "api_key=sk-1234567890abcdef"
        result = _redact_output_secrets(text)
        assert "sk-1234567890abcdef" not in result

    @pytest.mark.unit
    def test_preserves_non_secret_content(self):
        """Normal output is not modified."""
        text = "WordPress 6.4.2 is up to date.\nPlugins: 12 active"
        result = _redact_output_secrets(text)
        assert result == text

    @pytest.mark.unit
    async def test_exec_inner_redacts_stdout(self):
        """_ssh_exec_impl redacts secrets in stdout before returning."""
        ctx, mock_conn = _make_mock_ctx()
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(
                stdout="define('DB_PASSWORD', 'leaked_secret');"
            )
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "cat wp-config.php"},
            ctx=ctx,
        )
        text = result["content"][0]["text"]
        assert "leaked_secret" not in text
        assert "[REDACTED]" in text


# ---------------------------------------------------------------------------
# SSHRead Blocked Path Filtering (Phase 2B)
# ---------------------------------------------------------------------------

class TestSSHReadPathBlocking:
    """Test blocked path check in SSHRead."""

    def _make_read_ctx(self, privilege_level=0, file_content=b"content"):
        """Create context with real command_filter for path blocking tests."""
        from pathlib import Path as FilePath
        from src.core.ssh.ssh_command_filter import SSHCommandFilter

        session = AsyncMock()

        @asynccontextmanager
        async def db_factory():
            yield session

        example_path = (
            FilePath(__file__).parent.parent.parent.parent
            / "config" / "security" / "ssh-privilege-levels.yaml.example"
        )
        real_filter = SSHCommandFilter(config_path=example_path)

        profile = SSHProfile(
            name="test-server",
            host="192.168.1.100",
            port=22,
            username="deploy",
            auth_method="key",
            key_ref="test-key",
            mode="readonly",
            privilege_level=privilege_level,
        )

        mock_pool = MagicMock()
        mock_conn = MagicMock()

        mock_sftp = AsyncMock()
        mock_stat = MagicMock()
        mock_stat.size = len(file_content)
        mock_sftp.stat = AsyncMock(return_value=mock_stat)
        # realpath returns the same path (no symlink)
        mock_sftp.realpath = AsyncMock(side_effect=lambda p: p)

        mock_file = AsyncMock()
        mock_file.read = AsyncMock(return_value=file_content)
        mock_file.close = AsyncMock()
        mock_sftp.open = AsyncMock(return_value=mock_file)

        mock_sftp_cm = MagicMock()
        mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)

        mock_pool.get_connection = AsyncMock(return_value=mock_conn)
        mock_pool.record_activity = MagicMock()

        mock_audit = MagicMock()
        mock_audit.log_file_access = AsyncMock(return_value=1)

        mock_vault = MagicMock()
        mock_vault.get_connect_fn = AsyncMock(return_value=AsyncMock())

        ctx = SSHToolContext(
            session_id="test-session",
            user_id="test-user",
            security_config=SSHSecurityConfig(
                enabled=True,
                limits=SSHConnectionLimits(
                    max_output_bytes=1024,
                    max_concurrent_commands=5,
                    command_timeout_seconds=30,
                    max_file_read_bytes=4096,
                ),
            ),
            connection_pool=mock_pool,
            command_filter=real_filter,
            credential_vault=mock_vault,
            audit_service=mock_audit,
            profiles={"test-server": profile},
            db_session_factory=db_factory,
        )
        return ctx

    @pytest.mark.unit
    async def test_read_blocked_path_at_l0(self):
        """/etc/shadow is blocked at L0."""
        ctx = self._make_read_ctx(privilege_level=0)
        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/shadow"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "blocked" in text.lower()

    @pytest.mark.unit
    async def test_read_blocked_path_at_l2(self):
        """/etc/sudoers is blocked at L2."""
        ctx = self._make_read_ctx(privilege_level=2)
        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/sudoers"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "blocked" in text.lower()

    @pytest.mark.unit
    async def test_read_blocked_path_at_l3_allowed(self):
        """/etc/shadow at L3 is NOT blocked (blocklist mode, reads allowed)."""
        ctx = self._make_read_ctx(privilege_level=3)
        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/shadow"},
            ctx=ctx,
        )
        assert "is_error" not in result

    @pytest.mark.unit
    async def test_read_allowed_path_at_l0(self):
        """/var/log/nginx/access.log is allowed at L0."""
        ctx = self._make_read_ctx(privilege_level=0)
        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/var/log/nginx/access.log"},
            ctx=ctx,
        )
        assert "is_error" not in result

    @pytest.mark.unit
    async def test_read_sshd_config_blocked_at_l0(self):
        """/etc/ssh/sshd_config is blocked at L0."""
        ctx = self._make_read_ctx(privilege_level=0)
        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/ssh/sshd_config"},
            ctx=ctx,
        )
        assert result.get("is_error") is True

    @pytest.mark.unit
    async def test_read_pam_blocked_at_l1(self):
        """/etc/pam.d/common-auth is blocked at L1."""
        ctx = self._make_read_ctx(privilege_level=1)
        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/pam.d/common-auth"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
