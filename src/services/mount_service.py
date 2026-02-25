"""
External mount configuration service.

Handles loading and resolving mount configurations from external-mounts.yaml.
Supports both global mounts (Docker-level) and per-user mounts (sandbox-level).

This service integrates with the SandboxPathResolver to provide consistent
path resolution across all components:

- File Explorer API uses resolve_file_path_for_session() for browsing
- MCP tools use PathValidator which internally uses SandboxPathResolver
- All paths are expressed in sandbox format (canonical)

Note: This service runs inside Docker. External mounts are configured via:
- run.sh --mount-ro and --mount-rw for global mounts
- external-mounts.yaml per_user section for user-specific mounts
- external-mounts.yaml dynamic section for session-time dynamic mounts
"""
from dataclasses import dataclass
import fnmatch
import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple

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


def get_global_mounts_for_path_validator() -> dict[str, dict[str, Path]]:
    """
    Get global mount paths for PathValidator configuration.

    Reads the auto-generated-mounts.yaml manifest to get Docker container paths
    for global mounts. With the flattened mount structure, all mounts are at
    /mounts/{name} and mode is tracked separately.

    Returns:
        Dict with keys 'ro' and 'rw', each containing a dict of {name: container_path}:
        {
            'ro': {'global_var_log': Path('/mounts/global_var_log')},
            'rw': {'product_docs': Path('/mounts/product_docs')}
        }
    """
    result: dict[str, dict[str, Path]] = {"ro": {}, "rw": {}}

    manifest_path = Path("/auto-generated/auto-generated-mounts.yaml")
    if not manifest_path.exists():
        logger.debug(f"No mounts manifest at {manifest_path}")
        return result

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = yaml.safe_load(f) or {}

        mounts_data = manifest.get("mounts", {})

        # Process both RO and RW mounts
        for mode in ("ro", "rw"):
            for mount in mounts_data.get(mode, []):
                if not isinstance(mount, dict) or not mount.get("name"):
                    continue
                name = mount["name"]
                container_path = mount.get("container_path", f"/mounts/{name}")
                path = Path(container_path)
                if path.exists():
                    result[mode][name] = path
                    logger.debug(f"Loaded global {mode.upper()} mount: {name} -> {container_path}")

        ro_count = len(result["ro"])
        rw_count = len(result["rw"])
        if ro_count > 0 or rw_count > 0:
            logger.info(
                f"Loaded global mounts for PathValidator: {ro_count} RO, {rw_count} RW"
            )

    except Exception as e:
        logger.warning(f"Failed to load global mounts from manifest: {e}")

    return result


def get_all_mounts_with_host_paths(username: str | None = None) -> dict[str, list[dict]]:
    """
    Get all external mounts with their host paths for original-path mount support.

    This enables agents to use host paths (e.g., /var/log) directly, not just
    internal workspace paths (e.g., ./external/ro/global_var_log).

    Args:
        username: Username for resolving user-specific mounts ({username} placeholder)

    Returns:
        Dict with keys 'ro' and 'rw', each containing a list of mount info dicts:
        {
            'ro': [
                {'name': 'global_var_log', 'host_path': '/var/log', 'container_path': '/mounts/global_var_log'}
            ],
            'rw': [...]
        }
    """
    result: dict[str, list[dict]] = {"ro": [], "rw": []}

    manifest_path = Path("/auto-generated/auto-generated-mounts.yaml")
    if not manifest_path.exists():
        logger.debug(f"No mounts manifest at {manifest_path}")
        return result

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = yaml.safe_load(f) or {}

        mounts_data = manifest.get("mounts", {})

        # Process global mounts (ro, rw)
        for mode in ("ro", "rw"):
            for mount in mounts_data.get(mode, []):
                if not isinstance(mount, dict) or not mount.get("name") or not mount.get("host_path"):
                    continue
                name = mount["name"]
                host_path = mount["host_path"]
                container_path = mount.get("container_path", f"/mounts/{name}")
                # Verify container path exists
                if Path(container_path).exists():
                    result[mode].append({
                        "name": name,
                        "host_path": host_path,
                        "container_path": container_path,
                    })

        # Process user-specific mounts (user-ro, user-rw) if username provided
        if username:
            for mode, target_mode in (("user-ro", "ro"), ("user-rw", "rw")):
                for mount in mounts_data.get(mode, []):
                    if not isinstance(mount, dict) or not mount.get("name") or not mount.get("host_path"):
                        continue
                    name = mount["name"]
                    # Resolve {username} placeholder in host_path
                    host_path = mount["host_path"].replace("{username}", username)
                    container_path = mount.get("container_path", f"/mounts/{name}")
                    # Verify container path exists
                    if Path(container_path).exists():
                        result[target_mode].append({
                            "name": name,
                            "host_path": host_path,
                            "container_path": container_path,
                        })

        ro_count = len(result["ro"])
        rw_count = len(result["rw"])
        if ro_count > 0 or rw_count > 0:
            logger.debug(
                f"Loaded mounts with host_paths for original-path support: {ro_count} RO, {rw_count} RW"
            )

    except Exception as e:
        logger.warning(f"Failed to load mounts with host_paths from manifest: {e}")

    return result


