"""
Unit tests for SSHWrite _impl function, WriteTracker, and WriteBudget.

Tests the _ssh_write_impl function directly — bypassing the MCP wrapper
per CLAUDE.md gotcha #19.

All service dependencies are mocked. No real SSH connections are made.
"""
import asyncio
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from tools.ag3ntum.ag3ntum_ssh.tool import (
    SSHToolContext,
    WriteTracker,
    WriteBudget,
    ReadRecord,
    _ssh_write_impl,
    _compute_diff,
    _L2_ALLOWED_EXTENSIONS,
)
from src.core.ssh.ssh_config import SSHProfile, SSHSecurityConfig, SSHConnectionLimits
from src.core.ssh.ssh_command_filter import SSHCommandFilter, SSHFilterResult


# Auto-mock _check_ssh_enabled to return True for all tests in this module
@pytest.fixture(autouse=True)
def mock_ssh_enabled_check():
    """Default: SSH is enabled for the test user."""
    with patch(
        "tools.ag3ntum.ag3ntum_ssh.tool._check_ssh_enabled",
        new_callable=AsyncMock,
        return_value=True,
    ):
        yield


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
def mock_command_filter_allow_write():
    """Command filter that allows all write paths."""
    f = MagicMock()
    f.check_command = MagicMock(return_value=SSHFilterResult(
        allowed=True, action="allow", reason="ok",
        rule="L3:allow", category="admin",
    ))
    f.check_path_writable = MagicMock(return_value=SSHFilterResult(
        allowed=True, action="allow", reason="writable",
        rule="L3:write_allowed", category="write_access",
    ))
    f.check_path_readable = MagicMock(return_value=SSHFilterResult(
        allowed=True, action="allow", reason="readable",
        rule="L3:read_allowed", category="read_access",
    ))
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
def ssh_security_config_with_write():
    """SSH config that has max_file_write_bytes set."""
    return SSHSecurityConfig(
        enabled=True,
        default_mode="filtered_shell",
        limits=SSHConnectionLimits(
            max_connections_per_user=3,
            max_concurrent_commands=5,
            command_timeout_seconds=30,
            max_output_bytes=1024,
            max_file_read_bytes=2048,
            max_file_write_bytes=10_485_760,  # 10MB
        ),
    )


@pytest.fixture
def test_l3_profile():
    """L3 admin profile that can write files."""
    return SSHProfile(
        name="prod-web",
        host="10.0.1.1",
        port=22,
        username="admin",
        auth_method="key",
        key_ref="admin-key",
        mode="filtered_shell",
        privilege_level=3,
    )


@pytest.fixture
def write_tracker():
    return WriteTracker()


@pytest.fixture
def write_budget():
    return WriteBudget(max_bytes=10_485_760)


@pytest.fixture
def mock_write_services(
    ssh_security_config_with_write,
    test_l3_profile,
    mock_db_session,
    mock_connection_pool,
    mock_command_filter_allow_write,
    mock_credential_vault,
    mock_audit_service,
    write_tracker,
    write_budget,
):
    """SSHToolContext with write-capable setup."""
    # Pre-populate the WriteTracker so SSHWrite doesn't block
    write_tracker.record_read(
        "prod-web", "/etc/nginx/nginx.conf", "abc123def456", 500
    )
    return SSHToolContext(
        session_id="test-session",
        user_id="test-user",
        security_config=ssh_security_config_with_write,
        connection_pool=mock_connection_pool,
        command_filter=mock_command_filter_allow_write,
        credential_vault=mock_credential_vault,
        audit_service=mock_audit_service,
        profiles={"prod-web": test_l3_profile},
        db_session_factory=mock_db_session,
        write_tracker=write_tracker,
        write_budget=write_budget,
    )


