"""
Unified path validation for all Ag3ntum tools.

Single source of truth for path normalization, validation, and logging.
All Ag3ntum file tools use this validator before performing operations.

ARCHITECTURE:
=============

This module works in conjunction with sandbox_path_resolver.py to provide
a complete path handling solution:

1. SandboxPathResolver (sandbox_path_resolver.py):
   - Defines canonical path format (sandbox paths)
   - Provides bidirectional translation (sandbox ↔ Docker)
   - Context-aware resolution

2. Ag3ntumPathValidator (this module):
   - Security validation (blocklist, allowlist, boundaries)
   - Read-only path enforcement
   - Access logging

EXECUTION CONTEXT:
==================

This validator runs in the main Python process, which sees the REAL Docker
filesystem paths (e.g., /users/greg/sessions/xxx/workspace), NOT bwrap mount
paths (/workspace). The agent thinks it's working with /workspace, but we
must translate to real paths for Python file operations.

Bwrap paths (/workspace) are only visible inside subprocesses launched via
Ag3ntumBash. All other Ag3ntum tools (Ag3ntumRead, Ag3ntumWrite, etc.) run
in the main process and need this validator for security.

PATH TRANSLATION:
=================

Agent provides: /workspace/file.txt (sandbox path)
Validator returns: /users/greg/sessions/xxx/workspace/file.txt (Docker path)

For external mounts:
- /workspace/persistent/* → /users/{user}/ag3ntum/persistent/*
- /workspace/external/ro/* → /mounts/ro/*
- /workspace/external/rw/* → /mounts/rw/*
"""
import fnmatch
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Import sandbox path resolver for integrated path handling
from src.core.sandbox_path_resolver import (
    SandboxPathResolver,
    SandboxPathContext,
    configure_sandbox_path_resolver,
    cleanup_sandbox_path_resolver,
    get_sandbox_path_resolver,
    has_sandbox_path_resolver,
    PathResolutionError,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Security Constants - Single source of truth for path validation defaults
# =============================================================================

# Default blocklist patterns for sensitive files (matched against relative paths)
DEFAULT_BLOCKLIST: list[str] = [
    "*.env", ".env.*",  # .env, production.env, .env.local, .env.development, etc.
    "*.key", ".git/**", "__pycache__/**", "*.pyc",
    ".secrets/**", "*.pem", "*.p12", "*.pfx",
    "**/node_modules/**",  # Prevent massive directory traversal
]

# Exemptions from blocklist — safe template/documentation files that should remain accessible
DEFAULT_BLOCKLIST_EXEMPTIONS: list[str] = [
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.defaults",
]

# Default read-only path prefixes (relative to workspace)
# These paths can be read but not written/edited/deleted by the agent
DEFAULT_READONLY_PREFIXES: list[str] = [
    "skills/",           # Legacy skills location
    ".claude/",          # SDK configuration and skills (SECURITY: prevents skill tampering)
    "external/ro/",      # Read-only external mounts
    "external/user-ro/", # Per-user read-only mounts
]


# =============================================================================
# Path Sanitizer - Security hardening for external mount filenames
# =============================================================================

class PathSanitizer:
    """
    Sanitize filenames from external mounts for security.

    This class provides defense-in-depth against:
    - Path traversal attacks (../)
    - Null byte injection
    - Control character injection
    - Unicode normalization attacks
    - Windows reserved device names
    - Excessively long filenames

    Used primarily for validating filenames in externally mounted folders
    where we can't control the file naming conventions.
    """

    # Dangerous filename patterns to reject
    DANGEROUS_PATTERNS = [
        r"\.\.[\\/]",           # Path traversal (../ or ..\)
        r"^\.\.?$",             # Current/parent dir references
        r"[\x00-\x1f]",         # Control characters (ASCII 0-31)
        r"[<>:\"|?*]",          # Windows reserved characters
        r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$",  # Windows device names
    ]

    # Zero-width and invisible unicode characters that could hide content
    INVISIBLE_CHARS = [
        "\u200b",  # Zero-width space
        "\u200c",  # Zero-width non-joiner
        "\u200d",  # Zero-width joiner
        "\ufeff",  # Byte order mark
        "\u00ad",  # Soft hyphen
        "\u2060",  # Word joiner
        "\u2061",  # Function application
        "\u2062",  # Invisible times
        "\u2063",  # Invisible separator
        "\u2064",  # Invisible plus
    ]

    # Max filename length (common filesystem limit)
    MAX_FILENAME_LENGTH = 255

    @classmethod
    def sanitize_filename(cls, filename: str, raise_on_error: bool = True) -> str:
        """
        Sanitize a filename, optionally raising error if dangerous.

        Args:
            filename: The filename to sanitize
            raise_on_error: If True, raise PathValidationError for dangerous names.
                           If False, return sanitized version.

        Returns:
            Sanitized filename

        Raises:
            PathValidationError: If filename is dangerous and raise_on_error=True
        """
        if not filename:
            if raise_on_error:
                raise PathValidationError(
                    "Empty filename",
                    path=filename,
                    reason="Filename cannot be empty",
                )
            return ""

        original = filename

        # Normalize unicode to NFC form (canonical composition)
        # This prevents homograph attacks using visually similar characters
        try:
            filename = unicodedata.normalize("NFC", filename)
        except Exception:
            pass

        # Remove invisible/zero-width characters
        for char in cls.INVISIBLE_CHARS:
            filename = filename.replace(char, "")

        # Check for dangerous patterns
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, filename, re.IGNORECASE):
                if raise_on_error:
                    raise PathValidationError(
                        f"Dangerous filename pattern detected: {original!r}",
                        path=original,
                        reason="DANGEROUS_FILENAME",
                    )
                # For non-raising mode, remove the dangerous part
                filename = re.sub(pattern, "_", filename, flags=re.IGNORECASE)

        # Check length (after normalization)
        if len(filename.encode("utf-8")) > cls.MAX_FILENAME_LENGTH:
            if raise_on_error:
                raise PathValidationError(
                    f"Filename too long ({len(filename)} chars): {filename[:50]}...",
                    path=original,
                    reason="FILENAME_TOO_LONG",
                )
            # Truncate to max length while preserving extension if possible
            if "." in filename:
                name, ext = filename.rsplit(".", 1)
                max_name_len = cls.MAX_FILENAME_LENGTH - len(ext) - 1
                filename = name[:max_name_len] + "." + ext
            else:
                filename = filename[: cls.MAX_FILENAME_LENGTH]

        return filename

    @classmethod
    def validate_path_components(cls, path: Path) -> None:
        """
        Validate all components of a path.

        Args:
            path: The path to validate

        Raises:
            PathValidationError: If any component is dangerous
        """
        for component in path.parts:
            if component not in ("/", ""):
                cls.sanitize_filename(component, raise_on_error=True)

    @classmethod
    def has_null_bytes(cls, path: str) -> bool:
        """Check if path contains null bytes."""
        return "\x00" in path

    @classmethod
    def has_path_traversal(cls, path: str) -> bool:
        """Check if path contains traversal attempts."""
        # Normalize path separators
        normalized = path.replace("\\", "/")
        parts = normalized.split("/")
        return any(part == ".." for part in parts)


