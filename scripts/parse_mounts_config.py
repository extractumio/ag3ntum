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

    # Validate dynamic mount bases
    dynamic = config.get('dynamic', {})
    if dynamic:
        bases = dynamic.get('bases', [])
        if not isinstance(bases, list):
            errors.append("dynamic.bases must be a list")
        else:
            for i, base in enumerate(bases):
                if not isinstance(base, dict):
                    errors.append(f"dynamic.bases[{i}] must be a dict")
                    continue
                if not base.get('name'):
                    errors.append(f"dynamic.bases[{i}] missing 'name'")
                elif not validate_mount_name(base['name']):
                    errors.append(f"dynamic.bases[{i}] invalid name: {base.get('name')}")
                if not base.get('host_path'):
                    errors.append(f"dynamic.bases[{i}] missing 'host_path'")
                # Validate max_mode
                max_mode = base.get('max_mode', 'ro')
                if max_mode not in ('ro', 'rw'):
                    errors.append(f"dynamic.bases[{i}] invalid max_mode: {max_mode}")
                # Validate authorization
                auth = base.get('authorization', {})
                auth_mode = auth.get('mode', 'allowlist')
                if auth_mode not in ('allowlist', 'role', 'self_only'):
                    errors.append(f"dynamic.bases[{i}] invalid authorization.mode: {auth_mode}")
                if auth_mode == 'allowlist' and not auth.get('allowed_users'):
                    errors.append(f"dynamic.bases[{i}] allowlist mode requires 'allowed_users'")
                # Validate subpath_restrictions
                subpath_res = base.get('subpath_restrictions', {})
                subpath_mode = subpath_res.get('mode', 'blocklist')
                if subpath_mode not in ('allowlist', 'blocklist'):
                    errors.append(f"dynamic.bases[{i}] invalid subpath_restrictions.mode: {subpath_mode}")

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


def get_dynamic_bases(config: dict) -> dict:
    """Extract dynamic mount base configuration."""
    dynamic = config.get('dynamic', {})
    result = {
        'enabled': dynamic.get('enabled', False),
        'security': dynamic.get('security', {}),
        'bases': [],
    }

    if not result['enabled']:
        return result

    bases = dynamic.get('bases', [])
    if isinstance(bases, list):
        for base in bases:
            if isinstance(base, dict) and base.get('name') and base.get('host_path'):
                path = Path(base['host_path'])
                optional = base.get('optional', True)

                # Check if path exists (for non-optional bases without {username} placeholder)
                has_placeholder = '{username}' in base['host_path']
                if not has_placeholder and not path.exists() and not optional:
                    print(f"ERROR: Required dynamic base path does not exist: {path}", file=sys.stderr)
                    sys.exit(1)

                result['bases'].append({
                    'name': base['name'],
                    'host_path': base['host_path'],
                    'description': base.get('description', ''),
                    'max_mode': base.get('max_mode', 'ro'),
                    'authorization': base.get('authorization', {'mode': 'allowlist', 'allowed_users': []}),
                    'subpath_restrictions': base.get('subpath_restrictions', {'mode': 'blocklist', 'blocked': []}),
                    'optional': optional,
                })

    return result


def output_bash(global_mounts: dict, per_user_mounts: dict, dynamic_bases: dict = None) -> None:
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

    # Output dynamic mount bases (if enabled)
    if dynamic_bases and dynamic_bases.get('enabled'):
        for base in dynamic_bases.get('bases', []):
            max_mode = base.get('max_mode', 'ro')
            # Dynamic bases are mounted based on their max_mode
            # rw mode means the user CAN request rw, so we mount as rw
            # ro mode means read-only only
            mount_mode = 'rw' if max_mode == 'rw' else 'ro'
            print(f"MOUNT_DYNAMIC:{base['host_path']}:{base['name']}:{mount_mode}")


def output_json(global_mounts: dict, per_user_mounts: dict, dynamic_bases: dict = None) -> None:
    """Output in JSON format."""
    result = {
        'global': global_mounts,
        'per_user': per_user_mounts,
    }
    if dynamic_bases:
        result['dynamic'] = dynamic_bases
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
    dynamic_bases = get_dynamic_bases(config)

    # Output
    if args.mounts_json or args.per_user_json:
        output_json(global_mounts, per_user_mounts, dynamic_bases)
    else:
        output_bash(global_mounts, per_user_mounts, dynamic_bases)


if __name__ == '__main__':
    main()
