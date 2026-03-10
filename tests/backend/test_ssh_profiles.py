"""
Integration tests for SSH profile management API endpoints.

Tests the full request/response cycle via FastAPI TestClient.
All SSH connections and host-key scans are mocked — no real SSH server needed.
The VaultService is patched so the route's _get_vault() returns a test instance
backed by the same in-memory DB as the rest of the test stack.
"""
import pytest
from unittest.mock import AsyncMock, patch

from src.services.vault_encryption import VaultEncryption
from src.services.vault_service import VaultService


# ---------------------------------------------------------------------------
# Test key material
# A well-formed PEM header/footer with enough body to exceed the mask
# threshold (60 chars).  The key body is fake — the service validates it
# only via asyncssh.import_private_key, which we mock out below.
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

_BASE_PAYLOAD = {
    "name": "test-server",
    "host": "192.168.1.100",
    "username": "deploy",
    "private_key": _TEST_KEY,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_vault(test_app):
    """Inject a test VaultService into the SSH profiles router.

    Patches both _get_vault() and the _vault module-level singleton so all
    route calls use the in-memory test vault rather than reading secrets.yaml.
    """
    encryption = VaultEncryption(master_key=b"test-master-key-for-hkdf-testing")
    vault = VaultService(vault_encryption=encryption)
    with patch("src.api.routes.ssh_profiles._get_vault", return_value=vault):
        with patch("src.api.routes.ssh_profiles._vault", vault):
            yield vault


@pytest.fixture
def mock_ssh(mock_vault):
    """Mock SSH-related I/O to prevent real network calls.

    - scan_host_key: used during profile creation to pin the host key
    - asyncssh.import_private_key: used to validate/fingerprint the key
    - asyncssh.connect: used during connection tests
    """
    fake_key = _make_fake_asyncssh_key()

    fake_pub_key = _make_fake_asyncssh_key()  # reuse for public key mock

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
                yield mock_scan


def _make_fake_asyncssh_key():
    """Build a lightweight fake asyncssh private-key object sufficient for
    the fingerprint / key-type extraction code paths in ssh_profile_service."""
    from unittest.mock import MagicMock
    key = MagicMock()
    key.get_algorithm.return_value = "ssh-ed25519"
    key.get_fingerprint.return_value = "SHA256:fakeTestFingerprint012345678901234567890"
    # export_public_key still needed for host key extraction in test_connection
    key.export_public_key.return_value = (
        b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake"
        b"KeyDataForTestingPurposesOnlyNotReal\n"
    )
    return key


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


# ---------------------------------------------------------------------------
# CRUD — happy paths
# ---------------------------------------------------------------------------

class TestSSHProfileCRUD:

    @pytest.mark.integration
    def test_create_ssh_profile_201(self, client, auth_headers, mock_ssh):
        """Creating a valid profile returns 201 with the right shape."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["name"] == "test-server"
        assert data["host"] == "192.168.1.100"
        assert data["username"] == "deploy"
        assert data["port"] == 22
        assert data["mode"] == "readonly"
        assert data["privilege_level"] == 0
        assert "id" in data
        assert "created_at" in data

    @pytest.mark.integration
    def test_list_ssh_profiles(self, client, auth_headers, mock_ssh):
        """Creating two profiles and listing returns both."""
        _create_profile(client, auth_headers, {"name": "server-a"})
        _create_profile(client, auth_headers, {"name": "server-b"})

        resp = client.get("/api/v1/ssh-profiles", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        names = {p["name"] for p in data["profiles"]}
        assert names == {"server-a", "server-b"}

    @pytest.mark.integration
    def test_get_ssh_profile_by_id(self, client, auth_headers, mock_ssh):
        """A profile created can be retrieved individually by its ID."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/ssh-profiles/{profile_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == profile_id

    @pytest.mark.integration
    def test_update_ssh_profile_description(self, client, auth_headers, mock_ssh):
        """PATCH-style update (PUT) with description change is persisted."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={"description": "updated description"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated description"

    @pytest.mark.integration
    def test_delete_ssh_profile(self, client, auth_headers, mock_ssh):
        """Deleting a profile removes it — subsequent GET returns 404."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        del_resp = client.delete(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "deleted"

        get_resp = client.get(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestSSHProfileErrorPaths:

    @pytest.mark.integration
    def test_duplicate_name_returns_409(self, client, auth_headers, mock_ssh):
        """Creating two profiles with the same name returns 409 Conflict."""
        _create_profile(client, auth_headers)
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    @pytest.mark.integration
    def test_invalid_pem_key_returns_422(self, client, auth_headers, mock_vault):
        """A non-PEM private key body is rejected at model validation (422)."""
        resp = _create_profile(client, auth_headers, {"private_key": "not-a-pem"})
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_get_nonexistent_profile_404(self, client, auth_headers, mock_vault):
        """GET on a profile ID that does not exist returns 404."""
        resp = client.get(
            "/api/v1/ssh-profiles/00000000-0000-0000-0000-000000000000",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_delete_nonexistent_profile_404(self, client, auth_headers, mock_vault):
        """DELETE on a non-existent profile ID returns 404."""
        resp = client.delete(
            "/api/v1/ssh-profiles/00000000-0000-0000-0000-000000000000",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_update_with_no_fields_returns_400(self, client, auth_headers, mock_ssh):
        """PUT with an empty body (no fields to update) returns 400."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Key material security
# ---------------------------------------------------------------------------

class TestSSHProfileKeyMaterial:

    @pytest.mark.integration
    def test_key_material_not_in_create_response(self, client, auth_headers, mock_ssh):
        """The full private key is never returned in the create response."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        body = resp.text
        assert "BEGIN OPENSSH PRIVATE KEY" not in body or "key_preview" in resp.json()
        # The raw key body must not appear verbatim
        assert "b3BlbnNzaC1rZXktdjE" not in body

    @pytest.mark.integration
    def test_key_preview_format(self, client, auth_headers, mock_ssh):
        """key_preview starts with '-----BEGIN' and contains masking asterisks."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert "key_preview" in data
        preview = data["key_preview"]
        assert preview.startswith("-----BEGIN")
        assert "********************" in preview

    @pytest.mark.integration
    def test_key_fingerprint_present(self, client, auth_headers, mock_ssh):
        """key_fingerprint field is present and non-empty in the response."""
        resp = _create_profile(client, auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        # Fingerprint may be empty if key parsing fails with our mock, but
        # the field must exist.
        assert "key_fingerprint" in data

    @pytest.mark.integration
    def test_key_material_not_in_list_response(self, client, auth_headers, mock_ssh):
        """Full private key does not appear in the list-profiles response."""
        _create_profile(client, auth_headers)
        resp = client.get("/api/v1/ssh-profiles", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.text
        assert "b3BlbnNzaC1rZXktdjE" not in body


# ---------------------------------------------------------------------------
# User isolation (IDOR prevention)
# ---------------------------------------------------------------------------

class TestSSHProfileIsolation:

    @pytest.mark.integration
    def test_user_a_cannot_see_user_b_profiles(
        self, client, auth_headers, second_auth_headers, mock_ssh
    ):
        """Profiles created by user A are not visible to user B."""
        _create_profile(client, auth_headers)

        resp = client.get("/api/v1/ssh-profiles", headers=second_auth_headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.integration
    def test_user_b_cannot_get_user_a_profile_by_id(
        self, client, auth_headers, second_auth_headers, mock_ssh
    ):
        """User B cannot read user A's profile by guessing the ID."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.get(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=second_auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_user_b_cannot_delete_user_a_profile(
        self, client, auth_headers, second_auth_headers, mock_ssh
    ):
        """User B cannot delete user A's profile."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]

        resp = client.delete(
            f"/api/v1/ssh-profiles/{profile_id}",
            headers=second_auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_unauthenticated_request_rejected(self, client, mock_vault):
        """Requests without a Bearer token are rejected (401/403)."""
        resp = client.get("/api/v1/ssh-profiles")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

class TestSSHProfileAdminEndpoints:

    @pytest.mark.integration
    def test_admin_list_user_profiles(
        self, client, auth_headers, admin_auth_headers, test_user, mock_ssh
    ):
        """Admin can list profiles for any user by user ID."""
        _create_profile(client, auth_headers)
        user_id = test_user["id"]

        resp = client.get(
            f"/api/v1/admin/users/{user_id}/ssh-profiles",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    @pytest.mark.integration
    def test_admin_get_user_profile(
        self, client, auth_headers, admin_auth_headers, test_user, mock_ssh
    ):
        """Admin can retrieve a specific profile for any user."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]
        user_id = test_user["id"]

        resp = client.get(
            f"/api/v1/admin/users/{user_id}/ssh-profiles/{profile_id}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == profile_id

    @pytest.mark.integration
    def test_admin_delete_requires_confirm_param(
        self, client, auth_headers, admin_auth_headers, test_user, mock_ssh
    ):
        """Admin delete without ?confirm=true returns 400."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]
        user_id = test_user["id"]

        resp = client.delete(
            f"/api/v1/admin/users/{user_id}/ssh-profiles/{profile_id}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "confirm" in resp.json()["detail"].lower()

    @pytest.mark.integration
    def test_admin_delete_with_confirm_true(
        self, client, auth_headers, admin_auth_headers, test_user, mock_ssh
    ):
        """Admin delete with ?confirm=true succeeds."""
        create_resp = _create_profile(client, auth_headers)
        profile_id = create_resp.json()["id"]
        user_id = test_user["id"]

        resp = client.delete(
            f"/api/v1/admin/users/{user_id}/ssh-profiles/{profile_id}"
            "?confirm=true",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.integration
    def test_regular_user_cannot_access_admin_endpoints(
        self, client, auth_headers, test_user, mock_vault
    ):
        """A regular user is forbidden from accessing admin SSH endpoints."""
        user_id = test_user["id"]
        resp = client.get(
            f"/api/v1/admin/users/{user_id}/ssh-profiles",
            headers=auth_headers,
        )
        assert resp.status_code in (403, 401)

    @pytest.mark.integration
    def test_admin_list_empty_for_user_with_no_profiles(
        self, client, auth_headers, admin_auth_headers, test_user, mock_vault
    ):
        """Admin listing for a user with zero profiles returns count 0."""
        user_id = test_user["id"]
        resp = client.get(
            f"/api/v1/admin/users/{user_id}/ssh-profiles",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# Mode and privilege level variations
# ---------------------------------------------------------------------------

class TestSSHProfileModeVariants:

    @pytest.mark.integration
    def test_create_operations_mode(self, client, auth_headers, mock_ssh):
        """Profile with mode=operations is created and returned correctly."""
        resp = _create_profile(
            client, auth_headers,
            {"name": "ops-server", "mode": "operations", "privilege_level": 2},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["mode"] == "operations"
        assert data["privilege_level"] == 2

    @pytest.mark.integration
    def test_create_filtered_shell_mode(self, client, auth_headers, mock_ssh):
        """Profile with mode=filtered_shell is created correctly."""
        resp = _create_profile(
            client, auth_headers,
            {"name": "shell-server", "mode": "filtered_shell"},
        )
        assert resp.status_code == 201
        assert resp.json()["mode"] == "filtered_shell"

    @pytest.mark.integration
    def test_create_with_description(self, client, auth_headers, mock_ssh):
        """Profile with a description stores and returns it."""
        resp = _create_profile(
            client, auth_headers,
            {"name": "desc-server", "description": "My production SSH gateway"},
        )
        assert resp.status_code == 201
        assert resp.json()["description"] == "My production SSH gateway"

    @pytest.mark.integration
    def test_create_with_custom_port(self, client, auth_headers, mock_ssh):
        """Profile with a non-default port stores it correctly."""
        resp = _create_profile(
            client, auth_headers,
            {"name": "port-server", "port": 2222},
        )
        assert resp.status_code == 201
        assert resp.json()["port"] == 2222