class PathValidatorConfig(BaseModel):
    """
    Configuration for path validation.

    IMPORTANT: This uses REAL Docker filesystem paths, not bwrap mount paths.
    PathValidator runs in the main Python process, which sees the full Docker
    filesystem. Bwrap paths (/workspace) are only visible inside subprocesses.
    """

    # REAL path to session workspace (e.g., /users/greg/sessions/xxx/workspace)
    workspace_path: Path = Field(
        description="Actual filesystem path to session workspace (required)"
    )
    # REAL path to skills directory (legacy, unused - use global/user skills paths)
    skills_path: Path | None = Field(
        default=None, description="Deprecated: use global_skills_path/user_skills_path"
    )
    # REAL path to global skills directory (e.g., /skills/.claude/skills)
    global_skills_path: Path | None = Field(
        default=None, description="Path to global skills directory (read-only)"
    )
    # REAL path to user skills directory (e.g., /users/username/.claude/skills)
    user_skills_path: Path | None = Field(
        default=None, description="Path to user skills directory (read-only)"
    )

    # =========================================================================
    # EXTERNAL MOUNT PATHS - Host folders mounted via run.sh (flattened structure)
    # =========================================================================
    # These are Docker container paths (not bwrap paths).
    # With flattened mount structure, all mounts are at /mounts/{name}
    # Agent sees: /workspace/external/ro/* -> Real path: /mounts/{name}
    # Agent sees: /workspace/external/rw/* -> Real path: /mounts/{name}
    # Agent sees: /workspace/persistent/* -> Real path: /users/{username}/ag3ntum/persistent/*

    # Global mounts from external-mounts.yaml global section
    global_mounts_ro: dict[str, Path] = Field(
        default_factory=dict,
        description="Global read-only mounts: {name: container_path}"
    )
    global_mounts_rw: dict[str, Path] = Field(
        default_factory=dict,
        description="Global read-write mounts: {name: container_path}"
    )
    persistent_path: Path | None = Field(
        default=None,
        description="Path to user's persistent storage (/users/{username}/ag3ntum/persistent)"
    )

    # =========================================================================
    # PER-USER MOUNT PATHS - User-specific external mounts
    # =========================================================================
    # These are configured via external-mounts.yaml per_user section.
    # With flattened structure, mounts appear at /mounts/{name}
    # Agent sees: /workspace/external/user-ro/{name}/* -> Real path: /mounts/{name}/*
    # Agent sees: /workspace/external/user-rw/{name}/* -> Real path: /mounts/{name}/*

    user_mounts_ro: dict[str, Path] = Field(
        default_factory=dict,
        description="Per-user read-only mounts: {name: container_path}"
    )
    user_mounts_rw: dict[str, Path] = Field(
        default_factory=dict,
        description="Per-user read-write mounts: {name: container_path}"
    )

    # =========================================================================
    # DYNAMIC MOUNT PATHS - Session-time user-selected mounts
    # =========================================================================
    # These are configured via API at session creation time.
    # Agent sees: ./{alias}/* via symlinks at workspace root
    # Real path: /mounts/{base}/{subpath}/* (flattened structure)
    # The symlinks are created at workspace/{alias} pointing to /mounts/{base}/{subpath}

    dynamic_mounts_ro: dict[str, Path] = Field(
        default_factory=dict,
        description="Dynamic read-only mounts for this session: {alias: container_path}"
    )
    dynamic_mounts_rw: dict[str, Path] = Field(
        default_factory=dict,
        description="Dynamic read-write mounts for this session: {alias: container_path}"
    )

    # =========================================================================
    # ORIGINAL-PATH MOUNTS - Access paths at their original locations
    # =========================================================================
    # These allow accessing paths like /var/log at /var/log (not via workspace).
    # Docker mounts them at /mounts/paths/{encoded}, and bubblewrap bind-mounts
    # them to their original locations inside the sandbox.
    # For file tools in the main Python process, we translate original paths
    # to Docker paths: /var/log -> /mounts/paths/_var_log

    original_path_mounts_ro: dict[str, Path] = Field(
        default_factory=dict,
        description="Original-path read-only mounts: {original_path: docker_path}"
    )
    original_path_mounts_rw: dict[str, Path] = Field(
        default_factory=dict,
        description="Original-path read-write mounts: {original_path: docker_path}"
    )

    log_all_access: bool = Field(
        default=True, description="Log all path access attempts"
    )
    blocklist: list[str] = Field(
        default_factory=lambda: DEFAULT_BLOCKLIST.copy(),
        description="Glob patterns to block even within workspace",
    )
    blocklist_exemptions: list[str] = Field(
        default_factory=lambda: DEFAULT_BLOCKLIST_EXEMPTIONS.copy(),
        description="Filename patterns exempt from blocklist (e.g., .env.example)",
    )
    allowlist: list[str] | None = Field(
        default=None, description="If set, only these patterns are allowed"
    )
    readonly_prefixes: list[str] = Field(
        default_factory=lambda: DEFAULT_READONLY_PREFIXES.copy(),
        description="Path prefixes (relative to workspace) that are read-only",
    )


@dataclass
class ValidatedPath:
    """Result of path validation."""

    original: str
    normalized: Path
    is_readonly: bool = False


class PathValidationError(Exception):
    """Raised when path validation fails."""

    def __init__(self, message: str, path: str, reason: str):
        super().__init__(message)
        self.path = path
        self.reason = reason


