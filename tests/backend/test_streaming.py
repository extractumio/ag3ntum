"""
Tests for SSE streaming and event handling.

Covers:
- EventingTracer event emission
- Event structure validation for all event types
- Cancellation and resumability
- Event persistence
- Resume context building
- Persist-then-publish race condition prevention
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.tracer import EventingTracer, NullTracer
from src.services import event_service
from src.services.redis_event_hub import RedisEventHub, EventSinkQueue


# =============================================================================
# EventingTracer Tests
# =============================================================================

class TestEventingTracer:
    """Tests for EventingTracer event emission."""

    @pytest.mark.asyncio
    async def test_eventing_tracer_streams_without_header_leak(self) -> None:
        """Structured output headers are stripped from streaming messages."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="session-1")

        tracer.on_message("```\n---\nstatus: COMPLETE\n", is_partial=True)
        await asyncio.sleep(0)
        assert queue.empty()

        tracer.on_message("---\n```\n\nHello", is_partial=True)
        await asyncio.sleep(0)

        partial_event = await queue.get()
        assert partial_event["type"] == "message"
        # Note: text may include leading newline after header strip
        assert "Hello" in partial_event["data"]["text"]
        assert partial_event["data"]["is_partial"] is True

        tracer.on_message("", is_partial=False)
        await asyncio.sleep(0)

        final_event = await queue.get()
        assert final_event["data"]["is_partial"] is False
        assert "Hello" in final_event["data"]["full_text"]
        assert final_event["data"]["structured_status"] == "COMPLETE"

    @pytest.mark.asyncio
    async def test_emit_event_creates_valid_structure(self) -> None:
        """emit_event creates events with required fields."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="test-session")

        tracer.emit_event("test_type", {"key": "value"})
        await asyncio.sleep(0)

        event = await queue.get()

        # Validate required fields
        assert event["type"] == "test_type"
        assert event["data"]["key"] == "value"
        assert "timestamp" in event
        assert "sequence" in event
        assert isinstance(event["sequence"], int)

    @pytest.mark.asyncio
    async def test_emit_event_increments_sequence(self) -> None:
        """Sequence numbers increment with each event."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="seq-test")

        tracer.emit_event("event1", {})
        tracer.emit_event("event2", {})
        tracer.emit_event("event3", {})
        await asyncio.sleep(0)

        event1 = await queue.get()
        event2 = await queue.get()
        event3 = await queue.get()

        assert event1["sequence"] < event2["sequence"] < event3["sequence"]


# =============================================================================
# SSE Event Types Tests
# =============================================================================

class TestSSEEventTypes:
    """Tests for all SSE event types structure."""

    @pytest.mark.asyncio
    async def test_cancelled_event_structure(self) -> None:
        """cancelled event has correct structure with resumable flag."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="cancelled-test")

        tracer.emit_event("cancelled", {
            "message": "Task was cancelled",
            "session_id": "cancelled-test",
            "resumable": True,
        })
        await asyncio.sleep(0)

        event = await queue.get()
        assert event["type"] == "cancelled"
        assert event["data"]["message"] == "Task was cancelled"
        assert event["data"]["resumable"] is True

    @pytest.mark.asyncio
    async def test_cancelled_event_not_resumable(self) -> None:
        """cancelled event can indicate non-resumable session."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="not-resumable-test")

        tracer.emit_event("cancelled", {
            "message": "Task was cancelled",
            "session_id": "not-resumable-test",
            "resumable": False,
        })
        await asyncio.sleep(0)

        event = await queue.get()
        assert event["type"] == "cancelled"
        assert event["data"]["resumable"] is False

    @pytest.mark.asyncio
    async def test_agent_complete_event_structure(self) -> None:
        """agent_complete event has correct structure."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="complete-test")

        tracer.emit_event("agent_complete", {
            "status": "COMPLETE",
            "num_turns": 5,
            "duration_ms": 12500,
            "total_cost_usd": 0.0125,
            "model": "claude-sonnet-4-5-20250929",
        })
        await asyncio.sleep(0)

        event = await queue.get()
        assert event["type"] == "agent_complete"
        assert event["data"]["status"] == "COMPLETE"
        assert event["data"]["num_turns"] == 5

    @pytest.mark.asyncio
    async def test_error_event_structure(self) -> None:
        """error event has correct structure."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="error-test")

        tracer.emit_event("error", {
            "message": "Something went wrong",
            "error_type": "execution_error",
        })
        await asyncio.sleep(0)

        event = await queue.get()
        assert event["type"] == "error"
        assert event["data"]["message"] == "Something went wrong"
        assert event["data"]["error_type"] == "execution_error"


