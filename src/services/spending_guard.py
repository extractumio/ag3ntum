"""Spending guard — enforces budget caps before and during sessions."""
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models import Reseller, ResellerQuota, UsageRecord, User

logger = logging.getLogger(__name__)


class SpendingGuard:
    """Three-tier spending cap enforcement: platform -> reseller -> user."""

    async def check_budget(
        self, db: AsyncSession, user_id: str, user: Optional[User] = None
    ) -> tuple[bool, str]:
        """Pre-session budget check.
        1. Check user daily/monthly spending limits
        2. Check reseller daily/monthly spending limits
        Returns (can_start, reason_if_not).

        Args:
            db: Database session.
            user_id: The user ID to check.
            user: Optional pre-fetched User object to skip the DB query.
        """
        if user is None:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
        if not user:
            return (True, "")  # Can't enforce limits without user record

        now = datetime.now(timezone.utc)

        # Check user-level spending limits
        if user.spending_limit_daily_usd is not None:
            today_cost = await self._get_user_daily_cost(db, user_id, now)
            if today_cost >= user.spending_limit_daily_usd:
                return (False, "User daily spending limit reached")

        if user.spending_limit_monthly_usd is not None:
            month_cost = await self._get_user_monthly_cost(db, user_id, now)
            if month_cost >= user.spending_limit_monthly_usd:
                return (False, "User monthly spending limit reached")

        # Check reseller-level spending limits
        if user.reseller_id:
            result = await db.execute(
                select(Reseller).where(Reseller.id == user.reseller_id)
            )
            reseller = result.scalar_one_or_none()
            if reseller:
                result = await db.execute(
                    select(ResellerQuota).where(
                        ResellerQuota.reseller_id == reseller.id
                    )
                )
                quota = result.scalar_one_or_none()
                if quota:
                    quota.reset_if_needed()
                    if (reseller.max_daily_spending_usd is not None
                            and quota.daily_cost_usd >= reseller.max_daily_spending_usd):
                        return (False, "Reseller daily spending limit reached")
                    if (reseller.max_monthly_spending_usd is not None
                            and quota.monthly_cost_usd >= reseller.max_monthly_spending_usd):
                        return (False, "Reseller monthly spending limit reached")

        return (True, "")

    async def check_session_budget(self, db: AsyncSession, user_id: str,
                                   session_cost_usd: float) -> tuple[bool, str]:
        """Per-turn session budget check.
        Check if cumulative session cost exceeds per-session limit.
        """
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return (True, "")  # Can't check, allow

        if (user.spending_limit_per_session_usd is not None
                and session_cost_usd >= user.spending_limit_per_session_usd):
            return (False, "Per-session spending limit reached")

        return (True, "")

    async def get_spending_status(self, db: AsyncSession, user_id: str) -> dict[str, Any]:
        """Get current spending status for a user."""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return {"status": "unknown"}

        now = datetime.now(timezone.utc)
        daily = await self._get_user_daily_cost(db, user_id, now)
        monthly = await self._get_user_monthly_cost(db, user_id, now)

        status = "ok"
        if user.spending_limit_monthly_usd:
            pct = (monthly / user.spending_limit_monthly_usd) * 100
            if pct >= 100:
                status = "exceeded"
            elif pct >= 80:
                status = "warning"

        return {
            "limits": {
                "monthly_usd": user.spending_limit_monthly_usd,
                "daily_usd": user.spending_limit_daily_usd,
                "per_session_usd": user.spending_limit_per_session_usd,
            },
            "current": {
                "monthly_usd": monthly,
                "daily_usd": daily,
            },
            "status": status,
        }

    async def _get_user_daily_cost(self, db: AsyncSession,
                                   user_id: str, now: datetime) -> float:
        """Get user's total cost for today."""
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.sum(UsageRecord.cost_usd)).where(
                UsageRecord.user_id == user_id,
                UsageRecord.created_at >= today_start,
            )
        )
        return float(result.scalar() or 0.0)

    async def _get_user_monthly_cost(self, db: AsyncSession,
                                     user_id: str, now: datetime) -> float:
        """Get user's total cost for current month."""
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = await db.execute(
            select(func.sum(UsageRecord.cost_usd)).where(
                UsageRecord.user_id == user_id,
                UsageRecord.created_at >= month_start,
            )
        )
        return float(result.scalar() or 0.0)


spending_guard = SpendingGuard()
