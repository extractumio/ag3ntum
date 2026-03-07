"""
Tests for user and reseller creation flows.

Covers:
- Admin creating users (role=user, role=admin)
- Admin creating resellers (via POST /admin/resellers)
- Reseller creating users (via POST /reseller/users, role forced to "user")

Uses FastAPI TestClient with in-memory SQLite — no Docker or Redis required.
"""
import uuid

import pytest


# =============================================================================
# Admin Creates Users (role=user, role=admin)
# =============================================================================

class TestAdminCreateUser:
    """Admin creates users with different roles via POST /admin/users."""

    @pytest.mark.integration
    def test_create_user_role_user(self, client, admin_auth_headers):
        """POST /admin/users?role=user → 201 with role=user."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            params={"role": "user"},
            json={
                "username": f"usr{unique}",
                "email": f"usr_{unique}@test.com",
                "password": "StrongPass123",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "user"
        assert data["is_active"] is True
        assert data.get("reseller_id") is None

    @pytest.mark.integration
    def test_create_user_role_admin(self, client, admin_auth_headers):
        """POST /admin/users?role=admin → 201 with role=admin."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            params={"role": "admin"},
            json={
                "username": f"adm{unique}",
                "email": f"adm_{unique}@test.com",
                "password": "AdminPass1234",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "admin"
        assert data["is_active"] is True

    @pytest.mark.integration
    def test_create_user_default_role_is_user(self, client, admin_auth_headers):
        """POST /admin/users without role param defaults to 'user'."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"def{unique}",
                "email": f"def_{unique}@test.com",
                "password": "DefaultPass99",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "user"

    @pytest.mark.integration
    def test_create_user_role_reseller_rejected(self, client, admin_auth_headers):
        """POST /admin/users?role=reseller → 400 (invalid role)."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            params={"role": "reseller"},
            json={
                "username": f"bad{unique}",
                "email": f"bad_{unique}@test.com",
                "password": "BadRole12345",
            },
        )
        assert resp.status_code == 400
        assert "role" in resp.json()["detail"].lower()

    @pytest.mark.integration
    def test_create_user_role_superadmin_rejected(self, client, admin_auth_headers):
        """POST /admin/users?role=superadmin → 400 (invalid role)."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            params={"role": "superadmin"},
            json={
                "username": f"sup{unique}",
                "email": f"sup_{unique}@test.com",
                "password": "SuperPass123",
            },
        )
        assert resp.status_code == 400

    @pytest.mark.integration
    def test_created_user_can_login(self, client, admin_auth_headers):
        """User created by admin can login and get a JWT."""
        unique = uuid.uuid4().hex[:8]
        email = f"logintest_{unique}@test.com"
        password = "LoginTestPass1"
        client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"log{unique}",
                "email": email,
                "password": password,
            },
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login_resp.status_code == 200
        assert "access_token" in login_resp.json()

    @pytest.mark.integration
    def test_created_admin_has_admin_access(self, client, admin_auth_headers):
        """Admin user created via POST can access admin endpoints."""
        unique = uuid.uuid4().hex[:8]
        email = f"newadm_{unique}@test.com"
        password = "NewAdminPass1"
        client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            params={"role": "admin"},
            json={
                "username": f"nad{unique}",
                "email": email,
                "password": password,
            },
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        token = login_resp.json()["access_token"]
        stats_resp = client.get(
            "/api/v1/admin/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert stats_resp.status_code == 200


# =============================================================================
# Admin Creates Resellers
# =============================================================================

class TestAdminCreateReseller:
    """Admin creates resellers via POST /admin/resellers."""

    @pytest.mark.integration
    def test_create_reseller_minimal(self, client, admin_auth_headers):
        """POST /admin/resellers with minimal fields → 201."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
            json={
                "name": f"MinCo {unique}",
                "company": "Minimal Corp",
                "contact_email": f"min_{unique}@test.com",
                "password": "MinPass12345",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_active"] is True
        assert data["owner_user_id"] is not None
        assert data["owner_username"] is not None

    @pytest.mark.integration
    def test_create_reseller_with_limits(self, client, admin_auth_headers):
        """POST /admin/resellers with spending/user limits → fields populated."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
            json={
                "name": f"LimCo {unique}",
                "company": "Limited Corp",
                "contact_email": f"lim_{unique}@test.com",
                "password": "LimPass12345",
                "max_users": 3,
                "max_monthly_spending_usd": 50.0,
                "max_daily_spending_usd": 10.0,
                "spending_alert_threshold_pct": 75,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["limits"]["max_users"] == 3
        assert data["spending"]["limits"]["monthly_usd"] == 50.0
        assert data["spending"]["limits"]["daily_usd"] == 10.0

    @pytest.mark.integration
    def test_reseller_owner_can_login(self, client, admin_auth_headers):
        """Reseller owner account is automatically created and can log in."""
        unique = uuid.uuid4().hex[:8]
        email = f"ownlogin_{unique}@test.com"
        password = "OwnerPass1234"
        client.post(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
            json={
                "name": f"OwnCo {unique}",
                "company": "Own Inc",
                "contact_email": email,
                "password": password,
            },
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login_resp.status_code == 200

    @pytest.mark.integration
    def test_reseller_owner_has_reseller_profile(self, client, admin_auth_headers):
        """Reseller owner can access /reseller/profile."""
        unique = uuid.uuid4().hex[:8]
        email = f"prof_{unique}@test.com"
        password = "ProfPass12345"
        client.post(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
            json={
                "name": f"ProfCo {unique}",
                "company": "Profile Corp",
                "contact_email": email,
                "password": password,
            },
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        token = login_resp.json()["access_token"]
        profile_resp = client.get(
            "/api/v1/reseller/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert profile_resp.status_code == 200
        assert profile_resp.json()["company"] == "Profile Corp"

    @pytest.mark.integration
    def test_duplicate_reseller_email_rejected(self, client, admin_auth_headers):
        """Creating two resellers with the same contact_email → error."""
        unique = uuid.uuid4().hex[:8]
        email = f"dup_{unique}@test.com"
        payload = {
            "name": f"DupCo {unique}",
            "company": "Dup Corp",
            "contact_email": email,
            "password": "DupPass123456",
        }
        resp1 = client.post(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
            json=payload,
        )
        assert resp1.status_code == 201
        # Same email again
        payload["name"] = f"DupCo2 {unique}"
        resp2 = client.post(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
            json=payload,
        )
        assert resp2.status_code == 400


# =============================================================================
# Reseller Creates Users
# =============================================================================

class TestResellerCreateUser:
    """Reseller creates users via POST /reseller/users."""

    @pytest.mark.integration
    def test_reseller_create_user_201(
        self, client, reseller_auth_headers
    ):
        """POST /reseller/users → 201, user is active and managed by reseller."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": f"rsu{unique}",
                "email": f"rsu_{unique}@test.com",
                "password": "ResellerUser1",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        # ResellerUserResponse doesn't include 'role' — it's always 'user'
        assert data["is_active"] is True
        assert "id" in data
        assert data["username"] == f"rsu{unique}"

    @pytest.mark.integration
    def test_reseller_created_user_can_login(
        self, client, reseller_auth_headers
    ):
        """User created by reseller can login."""
        unique = uuid.uuid4().hex[:8]
        email = f"rslogin_{unique}@test.com"
        password = "LoginPass1234"
        client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": f"rsl{unique}",
                "email": email,
                "password": password,
            },
        )
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login_resp.status_code == 200

    @pytest.mark.integration
    def test_reseller_user_visible_in_admin_list(
        self, client, admin_auth_headers, reseller_auth_headers
    ):
        """Users created by reseller appear in admin /admin/users list."""
        unique = uuid.uuid4().hex[:8]
        username = f"vis{unique}"
        client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": username,
                "email": f"vis_{unique}@test.com",
                "password": "VisiblePass1",
            },
        )
        resp = client.get(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        usernames = [u["username"] for u in resp.json()["users"]]
        assert username in usernames
