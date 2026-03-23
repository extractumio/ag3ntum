"""
Unit tests for SSHAuditService.

Uses in-memory SQLite via ssh_db fixture (see conftest.py).
All writes go to the SSHAuditEvent table; reads use the query methods.
"""
import pytest
from src.services.ssh_audit_service import SSHAuditService


class TestSSHAuditService:

    @pytest.mark.unit
    async def test_log_command(self, audit_service, ssh_db, test_user_with_jwt):
        event_id = await audit_service.log_command(
            ssh_db,
            session_id="sess-1",
            user_id=test_user_with_jwt.id,
            ssh_profile="test-profile",
            remote_host="192.168.1.100",
            remote_user="deploy",
            remote_port=22,
            command="uptime",
            exit_code=0,
            output_bytes=42,
            duration_ms=150,
            privilege_level=0,
            mode="readonly",
        )
        assert event_id > 0

    @pytest.mark.unit
    async def test_log_blocked(self, audit_service, ssh_db, test_user_with_jwt):
        event_id = await audit_service.log_blocked(
            ssh_db,
            session_id="sess-1",
            user_id=test_user_with_jwt.id,
            ssh_profile="test-profile",
            remote_host="192.168.1.100",
            remote_user="deploy",
            remote_port=22,
            command="rm -rf /",
            reason="Destructive command blocked",
            rule="P0_observer:allowlist",
            privilege_level=0,
            mode="readonly",
        )
        assert event_id > 0

    @pytest.mark.unit
    async def test_query_by_session(self, audit_service, ssh_db, test_user_with_jwt):
        for i in range(3):
            await audit_service.log_command(
                ssh_db, "sess-q", test_user_with_jwt.id,
                "profile", "host", "user", 22,
                f"cmd-{i}", 0, 10, 100, 0, "readonly",
            )

        events = await audit_service.query_by_session(ssh_db, "sess-q")
        assert len(events) == 3

    @pytest.mark.unit
    async def test_query_blocked(self, audit_service, ssh_db, test_user_with_jwt):
        await audit_service.log_blocked(
            ssh_db, "sess-b", test_user_with_jwt.id,
            "profile", "host", "user", 22,
            "bad cmd", "reason", "rule", 0, "readonly",
        )
        blocked = await audit_service.query_blocked(ssh_db, test_user_with_jwt.id)
        assert len(blocked) >= 1
        assert blocked[0]["blocked"] is True

    @pytest.mark.unit
    async def test_get_stats(self, audit_service, ssh_db, test_user_with_jwt):
        uid = test_user_with_jwt.id
        await audit_service.log_command(
            ssh_db, "sess-s", uid, "p", "host1", "u", 22, "cmd1", 0, 10, 100, 0, "readonly",
        )
        await audit_service.log_command(
            ssh_db, "sess-s", uid, "p", "host2", "u", 22, "cmd2", 0, 10, 100, 0, "readonly",
        )
        await audit_service.log_blocked(
            ssh_db, "sess-s", uid, "p", "host1", "u", 22, "bad", "r", "rule", 0, "readonly",
        )

        stats = await audit_service.get_stats(ssh_db, uid)
        assert stats["total_commands"] == 2
        assert stats["total_blocked"] == 1
        assert stats["unique_hosts"] >= 1

    @pytest.mark.unit
    async def test_log_file_access(self, audit_service, ssh_db, test_user_with_jwt):
        event_id = await audit_service.log_file_access(
            ssh_db, "sess-f", test_user_with_jwt.id,
            "profile", "host", "user", 22,
            "/etc/nginx/nginx.conf", "read_file",
        )
        assert event_id > 0

    @pytest.mark.unit
    async def test_log_connection(self, audit_service, ssh_db, test_user_with_jwt):
        event_id = await audit_service.log_connection(
            ssh_db, "sess-c", test_user_with_jwt.id,
            "profile", "host", "user", 22, "connect",
        )
        assert event_id > 0

    @pytest.mark.unit
    async def test_query_by_session_returns_newest_first(
        self, audit_service, ssh_db, test_user_with_jwt
    ):
        """Events are returned ordered by timestamp descending."""
        for i in range(3):
            await audit_service.log_command(
                ssh_db, "sess-order", test_user_with_jwt.id,
                "profile", "host", "user", 22,
                f"ordered-cmd-{i}", 0, 10, 100, 0, "readonly",
            )

        events = await audit_service.query_by_session(ssh_db, "sess-order")
        assert len(events) == 3
        # Timestamps must be monotonically non-increasing
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.unit
    async def test_query_by_session_empty(self, audit_service, ssh_db):
        """Querying a session with no events returns an empty list."""
        events = await audit_service.query_by_session(ssh_db, "no-such-session")
        assert events == []

    @pytest.mark.unit
    async def test_query_blocked_empty_when_none(
        self, audit_service, ssh_db, test_user_with_jwt
    ):
        """User with no blocked events returns an empty list."""
        import uuid
        other_uid = str(uuid.uuid4())
        blocked = await audit_service.query_blocked(ssh_db, other_uid)
        assert blocked == []

    @pytest.mark.unit
    async def test_get_stats_file_accesses(
        self, audit_service, ssh_db, test_user_with_jwt
    ):
        uid = test_user_with_jwt.id
        await audit_service.log_file_access(
            ssh_db, "sess-fa", uid, "p", "host", "u", 22,
            "/etc/passwd", "read_file",
        )
        stats = await audit_service.get_stats(ssh_db, uid)
        assert stats["total_file_accesses"] >= 1

    @pytest.mark.unit
    async def test_get_stats_anomalies(
        self, audit_service, ssh_db, test_user_with_jwt
    ):
        uid = test_user_with_jwt.id
        await audit_service.log_anomaly(
            ssh_db, "sess-an", uid, "p", "host", "u", 22,
            "rapid_commands", "50 commands in 10s",
        )
        stats = await audit_service.get_stats(ssh_db, uid)
        assert stats["total_anomalies"] >= 1

    @pytest.mark.unit
    async def test_get_stats_unique_hosts(
        self, audit_service, ssh_db, test_user_with_jwt
    ):
        uid = test_user_with_jwt.id
        for host in ["host-a", "host-b", "host-a"]:  # 2 unique
            await audit_service.log_command(
                ssh_db, "sess-uh", uid, "p", host, "u", 22,
                "uptime", 0, 10, 100, 0, "readonly",
            )
        stats = await audit_service.get_stats(ssh_db, uid)
        assert stats["unique_hosts"] == 2

    @pytest.mark.unit
    async def test_log_command_with_optional_flags(
        self, audit_service, ssh_db, test_user_with_jwt
    ):
        """human_approved and context_isolated are stored correctly."""
        event_id = await audit_service.log_command(
            ssh_db,
            session_id="sess-flags",
            user_id=test_user_with_jwt.id,
            ssh_profile="test-profile",
            remote_host="192.168.1.100",
            remote_user="deploy",
            remote_port=22,
            command="mysqldump production",
            exit_code=0,
            output_bytes=1024,
            duration_ms=500,
            privilege_level=2,
            mode="filtered_shell",
            human_approved=True,
            context_isolated=True,
        )
        assert event_id > 0

    @pytest.mark.unit
    async def test_to_dict_fields(self, audit_service, ssh_db, test_user_with_jwt):
        """_to_dict includes all expected keys."""
        await audit_service.log_command(
            ssh_db, "sess-dict", test_user_with_jwt.id,
            "profile", "host", "user", 22, "uptime", 0, 10, 100, 0, "readonly",
        )
        events = await audit_service.query_by_session(ssh_db, "sess-dict")
        assert len(events) == 1
        event = events[0]
        expected_keys = {
            "id", "session_id", "user_id", "ssh_profile",
            "remote_host", "remote_user", "remote_port",
            "operation", "privilege_level", "command", "remote_path",
            "exit_code", "output_bytes", "duration_ms", "mode",
            "blocked", "block_reason", "block_rule",
            "human_approved", "context_isolated",
            "anomaly_detected", "anomaly_type",
            "relay_used", "relay_audit_id", "timestamp",
        }
        assert expected_keys.issubset(set(event.keys()))

    @pytest.mark.unit
    async def test_log_anomaly(self, audit_service, ssh_db, test_user_with_jwt):
        event_id = await audit_service.log_anomaly(
            ssh_db, "sess-anom", test_user_with_jwt.id,
            "profile", "host", "user", 22,
            "rapid_commands", "100 commands in 60s",
            privilege_level=0,
            mode="readonly",
        )
        assert event_id > 0
