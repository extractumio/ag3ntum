"""
SSH Service Manager — singleton managing shared SSH infrastructure.

Initialised once at API startup. Provides per-session SSH context building
for agent chat integration. Fail-closed: SSH disabled if config missing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from ..core.ssh.ssh_config import SSHSecurityConfig, SSHProfile, load_ssh_security_config
from ..core.ssh.ssh_connection_pool import SSHConnectionPool
from ..core.ssh.ssh_command_filter import SSHCommandFilter

if TYPE_CHECKING:
    from ..services.vault_service import VaultService

logger = logging.getLogger(__name__)


class SSHServiceManager:
    """Manages shared SSH infrastructure across all sessions.

    Lifecycle:
      API startup  → initialize()
      Per-session   → build_session_context()
      Session end   → cleanup_session()
      API shutdown  → shutdown()
    """

    def __init__(self) -> None:
        self._pool: Optional[SSHConnectionPool] = None
        self._security_config: Optional[SSHSecurityConfig] = None
        self._command_filter: Optional[SSHCommandFilter] = None
        self._enabled: bool = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def security_config(self) -> Optional[SSHSecurityConfig]:
        return self._security_config

    async def initialize(self) -> None:
        """Load config and create shared services. Called once at API startup."""
        self._security_config = load_ssh_security_config()
        if not self._security_config.enabled:
            logger.info("SSH agent integration disabled (ssh.enabled=false)")
            return

        self._enabled = True
        self._pool = SSHConnectionPool(
            idle_timeout_seconds=self._security_config.limits.session_timeout_seconds,
            max_connections_per_session=self._security_config.limits.max_connections_per_user,
        )
        self._command_filter = SSHCommandFilter()
        logger.info("SSH agent integration enabled")

    async def build_session_context(
        self,
        session_id: str,
        user_id: str,
        profiles: dict[str, SSHProfile],
        db_session_factory: Any,
        vault_service: VaultService,
    ) -> Optional[Any]:
        """Build per-session SSH context. Returns None if SSH disabled or no profiles.

        The returned object is an SSHToolContext dataclass from
        tools.ag3ntum.ag3ntum_ssh.tool — imported lazily to avoid
        circular dependencies at module level.
        """
        if (
            not self._enabled
            or not profiles
            or self._security_config is None
            or self._pool is None
            or self._command_filter is None
        ):
            return None

        # Lazy imports to avoid circular dependencies
        from tools.ag3ntum.ag3ntum_ssh.tool import (
            SSHApprovalStore,
            SSHToolContext,
        )
        from ..core.ssh.ssh_credential_vault import SSHCredentialVault
        from ..core.ssh.ssh_host_key_resolver import SSHHostKeyResolver
        from ..services.ssh_audit_service import SSHAuditService

        host_key_resolver = SSHHostKeyResolver(vault_service=vault_service)
        credential_vault = SSHCredentialVault(
            vault_service=vault_service,
            security_config=self._security_config,
            host_key_resolver=host_key_resolver,
        )
        audit_service = SSHAuditService()
        approval_store = SSHApprovalStore()

        # Local refs satisfy type narrowing (None guards above)
        pool = self._pool
        cmd_filter = self._command_filter
        sec_config = self._security_config

        return SSHToolContext(
            session_id=session_id,
            user_id=user_id,
            security_config=sec_config,
            connection_pool=pool,
            command_filter=cmd_filter,
            credential_vault=credential_vault,
            audit_service=audit_service,
            profiles=profiles,
            db_session_factory=db_session_factory,
            approval_store=approval_store,
            command_semaphore=asyncio.Semaphore(
                sec_config.limits.max_concurrent_commands,
            ),
        )

    async def cleanup_session(self, session_id: str) -> None:
        """Close all SSH connections for a session."""
        if self._pool:
            closed = await self._pool.close_session_connections(session_id)
            if closed:
                logger.info(
                    "Closed %d SSH connection(s) for session %s",
                    closed, session_id[:8],
                )

    async def shutdown(self) -> None:
        """Shutdown all SSH services. Called on API shutdown."""
        if self._pool:
            await self._pool.shutdown()
            logger.info("SSH connection pool shut down")


# Module-level singleton — initialized in API lifespan
ssh_service_manager = SSHServiceManager()
