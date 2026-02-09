#!/bin/bash
# =============================================================================
# Test Entrypoint Script
# =============================================================================
#
# This script sets up the test environment at container runtime.
# It is used ONLY when running tests via docker-compose.test.yml.
#
# Steps:
# 1. Install test-only sudoers rules (elevated test permissions)
# 2. Sync Linux users from database (same as entrypoint-api.sh)
# 3. Create fully-equipped test users (UIDs 59990, 59991) with DB entries, venvs, permissions
# 4. Drop privileges to ag3ntum_api via setpriv --init-groups
#
# This approach ensures:
# - Production image has NO test vulnerabilities baked in
# - Test permissions are only active during test runs
# - Same Docker image is used for both prod and test
# - Test users are available for permission/isolation tests
#
# =============================================================================

set -e

TEST_SUDOERS_SRC="/config/test/sudoers-test"
TEST_SUDOERS_DST="/etc/sudoers.d/ag3ntum-test"

# ---- Step 1: Install test sudoers ----

if [ -f "${TEST_SUDOERS_SRC}" ]; then
    echo "=============================================="
    echo "[TEST MODE] Installing test sudoers rules..."
    echo "=============================================="
    echo ""
    echo "WARNING: Test sudoers rules grant elevated privileges!"
    echo "         These should NEVER be used in production."
    echo ""

    cp "${TEST_SUDOERS_SRC}" "${TEST_SUDOERS_DST}"
    chmod 440 "${TEST_SUDOERS_DST}"
    chown root:root "${TEST_SUDOERS_DST}"

    echo "[TEST MODE] Test sudoers installed successfully"
    echo "[TEST MODE] Location: ${TEST_SUDOERS_DST}"
    echo ""
else
    echo "[TEST MODE] No test sudoers found at ${TEST_SUDOERS_SRC}"
    echo "[TEST MODE] Running without additional test permissions"
fi

# ---- Step 2: Sync Linux users from database ----
# Same logic as entrypoint-api.sh — recreates ephemeral Linux accounts
# from the persistent database so real users work in test mode too.

python3 /scripts/sync_linux_users.py --prefix "[TEST MODE] "

# ---- Step 3: Create fully-equipped test users ----
# These users are complete test accounts with Linux users, DB entries,
# Python venvs, and proper permissions. Tests can rely on these accounts
# existing and being ready for API authentication and agent execution.
#
# - ag3ntum_tester_a: UID/GID 59990
# - ag3ntum_tester_b: UID/GID 59991
#
# UIDs are at the high end of the isolated range (50000-60000) to avoid
# conflicts with real users allocated sequentially from 50000.
#
# Credentials (for test authentication):
#   Email: ag3ntum_tester_a@test.local / ag3ntum_tester_b@test.local
#   Password: TestPassword123!
#
# Group memberships (shared GID model):
# - Test users added to ag3ntum group (for home dir access)
# - ag3ntum_api added to test users' groups (for 660/770 file access)
#
# This step is test/dev-only — entrypoint-test.sh is never used in production
# (production uses entrypoint-api.sh via docker-compose.yml).

echo "[TEST MODE] Creating fully-equipped test users..."

for user_info in "ag3ntum_tester_a:59990" "ag3ntum_tester_b:59991"; do
    username="${user_info%%:*}"
    uid="${user_info##*:}"

    # ---- 3a. Create Linux user (idempotent — returns 9 if exists) ----
    useradd -M -d "/users/${username}" -s /bin/bash -u "${uid}" "${username}" 2>/dev/null || true

    # Add test user to ag3ntum group
    usermod -a -G ag3ntum "${username}" 2>/dev/null || true

    # Add ag3ntum_api to test user's primary group (shared GID)
    usermod -a -G "${username}" ag3ntum_api 2>/dev/null || true

    # ---- 3b. Create directory structure (matching user_service._create_linux_user) ----
    home_dir="/users/${username}"
    mkdir -p "${home_dir}/sessions" \
             "${home_dir}/ag3ntum/persistent" \
             "${home_dir}/.claude/skills"

    # Create persistent storage README if missing
    readme="${home_dir}/ag3ntum/persistent/README.md"
    if [ ! -f "${readme}" ]; then
        cat > "${readme}" << 'HEREDOC'
# Persistent Storage

Files in this directory persist across sessions.

