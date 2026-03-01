"""
HTTP integration tests for admin API endpoints.

Covers QA playbook sections 1, 15, 16, 17, 19.
Uses FastAPI TestClient with in-memory SQLite — no Docker or Redis required.
"""
import uuid

import pytest


# =============================================================================
# Section 1 — Admin Reseller Lifecycle
# =============================================================================

class TestAdminResellerLifecycle:
    """Full CRUD for resellers via admin endpoints."""

    @pytest.mark.integration
    def test_create_reseller_201(self, client, admin_auth_headers):
        """POST /admin/resellers → 201 with expected fields."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
            json={
                "name": f"Acme {unique}",
                "company": "Acme Inc.",
                "contact_email": f"admin_{unique}@acme.test",
                "password": "AcmePass123",
                "max_users": 5,
                "max_monthly_spending_usd": 100.0,
                "max_daily_spending_usd": 20.0,
                "spending_alert_threshold_pct": 80,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["is_active"] is True
        assert data["owner_user_id"] is not None
        assert data["owner_username"] is not None
        assert data["limits"]["max_users"] == 5

    @pytest.mark.integration
    def test_list_resellers(
        self, client, admin_auth_headers, test_reseller
    ):
        """GET /admin/resellers contains the created reseller."""
        resp = client.get(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "resellers" in data
        assert data["pagination"]["total"] >= 1
        ids = [r["id"] for r in data["resellers"]]
        assert test_reseller["id"] in ids

    @pytest.mark.integration
    def test_get_reseller_details_with_stats(
        self, client, admin_auth_headers, test_reseller
    ):
        """GET /admin/resellers/{id} includes stats section."""
        resp = client.get(
            f"/api/v1/admin/resellers/{test_reseller['id']}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == test_reseller["id"]
        assert "stats" in data
        assert data["stats"]["user_count"] >= 0

    @pytest.mark.integration
    def test_update_reseller(
        self, client, admin_auth_headers, test_reseller
    ):
        """PUT /admin/resellers/{id} updates fields."""
        resp = client.put(
            f"/api/v1/admin/resellers/{test_reseller['id']}",
            headers=admin_auth_headers,
            json={"max_users": 20, "notes": "Premium tier"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["limits"]["max_users"] == 20
        assert data["notes"] == "Premium tier"

    @pytest.mark.integration
    def test_get_reseller_config_tree(
        self, client, admin_auth_headers, test_reseller
    ):
        """GET /admin/resellers/{id}/config returns config tree."""
        resp = client.get(
            f"/api/v1/admin/resellers/{test_reseller['id']}/config",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["reseller_id"] == test_reseller["id"]
        assert "defaults" in data
        assert "users" in data
        assert isinstance(data["users"], list)


# =============================================================================
# Section 15 — Reseller Suspension
# =============================================================================

class TestResellerSuspensionHTTP:
    """Admin can suspend/unsuspend resellers; auth is blocked while suspended."""

    @pytest.mark.integration
    def test_suspend_reseller(
        self, client, admin_auth_headers, test_reseller
    ):
        """POST /admin/resellers/{id}/suspend → 200, is_active=false."""
        resp = client.post(
            f"/api/v1/admin/resellers/{test_reseller['id']}/suspend",
            headers=admin_auth_headers,
            json={"reason": "Payment overdue"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is False

    @pytest.mark.integration
    def test_suspended_reseller_jwt_fails(
        self, client, admin_auth_headers, test_reseller
    ):
        """Suspended reseller's owner JWT login fails."""
        # Suspend first
        client.post(
            f"/api/v1/admin/resellers/{test_reseller['id']}/suspend",
            headers=admin_auth_headers,
        )
        # Try logging in as the reseller owner
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": test_reseller["contact_email"],
                "password": test_reseller["password"],
            },
        )
        # Owner is now inactive — login may succeed but profile will fail,
        # OR login itself may fail. Either way, reseller ops are blocked.
        if login_resp.status_code == 200:
            token = login_resp.json()["access_token"]
            profile_resp = client.get(
                "/api/v1/reseller/profile",
                headers={"Authorization": f"Bearer {token}"},
            )
            # 401 or 403 — owner is suspended
            assert profile_resp.status_code in (401, 403)
        else:
            assert login_resp.status_code in (401, 403)

    @pytest.mark.integration
    def test_suspended_reseller_api_key_fails(
        self, client, admin_auth_headers, test_reseller,
        reseller_auth_headers,
    ):
        """API keys are deactivated when reseller is suspended."""
        # Create an API key before suspending
        key_resp = client.post(
            "/api/v1/reseller/api-keys",
            headers=reseller_auth_headers,
            json={"name": "PreSuspend", "scopes": ["users:read"]},
        )
        assert key_resp.status_code == 201
        raw_key = key_resp.json()["key"]

        # Suspend the reseller
        client.post(
            f"/api/v1/admin/resellers/{test_reseller['id']}/suspend",
            headers=admin_auth_headers,
        )

        # The API key should now be deactivated
        resp = client.get(
            "/api/v1/reseller/users",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    def test_unsuspend_reseller(
        self, client, admin_auth_headers, test_reseller
    ):
        """POST /admin/resellers/{id}/unsuspend restores access."""
        # Suspend first
        client.post(
            f"/api/v1/admin/resellers/{test_reseller['id']}/suspend",
            headers=admin_auth_headers,
        )
        # Unsuspend
        resp = client.post(
            f"/api/v1/admin/resellers/{test_reseller['id']}/unsuspend",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is True

    @pytest.mark.integration
    def test_reseller_functional_after_unsuspend(
        self, client, admin_auth_headers, test_reseller
    ):
        """Reseller can login and operate after unsuspension."""
        # Suspend then unsuspend
        client.post(
            f"/api/v1/admin/resellers/{test_reseller['id']}/suspend",
            headers=admin_auth_headers,
        )
        client.post(
            f"/api/v1/admin/resellers/{test_reseller['id']}/unsuspend",
            headers=admin_auth_headers,
        )
        # Re-login
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": test_reseller["contact_email"],
                "password": test_reseller["password"],
            },
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        profile_resp = client.get(
            "/api/v1/reseller/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert profile_resp.status_code == 200


# =============================================================================
# Section 16 — Admin User Management
# =============================================================================

class TestAdminUserManagementHTTP:
    """Admin-level user listing and direct creation."""

    @pytest.mark.integration
    def test_admin_list_all_users(
        self, client, admin_auth_headers, test_reseller, reseller_user
    ):
        """GET /admin/users returns all users including reseller-managed."""
        resp = client.get(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pagination"]["total"] >= 1
        # Check that reseller-managed users have reseller_name
        managed = [
            u for u in data["users"]
            if u.get("reseller_id") == test_reseller["id"]
        ]
        for u in managed:
            assert u.get("reseller_name") is not None

    @pytest.mark.integration
    def test_admin_create_direct_user(self, client, admin_auth_headers):
        """POST /admin/users creates a user with no reseller association."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"direct{unique}",
                "email": f"direct_{unique}@test.com",
                "password": "DirectPass123",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "user"
        assert data.get("reseller_id") is None


# =============================================================================
# Section 17 — Platform Stats & Audit
# =============================================================================

class TestPlatformStatsHTTP:
    """Admin platform-level endpoints."""

    @pytest.mark.integration
    def test_platform_stats(
        self, client, admin_auth_headers, test_reseller
    ):
        """GET /admin/stats returns counts."""
        resp = client.get(
            "/api/v1/admin/stats",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["resellers"]["total"] >= 1
        assert "users" in data
        assert "platform" in data

    @pytest.mark.integration
    def test_audit_log(self, client, admin_auth_headers):
        """GET /admin/audit returns (possibly empty) paginated entries."""
        resp = client.get(
            "/api/v1/admin/audit",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "pagination" in data

    @pytest.mark.integration
    def test_platform_usage(self, client, admin_auth_headers):
        """GET /admin/usage?period=month returns usage totals."""
        resp = client.get(
            "/api/v1/admin/usage",
            headers=admin_auth_headers,
            params={"period": "month"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "period" in data
        assert "totals" in data


# =============================================================================
# Section 19 — Delete Operations
# =============================================================================

class TestDeleteOperationsHTTP:
    """Reseller deletion with confirmation gate."""

    @pytest.mark.integration
    def test_delete_reseller_with_confirm(
        self, client, admin_auth_headers, second_reseller
    ):
        """DELETE /admin/resellers/{id}?confirm=true → 200."""
        resp = client.delete(
            f"/api/v1/admin/resellers/{second_reseller['id']}",
            headers=admin_auth_headers,
            params={"confirm": "true"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"

    @pytest.mark.integration
    def test_delete_without_confirm_400(
        self, client, admin_auth_headers, test_reseller
    ):
        """DELETE /admin/resellers/{id} without confirm → 400."""
        resp = client.delete(
            f"/api/v1/admin/resellers/{test_reseller['id']}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
