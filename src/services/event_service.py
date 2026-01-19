"""
Event persistence service for Ag3ntum.

Stores SSE events in the database for replay and recovery.

Implements robustness features:
- Retry logic for transient database failures
- Proper error handling with logging
- Event sequence validation
- Timeout on database operations
- Sensitive data scanning before persistence
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from ..db.database import AsyncSessionLocal
from ..services.session_service import session_service
from ..db.models import Event
from ..security import scan_and_redact, is_scanner_enabled

logger = logging.getLogger(__name__)

# Type variable for retry decorator
T = TypeVar("T")

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 0.05  # Start with 50ms
RETRY_BACKOFF_MULTIPLIER = 2.0

# Database operation timeout (seconds)
DB_OPERATION_TIMEOUT = 10.0


def with_db_retry(
    max_retries: int = MAX_RETRIES,
    retry_delay: float = RETRY_DELAY_SECONDS,
    backoff_multiplier: float = RETRY_BACKOFF_MULTIPLIER,
) -> Callable:
    """
    Decorator for retrying database operations on transient failures.

    Retries on OperationalError (connection issues, locks, etc.)
    but not on IntegrityError (constraint violations).
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_error: Optional[Exception] = None
            delay = retry_delay

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except OperationalError as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Event service DB operation failed "
                            f"(attempt {attempt + 1}/{max_retries + 1}): {e}. "
                            f"Retrying in {delay:.3f}s..."
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_multiplier
                    else:
                        logger.error(
                            f"Event service DB operation failed after "
                            f"{max_retries + 1} attempts: {e}"
                        )
                except IntegrityError:
                    # Don't retry integrity errors - they won't succeed
                    raise

            raise last_error  # type: ignore
        return wrapper
    return decorator


async def record_event(event: dict[str, Any]) -> bool:
    """
    Persist a structured event to the database.

    Args:
        event: The event dictionary to persist.

    Returns:
        True if event was recorded successfully, False otherwise.
    """
    session_id = event.get("session_id") or event.get("data", {}).get("session_id")
    if not session_id:
        logger.warning("Skipping event without session_id: %s", event.get("type"))
        return False

    timestamp_raw = event.get("timestamp")
    timestamp = None
    if isinstance(timestamp_raw, datetime):
        timestamp = timestamp_raw
    elif isinstance(timestamp_raw, str):
        try:
            timestamp = datetime.fromisoformat(timestamp_raw)
        except ValueError:
            timestamp = None
    if timestamp is None:
        timestamp = datetime.utcnow()

    payload = dict(event.get("data", {}))
    event_type = str(event.get("type") or "unknown")
    sequence = int(event.get("sequence") or 0)

    # Validate sequence number
    if sequence < 0:
        logger.warning(
            f"Invalid negative sequence number {sequence} for event {event_type} "
            f"in session {session_id}"
        )
        sequence = 0

    # Handle agent_start event - update resume_id
    if event_type == "agent_start":
        resume_id = payload.get("session_id")
        if isinstance(resume_id, str) and resume_id:
            try:
                session_service.update_resume_id(session_id, resume_id)
            except Exception as e:
                logger.warning(
                    f"Failed to update resume_id for {session_id}: {e}"
                )

    # Skip partial messages to reduce database writes
    if event_type == "message" and payload.get("is_partial"):
        return True  # Not an error, just skipped

    # Use full_text if available for message events
    if event_type == "message" and "full_text" in payload:
        payload["text"] = payload.get("full_text") or payload.get("text")
        payload.pop("full_text", None)

    try:
        # Use timeout to prevent hanging on database operations
        await asyncio.wait_for(
            _persist_event(session_id, sequence, event_type, payload, timestamp),
            timeout=DB_OPERATION_TIMEOUT
        )
        return True

    except asyncio.TimeoutError:
        logger.error(
            f"Timeout recording event {event_type} (seq={sequence}) "
            f"for session {session_id}"
        )
        return False

    except IntegrityError as e:
        # Duplicate sequence number - log but don't fail
        logger.warning(
            f"Duplicate event sequence {sequence} for session {session_id}: {e}"
        )
        return False

    except Exception as e:
        logger.error(
            f"Failed to record event {event_type} (seq={sequence}) "
            f"for session {session_id}: {e}"
        )
        return False


@with_db_retry()
async def _persist_event(
    session_id: str,
    sequence: int,
    event_type: str,
    payload: dict[str, Any],
    timestamp: datetime,
) -> None:
    """
    Internal function to persist event with retry logic.

    Args:
        session_id: The session ID.
        sequence: Event sequence number.
        event_type: Type of event.
        payload: Event data payload.
        timestamp: Event timestamp.
    """
    # Serialize payload with error handling
    try:
        data_json = json.dumps(payload, default=str)
    except (TypeError, ValueError) as e:
        logger.warning(
            f"Failed to serialize event payload for {event_type}: {e}. "
            "Storing error placeholder."
        )
        data_json = json.dumps({
            "error": "Failed to serialize payload",
            "original_type": event_type,
        })

    # Scan serialized payload for sensitive data before persisting
    if is_scanner_enabled():
        try:
            scan_result = scan_and_redact(data_json)
            if scan_result.has_secrets:
                data_json = scan_result.redacted_text
                logger.warning(
                    f"Redacted {scan_result.secret_count} secrets from {event_type} "
                    f"event in session {session_id}"
                )
        except Exception as e:
            logger.warning(f"Failed to scan event payload: {e}")

    async with AsyncSessionLocal() as db:
        db_event = Event(
            session_id=session_id,
            sequence=sequence,
            event_type=event_type,
            data=data_json,
            timestamp=timestamp,
        )
        db.add(db_event)
        await db.commit()


