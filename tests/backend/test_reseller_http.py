"""
HTTP integration tests for reseller API endpoints (core operations).

Covers QA playbook sections 2-5: self-service, user management,
suspension, API keys.
Uses FastAPI TestClient with in-memory SQLite — no Docker or Redis required.
"""
import uuid

import pytest


# =============================================================================
# Section 2 — Reseller Self-Service
# =============================================================================

class TestResellerSelfService:
    """Reseller owner can login, test connection, view profile and spending."""

    @pytest.mark.integration
    def test_login_as_reseller_owner(self, client, test_reseller):
        """Reseller owner can login with contact_email + password."""
        resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": test_reseller["contact_email"],
                "password": test_reseller["password"],
            },
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    @pytest.mark.integration
    def test_test_connection(self, client, reseller_auth_headers, test_reseller):
        """GET /reseller/test-connection → status=ok."""
        resp = client.get(
            "/api/v1/reseller/test-connection",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["reseller_id"] == test_reseller["id"]

    @pytest.mark.integration
    def test_get_profile(self, client, reseller_auth_headers, test_reseller):
        """GET /reseller/profile returns reseller details."""
        resp = client.get(
            "/api/v1/reseller/profile",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == test_reseller["id"]
        assert data["is_active"] is True
        assert "limits" in data

    @pytest.mark.integration
    def test_get_spending_status(self, client, reseller_auth_headers):
        """GET /reseller/spending returns limits and current amounts."""
        resp = client.get(
            "/api/v1/reseller/spending",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "limits" in data
        assert "current" in data
        assert data["status"] in ("ok", "warning", "exceeded")


# =============================================================================
# Section 3 — Reseller User Management
# =============================================================================

class TestResellerUserManagement:
    """Create, list, get, update, and duplicate-check users."""

    @pytest.mark.integration
    def test_create_user_201(self, client, reseller_auth_headers):
        """POST /reseller/users → 201 with expected fields."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": f"usr{unique}",
                "email": f"user_{unique}@test.com",
                "password": "UserPass1234",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_active"] is True
        assert data.get("quota") is not None

    @pytest.mark.integration
    def test_create_user_with_overrides(self, client, reseller_auth_headers):
        """POST /reseller/users with overrides applies them."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": f"ovr{unique}",
                "email": f"ovr_{unique}@test.com",
                "password": "Override1234",
                "quota_overrides": {
                    "max_concurrent_tasks": 3,
                    "max_daily_tasks": 100,
                },
                "feature_overrides": {"ssh_enabled": False},
                "metadata": {"department": "engineering"},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["quota"]["max_concurrent_tasks"] == 3
        assert data["quota"]["max_daily_tasks"] == 100

    @pytest.mark.integration
    def test_user_count_incremented(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Profile shows updated current_users after creating a user."""
        resp = client.get(
            "/api/v1/reseller/profile",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["limits"]["current_users"] >= 1

    @pytest.mark.integration
    def test_list_users(
        self, client, reseller_auth_headers, reseller_user
    ):
        """GET /reseller/users returns user list with pagination."""
        resp = client.get(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["users"]) >= 1
        assert data["pagination"]["total"] >= 1

    @pytest.mark.integration
    def test_list_users_with_search(
        self, client, reseller_auth_headers, reseller_user
    ):
        """GET /reseller/users?search= filters results."""
        username = reseller_user["username"]
        resp = client.get(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            params={"search": username},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pagination"]["total"] >= 1
        found = [u for u in data["users"] if u["username"] == username]
        assert len(found) == 1

    @pytest.mark.integration
    def test_get_single_user(
        self, client, reseller_auth_headers, reseller_user
    ):
        """GET /reseller/users/{id} returns the user."""
        resp = client.get(
            f"/api/v1/reseller/users/{reseller_user['id']}",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == reseller_user["id"]

    @pytest.mark.integration
    def test_update_user(
        self, client, reseller_auth_headers, reseller_user
    ):
        """PUT /reseller/users/{id} updates fields."""
        new_email = f"updated_{uuid.uuid4().hex[:6]}@test.com"
        resp = client.put(
            f"/api/v1/reseller/users/{reseller_user['id']}",
            headers=reseller_auth_headers,
            json={
                "email": new_email,
                "metadata": {"department": "security"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == new_email

    @pytest.mark.integration
    def test_duplicate_email_rejected_409(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Creating a user with an existing email → 409."""
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": f"dup{uuid.uuid4().hex[:8]}",
                "email": reseller_user["email"],
                "password": "DuplicatePass1",
            },
        )
        assert resp.status_code == 409


# =============================================================================
# Section 4 — User Suspension & Password
# =============================================================================

class TestUserSuspensionHTTP:
    """Suspend, double-suspend, unsuspend, and change password."""

    @pytest.mark.integration
    def test_suspend_user(
        self, client, reseller_auth_headers, reseller_user
    ):
        """POST /reseller/users/{id}/suspend → is_active=false."""
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    @pytest.mark.integration
    def test_double_suspend_409(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Suspending an already-suspended user → 409."""
        # First suspend
        client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=reseller_auth_headers,
        )
        # Second suspend
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 409

    @pytest.mark.integration
    def test_unsuspend_user(
        self, client, reseller_auth_headers, reseller_user
    ):
        """POST /reseller/users/{id}/unsuspend → is_active=true."""
        # Suspend first
        client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=reseller_auth_headers,
        )
        # Unsuspend
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/unsuspend",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True

    @pytest.mark.integration
    def test_change_password_revokes_tokens(
        self, client, reseller_auth_headers, reseller_user
    ):
        """POST /reseller/users/{id}/change-password → tokens_revoked=true."""
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/change-password",
            headers=reseller_auth_headers,
            json={"new_password": "NewPassword456"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "password_changed"
        assert data["tokens_revoked"] is True


# =============================================================================
# Section 5 — API Key Management
# =============================================================================

class TestAPIKeyHTTP:
    """Create, authenticate, scope-enforce, rotate, and revoke API keys."""

    @pytest.mark.integration
    def test_create_api_key_201(self, client, reseller_auth_headers):
        """POST /reseller/api-keys → 201, key starts with ag3_res_."""
        resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={
                "name": "CI/CD Key",
                "scopes": ["users:read", "users:create", "usage:read"],
                "rate_limit_per_minute": 30,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["key"].startswith("ag3_res_")
        assert data["scopes"] == ["users:read", "users:create", "usage:read"]
        assert data["is_active"] is True

    @pytest.mark.integration
    def test_create_api_key_with_ip_allowlist(
        self, client, reseller_auth_headers
    ):
        """API key with IP allowlist stores the IPs."""
        resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={
                "name": "Office Only",
                "scopes": ["users:read"],
                "ip_allowlist": ["10.0.0.1", "192.168.1.0"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert set(data["ip_allowlist"]) == {"10.0.0.1", "192.168.1.0"}

    @pytest.mark.integration
    def test_authenticate_with_api_key(
        self, client, reseller_auth_headers, reseller_user
    ):
        """API key with users:read scope can list users."""
        # Create key with users:read
        key_resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={"name": "ReadKey", "scopes": ["users:read"]},
        )
        assert key_resp.status_code == 201
        raw_key = key_resp.json()["key"]

        # Use the key to list users
        resp = client.get(
            "/api/v1/reseller/users",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_scope_enforcement_missing_scope_403(
        self, client, reseller_auth_headers
    ):
        """API key without users:create scope → 403 on user creation."""
        # Key with only users:read
        key_resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={"name": "ReadOnly", "scopes": ["users:read"]},
        )
        assert key_resp.status_code == 201
        raw_key = key_resp.json()["key"]

        # Try creating a user with the read-only key
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/reseller/users",
            headers={"X-API-Key": raw_key},
            json={
                "username": f"denied{unique}",
                "email": f"denied_{unique}@test.com",
                "password": "Denied12345",
            },
        )
        assert resp.status_code == 403
        assert "scope" in resp.json().get("detail", "").lower()

    @pytest.mark.integration
    def test_ip_allowlist_enforcement_403(
        self, client, reseller_auth_headers
    ):
        """API key with IP allowlist blocks requests from other IPs."""
        # Create key restricted to 10.0.0.1
        key_resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={
                "name": "IPRestricted",
                "scopes": ["users:read"],
                "ip_allowlist": ["10.0.0.1"],
            },
        )
        assert key_resp.status_code == 201
        raw_key = key_resp.json()["key"]

        # TestClient comes from localhost, not 10.0.0.1
        resp = client.get(
            "/api/v1/reseller/users",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 403
        assert "allowlist" in resp.json().get("detail", "").lower()

    @pytest.mark.integration
    def test_rotate_api_key(self, client, reseller_auth_headers):
        """POST /reseller/api-keys/{id}/rotate returns a new key."""
        # Create key
        key_resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={"name": "RotateMe", "scopes": ["users:read"]},
        )
        assert key_resp.status_code == 201
        original_key = key_resp.json()["key"]
        key_id = key_resp.json()["id"]

        # Rotate
        rotate_resp = client.post(
            f"/api/v1/reseller/api-keys/{key_id}/rotate",
            headers=reseller_auth_headers,
        )
        assert rotate_resp.status_code == 200
        new_key = rotate_resp.json()["key"]
        assert new_key != original_key
        assert new_key.startswith("ag3_res_")

    @pytest.mark.integration
    def test_revoked_key_rejected_401(self, client, reseller_auth_headers):
        """Revoked API key → 401."""
        # Create and revoke
        key_resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={"name": "RevokeMe", "scopes": ["users:read"]},
        )
        assert key_resp.status_code == 201
        raw_key = key_resp.json()["key"]
        key_id = key_resp.json()["id"]

        client.post(
            f"/api/v1/reseller/api-keys/{key_id}/revoke",
            headers=reseller_auth_headers,
        )

        resp = client.get(
            "/api/v1/reseller/users",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 401
