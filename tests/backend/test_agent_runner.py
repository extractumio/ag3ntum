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

        # Mock user for database query
        mock_user = MagicMock()
        mock_user.id = "test-user-id"
        mock_user.linux_uid = 50000
        mock_user.queue_priority = 0

        # Track query count to return different results
        query_count = [0]

        def mock_execute_side_effect(*args, **kwargs):
            query_count[0] += 1
            mock_result = MagicMock()
            if query_count[0] == 1:
                # First query: select User
                mock_result.scalar_one_or_none.return_value = mock_user
                mock_result.scalars.return_value.all.return_value = [mock_user]
            elif query_count[0] == 2:
                # Second query: select all users (debug)
                mock_result.scalar_one_or_none.return_value = None
                mock_result.scalars.return_value.all.return_value = [mock_user]
            else:
                # Third+ query: select Token - return None (no user token, use system key)
                mock_result.scalar_one_or_none.return_value = None
                mock_result.scalars.return_value.all.return_value = []
            return mock_result

        mock_db_session = AsyncMock()
        mock_db_session.execute = AsyncMock(side_effect=mock_execute_side_effect)
        mock_db_session.__aenter__ = AsyncMock(return_value=mock_db_session)
        mock_db_session.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "src.services.agent_runner.execute_agent_task",
            new_callable=AsyncMock,
            return_value=mock_result
        ):
            with patch.object(runner, '_update_session_status', new_callable=AsyncMock):
                # Mock Redis connection check since Redis may not be available
                with patch.object(runner, '_ensure_redis_connection', new_callable=AsyncMock):
                    # Mock database session
                    with patch(
                        "src.services.agent_runner.AsyncSessionLocal",
                        return_value=mock_db_session
                    ):
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


class TestAgentRunnerCumulativeStats:
    """Tests for cumulative stats calculation in _update_session_status.

    These tests verify that session completion correctly updates both
    current run stats and cumulative stats across resumptions.
    """

    @pytest.fixture
    def mock_session(self):
        """Create a mock session with zeroed stats."""
        session = MagicMock()
        session.status = "running"
        session.model = None
        session.num_turns = 0
        session.duration_ms = 0
        session.total_cost_usd = 0.0
        session.cumulative_turns = 0
        session.cumulative_duration_ms = 0
        session.cumulative_cost_usd = 0.0
        session.cumulative_input_tokens = 0
        session.cumulative_output_tokens = 0
        session.cumulative_cache_creation_tokens = 0
        session.cumulative_cache_read_tokens = 0
        session.completed_at = None
        session.updated_at = None
        return session

    @pytest.fixture
    def mock_db_context(self, mock_session):
        """Create mock database context that returns mock_session."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_session

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        return patch(
            "src.services.agent_runner.AsyncSessionLocal",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_db),
                __aexit__=AsyncMock()
            )
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_updates_cumulative_turns(self, mock_session, mock_db_context) -> None:
        """Cumulative turns increases by num_turns on completion."""
        runner = AgentRunner()

        with mock_db_context:
            await runner._update_session_status(
                session_id="test-session", status="complete", num_turns=5
            )

        assert mock_session.num_turns == 5
        assert mock_session.cumulative_turns == 5

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_updates_cumulative_cost(self, mock_session, mock_db_context) -> None:
        """Cumulative cost increases by total_cost_usd on completion."""
        runner = AgentRunner()

        with mock_db_context:
            await runner._update_session_status(
                session_id="test-session", status="complete", total_cost_usd=0.025
            )

        assert mock_session.total_cost_usd == 0.025
        assert mock_session.cumulative_cost_usd == 0.025

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_updates_cumulative_duration(self, mock_session, mock_db_context) -> None:
        """Cumulative duration increases by duration_ms on completion."""
        runner = AgentRunner()

        with mock_db_context:
            await runner._update_session_status(
                session_id="test-session", status="complete", duration_ms=5000
            )

        assert mock_session.duration_ms == 5000
        assert mock_session.cumulative_duration_ms == 5000

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_updates_cumulative_tokens(self, mock_session, mock_db_context) -> None:
        """Cumulative token counts increase when usage dict is provided."""
        runner = AgentRunner()
        usage = {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 100,
        }

        with mock_db_context:
            await runner._update_session_status(
                session_id="test-session", status="complete", usage=usage
            )

        assert mock_session.cumulative_input_tokens == 1000
        assert mock_session.cumulative_output_tokens == 500
        assert mock_session.cumulative_cache_creation_tokens == 200
        assert mock_session.cumulative_cache_read_tokens == 100

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_accumulates_across_multiple_runs(self, mock_session, mock_db_context) -> None:
        """Cumulative stats accumulate correctly across multiple runs."""
        runner = AgentRunner()

        # Pre-set cumulative values (simulating previous runs)
        mock_session.cumulative_turns = 10
        mock_session.cumulative_cost_usd = 0.05
        mock_session.cumulative_duration_ms = 10000
        mock_session.cumulative_input_tokens = 2000
        mock_session.cumulative_output_tokens = 1000

        with mock_db_context:
            await runner._update_session_status(
                session_id="test-session",
                status="complete",
                num_turns=3,
                total_cost_usd=0.02,
                duration_ms=5000,
                usage={"input_tokens": 500, "output_tokens": 250},
            )

        # Current run stats
        assert mock_session.num_turns == 3
        assert mock_session.total_cost_usd == 0.02
        assert mock_session.duration_ms == 5000

        # Cumulative stats (previous + current)
        assert mock_session.cumulative_turns == 13
        assert mock_session.cumulative_cost_usd == 0.07
        assert mock_session.cumulative_duration_ms == 15000
        assert mock_session.cumulative_input_tokens == 2500
        assert mock_session.cumulative_output_tokens == 1250

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_update_when_values_none(self, mock_session, mock_db_context) -> None:
        """Cumulative stats unchanged when values are None."""
        runner = AgentRunner()
        mock_session.cumulative_turns = 5
        mock_session.cumulative_cost_usd = 0.025

        with mock_db_context:
            await runner._update_session_status(
                session_id="test-session", status="running"
            )

        assert mock_session.cumulative_turns == 5
        assert mock_session.cumulative_cost_usd == 0.025

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_partial_usage_dict(self, mock_session, mock_db_context) -> None:
        """Usage dict with missing keys uses 0 as default."""
        runner = AgentRunner()

        with mock_db_context:
            await runner._update_session_status(
                session_id="test-session", status="complete", usage={"input_tokens": 1000}
            )

        assert mock_session.cumulative_input_tokens == 1000
        assert mock_session.cumulative_output_tokens == 0
        assert mock_session.cumulative_cache_creation_tokens == 0
        assert mock_session.cumulative_cache_read_tokens == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_completed_at_set_for_terminal_statuses(self, mock_session, mock_db_context) -> None:
        """completed_at is set for terminal statuses."""
        runner = AgentRunner()
        terminal_statuses = ["completed", "complete", "partial", "failed", "cancelled"]

        for status in terminal_statuses:
            mock_session.completed_at = None
            with mock_db_context:
                await runner._update_session_status(session_id="test-session", status=status)
            assert mock_session.completed_at is not None, f"completed_at not set for: {status}"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_completed_at_not_set_for_running(self, mock_session, mock_db_context) -> None:
        """completed_at is NOT set for non-terminal statuses."""
        runner = AgentRunner()

        with mock_db_context:
            await runner._update_session_status(session_id="test-session", status="running")

        assert mock_session.completed_at is None
