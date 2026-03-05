"""
Unit tests for SSH MCP tool _impl functions.

Tests the _ssh_exec_impl, _ssh_read_impl, and _ssh_connect_impl functions
directly — bypassing the MCP wrapper per CLAUDE.md gotcha #19.

All service dependencies are mocked. No real SSH connections are made.
"""
import asyncio
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from tools.ag3ntum.ag3ntum_ssh.tool import (
    SSHToolContext,
    _ssh_exec_impl,
    _ssh_read_impl,
    _ssh_connect_impl,
)
from src.core.ssh.ssh_config import SSHSecurityConfig, SSHConnectionLimits
from src.core.ssh.ssh_command_filter import SSHCommandFilter, SSHFilterResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db_session():
    """Async context manager factory yielding a mock AsyncSession."""
    session = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield session

    return factory


@pytest.fixture
def mock_connection_pool():
    pool = MagicMock()
    pool.get_connection = AsyncMock(return_value=MagicMock())
    pool.record_activity = MagicMock()
    pool.get_connection_info = MagicMock(return_value=[])
    pool.release_connection = AsyncMock()
    return pool


@pytest.fixture
def mock_command_filter():
    f = MagicMock()
    f.check_command = MagicMock(return_value=SSHFilterResult(
        allowed=True,
        action="allow",
        reason="command is safe",
        rule="L0_monitoring:allowlist",
        category="safe_read",
    ))
    # SSHRead uses check_path_readable for path filtering — default to allow
    f.check_path_readable = MagicMock(return_value=SSHFilterResult(
        allowed=True, action="allow", reason="read permitted",
        rule="L0:read_allowed", category="read_access",
    ))
    # Output redaction patterns from example config
    from pathlib import Path as _P
    _example = _P(__file__).parent.parent.parent.parent / "config" / "security" / "ssh-privilege-levels.yaml.example"
    _filt = SSHCommandFilter(config_path=_example)
    f.output_redaction_patterns = _filt.output_redaction_patterns
    return f


@pytest.fixture
def mock_credential_vault():
    vault = MagicMock()
    vault.get_connect_fn = AsyncMock(return_value=AsyncMock())
    return vault


@pytest.fixture
def mock_audit_service():
    audit = MagicMock()
    audit.log_command = AsyncMock(return_value=1)
    audit.log_blocked = AsyncMock(return_value=1)
    audit.log_file_access = AsyncMock(return_value=1)
    audit.log_connection = AsyncMock(return_value=1)
    return audit


@pytest.fixture
def mock_services(
    ssh_security_config,
    test_ssh_profile,
    mock_db_session,
    mock_connection_pool,
    mock_command_filter,
    mock_credential_vault,
    mock_audit_service,
):
    """SSHToolContext with all services mocked, SSH enabled."""
    return SSHToolContext(
        session_id="test-session",
        user_id="test-user",
        security_config=ssh_security_config,
        connection_pool=mock_connection_pool,
        command_filter=mock_command_filter,
        credential_vault=mock_credential_vault,
        audit_service=mock_audit_service,
        profiles={"test-server": test_ssh_profile},
        db_session_factory=mock_db_session,
    )


