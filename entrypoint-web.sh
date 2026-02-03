#!/bin/bash
set -e

cd /src/web_terminal_client

# Ensure node_modules directory exists and is writable by ag3ntum_api
# Named Docker volumes are created with root ownership by default
if [ ! -d "node_modules" ]; then
    echo "Creating node_modules directory..."
    mkdir -p node_modules
fi

# Fix ownership (running as root, target user is ag3ntum_api 45045)
chown -R 45045:45045 /src/web_terminal_client/node_modules

# Check if node_modules needs (re)installation
# Reinstall if: missing, empty, or missing platform-specific rollup binary
NEEDS_INSTALL=0

if [ ! -d "node_modules" ] || [ -z "$(ls -A node_modules 2>/dev/null)" ]; then
    NEEDS_INSTALL=1
    echo "node_modules missing or empty"
elif [ ! -d "node_modules/@rollup" ]; then
    NEEDS_INSTALL=1
    echo "rollup modules missing"
else
    # Check for platform-specific rollup binary (linux-arm64 or linux-x64)
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        ROLLUP_PLATFORM="linux-arm64-gnu"
    else
        ROLLUP_PLATFORM="linux-x64-gnu"
    fi

    if [ ! -d "node_modules/@rollup/rollup-${ROLLUP_PLATFORM}" ]; then
        NEEDS_INSTALL=1
        echo "Platform-specific rollup binary missing (@rollup/rollup-${ROLLUP_PLATFORM})"
    fi
fi

if [ "$NEEDS_INSTALL" = "1" ]; then
    echo "Installing frontend dependencies..."
    # Clear node_modules contents (can't remove the directory itself if it's a volume mount)
    rm -rf node_modules/* node_modules/.[!.]* 2>/dev/null || true
    # Run npm as ag3ntum_api (--no-package-lock avoids writing to the bind-mounted source tree)
    setpriv --reuid=45045 --regid=45045 --init-groups --inh-caps=+setgid --ambient-caps=+setgid -- npm install --no-fund --no-audit --no-package-lock
    echo "Frontend dependencies installed."
fi

# Drop to ag3ntum_api for the main process
exec setpriv --reuid=45045 --regid=45045 --init-groups --inh-caps=+setgid --ambient-caps=+setgid -- "$@"
