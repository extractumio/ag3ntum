"""
Per-user vault encryption using HKDF key derivation.

Each user's secrets are encrypted with a unique derived key based on:
- Master key (from secrets.yaml fernet_key)
- User's jwt_secret (rotates on password change)

Even if the master key is compromised, attackers need the user's
jwt_secret to decrypt their vault entries.
"""
from __future__ import annotations

import base64
import logging

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

_VAULT_INFO = b"ag3ntum-vault-v1"
_DERIVED_KEY_LENGTH = 32


class VaultEncryption:
    """Per-user key derivation for vault secrets.

    Each user's secrets encrypted with unique derived key based on:
    - Master key (from secrets.yaml fernet_key)
    - User's jwt_secret (rotates on password change)
    """

    def __init__(self, master_key: bytes) -> None:
        self._master_key = master_key

    def derive_user_key(self, user_id: str, jwt_secret: str) -> Fernet:
        """Derive per-user encryption key using HKDF.

        Uses HKDF with SHA256.
        Salt: user_id encoded UTF-8
        Info: b"ag3ntum-vault-v1"
        Input: master_key + jwt_secret encoded
        Output: 32 bytes -> base64 urlsafe encode -> Fernet key
        """
        key_material = self._master_key + jwt_secret.encode("utf-8")
        salt = user_id.encode("utf-8")

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=_DERIVED_KEY_LENGTH,
            salt=salt,
            info=_VAULT_INFO,
        )
        derived = hkdf.derive(key_material)
        fernet_key = base64.urlsafe_b64encode(derived)
        return Fernet(fernet_key)

    def encrypt(self, plaintext: str, user_key: Fernet) -> str:
        """Encrypt plaintext string with user-specific key."""
        return user_key.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str, user_key: Fernet) -> str:
        """Decrypt ciphertext with user-specific key."""
        return user_key.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
