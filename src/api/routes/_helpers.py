"""Shared helpers for route modules."""
import os

# Path to VERSION file relative to project root
_VERSION_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "VERSION"
)


def _read_version() -> str:
    """Read the platform version from the VERSION file."""
    try:
        with open(_VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unknown"
