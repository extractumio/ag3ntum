"""
Sandbox configuration and execution helpers for Ag3ntum.

Defines the sandbox configuration schema used by permission profiles and
provides a Bubblewrap-based command wrapper for tool execution.
"""
from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Iterable, Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class SandboxMountError(Exception):
    """Raised when a required sandbox mount source does not exist.
    
    This is a FAIL-CLOSED security mechanism. If mount sources are missing,
    the sandbox cannot provide proper isolation and command execution is denied.
    """
    pass


class SandboxMount(BaseModel):
    """Mount configuration for the sandbox."""

    source: str = Field(description="Host path to mount")
    target: str = Field(description="Path inside sandbox")
    mode: str = Field(default="ro", description="Mount mode: ro or rw")
    optional: bool = Field(
        default=False,
        description="If True, skip mount if source doesn't exist (don't fail)"
    )

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        normalized = (value or "ro").lower()
        if normalized not in {"ro", "rw"}:
            raise ValueError("Sandbox mount mode must be 'ro' or 'rw'")
        return normalized

    def resolve(self, placeholders: dict[str, str]) -> "SandboxMount":
        return SandboxMount(
            source=_resolve_placeholders(self.source, placeholders),
            target=_resolve_placeholders(self.target, placeholders),
            mode=self.mode,
            optional=self.optional,
        )


class SandboxNetworkConfig(BaseModel):
    """Network policy configuration for sandboxed tools."""

    enabled: bool = Field(default=False, description="Allow network access")
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Whitelisted domains for WebFetch/WebSearch",
    )
    allow_localhost: bool = Field(
        default=False,
        description="Allow access to localhost or private network ranges",
    )

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def normalize_domains(cls, value: list[str] | None) -> list[str]:
        if not value:
            return []
        return [domain.strip().lower() for domain in value if domain]


class SandboxEnvConfig(BaseModel):
    """Environment configuration for sandboxed tools."""

    home: str = Field(default="/workspace", description="HOME inside sandbox")
    path: str = Field(default="/usr/bin:/bin", description="PATH inside sandbox")
    clear_env: bool = Field(default=True, description="Clear environment vars")
    custom_env: dict[str, str] = Field(
        default_factory=dict,
        description="Custom environment variables to pass to sandbox (from sandboxed_envs)"
    )


class ProcFilteringConfig(BaseModel):
    """Configuration for /proc filtering in nested containers."""

    enabled: bool = Field(
        default=True,
        description="Enable filtered /proc (hides other processes)",
    )
    allowed_entries: list[str] = Field(
        default_factory=lambda: [
            "/proc/self",      # Own process info (required)
            "/proc/cpuinfo",   # CPU information
            "/proc/meminfo",   # Memory information
            "/proc/uptime",    # System uptime
            "/proc/version",   # Kernel version
        ],
        description="List of /proc entries to expose in filtered mode",
    )


class SandboxConfig(BaseModel):
    """Complete sandbox configuration."""

    enabled: bool = Field(default=True, description="Enable bubblewrap sandbox")
    file_sandboxing: bool = Field(
        default=True,
        description="Use bubblewrap for file system isolation",
    )
    network_sandboxing: bool = Field(
        default=True,
        description="Enforce network policy for WebFetch/WebSearch",
    )
    bwrap_path: str = Field(default="bwrap", description="Path to bubblewrap")
    use_tmpfs_root: bool = Field(default=True, description="Mount empty tmpfs at /")
    static_mounts: dict[str, SandboxMount] = Field(default_factory=dict)
    session_mounts: dict[str, SandboxMount] = Field(default_factory=dict)
    dynamic_mounts: list[SandboxMount] = Field(default_factory=list)
    network: SandboxNetworkConfig = Field(default_factory=SandboxNetworkConfig)
    environment: SandboxEnvConfig = Field(default_factory=SandboxEnvConfig)
    proc_filtering: ProcFilteringConfig = Field(default_factory=ProcFilteringConfig)
    writable_paths: list[str] = Field(default_factory=list)
    readonly_paths: list[str] = Field(default_factory=list)

    @field_validator("dynamic_mounts", mode="before")
    @classmethod
    def normalize_dynamic_mounts(cls, value: list[SandboxMount] | None) -> list[SandboxMount]:
        if not value:
            return []
        return value

    def resolve(self, placeholders: dict[str, str]) -> "SandboxConfig":
        def resolve_mounts(mounts: dict[str, SandboxMount]) -> dict[str, SandboxMount]:
            return {
                key: mount.resolve(placeholders)
                for key, mount in mounts.items()
            }

        # SECURITY: Create a fresh copy of environment to prevent cross-session leakage
        # Each session must have its own SandboxEnvConfig instance so that
        # user-specific sandboxed_envs don't leak between sessions
        fresh_environment = SandboxEnvConfig(
            home=self.environment.home,
            path=self.environment.path,
            clear_env=self.environment.clear_env,
            custom_env={},  # Start empty - will be populated per-session
        )

        return SandboxConfig(
            enabled=self.enabled,
            file_sandboxing=self.file_sandboxing,
            network_sandboxing=self.network_sandboxing,
            bwrap_path=_resolve_placeholders(self.bwrap_path, placeholders),
            use_tmpfs_root=self.use_tmpfs_root,
            static_mounts=resolve_mounts(self.static_mounts),
            session_mounts=resolve_mounts(self.session_mounts),
            dynamic_mounts=[mount.resolve(placeholders) for mount in self.dynamic_mounts],
            network=self.network,
            environment=fresh_environment,
            proc_filtering=self.proc_filtering,
            writable_paths=[
                _resolve_placeholders(path, placeholders)
                for path in self.writable_paths
            ],
            readonly_paths=[
                _resolve_placeholders(path, placeholders)
                for path in self.readonly_paths
            ],
        )