def _make_mock_sftp(
    file_exists: bool = True,
    file_content: bytes = b"old content\n",
    stat_permissions: int = 0o644,
    makedirs_fails: bool = False,
    rename_fails: bool = False,
):
    """Build a mock SFTP client for SSHWrite tests.

    Returns (mock_sftp, mock_sftp_cm, mock_conn).
    """
    mock_sftp = AsyncMock()

    # stat — if file exists, return a stat result; else raise
    if file_exists:
        stat_result = MagicMock()
        stat_result.permissions = stat_permissions
        stat_result.size = 50
        mock_sftp.stat = AsyncMock(return_value=stat_result)
    else:
        mock_sftp.stat = AsyncMock(side_effect=FileNotFoundError("not found"))

    # statvfs — pretend plenty of space
    vfs = MagicMock()
    vfs.avail = 1_000_000
    vfs.bsize = 512
    mock_sftp.statvfs = AsyncMock(return_value=vfs)

    # realpath — same as input (no symlinks)
    mock_sftp.realpath = AsyncMock(side_effect=lambda p: p)

    # open — return different content based on mode
    def _open_factory(path, mode, *args, **kwargs):
        mock_file = AsyncMock()
        if "r" in mode:
            mock_file.read = AsyncMock(return_value=file_content)
        else:
            mock_file.read = AsyncMock(return_value=b"")
        mock_file.write = AsyncMock()
        mock_file.close = AsyncMock()
        return mock_file

    mock_sftp.open = AsyncMock(side_effect=_open_factory)

    # makedirs / mkdir
    if makedirs_fails:
        mock_sftp.makedirs = AsyncMock(side_effect=AttributeError("no makedirs"))
        mock_sftp.mkdir = AsyncMock(side_effect=PermissionError("mkdir denied"))
    else:
        mock_sftp.makedirs = AsyncMock()
        mock_sftp.mkdir = AsyncMock()

    # listdir — no existing backups
    mock_sftp.listdir = AsyncMock(return_value=[])

    # remove — noop
    mock_sftp.remove = AsyncMock()

    # chmod — noop
    mock_sftp.chmod = AsyncMock()

    # rename
    if rename_fails:
        # posix_rename is tried first — make it fail so fallback rename is used,
        # which is also mocked to fail (simulating a real rename failure).
        mock_sftp.posix_rename = AsyncMock(side_effect=PermissionError("posix_rename denied"))
        mock_sftp.rename = AsyncMock(side_effect=PermissionError("rename denied"))
    else:
        mock_sftp.posix_rename = AsyncMock()
        mock_sftp.rename = AsyncMock()

    mock_sftp_cm = MagicMock()
    mock_sftp_cm.__aenter__ = AsyncMock(return_value=mock_sftp)
    mock_sftp_cm.__aexit__ = AsyncMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.start_sftp_client = MagicMock(return_value=mock_sftp_cm)

    return mock_sftp, mock_sftp_cm, mock_conn


# ---------------------------------------------------------------------------
# TestWriteTracker
# ---------------------------------------------------------------------------

class TestWriteTracker:

    @pytest.mark.unit
    def test_record_and_retrieve(self):
        wt = WriteTracker()
        wt.record_read("prod", "/etc/nginx.conf", "checksum123", 1024)
        rec = wt.get_read_record("prod", "/etc/nginx.conf")
        assert rec is not None
        assert rec.checksum == "checksum123"
        assert rec.size == 1024

    @pytest.mark.unit
    def test_different_profiles_isolated(self):
        wt = WriteTracker()
        wt.record_read("prod", "/etc/foo", "hash1", 10)
        wt.record_read("staging", "/etc/foo", "hash2", 20)
        assert wt.get_read_record("prod", "/etc/foo").checksum == "hash1"
        assert wt.get_read_record("staging", "/etc/foo").checksum == "hash2"

    @pytest.mark.unit
    def test_unread_path_returns_none(self):
        wt = WriteTracker()
        assert wt.get_read_record("prod", "/etc/unread.conf") is None

    @pytest.mark.unit
    def test_clear_session_removes_all(self):
        wt = WriteTracker()
        wt.record_read("prod", "/etc/a", "h1", 10)
        wt.record_read("prod", "/etc/b", "h2", 20)
        wt.clear_session()
        assert wt.get_read_record("prod", "/etc/a") is None
        assert wt.get_read_record("prod", "/etc/b") is None

    @pytest.mark.unit
    def test_overwrite_updates_checksum(self):
        wt = WriteTracker()
        wt.record_read("prod", "/etc/foo", "old", 5)
        wt.record_read("prod", "/etc/foo", "new", 10)
        assert wt.get_read_record("prod", "/etc/foo").checksum == "new"


