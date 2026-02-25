"""
Tests for SSHConnectionPool.

Tests use mock asyncssh connections — no real SSH servers required.
All tests are async because the pool uses asyncio locks and tasks.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.ssh.ssh_connection_pool import (
    SSHConnectionPool,
    SSHConnectionEntry,
    SSHCommandResult,
    SSHConnectionLimitError,
)


class TestSSHConnectionPool:

    @pytest.fixture
    def pool(self):
        return SSHConnectionPool(
            idle_timeout_seconds=5,
            max_connections_per_session=2,
            health_check_interval_seconds=60,
        )

    def _make_mock_conn(self, closed=False):
        """Create a mock asyncssh connection."""
        conn = MagicMock()
        conn.is_closed = MagicMock(return_value=closed)
        conn.close = MagicMock()
        conn.wait_closed = AsyncMock()
        conn._host = "test.example.com"
        conn._port = 22
        conn._username = "deploy"
        return conn

    # -----------------------------------------------------------------------
    # Connection creation and reuse
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    async def test_get_connection_creates_new(self, pool):
        """First call for a key invokes connect_fn and stores the connection."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)

        conn = await pool.get_connection("sess1", "profile1", "user1", connect_fn)

        assert conn is mock_conn
        connect_fn.assert_awaited_once()
        assert pool.total_connections == 1

    @pytest.mark.unit
    async def test_get_connection_reuses_existing(self, pool):
        """Second call for the same key returns cached connection without reconnecting."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)

        conn1 = await pool.get_connection("sess1", "profile1", "user1", connect_fn)
        conn2 = await pool.get_connection("sess1", "profile1", "user1", connect_fn)

        assert conn1 is conn2
        assert connect_fn.await_count == 1

    @pytest.mark.unit
    async def test_get_connection_reconnects_dead(self, pool):
        """If the stored connection is closed, get_connection reconnects transparently."""
        live_conn = self._make_mock_conn(closed=False)
        dead_conn = self._make_mock_conn(closed=True)
        calls = []

        async def connect_fn():
            calls.append(len(calls))
            return dead_conn if len(calls) == 1 else live_conn

        # First call: dead_conn stored (it reports closed=True immediately)
        conn1 = await pool.get_connection("sess1", "profile1", "user1", connect_fn)
        assert conn1 is dead_conn
        assert pool.total_connections == 1

        # Second call: pool sees dead_conn is closed, cleans up, reconnects
        conn2 = await pool.get_connection("sess1", "profile1", "user1", connect_fn)
        assert conn2 is live_conn
        assert len(calls) == 2

    @pytest.mark.unit
    async def test_get_connection_stores_host_attributes(self, pool):
        """Pool reads _host, _port, _username from the mock connection."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)

        await pool.get_connection("sess1", "profile1", "user1", connect_fn)

        info = pool.get_connection_info("sess1")
        assert info[0]["host"] == "test.example.com"
        assert info[0]["port"] == 22
        assert info[0]["username"] == "deploy"

    # -----------------------------------------------------------------------
    # Connection limit
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    async def test_connection_limit_enforced(self, pool):
        """Third connection for the same session raises SSHConnectionLimitError (limit=2)."""
        for i in range(2):
            mock_conn = self._make_mock_conn()
            connect_fn = AsyncMock(return_value=mock_conn)
            await pool.get_connection("sess1", f"profile{i}", "user1", connect_fn)

        assert pool.total_connections == 2

        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)
        with pytest.raises(SSHConnectionLimitError):
            await pool.get_connection("sess1", "profile2", "user1", connect_fn)

        # Pool count unchanged
        assert pool.total_connections == 2

    @pytest.mark.unit
    async def test_different_sessions_independent_limits(self, pool):
        """Two sessions each get their own quota; sess2 can connect after sess1 is full."""
        for i in range(2):
            mock_conn = self._make_mock_conn()
            connect_fn = AsyncMock(return_value=mock_conn)
            await pool.get_connection("sess1", f"profile{i}", "user1", connect_fn)

        # sess2 is not limited by sess1 quota
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)
        conn = await pool.get_connection("sess2", "profile1", "user2", connect_fn)

        assert conn is mock_conn
        assert pool.total_connections == 3

    @pytest.mark.unit
    async def test_same_key_does_not_count_toward_limit(self, pool):
        """Getting the same profile twice does not increment the session count."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)

        await pool.get_connection("sess1", "profile1", "user1", connect_fn)
        await pool.get_connection("sess1", "profile1", "user1", connect_fn)
        # Only one unique key exists — second profile should still succeed
        mock_conn2 = self._make_mock_conn()
        connect_fn2 = AsyncMock(return_value=mock_conn2)
        conn = await pool.get_connection("sess1", "profile2", "user1", connect_fn2)

        assert conn is mock_conn2
        assert pool.total_connections == 2

    # -----------------------------------------------------------------------
    # Release and close
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    async def test_release_connection(self, pool):
        """release_connection closes and removes the entry for one profile."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)
        await pool.get_connection("sess1", "profile1", "user1", connect_fn)
        assert pool.total_connections == 1

        await pool.release_connection("sess1", "profile1")

        assert pool.total_connections == 0
        mock_conn.close.assert_called_once()

    @pytest.mark.unit
    async def test_release_nonexistent_is_noop(self, pool):
        """Releasing a key that was never registered does not raise."""
        await pool.release_connection("nonexistent", "none")
        assert pool.total_connections == 0

    @pytest.mark.unit
    async def test_close_session_connections_all(self, pool):
        """close_session_connections closes every connection for a session."""
        for i in range(2):
            mock_conn = self._make_mock_conn()
            connect_fn = AsyncMock(return_value=mock_conn)
            await pool.get_connection("sess1", f"profile{i}", "user1", connect_fn)

        closed = await pool.close_session_connections("sess1")

        assert closed == 2
        assert pool.total_connections == 0

    @pytest.mark.unit
    async def test_close_session_connections_only_target_session(self, pool):
        """close_session_connections leaves other sessions untouched."""
        for session, profile in [("sess1", "p1"), ("sess2", "p1")]:
            mock_conn = self._make_mock_conn()
            connect_fn = AsyncMock(return_value=mock_conn)
            await pool.get_connection(session, profile, "user1", connect_fn)

        closed = await pool.close_session_connections("sess1")

        assert closed == 1
        assert pool.total_connections == 1
        info = pool.get_connection_info("sess2")
        assert len(info) == 1

    @pytest.mark.unit
    async def test_close_session_empty_returns_zero(self, pool):
        """Closing connections for a session with no entries returns 0."""
        closed = await pool.close_session_connections("ghost-session")
        assert closed == 0

    # -----------------------------------------------------------------------
    # Activity recording
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    async def test_record_activity_increments_command_count(self, pool):
        """record_activity bumps the entry's command_count."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)
        await pool.get_connection("sess1", "profile1", "user1", connect_fn)

        pool.record_activity("sess1", "profile1")
        pool.record_activity("sess1", "profile1")

        info = pool.get_connection_info("sess1")
        assert info[0]["command_count"] == 2

    @pytest.mark.unit
    async def test_record_activity_nonexistent_is_noop(self, pool):
        """record_activity on an unknown key does not raise."""
        pool.record_activity("ghost-session", "ghost-profile")

    # -----------------------------------------------------------------------
    # get_connection_info
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    async def test_get_connection_info_returns_expected_keys(self, pool):
        """Info dict contains the documented fields."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)
        await pool.get_connection("sess1", "profile1", "user1", connect_fn)

        info = pool.get_connection_info("sess1")

        assert len(info) == 1
        entry = info[0]
        assert entry["profile"] == "profile1"
        assert entry["alive"] is True
        assert entry["command_count"] == 0
        assert "connected_at" in entry
        assert "last_activity" in entry
        assert "idle_seconds" in entry

    @pytest.mark.unit
    async def test_get_connection_info_empty(self, pool):
        """Info for a session with no connections returns an empty list."""
        assert pool.get_connection_info("nonexistent") == []

    @pytest.mark.unit
    async def test_get_connection_info_alive_false_for_dead(self, pool):
        """alive=False is reported when the stored connection is closed."""
        mock_conn = self._make_mock_conn(closed=True)
        # Bypass get_connection's reconnect logic by inserting directly
        entry = SSHConnectionEntry(
            conn=mock_conn,
            profile_name="dead-profile",
            host="host",
            port=22,
            username="user",
            user_id="u1",
            session_id="sess1",
        )
        pool._connections["sess1:dead-profile"] = entry

        info = pool.get_connection_info("sess1")
        assert info[0]["alive"] is False

    # -----------------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    async def test_shutdown_closes_all_connections(self, pool):
        """shutdown() empties the pool and closes every connection."""
        for i in range(2):
            mock_conn = self._make_mock_conn()
            connect_fn = AsyncMock(return_value=mock_conn)
            await pool.get_connection("sess1", f"profile{i}", "user1", connect_fn)

        await pool.shutdown()

        assert pool.total_connections == 0

    @pytest.mark.unit
    async def test_shutdown_on_empty_pool_is_safe(self, pool):
        """shutdown() on an empty pool does not raise."""
        await pool.shutdown()
        assert pool.total_connections == 0

    # -----------------------------------------------------------------------
    # Watchdog reset
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    async def test_reset_watchdog_creates_new_task(self, pool):
        """_reset_watchdog cancels old task and starts a new one."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)
        await pool.get_connection("sess1", "profile1", "user1", connect_fn)

        key = "sess1:profile1"
        entry = pool._connections[key]
        old_task = entry._watchdog_task
        assert old_task is not None

        # Reset watchdog — should cancel old and create new task
        pool._reset_watchdog(key, entry)

        # Old task should be different from new task (cancel + recreate)
        assert entry._watchdog_task is not old_task
        assert entry._watchdog_task is not None
        assert not entry._watchdog_task.done()
        # Old task was cancel()'d — it enters "cancelling" state
        assert old_task.cancelling() or old_task.cancelled()

    @pytest.mark.unit
    async def test_get_connection_reuse_resets_watchdog(self, pool):
        """Second get_connection call for same key resets watchdog timer."""
        mock_conn = self._make_mock_conn()
        connect_fn = AsyncMock(return_value=mock_conn)
        await pool.get_connection("sess1", "profile1", "user1", connect_fn)

        key = "sess1:profile1"
        first_task = pool._connections[key]._watchdog_task
        assert first_task is not None

        # Reuse connection — should reset watchdog
        await pool.get_connection("sess1", "profile1", "user1", connect_fn)

        second_task = pool._connections[key]._watchdog_task
        assert first_task.cancelling() or first_task.cancelled()
        assert second_task is not first_task

    # -----------------------------------------------------------------------
    # SSHCommandResult dataclass
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_ssh_command_result_defaults(self):
        """SSHCommandResult has sensible defaults for optional fields."""
        result = SSHCommandResult(
            exit_code=0,
            stdout="hello",
            stderr="",
            command="echo hello",
        )
        assert result.timed_out is False
        assert result.connection_lost is False

    @pytest.mark.unit
    def test_ssh_command_result_fields(self):
        """SSHCommandResult stores all provided values."""
        result = SSHCommandResult(
            exit_code=1,
            stdout="out",
            stderr="err",
            command="ls /nope",
            timed_out=True,
            connection_lost=True,
        )
        assert result.exit_code == 1
        assert result.timed_out is True
        assert result.connection_lost is True
