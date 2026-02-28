"""
Unified credential vault service.

Manages all user secrets (SSH keys, API tokens, database URLs, etc.)
with per-user encryption and full audit trail. The agent NEVER sees
plaintext secret values — only references by name.

Evolves the existing Token model and EncryptionService into a
comprehensive vault with backward-compatible sandboxed_envs support.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User, VaultAuditLog, VaultSecret
from .vault_encryption import VaultEncryption

if TYPE_CHECKING:
    import asyncssh

logger = logging.getLogger(__name__)


class VaultService:
    """Unified credential management. Agent never accesses directly.

    All operations:
    1. Look up user's jwt_secret for key derivation
    2. Derive per-user encryption key
    3. Perform operation
    4. Log audit event
    """

    def __init__(self, vault_encryption: VaultEncryption) -> None:
        self._encryption = vault_encryption

    async def store_secret(
        self,
        db: AsyncSession,
        user_id: str,
        secret_type: str,
        name: str,
        plaintext_value: str,
        env_var_name: Optional[str] = None,
        description: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        ssh_key_type: Optional[str] = None,
    ) -> VaultSecret:
        """Encrypt and store a secret. Logs CREATE audit event."""
        user_key = await self._get_user_key(db, user_id)
        encrypted_value = self._encryption.encrypt(plaintext_value, user_key)

        secret = VaultSecret(
            user_id=user_id,
            secret_type=secret_type,
            name=name,
            encrypted_value=encrypted_value,
            env_var_name=env_var_name,
            description=description,
            expires_at=expires_at,
            ssh_key_type=ssh_key_type,
            is_active=True,
        )
        db.add(secret)
        await db.flush()  # populate secret.id before audit log

        await self._log_audit(
            db, user_id, "CREATE", "SUCCESS",
            vault_secret_id=secret.id,
        )
        await db.commit()
        await db.refresh(secret)
        logger.info("Stored secret '%s' (type=%s) for user %s", name, secret_type, user_id)
        return secret

    async def get_secret_value(
        self,
        db: AsyncSession,
        user_id: str,
        secret_id: int,
        session_id: str,
    ) -> str:
        """Decrypt and return secret value.

        Checks: is_active, expires_at.
        Updates: last_accessed_at, last_accessed_session_id.
        Logs: DECRYPT audit event.
        Raises: ValueError if inactive/expired/not found.
        """
        result = await db.execute(
            select(VaultSecret).where(
                VaultSecret.id == secret_id,
                VaultSecret.user_id == user_id,
            )
        )
        secret = result.scalar_one_or_none()

        if secret is None:
            await self._log_audit(
                db, user_id, "DECRYPT", "FAILED",
                vault_secret_id=secret_id,
                session_id=session_id,
                reason="secret not found",
            )
            await db.commit()
            raise ValueError(f"Secret {secret_id} not found for user {user_id}")

        if not secret.is_active:
            await self._log_audit(
                db, user_id, "DECRYPT", "DENIED",
                vault_secret_id=secret_id,
                session_id=session_id,
                reason="secret is inactive",
            )
            await db.commit()
            raise ValueError(f"Secret {secret_id} is inactive")

        if secret.expires_at is not None:
            now = datetime.now(timezone.utc)
            expires = secret.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                await self._log_audit(
                    db, user_id, "DECRYPT", "DENIED",
                    vault_secret_id=secret_id,
                    session_id=session_id,
                    reason="secret has expired",
                )
                await db.commit()
                raise ValueError(f"Secret {secret_id} has expired")

        user_key = await self._get_user_key(db, user_id)
        plaintext = self._encryption.decrypt(secret.encrypted_value, user_key)

        secret.last_accessed_at = datetime.now(timezone.utc)
        secret.last_accessed_session_id = session_id
        await self._log_audit(
            db, user_id, "DECRYPT", "SUCCESS",
            vault_secret_id=secret_id,
            session_id=session_id,
        )
        await db.commit()
        return plaintext

    async def get_ssh_key(
        self,
        db: AsyncSession,
        user_id: str,
        key_name: str,
        session_id: str,
    ) -> "asyncssh.SSHKey":
        """Decrypt SSH key and return AsyncSSH key object.

        Looks up by name + secret_type='ssh_private_key'.
        Decrypted bytes never leave this method as a string.
        Uses asyncssh.import_private_key() to convert.
        """
        import asyncssh  # lazy import

        result = await db.execute(
            select(VaultSecret).where(
                VaultSecret.user_id == user_id,
                VaultSecret.name == key_name,
                VaultSecret.secret_type == "ssh_private_key",
                VaultSecret.is_active.is_(True),
            )
        )
        secret = result.scalar_one_or_none()

        if secret is None:
            await self._log_audit(
                db, user_id, "DECRYPT", "FAILED",
                session_id=session_id,
                reason=f"SSH key '{key_name}' not found",
            )
            await db.commit()
            raise ValueError(f"SSH key '{key_name}' not found for user {user_id}")

        user_key = await self._get_user_key(db, user_id)
        plaintext = self._encryption.decrypt(secret.encrypted_value, user_key)

        try:
            ssh_key = asyncssh.import_private_key(plaintext)
        except Exception as exc:
            await self._log_audit(
                db, user_id, "DECRYPT", "FAILED",
                vault_secret_id=secret.id,
                session_id=session_id,
                reason=f"failed to parse SSH key: {exc}",
            )
            await db.commit()
            raise ValueError(f"Failed to parse SSH key '{key_name}': {exc}") from exc
        finally:
            # Zero out plaintext from memory (best-effort)
            del plaintext

        secret.last_accessed_at = datetime.now(timezone.utc)
        secret.last_accessed_session_id = session_id
        await self._log_audit(
            db, user_id, "DECRYPT", "SUCCESS",
            vault_secret_id=secret.id,
            session_id=session_id,
        )
        await db.commit()
        logger.info("Loaded SSH key '%s' for user %s", key_name, user_id)
        return ssh_key

    async def inject_env_vars(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
    ) -> dict[str, str]:
        """Get all active secrets with env_var_name for sandbox injection.

        Returns {env_var_name: decrypted_value}.
        Logs INJECT audit events.
        """
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(VaultSecret).where(
                VaultSecret.user_id == user_id,
                VaultSecret.is_active.is_(True),
                VaultSecret.env_var_name.is_not(None),
            )
        )
        secrets = result.scalars().all()

        user_key = await self._get_user_key(db, user_id)
        env_vars: dict[str, str] = {}

        for secret in secrets:
            if secret.expires_at is not None:
                expires = secret.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if now > expires:
                    logger.warning(
                        "Skipping expired secret '%s' (id=%s) during env injection",
                        secret.name, secret.id,
                    )
                    continue

            try:
                plaintext = self._encryption.decrypt(secret.encrypted_value, user_key)
                env_vars[secret.env_var_name] = plaintext  # type: ignore[index]
                await self._log_audit(
                    db, user_id, "INJECT", "SUCCESS",
                    vault_secret_id=secret.id,
                    session_id=session_id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to decrypt secret '%s' (id=%s) for env injection: %s",
                    secret.name, secret.id, exc,
                )
                await self._log_audit(
                    db, user_id, "INJECT", "FAILED",
                    vault_secret_id=secret.id,
                    session_id=session_id,
                    reason=str(exc),
                )

        await db.commit()
        logger.info(
            "Injected %d env vars for user %s (session=%s)",
            len(env_vars), user_id, session_id,
        )
        return env_vars

    async def list_secrets(
        self,
        db: AsyncSession,
        user_id: str,
        secret_type: Optional[str] = None,
    ) -> list[dict]:
        """List metadata only — NEVER returns plaintext values.

        Returns: list of dicts with id, name, secret_type, description,
                 is_active, expires_at, created_at, last_accessed_at.
        """
        query = select(VaultSecret).where(VaultSecret.user_id == user_id)
        if secret_type is not None:
            query = query.where(VaultSecret.secret_type == secret_type)
        query = query.order_by(VaultSecret.created_at.desc())

        result = await db.execute(query)
        secrets = result.scalars().all()

        return [
            {
                "id": s.id,
                "name": s.name,
                "secret_type": s.secret_type,
                "description": s.description,
                "env_var_name": s.env_var_name,
                "ssh_key_type": s.ssh_key_type,
                "is_active": s.is_active,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "last_accessed_at": (
                    s.last_accessed_at.isoformat() if s.last_accessed_at else None
                ),
            }
            for s in secrets
        ]

    async def rotate_secret(
        self,
        db: AsyncSession,
        user_id: str,
        secret_id: int,
        new_plaintext: str,
    ) -> VaultSecret:
        """Store new value, update rotated_at. Logs ROTATE audit."""
        result = await db.execute(
            select(VaultSecret).where(
                VaultSecret.id == secret_id,
                VaultSecret.user_id == user_id,
            )
        )
        secret = result.scalar_one_or_none()

        if secret is None:
            await self._log_audit(
                db, user_id, "ROTATE", "FAILED",
                vault_secret_id=secret_id,
                reason="secret not found",
            )
            await db.commit()
            raise ValueError(f"Secret {secret_id} not found for user {user_id}")

        user_key = await self._get_user_key(db, user_id)
        secret.encrypted_value = self._encryption.encrypt(new_plaintext, user_key)
        secret.rotated_at = datetime.now(timezone.utc)

        await self._log_audit(
            db, user_id, "ROTATE", "SUCCESS",
            vault_secret_id=secret_id,
        )
        await db.commit()
        await db.refresh(secret)
        logger.info("Rotated secret %s for user %s", secret_id, user_id)
        return secret

    async def delete_secret(
        self,
        db: AsyncSession,
        user_id: str,
        secret_id: int,
        reason: str,
    ) -> bool:
        """Soft delete: set is_active=False. Logs DELETE audit."""
        result = await db.execute(
            select(VaultSecret).where(
                VaultSecret.id == secret_id,
                VaultSecret.user_id == user_id,
            )
        )
        secret = result.scalar_one_or_none()

        if secret is None:
            await self._log_audit(
                db, user_id, "DELETE", "FAILED",
                vault_secret_id=secret_id,
                reason=f"secret not found: {reason}",
            )
            await db.commit()
            return False

        secret.is_active = False
        await self._log_audit(
            db, user_id, "DELETE", "SUCCESS",
            vault_secret_id=secret_id,
            reason=reason,
        )
        await db.commit()
        logger.info("Soft-deleted secret %s for user %s: %s", secret_id, user_id, reason)
        return True

    async def _get_user_key(self, db: AsyncSession, user_id: str) -> Fernet:
        """Look up user's jwt_secret and derive encryption key."""
        result = await db.execute(
            select(User.jwt_secret).where(User.id == user_id)
        )
        jwt_secret = result.scalar_one_or_none()
        if jwt_secret is None:
            raise ValueError(f"User {user_id} not found")
        return self._encryption.derive_user_key(user_id, jwt_secret)

    async def _log_audit(
        self,
        db: AsyncSession,
        user_id: str,
        action: str,
        status: str,
        vault_secret_id: Optional[int] = None,
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Write audit log entry."""
        entry = VaultAuditLog(
            user_id=user_id,
            action=action,
            status=status,
            vault_secret_id=vault_secret_id,
            session_id=session_id,
            reason=reason,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(entry)