def get_path_display_mapping(username: str | None = None) -> dict[str, str]:
    """
    Build a mapping of internal mount paths to host paths for display transformation.

    This enables transforming paths in agent output from internal format
    (e.g., ./external/ro/global_var_log/syslog) to user-friendly host paths
    (e.g., /var/log/syslog).

    Args:
        username: Username for resolving user-specific mounts ({username} placeholder)

    Returns:
        Dict mapping internal path prefix to host path:
        {
            "external/ro/global_var_log": "/var/log",
            "external/user-ro/all_documents": "/Users/greg/Documents",
            ...
        }
    """
    result: dict[str, str] = {}

    manifest_path = Path("/auto-generated/auto-generated-mounts.yaml")
    if not manifest_path.exists():
        return result

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = yaml.safe_load(f) or {}

        mounts_data = manifest.get("mounts", {})

        # Map section names to internal path prefixes
        # ro -> external/ro, rw -> external/rw, user-ro -> external/user-ro, etc.
        section_to_prefix = {
            "ro": "external/ro",
            "rw": "external/rw",
            "user-ro": "external/user-ro",
            "user-rw": "external/user-rw",
        }

        for section, prefix in section_to_prefix.items():
            for mount in mounts_data.get(section, []):
                if not isinstance(mount, dict) or not mount.get("name") or not mount.get("host_path"):
                    continue
                name = mount["name"]
                host_path = mount["host_path"]

                # Resolve {username} placeholder if username provided
                if "{username}" in host_path:
                    if username:
                        host_path = host_path.replace("{username}", username)
                    else:
                        # Skip user-specific mounts if no username
                        continue

                # Verify the mount exists (container path)
                container_path = mount.get("container_path", f"/mounts/{name}")
                if Path(container_path).exists():
                    internal_path = f"{prefix}/{name}"
                    result[internal_path] = host_path

        if result:
            logger.debug(f"Built path display mapping with {len(result)} entries")

    except Exception as e:
        logger.warning(f"Failed to build path display mapping: {e}")

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

    NOTE: This is a low-level function. Prefer using resolve_file_path_for_session()
    which uses the SandboxPathResolver for consistent path handling.

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


# =============================================================================
# Session-Aware Path Resolution (uses SandboxPathResolver)
# =============================================================================

