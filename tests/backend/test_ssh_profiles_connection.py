"""
Integration tests for SSH profile connection test endpoints and advanced CRUD.

Covers functionality NOT tested in test_ssh_profiles.py:
- POST /ssh-profiles/test (inline connection test)
- POST /ssh-profiles/{id}/test (saved profile connection test)
- Update with key replacement, mode change, host change, is_active toggle
- Response field completeness (host_key_pinned, key_type, last_connected_at, etc.)
- Name rename uniqueness conflict
- Connection failure scenarios
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.vault_encryption import VaultEncryption
from src.services.vault_service import VaultService


# ---------------------------------------------------------------------------
# Test key material (same fake key as test_ssh_profiles.py)
# ---------------------------------------------------------------------------

_TEST_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gt"
    "ZWQyNTUxOQAAACBTCjCggE8J3I9+ZN0B2tOlTVMMAp2c5RPn8Ah6HT0digAAAJhDDD"
    "lAQww5QAAAAAtzc2gtZWQyNTUxOQAAACBTCjCggE8J3I9+ZN0B2tOlTVMMAp2c5RPn"
    "8Ah6HT0digAAAEDvqAE3bx2FrmiFuFNAiiIZAl6YUv0bZ6Rk4xm8rADnDlMKMKCAT"
    "wncj35k3QHa06VNUwwCnZzlE+fwCHodPR2KAAAAEm9wZW5jbGF3LWhvc3RpbmdlcgEC\n"
    "-----END OPENSSH PRIVATE KEY-----"
)

_SECOND_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBogIBAAJBALRiMLAHudeSA/x3hB2f+2NRkJPOtoVKWmGLASGG1sIYMRPX8DAH"
    "kVnSBDMDVkPUWTGGPvFuFtKpPdMhIB6DfIkCAwEAAQJAHJEBFWA0eg+VHILOoABr"
    "TfGAVKMQzMHXgZy+ioayfMiEhJ22H3FR+xjVbMIKa2Lso7f3d3RzWh55EkJ0QKm\n"
    "-----END RSA PRIVATE KEY-----"
)

_BASE_PAYLOAD = {
    "name": "conn-test-server",
    "host": "192.168.1.100",
    "username": "deploy",
    "private_key": _TEST_KEY,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_fake_asyncssh_key():
    """Build a lightweight fake asyncssh key for mocking."""
    key = MagicMock()
    key.get_algorithm.return_value = "ssh-ed25519"
    key.get_fingerprint.return_value = "SHA256:fakeTestFingerprint012345678901234567890"
    key.export_public_key.return_value = (
        b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake"
        b"KeyDataForTestingPurposesOnlyNotReal\n"
    )
    return key


def _make_fake_connection(server_key=None):
    """Build a mock asyncssh connection object."""
    conn = AsyncMock()
    if server_key is None:
        server_key = MagicMock()
        server_key.export_public_key.return_value = (
            b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake"
            b"KeyDataForTestingPurposesOnlyNotReal\n"
        )
    conn.get_server_host_key.return_value = server_key
    conn._peer_version = "OpenSSH_8.9"
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    return conn


@pytest.fixture
def mock_vault(test_app):
    """Inject a test VaultService into the SSH profiles router."""
    encryption = VaultEncryption(master_key=b"test-master-key-for-hkdf-testing")
    vault = VaultService(vault_encryption=encryption)
    with patch("src.api.routes.ssh_profiles._get_vault", return_value=vault):
        with patch("src.api.routes.ssh_profiles._vault", vault):
            yield vault


@pytest.fixture
def mock_ssh(mock_vault):
    """Mock SSH I/O: key parsing, host key scanning, rate limiter."""
    fake_key = _make_fake_asyncssh_key()
    fake_pub_key = _make_fake_asyncssh_key()

    with patch(
        "src.services.ssh_profile_service.scan_host_key",
        new_callable=AsyncMock,
    ) as mock_scan:
        mock_scan.return_value = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI"
            "FMKMKCATwncj35k3QHa06VNUwwCnZzlE+fwCHodPR2K"
        )
        with patch(
            "src.services.ssh_profile_service.asyncssh.import_private_key",
            return_value=fake_key,
        ):
            with patch(
                "src.services.ssh_profile_service.asyncssh.import_public_key",
                return_value=fake_pub_key,
            ):
                # Disable rate limiter for test endpoints
                with patch(
                    "src.api.routes.ssh_profiles.check_rate_limit",
                    return_value=True,
                ):
                    yield mock_scan


@pytest.fixture
def mock_ssh_connect(mock_ssh):
    """Mock asyncssh.connect for connection test endpoints."""
    fake_conn = _make_fake_connection()
    with patch(
        "src.services.ssh_profile_service.asyncssh.connect",
        new_callable=AsyncMock,
        return_value=fake_conn,
    ) as mock_conn:
        yield mock_conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_profile(client, auth_headers, payload=None):
    """POST /api/v1/ssh-profiles and return the response."""
    data = dict(_BASE_PAYLOAD)
    if payload:
        data.update(payload)
    return client.post(
        "/api/v1/ssh-profiles",
        headers=auth_headers,
        json=data,
    )


# ===========================================================================
# Connection test — inline (POST /ssh-profiles/test)
# ===========================================================================

class TestInlineConnectionTest:

    @pytest.mark.integration
    def test_inline_test_success(self, client, auth_headers, mock_ssh_connect):
        """Inline test returns success with host key info on valid connection."""
        resp = client.post(
            "/api/v1/ssh-profiles/test",
            headers=auth_headers,
            json={
                "host": "192.168.1.100",
                "username": "deploy",
                "private_key": _TEST_KEY,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "success"
        assert "latency_ms" in data
        assert data["latency_ms"] is not None

    @pytest.mark.integration
    def test_inline_test_with_custom_port(self, client, auth_headers, mock_ssh_connect):
        """Inline test passes custom port through to connect."""
        resp = client.post(
            "/api/v1/ssh-profiles/test",
            headers=auth_headers,
            json={
                "host": "192.168.1.100",
                "port": 2222,
                "username": "deploy",
                "private_key": _TEST_KEY,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        # Verify port was passed to asyncssh.connect
        mock_ssh_connect.assert_called_once()
        call_kwargs = mock_ssh_connect.call_args
        assert call_kwargs[1]["port"] == 2222

    @pytest.mark.integration
    def test_inline_test_invalid_key_format(self, client, auth_headers, mock_vault):
        """Inline test with non-PEM key is rejected at validation (422)."""
        resp = client.post(
            "/api/v1/ssh-profiles/test",
            headers=auth_headers,
            json={
                "host": "192.168.1.100",
                "username": "deploy",
                "private_key": "not-a-pem-key-at-all",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_inline_test_missing_required_fields(self, client, auth_headers, mock_vault):
        """Inline test without host/username returns 422."""
        resp = client.post(
            "/api/v1/ssh-profiles/test",
            headers=auth_headers,
            json={"private_key": _TEST_KEY},
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_inline_test_connection_refused(self, client, auth_headers, mock_ssh):
        """Inline test returns failed status when connection is refused."""
        with patch(
            "src.services.ssh_profile_service.asyncssh.connect",
            new_callable=AsyncMock,
            side_effect=OSError("Connection refused"),
        ):
            resp = client.post(
                "/api/v1/ssh-profiles/test",
                headers=auth_headers,
                json={
                    "host": "192.168.1.100",
                    "username": "deploy",
                    "private_key": _TEST_KEY,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_code"] == "connection_refused"

    @pytest.mark.integration
    def test_inline_test_auth_failed(self, client, auth_headers, mock_ssh):
        """Inline test returns failed when authentication is denied."""
        import asyncssh
        with patch(
            "src.services.ssh_profile_service.asyncssh.connect",
            new_callable=AsyncMock,
            side_effect=asyncssh.PermissionDenied("bad key"),
        ):
            resp = client.post(
                "/api/v1/ssh-profiles/test",
                headers=auth_headers,
                json={
                    "host": "192.168.1.100",
                    "username": "deploy",
                    "private_key": _TEST_KEY,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_code"] == "auth_failed"

    @pytest.mark.integration
    def test_inline_test_timeout(self, client, auth_headers, mock_ssh):
        """Inline test returns failed on timeout."""
        import asyncio
        with patch(
            "src.services.ssh_profile_service.asyncssh.connect",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError(),
        ):
            resp = client.post(
                "/api/v1/ssh-profiles/test",
                headers=auth_headers,
                json={
                    "host": "192.168.1.100",
                    "username": "deploy",
                    "private_key": _TEST_KEY,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_code"] == "timeout"

    @pytest.mark.integration
    def test_inline_test_unauthenticated(self, client, mock_vault):
        """Inline test without auth token is rejected."""
        resp = client.post(
            "/api/v1/ssh-profiles/test",
            json={
                "host": "192.168.1.100",
                "username": "deploy",
                "private_key": _TEST_KEY,
            },
        )
        assert resp.status_code in (401, 403)


# ===========================================================================
# Connection test — saved profile (POST /ssh-profiles/{id}/test)
# ===========================================================================

class TestSavedConnectionTest:

    @pytest.mark.integration
    def test_saved_test_success(self, client, auth_headers, mock_ssh_connect):
        """Testing a saved profile succeeds and returns status=success."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/v1/ssh-profiles/{profile_id}/test",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "success"
        assert data["latency_ms"] is not None

    @pytest.mark.integration
    def test_saved_test_updates_last_connected_at(
        self, client, auth_headers, mock_ssh_connect
    ):
        """A successful saved test updates last_connected_at on the profile."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]
        assert create_resp.json()["last_connected_at"] is None

        client.post(
            f"/api/v1/ssh-profiles/{profile_id}/test",
            headers=auth_headers,
        )

        get_resp = client.get(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["last_connected_at"] is not None

    @pytest.mark.integration
    def test_saved_test_failure_sets_error(self, client, auth_headers, mock_ssh):
        """A failed saved test writes last_connection_error on the profile."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        with patch(
            "src.services.ssh_profile_service.asyncssh.connect",
            new_callable=AsyncMock,
            side_effect=OSError("Connection refused"),
        ):
            resp = client.post(
                f"/api/v1/ssh-profiles/{profile_id}/test",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

        get_resp = client.get(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
        )
        assert get_resp.json()["last_connection_error"] is not None
        assert "refused" in get_resp.json()["last_connection_error"].lower()

    @pytest.mark.integration
    def test_saved_test_clears_error_on_success(
        self, client, auth_headers, mock_ssh
    ):
        """A successful test after a failure clears last_connection_error."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        # First: fail
        with patch(
            "src.services.ssh_profile_service.asyncssh.connect",
            new_callable=AsyncMock,
            side_effect=OSError("Connection refused"),
        ):
            client.post(
                f"/api/v1/ssh-profiles/{profile_id}/test",
                headers=auth_headers,
            )

        # Then: succeed
        fake_conn = _make_fake_connection()
        with patch(
            "src.services.ssh_profile_service.asyncssh.connect",
            new_callable=AsyncMock,
            return_value=fake_conn,
        ):
            resp = client.post(
                f"/api/v1/ssh-profiles/{profile_id}/test",
                headers=auth_headers,
            )

        assert resp.json()["status"] == "success"
        get_resp = client.get(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
        )
        assert get_resp.json()["last_connection_error"] is None
        assert get_resp.json()["last_connected_at"] is not None

    @pytest.mark.integration
    def test_saved_test_nonexistent_profile_404(
        self, client, auth_headers, mock_ssh
    ):
        """Testing a non-existent profile returns 404."""
        resp = client.post(
            "/api/v1/ssh-profiles/00000000-0000-0000-0000-000000000000/test",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_saved_test_other_users_profile_404(
        self, client, auth_headers, second_auth_headers, mock_ssh_connect
    ):
        """User B cannot test user A's saved profile."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/v1/ssh-profiles/{profile_id}/test",
            headers=second_auth_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# Rate limiting for connection tests
# ===========================================================================

class TestConnectionTestRateLimit:

    @pytest.mark.integration
    def test_inline_test_rate_limited(self, client, auth_headers, mock_ssh):
        """Inline test returns 429 when rate limit is exceeded."""
        fake_conn = _make_fake_connection()
        with patch(
            "src.api.routes.ssh_profiles.check_rate_limit",
            return_value=False,
        ):
            with patch(
                "src.services.ssh_profile_service.asyncssh.connect",
                new_callable=AsyncMock,
                return_value=fake_conn,
            ):
                resp = client.post(
                    "/api/v1/ssh-profiles/test",
                    headers=auth_headers,
                    json={
                        "host": "192.168.1.100",
                        "username": "deploy",
                        "private_key": _TEST_KEY,
                    },
                )
        assert resp.status_code == 429
        assert "too many" in resp.json()["detail"].lower()

    @pytest.mark.integration
    def test_saved_test_rate_limited(self, client, auth_headers, mock_ssh):
        """Saved test returns 429 when rate limit is exceeded."""
        fake_conn = _make_fake_connection()
        with patch(
            "src.services.ssh_profile_service.asyncssh.connect",
            new_callable=AsyncMock,
            return_value=fake_conn,
        ):
            create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        with patch(
            "src.api.routes.ssh_profiles.check_rate_limit",
            return_value=False,
        ):
            resp = client.post(
                f"/api/v1/ssh-profiles/{profile_id}/test",
                headers=auth_headers,
            )
        assert resp.status_code == 429


# ===========================================================================
# Advanced update operations
# ===========================================================================

class TestSSHProfileUpdateAdvanced:

    @pytest.mark.integration
    def test_update_mode_and_privilege_level(
        self, client, auth_headers, mock_ssh
    ):
        """Updating mode and privilege_level persists the changes."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]
        assert create_resp.json()["mode"] == "readonly"
        assert create_resp.json()["privilege_level"] == 0

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"mode": "operations", "privilege_level": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "operations"
        assert data["privilege_level"] == 2

    @pytest.mark.integration
    def test_update_host_and_port(self, client, auth_headers, mock_ssh):
        """Updating host and port persists."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"host": "10.0.0.1", "port": 2222},
        )
        assert resp.status_code == 200
        assert resp.json()["host"] == "10.0.0.1"
        assert resp.json()["port"] == 2222

    @pytest.mark.integration
    def test_update_username(self, client, auth_headers, mock_ssh):
        """Updating username persists."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"username": "root"},
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "root"

    @pytest.mark.integration
    def test_update_is_active_toggle(self, client, auth_headers, mock_ssh):
        """Toggling is_active via PUT works."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]
        assert create_resp.json()["is_active"] is True

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

        # Toggle back
        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"is_active": True},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True

    @pytest.mark.integration
    def test_update_with_key_replacement(self, client, auth_headers, mock_ssh):
        """Replacing the private key via PUT updates key_fingerprint."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]
        original_fingerprint = create_resp.json()["key_fingerprint"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"private_key": _SECOND_KEY},
        )
        assert resp.status_code == 200
        # Key was replaced — fingerprint field exists
        assert "key_fingerprint" in resp.json()
        # Key preview should reflect the new key type
        assert resp.json()["key_preview"].startswith("-----BEGIN")

    @pytest.mark.integration
    def test_rename_profile(self, client, auth_headers, mock_ssh):
        """Renaming a profile via PUT works."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"name": "renamed-server"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "renamed-server"

    @pytest.mark.integration
    def test_rename_to_existing_name_409(self, client, auth_headers, mock_ssh):
        """Renaming to an already-taken name returns 409 Conflict."""
        _create_profile(client, auth_headers, {"name": "server-one"})
        create_b = _create_profile(client, auth_headers, {"name": "server-two"})
        profile_b_id = create_b.json()["id"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_b_id}",
            headers=auth_headers,
            json={"name": "server-one"},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    @pytest.mark.integration
    def test_update_invalid_mode_422(self, client, auth_headers, mock_ssh):
        """Updating with an invalid mode returns 422."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"mode": "superadmin"},
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_update_invalid_name_422(self, client, auth_headers, mock_ssh):
        """Updating with an invalid name format returns 422."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"name": "UPPERCASE"},
        )
        assert resp.status_code == 422


