"""
Short-lived, single-use connection tokens for SSE endpoints.

SSE connections via EventSource cannot set custom headers, so tokens must be
passed as query parameters. To avoid long-lived JWTs appearing in server logs,
this module provides short-lived connection tokens that are:

1. Exchanged from a valid JWT via POST /auth/connection-token
2. Stored in Redis with a 30-second TTL
3. Single-use (deleted after first validation)
4. Bound to the user_id from the original JWT

Usage:
    # Create a token
    token = await create_connection_token(user_id)

    # Validate and consume (single-use)
    user_id = await validate_connection_token(token)
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

import redis.asyncio as redis

from .redis_client import LazyRedisClient

logger = logging.getLogger(__name__)

_redis = LazyRedisClient()

# Token TTL in seconds
CONNECTION_TOKEN_TTL = 30

# Redis key prefix
_KEY_PREFIX = "conn_token:"


async def close_redis_client() -> None:
    """Close the module-level Redis client if it was initialized."""
    await _redis.close()


async def create_connection_token(user_id: str) -> str:
    """Create a short-lived, single-use connection token for SSE.

    Args:
        user_id: The authenticated user's ID.

    Returns:
        A random connection token string.

    Raises:
        RuntimeError: If Redis is unavailable.
    """
    token = secrets.token_urlsafe(32)
    key = f"{_KEY_PREFIX}{token}"
    try:
        client = _redis.get()
        await client.set(key, user_id, ex=CONNECTION_TOKEN_TTL)
        return token
    except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
        logger.error("Failed to create connection token: %s", e)
        raise RuntimeError("Could not create connection token") from e


async def validate_connection_token(token: str) -> Optional[str]:
    """Validate and consume a connection token (single-use).

    Returns the user_id if the token is valid, None otherwise.
    The token is deleted after successful validation.

    Args:
        token: The connection token to validate.

    Returns:
        The user_id if valid, None if expired/invalid/already used.
    """
    key = f"{_KEY_PREFIX}{token}"
    try:
        client = _redis.get()
        # GETDEL: atomically get and delete (single-use)
        user_id = await client.getdel(key)
        return user_id
    except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
        logger.warning("Connection token validation error: %s", e)
        return None