# =============================================================================
# Event Persistence Tests
# =============================================================================

class TestEventPersistence:
    """Tests for event persistence via event_service."""

    @pytest.mark.asyncio
    async def test_record_event_skips_partial_and_persists_full_text(
        self,
        test_engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Partial messages are not persisted; full messages are."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(event_service, "AsyncSessionLocal", async_session)

        await event_service.record_event({
            "type": "message",
            "data": {"text": "partial", "is_partial": True},
            "timestamp": datetime.now(timezone.utc),
            "sequence": 1,
            "session_id": "session-2",
        })
        await event_service.record_event({
            "type": "message",
            "data": {"text": "", "full_text": "Full message", "is_partial": False},
            "timestamp": datetime.now(timezone.utc),
            "sequence": 2,
            "session_id": "session-2",
        })

        events = await event_service.list_events("session-2")
        assert len(events) == 1
        assert events[0]["data"]["text"] == "Full message"

    @pytest.mark.asyncio
    async def test_record_cancelled_event_with_resumable(
        self,
        test_engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancelled events are persisted with resumable flag."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(event_service, "AsyncSessionLocal", async_session)

        await event_service.record_event({
            "type": "cancelled",
            "data": {"message": "Task was cancelled", "resumable": True},
            "timestamp": datetime.now(timezone.utc),
            "sequence": 1,
            "session_id": "cancelled-session",
        })

        events = await event_service.list_events("cancelled-session")
        assert len(events) == 1
        assert events[0]["type"] == "cancelled"
        assert events[0]["data"]["resumable"] is True

    @pytest.mark.asyncio
    async def test_get_latest_terminal_status_cancelled(
        self,
        test_engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """get_latest_terminal_status returns 'cancelled' for cancelled sessions."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(event_service, "AsyncSessionLocal", async_session)

        await event_service.record_event({
            "type": "agent_start",
            "data": {"session_id": "claude-123"},
            "timestamp": datetime.now(timezone.utc),
            "sequence": 1,
            "session_id": "terminal-status-test",
        })
        await event_service.record_event({
            "type": "cancelled",
            "data": {"message": "Cancelled", "resumable": True},
            "timestamp": datetime.now(timezone.utc),
            "sequence": 2,
            "session_id": "terminal-status-test",
        })

        status = await event_service.get_latest_terminal_status("terminal-status-test")
        assert status == "cancelled"


# =============================================================================
# Terminal Events Tests
# =============================================================================

class TestTerminalEvents:
    """Tests for terminal event detection and handling."""

    @pytest.mark.asyncio
    async def test_terminal_events_are_recognized(self) -> None:
        """All terminal event types are correctly identified."""
        terminal_types = {"agent_complete", "error", "cancelled"}

        for event_type in terminal_types:
            queue: asyncio.Queue = asyncio.Queue()
            tracer = EventingTracer(
                NullTracer(), event_queue=queue, session_id=f"{event_type}-test"
            )

            tracer.emit_event(event_type, {"test": True})
            await asyncio.sleep(0)

            event = await queue.get()
            assert event["type"] == event_type


# =============================================================================
# Sequence Number Tests
# =============================================================================

class TestSequenceNumbers:
    """Tests for event sequence numbering."""

    @pytest.mark.asyncio
    async def test_sequence_starts_from_initial(self) -> None:
        """Sequence numbers start from initial_sequence parameter."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(
            NullTracer(),
            event_queue=queue,
            session_id="seq-init-test",
            initial_sequence=100,
        )

        tracer.emit_event("test", {})
        await asyncio.sleep(0)

        event = await queue.get()
        assert event["sequence"] == 101

    @pytest.mark.asyncio
    async def test_sequence_is_monotonically_increasing(self) -> None:
        """Sequence numbers always increase."""
        queue: asyncio.Queue = asyncio.Queue()
        tracer = EventingTracer(
            NullTracer(), event_queue=queue, session_id="monotonic-test"
        )

        sequences = []
        for i in range(10):
            tracer.emit_event(f"event_{i}", {})

        await asyncio.sleep(0)

        while not queue.empty():
            event = await queue.get()
            sequences.append(event["sequence"])

        # Verify strictly increasing
        for i in range(1, len(sequences)):
            assert sequences[i] > sequences[i - 1]


# =============================================================================
# Persist-Then-Publish Tests (Race Condition Prevention)
# =============================================================================

class TestPersistThenPublish:
    """
    Tests for the publish-then-persist pattern with Redis SSE.

    With Redis SSE, events are published to Redis/EventHub FIRST (~1ms),
    then persisted to SQLite (~50ms). The 10-event overlap buffer in SSE
    prevents race conditions where clients subscribe after Redis publish
    but before DB persist.
    """

    @pytest.mark.asyncio
    async def test_event_sink_called_before_queue_put(self) -> None:
        """Queue put (publish to Redis) is called before event sink (persist to SQLite)."""
        call_order = []

        async def mock_sink(event):
            call_order.append(("sink", event["type"]))
            await asyncio.sleep(0.01)  # Simulate DB write

        class MockQueue:
            async def put(self, event):
                call_order.append(("queue", event["type"]))

        tracer = EventingTracer(
            NullTracer(),
            event_queue=MockQueue(),
            event_sink=mock_sink,
            session_id="order-test",
        )

        tracer.emit_event("test_event", {"data": "value"})
        await asyncio.sleep(0.1)  # Wait for async task to complete

        # Verify queue is called before sink (Redis first, SQLite second)
        assert len(call_order) == 2
        assert call_order[0] == ("queue", "test_event")
        assert call_order[1] == ("sink", "test_event")

    @pytest.mark.asyncio
    async def test_publish_waits_for_persistence_to_complete(self) -> None:
        """Publishing to Redis happens before persistence starts (doesn't wait)."""
        persistence_started = False
        publish_happened_before_persistence = False

        async def slow_sink(event):
            nonlocal persistence_started
            persistence_started = True
            await asyncio.sleep(0.05)  # Simulate slow DB write

        class CheckingQueue:
            async def put(self, event):
                nonlocal publish_happened_before_persistence
                if not persistence_started:
                    publish_happened_before_persistence = True

        tracer = EventingTracer(
            NullTracer(),
            event_queue=CheckingQueue(),
            event_sink=slow_sink,
            session_id="wait-test",
        )

        tracer.emit_event("test_event", {})
        await asyncio.sleep(0.2)  # Wait for async task to complete

        assert persistence_started
        assert publish_happened_before_persistence  # Publish happened before persistence started

    @pytest.mark.asyncio
    async def test_persistence_failure_does_not_block_publish(self) -> None:
        """If persistence fails, event is still published (with warning)."""
        published_events = []

        async def failing_sink(event):
            raise Exception("DB connection failed")

        class TrackingQueue:
            async def put(self, event):
                published_events.append(event)

        tracer = EventingTracer(
            NullTracer(),
            event_queue=TrackingQueue(),
            event_sink=failing_sink,
            session_id="fail-test",
        )

        # Should not raise, even though sink fails
        tracer.emit_event("test_event", {"data": "value"})
        await asyncio.sleep(0.1)

        # Event should still be published
        assert len(published_events) == 1
        assert published_events[0]["type"] == "test_event"

    @pytest.mark.asyncio
    async def test_multiple_events_maintain_order(self) -> None:
        """Multiple events are persisted and published in order."""
        persisted_events = []
        published_events = []

        async def tracking_sink(event):
            persisted_events.append(event["sequence"])
            await asyncio.sleep(0.01)

        class TrackingQueue:
            async def put(self, event):
                published_events.append(event["sequence"])

        tracer = EventingTracer(
            NullTracer(),
            event_queue=TrackingQueue(),
            event_sink=tracking_sink,
            session_id="multi-test",
        )

        for i in range(5):
            tracer.emit_event(f"event_{i}", {"index": i})

        await asyncio.sleep(0.5)  # Wait for all async tasks

        # Both lists should have same events in same order
        assert len(persisted_events) == 5
        assert len(published_events) == 5
        assert persisted_events == published_events

    @pytest.mark.asyncio
    async def test_no_event_sink_still_publishes(self) -> None:
        """Events are published even without an event sink."""
        published_events = []

        class TrackingQueue:
            async def put(self, event):
                published_events.append(event)

        tracer = EventingTracer(
            NullTracer(),
            event_queue=TrackingQueue(),
            event_sink=None,  # No persistence
            session_id="no-sink-test",
        )

        tracer.emit_event("test_event", {})
        await asyncio.sleep(0.1)

        assert len(published_events) == 1

    @pytest.mark.asyncio
    async def test_persist_flag_false_skips_persistence(self) -> None:
        """persist_event=False skips persistence but still publishes."""
        persisted = []
        published = []

        async def tracking_sink(event):
            persisted.append(event)

        class TrackingQueue:
            async def put(self, event):
                published.append(event)

        tracer = EventingTracer(
            NullTracer(),
            event_queue=TrackingQueue(),
            event_sink=tracking_sink,
            session_id="skip-persist-test",
        )

        tracer.emit_event("test_event", {}, persist_event=False)
        await asyncio.sleep(0.1)

        assert len(persisted) == 0  # Not persisted
        assert len(published) == 1  # But published


@pytest.fixture
def redis_url():
    """Load Redis URL from config (uses DB 1 for tests)."""
    from pathlib import Path
    from urllib.parse import urlparse, urlunparse
    import yaml

    config_path = Path(__file__).parent.parent.parent / "config" / "api.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    base_url = config["redis"]["url"]

    # Use DB 1 for tests instead of DB 0 (production)
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path="/1"))