class SandboxExecutor:
    """Build bubblewrap commands for sandboxed execution."""

    def __init__(
        self,
        config: SandboxConfig,
        linux_uid: Optional[int] = None,
        linux_gid: Optional[int] = None,
    ) -> None:
        self._config = config
        self._linux_uid = linux_uid
        self._linux_gid = linux_gid

    @property
    def config(self) -> SandboxConfig:
        return self._config

    @property
    def linux_uid(self) -> Optional[int]:
        return self._linux_uid

    @property
    def linux_gid(self) -> Optional[int]:
        return self._linux_gid

    def build_bwrap_command(
        self,
        command: Iterable[str],
        allow_network: bool,
        nested_container: bool = True,
    ) -> list[str]:
        """
        Build a bubblewrap command for the given command args.

        Args:
            command: Command arguments to execute inside sandbox.
            allow_network: Whether to allow network access.
            nested_container: If True, use flags compatible with running
                inside Docker (avoids pivot_root issues).
        """
        config = self._config

        # Base command - avoid flags that cause pivot_root in Docker
        # Split bwrap_path to handle "sudo bwrap" as separate arguments
        cmd = config.bwrap_path.split()

        # In Docker, we need to be careful about namespace operations
        # Use --unshare-user --unshare-pid for basic isolation
        # but avoid --unshare-all which requires pivot_root
        if nested_container:
            # --unshare-user is required when using --uid/--gid flags
            # It creates a new user namespace where we can set custom UIDs
            if self._linux_uid is not None or self._linux_gid is not None:
                cmd.append("--unshare-user")
                # Map the user's UID/GID so workspace files appear with correct ownership
                # Without this, host UIDs appear as 65534 (overflow) inside the namespace
                # Format: --uid-map <inside_uid> <outside_uid> <count>
                cmd.extend(["--uid-map", str(self._linux_uid), str(self._linux_uid), "1"])
                if self._linux_gid is not None:
                    cmd.extend(["--gid-map", str(self._linux_gid), str(self._linux_gid), "1"])
            cmd.extend([
                "--unshare-pid",
                "--unshare-uts",
                "--unshare-ipc",
            ])
        else:
            cmd.append("--unshare-all")

        cmd.extend([
            "--die-with-parent",
            "--new-session",
        ])

        # For nested containers: don't try to create a new root filesystem
        # Instead, bind-mount everything explicitly and block access to
        # sensitive paths by NOT mounting them
        if config.use_tmpfs_root and not nested_container:
            cmd.extend(["--tmpfs", "/"])
        else:
            # In Docker, create an isolated filesystem view
            # Start with tmpfs at /tmp for scratch space
            cmd.extend(["--tmpfs", "/tmp:size=100M"])

        # In nested containers (Docker), use filtered /proc for security
        # Full --proc /proc mount doesn't work in Docker (requires pivot_root)
        if nested_container:
            if config.proc_filtering.enabled:
                # SECURITY: Create filtered /proc with only safe entries
                # This prevents agents from seeing other processes and their environments
                cmd.extend(["--tmpfs", "/proc"])

                # Mount only safe /proc entries that exist on the host
                for entry in config.proc_filtering.allowed_entries:
                    if Path(entry).exists():
                        cmd.extend(["--ro-bind", entry, entry])
                    else:
                        logger.debug(f"Skipping non-existent proc entry: {entry}")

                logger.info(
                    f"BWRAP: Using filtered /proc with {len(config.proc_filtering.allowed_entries)} "
                    "entries (process isolation enabled)"
                )
            else:
                # INSECURE: Full /proc bind (exposes all processes)
                logger.warning(
                    "BWRAP: Using full /proc bind - ALL PROCESSES VISIBLE TO AGENT "
                    "(proc_filtering disabled)"
                )
                cmd.extend(["--ro-bind", "/proc", "/proc"])

            cmd.extend(["--dev-bind", "/dev", "/dev"])
        else:
            # Native mode: Use isolated proc (if this ever works outside Docker)
            cmd.extend(["--proc", "/proc", "--dev", "/dev"])

        # Mount static and session mounts
        # FAIL-CLOSED for required mounts, skip optional mounts if source doesn't exist
        for name, mount in list(config.static_mounts.items()) + list(config.session_mounts.items()):
            source_path = Path(mount.source)
            if not source_path.exists():
                if mount.optional:
                    logger.debug(f"BWRAP: Skipping optional mount '{name}': {mount.source} (not found)")
                    continue
                raise SandboxMountError(
                    f"SECURITY: Mount source does not exist for '{name}': {mount.source}. "
                    "Refusing to execute command without proper sandbox isolation."
                )
            cmd.extend(_mount_args(mount))

        # Mount dynamic mounts
        # FAIL-CLOSED for required mounts, skip optional mounts if source doesn't exist
        for mount in config.dynamic_mounts:
            source_path = Path(mount.source)
            if not source_path.exists():
                if mount.optional:
                    logger.debug(f"BWRAP: Skipping optional dynamic mount: {mount.source} (not found)")
                    continue
                raise SandboxMountError(
                    f"SECURITY: Dynamic mount source does not exist: {mount.source}. "
                    "Refusing to execute command without proper sandbox isolation."
                )
            cmd.extend(_mount_args(mount))

        # Network isolation - only if not in nested container
        if not allow_network and config.network_sandboxing and not nested_container:
            cmd.append("--unshare-net")

        if config.environment.clear_env:
            cmd.append("--clearenv")

        cmd.extend(["--setenv", "HOME", config.environment.home])
        cmd.extend(["--setenv", "PATH", config.environment.path])

        # Set execution context for path resolution
        # This allows SandboxPathResolver to detect it's running inside bubblewrap
        cmd.extend(["--setenv", "AG3NTUM_CONTEXT", "sandbox"])

        # Apply custom environment variables from sandboxed_envs
        # These are user-specific secrets that should be available in the sandbox
        if config.environment.custom_env:
            for env_name, env_value in config.environment.custom_env.items():
                # Security: validate env name to prevent injection
                if env_name and env_name.isidentifier() and env_value is not None:
                    cmd.extend(["--setenv", env_name, str(env_value)])
                    logger.debug(f"BWRAP: Set custom env {env_name}=***")
                else:
                    logger.warning(f"BWRAP: Skipping invalid env var name: {env_name}")

            if config.environment.custom_env:
                logger.info(
                    f"BWRAP: Applied {len(config.environment.custom_env)} custom env vars "
                    f"from sandboxed_envs"
                )

        # Add UID/GID dropping via bwrap (instead of preexec_fn)
        # This works because bwrap runs via sudo (configured in permissions.yaml)
        # and can drop to the target user UID/GID
        if self._linux_uid is not None:
            cmd.extend(["--uid", str(self._linux_uid)])
        if self._linux_gid is not None:
            cmd.extend(["--gid", str(self._linux_gid)])

        cmd.extend(["--chdir", config.environment.home])

        cmd.append("--")
        cmd.extend(list(command))

        return cmd

    def wrap_shell_command(self, command: str, allow_network: bool) -> str:
        """Wrap a shell command string in a bubblewrap invocation."""
        wrapped = self.build_bwrap_command(
            ["bash", "-lc", command],
            allow_network=allow_network,
        )
        return shlex.join(wrapped)

    def validate_mount_sources(self) -> list[str]:
        """Return a list of missing required mount sources for diagnostics.

        Optional mounts are not included in the missing list.
        """
        missing = []
        mounts = list(self._config.static_mounts.values()) + list(self._config.session_mounts.values())
        mounts += list(self._config.dynamic_mounts)
        for mount in mounts:
            if not Path(mount.source).exists() and not mount.optional:
                missing.append(mount.source)
        return missing


