"""
Redis Streams-based event hub for SSE streaming.

Uses Redis Streams for durable, ordered event delivery.
Unlike Pub/Sub, Streams persist events so consumers can:
- Join at any time and read from the beginning
- Resume from a specific point after disconnect
- Never miss events due to race conditions

Architecture:
- Redis Stream per session: session:{session_id}:events
- Events persist in stream until TTL expires or explicitly trimmed
- Consumers use XREAD BLOCK for efficient long-polling
- No background tasks needed - XREAD handles blocking natively

Redis is required - the system will fail to start without it.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Stream configuration
DEFAULT_STREAM_MAXLEN = 10000  # Keep last 10k events per session
DEFAULT_BLOCK_MS = 30000  # 30 second block timeout for XREAD
DEFAULT_STREAM_TTL_SECONDS = 86400  # 24 hour TTL for streams


@dataclass
class StreamPosition:
    """Tracks a consumer's position in a Redis Stream."""
    stream_id: str = "0"  # "0" = from beginning, "$" = only new
    events_received: int = 0
    last_sequence: int = 0


@dataclass
class SubscriberInfo:
    """Information about an active subscriber."""
    session_id: str
    position: StreamPosition = field(default_factory=StreamPosition)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


class RedisEventHub:
    """
    Redis Streams-based event hub for SSE streaming.

    Key advantages over Pub/Sub:
    - Events persist in the stream - no race conditions
    - Consumers can join anytime and read from any point
    - Automatic backpressure via stream trimming
    - Built-in ordering guarantees

    Stream naming: session:{session_id}:events
    """

    def __init__(
        self,
        redis_url: str,
        stream_maxlen: int = DEFAULT_STREAM_MAXLEN,
        block_ms: int = DEFAULT_BLOCK_MS,
        stream_ttl_seconds: int = DEFAULT_STREAM_TTL_SECONDS,
        socket_timeout: float = 35.0,  # Must be > block_ms/1000 (30s) + buffer
        socket_connect_timeout: float = 5.0,
    ):
        """
        Initialize Redis Streams event hub.

        Args:
            redis_url: Redis connection URL (e.g., redis://localhost:6379/0)
            stream_maxlen: Maximum events to keep per stream (MAXLEN ~)
            block_ms: XREAD block timeout in milliseconds
            stream_ttl_seconds: TTL for stream keys (cleanup idle sessions)
            socket_timeout: Redis socket timeout in seconds. MUST be greater than
                           block_ms/1000 to allow XREAD BLOCK to complete normally.
            socket_connect_timeout: Redis connection timeout in seconds
        """
        self._redis_url = redis_url
        self._stream_maxlen = stream_maxlen
        self._block_ms = block_ms
        self._stream_ttl_seconds = stream_ttl_seconds
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout

        # Connection pool for all operations
        self._redis_pool: Optional[redis.ConnectionPool] = None

        # Track active subscribers for stats/debugging
        self._subscribers: Dict[str, SubscriberInfo] = {}
        self._lock = asyncio.Lock()

        logger.info(
            f"RedisEventHub (Streams) initialized: url={redis_url}, "
            f"maxlen={stream_maxlen}, block_ms={block_ms}"
        )

    async def _ensure_pool(self) -> redis.ConnectionPool:
        """Lazy-initialize Redis connection pool."""
        if self._redis_pool is None:
            self._redis_pool = redis.ConnectionPool.from_url(
                self._redis_url,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_connect_timeout,
                decode_responses=True,  # Decode to strings for easier handling
            )
            logger.debug("Redis connection pool created")
        return self._redis_pool

    def _get_stream_key(self, session_id: str) -> str:
        """Get Redis Stream key for session."""
        return f"session:{session_id}:events"

    async def publish(self, session_id: str, event: Dict[str, Any]) -> str:
        """
        Publish an event to the session's stream.

        Events are durably stored in Redis until trimmed or TTL expires.
        This eliminates race conditions - consumers can always read from
        any point in the stream.

        Args:
            session_id: The session ID.
            event: The event to publish.

        Returns:
            The stream entry ID (e.g., "1234567890123-0").
        """
        stream_key = self._get_stream_key(session_id)
        pool = await self._ensure_pool()

        try:
            # Serialize event to JSON
            payload = json.dumps(event, default=str)

            async with redis.Redis(connection_pool=pool) as conn:
                # XADD with approximate maxlen trimming for efficiency
                # The ~ makes trimming approximate (faster, may keep slightly more)
                entry_id = await conn.xadd(
                    stream_key,
                    {"data": payload},
                    maxlen=self._stream_maxlen,
                    approximate=True,
                )

                # Set/refresh TTL on the stream key
                await conn.expire(stream_key, self._stream_ttl_seconds)

            logger.debug(
                f"Published event {event.get('type')} seq={event.get('sequence')} "
                f"to stream {stream_key}, entry_id={entry_id}"
            )
            return entry_id

        except Exception as e:
            logger.error(
                f"Failed to publish event to Redis Stream for session {session_id}: {e}"
            )
            raise

    async def subscribe(
        self,
        session_id: str,
        from_sequence: Optional[int] = None,
        from_stream_id: str = "0",
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Subscribe to events from a session's stream.

        This is an async generator that yields events as they arrive.
        Uses XREAD BLOCK for efficient long-polling.

        Args:
            session_id: The session ID to subscribe to.
            from_sequence: If provided, start from events after this sequence number.
                          Events are scanned to find the right starting point.
            from_stream_id: Redis Stream ID to start from:
                          - "0" = from the beginning (default, recommended)
                          - "$" = only new events (not recommended - may miss events)
                          - specific ID like "1234567890123-0"

        Yields:
            Event dictionaries as they arrive.
        """
        stream_key = self._get_stream_key(session_id)
        pool = await self._ensure_pool()

        # Create subscriber info for tracking
        subscriber_id = f"{session_id}_{id(asyncio.current_task())}"
        subscriber_info = SubscriberInfo(session_id=session_id)

        async with self._lock:
            self._subscribers[subscriber_id] = subscriber_info

        try:
            async with redis.Redis(connection_pool=pool) as conn:
                # Determine starting position
                last_id = from_stream_id

                # If from_sequence is provided, we need to find the right stream position
                # by scanning events until we find one with sequence > from_sequence
                if from_sequence is not None and from_sequence > 0:
                    # Read all events and find the right starting point
                    all_entries = await conn.xrange(stream_key, "-", "+")
                    for entry_id, fields in all_entries:
                        try:
                            event = json.loads(fields.get("data", "{}"))
                            seq = event.get("sequence", 0)
                            if seq > from_sequence:
                                # Found first event after our sequence
                                # Use the previous entry's ID as our starting point
                                break
                            last_id = entry_id
                        except (json.JSONDecodeError, KeyError):
                            continue

                logger.debug(
                    f"Subscriber {subscriber_id} starting from stream_id={last_id}"
                )

                # Main read loop
                while not subscriber_info.stop_event.is_set():
                    try:
                        # XREAD with BLOCK - waits for new messages or timeout
                        result = await conn.xread(
                            {stream_key: last_id},
                            block=self._block_ms,
                            count=100,  # Read up to 100 events at a time
                        )

                        if not result:
                            # Timeout - no new events, yield heartbeat
                            yield {
                                "type": "heartbeat",
                                "data": {
                                    "session_id": session_id,
                                    "server_time": datetime.now(timezone.utc).isoformat(),
                                    "stream_position": last_id,
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "sequence": -1,
                            }
                            continue

                        # Process received events
                        for stream_name, entries in result:
                            for entry_id, fields in entries:
                                last_id = entry_id
                                subscriber_info.position.stream_id = entry_id

                                try:
                                    event = json.loads(fields.get("data", "{}"))
                                    subscriber_info.position.events_received += 1
                                    subscriber_info.position.last_sequence = event.get(
                                        "sequence", 0
                                    )
                                    yield event

                                except (json.JSONDecodeError, KeyError) as e:
                                    logger.warning(
                                        f"Failed to decode event from stream: {e}"
                                    )
                                    continue

                    except asyncio.CancelledError:
                        logger.debug(f"Subscriber {subscriber_id} cancelled")
                        raise

                    except redis.ConnectionError as e:
                        logger.warning(f"Redis connection error in subscriber: {e}")
                        # Wait before retry, but check stop_event frequently
                        for _ in range(10):
                            if subscriber_info.stop_event.is_set():
                                return
                            await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            raise

        except Exception as e:
            logger.exception(f"Error in subscriber {subscriber_id}: {e}")
            raise

        finally:
            # Clean up subscriber tracking
            async with self._lock:
                self._subscribers.pop(subscriber_id, None)
            logger.debug(f"Subscriber {subscriber_id} cleaned up")

    async def stop_subscriber(self, session_id: str) -> None:
        """
        Signal all subscribers for a session to stop.

        Args:
            session_id: The session ID.
        """
        async with self._lock:
            for sub_id, info in self._subscribers.items():
                if info.session_id == session_id:
                    info.stop_event.set()

    async def get_stream_info(self, session_id: str) -> Dict[str, Any]:
        """
        Get information about a session's stream.

        Args:
            session_id: The session ID.

        Returns:
            Stream info including length, first/last entry IDs, etc.
        """
        stream_key = self._get_stream_key(session_id)
        pool = await self._ensure_pool()

        try:
            async with redis.Redis(connection_pool=pool) as conn:
                info = await conn.xinfo_stream(stream_key)
                return {
                    "length": info.get("length", 0),
                    "first_entry": info.get("first-entry"),
                    "last_entry": info.get("last-entry"),
                    "radix_tree_keys": info.get("radix-tree-keys", 0),
                    "radix_tree_nodes": info.get("radix-tree-nodes", 0),
                }
        except redis.ResponseError:
            # Stream doesn't exist
            return {"length": 0, "first_entry": None, "last_entry": None}

    async def get_events_after(
        self,
        session_id: str,
        after_sequence: int,
        limit: int = 1000,
    ) -> list[Dict[str, Any]]:
        """
        Get events from stream after a given sequence number.

        Useful for history replay when SSE reconnects.

        Args:
            session_id: The session ID.
            after_sequence: Return events with sequence > this value.
            limit: Maximum events to return.

        Returns:
            List of events ordered by sequence.
        """
        stream_key = self._get_stream_key(session_id)
        pool = await self._ensure_pool()

        events = []
        try:
            async with redis.Redis(connection_pool=pool) as conn:
                # Read all entries (we filter by sequence in Python)
                entries = await conn.xrange(stream_key, "-", "+", count=limit * 2)

                for entry_id, fields in entries:
                    try:
                        event = json.loads(fields.get("data", "{}"))
                        seq = event.get("sequence", 0)
                        if seq > after_sequence:
                            events.append(event)
                            if len(events) >= limit:
                                break
                    except (json.JSONDecodeError, KeyError):
                        continue

        except redis.ResponseError:
            # Stream doesn't exist
            pass

        return events

    async def get_subscriber_count(self, session_id: str) -> int:
        """
        Get the number of active subscribers for a session.

        Args:
            session_id: The session ID.

        Returns:
            Number of active subscribers.
        """
        async with self._lock:
            return sum(
                1 for info in self._subscribers.values()
                if info.session_id == session_id
            )

    async def get_subscriber_stats(self, session_id: str) -> list[Dict[str, Any]]:
        """
        Get statistics for all subscribers of a session.

        Args:
            session_id: The session ID.

        Returns:
            List of subscriber stats.
        """
        async with self._lock:
            return [
                {
                    "stream_id": info.position.stream_id,
                    "events_received": info.position.events_received,
                    "last_sequence": info.position.last_sequence,
                    "created_at": info.created_at.isoformat(),
                }
                for info in self._subscribers.values()
                if info.session_id == session_id
            ]

    async def trim_stream(self, session_id: str, maxlen: Optional[int] = None) -> int:
        """
        Trim a session's stream to a maximum length.

        Args:
            session_id: The session ID.
            maxlen: Maximum entries to keep (uses default if not specified).

        Returns:
            Number of entries removed.
        """
        stream_key = self._get_stream_key(session_id)
        pool = await self._ensure_pool()
        maxlen = maxlen or self._stream_maxlen

        try:
            async with redis.Redis(connection_pool=pool) as conn:
                # Get current length
                try:
                    info = await conn.xinfo_stream(stream_key)
                    old_len = info.get("length", 0)
                except redis.ResponseError:
                    return 0

                # Trim
                await conn.xtrim(stream_key, maxlen=maxlen, approximate=False)

                # Get new length
                info = await conn.xinfo_stream(stream_key)
                new_len = info.get("length", 0)

                removed = old_len - new_len
                if removed > 0:
                    logger.info(
                        f"Trimmed stream {stream_key}: {old_len} -> {new_len} "
                        f"({removed} removed)"
                    )
                return removed

        except Exception as e:
            logger.error(f"Failed to trim stream {stream_key}: {e}")
            return 0

    async def delete_stream(self, session_id: str) -> bool:
        """
        Delete a session's stream entirely.

        Args:
            session_id: The session ID.

        Returns:
            True if deleted, False otherwise.
        """
        stream_key = self._get_stream_key(session_id)
        pool = await self._ensure_pool()

        try:
            async with redis.Redis(connection_pool=pool) as conn:
                result = await conn.delete(stream_key)
                if result:
                    logger.info(f"Deleted stream {stream_key}")
                return bool(result)
        except Exception as e:
            logger.error(f"Failed to delete stream {stream_key}: {e}")
            return False

    async def close(self) -> None:
        """Close Redis connection pool and stop all subscribers."""
        # Signal all subscribers to stop
        async with self._lock:
            for info in self._subscribers.values():
                info.stop_event.set()

        # Wait a moment for subscribers to clean up
        await asyncio.sleep(0.1)

        # Close Redis pool
        if self._redis_pool:
            await self._redis_pool.disconnect()
            logger.info("Redis connection pool closed")


class EventSinkQueue:
    """
    Adapter to present RedisEventHub as an asyncio.Queue-like sink.

    Provides a queue-like interface for the tracer to push events,
    which are then published to the Redis Stream.
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

    async def put(self, event: Dict[str, Any]) -> None:
        """
        Put an event (publish to Redis Stream).

        Args:
            event: The event to publish.
        """
        await self._hub.publish(self._session_id, event)

    def put_nowait(self, event: Dict[str, Any]) -> None:
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
