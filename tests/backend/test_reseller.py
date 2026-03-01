"""
Tests for reseller API endpoints.

Covers:
- Authentication and authorization (require_reseller dependency)
- IDOR prevention (_get_owned_user isolation)
- User management (create, list, get, update, suspend, delete)
- API key management (create, list, revoke)
- Usage and spending endpoints
- Configuration endpoints (config, security, ssh-filters, spending-limits)
- Skills management (user skills, skill library)
"""
import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.db.database import Base, get_db
from src.db.models import APIKey, Reseller, ResellerQuota, User, UserQuota


# =============================================================================
# Fixtures
# =============================================================================

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    """In-memory test database engine."""
    engine = create_async_engine(
        TEST_DB_URL, echo=False, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(db_engine):
    """Async session factory for test DB."""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(db_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Database session for direct DB manipulation in tests."""
    async with db_session_factory() as session:
        yield session


async def _make_reseller(db: AsyncSession) -> tuple[User, Reseller, ResellerQuota]:
    """Create a reseller owner, reseller record, and quota in the test DB."""
    owner_id = str(uuid.uuid4())
    reseller_id = str(uuid.uuid4())

    owner = User(
        id=owner_id,
        username=f"reseller_{uuid.uuid4().hex[:6]}",
        email=f"reseller_{uuid.uuid4().hex[:6]}@test.com",
        password_hash=bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode(),
        role="reseller",
        jwt_secret=uuid.uuid4().hex,
        linux_uid=None,
        is_active=True,
        reseller_id=reseller_id,
    )
    db.add(owner)
    await db.flush()

    reseller = Reseller(
        id=reseller_id,
        name=f"Test Reseller {uuid.uuid4().hex[:4]}",
        company="Test Corp",
        contact_email=owner.email,
        is_active=True,
        owner_user_id=owner_id,
        max_users=10,
        max_concurrent_tasks=5,
        max_daily_tasks=100,
        max_monthly_spending_usd=1000.0,
        max_daily_spending_usd=100.0,
        spending_alert_threshold_pct=80,
    )
    db.add(reseller)

    quota = ResellerQuota(
        reseller_id=reseller_id,
        current_user_count=0,
        tasks_today=0,
        last_reset=datetime.now(timezone.utc),
        monthly_input_tokens=0,
        monthly_output_tokens=0,
        monthly_cost_usd=5.0,
        monthly_reset=datetime.now(timezone.utc),
        daily_cost_usd=1.0,
        daily_cost_reset=datetime.now(timezone.utc),
    )
    db.add(quota)
    await db.commit()

    return owner, reseller, quota


async def _make_managed_user(
    db: AsyncSession, reseller_id: str, username: str = None
) -> tuple[User, UserQuota]:
    """Create a user managed by a reseller."""
    user_id = str(uuid.uuid4())
    uname = username or f"user_{uuid.uuid4().hex[:6]}"

    user = User(
        id=user_id,
        username=uname,
        email=f"{uname}@test.com",
        password_hash=bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode(),
        role="user",
        jwt_secret=uuid.uuid4().hex,
        linux_uid=None,
        is_active=True,
        reseller_id=reseller_id,
    )
    db.add(user)
    await db.flush()

    quota = UserQuota(
        user_id=user_id,
        max_concurrent_tasks=2,
        max_daily_tasks=50,
        tasks_today=0,
        last_reset=datetime.now(timezone.utc),
    )
    db.add(quota)
    await db.commit()
    return user, quota


def _make_auth_context(reseller_id: str, user_id: str, role: str = "reseller"):
    """Create an AuthContext mock for the given reseller."""
    from src.api.deps import AuthContext
    return AuthContext(
        user_id=user_id,
        role=role,
        reseller_id=reseller_id,
        api_key_id=None,
        api_key_scopes=[],
    )


@pytest_asyncio.fixture
async def reseller_setup(db):
    """Return (owner, reseller, quota) for use in endpoint tests."""
    return await _make_reseller(db)


# =============================================================================
# Unit tests — _get_owned_user (IDOR prevention)
# =============================================================================

class TestGetOwnedUser:
    """Verify _get_owned_user rejects cross-reseller access."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_user_from_different_reseller(self, db, reseller_setup):
        """Cannot access a user that belongs to a different reseller."""
        from src.api.routes.reseller import _get_owned_user
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        # Create a second reseller and its user
        _, reseller2, _ = await _make_reseller(db)
        user2, _ = await _make_managed_user(db, reseller2.id)

        auth = _make_auth_context(reseller.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await _get_owned_user(db, auth, user2.id)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_own_user(self, db, reseller_setup):
        """Returns a user that belongs to the authenticated reseller."""
        from src.api.routes.reseller import _get_owned_user

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        auth = _make_auth_context(reseller.id, owner.id)

        result = await _get_owned_user(db, auth, user.id)
        assert result.id == user.id

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_nonexistent_user(self, db, reseller_setup):
        """Returns 404 for a user that does not exist."""
        from src.api.routes.reseller import _get_owned_user
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await _get_owned_user(db, auth, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404


# =============================================================================
# Unit tests — _period_bounds
# =============================================================================

class TestPeriodBounds:
    """Verify _period_bounds computes correct date ranges."""

    @pytest.mark.unit
    def test_current_month_starts_on_first(self):
        """current_month starts on the 1st of the month."""
        from src.api.routes.reseller import _period_bounds
        start, end = _period_bounds("current_month")
        assert start.day == 1
        assert start.hour == 0 and start.minute == 0 and start.second == 0

    @pytest.mark.unit
    def test_last_month_different_from_current(self):
        """last_month start precedes current_month start."""
        from src.api.routes.reseller import _period_bounds
        cur_start, _ = _period_bounds("current_month")
        last_start, _ = _period_bounds("last_month")
        assert last_start < cur_start

    @pytest.mark.unit
    def test_unknown_period_defaults_to_current_month(self):
        """Unknown period string falls back to current_month behavior."""
        from src.api.routes.reseller import _period_bounds
        start, _ = _period_bounds("unknown_value")
        now = datetime.now(timezone.utc)
        assert start.month == now.month
        assert start.day == 1


# =============================================================================
# Unit tests — _api_key_to_response
# =============================================================================

class TestAPIKeyToResponse:
    """Verify APIKey ORM records serialize correctly."""

    @pytest.mark.unit
    def test_converts_key_fields(self):
        """Key fields are correctly mapped to the response model."""
        from src.api.routes.reseller import _api_key_to_response

        key = APIKey(
            id=str(uuid.uuid4()),
            reseller_id="r1",
            user_id="u1",
            name="my key",
            key_prefix="ag3_res_",
            key_hash="hash",
            scopes=json.dumps(["users:read"]),
            ip_allowlist=json.dumps(["1.2.3.4"]),
            rate_limit_per_minute=60,
            is_active=True,
            last_used_at=None,
            last_used_ip=None,
            expires_at=None,
            created_at=datetime.now(timezone.utc),
        )

        resp = _api_key_to_response(key)
        assert resp.name == "my key"
        assert resp.scopes == ["users:read"]
        assert resp.ip_allowlist == ["1.2.3.4"]
        assert resp.is_active is True

    @pytest.mark.unit
    def test_handles_malformed_json(self):
        """Malformed JSON in scopes/ip_allowlist defaults to empty values."""
        from src.api.routes.reseller import _api_key_to_response

        key = APIKey(
            id=str(uuid.uuid4()),
            reseller_id="r1",
            user_id="u1",
            name="bad key",
            key_prefix="ag3_res_",
            key_hash="hash",
            scopes="not-json",
            ip_allowlist="not-json",
            rate_limit_per_minute=60,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )

        resp = _api_key_to_response(key)
        assert resp.scopes == []
        assert resp.ip_allowlist is None


# =============================================================================
# Integration tests — endpoint behavior via service layer
# =============================================================================

class TestCreateUser:
    """Tests for POST /reseller/users logic."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_when_user_limit_reached(self, db, reseller_setup):
        """Returns 422 when reseller has no remaining user slots."""
        from src.api.routes.reseller import create_user
        from src.api.reseller_models import CreateResellerUserRequest
        from fastapi import HTTPException

        owner, reseller, quota = reseller_setup
        # Fill users up to max_users (10)
        for _ in range(10):
            await _make_managed_user(db, reseller.id)

        auth = _make_auth_context(reseller.id, owner.id)
        body = CreateResellerUserRequest(
            username="newuser",
            email="new@test.com",
            password="password123",
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_user(body, auth, db)

        assert exc_info.value.status_code == 422
        assert "limit reached" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_duplicate_username(self, db, reseller_setup):
        """Returns 409 when username is already taken."""
        from src.api.routes.reseller import create_user
        from src.api.reseller_models import CreateResellerUserRequest
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        existing, _ = await _make_managed_user(db, reseller.id, username="takenname")

        auth = _make_auth_context(reseller.id, owner.id)
        body = CreateResellerUserRequest(
            username="takenname",
            email="different@test.com",
            password="password123",
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_user(body, auth, db)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_creates_user_and_quota(self, db, reseller_setup):
        """Successfully creates user with associated quota record."""
        from src.api.routes.reseller import create_user
        from src.api.reseller_models import CreateResellerUserRequest
        from sqlalchemy import select

        owner, reseller, _ = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)
        body = CreateResellerUserRequest(
            username="brandnew",
            email="brandnew@test.com",
            password="password123",
        )

        response = await create_user(body, auth, db)
        assert response.username == "brandnew"
        assert response.is_active is True

        # Verify quota was created
        result = await db.execute(
            select(UserQuota).where(UserQuota.user_id == response.id)
        )
        quota = result.scalar_one_or_none()
        assert quota is not None
        assert quota.max_daily_tasks == 50

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_missing_scope(self, db, reseller_setup):
        """Returns 403 when API key lacks users:create scope."""
        from src.api.routes.reseller import create_user
        from src.api.reseller_models import CreateResellerUserRequest
        from src.api.deps import AuthContext
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        # API key auth with no scopes
        auth = AuthContext(
            user_id=owner.id,
            role="reseller",
            reseller_id=reseller.id,
            api_key_id="some-key-id",
            api_key_scopes=[],  # No scopes
        )
        body = CreateResellerUserRequest(
            username="blocked",
            email="blocked@test.com",
            password="password123",
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_user(body, auth, db)

        assert exc_info.value.status_code == 403


class TestSuspendUser:
    """Tests for POST /reseller/users/{user_id}/suspend."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_suspends_active_user(self, db, reseller_setup):
        """Suspending an active user sets is_active=False."""
        from src.api.routes.reseller import suspend_user
        from src.api.reseller_models import SuspendRequest

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        auth = _make_auth_context(reseller.id, owner.id)

        result = await suspend_user(user.id, SuspendRequest(), auth, db)
        assert result.is_active is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_double_suspend(self, db, reseller_setup):
        """Suspending an already-suspended user returns 409."""
        from src.api.routes.reseller import suspend_user
        from src.api.reseller_models import SuspendRequest
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        user.is_active = False
        await db.commit()

        auth = _make_auth_context(reseller.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await suspend_user(user.id, SuspendRequest(), auth, db)

        assert exc_info.value.status_code == 409


class TestDeleteUser:
    """Tests for DELETE /reseller/users/{user_id}."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_deletes_owned_user(self, db, reseller_setup):
        """Deletes a user that belongs to the reseller."""
        from src.api.routes.reseller import delete_user
        from sqlalchemy import select

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        user_id = user.id
        auth = _make_auth_context(reseller.id, owner.id)

        result = await delete_user(user_id, auth, db)
        assert result.status == "deleted"
        assert result.id == user_id

        # User should be gone from DB
        check = await db.execute(select(User).where(User.id == user_id))
        assert check.scalar_one_or_none() is None

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_decrements_reseller_quota(self, db, reseller_setup):
        """Deleting a user decrements the reseller's current_user_count."""
        from src.api.routes.reseller import delete_user
        from sqlalchemy import select

        owner, reseller, quota = reseller_setup
        quota.current_user_count = 1
        await db.commit()

        user, _ = await _make_managed_user(db, reseller.id)
        auth = _make_auth_context(reseller.id, owner.id)

        await delete_user(user.id, auth, db)

        await db.refresh(quota)
        assert quota.current_user_count == 0


class TestChangePassword:
    """Tests for POST /reseller/users/{user_id}/change-password."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_increments_token_version(self, db, reseller_setup):
        """Changing password increments token_version to invalidate JWTs."""
        from src.api.routes.reseller import change_user_password
        from src.api.reseller_models import ChangePasswordRequest

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        original_version = user.token_version or 0
        auth = _make_auth_context(reseller.id, owner.id)

        result = await change_user_password(
            user.id, ChangePasswordRequest(new_password="newpassword99"), auth, db
        )

        assert result.tokens_revoked is True
        await db.refresh(user)
        assert (user.token_version or 0) == original_version + 1


class TestSpendingLimits:
    """Tests for PUT /reseller/users/{user_id}/spending-limits."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_limit_exceeding_reseller_cap(self, db, reseller_setup):
        """Returns 422 when user limit exceeds reseller's own monthly cap."""
        from src.api.routes.reseller import set_user_spending_limits
        from src.api.reseller_models import SetSpendingLimitsRequest
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        auth = _make_auth_context(reseller.id, owner.id)

        # reseller cap is 1000.0, request 2000.0
        body = SetSpendingLimitsRequest(max_monthly_usd=2000.0)

        with pytest.raises(HTTPException) as exc_info:
            await set_user_spending_limits(user.id, body, auth, db)

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_sets_valid_limits(self, db, reseller_setup):
        """Sets limits that are within the reseller's cap."""
        from src.api.routes.reseller import set_user_spending_limits
        from src.api.reseller_models import SetSpendingLimitsRequest

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        auth = _make_auth_context(reseller.id, owner.id)

        body = SetSpendingLimitsRequest(max_monthly_usd=500.0, max_daily_usd=50.0)
        result = await set_user_spending_limits(user.id, body, auth, db)

        await db.refresh(user)
        assert user.spending_limit_monthly_usd == 500.0
        assert user.spending_limit_daily_usd == 50.0


class TestSettingsMode:
    """Tests for PUT /reseller/users/{user_id}/settings-mode."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_sets_readonly_mode(self, db, reseller_setup):
        """Sets user settings_mode to readonly."""
        from src.api.routes.reseller import set_user_settings_mode
        from src.api.reseller_models import SetSettingsModeRequest

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        auth = _make_auth_context(reseller.id, owner.id)

        result = await set_user_settings_mode(
            user.id,
            SetSettingsModeRequest(mode="readonly", allowed_overrides=[]),
            auth,
            db,
        )

        assert result["settings_mode"] == "readonly"
        assert result["allowed_overrides"] == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_sets_configurable_mode_with_overrides(self, db, reseller_setup):
        """Sets user settings_mode to configurable with specific overrides."""
        from src.api.routes.reseller import set_user_settings_mode
        from src.api.reseller_models import SetSettingsModeRequest

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        auth = _make_auth_context(reseller.id, owner.id)

        result = await set_user_settings_mode(
            user.id,
            SetSettingsModeRequest(mode="configurable", allowed_overrides=["theme"]),
            auth,
            db,
        )

        assert result["settings_mode"] == "configurable"
        assert "theme" in result["allowed_overrides"]


class TestAPIKeys:
    """Tests for API key management endpoints."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_revoke_nonexistent_key_returns_404(self, db, reseller_setup):
        """Revoking a key that doesn't exist returns 404."""
        from src.api.routes.reseller import revoke_api_key
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await revoke_api_key(str(uuid.uuid4()), auth, db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_list_api_keys_empty(self, db, reseller_setup):
        """List returns empty when no keys exist."""
        from src.api.routes.reseller import list_api_keys

        owner, reseller, _ = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)

        result = await list_api_keys(auth, db)
        assert result.api_keys == []


class TestSkillLibrary:
    """Tests for skill library management."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_upload_and_list_skill(self, db, reseller_setup):
        """Uploading a skill makes it appear in list_skill_library."""
        from src.api.routes.reseller import upload_skill_to_library, list_skill_library
        from src.api.reseller_models import UploadSkillRequest

        owner, reseller, _ = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)

        await upload_skill_to_library(
            UploadSkillRequest(name="my-skill", content="# skill content"),
            auth,
            db,
        )

        result = await list_skill_library(auth, db)
        names = [s.name for s in result.skills]
        assert "my-skill" in names

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_remove_nonexistent_skill_returns_404(self, db, reseller_setup):
        """Removing a skill that doesn't exist returns 404."""
        from src.api.routes.reseller import remove_skill_from_library
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await remove_skill_from_library("nonexistent-skill", auth, db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_assign_skill_not_in_library_returns_404(self, db, reseller_setup):
        """Assigning a skill not in the library returns 404."""
        from src.api.routes.reseller import assign_skill_to_user
        from src.api.reseller_models import AssignSkillRequest
        from fastapi import HTTPException

        owner, reseller, _ = reseller_setup
        user, _ = await _make_managed_user(db, reseller.id)
        auth = _make_auth_context(reseller.id, owner.id)

        with pytest.raises(HTTPException) as exc_info:
            await assign_skill_to_user(
                user.id,
                AssignSkillRequest(name="no-such-skill"),
                auth,
                db,
            )

        assert exc_info.value.status_code == 404


class TestGetProfile:
    """Tests for GET /reseller/profile."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_profile_fields(self, db, reseller_setup):
        """Profile response contains expected fields."""
        from src.api.routes.reseller import get_profile

        owner, reseller, _ = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)

        result = await get_profile(auth, db)
        assert result.id == reseller.id
        assert result.is_active is True
        assert "max_users" in result.limits
        assert result.limits["max_users"] == 10


class TestTestConnection:
    """Tests for GET /reseller/test-connection."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_ok_status(self, db, reseller_setup):
        """test-connection returns status=ok with expected fields."""
        from src.api.routes.reseller import test_connection

        owner, reseller, _ = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)

        result = await test_connection(auth, db)
        assert result.status == "ok"
        assert result.reseller_id == reseller.id
        assert result.authenticated_as == owner.id


class TestGetSpending:
    """Tests for GET /reseller/spending."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_spending_status(self, db, reseller_setup):
        """Spending endpoint returns limits and current spend."""
        from src.api.routes.reseller import get_spending

        owner, reseller, quota = reseller_setup
        auth = _make_auth_context(reseller.id, owner.id)

        result = await get_spending(auth, db)
        assert result.limits.monthly_usd == 1000.0
        assert result.current.monthly_usd == pytest.approx(5.0)
        assert result.status in ("ok", "warning", "exceeded")
