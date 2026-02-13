"""
Tests for PatternDetector — unproductive loop detection.

Covers:
- Repetitive tool call detection (same tool + same input = trip)
- Parallel batch detection (same tool + different inputs = no trip)
- Mixed tool sequences
- Threshold configuration
- Silent turn detection
- TodoWrite-only pattern detection
- Reset behavior
"""
import pytest

from src.core.pattern_detector import PatternDetector


class TestRepetitiveToolCalls:
    """Tests for repetitive tool call loop detection."""

    @pytest.mark.unit
    def test_same_tool_same_input_trips(self) -> None:
        """Same tool with identical input N times should trip the detector."""
        pd = PatternDetector(max_repetitive_calls=3)

        pd.track_tool_call("LS", {"path": "/workspace"})
        assert not pd.tripped

        pd.track_tool_call("LS", {"path": "/workspace"})
        assert not pd.tripped

        pd.track_tool_call("LS", {"path": "/workspace"})
        assert pd.tripped
        assert "Unproductive loop" in pd.message
        assert "'LS'" in pd.message

    @pytest.mark.unit
    def test_same_tool_different_inputs_does_not_trip(self) -> None:
        """Same tool with different inputs (parallel batch) should NOT trip."""
        pd = PatternDetector(max_repetitive_calls=3)

        pd.track_tool_call("LS", {"path": "/workspace/dir1"})
        pd.track_tool_call("LS", {"path": "/workspace/dir2"})
        pd.track_tool_call("LS", {"path": "/workspace/dir3"})
        assert not pd.tripped

    @pytest.mark.unit
    def test_same_tool_mixed_inputs_does_not_trip(self) -> None:
        """Same tool with mix of same and different inputs should NOT trip."""
        pd = PatternDetector(max_repetitive_calls=5)

        pd.track_tool_call("Read", {"path": "/a"})
        pd.track_tool_call("Read", {"path": "/a"})
        pd.track_tool_call("Read", {"path": "/b"})
        pd.track_tool_call("Read", {"path": "/a"})
        pd.track_tool_call("Read", {"path": "/c"})
        assert not pd.tripped

    @pytest.mark.unit
    def test_mixed_tools_does_not_trip(self) -> None:
        """Different tools should NOT trip even with many calls."""
        pd = PatternDetector(max_repetitive_calls=3)

        pd.track_tool_call("LS", {"path": "/workspace"})
        pd.track_tool_call("Read", {"path": "/workspace/file.txt"})
        pd.track_tool_call("LS", {"path": "/workspace"})
        pd.track_tool_call("Grep", {"pattern": "TODO"})
        pd.track_tool_call("Read", {"path": "/workspace/file.txt"})
        assert not pd.tripped

    @pytest.mark.unit
    def test_below_threshold_does_not_trip(self) -> None:
        """Fewer repetitions than threshold should NOT trip."""
        pd = PatternDetector(max_repetitive_calls=5)

        for _ in range(4):
            pd.track_tool_call("LS", {"path": "/workspace"})

        assert not pd.tripped

    @pytest.mark.unit
    def test_threshold_from_config_respected(self) -> None:
        """Custom threshold via configure() should be respected."""
        pd = PatternDetector(max_repetitive_calls=10)
        pd.configure(max_repetitive_calls=2)

        pd.track_tool_call("LS", {"path": "/same"})
        pd.track_tool_call("LS", {"path": "/same"})
        assert pd.tripped

    @pytest.mark.unit
    def test_task_tool_exempt(self) -> None:
        """Task (subagent) tool should never trip the detector."""
        pd = PatternDetector(max_repetitive_calls=3)

        for _ in range(5):
            pd.track_tool_call("Task", {"prompt": "same prompt"})

        assert not pd.tripped

    @pytest.mark.unit
    def test_sequence_bounded(self) -> None:
        """Tool call sequence should not grow unbounded."""
        pd = PatternDetector(max_repetitive_calls=5)

        for i in range(100):
            pd.track_tool_call("Read", {"path": f"/file_{i}"})

        assert len(pd._tool_call_sequence) <= 50

    @pytest.mark.unit
    def test_none_input_same_tool_trips(self) -> None:
        """Same tool with None input repeated should trip (edge case)."""
        pd = PatternDetector(max_repetitive_calls=3)

        pd.track_tool_call("Write", None)
        pd.track_tool_call("Write", None)
        pd.track_tool_call("Write", None)
        assert pd.tripped

    @pytest.mark.unit
    def test_interleaved_then_repeated_trips(self) -> None:
        """After varied calls, a consecutive identical sequence should trip."""
        pd = PatternDetector(max_repetitive_calls=3)

        # Varied calls first
        pd.track_tool_call("Read", {"path": "/a"})
        pd.track_tool_call("Write", {"path": "/b", "content": "x"})
        assert not pd.tripped

        # Now identical calls
        pd.track_tool_call("LS", {"path": "/same"})
        pd.track_tool_call("LS", {"path": "/same"})
        pd.track_tool_call("LS", {"path": "/same"})
        assert pd.tripped


