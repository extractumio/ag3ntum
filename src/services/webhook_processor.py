"""Background webhook retry processor.

Periodically checks for pending webhook deliveries whose retry time
has passed and re-attempts delivery. Extends BaseProcessor.
"""
import logging

from .base_processor import BaseProcessor

logger = logging.getLogger(__name__)


class WebhookProcessor(BaseProcessor):
    """Background task that retries failed webhook deliveries."""

    def __init__(self, interval_seconds: int = 30) -> None:
        super().__init__(
            name="WebhookProcessor",
            interval_seconds=interval_seconds,
            error_sleep_seconds=5,
        )

    async def _tick(self) -> None:
        from ..db.database import AsyncSessionLocal
        from .webhook_service import webhook_service

        try:
            async with AsyncSessionLocal() as db:
                count = await webhook_service.retry_pending(db)
                if count > 0:
                    logger.info("WebhookProcessor retried %d deliveries", count)
        except Exception as e:
            logger.error("WebhookProcessor retry error: %s", e)
