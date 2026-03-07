"""
Tests for user creation input validation and duplicate conflict handling.

Covers:
- Duplicate username/email rejection (admin and reseller endpoints)
- Cross-endpoint username conflicts
- Field validation: length, format, required fields, boundary cases

Uses FastAPI TestClient with in-memory SQLite — no Docker or Redis required.
"""
import uuid

import pytest


# =============================================================================
# Duplicate Username / Email Conflicts
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
# Validation: Bad Fields
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
