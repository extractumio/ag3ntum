"""
Tests for short-lived SSE connection tokens.

Tests cover:
- Token creation and validation
- Single-use enforcement (token consumed on first use)
- TTL expiry
- Redis failure handling
- Connection-token endpoint
- validate_sse_token helper (connection token + JWT fallback)
"""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.services.connection_token import (
    create_connection_token,
    validate_connection_token,
    CONNECTION_TOKEN_TTL,
)


class TestCreateConnectionToken:
    """Tests for create_connection_token."""

    @pytest.mark.asyncio
    async def test_creates_token(self) -> None:
        """Should return a non-empty token string."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()

        with patch("src.services.connection_token._redis.get", return_value=mock_redis):
            token = await create_connection_token("user-123")

        assert isinstance(token, str)
        assert len(token) > 0

    @pytest.mark.asyncio
    async def test_stores_in_redis_with_ttl(self) -> None:
        """Should store user_id in Redis with the configured TTL."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()

        with patch("src.services.connection_token._redis.get", return_value=mock_redis):
            token = await create_connection_token("user-456")

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args[0][1] == "user-456"
        assert call_args[1]["ex"] == CONNECTION_TOKEN_TTL

    @pytest.mark.asyncio
    async def test_raises_on_redis_failure(self) -> None:
        """Should raise RuntimeError when Redis is unavailable."""
        import redis.asyncio as redis
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=redis.ConnectionError("down"))

        with patch("src.services.connection_token._redis.get", return_value=mock_redis):
            with pytest.raises(RuntimeError, match="Could not create"):
                await create_connection_token("user-789")


class TestValidateConnectionToken:
    """Tests for validate_connection_token."""

    @pytest.mark.asyncio
    async def test_returns_user_id_for_valid_token(self) -> None:
        """Should return user_id for a valid token."""
        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value="user-123")

        with patch("src.services.connection_token._redis.get", return_value=mock_redis):
            result = await validate_connection_token("valid-token")

        assert result == "user-123"

    @pytest.mark.asyncio
    async def test_returns_none_for_expired_token(self) -> None:
        """Should return None for an expired/invalid token."""
        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value=None)

        with patch("src.services.connection_token._redis.get", return_value=mock_redis):
            result = await validate_connection_token("expired-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_single_use_via_getdel(self) -> None:
        """Should use GETDEL for atomic single-use consumption."""
        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value="user-123")

        with patch("src.services.connection_token._redis.get", return_value=mock_redis):
            await validate_connection_token("one-time-token")

        # GETDEL should have been called (atomically deletes the key)
        mock_redis.getdel.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_on_redis_failure(self) -> None:
        """Should return None (not raise) when Redis is unavailable."""
        import redis.asyncio as redis
        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(side_effect=redis.ConnectionError("down"))

        with patch("src.services.connection_token._redis.get", return_value=mock_redis):
            result = await validate_connection_token("any-token")

        assert result is None


class TestConnectionTokenEndpoint:
    """Tests for POST /auth/connection-token endpoint."""

    @pytest.mark.unit
    def test_requires_authentication(self, client) -> None:
        """Should require JWT authentication."""
        response = client.post("/api/v1/auth/connection-token")
        assert response.status_code in (401, 403)

    @pytest.mark.unit
    def test_returns_connection_token(self, client, auth_headers) -> None:
        """Should return a connection token for authenticated users."""
        with patch(
            "src.api.routes.auth.create_connection_token",
            new_callable=AsyncMock,
            return_value="test-conn-token-abc",
        ):
            response = client.post(
                "/api/v1/auth/connection-token",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert "connection_token" in data
        assert data["connection_token"] == "test-conn-token-abc"

    @pytest.mark.unit
    def test_returns_503_on_redis_failure(self, client, auth_headers) -> None:
        """Should return 503 when Redis is unavailable."""
        with patch(
            "src.api.routes.auth.create_connection_token",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Redis down"),
        ):
            response = client.post(
                "/api/v1/auth/connection-token",
                headers=auth_headers,
            )

        assert response.status_code == 503


class TestValidateSSEToken:
    """Tests for the validate_sse_token helper in deps.py."""

    @pytest.mark.asyncio
    async def test_prefers_connection_token(self) -> None:
        """Should try connection token first."""
        from src.api.deps import validate_sse_token

        with patch(
            "src.api.deps.validate_connection_token",
            new_callable=AsyncMock,
            return_value="user-from-conn-token",
        ):
            db = AsyncMock()
            result = await validate_sse_token("some-token", None, db)

        assert result == "user-from-conn-token"

    @pytest.mark.asyncio
    async def test_falls_back_to_jwt(self) -> None:
        """Should fall back to JWT when connection token is invalid."""
        from src.api.deps import validate_sse_token

        with patch(
            "src.api.deps.validate_connection_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "src.api.deps.auth_service.validate_token",
                new_callable=AsyncMock,
                return_value="user-from-jwt",
            ):
                db = AsyncMock()
                result = await validate_sse_token("jwt-token", None, db)

        assert result == "user-from-jwt"

    @pytest.mark.asyncio
    async def test_extracts_from_authorization_header(self) -> None:
        """Should extract token from Authorization header if query param missing."""
        from src.api.deps import validate_sse_token

        with patch(
            "src.api.deps.validate_connection_token",
            new_callable=AsyncMock,
            return_value="user-123",
        ):
            db = AsyncMock()
            result = await validate_sse_token(
                None, "Bearer my-conn-token", db
            )

        assert result == "user-123"

    @pytest.mark.asyncio
    async def test_raises_401_when_no_token(self) -> None:
        """Should raise 401 when no token is provided."""
        from src.api.deps import validate_sse_token
        from fastapi import HTTPException

        db = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await validate_sse_token(None, None, db)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_raises_401_when_both_fail(self) -> None:
        """Should raise 401 when both connection token and JWT fail."""
        from src.api.deps import validate_sse_token
        from fastapi import HTTPException

        with patch(
            "src.api.deps.validate_connection_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "src.api.deps.auth_service.validate_token",
                new_callable=AsyncMock,
                return_value=None,
            ):
                db = AsyncMock()
                with pytest.raises(HTTPException) as exc_info:
                    await validate_sse_token("bad-token", None, db)

                assert exc_info.value.status_code == 401