class Ag3ntumPathValidator:
    """
    Centralized path validation for all Ag3ntum tools.

    IMPORTANT: This runs in the main Python process, NOT inside bwrap.
    It sees the REAL Docker filesystem paths, not bwrap mount paths.

    Responsibilities:
        1. Normalize paths: ./foo, /workspace/foo, foo -> /users/greg/sessions/xxx/workspace/foo
        2. Validate paths are within workspace boundary
        3. Check blocklist/allowlist patterns
        4. Identify read-only paths (skills)
        5. Log all access attempts
    """

    def __init__(self, config: PathValidatorConfig):
        """
        Initialize with session-specific configuration.

        Args:
            config: Must include workspace_path (the REAL path in Docker filesystem)
        """
        self.config = config
        self.workspace = config.workspace_path.resolve()  # REAL Docker path
        self.skills = config.skills_path.resolve() if config.skills_path else None
        # Additional read-only paths for skills access
        self.global_skills = config.global_skills_path.resolve() if config.global_skills_path else None
        self.user_skills = config.user_skills_path.resolve() if config.user_skills_path else None

        # External mount paths (flattened structure: all at /mounts/{name})
        # Agent sees: /workspace/external/ro/* -> Real path: /mounts/{name}
        # Agent sees: /workspace/external/rw/* -> Real path: /mounts/{name}
        self.global_mounts_ro: dict[str, Path] = {
            name: path.resolve() for name, path in config.global_mounts_ro.items()
        }
        self.global_mounts_rw: dict[str, Path] = {
            name: path.resolve() for name, path in config.global_mounts_rw.items()
        }
        # Agent sees: /workspace/persistent/* -> Real path: /users/{username}/ag3ntum/persistent/*
        self.persistent = config.persistent_path.resolve() if config.persistent_path else None

        # Per-user mount paths (resolved at session start, flattened structure)
        # Agent sees: /workspace/external/user-ro/{name}/* -> Real path: /mounts/{name}/*
        # Agent sees: /workspace/external/user-rw/{name}/* -> Real path: /mounts/{name}/*
        self.user_mounts_ro: dict[str, Path] = {
            name: path.resolve() for name, path in config.user_mounts_ro.items()
        }
        self.user_mounts_rw: dict[str, Path] = {
            name: path.resolve() for name, path in config.user_mounts_rw.items()
        }

        # Dynamic mount paths (configured per-session via API, flattened structure)
        # Agent sees: ./{alias}/* via symlinks -> Real path: /mounts/{base}/*
        self.dynamic_mounts_ro: dict[str, Path] = {
            alias: path.resolve() for alias, path in config.dynamic_mounts_ro.items()
        }
        self.dynamic_mounts_rw: dict[str, Path] = {
            alias: path.resolve() for alias, path in config.dynamic_mounts_rw.items()
        }

        # Original-path mounts (access paths at original locations)
        # Agent sees: /var/log/* -> Docker path: /mounts/paths/_var_log/*
        # The key is the original path, the value is the Docker path
        self.original_path_mounts_ro: dict[str, Path] = {
            orig: docker.resolve() for orig, docker in config.original_path_mounts_ro.items()
        }
        self.original_path_mounts_rw: dict[str, Path] = {
            orig: docker.resolve() for orig, docker in config.original_path_mounts_rw.items()
        }

        # Extract session context from workspace path for cross-user/cross-session blocking
        # Path format: .../users/{username}/sessions/{session_id}/workspace
        # Note: /users/ may appear anywhere in path (e.g., /tmp/test/users/... in tests)
        self._session_username: str | None = None
        self._session_id: str | None = None
        workspace_str = str(config.workspace_path)
        users_idx = workspace_str.find("/users/")
        if users_idx >= 0:
            # Extract the portion starting from /users/
            users_path = workspace_str[users_idx:]
            parts = users_path.split("/")
            # parts[0] = "", parts[1] = "users", parts[2] = username, ...
            if len(parts) >= 3:
                self._session_username = parts[2]
            if len(parts) >= 5 and parts[3] == "sessions":
                self._session_id = parts[4]

    def docker_to_display_path(self, docker_path: Path) -> str:
        """
        Convert a Docker internal path back to an agent-visible display path.

        Used by LS, Glob, Grep tools to show user-friendly paths instead of
        raw Docker internal paths (e.g., /mounts/global_var_log/apt/).

        Translation priority:
            1. Workspace-relative (e.g., ./src/main.py → src/main.py)
            2. Persistent storage (e.g., /users/.../persistent/x → persistent/x)
            3. Global RO mounts (e.g., /mounts/name/x → external/ro/name/x)
            4. Global RW mounts (e.g., /mounts/name/x → external/rw/name/x)
            5. Per-user RO mounts → external/user-ro/name/x
            6. Per-user RW mounts → external/user-rw/name/x
            7. Dynamic RO mounts → dynamic/alias/x
            8. Dynamic RW mounts → dynamic/alias/x
            9. Original-path mounts → /original/path/x
           10. Fallback: return str(docker_path)

        Args:
            docker_path: Docker filesystem path (may be unresolved/symlinked)

        Returns:
            Agent-visible display path string
        """
        # 1. Workspace-relative: try WITHOUT resolving first to preserve symlink names
        try:
            return str(docker_path.relative_to(self.workspace))
        except ValueError:
            pass

        # For mount paths, resolve to follow symlinks and match mount boundaries
        resolved = docker_path.resolve()

        # Also try workspace-relative with resolved path (for paths reached via symlinks)
        try:
            return str(resolved.relative_to(self.workspace))
        except ValueError:
            pass

        # 2. Persistent storage
        if self.persistent:
            try:
                rel = resolved.relative_to(self.persistent)
                return f"persistent/{rel}" if str(rel) != "." else "persistent"
            except ValueError:
                pass

        # 3-4. Global mounts
        for name, mount_path in self.global_mounts_ro.items():
            try:
                rel = resolved.relative_to(mount_path)
                return f"external/ro/{name}/{rel}" if str(rel) != "." else f"external/ro/{name}"
            except ValueError:
                pass

        for name, mount_path in self.global_mounts_rw.items():
            try:
                rel = resolved.relative_to(mount_path)
                return f"external/rw/{name}/{rel}" if str(rel) != "." else f"external/rw/{name}"
            except ValueError:
                pass

        # 5-6. Per-user mounts
        for name, mount_path in self.user_mounts_ro.items():
            try:
                rel = resolved.relative_to(mount_path)
                return f"external/user-ro/{name}/{rel}" if str(rel) != "." else f"external/user-ro/{name}"
            except ValueError:
                pass

        for name, mount_path in self.user_mounts_rw.items():
            try:
                rel = resolved.relative_to(mount_path)
                return f"external/user-rw/{name}/{rel}" if str(rel) != "." else f"external/user-rw/{name}"
            except ValueError:
                pass

        # 7-8. Dynamic mounts
        for alias, mount_path in self.dynamic_mounts_ro.items():
            try:
                rel = resolved.relative_to(mount_path)
                return f"dynamic/{alias}/{rel}" if str(rel) != "." else f"dynamic/{alias}"
            except ValueError:
                pass

        for alias, mount_path in self.dynamic_mounts_rw.items():
            try:
                rel = resolved.relative_to(mount_path)
                return f"dynamic/{alias}/{rel}" if str(rel) != "." else f"dynamic/{alias}"
            except ValueError:
                pass

        # 9. Original-path mounts (reverse: Docker path → original host path)
        for orig_path, docker_mount in self.original_path_mounts_ro.items():
            try:
                rel = resolved.relative_to(docker_mount)
                return f"{orig_path}/{rel}" if str(rel) != "." else orig_path
            except ValueError:
                pass

        for orig_path, docker_mount in self.original_path_mounts_rw.items():
            try:
                rel = resolved.relative_to(docker_mount)
                return f"{orig_path}/{rel}" if str(rel) != "." else orig_path
            except ValueError:
                pass

        # 10. Fallback
        return str(docker_path)

    def validate_path(
        self,
        path: str,
        operation: Literal["read", "write", "edit", "delete", "list", "glob", "grep"],
        allow_directory: bool = False,
    ) -> ValidatedPath:
        """
        Validate and normalize a path for the given operation.

        Args:
            path: User-provided path (relative or /workspace/... style)
            operation: Type of operation (affects read-only check)
            allow_directory: Whether directories are valid (for ls, glob)

        Returns:
            ValidatedPath with normalized path

        Raises:
            PathValidationError: If path is invalid or blocked
        """
        original = path

        # Step 1: Normalize the path
        try:
            normalized = self._normalize_path(path)
        except Exception as e:
            self._log_blocked(path, operation, f"Normalization failed: {e}")
            raise PathValidationError(
                f"Invalid path: {path}",
                path=path,
                reason=f"Path normalization failed: {e}",
            )

        # Step 1.5: SECURITY - Block cross-user and cross-session access FIRST
        # This prevents agents from accessing other users' or other sessions' data
        # Must run before boundary check to give specific error messages
        norm_str = str(normalized)

        # Cross-user access blocking
        if self._session_username and "/users/" in norm_str:
            path_username = self._extract_path_component(norm_str, "/users/")
            if path_username and path_username != self._session_username:
                # Check if this is an allowed exception (e.g., skills)
                is_allowed = (
                    (self.global_skills and self._is_within_boundary(normalized, self.global_skills)) or
                    (self.user_skills and self._is_within_boundary(normalized, self.user_skills))
                )
                if not is_allowed:
                    self._log_blocked(path, operation, f"Cross-user access blocked: {path_username}")
                    raise PathValidationError(
                        f"Access to other users' directories is not allowed: {path}",
                        path=path,
                        reason="CROSS_USER_ACCESS_BLOCKED",
                    )

        # Cross-session access blocking (same user, different session)
        if self._session_username and self._session_id:
            sessions_pattern = f"/users/{self._session_username}/sessions/"
            if sessions_pattern in norm_str:
                path_session_id = self._extract_path_component(norm_str, sessions_pattern)
                if path_session_id and path_session_id != self._session_id:
                    self._log_blocked(path, operation, f"Cross-session access blocked: {path_session_id}")
                    raise PathValidationError(
                        f"Access to other sessions is not allowed: {path}",
                        path=path,
                        reason="CROSS_SESSION_ACCESS_BLOCKED",
                    )

        # Step 2: Check boundary (workspace, skills, or external mount directories)
        # Paths can be within:
        # - Workspace (read-write for most, read-only for some prefixes)
        # - Global skills directory (read-only)
        # - User skills directory (read-only)
        # - External RO mounts (read-only)
        # - External RW mounts (read-write)
        # - Persistent storage (read-write)
        # - Per-user RO mounts (read-only)
        # - Per-user RW mounts (read-write)
        in_workspace = False
        in_global_skills = False
        in_user_skills = False
        in_external_ro = False
        in_external_rw = False
        in_persistent = False
        in_user_ro = False
        in_user_rw = False
        rel_path = ""

        try:
            rel_path = str(normalized.relative_to(self.workspace))
            in_workspace = True
        except ValueError:
            pass

        if not in_workspace and self.global_skills:
            try:
                rel_path = str(normalized.relative_to(self.global_skills))
                in_global_skills = True
            except ValueError:
                pass

        if not in_workspace and not in_global_skills and self.user_skills:
            try:
                rel_path = str(normalized.relative_to(self.user_skills))
                in_user_skills = True
            except ValueError:
                pass

        # Check global external mount boundaries (flattened structure)
        if not in_workspace and not in_global_skills and not in_user_skills:
            # Check global RO mounts
            for mount_name, mount_path in self.global_mounts_ro.items():
                try:
                    rel_path = str(normalized.relative_to(mount_path))
                    in_external_ro = True
                    break
                except ValueError:
                    pass

        if not in_workspace and not in_global_skills and not in_user_skills and not in_external_ro:
            # Check global RW mounts
            for mount_name, mount_path in self.global_mounts_rw.items():
                try:
                    rel_path = str(normalized.relative_to(mount_path))
                    in_external_rw = True
                    break
                except ValueError:
                    pass

        if not in_workspace and not in_global_skills and not in_user_skills and not in_external_ro and not in_external_rw:
            if self.persistent:
                try:
                    rel_path = str(normalized.relative_to(self.persistent))
                    in_persistent = True
                except ValueError:
                    pass

        # Check per-user mount boundaries
        if not in_workspace and not in_global_skills and not in_user_skills and not in_external_ro and not in_external_rw and not in_persistent:
            # Check per-user RO mounts
            for mount_name, mount_path in self.user_mounts_ro.items():
                try:
                    rel_path = str(normalized.relative_to(mount_path))
                    in_user_ro = True
                    break
                except ValueError:
                    pass

        if not in_workspace and not in_global_skills and not in_user_skills and not in_external_ro and not in_external_rw and not in_persistent and not in_user_ro:
            # Check per-user RW mounts
            for mount_name, mount_path in self.user_mounts_rw.items():
                try:
                    rel_path = str(normalized.relative_to(mount_path))
                    in_user_rw = True
                    break
                except ValueError:
                    pass

        # Check dynamic mount boundaries (session-time user-selected mounts)
        in_dynamic_ro = False
        in_dynamic_rw = False

        if not in_workspace and not in_global_skills and not in_user_skills and not in_external_ro and not in_external_rw and not in_persistent and not in_user_ro and not in_user_rw:
            # Check dynamic RO mounts
            for alias, mount_path in self.dynamic_mounts_ro.items():
                try:
                    rel_path = str(normalized.relative_to(mount_path))
                    in_dynamic_ro = True
                    break
                except ValueError:
                    pass

        if not in_workspace and not in_global_skills and not in_user_skills and not in_external_ro and not in_external_rw and not in_persistent and not in_user_ro and not in_user_rw and not in_dynamic_ro:
            # Check dynamic RW mounts
            for alias, mount_path in self.dynamic_mounts_rw.items():
                try:
                    rel_path = str(normalized.relative_to(mount_path))
                    in_dynamic_rw = True
                    break
                except ValueError:
                    pass

        # Check original-path mount boundaries
        # Original-path mounts allow access to paths like /var/log at their original locations
        in_original_ro = False
        in_original_rw = False

        not_in_any_yet = not (
            in_workspace or in_global_skills or in_user_skills or
            in_external_ro or in_external_rw or in_persistent or
            in_user_ro or in_user_rw or in_dynamic_ro or in_dynamic_rw
        )
        if not_in_any_yet:
            # Check original-path RO mounts
            for orig_path, docker_path in self.original_path_mounts_ro.items():
                try:
                    rel_path = str(normalized.relative_to(docker_path))
                    in_original_ro = True
                    break
                except ValueError:
                    pass

        if not_in_any_yet and not in_original_ro:
            # Check original-path RW mounts
            for orig_path, docker_path in self.original_path_mounts_rw.items():
                try:
                    rel_path = str(normalized.relative_to(docker_path))
                    in_original_rw = True
                    break
                except ValueError:
                    pass

        in_any_allowed = (
            in_workspace or in_global_skills or in_user_skills or
            in_external_ro or in_external_rw or in_persistent or
            in_user_ro or in_user_rw or in_dynamic_ro or in_dynamic_rw or
            in_original_ro or in_original_rw
        )

        if not in_any_allowed:
            self._log_blocked(path, operation, "Outside allowed directories")
            raise PathValidationError(
                f"Path outside allowed directories: {path}",
                path=path,
                reason="Path must be within workspace, skills, or external mount directories",
            )

        # Step 3: Check for path traversal attempts
        if ".." in path:
            # Even if normalized path is valid, log the attempt
            logger.warning(f"PATH_VALIDATOR: Traversal attempt in path: {path}")

        # Step 4: Check blocklist (workspace and external mount paths)
        # Security: blocklist applies to all areas to prevent accessing sensitive files
        should_check_blocklist = (
            in_workspace or in_external_ro or in_external_rw or in_persistent or
            in_user_ro or in_user_rw or in_dynamic_ro or in_dynamic_rw or
            in_original_ro or in_original_rw
        )
        if should_check_blocklist:
            # Check exemptions first — safe template files bypass the blocklist
            filename = normalized.name
            is_exempt = any(
                fnmatch.fnmatch(filename, exempt)
                for exempt in self.config.blocklist_exemptions
            )

            if not is_exempt:
                for pattern in self.config.blocklist:
                    if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
                        filename, pattern
                    ):
                        self._log_blocked(
                            path, operation, f"Matches blocklist pattern: {pattern}"
                        )
                        raise PathValidationError(
                            f"Path blocked by policy: {path}",
                            path=path,
                            reason=f"BLOCKLIST: Matches pattern: {pattern}",
                        )

        # Step 5: Check allowlist (if configured, only for workspace paths)
        if in_workspace and self.config.allowlist is not None:
            allowed = False
            for pattern in self.config.allowlist:
                if fnmatch.fnmatch(rel_path, pattern):
                    allowed = True
                    break
            if not allowed:
                self._log_blocked(path, operation, "Not in allowlist")
                raise PathValidationError(
                    f"Path not in allowlist: {path}",
                    path=path,
                    reason="Path does not match any allowed pattern",
                )

        # Step 6: Check if read-only
        # Read-only areas:
        # - Skills directories (global and user) are always read-only
        # - External RO mounts are always read-only
        # - Per-user RO mounts are always read-only
        # - Dynamic RO mounts are always read-only
        # - Original-path RO mounts are always read-only
        # - Workspace paths may have readonly_prefixes
        is_readonly = in_global_skills or in_user_skills or in_external_ro or in_user_ro or in_dynamic_ro or in_original_ro

        if in_workspace and not is_readonly:
            is_readonly = any(
                rel_path.startswith(ro_prefix.rstrip("/"))
                for ro_prefix in self.config.readonly_prefixes
            )

        if is_readonly and operation in ("write", "edit", "delete"):
            # Provide helpful error message for external RO mounts
            if in_external_ro or in_user_ro or in_dynamic_ro or in_original_ro:
                self._log_blocked(path, operation, "Read-only external mount")
                raise PathValidationError(
                    f"Cannot {operation} read-only mount: {path}",
                    path=path,
                    reason="Mount is read-only (external mount, per-user ro, dynamic ro, or original-path ro)",
                )
            else:
                self._log_blocked(path, operation, "Read-only path")
                raise PathValidationError(
                    f"Cannot {operation} read-only path: {path}",
                    path=path,
                    reason="Path is read-only",
                )

        # Log success
        self._log_allowed(original, normalized, operation)

        return ValidatedPath(
            original=original,
            normalized=normalized,
            is_readonly=is_readonly,
        )

    def _normalize_path(self, path: str) -> Path:
        """
        Normalize agent-provided path to REAL Docker filesystem path.

        The agent thinks it's working with bwrap paths:
        - /workspace/foo.txt -> becomes /users/greg/sessions/xxx/workspace/foo.txt
        - ./foo.txt -> becomes /users/greg/sessions/xxx/workspace/foo.txt
        - foo.txt -> becomes /users/greg/sessions/xxx/workspace/foo.txt

        External mount paths are translated as:
        - /workspace/external/ro/{name}/file -> /mounts/ro/{name}/file
        - /workspace/external/rw/{name}/file -> /mounts/rw/{name}/file
        - /workspace/persistent/file -> /users/{username}/ag3ntum/persistent/file
        - ./external/ro/{name}/file -> same translations
        - ./persistent/file -> same translation

        This translation is critical because the Python file tools run OUTSIDE bwrap
        and need the real Docker filesystem paths.
        """
        p = PurePosixPath(path)
        path_str = str(p)

        # First, normalize relative paths that reference external mounts or persistent
        # NOTE: Dynamic mounts are now at workspace root as symlinks (e.g., ./logs/ instead of ./dynamic/logs/)
        # and are resolved automatically via standard workspace path handling below.
        if not p.is_absolute():
            # Check if it's a relative external path like ./external/ro/... or ./persistent/...
            if path_str.startswith("./external/") or path_str.startswith("external/"):
                # Convert to absolute bwrap-style path
                path_str = "/workspace/" + path_str.lstrip("./")
                p = PurePosixPath(path_str)
            # Handle persistent paths - with or without trailing slash
            # PurePosixPath normalizes "./persistent/" to "persistent" (strips ./ and trailing /)
            # So we need to check for: ./persistent, ./persistent/, persistent, persistent/
            elif (
                path_str == "./persistent" or path_str == "persistent" or
                path_str.startswith("./persistent/") or path_str.startswith("persistent/")
            ):
                # Convert to absolute bwrap-style path
                path_str = "/workspace/" + path_str.lstrip("./")
                p = PurePosixPath(path_str)

        # Handle absolute /persistent/* path (bwrap sandbox internal path)
        # /persistent/* -> /users/{username}/ag3ntum/persistent/*
        # This is the path format agents see inside bwrap sandbox
        if path_str.startswith("/persistent/") or path_str == "/persistent":
            relative = path_str[len("/persistent/"):] if path_str != "/persistent" else ""
            return self._resolve_persistent_path(path_str, relative, path)

        # Handle agent paths that reference persistent storage (at workspace root, not under external/)
        # /workspace/persistent/* -> /users/{username}/ag3ntum/persistent/*
        if path_str.startswith("/workspace/persistent/") or path_str == "/workspace/persistent":
            if self.persistent:
                relative = path_str[len("/workspace/persistent/"):] if path_str != "/workspace/persistent" else ""
                return self._resolve_persistent_path(path_str, relative, path)
            # Persistent not configured, treat as workspace path
            relative_to_workspace = path_str[len("/workspace"):].lstrip("/")
            return (self.workspace / relative_to_workspace).resolve()

        # Handle agent paths that reference external mounts
        if path_str.startswith("/workspace/external/"):
            # Extract the part after /workspace/external/
            external_part = path_str[len("/workspace/external/"):]

            # Route to correct external mount (flattened structure: /mounts/{name})
            if external_part.startswith("ro/"):
                # Read-only external mount: /workspace/external/ro/{name}/* -> /mounts/{name}/*
                relative = external_part[3:]  # Remove "ro/"

                # Extract mount name (first path component)
                if "/" in relative:
                    mount_name, mount_relative = relative.split("/", 1)
                else:
                    mount_name = relative
                    mount_relative = ""

                # Check global RO mounts first
                if mount_name in self.global_mounts_ro:
                    mount_path = self.global_mounts_ro[mount_name]
                    if mount_relative:
                        resolved = (mount_path / mount_relative).resolve()
                    else:
                        resolved = mount_path.resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, mount_path):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes global-ro mount boundary",
                        )
                    return resolved
                # Fallback to user mounts for backward compatibility
                elif mount_name in self.user_mounts_ro:
                    mount_path = self.user_mounts_ro[mount_name]
                    if mount_relative:
                        resolved = (mount_path / mount_relative).resolve()
                    else:
                        resolved = mount_path.resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, mount_path):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes user-ro mount boundary",
                        )
                    return resolved
                else:
                    # Mount not found, treat as workspace path (will likely fail boundary check)
                    relative_to_workspace = path_str[len("/workspace"):].lstrip("/")
                    resolved = (self.workspace / relative_to_workspace).resolve()
                    return resolved

            elif external_part.startswith("rw/"):
                # Read-write external mount: /workspace/external/rw/{name}/* -> /mounts/{name}/*
                relative = external_part[3:]  # Remove "rw/"

                # Extract mount name (first path component)
                if "/" in relative:
                    mount_name, mount_relative = relative.split("/", 1)
                else:
                    mount_name = relative
                    mount_relative = ""

                # Check global RW mounts first
                if mount_name in self.global_mounts_rw:
                    mount_path = self.global_mounts_rw[mount_name]
                    if mount_relative:
                        resolved = (mount_path / mount_relative).resolve()
                    else:
                        resolved = mount_path.resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, mount_path):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes global-rw mount boundary",
                        )
                    return resolved
                # Fallback to user mounts for backward compatibility
                elif mount_name in self.user_mounts_rw:
                    mount_path = self.user_mounts_rw[mount_name]
                    if mount_relative:
                        resolved = (mount_path / mount_relative).resolve()
                    else:
                        resolved = mount_path.resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, mount_path):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes user-rw mount boundary",
                        )
                    return resolved
                else:
                    # Mount not found, treat as workspace path
                    relative_to_workspace = path_str[len("/workspace"):].lstrip("/")
                    resolved = (self.workspace / relative_to_workspace).resolve()
                    return resolved

            elif external_part.startswith("persistent/") or external_part == "persistent":
                # DEPRECATED: /workspace/external/persistent/* is deprecated
                # Use /workspace/persistent/* instead (persistent is now at workspace root)
                logger.warning(
                    f"Deprecated path: {path}. Use ./persistent/ instead of ./external/persistent/"
                )
                if self.persistent:
                    relative = external_part[11:] if external_part != "persistent" else ""  # Remove "persistent/"
                    return self._resolve_persistent_path(path_str, relative, path)
                # Persistent not configured, treat as workspace path
                relative_to_workspace = path_str[len("/workspace"):].lstrip("/")
                return (self.workspace / relative_to_workspace).resolve()

            elif external_part.startswith("user-ro/"):
                # Per-user read-only mount: /workspace/external/user-ro/{name}/* -> real path/*
                remaining = external_part[8:]  # Remove "user-ro/"
                # Extract mount name (first path component)
                if "/" in remaining:
                    mount_name, relative = remaining.split("/", 1)
                else:
                    mount_name = remaining
                    relative = ""

                if mount_name in self.user_mounts_ro:
                    mount_path = self.user_mounts_ro[mount_name]
                    if relative:
                        resolved = (mount_path / relative).resolve()
                    else:
                        resolved = mount_path.resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, mount_path):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes user-ro mount boundary",
                        )
                    return resolved
                else:
                    # Mount not configured, treat as workspace path
                    relative_to_workspace = path_str[len("/workspace"):].lstrip("/")
                    resolved = (self.workspace / relative_to_workspace).resolve()
                    return resolved

            elif external_part.startswith("user-rw/"):
                # Per-user read-write mount: /workspace/external/user-rw/{name}/* -> real path/*
                remaining = external_part[8:]  # Remove "user-rw/"
                # Extract mount name (first path component)
                if "/" in remaining:
                    mount_name, relative = remaining.split("/", 1)
                else:
                    mount_name = remaining
                    relative = ""

                if mount_name in self.user_mounts_rw:
                    mount_path = self.user_mounts_rw[mount_name]
                    if relative:
                        resolved = (mount_path / relative).resolve()
                    else:
                        resolved = mount_path.resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, mount_path):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes user-rw mount boundary",
                        )
                    return resolved
                else:
                    # Mount not configured, treat as workspace path
                    relative_to_workspace = path_str[len("/workspace"):].lstrip("/")
                    resolved = (self.workspace / relative_to_workspace).resolve()
                    return resolved

            # Unrecognized external path - fall through to workspace handling

        # NOTE: Dynamic mounts are now symlinked at workspace root (e.g., workspace/{alias})
        # instead of workspace/dynamic/{alias}. The symlink resolution in workspace path
        # handling below automatically resolves to /mounts/dynamic/{base}/{subpath}.
        # Validation then checks if the resolved path is within allowed dynamic_mounts_*.

        # Handle standard workspace paths
        if path_str.startswith("/workspace"):
            # Agent provided bwrap-style path: /workspace/foo -> workspace/foo
            relative_to_workspace = path_str[len("/workspace"):].lstrip("/")
            resolved = (self.workspace / relative_to_workspace).resolve()
            # Security: verify resolved path stays within workspace boundary
            if not self._is_within_boundary(resolved, self.workspace):
                raise PathValidationError(
                    f"Path traversal detected: {path}",
                    path=path,
                    reason="PATH_TRAVERSAL: Resolved path escapes workspace boundary",
                )
        elif not p.is_absolute():
            # Relative path: ./foo or foo -> workspace/foo
            resolved = (self.workspace / p).resolve()
        else:
            # Absolute path NOT starting with /workspace
            # Check if this is an original-path mount (e.g., /var/log)
            # Original-path mounts allow accessing paths at their original locations
            # Translate: /var/log -> /mounts/paths/_var_log
            original_mount = self._find_original_path_mount(path_str)
            if original_mount:
                orig_path, docker_path, is_ro = original_mount
                if path_str == orig_path:
                    resolved = docker_path.resolve()
                else:
                    # Path is under the mount (e.g., /var/log/syslog)
                    relative = path_str[len(orig_path):].lstrip("/")
                    resolved = (docker_path / relative).resolve()
                # Security: verify resolved path stays within mount boundary
                if not self._is_within_boundary(resolved, docker_path):
                    raise PathValidationError(
                        f"Path traversal detected: {path}",
                        path=path,
                        reason="PATH_TRAVERSAL: Resolved path escapes original-path mount boundary",
                    )
            else:
                # This is an escape attempt (like /etc/passwd)
                resolved = Path(p).resolve()

        return resolved

    def validate_no_symlink_escape(
        self, path: Path, boundary: Path, check_intermediate: bool = True
    ) -> Path:
        """
        Validate that path (including symlinks) doesn't escape boundary.

        This prevents TOCTOU attacks where:
        1. Attacker creates: /workspace/external/rw/projects/link -> /etc/passwd
        2. Validation passes (link exists in allowed area)
        3. Read follows symlink to /etc/passwd

        Args:
            path: The path to validate
            boundary: The boundary the resolved path must stay within
            check_intermediate: If True, check each intermediate symlink

        Returns:
            The fully resolved path

        Raises:
            PathValidationError: If path or any symlink escapes boundary
        """
        # Resolve the path fully (follows all symlinks)
        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise PathValidationError(
                f"Cannot resolve path: {path} - {e}",
                path=str(path),
                reason="PATH_RESOLUTION_ERROR",
            )

        # Check each intermediate component for symlink escape
        if check_intermediate and path.exists():
            current = Path("/")
            for part in path.parts[1:]:  # Skip root
                current = current / part
                if current.exists() and current.is_symlink():
                    try:
                        link_target = current.resolve()
                        link_target.relative_to(boundary)
                    except ValueError:
                        logger.warning(
                            f"PATH_VALIDATOR: Symlink escape detected: "
                            f"{current} -> {link_target} (outside {boundary})"
                        )
                        raise PathValidationError(
                            f"Symlink escape detected: {current}",
                            path=str(path),
                            reason="SYMLINK_ESCAPE",
                        )
                    except OSError:
                        # Broken symlink or permission error - allow to continue
                        pass

        # Final resolved path must be within boundary
        try:
            resolved.relative_to(boundary)
        except ValueError:
            raise PathValidationError(
                f"Path resolves outside boundary: {path} -> {resolved}",
                path=str(path),
                reason="PATH_ESCAPE",
            )

        return resolved

    def _log_allowed(self, original: str, normalized: Path, operation: str) -> None:
        """Log allowed path access."""
        if self.config.log_all_access:
            logger.info(
                f"PATH_VALIDATOR: ALLOWED {operation.upper()} "
                f"'{original}' -> '{normalized}'"
            )

    def _extract_path_component(self, path_str: str, pattern: str) -> str | None:
        """
        Extract the first path component after a pattern.

        Args:
            path_str: The path string to search
            pattern: The pattern to find (e.g., "/users/")

        Returns:
            The first component after the pattern, or None if not found
        """
        idx = path_str.find(pattern)
        if idx < 0:
            return None
        remaining = path_str[idx + len(pattern):]
        return remaining.split("/")[0] if remaining else None

    def _resolve_persistent_path(self, path: str, relative: str, original_path: str) -> Path:
        """
        Resolve a path within persistent storage with boundary validation.

        Args:
            path: The full path being resolved (for error messages)
            relative: The relative path within persistent storage
            original_path: The original user-provided path (for error messages)

        Returns:
            Resolved Path within persistent storage

        Raises:
            PathValidationError: If path escapes boundary or persistent not configured
        """
        if not self.persistent:
            raise PathValidationError(
                f"Persistent storage not configured: {path}",
                path=original_path,
                reason="Persistent storage path not available",
            )

        resolved = (self.persistent / relative).resolve() if relative else self.persistent.resolve()

        if not self._is_within_boundary(resolved, self.persistent):
            raise PathValidationError(
                f"Path traversal detected: {original_path}",
                path=original_path,
                reason="PATH_TRAVERSAL: Resolved path escapes persistent storage boundary",
            )
        return resolved

    def _find_original_path_mount(
        self, path: str
    ) -> tuple[str, Path, bool] | None:
        """
        Find the original-path mount that contains the given path.

        Args:
            path: An absolute path (e.g., "/var/log" or "/var/log/syslog")

        Returns:
            Tuple of (original_path, docker_path, is_readonly) if found, else None
        """
        best_match: tuple[str, Path, bool] | None = None
        best_len = 0

        # Check RO mounts
        for orig_path, docker_path in self.original_path_mounts_ro.items():
            if path == orig_path or path.startswith(orig_path + "/"):
                if len(orig_path) > best_len:
                    best_match = (orig_path, docker_path, True)
                    best_len = len(orig_path)

        # Check RW mounts
        for orig_path, docker_path in self.original_path_mounts_rw.items():
            if path == orig_path or path.startswith(orig_path + "/"):
                if len(orig_path) > best_len:
                    best_match = (orig_path, docker_path, False)
                    best_len = len(orig_path)

        return best_match

    def get_sandbox_root_entries(self) -> list[tuple[str, str, str]]:
        """
        Synthesize a virtual directory listing of the sandbox root (/).

        Returns entries matching what the agent would see inside bwrap,
        based on configured mounts. This avoids exposing Docker container
        internals while giving the agent a useful view of available paths.

        Returns:
            List of (display_path, access_mode, description) tuples.
            display_path: The path as the agent should see it (e.g., "/workspace")
            access_mode: "rw" or "ro"
            description: Human-readable description
        """
        entries: list[tuple[str, str, str]] = []

        # Core paths (always present)
        entries.append(("/workspace", "rw", "Session workspace (working directory)"))

        if self.persistent:
            entries.append(("/persistent", "rw", "Persistent storage (cross-session)"))

        # User environment
        # Venv is always configured in permissions.yaml session_mounts
        entries.append(("/venv", "ro", "Python virtual environment"))

        if self.global_skills:
            entries.append(("/skills", "ro", "Global skills"))
        if self.user_skills:
            entries.append(("/user-skills", "ro", "User skills"))

        # Original-path mounts (e.g., /var/log)
        for orig_path in sorted(self.original_path_mounts_ro.keys()):
            entries.append((orig_path, "ro", f"Mounted from host (read-only)"))
        for orig_path in sorted(self.original_path_mounts_rw.keys()):
            entries.append((orig_path, "rw", f"Mounted from host (read-write)"))

        return entries

    def find_virtual_children(self, parent_path: str) -> list[tuple[str, str, str]] | None:
        """
        Find virtual directory children for a path that is a parent of configured mounts.

        For example, if /var/log is a configured original-path mount,
        calling this with "/var" returns [("log", "ro", "Mounted from host")].

        Args:
            parent_path: Absolute path to check (e.g., "/var")

        Returns:
            List of (child_name, access_mode, description) if any mounts exist
            under this path. None if no mounts are found under this path.
        """
        parent = parent_path.rstrip("/") + "/"
        children: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        for orig_path in self.original_path_mounts_ro:
            if orig_path.startswith(parent):
                # Extract the immediate child component
                remainder = orig_path[len(parent):]
                child_name = remainder.split("/")[0]
                if child_name and child_name not in seen:
                    seen.add(child_name)
                    children.append((child_name, "ro", f"Contains mount: {orig_path}"))

        for orig_path in self.original_path_mounts_rw:
            if orig_path.startswith(parent):
                remainder = orig_path[len(parent):]
                child_name = remainder.split("/")[0]
                if child_name and child_name not in seen:
                    seen.add(child_name)
                    children.append((child_name, "rw", f"Contains mount: {orig_path}"))

        return children if children else None

    def _is_within_boundary(self, path: Path, boundary: Path) -> bool:
        """
        Check if a resolved path is within the given boundary.

        This prevents path traversal attacks where .. components
        could escape the intended directory boundary.

        Args:
            path: The resolved path to check
            boundary: The boundary directory path must stay within

        Returns:
            True if path is within boundary, False otherwise
        """
        try:
            # Resolve both paths to handle any symlinks
            resolved_path = path.resolve()
            resolved_boundary = boundary.resolve()
            # Check if path is relative to boundary
            resolved_path.relative_to(resolved_boundary)
            return True
        except ValueError:
            return False

    def _log_blocked(self, path: str, operation: str, reason: str) -> None:
        """Log blocked path access."""
        logger.warning(
            f"PATH_VALIDATOR: BLOCKED {operation.upper()} " f"'{path}' - {reason}"
        )


