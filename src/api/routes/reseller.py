"""Reseller API endpoints for user management, API keys, usage, and configuration."""
import csv
import hashlib
import io
import json
import logging
import math
import uuid
from calendar import monthrange
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import (
    APIKey, Reseller, ResellerQuota, ResellerSkillLibrary,
    Session, User, UserQuota, UserSkill, UsageRecord,
)
from ...services.api_key_service import api_key_service
from ...services.feature_flag_service import feature_flag_service
from ...services.spending_guard import spending_guard
from ...services.usage_service import usage_service
from ...services.webhook_service import webhook_service
from ..deps import AuthContext, require_reseller
from ..reseller_models import (
    APIKeyCreatedResponse, APIKeyListResponse, APIKeyResponse,
    AssignSkillRequest, ChangePasswordRequest, ConnectionTestResponse,
    CreateAPIKeyRequest, CreateResellerUserRequest, CreateWebhookRequest,
    DeleteUserResponse, PaginationInfo, PasswordChangedResponse,
    ResellerProfileResponse, ResellerUserListResponse, ResellerUserResponse,
    ResellerUserQuota,
    SetEnvVarsRequest, SetSettingsModeRequest, SetSpendingLimitsRequest,
    SkillResponse, SpendingCurrent, SpendingLimits, SpendingStatusResponse,
    SuspendRequest, SuspendUserResponse, UpdateResellerUserRequest,
    UpdateSecurityConfigRequest, UpdateSSHFiltersRequest,
    UpdateUserConfigRequest, UpdateWebhookRequest, UploadSkillRequest,
    UsagePeriod, UsageResponse, UsageTotals, UserConfigResponse,
    UserSkillsResponse, UserUsageBreakdown, UserUsageResponse,
    WebhookCreatedResponse, WebhookDeliveryListResponse,
    WebhookDeliveryResponse, WebhookEndpointResponse,
    WebhookListResponse, WhmcsMetricsResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reseller", tags=["reseller"])

from src.api.routes._helpers import _read_version


def _parse_json_field(value: Optional[str], default=None):
    """Parse a JSON string field, returning default on failure."""
    if default is None:
        default = {}
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _spending_response_from_guard(spending_data: dict) -> SpendingStatusResponse:
    """Build SpendingStatusResponse from spending_guard.get_spending_status()."""
    sp_limits = spending_data.get("limits", {})
    sp_current = spending_data.get("current", {})
    return SpendingStatusResponse(
        limits=SpendingLimits(
            monthly_usd=sp_limits.get("monthly_usd"),
            daily_usd=sp_limits.get("daily_usd"),
            per_session_usd=sp_limits.get("per_session_usd"),
        ),
        current=SpendingCurrent(
            monthly_usd=sp_current.get("monthly_usd", 0.0),
            daily_usd=sp_current.get("daily_usd", 0.0),
        ),
        status=spending_data.get("status", "ok"),
    )


def _safe_metadata(metadata_json: Optional[str]) -> Optional[dict]:
    """Parse metadata JSON, stripping env_vars to prevent secret leakage."""
    if not metadata_json:
        return None
    try:
        meta = json.loads(metadata_json)
    except (json.JSONDecodeError, TypeError):
        return None
    meta.pop("env_vars", None)
    return meta or None


# =============================================================================
# IDOR Prevention Helper
# =============================================================================

async def _get_owned_user(db: AsyncSession, auth: AuthContext, user_id: str) -> User:
    """Get a user that belongs to the authenticated reseller.

    Raises 404 if not found or not owned by this reseller. This prevents IDOR
    attacks where one reseller accesses another's users.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or user.reseller_id != auth.reseller_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _get_reseller(db: AsyncSession, auth: AuthContext) -> Reseller:
    """Fetch the Reseller record for the authenticated context.

    Raises 403 if the reseller record cannot be found.
    """
    if not auth.reseller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No reseller account associated with this token",
        )
    result = await db.execute(select(Reseller).where(Reseller.id == auth.reseller_id))
    reseller = result.scalar_one_or_none()
    if not reseller:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reseller record not found",
        )
    return reseller


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    """Return (start, end) UTC datetimes for 'current_month' or 'last_month'."""
    now = datetime.now(timezone.utc)
    if period == "last_month":
        if now.month == 1:
            year, month = now.year - 1, 12
        else:
            year, month = now.year, now.month - 1
        _, last_day = monthrange(year, month)
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    else:
        # default: current_month
        _, last_day = monthrange(now.year, now.month)
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        end = datetime(now.year, now.month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return start, end


def _api_key_to_response(key: APIKey) -> APIKeyResponse:
    """Convert an APIKey ORM record to an APIKeyResponse model."""
    try:
        scopes = json.loads(key.scopes) if key.scopes else []
    except (json.JSONDecodeError, TypeError):
        scopes = []
    try:
        ip_allowlist = json.loads(key.ip_allowlist) if key.ip_allowlist else None
    except (json.JSONDecodeError, TypeError):
        ip_allowlist = None
    return APIKeyResponse(
        id=key.id,
        key_prefix=key.key_prefix,
        name=key.name,
        scopes=scopes,
        ip_allowlist=ip_allowlist,
        rate_limit_per_minute=key.rate_limit_per_minute,
        is_active=key.is_active,
        last_used_at=key.last_used_at,
        last_used_ip=key.last_used_ip,
        expires_at=key.expires_at,
        created_at=key.created_at,
    )


# =============================================================================
# Self-Service
# =============================================================================

@router.get("/profile", response_model=ResellerProfileResponse)
async def get_profile(
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> ResellerProfileResponse:
    """Get the authenticated reseller's own profile."""
    reseller = await _get_reseller(db, auth)

    try:
        features = json.loads(reseller.features_json) if reseller.features_json else {}
    except (json.JSONDecodeError, TypeError):
        features = {}

    result = await db.execute(
        select(ResellerQuota).where(ResellerQuota.reseller_id == reseller.id)
    )
    quota = result.scalar_one_or_none()

    spending_resp = None
    if quota:
        quota.reset_if_needed()
        spending_resp = SpendingStatusResponse(
            limits=SpendingLimits(
                monthly_usd=reseller.max_monthly_spending_usd,
                daily_usd=reseller.max_daily_spending_usd,
            ),
            current=SpendingCurrent(
                monthly_usd=quota.monthly_cost_usd,
                daily_usd=quota.daily_cost_usd,
            ),
            alert_threshold_pct=reseller.spending_alert_threshold_pct,
            status="ok",
        )

    result = await db.execute(
        select(func.count(User.id)).where(User.reseller_id == reseller.id)
    )
    current_users = result.scalar() or 0

    limits = {
        "max_users": reseller.max_users,
        "current_users": current_users,
        "max_concurrent_tasks": reseller.max_concurrent_tasks,
        "max_daily_tasks": reseller.max_daily_tasks,
    }

    return ResellerProfileResponse(
        id=reseller.id,
        name=reseller.name,
        company=reseller.company,
        contact_email=reseller.contact_email,
        is_active=reseller.is_active,
        limits=limits,
        llm_provider=reseller.llm_provider,
        features=features,
        spending=spending_resp,
        created_at=reseller.created_at,
    )


@router.get("/test-connection", response_model=ConnectionTestResponse)
async def test_connection(
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> ConnectionTestResponse:
    """Verify API connectivity and authentication."""
    reseller = await _get_reseller(db, auth)
    return ConnectionTestResponse(
        status="ok",
        authenticated_as=auth.user_id,
        reseller_id=reseller.id,
        server_version=_read_version(),
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/spending", response_model=SpendingStatusResponse)
async def get_spending(
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> SpendingStatusResponse:
    """Get the reseller's current spending status."""
    reseller = await _get_reseller(db, auth)

    result = await db.execute(
        select(ResellerQuota).where(ResellerQuota.reseller_id == reseller.id)
    )
    quota = result.scalar_one_or_none()

    monthly_usd = 0.0
    daily_usd = 0.0
    if quota:
        quota.reset_if_needed()
        monthly_usd = quota.monthly_cost_usd
        daily_usd = quota.daily_cost_usd

    sp_status = "ok"
    if reseller.max_monthly_spending_usd and reseller.max_monthly_spending_usd > 0:
        pct = (monthly_usd / reseller.max_monthly_spending_usd) * 100
        if pct >= 100:
            sp_status = "exceeded"
        elif pct >= reseller.spending_alert_threshold_pct:
            sp_status = "warning"

    return SpendingStatusResponse(
        limits=SpendingLimits(
            monthly_usd=reseller.max_monthly_spending_usd,
            daily_usd=reseller.max_daily_spending_usd,
        ),
        current=SpendingCurrent(
            monthly_usd=monthly_usd,
            daily_usd=daily_usd,
        ),
        alert_threshold_pct=reseller.spending_alert_threshold_pct,
        status=sp_status,
    )


# =============================================================================
# User Management
# =============================================================================

@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=ResellerUserResponse)
async def create_user(
    body: CreateResellerUserRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> ResellerUserResponse:
    """Create a new user under this reseller.

    Checks:
    - Scope 'users:create'
    - User count does not exceed reseller.max_users
    - Username and email are unique

    Returns the created user record (201).
    """
    if not auth.has_scope("users:create"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: users:create",
        )

    reseller = await _get_reseller(db, auth)

    # Enforce user count limit
    result = await db.execute(
        select(func.count(User.id)).where(User.reseller_id == reseller.id)
    )
    current_count = result.scalar() or 0
    if current_count >= reseller.max_users:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"User limit reached ({reseller.max_users}). Upgrade your plan to add more users.",
        )

    # Check username uniqueness
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is already taken",
        )

    # Check email uniqueness
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{body.email}' is already registered",
        )

    password_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    user_id = str(uuid.uuid4())

    features_json = None
    if body.feature_overrides:
        features_json = json.dumps(body.feature_overrides)

    metadata_json = None
    if body.metadata:
        metadata_json = json.dumps(body.metadata)

    user = User(
        id=user_id,
        username=body.username,
        email=body.email,
        password_hash=password_hash,
        role="user",
        jwt_secret=uuid.uuid4().hex,
        linux_uid=None,
        is_active=True,
        reseller_id=reseller.id,
        features_json=features_json,
        metadata_json=metadata_json,
    )
    db.add(user)
    await db.flush()

    # Apply quota overrides (fall back to defaults)
    max_concurrent = 2
    max_daily = 50
    if body.quota_overrides:
        max_concurrent = body.quota_overrides.get("max_concurrent_tasks", max_concurrent)
        max_daily = body.quota_overrides.get("max_daily_tasks", max_daily)

    quota = UserQuota(
        user_id=user_id,
        max_concurrent_tasks=max_concurrent,
        max_daily_tasks=max_daily,
        tasks_today=0,
        last_reset=datetime.now(timezone.utc),
    )
    db.add(quota)

    # Increment reseller quota counter
    result = await db.execute(
        select(ResellerQuota).where(ResellerQuota.reseller_id == reseller.id)
    )
    rq = result.scalar_one_or_none()
    if rq:
        rq.current_user_count += 1

    await db.commit()
    await db.refresh(user)

    logger.info("Reseller %s created user %s (id=%s)", reseller.id, body.username, user_id)

    meta = _safe_metadata(user.metadata_json)

    try:
        features = json.loads(user.features_json) if user.features_json else None
    except (json.JSONDecodeError, TypeError):
        features = None

    return ResellerUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        quota=ResellerUserQuota(
            max_concurrent_tasks=max_concurrent,
            max_daily_tasks=max_daily,
            tasks_today=0,
        ),
        features=features,
        metadata=meta,
    )