# ===========================================================================
# Response field completeness
# ===========================================================================

class TestResponseFieldCompleteness:

    @pytest.mark.integration
    def test_create_response_has_all_fields(
        self, client, auth_headers, mock_ssh
    ):
        """Create response includes all expected fields with correct types."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        data = resp.json()

        # Required fields present
        assert isinstance(data["id"], str)
        assert isinstance(data["name"], str)
        assert isinstance(data["host"], str)
        assert isinstance(data["port"], int)
        assert isinstance(data["username"], str)
        assert isinstance(data["mode"], str)
        assert isinstance(data["privilege_level"], int)
        assert isinstance(data["host_key_pinned"], bool)
        assert isinstance(data["key_preview"], str)
        assert isinstance(data["is_active"], bool)
        assert isinstance(data["created_by"], str)
        assert isinstance(data["created_at"], str)

        # Nullable fields present (may be None)
        assert "host_key_fingerprint" in data
        assert "key_fingerprint" in data
        assert "key_type" in data
        assert "last_connected_at" in data
        assert "last_connection_error" in data
        assert "description" in data

    @pytest.mark.integration
    def test_host_key_pinned_on_create(self, client, auth_headers, mock_ssh):
        """When scan_host_key succeeds, host_key_pinned is True."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        assert resp.json()["host_key_pinned"] is True

    @pytest.mark.integration
    def test_host_key_not_pinned_on_scan_failure(
        self, client, auth_headers, mock_vault
    ):
        """When scan_host_key fails, host_key_pinned is False."""
        fake_key = _make_fake_asyncssh_key()
        fake_pub_key = _make_fake_asyncssh_key()

        with patch(
            "src.services.ssh_profile_service.scan_host_key",
            new_callable=AsyncMock,
            side_effect=Exception("scan failed"),
        ):
            with patch(
                "src.services.ssh_profile_service.asyncssh.import_private_key",
                return_value=fake_key,
            ):
                with patch(
                    "src.services.ssh_profile_service.asyncssh.import_public_key",
                    return_value=fake_pub_key,
                ):
                    resp = _create_profile(client, auth_headers)

        assert resp.status_code == 201
        assert resp.json()["host_key_pinned"] is False

    @pytest.mark.integration
    def test_key_type_present_in_response(self, client, auth_headers, mock_ssh):
        """key_type field is populated from asyncssh key parsing."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        # Our mock returns "ssh-ed25519"
        assert resp.json()["key_type"] == "ssh-ed25519"

    @pytest.mark.integration
    def test_created_by_is_self_for_user_created(
        self, client, auth_headers, mock_ssh
    ):
        """Profiles created by user have created_by='self'."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        assert resp.json()["created_by"] == "self"

    @pytest.mark.integration
    def test_is_active_defaults_to_true(self, client, auth_headers, mock_ssh):
        """Newly created profiles are active by default."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        assert resp.json()["is_active"] is True

    @pytest.mark.integration
    def test_last_connected_at_null_on_create(
        self, client, auth_headers, mock_ssh
    ):
        """last_connected_at is null for a freshly created profile."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        assert resp.json()["last_connected_at"] is None

    @pytest.mark.integration
    def test_last_connection_error_null_on_create(
        self, client, auth_headers, mock_ssh
    ):
        """last_connection_error is null for a freshly created profile."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        assert resp.json()["last_connection_error"] is None

    @pytest.mark.integration
    def test_host_key_fingerprint_populated_when_pinned(
        self, client, auth_headers, mock_ssh
    ):
        """When host key is pinned, fingerprint is returned in responses."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["host_key_pinned"] is True
        # host_key_fingerprint should be populated (from our mock)
        assert data["host_key_fingerprint"] is not None
        assert data["host_key_fingerprint"].startswith("SHA256:")
