"""
Tests for authentication endpoints and JWT handling.

Comprehensive coverage of:
- Token generation and validation with email/password login
- Response structure validation
- Authentication protection on endpoints
- User environment validation
- Token revocation (logout, change-password)
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.auth_service import AuthService


class TestAuthEndpoint:
    """Tests for POST /api/v1/auth/login."""

    @pytest.mark.unit
    def test_login_with_valid_credentials(self, client: TestClient, test_user: dict) -> None:
        """Can login with valid email and password."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "user_id" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0
        assert data["user_id"] == test_user["id"]

    @pytest.mark.unit
    def test_login_response_structure(self, client: TestClient, test_user: dict) -> None:
        """Login response has complete TokenResponse structure."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )

        assert response.status_code == 200
        data = response.json()
        
        # Validate all TokenResponse fields
        assert "access_token" in data
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0
        
        assert "token_type" in data
        assert data["token_type"] == "bearer"
        
        assert "user_id" in data
        assert isinstance(data["user_id"], str)
        assert len(data["user_id"]) > 0
        
        assert "expires_in" in data
        assert isinstance(data["expires_in"], int)
        assert data["expires_in"] > 0

    @pytest.mark.unit
    def test_token_is_valid_jwt(self, client: TestClient, test_user: dict) -> None:
        """Token returned is a valid JWT format."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )
        token = response.json()["access_token"]

        # JWT has 3 parts separated by dots
        parts = token.split(".")
        assert len(parts) == 3
        
        # Each part should be non-empty
        for part in parts:
            assert len(part) > 0

    @pytest.mark.unit
    def test_login_with_wrong_password(self, client: TestClient, test_user: dict) -> None:
        """Login fails with wrong password."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": "wrongpassword"},
        )

        assert response.status_code == 401
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_login_with_nonexistent_email(self, client: TestClient) -> None:
        """Login fails with nonexistent email."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@example.com", "password": "test123"},
        )

        assert response.status_code == 401

    @pytest.mark.unit
    def test_login_returns_json(self, client: TestClient, test_user: dict) -> None:
        """Login endpoint returns JSON content type."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )
        
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")


class TestAuthMeEndpoint:
    """Tests for GET /api/v1/auth/me."""

    @pytest.mark.unit
    def test_get_current_user(self, client: TestClient, auth_headers: dict, test_user: dict) -> None:
        """Can get current user info with valid token."""
        response = client.get("/api/v1/auth/me", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_user["id"]
        assert data["username"] == test_user["username"]
        assert data["email"] == test_user["email"]
        assert "role" in data
        assert "created_at" in data

    @pytest.mark.unit
    def test_get_current_user_requires_auth(self, client: TestClient) -> None:
        """Cannot get current user without authentication."""
        response = client.get("/api/v1/auth/me")

        assert response.status_code == 401


class TestAuthService:
    """Unit tests for AuthService."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_generate_token(self, test_user: dict) -> None:
        """Can generate a JWT token."""
        auth_service = AuthService()
        token, expires_in = auth_service.generate_token(
            test_user["id"],
            test_user["jwt_secret"]
        )

        assert token is not None
        assert len(token) > 0
        assert expires_in > 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch.object(AuthService, "validate_user_environment")
    async def test_validate_token_success(
        self, mock_validate_env, test_session: AsyncSession, test_user: dict
    ) -> None:
        """Valid token returns the user ID."""
        auth_service = AuthService()
        token, _ = auth_service.generate_token(
            test_user["id"],
            test_user["jwt_secret"]
        )

        result = await auth_service.validate_token(token, test_session)

        assert result == test_user["id"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_validate_token_invalid(self, test_session: AsyncSession) -> None:
        """Invalid token returns None."""
        auth_service = AuthService()
        result = await auth_service.validate_token("invalid-token", test_session)

        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch.object(AuthService, "validate_user_environment")
    async def test_validate_token_tampered(
        self, mock_validate_env, test_session: AsyncSession, test_user: dict
    ) -> None:
        """Tampered token returns None."""
        auth_service = AuthService()
        token, _ = auth_service.generate_token(
            test_user["id"],
            test_user["jwt_secret"]
        )
        # Tamper with the token
        tampered = token[:-5] + "xxxxx"

        result = await auth_service.validate_token(tampered, test_session)

        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_validate_empty_token(self, test_session: AsyncSession) -> None:
        """Empty token returns None."""
        auth_service = AuthService()
        result = await auth_service.validate_token("", test_session)
        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_validate_malformed_token(self, test_session: AsyncSession) -> None:
        """Malformed token (missing parts) returns None."""
        auth_service = AuthService()
        result = await auth_service.validate_token("not.a.valid.token.at.all", test_session)
        assert result is None


class TestAuthProtection:
    """Tests for authentication protection on endpoints."""

    @pytest.mark.unit
    def test_sessions_requires_auth(self, client: TestClient) -> None:
        """Session endpoints require authentication."""
        response = client.get("/api/v1/sessions")

        assert response.status_code == 401  # Unauthorized without token
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_sessions_with_valid_token(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Session endpoints work with valid token."""
        response = client.get("/api/v1/sessions", headers=auth_headers)

        assert response.status_code == 200

    @pytest.mark.unit
    def test_invalid_token_rejected(self, client: TestClient) -> None:
        """Invalid token is rejected."""
        headers = {"Authorization": "Bearer invalid-token"}
        response = client.get("/api/v1/sessions", headers=headers)

        assert response.status_code == 401

    @pytest.mark.unit
    def test_missing_bearer_prefix_rejected(self, client: TestClient, test_user: dict) -> None:
        """Token without Bearer prefix is rejected."""
        # Get a valid token first
        token_response = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )
        token = token_response.json()["access_token"]
        
        # Try without Bearer prefix
        headers = {"Authorization": token}
        response = client.get("/api/v1/sessions", headers=headers)

        assert response.status_code == 401

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_truncated_token_rejected(self, test_session: AsyncSession, test_user: dict) -> None:
        """Truncated token (missing signature) returns None on validation."""
        auth_service = AuthService()
        # Generate a valid token then truncate it
        token, _ = auth_service.generate_token(
            test_user["id"],
            test_user["jwt_secret"]
        )
        parts = token.split(".")
        # Remove the signature part
        truncated = ".".join(parts[:2])
        
        result = await auth_service.validate_token(truncated, test_session)
        assert result is None

    @pytest.mark.unit
    def test_health_does_not_require_auth(self, client: TestClient) -> None:
        """Health endpoint is accessible without auth."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200


class TestValidateUserEnvironment:
    """Tests for AuthService.validate_user_environment security checks."""

    @pytest.fixture
    def auth_service(self) -> AuthService:
        return AuthService()

    @pytest.fixture
    def temp_user_dir(self, tmp_path):
        """Create a temp directory structure for user environment tests."""
        user_dir = tmp_path / "testuser"
        user_dir.mkdir()
        return user_dir

    @pytest.mark.unit
    def test_missing_home_directory_raises(self, auth_service: AuthService) -> None:
        """Missing home directory raises UserEnvironmentError."""
        from src.services.auth_service import UserEnvironmentError

        with patch("src.services.auth_service.USERS_DIR", Path("/nonexistent")):
            with pytest.raises(UserEnvironmentError, match="home directory does not exist"):
                auth_service.validate_user_environment("nouser")

    @pytest.mark.unit
    def test_missing_venv_raises(self, auth_service: AuthService, temp_user_dir) -> None:
        """Missing venv directory raises UserEnvironmentError."""
        from src.services.auth_service import UserEnvironmentError

        with patch("src.services.auth_service.USERS_DIR", temp_user_dir.parent):
            with pytest.raises(UserEnvironmentError, match="Python environment not initialized"):
                auth_service.validate_user_environment(temp_user_dir.name)

    @pytest.mark.unit
    def test_missing_python_binary_raises(self, auth_service: AuthService, temp_user_dir) -> None:
        """Missing python3 binary raises UserEnvironmentError."""
        from src.services.auth_service import UserEnvironmentError

        # Create venv/bin but no python3
        venv_bin = temp_user_dir / "venv" / "bin"
        venv_bin.mkdir(parents=True)

        with patch("src.services.auth_service.USERS_DIR", temp_user_dir.parent):
            with pytest.raises(UserEnvironmentError, match="Python environment corrupted"):
                auth_service.validate_user_environment(temp_user_dir.name)

    @pytest.mark.unit
    def test_valid_environment_passes(self, auth_service: AuthService, temp_user_dir) -> None:
        """Valid user environment passes validation."""
        # Create complete valid structure
        venv_bin = temp_user_dir / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        python_bin = venv_bin / "python3"
        python_bin.touch()

        with patch("src.services.auth_service.USERS_DIR", temp_user_dir.parent):
            # Should not raise
            auth_service.validate_user_environment(temp_user_dir.name)


class TestTokenRevocation:
    """Tests for JWT token revocation via token_version."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch.object(AuthService, "validate_user_environment")
    async def test_token_with_wrong_version_rejected(
        self, mock_validate_env, test_session: AsyncSession, test_user: dict
    ) -> None:
        """Token with outdated version should be rejected."""
        auth_svc = AuthService()
        # Generate token with version 0
        token, _ = auth_svc.generate_token(
            test_user["id"], test_user["jwt_secret"], token_version=0
        )

        # Simulate version increment (logout)
        from sqlalchemy import select
        from src.db.models import User
        result = await test_session.execute(
            select(User).where(User.id == test_user["id"])
        )
        user = result.scalar_one()
        user.token_version = 1
        await test_session.commit()

        # Token should now be rejected
        result = await auth_svc.validate_token(token, test_session)
        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch.object(AuthService, "validate_user_environment")
    async def test_token_with_current_version_accepted(
        self, mock_validate_env, test_session: AsyncSession, test_user: dict
    ) -> None:
        """Token with matching version should be accepted."""
        auth_svc = AuthService()
        token, _ = auth_svc.generate_token(
            test_user["id"], test_user["jwt_secret"], token_version=0
        )

        result = await auth_svc.validate_token(token, test_session)
        assert result == test_user["id"]

    @pytest.mark.unit
    def test_logout_revokes_tokens(self, client: TestClient, test_user: dict) -> None:
        """POST /auth/logout should invalidate the token used."""
        # Login
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Logout
        with patch("src.api.routes.auth.check_rate_limit", new_callable=AsyncMock, return_value=True):
            with patch("src.api.routes.auth.reset_rate_limit", new_callable=AsyncMock):
                logout_resp = client.post("/api/v1/auth/logout", headers=headers)
        assert logout_resp.status_code == 200

        # Old token should now be rejected (401 on protected endpoint)
        me_resp = client.get("/api/v1/auth/me", headers=headers)
        assert me_resp.status_code == 401

    @pytest.mark.unit
    def test_change_password_returns_new_token(self, client: TestClient, test_user: dict) -> None:
        """POST /auth/change-password should return a new valid token."""
        # Login first
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Change password
        with patch("src.api.routes.auth.check_rate_limit", new_callable=AsyncMock, return_value=True):
            with patch("src.api.routes.auth.reset_rate_limit", new_callable=AsyncMock):
                change_resp = client.post(
                    "/api/v1/auth/change-password",
                    headers=headers,
                    json={
                        "current_password": test_user["password"],
                        "new_password": "NewSecurePassword456!",
                    },
                )
        assert change_resp.status_code == 200
        data = change_resp.json()
        assert "access_token" in data
        new_token = data["access_token"]

        # Old token should be invalid
        me_resp = client.get("/api/v1/auth/me", headers=headers)
        assert me_resp.status_code == 401

        # New token should work
        new_headers = {"Authorization": f"Bearer {new_token}"}
        me_resp = client.get("/api/v1/auth/me", headers=new_headers)
        assert me_resp.status_code == 200

    @pytest.mark.unit
    def test_change_password_wrong_current_rejected(self, client: TestClient, test_user: dict) -> None:
        """Change password with wrong current password should fail."""
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": test_user["email"], "password": test_user["password"]},
        )
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        with patch("src.api.routes.auth.check_rate_limit", new_callable=AsyncMock, return_value=True):
            with patch("src.api.routes.auth.reset_rate_limit", new_callable=AsyncMock):
                change_resp = client.post(
                    "/api/v1/auth/change-password",
                    headers=headers,
                    json={
                        "current_password": "wrongpassword",
                        "new_password": "NewPassword123!",
                    },
                )
        assert change_resp.status_code == 400
