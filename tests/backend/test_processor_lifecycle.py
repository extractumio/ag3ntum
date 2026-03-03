"""Unit tests for WebhookProcessor and RetentionProcessor lifecycle."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.services.webhook_processor import WebhookProcessor
from src.services.retention_processor import RetentionProcessor


# ---------------------------------------------------------------------------
# WebhookProcessor
# ---------------------------------------------------------------------------

class TestWebhookProcessorLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        proc = WebhookProcessor(interval_seconds=1)
        await proc.start()
        assert proc._running is True
        assert proc._task is not None
        await proc.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        proc = WebhookProcessor(interval_seconds=1)
        await proc.start()
        await proc.stop()
        assert proc._running is False
        assert proc._task is None

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        proc = WebhookProcessor(interval_seconds=1)
        await proc.start()
        first_task = proc._task
        await proc.start()  # should not create second task
        assert proc._task is first_task
        await proc.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_noop(self):
        proc = WebhookProcessor(interval_seconds=1)
        await proc.stop()  # should not raise
        assert proc._running is False

    @pytest.mark.asyncio
    async def test_loop_calls_tick(self):
        proc = WebhookProcessor(interval_seconds=0)  # instant loop

        with patch.object(proc, "_tick", new_callable=AsyncMock) as mock_tick:
            await proc.start()
            # Give loop time to iterate
            await asyncio.sleep(0.05)
            await proc.stop()
            assert mock_tick.call_count >= 1

    @pytest.mark.asyncio
    async def test_loop_recovers_from_error(self):
        proc = WebhookProcessor(interval_seconds=0)
        call_count = 0

        async def _failing_tick():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("DB error")

        _real_sleep = asyncio.sleep

        async def _fast_sleep(seconds):
            # Speed up error-recovery sleeps (>=1s) to be instant
            await _real_sleep(0 if seconds >= 1 else seconds)

        with patch.object(proc, "_tick", side_effect=_failing_tick), \
             patch("asyncio.sleep", side_effect=_fast_sleep):
            await proc.start()
            await _real_sleep(0.1)
            await proc.stop()
            # Should have been called at least twice (error + recovery)
            assert call_count >= 2

    @pytest.mark.asyncio
    async def test_tick_creates_db_session(self):
        proc = WebhookProcessor(interval_seconds=1)
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.db.database.AsyncSessionLocal",
            return_value=mock_db,
        ), patch(
            "src.services.webhook_service.webhook_service"
        ) as mock_svc:
            mock_svc.retry_pending = AsyncMock(return_value=0)
            await proc._tick()
            mock_svc.retry_pending.assert_called_once_with(mock_db)

    @pytest.mark.asyncio
    async def test_tick_handles_db_error(self):
        proc = WebhookProcessor(interval_seconds=1)
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.db.database.AsyncSessionLocal",
            return_value=mock_db,
        ):
            # Should not raise — error is caught and logged
            await proc._tick()


# ---------------------------------------------------------------------------
# RetentionProcessor
# ---------------------------------------------------------------------------

class TestRetentionProcessorLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        proc = RetentionProcessor(interval_seconds=1)
        await proc.start()
        assert proc._running is True
        assert proc._task is not None
        await proc.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        proc = RetentionProcessor(interval_seconds=1)
        await proc.start()
        await proc.stop()
        assert proc._running is False
        assert proc._task is None

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        proc = RetentionProcessor(interval_seconds=1)
        await proc.start()
        first_task = proc._task
        await proc.start()
        assert proc._task is first_task
        await proc.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_noop(self):
        proc = RetentionProcessor(interval_seconds=1)
        await proc.stop()
        assert proc._running is False

    @pytest.mark.asyncio
    async def test_default_interval_is_24h(self):
        proc = RetentionProcessor()
        assert proc._interval == 86400

    @pytest.mark.asyncio
    async def test_loop_calls_tick(self):
        proc = RetentionProcessor(interval_seconds=0)

        with patch.object(proc, "_tick", new_callable=AsyncMock) as mock_tick:
            await proc.start()
            await asyncio.sleep(0.05)
            await proc.stop()
            assert mock_tick.call_count >= 1

    @pytest.mark.asyncio
    async def test_loop_recovers_from_error(self):
        proc = RetentionProcessor(interval_seconds=0)
        call_count = 0

        async def _failing_tick():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("DB error")

        _real_sleep = asyncio.sleep

        async def _fast_sleep(seconds):
            await _real_sleep(0 if seconds >= 1 else seconds)

        with patch.object(proc, "_tick", side_effect=_failing_tick), \
             patch("asyncio.sleep", side_effect=_fast_sleep):
            await proc.start()
            await _real_sleep(0.15)
            await proc.stop()
            assert call_count >= 2

    @pytest.mark.asyncio
    async def test_tick_creates_db_session(self):
        proc = RetentionProcessor(interval_seconds=1)
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.db.database.AsyncSessionLocal",
            return_value=mock_db,
        ), patch(
            "src.services.data_retention_service.data_retention_service"
        ) as mock_svc:
            mock_svc.run_all = AsyncMock(return_value={"total_purged": 0})
            await proc._tick()
            mock_svc.run_all.assert_called_once_with(mock_db)

    @pytest.mark.asyncio
    async def test_tick_handles_db_error(self):
        proc = RetentionProcessor(interval_seconds=1)
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_db.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.db.database.AsyncSessionLocal",
            return_value=mock_db,
        ):
            await proc._tick()  # should not raise
