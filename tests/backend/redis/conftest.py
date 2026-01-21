"""
Pytest configuration and fixtures for Redis Streams tests.

Provides fixtures for:
- Redis connection and cleanup
- RedisEventHub (Streams) instances with async generator helpers
- EventingTracer factory for consistent tracer setup
- Mock event sinks with assertion helpers
- Test session ID generation
- Stream management utilities

IMPORTANT: This test suite requires redis package to be installed.
Tests will fail fast if dependencies are missing.

Architecture Note:
The RedisEventHub now uses Redis Streams (XADD/XREAD) instead of Pub/Sub.
Key differences:
- Events persist in the stream (no more race conditions)
- subscribe() returns an async generator, not a queue
- Consumers can start from any point in the stream
- No need for overlap buffer (streams are durable)
"""
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Callable, Any, AsyncIterator
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
    """
    Redis connection URL for tests (uses DB 1, not DB 0 from config).

    Supports environment variable override for local testing:
        REDIS_TEST_URL=redis://localhost:46379/1 pytest tests/backend/redis/

    When running locally (outside Docker), use localhost:46379.
    When running in Docker, use redis:6379 (from config).
    """
    import os

    # Check for environment variable override (for local testing)
    env_url = os.environ.get("REDIS_TEST_URL")
    if env_url:
        return env_url

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
    conn = redis.Redis.from_url(redis_url, decode_responses=True)

    # Test connection - fail fast if Redis not available
    try:
        await conn.ping()
    except Exception as e:
        await conn.close()
        raise RuntimeError(
            f"Redis connection failed. Ensure Redis is running with './run.sh build'. "
            f"Error: {e}"
        ) from e

    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def redis_event_hub(redis_url: str) -> AsyncGenerator[RedisEventHub, None]:
    """
    Provide a RedisEventHub (Streams) instance for tests.

    Uses shorter timeouts for faster test execution.
    Fails fast if Redis is not reachable or misconfigured.
    """
    hub = RedisEventHub(
        redis_url=redis_url,
        stream_maxlen=1000,  # Smaller for tests
        block_ms=2000,  # 2 second timeout for faster tests
        stream_ttl_seconds=300,  # 5 minute TTL for tests
    )

    # Test connection - fail fast if Redis not available
    try:
        pool = await hub._ensure_pool()
        async with redis.Redis(connection_pool=pool) as conn:
            await conn.ping()
    except Exception as e:
        await hub.close()
        raise RuntimeError(
            f"RedisEventHub initialization failed. Ensure Redis is running with './run.sh build'. "
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
# Stream Helper Fixtures
# =============================================================================

@dataclass
class StreamContext:
    """Context for managing stream subscription lifecycle in tests."""
    session_id: str
    hub: RedisEventHub
    events: list[dict[str, Any]]
    _task: asyncio.Task | None = None
    _stop_event: asyncio.Event | None = None

    async def start_collecting(self, from_sequence: int | None = None) -> None:
        """Start collecting events from the stream in background."""
        self._stop_event = asyncio.Event()
        self.events = []

        async def collect():
            try:
                async for event in self.hub.subscribe(self.session_id, from_sequence=from_sequence):
                    if self._stop_event.is_set():
                        break
                    self.events.append(event)
            except asyncio.CancelledError:
                pass

        self._task = asyncio.create_task(collect())
        await asyncio.sleep(0.1)  # Let collector start

    async def stop_collecting(self) -> list[dict[str, Any]]:
        """Stop collecting and return all collected events."""
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return self.events

    async def wait_for_events(self, count: int, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Wait until we have at least `count` events or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout
        while len(self.events) < count:
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.1)
        return self.events


@pytest_asyncio.fixture
async def stream_context_factory(
    redis_event_hub: RedisEventHub,
) -> AsyncGenerator[Callable[[str], StreamContext], None]:
    """
    Factory fixture for creating StreamContext instances.

    Usage:
        ctx = stream_context_factory(session_id)
        await ctx.start_collecting()
        # ... publish events ...
        events = await ctx.stop_collecting()
    """
    contexts: list[StreamContext] = []

    def create_context(session_id: str) -> StreamContext:
        ctx = StreamContext(
            session_id=session_id,
            hub=redis_event_hub,
            events=[],
        )
        contexts.append(ctx)
        return ctx

    yield create_context

    # Cleanup all created contexts
    for ctx in contexts:
        await ctx.stop_collecting()


# =============================================================================
# Tracer Factory Fixture
# =============================================================================

@dataclass
class TracerContext:
    """Context for managing tracer lifecycle in tests."""
    tracer: EventingTracer
    event_queue: EventSinkQueue
    session_id: str
    hub: RedisEventHub


@pytest_asyncio.fixture
async def tracer_factory(
    redis_event_hub: RedisEventHub,
    mock_event_sink: AsyncMock,
) -> AsyncGenerator[Callable[[str], TracerContext], None]:
    """
    Factory fixture for creating EventingTracer instances with Redis Streams integration.

    Usage:
        ctx = tracer_factory(session_id)
        ctx.tracer.emit_event("test", {"data": "value"})
        # Read events from stream using hub.subscribe() or get_events_after()
    """
    contexts: list[TracerContext] = []

    def create_tracer(session_id: str) -> TracerContext:
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
            hub=redis_event_hub,
        )
        contexts.append(ctx)
        return ctx

    yield create_tracer

    # Cleanup - delete test streams
    for ctx in contexts:
        await redis_event_hub.delete_stream(ctx.session_id)


