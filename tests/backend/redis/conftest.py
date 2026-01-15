"""
Pytest configuration and fixtures for Redis SSE tests.

Provides fixtures for:
- Redis connection and cleanup
- RedisEventHub instances with subscription helpers
- EventingTracer factory for consistent tracer setup
- Mock event sinks with assertion helpers
- Test session ID generation
- Event publishing utilities

IMPORTANT: This test suite requires redis package to be installed.
Tests will fail fast if dependencies are missing.
"""
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Callable, Any
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
from src.services.redis_event_hub import RedisEventHub, EventSinkQueue  # noqa: E402
from src.core.tracer import EventingTracer, NullTracer  # noqa: E402


# =============================================================================
# Configuration Fixtures
# =============================================================================

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


# =============================================================================
# Redis Connection Fixtures
# =============================================================================

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


# =============================================================================
# Session and Event Fixtures
# =============================================================================

@pytest.fixture
def test_session_id() -> str:
    """Generate a unique test session ID."""
    import uuid
    return f"test_session_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def mock_event_sink() -> AsyncMock:
    """Mock event sink for testing (simulates SQLite persistence)."""
    return AsyncMock()


# =============================================================================
# Tracer Factory Fixture
# =============================================================================

@dataclass
class TracerContext:
    """Context for managing tracer lifecycle in tests."""
    tracer: EventingTracer
    event_queue: EventSinkQueue
    session_id: str
    redis_queue: asyncio.Queue


@pytest_asyncio.fixture
async def tracer_factory(
    redis_event_hub: RedisEventHub,
    mock_event_sink: AsyncMock,
) -> AsyncGenerator[Callable[[str], Any], None]:
    """
    Factory fixture for creating EventingTracer instances with Redis integration.

    Usage:
        ctx = await tracer_factory(session_id)
        ctx.tracer.emit_event("test", {"data": "value"})
        event = await ctx.redis_queue.get()
    """
    contexts: list[TracerContext] = []

    async def create_tracer(session_id: str) -> TracerContext:
        # Subscribe to Redis first
        redis_queue = await redis_event_hub.subscribe(session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Create tracer with Redis integration
        event_queue = EventSinkQueue(redis_event_hub, session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=session_id,
        )

        ctx = TracerContext(
            tracer=tracer,
            event_queue=event_queue,
            session_id=session_id,
            redis_queue=redis_queue,
        )
        contexts.append(ctx)
        return ctx

    yield create_tracer

    # Cleanup all created tracers
    for ctx in contexts:
        await redis_event_hub.unsubscribe(ctx.session_id, ctx.redis_queue)


# =============================================================================
# Event Helper Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def subscribed_queue(
    redis_event_hub: RedisEventHub,
    test_session_id: str,
) -> AsyncGenerator[asyncio.Queue, None]:
    """
    Provide a Redis-subscribed queue for a session.

    Automatically cleans up subscription after test.
    """
    queue = await redis_event_hub.subscribe(test_session_id)
    await asyncio.sleep(0.1)  # Let listener start

    yield queue

    await redis_event_hub.unsubscribe(test_session_id, queue)


@pytest.fixture
def event_factory() -> Callable[..., dict[str, Any]]:
    """Factory for creating test events with default fields."""
    _sequence = [0]  # Mutable to track sequence across calls

    def create_event(
        event_type: str = "test_event",
        data: dict[str, Any] | None = None,
        session_id: str | None = None,
        sequence: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        _sequence[0] += 1
        return {
            "type": event_type,
            "data": data or {},
            "session_id": session_id or "test_session",
            "sequence": sequence if sequence is not None else _sequence[0],
            **extra,
        }

    return create_event


# =============================================================================
# Assertion Helpers
# =============================================================================

async def wait_for_event(
    queue: asyncio.Queue,
    timeout: float = 1.0,
    event_type: str | None = None,
) -> dict[str, Any]:
    """
    Wait for an event from a queue with optional type filtering.

    Args:
        queue: The asyncio Queue to wait on
        timeout: Maximum time to wait in seconds
        event_type: If specified, skip events until matching type found

    Returns:
        The event dict

    Raises:
        asyncio.TimeoutError: If no matching event within timeout
    """
    deadline = asyncio.get_event_loop().time() + timeout

    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"No event received within {timeout}s")

        try:
            event = await asyncio.wait_for(queue.get(), timeout=remaining)
            if event_type is None or event.get("type") == event_type:
                return event
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(f"No event of type '{event_type}' within {timeout}s")


async def drain_queue(queue: asyncio.Queue) -> list[dict[str, Any]]:
    """Drain all events from a queue without blocking."""
    events = []
    while not queue.empty():
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return events


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config: pytest.Config) -> None:
    """Register Redis-specific markers."""
    config.addinivalue_line(
        "markers",
        "redis: marks tests as requiring Redis (fails fast if Redis unavailable)"
    )
