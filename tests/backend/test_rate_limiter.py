"""
Tests for Redis-based auth rate limiter.

Tests cover:
1. Rate limiter allows requests within limits
2. Rate limiter blocks requests exceeding limits
3. Rate limiter resets correctly
4. Fail-open behavior when Redis is unavailable
5. Integration with login endpoint (mocked Redis)
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.services.rate_limiter import check_rate_limit, reset_rate_limit


class TestCheckRateLimit:
    """Tests for the check_rate_limit function."""

    @pytest.mark.asyncio
    async def test_allows_first_request(self):
        """First request should always be allowed."""
        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, True])

        mock_client = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.services.rate_limiter._redis.get", return_value=mock_client):
            result = await check_rate_limit("rate:test:key", max_attempts=5, window_seconds=60)

        assert result is True

    @pytest.mark.asyncio
    async def test_allows_up_to_max_attempts(self):
        """Requests up to max_attempts should be allowed."""
        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[5, False])

        mock_client = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.services.rate_limiter._redis.get", return_value=mock_client):
            result = await check_rate_limit("rate:test:key", max_attempts=5, window_seconds=60)

        assert result is True

    @pytest.mark.asyncio
    async def test_blocks_over_max_attempts(self):
        """Requests exceeding max_attempts should be blocked."""
        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[6, False])

        mock_client = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.services.rate_limiter._redis.get", return_value=mock_client):
            result = await check_rate_limit("rate:test:key", max_attempts=5, window_seconds=60)

        assert result is False

    @pytest.mark.asyncio
    async def test_fail_open_on_connection_error(self):
        """Should allow requests when Redis is unavailable (fail-open)."""
        import redis.asyncio as redis_lib

        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=redis_lib.ConnectionError("Redis down"))

        mock_client = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.services.rate_limiter._redis.get", return_value=mock_client):
            result = await check_rate_limit("rate:test:key", max_attempts=5, window_seconds=60)

        assert result is True

    @pytest.mark.asyncio
    async def test_fail_open_on_timeout(self):
        """Should allow requests on Redis timeout (fail-open)."""
        import redis.asyncio as redis_lib

        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=redis_lib.TimeoutError("Timeout"))

        mock_client = AsyncMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        with patch("src.services.rate_limiter._redis.get", return_value=mock_client):
            result = await check_rate_limit("rate:test:key", max_attempts=5, window_seconds=60)

        assert result is True


class TestResetRateLimit:
    """Tests for the reset_rate_limit function."""

    @pytest.mark.asyncio
    async def test_deletes_key(self):
        """Should delete the rate limit key from Redis."""
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=1)

        with patch("src.services.rate_limiter._redis.get", return_value=mock_client):
            await reset_rate_limit("rate:test:key")

        mock_client.delete.assert_called_once_with("rate:test:key")

    @pytest.mark.asyncio
    async def test_handles_redis_error_gracefully(self):
        """Should not raise on Redis errors during reset."""
        import redis.asyncio as redis_lib

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=redis_lib.ConnectionError("Redis down"))

        with patch("src.services.rate_limiter._redis.get", return_value=mock_client):
            # Should not raise
            await reset_rate_limit("rate:test:key")


class TestLoginRateLimiting:
    """Integration tests verifying rate limiting is wired into login."""

    @pytest.mark.unit
    def test_login_returns_429_when_account_rate_limited(self, client, test_user):
        """Login should return 429 when per-account limit is exceeded."""
        with patch("src.api.routes.auth.check_rate_limit", new_callable=AsyncMock) as mock_check:
            # First call (account check) returns False = rate limited
            mock_check.return_value = False

            response = client.post(
                "/api/v1/auth/login",
                json={"email": test_user["email"], "password": test_user["password"]},
            )

        assert response.status_code == 429
        assert "Too many" in response.json()["detail"]

    @pytest.mark.unit
    def test_login_returns_429_when_ip_rate_limited(self, client, test_user):
        """Login should return 429 when per-IP limit is exceeded."""
        with patch("src.api.routes.auth.check_rate_limit", new_callable=AsyncMock) as mock_check:
            # First call (account check) passes, second call (IP) fails
            mock_check.side_effect = [True, False]

            response = client.post(
                "/api/v1/auth/login",
                json={"email": test_user["email"], "password": test_user["password"]},
            )

        assert response.status_code == 429

    @pytest.mark.unit
    def test_login_succeeds_when_within_rate_limit(self, client, test_user):
        """Login should succeed normally when within rate limits."""
        with patch("src.api.routes.auth.check_rate_limit", new_callable=AsyncMock, return_value=True):
            with patch("src.api.routes.auth.reset_rate_limit", new_callable=AsyncMock):
                response = client.post(
                    "/api/v1/auth/login",
                    json={"email": test_user["email"], "password": test_user["password"]},
                )

        assert response.status_code == 200

    @pytest.mark.unit
    def test_login_resets_counter_on_success(self, client, test_user):
        """Successful login should reset the per-account rate limit counter."""
        with patch("src.api.routes.auth.check_rate_limit", new_callable=AsyncMock, return_value=True):
            with patch("src.api.routes.auth.reset_rate_limit", new_callable=AsyncMock) as mock_reset:
                response = client.post(
                    "/api/v1/auth/login",
                    json={"email": test_user["email"], "password": test_user["password"]},
                )

        assert response.status_code == 200
        mock_reset.assert_called_once()
        # Should reset the account key, not the IP key
        call_args = mock_reset.call_args[0][0]
        assert call_args.startswith("rate:auth:account:")