# ---------------------------------------------------------------------------
# TestWriteBudget
# ---------------------------------------------------------------------------

class TestWriteBudget:

    @pytest.mark.unit
    def test_initial_state(self):
        wb = WriteBudget(max_bytes=1000)
        assert wb.remaining == 1000
        assert wb.check(999)
        assert wb.check(1000)
        assert not wb.check(1001)

    @pytest.mark.unit
    def test_record_reduces_remaining(self):
        wb = WriteBudget(max_bytes=1000)
        wb.record(400)
        assert wb.remaining == 600
        assert wb.check(600)
        assert not wb.check(601)

    @pytest.mark.unit
    def test_exhausted_budget(self):
        wb = WriteBudget(max_bytes=100)
        wb.record(100)
        assert wb.remaining == 0
        assert not wb.check(1)

    @pytest.mark.unit
    def test_default_budget_is_10mb(self):
        wb = WriteBudget()
        assert wb.remaining == 10_485_760


# ---------------------------------------------------------------------------
# TestComputeDiff
# ---------------------------------------------------------------------------

class TestComputeDiff:

    @pytest.mark.unit
    def test_identical_content(self):
        diff = _compute_diff("hello\n", "hello\n", "/tmp/test.txt")
        assert diff == "(no changes)"

    @pytest.mark.unit
    def test_changed_content(self):
        diff = _compute_diff("old line\n", "new line\n", "/tmp/test.txt")
        assert "-old line" in diff
        assert "+new line" in diff

    @pytest.mark.unit
    def test_empty_old(self):
        diff = _compute_diff("", "new content\n", "/tmp/test.txt")
        assert "+new content" in diff

    @pytest.mark.unit
    def test_truncated_at_max_lines(self):
        old = "\n".join(f"line {i}" for i in range(100)) + "\n"
        new = "\n".join(f"modified {i}" for i in range(100)) + "\n"
        diff = _compute_diff(old, new, "/tmp/big.txt", max_lines=10)
        assert "more lines" in diff

    @pytest.mark.unit
    def test_path_appears_in_header(self):
        diff = _compute_diff("a\n", "b\n", "/etc/nginx.conf")
        assert "/etc/nginx.conf" in diff


# ---------------------------------------------------------------------------
# TestL2AllowedExtensions
# ---------------------------------------------------------------------------

class TestL2AllowedExtensions:

    @pytest.mark.unit
    def test_allowed_extensions_present(self):
        for ext in [".conf", ".yaml", ".yml", ".json", ".toml", ".ini", ".txt"]:
            assert ext in _L2_ALLOWED_EXTENSIONS, f"{ext} should be in allowlist"

    @pytest.mark.unit
    def test_dangerous_extensions_absent(self):
        for ext in [".py", ".sh", ".rb", ".php", ".exe", ".bin"]:
            assert ext not in _L2_ALLOWED_EXTENSIONS, f"{ext} should NOT be in allowlist"


# ---------------------------------------------------------------------------
# TestSSHWriteImpl — validation phase
# ---------------------------------------------------------------------------

