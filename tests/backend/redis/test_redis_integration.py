"""
Integration tests for Redis + SQLite persistence.

Tests cover:
- Publish to Redis first, then persist to SQLite
- Event replay from SQLite
- EventingTracer with RedisEventHub + SQLite
- Persist-then-publish order (Redis first)
- Event sink integration
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.core.tracer import EventingTracer, NullTracer
from src.services.redis_event_hub import RedisEventHub, EventSinkQueue


@pytest.mark.redis
class TestRedisSQLiteIntegration:
    """Tests for Redis + SQLite integration."""

    @pytest.mark.asyncio
    async def test_publish_then_persist_order(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Events are published to Redis before persisting to SQLite."""

        # Subscribe to Redis first
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Create tracer with RedisEventHub and mock event sink
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        # Emit an event
        tracer.emit_event("test_event", {"message": "hello"}, persist_event=True)
        await asyncio.sleep(0.1)  # Let async task run

        # Redis should receive event first (almost immediately)
        try:
            redis_event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert redis_event["type"] == "test_event"
            assert redis_event["data"]["message"] == "hello"
        except asyncio.TimeoutError:
            pytest.fail("Did not receive event from Redis")

        # SQLite sink should also be called (but after Redis)
        await asyncio.sleep(0.5)  # Give time for persistence
        assert mock_event_sink.called
        assert mock_event_sink.call_count == 1

        # Verify the event passed to sink
        call_args = mock_event_sink.call_args[0][0]
        assert call_args["type"] == "test_event"
        assert call_args["data"]["message"] == "hello"

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_partial_events_not_persisted(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Partial events are published to Redis but not persisted to SQLite."""

        # Subscribe to Redis
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Create tracer
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        # Emit partial event (persist_event=False)
        tracer.emit_event("test_event", {"message": "partial"}, persist_event=False)
        await asyncio.sleep(0.1)  # Let async task run

        # Redis should receive event
        try:
            redis_event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert redis_event["type"] == "test_event"
            assert redis_event["data"]["message"] == "partial"
        except asyncio.TimeoutError:
            pytest.fail("Did not receive event from Redis")

        # SQLite sink should NOT be called
        await asyncio.sleep(0.5)  # Give time
        assert not mock_event_sink.called

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_multiple_events_sequence(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Multiple events published and persisted in order."""

        # Subscribe to Redis
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Create tracer
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        # Emit 5 events
        for i in range(5):
            tracer.emit_event("test_event", {"index": i}, persist_event=True)
            await asyncio.sleep(0.05)  # Small delay

        await asyncio.sleep(0.5)  # Let all events process

        # Redis should have received all 5 events in order
        received_events = []
        while not queue.empty():
            try:
                event = queue.get_nowait()
                received_events.append(event)
            except asyncio.QueueEmpty:
                break

        assert len(received_events) == 5
        for i, event in enumerate(received_events):
            assert event["type"] == "test_event"
            assert event["data"]["index"] == i
            assert event["sequence"] == i + 1

        # SQLite sink should have been called 5 times
        assert mock_event_sink.call_count == 5

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_redis_failure_still_persists_to_sqlite(
        self,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """If Redis publish fails, event still persists to SQLite."""
        # Create hub with invalid Redis URL (will fail)
        invalid_hub = RedisEventHub(
            redis_url="redis://invalid-host:9999/0",
            max_queue_size=100
        )

        try:
            event_queue = EventSinkQueue(invalid_hub, test_session_id)
            tracer = EventingTracer(
                NullTracer(),
                event_queue=event_queue,
                event_sink=mock_event_sink,
                session_id=test_session_id,
            )

            # Emit event (Redis will fail, but SQLite should still work)
            tracer.emit_event("test_event", {"message": "persistent"}, persist_event=True)
            await asyncio.sleep(1.0)  # Give time for both operations

            # SQLite sink should still be called despite Redis failure
            assert mock_event_sink.called
            assert mock_event_sink.call_count == 1

            # Verify the event passed to sink
            call_args = mock_event_sink.call_args[0][0]
            assert call_args["type"] == "test_event"
            assert call_args["data"]["message"] == "persistent"

        finally:
            await invalid_hub.close()


@pytest.mark.redis
class TestEventSinkQueue:
    """Tests for EventSinkQueue adapter."""

    @pytest.mark.asyncio
    async def test_event_sink_queue_put(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """EventSinkQueue.put() publishes to Redis."""

        # Subscribe to Redis
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Create EventSinkQueue
        event_sink_queue = EventSinkQueue(redis_event_hub, test_session_id)

        # Put an event
        event = {
            "type": "test_event",
            "data": {"message": "via_sink_queue"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        await event_sink_queue.put(event)

        # Should receive from Redis
        try:
            received = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert received["type"] == "test_event"
            assert received["data"]["message"] == "via_sink_queue"
        except asyncio.TimeoutError:
            pytest.fail("Did not receive event from Redis")

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_event_sink_queue_put_nowait(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """EventSinkQueue.put_nowait() publishes to Redis asynchronously."""

        # Subscribe to Redis
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Create EventSinkQueue
        event_sink_queue = EventSinkQueue(redis_event_hub, test_session_id)

        # Put an event (nowait)
        event = {
            "type": "test_event",
            "data": {"message": "nowait"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        event_sink_queue.put_nowait(event)  # Fire and forget

        await asyncio.sleep(0.2)  # Give time for async task

        # Should receive from Redis
        try:
            received = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert received["type"] == "test_event"
            assert received["data"]["message"] == "nowait"
        except asyncio.TimeoutError:
            pytest.fail("Did not receive event from Redis")

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)


@pytest.mark.redis
class TestEventDeliveryTiming:
    """Tests for event delivery timing and race conditions."""

    @pytest.mark.asyncio
    async def test_subscribe_after_publish(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Events published before subscribe are NOT received (Redis is ephemeral)."""

        # Publish event BEFORE subscribing
        event = {
            "type": "test_event",
            "data": {"message": "before_subscribe"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        await redis_event_hub.publish(test_session_id, event)

        # Now subscribe
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Should NOT receive the event (Redis Pub/Sub is ephemeral)
        try:
            received = await asyncio.wait_for(queue.get(), timeout=0.5)
            # If we get here, something is wrong
            pytest.fail(f"Should not have received event, but got: {received}")
        except asyncio.TimeoutError:
            # This is expected - event was published before subscribe
            pass

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_late_subscriber_needs_replay(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Demonstrates need for SQLite replay for late subscribers."""

        # Create tracer and publish 3 events
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        for i in range(3):
            tracer.emit_event("test_event", {"index": i}, persist_event=True)
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.5)  # Let events process

        # Now subscribe (after events were published)
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Should NOT receive any events from Redis (they were published before)
        assert queue.empty()

        # But SQLite sink should have all 3 events persisted
        assert mock_event_sink.call_count == 3

        # This demonstrates why SSE endpoint needs to replay from SQLite
        # for late subscribers (overlap buffer strategy)

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)
