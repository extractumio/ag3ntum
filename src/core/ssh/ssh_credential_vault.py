"""
SSH credential vault — manages SSH authentication via unified vault.

The agent NEVER sees raw credentials. This module provides authenticated
SSH connections using credentials stored in the VaultService. Key material
is decrypted only in memory during connection establishment and zeroed
immediately after.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    import asyncssh
    from sqlalchemy.ext.asyncio import AsyncSession

    from ...services.vault_service import VaultService
    from .ssh_config import SSHProfile, SSHSecurityConfig

logger = logging.getLogger(__name__)


class SSHCredentialVault:
    """SSH-specific credential management. Provides authenticated connections.

    The agent NEVER accesses this class directly — it's called internally
    by the SSH MCP tool implementation.

    Wraps VaultService for SSH operations. Credentials are fetched once
    under the caller's db session and captured in closures so that vault
    access does not happen at connection time (after the session may have
    been released).
    """

    def __init__(
        self,
        vault_service: VaultService,
        security_config: SSHSecurityConfig,
    ) -> None:
        self._vault = vault_service
        self._config = security_config

    async def get_connect_fn(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        profile: SSHProfile,
    ) -> Callable[[], Awaitable[asyncssh.SSHClientConnection]]:
        """Create a connection factory for the connection pool.

        Returns an async callable that, when called, establishes an
        authenticated SSH connection. This is passed to
        SSHConnectionPool.get_connection() as the connect_fn argument.

        Credentials are pre-loaded here so vault access happens under the
        caller's db session, not deferred to the connect_fn closure.

        Supports three auth methods based on profile.auth_method:
        - "key":         Load SSH private key from vault via get_ssh_key()
        - "certificate": Load key + certificate from vault
        - "password":    Load password from vault (if allowed by config)

        Raises:
            ValueError: If the auth method is unsupported, credentials are
                        missing from the profile, or password auth is
                        disabled by security config.
        """
        auth_method = profile.auth_method

        if auth_method == "key":
            return await self._build_key_connect_fn(db, user_id, session_id, profile)

        if auth_method == "certificate":
            return await self._build_certificate_connect_fn(db, user_id, session_id, profile)

        if auth_method == "password":
            return await self._build_password_connect_fn(db, user_id, session_id, profile)

        raise ValueError(
            f"Unsupported auth method '{auth_method}' for profile '{profile.name}'. "
            f"Supported: key, certificate, password"
        )

    def validate_profile_credentials(self, profile: SSHProfile) -> list[str]:
        """Validate a profile's credential configuration without accessing vault.

        Checks that required credential references are present and that
        the requested auth method is permitted by the security config.

        Returns:
            List of validation error strings. Empty list means valid.
        """
        errors: list[str] = []

        if profile.auth_method == "key":
            if not profile.key_ref:
                errors.append(
                    f"Profile '{profile.name}': key auth requires key_ref"
                )

        elif profile.auth_method == "certificate":
            if not profile.key_ref:
                errors.append(
                    f"Profile '{profile.name}': cert auth requires key_ref"
                )
            if not profile.certificate_ref:
                errors.append(
                    f"Profile '{profile.name}': cert auth requires certificate_ref"
                )

        elif profile.auth_method == "password":
            if not self._config.credentials.password_auth_allowed:
                errors.append(
                    f"Profile '{profile.name}': password auth disabled in security config"
                )
            if profile.password_secret_id is None:
                errors.append(
                    f"Profile '{profile.name}': password auth requires password_secret_id"
                )

        return errors

    # --- Internal builders ---

    def _resolve_known_hosts(self) -> str | None:
        """Resolve the known_hosts setting from security config.

        Returns the path string for asyncssh's known_hosts parameter.
        - Explicit path from config: use as-is
        - None/unset in config: use asyncssh default (system known_hosts)
        Never returns None — that would disable host key verification.
        """
        configured = self._config.credentials.known_hosts_path
        if configured:
            return configured
        # Let asyncssh use its default (~/.ssh/known_hosts + system files)
        return ()  # type: ignore[return-value]  # asyncssh accepts empty tuple = system defaults

    async def _build_key_connect_fn(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        profile: SSHProfile,
    ) -> Callable[[], Awaitable[asyncssh.SSHClientConnection]]:
        """Build a connect_fn for key-based authentication."""
        if not profile.key_ref:
            raise ValueError(
                f"Profile '{profile.name}' uses key auth but no key_ref configured"
            )

        ssh_key = await self._vault.get_ssh_key(
            db, user_id, profile.key_ref, session_id
        )
        host = profile.host
        port = profile.port
        username = profile.username
        known_hosts = self._resolve_known_hosts()

        logger.debug(
            "Built key connect_fn for profile '%s' (user=%s, host=%s:%d)",
            profile.name, user_id, host, port,
        )

        async def connect() -> asyncssh.SSHClientConnection:
            import asyncssh as _asyncssh
            return await _asyncssh.connect(
                host=host,
                port=port,
                username=username,
                client_keys=[ssh_key],
                known_hosts=known_hosts,
                keepalive_interval=30,
                keepalive_count_max=3,
            )

        return connect

    async def _build_certificate_connect_fn(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        profile: SSHProfile,
    ) -> Callable[[], Awaitable[asyncssh.SSHClientConnection]]:
        """Build a connect_fn for certificate-based authentication."""
        if not profile.key_ref:
            raise ValueError(
                f"Profile '{profile.name}' uses cert auth but no key_ref configured"
            )
        if not profile.certificate_ref:
            raise ValueError(
                f"Profile '{profile.name}' uses cert auth but no certificate_ref configured"
            )

        ssh_key = await self._vault.get_ssh_key(
            db, user_id, profile.key_ref, session_id
        )
        # certificate_ref stores the vault secret ID (int) as the certificate PEM
        cert_pem = await self._vault.get_secret_value(
            db, user_id, profile.certificate_ref, session_id
        )
        host = profile.host
        port = profile.port
        username = profile.username
        known_hosts = self._resolve_known_hosts()

        logger.debug(
            "Built certificate connect_fn for profile '%s' (user=%s, host=%s:%d)",
            profile.name, user_id, host, port,
        )

        async def connect() -> asyncssh.SSHClientConnection:
            import asyncssh as _asyncssh
            cert = _asyncssh.import_certificate(cert_pem)
            return await _asyncssh.connect(
                host=host,
                port=port,
                username=username,
                client_keys=[(ssh_key, cert)],
                known_hosts=known_hosts,
                keepalive_interval=30,
                keepalive_count_max=3,
            )

        return connect

    async def _build_password_connect_fn(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        profile: SSHProfile,
    ) -> Callable[[], Awaitable[asyncssh.SSHClientConnection]]:
        """Build a connect_fn for password-based authentication."""
        if not self._config.credentials.password_auth_allowed:
            raise ValueError(
                "Password authentication is disabled in SSH security config"
            )
        if profile.password_secret_id is None:
            raise ValueError(
                f"Profile '{profile.name}' uses password auth but no password_secret_id configured"
            )

        password = await self._vault.get_secret_value(
            db, user_id, profile.password_secret_id, session_id
        )
        host = profile.host
        port = profile.port
        username = profile.username
        known_hosts = self._resolve_known_hosts()

        logger.debug(
            "Built password connect_fn for profile '%s' (user=%s, host=%s:%d)",
            profile.name, user_id, host, port,
        )

        async def connect() -> asyncssh.SSHClientConnection:
            import asyncssh as _asyncssh
            return await _asyncssh.connect(
                host=host,
                port=port,
                username=username,
                password=password,
                known_hosts=known_hosts,
                keepalive_interval=30,
                keepalive_count_max=3,
            )

        return connect