def resolve_file_path_for_session(
    session_id: str,
    sandbox_path: str,
) -> Tuple[Path, bool, str]:
    """
    Resolve a sandbox path to Docker path using the session's SandboxPathResolver.

    This is the recommended function for resolving paths in the File Explorer API
    and other Docker-side code that needs to access files based on sandbox paths.

    The function:
    1. Normalizes the input path to canonical sandbox format
    2. Translates to Docker filesystem path
    3. Determines if path is in an external mount
    4. Returns mount type for access control

    Args:
        session_id: The session ID (must have SandboxPathResolver configured)
        sandbox_path: Path in sandbox format (e.g., 'persistent/file.png',
                     '/workspace/file.txt', or './file.txt')

    Returns:
        Tuple of (docker_path, is_external, mount_type):
        - docker_path: Path object for the resolved Docker filesystem path
        - is_external: True if path is in an external mount
        - mount_type: Type of mount ('workspace', 'persistent', 'external_ro', etc.)

    Raises:
        RuntimeError: If SandboxPathResolver not configured for session
        PathResolutionError: If path cannot be resolved (outside allowed mounts)

    Example:
        >>> docker_path, is_external, mount_type = resolve_file_path_for_session(
        ...     "session123",
        ...     "persistent/image.png"
        ... )
        >>> print(docker_path)
        /users/greg/ag3ntum/persistent/image.png
        >>> print(is_external, mount_type)
        True persistent
    """
    # Import here to avoid circular dependency
    from src.core.sandbox_path_resolver import (
        get_sandbox_path_resolver,
        PathResolutionError,
    )

    resolver = get_sandbox_path_resolver(session_id)

    # Normalize to canonical sandbox format
    normalized = resolver.normalize(sandbox_path)

    # Get mount type
    mount_type = resolver.get_mount_type(normalized) or "unknown"

    # Determine if external
    is_external = mount_type in (
        "persistent", "external_ro", "external_rw",
        "user_mount_ro", "user_mount_rw"
    )

    # Translate to Docker path
    docker_path = Path(resolver.sandbox_to_docker(normalized))

    logger.debug(
        f"resolve_file_path_for_session: {sandbox_path} -> {docker_path} "
        f"(external={is_external}, type={mount_type})"
    )

    return docker_path, is_external, mount_type


def normalize_path_for_session(session_id: str, path: str) -> str:
    """
    Normalize any path to canonical sandbox format for a session.

    This is useful for normalizing paths before storage or comparison.

    Args:
        session_id: The session ID
        path: Input path (can be relative or absolute)

    Returns:
        Canonical sandbox path (e.g., /workspace/file.txt)

    Raises:
        RuntimeError: If SandboxPathResolver not configured for session
        PathResolutionError: If path is invalid
    """
    from src.core.sandbox_path_resolver import get_sandbox_path_resolver

    resolver = get_sandbox_path_resolver(session_id)
    return resolver.normalize(path)


def is_path_writable_for_session(session_id: str, sandbox_path: str) -> bool:
    """
    Check if a sandbox path is writable for a session.

    Args:
        session_id: The session ID
        sandbox_path: Path in sandbox format

    Returns:
        True if path is within a writable mount

    Raises:
        RuntimeError: If SandboxPathResolver not configured for session
    """
    from src.core.sandbox_path_resolver import get_sandbox_path_resolver

    resolver = get_sandbox_path_resolver(session_id)
    return resolver.is_path_writable(sandbox_path)


def translate_docker_path_to_sandbox(session_id: str, docker_path: str) -> str:
    """
    Translate a Docker path to sandbox format for display.

    This is useful for making error messages user-friendly by showing
    paths in the format the agent understands.

    Args:
        session_id: The session ID
        docker_path: Path in Docker format

    Returns:
        Sandbox path, or original path if translation fails

    Note:
        Does not raise exceptions - returns original path on failure
    """
    from src.core.sandbox_path_resolver import (
        get_sandbox_path_resolver,
        has_sandbox_path_resolver,
        PathResolutionError,
    )

    if not has_sandbox_path_resolver(session_id):
        return docker_path

    try:
        resolver = get_sandbox_path_resolver(session_id)
        return resolver.docker_to_sandbox(docker_path)
    except (PathResolutionError, Exception) as e:
        logger.debug(f"Could not translate Docker path to sandbox: {e}")
        return docker_path


# =============================================================================
# Dynamic Mount Service
# =============================================================================

