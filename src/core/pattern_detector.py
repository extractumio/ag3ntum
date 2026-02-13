"""
Pattern detector for identifying unproductive agent loops.

Tracks tool call sequences, TodoWrite-only patterns, and silent turns
to detect when an agent is stuck and not making progress.
"""
import hashlib
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_MAX_REPETITIVE_CALLS = 5
DEFAULT_MAX_SILENT_TURNS = 5
DEFAULT_MAX_TODOWRITE_ONLY_TURNS = 3


class PatternDetector:
    """
    Detects unproductive agent behavior patterns.

    Tracks three classes of stuck behavior:
    1. Repetitive tool calls - same tool called N times consecutively
    2. TodoWrite-only turns - agent only updates task lists without real work
    3. Silent turns - no text output and no tool calls for N turns

    When a pattern is detected, the detector sets tripped=True with a message.
    """

    def __init__(
        self,
        max_repetitive_calls: int = DEFAULT_MAX_REPETITIVE_CALLS,
        max_silent_turns: int = DEFAULT_MAX_SILENT_TURNS,
        max_todowrite_only_turns: int = DEFAULT_MAX_TODOWRITE_ONLY_TURNS,
    ) -> None:
        self._tripped: bool = False
        self._message: str = ""

        # Repetitive tool call tracking
        self._tool_call_sequence: list[tuple[str, str]] = []
        self._max_repetitive_calls: int = max_repetitive_calls

        # Silent turn tracking
        self._turns_without_meaningful_output: int = 0
        self._max_silent_turns: int = max_silent_turns
        self._current_turn_has_output: bool = False
        self._current_turn_has_tool_call: bool = False

        # TodoWrite-only turn tracking
        self._consecutive_todowrite_only_turns: int = 0
        self._max_todowrite_only_turns: int = max_todowrite_only_turns

    @property
    def tripped(self) -> bool:
        """Return True if a stuck pattern has been detected."""
        return self._tripped

    @property
    def message(self) -> str:
        """Return the detection message if tripped."""
        return self._message

    def trip(self, message: str) -> None:
        """Manually trip the detector with a message."""
        self._tripped = True
        self._message = message
        logger.warning(message)

    def configure(
        self,
        max_repetitive_calls: Optional[int] = None,
        max_silent_turns: Optional[int] = None,
        max_todowrite_only_turns: Optional[int] = None,
    ) -> None:
        """Update detection thresholds."""
        if max_repetitive_calls is not None:
            self._max_repetitive_calls = max_repetitive_calls
        if max_silent_turns is not None:
            self._max_silent_turns = max_silent_turns
        if max_todowrite_only_turns is not None:
            self._max_todowrite_only_turns = max_todowrite_only_turns

    def track_tool_call(self, tool_name: str, tool_input: Any) -> None:
        """
        Track a tool call for unproductive loop detection.

        Detects when the same tool is called repeatedly, which often
        indicates the model is stuck in a loop (e.g., calling TodoWrite
        repeatedly without making progress).

        Args:
            tool_name: Name of the tool being called.
            tool_input: The input arguments to the tool.
        """
        # Create a signature from the tool input
        try:
            input_str = json.dumps(tool_input, sort_keys=True, default=str)
        except (TypeError, ValueError):
            input_str = str(tool_input)

        input_hash = hashlib.md5(input_str.encode()).hexdigest()[:8]
        self._tool_call_sequence.append((tool_name, input_hash))

        # Keep sequence bounded to prevent memory growth
        if len(self._tool_call_sequence) > 50:
            self._tool_call_sequence = self._tool_call_sequence[-50:]

        # Check for repetitive pattern: same tool called N times consecutively
        if len(self._tool_call_sequence) >= self._max_repetitive_calls:
            recent = self._tool_call_sequence[-self._max_repetitive_calls:]
            recent_tools = [t[0] for t in recent]

            if len(set(recent_tools)) == 1:  # All same tool
                # Don't trip for subagent tools (Task) as they may legitimately be called multiple times
                if tool_name not in ("Task",):
                    # Check if inputs are also identical — parallel batch work
                    # (same tool, different inputs) should not trip the detector
                    recent_hashes = [t[1] for t in recent]
                    if len(set(recent_hashes)) <= 1:
                        # Genuine unproductive loop: same tool AND same input repeated
                        self.trip(
                            f"Unproductive loop detected: '{tool_name}' called "
                            f"{self._max_repetitive_calls} times consecutively with "
                            f"identical inputs. This suggests the agent is stuck. "
                            f"Consider using mcp__ag3ntum__AskUserQuestion to get user guidance."
                        )

    def check_todowrite_only_pattern(self, tool_name: str) -> None:
        """
        Check for TodoWrite-only turns.

        Detects when the agent is only calling TodoWrite without taking
        real actions, which indicates it's stuck updating task lists
        instead of actually working.

        Args:
            tool_name: Name of the tool that was just called.
        """
        if tool_name == "TodoWrite":
            # Check if recent sequence is TodoWrite-only
            if len(self._tool_call_sequence) >= 2:
                recent = self._tool_call_sequence[-3:]  # Look at last 3 calls
                if all(t[0] == "TodoWrite" for t in recent):
                    self._consecutive_todowrite_only_turns += 1

                    if self._consecutive_todowrite_only_turns >= self._max_todowrite_only_turns:
                        # Emit warning via logging (not circuit breaker - just a warning)
                        warning_msg = (
                            "Warning: Agent has called TodoWrite "
                            f"{self._consecutive_todowrite_only_turns} times "
                            "without taking other actions. If blocked, consider "
                            "asking the user for guidance with mcp__ag3ntum__AskUserQuestion."
                        )
                        logger.warning(warning_msg)
                        return  # Return the message for callers that want it
        else:
            # Non-TodoWrite tool resets the counter
            self._consecutive_todowrite_only_turns = 0

    def on_turn_start(self) -> None:
        """Called at the start of each turn to reset per-turn tracking."""
        self._current_turn_has_output = False
        self._current_turn_has_tool_call = False

    def on_meaningful_output(self) -> None:
        """Called when the agent produces meaningful text output."""
        self._current_turn_has_output = True
        self._turns_without_meaningful_output = 0

    def on_turn_end(self) -> None:
        """Called at the end of each turn to check for no-output pattern."""
        # Only count as "silent" if there's neither text output NOR tool calls.
        # An agent making tool calls is making progress, even without chat messages.
        if self._current_turn_has_output or self._current_turn_has_tool_call:
            # Productive turn - reset counter
            self._turns_without_meaningful_output = 0
        else:
            # Truly silent turn - no output and no tool calls
            self._turns_without_meaningful_output += 1

            if self._turns_without_meaningful_output >= self._max_silent_turns:
                self.trip(
                    f"No activity detected: Agent has produced no text output and made "
                    f"no tool calls for {self._turns_without_meaningful_output} consecutive "
                    f"turns. This suggests the agent is stuck."
                )

    @property
    def current_turn_has_tool_call(self) -> bool:
        """Whether the current turn has had a tool call."""
        return self._current_turn_has_tool_call

    @current_turn_has_tool_call.setter
    def current_turn_has_tool_call(self, value: bool) -> None:
        self._current_turn_has_tool_call = value
