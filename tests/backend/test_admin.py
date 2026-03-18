"""
Tests for admin API endpoints.

Covers:
- Admin authentication enforcement
- Reseller CRUD via admin API
- User management (cross-reseller)
- Platform stats and audit log
"""
import json
import uuid
from typing import AsyncGenerator

import bcrypt
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.db.database import Base
from src.db.models import Reseller, ResellerQuota, User


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
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture
async def db(db_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Database session for direct DB manipulation in tests."""
    async with db_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession) -> User:
    """Create an admin user for testing."""
    user = User(
        id=str(uuid.uuid4()),
        username="test_admin",
        email="admin@test.com",
        password_hash=bcrypt.hashpw(
            b"admin_password", bcrypt.gensalt()
        ).decode(),
        role="admin",
        jwt_secret=uuid.uuid4().hex,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def reseller_with_owner(db: AsyncSession) -> tuple[Reseller, User]:
    """Create a reseller with owner user for testing."""
    owner = User(
        id=str(uuid.uuid4()),
        username="reseller_testco",
        email="owner@testco.com",
        password_hash=bcrypt.hashpw(
            b"reseller_password", bcrypt.gensalt()
        ).decode(),
        role="reseller",
        jwt_secret=uuid.uuid4().hex,
        is_active=True,
    )
    db.add(owner)
    await db.flush()

    reseller = Reseller(
        id=str(uuid.uuid4()),
        name="TestCo",
        company="TestCo Ltd",
        contact_email="admin@testco.com",
        owner_user_id=owner.id,
        max_users=50,
        max_concurrent_tasks=10,
        max_daily_tasks=500,
    )
    db.add(reseller)
    await db.flush()

    quota = ResellerQuota(
        reseller_id=reseller.id,
        current_user_count=0,
    )
    db.add(quota)
    await db.commit()
    await db.refresh(reseller)
    await db.refresh(owner)
    return reseller, owner


def _mock_admin_auth(admin_user: User):
    """Create a mock AuthContext for admin auth."""
    from src.api.deps import AuthContext
    return AuthContext(
        user_id=admin_user.id,
        role="admin",
        reseller_id=None,
    )



# =============================================================================
# Tests
# =============================================================================

class TestAdminAuthEnforcement:
    """Verify that admin endpoints reject non-admin callers."""

    @pytest.mark.asyncio
    async def test_require_admin_rejects_regular_user(self):
        """Non-admin users should get 403."""
        from src.api.routes.admin import _require_admin
        from src.api.deps import AuthContext

        auth = AuthContext(
            user_id=str(uuid.uuid4()),
            role="user",
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await _require_admin(auth)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_admin_accepts_admin(self, admin_user):
        """Admin users should pass."""
        from src.api.routes.admin import _require_admin

        auth = _mock_admin_auth(admin_user)
        result = await _require_admin(auth)
        assert result.is_admin


class TestResellerCRUDViaAdmin:
    """Test reseller management through admin endpoints."""

    @pytest.mark.asyncio
    async def test_build_reseller_response(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test the response builder for reseller data."""
        from sqlalchemy.orm import selectinload

        from src.api.routes.admin import _build_reseller_response
        from src.db.models import Reseller as ResellerModel

        reseller, owner = reseller_with_owner
        # Eagerly load relationships to avoid MissingGreenlet
        result = await db.execute(
            select(ResellerModel)
            .options(
                selectinload(ResellerModel.owner),
                selectinload(ResellerModel.quota),
            )
            .where(ResellerModel.id == reseller.id)
        )
        loaded = result.scalar_one()
        response = _build_reseller_response(loaded)
        assert response.id == reseller.id
        assert response.name == "TestCo"
        assert response.is_active is True
        assert response.limits.max_users == 50

    @pytest.mark.asyncio
    async def test_reseller_service_create(self, db: AsyncSession):
        """Test creating a reseller via service."""
        from src.services.reseller_service import reseller_service

        reseller = await reseller_service.create_reseller(
            db=db,
            name="NewReseller",
            company="New Corp",
            contact_email="new@corp.com",
            password="secure_password_123",
            max_users=25,
        )
        assert reseller is not None
        assert reseller.name == "NewReseller"
        assert reseller.max_users == 25
        assert reseller.owner_user_id is not None

    @pytest.mark.asyncio
    async def test_reseller_service_get(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test getting a reseller by ID."""
        from src.services.reseller_service import reseller_service

        reseller, _ = reseller_with_owner
        fetched = await reseller_service.get_reseller(db, reseller.id)
        assert fetched is not None
        assert fetched.name == "TestCo"

    @pytest.mark.asyncio
    async def test_reseller_service_suspend(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test suspending a reseller."""
        from src.services.reseller_service import reseller_service

        reseller, _ = reseller_with_owner
        result = await reseller_service.suspend_reseller(
            db, reseller.id, reason="Test suspension"
        )
        assert result is not None

        # Verify reseller is now inactive
        fetched = await reseller_service.get_reseller(db, reseller.id)
        assert fetched.is_active is False
        assert fetched.suspended_at is not None

    @pytest.mark.asyncio
    async def test_reseller_service_unsuspend(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test unsuspending a reseller."""
        from src.services.reseller_service import reseller_service

        reseller, _ = reseller_with_owner

        # Suspend first
        await reseller_service.suspend_reseller(db, reseller.id)

        # Then unsuspend
        result = await reseller_service.unsuspend_reseller(db, reseller.id)
        assert result is not None

        # Verify reseller is active again
        fetched = await reseller_service.get_reseller(db, reseller.id)
        assert fetched.is_active is True
        assert fetched.suspended_at is None

    @pytest.mark.asyncio
    async def test_reseller_service_delete(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test deleting a reseller."""
        from src.services.reseller_service import reseller_service

        reseller, _ = reseller_with_owner
        result = await reseller_service.delete_reseller(db, reseller.id)
        assert result is not None

        # Verify reseller is gone
        fetched = await reseller_service.get_reseller(db, reseller.id)
        assert fetched is None

    @pytest.mark.asyncio
    async def test_reseller_service_list(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test listing resellers."""
        from src.services.reseller_service import reseller_service

        resellers, total = await reseller_service.list_resellers(db)
        assert total >= 1
        assert any(r.name == "TestCo" for r in resellers)

    @pytest.mark.asyncio
    async def test_reseller_service_stats(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test getting reseller stats."""
        from src.services.reseller_service import reseller_service

        reseller, _ = reseller_with_owner
        stats = await reseller_service.get_reseller_stats(db, reseller.id)
        assert "user_count" in stats
        assert "total_sessions" in stats


class TestAdminUserManagement:
    """Test admin-level user management."""

    @pytest.mark.asyncio
    async def test_admin_can_create_direct_user(self, db: AsyncSession):
        """Admin can create a user without reseller."""
        user = User(
            id=str(uuid.uuid4()),
            username="direct_user",
            email="direct@test.com",
            password_hash=bcrypt.hashpw(
                b"password123", bcrypt.gensalt()
            ).decode(),
            role="user",
            jwt_secret=uuid.uuid4().hex,
            is_active=True,
            reseller_id=None,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        assert user.reseller_id is None
        assert user.role == "user"

    @pytest.mark.asyncio
    async def test_admin_can_suspend_any_user(self, db: AsyncSession):
        """Admin can suspend any user."""
        user = User(
            id=str(uuid.uuid4()),
            username="suspend_me",
            email="suspend@test.com",
            password_hash=bcrypt.hashpw(
                b"password123", bcrypt.gensalt()
            ).decode(),
            role="user",
            jwt_secret=uuid.uuid4().hex,
            is_active=True,
        )
        db.add(user)
        await db.commit()

        user.is_active = False
        await db.commit()
        await db.refresh(user)
        assert user.is_active is False


class TestServiceIntegration:
    """Test service layer methods used by admin endpoints."""

    @pytest.mark.asyncio
    async def test_api_key_service_create_and_validate(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test API key creation and validation."""
        from src.services.api_key_service import api_key_service

        reseller, owner = reseller_with_owner
        record, raw_key = await api_key_service.create_key(
            db=db,
            reseller_id=reseller.id,
            user_id=owner.id,
            name="Test Key",
            scopes=["users:read", "users:create"],
        )
        assert record is not None
        assert raw_key.startswith("ag3_res_")
        assert record.key_prefix == raw_key[:16]

        # Validate the key
        validated = await api_key_service.validate_key(db, raw_key)
        assert validated is not None
        assert validated.id == record.id

    @pytest.mark.asyncio
    async def test_api_key_service_revoke(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test API key revocation."""
        from src.services.api_key_service import api_key_service

        reseller, owner = reseller_with_owner
        record, raw_key = await api_key_service.create_key(
            db=db,
            reseller_id=reseller.id,
            user_id=owner.id,
            name="Revoke Me",
            scopes=["users:read"],
        )

        revoked = await api_key_service.revoke_key(
            db, record.id, reseller.id
        )
        assert revoked is True

        # Validate should fail now
        validated = await api_key_service.validate_key(db, raw_key)
        assert validated is None

    @pytest.mark.asyncio
    async def test_feature_flag_service_resolve(self):
        """Test feature flag resolution."""
        from src.services.feature_flag_service import feature_flag_service

        # No overrides = defaults
        features = feature_flag_service.resolve_features(None, None)
        assert features["ssh_enabled"] is False
        assert features["web_fetch_enabled"] is False

        # Reseller override
        reseller_json = json.dumps({"web_fetch_enabled": True})
        features = feature_flag_service.resolve_features(reseller_json, None)
        assert features["web_fetch_enabled"] is True

        # User override on top of reseller
        user_json = json.dumps({"max_session_minutes": 60})
        features = feature_flag_service.resolve_features(
            reseller_json, user_json
        )
        assert features["web_fetch_enabled"] is True
        assert features["max_session_minutes"] == 60

    @pytest.mark.asyncio
    async def test_feature_flag_validate_override(self):
        """Test override ceiling validation."""
        from src.services.feature_flag_service import feature_flag_service

        ceiling = {"max_session_minutes": 30, "ssh_enabled": False}

        # Within ceiling
        valid, _ = feature_flag_service.validate_override(
            "max_session_minutes", 20, ceiling
        )
        assert valid is True

        # Exceeds ceiling
        valid, msg = feature_flag_service.validate_override(
            "max_session_minutes", 60, ceiling
        )
        assert valid is False
        assert "cannot exceed" in msg

        # Restricted boolean
        valid, msg = feature_flag_service.validate_override(
            "ssh_enabled", True, ceiling
        )
        assert valid is False

    @pytest.mark.asyncio
    async def test_spending_guard_check_budget(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test spending guard budget check."""
        from src.services.spending_guard import spending_guard

        reseller, owner = reseller_with_owner

        # Create a user under the reseller
        user = User(
            id=str(uuid.uuid4()),
            username="spending_test_user",
            email="spending@test.com",
            password_hash=bcrypt.hashpw(
                b"password123", bcrypt.gensalt()
            ).decode(),
            role="user",
            jwt_secret=uuid.uuid4().hex,
            is_active=True,
            reseller_id=reseller.id,
        )
        db.add(user)
        await db.commit()

        # No spending limits = should pass
        can_start, reason = await spending_guard.check_budget(db, user.id)
        assert can_start is True

    @pytest.mark.asyncio
    async def test_spending_guard_session_limit(self, db: AsyncSession):
        """Test per-session spending check."""
        from src.services.spending_guard import spending_guard

        user = User(
            id=str(uuid.uuid4()),
            username="session_limit_user",
            email="session_limit@test.com",
            password_hash=bcrypt.hashpw(
                b"password123", bcrypt.gensalt()
            ).decode(),
            role="user",
            jwt_secret=uuid.uuid4().hex,
            is_active=True,
            spending_limit_per_session_usd=5.0,
        )
        db.add(user)
        await db.commit()

        # Under limit
        can, _ = await spending_guard.check_session_budget(
            db, user.id, 3.0
        )
        assert can is True

        # Over limit
        can, reason = await spending_guard.check_session_budget(
            db, user.id, 6.0
        )
        assert can is False
        assert "Per-session" in reason

    @pytest.mark.asyncio
    async def test_reseller_quota_service(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test reseller quota checks."""
        from src.services.reseller_quota_service import (
            reseller_quota_service,
        )

        reseller, owner = reseller_with_owner

        # Create a managed user
        user = User(
            id=str(uuid.uuid4()),
            username="quota_test_user",
            email="quota@test.com",
            password_hash=bcrypt.hashpw(
                b"password123", bcrypt.gensalt()
            ).decode(),
            role="user",
            jwt_secret=uuid.uuid4().hex,
            is_active=True,
            reseller_id=reseller.id,
        )
        db.add(user)
        await db.commit()

        # Should pass (no tasks today)
        can_start, reason = (
            await reseller_quota_service.check_reseller_quota(db, user.id)
        )
        assert can_start is True

    @pytest.mark.asyncio
    async def test_usage_service_record(
        self, db: AsyncSession, reseller_with_owner
    ):
        """Test usage recording."""
        from src.services.usage_service import usage_service

        reseller, owner = reseller_with_owner
        record = await usage_service.record_session_usage(
            db=db,
            session_id="test-session-001",
            user_id=owner.id,
            reseller_id=reseller.id,
            model="claude-sonnet-4-20250514",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.05,
            duration_ms=30000,
            num_turns=5,
        )
        assert record is not None
        assert record.cost_usd == 0.05
        assert record.input_tokens == 1000


class TestPlatformConfig:
    """Test GET /admin/config endpoint."""

    @pytest.mark.asyncio
    async def test_get_platform_config_returns_defaults(self, admin_user, db):
        """Platform config returns default features, quotas, and spending."""
        from src.api.routes.admin import get_platform_config

        auth = _mock_admin_auth(admin_user)
        result = await get_platform_config(db=db, auth=auth)

        assert hasattr(result, "default_features")
        assert hasattr(result, "default_quotas")
        assert hasattr(result, "default_spending_limits")
        assert hasattr(result, "default_settings_mode")
        assert hasattr(result, "default_allowed_overrides")

        # Features should match DEFAULT_FEATURES
        from src.services.feature_flag_service import DEFAULT_FEATURES
        assert result.default_features == DEFAULT_FEATURES

        # Quotas
        assert result.default_quotas["global_max_concurrent"] == 4
        assert result.default_quotas["per_user_max_concurrent"] == 2
        assert result.default_quotas["per_user_daily_limit"] == 50

        # Settings mode
        assert result.default_settings_mode == "readonly"
        assert result.default_allowed_overrides == []


class TestAuthContext:
    """Test AuthContext dataclass."""

    def test_admin_properties(self):
        """Test is_admin property."""
        from src.api.deps import AuthContext
        auth = AuthContext(user_id="uid", role="admin")
        assert auth.is_admin is True
        assert auth.is_reseller is False

    def test_reseller_properties(self):
        """Test is_reseller property."""
        from src.api.deps import AuthContext
        auth = AuthContext(
            user_id="uid", role="reseller", reseller_id="rid"
        )
        assert auth.is_reseller is True
        assert auth.is_admin is False

    def test_jwt_has_all_scopes(self):
        """JWT auth (no API key) has all scopes."""
        from src.api.deps import AuthContext
        auth = AuthContext(user_id="uid", role="reseller")
        assert auth.has_scope("users:create") is True
        assert auth.has_scope("anything") is True

    def test_api_key_scope_check(self):
        """API key auth checks specific scopes."""
        from src.api.deps import AuthContext
        auth = AuthContext(
            user_id="uid",
            role="reseller",
            api_key_id="key123",
            api_key_scopes=["users:read", "users:create"],
        )
        assert auth.has_scope("users:read") is True
        assert auth.has_scope("users:create") is True
        assert auth.has_scope("users:delete") is False
