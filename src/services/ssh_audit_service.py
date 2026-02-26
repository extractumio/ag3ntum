"""
SSH audit service — logs all SSH operations to SQLite.

NOT accessible through MCP tools. The agent cannot read or modify
audit records. All writes go directly to the SSHAuditEvent table.

Provides query methods for UI/API display of audit trails.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import SSHAuditEvent

logger = logging.getLogger(__name__)


class SSHAuditService:
    """Writes SSH audit events directly to database.
    NOT accessible through MCP tools — agent cannot read or modify audit records.
    """

    async def log_command(
        self,
        db: AsyncSession,
        session_id: str,
        user_id: str,
        ssh_profile: str,
        remote_host: str,
        remote_user: str,
        remote_port: int,
        command: str,
        exit_code: Optional[int],
        output_bytes: int,
        duration_ms: int,
        privilege_level: int,
        mode: str,
        human_approved: bool = False,
        context_isolated: bool = False,
    ) -> int:
        """Log a command execution. Returns audit event ID."""
        event = SSHAuditEvent(
            session_id=session_id,
            user_id=user_id,
            ssh_profile=ssh_profile,
            remote_host=remote_host,
            remote_user=remote_user,
            remote_port=remote_port,
            operation="exec",
            command=command,
            exit_code=exit_code,
            output_bytes=output_bytes,
            duration_ms=duration_ms,
            privilege_level=privilege_level,
            mode=mode,
            blocked=False,
            human_approved=human_approved,
            context_isolated=context_isolated,
            timestamp=datetime.now(timezone.utc),
        )
        return await self._write(db, event)

    async def log_blocked(
        self,
        db: AsyncSession,
        session_id: str,
        user_id: str,
        ssh_profile: str,
        remote_host: str,
        remote_user: str,
        remote_port: int,
        command: str,
        reason: str,
        rule: str,
        privilege_level: int,
        mode: str,
    ) -> int:
        """Log a blocked command attempt."""
        event = SSHAuditEvent(
            session_id=session_id,
            user_id=user_id,
            ssh_profile=ssh_profile,
            remote_host=remote_host,
            remote_user=remote_user,
            remote_port=remote_port,
            operation="exec",
            command=command,
            privilege_level=privilege_level,
            mode=mode,
            blocked=True,
            block_reason=reason,
            block_rule=rule,
            timestamp=datetime.now(timezone.utc),
        )
        return await self._write(db, event)

    async def log_file_access(
        self,
        db: AsyncSession,
        session_id: str,
        user_id: str,
        ssh_profile: str,
        remote_host: str,
        remote_user: str,
        remote_port: int,
        path: str,
        operation: str,
        context_isolated: bool = False,
        privilege_level: int = 0,
        mode: str = "readonly",
    ) -> int:
        """Log a file read/write/upload/download."""
        event = SSHAuditEvent(
            session_id=session_id,
            user_id=user_id,
            ssh_profile=ssh_profile,
            remote_host=remote_host,
            remote_user=remote_user,
            remote_port=remote_port,
            operation=operation,
            remote_path=path,
            context_isolated=context_isolated,
            privilege_level=privilege_level,
            mode=mode,
            timestamp=datetime.now(timezone.utc),
        )
        return await self._write(db, event)

    async def log_connection(
        self,
        db: AsyncSession,
        session_id: str,
        user_id: str,
        ssh_profile: str,
        remote_host: str,
        remote_user: str,
        remote_port: int,
        event: str,
        privilege_level: int = 0,
        mode: str = "readonly",
        duration_ms: int = 0,
    ) -> int:
        """Log connection lifecycle events."""
        audit_event = SSHAuditEvent(
            session_id=session_id,
            user_id=user_id,
            ssh_profile=ssh_profile,
            remote_host=remote_host,
            remote_user=remote_user,
            remote_port=remote_port,
            operation=event,
            privilege_level=privilege_level,
            mode=mode,
            duration_ms=duration_ms,
            timestamp=datetime.now(timezone.utc),
        )
        return await self._write(db, audit_event)

    async def log_anomaly(
        self,
        db: AsyncSession,
        session_id: str,
        user_id: str,
        ssh_profile: str,
        remote_host: str,
        remote_user: str,
        remote_port: int,
        anomaly_type: str,
        details: str,
        privilege_level: int = 0,
        mode: str = "readonly",
    ) -> int:
        """Log detected anomaly."""
        event = SSHAuditEvent(
            session_id=session_id,
            user_id=user_id,
            ssh_profile=ssh_profile,
            remote_host=remote_host,
            remote_user=remote_user,
            remote_port=remote_port,
            operation="anomaly",
            anomaly_detected=True,
            anomaly_type=anomaly_type,
            block_reason=details,
            privilege_level=privilege_level,
            mode=mode,
            timestamp=datetime.now(timezone.utc),
        )
        return await self._write(db, event)

    async def log_host_key_event(
        self,
        db: AsyncSession,
        session_id: str,
        user_id: str,
        ssh_profile: str,
        remote_host: str,
        remote_port: int,
        event_type: str,
        details: str = "",
    ) -> int:
        """Log a host key verification event.

        event_type values:
        - host_key_verified:  Pinned key matched server key
        - host_key_missing:   No pinned key found (connection rejected)
        - host_key_mismatch:  Server key does not match pinned key (MITM?)
        - host_key_pinned:    New host key was pinned to vault
        """
        blocked = event_type in ("host_key_missing", "host_key_mismatch")
        anomaly = event_type == "host_key_mismatch"

        event = SSHAuditEvent(
            session_id=session_id,
            user_id=user_id,
            ssh_profile=ssh_profile,
            remote_host=remote_host,
            remote_user="",
            remote_port=remote_port,
            operation=event_type,
            blocked=blocked,
            block_reason=details if blocked else None,
            anomaly_detected=anomaly,
            anomaly_type="host_key_change" if anomaly else None,
            timestamp=datetime.now(timezone.utc),
        )
        return await self._write(db, event)

    async def query_by_session(
        self,
        db: AsyncSession,
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list:
        """Get audit events for a session, ordered by timestamp desc."""
        try:
            query = (
                select(SSHAuditEvent)
                .where(SSHAuditEvent.session_id == session_id)
                .order_by(SSHAuditEvent.timestamp.desc())
                .limit(limit)
                .offset(offset)
            )
            result = await db.execute(query)
            return [self._to_dict(e) for e in result.scalars().all()]
        except Exception as e:
            logger.error("Failed to query SSH audit events for session %s: %s", session_id, e)
            return []

    async def query_by_host(
        self,
        db: AsyncSession,
        user_id: str,
        host: str,
        hours: int = 24,
    ) -> list:
        """Get recent events for a specific host."""
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
            query = (
                select(SSHAuditEvent)
                .where(
                    SSHAuditEvent.user_id == user_id,
                    SSHAuditEvent.remote_host == host,
                    SSHAuditEvent.timestamp >= since,
                )
                .order_by(SSHAuditEvent.timestamp.desc())
            )
            result = await db.execute(query)
            return [self._to_dict(e) for e in result.scalars().all()]
        except Exception as e:
            logger.error("Failed to query SSH audit events for host %s: %s", host, e)
            return []

    async def query_blocked(
        self,
        db: AsyncSession,
        user_id: str,
        hours: int = 24,
    ) -> list:
        """Get blocked command attempts."""
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
            query = (
                select(SSHAuditEvent)
                .where(
                    SSHAuditEvent.user_id == user_id,
                    SSHAuditEvent.blocked.is_(True),
                    SSHAuditEvent.timestamp >= since,
                )
                .order_by(SSHAuditEvent.timestamp.desc())
            )
            result = await db.execute(query)
            return [self._to_dict(e) for e in result.scalars().all()]
        except Exception as e:
            logger.error("Failed to query blocked SSH audit events for user %s: %s", user_id, e)
            return []

    async def get_stats(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> dict:
        """Get aggregate statistics: total_commands, total_blocked,
        unique_hosts, total_file_accesses, total_anomalies."""
        try:
            r = await db.execute(
                select(func.count(SSHAuditEvent.id)).where(
                    SSHAuditEvent.user_id == user_id,
                    SSHAuditEvent.operation == "exec",
                    SSHAuditEvent.blocked.is_(False),
                )
            )
            total_commands = r.scalar_one() or 0

            r = await db.execute(
                select(func.count(SSHAuditEvent.id)).where(
                    SSHAuditEvent.user_id == user_id,
                    SSHAuditEvent.blocked.is_(True),
                )
            )
            total_blocked = r.scalar_one() or 0

            r = await db.execute(
                select(func.count()).select_from(
                    select(SSHAuditEvent.remote_host)
                    .where(SSHAuditEvent.user_id == user_id)
                    .distinct()
                    .subquery()
                )
            )
            unique_hosts = r.scalar_one() or 0

            file_ops = ("read_file", "write_file", "upload", "download")
            r = await db.execute(
                select(func.count(SSHAuditEvent.id)).where(
                    SSHAuditEvent.user_id == user_id,
                    SSHAuditEvent.operation.in_(file_ops),
                )
            )
            total_file_accesses = r.scalar_one() or 0

            r = await db.execute(
                select(func.count(SSHAuditEvent.id)).where(
                    SSHAuditEvent.user_id == user_id,
                    SSHAuditEvent.anomaly_detected.is_(True),
                )
            )
            total_anomalies = r.scalar_one() or 0

            return {
                "total_commands": total_commands,
                "total_blocked": total_blocked,
                "unique_hosts": unique_hosts,
                "total_file_accesses": total_file_accesses,
                "total_anomalies": total_anomalies,
            }
        except Exception as e:
            logger.error("Failed to get SSH audit stats for user %s: %s", user_id, e)
            return {
                "total_commands": 0,
                "total_blocked": 0,
                "unique_hosts": 0,
                "total_file_accesses": 0,
                "total_anomalies": 0,
            }

    async def _write(self, db: AsyncSession, event: SSHAuditEvent) -> int:
        """Write audit event to database. Returns event ID, 0 on failure.
        Resilient — logs errors without propagating to avoid failing SSH operations."""
        try:
            db.add(event)
            await db.commit()
            await db.refresh(event)
            return event.id
        except Exception as e:
            logger.error("Failed to write SSH audit event (op=%s): %s", event.operation, e)
            try:
                await db.rollback()
            except Exception:
                pass
            return 0

    @staticmethod
    def _to_dict(event: SSHAuditEvent) -> dict:
        """Convert SSHAuditEvent to dictionary."""
        return {
            "id": event.id,
            "session_id": event.session_id,
            "user_id": event.user_id,
            "ssh_profile": event.ssh_profile,
            "remote_host": event.remote_host,
            "remote_user": event.remote_user,
            "remote_port": event.remote_port,
            "operation": event.operation,
            "privilege_level": event.privilege_level,
            "command": event.command,
            "remote_path": event.remote_path,
            "exit_code": event.exit_code,
            "output_bytes": event.output_bytes,
            "duration_ms": event.duration_ms,
            "mode": event.mode,
            "blocked": event.blocked,
            "block_reason": event.block_reason,
            "block_rule": event.block_rule,
            "human_approved": event.human_approved,
            "context_isolated": event.context_isolated,
            "anomaly_detected": event.anomaly_detected,
            "anomaly_type": event.anomaly_type,
            "relay_used": event.relay_used,
            "relay_audit_id": event.relay_audit_id,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        }


ssh_audit_service = SSHAuditService()
