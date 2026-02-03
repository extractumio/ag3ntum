#!/bin/bash
set -e

# Ensure Linux users from database exist in the container.
# Linux accounts are ephemeral (lost on container rebuild) but database
# records and files persist on disk. This recreates accounts and sets up
# group memberships (shared GID model) before the API process starts,
# so the process inherits the correct supplementary groups via --init-groups.

python3 -c "
import sqlite3, subprocess, sys, os

db_path = '/data/ag3ntum.db'
if not os.path.exists(db_path):
    sys.exit(0)

conn = sqlite3.connect(db_path)
users = conn.execute(
    'SELECT username, linux_uid FROM users WHERE is_active = 1 AND linux_uid IS NOT NULL'
).fetchall()
conn.close()

created = 0
for username, uid in users:
    # Create Linux user (idempotent — returns 9 if exists)
    r = subprocess.run(
        ['useradd', '-M', '-d', f'/users/{username}',
         '-s', '/bin/bash', '-u', str(uid), username],
        capture_output=True
    )
    if r.returncode not in (0, 9):
        print(f'WARNING: useradd {username} failed: {r.stderr.decode().strip()}', file=sys.stderr)
        continue

    if r.returncode == 0:
        created += 1

    # Add user to ag3ntum group
    subprocess.run(['usermod', '-a', '-G', 'ag3ntum', username],
                   capture_output=True)

    # Add ag3ntum_api to user's primary group (shared GID for 660/770 file access)
    subprocess.run(['usermod', '-a', '-G', username, 'ag3ntum_api'],
                   capture_output=True)

if users:
    print(f'Linux user sync: {len(users)} users ({created} created, {len(users) - created} existing)')
"

# Drop to ag3ntum_api with refreshed supplementary groups.
# --init-groups reads /etc/group to set the correct group list.
exec setpriv --reuid=45045 --regid=45045 --init-groups \
    --inh-caps=+setgid --ambient-caps=+setgid -- "$@"
