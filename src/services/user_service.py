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
# API user UID - used for logging only (access control is app-level via PathValidator)
API_UID = 45045

# Groups that must never be deleted by groupdel (system/infrastructure groups)
PROTECTED_GROUPS = frozenset({
    "ag3ntum", "ag3ntum_api", "root", "sudo", "adm", "nogroup",
})

logger = logging.getLogger(__name__)


def refresh_process_supplementary_groups() -> None:
    """Refresh this process's supplementary groups from /etc/group.

    After usermod adds ag3ntum_api to a new user's group, the running process
    still has stale groups. This reads the current groups from /etc/group and
    applies them via os.setgroups().

    Requires CAP_SETGID (retained via ambient capabilities in entrypoint).
    """
    try:
        result = subprocess.run(
            ["id", "-G", "ag3ntum_api"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            logger.warning(f"Failed to read groups for ag3ntum_api: {result.stderr.strip()}")
            return

        gids = [int(g) for g in result.stdout.strip().split()]
        os.setgroups(gids)
        logger.info(f"Refreshed supplementary groups: {len(gids)} groups loaded")
    except PermissionError:
        logger.warning(
            "Cannot refresh supplementary groups (no CAP_SETGID). "
            "Groups will update on next container restart."
        )
    except Exception as e:
        logger.warning(f"Failed to refresh supplementary groups: {e}")


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
        skip_venv_install: bool = False,
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
            skip_venv_install: Skip pip install in venv (faster for tests that don't need packages)

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
            self._create_linux_user(username, linux_uid, skip_venv_install=skip_venv_install)
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

    async def ensure_linux_users_exist(self, db: AsyncSession) -> dict:
        """
        Ensure all database users have Linux accounts in the container.

        Called on API startup. Linux users are ephemeral (lost on container
        rebuild) but database records and files persist. This recreates the
        Linux accounts and group memberships needed for the shared GID model.

        Only creates the account and sets up groups — does NOT touch
        directories, venvs, or secrets (those persist on disk).

        Returns:
            Dict with counts: created, skipped, failed
        """
        result = await db.execute(
            select(User).where(User.is_active == True)
        )
        users = result.scalars().all()

        stats = {"created": 0, "skipped": 0, "failed": 0}

        for user in users:
            if user.linux_uid is None:
                continue

            try:
                # Create Linux user (idempotent — useradd returns 9 if exists)
                base_cmd = [
                    "sudo", "useradd", "-M", "-d", f"/users/{user.username}",
                    "-s", "/bin/bash", "-u", str(user.linux_uid),
                ]
                proc = subprocess.run(
                    base_cmd + [user.username],
                    capture_output=True, text=True,
                )
                created = proc.returncode == 0

                if proc.returncode == 9:
                    # Check if user actually exists in /etc/passwd
                    check = subprocess.run(
                        ["getent", "passwd", user.username],
                        capture_output=True,
                    )
                    if check.returncode != 0:
                        # User NOT in passwd — group name conflict.
                        # Check if a group with this name exists and adopt it.
                        gid_check = subprocess.run(
                            ["getent", "group", user.username],
                            capture_output=True, text=True,
                        )
                        if gid_check.returncode == 0:
                            existing_gid = gid_check.stdout.strip().split(":")[2]
                            proc = subprocess.run(
                                base_cmd + ["-g", existing_gid, user.username],
                                capture_output=True, text=True,
                            )
                            created = proc.returncode == 0
                        else:
                            # Clean stale shadow entry and retry
                            subprocess.run(
                                ["sudo", "sed", "-i",
                                 f"/^{user.username}:/d", "/etc/shadow"],
                                capture_output=True,
                            )
                            proc = subprocess.run(
                                base_cmd + [user.username],
                                capture_output=True, text=True,
                            )
                            created = proc.returncode == 0

                if proc.returncode not in (0, 9):
                    logger.error(
                        f"Failed to ensure Linux user {user.username}: {proc.stderr.strip()}"
                    )
                    stats["failed"] += 1
                    continue

                # Add user to ag3ntum group
                subprocess.run(
                    ["sudo", "usermod", "-a", "-G", "ag3ntum", user.username],
                    capture_output=True, check=True,
                )

                # Add ag3ntum_api to user's primary group (shared GID)
                subprocess.run(
                    ["sudo", "usermod", "-a", "-G", user.username, "ag3ntum_api"],
                    capture_output=True, check=True,
                )

                if created:
                    logger.info(
                        f"Created Linux user {user.username} (UID {user.linux_uid})"
                    )
                    stats["created"] += 1
                else:
                    stats["skipped"] += 1

            except subprocess.CalledProcessError as e:
                logger.error(
                    f"Failed to set up groups for {user.username}: "
                    f"{e.stderr.strip() if e.stderr else e}"
                )
                stats["failed"] += 1

        # Refresh groups in case any new users were synced
        if stats["created"] > 0:
            refresh_process_supplementary_groups()

        return stats

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

        # Get highest existing UID from database in target range
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

        # Also check system users (handles case where database and /etc/passwd
        # are out of sync, e.g., during tests with in-memory database)
        system_uids = self._get_system_uids_in_range(min_uid, max_uid)

        # If the next UID is already in use by the system, find the next available
        while next_uid in system_uids:
            next_uid += 1
            if next_uid > max_uid:
                raise ValueError(
                    f"UID range exhausted for mode {effective_mode.value}. "
                    f"Range [{min_uid}, {max_uid}] is full."
                )
                break

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

    def _get_system_uids_in_range(self, min_uid: int, max_uid: int) -> set[int]:
        """Get all existing system UIDs in the specified range.

        Reads /etc/passwd to find UIDs that are already in use by the system.
        This handles cases where the database and system users are out of sync,
        such as during tests with an in-memory database.

        Args:
            min_uid: Minimum UID of range to check
            max_uid: Maximum UID of range to check

        Returns:
            Set of UIDs that exist in /etc/passwd within the range
        """
        system_uids: set[int] = set()
        try:
            result = subprocess.run(
                ["getent", "passwd"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split(":")
                    if len(parts) >= 3:
                        try:
                            uid = int(parts[2])
                            if min_uid <= uid <= max_uid:
                                system_uids.add(uid)
                        except ValueError:
                            continue
        except Exception as e:
            logger.warning(f"Could not read system UIDs from /etc/passwd: {e}")
        return system_uids

    def _create_linux_user(self, username: str, uid: int, skip_venv_install: bool = False) -> None:
        """
        Create Linux user with sudo useradd and set up group-based permissions.

        Permission Model (Shared GID):
        ================================
        Uses Unix group permissions for mutual access between API and sandbox
        processes. PathValidator is the primary security gate for cross-user
        and cross-session isolation.

        Two group relationships:
        1. Sandbox user → ag3ntum group (home dir, skills, persistent access)
        2. ag3ntum_api → sandbox user's primary group (session file access)

        This shared GID model allows 660/770 permissions on session files
        (no world access) while both API and sandbox can read/write.

        Security Layers:
        - Primary: PathValidator (application-level access control)
        - Secondary: Unix group permissions (750 home, 770 sessions, 660 files)

        Directory Structure:
          /users/{username}/           # 750 (owner rwx, ag3ntum group rx)
          ├── .claude/                 # 770 (owner rwx, ag3ntum group rwx)
          │   └── skills/              # 770 (API can write skills)
          ├── ag3ntum/                 # 750 (ag3ntum group rx for traverse)
          │   ├── persistent/          # 770 (ag3ntum group rwx for API writes)
          │   └── secrets.yaml         # 600 (owner only, protects API keys)
          ├── sessions/                # 770 (ag3ntum group rwx, API creates dirs)
          │   └── {session_id}/        # 770 owner:uid/group:uid (shared GID)
          │       └── workspace/       # 770, files 660 (API in user's group)
          └── venv/                    # 755 (needs to be executable by sandbox)

        Security Properties:
        - API accesses home dirs via ag3ntum group membership
        - API accesses session files via shared GID (user's primary group)
        - PathValidator blocks cross-user access (CROSS_USER_ACCESS_BLOCKED)
        - PathValidator blocks cross-session access (CROSS_SESSION_ACCESS_BLOCKED)
        - Cross-user isolation: each user has unique GID; ag3ntum_api is in all
          groups but PathValidator prevents cross-user file access
        - secrets.yaml is owner-only (600) even though ag3ntum/ is group-traversable
        """
        home_dir = Path(f"/users/{username}")

        # Strategy:
        # 1. Ensure directory exists and we own it (so we can set permissions)
        # 2. Create Linux user (must exist before we can set ownership)
        # 3. Create directory structure with basic 700 permissions
        # 4. Transfer ownership to user:user
        # 5. Set up group-based permissions for API access

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

        # 2. Create Linux user first (needs to exist for chown later)
        try:
            subprocess.run(
                ["sudo", "useradd", "-M", "-d", str(home_dir), "-s", "/bin/bash",
                 "-u", str(uid), username],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            if e.returncode == 9:
                # useradd returns 9 when the username is found in /etc/passwd,
                # /etc/shadow, or /etc/group. Verify the user actually exists
                # in /etc/passwd (required for usermod/chown to work).
                check = subprocess.run(
                    ["getent", "passwd", username],
                    capture_output=True,
                )
                if check.returncode == 0:
                    logger.warning(f"Linux user {username} already exists. Proceeding with directory setup.")
                else:
                    self._cleanup_stale_user_entries(username, home_dir, uid)
            else:
                logger.error(f"Failed to create Linux user {username}: {e.stderr.decode()}")
                raise ValueError(f"Failed to create Linux user: {e.stderr.decode()}")

        # 3. Create directory structure with 700 permissions
        try:
            # Home dir: 700 (owner only)
            home_dir.chmod(0o700)

            # Sessions dir: 700 (owner only, API accesses via parent dir group permissions)
            sessions_dir = home_dir / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            sessions_dir.chmod(0o700)

            # .claude/skills directory for user skills
            skills_dir = home_dir / ".claude" / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            (home_dir / ".claude").chmod(0o700)
            skills_dir.chmod(0o700)

            # ag3ntum directory for user-specific config and secrets
            ag3ntum_dir = home_dir / "ag3ntum"
            ag3ntum_dir.mkdir(parents=True, exist_ok=True)
            ag3ntum_dir.chmod(0o700)

            # Persistent storage directory (inside ag3ntum)
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
                    "./persistent/  OR  /persistent/\n"
                    "```\n\n"
                    "## Use Cases\n"
                    "- Cache data you want to reuse between sessions\n"
                    "- Store files that should survive session cleanup\n"
                    "- Share data between multiple sessions\n"
                )

            # Create user-specific Python venv
            self._create_user_venv(home_dir, username, skip_venv_install=skip_venv_install)

            # Create user secrets.yaml from template (inside ag3ntum, so private)
            self._create_user_secrets(home_dir, username)

        except PermissionError as e:
            logger.error(f"Failed to chmod/mkdir {home_dir}: {e}")
            raise ValueError(f"Failed to set directory permissions: {e}")

        # 4. Transfer ownership to user:primary_group
        # Use username as group (resolves to user's actual primary group) instead
        # of {uid}:{uid}, which creates files with non-existent GID when the
        # username matches a pre-existing group (e.g., 'ag3ntum' group GID 1001
        # vs UID 50000).
        try:
            subprocess.run(
                ["sudo", "chown", "-R", f"{uid}:{username}", str(home_dir)],
                check=True,
                capture_output=True,
            )

            # Set venv to 755 (needs to be executable by sandbox)
            # Note: Don't check exists() here - after chown, home_dir is 700 for user
            # and API can't stat venv. Just run chmod - it will succeed or fail gracefully.
            venv_dir = home_dir / "venv"
            subprocess.run(
                ["sudo", "chmod", "-R", "755", str(venv_dir)],
                check=False,  # Don't fail if venv doesn't exist
                capture_output=True,
            )

            # Re-apply 700 to other directories after chown
            subprocess.run(
                ["sudo", "chmod", "700", str(home_dir)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["sudo", "chmod", "700", str(sessions_dir)],
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
            logger.error(f"Failed to set ownership for {home_dir}: {e.stderr.decode()}")
            raise ValueError(f"Failed to set ownership: {e}")

        # 5. Set up group-based permissions for API access
        # Permission Model:
        # - Primary: Application-level access control (PathValidator)
        # - Secondary: Unix group permissions (750 for dirs, 770 for persistent)
        # - Session directories: 700 (owner only, no group access)
        self._setup_group_permissions(home_dir, uid, username)

        # Refresh this process's supplementary groups so the new user's
        # GID is immediately available for shared-GID file access
        refresh_process_supplementary_groups()

        logger.info(f"Created/Updated Linux user {username} with UID {uid}")

    def _create_user_venv(self, home_dir: Path, username: str, skip_venv_install: bool = False) -> None:
        """
        Create user-specific Python virtual environment.

        This venv is separate from the backend system venv and is:
        - Located at /users/<username>/venv/
        - Mounted read-only at /venv inside the sandbox
        - Has its own requirements.txt for user customization

        Args:
            home_dir: User's home directory path
            username: Username for logging
            skip_venv_install: Skip pip install (faster for tests that don't need packages)
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

            # Install requirements into the venv (skip if skip_venv_install=True for faster tests)
            if skip_venv_install:
                logger.info(f"Skipping pip install for {username} (skip_venv_install=True)")
            else:
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

    def _cleanup_stale_user_entries(self, username: str, home_dir: Path, uid: int) -> None:
        """Clean up stale /etc/shadow or /etc/group entries and retry useradd.

        Called when useradd returns code 9 but the user doesn't exist in /etc/passwd.
        This happens when userdel partially succeeded or when ag3ntum_api is a
        supplementary member of the user's group (userdel won't remove the group).
        """
        logger.warning(
            f"useradd returned 9 for {username} but user not in /etc/passwd. "
            "Cleaning up stale entries and retrying."
        )
        # Remove stale group and shadow entries (guard against protected groups)
        if username not in PROTECTED_GROUPS:
            subprocess.run(["sudo", "groupdel", username], capture_output=True)
        subprocess.run(
            ["sudo", "sed", "-i", f"/^{username}:/d", "/etc/shadow"],
            capture_output=True,
        )

        # Retry useradd
        base_cmd = ["sudo", "useradd", "-M", "-d", str(home_dir), "-s", "/bin/bash",
                     "-u", str(uid)]
        retry = subprocess.run(base_cmd + [username], capture_output=True)

        # If the group still exists (groupdel failed), adopt it with -g
        if retry.returncode != 0:
            retry_err = retry.stderr.decode()
            if "group" in retry_err.lower() and "exists" in retry_err.lower():
                logger.info(f"Group {username} still exists after cleanup, retrying useradd with -g")
                gid_check = subprocess.run(
                    ["getent", "group", username], capture_output=True, text=True,
                )
                if gid_check.returncode == 0:
                    existing_gid = gid_check.stdout.strip().split(":")[2]
                    retry = subprocess.run(
                        base_cmd + ["-g", existing_gid, username],
                        capture_output=True,
                    )

        if retry.returncode != 0:
            logger.error(
                f"Retry useradd failed for {username} (code {retry.returncode}): "
                f"{retry.stderr.decode()}"
            )
            raise ValueError(f"Failed to create Linux user after cleanup: {retry.stderr.decode()}")

        logger.info(f"Created Linux user {username} after cleaning up stale entries")

    def _setup_group_permissions(self, home_dir: Path, uid: int, username: str) -> None:
        """
        Set up group-based permissions for API access to user directories.

        Two group relationships are established:
        1. Sandbox user → ag3ntum group (home dir, skills, persistent access)
        2. ag3ntum_api → sandbox user's primary group (session file access with 660/770)

        Permission Model:
        - User directories get 750 (owner rwx, ag3ntum group rx)
        - ag3ntum/ dir gets 750 (ag3ntum group traverse to persistent/)
        - Persistent storage gets 770 (ag3ntum group rwx for API writes)
        - secrets.yaml stays 600 (owner only, protects API keys)
        - Session files: 770 dirs / 660 files (owner:uid, group:uid)
        - ag3ntum_api in user's group → can access session files without world perms

        Security: PathValidator provides application-level access control as the
        primary security gate. Group permissions are defense-in-depth.
        Cross-user isolation: each user has unique primary GID. ag3ntum_api is in
        all user groups but PathValidator prevents cross-user file access.

        Important: File permission operations (chgrp/chmod) are separated from user
        group operations (usermod) so that directory permissions are always set even
        if usermod fails (e.g., stale user entries after delete/recreate). Without
        this separation, a usermod failure would leave the home directory at 700
        (owner-only), preventing the API from accessing it and blocking login.
        """
        # Phase 1: User group membership (may fail if Linux user is in bad state)
        # These failures are non-fatal — file permissions below are more important
        # for basic API access (login validation, session directory creation).
        usermod_ok = True
        try:
            # Add user to ag3ntum group
            subprocess.run(
                ["sudo", "usermod", "-a", "-G", "ag3ntum", username],
                check=True,
                capture_output=True,
            )
            logger.info(f"Added {username} to ag3ntum group")
        except subprocess.CalledProcessError as e:
            usermod_ok = False
            logger.warning(f"Failed to add {username} to ag3ntum group: {e.stderr.decode()}")

        try:
            # Add ag3ntum_api to sandbox user's primary group (shared GID)
            # This allows the API process to access session files with 660/770 perms
            # instead of the more permissive 666/777. Cross-user isolation is enforced
            # by PathValidator at the application layer.
            subprocess.run(
                ["sudo", "usermod", "-a", "-G", username, "ag3ntum_api"],
                check=True,
                capture_output=True,
            )
            logger.info(f"Added ag3ntum_api to {username}'s group (GID {uid})")
        except subprocess.CalledProcessError as e:
            usermod_ok = False
            logger.warning(f"Failed to add ag3ntum_api to {username}'s group: {e.stderr.decode()}")

        # Phase 2: File permissions (must succeed for login/API access)
        # These run regardless of whether usermod succeeded above.
        try:
            # Set group ownership to ag3ntum for key directories
            subprocess.run(
                ["sudo", "chgrp", "ag3ntum", str(home_dir)],
                check=True,
                capture_output=True,
            )

            # Set 750 permissions (owner rwx, group rx, others none)
            subprocess.run(
                ["sudo", "chmod", "750", str(home_dir)],
                check=True,
                capture_output=True,
            )

            # Set group permissions on subdirectories that API needs to access
            # These directories need 770 (rwx for group) because the API user (in ag3ntum group)
            # must be able to CREATE files/directories here (e.g., session directories)
            for subdir in [".claude", "sessions"]:
                subdir_path = home_dir / subdir
                if subdir_path.exists():
                    # Set group ownership recursively
                    subprocess.run(
                        ["sudo", "chgrp", "-R", "ag3ntum", str(subdir_path)],
                        check=True,
                        capture_output=True,
                    )
                    # 770 = rwx for owner (user), rwx for group (ag3ntum), none for others
                    # The API user needs write access to create session directories
                    # Use -R to also set permissions on subdirectories (e.g., .claude/skills)
                    subprocess.run(
                        ["sudo", "chmod", "-R", "770", str(subdir_path)],
                        check=True,
                        capture_output=True,
                    )

            # Set SGID bit on sessions/ so new session dirs inherit ag3ntum group
            sessions_path = home_dir / "sessions"
            if sessions_path.exists():
                subprocess.run(
                    ["sudo", "chmod", "2770", str(sessions_path)],
                    check=True,
                    capture_output=True,
                )

            # ag3ntum directory: needs group traverse permission (750) so API can access persistent/
            # secrets.yaml inside is protected by its own 600 permissions
            ag3ntum_dir = home_dir / "ag3ntum"
            if ag3ntum_dir.exists():
                # Set group ownership to ag3ntum (but NOT recursive - don't change secrets.yaml)
                subprocess.run(
                    ["sudo", "chgrp", "ag3ntum", str(ag3ntum_dir)],
                    check=True,
                    capture_output=True,
                )
                # 750 = rwx for owner, r-x for group (traverse only), none for others
                subprocess.run(
                    ["sudo", "chmod", "750", str(ag3ntum_dir)],
                    check=True,
                    capture_output=True,
                )

            # persistent dir needs write access for group
            persistent_dir = home_dir / "ag3ntum" / "persistent"
            if persistent_dir.exists():
                # Set group ownership to ag3ntum
                subprocess.run(
                    ["sudo", "chgrp", "-R", "ag3ntum", str(persistent_dir)],
                    check=True,
                    capture_output=True,
                )
                # 770 = rwx for owner, rwx for group, none for others
                subprocess.run(
                    ["sudo", "chmod", "770", str(persistent_dir)],
                    check=True,
                    capture_output=True,
                )

            logger.info(f"Set up file permissions for {username}")

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to set file permissions for {username}: {e.stderr.decode()}")

        if usermod_ok:
            logger.info(f"Set up group-based permissions for {username}")
        else:
            logger.warning(
                f"Group membership setup incomplete for {username}. "
                "File permissions are set but user group operations failed. "
                "A container restart (./run.sh restart) may be needed."
            )

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
                # Refresh groups to remove the deleted user's GID
                refresh_process_supplementary_groups()
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
        """
        Delete Linux user account and clean up associated system entries.

        Runs userdel to remove the user from /etc/passwd and /etc/shadow,
        then attempts groupdel to remove the user's primary group from
        /etc/group. The group cleanup prevents stale entries that cause
        useradd to return code 9 ("username already in use") when the user
        is recreated — useradd checks /etc/shadow and /etc/group in addition
        to /etc/passwd.
        """
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

        # Clean up the user's primary group if it still exists.
        # userdel only removes the group if no other user has it as primary
        # and no other users are members. A leftover group entry can cause
        # useradd to return code 9 on recreate.
        # Guard: never delete protected system/infrastructure groups.
        if username not in PROTECTED_GROUPS:
            try:
                subprocess.run(
                    ["sudo", "groupdel", username],
                    check=True,
                    capture_output=True,
                )
                logger.debug(f"Deleted group {username}")
            except subprocess.CalledProcessError:
                # Group doesn't exist or has other members — either is fine
                pass

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