## Access from Agent Sessions
```
./persistent/  OR  /persistent/
```
HEREDOC
    fi

    # ---- 3c. Create Python venv (cached on persistent volume) ----
    venv_dir="${home_dir}/venv"
    if [ ! -f "${venv_dir}/bin/python3" ]; then
        echo "[TEST MODE] Creating venv for ${username}..."
        python3 -m venv "${venv_dir}"
        # Copy default requirements if available
        if [ -f "/config/user_requirements.txt" ]; then
            cp /config/user_requirements.txt "${home_dir}/requirements.txt"
        fi
    fi

    # ---- 3d. Create secrets.yaml if missing ----
    secrets_file="${home_dir}/ag3ntum/secrets.yaml"
    if [ ! -f "${secrets_file}" ]; then
        cat > "${secrets_file}" << 'HEREDOC'
# Test user secrets — test/dev only
sandboxed_envs:
  GEMINI_API_KEY: ""
  OPENAI_API_KEY: ""
  ANTHROPIC_API_KEY: ""
HEREDOC
    fi

    # ---- 3e. Set ownership and permissions (matching user_service) ----
    # Transfer everything to the user first
    chown -R "${uid}:${uid}" "${home_dir}"

    # Venv: 755 (executable by sandbox)
    if [ -d "${venv_dir}" ]; then
        chmod -R 755 "${venv_dir}"
    fi

    # Home dir: 750 (owner rwx, ag3ntum group rx)
    chgrp ag3ntum "${home_dir}"
    chmod 750 "${home_dir}"

    # .claude and sessions: 770 (ag3ntum group rwx — API creates session dirs & writes skills)
    for subdir in ".claude" "sessions"; do
        if [ -d "${home_dir}/${subdir}" ]; then
            chgrp -R ag3ntum "${home_dir}/${subdir}"
            chmod -R 770 "${home_dir}/${subdir}"
        fi
    done

    # ag3ntum dir: 750 (group traverse only, so secrets.yaml stays protected)
    chgrp ag3ntum "${home_dir}/ag3ntum"
    chmod 750 "${home_dir}/ag3ntum"

    # persistent dir: 770 (ag3ntum group rwx for API writes)
    chgrp -R ag3ntum "${home_dir}/ag3ntum/persistent"
    chmod 770 "${home_dir}/ag3ntum/persistent"

    # secrets.yaml: 600 (owner only)
    chmod 600 "${secrets_file}"

    echo "[TEST MODE]   ${username} (UID ${uid}) — Linux user + dirs + venv + permissions"
done

# ---- 3f. Create database entries for test users ----
# Uses the app venv for bcrypt. Idempotent (skips if user already exists).
/opt/venv/bin/python3 -c "
import sqlite3, uuid, secrets, sys, os
from datetime import datetime, timezone

try:
    import bcrypt
except ImportError:
    print('[TEST MODE] ERROR: bcrypt not available in /opt/venv — cannot create test DB entries', file=sys.stderr)
    sys.exit(1)

db_path = '/data/ag3ntum.db'
if not os.path.exists(db_path):
    print('[TEST MODE] WARNING: Database not found at /data/ag3ntum.db — skipping DB entries')
    sys.exit(0)

password = b'TestPassword123!'
password_hash = bcrypt.hashpw(password, bcrypt.gensalt()).decode()

test_users = [
    ('ag3ntum_tester_a', 'ag3ntum_tester_a@test.local', 59990),
    ('ag3ntum_tester_b', 'ag3ntum_tester_b@test.local', 59991),
]

conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).isoformat()
created = 0

for username, email, linux_uid in test_users:
    # Check if user already exists (by username)
    row = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    if row:
        continue

    # Also check if email is taken (shouldn't be, but be safe)
    row = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
    if row:
        continue

    user_id = str(uuid.uuid4())
    jwt_secret = secrets.token_urlsafe(32)

    conn.execute(
        '''INSERT INTO users (id, username, email, password_hash, role, jwt_secret, linux_uid, is_active, queue_priority, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (user_id, username, email, password_hash, 'user', jwt_secret, linux_uid, 1, 0, now, now)
    )
    created += 1

conn.commit()
conn.close()
print(f'[TEST MODE] Test user DB entries: {created} created, {len(test_users) - created} existing')
"

echo "[TEST MODE] Test users ready: ag3ntum_tester_a (59990), ag3ntum_tester_b (59991)"
echo ""

# ---- Step 4: Drop privileges to ag3ntum_api ----
# Use setpriv --init-groups to refresh supplementary groups from /etc/group.
# This ensures the API process inherits the shared GID memberships set above.

exec setpriv --reuid=45045 --regid=45045 --init-groups \
    --inh-caps=+setgid --ambient-caps=+setgid -- "$@"