class TestSSHWriteImplValidation:

    @pytest.mark.unit
    async def test_ssh_disabled_returns_error(self, mock_write_services):
        with patch(
            "tools.ag3ntum.ag3ntum_ssh.tool._check_ssh_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await _ssh_write_impl(
                {"profile_name": "prod-web", "path": "/etc/test.conf",
                 "content": "data"},
                ctx=mock_write_services,
            )
        assert result.get("is_error") is True
        assert "disabled" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_missing_path_returns_error(self, mock_write_services):
        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "", "content": "data"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "required" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_relative_path_returns_error(self, mock_write_services):
        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "relative/path.conf",
             "content": "data"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "absolute" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_empty_content_returns_error(self, mock_write_services):
        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/test.conf", "content": ""},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "empty" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_missing_profile_returns_error(self, mock_write_services):
        result = await _ssh_write_impl(
            {"profile_name": "nonexistent", "path": "/etc/test.conf",
             "content": "data"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_write_denied_by_path_filter(self, mock_write_services):
        mock_write_services.command_filter.check_path_writable = MagicMock(
            return_value=SSHFilterResult(
                allowed=False, action="block",
                reason="path is in blocked list",
                rule="L3:blocked_paths", category="blocked_path",
            )
        )
        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/passwd", "content": "data"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "write denied" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_write_budget_exceeded(
        self, mock_write_services, write_budget
    ):
        write_budget._total = write_budget._max  # exhaust the budget
        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/test.conf",
             "content": "data"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "budget" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_no_prior_read_returns_error(
        self, mock_write_services, write_tracker
    ):
        """WriteTracker enforcement: must read before writing."""
        write_tracker._reads.clear()  # wipe pre-populated read
        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new content"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "must be read" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_l2_extension_blocked(self, mock_write_services, test_l3_profile):
        """L2 profile: .py extension is not allowed."""
        from src.core.ssh.ssh_config import SSHProfile
        l2_profile = SSHProfile(
            name="l2-server",
            host="10.0.1.1",
            port=22,
            username="ops",
            auth_method="key",
            key_ref="ops-key",
            mode="operations",
            privilege_level=2,
        )
        # Override the context with L2 profile
        mock_write_services.write_tracker.record_read(
            "l2-server", "/app/script.py", "hash", 100
        )
        ctx = SSHToolContext(
            session_id=mock_write_services.session_id,
            user_id=mock_write_services.user_id,
            security_config=mock_write_services.security_config,
            connection_pool=mock_write_services.connection_pool,
            command_filter=mock_write_services.command_filter,
            credential_vault=mock_write_services.credential_vault,
            audit_service=mock_write_services.audit_service,
            profiles={"l2-server": l2_profile},
            db_session_factory=mock_write_services.db_session_factory,
            write_tracker=mock_write_services.write_tracker,
            write_budget=mock_write_services.write_budget,
        )
        result = await _ssh_write_impl(
            {"profile_name": "l2-server", "path": "/app/script.py",
             "content": "code"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        assert "extension" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_l2_allowed_extension_passes_filter(self, mock_write_services):
        """L2 profile: .conf extension is allowed through the extension check."""
        from src.core.ssh.ssh_config import SSHProfile
        l2_profile = SSHProfile(
            name="l2-server",
            host="10.0.1.1",
            port=22,
            username="ops",
            auth_method="key",
            key_ref="ops-key",
            mode="operations",
            privilege_level=2,
        )
        mock_write_services.write_tracker.record_read(
            "l2-server", "/etc/app.conf", "hash", 100
        )
        ctx = SSHToolContext(
            session_id=mock_write_services.session_id,
            user_id=mock_write_services.user_id,
            security_config=mock_write_services.security_config,
            connection_pool=mock_write_services.connection_pool,
            command_filter=mock_write_services.command_filter,
            credential_vault=mock_write_services.credential_vault,
            audit_service=mock_write_services.audit_service,
            profiles={"l2-server": l2_profile},
            db_session_factory=mock_write_services.db_session_factory,
            write_tracker=mock_write_services.write_tracker,
            write_budget=mock_write_services.write_budget,
        )
        _, _, mock_conn = _make_mock_sftp(file_exists=False)
        ctx.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_write_impl(
            {"profile_name": "l2-server", "path": "/etc/app.conf",
             "content": "key=value\n"},
            ctx=ctx,
        )
        # Should not error on extension check — may fail or succeed elsewhere
        if result.get("is_error"):
            assert "extension" not in result["content"][0]["text"].lower()


# ---------------------------------------------------------------------------
# TestSSHWriteImpl — happy path
# ---------------------------------------------------------------------------

class TestSSHWriteImplHappyPath:

    @pytest.mark.unit
    async def test_write_new_file_success(self, mock_write_services):
        """Writing a new file (doesn't exist yet) succeeds."""
        _, _, mock_conn = _make_mock_sftp(file_exists=False)
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "worker_processes 4;\n"},
            ctx=mock_write_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "written successfully" in text.lower()
        assert "prod-web" in text
        assert "/etc/nginx/nginx.conf" in text

    @pytest.mark.unit
    async def test_write_existing_file_creates_backup(self, mock_write_services):
        """Writing an existing file creates a backup."""
        mock_sftp, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"old content\n"
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new content\n"},
            ctx=mock_write_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "backup" in text.lower()

    @pytest.mark.unit
    async def test_write_returns_diff(self, mock_write_services):
        """Response includes a unified diff."""
        _, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"old line\n"
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new line\n"},
            ctx=mock_write_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "Diff:" in text

    @pytest.mark.unit
    async def test_write_audit_logged(self, mock_write_services):
        """Successful write logs a file_access audit event."""
        _, _, mock_conn = _make_mock_sftp(file_exists=False)
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "data\n"},
            ctx=mock_write_services,
        )
        mock_write_services.audit_service.log_file_access.assert_awaited()

    @pytest.mark.unit
    async def test_write_budget_decremented(self, mock_write_services, write_budget):
        """Successful write deducts bytes from the write budget."""
        _, _, mock_conn = _make_mock_sftp(file_exists=False)
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        content = "hello world\n"
        expected_bytes = len(content.encode("utf-8"))
        initial_remaining = write_budget.remaining

        await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": content},
            ctx=mock_write_services,
        )
        assert write_budget.remaining == initial_remaining - expected_bytes

    @pytest.mark.unit
    async def test_write_connection_pool_activity_recorded(self, mock_write_services):
        """Successful write records pool activity."""
        _, _, mock_conn = _make_mock_sftp(file_exists=False)
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "data\n"},
            ctx=mock_write_services,
        )
        mock_write_services.connection_pool.record_activity.assert_called_once()

    @pytest.mark.unit
    async def test_write_shows_duration(self, mock_write_services):
        """Response includes duration in milliseconds."""
        _, _, mock_conn = _make_mock_sftp(file_exists=False)
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "data\n"},
            ctx=mock_write_services,
        )
        assert "is_error" not in result
        assert "Duration:" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# TestSSHWriteImpl — dry run
