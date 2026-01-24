"""
UID Security Configuration and Validation for Ag3ntum.

This module provides the security infrastructure for user isolation through
UID/GID management with two supported modes:

Mode A: Isolated Range (Default - Multi-tenant Safe)
    - UIDs allocated from a dedicated range (50000-60000)
    - These UIDs don't correspond to real host users
    - Safer for multi-tenant deployments
    - Files in bind mounts may need ownership management

Mode B: Direct Host Mapping (Opt-in - Dev/Single-tenant)
    - UIDs map directly to host system UIDs (1000-65533)
    - Session user UID in container = same UID on host
    - Simpler file permissions on bind-mounted volumes
    - Requires explicit opt-in due to security implications

Security Invariants (enforced in BOTH modes):
    1. UID 0 (root) is NEVER allowed for setuid operations
    2. System UIDs (1-999) are blocked
    3. Each session can only setuid to its own authenticated UID
    4. Seccomp policies enforce UID restrictions at kernel level

Usage:
    from src.core.uid_security import (
        UIDMode,
        UIDSecurityConfig,
        get_uid_security_config,
        validate_uid_for_setuid,
    )

    # Get current config
    config = get_uid_security_config()

    # Validate a UID before use
    if not validate_uid_for_setuid(uid, config):
        raise SecurityError("UID not allowed")
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)


class UIDMode(Enum):
    """UID mapping mode for user isolation.

    ISOLATED: Use dedicated UID range (50000-60000), safer for multi-tenant
    DIRECT: Map to host UIDs (1000-65533), simpler for dev/single-tenant
    """
    ISOLATED = "isolated"
    DIRECT = "direct"


class UIDSecurityError(Exception):
    """Raised when a UID security violation is detected."""
    pass


@dataclass
class UIDSecurityConfig:
    """Configuration for UID-based security and isolation.

    This configuration determines how UIDs are allocated and validated
    for sandboxed command execution.

    Attributes:
        mode: The UID mapping mode (ISOLATED or DIRECT)
        isolated_uid_min: Minimum UID for isolated mode (default: 50000)
        isolated_uid_max: Maximum UID for isolated mode (default: 60000)
        direct_uid_min: Minimum UID for direct mode (default: 1000)
        direct_uid_max: Maximum UID for direct mode (default: 65533)
        blocked_uids: Set of UIDs that are always blocked (includes 0)
        system_uid_max: Maximum UID considered a system account (default: 999)
        api_user_uid: UID of the API process (default: 45045)
        require_capability_check: Whether to verify CAP_SETUID before use
    """
    mode: UIDMode = UIDMode.ISOLATED

    # Isolated mode range (dedicated, doesn't map to host users)
    isolated_uid_min: int = 50000
    isolated_uid_max: int = 60000

    # Direct mode range (maps to host users)
    direct_uid_min: int = 1000
    direct_uid_max: int = 65533

    # Security invariants
    blocked_uids: Set[int] = field(default_factory=lambda: {0})  # Root always blocked
    system_uid_max: int = 999  # System accounts (0-999) blocked

    # API user (well above both ranges to avoid collision)
    api_user_uid: int = 45045

    # Capability checking
    require_capability_check: bool = True

    # Legacy compatibility - original UID range started at 2000
    # When migrating existing users, this range may also be valid
    legacy_uid_min: int = 2000
    legacy_uid_max: int = 49999
    allow_legacy_uids: bool = True

    def get_uid_range(self) -> tuple[int, int]:
        """Get the valid UID range for the current mode."""
        if self.mode == UIDMode.ISOLATED:
            return (self.isolated_uid_min, self.isolated_uid_max)
        else:
            return (self.direct_uid_min, self.direct_uid_max)

    def get_next_uid_start(self) -> int:
        """Get the starting UID for new user allocation."""
        if self.mode == UIDMode.ISOLATED:
            return self.isolated_uid_min
        else:
            return self.direct_uid_min

    def is_uid_in_valid_range(self, uid: int) -> bool:
        """Check if a UID is in the valid range for the current mode."""
        min_uid, max_uid = self.get_uid_range()
        in_current_range = min_uid <= uid <= max_uid

        # Also allow legacy range if enabled
        if self.allow_legacy_uids:
            in_legacy_range = self.legacy_uid_min <= uid <= self.legacy_uid_max
            return in_current_range or in_legacy_range

        return in_current_range

    def to_dict(self) -> dict:
        """Serialize config to dictionary."""
        return {
            "mode": self.mode.value,
            "isolated_uid_min": self.isolated_uid_min,
            "isolated_uid_max": self.isolated_uid_max,
            "direct_uid_min": self.direct_uid_min,
            "direct_uid_max": self.direct_uid_max,
            "blocked_uids": list(self.blocked_uids),
            "system_uid_max": self.system_uid_max,
            "api_user_uid": self.api_user_uid,
            "allow_legacy_uids": self.allow_legacy_uids,
        }


# Global configuration instance
_uid_security_config: Optional[UIDSecurityConfig] = None


def get_uid_security_config() -> UIDSecurityConfig:
    """Get the current UID security configuration.

    Returns cached config or loads from environment/config file.
    """
    global _uid_security_config

    if _uid_security_config is None:
        _uid_security_config = _load_uid_security_config()

    return _uid_security_config


def set_uid_security_config(config: UIDSecurityConfig) -> None:
    """Set the global UID security configuration.

    Used primarily for testing or explicit configuration.
    """
    global _uid_security_config
    _uid_security_config = config
    logger.info(f"UID security config set: mode={config.mode.value}")


def _load_uid_security_config() -> UIDSecurityConfig:
    """Load UID security configuration from environment or config file."""
    # Check environment variable for mode
    mode_str = os.environ.get("AG3NTUM_UID_MODE", "isolated").lower()

    if mode_str == "direct":
        mode = UIDMode.DIRECT
        logger.warning(
            "UID_MODE=direct: Using direct host UID mapping. "
            "This maps container UIDs directly to host UIDs. "
            "Ensure you understand the security implications."
        )
    else:
        mode = UIDMode.ISOLATED
        logger.info("UID_MODE=isolated: Using isolated UID range (50000-60000)")

    # Load additional settings from environment
    config = UIDSecurityConfig(mode=mode)

    # Override ranges if specified
    if os.environ.get("AG3NTUM_ISOLATED_UID_MIN"):
        config.isolated_uid_min = int(os.environ["AG3NTUM_ISOLATED_UID_MIN"])
    if os.environ.get("AG3NTUM_ISOLATED_UID_MAX"):
        config.isolated_uid_max = int(os.environ["AG3NTUM_ISOLATED_UID_MAX"])
    if os.environ.get("AG3NTUM_DIRECT_UID_MIN"):
        config.direct_uid_min = int(os.environ["AG3NTUM_DIRECT_UID_MIN"])
    if os.environ.get("AG3NTUM_DIRECT_UID_MAX"):
        config.direct_uid_max = int(os.environ["AG3NTUM_DIRECT_UID_MAX"])

    return config


def validate_uid_for_setuid(
    uid: int,
    config: Optional[UIDSecurityConfig] = None,
    session_uid: Optional[int] = None,
) -> tuple[bool, str]:
    """Validate that a UID is safe for setuid operations.

    This is a critical security function that enforces:
    1. UID 0 (root) is NEVER allowed
    2. System UIDs (1-999) are blocked
    3. UID must be in the valid range for the current mode
    4. If session_uid is provided, target UID must match (principle of least privilege)

    Args:
        uid: The UID to validate
        config: Security config (uses global if not provided)
        session_uid: If provided, the UID must match this (session's own UID)

    Returns:
        Tuple of (is_valid, reason_if_invalid)
    """
    if config is None:
        config = get_uid_security_config()

    # CRITICAL: Root is NEVER allowed
    if uid == 0:
        return (False, "SECURITY VIOLATION: UID 0 (root) is blocked unconditionally")

    # Block explicitly blocked UIDs
    if uid in config.blocked_uids:
        return (False, f"UID {uid} is in the blocked UIDs list")

    # Block system accounts
    if uid <= config.system_uid_max:
        return (False, f"UID {uid} is a system account (<=  {config.system_uid_max})")

    # Block API user UID (should never be used for sandboxed commands)
    if uid == config.api_user_uid:
        return (False, f"UID {uid} is the API user UID and cannot be used for sandbox")

    # Check UID is in valid range for current mode
    if not config.is_uid_in_valid_range(uid):
        min_uid, max_uid = config.get_uid_range()
        return (
            False,
            f"UID {uid} is outside valid range [{min_uid}, {max_uid}] for mode {config.mode.value}"
        )

    # Principle of least privilege: if session_uid is specified, must match
    if session_uid is not None and uid != session_uid:
        return (
            False,
            f"UID {uid} does not match session UID {session_uid} (principle of least privilege)"
        )

    return (True, "")


def validate_gid_for_setgid(
    gid: int,
    config: Optional[UIDSecurityConfig] = None,
) -> tuple[bool, str]:
    """Validate that a GID is safe for setgid operations.

    Similar to UID validation but for group IDs.
    """
    if config is None:
        config = get_uid_security_config()

    # GID 0 (root group) is never allowed
    if gid == 0:
        return (False, "SECURITY VIOLATION: GID 0 (root) is blocked unconditionally")

    # Block system groups
    if gid <= config.system_uid_max:
        return (False, f"GID {gid} is a system group (<= {config.system_uid_max})")

    # Check GID is in valid range (same ranges as UID)
    if not config.is_uid_in_valid_range(gid):
        min_gid, max_gid = config.get_uid_range()
        return (
            False,
            f"GID {gid} is outside valid range [{min_gid}, {max_gid}] for mode {config.mode.value}"
        )

    return (True, "")


def check_setuid_capability() -> tuple[bool, str]:
    """Check if the current process has CAP_SETUID capability.

    This checks whether the process can actually perform setuid operations.
    On Linux, this requires either:
    - Running as root (UID 0) - NOT recommended
    - Having CAP_SETUID capability

    Returns:
        Tuple of (has_capability, reason)
    """
    import platform

    if platform.system() != "Linux":
        return (False, "setuid capability check only supported on Linux")

    # Try to import Linux capability checking
    try:
        # Method 1: Check /proc/self/status for capabilities
        status_path = Path("/proc/self/status")
        if status_path.exists():
            content = status_path.read_text()
            for line in content.splitlines():
                if line.startswith("CapEff:"):
                    cap_hex = line.split(":")[1].strip()
                    cap_int = int(cap_hex, 16)
                    # CAP_SETUID is bit 7 (1 << 7 = 128)
                    # CAP_SETGID is bit 6 (1 << 6 = 64)
                    has_setuid = bool(cap_int & (1 << 7))
                    has_setgid = bool(cap_int & (1 << 6))

                    if has_setuid and has_setgid:
                        return (True, "CAP_SETUID and CAP_SETGID present")
                    elif has_setuid:
                        return (False, "CAP_SETUID present but CAP_SETGID missing")
                    elif has_setgid:
                        return (False, "CAP_SETGID present but CAP_SETUID missing")
                    else:
                        return (False, "Neither CAP_SETUID nor CAP_SETGID present")

        return (False, "Could not read /proc/self/status")

    except Exception as e:
        return (False, f"Error checking capabilities: {e}")


def get_seccomp_profile_path(mode: Optional[UIDMode] = None) -> Path:
    """Get the path to the seccomp profile for the given mode.

    Args:
        mode: UID mode (uses global config if not provided)

    Returns:
        Path to the appropriate seccomp profile JSON file
    """
    if mode is None:
        mode = get_uid_security_config().mode

    # Seccomp profiles are stored in config/security/
    config_dir = Path(os.environ.get("AG3NTUM_ROOT", "/")) / "config" / "security"

    if mode == UIDMode.ISOLATED:
        return config_dir / "seccomp-isolated.json"
    else:
        return config_dir / "seccomp-direct.json"


def generate_seccomp_profile(mode: UIDMode, output_path: Optional[Path] = None) -> dict:
    """Generate a seccomp profile for the specified mode.

    The profile restricts setuid/setgid syscalls to only allow
    UIDs/GIDs in the valid range for the mode.

    Args:
        mode: The UID mode to generate profile for
        output_path: If provided, write the profile to this file

    Returns:
        The seccomp profile as a dictionary
    """
    import json

    config = UIDSecurityConfig(mode=mode)
    min_uid, max_uid = config.get_uid_range()

    # Base seccomp profile structure
    profile = {
        "defaultAction": "SCMP_ACT_ALLOW",
        "architectures": [
            "SCMP_ARCH_X86_64",
            "SCMP_ARCH_X86",
            "SCMP_ARCH_AARCH64",
        ],
        "syscalls": [
            # Block setuid to UID 0 (root) - CRITICAL
            {
                "names": ["setuid", "setuid32", "setreuid", "setreuid32", "setresuid", "setresuid32", "setfsuid", "setfsuid32"],
                "action": "SCMP_ACT_ERRNO",
                "errnoRet": 1,  # EPERM
                "args": [
                    {
                        "index": 0,
                        "value": 0,
                        "op": "SCMP_CMP_EQ"
                    }
                ],
                "comment": "Block setuid to root (UID 0)"
            },
            # Block setgid to GID 0 (root group) - CRITICAL
            {
                "names": ["setgid", "setgid32", "setregid", "setregid32", "setresgid", "setresgid32", "setfsgid", "setfsgid32"],
                "action": "SCMP_ACT_ERRNO",
                "errnoRet": 1,  # EPERM
                "args": [
                    {
                        "index": 0,
                        "value": 0,
                        "op": "SCMP_CMP_EQ"
                    }
                ],
                "comment": "Block setgid to root (GID 0)"
            },
            # Block setuid to system accounts (1-999)
            {
                "names": ["setuid", "setuid32", "setreuid", "setreuid32", "setresuid", "setresuid32", "setfsuid", "setfsuid32"],
                "action": "SCMP_ACT_ERRNO",
                "errnoRet": 1,  # EPERM
                "args": [
                    {
                        "index": 0,
                        "value": config.system_uid_max,
                        "op": "SCMP_CMP_LE"
                    }
                ],
                "comment": f"Block setuid to system accounts (UID <= {config.system_uid_max})"
            },
            # Block setgid to system groups (1-999)
            {
                "names": ["setgid", "setgid32", "setregid", "setregid32", "setresgid", "setresgid32", "setfsgid", "setfsgid32"],
                "action": "SCMP_ACT_ERRNO",
                "errnoRet": 1,  # EPERM
                "args": [
                    {
                        "index": 0,
                        "value": config.system_uid_max,
                        "op": "SCMP_CMP_LE"
                    }
                ],
                "comment": f"Block setgid to system groups (GID <= {config.system_uid_max})"
            },
        ],
        "comment": f"Seccomp profile for Ag3ntum UID mode: {mode.value}. "
                   f"Valid UID range: [{min_uid}, {max_uid}]. "
                   "Root (0) and system accounts (1-999) are always blocked."
    }

    # For isolated mode, also block UIDs outside the isolated range
    if mode == UIDMode.ISOLATED:
        # Block UIDs below the isolated range (except those already blocked)
        profile["syscalls"].append({
            "names": ["setuid", "setuid32", "setreuid", "setreuid32", "setresuid", "setresuid32", "setfsuid", "setfsuid32"],
            "action": "SCMP_ACT_ERRNO",
            "errnoRet": 1,
            "args": [
                {
                    "index": 0,
                    "value": min_uid,
                    "op": "SCMP_CMP_LT"
                }
            ],
            "comment": f"Block setuid to UIDs below isolated range (< {min_uid})"
        })

        # Block UIDs above the isolated range
        profile["syscalls"].append({
            "names": ["setuid", "setuid32", "setreuid", "setreuid32", "setresuid", "setresuid32", "setfsuid", "setfsuid32"],
            "action": "SCMP_ACT_ERRNO",
            "errnoRet": 1,
            "args": [
                {
                    "index": 0,
                    "value": max_uid,
                    "op": "SCMP_CMP_GT"
                }
            ],
            "comment": f"Block setuid to UIDs above isolated range (> {max_uid})"
        })

        # Same for GIDs
        profile["syscalls"].append({
            "names": ["setgid", "setgid32", "setregid", "setregid32", "setresgid", "setresgid32", "setfsgid", "setfsgid32"],
            "action": "SCMP_ACT_ERRNO",
            "errnoRet": 1,
            "args": [
                {
                    "index": 0,
                    "value": min_uid,
                    "op": "SCMP_CMP_LT"
                }
            ],
            "comment": f"Block setgid to GIDs below isolated range (< {min_uid})"
        })

        profile["syscalls"].append({
            "names": ["setgid", "setgid32", "setregid", "setregid32", "setresgid", "setresgid32", "setfsgid", "setfsgid32"],
            "action": "SCMP_ACT_ERRNO",
            "errnoRet": 1,
            "args": [
                {
                    "index": 0,
                    "value": max_uid,
                    "op": "SCMP_CMP_GT"
                }
            ],
            "comment": f"Block setgid to GIDs above isolated range (> {max_uid})"
        })

    # Write to file if path provided
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(profile, f, indent=2)
        logger.info(f"Generated seccomp profile for mode {mode.value} at {output_path}")

    return profile


# Security audit logging
def log_uid_operation(
    operation: str,
    target_uid: int,
    session_uid: Optional[int] = None,
    success: bool = True,
    reason: str = "",
) -> None:
    """Log UID-related security operations for audit trail.

    Args:
        operation: The operation being performed (e.g., "setuid", "validate")
        target_uid: The target UID of the operation
        session_uid: The session's authenticated UID (if applicable)
        success: Whether the operation succeeded
        reason: Reason for failure (if applicable)
    """
    current_uid = os.getuid()

    if success:
        logger.info(
            f"UID_SECURITY: {operation} target={target_uid} "
            f"session_uid={session_uid} current_uid={current_uid} result=SUCCESS"
        )
    else:
        logger.warning(
            f"UID_SECURITY: {operation} target={target_uid} "
            f"session_uid={session_uid} current_uid={current_uid} "
            f"result=BLOCKED reason='{reason}'"
        )
