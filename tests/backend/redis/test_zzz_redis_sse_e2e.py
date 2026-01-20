"""
E2E tests for SSE streaming with Redis Streams.

Tests cover:
- SSE subscription and event streaming
- Late subscriber scenarios (no overlap buffer needed with Streams!)
- Deduplication by sequence number
- Terminal event handling
- Heartbeat mechanism
- Concurrent subscribers
- Reconnection scenarios

Key changes from Pub/Sub architecture:
- No overlap buffer needed - Streams persist events
- Late subscribers always see all events from the beginning
- Deduplication is simpler (just skip already-seen sequences)
- No race conditions between publish and subscribe
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from src.services.redis_event_hub import RedisEventHub, EventSinkQueue
from src.core.tracer import EventingTracer, NullTracer


@pytest.mark.redis
class TestSSEStreaming:
    """Basic SSE streaming tests with Redis Streams."""

    @pytest.mark.asyncio
    async def test_sse_receives_events_in_order(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """SSE subscription receives events in correct order."""
        # Publish events
        for i in range(10):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Subscribe and receive
        received_events = []
        async for event in redis_event_hub.subscribe(test_session_id):
            if event.get("type") == "test_event":
                received_events.append(event)
                if len(received_events) >= 10:
                    break

        # Verify order
        assert len(received_events) == 10
        for i, event in enumerate(received_events):
            assert event["data"]["index"] == i
            assert event["sequence"] == i + 1

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_sse_deduplication_by_sequence(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """SSE clients can deduplicate by sequence number."""
        # Publish 5 events
        for seq in range(1, 6):
            event = {
                "type": "test_event",
                "data": {"index": seq},
                "sequence": seq,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Simulate SSE client with deduplication logic
        seen_sequences = set()
        deduplicated_events = []

        async for event in redis_event_hub.subscribe(test_session_id):
            if event.get("type") == "test_event":
                seq = event["sequence"]

                # SSE deduplication logic
                if seq in seen_sequences:
                    continue  # Skip duplicate

                seen_sequences.add(seq)
                deduplicated_events.append(event)

                if len(deduplicated_events) >= 5:
                    break

        # Should have 5 unique events
        assert len(deduplicated_events) == 5
        sequences = [e["sequence"] for e in deduplicated_events]
        assert len(sequences) == len(set(sequences))  # No duplicates

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestSSELateSubscriber:
    """Tests for late subscriber scenarios - no overlap buffer needed with Streams!"""

    @pytest.mark.asyncio
    async def test_late_subscriber_sees_all_events(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Late subscriber sees ALL events - key advantage over Pub/Sub."""
        # Publish events BEFORE subscribing
        for i in range(10):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Subscribe AFTER publishing (late subscriber)
        received_events = []
        async for event in redis_event_hub.subscribe(test_session_id):
            if event.get("type") == "test_event":
                received_events.append(event)
                if len(received_events) >= 10:
                    break

        # Should have ALL 10 events (unlike Pub/Sub which would miss them all!)
        assert len(received_events) == 10
        for i, event in enumerate(received_events):
            assert event["data"]["index"] == i

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_subscribe_from_checkpoint(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """SSE can resume from a checkpoint (last seen sequence)."""
        # Publish 20 events
        for i in range(20):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Simulate reconnection: resume from sequence 10
        last_seen_sequence = 10
        received_events = []

        async for event in redis_event_hub.subscribe(
            test_session_id,
            from_sequence=last_seen_sequence
        ):
            if event.get("type") == "test_event":
                received_events.append(event)
                if len(received_events) >= 10:
                    break

        # Should have events 11-20 only
        assert len(received_events) == 10
        for event in received_events:
            assert event["sequence"] > last_seen_sequence

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestSSETerminalEvents:
    """Tests for terminal event handling in SSE."""

    @pytest.mark.asyncio
    async def test_terminal_events_end_stream(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Terminal events (agent_complete, error, cancelled) should end SSE stream."""
        terminal_types = ["agent_complete", "error", "cancelled"]

        for terminal_type in terminal_types:
            session_id = f"{test_session_id}_{terminal_type}"

            # Publish some events, then terminal event
            for i in range(3):
                event = {
                    "type": "test_event",
                    "data": {"index": i},
                    "sequence": i + 1,
                    "session_id": session_id,
                }
                await redis_event_hub.publish(session_id, event)

            # Publish terminal event
            terminal_event = {
                "type": terminal_type,
                "data": {"message": "session ended"},
                "sequence": 4,
                "session_id": session_id,
            }
            await redis_event_hub.publish(session_id, terminal_event)

            # Simulate SSE client logic
            received_events = []
            should_close = False

            async for event in redis_event_hub.subscribe(session_id):
                received_events.append(event)

                if event["type"] in ("agent_complete", "error", "cancelled"):
                    should_close = True
                    break

                if len(received_events) > 10:  # Safety limit
                    break

            # Should have received 3 events + terminal event
            assert len(received_events) == 4
            assert received_events[-1]["type"] == terminal_type
            assert should_close is True

            # Cleanup
            await redis_event_hub.delete_stream(session_id)

    @pytest.mark.asyncio
    async def test_terminal_event_always_delivered(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Terminal events must be delivered even if sequence was seen before."""
        # This tests the SSE logic that NEVER skips terminal events

        # Publish events with duplicate sequence (edge case)
        events_to_publish = [
            {"type": "test_event", "data": {"msg": "first"}, "sequence": 1},
            {"type": "test_event", "data": {"msg": "second"}, "sequence": 2},
            {"type": "agent_complete", "data": {"msg": "done"}, "sequence": 2},  # Same seq!
        ]

        for event in events_to_publish:
            event["session_id"] = test_session_id
            await redis_event_hub.publish(test_session_id, event)

        # SSE logic: skip duplicates BUT never skip terminal events
        seen_sequences = set()
        received_events = []

        async for event in redis_event_hub.subscribe(test_session_id):
            seq = event.get("sequence", 0)
            event_type = event.get("type")
            is_terminal = event_type in ("agent_complete", "error", "cancelled")

            # Deduplication logic (from sessions.py)
            if not is_terminal and seq in seen_sequences:
                continue

            seen_sequences.add(seq)
            received_events.append(event)

            if is_terminal:
                break

            if len(received_events) > 10:  # Safety
                break

        # Should have received all 3 events (terminal not skipped despite duplicate seq)
        assert len(received_events) == 3
        assert received_events[-1]["type"] == "agent_complete"

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestSSEHeartbeat:
    """Tests for SSE heartbeat mechanism."""

    @pytest.mark.asyncio
    async def test_heartbeat_on_empty_stream(
        self,
        redis_url: str,
        test_session_id: str
    ) -> None:
        """SSE receives heartbeat when stream is empty (timeout)."""
        # Create hub with short timeout
        hub = RedisEventHub(
            redis_url=redis_url,
            stream_maxlen=1000,
            block_ms=500,  # 0.5 second timeout
        )

        try:
            # Subscribe to empty stream
            heartbeat_received = False
            async for event in hub.subscribe(test_session_id):
                if event.get("type") == "heartbeat":
                    heartbeat_received = True
                    break

            assert heartbeat_received

        finally:
            await hub.close()

    @pytest.mark.asyncio
    async def test_heartbeat_contains_session_info(
        self,
        redis_url: str,
        test_session_id: str
    ) -> None:
        """Heartbeat contains session info for monitoring."""
        hub = RedisEventHub(
            redis_url=redis_url,
            stream_maxlen=1000,
            block_ms=500,
        )

        try:
            async for event in hub.subscribe(test_session_id):
                if event.get("type") == "heartbeat":
                    # Verify heartbeat structure
                    assert "data" in event
                    assert "session_id" in event["data"]
                    assert "server_time" in event["data"]
                    assert "stream_position" in event["data"]
                    break

        finally:
            await hub.close()


@pytest.mark.redis
class TestSSEConcurrentSubscribers:
    """Tests for multiple concurrent SSE connections."""

    @pytest.mark.asyncio
    async def test_multiple_sse_connections_same_session(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Multiple SSE connections to same session all receive all events."""
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
        async def read_stream() -> list[dict]:
            events = []
            async for event in redis_event_hub.subscribe(test_session_id):
                if event.get("type") == "test_event":
                    events.append(event)
                    if len(events) >= 5:
                        break
            return events

        # Run all 3 concurrently
        results = await asyncio.gather(
            read_stream(),
            read_stream(),
            read_stream(),
        )

        # All 3 should have received all 5 events
        for events in results:
            assert len(events) == 5
            for i, event in enumerate(events):
                assert event["sequence"] == i + 1

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_subscribers_at_different_positions(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Different subscribers can start from different positions."""
        # Publish 10 events
        for i in range(10):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Subscriber 1: from beginning
        async def read_from_start() -> list[dict]:
            events = []
            async for event in redis_event_hub.subscribe(test_session_id):
                if event.get("type") == "test_event":
                    events.append(event)
                    if len(events) >= 10:
                        break
            return events

        # Subscriber 2: from sequence 5
        async def read_from_seq_5() -> list[dict]:
            events = []
            async for event in redis_event_hub.subscribe(test_session_id, from_sequence=5):
                if event.get("type") == "test_event":
                    events.append(event)
                    if len(events) >= 5:
                        break
            return events

        results = await asyncio.gather(
            read_from_start(),
            read_from_seq_5(),
        )

        # Subscriber 1 should have all 10
        assert len(results[0]) == 10

        # Subscriber 2 should have 5 (sequences 6-10)
        assert len(results[1]) == 5
        for event in results[1]:
            assert event["sequence"] > 5

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestSSEReconnection:
    """Tests for SSE reconnection scenarios."""

    @pytest.mark.asyncio
    async def test_reconnect_resumes_from_last_sequence(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """SSE reconnection resumes from last seen sequence."""
        # Publish 10 events
        for i in range(10):
            event = {
                "type": "test_event",
                "data": {"index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # First connection: read 5 events
        last_sequence = 0
        async for event in redis_event_hub.subscribe(test_session_id):
            if event.get("type") == "test_event":
                last_sequence = event["sequence"]
                if last_sequence >= 5:
                    break

        # Simulate disconnect and reconnect
        # Reconnect from last seen sequence
        reconnect_events = []
        async for event in redis_event_hub.subscribe(
            test_session_id,
            from_sequence=last_sequence
        ):
            if event.get("type") == "test_event":
                reconnect_events.append(event)
                if len(reconnect_events) >= 5:
                    break

        # Should have received events 6-10
        assert len(reconnect_events) == 5
        assert reconnect_events[0]["sequence"] == 6
        assert reconnect_events[-1]["sequence"] == 10

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_events_published_during_disconnect_not_lost(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str
    ) -> None:
        """Events published during disconnect are not lost."""
        # First batch of events
        for i in range(5):
            event = {
                "type": "test_event",
                "data": {"batch": 1, "index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # First connection: read all
        first_events = []
        async for event in redis_event_hub.subscribe(test_session_id):
            if event.get("type") == "test_event":
                first_events.append(event)
                if len(first_events) >= 5:
                    break

        last_sequence = first_events[-1]["sequence"]

        # Simulate disconnect... events published while disconnected
        for i in range(5, 10):
            event = {
                "type": "test_event",
                "data": {"batch": 2, "index": i},
                "sequence": i + 1,
                "session_id": test_session_id,
            }
            await redis_event_hub.publish(test_session_id, event)

        # Reconnect - should get events from batch 2
        reconnect_events = []
        async for event in redis_event_hub.subscribe(
            test_session_id,
            from_sequence=last_sequence
        ):
            if event.get("type") == "test_event":
                reconnect_events.append(event)
                if len(reconnect_events) >= 5:
                    break

        # Should have batch 2 events
        assert len(reconnect_events) == 5
        for event in reconnect_events:
            assert event["data"]["batch"] == 2

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)


@pytest.mark.redis
class TestSSEWithTracer:
    """E2E tests combining EventingTracer with SSE streaming."""

    @pytest.mark.asyncio
    async def test_tracer_events_streamed_via_sse(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """Events emitted by tracer are streamed to SSE subscribers."""
        # Create tracer
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        # Emit events
        for i in range(5):
            tracer.emit_event("test_event", {"index": i}, persist_event=True)
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.5)  # Let events process

        # Subscribe and receive
        received_events = []
        async for event in redis_event_hub.subscribe(test_session_id):
            if event.get("type") == "test_event":
                received_events.append(event)
                if len(received_events) >= 5:
                    break

        # Should have received all events
        assert len(received_events) == 5
        for i, event in enumerate(received_events):
            assert event["data"]["index"] == i

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)

    @pytest.mark.asyncio
    async def test_tool_start_complete_events_streamed(
        self,
        redis_event_hub: RedisEventHub,
        test_session_id: str,
        mock_event_sink: AsyncMock
    ) -> None:
        """tool_start and tool_complete events are properly streamed."""
        # Create tracer
        event_queue = EventSinkQueue(redis_event_hub, test_session_id)
        tracer = EventingTracer(
            NullTracer(),
            event_queue=event_queue,
            event_sink=mock_event_sink,
            session_id=test_session_id,
        )

        # Emit tool events
        tracer.on_tool_start(
            tool_name="mcp__ag3ntum__Bash",
            tool_input={"command": "echo hello"},
            tool_id="tool_123"
        )
        await asyncio.sleep(0.1)

        tracer.on_tool_complete(
            tool_name="mcp__ag3ntum__Bash",
            tool_id="tool_123",
            result="hello",
            duration_ms=100,
            is_error=False,
        )
        await asyncio.sleep(0.1)

        # Subscribe and receive
        tool_events = []
        async for event in redis_event_hub.subscribe(test_session_id):
            if event.get("type") in ("tool_start", "tool_complete"):
                tool_events.append(event)
                if len(tool_events) >= 2:
                    break
            if len(tool_events) >= 2:
                break

        # Should have tool_start and tool_complete
        assert len(tool_events) == 2

        tool_start = next((e for e in tool_events if e["type"] == "tool_start"), None)
        tool_complete = next((e for e in tool_events if e["type"] == "tool_complete"), None)

        assert tool_start is not None
        assert tool_start["data"]["tool_name"] == "mcp__ag3ntum__Bash"
        assert tool_start["data"]["tool_id"] == "tool_123"

        assert tool_complete is not None
        assert tool_complete["data"]["tool_name"] == "mcp__ag3ntum__Bash"
        assert tool_complete["data"]["tool_id"] == "tool_123"

        # Cleanup
        await redis_event_hub.delete_stream(test_session_id)
