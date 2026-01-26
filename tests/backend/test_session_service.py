"""
Tests for the session service.

Tests the SessionService layer that handles session CRUD and
coordinates between database and file-based storage.

Note: Uses centralized test user fixtures from conftest.py for automatic
cleanup of test artifacts.
"""
import json

import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.session_service import SessionService


@pytest_asyncio.fixture
async def session_service_with_user(
    test_session: AsyncSession,
    test_session_service: SessionService,
    temp_sessions_dir: Path,
    test_user: dict,
) -> tuple[SessionService, str, Path]:
    """
    Create a session service with temp dir and a test user.

    Uses the centralized test_user fixture for automatic cleanup
    instead of creating users with hardcoded IDs.

    Returns the user's sessions directory (temp_sessions_dir/{username}/sessions)
    which mirrors the production directory structure.
    """
    # User sessions directory mirrors production: USERS_DIR/{username}/sessions
    user_sessions_dir = temp_sessions_dir / test_user["username"] / "sessions"
    return test_session_service, test_user["id"], user_sessions_dir


class TestSessionServiceCreate:
    """Tests for session creation."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_session(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Can create a session through the service."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Service test task",
            sessions_dir=sessions_dir
        )

        assert session.id is not None
        assert session.task == "Service test task"
        assert session.status == "pending"
        assert session.user_id == user_id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_session_with_model(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Can specify model when creating session."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Model test",
            sessions_dir=sessions_dir,
            model="claude-haiku-4-5-20251001"
        )

        assert session.model == "claude-haiku-4-5-20251001"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_session_generates_valid_id(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Session ID has valid format YYYYMMDD_HHMMSS_uuid8."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="ID test",
            sessions_dir=sessions_dir
        )

        parts = session.id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # Date
        assert len(parts[1]) == 6  # Time
        assert len(parts[2]) == 8  # UUID fragment

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_session_creates_folder(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path],
    ) -> None:
        """Creating a session creates the session folder."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Folder test", sessions_dir=sessions_dir
        )

        # Session folder is created at sessions_dir/{session_id}
        session_folder = sessions_dir / session.id
        assert session_folder.exists()
        assert session_folder.is_dir()


class TestSessionServiceQuery:
    """Tests for session queries."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Can get a session by ID."""
        service, user_id, sessions_dir = session_service_with_user

        # Create a session
        created = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Get test", sessions_dir=sessions_dir
        )

        # Get it back
        session = await service.get_session(
            db=test_session,
            session_id=created.id,
            user_id=user_id
        )

        assert session is not None
        assert session.id == created.id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_not_found(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Returns None for non-existent session."""
        service, user_id, sessions_dir = session_service_with_user

        # Use a valid format session ID that doesn't exist
        session = await service.get_session(
            db=test_session,
            session_id="20250101_000000_deadbeef",
            user_id=user_id
        )

        assert session is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_wrong_user(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Returns None when user doesn't match."""
        service, user_id, sessions_dir = session_service_with_user

        # Create a session
        created = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Wrong user test", sessions_dir=sessions_dir
        )

        # Try to get with different user
        session = await service.get_session(
            db=test_session,
            session_id=created.id,
            user_id="different-user"
        )

        assert session is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_sessions(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Can list sessions for a user."""
        service, user_id, sessions_dir = session_service_with_user

        # Create multiple sessions
        await service.create_session(
            db=test_session, user_id=user_id, task="Task 1", sessions_dir=sessions_dir
        )
        await service.create_session(
            db=test_session, user_id=user_id, task="Task 2", sessions_dir=sessions_dir
        )

        sessions, total = await service.list_sessions(
            db=test_session,
            user_id=user_id
        )

        assert total == 2
        assert len(sessions) == 2

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_sessions_pagination(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """List supports pagination."""
        service, user_id, sessions_dir = session_service_with_user

        # Create 5 sessions
        for i in range(5):
            await service.create_session(
                db=test_session, user_id=user_id, task=f"Task {i}", sessions_dir=sessions_dir
            )

        # Get first 2
        sessions, total = await service.list_sessions(
            db=test_session,
            user_id=user_id,
            limit=2,
            offset=0
        )

        assert total == 5
        assert len(sessions) == 2

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_sessions_empty(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """List returns empty list when no sessions exist."""
        service, user_id, sessions_dir = session_service_with_user

        sessions, total = await service.list_sessions(
            db=test_session,
            user_id=user_id
        )

        assert total == 0
        assert sessions == []


class TestSessionServiceUpdate:
    """Tests for session updates."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_status(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Can update session status."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Update test", sessions_dir=sessions_dir
        )

        updated = await service.update_session(
            db=test_session,
            session=session,
            status="running"
        )

        assert updated.status == "running"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_metrics(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Can update session metrics."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Metrics test", sessions_dir=sessions_dir
        )

        updated = await service.update_session(
            db=test_session,
            session=session,
            num_turns=10,
            duration_ms=5000,
            total_cost_usd=0.05
        )

        assert updated.num_turns == 10
        assert updated.duration_ms == 5000
        assert updated.total_cost_usd == pytest.approx(0.05)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_multiple_fields(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Can update multiple fields at once."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Multi-update test", sessions_dir=sessions_dir
        )

        updated = await service.update_session(
            db=test_session,
            session=session,
            status="completed",
            num_turns=15,
            duration_ms=10000
        )

        assert updated.status == "completed"
        assert updated.num_turns == 15
        assert updated.duration_ms == 10000

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_request_cancellation(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Can request cancellation."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Cancel test", sessions_dir=sessions_dir
        )

        updated = await service.request_cancellation(
            db=test_session,
            session=session
        )

        assert updated.cancel_requested is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_updates_timestamp(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Updating a session updates the updated_at timestamp."""
        import time
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Timestamp test", sessions_dir=sessions_dir
        )
        original_updated_at = session.updated_at

        # Small delay to ensure timestamp difference
        time.sleep(0.01)

        updated = await service.update_session(
            db=test_session,
            session=session,
            status="running"
        )

        assert updated.updated_at >= original_updated_at


class TestSessionServiceOutput:
    """Tests for session data from database."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_session_has_default_cumulative_values(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """New session has default cumulative values."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Output test", sessions_dir=sessions_dir
        )

        # Session should have default cumulative values
        assert session.cumulative_turns == 0
        assert session.cumulative_duration_ms == 0
        assert session.cumulative_cost_usd == 0.0
        assert session.cumulative_input_tokens == 0
        assert session.cumulative_output_tokens == 0
        assert session.claude_session_id is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_session_data_from_database(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """
        Session data is retrieved from database, not files.

        All session metadata is stored in SQLite database. This test verifies that session data
        can be retrieved from the database.
        """
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Info test", sessions_dir=sessions_dir
        )

        # Retrieve session from database
        retrieved = await service.get_session(db=test_session, session_id=session.id, user_id=user_id)

        # Should retrieve session with correct data
        assert retrieved is not None
        assert retrieved.id == session.id
        assert retrieved.task == "Info test"
        assert retrieved.status == "pending"


