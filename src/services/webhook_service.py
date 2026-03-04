"""Webhook notification service for reseller event delivery.

Handles CRUD for webhook endpoints, HMAC-SHA256 signed delivery,
and delivery log management.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import WebhookDeliveryLog, WebhookEndpoint

logger = logging.getLogger(__name__)

# Canonical set of valid webhook event types — imported by reseller_models.py
VALID_EVENT_TYPES = {
    "session.completed",
    "session.failed",
    "spending.warning",
    "spending.exceeded",
    "user.suspended",
    "user.created",
}

# Retry backoff: attempt 1=30s, 2=2min, 3=10min, 4=1h, 5=6h
RETRY_DELAYS_SECONDS = [30, 120, 600, 3600, 21600]


class WebhookService:
    """Manages webhook endpoints and delivers events."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return a reusable httpx client (lazy-init, connection pooling)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=5.0,
                limits=httpx.Limits(max_connections=20),
            )
        return self._client

    async def close(self) -> None:
        """Close the httpx client. Call during app shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    async def create_endpoint(
        self, db: AsyncSession, reseller_id: str, url: str,
        events: list[str], description: Optional[str] = None,
    ) -> tuple[WebhookEndpoint, str]:
        """Create a webhook endpoint and return (endpoint, secret).

        The secret is generated and stored; returned once for the caller
        to save. It's used for HMAC-SHA256 signature verification.
        """
        secret = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex
        endpoint = WebhookEndpoint(
            id=str(uuid.uuid4()),
            reseller_id=reseller_id,
            url=url,
            secret=secret,
            events=json.dumps(events),
            is_active=True,
            description=description,
        )
        db.add(endpoint)
        await db.commit()
        await db.refresh(endpoint)
        return endpoint, secret

    async def list_endpoints(
        self, db: AsyncSession, reseller_id: str,
    ) -> list[WebhookEndpoint]:
        """List all webhook endpoints for a reseller."""
        result = await db.execute(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.reseller_id == reseller_id)
            .order_by(WebhookEndpoint.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_endpoint(
        self, db: AsyncSession, endpoint_id: str, reseller_id: str,
    ) -> Optional[WebhookEndpoint]:
        """Get a single endpoint owned by the reseller."""
        result = await db.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == endpoint_id,
                WebhookEndpoint.reseller_id == reseller_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_endpoint(
        self, db: AsyncSession, endpoint_id: str, reseller_id: str,
        **kwargs: Any,
    ) -> Optional[WebhookEndpoint]:
        """Update a webhook endpoint. Returns None if not found."""
        endpoint = await self.get_endpoint(db, endpoint_id, reseller_id)
        if endpoint is None:
            return None

        for key, value in kwargs.items():
            if key == "events" and isinstance(value, list):
                setattr(endpoint, key, json.dumps(value))
            elif hasattr(endpoint, key):
                setattr(endpoint, key, value)

        endpoint.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(endpoint)
        return endpoint

    async def delete_endpoint(
        self, db: AsyncSession, endpoint_id: str, reseller_id: str,
    ) -> bool:
        """Delete a webhook endpoint and its delivery logs. Returns success."""
        endpoint = await self.get_endpoint(db, endpoint_id, reseller_id)
        if endpoint is None:
            return False
        await db.delete(endpoint)
        await db.commit()
        return True

    # -------------------------------------------------------------------------
    # Delivery
    # -------------------------------------------------------------------------

    def _sign_payload(self, secret: str, payload: str) -> str:
        """Generate HMAC-SHA256 signature for a payload."""
        return hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def deliver(
        self, db: AsyncSession, endpoint: WebhookEndpoint,
        event_type: str, data: dict[str, Any],
    ) -> WebhookDeliveryLog:
        """Deliver a webhook event to an endpoint.

        Creates a delivery log entry. On success, marks as 'delivered'.
        On failure, marks as 'pending' with next_retry_at for the
        WebhookProcessor to retry.
        """
        payload = json.dumps({
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        })
        signature = self._sign_payload(endpoint.secret, payload)

        delivery = WebhookDeliveryLog(
            endpoint_id=endpoint.id,
            event_type=event_type,
            payload=payload,
            status="pending",
            attempts=0,
            max_attempts=5,
        )
        db.add(delivery)
        await db.flush()

        await self._attempt_delivery(db, delivery, endpoint.url, signature)
        return delivery

    async def _attempt_delivery(
        self, db: AsyncSession, delivery: WebhookDeliveryLog,
        url: str, signature: str,
    ) -> None:
        """Attempt to deliver a webhook. Updates delivery status."""
        delivery.attempts += 1
        delivery.last_attempt_at = datetime.now(timezone.utc)

        try:
            client = self._get_client()
            resp = await client.post(
                url,
                content=delivery.payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": f"sha256={signature}",
                    "X-Webhook-Event": delivery.event_type,
                },
            )
            delivery.response_status = resp.status_code
            delivery.response_body = resp.text[:1000]  # Truncate

            if 200 <= resp.status_code < 300:
                delivery.status = "delivered"
                delivery.next_retry_at = None
            else:
                self._schedule_retry(delivery)
        except Exception as e:
            delivery.error = str(e)[:500]
            self._schedule_retry(delivery)

        await db.commit()

    def _schedule_retry(self, delivery: WebhookDeliveryLog) -> None:
        """Schedule next retry or mark as failed if max attempts reached."""
        if delivery.attempts >= delivery.max_attempts:
            delivery.status = "failed"
            delivery.next_retry_at = None
        else:
            idx = min(delivery.attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)
            delay = RETRY_DELAYS_SECONDS[idx]
            delivery.next_retry_at = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            )
            delivery.status = "pending"

    async def fire_event(
        self, db: AsyncSession, reseller_id: str,
        event_type: str, data: dict[str, Any],
    ) -> int:
        """Fire an event to all active endpoints subscribed to it.

        Delivers to all matching endpoints in parallel to avoid blocking
        the caller for N * timeout seconds.
        Returns the number of deliveries created.
        """
        endpoints = await self.list_endpoints(db, reseller_id)

        # Filter to active endpoints subscribed to this event
        to_deliver = []
        for ep in endpoints:
            if not ep.is_active:
                continue
            try:
                subscribed = json.loads(ep.events)
            except (json.JSONDecodeError, TypeError):
                continue
            if event_type in subscribed or "*" in subscribed:
                to_deliver.append(ep)

        if not to_deliver:
            return 0

        async def _safe_deliver(ep: WebhookEndpoint) -> bool:
            try:
                await self.deliver(db, ep, event_type, data)
                return True
            except Exception as e:
                logger.error(
                    "Failed to deliver webhook %s to %s: %s",
                    event_type, ep.url, e,
                )
                return False

        results = await asyncio.gather(
            *[_safe_deliver(ep) for ep in to_deliver],
        )
        return sum(1 for ok in results if ok)

    async def get_deliveries(
        self, db: AsyncSession, endpoint_id: str, reseller_id: str,
        limit: int = 50,
    ) -> list[WebhookDeliveryLog]:
        """Get recent delivery logs for an endpoint."""
        # Verify ownership
        endpoint = await self.get_endpoint(db, endpoint_id, reseller_id)
        if endpoint is None:
            return []

        result = await db.execute(
            select(WebhookDeliveryLog)
            .where(WebhookDeliveryLog.endpoint_id == endpoint_id)
            .order_by(WebhookDeliveryLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def retry_pending(self, db: AsyncSession) -> int:
        """Retry all pending deliveries whose next_retry_at has passed.

        Called by WebhookProcessor on a timer. Returns count of retries.
        """
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(WebhookDeliveryLog)
            .where(
                WebhookDeliveryLog.status == "pending",
                WebhookDeliveryLog.next_retry_at.isnot(None),
                WebhookDeliveryLog.next_retry_at <= now,
            )
            .limit(50)
        )
        deliveries = list(result.scalars().all())
        if not deliveries:
            return 0

        # Batch-load all needed endpoints in one query
        endpoint_ids = list({d.endpoint_id for d in deliveries})
        ep_result = await db.execute(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.id.in_(endpoint_ids))
        )
        endpoints_by_id = {
            ep.id: ep for ep in ep_result.scalars().all()
        }

        # Mark dead-endpoint deliveries as failed in a single batch
        live_deliveries = []
        for delivery in deliveries:
            endpoint = endpoints_by_id.get(delivery.endpoint_id)
            if endpoint is None or not endpoint.is_active:
                delivery.status = "failed"
                delivery.error = "Endpoint deleted or inactive"
            else:
                live_deliveries.append((delivery, endpoint))

        if any(d.status == "failed" for d in deliveries):
            await db.commit()

        count = 0
        for delivery, endpoint in live_deliveries:
            signature = self._sign_payload(endpoint.secret, delivery.payload)
            await self._attempt_delivery(
                db, delivery, endpoint.url, signature,
            )
            count += 1

        return count


    async def fire_best_effort(
        self, db: AsyncSession, reseller_id: Optional[str],
        event_type: str, data: dict[str, Any],
    ) -> None:
        """Fire a webhook event, swallowing all errors (best-effort).

        Use this wrapper from hot paths where webhook delivery must
        never interfere with the primary operation.
        """
        if not reseller_id:
            return
        try:
            await self.fire_event(db, reseller_id, event_type, data)
        except Exception as e:
            logger.debug("Webhook fire failed (non-critical): %s", e)


webhook_service = WebhookService()
