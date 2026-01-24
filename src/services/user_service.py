"""User management service.

This module handles user creation, deletion, and management with support for
two UID mapping modes:

Mode A: Isolated Range (Default)
    - UIDs allocated from dedicated range (legacy: 2000-49999, new: 50000-60000)
    - Safer for multi-tenant deployments
    - Set AG3NTUM_UID_MODE=isolated (default)

Mode B: Direct Host Mapping (Opt-in)
    - UIDs map to host system UIDs (1000-65533)
    - Set AG3NTUM_UID_MODE=direct
    - WARNING: Requires understanding of security implications

Security invariants enforced regardless of mode:
    - UID 0 (root) is NEVER allocated
    - System UIDs (1-999) are never used
    - Each user gets a unique UID validated against seccomp policies
"""
import logging
import os
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
from ..core.uid_security import (
    UIDMode,
    UIDSecurityConfig,
    get_uid_security_config,
    validate_uid_for_setuid,
    log_uid_operation,
)

logger = logging.getLogger(__name__)

# Default requirements file for user venvs
DEFAULT_USER_REQUIREMENTS = CONFIG_DIR / "user_requirements.txt"

# Default secrets template for user environments
DEFAULT_USER_SECRETS_TEMPLATE = CONFIG_DIR / "user_secrets.yaml.template"


