"""
API key service for reseller machine-to-machine authentication.

Handles creation, validation, rotation, and revocation of API keys.
Keys are stored as bcrypt hashes; the full key is shown only once at creation.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import APIKey, APIKeyAuditLog

logger = logging.getLogger(__name__)


class APIKeyService:
    """API key management for reseller M2M authentication."""

    PREFIX_RESELLER = "ag3_res_"
    PREFIX_ADMIN = "ag3_adm_"

    async def create_key(
        self,
        db: AsyncSession,
        reseller_id: Optional[str],
        user_id: str,
        name: str,
        scopes: list,
        ip_allowlist: Optional[list] = None,
        rate_limit: int = 60,
        expires_at: Optional[datetime] = None,
    ) -> tuple[APIKey, str]:
        """Create an API key. Returns (db_record, full_key).

        The full key is shown only once and cannot be recovered. Store it securely.

        Args:
            db: Database session.
            reseller_id: Reseller ID (None for admin-created keys).
            user_id: User ID that owns this key.
            name: Human-readable label.
            scopes: List of scope strings to encode.
            ip_allowlist: Optional list of allowed IP addresses.
            rate_limit: Requests per minute allowed; defaults to 60.
            expires_at: Optional expiry datetime (UTC).

        Returns:
            Tuple of (APIKey record, raw full key string).
        """
        prefix = self.PREFIX_RESELLER if reseller_id else self.PREFIX_ADMIN
        raw_key = f"{prefix}{uuid.uuid4().hex}"
        key_prefix = raw_key[:16]  # type prefix (8) + first 8 of random part
        key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()

        record = APIKey(
            id=str(uuid.uuid4()),
            reseller_id=reseller_id,
            user_id=user_id,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            scopes=json.dumps(scopes),
            ip_allowlist=json.dumps(ip_allowlist) if ip_allowlist is not None else None,
            rate_limit_per_minute=rate_limit,
            is_active=True,
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

        logger.info(
            "API key created: id=%s reseller=%s user=%s name=%s",
            record.id, reseller_id, user_id, name,
        )
        return record, raw_key

    async def validate_key(self, db: AsyncSession, raw_key: str) -> Optional[APIKey]:
        """Validate a raw API key string.

        Steps:
          1. Extract prefix (first 8 chars) for fast index lookup.
          2. Query active candidates by prefix.
          3. bcrypt.checkpw against each candidate.
          4. Check is_active and expiry.

        Args:
            db: Database session.
            raw_key: The full API key as presented by the caller.

        Returns:
            The matching APIKey record, or None if invalid/expired.
        """
        if not raw_key or len(raw_key) < 16:
            return None

        key_prefix = raw_key[:16]

        result = await db.execute(
            select(APIKey).where(
                APIKey.key_prefix == key_prefix,
                APIKey.is_active.is_(True),
            )
        )
        candidates = result.scalars().all()

        for key in candidates:
            try:
                match = bcrypt.checkpw(raw_key.encode(), key.key_hash.encode())
            except Exception:
                logger.warning("bcrypt.checkpw failed for key id=%s", key.id)
                continue

            if not match:
                continue

            # Verify expiry
            if key.expires_at is not None:
                now = datetime.now(timezone.utc)
                expires = key.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires < now:
                    logger.debug("API key expired: id=%s", key.id)
                    return None

            return key

        return None

    async def rotate_key(
        self,
        db: AsyncSession,
        key_id: str,
        reseller_id: str,
        grace_period_hours: int = 24,
    ) -> tuple[APIKey, str, dict]:
        """Rotate an API key: create a new key with the same config, mark old to expire.

        The old key remains active for `grace_period_hours` to allow in-flight
        requests using the old key to complete.

        Args:
            db: Database session.
            key_id: ID of the key to rotate.
            reseller_id: Reseller ID (used to verify ownership).
            grace_period_hours: Hours until the old key expires.

        Returns:
            Tuple of (new APIKey record, new raw full key).

        Raises:
            ValueError: If the key is not found or does not belong to the reseller.
        """
        from datetime import timedelta

        result = await db.execute(
            select(APIKey).where(
                APIKey.id == key_id,
                APIKey.reseller_id == reseller_id,
            )
        )
        old_key = result.scalar_one_or_none()
        if old_key is None:
            raise ValueError(f"API key not found: {key_id}")

        # Decode existing config
        scopes = json.loads(old_key.scopes) if old_key.scopes else []
        ip_allowlist = json.loads(old_key.ip_allowlist) if old_key.ip_allowlist else None

        # Create new key with same config
        new_record, raw_key = await self.create_key(
            db=db,
            reseller_id=old_key.reseller_id,
            user_id=old_key.user_id,
            name=old_key.name,
            scopes=scopes,
            ip_allowlist=ip_allowlist,
            rate_limit=old_key.rate_limit_per_minute,
            expires_at=old_key.expires_at,
        )

        # Schedule old key to expire after grace period
        grace_expires = datetime.now(timezone.utc) + timedelta(hours=grace_period_hours)
        # Re-fetch old_key after create_key committed (session may have refreshed)
        result = await db.execute(select(APIKey).where(APIKey.id == key_id))
        old_key = result.scalar_one_or_none()
        if old_key is not None:
            old_key.expires_at = grace_expires
            await db.commit()

        logger.info(
            "API key rotated: old=%s new=%s reseller=%s grace_hours=%d",
            key_id, new_record.id, reseller_id, grace_period_hours,
        )
        return new_record, raw_key, {"old_key_id": key_id, "old_key_expires_at": grace_expires}

    async def revoke_key(self, db: AsyncSession, key_id: str, reseller_id: str) -> bool:
        """Revoke (deactivate) an API key.

        Args:
            db: Database session.
            key_id: ID of the key to revoke.
            reseller_id: Reseller ID for ownership check.

        Returns:
            True if revoked, False if key not found.
        """
        result = await db.execute(
            select(APIKey).where(
                APIKey.id == key_id,
                APIKey.reseller_id == reseller_id,
            )
        )
        key = result.scalar_one_or_none()
        if key is None:
            return False

        key.is_active = False
        await db.commit()
        logger.info("API key revoked: id=%s reseller=%s", key_id, reseller_id)
        return True

    async def list_keys(self, db: AsyncSession, reseller_id: str) -> list[APIKey]:
        """List all API keys for a reseller (active and inactive).

        Args:
            db: Database session.
            reseller_id: Reseller ID.

        Returns:
            List of APIKey records ordered by created_at descending.
        """
        result = await db.execute(
            select(APIKey)
            .where(APIKey.reseller_id == reseller_id)
            .order_by(APIKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_last_used(
        self, db: AsyncSession, key_id: str, ip_address: str
    ) -> None:
        """Update last_used_at and last_used_ip on a key.

        Args:
            db: Database session.
            key_id: The API key ID to update.
            ip_address: The IP address of the request.
        """
        result = await db.execute(select(APIKey).where(APIKey.id == key_id))
        key = result.scalar_one_or_none()
        if key is None:
            logger.warning("update_last_used: key not found id=%s", key_id)
            return

        key.last_used_at = datetime.now(timezone.utc)
        key.last_used_ip = ip_address
        try:
            await db.commit()
        except Exception as e:
            logger.error("Failed to update last_used for key %s: %s", key_id, e)
            await db.rollback()

    def check_ip_allowed(self, key: APIKey, ip_address: str) -> bool:
        """Check if an IP address is permitted by the key's allowlist.

        An empty or null allowlist allows all IPs.

        Args:
            key: The APIKey record.
            ip_address: The client IP address to check.

        Returns:
            True if the IP is allowed, False otherwise.
        """
        if not key.ip_allowlist:
            return True

        try:
            allowlist = json.loads(key.ip_allowlist)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Malformed ip_allowlist on key id=%s", key.id)
            return False

        if not allowlist:
            return True

        return ip_address in allowlist

    def has_scope(self, key: APIKey, required_scope: str) -> bool:
        """Check if the key has a required scope.

        Args:
            key: The APIKey record.
            required_scope: The scope string to check for.

        Returns:
            True if the key's scopes include required_scope.
        """
        try:
            scopes = json.loads(key.scopes) if key.scopes else []
        except (json.JSONDecodeError, TypeError):
            logger.warning("Malformed scopes on key id=%s", key.id)
            return False

        return required_scope in scopes

    async def log_usage(
        self,
        db: AsyncSession,
        api_key_id: Optional[str],
        reseller_id: Optional[str],
        action: str,
        target_user_id: Optional[str],
        ip_address: str,
        status_code: int,
        error: Optional[str] = None,
    ) -> None:
        """Write an audit log entry for an API key usage event.

        Failures are logged but never propagate — audit logging must not
        break the calling request path.

        Args:
            db: Database session.
            api_key_id: The API key ID (may be None for failed lookups).
            reseller_id: The reseller ID (may be None).
            action: Action label (e.g. "authenticate", "create_user").
            target_user_id: The user being acted upon (may be None).
            ip_address: The client IP address.
            status_code: HTTP-style status code for the outcome.
            error: Optional error message if the action failed.
        """
        entry = APIKeyAuditLog(
            api_key_id=api_key_id,
            reseller_id=reseller_id,
            action=action,
            target_user_id=target_user_id,
            ip_address=ip_address,
            status_code=status_code,
            error=error,
            timestamp=datetime.now(timezone.utc),
        )
        try:
            db.add(entry)
            await db.commit()
        except Exception as e:
            logger.error(
                "Failed to write API key audit log (action=%s key=%s): %s",
                action, api_key_id, e,
            )
            try:
                await db.rollback()
            except Exception:
                pass


# Module-level singleton
api_key_service = APIKeyService()