@pytest.fixture
def ctx_disabled(
    test_ssh_profile,
    mock_db_session,
    mock_connection_pool,
    mock_command_filter,
    mock_credential_vault,
    mock_audit_service,
):
    """SSHToolContext with SSH disabled."""
    return SSHToolContext(
        session_id="test-session",
        user_id="test-user",
        security_config=SSHSecurityConfig(enabled=False),
        connection_pool=mock_connection_pool,
        command_filter=mock_command_filter,
        credential_vault=mock_credential_vault,
        audit_service=mock_audit_service,
        profiles={"test-server": test_ssh_profile},
        db_session_factory=mock_db_session,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_process(stdout="test output", stderr="", exit_status=0):
    """Build a mock asyncssh process for create_process().

    The mock process has .stdout/.stderr streams that return data
    once then empty string, .exit_status, .kill(), and .wait().
    """
    process = MagicMock()
    process.exit_status = exit_status
    process.kill = MagicMock()
    process.wait = AsyncMock()

    # Stdout stream: returns data on first read, empty on subsequent
    stdout_reads = iter([stdout, ""])
    mock_stdout = AsyncMock()
    mock_stdout.read = AsyncMock(side_effect=lambda n=32768: next(stdout_reads, ""))
    process.stdout = mock_stdout

    # Stderr stream: returns data on first read, empty on subsequent
    stderr_reads = iter([stderr, ""])
    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(side_effect=lambda n=32768: next(stderr_reads, ""))
    process.stderr = mock_stderr

    return process


# ---------------------------------------------------------------------------
# TestSSHExecImpl
# ---------------------------------------------------------------------------

class TestSSHExecImpl:

    @pytest.mark.unit
    async def test_ssh_disabled_returns_error(self, ctx_disabled):
        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=ctx_disabled,
        )
        assert result.get("is_error") is True
        assert "disabled" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_exec_success(self, mock_services):
        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(return_value=_make_mock_process("uptime output"))
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "Exit code: 0" in text
        assert "uptime output" in text

    @pytest.mark.unit
    async def test_exec_captures_stderr(self, mock_services):
        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="", stderr="warning: something", exit_status=1)
        )
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "bad-cmd"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "Exit code: 1" in text
        assert "warning: something" in text

    @pytest.mark.unit
    async def test_exec_command_blocked(self, mock_services):
        mock_services.command_filter.check_command = MagicMock(
            return_value=SSHFilterResult(
                allowed=False,
                action="block",
                reason="Destructive command blocked",
                rule="L0_monitoring:blocklist",
                category="destructive",
            )
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "rm -rf /"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "blocked" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_exec_blocked_calls_audit(self, mock_services):
        """Blocked commands are logged to the audit service."""
        mock_services.command_filter.check_command = MagicMock(
            return_value=SSHFilterResult(
                allowed=False,
                action="block",
                reason="Blocked",
                rule="test-rule",
                category="destructive",
            )
        )

        await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "rm -rf /"},
            ctx=mock_services,
        )
        mock_services.audit_service.log_blocked.assert_awaited_once()

    @pytest.mark.unit
    async def test_exec_requires_approval(self, mock_services):
        mock_services.command_filter.check_command = MagicMock(
            return_value=SSHFilterResult(
                allowed=False,
                action="requires_approval",
                reason="High-risk command needs review",
                rule="L2_approval_list",
                category="risky",
            )
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "mysqldump db"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "approval" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_exec_missing_profile(self, mock_services):
        result = await _ssh_exec_impl(
            {"profile_name": "nonexistent", "command": "uptime"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_exec_empty_command(self, mock_services):
        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": ""},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "required" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_exec_whitespace_only_command(self, mock_services):
        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "   "},
            ctx=mock_services,
        )
        assert result.get("is_error") is True

    @pytest.mark.unit
    async def test_exec_no_output(self, mock_services):
        """Commands with no stdout/stderr show (no output) placeholder."""
        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout="", stderr="", exit_status=0)
        )
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "true"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        assert "no output" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_exec_output_truncated(self, mock_services):
        """Output exceeding max_output_bytes is truncated with notice."""
        # max_output_bytes is 1024 from ssh_security_config fixture
        big_output = "x" * 2000
        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(
            return_value=_make_mock_process(stdout=big_output, stderr="", exit_status=0)
        )
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "cat bigfile"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        assert "truncated" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_exec_audit_logged_on_success(self, mock_services):
        """Successful command execution logs to audit service."""
        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(return_value=_make_mock_process("output"))
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=mock_services,
        )
        mock_services.audit_service.log_command.assert_awaited_once()

    @pytest.mark.unit
    async def test_exec_connection_error(self, mock_services):
        """Connection failure returns an error without propagating the exception."""
        mock_services.connection_pool.get_connection = AsyncMock(
            side_effect=RuntimeError("Connection timed out")
        )

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "connection failed" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_exec_profile_info_in_output(self, mock_services):
        """Result includes profile name, user, host, and port."""
        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(return_value=_make_mock_process("ok"))
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_exec_impl(
            {"profile_name": "test-server", "command": "uptime"},
            ctx=mock_services,
        )
        text = result["content"][0]["text"]
        assert "test-server" in text
        assert "deploy" in text  # from test_ssh_profile fixture
        assert "192.168.1.100" in text


# ---------------------------------------------------------------------------
# TestSSHReadImpl
# ---------------------------------------------------------------------------