class UserService:
    """Service for user management and Linux user creation.

    Supports two UID allocation modes:
    - ISOLATED (default): UIDs from dedicated range, safer for multi-tenant
    - DIRECT: UIDs map to host UIDs, simpler for dev/single-tenant

    Set AG3NTUM_UID_MODE environment variable to select mode.
    """

    def __init__(self):
        """Initialize the user service with UID security configuration."""
        self._uid_config: Optional[UIDSecurityConfig] = None

    @property
    def uid_config(self) -> UIDSecurityConfig:
        """Get the UID security configuration (lazy loaded)."""
        if self._uid_config is None:
            self._uid_config = get_uid_security_config()
        return self._uid_config

    async def create_user(
        self,
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
        role: str = "user",
        uid_mode: Optional[UIDMode] = None,
    ) -> User:
        """
        Create a new user with Linux account.

        Steps:
        1. Validate username/email uniqueness
        2. Hash password with bcrypt
        3. Generate per-user JWT secret
        4. Generate UID based on current mode (isolated or direct)
        5. Validate UID against security policies
        6. Create Linux user with sudo useradd
        7. Store linux_uid in database
        8. Create user directories

        Args:
            db: Database session
            username: Unique username (3-32 chars, alphanumeric)
            email: User email address
            password: User password (will be hashed)
            role: User role (default: "user")
            uid_mode: Override UID mode for this user (default: use global config)

        Raises:
            ValueError: If user already exists, creation fails, or UID validation fails
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

        # Determine UID mode and generate UID
        effective_mode = uid_mode or self.uid_config.mode
        linux_uid = await self._generate_next_uid(db, effective_mode)

        # SECURITY: Validate the generated UID
        uid_valid, uid_reason = validate_uid_for_setuid(linux_uid, self.uid_config)
        if not uid_valid:
            log_uid_operation("create_user", linux_uid, success=False, reason=uid_reason)
            raise ValueError(f"Generated UID {linux_uid} failed security validation: {uid_reason}")

        log_uid_operation("create_user", linux_uid, success=True)
        logger.info(
            f"Creating user {username} with UID {linux_uid} "
            f"(mode: {effective_mode.value})"
        )

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

    async def _generate_next_uid(
        self,
        db: AsyncSession,
        mode: Optional[UIDMode] = None,
    ) -> int:
        """Generate next available UID based on the configured mode.

        For ISOLATED mode:
            - New users: UIDs from 50000-60000
            - Legacy users: UIDs from 2000-49999 (still valid)

        For DIRECT mode:
            - UIDs from 1000-65533 (maps to host users)

        Args:
            db: Database session
            mode: UID mode to use (default: use global config)

        Returns:
            Next available UID in the valid range

        Raises:
            ValueError: If no valid UIDs are available in the range
        """
        effective_mode = mode or self.uid_config.mode

        # Get the starting UID for new allocations based on mode
        if effective_mode == UIDMode.ISOLATED:
            # For isolated mode, prefer the new range (50000+) for new users
            # Legacy users (2000-49999) are still valid but we don't allocate there
            min_uid = self.uid_config.isolated_uid_min
            max_uid = self.uid_config.isolated_uid_max
        else:
            # For direct mode, use host user range
            min_uid = self.uid_config.direct_uid_min
            max_uid = self.uid_config.direct_uid_max

        # Check for existing users in the target range
        result = await db.execute(
            select(User.linux_uid)
            .where(User.linux_uid >= min_uid)
            .where(User.linux_uid <= max_uid)
            .order_by(User.linux_uid.desc())
            .limit(1)
        )
        max_existing = result.scalar_one_or_none()

        if max_existing is not None:
            next_uid = max_existing + 1
        else:
            next_uid = min_uid

        # Verify we haven't exceeded the range
        if next_uid > max_uid:
            raise ValueError(
                f"UID range exhausted for mode {effective_mode.value}. "
                f"Range [{min_uid}, {max_uid}] is full."
            )

        logger.debug(
            f"Generated UID {next_uid} for mode {effective_mode.value} "
            f"(range: {min_uid}-{max_uid})"
        )

        return next_uid

    def _create_linux_user(self, username: str, uid: int) -> None:
        """
        Create Linux user with sudo useradd and set up tiered directory permissions.

        Permission Model (Tiered Access):
        ================================

        Tier 1 - Traverse-only (API can validate existence but not list):
          /users/{username}/      mode 711 (drwx--x--x) - traverse only
          /users/{username}/venv/ mode 711 (drwx--x--x) - traverse only (hides package list)

        Tier 2 - Operational (API + User via group):
          /users/{username}/sessions/ mode 770 (drwxrwx---) group=ag3ntum

        Tier 3 - Private (User only, accessed via sandbox UID switch):
          /users/{username}/ag3ntum/  mode 700 (drwx------) - secrets
          /users/{username}/.claude/  mode 700 (drwx------) - user skills

        Why this works:
        - API (UID 45045) can validate venv/bin/python3 exists (711 allows stat on children)
        - Package list in venv is hidden from other users (no read permission on dirs)
        - API can manage sessions via ag3ntum group membership
        - Secrets remain private, only accessible when sandbox runs as user's UID
        """
        home_dir = Path(f"/users/{username}")

        # Strategy:
        # 1. Ensure directory exists and we own it (so we can set permissions)
        # 2. Create directory structure with proper permissions
        # 3. Create Linux user with ag3ntum as supplementary group
        # 4. Transfer ownership with correct group settings

        # 1. Ensure directory exists
        try:
            home_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            logger.warning(f"Could not mkdir {home_dir}, assuming it exists. Attempting to claim ownership.")

        # Claim ownership to ag3ntum_api so we can manipulate it
        try:
            subprocess.run(
                ["sudo", "chown", "-R", "ag3ntum_api:ag3ntum_api", str(home_dir)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to claim ownership of {home_dir}: {e.stderr.decode()}")
            raise ValueError(f"Failed to setup user directory: {e}")

        # 2. Create directory structure with tiered permissions
        try:
            # TIER 1: Public - allows API to validate user environment
            # Home dir: 711 (execute-only for others - allows traversal but not listing)
            home_dir.chmod(0o711)

            # TIER 2: Operational - API needs access for session management
            # Sessions dir: 770 with group ag3ntum (API user is in this group)
            sessions_dir = home_dir / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            sessions_dir.chmod(0o770)

            # TIER 3: Private - only user can access (via sandbox UID switch)
            # .claude/skills directory for user skills
            skills_dir = home_dir / ".claude" / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            (home_dir / ".claude").chmod(0o700)
            skills_dir.chmod(0o700)

            # ag3ntum directory for user-specific config and secrets
            ag3ntum_dir = home_dir / "ag3ntum"
            ag3ntum_dir.mkdir(parents=True, exist_ok=True)
            ag3ntum_dir.chmod(0o700)

            # Persistent storage directory (inside ag3ntum, so private)
            persistent_dir = ag3ntum_dir / "persistent"
            persistent_dir.mkdir(parents=True, exist_ok=True)
            persistent_dir.chmod(0o700)

            # Create README explaining persistent storage
            readme_path = persistent_dir / "README.md"
            if not readme_path.exists():
                readme_path.write_text(
                    "# Persistent Storage\n\n"
                    "Files in this directory persist across sessions.\n\n"
                    "## Access from Agent Sessions\n"
                    "```\n"
                    "./external/persistent/\n"
                    "```\n\n"
                    "## Use Cases\n"
                    "- Cache data you want to reuse between sessions\n"
                    "- Store files that should survive session cleanup\n"
                    "- Share data between multiple sessions\n"
                )

            # Create user-specific Python venv (TIER 1: public, read-only)
            self._create_user_venv(home_dir, username)

            # Create user secrets.yaml from template (inside ag3ntum, so private)
            self._create_user_secrets(home_dir, username)

        except PermissionError as e:
            logger.error(f"Failed to chmod/mkdir {home_dir}: {e}")
            raise ValueError(f"Failed to set directory permissions: {e}")

        # 3. Create Linux user with ag3ntum as supplementary group
        # This allows the user to access group-writable directories
        try:
            subprocess.run(
                ["sudo", "useradd", "-M", "-d", str(home_dir), "-s", "/bin/bash",
                 "-u", str(uid), "-G", "ag3ntum", username],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            if e.returncode == 9:
                logger.warning(f"Linux user {username} already exists. Proceeding with directory setup.")
                # Add user to ag3ntum group if not already
                try:
                    subprocess.run(
                        ["sudo", "usermod", "-a", "-G", "ag3ntum", username],
                        check=True,
                        capture_output=True,
                    )
                except subprocess.CalledProcessError:
                    pass  # Group may not exist or user already in it
            else:
                logger.error(f"Failed to create Linux user {username}: {e.stderr.decode()}")
                raise ValueError(f"Failed to create Linux user: {e.stderr.decode()}")

        # 4. Transfer ownership with correct group settings
        # - Home dir and private paths: user:user (UID:UID)
        # - Sessions dir: user:ag3ntum (for API access)
        # - venv: user:user with mode 711 (traverse-only, hides package list)
        try:
            # First, set ownership of everything to user:user
            subprocess.run(
                ["sudo", "chown", "-R", f"{uid}:{uid}", str(home_dir)],
                check=True,
                capture_output=True,
            )

            # Then, set sessions dir group to ag3ntum for API access
            subprocess.run(
                ["sudo", "chgrp", "ag3ntum", str(sessions_dir)],
                check=True,
                capture_output=True,
            )

            # Set venv to traverse-only (711) - hides package list from other users
            # while allowing API to verify python3 binary exists via stat()
            # Subdirectories (lib, bin) also get 711 for traversal
            venv_dir = home_dir / "venv"
            if venv_dir.exists():
                # Set all directories to 711 (traverse only)
                subprocess.run(
                    ["sudo", "find", str(venv_dir), "-type", "d", "-exec", "chmod", "711", "{}", ";"],
                    check=True,
                    capture_output=True,
                )
                # Set all files to 644 (readable by owner, but hidden due to parent 711)
                subprocess.run(
                    ["sudo", "find", str(venv_dir), "-type", "f", "-exec", "chmod", "644", "{}", ";"],
                    check=True,
                    capture_output=True,
                )
                # Make bin files executable (755) - needed for python3, pip, etc.
                bin_dir = venv_dir / "bin"
                if bin_dir.exists():
                    subprocess.run(
                        ["sudo", "find", str(bin_dir), "-type", "f", "-exec", "chmod", "755", "{}", ";"],
                        check=True,
                        capture_output=True,
                    )

            # Re-apply permissions that may have been changed by chown -R
            subprocess.run(
                ["sudo", "chmod", "711", str(home_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["sudo", "chmod", "770", str(sessions_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["sudo", "chmod", "700", str(ag3ntum_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["sudo", "chmod", "700", str(home_dir / ".claude")],
                check=True,
                capture_output=True,
            )

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to set final ownership for {home_dir}: {e.stderr.decode()}")
            raise ValueError(f"Failed to set final ownership: {e}")

        logger.info(f"Created/Updated Linux user {username} with UID {uid} (tiered permissions)")

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


    async def delete_user(
        self,
        db: AsyncSession,
        username: str,
        delete_linux_user: bool = True,
    ) -> bool:
        """
        Delete a user and their associated resources.

        This method is primarily intended for test cleanup but can be used
        for user account deletion.

        Steps:
        1. Find user in database
        2. Delete Linux user (if exists and delete_linux_user=True)
        3. Remove user home directory
        4. Remove user from database

        Args:
            db: Database session
            username: Username to delete
            delete_linux_user: Whether to delete the Linux user account

        Returns:
            True if user was deleted, False if user not found

        Raises:
            ValueError: If deletion fails
        """
        # Find user in database
        result = await db.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()

        if not user:
            logger.warning(f"User {username} not found in database")
            return False

        home_dir = USERS_DIR / username

        # Delete Linux user if requested
        if delete_linux_user and user.linux_uid:
            try:
                self._delete_linux_user(username)
            except Exception as e:
                logger.warning(f"Failed to delete Linux user {username}: {e}")
                # Continue with cleanup even if Linux user deletion fails

        # Remove home directory
        if home_dir.exists():
            try:
                shutil.rmtree(home_dir)
                logger.info(f"Removed home directory for {username}")
            except Exception as e:
                logger.warning(f"Failed to remove home directory for {username}: {e}")
                # Try with sudo if normal deletion fails
                try:
                    subprocess.run(
                        ["sudo", "rm", "-rf", str(home_dir)],
                        check=True,
                        capture_output=True,
                    )
                    logger.info(f"Removed home directory for {username} with sudo")
                except subprocess.CalledProcessError as e:
                    logger.error(f"Failed to remove home directory with sudo: {e}")

        # Delete from database
        await db.delete(user)
        await db.commit()

        logger.info(f"Deleted user {username}")
        return True

    def _delete_linux_user(self, username: str) -> None:
        """Delete Linux user account."""
        try:
            subprocess.run(
                ["sudo", "userdel", username],
                check=True,
                capture_output=True,
            )
            logger.info(f"Deleted Linux user {username}")
        except subprocess.CalledProcessError as e:
            if e.returncode == 6:
                # User doesn't exist - that's fine
                logger.debug(f"Linux user {username} doesn't exist")
            else:
                logger.warning(f"Failed to delete Linux user {username}: {e.stderr.decode()}")
                raise

    def cleanup_test_users(self, pattern: str = "testuser_") -> int:
        """
        Clean up test user directories from /users/.

        This method removes directories that match the test user pattern
        without requiring database access. Use this for manual cleanup
        or when the database is unavailable.

        Args:
            pattern: Pattern prefix to match (default: "testuser_")

        Returns:
            Number of directories removed
        """
        removed = 0
        patterns_to_clean = [pattern, "testuser2_", "e2e_user_"]

        for p in patterns_to_clean:
            for user_dir in USERS_DIR.glob(f"{p}*"):
                if user_dir.is_dir():
                    try:
                        # Try normal deletion first
                        shutil.rmtree(user_dir)
                        logger.info(f"Removed test user directory: {user_dir}")
                        removed += 1
                    except PermissionError:
                        # Try with sudo
                        try:
                            subprocess.run(
                                ["sudo", "rm", "-rf", str(user_dir)],
                                check=True,
                                capture_output=True,
                            )
                            logger.info(f"Removed test user directory with sudo: {user_dir}")
                            removed += 1
                        except subprocess.CalledProcessError as e:
                            logger.error(f"Failed to remove {user_dir}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to remove {user_dir}: {e}")

        # Also try to delete corresponding Linux users
        for p in patterns_to_clean:
            try:
                result = subprocess.run(
                    ["getent", "passwd"],
                    capture_output=True,
                    text=True,
                )
                for line in result.stdout.splitlines():
                    username = line.split(":")[0]
                    if username.startswith(p):
                        try:
                            subprocess.run(
                                ["sudo", "userdel", username],
                                check=True,
                                capture_output=True,
                            )
                            logger.info(f"Deleted Linux user: {username}")
                        except subprocess.CalledProcessError:
                            pass  # User might already be deleted
            except Exception as e:
                logger.debug(f"Could not enumerate Linux users: {e}")

        return removed


user_service = UserService()
