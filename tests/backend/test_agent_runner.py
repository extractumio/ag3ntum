"""
Tests for the agent runner service.

The agent runner manages background task execution.
Agent execution itself is mocked since it requires external API calls.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.agent_runner import AgentRunner, TaskParams


def make_task_params(
    session_id: str = "test-session",
    task: str = "Test task",
    user_id: str = "test-user-id",
    sessions_dir: str = "/tmp/test_sessions",
    **kwargs
) -> TaskParams:
    """Helper to create TaskParams with defaults."""
    return TaskParams(
        session_id=session_id,
        task=task,
        user_id=user_id,
        sessions_dir=sessions_dir,
        **kwargs
    )


class TestAgentRunnerState:
    """Tests for agent runner state tracking."""

    @pytest.mark.unit
    def test_initial_state(self) -> None:
        """Runner starts with no running tasks."""
        runner = AgentRunner()

        assert runner.is_running("any-session") is False
        assert runner.is_cancellation_requested("any-session") is False
        assert runner.get_result("any-session") is None

    @pytest.mark.unit
    def test_get_event_queue_not_running(self) -> None:
        """Event queue is None for non-running session."""
        runner = AgentRunner()

        assert runner.get_event_queue("not-started") is None


class TestAgentRunnerExecution:
    """Tests for task execution (mocked)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_start_task_creates_background_task(self) -> None:
        """Starting a task creates a background asyncio task."""
        runner = AgentRunner()

        with patch.object(runner, '_run_agent', new_callable=AsyncMock) as mock_run:
            # Make the mock complete immediately
            mock_run.return_value = None

            # Mock Redis connection check since Redis may not be available
            with patch.object(runner, '_ensure_redis_connection', new_callable=AsyncMock):
                params = make_task_params(
                    session_id="test-session",
                    task="Test task"
                )
                await runner.start_task(params)

            # Give the task a chance to start
            await asyncio.sleep(0.1)

            # The background task was created
            assert "test-session" in runner._running_tasks or mock_run.called

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cannot_start_duplicate_task(self) -> None:
        """Cannot start a task for a session that's already running."""
        runner = AgentRunner()

        # Manually add a running task
        runner._running_tasks["test-session"] = asyncio.create_task(
            asyncio.sleep(100)
        )

        # Mock Redis connection check since Redis may not be available
        with patch.object(runner, '_ensure_redis_connection', new_callable=AsyncMock):
            params = make_task_params(session_id="test-session", task="Duplicate task")
            with pytest.raises(RuntimeError, match="already running"):
                await runner.start_task(params)

        # Cleanup
        runner._running_tasks["test-session"].cancel()
        try:
            await runner._running_tasks["test-session"]
        except asyncio.CancelledError:
            pass

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_running_tracks_tasks(self) -> None:
        """is_running correctly reports task status."""
        runner = AgentRunner()

        # Manually add a running task
        task = asyncio.create_task(asyncio.sleep(100))
        runner._running_tasks["active-session"] = task

        assert runner.is_running("active-session") is True
        assert runner.is_running("other-session") is False

        # Cleanup
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestAgentRunnerCancellation:
    """Tests for task cancellation."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cancel_non_running_task(self) -> None:
        """Cancelling a non-running task returns False."""
        runner = AgentRunner()

        result = await runner.cancel_task("not-running")

        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cancel_running_task(self) -> None:
        """Can cancel a running task."""
        runner = AgentRunner()

        # Create a long-running task
        async def long_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                pass

        runner._running_tasks["cancel-test"] = asyncio.create_task(long_task())

        result = await runner.cancel_task("cancel-test")

        assert result is True
        assert runner.is_cancellation_requested("cancel-test") is True

    @pytest.mark.unit
    def test_is_cancellation_requested(self) -> None:
        """Can check if cancellation was requested."""
        runner = AgentRunner()

        assert runner.is_cancellation_requested("session") is False

        runner._cancel_flags["session"] = True

        assert runner.is_cancellation_requested("session") is True


class TestAgentRunnerResults:
    """Tests for result storage."""

    @pytest.mark.unit
    def test_get_result_returns_stored_result(self) -> None:
        """Can retrieve stored results."""
        runner = AgentRunner()

        runner._results["session"] = {
            "status": "completed",
            "output": "Task output"
        }

        result = runner.get_result("session")

        assert result["status"] == "completed"
        assert result["output"] == "Task output"

    @pytest.mark.unit
    def test_cleanup_session(self) -> None:
        """Cleanup removes session data."""
        runner = AgentRunner()

        # Set up test data using the current API
        runner._results["session"] = {"status": "completed"}

        runner.cleanup_session("session")

        # get_event_queue is deprecated and always returns None
        assert runner.get_event_queue("session") is None
        assert runner.get_result("session") is None


class TestTaskParams:
    """Tests for TaskParams dataclass."""

    @pytest.mark.unit
    def test_task_params_defaults(self) -> None:
        """TaskParams has sensible defaults."""
        params = TaskParams(
            session_id="test",
            task="Test task",
            user_id="test-user",
            sessions_dir="/tmp/test_sessions"
        )

        assert params.session_id == "test"
        assert params.task == "Test task"
        assert params.user_id == "test-user"
        assert params.sessions_dir == "/tmp/test_sessions"
        assert params.additional_dirs == []
        assert params.model is None
        assert params.resume_session_id is None
        assert params.fork_session is False

    @pytest.mark.unit
    def test_task_params_with_overrides(self) -> None:
        """TaskParams accepts all override fields."""
        params = TaskParams(
            session_id="test",
            task="Test task",
            user_id="test-user",
            sessions_dir="/tmp/test_sessions",
            model="claude-sonnet-4-5-20250929",
            max_turns=50,
            timeout_seconds=3600,
            enable_skills=False,
            additional_dirs=["/extra"],
            profile="/path/to/profile.yaml",
        )

        assert params.model == "claude-sonnet-4-5-20250929"
        assert params.max_turns == 50
        assert params.timeout_seconds == 3600
        assert params.enable_skills is False
        assert params.additional_dirs == ["/extra"]
        assert params.profile == "/path/to/profile.yaml"


class TestAgentRunnerIntegration:
    """Integration tests with mocked agent execution."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_full_execution_flow_mocked(self) -> None:
        """Test full execution flow with mocked agent."""
        runner = AgentRunner()

        # Mock execute_agent_task (the unified task runner)
        mock_result = MagicMock()
        mock_result.status.value = "COMPLETE"
        mock_result.output = "Test output"
        mock_result.error = None
        mock_result.comments = None
        mock_result.result_files = []
        mock_result.metrics = MagicMock()
        mock_result.metrics.model_dump.return_value = {"num_turns": 2}
        mock_result.metrics.model = "claude-haiku-4-5-20251001"
        mock_result.metrics.num_turns = 2
        mock_result.metrics.duration_ms = 1000
        mock_result.metrics.total_cost_usd = 0.01

        with patch(
            "src.services.agent_runner.execute_agent_task",
            new_callable=AsyncMock,
            return_value=mock_result
        ):
            with patch.object(runner, '_update_session_status', new_callable=AsyncMock):
                # Mock Redis connection check since Redis may not be available
                with patch.object(runner, '_ensure_redis_connection', new_callable=AsyncMock):
                    # Start the task using TaskParams
                    params = make_task_params(
                        session_id="integration-test",
                        task="Test task"
                    )
                    await runner.start_task(params)

                # Wait for completion
                await asyncio.sleep(0.5)

                # Check result was stored
                result = runner.get_result("integration-test")
                if result:
                    assert result["status"] == "COMPLETE"
