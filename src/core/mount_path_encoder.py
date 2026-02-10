"""
Mount path encoder for original-path mounts.

Provides encoding/decoding functions for paths that need to be accessible
at their original locations (e.g., /var/log) within the sandbox.

Path Encoding:
    /var/log -> _var_log
    /data/output -> _data_output
    /home/user/docs -> _home_user_docs

Docker Mount Path:
    /var/log -> /mounts/paths/_var_log
"""
from __future__ import annotations

import re
from pathlib import Path

# Reserved paths that cannot be mounted (system-critical)
RESERVED_PATHS = frozenset({
    "/",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/usr",
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/tmp",
    "/workspace",  # Agent workspace
    "/mounts",     # Mount namespace
    "/persistent", # Persistent storage
    "/skills",     # Skills directory
    "/venv",       # Python venv
    "/root",       # Root home
})

# Paths that are always blocked (even as subpaths)
BLOCKED_PATTERNS = (
    r"^/proc(/|$)",
    r"^/sys(/|$)",
    r"^/dev(/|$)",
)


def encode_path(path: str) -> str:
    """
    Encode a path for use as a mount directory name.

    Replaces slashes with underscores.

    Args:
        path: The original path (e.g., "/var/log")

    Returns:
        The encoded name (e.g., "_var_log")

    Examples:
        >>> encode_path("/var/log")
        '_var_log'
        >>> encode_path("/data/output")
        '_data_output'
    """
    # Normalize the path (remove trailing slashes, resolve .)
    normalized = str(Path(path).resolve()) if path else ""

    # Replace slashes with underscores
    return normalized.replace("/", "_")


def decode_path(encoded: str) -> str:
    """
    Decode an encoded path back to the original.

    Replaces underscores with slashes.

    Args:
        encoded: The encoded name (e.g., "_var_log")

    Returns:
        The original path (e.g., "/var/log")

    Examples:
        >>> decode_path("_var_log")
        '/var/log'
        >>> decode_path("_data_output")
        '/data/output'
    """
    # Replace underscores with slashes
    # Handle leading underscore -> leading slash
    if encoded.startswith("_"):
        return encoded.replace("_", "/")
    return "/" + encoded.replace("_", "/")


def to_docker_path(original_path: str) -> str:
    """
    Convert an original path to its Docker mount path.

    Args:
        original_path: The original filesystem path (e.g., "/var/log")

    Returns:
        The Docker container path (e.g., "/mounts/paths/_var_log")

    Examples:
        >>> to_docker_path("/var/log")
        '/mounts/paths/_var_log'
    """
    encoded = encode_path(original_path)
    return f"/mounts/paths/{encoded}"


def from_docker_path(docker_path: str) -> str | None:
    """
    Extract the original path from a Docker mount path.

    Args:
        docker_path: The Docker container path (e.g., "/mounts/paths/_var_log")

    Returns:
        The original path (e.g., "/var/log"), or None if not a valid mount path
    """
    prefix = "/mounts/paths/"
    if not docker_path.startswith(prefix):
        return None

    encoded = docker_path[len(prefix):]
    # Remove any subpath (we just want the mount root)
    if "/" in encoded:
        encoded = encoded.split("/")[0]

    return decode_path(encoded)


def is_reserved_path(path: str) -> bool:
    """
    Check if a path is in the reserved blocklist.

    Reserved paths are system-critical and cannot be mounted.

    Args:
        path: The path to check

    Returns:
        True if the path is reserved (blocked)
    """
    normalized = str(Path(path).resolve()) if path else ""

    # Check exact matches
    if normalized in RESERVED_PATHS:
        return True

    # Check blocked patterns
    return any(re.match(pattern, normalized) for pattern in BLOCKED_PATTERNS)


def is_path_under_reserved(path: str) -> bool:
    """
    Check if a path is under any reserved path.

    Args:
        path: The path to check

    Returns:
        True if the path is under a reserved directory
    """
    normalized = str(Path(path).resolve()) if path else ""
    return any(normalized.startswith(reserved + "/") for reserved in RESERVED_PATHS)


def validate_original_path(path: str) -> tuple[bool, str | None]:
    """
    Validate that a path can be used as an original-path mount.

    Args:
        path: The path to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not path:
        return False, "Path is empty"

    if not path.startswith("/"):
        return False, "Path must be absolute"

    if is_reserved_path(path):
        return False, f"Path '{path}' is reserved and cannot be mounted"

    # Breadth validation: reject overly broad mounts
    depth = len([p for p in path.strip("/").split("/") if p])
    if depth < 2:
        return False, (
            f"Path '{path}' is too broad (depth {depth}, minimum 2). "
            f"Mount a more specific path like '{path}/subdir'"
        )

    # Check for encoding collisions
    # (paths that encode to the same value)
    encoded = encode_path(path)
    decoded = decode_path(encoded)
    if decoded != path:
        return False, f"Path '{path}' cannot be safely encoded (collision)"

    return True, None


def check_encoding_collision(path1: str, path2: str) -> bool:
    """
    Check if two paths would encode to the same value.

    Args:
        path1: First path
        path2: Second path

    Returns:
        True if there would be a collision
    """
    return encode_path(path1) == encode_path(path2) and path1 != path2