@dataclass
class DynamicMountBase:
    """Configuration for a dynamic mount base."""
    name: str
    host_path: str
    container_path: str
    description: str
    max_mode: str  # "ro" or "rw"
    authorization_mode: str  # "allowlist", "role", "self_only"
    allowed_users: list[str]
    subpath_mode: str  # "allowlist" or "blocklist"
    allowed_subpaths: list[str]
    blocked_subpaths: list[str]
    subpath_exceptions: list[str]
    optional: bool


@dataclass
class DynamicMountValidation:
    """Result of validating a dynamic mount request."""
    is_valid: bool
    error: Optional[str] = None
    denial_code: Optional[str] = None
    resolved_container_path: Optional[str] = None
    resolved_mode: Optional[str] = None


class DynamicMountService:
    """Service for validating and resolving dynamic mount requests."""

    # Characters allowed in subpath (strict whitelist)
    ALLOWED_SUBPATH_CHARS = re.compile(r'^[a-zA-Z0-9/_.-]+$')

    # Dangerous patterns to reject
    DANGEROUS_PATTERNS = [
        r'\.\.',           # Path traversal
        r'\x00',           # Null byte
        r'\\',             # Backslash
    ]

    def __init__(self, config: dict):
        self.config = config
        self.dynamic_config = config.get("dynamic", {})
        self.enabled = self.dynamic_config.get("enabled", False)
        self.security = self.dynamic_config.get("security", {})
        self.bases = self._load_bases()
        self.global_blocked = self.security.get("global_blocked_subpaths", [])

    def _load_bases(self) -> dict[str, DynamicMountBase]:
        """Load dynamic mount bases from config."""
        bases = {}
        for base_config in self.dynamic_config.get("bases", []):
            auth = base_config.get("authorization", {})
            subpath_res = base_config.get("subpath_restrictions", {})

            base = DynamicMountBase(
                name=base_config["name"],
                host_path=base_config["host_path"],
                container_path=f"/mounts/{base_config['name']}",
                description=base_config.get("description", ""),
                max_mode=base_config.get("max_mode", "ro"),
                authorization_mode=auth.get("mode", "allowlist"),
                allowed_users=auth.get("allowed_users", []),
                subpath_mode=subpath_res.get("mode", "blocklist"),
                allowed_subpaths=subpath_res.get("allowed", []),
                blocked_subpaths=subpath_res.get("blocked", []),
                subpath_exceptions=subpath_res.get("exceptions", []),
                optional=base_config.get("optional", True),
            )
            bases[base.name] = base
        return bases

    def get_available_bases(self, username: str) -> list[DynamicMountBase]:
        """Get list of bases available to a user."""
        available = []
        for base in self.bases.values():
            if self._is_user_authorized(base, username):
                available.append(base)
        return available

    def validate_mount_request(
        self,
        request: "DynamicMountRequest",
        username: str,
    ) -> DynamicMountValidation:
        """
        Validate a dynamic mount request.

        SECURITY: Username comes from JWT token, never from request body.
        """
        # Import here to avoid circular dependency
        from src.api.models import DynamicMountRequest

        # 1. Feature enabled check
        if not self.enabled:
            return DynamicMountValidation(
                is_valid=False,
                error="Dynamic mounts feature is disabled. Clear your browser's mount selections or enable dynamic mounts in external-mounts.yaml",
                denial_code="FEATURE_DISABLED"
            )

        # 2. Base exists check
        base = self.bases.get(request.base)
        if not base:
            return DynamicMountValidation(
                is_valid=False,
                error=f"Unknown dynamic base: {request.base}",
                denial_code="BASE_NOT_FOUND"
            )

        # 3. User authorization check
        if not self._is_user_authorized(base, username):
            logger.warning(
                f"SECURITY: User '{username}' denied access to base '{request.base}'"
            )
            return DynamicMountValidation(
                is_valid=False,
                error=f"Not authorized for base: {request.base}",
                denial_code="NOT_AUTHORIZED"
            )

        # 4. Subpath validation
        subpath = request.subpath or ""
        subpath_result = self._validate_subpath(base, subpath, username)
        if not subpath_result.is_valid:
            return subpath_result

        # 5. Mode validation
        effective_mode = request.mode or "ro"
        if effective_mode == "rw" and base.max_mode == "ro":
            return DynamicMountValidation(
                is_valid=False,
                error=f"Base '{request.base}' only allows read-only access",
                denial_code="MODE_EXCEEDS_MAX"
            )

        # 6. Resolve container path with username substitution
        container_path = base.container_path.replace("{username}", username)
        if subpath:
            container_path = f"{container_path}/{subpath}"

        # 7. Validate resolved path exists (unless optional)
        resolved_path = Path(container_path)
        if not base.optional and not resolved_path.exists():
            return DynamicMountValidation(
                is_valid=False,
                error=f"Path does not exist: {container_path}",
                denial_code="PATH_NOT_FOUND"
            )

        # 8. Symlink resolution and containment check
        containment_result = self._validate_path_containment(
            resolved_path, base.container_path.replace("{username}", username)
        )
        if not containment_result.is_valid:
            return containment_result

        return DynamicMountValidation(
            is_valid=True,
            resolved_container_path=container_path,
            resolved_mode=effective_mode
        )

    def _is_user_authorized(self, base: DynamicMountBase, username: str) -> bool:
        """Check if user is authorized for this base."""
        if base.authorization_mode == "self_only":
            return True  # Path contains {username}, enforced at path level

        if base.authorization_mode == "allowlist":
            for allowed in base.allowed_users:
                if allowed == "*":
                    return True
                if allowed == "{self}":
                    return True  # self_only check happens at path level
                if allowed == username:
                    return True
            return False

        # role-based not implemented yet
        return False

    def _validate_subpath(
        self,
        base: DynamicMountBase,
        subpath: str,
        username: str
    ) -> DynamicMountValidation:
        """Validate subpath against restrictions."""
        if not subpath:
            return DynamicMountValidation(is_valid=True)

        # Check dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, subpath):
                logger.warning(
                    f"SECURITY: Dangerous pattern in subpath: {subpath[:100]}"
                )
                return DynamicMountValidation(
                    is_valid=False,
                    error="Invalid subpath: contains forbidden characters",
                    denial_code="DANGEROUS_PATTERN"
                )

        # Check character whitelist
        if not self.ALLOWED_SUBPATH_CHARS.match(subpath):
            return DynamicMountValidation(
                is_valid=False,
                error="Invalid subpath: contains forbidden characters",
                denial_code="INVALID_CHARACTERS"
            )

        # Check max depth
        max_depth = self.security.get("max_subpath_depth", 10)
        if subpath.count("/") >= max_depth:
            return DynamicMountValidation(
                is_valid=False,
                error=f"Subpath exceeds max depth of {max_depth}",
                denial_code="MAX_DEPTH_EXCEEDED"
            )

        # Check global blocked patterns
        for blocked in self.global_blocked:
            if self._path_matches_pattern(subpath, blocked):
                logger.warning(
                    f"SECURITY: Subpath matches global block: {subpath} ~ {blocked}"
                )
                return DynamicMountValidation(
                    is_valid=False,
                    error="Subpath is blocked by security policy",
                    denial_code="GLOBAL_BLOCKED"
                )

        # Check base-specific restrictions
        if base.subpath_mode == "allowlist":
            if not any(self._path_matches_pattern(subpath, p) for p in base.allowed_subpaths):
                return DynamicMountValidation(
                    is_valid=False,
                    error="Subpath not in allowed list",
                    denial_code="NOT_IN_ALLOWLIST"
                )
        elif base.subpath_mode == "blocklist":
            for blocked in base.blocked_subpaths:
                if self._path_matches_pattern(subpath, blocked):
                    # Check exceptions
                    if not any(self._path_matches_pattern(subpath, e) for e in base.subpath_exceptions):
                        return DynamicMountValidation(
                            is_valid=False,
                            error="Subpath is blocked",
                            denial_code="BLOCKED_BY_BASE"
                        )

        return DynamicMountValidation(is_valid=True)

    def _validate_path_containment(
        self,
        path: Path,
        base_path: str
    ) -> DynamicMountValidation:
        """Validate that resolved path stays within base after symlink resolution."""
        try:
            if path.exists():
                real_path = path.resolve()
                base_real = Path(base_path).resolve()

                try:
                    real_path.relative_to(base_real)
                except ValueError:
                    logger.error(
                        f"SECURITY: Path escaped base! path={path}, real={real_path}, base={base_real}"
                    )
                    return DynamicMountValidation(
                        is_valid=False,
                        error="Path escapes base directory",
                        denial_code="PATH_ESCAPE"
                    )
        except Exception as e:
            logger.error(f"SECURITY: Path resolution failed: {e}")
            return DynamicMountValidation(
                is_valid=False,
                error="Path validation failed",
                denial_code="RESOLUTION_FAILED"
            )

        return DynamicMountValidation(is_valid=True)

    def _path_matches_pattern(self, path: str, pattern: str) -> bool:
        """Match path against pattern (supports wildcards)."""
        path = path.strip("/")
        pattern = pattern.strip("/")

        path_parts = path.split("/")
        pattern_parts = pattern.split("/")

        for i, part in enumerate(pattern_parts):
            if i >= len(path_parts):
                return False
            if not fnmatch.fnmatch(path_parts[i], part):
                return False

        return True