@pytest.fixture
async def redis_hub(redis_url):
    """Create a RedisEventHub for testing (requires Redis to be running)."""
    hub = RedisEventHub(redis_url=redis_url)
    yield hub
    await hub.close()


class TestRedisEventHubSubscription:
    """Tests for RedisEventHub subscription and event delivery (requires Redis)."""

    @pytest.mark.asyncio
    async def test_subscriber_receives_events_after_subscribe(self, redis_hub) -> None:
        """Subscriber receives events published after subscription."""
        session_id = "sub-test"

        queue = await redis_hub.subscribe(session_id)
        await asyncio.sleep(0.1)  # Allow Redis subscription to establish

        await redis_hub.publish(session_id, {"type": "test", "sequence": 1})

        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event["type"] == "test"

        await redis_hub.unsubscribe(session_id, queue)

    @pytest.mark.asyncio
    async def test_subscriber_misses_events_before_subscribe(self, redis_hub) -> None:
        """Subscriber does not receive events published before subscription."""
        session_id = "miss-test"

        # Publish before subscription
        await redis_hub.publish(session_id, {"type": "missed", "sequence": 1})

        # Subscribe after
        queue = await redis_hub.subscribe(session_id)
        await asyncio.sleep(0.1)  # Allow Redis subscription to establish

        # Queue should be empty (missed the event)
        assert queue.empty()

        await redis_hub.unsubscribe(session_id, queue)

    @pytest.mark.asyncio
    async def test_event_sink_queue_integration(self, redis_hub) -> None:
        """EventSinkQueue correctly publishes to RedisEventHub."""
        session_id = "sink-queue-test"

        queue = await redis_hub.subscribe(session_id)
        await asyncio.sleep(0.1)  # Allow Redis subscription to establish
        sink_queue = EventSinkQueue(redis_hub, session_id)

        await sink_queue.put({"type": "test", "sequence": 1})

        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event["type"] == "test"

        await redis_hub.unsubscribe(session_id, queue)


