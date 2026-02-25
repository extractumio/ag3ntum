"""
Tests for VaultEncryption and VaultService.

Uses in-memory SQLite via the ssh_db / test_user_with_jwt fixtures
defined in tests/backend/ssh/conftest.py. No containers required.
"""
import pytest
from datetime import datetime, timezone, timedelta

from src.services.vault_encryption import VaultEncryption
from src.services.vault_service import VaultService


# ---------------------------------------------------------------------------
# VaultEncryption unit tests
# ---------------------------------------------------------------------------

class TestVaultEncryption:

    @pytest.mark.unit
    def test_encrypt_decrypt_roundtrip(self, vault_encryption):
        """Encrypting then decrypting the same value yields the original plaintext."""
        key = vault_encryption.derive_user_key("user-123", "jwt-secret")
        plaintext = "super-secret-ssh-key-content"

        encrypted = vault_encryption.encrypt(plaintext, key)

        assert encrypted != plaintext
        assert vault_encryption.decrypt(encrypted, key) == plaintext

    @pytest.mark.unit
    def test_encrypted_value_is_not_plaintext(self, vault_encryption):
        """The encrypted blob must not contain the original plaintext."""
        key = vault_encryption.derive_user_key("user-1", "jwt-1")
        plaintext = "my-secret-api-key"

        encrypted = vault_encryption.encrypt(plaintext, key)

        assert plaintext not in encrypted

    @pytest.mark.unit
    def test_same_plaintext_produces_different_ciphertexts(self, vault_encryption):
        """Fernet uses random IVs so the same plaintext encrypts differently each time."""
        key = vault_encryption.derive_user_key("user-1", "jwt-1")
        plaintext = "same-plaintext"

        enc1 = vault_encryption.encrypt(plaintext, key)
        enc2 = vault_encryption.encrypt(plaintext, key)

        assert enc1 != enc2

    @pytest.mark.unit
    def test_different_users_produce_different_keys(self, vault_encryption):
        """Different user_id + jwt_secret combinations yield different derived keys."""
        key1 = vault_encryption.derive_user_key("user-1", "jwt-1")
        key2 = vault_encryption.derive_user_key("user-2", "jwt-2")
        plaintext = "test"

        enc1 = vault_encryption.encrypt(plaintext, key1)
        enc2 = vault_encryption.encrypt(plaintext, key2)

        # Ciphertexts differ and cross-decryption must fail
        assert enc1 != enc2
        with pytest.raises(Exception):
            vault_encryption.decrypt(enc1, key2)

    @pytest.mark.unit
    def test_wrong_key_cannot_decrypt(self, vault_encryption):
        """Decrypting with a different key raises an exception."""
        key_a = vault_encryption.derive_user_key("user-a", "secret-a")
        key_b = vault_encryption.derive_user_key("user-b", "secret-b")

        encrypted = vault_encryption.encrypt("private", key_a)

        with pytest.raises(Exception):
            vault_encryption.decrypt(encrypted, key_b)

    @pytest.mark.unit
    def test_key_rotation_invalidates_old_ciphertext(self, vault_encryption):
        """Rotating jwt_secret changes the derived key; old ciphertexts cannot be decrypted."""
        key_old = vault_encryption.derive_user_key("user-1", "old-jwt-secret")
        key_new = vault_encryption.derive_user_key("user-1", "new-jwt-secret")

        encrypted_with_old = vault_encryption.encrypt("rotate-me", key_old)

        with pytest.raises(Exception):
            vault_encryption.decrypt(encrypted_with_old, key_new)

    @pytest.mark.unit
    def test_derive_user_key_same_inputs_deterministic(self, vault_encryption):
        """Deriving a key with the same inputs always yields a Fernet that can decrypt."""
        key1 = vault_encryption.derive_user_key("stable-user", "stable-jwt")
        key2 = vault_encryption.derive_user_key("stable-user", "stable-jwt")

        enc = vault_encryption.encrypt("data", key1)
        assert vault_encryption.decrypt(enc, key2) == "data"


# ---------------------------------------------------------------------------
# VaultService integration tests (in-memory SQLite)
# ---------------------------------------------------------------------------

