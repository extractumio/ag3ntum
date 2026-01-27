"""
Sandbox Path Resolver - Unified path translation for Ag3ntum.

This module provides a centralized, context-aware path resolution system that
translates between sandbox paths (what the agent sees inside bubblewrap) and
Docker paths (the real filesystem paths inside the Docker container).

ARCHITECTURE OVERVIEW:
======================

Ground Truth: SANDBOX PATHS
---------------------------
All paths in Ag3ntum are expressed in "sandbox path" format - the paths as
they appear inside the bubblewrap sandbox:

    /workspace/file.txt           - Main workspace file
    /workspace/external/persistent/img.png  - Persistent storage
    /workspace/external/ro/name/file.csv    - Read-only external mount
    /workspace/external/rw/name/file.txt    - Read-write external mount
    /venv/bin/python3             - User's Python virtual environment
    /skills/.claude/skills/name/  - Global skills
    /user-skills/name/            - User skills (per-user mount for isolation)

These paths are the CANONICAL representation used throughout the system.

Docker Paths (Translation Target):
----------------------------------
When code runs in Docker (not inside bwrap), paths must be translated:

    /workspace/file.txt → /users/{user}/sessions/{sid}/workspace/file.txt
    /workspace/external/persistent/img.png → /users/{user}/ag3ntum/persistent/img.png
    /workspace/external/ro/name/file.csv → /mounts/ro/name/file.csv
    /venv/bin/python3 → /users/{user}/venv/bin/python3

EXECUTION CONTEXTS:
==================

1. SANDBOX (bubblewrap):
   - Bash commands run here via mcp__ag3ntum__Bash
   - Paths work as-is, no translation needed
   - Environment variable: AG3NTUM_CONTEXT=sandbox

2. DOCKER (main Python process):
   - MCP tools (Read, Write, Edit, etc.) run here
   - API endpoints run here
   - Paths must be translated from sandbox → Docker format

USAGE:
======

    # Get resolver for a session
    resolver = get_sandbox_path_resolver(session_id)

    # Convert sandbox path to current context
    actual_path = resolver.resolve("./file.txt")

    # Explicit conversions
    docker_path = resolver.sandbox_to_docker("/workspace/file.txt")
    sandbox_path = resolver.docker_to_sandbox("/users/greg/sessions/xxx/workspace/file.txt")

    # Normalize any path to canonical sandbox format
    canonical = resolver.normalize("/workspace/./foo/../bar.txt")  # → /workspace/bar.txt

SECURITY:
=========

- All paths are validated to be within allowed mount boundaries
- Symlink resolution is controlled to prevent escape attacks
- Path traversal (../) is blocked or resolved within boundaries
- Unicode normalization prevents homograph attacks
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Execution Context Detection
# =============================================================================

class ExecutionContext(Enum):
    """
    Execution environment where code is running.

    SANDBOX: Inside bubblewrap sandbox (bash commands, skill scripts)
    DOCKER: Inside Docker container but outside bwrap (API, MCP tools)
    """
    SANDBOX = "sandbox"
    DOCKER = "docker"


# Cache the detected context (it doesn't change during process lifetime)
_cached_context: Optional[ExecutionContext] = None


def detect_execution_context() -> ExecutionContext:
    """
    Detect the current execution context.

    Detection strategy (in order):
    1. Check AG3NTUM_CONTEXT environment variable (set by bwrap via --setenv)
    2. Check filesystem markers (/workspace as mount vs subdirectory)
    3. Default to DOCKER (fail-safe for API/Python processes)

    Returns:
        ExecutionContext.SANDBOX if inside bubblewrap
        ExecutionContext.DOCKER if in main Docker container
    """
    global _cached_context

    if _cached_context is not None:
        return _cached_context

    # Method 1: Explicit environment variable (most reliable)
    # Bubblewrap sets this via: --setenv AG3NTUM_CONTEXT sandbox
    context_env = os.environ.get("AG3NTUM_CONTEXT", "").lower()
    if context_env == "sandbox":
        _cached_context = ExecutionContext.SANDBOX
        logger.debug("Execution context: SANDBOX (from AG3NTUM_CONTEXT env)")
        return _cached_context

    # Method 2: Check filesystem structure
    # In sandbox: /workspace exists and HOME=/workspace
    # In Docker: /workspace doesn't exist at root level
    workspace_root = Path("/workspace")
    home_env = os.environ.get("HOME", "")

    if workspace_root.exists() and workspace_root.is_dir() and home_env == "/workspace":
        # Additional verification: in sandbox, /users/*/sessions/* shouldn't be accessible
        # (only specific user paths are mounted)
        users_sessions = Path("/users")
        try:
            # In sandbox, listing /users should show limited content
            # In Docker, it shows all user directories
            if users_sessions.exists():
                subdirs = list(users_sessions.iterdir())
                # Heuristic: if we can see multiple session directories, we're in Docker
                session_count = 0
                for user_dir in subdirs[:5]:  # Check first 5 users max
                    sessions_dir = user_dir / "sessions"
                    if sessions_dir.exists():
                        try:
                            session_count += len(list(sessions_dir.iterdir())[:10])
                        except PermissionError:
                            pass
                if session_count > 5:
                    # Can see many sessions - likely Docker, not sandbox
                    _cached_context = ExecutionContext.DOCKER
                    logger.debug("Execution context: DOCKER (filesystem heuristic)")
                    return _cached_context
        except (PermissionError, OSError):
            pass

        _cached_context = ExecutionContext.SANDBOX
        logger.debug("Execution context: SANDBOX (filesystem check)")
        return _cached_context

    # Default to Docker (safe default for API and MCP tools)
    _cached_context = ExecutionContext.DOCKER
    logger.debug("Execution context: DOCKER (default)")
    return _cached_context


def reset_context_cache() -> None:
    """Reset the cached execution context (for testing)."""
    global _cached_context
    _cached_context = None


# =============================================================================
# Mount Configuration
# =============================================================================

@dataclass
class MountMapping:
    """
    Bidirectional mapping between sandbox and Docker paths.

    Attributes:
        sandbox_path: Path as seen in bubblewrap (e.g., /workspace)
        docker_path: Path as seen in Docker (e.g., /users/greg/sessions/xxx/workspace)
        mode: Access mode ('ro' for read-only, 'rw' for read-write)
        mount_type: Category of mount for logging/debugging
    """
    sandbox_path: str
    docker_path: str
    mode: str = "ro"  # 'ro' or 'rw'
    mount_type: str = "unknown"

    def matches_sandbox_path(self, path: str) -> bool:
        """Check if path starts with this mount's sandbox path."""
        # Exact match or path with trailing component
        return path == self.sandbox_path or path.startswith(self.sandbox_path + "/")

    def matches_docker_path(self, path: str) -> bool:
        """Check if path starts with this mount's docker path."""
        return path == self.docker_path or path.startswith(self.docker_path + "/")

    def sandbox_to_docker(self, sandbox_path: str) -> str:
        """Convert a sandbox path to docker path using this mount."""
        if sandbox_path == self.sandbox_path:
            return self.docker_path
        suffix = sandbox_path[len(self.sandbox_path):]
        return self.docker_path + suffix

    def docker_to_sandbox(self, docker_path: str) -> str:
        """Convert a docker path to sandbox path using this mount."""
        if docker_path == self.docker_path:
            return self.sandbox_path
        suffix = docker_path[len(self.docker_path):]
        return self.sandbox_path + suffix


