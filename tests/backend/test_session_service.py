"""
Tests for the session service.

Tests the SessionService layer that handles session CRUD and 
coordinates between database and file-based storage.
"""
import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.services.session_service import SessionService


@pytest_asyncio.fixture
async def session_service_with_user(
    test_session: AsyncSession,
    test_session_service: SessionService,
    temp_sessions_dir: Path
) -> tuple[SessionService, str, Path]:
    """Create a session service with temp dir and a test user."""
    import secrets
    import uuid
    
    try:
        import bcrypt
        password_hash = bcrypt.hashpw(b"test123", bcrypt.gensalt()).decode()
    except ImportError:
        import hashlib
        password_hash = hashlib.sha256(b"test123").hexdigest()
    
    # Create a test user with all required fields
    user = User(
        id="service-test-user",
        username="serviceuser",
        email="service@example.com",
        password_hash=password_hash,
        role="user",
        jwt_secret=secrets.token_urlsafe(32),
        linux_uid=None,
        is_active=True,
    )
    test_session.add(user)
    await test_session.commit()

    return test_session_service, "service-test-user", temp_sessions_dir


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
        temp_sessions_dir: Path
    ) -> None:
        """Creating a session creates the session folder."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Folder test", sessions_dir=sessions_dir
        )

        session_folder = temp_sessions_dir / session.id
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
    """Tests for session output retrieval."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_output_empty(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Get session info returns data for new session."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Output test", sessions_dir=sessions_dir
        )

        # Use get_session_info instead of removed get_session_output
        info = service.get_session_info(session.id)

        # Should return session info dict
        assert isinstance(info, dict)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_info_empty(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str, Path]
    ) -> None:
        """Get session info returns data for existing session."""
        service, user_id, sessions_dir = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Info test", sessions_dir=sessions_dir
        )

        info = service.get_session_info(session.id)

        # Should return session info dict
        assert isinstance(info, dict)
        assert "session_id" in info
        assert info["session_id"] == session.id
