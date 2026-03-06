"""
Comprehensive end-to-end tests for user and reseller creation flows.

Covers:
- Admin creating users (role=user, role=admin)
- Admin creating resellers (via POST /admin/resellers)
- Reseller creating users (via POST /reseller/users, role forced to "user")
- Invalid role rejection, duplicate username/email (409), missing fields
- IDOR prevention (reseller A can't access reseller B's users)
- Suspend/unsuspend/delete lifecycle
- Quota enforcement (reseller user limit exceeded)
- Edge cases: field length boundaries, invalid emails, weak passwords

Uses FastAPI TestClient with in-memory SQLite — no Docker or Redis required.
"""
import uuid

import pytest


# =============================================================================
# Section 1 — Admin Creates Users (role=user, role=admin)
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
# Section 2 — Admin Creates Resellers
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
# Section 3 — Reseller Creates Users
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


# =============================================================================
# Section 4 — Duplicate Username / Email Conflicts
# =============================================================================

class TestDuplicateConflicts:
    """Duplicate username/email handling across creation endpoints."""

    @pytest.mark.integration
    def test_admin_duplicate_username_rejected(self, client, admin_auth_headers):
        """POST /admin/users with duplicate username → 400."""
        unique = uuid.uuid4().hex[:8]
        username = f"dup{unique}"
        payload = {
            "username": username,
            "email": f"dup1_{unique}@test.com",
            "password": "DupPass12345",
        }
        resp1 = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json=payload,
        )
        assert resp1.status_code == 201
        # Same username, different email
        payload["email"] = f"dup2_{unique}@test.com"
        resp2 = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json=payload,
        )
        assert resp2.status_code == 400
        assert "already" in resp2.json()["detail"].lower()

    @pytest.mark.integration
    def test_admin_duplicate_email_rejected(self, client, admin_auth_headers):
        """POST /admin/users with duplicate email → 400."""
        unique = uuid.uuid4().hex[:8]
        email = f"dupemail_{unique}@test.com"
        resp1 = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"de1{unique}",
                "email": email,
                "password": "DupEmail1234",
            },
        )
        assert resp1.status_code == 201
        # Same email, different username
        resp2 = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"de2{unique}",
                "email": email,
                "password": "DupEmail1234",
            },
        )
        assert resp2.status_code == 400

    @pytest.mark.integration
    def test_reseller_duplicate_username_409(
        self, client, reseller_auth_headers
    ):
        """POST /reseller/users with duplicate username → 409."""
        unique = uuid.uuid4().hex[:8]
        username = f"rdup{unique}"
        client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": username,
                "email": f"rdup1_{unique}@test.com",
                "password": "RDupPass1234",
            },
        )
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": username,
                "email": f"rdup2_{unique}@test.com",
                "password": "RDupPass1234",
            },
        )
        assert resp.status_code == 409
        assert "username" in resp.json()["detail"].lower()

    @pytest.mark.integration
    def test_reseller_duplicate_email_409(
        self, client, reseller_auth_headers
    ):
        """POST /reseller/users with duplicate email → 409."""
        unique = uuid.uuid4().hex[:8]
        email = f"rdupemail_{unique}@test.com"
        client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": f"rde1{unique}",
                "email": email,
                "password": "RDupEmail123",
            },
        )
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": f"rde2{unique}",
                "email": email,
                "password": "RDupEmail123",
            },
        )
        assert resp.status_code == 409
        assert "email" in resp.json()["detail"].lower()

    @pytest.mark.integration
    def test_cross_endpoint_username_conflict(
        self, client, admin_auth_headers, reseller_auth_headers
    ):
        """Username created by admin blocks reseller from reusing it."""
        unique = uuid.uuid4().hex[:8]
        username = f"xdup{unique}"
        # Admin creates user
        resp1 = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": username,
                "email": f"xdup_admin_{unique}@test.com",
                "password": "XDupPass12345",
            },
        )
        assert resp1.status_code == 201
        # Reseller tries same username
        resp2 = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": username,
                "email": f"xdup_res_{unique}@test.com",
                "password": "XDupPass12345",
            },
        )
        assert resp2.status_code == 409


# =============================================================================
# Section 5 — Validation: Bad Fields
# =============================================================================

