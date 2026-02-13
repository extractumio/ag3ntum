#!/bin/bash
set -e

# --- Permission management (single authority for all file ownership) ---
# The container entrypoint is the SOLE authority for file permissions.
# Host scripts (run.sh, install.sh) only do mkdir -p — no chown, no sudo.
# This block runs as root before privilege drop. Idempotent:
#   First start:  host-created dirs are user-owned → chown to 45045
#   Rebuild:      dirs already 45045-owned → no-op
chown 45045:45045 /data /logs 2>/dev/null || true
chown -R 45045:45045 /data /logs 2>/dev/null || true

# /users: only top-level — per-user subdirs keep their own UID ownership
# (managed by sync_linux_users.py and user creation code)
chown 45045:45045 /users 2>/dev/null || true

# Secure secrets config
if [ -f /config/secrets.yaml ]; then
    chown 45045:45045 /config/secrets.yaml
    chmod 600 /config/secrets.yaml
fi

# Ensure Linux users from database exist in the container.
# Linux accounts are ephemeral (lost on container rebuild) but database
# records and files persist on disk. This recreates accounts and sets up
# group memberships (shared GID model) before the API process starts,
# so the process inherits the correct supplementary groups via --init-groups.

python3 /scripts/sync_linux_users.py

# Drop to ag3ntum_api with refreshed supplementary groups.
# --init-groups reads /etc/group to set the correct group list.
exec setpriv --reuid=45045 --regid=45045 --init-groups \
    --inh-caps=+setgid --ambient-caps=+setgid -- "$@"
