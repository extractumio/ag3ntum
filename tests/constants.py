"""
Global test constants for pre-built test users.

These users are created by entrypoint-test.sh at container startup with full
Linux accounts, DB entries, venvs, persistent storage, and shared GID memberships.
UIDs are at the high end of the isolated range (50000-60000) to avoid conflicts
with real users allocated sequentially from 50000.

Import these in any test file instead of redefining locally:

    from tests.constants import PREBUILT_USER_A_USERNAME, PREBUILT_USER_A_UID
"""

# --- Pre-built test user A ---
PREBUILT_USER_A_USERNAME = "ag3ntum_tester_a"
PREBUILT_USER_A_UID = 59990
PREBUILT_USER_A_EMAIL = "ag3ntum_tester_a@test.local"
PREBUILT_USER_A_PASSWORD = "TestPassword123!"

# --- Pre-built test user B ---
PREBUILT_USER_B_USERNAME = "ag3ntum_tester_b"
PREBUILT_USER_B_UID = 59991
PREBUILT_USER_B_EMAIL = "ag3ntum_tester_b@test.local"
PREBUILT_USER_B_PASSWORD = "TestPassword123!"