def _resolve_placeholders(value: str, placeholders: dict[str, str]) -> str:
    resolved = value
    for key, replacement in placeholders.items():
        resolved = resolved.replace(f"{{{key}}}", replacement)
    return resolved


def _mount_args(mount: SandboxMount) -> list[str]:
    if mount.mode == "rw":
        return ["--bind", mount.source, mount.target]
    return ["--ro-bind", mount.source, mount.target]


class UIDValidationError(Exception):
    """Raised when a UID/GID fails security validation."""
    pass


def create_demote_fn(
    uid: int,
    gid: int,
    session_uid: Optional[int] = None,
    validate: bool = True,
):
    """
    Create preexec_fn for dropping privileges before exec.

    This function returns a callable that, when used as preexec_fn in
    subprocess creation, will drop the process privileges to the specified
    UID and GID before executing the command.

    This is used by Ag3ntumBash to ensure sandboxed commands run as the
    session user's UID instead of the API user (45045), so files created
    by the agent are owned by the correct user.

    SECURITY: This function performs UID/GID validation before creating the
    demote function. The following are ALWAYS blocked:
    - UID 0 (root)
    - GID 0 (root group)
    - System UIDs/GIDs (1-999)
    - UIDs outside the configured range

    Args:
        uid: Linux user ID to switch to.
        gid: Linux group ID to switch to.
        session_uid: If provided, validates that uid matches the session's UID
                     (principle of least privilege - each session only uses its own UID)
        validate: If True (default), perform UID/GID validation. Set to False
                  only for testing purposes.

    Returns:
        Callable that drops privileges when called.

    Raises:
        UIDValidationError: If the UID or GID fails security validation.
    """
    import os

    # Import UID security validation
    from src.core.uid_security import (
        validate_uid_for_setuid,
        validate_gid_for_setgid,
        log_uid_operation,
    )

    # SECURITY: Validate UID before creating demote function
    if validate:
        uid_valid, uid_reason = validate_uid_for_setuid(uid, session_uid=session_uid)
        if not uid_valid:
            log_uid_operation("create_demote_fn", uid, session_uid, success=False, reason=uid_reason)
            raise UIDValidationError(f"UID validation failed: {uid_reason}")

        gid_valid, gid_reason = validate_gid_for_setgid(gid)
        if not gid_valid:
            log_uid_operation("create_demote_fn", uid, session_uid, success=False, reason=gid_reason)
            raise UIDValidationError(f"GID validation failed: {gid_reason}")

        log_uid_operation("create_demote_fn", uid, session_uid, success=True)

    def demote():
        try:
            # Drop supplementary groups first
            os.setgroups([])
            # Set GID (must be before UID)
            os.setgid(gid)
            # Set UID (must be last)
            os.setuid(uid)
        except Exception as e:
            logger.error(f"Failed to drop privileges to UID={uid}, GID={gid}: {e}")
            raise

    return demote


