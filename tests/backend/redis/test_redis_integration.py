"""
Integration tests for Redis Streams + SQLite persistence.

Tests cover:
- Publish to Redis Stream first, then persist to SQLite
- Event replay from stream
- EventingTracer with RedisEventHub + SQLite
- Publish-then-persist order (Redis Streams first)
- Event sink integration
- Late subscriber scenarios (key advantage of Streams over Pub/Sub)

Key architectural differences from Pub/Sub:
- Events persist in Redis Stream - no race conditions
- Late subscribers can read all events from the beginning
- SQLite becomes backup/long-term storage, not primary delivery
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.tracer import EventingTracer, NullTracer
from src.services.redis_event_hub import RedisEventHub, EventSinkQueue


@pytest.mark.redis
class TestRedisStreamsSQLiteIntegration:
    """Tests for Redis Streams + SQLite integration."""

    @pytest.mark.asyncio
    async def test_publish_then_persist_order(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Events are published to Redis Stream before persisting to SQLite."""
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
        await asyncio.sleep(0.3)  # Let async task run

        # Event should be in Redis Stream
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 1

        # SQLite sink should also be called
        assert mock_event_sink.called
        assert mock_event_sink.call_count == 1

        # Verify the event passed to sink
        call_args = mock_event_sink.call_args[0][0]
        assert call_args["type"] == "test_event"
        assert call_args["data"]["message"] == "hello"

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_partial_events_not_persisted_to_sqlite(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Partial events are published to Redis Stream but not persisted to SQLite."""
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
        await asyncio.sleep(0.3)  # Let async task run

        # Event should be in Redis Stream (streams always persist!)
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 1

        # SQLite sink should NOT be called
        assert not mock_event_sink.called

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_multiple_events_sequence(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Multiple events published and persisted in order."""
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

        # Redis Stream should have all 5 events
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 5

        # Read events from stream to verify order
        received_events = []
        async for evt in redis_event_hub.subscribe(test_session_id):
            if evt.get("type") == "test_event":
                received_events.append(evt)
                if len(received_events) >= 5:
                    break

        assert len(received_events) == 5
        for i, event in enumerate(received_events):
            assert event["data"]["index"] == i
            assert event["sequence"] == i + 1

        # SQLite sink should have been called 5 times
        assert mock_event_sink.call_count == 5

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_redis_failure_falls_back_to_sqlite(
        self,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """If Redis publish fails, event still persists to SQLite."""
        # Create hub with invalid Redis URL (will fail)
        invalid_hub = RedisEventHub(
            redis_url="redis://invalid-host:9999/0",
            stream_maxlen=1000
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
class TestEventSinkQueueIntegration:
    """Tests for EventSinkQueue adapter integration."""

    @pytest.mark.asyncio
    async def test_event_sink_queue_put(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """EventSinkQueue.put() publishes to Redis Stream."""
        event_sink_queue = EventSinkQueue(redis_event_hub, test_session_id)

        event = {
            "type": "test_event",
            "data": {"message": "via_sink_queue"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        await event_sink_queue.put(event)

        # Event should be in stream
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 1

        # Read it back
        async for evt in redis_event_hub.subscribe(test_session_id):
            if evt.get("type") == "test_event":
                assert evt["data"]["message"] == "via_sink_queue"
                break

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_event_sink_queue_put_nowait(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """EventSinkQueue.put_nowait() publishes to Redis Stream asynchronously."""
        event_sink_queue = EventSinkQueue(redis_event_hub, test_session_id)

        event = {
            "type": "test_event",
            "data": {"message": "nowait"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        event_sink_queue.put_nowait(event)  # Fire and forget

        await asyncio.sleep(0.2)  # Give time for async task

        # Event should be in stream
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 1

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestLateSubscriberScenarios:
    """Tests for late subscriber scenarios - key advantage of Streams over Pub/Sub."""

    @pytest.mark.asyncio
    async def test_late_subscriber_sees_all_events(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Late subscriber (subscribes AFTER events published) still sees all events."""
        # Create tracer and publish 5 events BEFORE any subscriber
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        for i in range(5):
            tracer.emit_event("test_event", {"index": i}, persist_event=True)
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.5)  # Let all events process

        # Verify events are in stream
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 5

        # Now subscribe (late subscriber)
        received_events = []
        async for evt in redis_event_hub.subscribe(test_session_id):
            if evt.get("type") == "test_event":
                received_events.append(evt)
                if len(received_events) >= 5:
                    break

        # Should have received ALL 5 events (this would fail with Pub/Sub!)
        assert len(received_events) == 5
        for i, event in enumerate(received_events):
            assert event["data"]["index"] == i

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_subscriber_starts_from_specific_sequence(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Subscriber can start reading from a specific sequence (resume scenario)."""
        # Publish 10 events
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        for i in range(10):
            tracer.emit_event("test_event", {"index": i}, persist_event=True)
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.5)

        # Subscribe starting from sequence 5 (resume from checkpoint)
        received_events = []
        async for evt in redis_event_hub.subscribe(test_session_id, from_sequence=5):
            if evt.get("type") == "test_event":
                received_events.append(evt)
                if len(received_events) >= 5:
                    break

        # Should have events 6-10 only
        assert len(received_events) == 5
        for event in received_events:
            assert event["sequence"] > 5

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_multiple_late_subscribers_all_see_events(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Multiple late subscribers all see the same events independently."""
        # Publish events BEFORE any subscriber
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        for i in range(5):
            tracer.emit_event("test_event", {"index": i}, persist_event=True)
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.5)

        # Create 3 late subscribers
        async def read_events() -> list[dict]:
            events = []
            async for evt in redis_event_hub.subscribe(test_session_id):
                if evt.get("type") == "test_event":
                    events.append(evt)
                    if len(events) >= 5:
                        break
            return events

        # Run all 3 concurrently
        results = await asyncio.gather(
            read_events(),
            read_events(),
            read_events(),
        )

        # All 3 should have received all 5 events
        for events in results:
            assert len(events) == 5
            for i, event in enumerate(events):
                assert event["data"]["index"] == i

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestStreamDurability:
    """Tests for stream durability and persistence."""

    @pytest.mark.asyncio
    async def test_events_persist_across_reconnect(
        self,
        redis_url: str,
        test_session_id: str,
    ) -> None:
        """Events persist in stream even after hub is closed and recreated."""
        # Create hub and publish events
        hub1 = RedisEventHub(redis_url=redis_url, stream_maxlen=1000)

        for i in range(3):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await hub1.publish(test_session_id, event)

        await hub1.close()

        # Create new hub and read events
        hub2 = RedisEventHub(redis_url=redis_url, stream_maxlen=1000)

        try:
            # Events should still be there
            info = await hub2.get_stream_info(test_session_id)
            assert info["length"] == 3

            # Can read all events
            received_events = []
            async for evt in hub2.subscribe(test_session_id):
                if evt.get("type") == "test_event":
                    received_events.append(evt)
                    if len(received_events) >= 3:
                        break

            assert len(received_events) == 3

        finally:
            await hub2.delete_stream(test_session_id)
            await hub2.close()

    @pytest.mark.asyncio
    async def test_get_events_after_for_replay(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
    ) -> None:
        """get_events_after can be used for efficient replay without subscription."""
        # Publish 10 events
        for i in range(10):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Use get_events_after for bulk replay (more efficient than subscribing)
        events = await redis_event_hub.get_events_after(
            test_session_id,
            after_sequence=0,  # Get all
            limit=100
        )

        assert len(events) == 10
        for i, event in enumerate(events):
            assert event["data"]["index"] == i
            assert event["sequence"] == i + 1

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)
