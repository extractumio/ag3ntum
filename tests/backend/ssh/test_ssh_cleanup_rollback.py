"""
Unit tests for SSHConnect cleanup_backups and rollback actions.

Tests the _ssh_connect_impl function directly for the
cleanup_backups and rollback action paths.
All SSH connections are mocked — no real servers needed.
"""
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.ssh.ssh_command_filter import SSHCommandFilter
from src.core.ssh.ssh_config import (
    SSHConnectionLimits,
    SSHProfile,
    SSHSecurityConfig,
)
from tools.ag3ntum.ag3ntum_ssh.tool import (
    SSHToolContext,
    WriteBudget,
    WriteTracker,
    _ssh_connect_impl,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_ssh_enabled():
    """Default: SSH is enabled for all tests in this module."""
    with patch(
        "tools.ag3ntum.ag3ntum_ssh.tool._check_ssh_enabled",
        new_callable=AsyncMock,
        return_value=True,
    ):
        yield


@pytest.fixture
def example_config_path():
    """Path to the example privilege-levels YAML."""
    return (
        Path(__file__).parent.parent.parent.parent
        / "config" / "security" / "ssh-privilege-levels.yaml.example"
    )


@pytest.fixture
def real_command_filter(example_config_path):
    """Real SSHCommandFilter loaded from example config."""
    return SSHCommandFilter(config_path=example_config_path)


@pytest.fixture
def l3_profile():
    """L3 administration profile for cleanup/rollback testing."""
    return SSHProfile(
        name="test-srv",
        host="10.0.1.5",
        port=22,
        username="admin",
        auth_method="key",
        key_ref="k",
        mode="filtered_shell",
        privilege_level=3,
    )


def _make_ctx(
    profile=None,
    command_filter=None,
    connection_pool=None,
    credential_vault=None,
    db_session_factory=None,
    audit_service=None,
):
    """Build a minimal SSHToolContext with sensible defaults."""
    if profile is None:
        profile = SSHProfile(
            name="test-srv",
            host="10.0.1.5",
            port=22,
            username="admin",
            auth_method="key",
            key_ref="k",
            mode="filtered_shell",
            privilege_level=3,
        )

    example_path = (
        Path(__file__).parent.parent.parent.parent
        / "config" / "security" / "ssh-privilege-levels.yaml.example"
    )

    if command_filter is None:
        command_filter = SSHCommandFilter(config_path=example_path)

    if connection_pool is None:
        pool = MagicMock()
        pool.get_connection_info.return_value = []
        connection_pool = pool

    if credential_vault is None:
        vault = AsyncMock()
        vault.get_connect_fn = AsyncMock(return_value=AsyncMock())
        credential_vault = vault

    if db_session_factory is None:
        @asynccontextmanager
        async def _factory():
            yield AsyncMock()
        db_session_factory = _factory

    if audit_service is None:
        audit = AsyncMock()
        audit.log_file_access = AsyncMock()
        audit_service = audit

    return SSHToolContext(
        session_id="sess-cleanup-test",
        user_id="user-cleanup-test",
        security_config=SSHSecurityConfig(
            enabled=True,
            limits=SSHConnectionLimits(),
        ),
        connection_pool=connection_pool,
        command_filter=command_filter,
        credential_vault=credential_vault,
        audit_service=audit_service,
        profiles={profile.name: profile},
        db_session_factory=db_session_factory,
        write_tracker=WriteTracker(),
        write_budget=WriteBudget(),
    )


def _make_pool_with_connection(mock_conn):
    """Build a mock connection pool that returns mock_conn."""
    pool = MagicMock()
    pool.get_connection = AsyncMock(return_value=mock_conn)
    pool.get_connection_info.return_value = []
    return pool


def _make_sftp_cm(mock_sftp):
    """Wrap a mock sftp in an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_sftp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_conn(mock_sftp):
    """Build a mock SSH connection that yields mock_sftp."""
    conn = MagicMock()
    conn.start_sftp_client = MagicMock(return_value=_make_sftp_cm(mock_sftp))
    return conn


# ---------------------------------------------------------------------------
# TestCleanupBackups
# ---------------------------------------------------------------------------

class TestCleanupBackups:
    """Tests for cleanup_backups action."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_lists_backups_with_sizes(self):
        """Listing backups (no cleanup_id) returns entries with sizes."""
        stat_mock = MagicMock()
        stat_mock.size = 1024

        mock_sftp = AsyncMock()
        mock_sftp.listdir = AsyncMock(return_value=["file1.bak", "file2.bak"])
        mock_sftp.stat = AsyncMock(return_value=stat_mock)

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv"},
            ctx=ctx,
        )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "file1.bak" in text
        assert "file2.bak" in text

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_empty_backup_dir_returns_message(self):
        """Empty backup directory returns a friendly no-backups message."""
        mock_sftp = AsyncMock()
        mock_sftp.listdir = AsyncMock(return_value=[])

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv"},
            ctx=ctx,
        )

        assert "is_error" not in result
        assert "No backups found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_missing_backup_dir_returns_message(self):
        """Non-existent backup directory returns a friendly message."""
        mock_sftp = AsyncMock()
        mock_sftp.listdir = AsyncMock(side_effect=Exception("no such file"))

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv"},
            ctx=ctx,
        )

        assert "is_error" not in result
        assert "No backups found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_all_requires_confirm(self):
        """cleanup_id='all' without confirm=true returns error."""
        mock_sftp = AsyncMock()
        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv",
             "cleanup_id": "all"},
            ctx=ctx,
        )

        assert result.get("is_error") is True
        assert "confirm=true" in result["content"][0]["text"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_all_with_confirm_deletes_entries(self):
        """cleanup_id='all' with confirm=true deletes all backup entries."""
        mock_sftp = AsyncMock()
        # listdir for the backup dir returns two entries
        mock_sftp.listdir = AsyncMock(return_value=["snap-1", "snap-2"])
        # sub-listdir raises (they are files, not dirs)
        mock_sftp.remove = AsyncMock()

        # Make inner listdir raise so entries are treated as files
        call_count = {"n": 0}

        async def _listdir(path):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ["snap-1", "snap-2"]
            raise Exception("not a dir")

        mock_sftp.listdir = _listdir
        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv",
             "cleanup_id": "all", "confirm": True},
            ctx=ctx,
        )

        assert "is_error" not in result
        assert "Deleted" in result["content"][0]["text"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_deletes_specific_snapshot(self):
        """cleanup_id with a specific name deletes that backup."""
        mock_sftp = AsyncMock()
        # listdir raises (target is a file, not dir)
        mock_sftp.listdir = AsyncMock(side_effect=Exception("not a dir"))
        mock_sftp.remove = AsyncMock()

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv",
             "cleanup_id": "nginx.conf.20260318T103045Z.bak"},
            ctx=ctx,
        )

        assert "is_error" not in result
        assert "Deleted" in result["content"][0]["text"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_specific_snapshot_not_found_returns_error(self):
        """Deleting a non-existent backup returns an error."""
        mock_sftp = AsyncMock()
        mock_sftp.listdir = AsyncMock(side_effect=Exception("not found"))
        mock_sftp.remove = AsyncMock(side_effect=Exception("file not found"))

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv",
             "cleanup_id": "nonexistent.bak"},
            ctx=ctx,
        )

        assert result.get("is_error") is True

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_connection_failure_returns_error(self):
        """Connection failure returns a meaningful error."""
        pool = MagicMock()
        pool.get_connection = AsyncMock(side_effect=RuntimeError("refused"))
        pool.get_connection_info.return_value = []

        ctx = _make_ctx(connection_pool=pool)

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv"},
            ctx=ctx,
        )

        assert result.get("is_error") is True
        assert "connection failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_lists_sizes(self):
        """Backup listing includes size information for each entry."""
        stat_mock = MagicMock()
        stat_mock.size = 512

        mock_sftp = AsyncMock()
        mock_sftp.listdir = AsyncMock(return_value=["backup.bak"])
        mock_sftp.stat = AsyncMock(return_value=stat_mock)

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "test-srv"},
            ctx=ctx,
        )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "512" in text  # size included

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cleanup_unknown_profile_returns_error(self):
        """Cleanup with an unknown profile name returns an error."""
        ctx = _make_ctx()
        result = await _ssh_connect_impl(
            {"action": "cleanup_backups", "profile_name": "unknown-prof"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"].lower()


# ---------------------------------------------------------------------------
# TestRollback
# ---------------------------------------------------------------------------

class TestRollback:
    """Tests for rollback action."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rollback_requires_snapshot_id(self):
        """Rollback without snapshot_id returns error."""
        ctx = _make_ctx()
        result = await _ssh_connect_impl(
            {"action": "rollback", "profile_name": "test-srv"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        assert "snapshot_id" in result["content"][0]["text"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rollback_nonexistent_snapshot_returns_error(self):
        """Rollback with missing manifest returns error."""
        mock_sftp = AsyncMock()
        # Manifest open raises
        mock_sftp.open = AsyncMock(side_effect=Exception("no such file"))

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "rollback", "profile_name": "test-srv",
             "snapshot_id": "nonexistent-snap"},
            ctx=ctx,
        )

        assert result.get("is_error") is True
        assert "manifest" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rollback_connection_failure_returns_error(self):
        """Rollback with connection failure returns error."""
        pool = MagicMock()
        pool.get_connection = AsyncMock(side_effect=RuntimeError("connection refused"))
        pool.get_connection_info.return_value = []

        ctx = _make_ctx(connection_pool=pool)

        result = await _ssh_connect_impl(
            {"action": "rollback", "profile_name": "test-srv",
             "snapshot_id": "snap-1"},
            ctx=ctx,
        )

        assert result.get("is_error") is True
        assert "connection failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rollback_restores_from_manifest(self):
        """Rollback reads manifest and restores files."""
        manifest = {
            "files": [
                {
                    "path": "/etc/nginx/nginx.conf",
                    "backup_name": "nginx.conf.original",
                }
            ]
        }

        # Track open() calls by path
        manifest_file = AsyncMock()
        manifest_file.read = AsyncMock(return_value=json.dumps(manifest).encode())
        manifest_file.close = AsyncMock()

        backup_file = AsyncMock()
        backup_file.read = AsyncMock(return_value=b"original content")
        backup_file.close = AsyncMock()

        temp_file = AsyncMock()
        temp_file.write = AsyncMock()
        temp_file.close = AsyncMock()

        async def _open(path, mode="rb"):
            if "manifest" in path:
                return manifest_file
            if ".original" in path or mode == "rb":
                return backup_file
            return temp_file

        mock_sftp = AsyncMock()
        mock_sftp.open = _open
        mock_sftp.rename = AsyncMock()

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "rollback", "profile_name": "test-srv",
             "snapshot_id": "ag3ntum-batch-20260318T114500Z"},
            ctx=ctx,
        )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        assert "restored" in text.lower()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rollback_validates_paths_before_restore(self):
        """Rollback blocks paths in the command filter blocked list."""
        # /etc/shadow is in blocked_paths for L2 and also blocked at L3
        # because blocked_paths are checked BEFORE the L3 unrestricted rule.
        manifest = {
            "files": [
                {
                    "path": "/etc/shadow",
                    "backup_name": "shadow.original",
                }
            ]
        }

        manifest_file = AsyncMock()
        manifest_file.read = AsyncMock(return_value=json.dumps(manifest).encode())
        manifest_file.close = AsyncMock()

        mock_sftp = AsyncMock()
        mock_sftp.open = AsyncMock(return_value=manifest_file)

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "rollback", "profile_name": "test-srv",
             "snapshot_id": "snap-1"},
            ctx=ctx,
        )

        assert result.get("is_error") is True
        text = result["content"][0]["text"].lower()
        assert "not writable" in text or "blocked" in text or "protected" in text

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rollback_unknown_profile_returns_error(self):
        """Rollback with an unknown profile returns an error."""
        ctx = _make_ctx()
        result = await _ssh_connect_impl(
            {"action": "rollback", "profile_name": "no-such-profile",
             "snapshot_id": "snap-1"},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rollback_empty_snapshot_id_returns_error(self):
        """Rollback with empty string snapshot_id returns error."""
        ctx = _make_ctx()
        result = await _ssh_connect_impl(
            {"action": "rollback", "profile_name": "test-srv",
             "snapshot_id": ""},
            ctx=ctx,
        )
        assert result.get("is_error") is True
        assert "snapshot_id" in result["content"][0]["text"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rollback_manifest_with_multiple_files(self):
        """Rollback reports results for all files in the manifest."""
        manifest = {
            "files": [
                {"path": "/etc/app/a.conf", "backup_name": "a.conf.original"},
                {"path": "/etc/app/b.conf", "backup_name": "b.conf.original"},
            ]
        }

        call_count = {"n": 0}

        manifest_file = AsyncMock()
        manifest_file.read = AsyncMock(return_value=json.dumps(manifest).encode())
        manifest_file.close = AsyncMock()

        content_file = AsyncMock()
        content_file.read = AsyncMock(return_value=b"content")
        content_file.close = AsyncMock()

        write_file = AsyncMock()
        write_file.write = AsyncMock()
        write_file.close = AsyncMock()

        async def _open(path, mode="rb"):
            if "manifest" in path:
                return manifest_file
            if mode == "rb":
                return content_file
            return write_file

        mock_sftp = AsyncMock()
        mock_sftp.open = _open
        mock_sftp.rename = AsyncMock()

        ctx = _make_ctx(connection_pool=_make_pool_with_connection(_make_conn(mock_sftp)))

        result = await _ssh_connect_impl(
            {"action": "rollback", "profile_name": "test-srv",
             "snapshot_id": "snap-multi"},
            ctx=ctx,
        )

        assert "is_error" not in result
        text = result["content"][0]["text"]
        # Both files should appear in the result
        assert "/etc/app/a.conf" in text
        assert "/etc/app/b.conf" in text