@with_db_retry()
async def list_events(
    session_id: str,
    after_sequence: Optional[int] = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """
    List persisted events for a session in sequence order.

    Args:
        session_id: The session ID.
        after_sequence: Only return events after this sequence number.
        limit: Maximum number of events to return.

    Returns:
        List of event dictionaries.
    """
    try:
        result = await asyncio.wait_for(
            _fetch_events(session_id, after_sequence, limit),
            timeout=DB_OPERATION_TIMEOUT
        )
        return result

    except asyncio.TimeoutError:
        logger.error(f"Timeout listing events for session {session_id}")
        return []

    except Exception as e:
        logger.error(f"Failed to list events for session {session_id}: {e}")
        return []


async def _fetch_events(
    session_id: str,
    after_sequence: Optional[int],
    limit: int,
) -> list[dict[str, Any]]:
    """Internal function to fetch events with retry logic."""
    async with AsyncSessionLocal() as db:
        query = select(Event).where(Event.session_id == session_id)
        if after_sequence is not None:
            query = query.where(Event.sequence > after_sequence)
        query = query.order_by(Event.sequence.asc()).limit(limit)
        result = await db.execute(query)
        events = list(result.scalars().all())

    return [
        {
            "type": event.event_type,
            "data": _safe_json_loads(event.data),
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            "sequence": event.sequence,
            "session_id": event.session_id,
        }
        for event in events
    ]


def _safe_json_loads(data: Optional[str]) -> dict[str, Any]:
    """
    Safely parse JSON data with error handling.

    Args:
        data: JSON string to parse.

    Returns:
        Parsed dictionary, or empty dict on error.
    """
    if not data:
        return {}
    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse event data: {e}")
        return {"_parse_error": str(e)}


@with_db_retry()
async def get_last_sequence(session_id: str) -> int:
    """
    Get the latest sequence number for a session.

    Args:
        session_id: The session ID.

    Returns:
        Latest sequence number, or 0 if no events.
    """
    try:
        result = await asyncio.wait_for(
            _fetch_last_sequence(session_id),
            timeout=DB_OPERATION_TIMEOUT
        )
        return result

    except asyncio.TimeoutError:
        logger.error(f"Timeout getting last sequence for session {session_id}")
        return 0

    except Exception as e:
        logger.warning(f"Failed to get last sequence for {session_id}: {e}")
        return 0


async def _fetch_last_sequence(session_id: str) -> int:
    """Internal function to fetch last sequence."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.max(Event.sequence)).where(Event.session_id == session_id)
        )
        last = result.scalar_one_or_none()
        return int(last or 0)


@with_db_retry()
async def get_latest_terminal_status(session_id: str) -> Optional[str]:
    """
    Return the latest terminal status from persisted events, if any.

    Args:
        session_id: The session ID.

    Returns:
        Terminal status string, or None if no terminal event found.
    """
    try:
        result = await asyncio.wait_for(
            _fetch_terminal_status(session_id),
            timeout=DB_OPERATION_TIMEOUT
        )
        return result

    except asyncio.TimeoutError:
        logger.error(f"Timeout getting terminal status for session {session_id}")
        return None

    except Exception as e:
        logger.warning(f"Failed to get terminal status for {session_id}: {e}")
        return None


async def _fetch_terminal_status(session_id: str) -> Optional[str]:
    """Internal function to fetch terminal status."""
    async with AsyncSessionLocal() as db:
        query = (
            select(Event)
            .where(
                Event.session_id == session_id,
                Event.event_type.in_(["agent_complete", "error", "cancelled"]),
            )
            .order_by(Event.sequence.desc())
            .limit(1)
        )
        result = await db.execute(query)
        event = result.scalar_one_or_none()

    if not event:
        return None

    payload = _safe_json_loads(event.data)

    if event.event_type == "agent_complete":
        status_value = str(payload.get("status", "complete")).lower()
        if status_value == "error":
            return "failed"
        return status_value
    if event.event_type == "cancelled":
        return "cancelled"
    return "failed"


async def delete_events(session_id: str) -> int:
    """
    Delete all events for a session.

    Used for session cleanup.

    Args:
        session_id: The session ID.

    Returns:
        Number of events deleted.
    """
    try:
        result = await asyncio.wait_for(
            _delete_session_events(session_id),
            timeout=DB_OPERATION_TIMEOUT
        )
        return result

    except asyncio.TimeoutError:
        logger.error(f"Timeout deleting events for session {session_id}")
        return 0

    except Exception as e:
        logger.error(f"Failed to delete events for session {session_id}: {e}")
        return 0


@with_db_retry()
async def _delete_session_events(session_id: str) -> int:
    """Internal function to delete events."""
    from sqlalchemy import delete

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(Event).where(Event.session_id == session_id)
        )
        await db.commit()
        return result.rowcount or 0


async def get_event_count(session_id: str) -> int:
    """
    Get the total count of events for a session.

    Args:
        session_id: The session ID.

    Returns:
        Number of events.
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(func.count()).select_from(Event).where(
                    Event.session_id == session_id
                )
            )
            return result.scalar_one() or 0

    except Exception as e:
        logger.warning(f"Failed to get event count for {session_id}: {e}")
        return 0
