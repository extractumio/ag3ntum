#!/bin/bash
set -e

# =============================================================================
# Web Container Entrypoint (mode-aware)
# =============================================================================
#
# Production mode (AG3NTUM_MODE=prod):
#   Skips npm install entirely. The pre-built bundle at /web_dist is served
#   by uvicorn+starlette (src/web_frontend_server.py). Fast startup.
#
# Development mode (AG3NTUM_MODE=dev):
#   Installs npm dependencies, copies Vite configs to writable dir, then
#   runs the Vite dev server with HMR for frontend development.
# =============================================================================

# --- Production mode: skip npm install, just drop privileges ---
if [ "${AG3NTUM_MODE}" = "prod" ]; then
    exec setpriv --reuid=45045 --regid=45045 --init-groups \
        --inh-caps=+setgid --ambient-caps=+setgid -- "$@"
fi

# --- Development mode: full setup ---

# Ensure /app/node_modules directory is writable by ag3ntum_api
# Named Docker volumes are created with root ownership by default
chown -R 45045:45045 /app/node_modules

# Copy package.json from source to /app/ so npm install writes to /app/node_modules
cp /src/web_terminal_client/package.json /app/package.json

# Copy Vite/vitest configs to a writable directory so Vite can create its
# temp files (.timestamp-*.mjs) there. Source tree is mounted read-only.
# A node_modules symlink lets the ESM resolver find packages from this location.
# Port-qualified path supports multiple instances on the same host.
VITE_CONFIG_DIR="/tmp/vite-${AG3NTUM_WEB_PORT:-50080}"
mkdir -p "$VITE_CONFIG_DIR"
cp /src/web_terminal_client/vite.config.mjs "$VITE_CONFIG_DIR/"
cp /src/web_terminal_client/vitest.config.mjs "$VITE_CONFIG_DIR/"
cp /src/web_terminal_client/vite.shared.mjs "$VITE_CONFIG_DIR/"
ln -sf /app/node_modules "$VITE_CONFIG_DIR/node_modules"
chown -R 45045:45045 "$VITE_CONFIG_DIR"

# Check if node_modules needs (re)installation
# Reinstall if: missing, empty, or missing platform-specific rollup binary
NEEDS_INSTALL=0

if [ ! -d "/app/node_modules" ] || [ -z "$(ls -A /app/node_modules 2>/dev/null)" ]; then
    NEEDS_INSTALL=1
    echo "node_modules missing or empty"
elif [ ! -d "/app/node_modules/@rollup" ]; then
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

    if [ ! -d "/app/node_modules/@rollup/rollup-${ROLLUP_PLATFORM}" ]; then
        NEEDS_INSTALL=1
        echo "Platform-specific rollup binary missing (@rollup/rollup-${ROLLUP_PLATFORM})"
    fi
fi

if [ "$NEEDS_INSTALL" = "1" ]; then
    echo "Installing frontend dependencies..."
    # Clear node_modules contents (can't remove the directory itself if it's a volume mount)
    rm -rf /app/node_modules/* /app/node_modules/.[!.]* 2>/dev/null || true
    # Ensure npm cache is owned by ag3ntum_api (defense-in-depth: if a previous
    # docker compose exec ran npm as root, the cache has root-owned files)
    chown -R 45045:45045 /tmp/.npm 2>/dev/null || true
    # Run npm as ag3ntum_api from /app/ (--no-package-lock avoids writing to the bind-mounted source tree)
    # Subshell prevents 'cd /app' from leaking into the parent — vite needs cwd to stay at working_dir
    (cd /app && setpriv --reuid=45045 --regid=45045 --init-groups --inh-caps=+setgid --ambient-caps=+setgid -- npm install --no-fund --no-audit --no-package-lock --legacy-peer-deps)
    echo "Frontend dependencies installed."
fi

# Drop to ag3ntum_api for the main process
exec setpriv --reuid=45045 --regid=45045 --init-groups --inh-caps=+setgid --ambient-caps=+setgid -- "$@"