# Singleton instance of DynamicMountService
_dynamic_mount_service: Optional[DynamicMountService] = None
_dynamic_config_mtime: float = 0


def get_dynamic_mount_service() -> DynamicMountService:
    """
    Get the singleton DynamicMountService instance.

    Reloads configuration if the file has been modified.
    """
    global _dynamic_mount_service, _dynamic_config_mtime

    config_path = Path("/config/external-mounts.yaml")

    # Check if we need to reload
    if config_path.exists():
        current_mtime = config_path.stat().st_mtime
        if _dynamic_mount_service is None or current_mtime != _dynamic_config_mtime:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                _dynamic_mount_service = DynamicMountService(config)
                _dynamic_config_mtime = current_mtime
                logger.info(
                    f"Loaded dynamic mount service: enabled={_dynamic_mount_service.enabled}, "
                    f"bases={list(_dynamic_mount_service.bases.keys())}"
                )
            except Exception as e:
                logger.error(f"Failed to load dynamic mount config: {e}")
                _dynamic_mount_service = DynamicMountService({})
    elif _dynamic_mount_service is None:
        _dynamic_mount_service = DynamicMountService({})

    return _dynamic_mount_service


def invalidate_dynamic_mount_cache() -> None:
    """Force reload of dynamic mount configuration on next access."""
    global _dynamic_mount_service, _dynamic_config_mtime
    _dynamic_mount_service = None
    _dynamic_config_mtime = 0