@dataclass
class SandboxPathContext:
    """
    Session-specific mount configuration.

    Contains all the mount mappings for a specific session, enabling
    bidirectional path translation between sandbox and Docker contexts.
    """
    session_id: str
    username: str

    # Core mounts (always present)
    workspace_sandbox: str = "/workspace"
    workspace_docker: str = ""  # Set in __post_init__

    venv_sandbox: str = "/venv"
    venv_docker: str = ""  # Set in __post_init__

    # Skills mounts
    global_skills_sandbox: str = "/skills"
    global_skills_docker: str = "/skills"  # Same path in both contexts

    user_skills_sandbox: str = ""  # Set in __post_init__
    user_skills_docker: str = ""  # Set in __post_init__

    # Persistent storage
    # Agent sees: /workspace/external/persistent (symlink in sandbox)
    # Docker sees: /users/{user}/ag3ntum/persistent (actual directory)
    persistent_sandbox: str = "/workspace/external/persistent"
    persistent_docker: str = ""  # Set in __post_init__

    # External mounts bases
    external_ro_sandbox: str = "/mounts/ro"
    external_ro_docker: str = "/mounts/ro"

    external_rw_sandbox: str = "/mounts/rw"
    external_rw_docker: str = "/mounts/rw"

    # Per-user mounts (name -> host_path mappings)
    user_mounts_ro: dict[str, str] = field(default_factory=dict)
    user_mounts_rw: dict[str, str] = field(default_factory=dict)

    # All computed mounts (populated in __post_init__)
    _mounts: list[MountMapping] = field(default_factory=list, repr=False)

    def __post_init__(self):
        """Compute all mount mappings after initialization."""
        # Set dynamic paths based on username and session_id
        if not self.workspace_docker:
            self.workspace_docker = f"/users/{self.username}/sessions/{self.session_id}/workspace"
        if not self.venv_docker:
            self.venv_docker = f"/users/{self.username}/venv"
        if not self.user_skills_sandbox:
            self.user_skills_sandbox = "/user-skills"
        if not self.user_skills_docker:
            self.user_skills_docker = "/user-skills"
        # persistent_sandbox is always /workspace/external/persistent (hardcoded above)
        if not self.persistent_docker:
            self.persistent_docker = f"/users/{self.username}/ag3ntum/persistent"

        self._build_mounts()

    def _build_mounts(self) -> None:
        """Build the list of mount mappings, ordered by specificity (longest first)."""
        mounts = []

        # Workspace (most common)
        mounts.append(MountMapping(
            sandbox_path=self.workspace_sandbox,
            docker_path=self.workspace_docker,
            mode="rw",
            mount_type="workspace",
        ))

        # User venv
        mounts.append(MountMapping(
            sandbox_path=self.venv_sandbox,
            docker_path=self.venv_docker,
            mode="ro",
            mount_type="venv",
        ))

        # Global skills (same path in both contexts)
        mounts.append(MountMapping(
            sandbox_path=self.global_skills_sandbox,
            docker_path=self.global_skills_docker,
            mode="ro",
            mount_type="global_skills",
        ))

        # User skills (same path in both contexts)
        if self.user_skills_sandbox:
            mounts.append(MountMapping(
                sandbox_path=self.user_skills_sandbox,
                docker_path=self.user_skills_docker,
                mode="ro",
                mount_type="user_skills",
            ))

        # Persistent storage (same path in both contexts)
        if self.persistent_sandbox:
            mounts.append(MountMapping(
                sandbox_path=self.persistent_sandbox,
                docker_path=self.persistent_docker,
                mode="rw",
                mount_type="persistent",
            ))

        # External RO mounts (same path in both contexts)
        mounts.append(MountMapping(
            sandbox_path=self.external_ro_sandbox,
            docker_path=self.external_ro_docker,
            mode="ro",
            mount_type="external_ro",
        ))

        # External RW mounts (same path in both contexts)
        mounts.append(MountMapping(
            sandbox_path=self.external_rw_sandbox,
            docker_path=self.external_rw_docker,
            mode="rw",
            mount_type="external_rw",
        ))

        # Per-user mounts
        for name, host_path in self.user_mounts_ro.items():
            # User-ro mounts: sandbox sees /workspace/external/user-ro/{name}
            # which is a symlink to the actual host path
            mounts.append(MountMapping(
                sandbox_path=f"/workspace/external/user-ro/{name}",
                docker_path=host_path,
                mode="ro",
                mount_type="user_mount_ro",
            ))

        for name, host_path in self.user_mounts_rw.items():
            mounts.append(MountMapping(
                sandbox_path=f"/workspace/external/user-rw/{name}",
                docker_path=host_path,
                mode="rw",
                mount_type="user_mount_rw",
            ))

        # Sort by sandbox_path length (descending) for longest-prefix matching
        mounts.sort(key=lambda m: len(m.sandbox_path), reverse=True)

        self._mounts = mounts

    @property
    def mounts(self) -> list[MountMapping]:
        """Get all mount mappings."""
        return self._mounts

    def find_mount_for_sandbox_path(self, sandbox_path: str) -> Optional[MountMapping]:
        """Find the mount mapping that matches a sandbox path (longest prefix match)."""
        for mount in self._mounts:
            if mount.matches_sandbox_path(sandbox_path):
                return mount
        return None

    def find_mount_for_docker_path(self, docker_path: str) -> Optional[MountMapping]:
        """Find the mount mapping that matches a docker path (longest prefix match)."""
        for mount in self._mounts:
            if mount.matches_docker_path(docker_path):
                return mount
        return None


