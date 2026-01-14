"""
Pytest configuration and fixtures for Redis SSE tests.

Provides fixtures for:
- Redis connection and cleanup
- RedisEventHub instances
- Mock event sinks
- Test session IDs

IMPORTANT: This test suite requires redis package to be installed.
Tests will fail fast if dependencies are missing.
"""
import asyncio
import sys
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio
import yaml

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import redis - fail fast if not available
import redis.asyncio as redis

# Import project modules - fail fast if not available
from src.services.redis_event_hub import RedisEventHub  # noqa: E402


@pytest.fixture(scope="session")
def redis_url() -> str:
    """Redis connection URL for tests (uses DB 1, not DB 0 from config)."""
    config_path = PROJECT_ROOT / "config" / "api.yaml"
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        base_url = config.get("redis", {}).get("url")
        if not base_url:
            raise ValueError("redis.url not found in config")
    except Exception as e:
        raise RuntimeError(
            f"Failed to load Redis URL from {config_path}: {e}\n"
            f"Ensure config/api.yaml exists with redis.url configured."
        ) from e

    # Use DB 1 for tests instead of DB 0 (production)
    parsed = urlparse(base_url)
    test_url = urlunparse(parsed._replace(path="/1"))
    return test_url


@pytest_asyncio.fixture
async def redis_connection(redis_url: str) -> AsyncGenerator[redis.Redis, None]:
    """
    Provide a Redis connection for tests.

    Fails fast if Redis is not reachable or misconfigured.
    """
    conn = redis.Redis.from_url(redis_url, decode_responses=False)

    # Test connection - fail fast if Redis not available
    try:
        await conn.ping()
    except Exception as e:
        await conn.close()
        raise RuntimeError(
            f"Redis connection failed. Ensure Redis is running with './deploy.sh build'. "
            f"Error: {e}"
        ) from e

    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def redis_event_hub(redis_url: str) -> AsyncGenerator[RedisEventHub, None]:
    """
    Provide a RedisEventHub instance for tests.

    Fails fast if Redis is not reachable or misconfigured.
    """
    hub = RedisEventHub(redis_url=redis_url, max_queue_size=100)

    # Test connection - fail fast if Redis not available
    try:
        pool = await hub._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            await conn.ping()
    except Exception as e:
        await hub.close()
        raise RuntimeError(
            f"RedisEventHub initialization failed. Ensure Redis is running with './deploy.sh build'. "
            f"Error: {e}"
        ) from e

    try:
        yield hub
    finally:
        await hub.close()


@pytest_asyncio.fixture
async def clean_redis(redis_connection: redis.Redis) -> AsyncGenerator[redis.Redis, None]:
    """
    Provide a clean Redis instance (flushes test DB before and after).

    Fails fast if Redis is not available.
    """
    # Flush before test
    await redis_connection.flushdb()
    yield redis_connection
    # Flush after test
    await redis_connection.flushdb()


@pytest.fixture
def test_session_id() -> str:
    """Generate a unique test session ID."""
    import uuid
    return f"test_session_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def mock_event_sink() -> AsyncMock:
    """Mock event sink for testing (simulates SQLite persistence)."""
    return AsyncMock()


def pytest_configure(config: pytest.Config) -> None:
    """Register Redis-specific markers."""
    config.addinivalue_line(
        "markers",
        "redis: marks tests as requiring Redis (fails fast if Redis unavailable)"
    )
