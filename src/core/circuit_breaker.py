"""
Circuit breaker for detecting repeated identical tool failures.

Tracks consecutive tool failures with the same error signature and trips
when a threshold is exceeded, preventing infinite retry loops.
"""
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default threshold for consecutive identical failures
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5


class CircuitBreaker:
    """
    Tracks consecutive identical tool failures and trips when threshold is exceeded.

    Each tool is tracked independently. When a tool fails with the same error
    signature N times in a row, the circuit breaker trips and the agent is stopped.
    A successful tool execution resets the counter for that tool.
    """

    def __init__(self, max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES) -> None:
        self._tool_failure_tracker: dict[str, tuple[str, int]] = {}
        self._tripped: bool = False
        self._message: str = ""
        self._max_consecutive_failures: int = max_consecutive_failures

    @property
    def tripped(self) -> bool:
        """Return True if the circuit breaker has been tripped."""
        return self._tripped

    @property
    def message(self) -> str:
        """Return the circuit breaker error message if tripped."""
        return self._message

    def trip(self, message: str) -> None:
        """Manually trip the circuit breaker with a message."""
        self._tripped = True
        self._message = message
        logger.warning(message)

    def configure(self, max_consecutive_failures: Optional[int] = None) -> None:
        """Update the max consecutive failures threshold."""
        if max_consecutive_failures is not None:
            self._max_consecutive_failures = max_consecutive_failures

    @staticmethod
    def extract_error_signature(error_content: Any) -> str:
        """
        Extract a normalized error signature from tool result content.

        Creates a fingerprint of the error type to detect repeated identical
        failures (e.g., same validation error, same missing parameter).

        Args:
            error_content: The tool result content (may be string or list).

        Returns:
            A normalized error signature string.
        """
        if isinstance(error_content, str):
            text = error_content
        elif isinstance(error_content, list):
            parts = []
            for block in error_content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
                elif isinstance(block, str):
                    parts.append(block)
            text = " ".join(parts)
        else:
            text = str(error_content)

        text = text[:500]

        # Remove UUIDs, timestamps, and other variable parts
        text = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '<UUID>', text)
        text = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '<TIMESTAMP>', text)
        text = re.sub(r'tool_use_[a-zA-Z0-9_]+', '<TOOL_ID>', text)

        return text[:200].strip()

    def track_failure(self, tool_name: str, error_content: Any) -> None:
        """
        Track a tool failure and check if circuit breaker should trip.

        Args:
            tool_name: Name of the failed tool.
            error_content: The error content from the tool result.
        """
        error_sig = self.extract_error_signature(error_content)

        if tool_name in self._tool_failure_tracker:
            prev_sig, prev_count = self._tool_failure_tracker[tool_name]
            if prev_sig == error_sig:
                new_count = prev_count + 1
                self._tool_failure_tracker[tool_name] = (error_sig, new_count)

                if new_count >= self._max_consecutive_failures:
                    self.trip(
                        f"Circuit breaker tripped: Tool '{tool_name}' failed "
                        f"{new_count} consecutive times with the same error. "
                        f"Error pattern: {error_sig[:100]}..."
                    )
            else:
                self._tool_failure_tracker[tool_name] = (error_sig, 1)
        else:
            self._tool_failure_tracker[tool_name] = (error_sig, 1)

    def reset_tool(self, tool_name: str) -> None:
        """
        Reset the failure tracker for a tool after a successful execution.

        Args:
            tool_name: Name of the tool that succeeded.
        """
        if tool_name in self._tool_failure_tracker:
            del self._tool_failure_tracker[tool_name]