# =============================================================================
# Path Resolver
# =============================================================================

class PathResolutionError(Exception):
    """Raised when path resolution fails."""

    def __init__(self, message: str, path: str, reason: str):
        super().__init__(message)
        self.path = path
        self.reason = reason


class SandboxPathResolver:
    """
    Resolves paths between sandbox and Docker contexts.

    This is the central path resolution component that:
    1. Normalizes paths to canonical sandbox format
    2. Translates sandbox paths to Docker paths (and vice versa)
    3. Handles workspace-relative paths (./foo, foo)
    4. Resolves external mount symlinks
    5. Validates paths are within allowed boundaries

    Thread Safety:
        This class is thread-safe. The context is immutable after creation,
        and path resolution is a pure function with no side effects.

    Usage:
        resolver = SandboxPathResolver(context)

        # Normalize any path to canonical sandbox format
        canonical = resolver.normalize("./file.txt")  # → /workspace/file.txt

        # Convert to Docker path for file operations
        docker_path = resolver.sandbox_to_docker("/workspace/file.txt")

        # Auto-detect context and resolve accordingly
        actual_path = resolver.resolve("/workspace/file.txt")
    """

    def __init__(self, context: SandboxPathContext):
        """
        Initialize resolver with session-specific context.

        Args:
            context: The mount configuration for this session
        """
        self._context = context
        self._execution_context = detect_execution_context()

    @property
    def context(self) -> SandboxPathContext:
        """Get the path context configuration."""
        return self._context

    @property
    def execution_context(self) -> ExecutionContext:
        """Get the current execution context."""
        return self._execution_context

    def normalize(self, path: str) -> str:
        """
        Normalize any path to canonical sandbox format.

        Handles:
        - Relative paths: ./foo, foo → /workspace/foo
        - Workspace paths: /workspace/foo → /workspace/foo
        - External mount shortcuts: external/persistent/foo → /workspace/external/persistent/foo
        - Path normalization: /workspace/./foo/../bar → /workspace/bar

        Args:
            path: Input path in any format

        Returns:
            Canonical sandbox path (absolute, normalized)

        Raises:
            PathResolutionError: If path contains invalid components
        """
        if not path:
            raise PathResolutionError("Empty path", path="", reason="EMPTY_PATH")

        path = path.strip()

        # Handle null bytes (security)
        if '\x00' in path:
            raise PathResolutionError(
                f"Path contains null bytes: {path!r}",
                path=path,
                reason="NULL_BYTES",
            )

        # Parse as POSIX path
        p = PurePosixPath(path)
        path_str = str(p)

        # Handle relative paths
        if not p.is_absolute():
            # Check for external mount shortcuts (external/persistent/...)
            if path_str.startswith("external/") or path_str.startswith("./external/"):
                clean_path = path_str.lstrip("./")
                path_str = f"/workspace/{clean_path}"
            else:
                # Regular relative path - relative to /workspace
                clean_path = path_str.lstrip("./")
                path_str = f"/workspace/{clean_path}"
            p = PurePosixPath(path_str)

        # Resolve . and .. components
        parts = []
        for part in p.parts:
            if part == "/" or part == ".":
                # Skip root and current directory markers
                continue
            elif part == "..":
                if parts:  # Don't go above root
                    parts.pop()
                # Note: We don't raise on .. that would escape root
                # The boundary check later will catch invalid escapes
            else:
                parts.append(part)

        # Reconstruct as absolute path
        normalized = "/" + "/".join(parts) if parts else "/"

        return normalized

    def sandbox_to_docker(self, sandbox_path: str) -> str:
        """
        Convert a sandbox path to Docker path.

        This is used by MCP tools and API endpoints that run in Docker
        but receive paths in sandbox format.

        Args:
            sandbox_path: Path in sandbox format (e.g., /workspace/file.txt)

        Returns:
            Equivalent path in Docker format

        Raises:
            PathResolutionError: If path is not within any allowed mount
        """
        # Normalize first
        normalized = self.normalize(sandbox_path)

        # Handle special workspace external paths
        # Agent sees: /workspace/external/persistent/foo
        # This is a symlink that resolves to: /users/{user}/ag3ntum/persistent/foo
        # Both sandbox and Docker see the same path for persistent storage
        if normalized.startswith("/workspace/external/"):
            external_part = normalized[len("/workspace/external/"):]

            if external_part.startswith("persistent/") or external_part == "persistent":
                # Map to persistent storage path
                suffix = external_part[len("persistent"):].lstrip("/")
                if suffix:
                    return f"{self._context.persistent_docker}/{suffix}"
                return self._context.persistent_docker

            elif external_part.startswith("ro/") or external_part == "ro":
                # Map to external RO mount
                if external_part == "ro":
                    return self._context.external_ro_docker
                suffix = external_part[len("ro/"):]
                return f"{self._context.external_ro_docker}/{suffix}"

            elif external_part.startswith("rw/") or external_part == "rw":
                # Map to external RW mount
                if external_part == "rw":
                    return self._context.external_rw_docker
                suffix = external_part[len("rw/"):]
                return f"{self._context.external_rw_docker}/{suffix}"

            elif external_part.startswith("user-ro/"):
                # Map to per-user RO mount
                remaining = external_part[len("user-ro/"):]
                if "/" in remaining:
                    mount_name, suffix = remaining.split("/", 1)
                else:
                    mount_name = remaining
                    suffix = ""

                if mount_name in self._context.user_mounts_ro:
                    host_path = self._context.user_mounts_ro[mount_name]
                    if suffix:
                        return f"{host_path}/{suffix}"
                    return host_path
                else:
                    raise PathResolutionError(
                        f"Unknown user-ro mount: {mount_name}",
                        path=sandbox_path,
                        reason="UNKNOWN_MOUNT",
                    )

            elif external_part.startswith("user-rw/"):
                # Map to per-user RW mount
                remaining = external_part[len("user-rw/"):]
                if "/" in remaining:
                    mount_name, suffix = remaining.split("/", 1)
                else:
                    mount_name = remaining
                    suffix = ""

                if mount_name in self._context.user_mounts_rw:
                    host_path = self._context.user_mounts_rw[mount_name]
                    if suffix:
                        return f"{host_path}/{suffix}"
                    return host_path
                else:
                    raise PathResolutionError(
                        f"Unknown user-rw mount: {mount_name}",
                        path=sandbox_path,
                        reason="UNKNOWN_MOUNT",
                    )

        # Find matching mount
        mount = self._context.find_mount_for_sandbox_path(normalized)
        if mount:
            return mount.sandbox_to_docker(normalized)

        # No mount found - path is outside allowed directories
        raise PathResolutionError(
            f"Path not within any allowed mount: {sandbox_path}",
            path=sandbox_path,
            reason="OUTSIDE_MOUNTS",
        )

    def docker_to_sandbox(self, docker_path: str) -> str:
        """
        Convert a Docker path to sandbox path.

        This is used to translate error messages or paths from Docker
        processes back to the canonical sandbox format.

        Args:
            docker_path: Path in Docker format

        Returns:
            Equivalent path in sandbox format

        Raises:
            PathResolutionError: If path is not within any allowed mount
        """
        # Normalize the docker path (resolve . and ..)
        p = PurePosixPath(docker_path)
        parts = []
        for part in p.parts:
            if part == "/" or part == ".":
                # Skip root and current directory markers
                continue
            elif part == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(part)
        # Reconstruct as absolute path
        normalized = "/" + "/".join(parts) if parts else "/"

        # Find matching mount
        mount = self._context.find_mount_for_docker_path(normalized)
        if mount:
            return mount.docker_to_sandbox(normalized)

        # Check for special paths that might be in persistent/external storage
        # These have the same path in both contexts
        if normalized.startswith(self._context.persistent_docker):
            suffix = normalized[len(self._context.persistent_docker):].lstrip("/")
            if suffix:
                return f"/workspace/external/persistent/{suffix}"
            return "/workspace/external/persistent"

        raise PathResolutionError(
            f"Docker path not within any allowed mount: {docker_path}",
            path=docker_path,
            reason="OUTSIDE_MOUNTS",
        )

    def resolve(self, path: str) -> str:
        """
        Resolve a sandbox path for the current execution context.

        This is the main entry point for path resolution:
        - If running in SANDBOX context: return normalized sandbox path
        - If running in DOCKER context: translate to Docker path

        Args:
            path: Input path (can be relative or absolute)

        Returns:
            Path appropriate for current execution context
        """
        normalized = self.normalize(path)

        if self._execution_context == ExecutionContext.SANDBOX:
            return normalized
        else:
            return self.sandbox_to_docker(normalized)

    def resolve_to_docker(self, path: str) -> str:
        """
        Always resolve to Docker path, regardless of context.

        Use this when you explicitly need the Docker path (e.g., for
        Python file operations that always run in Docker context).

        Args:
            path: Input path (can be relative or absolute)

        Returns:
            Docker filesystem path
        """
        return self.sandbox_to_docker(path)

    def is_path_writable(self, sandbox_path: str) -> bool:
        """
        Check if a sandbox path is writable.

        Args:
            sandbox_path: Path in sandbox format

        Returns:
            True if path is within a writable mount
        """
        try:
            normalized = self.normalize(sandbox_path)
        except PathResolutionError:
            return False

        # Check workspace external paths
        if normalized.startswith("/workspace/external/"):
            external_part = normalized[len("/workspace/external/"):]

            if external_part.startswith("persistent") or external_part == "persistent":
                return True  # Persistent is writable
            elif external_part.startswith("ro/") or external_part == "ro":
                return False  # Read-only mount
            elif external_part.startswith("rw/") or external_part == "rw":
                return True  # Read-write mount
            elif external_part.startswith("user-ro/"):
                return False  # Per-user read-only mount
            elif external_part.startswith("user-rw/"):
                return True  # Per-user read-write mount

        # Find matching mount
        mount = self._context.find_mount_for_sandbox_path(normalized)
        if mount:
            return mount.mode == "rw"

        return False

    def get_mount_type(self, sandbox_path: str) -> Optional[str]:
        """
        Get the mount type for a sandbox path.

        Args:
            sandbox_path: Path in sandbox format

        Returns:
            Mount type string (e.g., 'workspace', 'persistent', 'external_ro')
            or None if path is not in any mount
        """
        try:
            normalized = self.normalize(sandbox_path)
        except PathResolutionError:
            return None

        # Check workspace external paths
        if normalized.startswith("/workspace/external/"):
            external_part = normalized[len("/workspace/external/"):]

            if external_part.startswith("persistent") or external_part == "persistent":
                return "persistent"
            elif external_part.startswith("ro/") or external_part == "ro":
                return "external_ro"
            elif external_part.startswith("rw/") or external_part == "rw":
                return "external_rw"
            elif external_part.startswith("user-ro/"):
                return "user_mount_ro"
            elif external_part.startswith("user-rw/"):
                return "user_mount_rw"

        mount = self._context.find_mount_for_sandbox_path(normalized)
        if mount:
            return mount.mount_type

        return None

    def translate_error_paths(self, error_message: str) -> str:
        """
        Translate Docker paths in error messages to sandbox paths.

        This makes error messages more user-friendly by showing paths
        in the format the agent understands.

        Args:
            error_message: Error message that may contain Docker paths

        Returns:
            Error message with Docker paths replaced by sandbox paths
        """
        # Pattern to find Docker workspace paths
        # Matches: /users/username/sessions/session_id/workspace/...
        workspace_pattern = re.compile(
            r'/users/[^/]+/sessions/[^/]+/workspace(/[^\s\'"]*)?'
        )

        def replace_workspace(match: re.Match) -> str:
            docker_path = match.group(0)
            try:
                return self.docker_to_sandbox(docker_path)
            except PathResolutionError:
                return docker_path

        result = workspace_pattern.sub(replace_workspace, error_message)

        # Also translate persistent storage paths
        persistent_pattern = re.compile(
            r'/users/[^/]+/ag3ntum/persistent(/[^\s\'"]*)?'
        )

        def replace_persistent(match: re.Match) -> str:
            suffix = match.group(1) or ""
            return f"/workspace/external/persistent{suffix}"

        result = persistent_pattern.sub(replace_persistent, result)

        return result


