"""
SSH connection pool with persistent connections, keepalive, and reconnection.

Manages SSH connections per-user, per-session, per-profile with:
- SSH-level keepalive (encrypted, application-layer)
- Idle timeout with asyncio watchdog timer
- Transparent reconnection on next use after disconnect
- Background health checker for zombie detection
- All connections closed on session end
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SSHConnectionLimitError(Exception):
    """Raised when connection limit per session is exceeded."""
    pass


@dataclass
class SSHCommandResult:
    """Result of an SSH command execution."""
    exit_code: int
    stdout: str
    stderr: str
    command: str
    timed_out: bool = False
    connection_lost: bool = False


@dataclass
class SSHConnectionEntry:
    """Tracks a single SSH connection with lifecycle metadata."""
    conn: Any  # asyncssh.SSHClientConnection (typed as Any to avoid import)
    profile_name: str
    host: str
    port: int
    username: str
    user_id: str
    session_id: str
    connected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_activity: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    command_count: int = 0
    relay_mode: bool = False
    privilege_level: int = 0
    _watchdog_task: Optional[asyncio.Task] = field(
        default=None, repr=False
    )


class SSHConnectionPool:
    """Manages persistent SSH connections across agent turns.

    Key properties:
    - Connections are per-user, per-session, per-profile (no sharing)
    - SSH-level keepalive via ServerAliveInterval (30s)
    - Idle timeout with watchdog timer (configurable, default 15min)
    - Transparent reconnection on next use after disconnect
    - All connections closed on session end
    - Background health checker for zombie detection
    """

    def __init__(
        self,
        idle_timeout_seconds: int = 900,  # 15 minutes
        max_connections_per_session: int = 5,
        health_check_interval_seconds: int = 60,
    ) -> None:
        self._connections: dict[str, SSHConnectionEntry] = {}
        self._idle_timeout = idle_timeout_seconds
        self._max_per_session = max_connections_per_session
        self._health_check_interval = health_check_interval_seconds
        self._health_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def _connection_key(
        self, session_id: str, profile_name: str
    ) -> str:
        """Generate unique connection key."""
        return f"{session_id}:{profile_name}"

    async def get_connection(
        self,
        session_id: str,
        profile_name: str,
        user_id: str,
        connect_fn: Any,  # async callable returning SSHClientConnection
    ) -> Any:
        """Get or create a connection with transparent reconnection.

        Args:
            session_id: The Ag3ntum session ID.
            profile_name: SSH profile name.
            user_id: The Ag3ntum user ID.
            connect_fn: Async callable that returns an authenticated
                        asyncssh.SSHClientConnection. Called on new
                        connections or reconnections.

        Returns:
            Live asyncssh.SSHClientConnection.

        Raises:
            SSHConnectionLimitError: If session has too many connections.
        """
        key = self._connection_key(session_id, profile_name)

        async with self._lock:
            entry = self._connections.get(key)

            # Check if existing connection is alive
            if entry is not None:
                if not self._is_connection_closed(entry.conn):
                    # Connection alive — reset watchdog and return
                    entry.last_activity = datetime.now(timezone.utc)
                    self._reset_watchdog(key, entry)
                    return entry.conn

                # Connection dead — clean up
                logger.info(
                    f"SSH connection dead for {profile_name}, "
                    f"will reconnect"
                )
                await self._cleanup_entry(
                    key, entry, reason="connection_lost"
                )

            # Check per-session limit
            session_count = sum(
                1 for k in self._connections
                if k.startswith(f"{session_id}:")
            )
            if session_count >= self._max_per_session:
                raise SSHConnectionLimitError(
                    f"Maximum {self._max_per_session} concurrent SSH "
                    f"connections per session"
                )

            # Connect
            logger.info(f"Establishing SSH connection for {profile_name}")
            conn = await connect_fn()

            entry = SSHConnectionEntry(
                conn=conn,
                profile_name=profile_name,
                host=getattr(conn, '_host', 'unknown'),
                port=getattr(conn, '_port', 22),
                username=getattr(conn, '_username', 'unknown'),
                user_id=user_id,
                session_id=session_id,
            )

            self._connections[key] = entry
            self._start_watchdog(key, entry)
            self._ensure_health_checker()

            logger.info(
                f"SSH connection established: {profile_name} "
                f"(session={session_id})"
            )
            return conn

    async def release_connection(
        self, session_id: str, profile_name: str,
    ) -> None:
        """Explicitly close a connection."""
        key = self._connection_key(session_id, profile_name)
        async with self._lock:
            entry = self._connections.get(key)
            if entry:
                await self._cleanup_entry(
                    key, entry, reason="explicit_close"
                )

    async def close_session_connections(
        self, session_id: str,
    ) -> int:
        """Close ALL connections for a session.

        Called when an agent session ends.

        Returns:
            Number of connections closed.
        """
        closed = 0
        async with self._lock:
            keys_to_close = [
                k for k in self._connections
                if k.startswith(f"{session_id}:")
            ]
            for key in keys_to_close:
                entry = self._connections[key]
                await self._cleanup_entry(
                    key, entry, reason="session_end"
                )
                closed += 1
        if closed:
            logger.info(
                f"Closed {closed} SSH connection(s) for session "
                f"{session_id}"
            )
        return closed

    def record_activity(
        self, session_id: str, profile_name: str,
    ) -> None:
        """Record activity on a connection (resets watchdog)."""
        key = self._connection_key(session_id, profile_name)
        entry = self._connections.get(key)
        if entry:
            entry.last_activity = datetime.now(timezone.utc)
            entry.command_count += 1

    def get_connection_info(
        self, session_id: str,
    ) -> list[dict]:
        """Get info about all connections for a session."""
        result = []
        for key, entry in self._connections.items():
            if key.startswith(f"{session_id}:"):
                now = datetime.now(timezone.utc)
                result.append({
                    "profile": entry.profile_name,
                    "host": entry.host,
                    "port": entry.port,
                    "username": entry.username,
                    "connected_at": entry.connected_at.isoformat(),
                    "last_activity": entry.last_activity.isoformat(),
                    "command_count": entry.command_count,
                    "privilege_level": entry.privilege_level,
                    "relay_mode": entry.relay_mode,
                    "alive": not self._is_connection_closed(entry.conn),
                    "idle_seconds": int(
                        (now - entry.last_activity).total_seconds()
                    ),
                })
        return result

    @property
    def total_connections(self) -> int:
        """Total number of tracked connections."""
        return len(self._connections)

    # --- Internal helpers ---

    def _is_connection_closed(self, conn: Any) -> bool:
        """Check if an asyncssh connection is closed."""
        try:
            # asyncssh connections have a _transport attribute
            # that is None when closed, or use is_closed() if available
            if hasattr(conn, 'is_closed'):
                return conn.is_closed()
            if hasattr(conn, '_transport'):
                return conn._transport is None
            return True  # Assume closed if we can't check
        except Exception:
            return True

    def _start_watchdog(
        self, key: str, entry: SSHConnectionEntry,
    ) -> None:
        """Start idle timeout watchdog for a connection."""
        async def _watchdog() -> None:
            while True:
                await asyncio.sleep(self._idle_timeout)
                elapsed = (
                    datetime.now(timezone.utc) - entry.last_activity
                ).total_seconds()
                if elapsed >= self._idle_timeout:
                    async with self._lock:
                        if key in self._connections:
                            await self._cleanup_entry(
                                key, entry,
                                reason=f"idle_timeout_{int(elapsed)}s",
                            )
                    return

        if entry._watchdog_task is not None:
            entry._watchdog_task.cancel()
        entry._watchdog_task = asyncio.create_task(_watchdog())

    def _reset_watchdog(
        self, key: str, entry: SSHConnectionEntry,
    ) -> None:
        """Cancel and restart the watchdog timer on activity."""
        if entry._watchdog_task is not None:
            entry._watchdog_task.cancel()
        self._start_watchdog(key, entry)

    async def _cleanup_entry(
        self, key: str, entry: SSHConnectionEntry, reason: str,
    ) -> None:
        """Close connection and clean up resources."""
        if entry._watchdog_task is not None:
            entry._watchdog_task.cancel()
            entry._watchdog_task = None
        try:
            if not self._is_connection_closed(entry.conn):
                entry.conn.close()
                await asyncio.wait_for(
                    entry.conn.wait_closed(), timeout=5
                )
        except asyncio.TimeoutError:
            logger.warning(
                f"SSH connection close timed out: {entry.profile_name}"
            )
        except Exception as e:
            logger.debug(
                f"Error closing SSH connection {entry.profile_name}: {e}"
            )
        self._connections.pop(key, None)
        alive_seconds = int(
            (datetime.now(timezone.utc) - entry.connected_at)
            .total_seconds()
        )
        logger.info(
            f"SSH connection closed: {entry.profile_name} "
            f"({reason}, {entry.command_count} commands, "
            f"alive {alive_seconds}s)"
        )

    def _ensure_health_checker(self) -> None:
        """Start background health check if not running."""
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(
                self._health_check_loop()
            )

    async def _health_check_loop(self) -> None:
        """Periodically check connection health and close zombies."""
        while self._connections:
            await asyncio.sleep(self._health_check_interval)
            async with self._lock:
                for key, entry in list(self._connections.items()):
                    if self._is_connection_closed(entry.conn):
                        await self._cleanup_entry(
                            key, entry, reason="zombie_detected"
                        )
        self._health_task = None

    async def shutdown(self) -> None:
        """Close all connections and stop background tasks."""
        async with self._lock:
            for key, entry in list(self._connections.items()):
                await self._cleanup_entry(
                    key, entry, reason="pool_shutdown"
                )
        if self._health_task is not None:
            self._health_task.cancel()
            self._health_task = None