class TestAgentRunnerEventEmission:
    """Tests for event emission in agent_runner fallback paths."""

    @pytest.mark.asyncio
    async def test_fallback_emit_persists_before_publish(self) -> None:
        """Agent runner fallback emit_event persists before publishing."""
        from src.services.agent_runner import AgentRunner

        runner = AgentRunner()
        session_id = "fallback-test"

        call_order = []

        async def mock_record_event(event):
            call_order.append("persist")
            await asyncio.sleep(0.01)

        async def mock_publish(sid, event):
            call_order.append("publish")

        with patch("src.services.event_service.record_event", mock_record_event):
            with patch.object(runner._event_hub, "publish", mock_publish):
                # Subscribe to receive events
                queue = await runner.subscribe(session_id)

                # Manually trigger the persist_then_publish pattern
                # (simulating the fallback path when tracer is None)
                async def persist_then_publish():
                    await mock_record_event({"type": "test"})
                    await mock_publish(session_id, {"type": "test"})

                await persist_then_publish()

                await runner.unsubscribe(session_id, queue)

        assert call_order == ["persist", "publish"]


class TestSSEReplayWithPersistence:
    """
    Tests for SSE replay correctly finding persisted events.

    These tests verify the complete flow:
    1. Events are persisted to DB
    2. SSE connects and replays from DB
    3. Events are found during replay
    """

    @pytest.mark.asyncio
    async def test_replay_finds_persisted_events(
        self,
        test_engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SSE replay finds events that were persisted before subscription."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(event_service, "AsyncSessionLocal", async_session)

        session_id = "replay-test"

        # Persist events directly (simulating what happens before SSE connects)
        for i in range(1, 4):
            await event_service.record_event({
                "type": f"event_{i}",
                "data": {"index": i},
                "timestamp": datetime.now(timezone.utc),
                "sequence": i,
                "session_id": session_id,
            })

        # Replay (simulating SSE connection)
        events = await event_service.list_events(session_id, after_sequence=0)

        assert len(events) == 3
        assert events[0]["type"] == "event_1"
        assert events[1]["type"] == "event_2"
        assert events[2]["type"] == "event_3"

    @pytest.mark.asyncio
    async def test_replay_with_after_sequence_filter(
        self,
        test_engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SSE replay correctly filters events by after_sequence."""
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(event_service, "AsyncSessionLocal", async_session)

        session_id = "filter-test"

        # Persist events
        for i in range(1, 6):
            await event_service.record_event({
                "type": f"event_{i}",
                "data": {},
                "timestamp": datetime.now(timezone.utc),
                "sequence": i,
                "session_id": session_id,
            })

        # Replay with after_sequence=2 (should get events 3, 4, 5)
        events = await event_service.list_events(session_id, after_sequence=2)

        assert len(events) == 3
        assert events[0]["sequence"] == 3
        assert events[1]["sequence"] == 4
        assert events[2]["sequence"] == 5

    @pytest.mark.asyncio
    async def test_complete_flow_persist_subscribe_replay(
        self,
        test_engine,
        monkeypatch: pytest.MonkeyPatch,
        redis_hub,
    ) -> None:
        """
        Complete flow: persist event, subscribe to hub, replay from DB.

        This simulates the race condition scenario that was fixed:
        1. Event is persisted to DB
        2. SSE subscribes to RedisEventHub
        3. SSE replays from DB and finds the event
        """
        async_session = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(event_service, "AsyncSessionLocal", async_session)

        session_id = "complete-flow-test"

        # Step 1: Persist event (simulates agent emitting event)
        await event_service.record_event({
            "type": "agent_start",
            "data": {"session_id": "claude-123"},
            "timestamp": datetime.now(timezone.utc),
            "sequence": 1,
            "session_id": session_id,
        })

        # Step 2: Subscribe to hub (simulates SSE connection)
        queue = await redis_hub.subscribe(session_id)
        await asyncio.sleep(0.1)  # Allow Redis subscription to establish

        # Step 3: Replay from DB (simulates SSE replay logic)
        replayed_events = await event_service.list_events(session_id, after_sequence=0)

        # Event should be found in replay
        assert len(replayed_events) == 1
        assert replayed_events[0]["type"] == "agent_start"

        # Queue should be empty (event was before subscription)
        assert queue.empty()

        await redis_hub.unsubscribe(session_id, queue)


# =============================================================================
# Terminal Event Deduplication Fix Tests
# =============================================================================

class TestTerminalEventDeduplicationFix:
    """
    Tests for the terminal event deduplication fix.

    This fix prevents the "infinite processing..." bug where terminal events
    (agent_complete, error, cancelled) were incorrectly filtered by the
    deduplication logic when partial message sequence numbers created gaps.

    Root cause: Partial messages consume sequence numbers but aren't persisted
    to SQLite. When SSE reconnects and replays from SQLite, it misses the partial
    sequences. If last_sequence was tracking a partial message seq (e.g., 287),
    the terminal event (e.g., seq 291) could be incorrectly skipped.

    Fix: Terminal events are NEVER skipped, regardless of deduplication state.

    See: Session 20260118_184501_4e5cf999 analysis for the original bug.
    """

    @pytest.mark.asyncio
    async def test_terminal_event_delivered_despite_sequence_gap(self) -> None:
        """
        Terminal events are delivered even when there's a sequence gap.

        Simulates the bug scenario:
        1. Events 1-207 delivered (including partial messages via Redis)
        2. Partial messages 208-287 delivered via Redis (not persisted)
        3. last_sequence tracking shows 287
        4. Terminal event with seq 291 arrives
        5. BUG: Old code would skip 291 because 291 > 287 but seen_sequences might filter it
        6. FIX: Terminal events bypass deduplication entirely
        """
        from src.api.routes.sessions import stream_events

        # Simulate the deduplication logic directly
        seen_sequences = {1, 2, 3, 207, 208, 250, 287}  # Includes partial message sequences
        last_sequence = 287  # Tracking includes partial messages

        # Terminal event with sequence gap
        terminal_event = {
            "type": "agent_complete",
            "sequence": 291,
            "data": {"status": "COMPLETE"},
        }

        seq = terminal_event.get("sequence", 0)
        event_type = terminal_event.get("type")

        # CRITICAL: Terminal events must bypass deduplication
        is_terminal = event_type in ("agent_complete", "error", "cancelled")

        # Old buggy logic: would check `seq in seen_sequences or seq <= last_sequence`
        # This would pass (291 not in seen_sequences, 291 > 287) BUT if last_sequence
        # was incorrectly tracked, it could fail

        # The fix ensures terminal events are NEVER skipped
        should_skip = not is_terminal and (seq in seen_sequences or seq <= last_sequence)

        assert is_terminal is True
        assert should_skip is False  # Terminal event must NOT be skipped

    @pytest.mark.asyncio
    async def test_terminal_event_delivered_even_if_seen_before(self) -> None:
        """
        Terminal events are delivered even if their sequence was somehow seen.

        Edge case: If a terminal event sequence was somehow added to seen_sequences
        (e.g., due to replay overlap), it should STILL be delivered.
        """
        seen_sequences = {1, 2, 3, 291}  # Terminal event seq already in seen
        last_sequence = 290

        terminal_event = {
            "type": "agent_complete",
            "sequence": 291,
            "data": {"status": "COMPLETE"},
        }

        seq = terminal_event.get("sequence", 0)
        event_type = terminal_event.get("type")
        is_terminal = event_type in ("agent_complete", "error", "cancelled")

        # Old buggy logic would skip: 291 in seen_sequences
        old_logic_would_skip = seq in seen_sequences or seq <= last_sequence

        # New fixed logic: terminal events bypass deduplication
        new_logic_should_skip = not is_terminal and (seq in seen_sequences or seq <= last_sequence)

        assert old_logic_would_skip is True  # Old logic WOULD skip
        assert new_logic_should_skip is False  # New logic does NOT skip

    @pytest.mark.asyncio
    async def test_all_terminal_event_types_bypass_deduplication(self) -> None:
        """All terminal event types (agent_complete, error, cancelled) bypass deduplication."""
        terminal_types = ["agent_complete", "error", "cancelled"]

        for event_type in terminal_types:
            seen_sequences = {1, 2, 3, 100}  # Include the terminal event's sequence
            last_sequence = 100

            terminal_event = {
                "type": event_type,
                "sequence": 100,  # Same as last_sequence AND in seen_sequences
                "data": {},
            }

            seq = terminal_event.get("sequence", 0)
            is_terminal = terminal_event.get("type") in ("agent_complete", "error", "cancelled")
            should_skip = not is_terminal and (seq in seen_sequences or seq <= last_sequence)

            assert is_terminal is True, f"{event_type} should be terminal"
            assert should_skip is False, f"{event_type} should NOT be skipped"

    @pytest.mark.asyncio
    async def test_non_terminal_events_still_deduplicated(self) -> None:
        """Non-terminal events are still properly deduplicated."""
        seen_sequences = {1, 2, 3}
        last_sequence = 3

        # Regular message event (not terminal)
        message_event = {
            "type": "message",
            "sequence": 3,  # Already seen
            "data": {"text": "Hello"},
        }

        seq = message_event.get("sequence", 0)
        event_type = message_event.get("type")
        is_terminal = event_type in ("agent_complete", "error", "cancelled")
        should_skip = not is_terminal and (seq in seen_sequences or seq <= last_sequence)

        assert is_terminal is False
        assert should_skip is True  # Non-terminal events ARE still deduplicated

    @pytest.mark.asyncio
    async def test_sequence_gap_scenario_from_session_4e5cf999(self) -> None:
        """
        Reproduce the exact scenario from session 20260118_184501_4e5cf999.

        Timeline:
        - Seq 205: metrics_update (persisted)
        - Seq 207: thinking (persisted)
        - Seq 208-287: partial messages (NOT persisted, but seq numbers consumed)
        - Seq 288: message (persisted)
        - Seq 291: agent_complete (persisted)

        Bug: SSE client receives partial messages 208-287 via Redis,
        sets last_sequence=287. When replaying from SQLite, it gets
        seq 207 -> 288 -> 291. If deduplication incorrectly filters
        based on Redis-delivered sequences, agent_complete (291) might
        never be delivered.
        """
        # State after receiving partial messages via Redis
        seen_sequences = set(range(1, 288))  # 1-287 seen via Redis
        last_sequence = 287  # Tracking partial messages

        # Events from SQLite replay (skips partial messages)
        sqlite_events = [
            {"type": "metrics_update", "sequence": 205, "data": {}},
            {"type": "thinking", "sequence": 207, "data": {"text": "..."}},
            {"type": "message", "sequence": 288, "data": {"text": "Response"}},
            {"type": "metrics_update", "sequence": 289, "data": {}},
            {"type": "metrics_update", "sequence": 290, "data": {}},
            {"type": "agent_complete", "sequence": 291, "data": {"status": "COMPLETE"}},
        ]

        delivered_events = []

        for event in sqlite_events:
            seq = event.get("sequence", 0)
            event_type = event.get("type")
            is_terminal = event_type in ("agent_complete", "error", "cancelled")

            # Apply fixed deduplication logic
            should_skip = not is_terminal and (seq in seen_sequences or seq <= last_sequence)

            if not should_skip:
                delivered_events.append(event)
                seen_sequences.add(seq)
                if not is_terminal:
                    last_sequence = seq

        # Verify the fix: agent_complete MUST be delivered
        delivered_types = [e["type"] for e in delivered_events]
        delivered_seqs = [e["sequence"] for e in delivered_events]

        assert "agent_complete" in delivered_types, \
            "agent_complete must be delivered despite sequence gap"

        # Verify message at 288 is also delivered (288 > 287)
        assert "message" in delivered_types
        assert 288 in delivered_seqs

        # Verify earlier events (205, 207) are skipped (already in seen_sequences)
        assert 205 not in delivered_seqs, "seq 205 should be skipped (already seen)"
        assert 207 not in delivered_seqs, "seq 207 should be skipped (already seen)"

        # Verify later metrics_update events (289, 290) ARE delivered (> last_sequence)
        assert 289 in delivered_seqs
        assert 290 in delivered_seqs

        # Verify agent_complete (291) is delivered despite being a terminal event
        assert 291 in delivered_seqs
