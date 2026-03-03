"""Background data-retention processor.

Runs once daily, purging records older than their configured retention
periods. Extends BaseProcessor.
"""
import logging

from .base_processor import BaseProcessor

logger = logging.getLogger(__name__)

# Default: run once every 24 hours
_DEFAULT_INTERVAL = 86400


class RetentionProcessor(BaseProcessor):
    """Background task that periodically runs data-retention purge."""

    def __init__(self, interval_seconds: int = _DEFAULT_INTERVAL) -> None:
        super().__init__(
            name="RetentionProcessor",
            interval_seconds=interval_seconds,
            error_sleep_seconds=60,
        )

    async def _tick(self) -> None:
        from ..db.database import AsyncSessionLocal
        from .data_retention_service import data_retention_service

        try:
            async with AsyncSessionLocal() as db:
                results = await data_retention_service.run_all(db)
                total = results.get("total_purged", 0)
                if total > 0:
                    logger.info(
                        "RetentionProcessor purged %d total rows", total,
                    )
        except Exception as e:
            logger.error("RetentionProcessor purge error: %s", e)
