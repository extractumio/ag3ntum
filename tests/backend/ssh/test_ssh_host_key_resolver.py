"""
Tests for SSHHostKeyResolver — per-profile host key pinning via vault.

Uses in-memory SQLite via the ssh_db / test_user_with_jwt fixtures.
No containers required.
"""
import pytest
from unittest.mock import MagicMock, patch

from src.core.ssh.ssh_host_key_resolver import (
    HOST_KEY_SECRET_TYPE,
    SSHHostKeyResolver,
)
from src.core.ssh.ssh_config import SSHProfile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def host_key_resolver(vault_service):
    """SSHHostKeyResolver wired to the test VaultService."""
    return SSHHostKeyResolver(vault_service)


@pytest.fixture
def test_profile():
    """Basic SSH profile for resolver tests."""
    return SSHProfile(
        name="test-server",
        host="192.168.1.100",
        port=22,
        username="deploy",
        auth_method="key",
        key_ref="test-key",
    )


# A valid OpenSSH public key string (ed25519, test-only)
VALID_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl test@example"
)


async def _store_host_key(vault_service, ssh_db, user_id, profile_name, key_str=VALID_PUBLIC_KEY):
    """Helper: store a pinned host key in vault."""
    return await vault_service.store_secret(
        ssh_db,
        user_id=user_id,
        secret_type=HOST_KEY_SECRET_TYPE,
        name=profile_name,
        plaintext_value=key_str,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSSHHostKeyResolver:

    @pytest.mark.unit
    async def test_resolve_with_pinned_key_returns_callable(
        self, host_key_resolver, ssh_db, test_user_with_jwt, test_profile
    ):
        """When a valid pinned key exists, resolve returns a callable
        that yields the key in the trust list."""
        await _store_host_key(
            host_key_resolver._vault, ssh_db,
            test_user_with_jwt.id, test_profile.name,
        )

        mock_key = MagicMock()
        with patch("asyncssh.import_public_key", return_value=mock_key):
            result = await host_key_resolver.resolve(
                test_profile, test_user_with_jwt.id, "session-1", ssh_db
            )

        assert callable(result)
        trusted, ca, revoked = result("host", "addr", 22)
        assert len(trusted) == 1
        assert trusted[0] is mock_key
        assert ca == []
        assert revoked == []

    @pytest.mark.unit
    async def test_resolve_no_pinned_key_returns_reject_callable(
        self, host_key_resolver, ssh_db, test_user_with_jwt, test_profile
    ):
        """When no pinned key exists, resolve returns a reject callable
        (empty trust list)."""
        result = await host_key_resolver.resolve(
            test_profile, test_user_with_jwt.id, "session-1", ssh_db
        )

        assert callable(result)
        trusted, ca, revoked = result("host", "addr", 22)
        assert trusted == []
        assert ca == []
        assert revoked == []

    @pytest.mark.unit
    async def test_resolve_corrupt_key_returns_reject_callable(
        self, host_key_resolver, ssh_db, test_user_with_jwt, test_profile
    ):
        """When the pinned key fails to parse, resolve returns a reject
        callable (fail-closed)."""
        await _store_host_key(
            host_key_resolver._vault, ssh_db,
            test_user_with_jwt.id, test_profile.name,
            key_str="not-a-valid-public-key",
        )

        with patch(
            "asyncssh.import_public_key",
            side_effect=ValueError("bad key"),
        ):
            result = await host_key_resolver.resolve(
                test_profile, test_user_with_jwt.id, "session-1", ssh_db
            )

        assert callable(result)
        trusted, ca, revoked = result("host", "addr", 22)
        assert trusted == []

    @pytest.mark.unit
    async def test_resolve_inactive_key_is_ignored(
        self, host_key_resolver, ssh_db, test_user_with_jwt, test_profile
    ):
        """A soft-deleted (is_active=False) key is treated as missing."""
        secret = await _store_host_key(
            host_key_resolver._vault, ssh_db,
            test_user_with_jwt.id, test_profile.name,
        )
        # Soft-delete
        await host_key_resolver._vault.delete_secret(
            ssh_db, test_user_with_jwt.id, secret.id, "test cleanup"
        )

        result = await host_key_resolver.resolve(
            test_profile, test_user_with_jwt.id, "session-1", ssh_db
        )

        trusted, ca, revoked = result("host", "addr", 22)
        assert trusted == []

    @pytest.mark.unit
    async def test_resolve_cross_user_isolation(
        self, host_key_resolver, ssh_db, test_user_with_jwt, test_profile
    ):
        """User A's pinned key is not visible to user B."""
        await _store_host_key(
            host_key_resolver._vault, ssh_db,
            test_user_with_jwt.id, test_profile.name,
        )

        # Different user should get reject callable
        result = await host_key_resolver.resolve(
            test_profile, "other-user-id", "session-1", ssh_db
        )

        trusted, ca, revoked = result("host", "addr", 22)
        assert trusted == []

    @pytest.mark.unit
    async def test_callable_returns_3_tuple(
        self, host_key_resolver, ssh_db, test_user_with_jwt, test_profile
    ):
        """Both accept and reject callables return exactly a 3-tuple."""
        # Reject (no key)
        reject = await host_key_resolver.resolve(
            test_profile, test_user_with_jwt.id, "session-1", ssh_db
        )
        result = reject("host", "addr", 22)
        assert isinstance(result, tuple)
        assert len(result) == 3

        # Accept (with key)
        await _store_host_key(
            host_key_resolver._vault, ssh_db,
            test_user_with_jwt.id, test_profile.name,
        )

        with patch("asyncssh.import_public_key", return_value=MagicMock()):
            accept = await host_key_resolver.resolve(
                test_profile, test_user_with_jwt.id, "session-2", ssh_db
            )

        result = accept("host", "addr", 22)
        assert isinstance(result, tuple)
        assert len(result) == 3

    @pytest.mark.unit
    async def test_resolve_called_under_active_db_session(
        self, host_key_resolver, ssh_db, test_user_with_jwt, test_profile
    ):
        """Verify resolve works under the caller's active db session
        (eager loading pattern — no deferred queries)."""
        await _store_host_key(
            host_key_resolver._vault, ssh_db,
            test_user_with_jwt.id, test_profile.name,
        )

        with patch("asyncssh.import_public_key", return_value=MagicMock()):
            # This should complete without session errors
            result = await host_key_resolver.resolve(
                test_profile, test_user_with_jwt.id, "session-1", ssh_db
            )

        assert callable(result)