# ---------------------------------------------------------------------------

class TestSSHWriteImplDryRun:

    @pytest.mark.unit
    async def test_dry_run_shows_preview(self, mock_write_services):
        """Dry run returns preview text without writing."""
        mock_sftp, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"old content\n"
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new content\n", "dry_run": True},
            ctx=mock_write_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "DRY RUN" in text
        assert "no changes made" in text.lower()

    @pytest.mark.unit
    async def test_dry_run_no_write_occurs(self, mock_write_services):
        """Dry run: rename (the atomic write) is never called."""
        mock_sftp, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"old\n"
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new\n", "dry_run": True},
            ctx=mock_write_services,
        )
        mock_sftp.rename.assert_not_awaited()

    @pytest.mark.unit
    async def test_dry_run_includes_diff(self, mock_write_services):
        """Dry run response includes a diff."""
        mock_sftp, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"old line\n"
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new line\n", "dry_run": True},
            ctx=mock_write_services,
        )
        assert "Diff:" in result["content"][0]["text"]

    @pytest.mark.unit
    async def test_dry_run_does_not_audit(self, mock_write_services):
        """Dry run does not log an audit event."""
        mock_sftp, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"old\n"
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new\n", "dry_run": True},
            ctx=mock_write_services,
        )
        mock_write_services.audit_service.log_file_access.assert_not_awaited()

    @pytest.mark.unit
    async def test_dry_run_does_not_deduct_budget(self, mock_write_services, write_budget):
        """Dry run does not consume write budget."""
        mock_sftp, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"old\n"
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        initial = write_budget.remaining
        await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new\n", "dry_run": True},
            ctx=mock_write_services,
        )
        assert write_budget.remaining == initial