class TestSilentTurnDetection:
    """Tests for silent turn (no output, no tool calls) detection."""

    @pytest.mark.unit
    def test_silent_turns_trip(self) -> None:
        """N consecutive silent turns should trip."""
        pd = PatternDetector(max_silent_turns=3)

        for _ in range(3):
            pd.on_turn_start()
            pd.on_turn_end()

        assert pd.tripped
        assert "No activity detected" in pd.message

    @pytest.mark.unit
    def test_output_resets_silent_counter(self) -> None:
        """Meaningful output should reset the silent turn counter."""
        pd = PatternDetector(max_silent_turns=3)

        pd.on_turn_start()
        pd.on_turn_end()  # 1 silent

        pd.on_turn_start()
        pd.on_turn_end()  # 2 silent

        pd.on_turn_start()
        pd.on_meaningful_output()  # Output — reset
        pd.on_turn_end()

        pd.on_turn_start()
        pd.on_turn_end()  # 1 silent again

        assert not pd.tripped

    @pytest.mark.unit
    def test_tool_call_resets_silent_counter(self) -> None:
        """Tool calls in a turn should count as productive."""
        pd = PatternDetector(max_silent_turns=3)

        pd.on_turn_start()
        pd.on_turn_end()  # 1 silent

        pd.on_turn_start()
        pd.on_turn_end()  # 2 silent

        pd.on_turn_start()
        pd.current_turn_has_tool_call = True
        pd.on_turn_end()  # Has tool call — reset

        pd.on_turn_start()
        pd.on_turn_end()  # 1 silent again

        assert not pd.tripped


class TestTodoWriteOnlyPattern:
    """Tests for TodoWrite-only pattern detection."""

    @pytest.mark.unit
    def test_todowrite_only_warns(self) -> None:
        """Repeated TodoWrite-only calls should produce a warning (not trip)."""
        pd = PatternDetector(max_todowrite_only_turns=2)

        # Build up sequence of TodoWrite calls
        pd.track_tool_call("TodoWrite", {"tasks": []})
        pd.track_tool_call("TodoWrite", {"tasks": []})
        pd.track_tool_call("TodoWrite", {"tasks": []})
        pd.check_todowrite_only_pattern("TodoWrite")
        pd.check_todowrite_only_pattern("TodoWrite")

        # TodoWrite pattern only logs a warning, doesn't trip
        assert not pd.tripped

    @pytest.mark.unit
    def test_non_todowrite_resets_counter(self) -> None:
        """A non-TodoWrite call should reset the consecutive counter."""
        pd = PatternDetector(max_todowrite_only_turns=2)

        pd.track_tool_call("TodoWrite", {"tasks": []})
        pd.check_todowrite_only_pattern("TodoWrite")

        pd.track_tool_call("Read", {"path": "/file"})
        pd.check_todowrite_only_pattern("Read")

        assert pd._consecutive_todowrite_only_turns == 0


class TestManualTrip:
    """Tests for manual trip() method."""

    @pytest.mark.unit
    def test_manual_trip(self) -> None:
        """Manual trip sets tripped and message."""
        pd = PatternDetector()
        pd.trip("Custom reason")

        assert pd.tripped
        assert pd.message == "Custom reason"
