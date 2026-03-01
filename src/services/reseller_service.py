"""
Reseller service for Ag3ntum API.

Handles reseller CRUD operations, lifecycle management (suspend/unsuspend/delete),
and aggregate statistics. Each reseller gets an owner user account with role="reseller"
that is used for authentication; resellers themselves do not run sandboxed sessions
and have no Linux UID.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.models import (
    APIKey, APIKeyAuditLog, Reseller, ResellerQuota, ResellerSkillLibrary,
    Session, Token, User, UserQuota, UserSkill, UsageRecord,
)

logger = logging.getLogger(__name__)


class ResellerNotFoundError(Exception):
    """Raised when a reseller cannot be found."""
    pass


class ResellerService:
    """CRUD and lifecycle management for resellers."""

    async def create_reseller(
        self,
        db: AsyncSession,
        name: str,
        company: Optional[str],
        contact_email: str,
        password: str,
        max_users: int = 50,
        max_concurrent_tasks: int = 10,
        max_daily_tasks: int = 500,
        llm_provider: Optional[str] = None,
        features: Optional[dict] = None,
        notes: Optional[str] = None,
        max_monthly_spending_usd: Optional[float] = None,
        max_daily_spending_usd: Optional[float] = None,
        spending_alert_threshold_pct: int = 80,
    ) -> Reseller:
        """Create reseller + owner user account (role=reseller) + quota record.

        Side effects:
        1. Create User with role="reseller", username derived from name
        2. Create Reseller linked to that user
        3. Create ResellerQuota with zeroed counters
        Does NOT create a Linux user (resellers don't run sandboxed sessions).
        """
        username = (
            "reseller_"
            + name.lower().replace(" ", "_").replace("-", "_")
        )
        password_hash = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt()
        ).decode()

        owner_id = str(uuid.uuid4())
        owner = User(
            id=owner_id,
            username=username,
            email=contact_email,
            password_hash=password_hash,
            role="reseller",
            jwt_secret=uuid.uuid4().hex,
            linux_uid=None,
            is_active=True,
        )
        db.add(owner)
        await db.flush()

        reseller_id = str(uuid.uuid4())
        reseller = Reseller(
            id=reseller_id,
            name=name,
            company=company,
            contact_email=contact_email,
            is_active=True,
            owner_user_id=owner_id,
            max_users=max_users,
            max_concurrent_tasks=max_concurrent_tasks,
            max_daily_tasks=max_daily_tasks,
            max_monthly_spending_usd=max_monthly_spending_usd,
            max_daily_spending_usd=max_daily_spending_usd,
            spending_alert_threshold_pct=spending_alert_threshold_pct,
            llm_provider=llm_provider,
            features_json=json.dumps(features) if features else None,
            notes=notes,
        )
        db.add(reseller)
        await db.flush()

        # Link owner user back to the reseller
        owner.reseller_id = reseller_id

        quota = ResellerQuota(
            reseller_id=reseller_id,
            current_user_count=0,
            tasks_today=0,
            last_reset=datetime.now(timezone.utc),
            monthly_input_tokens=0,
            monthly_output_tokens=0,
            monthly_cost_usd=0.0,
            monthly_reset=datetime.now(timezone.utc),
            daily_cost_usd=0.0,
            daily_cost_reset=datetime.now(timezone.utc),
        )
        db.add(quota)
        await db.commit()

        await db.refresh(reseller)
        logger.info(f"Created reseller '{name}' (id={reseller_id}, owner={username})")
        return reseller

    async def get_reseller(
        self, db: AsyncSession, reseller_id: str
    ) -> Optional[Reseller]:
        """Get reseller by ID with eager-loaded relationships."""
        result = await db.execute(
            select(Reseller)
            .options(
                selectinload(Reseller.owner),
                selectinload(Reseller.users),
                selectinload(Reseller.api_keys),
                selectinload(Reseller.quota),
            )
            .where(Reseller.id == reseller_id)
        )
        return result.scalar_one_or_none()

    async def get_reseller_by_owner(
        self, db: AsyncSession, owner_user_id: str
    ) -> Optional[Reseller]:
        """Get reseller by owner user ID."""
        result = await db.execute(
            select(Reseller)
            .options(
                selectinload(Reseller.owner),
                selectinload(Reseller.users),
                selectinload(Reseller.api_keys),
                selectinload(Reseller.quota),
            )
            .where(Reseller.owner_user_id == owner_user_id)
        )
        return result.scalar_one_or_none()

    async def list_resellers(
        self,
        db: AsyncSession,
        page: int = 1,
        per_page: int = 50,
        status_filter: str = "all",
        search: Optional[str] = None,
    ) -> tuple[list[Reseller], int]:
        """List resellers with pagination, filtering, and search.

        Returns (resellers, total_count).
        """
        base_query = select(Reseller)

        if status_filter == "active":
            base_query = base_query.where(Reseller.is_active == True)  # noqa: E712
        elif status_filter == "suspended":
            base_query = base_query.where(Reseller.is_active == False)  # noqa: E712

        if search:
            base_query = base_query.where(
                Reseller.name.ilike(f"%{search}%")
            )

        count_result = await db.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        offset = (page - 1) * per_page
        paged_query = (
            base_query
            .options(
                selectinload(Reseller.owner),
                selectinload(Reseller.quota),
            )
            .order_by(Reseller.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        result = await db.execute(paged_query)
        resellers = list(result.scalars().all())
        return resellers, total

    async def update_reseller(
        self, db: AsyncSession, reseller_id: str, **kwargs
    ) -> Reseller:
        """Update reseller fields. Only non-None kwargs are applied."""
        reseller = await self.get_reseller(db, reseller_id)
        if reseller is None:
            raise ResellerNotFoundError(f"Reseller {reseller_id} not found")

        allowed_fields = {
            "name", "company", "contact_email", "max_users",
            "max_concurrent_tasks", "max_daily_tasks", "llm_provider",
            "features_json", "notes", "max_monthly_spending_usd",
            "max_daily_spending_usd", "spending_alert_threshold_pct",
        }
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                setattr(reseller, key, value)

        reseller.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(reseller)
        logger.info(f"Updated reseller {reseller_id}: {list(kwargs.keys())}")
        return reseller

    async def suspend_reseller(
        self, db: AsyncSession, reseller_id: str, reason: Optional[str] = None
    ) -> dict:
        """Suspend reseller and all their users, API keys, and active sessions.

        Uses bulk SQL statements to avoid SQLAlchemy CircularDependencyError
        caused by mutual FK between Reseller.owner_user_id and User.reseller_id.

        Steps:
        1. Store pre_suspend_user_states (JSON mapping user_id -> was_active)
        2. Set all managed users is_active=False
        3. Cancel active sessions (status -> 'cancelled')
        4. Deactivate all API keys
        5. Set reseller is_active=False, suspended_at, suspended_reason

        Returns dict with counts of affected records.
        """
        # Verify reseller exists (lightweight query, no eager loads)
        result = await db.execute(
            select(Reseller).where(Reseller.id == reseller_id)
        )
        reseller = result.scalar_one_or_none()
        if reseller is None:
            raise ResellerNotFoundError(f"Reseller {reseller_id} not found")

        # Snapshot pre-suspension user states via query
        user_rows = (await db.execute(
            select(User.id, User.is_active)
            .where(User.reseller_id == reseller_id)
        )).all()
        user_ids = [row[0] for row in user_rows]
        pre_states = {row[0]: row[1] for row in user_rows}

        # 1. Store pre-suspension states
        await db.execute(
            update(Reseller)
            .where(Reseller.id == reseller_id)
            .values(pre_suspend_user_states=json.dumps(pre_states))
        )

        # 2. Deactivate all managed users
        users_suspended = 0
        if user_ids:
            result = await db.execute(
                update(User)
                .where(User.reseller_id == reseller_id)
                .values(is_active=False)
            )
            users_suspended = result.rowcount

        # 3. Cancel active sessions
        sessions_cancelled = 0
        if user_ids:
            active_statuses = ("pending", "running", "queued")
            result = await db.execute(
                update(Session)
                .where(
                    Session.user_id.in_(user_ids),
                    Session.status.in_(active_statuses),
                )
                .values(status="cancelled")
            )
            sessions_cancelled = result.rowcount

        # 4. Deactivate all API keys
        result = await db.execute(
            update(APIKey)
            .where(APIKey.reseller_id == reseller_id, APIKey.is_active.is_(True))
            .values(is_active=False)
        )
        keys_deactivated = result.rowcount

        # 5. Suspend the reseller
        now = datetime.now(timezone.utc)
        await db.execute(
            update(Reseller)
            .where(Reseller.id == reseller_id)
            .values(
                is_active=False,
                suspended_at=now,
                suspended_reason=reason,
                updated_at=now,
            )
        )

        await db.commit()
        logger.info(
            f"Suspended reseller {reseller_id}: "
            f"{users_suspended} users, {sessions_cancelled} sessions, "
            f"{keys_deactivated} API keys affected"
        )
        return {
            "reseller_id": reseller_id,
            "users_suspended": users_suspended,
            "sessions_cancelled": sessions_cancelled,
            "keys_deactivated": keys_deactivated,
        }

    async def unsuspend_reseller(
        self, db: AsyncSession, reseller_id: str
    ) -> dict:
        """Restore reseller from suspension.

        Uses bulk SQL statements to avoid SQLAlchemy CircularDependencyError.

        Steps:
        1. Restore managed users to their pre-suspension active states
        2. Reactivate API keys
        3. Set reseller is_active=True, clear suspended_at

        Returns dict with counts of restored records.
        """
        result = await db.execute(
            select(Reseller).where(Reseller.id == reseller_id)
        )
        reseller = result.scalar_one_or_none()
        if reseller is None:
            raise ResellerNotFoundError(f"Reseller {reseller_id} not found")

        pre_states: dict = {}
        if reseller.pre_suspend_user_states:
            try:
                pre_states = json.loads(reseller.pre_suspend_user_states)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    f"Could not parse pre_suspend_user_states for reseller {reseller_id}; "
                    "restoring all users as active"
                )

        # Restore each user to their pre-suspension state
        users_restored = 0
        if pre_states:
            for user_id, was_active in pre_states.items():
                await db.execute(
                    update(User)
                    .where(User.id == user_id)
                    .values(is_active=was_active)
                )
                users_restored += 1
        else:
            # No pre-states saved — restore all as active
            result = await db.execute(
                update(User)
                .where(User.reseller_id == reseller_id)
                .values(is_active=True)
            )
            users_restored = result.rowcount

        # Reactivate all API keys
        result = await db.execute(
            update(APIKey)
            .where(APIKey.reseller_id == reseller_id)
            .values(is_active=True)
        )
        keys_reactivated = result.rowcount

        # Unsuspend the reseller
        now = datetime.now(timezone.utc)
        await db.execute(
            update(Reseller)
            .where(Reseller.id == reseller_id)
            .values(
                is_active=True,
                suspended_at=None,
                suspended_reason=None,
                pre_suspend_user_states=None,
                updated_at=now,
            )
        )

        await db.commit()
        logger.info(
            f"Unsuspended reseller {reseller_id}: "
            f"{users_restored} users restored, {keys_reactivated} API keys reactivated"
        )
        return {
            "reseller_id": reseller_id,
            "users_restored": users_restored,
            "keys_reactivated": keys_reactivated,
        }

    async def delete_reseller(
        self, db: AsyncSession, reseller_id: str
    ) -> dict:
        """Delete reseller and all associated data. Irreversible.

        Uses bulk SQL to avoid CircularDependencyError. Deletion order:
        1. Break circular FK: null out User.reseller_id and Reseller.owner_user_id
        2. Delete child records (sessions, tokens, quotas, skills, usage, etc.)
        3. Delete users
        4. Delete reseller (cascades API keys, quota, skill library)

        Returns dict with deletion counts.
        """
        # Verify reseller exists (lightweight)
        result = await db.execute(
            select(Reseller.id).where(Reseller.id == reseller_id)
        )
        row = result.one_or_none()
        if row is None:
            raise ResellerNotFoundError(f"Reseller {reseller_id} not found")

        # Collect all user IDs (including owner)
        user_rows = (await db.execute(
            select(User.id).where(User.reseller_id == reseller_id)
        )).all()
        user_ids = [r[0] for r in user_rows]

        # 1. Break circular FK: null out User.reseller_id so users can be
        #    deleted independently of the reseller record
        await db.execute(
            update(User)
            .where(User.reseller_id == reseller_id)
            .values(reseller_id=None)
        )
        await db.flush()

        # 2. Delete child records for affected users
        sessions_deleted = 0
        if user_ids:
            # Sessions
            result = await db.execute(
                delete(Session).where(Session.user_id.in_(user_ids))
            )
            sessions_deleted = result.rowcount

            # Tokens
            await db.execute(
                delete(Token).where(Token.user_id.in_(user_ids))
            )

            # User quotas
            await db.execute(
                delete(UserQuota).where(UserQuota.user_id.in_(user_ids))
            )

            # User skills
            await db.execute(
                delete(UserSkill).where(UserSkill.user_id.in_(user_ids))
            )

            # Usage records
            await db.execute(
                delete(UsageRecord).where(UsageRecord.user_id.in_(user_ids))
            )

        # API key audit logs
        key_ids_result = await db.execute(
            select(APIKey.id).where(APIKey.reseller_id == reseller_id)
        )
        key_ids = [r[0] for r in key_ids_result.all()]
        if key_ids:
            await db.execute(
                delete(APIKeyAuditLog).where(APIKeyAuditLog.api_key_id.in_(key_ids))
            )

        # API keys
        await db.execute(
            delete(APIKey).where(APIKey.reseller_id == reseller_id)
        )

        # Reseller skill library
        await db.execute(
            delete(ResellerSkillLibrary).where(
                ResellerSkillLibrary.reseller_id == reseller_id
            )
        )

        # Reseller quota
        await db.execute(
            delete(ResellerQuota).where(ResellerQuota.reseller_id == reseller_id)
        )

        # 3. Delete reseller BEFORE users (reseller.owner_user_id FK → users.id)
        await db.execute(
            delete(Reseller).where(Reseller.id == reseller_id)
        )

        # 4. Delete users (safe now: reseller_id FK was nulled, reseller row is gone)
        users_deleted = 0
        if user_ids:
            result = await db.execute(
                delete(User).where(User.id.in_(user_ids))
            )
            users_deleted = result.rowcount

        await db.commit()
        logger.info(
            f"Deleted reseller {reseller_id}: "
            f"{users_deleted} users, {sessions_deleted} sessions removed"
        )
        return {
            "reseller_id": reseller_id,
            "users_deleted": users_deleted,
            "sessions_deleted": sessions_deleted,
        }

    async def get_reseller_stats(
        self, db: AsyncSession, reseller_id: str
    ) -> dict:
        """Get aggregate statistics for a reseller."""
        reseller = await self.get_reseller(db, reseller_id)
        if reseller is None:
            raise ResellerNotFoundError(f"Reseller {reseller_id} not found")

        user_ids = [user.id for user in reseller.users]

        active_sessions = 0
        total_sessions = 0
        total_cost_usd = 0.0

        if user_ids:
            active_result = await db.execute(
                select(func.count(Session.id)).where(
                    Session.user_id.in_(user_ids),
                    Session.status.in_(("pending", "running", "queued")),
                )
            )
            active_sessions = active_result.scalar_one() or 0

            total_result = await db.execute(
                select(func.count(Session.id)).where(
                    Session.user_id.in_(user_ids)
                )
            )
            total_sessions = total_result.scalar_one() or 0

            cost_result = await db.execute(
                select(func.sum(Session.cumulative_cost_usd)).where(
                    Session.user_id.in_(user_ids)
                )
            )
            total_cost_usd = cost_result.scalar_one() or 0.0

        quota = reseller.quota
        quota.reset_if_needed() if quota else None

        return {
            "reseller_id": reseller_id,
            "name": reseller.name,
            "is_active": reseller.is_active,
            "user_count": len(reseller.users),
            "max_users": reseller.max_users,
            "active_sessions": active_sessions,
            "total_sessions": total_sessions,
            "total_cost_usd": round(total_cost_usd, 6),
            "api_key_count": len(reseller.api_keys),
            "quota": {
                "tasks_today": quota.tasks_today if quota else 0,
                "monthly_cost_usd": quota.monthly_cost_usd if quota else 0.0,
                "daily_cost_usd": quota.daily_cost_usd if quota else 0.0,
                "monthly_input_tokens": quota.monthly_input_tokens if quota else 0,
                "monthly_output_tokens": quota.monthly_output_tokens if quota else 0,
            } if quota else None,
        }

    async def get_user_count(
        self, db: AsyncSession, reseller_id: str
    ) -> int:
        """Get current user count for a reseller."""
        result = await db.execute(
            select(func.count(User.id)).where(
                User.reseller_id == reseller_id
            )
        )
        return result.scalar_one() or 0


reseller_service = ResellerService()