class TestSSHReadImpl:

    @pytest.mark.unit
    async def test_ssh_disabled_returns_error(self, ctx_disabled):
        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/hostname"},
            ctx=ctx_disabled,
        )
        assert result.get("is_error") is True
        assert "disabled" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_read_success(self, mock_services):
        """Read a small text file and verify numbered output."""
        mock_sftp = AsyncMock()
        mock_stat = MagicMock()
        mock_stat.size = 18
        mock_sftp.stat = AsyncMock(return_value=mock_stat)
        mock_sftp.open = AsyncMock()

        mock_file = AsyncMock()
        mock_file.read = AsyncMock(return_value=b"line1\nline2\nline3\n")
        mock_file.close = AsyncMock()
        mock_sftp.open.return_value = mock_file

        # conn.start_sftp_client() is an async context manager
        mock_sftp_cm = MagicMock()
        mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/hostname"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "line1" in text
        assert "line2" in text
        assert "line3" in text

    @pytest.mark.unit
    async def test_read_line_numbers_format(self, mock_services):
        """Output uses the N|line format."""
        mock_sftp = AsyncMock()
        mock_stat = MagicMock()
        mock_stat.size = 5
        mock_sftp.stat = AsyncMock(return_value=mock_stat)

        mock_file = AsyncMock()
        mock_file.read = AsyncMock(return_value=b"hello")
        mock_file.close = AsyncMock()
        mock_sftp.open = AsyncMock(return_value=mock_file)

        mock_sftp_cm = MagicMock()
        mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/tmp/test.txt"},
            ctx=mock_services,
        )
        text = result["content"][0]["text"]
        # Line number format: "     1|hello"
        assert "|" in text
        assert "hello" in text

    @pytest.mark.unit
    async def test_read_missing_path(self, mock_services):
        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": ""},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "required" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_read_missing_profile(self, mock_services):
        result = await _ssh_read_impl(
            {"profile_name": "no-such-profile", "path": "/etc/hostname"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_read_file_too_large(self, mock_services):
        """File larger than max_file_read_bytes returns an error before reading."""
        mock_sftp = AsyncMock()
        mock_stat = MagicMock()
        # max_file_read_bytes is 2048 from ssh_security_config fixture
        mock_stat.size = 99999
        mock_sftp.stat = AsyncMock(return_value=mock_stat)

        mock_sftp_cm = MagicMock()
        mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/var/log/big.log"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "too large" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_read_sftp_failure(self, mock_services):
        """SFTP open error returns a sanitised error without tracebacks."""
        mock_sftp = AsyncMock()
        mock_stat = MagicMock()
        mock_stat.size = 10
        mock_sftp.stat = AsyncMock(return_value=mock_stat)
        mock_sftp.open = AsyncMock(side_effect=PermissionError("Permission denied"))

        mock_sftp_cm = MagicMock()
        mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/shadow"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "failed to read" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_read_audit_logged(self, mock_services):
        """Successful read logs a file_access event."""
        mock_sftp = AsyncMock()
        mock_stat = MagicMock()
        mock_stat.size = 5
        mock_sftp.stat = AsyncMock(return_value=mock_stat)

        mock_file = AsyncMock()
        mock_file.read = AsyncMock(return_value=b"data!")
        mock_file.close = AsyncMock()
        mock_sftp.open = AsyncMock(return_value=mock_file)

        mock_sftp_cm = MagicMock()
        mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/hostname"},
            ctx=mock_services,
        )
        mock_services.audit_service.log_file_access.assert_awaited_once()

    @pytest.mark.unit
    async def test_read_header_contains_host(self, mock_services):
        """Result header includes the remote path and host."""
        mock_sftp = AsyncMock()
        mock_stat = MagicMock()
        mock_stat.size = 4
        mock_sftp.stat = AsyncMock(return_value=mock_stat)

        mock_file = AsyncMock()
        mock_file.read = AsyncMock(return_value=b"test")
        mock_file.close = AsyncMock()
        mock_sftp.open = AsyncMock(return_value=mock_file)

        mock_sftp_cm = MagicMock()
        mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
        mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)
        mock_services.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_read_impl(
            {"profile_name": "test-server", "path": "/etc/nginx/nginx.conf"},
            ctx=mock_services,
        )
        text = result["content"][0]["text"]
        assert "192.168.1.100" in text
        assert "/etc/nginx/nginx.conf" in text


# ---------------------------------------------------------------------------
# TestSSHConnectImpl
# ---------------------------------------------------------------------------

