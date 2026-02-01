#!/bin/bash
# =============================================================================
# Test Entrypoint Script
# =============================================================================
#
# This script injects test-only sudoers rules at container runtime.
# It is used ONLY when running tests via docker-compose.test.yml.
#
# The test sudoers file is mounted at /config/test/sudoers-test and
# installed to /etc/sudoers.d/ag3ntum-test with proper permissions.
#
# This approach ensures:
# - Production image has NO test vulnerabilities baked in
# - Test permissions are only active during test runs
# - Same Docker image is used for both prod and test
#
# =============================================================================

set -e

TEST_SUDOERS_SRC="/config/test/sudoers-test"
TEST_SUDOERS_DST="/etc/sudoers.d/ag3ntum-test"

# Determine if we're running as root
RUNNING_AS_ROOT=0
if [ "$(id -u)" = "0" ]; then
    RUNNING_AS_ROOT=1
fi

# Check if test sudoers file is mounted
if [ -f "${TEST_SUDOERS_SRC}" ]; then
    echo "=============================================="
    echo "[TEST MODE] Installing test sudoers rules..."
    echo "=============================================="
    echo ""
    echo "WARNING: Test sudoers rules grant elevated privileges!"
    echo "         These should NEVER be used in production."
    echo ""

    # Install sudoers file with correct permissions
    # When running as root, no sudo needed
    if [ "${RUNNING_AS_ROOT}" = "1" ]; then
        cp "${TEST_SUDOERS_SRC}" "${TEST_SUDOERS_DST}"
        chmod 440 "${TEST_SUDOERS_DST}"
        chown root:root "${TEST_SUDOERS_DST}"
    else
        sudo cp "${TEST_SUDOERS_SRC}" "${TEST_SUDOERS_DST}"
        sudo chmod 440 "${TEST_SUDOERS_DST}"
        sudo chown root:root "${TEST_SUDOERS_DST}"
    fi

    echo "[TEST MODE] Test sudoers installed successfully"
    echo "[TEST MODE] Location: ${TEST_SUDOERS_DST}"
    echo ""
else
    echo "[TEST MODE] No test sudoers found at ${TEST_SUDOERS_SRC}"
    echo "[TEST MODE] Running without additional test permissions"
fi

# Drop privileges to ag3ntum_api if running as root
if [ "${RUNNING_AS_ROOT}" = "1" ]; then
    echo "[TEST MODE] Dropping privileges to ag3ntum_api (UID 45045)..."
    exec su -s /bin/bash ag3ntum_api -c "exec $*"
else
    # Already running as ag3ntum_api
    exec "$@"
fi
