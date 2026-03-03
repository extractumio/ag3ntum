"""Unit tests for simplify fixes — fire_best_effort, update_last_used,
retry_pending batch load."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.webhook_service import WebhookService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc():
    return WebhookService()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    return db


def _make_endpoint(id="ep-1", reseller_id="res-1",
                   url="https://example.com/hook", secret="abc123",
                   events='["session.completed"]', is_active=True):
    ep = MagicMock()
    ep.id = id
    ep.reseller_id = reseller_id
    ep.url = url
    ep.secret = secret
    ep.events = events
    ep.is_active = is_active
    ep.created_at = datetime.now(timezone.utc)
    return ep


def _make_delivery(endpoint_id="ep-1", event_type="session.completed",
                   payload='{"event":"session.completed"}',
                   status="pending", attempts=1, max_attempts=5):
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
# fire_best_effort
# ---------------------------------------------------------------------------

class TestFireBestEffort:
    @pytest.mark.asyncio
    async def test_skips_null_reseller_id(self, svc, mock_db):
        with patch.object(svc, "fire_event", new_callable=AsyncMock) as mock_fire:
            await svc.fire_best_effort(mock_db, None, "session.completed", {})
            mock_fire.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_fire_event(self, svc, mock_db):
        with patch.object(svc, "fire_event", new_callable=AsyncMock) as mock_fire:
            mock_fire.return_value = 1
            await svc.fire_best_effort(
                mock_db, "res-1", "session.completed", {"key": "val"},
            )
            mock_fire.assert_called_once_with(
                mock_db, "res-1", "session.completed", {"key": "val"},
            )

    @pytest.mark.asyncio
    async def test_swallows_exception(self, svc, mock_db):
        with patch.object(svc, "fire_event", new_callable=AsyncMock) as mock_fire:
            mock_fire.side_effect = RuntimeError("DB error")
            # Should not raise
            await svc.fire_best_effort(
                mock_db, "res-1", "session.completed", {},
            )

    @pytest.mark.asyncio
    async def test_skips_empty_string_reseller_id(self, svc, mock_db):
        """Empty string is falsy, should be skipped."""
        with patch.object(svc, "fire_event", new_callable=AsyncMock) as mock_fire:
            await svc.fire_best_effort(mock_db, "", "session.completed", {})
            mock_fire.assert_not_called()


# ---------------------------------------------------------------------------
# retry_pending — batch endpoint load
# ---------------------------------------------------------------------------

class TestRetryPendingBatchLoad:
    @pytest.mark.asyncio
    async def test_empty_deliveries_returns_zero(self, svc, mock_db):
        # First execute: delivery query returns empty
        mock_delivery_result = MagicMock()
        mock_delivery_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_delivery_result)

        count = await svc.retry_pending(mock_db)
        assert count == 0
        # Only one query (delivery fetch), no endpoint query
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_batch_loads_endpoints(self, svc, mock_db):
        """Two deliveries for same endpoint => only one endpoint query."""
        d1 = _make_delivery(endpoint_id="ep-1")
        d2 = _make_delivery(endpoint_id="ep-1")
        ep = _make_endpoint(id="ep-1")

        # First execute: delivery query
        mock_delivery_result = MagicMock()
        mock_delivery_result.scalars.return_value.all.return_value = [d1, d2]

        # Second execute: endpoint batch query
        mock_ep_result = MagicMock()
        mock_ep_result.scalars.return_value.all.return_value = [ep]

        call_count = 0

        async def _side_effect(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_delivery_result
            return mock_ep_result

        mock_db.execute = AsyncMock(side_effect=_side_effect)

        with patch.object(svc, "_attempt_delivery", new_callable=AsyncMock):
            count = await svc.retry_pending(mock_db)

        assert count == 2
        # Exactly 2 queries: delivery fetch + batch endpoint fetch
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_multiple_endpoints_batch_loaded(self, svc, mock_db):
        """Deliveries for different endpoints still use one batch query."""
        d1 = _make_delivery(endpoint_id="ep-1")
        d2 = _make_delivery(endpoint_id="ep-2")
        ep1 = _make_endpoint(id="ep-1")
        ep2 = _make_endpoint(id="ep-2", url="https://other.com/hook")

        mock_delivery_result = MagicMock()
        mock_delivery_result.scalars.return_value.all.return_value = [d1, d2]

        mock_ep_result = MagicMock()
        mock_ep_result.scalars.return_value.all.return_value = [ep1, ep2]

        call_count = 0

        async def _side_effect(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_delivery_result
            return mock_ep_result

        mock_db.execute = AsyncMock(side_effect=_side_effect)

        with patch.object(svc, "_attempt_delivery", new_callable=AsyncMock):
            count = await svc.retry_pending(mock_db)

        assert count == 2
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_missing_endpoint_marks_failed(self, svc, mock_db):
        d = _make_delivery(endpoint_id="deleted-ep")

        mock_delivery_result = MagicMock()
        mock_delivery_result.scalars.return_value.all.return_value = [d]

        mock_ep_result = MagicMock()
        mock_ep_result.scalars.return_value.all.return_value = []  # no endpoints

        call_count = 0

        async def _side_effect(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_delivery_result
            return mock_ep_result

        mock_db.execute = AsyncMock(side_effect=_side_effect)

        count = await svc.retry_pending(mock_db)
        assert count == 0
        assert d.status == "failed"
        assert "deleted or inactive" in d.error

    @pytest.mark.asyncio
    async def test_inactive_endpoint_marks_failed(self, svc, mock_db):
        d = _make_delivery(endpoint_id="ep-1")
        ep = _make_endpoint(id="ep-1", is_active=False)

        mock_delivery_result = MagicMock()
        mock_delivery_result.scalars.return_value.all.return_value = [d]

        mock_ep_result = MagicMock()
        mock_ep_result.scalars.return_value.all.return_value = [ep]

        call_count = 0

        async def _side_effect(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_delivery_result
            return mock_ep_result

        mock_db.execute = AsyncMock(side_effect=_side_effect)

        count = await svc.retry_pending(mock_db)
        assert count == 0
        assert d.status == "failed"


# ---------------------------------------------------------------------------
# update_last_used (direct UPDATE)
# ---------------------------------------------------------------------------

class TestUpdateLastUsed:
    @pytest.mark.asyncio
    async def test_executes_update_statement(self):
        from src.services.api_key_service import APIKeyService

        svc = APIKeyService()
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()

        await svc.update_last_used(mock_db, "key-123", "10.0.0.1")
        # Should call execute with an UPDATE statement + commit
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_db_error_gracefully(self):
        from src.services.api_key_service import APIKeyService

        svc = APIKeyService()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=RuntimeError("DB error"))
        mock_db.rollback = AsyncMock()

        # Should not raise
        await svc.update_last_used(mock_db, "key-123", "10.0.0.1")
        mock_db.rollback.assert_called_once()