@router.get("/users", response_model=ResellerUserListResponse)
async def list_users(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    search: Optional[str] = Query(default=None),
    filter_status: Optional[str] = Query(default=None, alias="status"),
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> ResellerUserListResponse:
    """List users managed by this reseller with pagination and optional filtering."""
    if not auth.has_scope("users:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: users:read",
        )

    query = select(User).where(User.reseller_id == auth.reseller_id)

    if search:
        query = query.where(
            User.username.ilike(f"%{search}%") | User.email.ilike(f"%{search}%")
        )

    if filter_status == "active":
        query = query.where(User.is_active.is_(True))
    elif filter_status == "suspended":
        query = query.where(User.is_active.is_(False))

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one() or 0

    offset = (page - 1) * per_page
    paged_result = await db.execute(
        query.order_by(User.created_at.desc()).offset(offset).limit(per_page)
    )
    users = list(paged_result.scalars().all())

    total_pages = math.ceil(total / per_page) if total > 0 else 1

    user_responses = []
    for u in users:
        result = await db.execute(
            select(UserQuota).where(UserQuota.user_id == u.id)
        )
        uq = result.scalar_one_or_none()

        sessions_result = await db.execute(
            select(func.count(Session.id)).where(Session.user_id == u.id)
        )
        sessions_total = sessions_result.scalar() or 0

        last_session_result = await db.execute(
            select(Session.created_at)
            .where(Session.user_id == u.id)
            .order_by(Session.created_at.desc())
            .limit(1)
        )
        last_session_at = last_session_result.scalar_one_or_none()

        meta = _safe_metadata(u.metadata_json)

        user_responses.append(ResellerUserResponse(
            id=u.id,
            username=u.username,
            email=u.email,
            is_active=u.is_active,
            created_at=u.created_at,
            updated_at=u.updated_at,
            last_session_at=last_session_at,
            sessions_total=sessions_total,
            quota=ResellerUserQuota(
                max_concurrent_tasks=uq.max_concurrent_tasks if uq else 2,
                max_daily_tasks=uq.max_daily_tasks if uq else 50,
                tasks_today=uq.tasks_today if uq else 0,
            ) if uq else None,
            metadata=meta,
        ))

    return ResellerUserListResponse(
        users=user_responses,
        pagination=PaginationInfo(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
        ),
    )


@router.get("/users/{user_id}", response_model=ResellerUserResponse)
async def get_user(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> ResellerUserResponse:
    """Get details for a specific user owned by this reseller."""
    if not auth.has_scope("users:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: users:read",
        )

    user = await _get_owned_user(db, auth, user_id)

    result = await db.execute(select(UserQuota).where(UserQuota.user_id == user.id))
    uq = result.scalar_one_or_none()

    sessions_result = await db.execute(
        select(func.count(Session.id)).where(Session.user_id == user.id)
    )
    sessions_total = sessions_result.scalar() or 0

    last_session_result = await db.execute(
        select(Session.created_at)
        .where(Session.user_id == user.id)
        .order_by(Session.created_at.desc())
        .limit(1)
    )
    last_session_at = last_session_result.scalar_one_or_none()

    meta = _safe_metadata(user.metadata_json)

    try:
        features = json.loads(user.features_json) if user.features_json else None
    except (json.JSONDecodeError, TypeError):
        features = None

    return ResellerUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_session_at=last_session_at,
        sessions_total=sessions_total,
        quota=ResellerUserQuota(
            max_concurrent_tasks=uq.max_concurrent_tasks if uq else 2,
            max_daily_tasks=uq.max_daily_tasks if uq else 50,
            tasks_today=uq.tasks_today if uq else 0,
        ) if uq else None,
        features=features,
        metadata=meta,
    )


@router.put("/users/{user_id}", response_model=ResellerUserResponse)
async def update_user(
    user_id: str,
    body: UpdateResellerUserRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> ResellerUserResponse:
    """Update a user's email, quota overrides, feature overrides, or metadata."""
    if not auth.has_scope("users:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: users:update",
        )

    user = await _get_owned_user(db, auth, user_id)

    if body.email is not None:
        # Check email uniqueness (exclude self)
        result = await db.execute(
            select(User).where(User.email == body.email, User.id != user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' is already registered",
            )
        user.email = body.email

    if body.feature_overrides is not None:
        user.features_json = json.dumps(body.feature_overrides)

    if body.metadata is not None:
        user.metadata_json = json.dumps(body.metadata)

    if body.quota_overrides is not None:
        result = await db.execute(select(UserQuota).where(UserQuota.user_id == user.id))
        uq = result.scalar_one_or_none()
        if uq:
            if "max_concurrent_tasks" in body.quota_overrides:
                uq.max_concurrent_tasks = int(body.quota_overrides["max_concurrent_tasks"])
            if "max_daily_tasks" in body.quota_overrides:
                uq.max_daily_tasks = int(body.quota_overrides["max_daily_tasks"])

    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    result = await db.execute(select(UserQuota).where(UserQuota.user_id == user.id))
    uq = result.scalar_one_or_none()

    meta = _safe_metadata(user.metadata_json)

    return ResellerUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        quota=ResellerUserQuota(
            max_concurrent_tasks=uq.max_concurrent_tasks if uq else 2,
            max_daily_tasks=uq.max_daily_tasks if uq else 50,
            tasks_today=uq.tasks_today if uq else 0,
        ) if uq else None,
        metadata=meta,
    )


@router.post("/users/{user_id}/suspend", response_model=SuspendUserResponse)
async def suspend_user(
    user_id: str,
    body: Optional[SuspendRequest] = None,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> SuspendUserResponse:
    """Suspend a user account, cancelling all active sessions."""
    if not auth.has_scope("users:suspend"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: users:suspend",
        )

    user = await _get_owned_user(db, auth, user_id)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already suspended",
        )

    # Cancel active sessions
    active_statuses = ("pending", "running", "queued")
    sessions_result = await db.execute(
        select(Session).where(
            Session.user_id == user.id,
            Session.status.in_(active_statuses),
        )
    )
    sessions = list(sessions_result.scalars().all())
    for sess in sessions:
        sess.status = "cancelled"

    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(
        "Reseller %s suspended user %s, cancelled %d sessions",
        auth.reseller_id, user_id, len(sessions),
    )

    return SuspendUserResponse(
        id=user.id,
        username=user.username,
        is_active=user.is_active,
        suspended_at=datetime.now(timezone.utc),
        active_sessions_cancelled=len(sessions),
    )


@router.post("/users/{user_id}/unsuspend", response_model=SuspendUserResponse)
async def unsuspend_user(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> SuspendUserResponse:
    """Restore a suspended user account."""
    if not auth.has_scope("users:suspend"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: users:suspend",
        )

    user = await _get_owned_user(db, auth, user_id)

    if user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is not suspended",
        )

    user.is_active = True
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("Reseller %s unsuspended user %s", auth.reseller_id, user_id)

    return SuspendUserResponse(
        id=user.id,
        username=user.username,
        is_active=user.is_active,
        active_sessions_cancelled=0,
    )


@router.post("/users/{user_id}/change-password", response_model=PasswordChangedResponse)
async def change_user_password(
    user_id: str,
    body: ChangePasswordRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> PasswordChangedResponse:
    """Set a new password for a managed user and revoke all existing tokens."""
    if not auth.has_scope("users:password"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: users:password",
        )

    user = await _get_owned_user(db, auth, user_id)

    new_hash = bcrypt.hashpw(body.new_password.encode(), bcrypt.gensalt()).decode()
    user.password_hash = new_hash
    # Increment token_version to invalidate all issued JWTs
    user.token_version = (user.token_version or 0) + 1
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("Reseller %s changed password for user %s", auth.reseller_id, user_id)

    return PasswordChangedResponse(status="password_changed", tokens_revoked=True)


@router.delete("/users/{user_id}", response_model=DeleteUserResponse)
async def delete_user(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> DeleteUserResponse:
    """Permanently delete a managed user and all associated data."""
    if not auth.has_scope("users:delete"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: users:delete",
        )

    user = await _get_owned_user(db, auth, user_id)
    username = user.username

    # Count sessions before deletion
    sessions_result = await db.execute(
        select(func.count(Session.id)).where(Session.user_id == user.id)
    )
    sessions_deleted = sessions_result.scalar() or 0

    # Delete the user (cascade deletes sessions, tokens, quota, vault secrets)
    await db.delete(user)

    # Decrement reseller quota user count
    rq_result = await db.execute(
        select(ResellerQuota).where(ResellerQuota.reseller_id == auth.reseller_id)
    )
    rq = rq_result.scalar_one_or_none()
    if rq and rq.current_user_count > 0:
        rq.current_user_count -= 1

    await db.commit()

    logger.info(
        "Reseller %s deleted user %s (%s sessions removed)",
        auth.reseller_id, user_id, sessions_deleted,
    )

    return DeleteUserResponse(
        status="deleted",
        id=user_id,
        username=username,
        sessions_deleted=sessions_deleted,
        files_cleaned=True,
    )


# =============================================================================
# API Key Management
# =============================================================================

@router.post("/api-keys", status_code=status.HTTP_201_CREATED, response_model=APIKeyCreatedResponse)
async def create_api_key(
    body: CreateAPIKeyRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> APIKeyCreatedResponse:
    """Create a new API key for this reseller.

    The full key value is returned only once and cannot be recovered.
    """
    if not auth.has_scope("keys:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: keys:manage",
        )

    record, raw_key = await api_key_service.create_key(
        db=db,
        reseller_id=auth.reseller_id,
        user_id=auth.user_id,
        name=body.name,
        scopes=body.scopes,
        ip_allowlist=body.ip_allowlist,
        rate_limit=body.rate_limit_per_minute,
        expires_at=body.expires_at,
    )

    try:
        scopes = json.loads(record.scopes) if record.scopes else []
    except (json.JSONDecodeError, TypeError):
        scopes = []

    return APIKeyCreatedResponse(
        id=record.id,
        key_prefix=record.key_prefix,
        name=record.name,
        scopes=scopes,
        ip_allowlist=body.ip_allowlist,
        rate_limit_per_minute=record.rate_limit_per_minute,
        is_active=record.is_active,
        expires_at=record.expires_at,
        created_at=record.created_at,
        key=raw_key,
    )


@router.get("/api-keys", response_model=APIKeyListResponse)
async def list_api_keys(
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> APIKeyListResponse:
    """List all API keys for this reseller."""
    if not auth.has_scope("keys:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: keys:manage",
        )

    if not auth.reseller_id:
        raise HTTPException(status_code=403, detail="No reseller context")
    keys = await api_key_service.list_keys(db, auth.reseller_id)
    return APIKeyListResponse(api_keys=[_api_key_to_response(k) for k in keys])


@router.post("/api-keys/{key_id}/rotate")
async def rotate_api_key(
    key_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Rotate an API key.

    Creates a new key with the same configuration. The old key remains active
    for 24 hours to allow in-flight requests to complete, then expires.
    The new key's full value is returned only once.
    Returns the new key data plus old_key_id and old_key_expires_at.
    """
    if not auth.has_scope("keys:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: keys:manage",
        )

    if not auth.reseller_id:
        raise HTTPException(status_code=403, detail="No reseller context")
    try:
        new_record, raw_key, rotation_info = await api_key_service.rotate_key(
            db=db,
            key_id=key_id,
            reseller_id=auth.reseller_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    try:
        scopes = json.loads(new_record.scopes) if new_record.scopes else []
    except (json.JSONDecodeError, TypeError):
        scopes = []

    resp = APIKeyCreatedResponse(
        id=new_record.id,
        key_prefix=new_record.key_prefix,
        name=new_record.name,
        scopes=scopes,
        rate_limit_per_minute=new_record.rate_limit_per_minute,
        is_active=new_record.is_active,
        expires_at=new_record.expires_at,
        created_at=new_record.created_at,
        key=raw_key,
    )
    # Include rotation metadata in response
    return {
        **resp.model_dump(),
        "old_key_id": rotation_info["old_key_id"],
        "old_key_expires_at": rotation_info["old_key_expires_at"].isoformat(),
    }


@router.post("/api-keys/{key_id}/revoke")
async def revoke_api_key(
    key_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Immediately revoke an API key, making it permanently inactive."""
    if not auth.has_scope("keys:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: keys:manage",
        )

    if not auth.reseller_id:
        raise HTTPException(status_code=403, detail="No reseller context")
    revoked = await api_key_service.revoke_key(db, key_id, auth.reseller_id)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    return {"status": "revoked", "key_id": key_id}


# =============================================================================
# Usage
# =============================================================================

@router.get("/usage", response_model=UsageResponse)
async def get_reseller_usage(
    period: str = Query(default="current_month", pattern="^(current_month|last_month)$"),
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    """Get aggregate usage for this reseller across all managed users."""
    if not auth.has_scope("usage:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: usage:read",
        )

    start, end = _period_bounds(period)
    data = await usage_service.get_reseller_usage(
        db, auth.reseller_id, start, end, group_by="user"
    )

    totals = data.get("totals", {})
    by_user_raw = data.get("by_user", [])

    by_user = [
        UserUsageBreakdown(
            user_id=u["user_id"],
            username=u["username"],
            sessions=u["sessions"],
            input_tokens=u["input_tokens"],
            output_tokens=u["output_tokens"],
            cost_usd=u["cost_usd"],
            ssh_commands=u["ssh_commands"],
        )
        for u in by_user_raw
    ]

    return UsageResponse(
        period=UsagePeriod(start=start, end=end),
        totals=UsageTotals(
            sessions=totals.get("sessions", 0),
            input_tokens=totals.get("input_tokens", 0),
            output_tokens=totals.get("output_tokens", 0),
            cost_usd=totals.get("cost_usd", 0.0),
            active_users=totals.get("active_users", 0),
            ssh_commands=totals.get("ssh_commands", 0),
        ),
        by_user=by_user,
    )


@router.get("/users/{user_id}/usage", response_model=UserUsageResponse)
async def get_user_usage(
    user_id: str,
    period: str = Query(default="current_month", pattern="^(current_month|last_month)$"),
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> UserUsageResponse:
    """Get usage detail for a specific managed user."""
    if not auth.has_scope("usage:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: usage:read",
        )

    user = await _get_owned_user(db, auth, user_id)
    start, end = _period_bounds(period)

    data = await usage_service.get_user_usage(db, user.id, start, end)

    # Fetch recent sessions for this user in the period
    sessions_result = await db.execute(
        select(Session)
        .where(
            Session.user_id == user.id,
            Session.created_at >= start,
            Session.created_at <= end,
        )
        .order_by(Session.created_at.desc())
        .limit(50)
    )
    sessions_list = [
        {
            "id": s.id,
            "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "cost_usd": s.cumulative_cost_usd,
            "num_turns": s.num_turns,
        }
        for s in sessions_result.scalars().all()
    ]

    return UserUsageResponse(
        user_id=user.id,
        username=user.username,
        period=UsagePeriod(start=start, end=end),
        totals=UsageTotals(
            sessions=data.get("sessions", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cost_usd=data.get("cost_usd", 0.0),
            ssh_commands=data.get("ssh_commands", 0),
        ),
        sessions=sessions_list,
    )


@router.get("/usage/metrics", response_model=WhmcsMetricsResponse)
async def get_usage_metrics(
    period: str = Query(
        default="current_month", pattern="^(current_month|last_month)$"
    ),
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> WhmcsMetricsResponse:
    """Get usage in WHMCS MetricProvider format for billing integration.

    Returns per-user session count, token count, and cost in a format
    compatible with WHMCS MetricProvider modules.
    """
    if not auth.has_scope("usage:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: usage:read",
        )

    start, end = _period_bounds(period)
    data = await usage_service.get_reseller_metrics(
        db, auth.reseller_id, start, end
    )
    return WhmcsMetricsResponse(**data)


@router.get("/usage/export")
async def export_usage(
    period: str = Query(
        default="current_month", pattern="^(current_month|last_month)$"
    ),
    fmt: str = Query(default="json", alias="format", pattern="^(json|csv)$"),
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
):
    """Export usage data as JSON or CSV download.

    Returns a file download with Content-Disposition header.
    """
    if not auth.has_scope("usage:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: usage:read",
        )

    start, end = _period_bounds(period)
    records = await usage_service.export_usage_data(
        db, auth.reseller_id, start, end
    )

    if fmt == "csv":
        output = io.StringIO()
        if records:
            writer = csv.DictWriter(output, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)
        csv_content = output.getvalue()
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=usage_{period}.csv"
                ),
            },
        )

    # Default: JSON
    return JSONResponse(
        content={"period": period, "records": records, "count": len(records)},
        headers={
            "Content-Disposition": (
                f"attachment; filename=usage_{period}.json"
            ),
        },
    )


# =============================================================================
# Configuration
# =============================================================================

@router.get("/users/{user_id}/config", response_model=UserConfigResponse)
async def get_user_config(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> UserConfigResponse:
    """Get the full configuration for a managed user."""
    if not auth.has_scope("config:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:read",
        )

    user = await _get_owned_user(db, auth, user_id)
    reseller = await _get_reseller(db, auth)

    effective_features = feature_flag_service.resolve_features(
        reseller.features_json, user.features_json
    )

    security = _parse_json_field(user.security_overrides_json)

    try:
        allowed_overrides = json.loads(user.allowed_overrides) if user.allowed_overrides else []
    except (json.JSONDecodeError, TypeError):
        allowed_overrides = []

    spending_data = await spending_guard.get_spending_status(db, user.id)
    spending_resp = _spending_response_from_guard(spending_data)

    # Skills summary
    skills_result = await db.execute(
        select(func.count(UserSkill.id)).where(UserSkill.user_id == user.id)
    )
    skill_count = skills_result.scalar() or 0

    return UserConfigResponse(
        user_id=user.id,
        settings_mode=user.settings_mode or "readonly",
        allowed_overrides=allowed_overrides,
        features=effective_features,
        security=security,
        spending=spending_resp,
        skills={"count": skill_count},
        ssh_filters={},
    )


@router.put("/users/{user_id}/config", response_model=UserConfigResponse)
async def update_user_config(
    user_id: str,
    body: UpdateUserConfigRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> UserConfigResponse:
    """Update configuration for a managed user."""
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    user = await _get_owned_user(db, auth, user_id)

    if body.settings_mode is not None:
        if body.settings_mode not in ("readonly", "configurable"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="settings_mode must be 'readonly' or 'configurable'",
            )
        user.settings_mode = body.settings_mode

    if body.allowed_overrides is not None:
        user.allowed_overrides = json.dumps(body.allowed_overrides)

    if body.feature_overrides is not None:
        user.features_json = json.dumps(body.feature_overrides)

    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    # Return updated config via the get endpoint logic
    return await get_user_config(user_id, auth, db)


@router.get("/users/{user_id}/security")
async def get_user_security(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get security configuration for a managed user."""
    if not auth.has_scope("security:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: security:manage",
        )

    user = await _get_owned_user(db, auth, user_id)

    security = _parse_json_field(user.security_overrides_json)

    return {"user_id": user.id, "security": security}


@router.put("/users/{user_id}/security")
async def update_user_security(
    user_id: str,
    body: UpdateSecurityConfigRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update security configuration for a managed user."""
    if not auth.has_scope("security:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: security:manage",
        )

    user = await _get_owned_user(db, auth, user_id)

    security = _parse_json_field(user.security_overrides_json)

    patch = body.model_dump(exclude_none=True)
    security.update(patch)
    user.security_overrides_json = json.dumps(security)
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {"user_id": user.id, "security": security}


@router.get("/users/{user_id}/ssh-filters")
async def get_user_ssh_filters(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get SSH filter configuration for a managed user."""
    if not auth.has_scope("security:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: security:manage",
        )

    user = await _get_owned_user(db, auth, user_id)

    security = _parse_json_field(user.security_overrides_json)

    ssh_filters = security.get("ssh_filters", {})
    return {"user_id": user.id, "ssh_filters": ssh_filters}


@router.put("/users/{user_id}/ssh-filters")
async def update_user_ssh_filters(
    user_id: str,
    body: UpdateSSHFiltersRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update SSH filter configuration for a managed user."""
    if not auth.has_scope("security:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: security:manage",
        )

    user = await _get_owned_user(db, auth, user_id)

    security = _parse_json_field(user.security_overrides_json)

    patch = body.model_dump(exclude_none=True)
    ssh_filters = security.get("ssh_filters", {})
    ssh_filters.update(patch)
    security["ssh_filters"] = ssh_filters
    user.security_overrides_json = json.dumps(security)
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {"user_id": user.id, "ssh_filters": ssh_filters}


@router.get("/users/{user_id}/env-vars")
async def get_user_env_vars(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List environment variable names (not values) for a managed user."""
    if not auth.has_scope("config:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:read",
        )

    user = await _get_owned_user(db, auth, user_id)

    try:
        meta = json.loads(user.metadata_json) if user.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}

    env_vars = meta.get("env_vars", {})
    return {"user_id": user.id, "env_var_names": list(env_vars.keys())}


@router.put("/users/{user_id}/env-vars")
async def set_user_env_vars(
    user_id: str,
    body: SetEnvVarsRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Set environment variables for a managed user.

    Merges the provided variables into the existing set.
    Existing variables not mentioned in the request are preserved.
    """
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    user = await _get_owned_user(db, auth, user_id)

    try:
        meta = json.loads(user.metadata_json) if user.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}

    env_vars = meta.get("env_vars", {})
    env_vars.update(body.env_vars)
    meta["env_vars"] = env_vars
    user.metadata_json = json.dumps(meta)
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(
        "Reseller %s set %d env vars for user %s",
        auth.reseller_id, len(body.env_vars), user_id,
    )

    return {"user_id": user.id, "env_var_names": list(env_vars.keys())}


@router.delete("/users/{user_id}/env-vars/{name}")
async def delete_user_env_var(
    user_id: str,
    name: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove a specific environment variable from a managed user."""
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    user = await _get_owned_user(db, auth, user_id)

    try:
        meta = json.loads(user.metadata_json) if user.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}

    env_vars = meta.get("env_vars", {})
    if name not in env_vars:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Environment variable '{name}' not found",
        )

    del env_vars[name]
    meta["env_vars"] = env_vars
    user.metadata_json = json.dumps(meta)
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(
        "Reseller %s deleted env var '%s' for user %s",
        auth.reseller_id, name, user_id,
    )

    return {"user_id": user.id, "deleted": name, "env_var_names": list(env_vars.keys())}


@router.get("/users/{user_id}/spending", response_model=SpendingStatusResponse)
async def get_user_spending(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> SpendingStatusResponse:
    """Get current spending status for a managed user."""
    if not auth.has_scope("usage:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: usage:read",
        )

    user = await _get_owned_user(db, auth, user_id)

    spending_data = await spending_guard.get_spending_status(db, user.id)
    return _spending_response_from_guard(spending_data)


@router.put("/users/{user_id}/spending-limits", response_model=SpendingStatusResponse)
async def set_user_spending_limits(
    user_id: str,
    body: SetSpendingLimitsRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> SpendingStatusResponse:
    """Set spending limits for a managed user.

    User limits cannot exceed the reseller's own limits.
    """
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    user = await _get_owned_user(db, auth, user_id)
    reseller = await _get_reseller(db, auth)

    # Validate user limits don't exceed reseller limits
    if (
        body.max_monthly_usd is not None
        and reseller.max_monthly_spending_usd is not None
        and body.max_monthly_usd > reseller.max_monthly_spending_usd
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"User monthly limit ({body.max_monthly_usd}) cannot exceed "
                f"reseller limit ({reseller.max_monthly_spending_usd})"
            ),
        )

    if (
        body.max_daily_usd is not None
        and reseller.max_daily_spending_usd is not None
        and body.max_daily_usd > reseller.max_daily_spending_usd
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"User daily limit ({body.max_daily_usd}) cannot exceed "
                f"reseller limit ({reseller.max_daily_spending_usd})"
            ),
        )

    if body.max_monthly_usd is not None:
        user.spending_limit_monthly_usd = body.max_monthly_usd
    if body.max_daily_usd is not None:
        user.spending_limit_daily_usd = body.max_daily_usd
    if body.max_per_session_usd is not None:
        user.spending_limit_per_session_usd = body.max_per_session_usd

    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    spending_data = await spending_guard.get_spending_status(db, user.id)
    return _spending_response_from_guard(spending_data)


@router.put("/users/{user_id}/settings-mode")
async def set_user_settings_mode(
    user_id: str,
    body: SetSettingsModeRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Set whether a user can override their own settings and which overrides are allowed."""
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    user = await _get_owned_user(db, auth, user_id)

    user.settings_mode = body.mode
    user.allowed_overrides = json.dumps(body.allowed_overrides)
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "user_id": user.id,
        "settings_mode": user.settings_mode,
        "allowed_overrides": body.allowed_overrides,
    }


# =============================================================================
# Skills — User
# =============================================================================

@router.get("/users/{user_id}/skills", response_model=UserSkillsResponse)
async def list_user_skills(
    user_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> UserSkillsResponse:
    """List skills assigned to a managed user."""
    if not auth.has_scope("skills:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: skills:manage",
        )

    user = await _get_owned_user(db, auth, user_id)

    result = await db.execute(
        select(UserSkill).where(UserSkill.user_id == user.id)
        .order_by(UserSkill.created_at.desc())
    )
    skills = list(result.scalars().all())

    return UserSkillsResponse(
        skills=[
            SkillResponse(
                name=s.name,
                source=s.source,
                is_enabled=s.is_enabled,
                content_hash=s.content_hash,
                created_at=s.created_at,
            )
            for s in skills
        ],
        limits={"max_custom_skills": 10},
    )


@router.post("/users/{user_id}/skills", status_code=status.HTTP_201_CREATED)
async def assign_skill_to_user(
    user_id: str,
    body: AssignSkillRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Assign a skill from the reseller library to a managed user."""
    if not auth.has_scope("skills:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: skills:manage",
        )

    user = await _get_owned_user(db, auth, user_id)

    # Verify the skill exists in the reseller library
    lib_result = await db.execute(
        select(ResellerSkillLibrary).where(
            ResellerSkillLibrary.reseller_id == auth.reseller_id,
            ResellerSkillLibrary.name == body.name,
            ResellerSkillLibrary.is_active.is_(True),
        )
    )
    library_skill = lib_result.scalar_one_or_none()
    if not library_skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{body.name}' not found in reseller library",
        )

    # Check if already assigned
    existing_result = await db.execute(
        select(UserSkill).where(
            UserSkill.user_id == user.id,
            UserSkill.name == body.name,
        )
    )
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill '{body.name}' is already assigned to this user",
        )

    skill = UserSkill(
        user_id=user.id,
        reseller_id=auth.reseller_id,
        name=body.name,
        source="library",
        is_enabled=True,
        content_hash=library_skill.content_hash,
        created_at=datetime.now(timezone.utc),
    )
    db.add(skill)
    await db.commit()

    return {
        "status": "assigned",
        "user_id": user.id,
        "skill_name": body.name,
    }


@router.delete("/users/{user_id}/skills/{skill_name}")
async def remove_user_skill(
    user_id: str,
    skill_name: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove a skill assignment from a managed user."""
    if not auth.has_scope("skills:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: skills:manage",
        )

    user = await _get_owned_user(db, auth, user_id)

    result = await db.execute(
        select(UserSkill).where(
            UserSkill.user_id == user.id,
            UserSkill.name == skill_name,
        )
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_name}' not assigned to this user",
        )

    await db.delete(skill)
    await db.commit()

    return {"status": "removed", "user_id": user.id, "skill_name": skill_name}


async def _set_skill_enabled(
    db: AsyncSession, auth: AuthContext, user_id: str, skill_name: str, enabled: bool
) -> dict:
    """Set the enabled state of a skill for a managed user.

    Raises 403 if the caller lacks the skills:manage scope,
    404 if the user or skill is not found.
    """
    if not auth.has_scope("skills:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: skills:manage",
        )

    user = await _get_owned_user(db, auth, user_id)

    result = await db.execute(
        select(UserSkill).where(
            UserSkill.user_id == user.id,
            UserSkill.name == skill_name,
        )
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_name}' not assigned to this user",
        )

    skill.is_enabled = enabled
    await db.commit()

    action = "enabled" if enabled else "disabled"
    return {"status": action, "user_id": user.id, "skill_name": skill_name}


@router.post("/users/{user_id}/skills/{skill_name}/enable")
async def enable_user_skill(
    user_id: str,
    skill_name: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Enable a skill for a managed user."""
    return await _set_skill_enabled(db, auth, user_id, skill_name, enabled=True)


@router.post("/users/{user_id}/skills/{skill_name}/disable")
async def disable_user_skill(
    user_id: str,
    skill_name: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disable a skill for a managed user without removing the assignment."""
    return await _set_skill_enabled(db, auth, user_id, skill_name, enabled=False)


# =============================================================================
# Skills — Library
# =============================================================================

@router.get("/skill-library", response_model=UserSkillsResponse)
async def list_skill_library(
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> UserSkillsResponse:
    """List all skills in this reseller's library."""
    if not auth.has_scope("skills:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: skills:manage",
        )

    result = await db.execute(
        select(ResellerSkillLibrary)
        .where(
            ResellerSkillLibrary.reseller_id == auth.reseller_id,
            ResellerSkillLibrary.is_active.is_(True),
        )
        .order_by(ResellerSkillLibrary.created_at.desc())
    )
    skills = list(result.scalars().all())

    return UserSkillsResponse(
        skills=[
            SkillResponse(
                name=s.name,
                source="library",
                is_enabled=s.is_active,
                content_hash=s.content_hash,
                created_at=s.created_at,
            )
            for s in skills
        ],
        limits={},
    )


@router.post("/skill-library", status_code=status.HTTP_201_CREATED)
async def upload_skill_to_library(
    body: UploadSkillRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Upload a skill to this reseller's library.

    If a skill with the same name already exists, updates its content.
    """
    if not auth.has_scope("skills:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: skills:manage",
        )

    content_hash = hashlib.sha256(body.content.encode()).hexdigest()

    existing_result = await db.execute(
        select(ResellerSkillLibrary).where(
            ResellerSkillLibrary.reseller_id == auth.reseller_id,
            ResellerSkillLibrary.name == body.name,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        existing.description = body.description
        existing.content_hash = content_hash
        existing.is_active = True
        await db.commit()
        return {
            "status": "updated",
            "name": body.name,
            "content_hash": content_hash,
        }

    skill = ResellerSkillLibrary(
        reseller_id=auth.reseller_id,
        name=body.name,
        description=body.description,
        content_hash=content_hash,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(skill)
    await db.commit()

    return {
        "status": "created",
        "name": body.name,
        "content_hash": content_hash,
    }


@router.delete("/skill-library/{skill_name}")
async def remove_skill_from_library(
    skill_name: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove a skill from this reseller's library.

    Marks the library entry as inactive. Does not remove existing user assignments.
    """
    if not auth.has_scope("skills:manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: skills:manage",
        )

    result = await db.execute(
        select(ResellerSkillLibrary).where(
            ResellerSkillLibrary.reseller_id == auth.reseller_id,
            ResellerSkillLibrary.name == skill_name,
            ResellerSkillLibrary.is_active.is_(True),
        )
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_name}' not found in reseller library",
        )

    skill.is_active = False
    await db.commit()

    return {"status": "removed", "skill_name": skill_name}


# =============================================================================
# Webhooks
# =============================================================================

@router.post("/webhooks", status_code=201, response_model=WebhookCreatedResponse)
async def create_webhook(
    body: CreateWebhookRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> WebhookCreatedResponse:
    """Register a new webhook endpoint."""
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    endpoint, secret = await webhook_service.create_endpoint(
        db, auth.reseller_id, body.url,
        body.events, body.description,
    )
    events = json.loads(endpoint.events)
    return WebhookCreatedResponse(
        id=endpoint.id, url=endpoint.url, events=events,
        is_active=endpoint.is_active, description=endpoint.description,
        created_at=endpoint.created_at, updated_at=endpoint.updated_at,
        secret=secret,
    )


@router.get("/webhooks", response_model=WebhookListResponse)
async def list_webhooks(
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> WebhookListResponse:
    """List all webhook endpoints."""
    if not auth.has_scope("config:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:read",
        )

    endpoints = await webhook_service.list_endpoints(db, auth.reseller_id)
    items = []
    for ep in endpoints:
        try:
            events = json.loads(ep.events)
        except (json.JSONDecodeError, TypeError):
            events = []
        items.append(WebhookEndpointResponse(
            id=ep.id, url=ep.url, events=events,
            is_active=ep.is_active, description=ep.description,
            created_at=ep.created_at, updated_at=ep.updated_at,
        ))
    return WebhookListResponse(webhooks=items)


@router.put("/webhooks/{webhook_id}", response_model=WebhookEndpointResponse)
async def update_webhook(
    webhook_id: str,
    body: UpdateWebhookRequest,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> WebhookEndpointResponse:
    """Update a webhook endpoint."""
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    update_data = body.model_dump(exclude_none=True)
    endpoint = await webhook_service.update_endpoint(
        db, webhook_id, auth.reseller_id, **update_data,
    )
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    events = json.loads(endpoint.events)
    return WebhookEndpointResponse(
        id=endpoint.id, url=endpoint.url, events=events,
        is_active=endpoint.is_active, description=endpoint.description,
        created_at=endpoint.created_at, updated_at=endpoint.updated_at,
    )


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a webhook endpoint and all its delivery logs."""
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    success = await webhook_service.delete_endpoint(
        db, webhook_id, auth.reseller_id,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted", "webhook_id": webhook_id}


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(
    webhook_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Send a test event to a webhook endpoint."""
    if not auth.has_scope("config:update"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:update",
        )

    endpoint = await webhook_service.get_endpoint(
        db, webhook_id, auth.reseller_id,
    )
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    delivery = await webhook_service.deliver(
        db, endpoint, "test.ping", {"message": "Test webhook delivery"},
    )
    return {
        "status": delivery.status,
        "delivery_id": delivery.id,
        "response_status": delivery.response_status,
    }


@router.get(
    "/webhooks/{webhook_id}/deliveries",
    response_model=WebhookDeliveryListResponse,
)
async def get_webhook_deliveries(
    webhook_id: str,
    auth: AuthContext = Depends(require_reseller),
    db: AsyncSession = Depends(get_db),
) -> WebhookDeliveryListResponse:
    """Get recent delivery log for a webhook endpoint."""
    if not auth.has_scope("config:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required scope: config:read",
        )

    deliveries = await webhook_service.get_deliveries(
        db, webhook_id, auth.reseller_id,
    )
    items = [
        WebhookDeliveryResponse(
            id=d.id, event_type=d.event_type, status=d.status,
            attempts=d.attempts, max_attempts=d.max_attempts,
            response_status=d.response_status, error=d.error,
            last_attempt_at=d.last_attempt_at,
            next_retry_at=d.next_retry_at, created_at=d.created_at,
        )
        for d in deliveries
    ]
    return WebhookDeliveryListResponse(deliveries=items)
