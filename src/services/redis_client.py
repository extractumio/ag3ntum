"""
Shared lazy-initialized Redis client for lightweight services.

Used by connection_token and rate_limiter modules that need a simple
module-level Redis client without constructor injection.
"""
from __future__ import annotations

from typing import Optional

import redis.asyncio as redis

_DEFAULT_REDIS_URL = "redis://redis:6379/0"


class LazyRedisClient:
    """Lazy-initialized async Redis client with cleanup support."""

    def __init__(self) -> None:
        self._client: Optional[redis.Redis] = None

    def get(self) -> redis.Redis:
        """Get or create the Redis client."""
        if self._client is None:
            self._client = redis.from_url(
                _DEFAULT_REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the Redis client if it was initialized."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