# =============================================================================
# Session-Scoped Validator Management
# =============================================================================

# Session-scoped validators (NOT singleton - each session has its own)
_session_validators: dict[str, Ag3ntumPathValidator] = {}

# Session-scoped linux UIDs for file ownership (sandbox user UID per session)
_session_linux_uids: dict[str, int] = {}


def set_session_linux_uid(session_id: str, linux_uid: int) -> None:
    """Store the linux_uid (sandbox user UID) for a session."""
    _session_linux_uids[session_id] = linux_uid
    logger.debug(f"PATH_VALIDATOR: Set linux_uid={linux_uid} for session {session_id}")


def get_session_linux_uid(session_id: str) -> int | None:
    """Get the linux_uid for a session, or None if not set."""
    return _session_linux_uids.get(session_id)


def get_path_validator(session_id: str) -> Ag3ntumPathValidator:
    """
    Get the path validator for a session.

    Args:
        session_id: The session ID

    Returns:
        The configured Ag3ntumPathValidator for this session

    Raises:
        RuntimeError: If validator not configured for this session
    """
    if session_id not in _session_validators:
        raise RuntimeError(
            f"PathValidator not configured for session {session_id}. "
            "Call configure_path_validator() first."
        )
    return _session_validators[session_id]


def configure_path_validator(
    session_id: str,
    workspace_path: Path,
    username: str | None = None,
    skills_path: Path | None = None,
    global_skills_path: Path | None = None,
    user_skills_path: Path | None = None,
    global_mounts_ro: dict[str, Path] | None = None,
    global_mounts_rw: dict[str, Path] | None = None,
    persistent_path: Path | None = None,
    user_mounts_ro: dict[str, Path] | None = None,
    user_mounts_rw: dict[str, Path] | None = None,
    dynamic_mounts_ro: dict[str, Path] | None = None,
    dynamic_mounts_rw: dict[str, Path] | None = None,
    original_path_mounts_ro: dict[str, Path] | None = None,
    original_path_mounts_rw: dict[str, Path] | None = None,
    blocklist: list[str] | None = None,
    readonly_prefixes: list[str] | None = None,
) -> Ag3ntumPathValidator:
    """
    Configure and return path validator for a session.

    This function also configures the SandboxPathResolver for the session,
    ensuring both components are available for path handling.

    Args:
        session_id: The session ID
        workspace_path: REAL Docker filesystem path to session workspace
        username: Username for this session (extracted from path if not provided)
        skills_path: Deprecated, use global_skills_path/user_skills_path
        global_skills_path: Path to global skills directory (read-only)
        user_skills_path: Path to user skills directory (read-only)
        global_mounts_ro: Global read-only mounts {name: container_path} (flattened)
        global_mounts_rw: Global read-write mounts {name: container_path} (flattened)
        persistent_path: Path to user's persistent storage
        user_mounts_ro: Per-user read-only mounts {name: container_path}
        user_mounts_rw: Per-user read-write mounts {name: container_path}
        dynamic_mounts_ro: Dynamic read-only mounts {alias: container_path}
        dynamic_mounts_rw: Dynamic read-write mounts {alias: container_path}
        original_path_mounts_ro: Original-path read-only mounts {orig_path: docker_path}
        original_path_mounts_rw: Original-path read-write mounts {orig_path: docker_path}
        blocklist: Optional list of blocked patterns (defaults to common sensitive files)
        readonly_prefixes: Optional list of read-only path prefixes

    Returns:
        The configured Ag3ntumPathValidator
    """
    # Extract username from workspace path if not provided
    # Path format: /users/{username}/sessions/{session_id}/workspace
    if username is None:
        workspace_str = str(workspace_path)
        if workspace_str.startswith("/users/"):
            parts = workspace_str.split("/")
            if len(parts) >= 3:
                username = parts[2]
        if username is None:
            logger.warning(
                f"Could not extract username from workspace path: {workspace_path}. "
                "SandboxPathResolver will not be configured."
            )

    config = PathValidatorConfig(
        workspace_path=workspace_path,
        skills_path=skills_path,
        global_skills_path=global_skills_path,
        user_skills_path=user_skills_path,
        global_mounts_ro=global_mounts_ro or {},
        global_mounts_rw=global_mounts_rw or {},
        persistent_path=persistent_path,
        user_mounts_ro=user_mounts_ro or {},
        user_mounts_rw=user_mounts_rw or {},
        dynamic_mounts_ro=dynamic_mounts_ro or {},
        dynamic_mounts_rw=dynamic_mounts_rw or {},
        original_path_mounts_ro=original_path_mounts_ro or {},
        original_path_mounts_rw=original_path_mounts_rw or {},
        blocklist=blocklist or DEFAULT_BLOCKLIST.copy(),
        readonly_prefixes=readonly_prefixes or DEFAULT_READONLY_PREFIXES.copy(),
    )
    validator = Ag3ntumPathValidator(config)
    _session_validators[session_id] = validator

    # Also configure SandboxPathResolver for this session
    if username:
        try:
            configure_sandbox_path_resolver(
                session_id=session_id,
                username=username,
                workspace_docker=str(workspace_path),
                global_mounts_ro={k: str(v) for k, v in (global_mounts_ro or {}).items()},
                global_mounts_rw={k: str(v) for k, v in (global_mounts_rw or {}).items()},
                user_mounts_ro={k: str(v) for k, v in (user_mounts_ro or {}).items()},
                user_mounts_rw={k: str(v) for k, v in (user_mounts_rw or {}).items()},
            )
        except Exception as e:
            logger.warning(f"Failed to configure SandboxPathResolver: {e}")

    # Log mount info if any configured
    global_ro_count = len(global_mounts_ro) if global_mounts_ro else 0
    global_rw_count = len(global_mounts_rw) if global_mounts_rw else 0
    user_ro_count = len(user_mounts_ro) if user_mounts_ro else 0
    user_rw_count = len(user_mounts_rw) if user_mounts_rw else 0
    orig_ro_count = len(original_path_mounts_ro) if original_path_mounts_ro else 0
    orig_rw_count = len(original_path_mounts_rw) if original_path_mounts_rw else 0

    logger.info(
        f"PATH_VALIDATOR: Configured for session {session_id} "
        f"with workspace={workspace_path}, username={username}, "
        f"global_mounts={global_ro_count} RO/{global_rw_count} RW, "
        f"persistent={persistent_path}, "
        f"user_mounts={user_ro_count} RO/{user_rw_count} RW, "
        f"original_paths={orig_ro_count} RO/{orig_rw_count} RW"
    )
    return validator


