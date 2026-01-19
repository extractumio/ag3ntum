"""
External mount configuration service.

Handles loading and resolving mount configurations from external-mounts.yaml.
Supports both global mounts (Docker-level) and per-user mounts (sandbox-level).

Note: This service runs inside Docker. External mounts are configured via:
- deploy.sh --mount-ro and --mount-rw for global mounts
- external-mounts.yaml per_user section for user-specific mounts
"""
import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MountConfig(BaseModel):
    """Configuration for a single mount."""
    name: str
    host_path: str
    description: str = ""
    optional: bool = True
    users: list[str] = Field(default_factory=list)  # Empty = global mount


class MountsConfiguration(BaseModel):
    """Full mounts configuration from YAML."""
    global_ro: list[MountConfig] = Field(default_factory=list)
    global_rw: list[MountConfig] = Field(default_factory=list)
    per_user_ro: list[MountConfig] = Field(default_factory=list)
    per_user_rw: list[MountConfig] = Field(default_factory=list)


# Cache the loaded configuration
_cached_config: Optional[MountsConfiguration] = None
_config_mtime: float = 0


def _load_mounts_config() -> MountsConfiguration:
    """
    Load mounts configuration from YAML file.

    Uses caching to avoid re-reading the file on every request.
    """
    global _cached_config, _config_mtime

    config_path = Path("/config/external-mounts.yaml")

    if not config_path.exists():
        # No config file - return empty configuration
        return MountsConfiguration()

    # Check if file has been modified
    current_mtime = config_path.stat().st_mtime
    if _cached_config is not None and current_mtime == _config_mtime:
        return _cached_config

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f) or {}

        config = MountsConfiguration()

        # Parse global mounts
        global_section = raw_config.get("global", {})
        for mount_data in global_section.get("ro", []):
            if isinstance(mount_data, dict) and mount_data.get("name"):
                config.global_ro.append(MountConfig(**mount_data))
        for mount_data in global_section.get("rw", []):
            if isinstance(mount_data, dict) and mount_data.get("name"):
                config.global_rw.append(MountConfig(**mount_data))

        # Parse per-user mounts
        per_user_section = raw_config.get("per_user", {})
        for mount_data in per_user_section.get("ro", []):
            if isinstance(mount_data, dict) and mount_data.get("name"):
                config.per_user_ro.append(MountConfig(**mount_data))
        for mount_data in per_user_section.get("rw", []):
            if isinstance(mount_data, dict) and mount_data.get("name"):
                config.per_user_rw.append(MountConfig(**mount_data))

        _cached_config = config
        _config_mtime = current_mtime

        logger.info(
            f"Loaded mounts config: {len(config.global_ro)} global RO, "
            f"{len(config.global_rw)} global RW, "
            f"{len(config.per_user_ro)} per-user RO, "
            f"{len(config.per_user_rw)} per-user RW"
        )

        return config

    except Exception as e:
        logger.error(f"Failed to load mounts config: {e}")
        return MountsConfiguration()


