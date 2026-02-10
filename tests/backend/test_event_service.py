"""
Tests for event_service.py.

Tests the event persistence service:
- Event recording and validation
- Sequence number handling
- Event listing and ordering
- Terminal status detection
- Deduplication (IntegrityError handling)
- Retry logic (with_db_retry decorator)
- Sensitive data scanning
- Timeout handling
- Safe JSON parsing
"""
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from src.services.event_service import (
    record_event,
    _safe_json_loads,
    DB_OPERATION_TIMEOUT,
)
from src.db.retry import (
    with_db_retry,
    DEFAULT_MAX_RETRIES as MAX_RETRIES,
    DEFAULT_RETRY_DELAY_SECONDS as RETRY_DELAY_SECONDS,
)


class TestSafeJsonLoads:
    """Tests for _safe_json_loads function."""

    @pytest.mark.unit
    def test_valid_json(self):
        """Test parsing valid JSON."""
        result = _safe_json_loads('{"key": "value"}')
        assert result == {"key": "value"}

    @pytest.mark.unit
    def test_empty_string(self):
        """Test empty string returns empty dict."""
        result = _safe_json_loads("")
        assert result == {}

    @pytest.mark.unit
    def test_none_input(self):
        """Test None input returns empty dict."""
        result = _safe_json_loads(None)
        assert result == {}

    @pytest.mark.unit
    def test_invalid_json(self):
        """Test invalid JSON returns error dict."""
        result = _safe_json_loads("{invalid json}")
        assert "_parse_error" in result

    @pytest.mark.unit
    def test_complex_json(self):
        """Test parsing complex nested JSON."""
        data = json.dumps({
            "type": "message",
            "nested": {"key": "value"},
            "list": [1, 2, 3],
        })
        result = _safe_json_loads(data)
        assert result["type"] == "message"
        assert result["nested"]["key"] == "value"


