"""
Redis-based rate limiter for authentication endpoints.

Uses Redis INCR with EXPIRE to track failed attempts per key.
Supports per-account and per-IP rate limiting.
"""
from __future__ import annotations

import logging

from .redis_client import LazyRedisClient

logger = logging.getLogger(__name__)

_redis = LazyRedisClient()


async def close_redis_client() -> None:
    """Close the module-level Redis client if it was initialized."""
    await _redis.close()


async def check_rate_limit(
    key: str,
    max_attempts: int,
    window_seconds: int,
) -> bool:
    """
    Check if a rate limit key has exceeded its allowed attempts.

    Increments the counter for the key and sets expiry on first use.
    Returns True if the request is allowed, False if rate-limited.

    Args:
        key: Redis key for the rate limit (e.g., "rate:auth:ip:1.2.3.4")
        max_attempts: Maximum allowed attempts within the window.
        window_seconds: Time window in seconds.

    Returns:
        True if within limit (allowed), False if over limit (blocked).
    """
    try:
        client = _redis.get()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        results = await pipe.execute()
        count = results[0]
        return count <= max_attempts
    except Exception as e:
        # Fail-open: if Redis is unavailable, allow the request.
        # Authentication still validates credentials — rate limiting is
        # defense-in-depth, not the sole control.
        logger.warning("Rate limiter Redis error (fail-open): %s", e)
        return True


async def reset_rate_limit(key: str) -> None:
    """
    Reset (delete) a rate limit key.

    Used after a successful login to clear the failed-attempt counter.

    Args:
        key: Redis key to reset.
    """
    try:
        client = _redis.get()
        await client.delete(key)
    except Exception as e:
        logger.warning("Rate limiter reset error: %s", e)
