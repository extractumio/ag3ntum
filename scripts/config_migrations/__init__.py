"""
Config migration discovery and chaining.

Migration files follow the naming convention:
    migrate_{from_version}_to_{to_version}.py

where versions use underscores instead of dots (e.g., 0_2_0 for 0.2.0).

Each migration file must define a function:
    def migrate(config: dict, filename: str) -> dict:
        '''Apply migration to a config dict. Return modified dict.'''
"""
import importlib
import re
from pathlib import Path
from typing import Callable

# Type for migration function: (config_dict, filename) -> config_dict
MigrationFn = Callable[[dict, str], dict]


def _version_to_str(underscored: str) -> str:
    """Convert '0_2_0' to '0.2.0'."""
    return underscored.replace("_", ".")


def _version_to_underscore(dotted: str) -> str:
    """Convert '0.2.0' to '0_2_0'."""
    return dotted.replace(".", "_")


def discover_migrations(
    from_version: str, to_version: str
) -> list[tuple[str, str, MigrationFn]]:
    """
    Discover and chain migration functions from from_version to to_version.

    Returns list of (from_ver, to_ver, migrate_fn) tuples in order.
    """
    migrations_dir = Path(__file__).parent
    pattern = re.compile(r"^migrate_(\d+_\d+_\d+)_to_(\d+_\d+_\d+)\.py$")

    # Discover all available migrations
    available = {}  # from_version -> (to_version, module_name)
    for f in sorted(migrations_dir.iterdir()):
        m = pattern.match(f.name)
        if m:
            fv = _version_to_str(m.group(1))
            tv = _version_to_str(m.group(2))
            if fv in available:
                existing_tv, existing_mod = available[fv]
                raise ValueError(
                    f"Duplicate migration from {fv}: "
                    f"{existing_mod} -> {existing_tv} and "
                    f"{f.stem} -> {tv}"
                )
            available[fv] = (tv, f.stem)

    # Chain from from_version to to_version
    chain = []
    current = from_version
    visited = set()

    while current != to_version and current in available:
        if current in visited:
            raise ValueError(f"Circular migration detected at {current}")
        visited.add(current)

        next_ver, module_name = available[current]
        mod = importlib.import_module(f".{module_name}", package=__package__)
        if not hasattr(mod, "migrate"):
            raise AttributeError(
                f"Migration {module_name} missing migrate() function"
            )
        chain.append((current, next_ver, mod.migrate))
        current = next_ver

    return chain
