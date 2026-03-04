"""Data retention service — purges old records from configurable tables.

Retention periods are stored in the platform_config DB table and
can be modified via the admin API. Defaults are applied if no
overrides exist.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    APIKeyAuditLog,
    Event,
    UsageRecord,
    WebhookDeliveryLog,
)

logger = logging.getLogger(__name__)

# Default retention periods in days
DEFAULT_RETENTION = {
    "usage_records": 395,       # ~13 months
    "events": 30,
    "webhook_delivery_log": 90,
    "api_key_audit_log": 365,
}

# Map table key → (ORM model, timestamp column attribute name)
_TABLE_MAP = {
    "usage_records": (UsageRecord, "created_at"),
    "events": (Event, "timestamp"),
    "webhook_delivery_log": (WebhookDeliveryLog, "created_at"),
    "api_key_audit_log": (APIKeyAuditLog, "timestamp"),
}


class DataRetentionService:
    """Purges old records based on configurable retention periods."""

    def get_defaults(self) -> dict[str, int]:
        """Return default retention periods (days)."""
        return dict(DEFAULT_RETENTION)

    async def get_retention_config(
        self, db: AsyncSession,
    ) -> dict[str, int]:
        """Load retention config from platform_config, merged with defaults."""
        from .feature_flag_service import feature_flag_service
        await feature_flag_service.ensure_loaded(db)
        overrides = feature_flag_service.get_platform_retention()
        config = dict(DEFAULT_RETENTION)
        config.update(overrides)
        return config

    async def update_retention_config(
        self, db: AsyncSession, updates: dict[str, int],
        updated_by: str | None = None,
    ) -> dict[str, int]:
        """Update retention config in platform_config and return merged result."""
        from .feature_flag_service import feature_flag_service
        # Validate keys
        valid_keys = set(DEFAULT_RETENTION.keys())
        filtered = {k: v for k, v in updates.items() if k in valid_keys}
        if not filtered:
            return await self.get_retention_config(db)

        await feature_flag_service.update_platform_defaults(
            db, "retention", filtered, updated_by,
        )
        return await self.get_retention_config(db)

    async def purge_table(
        self, db: AsyncSession, table_key: str, retention_days: int,
        *, autocommit: bool = True,
    ) -> int:
        """Delete records older than retention_days from a single table.

        Returns the number of deleted rows.  Set ``autocommit=False``
        when called inside a batch that will commit later.
        """
        if table_key not in _TABLE_MAP:
            logger.warning("Unknown retention table: %s", table_key)
            return 0

        model, ts_col = _TABLE_MAP[table_key]
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        col = getattr(model, ts_col)

        result = await db.execute(
            delete(model).where(col < cutoff)
        )
        if autocommit:
            await db.commit()
        count = result.rowcount or 0
        if count > 0:
            logger.info(
                "Retention: purged %d rows from %s (older than %d days)",
                count, table_key, retention_days,
            )
        return count

    async def run_all(
        self, db: AsyncSession,
        config: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Run retention purge on all configured tables.

        Returns summary with per-table counts.
        """
        if config is None:
            config = await self.get_retention_config(db)

        results: dict[str, Any] = {}
        total = 0
        for table_key, days in config.items():
            if table_key not in _TABLE_MAP:
                continue
            count = await self.purge_table(
                db, table_key, days, autocommit=False,
            )
            results[table_key] = {"purged": count, "retention_days": days}
            total += count

        await db.commit()
        results["total_purged"] = total
        return results


data_retention_service = DataRetentionService()
