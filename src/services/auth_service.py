"""
Authentication service for Ag3ntum API.

Handles JWT token generation and validation with per-user secrets.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import USERS_DIR
from ..db.models import User

logger = logging.getLogger(__name__)


class UserEnvironmentError(Exception):
    """
    Raised when a user's environment is misconfigured.

    This indicates the user account exists in the database but required
    filesystem resources (home directory, venv) are missing. The user
    must be recreated to fix this issue.
    """
    pass

# JWT configuration
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 168  # 7 days


class AuthService:
    """
    Service for JWT token management.

    Provides methods for token generation, validation, and user management.
    """

    def __init__(self) -> None:
        """Initialize the auth service."""
        pass

    def validate_user_environment(self, username: str) -> None:
        """
        Validate that user's filesystem environment is properly configured.

        Checks for required directories that must exist for the sandbox to work:
        - Home directory: /users/{username}
        - Python venv: /users/{username}/venv

        Permission Model Note:
        =====================
        This validation runs as the API user (ag3ntum_api), not the target user.
        The directory permissions must allow the API to traverse and check existence:
        - /users/{username}/     mode 711 (execute allows traversal)
        - /users/{username}/venv mode 711 (execute allows traversal, hides package list)

        If validation fails with PermissionError, the user's directory permissions
        are misconfigured and need to be fixed.

        Args:
            username: The username to validate.

        Raises:
            UserEnvironmentError: If any required resource is missing or inaccessible.
        """
        user_home = USERS_DIR / username

        # Check home directory
        try:
            if not user_home.exists():
                logger.error(
                    f"SECURITY: User '{username}' home directory missing: {user_home}. "
                    "User account is misconfigured."
                )
                raise UserEnvironmentError(
                    f"User '{username}' is misconfigured: home directory does not exist. "
                    "Please contact administrator to recreate the account."
                )
        except PermissionError as e:
            logger.error(
                f"SECURITY: Cannot access user '{username}' home directory: {user_home}. "
                f"Permission denied: {e}. Directory permissions may be misconfigured."
            )
            raise UserEnvironmentError(
                f"User '{username}' environment is inaccessible. "
                "This is a server configuration issue. Please contact administrator."
            )

        # Check venv directory (required for sandbox)
        venv_path = user_home / "venv"
        try:
            if not venv_path.exists():
                logger.error(
                    f"SECURITY: User '{username}' venv missing: {venv_path}. "
                    "User account is misconfigured."
                )
                raise UserEnvironmentError(
                    f"User '{username}' is misconfigured: Python environment not initialized. "
                    "Please contact administrator to recreate the account."
                )
        except PermissionError as e:
            logger.error(
                f"SECURITY: Cannot access user '{username}' venv: {venv_path}. "
                f"Permission denied: {e}. Home directory should have mode 711, venv should have mode 755."
            )
            raise UserEnvironmentError(
                f"User '{username}' environment is inaccessible due to permission settings. "
                "Please contact administrator to fix directory permissions."
            )

        # Check venv has Python binary
        python_bin = venv_path / "bin" / "python3"
        try:
            if not python_bin.exists():
                logger.error(
                    f"SECURITY: User '{username}' venv corrupted: {python_bin} missing. "
                    "User account is misconfigured."
                )
                raise UserEnvironmentError(
                    f"User '{username}' is misconfigured: Python environment corrupted. "
                    "Please contact administrator to recreate the account."
                )
        except PermissionError as e:
            logger.error(
                f"SECURITY: Cannot access user '{username}' python binary: {python_bin}. "
                f"Permission denied: {e}. venv/bin should have mode 755."
            )
            raise UserEnvironmentError(
                f"User '{username}' Python environment is inaccessible. "
                "Please contact administrator to fix directory permissions."
            )

        logger.debug(f"User environment validated for '{username}'")

    def generate_token(
        self, user_id: str, user_secret: str, token_version: int = 0
    ) -> tuple[str, int]:
        """
        Generate a JWT token for a user using their secret.

        Args:
            user_id: The user ID to encode in the token.
            user_secret: The user's personal JWT secret.
            token_version: Current token version for revocation support.

        Returns:
            Tuple of (token, expires_in_seconds).
        """
        expiry = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
        expires_in = JWT_EXPIRY_HOURS * 3600

        payload = {
            "sub": user_id,
            "exp": expiry,
            "iat": datetime.now(timezone.utc),
            "type": "access",
            "tv": token_version,
        }

        token = jwt.encode(payload, user_secret, algorithm=JWT_ALGORITHM)
        return token, expires_in

    async def validate_token(self, token: str, db: AsyncSession) -> Optional[str]:
        """
        Validate a JWT token using per-user secret (two-phase decode).

        Also validates that the user's filesystem environment is properly
        configured. If the user's home directory or venv is missing,
        authentication fails with UserEnvironmentError.

        Args:
            token: The JWT token to validate.
            db: Database session.

        Returns:
            User ID if valid, None otherwise.

        Raises:
            UserEnvironmentError: If user's environment is misconfigured.
        """
        try:
            # Phase 1: Decode without verification to get user_id
            unverified = jwt.decode(token, options={"verify_signature": False})
            user_id = unverified.get("sub")
            if not user_id:
                return None

            # Phase 2: Fetch user and verify with their secret
            user = await self.get_user_by_id(db, user_id)
            if not user or not user.is_active:
                return None

            # Phase 3: Validate user's filesystem environment
            # Skip for API-only roles (admin, reseller) that don't use sandboxes
            if user.role not in ("admin", "reseller"):
                self.validate_user_environment(user.username)

            # Verify with user's secret
            payload = jwt.decode(token, user.jwt_secret, algorithms=[JWT_ALGORITHM])

            # Check token version for revocation
            token_tv = payload.get("tv", 0)
            if token_tv != user.token_version:
                logger.debug("Token revoked (version mismatch: token=%d, user=%d)", token_tv, user.token_version)
                return None

            return payload.get("sub")

        except UserEnvironmentError:
            # Re-raise environment errors - these should propagate to caller
            raise
        except jwt.ExpiredSignatureError:
            logger.debug("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug(f"Invalid token: {e}")
            return None

    async def authenticate(
        self,
        db: AsyncSession,
        email: str,
        password: str
    ) -> tuple[User, str, int]:
        """
        Authenticate user and return token.

        Also validates that the user's filesystem environment is properly
        configured before allowing login.

        Args:
            db: Database session.
            email: User email.
            password: User password.

        Returns:
            Tuple of (User, token, expires_in_seconds).

        Raises:
            ValueError: If authentication fails.
            UserEnvironmentError: If user's environment is misconfigured.
        """
        import bcrypt

        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise ValueError("Invalid credentials")

        if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            raise ValueError("Invalid credentials")

        # Validate user's filesystem environment before issuing token
        # Skip for API-only roles (admin, reseller) that don't use sandboxes
        if user.role not in ("admin", "reseller"):
            self.validate_user_environment(user.username)

        token, expires_in = self.generate_token(user.id, user.jwt_secret, user.token_version)
        return user, token, expires_in

    async def get_user_by_id(
        self,
        db: AsyncSession,
        user_id: str
    ) -> Optional[User]:
        """
        Get a user by ID.

        Args:
            db: Database session.
            user_id: The user ID to look up.

        Returns:
            User if found, None otherwise.
        """
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def revoke_tokens(self, db: AsyncSession, user_id: str) -> None:
        """
        Revoke all existing tokens for a user by incrementing token_version.

        Args:
            db: Database session.
            user_id: The user whose tokens should be revoked.
        """
        user = await self.get_user_by_id(db, user_id)
        if user:
            user.token_version += 1
            await db.commit()
            logger.info("Revoked all tokens for user %s (new version: %d)", user_id, user.token_version)

    async def change_password(
        self,
        db: AsyncSession,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> tuple[str, int]:
        """
        Change user password and revoke all existing tokens.

        Args:
            db: Database session.
            user_id: The user ID.
            current_password: Current password for verification.
            new_password: New password to set.

        Returns:
            Tuple of (new_token, expires_in_seconds).

        Raises:
            ValueError: If current password is wrong or user not found.
        """
        import bcrypt

        user = await self.get_user_by_id(db, user_id)
        if not user:
            raise ValueError("User not found")

        if not bcrypt.checkpw(current_password.encode(), user.password_hash.encode()):
            raise ValueError("Invalid current password")

        # Update password
        user.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        # Revoke all existing tokens
        user.token_version += 1
        await db.commit()

        # Issue a new token with the updated version
        token, expires_in = self.generate_token(user.id, user.jwt_secret, user.token_version)
        logger.info("Password changed for user %s, tokens revoked (version: %d)", user_id, user.token_version)
        return token, expires_in

    async def delete_user(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> bool:
        """
        Delete a user by ID.

        Args:
            db: Database session.
            user_id: The user ID to delete.

        Returns:
            True if user was deleted, False if not found.
        """
        from sqlalchemy import delete

        result = await db.execute(
            delete(User).where(User.id == user_id)
        )
        await db.commit()
        return result.rowcount > 0


# Global auth service instance
auth_service = AuthService()