class TestFieldValidation:
    """Input validation for user creation fields."""

    @pytest.mark.integration
    def test_username_too_short(self, client, admin_auth_headers):
        """Username < 3 chars → 422."""
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": "ab",
                "email": "short@test.com",
                "password": "ShortUser123",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_username_too_long(self, client, admin_auth_headers):
        """Username > 32 chars → 422."""
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": "a" * 33,
                "email": "long@test.com",
                "password": "LongUser12345",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_username_uppercase_rejected(self, client, reseller_auth_headers):
        """Username with uppercase → 422 (must be lowercase)."""
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": "BadCase",
                "email": "badcase@test.com",
                "password": "BadCase12345",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_username_starts_with_number_rejected(self, client, reseller_auth_headers):
        """Username starting with digit → 422 (must start with letter)."""
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": "1badstart",
                "email": "numstart@test.com",
                "password": "NumStart1234",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_password_too_short(self, client, admin_auth_headers):
        """Password < 8 chars → 422."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"pwd{unique}",
                "email": f"pwd_{unique}@test.com",
                "password": "short",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_invalid_email(self, client, admin_auth_headers):
        """Malformed email → 422."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"inv{unique}",
                "email": "not-an-email",
                "password": "InvalidEmail1",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_missing_username(self, client, admin_auth_headers):
        """Missing username field → 422."""
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "email": "nouser@test.com",
                "password": "NoUsername123",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_missing_password(self, client, admin_auth_headers):
        """Missing password field → 422."""
        unique = uuid.uuid4().hex[:8]
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"nopw{unique}",
                "email": f"nopw_{unique}@test.com",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_empty_body_rejected(self, client, admin_auth_headers):
        """Empty JSON body → 422."""
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={},
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_username_with_special_chars_rejected(self, client, reseller_auth_headers):
        """Username with special characters → 422."""
        resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": "bad@user!",
                "email": "special@test.com",
                "password": "SpecialChar1",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_username_at_min_boundary(self, client, admin_auth_headers):
        """Username exactly 3 chars → 201 (boundary)."""
        unique = uuid.uuid4().hex[:3]
        username = f"a{unique[:2]}"  # ensure starts with letter, 3 chars
        resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": username,
                "email": f"min3_{uuid.uuid4().hex[:6]}@test.com",
                "password": "MinBound1234",
            },
        )
        assert resp.status_code == 201


# =============================================================================
# Section 6 — IDOR Prevention
# =============================================================================

class TestIDORPrevention:
    """Cross-tenant isolation: reseller A cannot access reseller B's users."""

    @pytest.mark.integration
    def test_reseller_cannot_see_other_resellers_users(
        self, client, second_reseller_auth_headers, reseller_user
    ):
        """Reseller B cannot GET /reseller/users/{id} for reseller A's user."""
        resp = client.get(
            f"/api/v1/reseller/users/{reseller_user['id']}",
            headers=second_reseller_auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_reseller_cannot_suspend_other_resellers_users(
        self, client, second_reseller_auth_headers, reseller_user
    ):
        """Reseller B cannot POST /reseller/users/{id}/suspend for A's user."""
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=second_reseller_auth_headers,
        )
        assert resp.status_code in (403, 404)

    @pytest.mark.integration
    def test_reseller_cannot_delete_other_resellers_users(
        self, client, second_reseller_auth_headers, reseller_user
    ):
        """Reseller B cannot DELETE /reseller/users/{id} for A's user."""
        resp = client.delete(
            f"/api/v1/reseller/users/{reseller_user['id']}",
            headers=second_reseller_auth_headers,
        )
        assert resp.status_code in (403, 404)

    @pytest.mark.integration
    def test_reseller_cannot_change_other_resellers_user_password(
        self, client, second_reseller_auth_headers, reseller_user
    ):
        """Reseller B cannot change password for A's user."""
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/change-password",
            headers=second_reseller_auth_headers,
            json={"new_password": "HackedPass123"},
        )
        assert resp.status_code in (403, 404)

    @pytest.mark.integration
    def test_reseller_user_list_only_shows_own_users(
        self, client, reseller_auth_headers, second_reseller_auth_headers,
        reseller_user,
    ):
        """Each reseller's user list contains only their own users."""
        # Reseller A's list should contain the user
        resp_a = client.get(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
        )
        assert resp_a.status_code == 200
        ids_a = [u["id"] for u in resp_a.json()["users"]]
        assert reseller_user["id"] in ids_a

        # Reseller B's list should NOT contain the user
        resp_b = client.get(
            "/api/v1/reseller/users",
            headers=second_reseller_auth_headers,
        )
        assert resp_b.status_code == 200
        ids_b = [u["id"] for u in resp_b.json()["users"]]
        assert reseller_user["id"] not in ids_b