def cleanup_path_validator(session_id: str) -> None:
    """
    Remove path validator when session ends.

    This also cleans up the associated SandboxPathResolver.

    Args:
        session_id: The session ID to clean up
    """
    if session_id in _session_validators:
        del _session_validators[session_id]
        logger.info(f"PATH_VALIDATOR: Cleaned up validator for session {session_id}")

    # Also cleanup session linux_uid
    _session_linux_uids.pop(session_id, None)

    # Also cleanup SandboxPathResolver
    cleanup_sandbox_path_resolver(session_id)


def has_path_validator(session_id: str) -> bool:
    """
    Check if a path validator is configured for a session.

    Args:
        session_id: The session ID to check

    Returns:
        True if validator is configured, False otherwise
    """
    return session_id in _session_validators


# =============================================================================
# Sandbox Path Resolution Utilities
# =============================================================================

def get_resolver_for_session(session_id: str) -> Optional[SandboxPathResolver]:
    """
    Get the SandboxPathResolver for a session if available.

    Args:
        session_id: The session ID

    Returns:
        SandboxPathResolver if configured, None otherwise
    """
    if has_sandbox_path_resolver(session_id):
        return get_sandbox_path_resolver(session_id)
    return None


def sandbox_to_docker_path(session_id: str, sandbox_path: str) -> str:
    """
    Convert a sandbox path to Docker path for a session.

    This is a convenience function for converting agent-provided paths
    (in sandbox format) to Docker filesystem paths.

    Args:
        session_id: The session ID
        sandbox_path: Path in sandbox format (e.g., /workspace/file.txt)

    Returns:
        Docker filesystem path

    Raises:
        RuntimeError: If resolver not configured for session
        PathResolutionError: If path cannot be resolved
    """
    resolver = get_sandbox_path_resolver(session_id)
    return resolver.sandbox_to_docker(sandbox_path)


