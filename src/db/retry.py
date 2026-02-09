"""
Database retry decorator for transient failure handling.

Shared by session_service.py and event_service.py.
"""
import asyncio
import logging
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from sqlalchemy.exc import IntegrityError, OperationalError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 0.1
DEFAULT_RETRY_BACKOFF_MULTIPLIER = 2.0


def with_db_retry(
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
    backoff_multiplier: float = DEFAULT_RETRY_BACKOFF_MULTIPLIER,
) -> Callable:
    """
    Decorator for retrying database operations on transient failures.

    Retries on OperationalError (connection issues, locks, etc.)
    but not on IntegrityError (constraint violations).
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_error: Optional[Exception] = None
            delay = retry_delay

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except OperationalError as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Database operation failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                            f"Retrying in {delay:.3f}s..."
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_multiplier
                    else:
                        logger.error(
                            f"Database operation failed after {max_retries + 1} attempts: {e}"
                        )
                except IntegrityError:
                    # Don't retry integrity errors - they won't succeed
                    raise

            assert last_error is not None  # loop always runs at least once
            raise last_error
        return wrapper
    return decorator
