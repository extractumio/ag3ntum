"""
Tests for database models.
"""
import secrets
import uuid
import pytest
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User, Session


class TestUserModel:
    """Tests for the User model."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_user(self, test_session: AsyncSession) -> None:
        """Can create a user in the database."""
        user = User(
            id=str(uuid.uuid4()),
            username="testuser",
            email="test@example.com",
            password_hash="hashed_password",
            jwt_secret=secrets.token_urlsafe(32),
            role="user",
        )
        test_session.add(user)
        await test_session.commit()

        result = await test_session.execute(
            select(User).where(User.username == "testuser")
        )
        db_user = result.scalar_one()

        assert db_user.username == "testuser"
        assert db_user.email == "test@example.com"
        assert db_user.role == "user"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_user_default_role(self, test_session: AsyncSession) -> None:
        """User role defaults to 'user'."""
        user = User(
            id=str(uuid.uuid4()),
            username="defaultuser",
            email="default@example.com",
            password_hash="hashed_password",
            jwt_secret=secrets.token_urlsafe(32),
        )
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        assert user.role == "user"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_user_created_at(self, test_session: AsyncSession) -> None:
        """User has a created_at timestamp."""
        user = User(
            id=str(uuid.uuid4()),
            username="timestampuser",
            email="timestamp@example.com",
            password_hash="hashed_password",
            jwt_secret=secrets.token_urlsafe(32),
        )
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        assert user.created_at is not None
        assert isinstance(user.created_at, datetime)


class TestSessionModel:
    """Tests for the Session model."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_session(self, test_session: AsyncSession) -> None:
        """Can create a session in the database."""
        # First create a user
        user = User(
            id=str(uuid.uuid4()),
            username="sessionowner",
            email="sessionowner@example.com",
            password_hash="hashed_password",
            jwt_secret=secrets.token_urlsafe(32),
        )
        test_session.add(user)
        await test_session.commit()

        # Create session
        session = Session(
            id="20260103_120000_abcd1234",
            user_id=user.id,
            task="Test task",
            status="pending"
        )
        test_session.add(session)
        await test_session.commit()

        result = await test_session.execute(
            select(Session).where(Session.id == "20260103_120000_abcd1234")
        )
        db_session = result.scalar_one()

        assert db_session.id == "20260103_120000_abcd1234"
        assert db_session.task == "Test task"
        assert db_session.status == "pending"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_session_defaults(self, test_session: AsyncSession) -> None:
        """Session has correct default values."""
        user = User(
            id=str(uuid.uuid4()),
            username="defaultsowner",
            email="defaults@example.com",
            password_hash="hashed_password",
            jwt_secret=secrets.token_urlsafe(32),
        )
        test_session.add(user)
        await test_session.commit()

        session = Session(
            id="20260103_120000_defaults",
            user_id=user.id,
            task="Defaults test"
        )
        test_session.add(session)
        await test_session.commit()
        await test_session.refresh(session)

        assert session.status == "pending"
        assert session.num_turns == 0
        assert session.cancel_requested is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_session_user_relationship(
        self,
        test_session: AsyncSession
    ) -> None:
        """Session has relationship to user."""
        user = User(
            id=str(uuid.uuid4()),
            username="relowner",
            email="rel@example.com",
            password_hash="hashed_password",
            jwt_secret=secrets.token_urlsafe(32),
        )
        test_session.add(user)
        await test_session.commit()

        session = Session(
            id="20260103_120000_relation",
            user_id=user.id,
            task="Relationship test"
        )
        test_session.add(session)
        await test_session.commit()

        # Refresh to load relationship
        await test_session.refresh(session)
        await test_session.refresh(user)

        assert session.user_id == user.id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_session_update(self, test_session: AsyncSession) -> None:
        """Can update session fields."""
        user = User(
            id=str(uuid.uuid4()),
            username="updateowner",
            email="update@example.com",
            password_hash="hashed_password",
            jwt_secret=secrets.token_urlsafe(32),
        )
        test_session.add(user)
        await test_session.commit()

        session = Session(
            id="20260103_120000_update",
            user_id=user.id,
            task="Update test"
        )
        test_session.add(session)
        await test_session.commit()

        # Update the session
        session.status = "completed"
        session.num_turns = 5
        session.total_cost_usd = 0.0123
        await test_session.commit()
        await test_session.refresh(session)

        assert session.status == "completed"
        assert session.num_turns == 5
        assert session.total_cost_usd == pytest.approx(0.0123)