def docker_to_sandbox_path(session_id: str, docker_path: str) -> str:
    """
    Convert a Docker path to sandbox path for a session.

    This is useful for translating error messages or paths from Docker
    processes back to the canonical sandbox format.

    Args:
        session_id: The session ID
        docker_path: Path in Docker format

    Returns:
        Sandbox path

    Raises:
        RuntimeError: If resolver not configured for session
        PathResolutionError: If path cannot be resolved
    """
    resolver = get_sandbox_path_resolver(session_id)
    return resolver.docker_to_sandbox(docker_path)


def translate_error_message(session_id: str, error_message: str) -> str:
    """
    Translate Docker paths in an error message to sandbox paths.

    This makes error messages more user-friendly by showing paths
    in the format the agent understands.

    Args:
        session_id: The session ID
        error_message: Error message that may contain Docker paths

    Returns:
        Error message with Docker paths replaced by sandbox paths
    """
    resolver = get_resolver_for_session(session_id)
    if resolver:
        return resolver.translate_error_paths(error_message)
    return error_message


def normalize_sandbox_path(session_id: str, path: str) -> str:
    """
    Normalize any path to canonical sandbox format.

    Args:
        session_id: The session ID
        path: Input path (can be relative or absolute)

    Returns:
        Canonical sandbox path (e.g., /workspace/file.txt)

    Raises:
        RuntimeError: If resolver not configured for session
        PathResolutionError: If path is invalid
    """
    resolver = get_sandbox_path_resolver(session_id)
    return resolver.normalize(path)
