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

# Check if test sudoers file is mounted
if [ -f "${TEST_SUDOERS_SRC}" ]; then
    echo "=============================================="
    echo "[TEST MODE] Installing test sudoers rules..."
    echo "=============================================="
    echo ""
    echo "WARNING: Test sudoers rules grant elevated privileges!"
    echo "         These should NEVER be used in production."
    echo ""

    # Validate sudoers syntax before installing
    # visudo -c checks syntax; use full path as /usr/sbin may not be in PATH
    # Try without sudo first, fall back to sudo if needed
    SYNTAX_OK=0
    if /usr/sbin/visudo -c -f "${TEST_SUDOERS_SRC}" >/dev/null 2>&1; then
        SYNTAX_OK=1
    elif sudo /usr/sbin/visudo -c -f "${TEST_SUDOERS_SRC}" >/dev/null 2>&1; then
        SYNTAX_OK=1
    fi

    if [ "${SYNTAX_OK}" = "1" ]; then
        # Copy with secure permissions
        sudo cp "${TEST_SUDOERS_SRC}" "${TEST_SUDOERS_DST}"
        sudo chmod 440 "${TEST_SUDOERS_DST}"
        sudo chown root:root "${TEST_SUDOERS_DST}"

        echo "[TEST MODE] Test sudoers installed successfully"
        echo "[TEST MODE] Location: ${TEST_SUDOERS_DST}"
        echo ""
    else
        echo "[ERROR] Invalid sudoers syntax in ${TEST_SUDOERS_SRC}"
        echo "[ERROR] Validate with: visudo -c -f ${TEST_SUDOERS_SRC}"
        exit 1
    fi
else
    echo "[TEST MODE] No test sudoers found at ${TEST_SUDOERS_SRC}"
    echo "[TEST MODE] Running without additional test permissions"
fi

# Execute the original command passed to the container
exec "$@"