# =============================================================================
# Original-Path Mount Service
# =============================================================================

@dataclass
class OriginalPathMount:
    """Configuration for an original-path mount."""
    path: str  # Original path (e.g., "/var/log")
    encoded: str  # Encoded name (e.g., "_var_log")
    container_path: str  # Docker path (e.g., "/mounts/paths/_var_log")
    description: str
    mode: str  # "ro" or "rw"
    allowed_users: list[str]
    optional: bool


class OriginalPathMountService:
    """
    Service for original-path mounts.

    Original-path mounts allow accessing host paths at their original locations
    within the sandbox. For example, /var/log on the host can be accessed as
    /var/log inside the sandbox (not just via workspace symlinks).

    This is achieved by:
    1. Docker mounts host path to /mounts/paths/{encoded}
    2. Bubblewrap bind-mounts /mounts/paths/{encoded} to original path
    3. Agent can access /var/log directly
    """

    def __init__(self, config: dict):
        self.config = config
        self.original_paths_config = config.get("original_paths", {})
        self.mounts = self._load_mounts()

    def _load_mounts(self) -> dict[str, OriginalPathMount]:
        """Load original-path mounts from config."""
        mounts = {}

        for mode in ("ro", "rw"):
            for mount_config in self.original_paths_config.get(mode, []):
                if not isinstance(mount_config, dict) or not mount_config.get("path"):
                    continue

                path = mount_config["path"]
                encoded = path.replace("/", "_")

                mount = OriginalPathMount(
                    path=path,
                    encoded=encoded,
                    container_path=f"/mounts/paths/{encoded}",
                    description=mount_config.get("description", ""),
                    mode=mode,
                    allowed_users=mount_config.get("allowed_users", ["*"]),
                    optional=mount_config.get("optional", True),
                )
                mounts[path] = mount

        return mounts

    def get_mounts_for_user(self, username: str) -> list[OriginalPathMount]:
        """
        Get original-path mounts available to a user.

        Args:
            username: The username to check access for

        Returns:
            List of OriginalPathMount objects the user can access
        """
        available = []

        for mount in self.mounts.values():
            if self._is_user_authorized(mount, username):
                # Check if Docker mount exists
                container_path = Path(mount.container_path)
                if container_path.exists() or mount.optional:
                    available.append(mount)

        return available

    def _is_user_authorized(self, mount: OriginalPathMount, username: str) -> bool:
        """Check if user is authorized for this mount."""
        for allowed in mount.allowed_users:
            if allowed == "*":
                return True
            if allowed == username:
                return True
        return False

    def get_mount_for_path(self, path: str) -> OriginalPathMount | None:
        """
        Get the mount that contains a given path.

        Args:
            path: An original path (e.g., "/var/log" or "/var/log/syslog")

        Returns:
            The OriginalPathMount if path is under a mount, else None
        """
        # Find the longest matching mount path
        best_match = None
        best_len = 0

        for mount_path, mount in self.mounts.items():
            if path == mount_path or path.startswith(mount_path + "/"):
                if len(mount_path) > best_len:
                    best_match = mount
                    best_len = len(mount_path)

        return best_match

    def translate_to_docker_path(
        self,
        original_path: str,
        username: str
    ) -> str | None:
        """
        Translate an original path to its Docker container path.

        Args:
            original_path: Path like "/var/log" or "/var/log/syslog"
            username: User requesting access (for authorization check)

        Returns:
            Docker path like "/mounts/paths/_var_log/syslog", or None if not allowed
        """
        mount = self.get_mount_for_path(original_path)
        if not mount:
            return None

        if not self._is_user_authorized(mount, username):
            logger.warning(
                f"SECURITY: User '{username}' denied access to original path '{original_path}'"
            )
            return None

        # Calculate relative path within mount
        if original_path == mount.path:
            return mount.container_path
        else:
            relative = original_path[len(mount.path):].lstrip("/")
            return f"{mount.container_path}/{relative}"


# Singleton instance
_original_path_service: OriginalPathMountService | None = None
_original_path_config_mtime: float = 0


def get_original_path_mount_service() -> OriginalPathMountService:
    """
    Get the singleton OriginalPathMountService instance.

    Reloads configuration if the file has been modified.
    """
    global _original_path_service, _original_path_config_mtime

    config_path = Path("/config/external-mounts.yaml")

    if config_path.exists():
        current_mtime = config_path.stat().st_mtime
        if _original_path_service is None or current_mtime != _original_path_config_mtime:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                _original_path_service = OriginalPathMountService(config)
                _original_path_config_mtime = current_mtime
                logger.info(
                    f"Loaded original-path mount service: "
                    f"mounts={list(_original_path_service.mounts.keys())}"
                )
            except Exception as e:
                logger.error(f"Failed to load original-path mount config: {e}")
                _original_path_service = OriginalPathMountService({})
    elif _original_path_service is None:
        _original_path_service = OriginalPathMountService({})

    return _original_path_service


def invalidate_original_path_mount_cache() -> None:
    """Force reload of original-path mount configuration on next access."""
    global _original_path_service, _original_path_config_mtime
    _original_path_service = None
    _original_path_config_mtime = 0
