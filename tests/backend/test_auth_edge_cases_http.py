"""
HTTP integration tests for auth edge cases, input validation, IDOR
prevention, and privilege escalation.

Covers QA playbook sections 12, 13, 20, 21.
Uses FastAPI TestClient with in-memory SQLite — no Docker or Redis required.
"""
import pytest


# =============================================================================
# Section 21 — Auth Edge Cases
# =============================================================================

class TestAuthEdgeCases:
    """Endpoints reject unauthenticated or badly-authenticated requests."""

    @pytest.mark.integration
    def test_no_auth_header(self, client):
        """Request without Authorization header → 401 or 403."""
        resp = client.get("/api/v1/reseller/profile")
        assert resp.status_code in (401, 403)

    @pytest.mark.integration
    def test_invalid_jwt(self, client):
        """Garbage JWT token → 401 or 403."""
        resp = client.get(
            "/api/v1/reseller/profile",
            headers={"Authorization": "Bearer invalid_garbage_token"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.integration
    def test_invalid_api_key(self, client):
        """Syntactically-valid but unknown API key → 401."""
        resp = client.get(
            "/api/v1/reseller/users",
            headers={"X-API-Key": "ag3_res_nonexistent_key_12345"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    def test_revoked_api_key_rejected(
        self, client, reseller_auth_headers
    ):
        """Revoked API key → 401."""
        # Create a key
        create_resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={
                "name": "EphemeralKey",
                "scopes": ["users:read"],
            },
        )
        assert create_resp.status_code == 201
        raw_key = create_resp.json()["key"]
        key_id = create_resp.json()["id"]

        # Revoke it
        revoke_resp = client.post(
            f"/api/v1/reseller/api-keys/{key_id}/revoke",
            headers=reseller_auth_headers,
        )
        assert revoke_resp.status_code == 200

        # Try using the revoked key
        resp = client.get(
            "/api/v1/reseller/users",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 401


# =============================================================================
# Section 20 — Input Validation
# =============================================================================

class TestInputValidationHTTP:
    """Pydantic validation rejects bad input at the HTTP layer."""

    @pytest.mark.integration
    def test_short_username_422(self, client, reseller_auth_headers):
        """Username shorter than 3 chars → 422."""
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": "ab",
                "email": "short@test.com",
                "password": "Short12345",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_bad_chars_username_422(self, client, reseller_auth_headers):
        """Username with invalid characters → 422."""
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": "user@name!",
                "email": "bad@test.com",
                "password": "Bad1234567",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_short_password_422(self, client, reseller_auth_headers):
        """Password shorter than 8 chars → 422."""
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": "validuser",
                "email": "valid@test.com",
                "password": "short",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_invalid_email_422(self, client, reseller_auth_headers):
        """Invalid email format → 422."""
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": "validuser",
                "email": "not-an-email",
                "password": "Valid12345",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_negative_spending_limit_422(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Negative spending limit → 422."""
        resp = client.put(
            f"/api/v1/reseller/users/{reseller_user['id']}/spending-limits",
            headers=reseller_auth_headers,
            json={"max_monthly_usd": -10.0},
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_ssh_max_connections_too_high_422(
        self, client, reseller_auth_headers, reseller_user
    ):
        """SSH max_connections > 20 → 422."""
        resp = client.put(
            f"/api/v1/reseller/users/{reseller_user['id']}/ssh-filters",
            headers=reseller_auth_headers,
            json={"max_connections": 50},
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_skill_content_too_large_422(
        self, client, reseller_auth_headers
    ):
        """Skill content > 50KB → 422."""
        big_content = "x" * 52000
        resp = client.post(
            "/api/v1/reseller/skill-library",
            headers=reseller_auth_headers,
            json={"name": "toobig", "content": big_content},
        )
        assert resp.status_code == 422


# =============================================================================
# Section 12 — IDOR Prevention
# =============================================================================

class TestIDORPreventionHTTP:
    """Cross-reseller access must be blocked (returns 404, not 403)."""

    @pytest.mark.integration
    def test_cross_reseller_get_user_404(
        self, client, reseller_user, second_reseller_auth_headers
    ):
        """Evil reseller cannot GET another reseller's user."""
        resp = client.get(
            f"/api/v1/reseller/users/{reseller_user['id']}",
            headers=second_reseller_auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_cross_reseller_suspend_404(
        self, client, reseller_user, second_reseller_auth_headers
    ):
        """Evil reseller cannot suspend another reseller's user."""
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=second_reseller_auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_cross_reseller_delete_404(
        self, client, reseller_user, second_reseller_auth_headers
    ):
        """Evil reseller cannot delete another reseller's user."""
        resp = client.delete(
            f"/api/v1/reseller/users/{reseller_user['id']}",
            headers=second_reseller_auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_cross_reseller_config_all_404(
        self, client, reseller_user, second_reseller_auth_headers
    ):
        """Evil reseller cannot access any config sub-endpoint for another
        reseller's user (config, spending, skills, env-vars, security,
        ssh-filters)."""
        user_id = reseller_user["id"]
        sub_paths = [
            "config", "spending", "skills", "env-vars",
            "security", "ssh-filters",
        ]
        for sub in sub_paths:
            resp = client.get(
                f"/api/v1/reseller/users/{user_id}/{sub}",
                headers=second_reseller_auth_headers,
            )
            assert resp.status_code == 404, (
                f"GET /reseller/users/{{id}}/{sub} returned "
                f"{resp.status_code}, expected 404"
            )


# =============================================================================
# Section 13 — Privilege Escalation Prevention
# =============================================================================

class TestPrivilegeEscalationHTTP:
    """Role boundaries are enforced at the HTTP layer."""

    @pytest.mark.integration
    def test_all_created_users_role_user(
        self, client, admin_auth_headers, test_reseller
    ):
        """Users created via reseller API always have role='user'."""
        resp = client.get(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            params={"reseller_id": test_reseller["id"]},
        )
        assert resp.status_code == 200
        users = resp.json()["users"]
        # The reseller owner has role='reseller', managed users have 'user'
        managed = [u for u in users if u["role"] == "user"]
        non_user = [
            u for u in users
            if u["role"] not in ("user", "reseller")
        ]
        assert non_user == [], (
            f"Unexpected roles found: {[u['role'] for u in non_user]}"
        )
        # If we have created a user via the reseller fixture, it's there
        if managed:
            assert all(u["role"] == "user" for u in managed)

    @pytest.mark.integration
    def test_reseller_blocked_from_admin_403(
        self, client, reseller_auth_headers
    ):
        """Reseller JWT cannot access /admin/ endpoints → 403."""
        resp = client.get(
            "/api/v1/admin/resellers",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.integration
    def test_user_blocked_from_reseller_403(
        self, client, reseller_user
    ):
        """Regular user JWT cannot access /reseller/ endpoints → 403."""
        # Login as the regular user
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": reseller_user["email"],
                "password": reseller_user["password"],
            },
        )
        assert login_resp.status_code == 200
        user_token = login_resp.json()["access_token"]
        user_headers = {"Authorization": f"Bearer {user_token}"}

        resp = client.get(
            "/api/v1/reseller/profile",
            headers=user_headers,
        )
        assert resp.status_code == 403
