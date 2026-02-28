"""
Tests for SSH tool hardening features.

Covers: credential redaction, rate limiting, approval tokens.
"""
import pytest

from tools.ag3ntum.ag3ntum_ssh.tool import (
    _redact_credentials,
    SSHApprovalStore,
)
from src.core.ssh.ssh_rate_limiter import SSHRateLimiter


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


class TestSSHRateLimiter:
    """Test SSH command rate limiter."""

    @pytest.mark.unit
    def test_allows_within_limit(self):
        """Commands within rate limit are allowed."""
        limiter = SSHRateLimiter(max_per_minute=5)
        for _ in range(5):
            assert limiter.check("session1", "profile1")

    @pytest.mark.unit
    def test_blocks_over_limit(self):
        """Commands exceeding rate limit are blocked."""
        limiter = SSHRateLimiter(max_per_minute=3)
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert not limiter.check("session1", "profile1")

    @pytest.mark.unit
    def test_separate_sessions(self):
        """Different sessions have independent rate limits."""
        limiter = SSHRateLimiter(max_per_minute=2)
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert not limiter.check("session1", "profile1")
        # Different session is still allowed
        assert limiter.check("session2", "profile1")

    @pytest.mark.unit
    def test_separate_profiles(self):
        """Different profiles have independent rate limits."""
        limiter = SSHRateLimiter(max_per_minute=2)
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert not limiter.check("session1", "profile1")
        # Different profile is still allowed
        assert limiter.check("session1", "profile2")

    @pytest.mark.unit
    def test_reset_clears_window(self):
        """Reset clears the rate limit window."""
        limiter = SSHRateLimiter(max_per_minute=2)
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile1")
        assert not limiter.check("session1", "profile1")
        limiter.reset("session1", "profile1")
        assert limiter.check("session1", "profile1")

    @pytest.mark.unit
    def test_reset_session(self):
        """reset_session clears all profiles for a session."""
        limiter = SSHRateLimiter(max_per_minute=1)
        limiter.check("session1", "profile1")
        limiter.check("session1", "profile2")
        limiter.reset_session("session1")
        assert limiter.check("session1", "profile1")
        assert limiter.check("session1", "profile2")


class TestSSHApprovalStore:
    """Test SSH command approval token store."""

    @pytest.mark.unit
    def test_unapproved_command(self):
        """Unapproved command returns False."""
        store = SSHApprovalStore()
        assert not store.is_approved("session1", "mysqldump mydb")

    @pytest.mark.unit
    def test_approved_command(self):
        """Approved command returns True on check."""
        store = SSHApprovalStore()
        store.approve("session1", "mysqldump mydb")
        assert store.is_approved("session1", "mysqldump mydb")

    @pytest.mark.unit
    def test_approval_is_session_scoped(self):
        """Approval in one session doesn't carry to another."""
        store = SSHApprovalStore()
        store.approve("session1", "mysqldump mydb")
        assert not store.is_approved("session2", "mysqldump mydb")

    @pytest.mark.unit
    def test_approval_is_command_specific(self):
        """Approval for one command doesn't apply to another."""
        store = SSHApprovalStore()
        store.approve("session1", "mysqldump mydb")
        assert not store.is_approved("session1", "pg_dump otherdb")

    @pytest.mark.unit
    def test_approve_returns_id(self):
        """approve() returns a non-empty approval ID string."""
        store = SSHApprovalStore()
        approval_id = store.approve("session1", "mysqldump mydb")
        assert isinstance(approval_id, str)
        assert len(approval_id) > 0

    @pytest.mark.unit
    def test_clear_session(self):
        """clear_session removes all approvals for a session."""
        store = SSHApprovalStore()
        store.approve("session1", "mysqldump mydb")
        store.clear_session("session1")
        assert not store.is_approved("session1", "mysqldump mydb")