class TestVaultService:

    # --- store and retrieve ---

    @pytest.mark.unit
    async def test_store_and_retrieve_secret(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """store_secret encrypts the value; get_secret_value decrypts it correctly."""
        secret = await vault_service.store_secret(
            ssh_db,
            user_id=test_user_with_jwt.id,
            secret_type="api_key",
            name="openai-key",
            plaintext_value="sk-test-12345",
        )

        assert secret.id is not None
        assert secret.encrypted_value != "sk-test-12345"

        plaintext = await vault_service.get_secret_value(
            ssh_db, test_user_with_jwt.id, secret.id, "session-1"
        )
        assert plaintext == "sk-test-12345"

    @pytest.mark.unit
    async def test_store_secret_sets_is_active(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """Newly stored secrets are active by default."""
        secret = await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "api_key", "active-key", "val",
        )
        assert secret.is_active is True

    @pytest.mark.unit
    async def test_store_secret_with_env_var_name(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """env_var_name is persisted on the stored secret."""
        secret = await vault_service.store_secret(
            ssh_db,
            user_id=test_user_with_jwt.id,
            secret_type="api_key",
            name="env-key",
            plaintext_value="env-val",
            env_var_name="MY_ENV_VAR",
        )
        assert secret.env_var_name == "MY_ENV_VAR"

    # --- list secrets ---

    @pytest.mark.unit
    async def test_list_secrets_no_plaintext(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """list_secrets returns metadata only — no encrypted_value or plaintext."""
        await vault_service.store_secret(
            ssh_db,
            user_id=test_user_with_jwt.id,
            secret_type="api_key",
            name="my-key",
            plaintext_value="secret-value",
        )

        secrets = await vault_service.list_secrets(ssh_db, test_user_with_jwt.id)

        assert len(secrets) == 1
        assert secrets[0]["name"] == "my-key"
        assert "encrypted_value" not in secrets[0]
        assert "plaintext" not in str(secrets[0])

    @pytest.mark.unit
    async def test_list_secrets_multiple_entries(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """list_secrets returns all stored secrets for the user."""
        for i in range(3):
            await vault_service.store_secret(
                ssh_db, test_user_with_jwt.id, "api_key", f"key-{i}", f"val-{i}",
            )

        secrets = await vault_service.list_secrets(ssh_db, test_user_with_jwt.id)
        assert len(secrets) == 3

    @pytest.mark.unit
    async def test_list_secrets_filtered_by_type(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """list_secrets with secret_type filter returns only matching entries."""
        await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "api_key", "api-1", "v1",
        )
        await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "bearer_token", "token-1", "t1",
        )

        api_secrets = await vault_service.list_secrets(
            ssh_db, test_user_with_jwt.id, secret_type="api_key"
        )
        assert len(api_secrets) == 1
        assert api_secrets[0]["secret_type"] == "api_key"

    # --- delete (soft delete) ---

    @pytest.mark.unit
    async def test_delete_secret_soft_delete(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """delete_secret marks is_active=False and raises on subsequent access."""
        secret = await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "api_key", "del-key", "val",
        )

        deleted = await vault_service.delete_secret(
            ssh_db, test_user_with_jwt.id, secret.id, "test cleanup"
        )
        assert deleted is True

        with pytest.raises(ValueError, match="inactive"):
            await vault_service.get_secret_value(
                ssh_db, test_user_with_jwt.id, secret.id, "session-1"
            )

    @pytest.mark.unit
    async def test_delete_nonexistent_returns_false(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """delete_secret returns False when the secret does not exist."""
        result = await vault_service.delete_secret(
            ssh_db, test_user_with_jwt.id, 99999, "reason"
        )
        assert result is False

    # --- rotate ---

    @pytest.mark.unit
    async def test_rotate_secret(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """rotate_secret stores the new value; retrieval returns the new plaintext."""
        secret = await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "api_key", "rot-key", "old-value",
        )

        await vault_service.rotate_secret(
            ssh_db, test_user_with_jwt.id, secret.id, "new-value"
        )

        plaintext = await vault_service.get_secret_value(
            ssh_db, test_user_with_jwt.id, secret.id, "session-1"
        )
        assert plaintext == "new-value"

    @pytest.mark.unit
    async def test_rotate_sets_rotated_at(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """rotate_secret sets the rotated_at timestamp."""
        secret = await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "api_key", "ts-key", "original",
        )
        assert secret.rotated_at is None

        updated = await vault_service.rotate_secret(
            ssh_db, test_user_with_jwt.id, secret.id, "new-val"
        )
        assert updated.rotated_at is not None

    @pytest.mark.unit
    async def test_rotate_nonexistent_raises(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """rotate_secret raises ValueError when the secret does not exist."""
        with pytest.raises(ValueError, match="not found"):
            await vault_service.rotate_secret(
                ssh_db, test_user_with_jwt.id, 99999, "new-val"
            )

    # --- expiry ---

    @pytest.mark.unit
    async def test_expired_secret_denied(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """get_secret_value raises ValueError when the secret has expired."""
        secret = await vault_service.store_secret(
            ssh_db,
            user_id=test_user_with_jwt.id,
            secret_type="api_key",
            name="exp-key",
            plaintext_value="expired-val",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

        with pytest.raises(ValueError, match="expired"):
            await vault_service.get_secret_value(
                ssh_db, test_user_with_jwt.id, secret.id, "session-1"
            )

    @pytest.mark.unit
    async def test_future_expiry_allows_access(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """get_secret_value succeeds when expires_at is in the future."""
        secret = await vault_service.store_secret(
            ssh_db,
            user_id=test_user_with_jwt.id,
            secret_type="api_key",
            name="future-exp",
            plaintext_value="still-valid",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )

        plaintext = await vault_service.get_secret_value(
            ssh_db, test_user_with_jwt.id, secret.id, "session-1"
        )
        assert plaintext == "still-valid"

    # --- access control ---

    @pytest.mark.unit
    async def test_nonexistent_secret_raises_not_found(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """get_secret_value raises ValueError with 'not found' for unknown IDs."""
        with pytest.raises(ValueError, match="not found"):
            await vault_service.get_secret_value(
                ssh_db, test_user_with_jwt.id, 99999, "session-1"
            )

    @pytest.mark.unit
    async def test_wrong_user_cannot_access(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """A different user_id cannot retrieve secrets that belong to another user."""
        secret = await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "api_key", "priv-key", "priv-val",
        )

        with pytest.raises(ValueError, match="not found"):
            await vault_service.get_secret_value(
                ssh_db, "other-user-id", secret.id, "session-1"
            )

    @pytest.mark.unit
    async def test_get_updates_last_accessed(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """get_secret_value sets last_accessed_at and last_accessed_session_id."""
        secret = await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "api_key", "access-key", "val",
        )
        assert secret.last_accessed_at is None

        await vault_service.get_secret_value(
            ssh_db, test_user_with_jwt.id, secret.id, "my-session"
        )

        # Reload from DB
        from sqlalchemy import select
        from src.db.models import VaultSecret
        result = await ssh_db.execute(
            select(VaultSecret).where(VaultSecret.id == secret.id)
        )
        refreshed = result.scalar_one()
        assert refreshed.last_accessed_at is not None
        assert refreshed.last_accessed_session_id == "my-session"

    # --- env var injection ---

    @pytest.mark.unit
    async def test_inject_env_vars(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """inject_env_vars returns {env_var_name: decrypted_value} for active secrets."""
        await vault_service.store_secret(
            ssh_db,
            user_id=test_user_with_jwt.id,
            secret_type="api_key",
            name="injected",
            plaintext_value="injected-value",
            env_var_name="MY_API_KEY",
        )

        env_vars = await vault_service.inject_env_vars(
            ssh_db, test_user_with_jwt.id, "session-1"
        )

        assert env_vars["MY_API_KEY"] == "injected-value"

    @pytest.mark.unit
    async def test_inject_env_vars_skips_no_env_name(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """Secrets without env_var_name are not included in inject_env_vars."""
        await vault_service.store_secret(
            ssh_db, test_user_with_jwt.id, "api_key", "no-env-key", "val",
            # env_var_name=None (default)
        )

        env_vars = await vault_service.inject_env_vars(
            ssh_db, test_user_with_jwt.id, "session-1"
        )

        assert env_vars == {}

    @pytest.mark.unit
    async def test_inject_env_vars_skips_expired(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """inject_env_vars skips expired secrets silently."""
        await vault_service.store_secret(
            ssh_db,
            user_id=test_user_with_jwt.id,
            secret_type="api_key",
            name="expired-env",
            plaintext_value="old-val",
            env_var_name="EXPIRED_KEY",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

        env_vars = await vault_service.inject_env_vars(
            ssh_db, test_user_with_jwt.id, "session-1"
        )

        assert "EXPIRED_KEY" not in env_vars

    @pytest.mark.unit
    async def test_inject_env_vars_multiple(
        self, vault_service, ssh_db, test_user_with_jwt
    ):
        """inject_env_vars returns all active env-mapped secrets at once."""
        pairs = [("KEY_A", "val-a"), ("KEY_B", "val-b"), ("KEY_C", "val-c")]
        for i, (env_name, val) in enumerate(pairs):
            await vault_service.store_secret(
                ssh_db, test_user_with_jwt.id, "api_key",
                f"secret-{i}", val, env_var_name=env_name,
            )

        env_vars = await vault_service.inject_env_vars(
            ssh_db, test_user_with_jwt.id, "session-1"
        )

        for env_name, val in pairs:
            assert env_vars[env_name] == val
