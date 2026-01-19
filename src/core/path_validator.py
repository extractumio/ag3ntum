"""
Unified path validation for all Ag3ntum tools.

Single source of truth for path normalization, validation, and logging.
All Ag3ntum file tools use this validator before performing operations.

CRITICAL ARCHITECTURE NOTE:
This validator runs in the main Python process, which sees the REAL Docker
filesystem paths (e.g., /users/greg/sessions/xxx/workspace), NOT bwrap mount
paths (/workspace). The agent thinks it's working with /workspace, but we
must translate to real paths for Python file operations.

Bwrap paths (/workspace) are only visible inside subprocesses launched via
Ag3ntumBash. All other Ag3ntum tools (Ag3ntumRead, Ag3ntumWrite, etc.) run
in the main process and need this validator for security.
"""
import fnmatch
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
    # EXTERNAL MOUNT PATHS - Host folders mounted via deploy.sh
    # =========================================================================
    # These are Docker container paths (not bwrap paths).
    # Agent sees: /workspace/external/ro/* -> Real path: /mounts/ro/*
    # Agent sees: /workspace/external/rw/* -> Real path: /mounts/rw/*
    # Agent sees: /workspace/external/persistent/* -> Real path: /users/{username}/ag3ntum/persistent/*

    external_ro_base: Path | None = Field(
        default=None,
        description="Base path for read-only external mounts (/mounts/ro)"
    )
    external_rw_base: Path | None = Field(
        default=None,
        description="Base path for read-write external mounts (/mounts/rw)"
    )
    persistent_path: Path | None = Field(
        default=None,
        description="Path to user's persistent storage (/users/{username}/ag3ntum/persistent)"
    )

    # =========================================================================
    # PER-USER MOUNT PATHS - User-specific external mounts
    # =========================================================================
    # These are configured via external-mounts.yaml per_user section.
    # Agent sees: /workspace/external/user-ro/{name}/* -> Real path: {host_path}/*
    # Agent sees: /workspace/external/user-rw/{name}/* -> Real path: {host_path}/*

    user_mounts_ro: dict[str, Path] = Field(
        default_factory=dict,
        description="Per-user read-only mounts: {name: real_path}"
    )
    user_mounts_rw: dict[str, Path] = Field(
        default_factory=dict,
        description="Per-user read-write mounts: {name: real_path}"
    )

    log_all_access: bool = Field(
        default=True, description="Log all path access attempts"
    )
    blocklist: list[str] = Field(
        default_factory=lambda: [
            "*.env", "*.key", ".git/**", "__pycache__/**", "*.pyc",
            ".secrets/**", "*.pem", "*.p12", "*.pfx",
            # Security: block common sensitive patterns in external mounts
            "**/node_modules/**",  # Prevent massive directory traversal
        ],
        description="Glob patterns to block even within workspace",
    )
    allowlist: list[str] | None = Field(
        default=None, description="If set, only these patterns are allowed"
    )
    # Skills are read-only (path prefixes relative to workspace)
    readonly_prefixes: list[str] = Field(
        default_factory=lambda: ["skills/", "external/ro/", "external/user-ro/"],
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

        # External mount paths
        # Agent sees: /workspace/external/ro/* -> Real path: /mounts/ro/*
        self.external_ro = config.external_ro_base.resolve() if config.external_ro_base else None
        # Agent sees: /workspace/external/rw/* -> Real path: /mounts/rw/*
        self.external_rw = config.external_rw_base.resolve() if config.external_rw_base else None
        # Agent sees: /workspace/external/persistent/* -> Real path: /users/{username}/ag3ntum/persistent/*
        self.persistent = config.persistent_path.resolve() if config.persistent_path else None

        # Per-user mount paths (resolved at session start)
        # Agent sees: /workspace/external/user-ro/{name}/* -> Real path from config
        # Agent sees: /workspace/external/user-rw/{name}/* -> Real path from config
        self.user_mounts_ro: dict[str, Path] = {
            name: path.resolve() for name, path in config.user_mounts_ro.items()
        }
        self.user_mounts_rw: dict[str, Path] = {
            name: path.resolve() for name, path in config.user_mounts_rw.items()
        }

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

        # Check external mount boundaries
        if not in_workspace and not in_global_skills and not in_user_skills:
            if self.external_ro:
                try:
                    rel_path = str(normalized.relative_to(self.external_ro))
                    in_external_ro = True
                except ValueError:
                    pass

        if not in_workspace and not in_global_skills and not in_user_skills and not in_external_ro:
            if self.external_rw:
                try:
                    rel_path = str(normalized.relative_to(self.external_rw))
                    in_external_rw = True
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

        in_any_allowed = (
            in_workspace or in_global_skills or in_user_skills or
            in_external_ro or in_external_rw or in_persistent or
            in_user_ro or in_user_rw
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
        should_check_blocklist = in_workspace or in_external_ro or in_external_rw or in_persistent or in_user_ro or in_user_rw
        if should_check_blocklist:
            for pattern in self.config.blocklist:
                if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
                    normalized.name, pattern
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
        # - Workspace paths may have readonly_prefixes
        is_readonly = in_global_skills or in_user_skills or in_external_ro or in_user_ro

        if in_workspace and not is_readonly:
            is_readonly = any(
                rel_path.startswith(ro_prefix.rstrip("/"))
                for ro_prefix in self.config.readonly_prefixes
            )

        if is_readonly and operation in ("write", "edit", "delete"):
            # Provide helpful error message for external RO mounts
            if in_external_ro or in_user_ro:
                self._log_blocked(path, operation, "Read-only external mount")
                raise PathValidationError(
                    f"Cannot {operation} read-only external mount: {path}",
                    path=path,
                    reason="External mount is read-only (mounted with --mount-ro or per-user ro)",
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
        - /workspace/external/persistent/file -> /users/{username}/ag3ntum/persistent/file
        - ./external/ro/{name}/file -> same translations

        This translation is critical because the Python file tools run OUTSIDE bwrap
        and need the real Docker filesystem paths.
        """
        p = PurePosixPath(path)
        path_str = str(p)

        # First, normalize relative paths that reference external mounts
        if not p.is_absolute():
            # Check if it's a relative external path like ./external/ro/...
            if path_str.startswith("./external/") or path_str.startswith("external/"):
                # Convert to absolute bwrap-style path
                path_str = "/workspace/" + path_str.lstrip("./")
                p = PurePosixPath(path_str)

        # Handle agent paths that reference external mounts
        if path_str.startswith("/workspace/external/"):
            # Extract the part after /workspace/external/
            external_part = path_str[len("/workspace/external/"):]

            # Route to correct external mount
            if external_part.startswith("ro/"):
                # Read-only external mount: /workspace/external/ro/* -> /mounts/ro/*
                relative = external_part[3:]  # Remove "ro/"

                if self.external_ro:
                    # Base path exists (Docker mode) - use it
                    resolved = (self.external_ro / relative).resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, self.external_ro):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes external ro mount boundary",
                        )
                    return resolved
                else:
                    # No base path - try to find mount in user_mounts_ro (non-Docker mode)
                    # Path format: {mount_name}/... (e.g., "greg_downloads/file.txt")
                    if "/" in relative:
                        mount_name, mount_relative = relative.split("/", 1)
                    else:
                        mount_name = relative
                        mount_relative = ""

                    if mount_name in self.user_mounts_ro:
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
                # Read-write external mount: /workspace/external/rw/* -> /mounts/rw/*
                relative = external_part[3:]  # Remove "rw/"

                if self.external_rw:
                    # Base path exists (Docker mode) - use it
                    resolved = (self.external_rw / relative).resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, self.external_rw):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes external rw mount boundary",
                        )
                    return resolved
                else:
                    # No base path - try to find mount in user_mounts_rw (non-Docker mode)
                    if "/" in relative:
                        mount_name, mount_relative = relative.split("/", 1)
                    else:
                        mount_name = relative
                        mount_relative = ""

                    if mount_name in self.user_mounts_rw:
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

            elif external_part.startswith("persistent/"):
                # Persistent storage: /workspace/external/persistent/* -> /users/{username}/ag3ntum/persistent/*
                if self.persistent:
                    relative = external_part[11:]  # Remove "persistent/"
                    resolved = (self.persistent / relative).resolve()
                    # Security: verify resolved path stays within boundary
                    if not self._is_within_boundary(resolved, self.persistent):
                        raise PathValidationError(
                            f"Path traversal detected: {path}",
                            path=path,
                            reason="PATH_TRAVERSAL: Resolved path escapes persistent storage boundary",
                        )
                    return resolved
                else:
                    # Persistent not configured, treat as workspace path
                    relative_to_workspace = path_str[len("/workspace"):].lstrip("/")
                    resolved = (self.workspace / relative_to_workspace).resolve()
                    return resolved

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
    skills_path: Path | None = None,
    global_skills_path: Path | None = None,
    user_skills_path: Path | None = None,
    external_ro_base: Path | None = None,
    external_rw_base: Path | None = None,
    persistent_path: Path | None = None,
    user_mounts_ro: dict[str, Path] | None = None,
    user_mounts_rw: dict[str, Path] | None = None,
    blocklist: list[str] | None = None,
    readonly_prefixes: list[str] | None = None,
) -> Ag3ntumPathValidator:
    """
    Configure and return path validator for a session.

    Args:
        session_id: The session ID
        workspace_path: REAL Docker filesystem path to session workspace
        skills_path: Deprecated, use global_skills_path/user_skills_path
        global_skills_path: Path to global skills directory (read-only)
        user_skills_path: Path to user skills directory (read-only)
        external_ro_base: Base path for read-only external mounts (/mounts/ro)
        external_rw_base: Base path for read-write external mounts (/mounts/rw)
        persistent_path: Path to user's persistent storage
        user_mounts_ro: Per-user read-only mounts {name: real_path}
        user_mounts_rw: Per-user read-write mounts {name: real_path}
        blocklist: Optional list of blocked patterns (defaults to common sensitive files)
        readonly_prefixes: Optional list of read-only path prefixes

    Returns:
        The configured Ag3ntumPathValidator
    """
    config = PathValidatorConfig(
        workspace_path=workspace_path,
        skills_path=skills_path,
        global_skills_path=global_skills_path,
        user_skills_path=user_skills_path,
        external_ro_base=external_ro_base,
        external_rw_base=external_rw_base,
        persistent_path=persistent_path,
        user_mounts_ro=user_mounts_ro or {},
        user_mounts_rw=user_mounts_rw or {},
        blocklist=blocklist or [
            "*.env", "*.key", ".git/**", "__pycache__/**", "*.pyc",
            ".secrets/**", "*.pem", "*.p12", "*.pfx",
            "**/node_modules/**",
        ],
        readonly_prefixes=readonly_prefixes or ["skills/", "external/ro/", "external/user-ro/"],
    )
    validator = Ag3ntumPathValidator(config)
    _session_validators[session_id] = validator

    # Log user mount info if any configured
    user_ro_count = len(user_mounts_ro) if user_mounts_ro else 0
    user_rw_count = len(user_mounts_rw) if user_mounts_rw else 0

    logger.info(
        f"PATH_VALIDATOR: Configured for session {session_id} "
        f"with workspace={workspace_path}, "
        f"external_ro={external_ro_base}, external_rw={external_rw_base}, "
        f"persistent={persistent_path}, "
        f"user_mounts_ro={user_ro_count}, user_mounts_rw={user_rw_count}"
    )
    return validator


def cleanup_path_validator(session_id: str) -> None:
    """
    Remove path validator when session ends.

    Args:
        session_id: The session ID to clean up
    """
    if session_id in _session_validators:
        del _session_validators[session_id]
        logger.info(f"PATH_VALIDATOR: Cleaned up validator for session {session_id}")


def has_path_validator(session_id: str) -> bool:
    """
    Check if a path validator is configured for a session.

    Args:
        session_id: The session ID to check

    Returns:
        True if validator is configured, False otherwise
    """
    return session_id in _session_validators