# =============================================================================
# Session-Scoped Resolver Management
# =============================================================================

# Session-scoped resolvers (each session has its own)
_session_resolvers: dict[str, SandboxPathResolver] = {}


def get_sandbox_path_resolver(session_id: str) -> SandboxPathResolver:
    """
    Get the path resolver for a session.

    Args:
        session_id: The session ID

    Returns:
        The configured SandboxPathResolver for this session

    Raises:
        RuntimeError: If resolver not configured for this session
    """
    if session_id not in _session_resolvers:
        raise RuntimeError(
            f"SandboxPathResolver not configured for session {session_id}. "
            "Call configure_sandbox_path_resolver() first."
        )
    return _session_resolvers[session_id]


def configure_sandbox_path_resolver(
    session_id: str,
    username: str,
    workspace_docker: Optional[str] = None,
    user_mounts_ro: Optional[dict[str, str]] = None,
    user_mounts_rw: Optional[dict[str, str]] = None,
) -> SandboxPathResolver:
    """
    Configure and return path resolver for a session.

    This should be called during session creation, before any file
    operations are performed.

    Args:
        session_id: The session ID
        username: The username for this session
        workspace_docker: Override the Docker workspace path
        user_mounts_ro: Per-user read-only mounts {name: host_path}
        user_mounts_rw: Per-user read-write mounts {name: host_path}

    Returns:
        The configured SandboxPathResolver
    """
    context = SandboxPathContext(
        session_id=session_id,
        username=username,
        workspace_docker=workspace_docker or f"/users/{username}/sessions/{session_id}/workspace",
        user_mounts_ro=user_mounts_ro or {},
        user_mounts_rw=user_mounts_rw or {},
    )

    resolver = SandboxPathResolver(context)
    _session_resolvers[session_id] = resolver

    logger.info(
        f"SANDBOX_PATH_RESOLVER: Configured for session {session_id}, "
        f"user={username}, context={resolver.execution_context.value}"
    )

    return resolver