# =============================================================================
# Event Helper Fixtures
# =============================================================================

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

async def collect_events_from_stream(
    hub: RedisEventHub,
    session_id: str,
    timeout: float = 2.0,
    max_events: int = 100,
    from_sequence: int | None = None,
) -> list[dict[str, Any]]:
    """
    Collect events from a stream with timeout.

    Args:
        hub: The RedisEventHub instance
        session_id: The session ID
        timeout: Maximum time to collect
        max_events: Stop after collecting this many events
        from_sequence: Start from events after this sequence

    Returns:
        List of collected events
    """
    events = []
    stop_event = asyncio.Event()

    async def collect():
        try:
            async for event in hub.subscribe(session_id, from_sequence=from_sequence):
                if stop_event.is_set():
                    break
                events.append(event)
                if len(events) >= max_events:
                    break
                # Skip heartbeats for counting
                if event.get("type") != "heartbeat" and len([e for e in events if e.get("type") != "heartbeat"]) >= max_events:
                    break
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(collect())

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    return events


async def wait_for_stream_event(
    hub: RedisEventHub,
    session_id: str,
    timeout: float = 2.0,
    event_type: str | None = None,
    from_sequence: int | None = None,
) -> dict[str, Any]:
    """
    Wait for a specific event type from a stream.

    Args:
        hub: The RedisEventHub instance
        session_id: The session ID
        timeout: Maximum time to wait
        event_type: If specified, skip events until matching type found
        from_sequence: Start from events after this sequence

    Returns:
        The matching event

    Raises:
        asyncio.TimeoutError: If no matching event within timeout
    """
    deadline = asyncio.get_event_loop().time() + timeout

    async for event in hub.subscribe(session_id, from_sequence=from_sequence):
        if asyncio.get_event_loop().time() > deadline:
            raise asyncio.TimeoutError(f"No event of type '{event_type}' within {timeout}s")

        if event_type is None or event.get("type") == event_type:
            return event

        # Skip heartbeats if looking for specific type
        if event.get("type") == "heartbeat":
            continue

    raise asyncio.TimeoutError(f"Stream ended without finding event of type '{event_type}'")


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config: pytest.Config) -> None:
    """Register Redis-specific markers."""
    config.addinivalue_line(
        "markers",
        "redis: marks tests as requiring Redis (fails fast if Redis unavailable)"
    )
