"""Reseller-level quota enforcement service."""
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models import Reseller, ResellerQuota, User

logger = logging.getLogger(__name__)


class ResellerQuotaService:
    """Checks reseller-level quotas before allowing task start."""

    async def check_reseller_quota(
        self, db: AsyncSession, user_id: str, user: Optional[User] = None
    ) -> tuple[bool, str]:
        """Check if task can start based on reseller quotas.
        1. Look up user's reseller_id
        2. If no reseller, allow (direct admin user)
        3. If reseller not active, deny
        4. Check reseller's daily task limit
        Returns (can_start, reason_if_not).

        Args:
            db: Database session.
            user_id: The user ID to check.
            user: Optional pre-fetched User object to skip the DB query.
        """
        if user is None:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
        if not user or not user.reseller_id:
            return (True, "")  # No reseller = direct user, no reseller quota

        # Get reseller
        result = await db.execute(select(Reseller).where(Reseller.id == user.reseller_id))
        reseller = result.scalar_one_or_none()
        if not reseller:
            return (True, "")
        if not reseller.is_active:
            return (False, "Reseller account is suspended")

        # Get quota
        result = await db.execute(
            select(ResellerQuota).where(ResellerQuota.reseller_id == reseller.id)
        )
        quota = result.scalar_one_or_none()
        if not quota:
            return (True, "")

        quota.reset_if_needed()

        if quota.tasks_today >= reseller.max_daily_tasks:
            return (False, f"Reseller daily task limit reached ({reseller.max_daily_tasks})")

        return (True, "")

    async def _get_quota(self, db: AsyncSession,
                         reseller_id: str) -> Optional[ResellerQuota]:
        """Fetch and reset the reseller quota if needed."""
        result = await db.execute(
            select(ResellerQuota).where(
                ResellerQuota.reseller_id == reseller_id
            )
        )
        quota = result.scalar_one_or_none()
        if quota:
            quota.reset_if_needed()
        return quota

    async def increment_task_count(self, db: AsyncSession,
                                   reseller_id: str) -> None:
        """Increment daily task count for reseller."""
        quota = await self._get_quota(db, reseller_id)
        if quota:
            quota.tasks_today += 1
            await db.commit()

    async def record_cost(self, db: AsyncSession, reseller_id: str,
                          cost_usd: float, input_tokens: int = 0,
                          output_tokens: int = 0) -> None:
        """Record cost against reseller quota."""
        quota = await self._get_quota(db, reseller_id)
        if quota:
            quota.daily_cost_usd += cost_usd
            quota.monthly_cost_usd += cost_usd
            quota.monthly_input_tokens += input_tokens
            quota.monthly_output_tokens += output_tokens
            await db.commit()


reseller_quota_service = ResellerQuotaService()