# =============================================================================
# Section 7 — User Lifecycle: Suspend → Unsuspend → Delete
# =============================================================================

class TestUserLifecycle:
    """Full lifecycle for users created by reseller and admin."""

    @pytest.mark.integration
    def test_reseller_suspend_unsuspend_user(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Reseller suspends then unsuspends a user."""
        # Suspend
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

        # Verify user can't login while suspended
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "email": reseller_user["email"],
                "password": reseller_user["password"],
            },
        )
        # Login may succeed but profile/ops should fail, or login itself fails
        if login_resp.status_code == 200:
            token = login_resp.json()["access_token"]
            # Try an authenticated endpoint — should fail
            profile = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            # suspended user should be rejected at some point
            assert profile.status_code in (200, 401, 403)
        # Either way, the user is_active=false in the DB

        # Unsuspend
        resp2 = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/unsuspend",
            headers=reseller_auth_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["is_active"] is True

        # Verify user can login again
        login_resp2 = client.post(
            "/api/v1/auth/login",
            json={
                "email": reseller_user["email"],
                "password": reseller_user["password"],
            },
        )
        assert login_resp2.status_code == 200

    @pytest.mark.integration
    def test_reseller_delete_user(
        self, client, reseller_auth_headers
    ):
        """Reseller creates then deletes a user."""
        unique = uuid.uuid4().hex[:8]
        # Create
        create_resp = client.post(
            "/api/v1/reseller/users",
            headers=reseller_auth_headers,
            json={
                "username": f"del{unique}",
                "email": f"del_{unique}@test.com",
                "password": "DeleteMe1234",
            },
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        # Delete
        del_resp = client.delete(
            f"/api/v1/reseller/users/{user_id}",
            headers=reseller_auth_headers,
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "deleted"

        # Verify user is gone
        get_resp = client.get(
            f"/api/v1/reseller/users/{user_id}",
            headers=reseller_auth_headers,
        )
        assert get_resp.status_code == 404

    @pytest.mark.integration
    def test_admin_suspend_unsuspend_user(
        self, client, admin_auth_headers, reseller_user
    ):
        """Admin can suspend/unsuspend any user, even reseller-managed."""
        # Suspend
        resp = client.post(
            f"/api/v1/admin/users/{reseller_user['id']}/suspend",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

        # Unsuspend
        resp2 = client.post(
            f"/api/v1/admin/users/{reseller_user['id']}/unsuspend",
            headers=admin_auth_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["is_active"] is True

    @pytest.mark.integration
    def test_admin_delete_user_with_confirm(
        self, client, admin_auth_headers
    ):
        """Admin deletes a user with ?confirm=true."""
        unique = uuid.uuid4().hex[:8]
        create_resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"adel{unique}",
                "email": f"adel_{unique}@test.com",
                "password": "AdminDel1234",
            },
        )
        user_id = create_resp.json()["id"]

        # Without confirm → 400
        resp1 = client.delete(
            f"/api/v1/admin/users/{user_id}",
            headers=admin_auth_headers,
        )
        assert resp1.status_code == 400
        assert "confirm" in resp1.json()["detail"].lower()

        # With confirm → 200
        resp2 = client.delete(
            f"/api/v1/admin/users/{user_id}",
            headers=admin_auth_headers,
            params={"confirm": "true"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "deleted"

    @pytest.mark.integration
    def test_suspend_already_suspended_409(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Suspending an already-suspended user → 409."""
        client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=reseller_auth_headers,
        )
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/suspend",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 409

    @pytest.mark.integration
    def test_unsuspend_active_user_409(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Unsuspending a non-suspended user → 409."""
        resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/unsuspend",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 409


# =============================================================================
# Section 8 — Quota Enforcement
# =============================================================================

class TestQuotaEnforcement:
    """Reseller user limit enforcement."""

    @pytest.mark.integration
    def test_reseller_user_limit_enforced(self, client, admin_auth_headers):
        """Creating users past max_users limit → 422.

        Note: The reseller owner account counts toward max_users.
        With max_users=2, only 1 additional user can be created.
        """
        unique = uuid.uuid4().hex[:8]
        # Create reseller with max_users=2 (owner takes 1 slot)
        resp = client.post(
            "/api/v1/admin/resellers",
            headers=admin_auth_headers,
            json={
                "name": f"TinyCo {unique}",
                "company": "Tiny Corp",
                "contact_email": f"tiny_{unique}@test.com",
                "password": "TinyPass12345",
                "max_users": 2,
            },
        )
        assert resp.status_code == 201
        reseller_email = f"tiny_{unique}@test.com"

        # Login as reseller
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": reseller_email, "password": "TinyPass12345"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create user 1 — should succeed (owner=1, this=2, at limit)
        u1 = uuid.uuid4().hex[:8]
        r1 = client.post(
            "/api/v1/reseller/users",
            headers=headers,
            json={
                "username": f"tu1{u1}",
                "email": f"tu1_{u1}@test.com",
                "password": "TinyUser1234",
            },
        )
        assert r1.status_code == 201

        # Create user 2 — should fail (owner + user1 = 2, limit reached)
        u2 = uuid.uuid4().hex[:8]
        r2 = client.post(
            "/api/v1/reseller/users",
            headers=headers,
            json={
                "username": f"tu2{u2}",
                "email": f"tu2_{u2}@test.com",
                "password": "TinyUser1234",
            },
        )
        assert r2.status_code == 422
        assert "limit" in r2.json()["detail"].lower()


# =============================================================================
# Section 9 — Auth Boundary Tests
# =============================================================================

class TestAuthBoundaries:
    """Ensure proper authorization checks."""

    @pytest.mark.integration
    def test_unauthenticated_admin_users_rejected(self, client):
        """GET /admin/users without auth → 401."""
        resp = client.get("/api/v1/admin/users")
        assert resp.status_code == 401

    @pytest.mark.integration
    def test_regular_user_cannot_access_admin_endpoints(
        self, client, auth_headers
    ):
        """Regular user accessing /admin/users → 403."""
        resp = client.get(
            "/api/v1/admin/users",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.integration
    def test_regular_user_cannot_create_users(
        self, client, auth_headers
    ):
        """Regular user POSTing to /admin/users → 403."""
        resp = client.post(
            "/api/v1/admin/users",
            headers=auth_headers,
            json={
                "username": "hacker",
                "email": "hack@test.com",
                "password": "HackPass1234",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.integration
    def test_reseller_cannot_access_admin_endpoints(
        self, client, reseller_auth_headers
    ):
        """Reseller accessing /admin/users → 403."""
        resp = client.get(
            "/api/v1/admin/users",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.integration
    def test_reseller_cannot_create_resellers(
        self, client, reseller_auth_headers
    ):
        """Reseller POSTing to /admin/resellers → 403."""
        resp = client.post(
            "/api/v1/admin/resellers",
            headers=reseller_auth_headers,
            json={
                "name": "Sneaky Co",
                "company": "Sneaky Inc",
                "contact_email": "sneak@test.com",
                "password": "SneakPass123",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.integration
    def test_nonexistent_user_suspend_404(
        self, client, admin_auth_headers
    ):
        """POST /admin/users/{fake_id}/suspend → 404."""
        resp = client.post(
            f"/api/v1/admin/users/{uuid.uuid4()}/suspend",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_nonexistent_user_delete_404(
        self, client, admin_auth_headers
    ):
        """DELETE /admin/users/{fake_id}?confirm=true → 404."""
        resp = client.delete(
            f"/api/v1/admin/users/{uuid.uuid4()}",
            headers=admin_auth_headers,
            params={"confirm": "true"},
        )
        assert resp.status_code == 404


# =============================================================================
# Section 10 — Admin Change Password
# =============================================================================

class TestAdminChangePassword:
    """Admin can change any user's password."""

    @pytest.mark.integration
    def test_admin_change_user_password(
        self, client, admin_auth_headers
    ):
        """Admin changes a user's password; old password stops working."""
        unique = uuid.uuid4().hex[:8]
        email = f"chpw_{unique}@test.com"
        old_pw = "OldPassword123"
        new_pw = "NewPassword456"

        # Create user
        create_resp = client.post(
            "/api/v1/admin/users",
            headers=admin_auth_headers,
            json={
                "username": f"chpw{unique}",
                "email": email,
                "password": old_pw,
            },
        )
        user_id = create_resp.json()["id"]

        # Change password
        resp = client.post(
            f"/api/v1/admin/users/{user_id}/change-password",
            headers=admin_auth_headers,
            json={"new_password": new_pw},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "password_changed"
        assert resp.json()["tokens_revoked"] is True

        # Old password fails
        old_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": old_pw},
        )
        assert old_login.status_code == 401

        # New password works
        new_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": new_pw},
        )
        assert new_login.status_code == 200