def get_user_mounts(username: str) -> dict[str, list[dict]]:
    """
    Get mount configurations for a specific user.

    Returns mounts that:
    - Are per-user mounts with users=["*"] (available to all users)
    - Are per-user mounts where username is in the users list

    Args:
        username: The username to get mounts for

    Returns:
        Dict with keys 'ro' and 'rw', each containing a list of mount configs:
        {
            'ro': [{'name': 'xxx', 'host_path': '/resolved/path', 'optional': True}],
            'rw': [{'name': 'xxx', 'host_path': '/resolved/path', 'optional': False}]
        }
    """
    config = _load_mounts_config()
    result = {"ro": [], "rw": []}

    def resolve_path(host_path: str, username: str) -> str:
        """Resolve {username} placeholder in path."""
        return host_path.replace("{username}", username)

    def user_allowed(mount: MountConfig, username: str) -> bool:
        """Check if user is allowed to access this mount."""
        if not mount.users:
            return False  # No users specified = not a per-user mount
        if "*" in mount.users:
            return True  # Available to all users
        return username in mount.users

    # Process per-user RO mounts
    for mount in config.per_user_ro:
        if user_allowed(mount, username):
            resolved_path = resolve_path(mount.host_path, username)
            path = Path(resolved_path)

            # Check if path exists (for non-optional mounts)
            if not path.exists() and not mount.optional:
                logger.warning(
                    f"Required per-user RO mount missing for {username}: {resolved_path}"
                )
                continue

            if path.exists() or mount.optional:
                result["ro"].append({
                    "name": mount.name,
                    "host_path": resolved_path,
                    "description": mount.description,
                    "optional": mount.optional,
                })
                logger.debug(
                    f"Added per-user RO mount for {username}: {mount.name} -> {resolved_path}"
                )

    # Process per-user RW mounts
    for mount in config.per_user_rw:
        if user_allowed(mount, username):
            resolved_path = resolve_path(mount.host_path, username)
            path = Path(resolved_path)

            # Check if path exists (for non-optional mounts)
            if not path.exists() and not mount.optional:
                logger.warning(
                    f"Required per-user RW mount missing for {username}: {resolved_path}"
                )
                continue

            if path.exists() or mount.optional:
                result["rw"].append({
                    "name": mount.name,
                    "host_path": resolved_path,
                    "description": mount.description,
                    "optional": mount.optional,
                })
                logger.debug(
                    f"Added per-user RW mount for {username}: {mount.name} -> {resolved_path}"
                )

    return result


def invalidate_cache() -> None:
    """Force reload of configuration on next access."""
    global _cached_config, _config_mtime
    _cached_config = None
    _config_mtime = 0


def resolve_external_symlink(symlink_path: Path) -> Optional[Path]:
    """
    Resolve an external mount symlink to its actual filesystem path.

    External mounts in workspace are symlinks that point to Docker container paths like
    /mounts/ro/name. Inside Docker, these paths exist and can be resolved directly.

    Args:
        symlink_path: Path to the external mount symlink

    Returns:
        The resolved real filesystem path, or None if not resolvable
    """
    if not symlink_path.is_symlink():
        # Not a symlink, return as-is if it exists
        return symlink_path if symlink_path.exists() else None

    # Get the symlink target
    try:
        target = os.readlink(symlink_path)
    except OSError:
        return None

    # Inside Docker, the symlink target should exist
    target_path = Path(target)
    if target_path.exists():
        return target_path

    # Symlink target doesn't exist - mount may not be configured
    logger.warning(f"External mount symlink target does not exist: {target}")
    return None


def resolve_file_path_for_external_mount(
    workspace_path: Path,
    relative_path: str,
) -> tuple[Path, bool]:
    """
    Resolve a relative path that might be in an external mount to its actual filesystem path.

    This is the main helper for File Explorer API endpoints that need to access files
    in external mounts.

    Args:
        workspace_path: Absolute path to the workspace root
        relative_path: Relative path from workspace (e.g., 'external/ro/downloads/file.txt')

    Returns:
        Tuple of (resolved_path, is_external):
        - resolved_path: The actual filesystem path to use
        - is_external: True if this is an external mount path
    """
    is_external = relative_path.startswith("external/") or relative_path == "external"
    target_path = workspace_path / relative_path

    if not is_external:
        return target_path, False

    # Walk through path components to find and resolve symlinks
    parts = relative_path.split('/')
    current_path = workspace_path

    for i, part in enumerate(parts):
        current_path = current_path / part
        if current_path.is_symlink():
            # Resolve this symlink
            resolved = resolve_external_symlink(current_path)
            if resolved:
                # Reconstruct path with remaining parts
                remaining_parts = parts[i+1:]
                if remaining_parts:
                    resolved = resolved / '/'.join(remaining_parts)
                return resolved, True
            else:
                # Symlink couldn't be resolved - return original path
                return target_path, True

    # No symlink found, return original path
    return target_path, True
