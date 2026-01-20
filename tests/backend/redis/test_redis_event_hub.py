"""
Unit tests for RedisEventHub (Streams implementation).

Tests cover:
- Publish events to Redis Streams (XADD)
- Subscribe to streams via async generator (XREAD)
- Stream persistence (events available after publish)
- Late subscriber support (read from beginning)
- Stream management (trim, delete, info)
- Statistics tracking
- Connection pooling
- Error handling

Key architectural difference from Pub/Sub:
- Events persist in the stream until TTL expires or manually deleted
- Consumers can start from any point (beginning, specific ID, or sequence)
- No race conditions - late subscribers see all events
"""
import asyncio

import pytest

from src.services.redis_event_hub import RedisEventHub


@pytest.mark.redis
class TestRedisStreamsBasics:
    """Basic Redis Streams functionality tests."""

    @pytest.mark.asyncio
    async def test_get_stream_key(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Stream keys follow session:{id}:events pattern."""
        stream_key = redis_event_hub._get_stream_key(test_session_id)
        assert stream_key == f"session:{test_session_id}:events"

    @pytest.mark.asyncio
    async def test_publish_returns_entry_id(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Publish returns the Redis Stream entry ID."""
        event = {
            "type": "test_event",
            "data": {"message": "hello"},
            "sequence": 1,
            "session_id": test_session_id,
        }

        entry_id = await redis_event_hub.publish(test_session_id, event)

        # Entry ID format: timestamp-sequence (e.g., "1234567890123-0")
        assert entry_id is not None
        assert "-" in entry_id

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_publish_persists_event_in_stream(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Published events persist in the stream."""
        event = {
            "type": "test_event",
            "data": {"message": "persistent"},
            "sequence": 1,
            "session_id": test_session_id,
        }

        await redis_event_hub.publish(test_session_id, event)

        # Event should be in stream
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 1

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_stream_info_empty_stream(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """get_stream_info handles non-existent streams gracefully."""
        info = await redis_event_hub.get_stream_info(test_session_id)

        assert info["length"] == 0
        assert info["first_entry"] is None
        assert info["last_entry"] is None


@pytest.mark.redis
class TestRedisStreamsPublish:
    """Redis Streams publish functionality tests."""

    @pytest.mark.asyncio
    async def test_publish_multiple_events(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Multiple events are stored in order."""
        # Publish 5 events
        for i in range(5):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Stream should have 5 events
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 5

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_publish_with_non_serializable_object(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Publish handles non-serializable objects gracefully (uses default=str)."""
        event = {
            "type": "test_event",
            "data": {"obj": object()},  # Can't serialize object()
            "sequence": 1,
            "session_id": test_session_id,
        }

        # Should not raise (uses default=str)
        try:
            await redis_event_hub.publish(test_session_id, event)
        except Exception as e:
            pytest.fail(f"Publish raised exception: {e}")

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestRedisStreamsSubscribe:
    """Redis Streams subscribe functionality tests."""

    @pytest.mark.asyncio
    async def test_subscribe_returns_async_generator(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Subscribe returns an async generator."""
        gen = redis_event_hub.subscribe(test_session_id)

        # Should be an async generator
        assert hasattr(gen, '__anext__')

        # Cleanup - stop the generator
        await redis_event_hub.stop_subscriber(test_session_id)

    @pytest.mark.asyncio
    async def test_subscribe_receives_published_event(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Subscriber receives events published to stream."""
        # Publish event first (streams persist!)
        event = {
            "type": "test_event",
            "data": {"message": "hello"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        await redis_event_hub.publish(test_session_id, event)

        # Subscribe and read
        received = None
        async for evt in redis_event_hub.subscribe(test_session_id):
            if evt.get("type") == "test_event":
                received = evt
                break

        assert received is not None
        assert received["type"] == "test_event"
        assert received["data"]["message"] == "hello"
        assert received["sequence"] == 1

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_subscribe_receives_multiple_events_in_order(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Subscriber receives events in order."""
        # Publish 5 events
        for i in range(5):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Subscribe and collect
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

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_late_subscriber_sees_all_events(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Late subscriber (after publish) still sees all events - key Streams advantage!"""
        # Publish events BEFORE subscribing
        for i in range(3):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Now subscribe (late subscriber)
        received_events = []
        async for evt in redis_event_hub.subscribe(test_session_id):
            if evt.get("type") == "test_event":
                received_events.append(evt)
                if len(received_events) >= 3:
                    break

        # Should have received ALL events (unlike Pub/Sub which would miss them)
        assert len(received_events) == 3
        for i, event in enumerate(received_events):
            assert event["data"]["index"] == i

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_subscribe_from_sequence(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Subscribe can start from a specific sequence number."""
        # Publish 10 events
        for i in range(10):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Subscribe starting from sequence 5 (should get events 6-10)
        received_events = []
        async for evt in redis_event_hub.subscribe(test_session_id, from_sequence=5):
            if evt.get("type") == "test_event":
                received_events.append(evt)
                if len(received_events) >= 5:
                    break

        # Should have events with sequence > 5
        assert len(received_events) == 5
        for event in received_events:
            assert event["sequence"] > 5

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_subscribe_heartbeat_on_timeout(
        self,
        redis_url: str,
        test_session_id: str
    ) -> None:
        """Subscriber receives heartbeat when no events within timeout."""
        # Create hub with very short timeout
        hub = RedisEventHub(
            redis_url=redis_url,
            stream_maxlen=1000,
            block_ms=500,  # 0.5 second timeout
        )

        try:
            # Subscribe to empty stream
            heartbeat_received = False
            async for evt in hub.subscribe(test_session_id):
                if evt.get("type") == "heartbeat":
                    heartbeat_received = True
                    break

            assert heartbeat_received

        finally:
            await hub.close()


@pytest.mark.redis
class TestRedisStreamsMultipleSubscribers:
    """Tests for multiple concurrent subscribers."""

    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_session(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Multiple subscribers can read the same stream independently."""
        # Publish events
        for i in range(5):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Create 3 concurrent subscribers
        async def read_stream(name: str) -> list[dict]:
            events = []
            async for evt in redis_event_hub.subscribe(test_session_id):
                if evt.get("type") == "test_event":
                    events.append(evt)
                    if len(events) >= 5:
                        break
            return events

        # Run all 3 subscribers concurrently
        results = await asyncio.gather(
            read_stream("sub1"),
            read_stream("sub2"),
            read_stream("sub3"),
        )

        # All 3 should have received all 5 events
        for events in results:
            assert len(events) == 5

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_subscriber_count_tracking(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Subscriber count is tracked correctly."""
        # Initially no subscribers
        count = await redis_event_hub.get_subscriber_count(test_session_id)
        assert count == 0

        # Start subscribers (run in background)
        stop_events = []
        tasks = []

        async def subscriber(stop_event: asyncio.Event):
            async for evt in redis_event_hub.subscribe(test_session_id):
                if stop_event.is_set():
                    break

        for _ in range(3):
            stop_event = asyncio.Event()
            stop_events.append(stop_event)
            task = asyncio.create_task(subscriber(stop_event))
            tasks.append(task)

        await asyncio.sleep(0.2)  # Let subscribers start

        # Should have 3 subscribers
        count = await redis_event_hub.get_subscriber_count(test_session_id)
        assert count == 3

        # Stop subscribers
        for stop_event in stop_events:
            stop_event.set()
        await redis_event_hub.stop_subscriber(test_session_id)

        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.redis
class TestRedisStreamsManagement:
    """Stream management (trim, delete) tests."""

    @pytest.mark.asyncio
    async def test_trim_stream(
        self,
        redis_url: str,
        test_session_id: str
    ) -> None:
        """Trim reduces stream to specified maxlen."""
        hub = RedisEventHub(
            redis_url=redis_url,
            stream_maxlen=10000,  # High limit initially
        )

        try:
            # Publish 20 events
            for i in range(20):
                event = {
                    "type": "test_event",
                    "data": {"index": i},
                    "sequence": i + 1,
                    "session_id": test_session_id,
                }
                await hub.publish(test_session_id, event)

            # Verify 20 events
            info = await hub.get_stream_info(test_session_id)
            assert info["length"] == 20

            # Trim to 10
            removed = await hub.trim_stream(test_session_id, maxlen=10)

            # Should have removed 10 events
            assert removed == 10

            # Verify 10 remaining
            info = await hub.get_stream_info(test_session_id)
            assert info["length"] == 10

        finally:
            await hub.delete_stream(test_session_id)
            await hub.close()

    @pytest.mark.asyncio
    async def test_delete_stream(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Delete removes entire stream."""
        # Publish event
        event = {
            "type": "test_event",
            "data": {"message": "to_delete"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        await redis_event_hub.publish(test_session_id, event)

        # Verify exists
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 1

        # Delete
        result = await redis_event_hub.delete_stream(test_session_id)
        assert result is True

        # Verify deleted
        info = await redis_event_hub.get_stream_info(test_session_id)
        assert info["length"] == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_stream(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Delete handles non-existent stream gracefully."""
        result = await redis_event_hub.delete_stream("nonexistent_session")
        assert result is False


@pytest.mark.redis
class TestRedisStreamsGetEventsAfter:
    """Tests for get_events_after helper method."""

    @pytest.mark.asyncio
    async def test_get_events_after_sequence(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """get_events_after returns events after specified sequence."""
        # Publish 10 events
        for i in range(10):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Get events after sequence 5
        events = await redis_event_hub.get_events_after(test_session_id, after_sequence=5)

        # Should have events 6-10
        assert len(events) == 5
        for event in events:
            assert event["sequence"] > 5

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_get_events_after_with_limit(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """get_events_after respects limit parameter."""
        # Publish 20 events
        for i in range(20):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Get max 5 events after sequence 5
        events = await redis_event_hub.get_events_after(
            test_session_id,
            after_sequence=5,
            limit=5
        )

        # Should have exactly 5 events
        assert len(events) == 5

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_get_events_after_empty_stream(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """get_events_after returns empty list for non-existent stream."""
        events = await redis_event_hub.get_events_after(
            "nonexistent_session",
            after_sequence=0
        )
        assert events == []


@pytest.mark.redis
class TestRedisStreamsStats:
    """Redis Streams statistics tracking tests."""

    @pytest.mark.asyncio
    async def test_get_subscriber_stats(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """get_subscriber_stats returns stats for active subscribers."""
        # Publish some events
        for i in range(3):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Start subscriber in background - keep it active to check stats
        events_received = []
        stop_event = asyncio.Event()

        async def subscriber():
            async for evt in redis_event_hub.subscribe(test_session_id):
                events_received.append(evt)
                if stop_event.is_set():
                    break
                # Don't break early - keep subscriber active for stats check

        task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.3)  # Let subscriber start and read some events

        # Get stats while subscriber is still active
        stats_list = await redis_event_hub.get_subscriber_stats(test_session_id)

        assert len(stats_list) >= 1
        for stats in stats_list:
            assert "stream_id" in stats
            assert "events_received" in stats
            assert "last_sequence" in stats
            assert "created_at" in stats

        # Cleanup
        stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestRedisStreamsErrorHandling:
    """Redis Streams error handling tests."""

    @pytest.mark.asyncio
    async def test_publish_with_invalid_redis_continues(
        self,
        test_session_id: str,
    ) -> None:
        """Publish to invalid Redis raises but doesn't crash."""
        invalid_hub = RedisEventHub(
            redis_url="redis://invalid-host:9999/0",
            stream_maxlen=1000,
        )

        event = {
            "type": "test_event",
            "data": {"message": "should_fail"},
            "sequence": 1,
            "session_id": test_session_id,
        }

        # Should raise (Redis not available)
        with pytest.raises(Exception):
            await invalid_hub.publish(test_session_id, event)

        await invalid_hub.close()

    @pytest.mark.asyncio
    async def test_close_stops_all_subscribers(
        self,
        redis_url: str,
        test_session_id: str,
    ) -> None:
        """Close stops all active subscribers."""
        hub = RedisEventHub(
            redis_url=redis_url,
            stream_maxlen=1000,
            block_ms=5000,
        )

        # Publish event to create stream
        await hub.publish(test_session_id, {"type": "test", "sequence": 1})

        # Start subscribers
        tasks = []
        for _ in range(3):
            async def subscriber():
                async for evt in hub.subscribe(test_session_id):
                    pass
            task = asyncio.create_task(subscriber())
            tasks.append(task)

        await asyncio.sleep(0.2)  # Let subscribers start

        # Close should stop all subscribers
        await hub.close()

        # Give tasks time to finish after receiving stop signal
        await asyncio.sleep(0.2)

        # All tasks should complete (cancelled)
        for task in tasks:
            assert task.done() or task.cancelled()


@pytest.mark.redis
class TestEventSinkQueue:
    """Tests for EventSinkQueue adapter."""

    @pytest.mark.asyncio
    async def test_event_sink_queue_put(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """EventSinkQueue.put() publishes to Redis Stream."""
        from src.services.redis_event_hub import EventSinkQueue

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

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_event_sink_queue_put_nowait(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """EventSinkQueue.put_nowait() publishes asynchronously."""
        from src.services.redis_event_hub import EventSinkQueue

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
