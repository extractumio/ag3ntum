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

from ..db.models import User

logger = logging.getLogger(__name__)

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

    def generate_token(self, user_id: str, user_secret: str) -> tuple[str, int]:
        """
        Generate a JWT token for a user using their secret.

        Args:
            user_id: The user ID to encode in the token.
            user_secret: The user's personal JWT secret.

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
        }

        token = jwt.encode(payload, user_secret, algorithm=JWT_ALGORITHM)
        return token, expires_in

    async def validate_token(self, token: str, db: AsyncSession) -> Optional[str]:
        """
        Validate a JWT token using per-user secret (two-phase decode).

        Args:
            token: The JWT token to validate.
            db: Database session.

        Returns:
            User ID if valid, None otherwise.
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

            # Verify with user's secret
            payload = jwt.decode(token, user.jwt_secret, algorithms=[JWT_ALGORITHM])
            return payload.get("sub")

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

        Args:
            db: Database session.
            email: User email.
            password: User password.

        Returns:
            Tuple of (User, token, expires_in_seconds).

        Raises:
            ValueError: If authentication fails.
        """
        import bcrypt

        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise ValueError("Invalid credentials")

        if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
            raise ValueError("Invalid credentials")

        token, expires_in = self.generate_token(user.id, user.jwt_secret)
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

    async def create_user(
        self,
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
        role: str = "user",
    ) -> tuple[User, str, int]:
        """
        Create a new user and return token.

        Args:
            db: Database session.
            username: Unique username.
            email: Unique email address.
            password: Plain text password (will be hashed).
            role: User role (default: "user").

        Returns:
            Tuple of (User, token, expires_in_seconds).

        Raises:
            ValueError: If username or email already exists.
        """
        import secrets
        import uuid

        import bcrypt

        # Check if username or email already exists
        existing = await db.execute(
            select(User).where(
                (User.username == username) | (User.email == email)
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("Username or email already exists")

        # Generate password hash and JWT secret
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        jwt_secret = secrets.token_urlsafe(32)

        # Create user
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            email=email,
            password_hash=password_hash,
            role=role,
            jwt_secret=jwt_secret,
            linux_uid=None,
            is_active=True,
        )

        db.add(user)
        await db.commit()
        await db.refresh(user)

        # Generate token
        token, expires_in = self.generate_token(user.id, user.jwt_secret)
        return user, token, expires_in

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

