"""
SSH host key resolver — per-profile host key pinning via vault.

Queries VaultSecret for pinned host keys and returns asyncssh-compatible
known_hosts callables. Fail-closed: no pinned key = connection refused.

Phase 1: strict mode only. No TOFU, no CA-cert verification.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ...services.vault_service import VaultService
    from .ssh_config import SSHProfile

logger = logging.getLogger(__name__)

HOST_KEY_SECRET_TYPE = "ssh_host_key"


class SSHHostKeyResolver:
    """Resolves pinned host keys from vault for asyncssh known_hosts.

    Each SSH profile can have a host key pinned in the vault as a
    VaultSecret with secret_type='ssh_host_key' and name=profile.name.
    The resolver returns an asyncssh-compatible callable that performs
    cryptographic verification against the pinned key.
    """

    def __init__(self, vault_service: "VaultService") -> None:
        self._vault = vault_service

    async def resolve(
        self,
        profile: "SSHProfile",
        user_id: str,
        session_id: str,
        db: "AsyncSession",
    ) -> Callable[..., Any]:
        """Resolve known_hosts callable for a profile.

        Returns an asyncssh-compatible known_hosts callable:
        - If pinned key found: callable returns ([key], [], [])
        - If no key or parse error: callable returns ([], [], []) — reject

        The 3-tuple format is (trusted_keys, ca_keys, revoked_keys).
        asyncssh pads to 7-tuple internally for backward compat.
        """
        pinned_key_str = await self._load_pinned_key(
            profile.name, user_id, session_id, db
        )

        if pinned_key_str is None:
            logger.warning(
                "No pinned host key for SSH profile '%s' (user=%s). "
                "Connection will be rejected (fail-closed).",
                profile.name, user_id,
            )
            return self._make_reject_callable()

        try:
            import asyncssh
            public_key = asyncssh.import_public_key(pinned_key_str)
        except Exception as exc:
            logger.error(
                "Failed to parse pinned host key for profile '%s' "
                "(user=%s): %s. Connection will be rejected (fail-closed).",
                profile.name, user_id, exc,
            )
            return self._make_reject_callable()

        logger.debug(
            "Resolved pinned host key for profile '%s' (user=%s)",
            profile.name, user_id,
        )

        def known_hosts_callable(
            _host: str, _addr: str, _port: int
        ) -> tuple[list, list, list]:
            return ([public_key], [], [])

        return known_hosts_callable

    async def _load_pinned_key(
        self,
        profile_name: str,
        user_id: str,
        session_id: str,
        db: "AsyncSession",
    ) -> Optional[str]:
        """Load pinned host key from vault.

        Queries VaultSecret for (user_id, secret_type='ssh_host_key',
        name=profile_name, is_active=True).

        Returns the decrypted public key string, or None if not found.
        """
        from sqlalchemy import select
        from ...db.models import VaultSecret

        result = await db.execute(
            select(VaultSecret).where(
                VaultSecret.user_id == user_id,
                VaultSecret.secret_type == HOST_KEY_SECRET_TYPE,
                VaultSecret.name == profile_name,
                VaultSecret.is_active.is_(True),
            )
        )
        secret = result.scalar_one_or_none()

        if secret is None:
            return None

        try:
            return await self._vault.get_secret_value(
                db, user_id, secret.id, session_id
            )
        except ValueError as exc:
            logger.error(
                "Failed to decrypt pinned host key for profile '%s' "
                "(user=%s): %s",
                profile_name, user_id, exc,
            )
            return None

    @staticmethod
    def _make_reject_callable() -> Callable[..., tuple[list, list, list]]:
        """Return a known_hosts callable that rejects all host keys.

        asyncssh interprets an empty trusted_keys list as
        'no trusted keys' and refuses the connection.
        """
        def reject(
            _host: str, _addr: str, _port: int
        ) -> tuple[list, list, list]:
            return ([], [], [])

        return reject
