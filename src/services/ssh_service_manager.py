"""
SSH Service Manager — singleton managing shared SSH infrastructure.

Initialised once at API startup. Always ready at platform level.
Per-user SSH access is controlled by feature flags (admin toggle).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from ..core.ssh.ssh_config import (
    SSHProfile,
    SSHSecurityConfig,
    _LEGACY_SSH_SECURITY_CONFIG_PATH,
    get_default_ssh_security_config,
)
from ..core.ssh.ssh_connection_pool import SSHConnectionPool
from ..core.ssh.ssh_command_filter import SSHCommandFilter
from .redis_client import LazyRedisClient

if TYPE_CHECKING:
    from ..services.vault_service import VaultService

logger = logging.getLogger(__name__)

# Redis cache key template and TTL for per-user SSH feature check
_SSH_FEATURE_CACHE_KEY = "feature:ssh_enabled:{user_id}"
_SSH_FEATURE_CACHE_TTL = 30  # seconds

_redis = LazyRedisClient()


class SSHServiceManager:
    """Manages shared SSH infrastructure across all sessions.

    Lifecycle:
      API startup  → initialize()  (always succeeds — hardcoded defaults)
      Per-session   → build_session_context()
      Session end   → cleanup_session()
      API shutdown  → shutdown()
    """

    def __init__(self) -> None:
        self._pool: Optional[SSHConnectionPool] = None
        self._security_config = get_default_ssh_security_config()
        self._command_filter: Optional[SSHCommandFilter] = None
        self._initialized: bool = False

    @property
    def enabled(self) -> bool:
        """Platform-level SSH readiness — always True after initialize()."""
        return self._initialized

    @property
    def security_config(self) -> SSHSecurityConfig:
        return self._security_config

    async def initialize(self) -> None:
        """Create shared services. Called once at API startup.

        Always succeeds — no YAML dependency. SSH infrastructure is
        always ready; per-user access is gated by feature flags.
        """
        # Warn about legacy YAML config if it exists
        if _LEGACY_SSH_SECURITY_CONFIG_PATH.exists():
            logger.warning(
                "ssh-security.yaml found at %s but is no longer used. "
                "SSH is now managed per-user via admin panel feature flags.",
                _LEGACY_SSH_SECURITY_CONFIG_PATH,
            )

        self._pool = SSHConnectionPool(
            idle_timeout_seconds=self._security_config.limits.session_timeout_seconds,
            max_connections_per_session=self._security_config.limits.max_connections_per_user,
        )
        self._command_filter = SSHCommandFilter()
        self._initialized = True
        logger.info("SSH agent integration ready (per-user access via feature flags)")

    async def is_user_ssh_enabled(self, user_id: str) -> bool:
        """Check if SSH is enabled for a user via feature flags.

        Uses Redis cache with 30s TTL to avoid DB queries on every call.
        Falls back to direct DB query if Redis is unavailable.
        """
        cache_key = _SSH_FEATURE_CACHE_KEY.format(user_id=user_id)

        # Try Redis cache first
        client = None
        try:
            client = _redis.get()
            cached = await client.get(cache_key)
            if cached is not None:
                return cached == "1"
        except Exception:
            client = None  # Redis unavailable — fall through to DB

        # Query feature flags from DB
        enabled = await self._resolve_user_ssh_flag(user_id)

        # Cache the result (reuse client from above)
        if client is not None:
            try:
                await client.set(
                    cache_key,
                    "1" if enabled else "0",
                    ex=_SSH_FEATURE_CACHE_TTL,
                )
            except Exception:
                pass  # Cache write failure is non-fatal

        return enabled

    @staticmethod
    async def _resolve_user_ssh_flag(user_id: str) -> bool:
        """Resolve ssh_enabled for a user through the 3-tier feature flag system."""
        from ..db.database import AsyncSessionLocal
        from ..db.models import User, Reseller
        from ..services.feature_flag_service import feature_flag_service
        from sqlalchemy import select

        try:
            async with AsyncSessionLocal() as db:
                await feature_flag_service.ensure_loaded(db)

                result = await db.execute(
                    select(User).where(User.id == user_id)
                )
                user = result.scalar_one_or_none()
                if not user:
                    return False

                reseller = None
                if user.reseller_id:
                    res_result = await db.execute(
                        select(Reseller).where(Reseller.id == user.reseller_id)
                    )
                    reseller = res_result.scalar_one_or_none()

                features = feature_flag_service.get_user_effective_features(
                    user, reseller
                )
                return bool(features.get("ssh_enabled", False))
        except Exception as e:
            logger.warning("Failed to resolve SSH feature flag for user %s: %s", user_id, e)
            return False  # Fail-closed

    @staticmethod
    async def invalidate_ssh_cache(user_id: str) -> None:
        """Invalidate the Redis cache for a user's SSH feature flag.

        Called when admin toggles SSH for a user.
        """
        cache_key = _SSH_FEATURE_CACHE_KEY.format(user_id=user_id)
        try:
            client = _redis.get()
            await client.delete(cache_key)
        except Exception:
            pass  # Non-fatal

    async def build_session_context(
        self,
        session_id: str,
        user_id: str,
        profiles: dict[str, SSHProfile],
        db_session_factory: Any,
        vault_service: VaultService,
    ) -> Optional[Any]:
        """Build per-session SSH context. Returns None if not ready or no profiles.

        The returned object is an SSHToolContext dataclass from
        tools.ag3ntum.ag3ntum_ssh.tool — imported lazily to avoid
        circular dependencies at module level.
        """
        if (
            not self._initialized
            or not profiles
            or self._pool is None
            or self._command_filter is None
        ):
            return None

        # Lazy imports to avoid circular dependencies
        from tools.ag3ntum.ag3ntum_ssh.tool import (
            SSHApprovalStore,
            SSHToolContext,
            WriteTracker,
            WriteBudget,
        )
        from ..core.ssh.ssh_credential_vault import SSHCredentialVault
        from ..core.ssh.ssh_host_key_resolver import SSHHostKeyResolver
        from ..services.ssh_audit_service import ssh_audit_service

        host_key_resolver = SSHHostKeyResolver(vault_service=vault_service)
        credential_vault = SSHCredentialVault(
            vault_service=vault_service,
            security_config=self._security_config,
            host_key_resolver=host_key_resolver,
        )
        approval_store = SSHApprovalStore()
        write_tracker = WriteTracker()
        write_budget = WriteBudget()

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
            audit_service=ssh_audit_service,
            profiles=profiles,
            db_session_factory=db_session_factory,
            approval_store=approval_store,
            command_semaphore=asyncio.Semaphore(
                sec_config.limits.max_concurrent_commands,
            ),
            ssh_enabled_check=self.is_user_ssh_enabled,
            write_tracker=write_tracker,
            write_budget=write_budget,
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
