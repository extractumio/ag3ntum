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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
    log_all_access: bool = Field(
        default=True, description="Log all path access attempts"
    )
    blocklist: list[str] = Field(
        default_factory=lambda: ["*.env", "*.key", ".git/**", "__pycache__/**", "*.pyc"],
        description="Glob patterns to block even within workspace",
    )
    allowlist: list[str] | None = Field(
        default=None, description="If set, only these patterns are allowed"
    )
    # Skills are read-only (path prefixes relative to workspace)
    readonly_prefixes: list[str] = Field(
        default_factory=lambda: ["skills/"],
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

        # Step 2: Check boundary (workspace OR skills directories)
        # Paths can be within:
        # - Workspace (read-write for most, read-only for some prefixes)
        # - Global skills directory (read-only)
        # - User skills directory (read-only)
        in_workspace = False
        in_global_skills = False
        in_user_skills = False
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

        if not in_workspace and not in_global_skills and not in_user_skills:
            self._log_blocked(path, operation, "Outside allowed directories")
            raise PathValidationError(
                f"Path outside allowed directories: {path}",
                path=path,
                reason=f"Path must be within workspace or skills directories",
            )

        # Step 3: Check for path traversal attempts
        if ".." in path:
            # Even if normalized path is valid, log the attempt
            logger.warning(f"PATH_VALIDATOR: Traversal attempt in path: {path}")

        # Step 4: Check blocklist (only for workspace paths)
        if in_workspace:
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
                        reason=f"Matches blocklist pattern: {pattern}",
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
        # Skills directories are always read-only
        # Workspace paths check readonly_prefixes
        is_readonly = in_global_skills or in_user_skills
        if in_workspace and not is_readonly:
            is_readonly = any(
                rel_path.startswith(ro_prefix.rstrip("/"))
                for ro_prefix in self.config.readonly_prefixes
            )

        if is_readonly and operation in ("write", "edit", "delete"):
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

        This translation is critical because the Python file tools run OUTSIDE bwrap
        and need the real Docker filesystem paths.
        """
        p = PurePosixPath(path)

        # Handle agent paths that look like bwrap mounts
        if str(p).startswith("/workspace"):
            # Agent provided bwrap-style path: /workspace/foo -> workspace/foo
            relative_to_workspace = str(p)[len("/workspace") :].lstrip("/")
            resolved = (self.workspace / relative_to_workspace).resolve()
        elif not p.is_absolute():
            # Relative path: ./foo or foo -> workspace/foo
            resolved = (self.workspace / p).resolve()
        else:
            # Absolute path NOT starting with /workspace
            # This is an escape attempt (like /etc/passwd)
            resolved = Path(p).resolve()

        return resolved

    def _log_allowed(self, original: str, normalized: Path, operation: str) -> None:
        """Log allowed path access."""
        if self.config.log_all_access:
            logger.info(
                f"PATH_VALIDATOR: ALLOWED {operation.upper()} "
                f"'{original}' -> '{normalized}'"
            )

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
        blocklist=blocklist or ["*.env", "*.key", ".git/**", "__pycache__/**", "*.pyc"],
        readonly_prefixes=readonly_prefixes or ["skills/"],
    )
    validator = Ag3ntumPathValidator(config)
    _session_validators[session_id] = validator
    logger.info(
        f"PATH_VALIDATOR: Configured for session {session_id} "
        f"with workspace={workspace_path}"
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
