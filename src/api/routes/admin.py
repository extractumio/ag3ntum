"""Admin API endpoints for platform management."""
import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import (
    APIKey, APIKeyAuditLog, Reseller,
    Session, User,
)
from ...services.reseller_service import ResellerNotFoundError, reseller_service
from ..deps import AuthContext, get_auth_context
from ..reseller_models import (
    AuditLogEntry, AuditLogResponse, ChangePasswordRequest,
    CreateResellerRequest, CreateResellerUserRequest,
    DeleteResellerResponse, DeleteUserResponse, PaginationInfo,
    PasswordChangedResponse, PlatformConfigResponse, PlatformStats,
    ResellerLimits, ResellerListResponse, ResellerResponse,
    ResellerSpending, ResellerStats, RetentionConfigResponse,
    RetentionRunResponse, SpendingCurrent, SpendingLimits,
    SuspendResellerResponse, SuspendRequest, SuspendUserResponse,
    UnsuspendResellerResponse, UpdatePlatformConfigRequest,
    UpdateResellerRequest, UpdateRetentionRequest,
    UsagePeriod, UsageResponse, UsageTotals,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

from src.api.routes._helpers import _read_version


# =============================================================================
# Admin auth dependency
# =============================================================================

async def _require_admin(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Reject non-admin callers with 403."""
    if not auth.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return auth


# =============================================================================
# Helpers
# =============================================================================


def _build_reseller_response(reseller: Reseller) -> ResellerResponse:
    """Convert a Reseller ORM object to a ResellerResponse."""
    quota = reseller.quota
    current_users = quota.current_user_count if quota else 0
    features = {}
    if reseller.features_json:
        try:
            features = json.loads(reseller.features_json)
        except (json.JSONDecodeError, TypeError):
            pass

    limits = ResellerLimits(
        max_users=reseller.max_users,
        current_users=current_users,
        max_concurrent_tasks=reseller.max_concurrent_tasks,
        max_daily_tasks=reseller.max_daily_tasks,
    )

    spending = ResellerSpending(
        limits=SpendingLimits(
            monthly_usd=reseller.max_monthly_spending_usd,
            daily_usd=reseller.max_daily_spending_usd,
        ),
        current=SpendingCurrent(
            monthly_usd=quota.monthly_cost_usd if quota else 0.0,
            daily_usd=quota.daily_cost_usd if quota else 0.0,
        ),
        alert_threshold_pct=reseller.spending_alert_threshold_pct,
    )

    owner_username = reseller.owner.username if reseller.owner else None

    return ResellerResponse(
        id=reseller.id,
        name=reseller.name,
        company=reseller.company,
        contact_email=reseller.contact_email,
        owner_user_id=reseller.owner_user_id,
        owner_username=owner_username,
        is_active=reseller.is_active,
        suspended_at=reseller.suspended_at,
        limits=limits,
        llm_provider=reseller.llm_provider,
        features=features,
        spending=spending,
        notes=reseller.notes,
        created_at=reseller.created_at,
        updated_at=reseller.updated_at,
    )


# =============================================================================
# Reseller management
# =============================================================================

@router.post("/resellers", status_code=201, response_model=ResellerResponse)
async def create_reseller(
    body: CreateResellerRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> ResellerResponse:
    """Create a new reseller with an owner user account and quota record."""
    try:
        reseller = await reseller_service.create_reseller(
            db=db,
            name=body.name,
            company=body.company,
            contact_email=body.contact_email,
            password=body.password,
            max_users=body.max_users,
            max_concurrent_tasks=body.max_concurrent_tasks,
            max_daily_tasks=body.max_daily_tasks,
            llm_provider=body.llm_provider,
            features=body.features,
            notes=body.notes,
            max_monthly_spending_usd=body.max_monthly_spending_usd,
            max_daily_spending_usd=body.max_daily_spending_usd,
            spending_alert_threshold_pct=body.spending_alert_threshold_pct,
        )
    except Exception as exc:
        logger.error("Failed to create reseller: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # Reload with relationships for response building
    reseller = await reseller_service.get_reseller(db, reseller.id)
    return _build_reseller_response(reseller)


@router.get("/resellers", response_model=ResellerListResponse)
async def list_resellers(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    status_filter: str = Query(default="all", alias="status"),
    search: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> ResellerListResponse:
    """List resellers with pagination and optional filtering."""
    resellers, total = await reseller_service.list_resellers(
        db=db,
        page=page,
        per_page=per_page,
        status_filter=status_filter,
        search=search,
    )

    items = [_build_reseller_response(r) for r in resellers]
    total_pages = math.ceil(total / per_page) if total > 0 else 1

    return ResellerListResponse(
        resellers=items,
        pagination=PaginationInfo(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
        ),
    )


@router.get("/resellers/{reseller_id}", response_model=ResellerResponse)
async def get_reseller(
    reseller_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> ResellerResponse:
    """Get full reseller details including stats and API keys."""
    reseller = await reseller_service.get_reseller(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="Reseller not found")

    stats_data = await reseller_service.get_reseller_stats(db, reseller_id)
    response = _build_reseller_response(reseller)

    response.stats = ResellerStats(
        user_count=stats_data.get("user_count", 0),
        active_users_30d=0,  # Not tracked separately
        total_sessions=stats_data.get("total_sessions", 0),
        total_cost_usd=stats_data.get("total_cost_usd", 0.0),
        api_keys_active=sum(1 for k in reseller.api_keys if k.is_active),
        sessions_this_month=0,
        cost_this_month_usd=stats_data.get("quota", {}).get("monthly_cost_usd", 0.0)
        if stats_data.get("quota") else 0.0,
    )

    return response


@router.get("/resellers/{reseller_id}/config")
async def get_reseller_config(
    reseller_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> dict:
    """Return full config tree: reseller defaults plus all per-user overrides."""
    reseller = await reseller_service.get_reseller(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="Reseller not found")

    reseller_features = {}
    if reseller.features_json:
        try:
            reseller_features = json.loads(reseller.features_json)
        except (json.JSONDecodeError, TypeError):
            pass

    user_configs = []
    for user in reseller.users:
        feature_overrides = {}
        security_overrides = {}
        if user.features_json:
            try:
                feature_overrides = json.loads(user.features_json)
            except (json.JSONDecodeError, TypeError):
                pass
        if user.security_overrides_json:
            try:
                security_overrides = json.loads(user.security_overrides_json)
            except (json.JSONDecodeError, TypeError):
                pass

        user_configs.append({
            "user_id": user.id,
            "username": user.username,
            "settings_mode": user.settings_mode,
            "feature_overrides": feature_overrides,
            "security_overrides": security_overrides,
            "spending_limits": {
                "monthly_usd": user.spending_limit_monthly_usd,
                "daily_usd": user.spending_limit_daily_usd,
                "per_session_usd": user.spending_limit_per_session_usd,
            },
        })

    return {
        "reseller_id": reseller_id,
        "reseller_name": reseller.name,
        "defaults": {
            "max_users": reseller.max_users,
            "max_concurrent_tasks": reseller.max_concurrent_tasks,
            "max_daily_tasks": reseller.max_daily_tasks,
            "llm_provider": reseller.llm_provider,
            "features": reseller_features,
            "spending_caps": {
                "monthly_usd": reseller.max_monthly_spending_usd,
                "daily_usd": reseller.max_daily_spending_usd,
                "alert_threshold_pct": reseller.spending_alert_threshold_pct,
            },
        },
        "users": user_configs,
    }


@router.put("/resellers/{reseller_id}", response_model=ResellerResponse)
async def update_reseller(
    reseller_id: str,
    body: UpdateResellerRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> ResellerResponse:
    """Update reseller fields."""
    update_kwargs = body.model_dump(exclude_none=True)
    if "features" in update_kwargs:
        update_kwargs["features_json"] = json.dumps(update_kwargs.pop("features"))

    try:
        reseller = await reseller_service.update_reseller(db, reseller_id, **update_kwargs)
    except ResellerNotFoundError:
        raise HTTPException(status_code=404, detail="Reseller not found")

    return _build_reseller_response(reseller)


@router.post("/resellers/{reseller_id}/suspend", response_model=SuspendResellerResponse)
async def suspend_reseller(
    reseller_id: str,
    body: Optional[SuspendRequest] = None,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> SuspendResellerResponse:
    """Suspend a reseller and all their users, API keys, and active sessions."""
    try:
        result = await reseller_service.suspend_reseller(
            db, reseller_id, reason=body.reason if body else None
        )
    except ResellerNotFoundError:
        raise HTTPException(status_code=404, detail="Reseller not found")

    reseller = await reseller_service.get_reseller(db, reseller_id)
    return SuspendResellerResponse(
        id=reseller_id,
        name=reseller.name if reseller else "",
        is_active=False,
        suspended_at=reseller.suspended_at if reseller else datetime.now(timezone.utc),
        users_suspended=result.get("users_suspended", 0),
        sessions_cancelled=result.get("sessions_cancelled", 0),
        api_keys_deactivated=result.get("keys_deactivated", 0),
    )


@router.post("/resellers/{reseller_id}/unsuspend", response_model=UnsuspendResellerResponse)
async def unsuspend_reseller(
    reseller_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> UnsuspendResellerResponse:
    """Restore a suspended reseller."""
    try:
        result = await reseller_service.unsuspend_reseller(db, reseller_id)
    except ResellerNotFoundError:
        raise HTTPException(status_code=404, detail="Reseller not found")

    reseller = await reseller_service.get_reseller(db, reseller_id)
    return UnsuspendResellerResponse(
        id=reseller_id,
        name=reseller.name if reseller else "",
        is_active=True,
        users_restored=result.get("users_restored", 0),
        api_keys_reactivated=result.get("keys_reactivated", 0),
    )


@router.delete("/resellers/{reseller_id}", response_model=DeleteResellerResponse)
async def delete_reseller(
    reseller_id: str,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> DeleteResellerResponse:
    """Delete a reseller and all associated data. Requires ?confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deletion requires ?confirm=true query parameter.",
        )

    reseller = await reseller_service.get_reseller(db, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="Reseller not found")
    reseller_name = reseller.name

    try:
        result = await reseller_service.delete_reseller(db, reseller_id)
    except ResellerNotFoundError:
        raise HTTPException(status_code=404, detail="Reseller not found")

    return DeleteResellerResponse(
        status="deleted",
        name=reseller_name,
        users_deleted=result.get("users_deleted", 0),
        sessions_deleted=result.get("sessions_deleted", 0),
    )


# =============================================================================
# User management (admin override — cross-reseller)
# =============================================================================

@router.get("/users")
async def list_all_users(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    reseller_id: Optional[str] = Query(default=None),
    status_filter: str = Query(default="all", alias="status"),
    role: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> dict:
    """List all users across all resellers."""
    query = select(User)

    if reseller_id is not None:
        query = query.where(User.reseller_id == reseller_id)
    if status_filter == "active":
        query = query.where(User.is_active == True)  # noqa: E712
    elif status_filter == "suspended":
        query = query.where(User.is_active == False)  # noqa: E712
    if role:
        query = query.where(User.role == role)
    if search:
        query = query.where(
            User.username.ilike(f"%{search}%") | User.email.ilike(f"%{search}%")
        )

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    offset = (page - 1) * per_page
    paged_result = await db.execute(
        query.order_by(User.created_at.desc()).offset(offset).limit(per_page)
    )
    users = list(paged_result.scalars().all())

    # Batch-load reseller names
    reseller_ids = {u.reseller_id for u in users if u.reseller_id}
    reseller_names: dict[str, str] = {}
    if reseller_ids:
        res_result = await db.execute(
            select(Reseller.id, Reseller.name).where(Reseller.id.in_(reseller_ids))
        )
        for row in res_result:
            reseller_names[row[0]] = row[1]

    items = [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "role": u.role,
            "is_active": u.is_active,
            "reseller_id": u.reseller_id,
            "reseller_name": reseller_names.get(u.reseller_id) if u.reseller_id else None,
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]

    total_pages = math.ceil(total / per_page) if total > 0 else 1
    return {
        "users": items,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        },
    }


@router.post("/users", status_code=201)
async def create_direct_user(
    body: CreateResellerUserRequest,
    role: str = Query(default="user"),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> dict:
    """Create a user without reseller association (admin-managed direct user)."""
    if role not in ("user", "admin"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role must be 'user' or 'admin'",
        )

    # Check username/email uniqueness
    existing = await db.execute(
        select(User).where(
            (User.username == body.username) | (User.email == body.email)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already in use",
        )

    password_hash = bcrypt.hashpw(
        body.password.encode(), bcrypt.gensalt()
    ).decode()

    user = User(
        id=str(uuid.uuid4()),
        username=body.username,
        email=body.email,
        password_hash=password_hash,
        role=role,
        jwt_secret=uuid.uuid4().hex,
        linux_uid=None,
        is_active=True,
        reseller_id=None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("Admin created direct user '%s' (role=%s)", body.username, role)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
    }


@router.post("/users/{user_id}/suspend", response_model=SuspendUserResponse)
async def admin_suspend_user(
    user_id: str,
    body: Optional[SuspendRequest] = None,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> SuspendUserResponse:
    """Suspend any user regardless of reseller."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)

    # Cancel active sessions
    sessions_result = await db.execute(
        select(Session).where(
            Session.user_id == user_id,
            Session.status.in_(("pending", "running", "queued")),
        )
    )
    cancelled = 0
    for session in sessions_result.scalars().all():
        session.status = "cancelled"
        cancelled += 1

    await db.commit()
    logger.info("Admin suspended user %s (%s sessions cancelled)", user_id, cancelled)

    return SuspendUserResponse(
        id=user_id,
        username=user.username,
        is_active=False,
        suspended_at=datetime.now(timezone.utc),
        active_sessions_cancelled=cancelled,
    )


@router.post("/users/{user_id}/unsuspend", response_model=SuspendUserResponse)
async def admin_unsuspend_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> SuspendUserResponse:
    """Restore any suspended user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("Admin unsuspended user %s", user_id)
    return SuspendUserResponse(
        id=user_id,
        username=user.username,
        is_active=True,
        suspended_at=None,
        active_sessions_cancelled=0,
    )


@router.post("/users/{user_id}/change-password", response_model=PasswordChangedResponse)
async def admin_change_password(
    user_id: str,
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> PasswordChangedResponse:
    """Change any user's password and revoke all existing tokens."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = bcrypt.hashpw(
        body.new_password.encode(), bcrypt.gensalt()
    ).decode()
    # Revoke all tokens by incrementing token_version
    user.token_version = (user.token_version or 0) + 1
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("Admin changed password for user %s", user_id)
    return PasswordChangedResponse(status="password_changed", tokens_revoked=True)


@router.delete("/users/{user_id}", response_model=DeleteUserResponse)
async def admin_delete_user(
    user_id: str,
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> DeleteUserResponse:
    """Delete any user. Requires ?confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deletion requires ?confirm=true query parameter.",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    username = user.username

    # Delete sessions first (bulk)
    del_result = await db.execute(
        delete(Session).where(Session.user_id == user_id)
    )
    sessions_deleted = del_result.rowcount

    await db.delete(user)
    await db.commit()

    logger.info("Admin deleted user '%s' (%s sessions removed)", username, sessions_deleted)
    return DeleteUserResponse(
        status="deleted",
        id=user_id,
        username=username,
        sessions_deleted=sessions_deleted,
        files_cleaned=False,  # Filesystem cleanup is out-of-band
    )


# =============================================================================
# Platform configuration
# =============================================================================

@router.get("/config", response_model=PlatformConfigResponse)
async def get_platform_config(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> PlatformConfigResponse:
    """Return platform-level default configuration.

    Exposes the effective settings (hardcoded + DB overrides) that
    resellers and users inherit from.
    """
    from ...services.feature_flag_service import feature_flag_service

    await feature_flag_service.ensure_loaded(db)

    return PlatformConfigResponse(
        default_features=feature_flag_service.get_platform_features(),
        default_quotas=feature_flag_service.get_platform_quotas(),
        default_spending_limits=feature_flag_service.get_platform_spending(),
    )


@router.put("/config", response_model=PlatformConfigResponse)
async def update_platform_config(
    body: UpdatePlatformConfigRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> PlatformConfigResponse:
    """Update platform-level default configuration.

    Updates the DB-stored overrides. Null values in the body reset
    individual keys back to hardcoded defaults.
    """
    from ...services.feature_flag_service import feature_flag_service

    await feature_flag_service.ensure_loaded(db)

    if body.features is not None:
        await feature_flag_service.update_platform_defaults(
            db, "features", body.features, updated_by=auth.user_id,
        )
    if body.quotas is not None:
        await feature_flag_service.update_platform_defaults(
            db, "quotas", body.quotas, updated_by=auth.user_id,
        )
    if body.spending is not None:
        await feature_flag_service.update_platform_defaults(
            db, "spending", body.spending, updated_by=auth.user_id,
        )

    return PlatformConfigResponse(
        default_features=feature_flag_service.get_platform_features(),
        default_quotas=feature_flag_service.get_platform_quotas(),
        default_spending_limits=feature_flag_service.get_platform_spending(),
    )


# =============================================================================
# Platform stats & reporting
# =============================================================================

@router.get("/stats", response_model=PlatformStats)
async def platform_stats(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> PlatformStats:
    """Return platform-wide dashboard statistics."""
    version = _read_version()

    # Reseller counts
    total_resellers_r = await db.execute(select(func.count(Reseller.id)))
    total_resellers = total_resellers_r.scalar_one() or 0

    active_resellers_r = await db.execute(
        select(func.count(Reseller.id)).where(Reseller.is_active == True)  # noqa: E712
    )
    active_resellers = active_resellers_r.scalar_one() or 0
    suspended_resellers = total_resellers - active_resellers

    # User counts
    total_users_r = await db.execute(select(func.count(User.id)))
    total_users = total_users_r.scalar_one() or 0

    active_users_r = await db.execute(
        select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
    )
    active_users = active_users_r.scalar_one() or 0

    suspended_users = total_users - active_users

    # Users by role
    roles_r = await db.execute(
        select(User.role, func.count(User.id)).group_by(User.role)
    )
    by_role = {row[0]: row[1] for row in roles_r}

    # Session counts
    total_sessions_r = await db.execute(select(func.count(Session.id)))
    total_sessions = total_sessions_r.scalar_one() or 0

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_sessions_r = await db.execute(
        select(func.count(Session.id)).where(Session.created_at >= today_start)
    )
    sessions_today = today_sessions_r.scalar_one() or 0

    active_now_r = await db.execute(
        select(func.count(Session.id)).where(
            Session.status.in_(("running", "pending"))
        )
    )
    active_now = active_now_r.scalar_one() or 0

    queued_r = await db.execute(
        select(func.count(Session.id)).where(Session.status == "queued")
    )
    queued = queued_r.scalar_one() or 0

    # Usage this month
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    month_sessions_r = await db.execute(
        select(func.count(Session.id)).where(Session.created_at >= month_start)
    )
    month_sessions = month_sessions_r.scalar_one() or 0

    month_cost_r = await db.execute(
        select(func.sum(Session.cumulative_cost_usd)).where(
            Session.created_at >= month_start
        )
    )
    month_cost = float(month_cost_r.scalar_one() or 0.0)

    month_input_r = await db.execute(
        select(func.sum(Session.cumulative_input_tokens)).where(
            Session.created_at >= month_start
        )
    )
    month_input_tokens = int(month_input_r.scalar_one() or 0)

    month_output_r = await db.execute(
        select(func.sum(Session.cumulative_output_tokens)).where(
            Session.created_at >= month_start
        )
    )
    month_output_tokens = int(month_output_r.scalar_one() or 0)

    avg_cost = round(month_cost / month_sessions, 6) if month_sessions > 0 else 0.0

    # Top models this month
    models_r = await db.execute(
        select(Session.model, func.count(Session.id).label("cnt"))
        .where(Session.created_at >= month_start, Session.model.is_not(None))
        .group_by(Session.model)
        .order_by(func.count(Session.id).desc())
        .limit(5)
    )
    top_models = [{"model": row[0], "sessions": row[1]} for row in models_r]

    return PlatformStats(
        platform={
            "version": version,
            "uptime_seconds": 0,
        },
        resellers={
            "total": total_resellers,
            "active": active_resellers,
            "suspended": suspended_resellers,
        },
        users={
            "total": total_users,
            "active": active_users,
            "suspended": suspended_users,
            "by_role": by_role,
        },
        sessions={
            "total": total_sessions,
            "today": sessions_today,
            "active_now": active_now,
            "queued": queued,
        },
        usage_this_month={
            "total_sessions": month_sessions,
            "cost_usd": round(month_cost, 6),
            "input_tokens": month_input_tokens,
            "output_tokens": month_output_tokens,
            "avg_cost_usd": avg_cost,
            "top_models": top_models,
        },
        capacity={
            "global_max_concurrent": 4,
            "active": active_now,
            "redis_memory_mb": 0,
            "disk_usage_gb": 0,
        },
    )


@router.get("/usage", response_model=UsageResponse)
async def platform_usage(
    period: str = Query(default="month", description="'day', 'week', 'month', or 'custom'"),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    group_by: Optional[str] = Query(default=None, description="'reseller' or 'user'"),
    reseller_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> UsageResponse:
    """Aggregate usage report across all resellers."""
    now = datetime.now(timezone.utc)

    if period == "day":
        period_start = now - timedelta(days=1)
    elif period == "week":
        period_start = now - timedelta(weeks=1)
    elif period == "month":
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "custom" and start and end:
        period_start = start
        now = end
    else:
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    query = select(Session).where(Session.created_at >= period_start, Session.created_at <= now)
    if reseller_id:
        # Filter to sessions belonging to users of this reseller
        user_ids_r = await db.execute(
            select(User.id).where(User.reseller_id == reseller_id)
        )
        user_ids = [row[0] for row in user_ids_r]
        if user_ids:
            query = query.where(Session.user_id.in_(user_ids))
        else:
            # Reseller has no users — return empty immediately
            return UsageResponse(
                period=UsagePeriod(start=period_start, end=now),
                totals=UsageTotals(),
            )

    result = await db.execute(query)
    sessions = list(result.scalars().all())

    total_sessions = len(sessions)
    total_cost = sum(s.cumulative_cost_usd or 0.0 for s in sessions)
    total_input = sum(s.cumulative_input_tokens or 0 for s in sessions)
    total_output = sum(s.cumulative_output_tokens or 0 for s in sessions)
    unique_users = len({s.user_id for s in sessions})

    return UsageResponse(
        period=UsagePeriod(start=period_start, end=now),
        totals=UsageTotals(
            sessions=total_sessions,
            input_tokens=total_input,
            output_tokens=total_output,
            cost_usd=round(total_cost, 6),
            active_users=unique_users,
        ),
    )


@router.get("/audit", response_model=AuditLogResponse)
async def audit_log(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    reseller_id: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> AuditLogResponse:
    """List API key audit log entries with optional filtering."""
    query = select(APIKeyAuditLog)

    if reseller_id:
        query = query.where(APIKeyAuditLog.reseller_id == reseller_id)
    if action:
        query = query.where(APIKeyAuditLog.action == action)
    if start:
        query = query.where(APIKeyAuditLog.timestamp >= start)
    if end:
        query = query.where(APIKeyAuditLog.timestamp <= end)

    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar_one()

    offset = (page - 1) * per_page
    paged_result = await db.execute(
        query.order_by(APIKeyAuditLog.timestamp.desc()).offset(offset).limit(per_page)
    )
    entries_raw = list(paged_result.scalars().all())

    # Batch-load API key names and reseller names for display
    key_ids = {e.api_key_id for e in entries_raw if e.api_key_id}
    key_names: dict[str, str] = {}
    if key_ids:
        keys_r = await db.execute(
            select(APIKey.id, APIKey.name).where(APIKey.id.in_(key_ids))
        )
        for row in keys_r:
            key_names[row[0]] = row[1]

    res_ids = {e.reseller_id for e in entries_raw if e.reseller_id}
    res_names: dict[str, str] = {}
    if res_ids:
        res_r = await db.execute(
            select(Reseller.id, Reseller.name).where(Reseller.id.in_(res_ids))
        )
        for row in res_r:
            res_names[row[0]] = row[1]

    entries = [
        AuditLogEntry(
            id=e.id,
            timestamp=e.timestamp,
            api_key_name=key_names.get(e.api_key_id) if e.api_key_id else None,
            reseller_name=res_names.get(e.reseller_id) if e.reseller_id else None,
            action=e.action,
            target_user=e.target_user_id,
            ip_address=e.ip_address,
            status_code=e.status_code,
            error=e.error,
        )
        for e in entries_raw
    ]

    total_pages = math.ceil(total / per_page) if total > 0 else 1
    return AuditLogResponse(
        entries=entries,
        pagination=PaginationInfo(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
        ),
    )


# =============================================================================
# Data retention
# =============================================================================

@router.get("/retention", response_model=RetentionConfigResponse)
async def get_retention_config(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> RetentionConfigResponse:
    """Return current data retention configuration (days per table)."""
    from ...services.data_retention_service import data_retention_service
    config = await data_retention_service.get_retention_config(db)
    return RetentionConfigResponse(**config)


@router.put("/retention", response_model=RetentionConfigResponse)
async def update_retention_config(
    body: UpdateRetentionRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> RetentionConfigResponse:
    """Update data retention periods. Values in days (minimum 1)."""
    from ...services.data_retention_service import data_retention_service
    updates = body.model_dump(exclude_none=True)
    if not updates:
        config = await data_retention_service.get_retention_config(db)
    else:
        config = await data_retention_service.update_retention_config(
            db, updates, updated_by=auth.user_id,
        )
    return RetentionConfigResponse(**config)


@router.post("/retention/run", response_model=RetentionRunResponse)
async def run_retention(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(_require_admin),
) -> RetentionRunResponse:
    """Manually trigger a data retention purge and return results."""
    from ...services.data_retention_service import data_retention_service
    results = await data_retention_service.run_all(db)
    total = results.pop("total_purged", 0)
    return RetentionRunResponse(total_purged=total, tables=results)
