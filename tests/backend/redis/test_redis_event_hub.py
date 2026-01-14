"""
Unit tests for RedisEventHub.

Tests cover:
- Subscribe/unsubscribe operations
- Publish to Redis channels
- Background listener tasks
- Backpressure handling (queue full)
- Statistics tracking
- Connection pooling
- Error handling
"""
import asyncio

import pytest

from src.services.redis_event_hub import RedisEventHub


@pytest.mark.redis
class TestRedisEventHubBasics:
    """Basic RedisEventHub functionality tests."""

    @pytest.mark.asyncio
    async def test_subscribe_creates_queue(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Subscribe creates a local queue and background listener task."""

        queue = await redis_event_hub.subscribe(test_session_id)

        # Queue should be created
        assert queue is not None
        assert queue.maxsize == 100  # From fixture
        assert queue.empty()

        # Background task should be created
        assert test_session_id in redis_event_hub._subscribers
        assert queue in redis_event_hub._subscriber_tasks

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_unsubscribe_cancels_listener_task(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Unsubscribe cancels the background listener task."""

        queue = await redis_event_hub.subscribe(test_session_id)
        task = redis_event_hub._subscriber_tasks.get(queue)
        assert task is not None
        assert not task.done()

        # Unsubscribe
        await redis_event_hub.unsubscribe(test_session_id, queue)

        # Task should be cancelled
        assert task.done()
        assert task.cancelled() or task.exception() is not None

        # Queue should be removed
        assert queue not in redis_event_hub._subscriber_tasks
        assert queue not in redis_event_hub._subscriber_stats

    @pytest.mark.asyncio
    async def test_get_channel_name(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Channel names follow session:{id}:events pattern."""

        channel = redis_event_hub._get_channel_name(test_session_id)
        assert channel == f"session:{test_session_id}:events"

    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_session(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Multiple subscribers can subscribe to same session."""

        queue1 = await redis_event_hub.subscribe(test_session_id)
        queue2 = await redis_event_hub.subscribe(test_session_id)

        # Both queues should exist
        assert test_session_id in redis_event_hub._subscribers
        assert len(redis_event_hub._subscribers[test_session_id]) == 2
        assert queue1 in redis_event_hub._subscribers[test_session_id]
        assert queue2 in redis_event_hub._subscribers[test_session_id]

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue1)
        await redis_event_hub.unsubscribe(test_session_id, queue2)


@pytest.mark.redis
class TestRedisEventHubPublish:
    """RedisEventHub publish functionality tests."""

    @pytest.mark.asyncio
    async def test_publish_event(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Publish event to Redis channel."""

        event = {
            "type": "test_event",
            "data": {"message": "hello"},
            "sequence": 1,
            "session_id": test_session_id,
        }

        # Subscribe first
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Publish event
        await redis_event_hub.publish(test_session_id, event)

        # Should receive event
        try:
            received = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert received["type"] == "test_event"
            assert received["data"]["message"] == "hello"
            assert received["sequence"] == 1
        except asyncio.TimeoutError:
            pytest.fail("Did not receive event from Redis")

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_publish_multiple_events(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Publish multiple events in order."""

        # Subscribe first
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Publish 5 events
        for i in range(5):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Receive all 5 events
        received_events = []
        for _ in range(5):
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
                received_events.append(event)
            except asyncio.TimeoutError:
                break

        assert len(received_events) == 5
        for i, event in enumerate(received_events):
            assert event["data"]["index"] == i
            assert event["sequence"] == i + 1

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Publish event to multiple subscribers (fanout)."""

        # Subscribe with 3 different queues
        queue1 = await redis_event_hub.subscribe(test_session_id)
        queue2 = await redis_event_hub.subscribe(test_session_id)
        queue3 = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listeners start

        # Publish one event
        event = {
            "type": "fanout_test",
            "data": {"message": "broadcast"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        await redis_event_hub.publish(test_session_id, event)

        # All 3 subscribers should receive it
        for queue in [queue1, queue2, queue3]:
            try:
                received = await asyncio.wait_for(queue.get(), timeout=2.0)
                assert received["type"] == "fanout_test"
                assert received["data"]["message"] == "broadcast"
            except asyncio.TimeoutError:
                pytest.fail(f"Queue {id(queue)} did not receive event")

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue1)
        await redis_event_hub.unsubscribe(test_session_id, queue2)
        await redis_event_hub.unsubscribe(test_session_id, queue3)


@pytest.mark.redis
class TestRedisEventHubBackpressure:
    """RedisEventHub backpressure handling tests."""

    @pytest.mark.asyncio
    async def test_backpressure_drops_oldest_event(
        self,
        redis_url: str,
        test_session_id: str,
    ) -> None:
        """When queue is full, oldest event is dropped."""

        # Create hub with small queue (5 events)
        hub = RedisEventHub(redis_url=redis_url, max_queue_size=5)
        try:
            queue = await hub.subscribe(test_session_id)
            await asyncio.sleep(0.1)  # Let listener start

            # Publish 10 events (queue can hold 5)
            for i in range(10):
                event = {
                    "type": "test_event",
                    "data": {"index": i},
                    "sequence": i + 1,
                    "session_id": test_session_id,
                }
                await hub.publish(test_session_id, event)
                await asyncio.sleep(0.01)  # Small delay

            # Give listener time to process
            await asyncio.sleep(0.5)

            # Queue should have ~5 events (oldest dropped)
            events_in_queue = []
            while not queue.empty():
                try:
                    event = queue.get_nowait()
                    events_in_queue.append(event)
                except asyncio.QueueEmpty:
                    break

            # Should have received the last 5 events (0-4 dropped, 5-9 kept)
            assert len(events_in_queue) <= 5
            if len(events_in_queue) > 0:
                # First event in queue should be from the end (oldest dropped)
                first_event = events_in_queue[0]
                assert first_event["data"]["index"] >= 5

        finally:
            await hub.close()

    @pytest.mark.asyncio
    async def test_backpressure_stats_tracking(
        self,
        redis_url: str,
        test_session_id: str,
    ) -> None:
        """Backpressure updates dropped event stats."""

        # Create hub with small queue (5 events)
        hub = RedisEventHub(redis_url=redis_url, max_queue_size=5)
        try:
            queue = await hub.subscribe(test_session_id)
            await asyncio.sleep(0.1)  # Let listener start

            # Get initial stats
            stats_before = hub._subscriber_stats.get(queue)
            assert stats_before is not None
            initial_dropped = stats_before.events_dropped

            # Publish 20 events (queue can hold 5)
            for i in range(20):
                event = {
                    "type": "test_event",
                    "data": {"index": i},
                    "sequence": i + 1,
                    "session_id": test_session_id,
                }
                await hub.publish(test_session_id, event)
                await asyncio.sleep(0.01)  # Small delay

            # Give listener time to process
            await asyncio.sleep(0.5)

            # Check stats - should have dropped events
            stats_after = hub._subscriber_stats.get(queue)
            assert stats_after is not None
            # Should have dropped at least 15 events (20 published - 5 capacity)
            assert stats_after.events_dropped > initial_dropped

        finally:
            await hub.close()


@pytest.mark.redis
class TestRedisEventHubStats:
    """RedisEventHub statistics tracking tests."""

    @pytest.mark.asyncio
    async def test_get_subscriber_count(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """get_subscriber_count returns correct count."""

        # No subscribers initially
        count = await redis_event_hub.get_subscriber_count(test_session_id)
        assert count == 0

        # Add 2 subscribers
        queue1 = await redis_event_hub.subscribe(test_session_id)
        queue2 = await redis_event_hub.subscribe(test_session_id)

        count = await redis_event_hub.get_subscriber_count(test_session_id)
        assert count == 2

        # Remove 1 subscriber
        await redis_event_hub.unsubscribe(test_session_id, queue1)

        count = await redis_event_hub.get_subscriber_count(test_session_id)
        assert count == 1

        # Remove last subscriber
        await redis_event_hub.unsubscribe(test_session_id, queue2)

        count = await redis_event_hub.get_subscriber_count(test_session_id)
        assert count == 0

    @pytest.mark.asyncio
    async def test_get_subscriber_stats(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """get_subscriber_stats returns stats for all subscribers."""

        # Add 2 subscribers
        queue1 = await redis_event_hub.subscribe(test_session_id)
        queue2 = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listeners start

        # Publish some events
        for i in range(3):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        await asyncio.sleep(0.5)  # Let events be received

        # Get stats
        stats_list = await redis_event_hub.get_subscriber_stats(test_session_id)

        assert len(stats_list) == 2
        for stats in stats_list:
            # Each subscriber should have received 3 events
            assert stats["events_received"] >= 3
            assert "events_dropped" in stats
            assert "last_sequence_sent" in stats
            assert "queue_size" in stats
            assert "queue_full" in stats

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue1)
        await redis_event_hub.unsubscribe(test_session_id, queue2)


@pytest.mark.redis
class TestRedisEventHubErrorHandling:
    """RedisEventHub error handling tests."""

    @pytest.mark.asyncio
    async def test_publish_with_invalid_json(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Publish handles non-serializable objects gracefully."""

        # Create event with non-serializable object
        event = {
            "type": "test_event",
            "data": {"obj": object()},  # Can't serialize object()
            "sequence": 1,
            "session_id": test_session_id,
        }

        # Publish should not raise (uses default=str)
        try:
            await redis_event_hub.publish(test_session_id, event)
        except Exception as e:
            pytest.fail(f"Publish raised exception: {e}")

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_session(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Unsubscribe handles nonexistent session gracefully."""

        # Create a fake queue
        fake_queue = asyncio.Queue()

        # Unsubscribe should not raise
        try:
            await redis_event_hub.unsubscribe("nonexistent_session", fake_queue)
        except Exception as e:
            pytest.fail(f"Unsubscribe raised exception: {e}")

    @pytest.mark.asyncio
    async def test_close_with_active_subscribers(
        self,
        redis_url: str,
        test_session_id: str,
    ) -> None:
        """Close cancels all active listener tasks."""

        hub = RedisEventHub(redis_url=redis_url, max_queue_size=100)
        try:
            # Add 3 subscribers
            queue1 = await hub.subscribe(test_session_id)
            queue2 = await hub.subscribe(test_session_id)
            queue3 = await hub.subscribe(test_session_id)

            # Verify tasks are running
            assert len(hub._subscriber_tasks) == 3
            for task in hub._subscriber_tasks.values():
                assert not task.done()

            # Close should cancel all tasks
            await hub.close()

            # All tasks should be done
            for task in hub._subscriber_tasks.values():
                assert task.done()

        except Exception:
            # Ensure cleanup even if test fails
            await hub.close()
            raise
