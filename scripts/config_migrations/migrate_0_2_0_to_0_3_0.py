"""
Config migration: 0.2.0 -> 0.3.0

Changes:
- Adds _version key to all config files (config versioning support)
"""


def migrate(config: dict, filename: str) -> dict:
    """
    Migrate a config dict from 0.2.0 to 0.3.0.

    Args:
        config: The parsed YAML config dictionary.
        filename: The config filename (e.g., "api.yaml") for conditional logic.

    Returns:
        Modified config dict.
    """
    # Add version tracking if not present
    config["_version"] = "0.3.0"

    return config
