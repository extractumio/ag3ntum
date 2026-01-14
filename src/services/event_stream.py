"""
Event streaming fanout for SSE.

Provides per-session subscriber queues with backpressure handling.

Implements robustness features:
- Proper backpressure handling with event counting
- Dropped event tracking for client notification
- Graceful degradation under load
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, DefaultDict, Dict, Set

logger = logging.getLogger(__name__)


@dataclass
class SubscriberStats:
    """Statistics for a subscriber queue."""
    events_received: int = 0
    events_dropped: int = 0
    last_sequence_sent: int = 0


class EventHub:
    """
    Fan out events to multiple subscribers per session.

    Handles backpressure by dropping oldest events when queues are full,
    but tracks dropped events so clients can be notified.
    """

    def __init__(self, max_queue_size: int = 500) -> None:
        self._subscribers: DefaultDict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._subscriber_stats: Dict[asyncio.Queue, SubscriberStats] = {}
        self._lock = asyncio.Lock()
        self._max_queue_size = max_queue_size
        # Track total events per session for monitoring
        self._session_event_counts: DefaultDict[str, int] = defaultdict(int)

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        """
        Subscribe to events for a session.

        Args:
            session_id: The session ID to subscribe to.

        Returns:
            Queue to receive events from.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers[session_id].add(queue)
            self._subscriber_stats[queue] = SubscriberStats()
        logger.debug(f"New subscriber for session {session_id}")
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        """
        Unsubscribe from events for a session.

        Args:
            session_id: The session ID.
            queue: The queue to unsubscribe.
        """
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            # Clean up stats
            stats = self._subscriber_stats.pop(queue, None)
            if stats and stats.events_dropped > 0:
                logger.info(
                    f"Subscriber for session {session_id} unsubscribed. "
                    f"Stats: {stats.events_received} received, "
                    f"{stats.events_dropped} dropped"
                )
            if not subscribers:
                self._subscribers.pop(session_id, None)

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        """
        Publish an event to all subscribers for a session.

        Handles backpressure by dropping oldest events when queues are full.

        Args:
            session_id: The session ID.
            event: The event to publish.
        """
        async with self._lock:
            subscribers = list(self._subscribers.get(session_id, set()))
            self._session_event_counts[session_id] += 1

        sequence = event.get("sequence", 0)

        for queue in subscribers:
            stats = self._subscriber_stats.get(queue)

            if queue.full():
                # Queue is full - drop oldest event
                try:
                    dropped_event = queue.get_nowait()
                    if stats:
                        stats.events_dropped += 1

                    # Log with context about what was dropped
                    dropped_type = dropped_event.get("type", "unknown")
                    dropped_seq = dropped_event.get("sequence", "?")
                    logger.warning(
                        f"Dropping event (type={dropped_type}, seq={dropped_seq}) "
                        f"for session {session_id} due to backpressure. "
                        f"Total dropped for this subscriber: "
                        f"{stats.events_dropped if stats else '?'}"
                    )
                except asyncio.QueueEmpty:
                    pass

            try:
                queue.put_nowait(event)
                if stats:
                    stats.events_received += 1
                    stats.last_sequence_sent = sequence
            except asyncio.QueueFull:
                # Shouldn't happen after we just removed one, but handle it
                if stats:
                    stats.events_dropped += 1
                logger.error(
                    f"Failed to enqueue event for session {session_id} "
                    f"even after dropping oldest"
                )

    async def get_subscriber_stats(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        """
        Get statistics for all subscribers of a session.

        Args:
            session_id: The session ID.

        Returns:
            List of stats dictionaries.
        """
        async with self._lock:
            subscribers = self._subscribers.get(session_id, set())
            return [
                {
                    "events_received": self._subscriber_stats.get(q, SubscriberStats()).events_received,
                    "events_dropped": self._subscriber_stats.get(q, SubscriberStats()).events_dropped,
                    "last_sequence_sent": self._subscriber_stats.get(q, SubscriberStats()).last_sequence_sent,
                    "queue_size": q.qsize(),
                    "queue_full": q.full(),
                }
                for q in subscribers
            ]

    async def get_session_event_count(self, session_id: str) -> int:
        """
        Get total events published for a session.

        Args:
            session_id: The session ID.

        Returns:
            Total event count.
        """
        return self._session_event_counts.get(session_id, 0)

    async def get_subscriber_count(self, session_id: str) -> int:
        """
        Get the number of active subscribers for a session.

        Args:
            session_id: The session ID.

        Returns:
            Number of subscribers.
        """
        async with self._lock:
            return len(self._subscribers.get(session_id, set()))

    async def publish_backpressure_warning(
        self, session_id: str, dropped_count: int
    ) -> None:
        """
        Send a warning event to subscribers about dropped events.

        Args:
            session_id: The session ID.
            dropped_count: Number of events that were dropped.
        """
        warning_event = {
            "type": "warning",
            "data": {
                "message": f"{dropped_count} events were dropped due to backpressure",
                "dropped_count": dropped_count,
                "warning_type": "backpressure",
            },
            "session_id": session_id,
        }
        await self.publish(session_id, warning_event)


class EventSinkQueue:
    """
    Adapter to present EventHub as an asyncio.Queue-like sink.

    Provides a queue-like interface for the tracer to push events,
    which are then fanned out to all subscribers.
    """

    def __init__(self, hub: EventHub, session_id: str) -> None:
        self._hub = hub
        self._session_id = session_id

    async def put(self, event: dict[str, Any]) -> None:
        """
        Put an event (publish to all subscribers).

        Args:
            event: The event to publish.
        """
        await self._hub.publish(self._session_id, event)

    def put_nowait(self, event: dict[str, Any]) -> None:
        """
        Put an event without waiting (fire-and-forget).

        Creates an async task to publish the event.

        Args:
            event: The event to publish.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                f"Cannot publish event for session {self._session_id}: "
                "no running event loop"
            )
            return
        loop.create_task(self.put(event))
