"""Service layer for SSH profile management."""
import asyncio
import base64
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import asyncssh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.ssh_profile_models import mask_ssh_key
from ..core.ssh.ssh_config import SSHProfile
from ..core.ssh.ssh_host_key_scanner import scan_host_key
from ..db.models import SSHProfileRecord, VaultSecret
from ..services.vault_service import VaultService

logger = logging.getLogger(__name__)

# Connection test timeout
_TEST_TIMEOUT_SECONDS = 10


def _compute_key_fingerprint(pem_key: str) -> tuple[str, str]:
    """Compute SHA256 fingerprint and key type from a PEM private key."""
    try:
        key = asyncssh.import_private_key(pem_key)
        key_type = key.export_public_key("openssh").decode().split()[0]
        return key.get_fingerprint(), key_type
    except Exception as e:
        logger.warning("Failed to compute key fingerprint: %s", e)
        return "", "unknown"


def _compute_host_key_fingerprint(openssh_str: str) -> tuple[str | None, str | None]:
    """Compute SHA256 fingerprint from an OpenSSH public key string.

    Returns (fingerprint, key_type) or (None, None) on failure.
    """
    try:
        parts = openssh_str.strip().split()
        key_type = parts[0] if parts else None
        if len(parts) >= 2:
            raw = base64.b64decode(parts[1])
            fingerprint = (
                "SHA256:"
                + base64.b64encode(hashlib.sha256(raw).digest())
                .decode()
                .rstrip("=")
            )
            return fingerprint, key_type
    except Exception:
        pass
    return None, None


def _validate_private_key(pem_key: str, passphrase: Optional[str] = None) -> asyncssh.SSHKey:
    """Validate and import a PEM private key. Raises ValueError on failure."""
    try:
        return asyncssh.import_private_key(pem_key, passphrase=passphrase)
    except asyncssh.KeyImportError as e:
        if "passphrase" in str(e).lower() or "encrypted" in str(e).lower():
            raise ValueError(
                "Key is passphrase-protected but no passphrase was provided"
            )
        raise ValueError(f"Invalid SSH private key: {e}")
    except Exception as e:
        raise ValueError(f"Failed to parse SSH key: {e}")