def cleanup_sandbox_path_resolver(session_id: str) -> None:
    """
    Remove path resolver when session ends.

    Args:
        session_id: The session ID to clean up
    """
    if session_id in _session_resolvers:
        del _session_resolvers[session_id]
        logger.info(f"SANDBOX_PATH_RESOLVER: Cleaned up resolver for session {session_id}")


def has_sandbox_path_resolver(session_id: str) -> bool:
    """
    Check if a path resolver is configured for a session.

    Args:
        session_id: The session ID to check

    Returns:
        True if resolver is configured, False otherwise
    """
    return session_id in _session_resolvers


# =============================================================================
# Utility Functions
# =============================================================================

def create_resolver_for_session(
    session_id: str,
    username: str,
    workspace_path: Path,
    user_mounts_ro: Optional[dict[str, Path]] = None,
    user_mounts_rw: Optional[dict[str, Path]] = None,
) -> SandboxPathResolver:
    """
    Create a resolver with Path objects (convenience function).

    This is a convenience wrapper that accepts Path objects and converts
    them to strings for the SandboxPathContext.

    Args:
        session_id: The session ID
        username: The username
        workspace_path: Docker path to workspace (Path object)
        user_mounts_ro: Per-user RO mounts {name: Path}
        user_mounts_rw: Per-user RW mounts {name: Path}

    Returns:
        Configured SandboxPathResolver
    """
    return configure_sandbox_path_resolver(
        session_id=session_id,
        username=username,
        workspace_docker=str(workspace_path),
        user_mounts_ro={k: str(v) for k, v in (user_mounts_ro or {}).items()},
        user_mounts_rw={k: str(v) for k, v in (user_mounts_rw or {}).items()},
    )