class TestRecordEvent:
    """Tests for record_event function."""

    @pytest.mark.asyncio
    async def test_missing_session_id(self):
        """Test that events without session_id are skipped."""
        event = {"type": "message", "data": {"text": "hello"}}
        result = await record_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_session_id_in_top_level(self):
        """Test session_id at top level of event."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": 1,
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            result = await record_event(event)
            assert result is True
            mock_persist.assert_called_once()
            call_args = mock_persist.call_args
            assert call_args[0][0] == "test-session"

    @pytest.mark.asyncio
    async def test_session_id_in_data(self):
        """Test session_id in data sub-dict."""
        event = {
            "type": "message",
            "sequence": 1,
            "data": {"session_id": "nested-session", "text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock):
            result = await record_event(event)
            assert result is True

    @pytest.mark.asyncio
    async def test_negative_sequence_clamped_to_zero(self):
        """Test that negative sequence numbers are clamped to 0."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": -5,
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            result = await record_event(event)
            assert result is True
            call_args = mock_persist.call_args
            # Sequence should be clamped to 0
            assert call_args[0][1] == 0

    @pytest.mark.asyncio
    async def test_partial_message_skipped(self):
        """Test that partial messages are skipped without error."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": 1,
            "data": {"text": "partial", "is_partial": True},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            result = await record_event(event)
            assert result is True
            # _persist_event should NOT be called for partial messages
            mock_persist.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_text_replaces_text(self):
        """Test that full_text is used over text for message events."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": 1,
            "data": {
                "text": "partial text",
                "full_text": "complete text",
            },
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            result = await record_event(event)
            assert result is True
            call_args = mock_persist.call_args
            payload = call_args[0][3]  # 4th arg is payload
            assert payload["text"] == "complete text"
            assert "full_text" not in payload

    @pytest.mark.asyncio
    async def test_integrity_error_returns_false(self):
        """Test that IntegrityError (duplicate) returns False."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": 1,
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock,
                   side_effect=IntegrityError("duplicate", {}, None)):
            result = await record_event(event)
            assert result is False

    @pytest.mark.asyncio
    async def test_general_exception_returns_false(self):
        """Test that general exceptions return False."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": 1,
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock,
                   side_effect=RuntimeError("unexpected")):
            result = await record_event(event)
            assert result is False

    @pytest.mark.asyncio
    async def test_datetime_timestamp_preserved(self):
        """Test that datetime timestamp is preserved."""
        ts = datetime(2025, 6, 15, 12, 0, 0)
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": 1,
            "timestamp": ts,
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            await record_event(event)
            call_args = mock_persist.call_args
            assert call_args[0][4] == ts  # 5th arg is timestamp

    @pytest.mark.asyncio
    async def test_string_timestamp_parsed(self):
        """Test that ISO string timestamp is parsed."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": 1,
            "timestamp": "2025-06-15T12:00:00",
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            await record_event(event)
            call_args = mock_persist.call_args
            ts = call_args[0][4]
            assert isinstance(ts, datetime)
            assert ts.year == 2025

    @pytest.mark.asyncio
    async def test_invalid_timestamp_uses_now(self):
        """Test that invalid timestamp falls back to current time."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "sequence": 1,
            "timestamp": "not-a-date",
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            await record_event(event)
            call_args = mock_persist.call_args
            ts = call_args[0][4]
            assert isinstance(ts, datetime)

    @pytest.mark.asyncio
    async def test_agent_start_updates_resume_id(self):
        """Test that agent_start event updates resume_id."""
        event = {
            "session_id": "test-session",
            "type": "agent_start",
            "sequence": 0,
            "data": {"session_id": "claude-sdk-session-123"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock), \
             patch('src.services.event_service.session_service') as mock_ss:
            await record_event(event)
            mock_ss.update_resume_id.assert_called_once_with(
                "test-session", "claude-sdk-session-123"
            )

    @pytest.mark.asyncio
    async def test_missing_sequence_defaults_to_zero(self):
        """Test that missing sequence defaults to 0."""
        event = {
            "session_id": "test-session",
            "type": "message",
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            await record_event(event)
            call_args = mock_persist.call_args
            assert call_args[0][1] == 0  # sequence = 0

    @pytest.mark.asyncio
    async def test_missing_type_defaults_to_unknown(self):
        """Test that missing type defaults to 'unknown'."""
        event = {
            "session_id": "test-session",
            "sequence": 1,
            "data": {"text": "hello"},
        }

        with patch('src.services.event_service._persist_event', new_callable=AsyncMock) as mock_persist:
            await record_event(event)
            call_args = mock_persist.call_args
            assert call_args[0][2] == "unknown"  # event_type


class TestWithDbRetry:
    """Tests for with_db_retry decorator."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Test that successful calls don't retry."""
        call_count = 0

        @with_db_retry(max_retries=3, retry_delay=0.001)
        async def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await success_func()
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_operational_error(self):
        """Test retry on OperationalError."""
        call_count = 0

        @with_db_retry(max_retries=2, retry_delay=0.001)
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OperationalError("db locked", {}, None)
            return "recovered"

        result = await flaky_func()
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_integrity_error(self):
        """Test that IntegrityError is NOT retried."""
        call_count = 0

        @with_db_retry(max_retries=3, retry_delay=0.001)
        async def integrity_fail():
            nonlocal call_count
            call_count += 1
            raise IntegrityError("duplicate key", {}, None)

        with pytest.raises(IntegrityError):
            await integrity_fail()

        assert call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self):
        """Test that exhausted retries re-raise the error."""
        @with_db_retry(max_retries=2, retry_delay=0.001)
        async def always_fail():
            raise OperationalError("always fails", {}, None)

        with pytest.raises(OperationalError):
            await always_fail()

    @pytest.mark.asyncio
    async def test_backoff_multiplier(self):
        """Test exponential backoff timing."""
        import time
        timestamps = []

        @with_db_retry(max_retries=2, retry_delay=0.05, backoff_multiplier=2.0)
        async def timed_fail():
            timestamps.append(time.monotonic())
            raise OperationalError("fail", {}, None)

        with pytest.raises(OperationalError):
            await timed_fail()

        # Should have 3 calls (initial + 2 retries)
        assert len(timestamps) == 3
        # Second delay should be roughly 2x the first (backoff_multiplier=2.0)
        delay1 = timestamps[1] - timestamps[0]
        delay2 = timestamps[2] - timestamps[1]
        assert delay2 > delay1 * 1.2  # Generous tolerance for CI jitter


class TestConstants:
    """Tests for module constants."""

    @pytest.mark.unit
    def test_max_retries(self):
        """Test MAX_RETRIES constant."""
        assert MAX_RETRIES == 3

    @pytest.mark.unit
    def test_retry_delay(self):
        """Test RETRY_DELAY_SECONDS constant."""
        assert RETRY_DELAY_SECONDS == 0.1

    @pytest.mark.unit
    def test_db_timeout(self):
        """Test DB_OPERATION_TIMEOUT constant."""
        assert DB_OPERATION_TIMEOUT == 10.0
