"""User management service."""
import logging
import re
import secrets
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import USERS_DIR
from ..db.models import User

logger = logging.getLogger(__name__)


class UserService:
    """Service for user management and Linux user creation."""

    async def create_user(
        self,
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
        role: str = "user",
    ) -> User:
        """
        Create a new user with Linux account.

        Steps:
        1. Validate username/email uniqueness
        2. Hash password with bcrypt
        3. Generate per-user JWT secret
        4. Create Linux user with sudo useradd
        5. Store linux_uid in database
        6. Create user directories

        Raises:
            ValueError: If user already exists or creation fails
        """
        # Validate username format (Linux username constraints)
        if not self._validate_username(username):
            raise ValueError(
                "Invalid username. Use 3-32 alphanumeric chars, start with letter."
            )

        # Check uniqueness
        existing = await db.execute(
            select(User).where(
                (User.username == username) | (User.email == email)
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("Username or email already exists")

        # Hash password
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        # Generate per-user JWT secret
        jwt_secret = secrets.token_urlsafe(32)

        # Generate UID (start from 2000 to avoid system users)
        linux_uid = await self._generate_next_uid(db)

        # Create Linux user with sudo
        try:
            self._create_linux_user(username, linux_uid)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create Linux user {username}: {e}")
            raise ValueError(f"Failed to create Linux user: {e}")

        # Create database record
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            email=email,
            password_hash=password_hash,
            role=role,
            jwt_secret=jwt_secret,
            linux_uid=linux_uid,
            is_active=True,
        )

        db.add(user)
        await db.commit()
        await db.refresh(user)

        logger.info(f"Created user {username} (UID: {linux_uid})")
        return user

    def _validate_username(self, username: str) -> bool:
        """Validate Linux username format."""
        # 3-32 chars, alphanumeric + underscore, start with letter
        pattern = r"^[a-z_][a-z0-9_]{2,31}$"
        return bool(re.match(pattern, username))

    async def _generate_next_uid(self, db: AsyncSession) -> int:
        """Generate next available UID (starting from 2000)."""
        result = await db.execute(
            select(User.linux_uid).order_by(User.linux_uid.desc()).limit(1)
        )
        max_uid = result.scalar_one_or_none()
        return (max_uid + 1) if max_uid and max_uid >= 2000 else 2000

    def _create_linux_user(self, username: str, uid: int) -> None:
        """Create Linux user with sudo useradd."""
        home_dir = Path(f"/users/{username}")

        # Strategy:
        # 1. Ensure directory exists and we own it (so we can set permissions)
        # 2. Set strict permissions (700)
        # 3. Create user (without creating home, since we managed it)
        # 4. Transfer ownership to the new user

        # 1. Ensure directory exists
        try:
            home_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            # Directory likely exists but we can't access it (e.g., wrong owner/perms)
            logger.warning(f"Could not mkdir {home_dir}, assuming it exists/permission denied. Attempting to claim ownership.")

        # Claim ownership to ag3ntum_api so we can manipulate it
        # This fixes cases where directory exists with bad perms (e.g. 000) or wrong owner
        try:
            subprocess.run(
                ["sudo", "chown", "-R", "ag3ntum_api:ag3ntum_api", str(home_dir)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to claim ownership of {home_dir}: {e.stderr.decode()}")
            raise ValueError(f"Failed to setup user directory: {e}")

        # 2. Set strict permissions (700)
        # We own it now, so we can chmod
        try:
            home_dir.chmod(0o700)
            
            # Create sessions directory while we have access
            sessions_dir = home_dir / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            sessions_dir.chmod(0o700)
            
            # Create .claude/skills directory for user skills
            # Required by sandbox configuration in permissions.yaml
            skills_dir = home_dir / ".claude" / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            skills_dir.chmod(0o700)
            
        except PermissionError as e:
            logger.error(f"Failed to chmod/mkdir {home_dir}: {e}")
            raise ValueError(f"Failed to set directory permissions: {e}")

        # 3. Create Linux user
        # Always use -M because we managed the directory ourselves
        # Check if user exists first to avoid error
        try:
            subprocess.run(
                ["sudo", "useradd", "-M", "-d", str(home_dir), "-s", "/bin/bash", "-u", str(uid), username],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            if e.returncode == 9:
                logger.warning(f"Linux user {username} already exists. Proceeding with directory setup.")
            else:
                logger.error(f"Failed to create Linux user {username}: {e.stderr.decode()}")
                raise ValueError(f"Failed to create Linux user: {e.stderr.decode()}")

        # 4. Transfer ownership to the new user
        try:
            subprocess.run(
                ["sudo", "chown", "-R", f"{uid}:{uid}", str(home_dir)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to set final ownership for {home_dir}: {e.stderr.decode()}")
            raise ValueError(f"Failed to set final ownership: {e}")

        logger.info(f"Created/Updated Linux user {username} with UID {uid}")


user_service = UserService()
