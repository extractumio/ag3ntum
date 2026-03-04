"""Unit tests for WebhookService — CRUD, signing, delivery, retries."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.webhook_service import WebhookService, RETRY_DELAYS_SECONDS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc():
    return WebhookService()


@pytest.fixture
def mock_db():
    """Async DB session mock."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    return db


def _make_endpoint(
    id="ep-1", reseller_id="res-1", url="https://example.com/hook",
    secret="abc123", events='["session.completed"]', is_active=True,
):
    ep = MagicMock()
    ep.id = id
    ep.reseller_id = reseller_id
    ep.url = url
    ep.secret = secret
    ep.events = events
    ep.is_active = is_active
    ep.created_at = datetime.now(timezone.utc)
    return ep


def _make_delivery(
    endpoint_id="ep-1", event_type="session.completed",
    payload='{"event":"session.completed"}',
    status="pending", attempts=1, max_attempts=5,
):
    d = MagicMock()
    d.endpoint_id = endpoint_id
    d.event_type = event_type
    d.payload = payload
    d.status = status
    d.attempts = attempts
    d.max_attempts = max_attempts
    d.last_attempt_at = None
    d.next_retry_at = None
    d.response_status = None
    d.response_body = None
    d.error = None
    return d


# ---------------------------------------------------------------------------
# HMAC signing
# ---------------------------------------------------------------------------

class TestSignPayload:
    def test_sign_produces_hex_digest(self, svc):
        sig = svc._sign_payload("secret", '{"test": true}')
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest

    def test_sign_is_deterministic(self, svc):
        p = '{"event":"test"}'
        assert svc._sign_payload("key", p) == svc._sign_payload("key", p)

    def test_different_secrets_different_sigs(self, svc):
        p = '{"event":"test"}'
        assert svc._sign_payload("a", p) != svc._sign_payload("b", p)


# ---------------------------------------------------------------------------
# Retry scheduling
# ---------------------------------------------------------------------------

class TestScheduleRetry:
    def test_schedule_first_retry(self, svc):
        d = _make_delivery(attempts=1)
        svc._schedule_retry(d)
        assert d.status == "pending"
        assert d.next_retry_at is not None

    def test_max_attempts_marks_failed(self, svc):
        d = _make_delivery(attempts=5, max_attempts=5)
        svc._schedule_retry(d)
        assert d.status == "failed"
        assert d.next_retry_at is None

    def test_backoff_increases(self, svc):
        delays = []
        for attempt in range(1, 6):
            d = _make_delivery(attempts=attempt, max_attempts=6)
            before = datetime.now(timezone.utc)
            svc._schedule_retry(d)
            if d.next_retry_at:
                delta = (d.next_retry_at - before).total_seconds()
                delays.append(delta)
        # Each subsequent delay should be >= previous
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1]


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

class TestDelivery:
    @pytest.mark.asyncio
    async def test_deliver_success(self, svc, mock_db):
        ep = _make_endpoint()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(svc, "_get_client", return_value=mock_client):
            delivery = await svc.deliver(
                mock_db, ep, "session.completed", {"session_id": "s1"},
            )
            assert delivery.status == "delivered"
            assert delivery.attempts == 1

    @pytest.mark.asyncio
    async def test_deliver_failure_schedules_retry(self, svc, mock_db):
        ep = _make_endpoint()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(svc, "_get_client", return_value=mock_client):
            delivery = await svc.deliver(
                mock_db, ep, "session.completed", {"session_id": "s1"},
            )
            assert delivery.status == "pending"
            assert delivery.next_retry_at is not None

    @pytest.mark.asyncio
    async def test_deliver_exception_schedules_retry(self, svc, mock_db):
        ep = _make_endpoint()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))

        with patch.object(svc, "_get_client", return_value=mock_client):
            delivery = await svc.deliver(
                mock_db, ep, "session.completed", {"session_id": "s1"},
            )
            assert delivery.status == "pending"
            assert delivery.error is not None


# ---------------------------------------------------------------------------
# fire_event
# ---------------------------------------------------------------------------

class TestFireEvent:
    @pytest.mark.asyncio
    async def test_fire_event_skips_inactive(self, svc, mock_db):
        ep = _make_endpoint(is_active=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [ep]
        mock_db.execute = AsyncMock(return_value=mock_result)

        count = await svc.fire_event(
            mock_db, "res-1", "session.completed", {},
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_fire_event_skips_unsubscribed(self, svc, mock_db):
        ep = _make_endpoint(events='["spending.warning"]')

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [ep]
        mock_db.execute = AsyncMock(return_value=mock_result)

        count = await svc.fire_event(
            mock_db, "res-1", "session.completed", {},
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_fire_event_wildcard_subscribes_all(self, svc, mock_db):
        ep = _make_endpoint(events='["*"]')

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [ep]
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch.object(svc, "deliver", new_callable=AsyncMock) as mock_deliver:
            mock_deliver.return_value = _make_delivery()
            count = await svc.fire_event(
                mock_db, "res-1", "session.completed", {},
            )
            assert count == 1
            mock_deliver.assert_called_once()


# ---------------------------------------------------------------------------
# Retry backoff values
# ---------------------------------------------------------------------------

class TestRetryDelays:
    def test_five_delay_tiers(self):
        assert len(RETRY_DELAYS_SECONDS) == 5

    def test_delays_increase(self):
        for i in range(1, len(RETRY_DELAYS_SECONDS)):
            assert RETRY_DELAYS_SECONDS[i] > RETRY_DELAYS_SECONDS[i - 1]