class TestSSHConnectImpl:

    @pytest.mark.unit
    async def test_ssh_disabled(self, ctx_disabled):
        result = await _ssh_connect_impl(
            {"action": "list", "profile_name": ""},
            ctx=ctx_disabled,
        )
        assert result.get("is_error") is True
        assert "disabled" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_list_profiles(self, mock_services):
        result = await _ssh_connect_impl(
            {"action": "list", "profile_name": ""},
            ctx=mock_services,
        )
        assert "is_error" not in result
        assert "test-server" in result["content"][0]["text"]

    @pytest.mark.unit
    async def test_list_shows_all_profiles(self, mock_services):
        """list action shows all configured profiles."""
        result = await _ssh_connect_impl(
            {"action": "list", "profile_name": ""},
            ctx=mock_services,
        )
        text = result["content"][0]["text"]
        assert "deploy" in text          # username from test_ssh_profile
        assert "192.168.1.100" in text  # host from test_ssh_profile

    @pytest.mark.unit
    async def test_connect(self, mock_services):
        result = await _ssh_connect_impl(
            {"action": "connect", "profile_name": "test-server"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        assert "Connected" in result["content"][0]["text"]

    @pytest.mark.unit
    async def test_connect_logs_audit(self, mock_services):
        """Successful connect logs a connection event."""
        await _ssh_connect_impl(
            {"action": "connect", "profile_name": "test-server"},
            ctx=mock_services,
        )
        mock_services.audit_service.log_connection.assert_awaited()

    @pytest.mark.unit
    async def test_disconnect(self, mock_services):
        result = await _ssh_connect_impl(
            {"action": "disconnect", "profile_name": "test-server"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        assert "Disconnected" in result["content"][0]["text"]

    @pytest.mark.unit
    async def test_disconnect_calls_pool(self, mock_services):
        """Disconnect calls release_connection on the pool."""
        await _ssh_connect_impl(
            {"action": "disconnect", "profile_name": "test-server"},
            ctx=mock_services,
        )
        mock_services.connection_pool.release_connection.assert_awaited_once_with(
            "test-session", "test-server"
        )

    @pytest.mark.unit
    async def test_status_not_connected(self, mock_services):
        """Status for a profile with no active connection."""
        mock_services.connection_pool.get_connection_info = MagicMock(return_value=[])

        result = await _ssh_connect_impl(
            {"action": "status", "profile_name": "test-server"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        assert "not connected" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_status_connected(self, mock_services):
        """Status shows alive when a connection entry is present."""
        mock_services.connection_pool.get_connection_info = MagicMock(return_value=[{
            "profile": "test-server",
            "host": "192.168.1.100",
            "port": 22,
            "username": "deploy",
            "connected_at": "2026-02-25T10:00:00",
            "last_activity": "2026-02-25T10:01:00",
            "idle_seconds": 60,
            "command_count": 5,
            "privilege_level": 0,
            "alive": True,
        }])

        result = await _ssh_connect_impl(
            {"action": "status", "profile_name": "test-server"},
            ctx=mock_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "alive" in text.lower()

    @pytest.mark.unit
    async def test_invalid_action(self, mock_services):
        result = await _ssh_connect_impl(
            {"action": "invalid", "profile_name": "test-server"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True

    @pytest.mark.unit
    async def test_missing_action(self, mock_services):
        result = await _ssh_connect_impl(
            {"action": "", "profile_name": "test-server"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True

    @pytest.mark.unit
    async def test_connect_missing_profile(self, mock_services):
        """connect with a nonexistent profile returns an error."""
        result = await _ssh_connect_impl(
            {"action": "connect", "profile_name": "no-such-profile"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_disconnect_missing_profile(self, mock_services):
        """disconnect with a nonexistent profile returns an error."""
        result = await _ssh_connect_impl(
            {"action": "disconnect", "profile_name": "no-such-profile"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True

    @pytest.mark.unit
    async def test_connect_connection_failure(self, mock_services):
        """Connection failure returns a sanitised error."""
        mock_services.connection_pool.get_connection = AsyncMock(
            side_effect=RuntimeError("Network unreachable")
        )

        result = await _ssh_connect_impl(
            {"action": "connect", "profile_name": "test-server"},
            ctx=mock_services,
        )
        assert result.get("is_error") is True
        assert "connection failed" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_list_no_active_connections_notice(self, mock_services):
        """list shows a notice when no sessions are active."""
        mock_services.connection_pool.get_connection_info = MagicMock(return_value=[])

        result = await _ssh_connect_impl(
            {"action": "list", "profile_name": ""},
            ctx=mock_services,
        )
        text = result["content"][0]["text"]
        assert "no active connections" in text.lower()
