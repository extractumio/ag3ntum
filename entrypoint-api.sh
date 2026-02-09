#!/bin/bash
set -e

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
