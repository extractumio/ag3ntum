"""
Centralized test user management for automatic cleanup.

This module provides a TestUserManager class that handles the complete
lifecycle of test users - creation, tracking, and cleanup. All test fixtures
that need users should use this manager to ensure proper cleanup.

Usage:
    manager = TestUserManager()
    user = await manager.create_user(session_factory, role="user")
    # ... run tests ...
    await manager.cleanup(session_factory)  # Cleans up all created users
"""
import hashlib
import secrets
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Try to import bcrypt, fall back to hashlib if not available
try:
    import bcrypt
    HAS_BCRYPT = True
except ImportError:
    HAS_BCRYPT = False


@dataclass
class TestUserData:
    """Data structure for test user credentials and metadata."""
    id: str
    username: str
    email: str
    password: str
    password_hash: str
    role: str
    jwt_secret: str
    linux_uid: Optional[int] = None
    is_active: bool = True

    def to_dict(self) -> dict:
        """Convert to dictionary for fixture compatibility."""
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "password": self.password,
            "role": self.role,
            "jwt_secret": self.jwt_secret,
        }


@dataclass
class TestUserManager:
    """
    Centralized manager for test user lifecycle.

    Handles user creation with unique identifiers, tracks all created users,
    and provides cleanup that removes both database records and filesystem
    artifacts (user directories, Linux users if applicable).

    Usage:
        manager = TestUserManager()

        # Create users
        user1 = await manager.create_user(session_factory)
        user2 = await manager.create_user(session_factory, role="admin")

        # After tests, cleanup everything
        await manager.cleanup(session_factory)
    """

    # Prefix for test usernames to identify them for cleanup
    username_prefix: str = "testuser_"

    # Base password for test users (can be overridden per-user)
    default_password: str = "test123"

    # Track all created users for cleanup
    _created_users: list[TestUserData] = field(default_factory=list)

    # Users directory for filesystem cleanup
    users_dir: Path = field(default_factory=lambda: Path("/users"))

    def _hash_password(self, password: str) -> str:
        """Hash password using bcrypt if available, otherwise SHA256."""
        if HAS_BCRYPT:
            return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        else:
            return hashlib.sha256(password.encode()).hexdigest()

    def _generate_unique_id(self) -> str:
        """Generate a unique 8-character identifier."""
        return str(uuid.uuid4())[:8]

    def _create_user_directories(self, username: str) -> None:
        """
        Create the required directory structure for a test user.

        Creates:
        - {users_dir}/{username}/sessions/ - For session storage
        - {users_dir}/{username}/ag3ntum/persistent/ - For persistent storage

        This mirrors the production directory structure created during
        user registration.
        """
        user_home = self.users_dir / username

        # Create sessions directory
        sessions_dir = user_home / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        # Create persistent storage directory
        persistent_dir = user_home / "ag3ntum" / "persistent"
        persistent_dir.mkdir(parents=True, exist_ok=True)

    async def create_user(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        role: str = "user",
        username_prefix: Optional[str] = None,
        password: Optional[str] = None,
        email_domain: str = "example.com",
        linux_uid: Optional[int] = None,
        is_active: bool = True,
    ) -> TestUserData:
        """
        Create a test user with unique credentials.

        Args:
            session_factory: Async session factory for database operations
            role: User role ("user" or "admin")
            username_prefix: Custom prefix for username (default: "testuser_")
            password: Custom password (default: "test123")
            email_domain: Domain for email address
            linux_uid: Optional Linux UID for the user
            is_active: Whether the user is active

        Returns:
            TestUserData with all user credentials
        """
        # Import here to avoid circular imports
        from src.db.models import User

        # Generate unique identifiers
        unique_id = self._generate_unique_id()
        prefix = username_prefix or self.username_prefix

        # Handle different prefixes for different user types
        if role == "admin":
            prefix = "testadmin_"

        username = f"{prefix}{unique_id}"
        email = f"{prefix.rstrip('_')}_{unique_id}@{email_domain}"
        pwd = password or self.default_password

        # Create user data
        user_data = TestUserData(
            id=str(uuid.uuid4()),
            username=username,
            email=email,
            password=pwd,
            password_hash=self._hash_password(pwd),
            role=role,
            jwt_secret=secrets.token_urlsafe(32),
            linux_uid=linux_uid,
            is_active=is_active,
        )

        # Create user directory structure (sessions, persistent storage)
        self._create_user_directories(username)

        # Create user in database
        user = User(
            id=user_data.id,
            username=user_data.username,
            email=user_data.email,
            password_hash=user_data.password_hash,
            role=user_data.role,
            jwt_secret=user_data.jwt_secret,
            linux_uid=user_data.linux_uid,
            is_active=user_data.is_active,
        )

        async with session_factory() as session:
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # Track for cleanup
        self._created_users.append(user_data)

        return user_data

    async def create_second_user(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        **kwargs
    ) -> TestUserData:
        """
        Create a second test user (for isolation tests).

        Convenience method that uses "testuser2_" prefix and different password.
        """
        return await self.create_user(
            session_factory,
            username_prefix="testuser2_",
            password=kwargs.pop("password", "test456"),
            **kwargs
        )

    async def create_admin_user(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        **kwargs
    ) -> TestUserData:
        """
        Create a test admin user.

        Convenience method that sets role="admin" and uses appropriate prefix.
        """
        return await self.create_user(
            session_factory,
            role="admin",
            password=kwargs.pop("password", "adminpass123"),
            **kwargs
        )

    def _cleanup_user_directory(self, username: str) -> None:
        """Remove user directory from filesystem."""
        user_dir = self.users_dir / username
        if user_dir.exists() and user_dir.is_dir():
            try:
                shutil.rmtree(user_dir, ignore_errors=True)
            except Exception:
                # Try with sudo for permission issues
                try:
                    subprocess.run(
                        ["sudo", "rm", "-rf", str(user_dir)],
                        check=False,
                        capture_output=True,
                    )
                except Exception:
                    pass

    def _cleanup_linux_user(self, username: str) -> None:
        """Remove Linux user if it exists."""
        try:
            # Check if user exists
            result = subprocess.run(
                ["id", username],
                capture_output=True,
            )
            if result.returncode == 0:
                # User exists, try to delete
                subprocess.run(
                    ["sudo", "userdel", username],
                    check=False,
                    capture_output=True,
                )
        except Exception:
            pass

    async def cleanup_user(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_data: TestUserData,
        *,
        cleanup_filesystem: bool = True,
        cleanup_linux_user: bool = False,
    ) -> None:
        """
        Clean up a single user.

        Args:
            session_factory: Async session factory for database operations
            user_data: The user to clean up
            cleanup_filesystem: Whether to remove user directory
            cleanup_linux_user: Whether to remove Linux user
        """
        from sqlalchemy import delete
        from src.db.models import User

        # Remove from database
        async with session_factory() as session:
            await session.execute(
                delete(User).where(User.id == user_data.id)
            )
            await session.commit()

        # Filesystem cleanup
        if cleanup_filesystem:
            self._cleanup_user_directory(user_data.username)

        # Linux user cleanup
        if cleanup_linux_user:
            self._cleanup_linux_user(user_data.username)

        # Remove from tracking list
        if user_data in self._created_users:
            self._created_users.remove(user_data)

    async def cleanup(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        cleanup_filesystem: bool = True,
        cleanup_linux_user: bool = False,
    ) -> None:
        """
        Clean up all tracked users.

        This should be called after tests complete to ensure all test users
        are removed from the database and filesystem.

        Args:
            session_factory: Async session factory for database operations
            cleanup_filesystem: Whether to remove user directories
            cleanup_linux_user: Whether to remove Linux users
        """
        from sqlalchemy import delete
        from src.db.models import User

        # Batch delete from database for efficiency
        if self._created_users:
            user_ids = [u.id for u in self._created_users]
            async with session_factory() as session:
                await session.execute(
                    delete(User).where(User.id.in_(user_ids))
                )
                await session.commit()

        # Cleanup filesystem for each user
        for user_data in self._created_users:
            if cleanup_filesystem:
                self._cleanup_user_directory(user_data.username)
            if cleanup_linux_user:
                self._cleanup_linux_user(user_data.username)

        # Clear tracking list
        self._created_users.clear()

    def cleanup_filesystem_only(self) -> None:
        """
        Clean up only filesystem artifacts for all tracked users.

        Useful when database cleanup is handled separately (e.g., by
        dropping tables in test teardown).
        """
        for user_data in self._created_users:
            self._cleanup_user_directory(user_data.username)
        self._created_users.clear()

    @property
    def created_users(self) -> list[TestUserData]:
        """Get list of all created users (read-only)."""
        return list(self._created_users)

    @property
    def user_count(self) -> int:
        """Get count of created users."""
        return len(self._created_users)
