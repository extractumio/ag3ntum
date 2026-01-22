#!/bin/bash
#
# Fix User Directory Permissions
# ==============================
#
# This script fixes directory permissions for existing users to match
# the tiered permission model.
#
# Permission Model:
# - /users/{user}/              mode 711 (traverse only)
# - /users/{user}/venv/         mode 755 (world-readable)
# - /users/{user}/sessions/     mode 770, group=ag3ntum (API access)
# - /users/{user}/ag3ntum/      mode 700 (private)
# - /users/{user}/.claude/      mode 700 (private)
#
# Usage:
#   sudo ./fix_user_permissions.sh [username]
#
# If no username is provided, fixes all users in /users/

set -e

USERS_DIR="/users"

fix_user_permissions() {
    local username="$1"
    local user_home="${USERS_DIR}/${username}"

    if [ ! -d "$user_home" ]; then
        echo "Error: User home directory does not exist: $user_home"
        return 1
    fi

    echo "Fixing permissions for user: $username"

    # Get user's UID
    local uid
    uid=$(id -u "$username" 2>/dev/null) || {
        echo "Warning: Linux user '$username' does not exist. Skipping ownership changes."
        uid=""
    }

    # Create ag3ntum group if it doesn't exist
    if ! getent group ag3ntum >/dev/null 2>&1; then
        echo "Creating ag3ntum group..."
        groupadd ag3ntum
    fi

    # Add ag3ntum_api user to ag3ntum group if not already
    if id ag3ntum_api >/dev/null 2>&1; then
        if ! id -nG ag3ntum_api | grep -qw ag3ntum; then
            echo "Adding ag3ntum_api to ag3ntum group..."
            usermod -a -G ag3ntum ag3ntum_api
        fi
    fi

    # TIER 1: Public paths - API can validate
    echo "  Setting home directory to mode 711..."
    chmod 711 "$user_home"

    # venv should be world-readable
    if [ -d "$user_home/venv" ]; then
        echo "  Setting venv to mode 755..."
        chmod -R 755 "$user_home/venv"
    fi

    # TIER 2: Operational paths - API + User via group
    if [ -d "$user_home/sessions" ]; then
        echo "  Setting sessions to mode 770 with group ag3ntum..."
        chmod 770 "$user_home/sessions"
        chgrp ag3ntum "$user_home/sessions"
    fi

    # TIER 3: Private paths - User only
    if [ -d "$user_home/ag3ntum" ]; then
        echo "  Setting ag3ntum to mode 700..."
        chmod 700 "$user_home/ag3ntum"
    fi

    if [ -d "$user_home/.claude" ]; then
        echo "  Setting .claude to mode 700..."
        chmod 700 "$user_home/.claude"
    fi

    # Set ownership if UID is known
    if [ -n "$uid" ]; then
        echo "  Setting ownership to $uid:$uid..."
        chown "$uid:$uid" "$user_home"

        if [ -d "$user_home/venv" ]; then
            chown -R "$uid:$uid" "$user_home/venv"
        fi

        if [ -d "$user_home/sessions" ]; then
            chown "$uid:ag3ntum" "$user_home/sessions"
        fi

        if [ -d "$user_home/ag3ntum" ]; then
            chown -R "$uid:$uid" "$user_home/ag3ntum"
        fi

        if [ -d "$user_home/.claude" ]; then
            chown -R "$uid:$uid" "$user_home/.claude"
        fi
    fi

    echo "  Done fixing permissions for $username"
}

# Main
if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root (use sudo)"
    exit 1
fi

if [ -n "$1" ]; then
    # Fix specific user
    fix_user_permissions "$1"
else
    # Fix all users
    echo "Fixing permissions for all users in $USERS_DIR..."

    for user_dir in "$USERS_DIR"/*/; do
        if [ -d "$user_dir" ]; then
            username=$(basename "$user_dir")
            fix_user_permissions "$username" || true
        fi
    done
fi

echo ""
echo "Permission fix complete!"
echo ""
echo "Remember to restart the API server for changes to take effect:"
echo "  docker restart ag3ntum-ag3ntum-api-1"
