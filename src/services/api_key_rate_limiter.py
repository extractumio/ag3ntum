"""
Redis-based per-API-key rate limiter.

Uses a Redis sliding window counter (INCR + EXPIRE) to enforce per-key
request limits within a 60-second window. Fail-open: if Redis is
unavailable, the request is allowed and a warning is logged.
"""
from __future__ import annotations

import logging

from .redis_client import LazyRedisClient

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60
_KEY_PREFIX = "api_key_rate"

_redis = LazyRedisClient()


async def check_api_key_rate_limit(key_id: str, limit: int = 60) -> bool:
    """
    Check whether an API key is within its request-rate limit.

    Increments the sliding-window counter for the key and sets a 60-second
    expiry on first use. Returns True if the request is within the limit,
    False if the limit has been exceeded.

    Args:
        key_id: The API key's database ID (used to build the Redis key).
        limit:  Maximum requests allowed per 60-second window.
                Defaults to 60; pass key.rate_limit_per_minute at call site.

    Returns:
        True if within limit (request allowed), False if over limit (block).
    """
    redis_key = f"{_KEY_PREFIX}:{key_id}"
    try:
        client = _redis.get()
        pipe = client.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, _WINDOW_SECONDS, nx=True)
        results = await pipe.execute()
        count = results[0]
        return count <= limit
    except Exception as e:
        # Fail-open: rate limiting is defense-in-depth; key validation
        # still runs. Log and allow so Redis downtime does not lock users out.
        logger.warning("API key rate limiter Redis error (fail-open): %s", e)
        return True