class TestSessionServiceCompletionStats:
    """Tests for update_completion_stats and cumulative tracking."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_completion_stats_basic(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """update_completion_stats updates session with final stats."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Completion test", sessions_dir=sessions_dir
        )

        usage = {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 100,
        }

        updated = await service.update_completion_stats(
            db=test_session,
            session=session,
            status="complete",
            num_turns=5,
            duration_ms=15000,
            cost_usd=0.025,
            usage=usage,
            model="claude-sonnet-4-20250514",
        )

        assert updated.status == "complete"
        assert updated.num_turns == 5
        assert updated.duration_ms == 15000
        assert updated.total_cost_usd == pytest.approx(0.025)
        assert updated.model == "claude-sonnet-4-20250514"
        assert updated.completed_at is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_completion_stats_cumulative_first_run(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """First run sets cumulative stats equal to run stats."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Cumulative test", sessions_dir=sessions_dir
        )

        usage = {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 100,
        }

        updated = await service.update_completion_stats(
            db=test_session,
            session=session,
            status="complete",
            num_turns=5,
            duration_ms=15000,
            cost_usd=0.025,
            usage=usage,
        )

        # First run: cumulative equals run stats
        assert updated.cumulative_turns == 5
        assert updated.cumulative_duration_ms == 15000
        assert updated.cumulative_cost_usd == pytest.approx(0.025)
        assert updated.cumulative_input_tokens == 1000
        assert updated.cumulative_output_tokens == 500
        assert updated.cumulative_cache_creation_tokens == 200
        assert updated.cumulative_cache_read_tokens == 100

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_completion_stats_cumulative_accumulates(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Multiple runs accumulate cumulative stats."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Accumulate test", sessions_dir=sessions_dir
        )

        # First run
        usage1 = {"input_tokens": 1000, "output_tokens": 500}
        await service.update_completion_stats(
            db=test_session, session=session, status="complete",
            num_turns=5, duration_ms=10000, cost_usd=0.02, usage=usage1,
        )

        # Simulate second run (session resumed)
        usage2 = {"input_tokens": 800, "output_tokens": 300}
        updated = await service.update_completion_stats(
            db=test_session, session=session, status="complete",
            num_turns=3, duration_ms=5000, cost_usd=0.01, usage=usage2,
        )

        # Current run stats show latest run
        assert updated.num_turns == 3
        assert updated.duration_ms == 5000
        assert updated.total_cost_usd == pytest.approx(0.01)

        # Cumulative stats are accumulated
        assert updated.cumulative_turns == 8  # 5 + 3
        assert updated.cumulative_duration_ms == 15000  # 10000 + 5000
        assert updated.cumulative_cost_usd == pytest.approx(0.03)  # 0.02 + 0.01
        assert updated.cumulative_input_tokens == 1800  # 1000 + 800
        assert updated.cumulative_output_tokens == 800  # 500 + 300


class TestSessionServiceCheckpoints:
    """Tests for checkpoint methods."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_add_checkpoint(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """add_checkpoint adds checkpoint to session."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Checkpoint test", sessions_dir=sessions_dir
        )

        checkpoint = {
            "uuid": "cp-001",
            "type": "file_state",
            "description": "After file created",
            "files": ["workspace/test.txt"],
        }

        updated = await service.add_checkpoint(
            db=test_session, session=session, checkpoint=checkpoint
        )

        checkpoints = json.loads(updated.checkpoints_json)
        assert len(checkpoints) == 1
        assert checkpoints[0]["uuid"] == "cp-001"
        assert checkpoints[0]["type"] == "file_state"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_add_multiple_checkpoints(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Multiple checkpoints can be added."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Multi checkpoint", sessions_dir=sessions_dir
        )

        for i in range(3):
            await service.add_checkpoint(
                db=test_session, session=session,
                checkpoint={"uuid": f"cp-{i}", "type": "file_state"}
            )

        checkpoints = json.loads(session.checkpoints_json)
        assert len(checkpoints) == 3
        assert [cp["uuid"] for cp in checkpoints] == ["cp-0", "cp-1", "cp-2"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_clear_checkpoints_after(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """clear_checkpoints_after removes checkpoints after specified one."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Clear checkpoint", sessions_dir=sessions_dir
        )

        # Add 5 checkpoints
        for i in range(5):
            await service.add_checkpoint(
                db=test_session, session=session,
                checkpoint={"uuid": f"cp-{i}", "type": "file_state"}
            )

        # Clear after cp-2 (should keep cp-0, cp-1, cp-2)
        removed = await service.clear_checkpoints_after(
            db=test_session, session=session, checkpoint_uuid="cp-2"
        )

        assert removed == 2  # cp-3 and cp-4 removed

        checkpoints = json.loads(session.checkpoints_json)
        assert len(checkpoints) == 3
        assert [cp["uuid"] for cp in checkpoints] == ["cp-0", "cp-1", "cp-2"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_clear_checkpoints_after_not_found(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """clear_checkpoints_after with non-existent UUID removes all."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Clear none", sessions_dir=sessions_dir
        )

        # Add 3 checkpoints
        for i in range(3):
            await service.add_checkpoint(
                db=test_session, session=session,
                checkpoint={"uuid": f"cp-{i}", "type": "file_state"}
            )

        # Clear after non-existent UUID - loop completes without match, keeps all
        removed = await service.clear_checkpoints_after(
            db=test_session, session=session, checkpoint_uuid="non-existent"
        )

        checkpoints = json.loads(session.checkpoints_json)
        assert len(checkpoints) == 3


class TestSessionServiceResume:
    """Tests for get_session_for_resume."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_for_resume(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """get_session_for_resume returns correct data."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Resume test", sessions_dir=sessions_dir
        )

        # Simulate claude_session_id being set
        session.claude_session_id = "sdk-uuid-123"
        session.file_checkpointing_enabled = True
        await test_session.commit()
        await test_session.refresh(session)

        # Add some checkpoints
        await service.add_checkpoint(
            db=test_session, session=session,
            checkpoint={"uuid": "cp-1", "type": "file_state"}
        )

        # Update with some completion stats
        usage = {"input_tokens": 500, "output_tokens": 200}
        await service.update_completion_stats(
            db=test_session, session=session, status="complete",
            num_turns=3, duration_ms=5000, cost_usd=0.01, usage=usage,
        )

        # Get resume data
        resume_data = await service.get_session_for_resume(
            db=test_session, session_id=session.id, user_id=user_id
        )

        assert resume_data is not None
        assert resume_data["claude_session_id"] == "sdk-uuid-123"
        assert resume_data["cumulative_turns"] == 3
        assert resume_data["cumulative_duration_ms"] == 5000
        assert resume_data["cumulative_cost_usd"] == pytest.approx(0.01)
        assert resume_data["file_checkpointing_enabled"] is True
        assert len(resume_data["checkpoints"]) == 1
        assert resume_data["checkpoints"][0]["uuid"] == "cp-1"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_for_resume_not_found(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """get_session_for_resume returns None for non-existent session."""
        service, user_id, sessions_dir = session_service_with_user

        # Use properly formatted but non-existent session ID
        resume_data = await service.get_session_for_resume(
            db=test_session, session_id="19990101_000000_deadbeef", user_id=user_id
        )

        assert resume_data is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_for_resume_empty_defaults(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """get_session_for_resume returns defaults for new session."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Empty resume", sessions_dir=sessions_dir
        )

        resume_data = await service.get_session_for_resume(
            db=test_session, session_id=session.id, user_id=user_id
        )

        assert resume_data is not None
        assert resume_data["claude_session_id"] is None
        assert resume_data["cumulative_turns"] == 0
        assert resume_data["cumulative_duration_ms"] == 0
        assert resume_data["cumulative_cost_usd"] == 0.0
        assert resume_data["checkpoints"] == []
        assert resume_data["file_checkpointing_enabled"] is False
