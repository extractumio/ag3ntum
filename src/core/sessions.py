"""
Session directory management for Ag3ntum.

Handles session directory creation and workspace setup.
Each session has an isolated workspace with:
- agent.jsonl - Claude SDK event log
- workspace/ - Agent working directory with external mounts

NOTE: All session metadata is stored in SQLite database (Session model),
NOT in files. This module only manages directory structure.

SECURITY: Session directories use shared GID access model:
- Permissions: 770 dirs / 660 files (owner + group, no world access)
- Owner: sandbox user's UID (both UID and GID set to sandbox user)
- ag3ntum_api is in each sandbox user's group (shared GID)
- PathValidator provides application-level cross-session/cross-user isolation
"""
import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .exceptions import DynamicMountError, SessionError
from .path_validator import get_session_linux_uid

logger = logging.getLogger(__name__)


def _sudo_chown(path: Path, uid: int) -> None:
    """
    Set ownership of a path using sudo chown.

    The API process (ag3ntum_api) doesn't have CAP_CHOWN as an effective
    capability when running as non-root. We use sudo chown instead,
    which is allowed via sudoers rules in the Dockerfile:
      ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chown *:* /users/*

    Args:
        path: Path to change ownership of
        uid: UID to set as owner and group
    """
    try:
        result = subprocess.run(
            ["sudo", "/usr/bin/chown", f"{uid}:{uid}", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(f"sudo chown failed for {path}: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        logger.warning(f"sudo chown timed out for {path}")
    except Exception as e:
        logger.warning(f"Could not set ownership of {path} to {uid}: {e}")


def chown_to_session_user(path: Path, session_id: str) -> None:
    """
    Change ownership of a file/directory to the session's sandbox user.

    Called by Write/Edit/MultiEdit MCP tools after creating/modifying files.
    Sets both UID and GID to the sandbox user's ID (e.g., 50000:50000).
    Since ag3ntum_api is in the sandbox user's group (shared GID model),
    both the API process and Bash sandbox can access files with 660/770 perms.

    Uses sudo chown for paths under /users/*. Silently skips for
    other paths (e.g., /mounts/*) where ownership is managed by the host.

    Args:
        path: Path to change ownership of
        session_id: Session ID to look up the linux_uid
    """
    linux_uid = get_session_linux_uid(session_id)
    if linux_uid is None:
        return

    if str(path).startswith("/users/"):
        _sudo_chown(path, linux_uid)


def sudo_chown_recursive(path: Path, uid: int) -> None:
    """
    Recursively set ownership of a path using sudo chown -R.

    The API process (ag3ntum_api) doesn't have CAP_CHOWN as an effective
    capability when running as non-root. We use sudo chown instead,
    which is allowed via sudoers rules in the Dockerfile:
      ag3ntum_api ALL=(root) NOPASSWD: /usr/bin/chown -R *:* /users/*

    Args:
        path: Path to change ownership of (recursively)
        uid: UID to set as owner and group
    """
    try:
        result = subprocess.run(
            ["sudo", "/usr/bin/chown", "-R", f"{uid}:{uid}", str(path)],
            capture_output=True,
            text=True,
            timeout=30,  # Longer timeout for recursive operation
        )
        if result.returncode != 0:
            logger.warning(f"sudo chown -R failed for {path}: {result.stderr.strip()}")
        else:
            logger.info(f"Set ownership of {path} (recursive) to {uid}:{uid}")
    except subprocess.TimeoutExpired:
        logger.warning(f"sudo chown -R timed out for {path}")
    except Exception as e:
        logger.warning(f"Could not set ownership of {path} to {uid}: {e}")


class SessionManager:
    """
    Manages session directories and file paths.

    NOTE: Session metadata (status, cumulative stats, etc.) is stored in
    the SQLite database, NOT in files. This class only handles:
    - Directory structure creation
    - File path resolution
    - External mount symlinks
    - Workspace cleanup

    The session directory contains:
    - agent.jsonl - SDK event log (required by Claude SDK)
    - workspace/ - Agent working directory
    """

    def __init__(self, sessions_dir: Path) -> None:
        """
        Initialize the session manager.

        Args:
            sessions_dir: Directory to store sessions.
        """
        self._sessions_dir = sessions_dir
        # Note: Directory is created on-demand in create_session_directory(), not here.
        # This allows lazy initialization for per-user session directories.

    def create_session_directory(
        self,
        session_id: str | None = None,
        owner_uid: Optional[int] = None,
    ) -> str:
        """
        Create the directory structure for a new session with shared access.

        Session directories use 770 permissions to allow both API (via sudo bwrap)
        and sandbox processes to access files. Cross-session isolation is enforced
        at the application level by PathValidator.

        Security Properties:
        - Permissions: 770 (owner and group can read/write/execute)
        - Owner: sandbox user's UID (owner_uid) if provided
        - Group: same as owner UID (sandbox user's primary group)
        - API accesses via sudo bwrap (root), sandbox runs as owner_uid
        - Cross-user isolation: PathValidator blocks cross-user access
        - Cross-session isolation: separate directories + PathValidator

        Args:
            session_id: Optional session ID. If None, generates one.
            owner_uid: UID to set as owner (for sandbox access).
                       If None, directories owned by API process.

        Returns:
            The session ID (generated or provided).
        """
        if session_id is None:
            session_id = generate_session_id()

        session_dir = self.get_session_dir(session_id)

        # Create session directory with 770 permissions (owner + group access)
        # This allows both API (via sudo) and sandbox (as owner) to access
        self._create_session_directory_basic(session_dir, owner_uid)

        logger.info(f"Created session directory: {session_id} (owner_uid={owner_uid})")
        return session_id

    def _create_session_directory_basic(
        self,
        session_dir: Path,
        owner_uid: Optional[int] = None,
    ) -> None:
        """
        Create session directory with 770 permissions (owner + group access).

        The 770 permissions allow:
        - Owner (sandbox user): full read/write/execute access
        - Group (sandbox user's group): full read/write/execute access
        - API process: accesses via sudo bwrap (runs as root initially)

        This is necessary because:
        - API creates directories but can't chown without CAP_CHOWN
        - Sandbox (bubblewrap) uses sudo to gain root for file access
        - Files created by sandbox commands are owned by sandbox user
        - API needs to read these files for File Browser, etc.

        Args:
            session_dir: Path to the session directory
            owner_uid: Optional UID to set as owner (also used as GID)
        """
        session_dir.mkdir(parents=True, exist_ok=True)
        try:
            session_dir.chmod(0o770)
        except PermissionError:
            # Directory already exists and is owned by sandbox user - that's OK
            pass

        workspace = session_dir / "workspace"
        workspace.mkdir(exist_ok=True)
        try:
            # 777 permissions allow both API and sandbox to read/write
            # Security is enforced by PathValidator and bubblewrap sandbox, not by permissions
            workspace.chmod(0o777)
        except PermissionError:
            # Directory already exists and is owned by sandbox user - that's OK
            pass

        # Note: Don't chown here - ownership is set after all directories are
        # created (in setup_external_mounts) to avoid permission issues where
        # the API can't create subdirectories after losing ownership

    def get_session_dir(self, session_id: str) -> Path:
        """
        Get the directory for a session.

        Args:
            session_id: The session ID.

        Returns:
            Path to the session directory.
        """
        return self._sessions_dir / session_id

    def get_log_file(self, session_id: str) -> Path:
        """
        Get the log file path for a session.

        Args:
            session_id: The session ID.

        Returns:
            Path to the agent.jsonl file.
        """
        return self.get_session_dir(session_id) / "agent.jsonl"

    def get_workspace_dir(self, session_id: str) -> Path:
        """
        Get the workspace directory for a session.

        The workspace is a sandboxed subdirectory where the agent can
        write output. This is separate from the session directory to
        prevent the agent from reading logs and other sensitive files.

        Args:
            session_id: The session ID.

        Returns:
            Path to the workspace directory.
        """
        workspace = self.get_session_dir(session_id) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def setup_external_mounts(
        self, session_id: str, username: str, owner_uid: Optional[int] = None
    ) -> None:
        """
        Create symlinks for external mounts in the workspace.

        Creates the ./external/ directory structure with symlinks to:
        - ./external/ro/{name} -> /mounts/{name} (global read-only mounts)
        - ./external/rw/{name} -> /mounts/{name} (global read-write mounts)
        - ./external/user-ro/{name} -> /mounts/{name} (per-user read-only mounts)
        - ./external/user-rw/{name} -> /mounts/{name} (per-user read-write mounts)

        Also creates persistent storage symlink:
        - ./persistent -> /persistent (sandbox mount target)

        This allows both the File Browser UI and agent tools to see the same files.

        Args:
            session_id: The session ID.
            username: The username for persistent storage path and per-user mounts.
            owner_uid: UID to set as owner (for sandbox access). If None, uses default.
        """
        import yaml

        workspace = self.get_workspace_dir(session_id)
        external_dir = workspace / "external"

        # Create base directories with 770 permissions for sandbox access
        dirs_to_create = [
            external_dir,
            external_dir / "ro",
            external_dir / "rw",
            external_dir / "user-ro",
            external_dir / "user-rw",
        ]
        for dir_path in dirs_to_create:
            dir_path.mkdir(parents=True, exist_ok=True)
            # 777 permissions allow both API and sandbox to read/write
            # Security is enforced by PathValidator and bubblewrap sandbox
            dir_path.chmod(0o777)

        # Load global mounts configuration from auto-generated-mounts.yaml (generated by run.sh)
        mounts_file = Path("/data/auto-generated/auto-generated-mounts.yaml")

        if mounts_file.exists():
            try:
                with open(mounts_file, "r", encoding="utf-8") as f:
                    manifest = yaml.safe_load(f) or {}

                mounts_data = manifest.get("mounts", {})

                # Create RO mount symlinks (flattened: /mounts/{name})
                if isinstance(mounts_data.get("ro"), list):
                    for mount in mounts_data["ro"]:
                        if isinstance(mount, dict) and mount.get("name"):
                            name = mount["name"]
                            link = external_dir / "ro" / name
                            # Use container_path from manifest if available, else flat path
                            container_path = mount.get("container_path", f"/mounts/{name}")
                            target = Path(container_path)

                            # Skip if mount doesn't exist in Docker
                            if not target.exists():
                                logger.debug(f"Skipping RO mount '{name}': {target} does not exist")
                                continue

                            # Skip if symlink already exists and is valid
                            if link.is_symlink() and link.exists():
                                continue

                            # Remove broken symlink if present
                            if link.is_symlink():
                                try:
                                    link.unlink()
                                except OSError as e:
                                    logger.warning(f"Failed to remove broken RO symlink {name}: {e}")
                                    continue

                            # Skip if regular file/directory exists
                            if link.exists():
                                continue

                            try:
                                link.symlink_to(target)
                                logger.debug(f"Created RO mount symlink: {link} -> {target}")
                            except OSError as e:
                                logger.warning(f"Failed to create RO symlink for {name}: {e}")

                # Create RW mount symlinks (flattened: /mounts/{name})
                if isinstance(mounts_data.get("rw"), list):
                    for mount in mounts_data["rw"]:
                        if isinstance(mount, dict) and mount.get("name"):
                            name = mount["name"]
                            link = external_dir / "rw" / name
                            # Use container_path from manifest if available, else flat path
                            container_path = mount.get("container_path", f"/mounts/{name}")
                            target = Path(container_path)

                            # Skip if mount doesn't exist in Docker
                            if not target.exists():
                                logger.debug(f"Skipping RW mount '{name}': {target} does not exist")
                                continue

                            # Skip if symlink already exists and is valid
                            if link.is_symlink() and link.exists():
                                continue

                            # Remove broken symlink if present
                            if link.is_symlink():
                                try:
                                    link.unlink()
                                except OSError as e:
                                    logger.warning(f"Failed to remove broken RW symlink {name}: {e}")
                                    continue

                            # Skip if regular file/directory exists
                            if link.exists():
                                continue

                            try:
                                link.symlink_to(target)
                                logger.debug(f"Created RW mount symlink: {link} -> {target}")
                            except OSError as e:
                                logger.warning(f"Failed to create RW symlink for {name}: {e}")

            except Exception as e:
                logger.warning(f"Failed to load mounts config: {e}")

        # Load per-user mounts from external-mounts.yaml
        from ..services.mount_service import get_user_mounts

        try:
            user_mounts = get_user_mounts(username)

            # Create per-user RO mount symlinks (flattened: /mounts/{name})
            for mount_info in user_mounts.get("ro", []):
                name = mount_info["name"]
                link = external_dir / "user-ro" / name
                # Use container_path from mount_info if available, else flat path
                container_path = mount_info.get("container_path", f"/mounts/{name}")
                target = Path(container_path)

                # Skip if mount doesn't exist and is required
                if not target.exists():
                    is_optional = mount_info.get("optional", True)
                    if not is_optional:
                        logger.warning(f"Required per-user RO mount missing: {target}")
                    continue

                if not link.exists() and not link.is_symlink():
                    try:
                        link.symlink_to(target)
                        logger.debug(f"Created user RO mount symlink: {link} -> {target}")
                    except OSError as e:
                        logger.warning(f"Failed to create user RO symlink for {name}: {e}")

            # Create per-user RW mount symlinks (flattened: /mounts/{name})
            for mount_info in user_mounts.get("rw", []):
                name = mount_info["name"]
                link = external_dir / "user-rw" / name
                # Use container_path from mount_info if available, else flat path
                container_path = mount_info.get("container_path", f"/mounts/{name}")
                target = Path(container_path)

                # Skip if mount doesn't exist and is required
                if not target.exists():
                    is_optional = mount_info.get("optional", True)
                    if not is_optional:
                        logger.warning(f"Required per-user RW mount missing: {target}")
                    continue

                if not link.exists() and not link.is_symlink():
                    try:
                        link.symlink_to(target)
                        logger.debug(f"Created user RW mount symlink: {link} -> {target}")
                    except OSError as e:
                        logger.warning(f"Failed to create user RW symlink for {name}: {e}")

        except Exception as e:
            logger.warning(f"Failed to load per-user mounts: {e}")

        # Create persistent storage symlink at workspace root (not under external/)
        # The persistent directory is at {user_home}/ag3ntum/persistent in Docker.
        # The symlink points to the Docker path so that ALL tools work correctly:
        #   - LS/Glob/Grep: traverse directories without broken symlinks
        #   - Read/Write/Edit: PathValidator translates to Docker path (same result)
        #   - Bash (bwrap): agent_core.py adds a bwrap mount at the Docker path
        #     so the symlink resolves inside the sandbox too
        # This follows the same pattern as external mount symlinks which point
        # to Docker paths (/mounts/{name}), not sandbox paths.
        persistent_link = workspace / "persistent"
        user_home = self._sessions_dir.parent  # e.g., /users/{username}/sessions -> /users/{username}
        persistent_dir_host = user_home / "ag3ntum" / "persistent"  # Docker path (symlink target)

        # Ensure the persistent directory exists
        # FAIL FAST: If we can't create it, the session should fail - not silently skip
        if not persistent_dir_host.exists():
            try:
                persistent_dir_host.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created persistent storage directory: {persistent_dir_host}")
            except OSError as e:
                raise SessionError(
                    f"Failed to create persistent storage directory {persistent_dir_host}: {e}. "
                    "This directory is required for bwrap sandbox mounts."
                )

        # Create or fix the symlink (points to Docker path)
        try:
            if persistent_link.is_symlink():
                current_target = persistent_link.readlink()
                if current_target != persistent_dir_host:
                    persistent_link.unlink()
                    persistent_link.symlink_to(persistent_dir_host)
                    logger.debug(f"Fixed persistent symlink: {persistent_link} -> {persistent_dir_host}")
            elif not persistent_link.exists():
                persistent_link.symlink_to(persistent_dir_host)
                logger.debug(f"Created persistent symlink: {persistent_link} -> {persistent_dir_host}")
        except OSError as e:
            raise SessionError(
                f"Failed to create persistent storage symlink: {e}"
            )

        # Note: We use 777 permissions instead of chown to allow both API and sandbox access
        # Security is enforced by PathValidator (blocks cross-session access) and
        # bubblewrap sandbox (isolates subprocess execution)

        logger.info(f"Set up external mounts for session {session_id}")

    def setup_dynamic_mounts(
        self,
        session_id: str,
        username: str,
        mount_requests: list,
        owner_uid: Optional[int] = None,
    ) -> list:
        """
        Set up dynamic mounts for a session.

        Creates symlinks at workspace root (e.g., workspace/{alias}) pointing to
        container mount paths (/mounts/{base}/{subpath}).
        Returns list of mount info for PathValidator and Bubblewrap configuration.

        Args:
            session_id: The session ID.
            username: The username (from JWT token) for authorization.
            mount_requests: List of DynamicMountRequest objects.
            owner_uid: UID to set as symlink owner.

        Returns:
            List of DynamicMountInfo objects describing the mounted paths.

        Raises:
            DynamicMountError: If mount validation fails or setup encounters an error.
        """
        from src.api.models import DynamicMountInfo, DynamicMountRequest
        from src.services.mount_service import get_dynamic_mount_service

        workspace = self.get_workspace_dir(session_id)

        # Reserved workspace paths that cannot be used as mount aliases
        reserved_paths = {"external", "persistent", ".claude", "output.yaml"}

        mount_service = get_dynamic_mount_service()
        max_mounts = mount_service.security.get("max_mounts_per_session", 10)

        if len(mount_requests) > max_mounts:
            raise DynamicMountError(
                f"Too many mounts requested ({len(mount_requests)}). Maximum: {max_mounts}"
            )

        mounted: list[DynamicMountInfo] = []
        seen_aliases: set[str] = set()

        for request in mount_requests:
            # Check for duplicate aliases
            if request.alias in seen_aliases:
                raise DynamicMountError(f"Duplicate mount alias: {request.alias}")
            seen_aliases.add(request.alias)

            # Check for reserved paths
            if request.alias in reserved_paths:
                raise DynamicMountError(
                    f"Mount alias '{request.alias}' is reserved and cannot be used"
                )

            # Validate mount request
            validation = mount_service.validate_mount_request(request, username)

            if not validation.is_valid:
                logger.warning(
                    f"DYNAMIC_MOUNT_DENIED: session={session_id}, user={username}, "
                    f"base={request.base}, subpath={request.subpath}, "
                    f"reason={validation.denial_code}"
                )
                raise DynamicMountError(
                    f"Mount '{request.alias}' denied: {validation.error}"
                )

            # Create symlink at workspace root (same level as external/, persistent/)
            link_path = workspace / request.alias
            target_path = Path(validation.resolved_container_path)

            if link_path.exists() or link_path.is_symlink():
                raise DynamicMountError(f"Mount alias '{request.alias}' already exists")

            try:
                link_path.symlink_to(target_path)
            except OSError as e:
                raise DynamicMountError(
                    f"Failed to create symlink for '{request.alias}': {e}"
                )

            # Set ownership if provided (use lchown for symlinks)
            if owner_uid is not None:
                try:
                    os.lchown(link_path, owner_uid, owner_uid)
                except OSError as e:
                    logger.warning(f"Could not set ownership of {link_path}: {e}")

            logger.info(
                f"DYNAMIC_MOUNT_CREATED: session={session_id}, user={username}, "
                f"alias={request.alias}, target={target_path}, mode={validation.resolved_mode}"
            )

            # Resolve host_path for display (base host_path + subpath)
            base_obj = mount_service.bases.get(request.base)
            host_path = None
            if base_obj:
                hp = base_obj.host_path.replace("{username}", username)
                if request.subpath:
                    hp = f"{hp}/{request.subpath}"
                host_path = hp

            mounted.append(DynamicMountInfo(
                alias=request.alias,
                workspace_path=f"./{request.alias}",
                mode=validation.resolved_mode,
                source_base=request.base,
                source_subpath=request.subpath,
                host_path=host_path,
            ))

        # Persist dynamic mount metadata for File Explorer
        session_dir = self.get_session_dir(session_id)
        meta_file = session_dir / ".dynamic-mounts.json"
        meta = {
            m.alias: {
                "mode": m.mode,
                "source_base": m.source_base,
                "source_subpath": m.source_subpath,
                "host_path": m.host_path,
            }
            for m in mounted
        }
        try:
            meta_file.write_text(json.dumps(meta))
        except OSError as e:
            logger.warning(
                f"Failed to write dynamic mount metadata for session {session_id}: {e}"
            )

        logger.info(
            f"Set up {len(mounted)} dynamic mounts for session {session_id}"
        )
        return mounted

    def get_original_path_mounts(
        self,
        username: str,
    ) -> list:
        """
        Get original-path mounts available to a user.

        Original-path mounts allow accessing paths like /var/log at their
        original locations within the sandbox. These are configured in
        external-mounts.yaml under original_paths.

        Args:
            username: The username (from JWT token) for authorization.

        Returns:
            List of OriginalPathMount objects describing the available mounts.
        """
        from src.services.mount_service import (
            get_original_path_mount_service,
            OriginalPathMount,
        )

        mount_service = get_original_path_mount_service()
        return mount_service.get_mounts_for_user(username)

    def cleanup_workspace_skills(self, session_id: str) -> None:
        """
        Remove the skills folder from a session's workspace.

        Called after agent run completes to clean up merged skills symlinks.
        The .claude/skills/ directory contains symlinks to actual skill sources.
        Workspace files are preserved.

        Args:
            session_id: The session ID.
        """
        # New structure: .claude/skills/ contains symlinks
        claude_skills_dir = (
            self.get_session_dir(session_id) / "workspace" / ".claude" / "skills"
        )

        if claude_skills_dir.exists():
            try:
                # Remove the directory with all its symlinks
                shutil.rmtree(claude_skills_dir)
                logger.info(
                    f"Cleaned up workspace/.claude/skills/ for session {session_id}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to cleanup workspace skills for session {session_id}: {e}"
                )

        # Also clean old-style skills/ symlink for backward compatibility
        old_skills_link = self.get_session_dir(session_id) / "workspace" / "skills"
        if old_skills_link.is_symlink():
            try:
                old_skills_link.unlink()
                logger.debug(f"Removed legacy skills symlink for session {session_id}")
            except Exception as e:
                logger.warning(f"Failed to remove legacy skills symlink: {e}")

    def session_dir_exists(self, session_id: str) -> bool:
        """
        Check if a session directory exists.

        Args:
            session_id: The session ID.

        Returns:
            True if the session directory exists, False otherwise.
        """
        return self.get_session_dir(session_id).exists()


def generate_session_id() -> str:
    """Generate a unique session ID."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{ts}_{uid}"


def secure_file_write(
    file_path: Path,
    content: bytes | str,
    owner_uid: Optional[int] = None,
    mode: int = 0o660,
) -> None:
    """
    Write a file with shared access permissions.

    This function is used for writing files in session directories
    (like agent.jsonl) with permissions that allow both owner and group access.

    Security Properties:
    - File permissions: 660 by default (owner + group read/write)
    - No world access
    - Ownership: Set to owner_uid if provided (both UID and GID)

    Args:
        file_path: Path to write to
        content: Content to write (bytes or str)
        owner_uid: UID to set as owner (optional)
        mode: File permission mode (default: 660 = owner + group read/write)
    """
    # Write content
    if isinstance(content, str):
        file_path.write_text(content)
    else:
        file_path.write_bytes(content)

    # Set permissions BEFORE ownership (in case we lose access after chown)
    file_path.chmod(mode)

    # Set ownership if provided (both owner and group to sandbox user)
    if owner_uid is not None:
        try:
            os.chown(file_path, owner_uid, owner_uid)
        except OSError as e:
            logger.warning(f"Could not set ownership of {file_path} to {owner_uid}: {e}")


def _secure_path(path: Path, mode: int, owner_uid: Optional[int]) -> None:
    """Helper to set permissions and ownership on a path."""
    try:
        path.chmod(mode)
        if owner_uid:
            os.chown(path, owner_uid, owner_uid)
    except OSError:
        pass


def ensure_secure_session_files(
    session_dir: Path,
    owner_uid: Optional[int] = None,
) -> None:
    """
    Ensure all session files have appropriate permissions after agent run.

    Session files use shared access permissions (770/660) to allow both
    API and sandbox processes to access them. The ag3ntum_api process is
    in the sandbox user's primary group (shared GID model, set at user
    creation), so 660/770 is sufficient — no world-readable permissions needed.

    Cross-session isolation is enforced by PathValidator at the application level.

    Security Properties:
    - Directories: 770 (owner rwx + group rwx, no world)
    - Files: 660 (owner rw + group rw, no world)
    - Sensitive files (agent.jsonl, .claude.json): 660
    - Ownership: uid:uid (sandbox user owns both UID and GID)

    Args:
        session_dir: Path to the session directory
        owner_uid: UID to set as owner (optional)
    """
    if not session_dir.exists():
        return

    # Secure session directory with shared access
    _secure_path(session_dir, 0o770, owner_uid)

    # Secure sensitive root files (still need group read for API access)
    for sensitive_file in ["agent.jsonl", ".claude.json"]:
        file_path = session_dir / sensitive_file
        if file_path.exists():
            _secure_path(file_path, 0o660, owner_uid)

    # Recursively secure workspace directory with shared access
    workspace = session_dir / "workspace"
    if workspace.exists():
        for root, dirs, files in os.walk(workspace):
            root_path = Path(root)
            _secure_path(root_path, 0o770, owner_uid)
            for f in files:
                _secure_path(root_path / f, 0o660, owner_uid)

    logger.debug(f"Secured session files: {session_dir} (owner_uid={owner_uid})")
