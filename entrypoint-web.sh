#!/bin/bash
set -e

cd /src/web_terminal_client

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
    rm -f package-lock.json
    npm install --no-fund --no-audit
    echo "Frontend dependencies installed."
fi

exec "$@"

