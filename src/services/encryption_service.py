"""Encryption service for sensitive data."""
import logging
from pathlib import Path
from typing import Optional

import yaml
from cryptography.fernet import Fernet

from ..config import CONFIG_DIR

logger = logging.getLogger(__name__)
SECRETS_FILE: Path = CONFIG_DIR / "secrets.yaml"


class EncryptionService:
    """Service for encrypting/decrypting sensitive data with Fernet."""

    def __init__(self) -> None:
        self._fernet: Optional[Fernet] = None

    def _get_fernet_key(self) -> Fernet:
        """Get or generate Fernet key from secrets.yaml."""
        if self._fernet:
            return self._fernet

        secrets_data = {}
        if SECRETS_FILE.exists():
            with SECRETS_FILE.open("r", encoding="utf-8") as f:
                secrets_data = yaml.safe_load(f) or {}

        fernet_key = secrets_data.get("fernet_key")

        if not fernet_key:
            fernet_key = Fernet.generate_key().decode("utf-8")
            secrets_data["fernet_key"] = fernet_key

            with SECRETS_FILE.open("w", encoding="utf-8") as f:
                yaml.dump(secrets_data, f, default_flow_style=False)
            logger.info("Generated and saved new Fernet key")

        self._fernet = Fernet(fernet_key.encode())
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string."""
        fernet = self._get_fernet_key()
        return fernet.encrypt(plaintext.encode()).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext string."""
        fernet = self._get_fernet_key()
        return fernet.decrypt(ciphertext.encode()).decode("utf-8")


encryption_service = EncryptionService()