# ---------------------------------------------------------------------------
# TestSSHWriteImpl — conflict detection
# ---------------------------------------------------------------------------

class TestSSHWriteImplConflict:

    @pytest.mark.unit
    async def test_conflict_warning_on_modified_file(
        self, mock_write_services, write_tracker
    ):
        """If file content changed since the read, a warning is shown."""
        import hashlib
        # Record a read with hash of "original content"
        original = b"original content\n"
        write_tracker._reads[("prod-web", "/etc/nginx/nginx.conf")] = ReadRecord(
            checksum=hashlib.sha256(original).hexdigest(),
            size=len(original),
            read_at=0.0,
        )

        # SFTP returns "modified content" — different from what was recorded
        _, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"modified content\n"
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new content\n"},
            ctx=mock_write_services,
        )
        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "WARNING" in text
        assert "modified" in text.lower()

    @pytest.mark.unit
    async def test_no_conflict_when_unchanged(
        self, mock_write_services, write_tracker
    ):
        """No conflict warning when file matches the recorded checksum."""
        import hashlib
        unchanged = b"stable content\n"
        write_tracker._reads[("prod-web", "/etc/nginx/nginx.conf")] = ReadRecord(
            checksum=hashlib.sha256(unchanged).hexdigest(),
            size=len(unchanged),
            read_at=0.0,
        )

        _, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=unchanged
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "updated content\n"},
            ctx=mock_write_services,
        )
        assert "is_error" not in result
        assert "WARNING" not in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# TestSSHWriteImpl — error cases
# ---------------------------------------------------------------------------

class TestSSHWriteImplErrors:

    @pytest.mark.unit
    async def test_connection_failure(self, mock_write_services):
        mock_write_services.connection_pool.get_connection = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )
        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "data\n"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "connection failed" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_rename_failure_returns_error(self, mock_write_services):
        """Atomic rename failure returns error with backup path."""
        _, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=b"old\n", rename_fails=True
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "new\n"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "write failed" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_binary_file_rejected(self, mock_write_services):
        """Binary target file is rejected."""
        binary_content = b"ELF\x00\x00\x00" + bytes(100)  # lots of nulls
        _, _, mock_conn = _make_mock_sftp(
            file_exists=True, file_content=binary_content
        )
        mock_write_services.connection_pool.get_connection = AsyncMock(
            return_value=mock_conn
        )

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "safe text\n"},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "binary" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_content_size_exceeded(self, mock_write_services):
        """Content exceeding max_file_write_bytes is rejected."""
        big_content = "x" * (10_485_761)  # over 10MB
        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": big_content},
            ctx=mock_write_services,
        )
        assert result.get("is_error") is True
        assert "exceeds" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    async def test_no_write_tracker_skips_pre_read_check(
        self, mock_write_services
    ):
        """Without a WriteTracker, pre-read enforcement is skipped."""
        ctx = SSHToolContext(
            session_id=mock_write_services.session_id,
            user_id=mock_write_services.user_id,
            security_config=mock_write_services.security_config,
            connection_pool=mock_write_services.connection_pool,
            command_filter=mock_write_services.command_filter,
            credential_vault=mock_write_services.credential_vault,
            audit_service=mock_write_services.audit_service,
            profiles=mock_write_services.profiles,
            db_session_factory=mock_write_services.db_session_factory,
            write_tracker=None,  # no tracker
            write_budget=None,
        )
        _, _, mock_conn = _make_mock_sftp(file_exists=False)
        ctx.connection_pool.get_connection = AsyncMock(return_value=mock_conn)

        result = await _ssh_write_impl(
            {"profile_name": "prod-web", "path": "/etc/nginx/nginx.conf",
             "content": "data\n"},
            ctx=ctx,
        )
        # Should NOT error due to missing read (no tracker means no enforcement)
        assert "must be read" not in result.get("content", [{}])[0].get("text", "").lower()