# Alias for backward compatibility
_create_demote_fn = create_demote_fn


async def execute_sandboxed_command(
    executor: SandboxExecutor,
    command: str,
    allow_network: bool = False,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """
    Execute a shell command inside the bubblewrap sandbox.

    This is the core sandboxed execution function that wraps any command
    in bubblewrap with the configured mounts and isolation.

    Args:
        executor: SandboxExecutor with resolved mount configuration.
        command: Shell command to execute inside the sandbox.
        allow_network: Whether to allow network access.
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (exit_code, stdout, stderr).
    """
    import asyncio

    # Build the bwrap command
    bwrap_cmd = executor.build_bwrap_command(
        ["bash", "-c", command],
        allow_network=allow_network,
    )

    logger.info(f"SANDBOX EXEC: {' '.join(bwrap_cmd[:10])}...")
    logger.debug(f"SANDBOX FULL CMD: {' '.join(bwrap_cmd)}")

    # Note: UID/GID dropping is now handled by bwrap --uid/--gid flags
    # (configured in build_bwrap_command). This requires bwrap to run via sudo
    # (bwrap_path: "sudo bwrap" in permissions.yaml), which has NOPASSWD access
    # configured in the Dockerfile sudoers rules.
    if executor.linux_uid is not None and executor.linux_gid is not None:
        logger.debug(f"Bwrap will drop privileges to UID={executor.linux_uid}, GID={executor.linux_gid}")

    try:
        process = await asyncio.create_subprocess_exec(
            *bwrap_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        exit_code = process.returncode or 0
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        logger.info(f"SANDBOX RESULT: exit={exit_code}, stdout_len={len(stdout)}")
        return exit_code, stdout, stderr

    except asyncio.TimeoutError:
        logger.warning(f"SANDBOX TIMEOUT: Command timed out after {timeout}s")
        if process:
            process.kill()
        return 124, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        logger.error(f"SANDBOX ERROR: {e}")
        return 1, "", str(e)
