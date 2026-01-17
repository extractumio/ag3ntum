"""User management service."""
import logging
import re
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import USERS_DIR, CONFIG_DIR
from ..db.models import User

logger = logging.getLogger(__name__)

# Default requirements file for user venvs
DEFAULT_USER_REQUIREMENTS = CONFIG_DIR / "user_requirements.txt"

# Default secrets template for user environments
DEFAULT_USER_SECRETS_TEMPLATE = CONFIG_DIR / "user_secrets.yaml.template"


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

            # Create user-specific Python venv
            # This is separate from the backend venv and mounted read-only in sandbox
            self._create_user_venv(home_dir, username)

            # Create user secrets.yaml from template
            # User can add API keys here after registration
            self._create_user_secrets(home_dir, username)

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

    def _create_user_venv(self, home_dir: Path, username: str) -> None:
        """
        Create user-specific Python virtual environment.

        This venv is separate from the backend system venv and is:
        - Located at /users/<username>/venv/
        - Mounted read-only at /venv inside the sandbox
        - Has its own requirements.txt for user customization

        Args:
            home_dir: User's home directory path
            username: Username for logging
        """
        venv_dir = home_dir / "venv"
        requirements_file = home_dir / "requirements.txt"

        # Skip if venv already exists
        if (venv_dir / "bin" / "python3").exists():
            logger.info(f"User venv already exists for {username}, skipping creation")
            return

        logger.info(f"Creating user venv for {username} at {venv_dir}")

        try:
            # Create the venv using system Python
            # Use --system-site-packages=false to keep it isolated
            subprocess.run(
                ["python3", "-m", "venv", str(venv_dir)],
                check=True,
                capture_output=True,
            )
            logger.info(f"Created venv at {venv_dir}")

            # Copy default requirements.txt if it doesn't exist
            if not requirements_file.exists() and DEFAULT_USER_REQUIREMENTS.exists():
                shutil.copy(DEFAULT_USER_REQUIREMENTS, requirements_file)
                logger.info(f"Copied default requirements to {requirements_file}")
            elif not requirements_file.exists():
                # Create minimal requirements if default doesn't exist
                requirements_file.write_text(
                    "# User Python environment requirements\n"
                    "# Add packages here and run: pip install -r requirements.txt\n"
                    "requests>=2.31.0\n"
                )
                logger.warning(
                    f"Default requirements not found at {DEFAULT_USER_REQUIREMENTS}, "
                    f"created minimal requirements for {username}"
                )

            # Install requirements into the venv
            pip_path = venv_dir / "bin" / "pip"
            if requirements_file.exists():
                logger.info(f"Installing requirements for {username}...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    capture_output=True,
                    timeout=300,  # 5 minute timeout for pip install
                )
                if result.returncode != 0:
                    logger.warning(
                        f"pip install had issues for {username}: {result.stderr.decode()[:500]}"
                    )
                else:
                    logger.info(f"Installed requirements for {username}")

            # Set permissions - venv should be readable but owned by user
            venv_dir.chmod(0o755)
            for item in venv_dir.rglob("*"):
                try:
                    if item.is_dir():
                        item.chmod(0o755)
                    else:
                        item.chmod(0o644)
                except PermissionError:
                    pass  # Some files may already have restricted perms

            # Make binaries executable
            bin_dir = venv_dir / "bin"
            if bin_dir.exists():
                for binary in bin_dir.iterdir():
                    try:
                        binary.chmod(0o755)
                    except PermissionError:
                        pass

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create venv for {username}: {e.stderr.decode()}")
            # Don't raise - venv creation is not critical for user creation
            # User can recreate it manually if needed
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout creating venv for {username}")
        except Exception as e:
            logger.error(f"Unexpected error creating venv for {username}: {e}")

    def _create_user_secrets(self, home_dir: Path, username: str) -> None:
        """
        Create user-specific secrets.yaml from template.

        This file contains API keys and other secrets that are:
        - Located at /users/<username>/ag3ntum/secrets.yaml
        - Readable only by the user (chmod 600)
        - Passed to sandbox as environment variables via sandboxed_envs

        The ag3ntum subdirectory is created to match the expected path in
        load_sandboxed_envs() function in config.py.

        Args:
            home_dir: User's home directory path
            username: Username for logging
        """
        # Create ag3ntum config directory
        ag3ntum_dir = home_dir / "ag3ntum"
        ag3ntum_dir.mkdir(parents=True, exist_ok=True)
        ag3ntum_dir.chmod(0o700)

        secrets_file = ag3ntum_dir / "secrets.yaml"

        # Skip if secrets already exists
        if secrets_file.exists():
            logger.info(f"User secrets already exists for {username}, skipping creation")
            return

        logger.info(f"Creating user secrets.yaml for {username}")

        try:
            if DEFAULT_USER_SECRETS_TEMPLATE.exists():
                shutil.copy(DEFAULT_USER_SECRETS_TEMPLATE, secrets_file)
                logger.info(f"Copied secrets template to {secrets_file}")
            else:
                # Create minimal secrets file if template doesn't exist
                # Must use sandboxed_envs: section format for load_sandboxed_envs()
                secrets_file.write_text(
                    "# User secrets configuration\n"
                    "# Add your API keys here\n"
                    "#\n"
                    "# These are passed to the sandbox as environment variables\n"
                    "\n"
                    "sandboxed_envs:\n"
                    "  # Google Gemini API key\n"
                    "  GEMINI_API_KEY: \"\"\n"
                    "\n"
                    "  # OpenAI API key\n"
                    "  OPENAI_API_KEY: \"\"\n"
                    "\n"
                    "  # Anthropic Claude API key\n"
                    "  ANTHROPIC_API_KEY: \"\"\n"
                )
                logger.warning(
                    f"Secrets template not found at {DEFAULT_USER_SECRETS_TEMPLATE}, "
                    f"created minimal secrets for {username}"
                )

            # Set strict permissions - only user can read
            secrets_file.chmod(0o600)
            logger.info(f"Set secrets.yaml permissions to 600 for {username}")

        except Exception as e:
            logger.error(f"Failed to create secrets for {username}: {e}")
            # Don't raise - secrets creation is not critical for user creation
            # User can create it manually if needed


user_service = UserService()