async def create_profile(
    db: AsyncSession,
    vault: VaultService,
    user_id: str,
    name: str,
    host: str,
    port: int,
    username: str,
    private_key: str,
    passphrase: Optional[str] = None,
    mode: str = "readonly",
    privilege_level: int = 0,
    allowed_operations: Optional[list[str]] = None,
    description: Optional[str] = None,
    created_by: str = "self",
) -> SSHProfileRecord:
    """Create a new SSH profile with vault-stored key."""
    # Validate key
    _validate_private_key(private_key, passphrase)
    fingerprint, key_type = _compute_key_fingerprint(private_key)
    logger.debug("Key fingerprint for new profile '%s': %s (%s)", name, fingerprint, key_type)

    # Check duplicate name
    existing = await db.execute(
        select(SSHProfileRecord).where(
            SSHProfileRecord.user_id == user_id,
            SSHProfileRecord.name == name,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Profile name '{name}' already exists")

    # Store key in vault
    vault_name = f"ssh_profile_{name}"
    key_secret = await vault.store_secret(
        db=db,
        user_id=user_id,
        secret_type="ssh_private_key",
        name=vault_name,
        plaintext_value=private_key,
        ssh_key_type=key_type.replace("ssh-", ""),
    )

    # Store passphrase in vault if provided
    passphrase_secret_id = None
    if passphrase:
        pp_secret = await vault.store_secret(
            db=db,
            user_id=user_id,
            secret_type="ssh_passphrase",
            name=f"{vault_name}_passphrase",
            plaintext_value=passphrase,
        )
        passphrase_secret_id = pp_secret.id

    # Try to pin host key (non-blocking — failure is OK)
    host_key_pinned = False
    try:
        host_key_str = await scan_host_key(host, port, timeout=float(_TEST_TIMEOUT_SECONDS))
        await vault.store_secret(
            db=db,
            user_id=user_id,
            secret_type="ssh_host_key",
            name=name,
            plaintext_value=host_key_str,
        )
        host_key_pinned = True
    except Exception as e:
        logger.warning("Could not pin host key for %s:%d: %s", host, port, e)

    # Create DB record
    profile_id = str(uuid.uuid4())
    record = SSHProfileRecord(
        id=profile_id,
        user_id=user_id,
        name=name,
        host=host,
        port=port,
        username=username,
        auth_method="key",
        key_vault_secret_id=key_secret.id,
        mode=mode,
        privilege_level=privilege_level,
        allowed_operations=json.dumps(allowed_operations) if allowed_operations else None,
        passphrase_vault_secret_id=passphrase_secret_id,
        host_key_pinned=host_key_pinned,
        description=description,
        is_active=True,
        created_by=created_by,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_profiles(
    db: AsyncSession,
    user_id: str,
) -> list[SSHProfileRecord]:
    """List all SSH profiles for a user."""
    result = await db.execute(
        select(SSHProfileRecord).where(
            SSHProfileRecord.user_id == user_id,
        ).order_by(SSHProfileRecord.name)
    )
    return list(result.scalars().all())


async def get_profile(
    db: AsyncSession,
    user_id: str,
    profile_id: str,
) -> Optional[SSHProfileRecord]:
    """Get a single profile, scoped to user."""
    result = await db.execute(
        select(SSHProfileRecord).where(
            SSHProfileRecord.id == profile_id,
            SSHProfileRecord.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def update_profile(
    db: AsyncSession,
    vault: VaultService,
    user_id: str,
    profile_id: str,
    **kwargs,
) -> Optional[SSHProfileRecord]:
    """Update an SSH profile. Pass only fields to change."""
    record = await get_profile(db, user_id, profile_id)
    if not record:
        return None

    # Handle key replacement
    private_key = kwargs.pop("private_key", None)
    passphrase = kwargs.pop("passphrase", None)
    if private_key:
        _validate_private_key(private_key, passphrase)
        fingerprint, key_type = _compute_key_fingerprint(private_key)
        logger.debug("Rotating key for profile '%s': %s (%s)", record.name, fingerprint, key_type)

        # Delete old key secret
        if record.key_vault_secret_id:
            await vault.delete_secret(
                db, user_id, record.key_vault_secret_id,
                reason="key_rotation"
            )

        # Store new key
        vault_name = f"ssh_profile_{record.name}"
        new_secret = await vault.store_secret(
            db=db,
            user_id=user_id,
            secret_type="ssh_private_key",
            name=vault_name,
            plaintext_value=private_key,
            ssh_key_type=key_type.replace("ssh-", ""),
        )
        record.key_vault_secret_id = new_secret.id

        # Handle passphrase
        if record.passphrase_vault_secret_id:
            await vault.delete_secret(
                db, user_id, record.passphrase_vault_secret_id,
                reason="key_rotation"
            )
            record.passphrase_vault_secret_id = None

        if passphrase:
            pp_secret = await vault.store_secret(
                db=db,
                user_id=user_id,
                secret_type="ssh_passphrase",
                name=f"{vault_name}_passphrase",
                plaintext_value=passphrase,
            )
            record.passphrase_vault_secret_id = pp_secret.id

    # Handle name change (also check uniqueness)
    new_name = kwargs.get("name")
    if new_name and new_name != record.name:
        existing = await db.execute(
            select(SSHProfileRecord).where(
                SSHProfileRecord.user_id == user_id,
                SSHProfileRecord.name == new_name,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Profile name '{new_name}' already exists")

    # Handle allowed_operations serialization
    ops = kwargs.pop("allowed_operations", None)
    if ops is not None:
        kwargs["allowed_operations"] = json.dumps(ops)

    # Apply remaining updates
    for key, value in kwargs.items():
        if value is not None and hasattr(record, key):
            setattr(record, key, value)

    await db.commit()
    await db.refresh(record)
    return record


async def delete_profile(
    db: AsyncSession,
    vault: VaultService,
    user_id: str,
    profile_id: str,
) -> bool:
    """Delete a profile and its vault secrets."""
    record = await get_profile(db, user_id, profile_id)
    if not record:
        return False

    # Delete vault secrets
    if record.key_vault_secret_id:
        await vault.delete_secret(
            db, user_id, record.key_vault_secret_id,
            reason="profile_deleted"
        )
    if record.passphrase_vault_secret_id:
        await vault.delete_secret(
            db, user_id, record.passphrase_vault_secret_id,
            reason="profile_deleted"
        )
    # Delete host key if pinned
    try:
        host_key_result = await db.execute(
            select(VaultSecret).where(
                VaultSecret.user_id == user_id,
                VaultSecret.secret_type == "ssh_host_key",
                VaultSecret.name == record.name,
                VaultSecret.is_active.is_(True),
            )
        )
        host_key = host_key_result.scalar_one_or_none()
        if host_key:
            await vault.delete_secret(
                db, user_id, host_key.id, reason="profile_deleted"
            )
    except Exception:
        pass

    await db.delete(record)
    await db.commit()
    return True


async def test_connection(
    host: str,
    port: int,
    username: str,
    private_key: str,
    passphrase: Optional[str] = None,
) -> dict:
    """Test an SSH connection without saving anything.

    Returns dict with status, error_code, message, host_key_fingerprint,
    host_key_type, server_banner, latency_ms.
    """
    # Validate key first
    try:
        key = _validate_private_key(private_key, passphrase)
    except ValueError as e:
        return {
            "status": "failed",
            "error_code": "invalid_key",
            "message": str(e),
        }

    start = time.monotonic()
    # Temporarily set HOME to /tmp to prevent asyncssh from scanning
    # /root/.ssh/ (which is inaccessible in the container context).
    orig_home = os.environ.get("HOME", "")
    os.environ["HOME"] = "/tmp"
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                host,
                port=port,
                username=username,
                client_keys=[key],
                known_hosts=None,  # Accept any host key for test
                client_host_keys=[],  # Don't scan filesystem for certs
                agent_path=None,  # Don't use SSH agent
                config=[],  # Don't load ~/.ssh/config
                keepalive_interval=0,
            ),
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        # Extract host key from the live connection (avoids second TCP handshake)
        hk_fingerprint = None
        hk_type = None
        try:
            server_key = conn.get_server_host_key()
            if server_key:
                hk_pub = server_key.export_public_key("openssh").decode()
                hk_fingerprint, hk_type = _compute_host_key_fingerprint(hk_pub)
        except Exception:
            pass

        # Get server banner
        server_banner = None
        try:
            server_banner = str(
                getattr(conn, '_peer_version', '') or ''
            )
        except Exception:
            pass

        conn.close()
        await conn.wait_closed()

    except asyncio.TimeoutError:
        return {
            "status": "failed",
            "error_code": "timeout",
            "message": f"Connection timed out after {_TEST_TIMEOUT_SECONDS}s",
        }
    except asyncssh.PermissionDenied:
        return {
            "status": "failed",
            "error_code": "auth_failed",
            "message": "Authentication failed — check username and key",
        }
    except asyncssh.ConnectionLost:
        return {
            "status": "failed",
            "error_code": "connection_refused",
            "message": "Connection lost during handshake",
        }
    except OSError as e:
        if "refused" in str(e).lower():
            error_code = "connection_refused"
            msg = f"Connection refused on {host}:{port}"
        else:
            error_code = "host_unreachable"
            msg = f"Cannot reach {host}:{port}: {e}"
        return {
            "status": "failed",
            "error_code": error_code,
            "message": msg,
        }
    except Exception as e:
        return {
            "status": "failed",
            "error_code": "unknown",
            "message": f"Connection failed: {e}",
        }
    finally:
        os.environ["HOME"] = orig_home

    return {
        "status": "success",
        "message": f"Connected successfully ({latency_ms}ms)",
        "host_key_fingerprint": hk_fingerprint,
        "host_key_type": hk_type,
        "server_banner": server_banner,
        "latency_ms": latency_ms,
    }


async def test_saved_connection(
    db: AsyncSession,
    vault: VaultService,
    user_id: str,
    profile_id: str,
) -> Optional[dict]:
    """Test an existing saved profile's connection."""
    record = await get_profile(db, user_id, profile_id)
    if not record:
        return None

    # Retrieve key from vault
    try:
        key_pem = await vault.get_secret_value(
            db, user_id, record.key_vault_secret_id,
            session_id="ssh_profile_test",
        )
    except Exception as e:
        return {
            "status": "failed",
            "error_code": "vault_error",
            "message": f"Could not retrieve key from vault: {e}",
        }

    # Retrieve passphrase if stored
    passphrase = None
    if record.passphrase_vault_secret_id:
        try:
            passphrase = await vault.get_secret_value(
                db, user_id, record.passphrase_vault_secret_id,
                session_id="ssh_profile_test",
            )
        except Exception:
            pass

    result = await test_connection(
        host=record.host,
        port=record.port,
        username=record.username,
        private_key=key_pem,
        passphrase=passphrase,
    )

    # Update record with result
    now = datetime.now(timezone.utc)
    if result["status"] == "success":
        record.last_connected_at = now
        record.last_connection_error = None
    else:
        record.last_connection_error = result.get("message", "Unknown error")

    await db.commit()
    return result


def profile_to_ssh_profile(record: SSHProfileRecord) -> SSHProfile:
    """Convert a DB SSHProfileRecord to the core SSHProfile dataclass."""
    ops = []
    if record.allowed_operations:
        try:
            ops = json.loads(record.allowed_operations)
        except (json.JSONDecodeError, TypeError):
            pass

    return SSHProfile(
        name=record.name,
        host=record.host,
        port=record.port,
        username=record.username,
        auth_method=record.auth_method,
        key_ref=f"ssh_profile_{record.name}",
        mode=record.mode,
        privilege_level=record.privilege_level,
        allowed_operations=ops,
        description=record.description or "",
    )


async def build_profile_response(
    db: AsyncSession,
    vault: VaultService,
    record: SSHProfileRecord,
    user_id: str,
) -> dict:
    """Build the API response dict for a profile record."""
    key_preview = ""
    key_fingerprint = None
    key_type = None

    if record.key_vault_secret_id:
        try:
            key_pem = await vault.get_secret_value(
                db, user_id, record.key_vault_secret_id,
                session_id="ssh_profile_api",
            )
            key_preview = mask_ssh_key(key_pem)
            key_fingerprint, key_type = _compute_key_fingerprint(key_pem)
        except Exception:
            key_preview = "(key unavailable)"

    # Get host key fingerprint if pinned
    hk_fingerprint = None
    if record.host_key_pinned:
        try:
            hk_result = await db.execute(
                select(VaultSecret).where(
                    VaultSecret.user_id == user_id,
                    VaultSecret.secret_type == "ssh_host_key",
                    VaultSecret.name == record.name,
                    VaultSecret.is_active.is_(True),
                )
            )
            hk_secret = hk_result.scalar_one_or_none()
            if hk_secret:
                hk_pem = await vault.get_secret_value(
                    db, user_id, hk_secret.id,
                    session_id="ssh_profile_api",
                )
                hk_fingerprint, _ = _compute_host_key_fingerprint(hk_pem)
        except Exception:
            pass

    return {
        "id": record.id,
        "name": record.name,
        "host": record.host,
        "port": record.port,
        "username": record.username,
        "mode": record.mode,
        "privilege_level": record.privilege_level,
        "host_key_pinned": record.host_key_pinned,
        "host_key_fingerprint": hk_fingerprint,
        "key_preview": key_preview,
        "key_fingerprint": key_fingerprint,
        "key_type": key_type,
        "is_active": record.is_active,
        "last_connected_at": record.last_connected_at,
        "last_connection_error": record.last_connection_error,
        "description": record.description,
        "created_by": record.created_by,
        "created_at": record.created_at,
    }
