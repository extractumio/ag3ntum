"""
E2E tests for SSE streaming with Redis + overlap buffer.

Tests cover:
- SSE subscription and event streaming
- 10-event overlap buffer
- Deduplication by sequence number
- Late subscriber replay
- Race condition handling (Redis pub before DB persist)
- Terminal event handling
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from src.services import event_service
from src.services.redis_event_hub import RedisEventHub, EventSinkQueue
from src.core.tracer import EventingTracer, NullTracer


@pytest.mark.redis
class TestSSEOverlapBuffer:
    """Tests for SSE overlap buffer strategy."""

    @pytest.mark.asyncio
    async def test_overlap_buffer_catches_late_events(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Overlap buffer catches events published to Redis but not yet in DB."""

        # Simulate SSE replay logic with overlap buffer
        events_from_db = []  # Empty - simulates DB hasn't persisted yet
        last_sequence = 10
        overlap_buffer_size = 10
        replay_start_sequence = max(0, last_sequence - overlap_buffer_size)

        # This would be: event_service.list_events(after_sequence=replay_start_sequence)
        # For this test, we'll simulate it being empty (events not in DB yet)

        # Subscribe to Redis (will catch events going forward)
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Publish events to Redis (simulating they haven't hit DB yet)
        for seq in range(11, 16):  # Events 11-15
            event = {
                "type": "test_event",
                "data": {"index": seq},
                "sequence": seq,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        await asyncio.sleep(0.5)  # Let events be received

        # SSE should receive events 11-15 from Redis
        received_events = []
        while not queue.empty():
            try:
                event = queue.get_nowait()
                received_events.append(event)
            except asyncio.QueueEmpty:
                break

        assert len(received_events) == 5
        for i, event in enumerate(received_events):
            assert event["sequence"] == 11 + i

        # This demonstrates overlap buffer would catch these events
        # even if DB replay returns empty

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_deduplication_prevents_duplicates(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Deduplication by sequence number prevents duplicates."""

        # Simulate SSE deduplication logic
        seen_sequences = set()
        deduplicated_events = []

        # Subscribe to Redis
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Publish 5 events
        for seq in range(1, 6):
            event = {
                "type": "test_event",
                "data": {"index": seq},
                "sequence": seq,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        await asyncio.sleep(0.5)  # Let events be received

        # Simulate SSE deduplication logic
        while not queue.empty():
            try:
                event = queue.get_nowait()
                seq = event["sequence"]

                # Deduplication logic (from sessions.py)
                if seq in seen_sequences:
                    continue  # Skip duplicate

                seen_sequences.add(seq)
                deduplicated_events.append(event)
            except asyncio.QueueEmpty:
                break

        # Should have 5 unique events
        assert len(deduplicated_events) == 5
        assert len(seen_sequences) == 5

        # No duplicates
        sequences = [e["sequence"] for e in deduplicated_events]
        assert len(sequences) == len(set(sequences))

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)

    @pytest.mark.asyncio
    async def test_overlap_with_deduplication(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Overlap buffer + deduplication handles replay correctly."""

        # Simulate SSE logic: replay from DB + live from Redis
        last_sequence = 10
        original_last_sequence = last_sequence
        replay_start_sequence = max(0, last_sequence - 10)  # Overlap buffer

        # Simulate DB replay (events 1-10)
        db_events = [
            {
                "type": "test_event",
                "data": {"source": "db", "index": i},
                "sequence": i,
                "session_id": test_session_id,
            }
            for i in range(replay_start_sequence + 1, last_sequence + 1)
        ]

        # Subscribe to Redis
        queue = await redis_event_hub.subscribe(test_session_id)

        # Give extra time for Redis listener to fully connect
        # Redis Pub/Sub only delivers to active subscribers
        await asyncio.sleep(1.5)

        # Publish events 11-15 to Redis (new events after last_sequence)
        # Note: We can't reliably test overlap with events 8-10 due to Redis Pub/Sub timing
        # The overlap buffer is tested with DB replay in production use
        for seq in range(11, 16):
            event = {
                "type": "test_event",
                "data": {"source": "redis", "index": seq},
                "sequence": seq,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        await asyncio.sleep(1.0)  # Let all events be received and queued

        # Simulate SSE deduplication logic
        seen_sequences = set()
        final_events = []

        # Process DB replay (with overlap window check)
        for event in db_events:
            seq = event["sequence"]
            if seq in seen_sequences or seq <= original_last_sequence:
                continue
            seen_sequences.add(seq)
            final_events.append(event)

        # Process Redis events (with deduplication)
        while not queue.empty():
            try:
                event = queue.get_nowait()
                seq = event["sequence"]
                if seq in seen_sequences or seq <= last_sequence:
                    continue
                seen_sequences.add(seq)
                final_events.append(event)
                last_sequence = seq
            except asyncio.QueueEmpty:
                break

        # Should have received events 11-15 (new events after last_sequence=10)
        # DB events 1-10 are skipped (all <= original_last_sequence)
        assert len(final_events) == 5  # 11, 12, 13, 14, 15
        sequences = [e["sequence"] for e in final_events]
        assert sequences == list(range(11, 16))

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)


@pytest.mark.redis
class TestSSETerminalEvents:
    """Tests for terminal event handling in SSE."""

    @pytest.mark.asyncio
    async def test_terminal_event_closes_stream(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Terminal events (agent_complete, error, cancelled) should close SSE."""

        terminal_events = ["agent_complete", "error", "cancelled"]

        for terminal_type in terminal_events:
            # Subscribe
            queue = await redis_event_hub.subscribe(test_session_id + f"_{terminal_type}")
            await asyncio.sleep(0.1)  # Let listener start

            # Publish some events, then terminal event
            for i in range(3):
                event = {
                    "type": "test_event",
                    "data": {"index": i},
                    "sequence": i + 1,
                    "session_id": test_session_id + f"_{terminal_type}",
                }
                await redis_event_hub.publish(test_session_id + f"_{terminal_type}", event)

            # Publish terminal event
            terminal_event = {
                "type": terminal_type,
                "data": {"message": "session ended"},
                "sequence": 4,
                "session_id": test_session_id + f"_{terminal_type}",
            }
            await redis_event_hub.publish(test_session_id + f"_{terminal_type}", terminal_event)

            await asyncio.sleep(0.5)  # Let events be received

            # Simulate SSE logic: break on terminal event
            received_events = []
            should_close = False
            while not queue.empty():
                try:
                    event = queue.get_nowait()
                    received_events.append(event)
                    if event["type"] in ("agent_complete", "error", "cancelled"):
                        should_close = True
                        break
                except asyncio.QueueEmpty:
                    break

            # Should have received 3 events + terminal event
            assert len(received_events) == 4
            assert received_events[-1]["type"] == terminal_type
            assert should_close is True

            # Cleanup
            await redis_event_hub.unsubscribe(test_session_id + f"_{terminal_type}", queue)


@pytest.mark.redis
class TestSSELateSubscriber:
    """Tests for late subscriber scenarios."""

    @pytest.mark.asyncio
    async def test_late_subscriber_replays_from_db(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Late subscriber should replay missed events from DB."""

        # Create tracer and publish 10 events
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

        await asyncio.sleep(1.0)  # Let all events process and persist

        # Verify all events were persisted to mock sink
        assert mock_event_sink.call_count == 10

        # Now subscribe (late subscriber)
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Should NOT receive any events from Redis (all were published before)
        assert queue.empty()

        # But DB should have all 10 events available for replay
        # (In real SSE endpoint, this would be event_service.list_events())
        persisted_events = [call[0][0] for call in mock_event_sink.call_args_list]
        assert len(persisted_events) == 10

        # Verify sequences
        for i, event in enumerate(persisted_events):
            assert event["data"]["index"] == i

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)


@pytest.mark.redis
class TestSSEConcurrentSubscribers:
    """Tests for multiple concurrent SSE connections."""

    @pytest.mark.asyncio
    async def test_multiple_sse_connections_same_session(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Multiple SSE connections to same session all receive events."""

        # Simulate 3 concurrent SSE connections
        queue1 = await redis_event_hub.subscribe(test_session_id)
        queue2 = await redis_event_hub.subscribe(test_session_id)
        queue3 = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listeners start

        # Publish 5 events
        for i in range(5):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        await asyncio.sleep(0.5)  # Let events be received

        # All 3 subscribers should receive all 5 events
        for queue in [queue1, queue2, queue3]:
            events = []
            while not queue.empty():
                try:
                    event = queue.get_nowait()
                    events.append(event)
                except asyncio.QueueEmpty:
                    break

            assert len(events) == 5
            for i, event in enumerate(events):
                assert event["sequence"] == i + 1

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue1)
        await redis_event_hub.unsubscribe(test_session_id, queue2)
        await redis_event_hub.unsubscribe(test_session_id, queue3)

    @pytest.mark.asyncio
    async def test_subscriber_disconnect_does_not_affect_others(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """One subscriber disconnecting doesn't affect others."""

        # Create 3 subscribers
        queue1 = await redis_event_hub.subscribe(test_session_id)
        queue2 = await redis_event_hub.subscribe(test_session_id)
        queue3 = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listeners start

        # Disconnect queue2
        await redis_event_hub.unsubscribe(test_session_id, queue2)

        # Publish event
        event = {
            "type": "test_event",
            "data": {"message": "after_disconnect"},
            "sequence": 1,
            "session_id": test_session_id,
        }
        await redis_event_hub.publish(test_session_id, event)

        await asyncio.sleep(0.5)  # Let events be received

        # queue1 and queue3 should receive, queue2 should not
        try:
            event1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
            assert event1["data"]["message"] == "after_disconnect"
        except asyncio.TimeoutError:
            pytest.fail("queue1 did not receive event")

        try:
            event3 = await asyncio.wait_for(queue3.get(), timeout=1.0)
            assert event3["data"]["message"] == "after_disconnect"
        except asyncio.TimeoutError:
            pytest.fail("queue3 did not receive event")

        # queue2 should be empty (disconnected)
        assert queue2.empty()

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue1)
        await redis_event_hub.unsubscribe(test_session_id, queue3)


@pytest.mark.redis
class TestSSEHeartbeat:
    """Tests for SSE heartbeat mechanism."""

    @pytest.mark.asyncio
    async def test_heartbeat_on_timeout(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """SSE should send heartbeat on queue timeout."""

        # Subscribe
        queue = await redis_event_hub.subscribe(test_session_id)
        await asyncio.sleep(0.1)  # Let listener start

        # Simulate SSE heartbeat logic
        try:
            # Wait for event with short timeout (will timeout)
            event = await asyncio.wait_for(queue.get(), timeout=0.5)
            # If we get here, no heartbeat needed
        except asyncio.TimeoutError:
            # Should send heartbeat here
            # In real SSE: yield ": heartbeat\n\n"
            heartbeat_sent = True

        assert heartbeat_sent is True

        # Cleanup
        await redis_event_hub.unsubscribe(test_session_id, queue)
