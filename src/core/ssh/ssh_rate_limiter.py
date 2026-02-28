"""SSH command rate limiter.

Sliding-window counter per (session_id, profile_name) to enforce
rate_limit_commands_per_minute from the security config.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class SSHRateLimiter:
    """Sliding-window rate limiter for SSH commands."""

    def __init__(self, max_per_minute: int = 30) -> None:
        self._max = max_per_minute
        self._windows: dict[str, list[float]] = {}

    def check(self, session_id: str, profile_name: str) -> bool:
        """Return True if the request is within rate limits.

        Returns False if rate limit exceeded.
        """
        key = f"{session_id}:{profile_name}"
        now = time.monotonic()
        window = self._windows.setdefault(key, [])
        # Remove entries older than 60 seconds
        window[:] = [t for t in window if now - t < 60]
        if len(window) >= self._max:
            logger.warning(
                "SSHRateLimiter: Rate limit exceeded for %s "
                "(%d/%d per minute)",
                key, len(window), self._max,
            )
            return False
        window.append(now)
        return True

    def reset(self, session_id: str, profile_name: str) -> None:
        """Reset rate limit window for a session/profile."""
        key = f"{session_id}:{profile_name}"
        self._windows.pop(key, None)

    def reset_session(self, session_id: str) -> None:
        """Reset all rate limit windows for a session."""
        prefix = f"{session_id}:"
        keys = [k for k in self._windows if k.startswith(prefix)]
        for k in keys:
            del self._windows[k]
