#!/usr/bin/env python3
"""
Config migration runner for Ag3ntum.

Migrates config files between versions using discovered migration scripts.

Usage:
    python3 scripts/migrate_config.py --from 0.2.0 --to 0.3.0
    python3 scripts/migrate_config.py --from 0.2.0 --to 0.3.0 --dry-run
"""
import argparse
import copy
import shutil
import sys
from pathlib import Path

import yaml

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config_migrations import discover_migrations

# Config files to migrate (relative to config/)
CONFIG_FILES = [
    "api.yaml",
    "agent.yaml",
    "external-mounts.yaml",
    "llm-api-proxy.yaml",
    "secrets.yaml",
]

# Keys in secrets.yaml that must never be logged or displayed by migrations
SENSITIVE_KEYS = frozenset({
    "anthropic_api_key",
    "fernet_key",
    "jwt_secret",
})

# Default version for configs that predate the _version tracking system
DEFAULT_VERSION = "0.2.0"


def get_config_version(config_dir: Path) -> str:
    """
    Determine the current config version.

    Priority:
    1. _version key in any config file
    2. .ag3ntum-version file
    3. Default to "0.2.0" (pre-versioning)
    """
    # Check config files for _version
    for fname in CONFIG_FILES:
        fpath = config_dir / fname
        if fpath.exists():
            try:
                with fpath.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict) and "_version" in data:
                    return str(data["_version"])
            except Exception:
                continue

    # Check .ag3ntum-version
    version_file = config_dir.parent / ".ag3ntum-version"
    if version_file.exists():
        return version_file.read_text().strip()

    return DEFAULT_VERSION


def backup_config(fpath: Path, version: str) -> Path | None:
    """Create a backup of a config file before migration."""
    if not fpath.exists():
        return None
    backup = fpath.with_suffix(f".pre-{version}.bak")
    shutil.copy2(fpath, backup)
    return backup


def migrate_file(
    fpath: Path,
    migrations: list,
    dry_run: bool = False,
    from_version: str = "",
) -> bool:
    """
    Apply migrations to a single config file.

    Returns True if changes were made.
    """
    if not fpath.exists():
        print(f"  SKIP {fpath.name} (not found)")
        return False

    try:
        with fpath.open("r", encoding="utf-8") as f:
            original_text = f.read()

        config = yaml.safe_load(original_text)
        if not isinstance(config, dict):
            print(f"  SKIP {fpath.name} (not a YAML mapping)")
            return False
    except Exception as e:
        print(f"  ERROR {fpath.name}: {e}")
        return False

    # Apply each migration in chain
    modified = False
    for from_ver, to_ver, migrate_fn in migrations:
        original = copy.deepcopy(config)  # deep copy for nested change detection
        config = migrate_fn(config, fpath.name)
        if config != original:
            modified = True
            if dry_run:
                # Show what changed (without sensitive values)
                for key in set(config.keys()) - set(original.keys()):
                    if key not in SENSITIVE_KEYS:
                        print(f"    + {key}: {config[key]}")
                for key in set(config.keys()) & set(original.keys()):
                    if config[key] != original.get(key) and key not in SENSITIVE_KEYS:
                        print(f"    ~ {key}: {original[key]} -> {config[key]}")

    if modified and not dry_run:
        # Backup before writing
        backup_config(fpath, from_version)

        # Write migrated config preserving comments where possible
        # Since PyYAML strips comments, we write cleanly
        with fpath.open("w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(f"  MIGRATED {fpath.name}")
    elif modified and dry_run:
        print(f"  WOULD MIGRATE {fpath.name}")
    else:
        print(f"  OK {fpath.name} (no changes needed)")

    return modified


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Ag3ntum config files between versions"
    )
    parser.add_argument(
        "--from", dest="from_version", required=True,
        help="Source version (e.g., 0.2.0)"
    )
    parser.add_argument(
        "--to", dest="to_version", required=True,
        help="Target version (e.g., 0.3.0)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without modifying files"
    )
    parser.add_argument(
        "--config-dir", type=Path, default=PROJECT_ROOT / "config",
        help="Config directory (default: config/)"
    )
    args = parser.parse_args()

    print(f"Config migration: {args.from_version} -> {args.to_version}")
    if args.dry_run:
        print("(dry run -- no files will be modified)")
    print()

    # Discover migration chain
    try:
        migrations = discover_migrations(args.from_version, args.to_version)
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    if not migrations:
        if args.from_version == args.to_version:
            print("Already at target version. Nothing to do.")
            return 0
        print(f"No migration path found from {args.from_version} to {args.to_version}")
        return 1

    chain_str = " -> ".join(
        [migrations[0][0]] + [m[1] for m in migrations]
    )
    print(f"Migration chain: {chain_str}")
    print()

    # Migrate each config file
    any_modified = False
    for fname in CONFIG_FILES:
        fpath = args.config_dir / fname
        if migrate_file(fpath, migrations, args.dry_run, args.from_version):
            any_modified = True

    print()
    if args.dry_run:
        if any_modified:
            print("Dry run complete. Run without --dry-run to apply changes.")
        else:
            print("No changes needed.")
    else:
        if any_modified:
            print("Config migration complete.")
        else:
            print("All configs already up to date.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
