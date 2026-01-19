"""
Redis-based event hub for SSE streaming.

Uses Redis Pub/Sub for cross-container event delivery.
Each subscriber gets a local asyncio.Queue fed by a background
task listening to Redis.

Architecture:
- Redis channel per session: session:{session_id}:events
- Background listener task per subscriber
- Local queue buffering (500 events max)
- Backpressure handling via dropping oldest events
- Connection pooling and automatic reconnection

Redis is required - the system will fail to start without it.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Dict, Optional, Set

import redis.asyncio as redis

logger = logging.getLogger(__name__)


@dataclass
class SubscriberStats:
    """Statistics for a subscriber queue."""
    events_received: int = 0
    events_dropped: int = 0
    last_sequence_sent: int = 0


class RedisEventHub:
    """
    Redis Pub/Sub based event hub for SSE streaming.

    Replaces in-memory EventHub with Redis-backed pub/sub for
    horizontal scaling and cross-container event delivery.

    Each subscriber maintains:
    - Local asyncio.Queue (500 events max)
    - Background task listening to Redis Pub/Sub
    - Statistics tracking (events received/dropped)

    Redis channels use pattern: session:{session_id}:events
    """

    def __init__(
        self,
        redis_url: str,
        max_queue_size: int = 500,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
    ):
        """
        Initialize Redis event hub.

        Args:
            redis_url: Redis connection URL (e.g., redis://localhost:6379/0)
            max_queue_size: Maximum events in local queue per subscriber
            socket_timeout: Redis socket timeout in seconds
            socket_connect_timeout: Redis connection timeout in seconds
        """
        if redis is None:
            raise ImportError(
                "redis package is required for RedisEventHub. "
                "Install with: pip install redis>=5.0.0"
            )

        self._redis_url = redis_url
        self._max_queue_size = max_queue_size
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout

        # Connection pool for publishing
        self._redis_pool: Optional[redis.ConnectionPool] = None

        # Track local subscribers and their queues
        self._subscribers: DefaultDict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._subscriber_stats: Dict[asyncio.Queue, SubscriberStats] = {}
        self._subscriber_tasks: Dict[asyncio.Queue, asyncio.Task] = {}
        self._lock = asyncio.Lock()

        logger.info(
            f"RedisEventHub initialized with URL={redis_url}, "
            f"max_queue_size={max_queue_size}"
        )

    async def _ensure_pool(self) -> redis.ConnectionPool:
        """Lazy-initialize Redis connection pool."""
        if self._redis_pool is None:
            self._redis_pool = redis.ConnectionPool.from_url(
                self._redis_url,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_connect_timeout,
                decode_responses=False,  # Keep binary for JSON
            )
            logger.debug("Redis connection pool created")
        return self._redis_pool

    def _get_channel_name(self, session_id: str) -> str:
        """Get Redis channel name for session."""
        return f"session:{session_id}:events"

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        """
        Subscribe to events for a session.

        Creates a local asyncio.Queue and spawns a background task
        to listen to Redis pub/sub and feed the queue.

        Args:
            session_id: The session ID to subscribe to.

        Returns:
            Queue to receive events from.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)

        async with self._lock:
            self._subscribers[session_id].add(queue)
            self._subscriber_stats[queue] = SubscriberStats()

        # Spawn background task to listen to Redis
        task = asyncio.create_task(
            self._redis_listener_task(session_id, queue),
            name=f"redis_listener_{session_id}_{id(queue)}"
        )
        self._subscriber_tasks[queue] = task

        logger.debug(f"New Redis subscriber for session {session_id}")
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        """
        Unsubscribe from events for a session.

        Cancels the background Redis listener task and cleans up queue.

        Args:
            session_id: The session ID.
            queue: The queue to unsubscribe.
        """
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if not subscribers:
                return
            subscribers.discard(queue)

            # Cancel background task
            task = self._subscriber_tasks.pop(queue, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

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

    async def _redis_listener_task(
        self,
        session_id: str,
        queue: asyncio.Queue
    ) -> None:
        """
        Background task to listen to Redis pub/sub and feed local queue.

        Runs until cancelled or connection fails.

        Args:
            session_id: The session ID.
            queue: The local queue to feed.
        """
        channel_name = self._get_channel_name(session_id)
        pool = await self._ensure_pool()

        try:
            async with redis.Redis(connection_pool=pool) as conn:
                pubsub = conn.pubsub()
                await pubsub.subscribe(channel_name)

                logger.debug(
                    f"Redis listener started for session {session_id}, "
                    f"channel {channel_name}"
                )

                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue

                    try:
                        # Decode event from JSON
                        event = json.loads(message["data"])

                        # Get stats
                        stats = self._subscriber_stats.get(queue)

                        # Handle backpressure
                        if queue.full():
                            try:
                                dropped = queue.get_nowait()
                                if stats:
                                    stats.events_dropped += 1
                                logger.warning(
                                    f"Dropping event (seq={dropped.get('sequence')}) "
                                    f"for session {session_id} due to backpressure"
                                )
                            except asyncio.QueueEmpty:
                                pass

                        # Put event in queue
                        try:
                            queue.put_nowait(event)
                            if stats:
                                stats.events_received += 1
                                stats.last_sequence_sent = event.get("sequence", 0)
                        except asyncio.QueueFull:
                            if stats:
                                stats.events_dropped += 1
                            logger.error(
                                f"Failed to enqueue event for session {session_id}"
                            )

                    except (json.JSONDecodeError, KeyError) as e:
                        logger.error(
                            f"Failed to decode Redis message for {session_id}: {e}"
                        )
                        continue

        except asyncio.CancelledError:
            logger.debug(f"Redis listener cancelled for session {session_id}")
            raise

        except Exception as e:
            logger.exception(
                f"Redis listener error for session {session_id}: {e}"
            )
            # TODO: Could emit error event to queue here

        finally:
            try:
                await pubsub.unsubscribe(channel_name)
                await pubsub.close()
            except Exception as e:
                logger.warning(f"Error closing pubsub: {e}")

    async def publish(self, session_id: str, event: Dict[str, Any]) -> None:
        """
        Publish an event to all subscribers for a session via Redis.

        Args:
            session_id: The session ID.
            event: The event to publish.
        """
        channel_name = self._get_channel_name(session_id)
        pool = await self._ensure_pool()

        try:
            # Serialize event
            payload = json.dumps(event, default=str)

            # Publish to Redis
            async with redis.Redis(connection_pool=pool) as conn:
                num_subscribers = await conn.publish(channel_name, payload)

            logger.debug(
                f"Published event {event.get('type')} seq={event.get('sequence')} "
                f"to {num_subscribers} Redis subscribers for session {session_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to publish event to Redis for session {session_id}: {e}"
            )
            # Don't raise - event may still be in DB for replay
            # Emit infrastructure error to local subscribers so clients know something is wrong
            await self._emit_infrastructure_error(session_id, "redis_publish_failed", str(e))

    async def get_subscriber_count(self, session_id: str) -> int:
        """
        Get the number of active local subscribers for a session.

        Note: This only counts local subscribers in this API container,
        not across all containers. Use Redis PUBSUB NUMSUB for global count.

        Args:
            session_id: The session ID.

        Returns:
            Number of local subscribers.
        """
        async with self._lock:
            return len(self._subscribers.get(session_id, set()))

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

    async def _emit_infrastructure_error(
        self, session_id: str, error_type: str, error_message: str
    ) -> None:
        """
        Emit an infrastructure error event to all local subscribers.

        This notifies connected clients when infrastructure issues occur
        (e.g., Redis publish failures) so they can take appropriate action.

        Args:
            session_id: The session ID.
            error_type: Type of infrastructure error (e.g., "redis_publish_failed").
            error_message: Human-readable error message.
        """
        from datetime import datetime, timezone

        error_event = {
            "type": "infrastructure_error",
            "data": {
                "error_type": error_type,
                "message": error_message,
                "recoverable": True,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": -1,  # Infrastructure events don't have sequence numbers
        }

        async with self._lock:
            subscribers = self._subscribers.get(session_id, set())
            for queue in subscribers:
                try:
                    # Use put_nowait to avoid blocking
                    queue.put_nowait(error_event)
                except asyncio.QueueFull:
                    # Queue is full, skip this subscriber
                    logger.warning(
                        f"Could not deliver infrastructure error to subscriber: queue full"
                    )

    async def close(self) -> None:
        """Close Redis connection pool and cancel all listener tasks."""
        # Cancel all listener tasks
        tasks = list(self._subscriber_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Close Redis pool
        if self._redis_pool:
            await self._redis_pool.disconnect()
            logger.info("Redis connection pool closed")


class EventSinkQueue:
    """
    Adapter to present RedisEventHub as an asyncio.Queue-like sink.

    Provides a queue-like interface for the tracer to push events,
    which are then published to Redis Pub/Sub.
    """

    def __init__(self, hub: RedisEventHub, session_id: str) -> None:
        """
        Initialize event sink queue.

        Args:
            hub: The RedisEventHub instance.
            session_id: The session ID.
        """
        self._hub = hub
        self._session_id = session_id

    async def put(self, event: dict[str, Any]) -> None:
        """
        Put an event (publish to Redis Pub/Sub).

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
