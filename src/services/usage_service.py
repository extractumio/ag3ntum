"""Usage recording and aggregation service."""
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db.models import UsageRecord, User

logger = logging.getLogger(__name__)


class UsageService:
    """Records and queries session usage data."""

    async def record_session_usage(self, db: AsyncSession, session_id: str,
                                   user_id: str, reseller_id: Optional[str],
                                   model: str, input_tokens: int,
                                   output_tokens: int, cost_usd: float,
                                   duration_ms: int, num_turns: int,
                                   cache_creation_tokens: int = 0,
                                   cache_read_tokens: int = 0,
                                   ssh_commands: int = 0,
                                   files_uploaded: int = 0) -> UsageRecord:
        """Record a completed session's usage. Fire-and-forget pattern."""
        now = datetime.now(timezone.utc)
        record = UsageRecord(
            user_id=user_id,
            reseller_id=reseller_id,
            session_id=session_id,
            period_start=now,
            period_end=now,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            cost_usd=cost_usd,
            model=model,
            duration_ms=duration_ms,
            num_turns=num_turns,
            ssh_commands_executed=ssh_commands,
            files_uploaded=files_uploaded,
        )
        db.add(record)
        await db.commit()
        return record

    async def get_reseller_usage(self, db: AsyncSession, reseller_id: str,
                                 start: datetime, end: datetime,
                                 group_by: str = "user") -> dict[str, Any]:
        """Get aggregated usage for a reseller in a period."""
        query = select(
            func.count(UsageRecord.id).label("sessions"),
            func.sum(UsageRecord.input_tokens).label("input_tokens"),
            func.sum(UsageRecord.output_tokens).label("output_tokens"),
            func.sum(UsageRecord.cost_usd).label("cost_usd"),
            func.sum(UsageRecord.ssh_commands_executed).label("ssh_commands"),
        ).where(
            UsageRecord.reseller_id == reseller_id,
            UsageRecord.created_at >= start,
            UsageRecord.created_at <= end,
        )
        result = await db.execute(query)
        row = result.one()

        totals: dict[str, Any] = {
            "sessions": row.sessions or 0,
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "cost_usd": float(row.cost_usd or 0),
            "ssh_commands": row.ssh_commands or 0,
        }

        # Get per-user breakdown
        by_user = []
        if group_by == "user":
            user_query = select(
                UsageRecord.user_id,
                func.count(UsageRecord.id).label("sessions"),
                func.sum(UsageRecord.input_tokens).label("input_tokens"),
                func.sum(UsageRecord.output_tokens).label("output_tokens"),
                func.sum(UsageRecord.cost_usd).label("cost_usd"),
                func.sum(UsageRecord.ssh_commands_executed).label("ssh_commands"),
            ).where(
                UsageRecord.reseller_id == reseller_id,
                UsageRecord.created_at >= start,
                UsageRecord.created_at <= end,
            ).group_by(UsageRecord.user_id)

            result = await db.execute(user_query)
            for urow in result.all():
                # Look up username
                u_result = await db.execute(
                    select(User.username).where(User.id == urow.user_id)
                )
                username = u_result.scalar_one_or_none() or "unknown"
                by_user.append({
                    "user_id": urow.user_id,
                    "username": username,
                    "sessions": urow.sessions or 0,
                    "input_tokens": urow.input_tokens or 0,
                    "output_tokens": urow.output_tokens or 0,
                    "cost_usd": float(urow.cost_usd or 0),
                    "ssh_commands": urow.ssh_commands or 0,
                })

        # Count active users
        active_users_q = select(
            func.count(func.distinct(UsageRecord.user_id))
        ).where(
            UsageRecord.reseller_id == reseller_id,
            UsageRecord.created_at >= start,
            UsageRecord.created_at <= end,
        )
        active_users = (await db.execute(active_users_q)).scalar() or 0
        totals["active_users"] = active_users

        return {"totals": totals, "by_user": by_user}

    async def get_user_usage(self, db: AsyncSession, user_id: str,
                             start: datetime, end: datetime) -> dict[str, Any]:
        """Get aggregated usage for a single user."""
        query = select(
            func.count(UsageRecord.id).label("sessions"),
            func.sum(UsageRecord.input_tokens).label("input_tokens"),
            func.sum(UsageRecord.output_tokens).label("output_tokens"),
            func.sum(UsageRecord.cost_usd).label("cost_usd"),
            func.sum(UsageRecord.ssh_commands_executed).label("ssh_commands"),
        ).where(
            UsageRecord.user_id == user_id,
            UsageRecord.created_at >= start,
            UsageRecord.created_at <= end,
        )
        result = await db.execute(query)
        row = result.one()
        return {
            "sessions": row.sessions or 0,
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "cost_usd": float(row.cost_usd or 0),
            "ssh_commands": row.ssh_commands or 0,
        }

    async def get_reseller_metrics(self, db: AsyncSession, reseller_id: str,
                                   start: datetime, end: datetime) -> dict[str, Any]:
        """Get per-user usage in WHMCS MetricProvider format.

        Returns:
            {
              "metrics": {
                "sessions": {"type": "snapcount", "display": "Sessions"},
                "tokens":   {"type": "snapcount", "display": "Total Tokens"},
                "cost":     {"type": "snapcount", "display": "Cost (USD)"},
              },
              "usage": {
                "<username>": {"sessions": N, "tokens": N, "cost": N.NN},
                ...
              }
            }
        """
        # Per-user aggregated usage
        user_query = select(
            User.username,
            func.count(UsageRecord.id).label("sessions"),
            func.coalesce(
                func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), 0
            ).label("tokens"),
            func.coalesce(func.sum(UsageRecord.cost_usd), 0).label("cost"),
        ).join(
            User, User.id == UsageRecord.user_id
        ).where(
            UsageRecord.reseller_id == reseller_id,
            UsageRecord.created_at >= start,
            UsageRecord.created_at <= end,
        ).group_by(User.username)

        result = await db.execute(user_query)
        usage: dict[str, dict[str, Any]] = {}
        for row in result.all():
            usage[row.username] = {
                "sessions": row.sessions or 0,
                "tokens": int(row.tokens or 0),
                "cost": round(float(row.cost or 0), 6),
            }

        return {
            "metrics": {
                "sessions": {"type": "snapcount", "display": "Sessions"},
                "tokens": {"type": "snapcount", "display": "Total Tokens"},
                "cost": {"type": "snapcount", "display": "Cost (USD)"},
            },
            "usage": usage,
        }

    async def export_usage_data(
        self, db: AsyncSession, reseller_id: str,
        start: datetime, end: datetime,
        max_rows: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Export raw usage records for a reseller in a period.

        Returns a list of dicts suitable for CSV/JSON download.
        Limited to ``max_rows`` to prevent unbounded memory usage.
        """
        query = select(
            UsageRecord, User.username
        ).join(
            User, User.id == UsageRecord.user_id
        ).where(
            UsageRecord.reseller_id == reseller_id,
            UsageRecord.created_at >= start,
            UsageRecord.created_at <= end,
        ).order_by(UsageRecord.created_at.desc()).limit(max_rows)

        result = await db.execute(query)
        records = []
        for row in result.all():
            record = row[0]  # UsageRecord
            username = row[1]  # User.username
            records.append({
                "session_id": record.session_id,
                "username": username,
                "user_id": record.user_id,
                "model": record.model,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "cache_creation_tokens": record.cache_creation_tokens,
                "cache_read_tokens": record.cache_read_tokens,
                "cost_usd": round(record.cost_usd, 6),
                "duration_ms": record.duration_ms,
                "num_turns": record.num_turns,
                "ssh_commands": record.ssh_commands_executed,
                "files_uploaded": record.files_uploaded,
                "created_at": record.created_at.isoformat(),
            })
        return records


usage_service = UsageService()
