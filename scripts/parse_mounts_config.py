#!/usr/bin/env python3
"""
Parse external-mounts.yaml configuration and output mount specs.

Used by run.sh to read mount configuration from YAML instead of CLI args.

Output formats:
  --mounts-json: JSON output for complex parsing
  --mounts-bash: Bash-compatible output (default)
  --validate-only: Just validate, don't output
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml


def validate_mount_name(name: str) -> bool:
    """Validate mount name is safe."""
    if not name:
        return False
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return False
    if len(name) > 64:
        return False
    reserved = ['persistent', 'ro', 'rw', 'external']
    if name.lower() in reserved:
        return False
    return True


def validate_mount_config(config: dict) -> list[str]:
    """Validate mount configuration, return list of errors."""
    errors = []

    # Validate global mounts
    global_mounts = config.get('global', {})
    for mode in ['ro', 'rw']:
        mounts = global_mounts.get(mode, [])
        if not isinstance(mounts, list):
            errors.append(f"global.{mode} must be a list")
            continue
        for i, mount in enumerate(mounts):
            if not isinstance(mount, dict):
                errors.append(f"global.{mode}[{i}] must be a dict")
                continue
            if not mount.get('name'):
                errors.append(f"global.{mode}[{i}] missing 'name'")
            elif not validate_mount_name(mount['name']):
                errors.append(f"global.{mode}[{i}] invalid name: {mount.get('name')}")
            if not mount.get('host_path'):
                errors.append(f"global.{mode}[{i}] missing 'host_path'")

    # Validate per-user mounts
    per_user = config.get('per_user', {})
    for mode in ['ro', 'rw']:
        mounts = per_user.get(mode, [])
        if not isinstance(mounts, list):
            errors.append(f"per_user.{mode} must be a list")
            continue
        for i, mount in enumerate(mounts):
            if not isinstance(mount, dict):
                errors.append(f"per_user.{mode}[{i}] must be a dict")
                continue
            if not mount.get('name'):
                errors.append(f"per_user.{mode}[{i}] missing 'name'")
            elif not validate_mount_name(mount['name']):
                errors.append(f"per_user.{mode}[{i}] invalid name: {mount.get('name')}")
            if not mount.get('host_path'):
                errors.append(f"per_user.{mode}[{i}] missing 'host_path'")
            if not mount.get('users'):
                errors.append(f"per_user.{mode}[{i}] missing 'users' list")

    return errors


def get_global_mounts(config: dict) -> dict:
    """Extract global mounts (for Docker volume mounts)."""
    result = {'ro': [], 'rw': []}

    global_mounts = config.get('global', {})
    for mode in ['ro', 'rw']:
        mounts = global_mounts.get(mode, [])
        if isinstance(mounts, list):
            for mount in mounts:
                if isinstance(mount, dict) and mount.get('name') and mount.get('host_path'):
                    path = Path(mount['host_path'])
                    optional = mount.get('optional', False)

                    # Check if path exists (for non-optional mounts)
                    if not path.exists() and not optional:
                        print(f"ERROR: Required mount path does not exist: {path}", file=sys.stderr)
                        sys.exit(1)

                    if path.exists() or optional:
                        result[mode].append({
                            'name': mount['name'],
                            'host_path': str(path.resolve()) if path.exists() else str(path),
                            'description': mount.get('description', ''),
                            'optional': optional,
                        })

    return result


def get_per_user_mounts(config: dict) -> dict:
    """Extract per-user mount configuration."""
    result = {'ro': [], 'rw': []}

    per_user = config.get('per_user', {})
    for mode in ['ro', 'rw']:
        mounts = per_user.get(mode, [])
        if isinstance(mounts, list):
            for mount in mounts:
                if isinstance(mount, dict) and mount.get('name') and mount.get('host_path'):
                    result[mode].append({
                        'name': mount['name'],
                        'host_path': mount['host_path'],  # Keep placeholder
                        'description': mount.get('description', ''),
                        'users': mount.get('users', []),
                        'optional': mount.get('optional', True),
                    })

    return result


def output_bash(global_mounts: dict, per_user_mounts: dict) -> None:
    """Output in bash-compatible format."""
    # Output global RO mounts
    for mount in global_mounts['ro']:
        print(f"MOUNT_RO:{mount['host_path']}:{mount['name']}")

    # Output global RW mounts
    for mount in global_mounts['rw']:
        print(f"MOUNT_RW:{mount['host_path']}:{mount['name']}")

    # Output per-user RO mounts (mount them like global mounts for Docker access)
    for mount in per_user_mounts['ro']:
        # Only output if the path exists (validation happens in run.sh)
        print(f"MOUNT_USER_RO:{mount['host_path']}:{mount['name']}")

    # Output per-user RW mounts
    for mount in per_user_mounts['rw']:
        print(f"MOUNT_USER_RW:{mount['host_path']}:{mount['name']}")


def output_json(global_mounts: dict, per_user_mounts: dict) -> None:
    """Output in JSON format."""
    result = {
        'global': global_mounts,
        'per_user': per_user_mounts,
    }
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description='Parse external mounts configuration')
    parser.add_argument(
        '--config', '-c',
        default='config/external-mounts.yaml',
        help='Path to external-mounts.yaml'
    )
    parser.add_argument(
        '--mounts-json',
        action='store_true',
        help='Output in JSON format'
    )
    parser.add_argument(
        '--mounts-bash',
        action='store_true',
        help='Output in bash-compatible format (default)'
    )
    parser.add_argument(
        '--validate-only',
        action='store_true',
        help='Only validate configuration'
    )
    parser.add_argument(
        '--per-user-json',
        action='store_true',
        help='Output per-user mounts in JSON format'
    )

    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        # No config file is OK - just means no YAML-based mounts
        if args.validate_only:
            print("No config file found (OK)", file=sys.stderr)
        sys.exit(0)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"ERROR: Failed to parse {config_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate
    errors = validate_mount_config(config)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

    if args.validate_only:
        print("Configuration is valid", file=sys.stderr)
        sys.exit(0)

    # Extract mounts
    global_mounts = get_global_mounts(config)
    per_user_mounts = get_per_user_mounts(config)

    # Output
    if args.mounts_json or args.per_user_json:
        output_json(global_mounts, per_user_mounts)
    else:
        output_bash(global_mounts, per_user_mounts)


if __name__ == '__main__':
    main()
