#!/usr/bin/env python3
"""
Sync Linux users from the database.

Linux accounts are ephemeral (lost on container rebuild) but database
records and files persist on disk. This recreates accounts and sets up
group memberships (shared GID model) so the API process inherits the
correct supplementary groups via setpriv --init-groups.

Called by both entrypoint-api.sh and entrypoint-test.sh.
"""
import os
import sqlite3
import subprocess
import sys

DB_PATH = "/data/ag3ntum.db"
LOG_PREFIX = ""


def sync_users(prefix: str = "") -> None:
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    users = conn.execute(
        "SELECT username, linux_uid FROM users WHERE is_active = 1 AND linux_uid IS NOT NULL"
    ).fetchall()
    conn.close()

    created = 0
    for username, uid in users:
        # Create Linux user (idempotent -- returns 9 if exists)
        base_cmd = [
            "useradd", "-M", "-d", f"/users/{username}",
            "-s", "/bin/bash", "-u", str(uid),
        ]
        r = subprocess.run(base_cmd + [username], capture_output=True)

        if r.returncode == 9:
            # useradd returns 9 when username found in passwd/shadow/group.
            # Check if user actually exists in /etc/passwd.
            check = subprocess.run(
                ["getent", "passwd", username], capture_output=True,
            )
            if check.returncode != 0:
                # User NOT in passwd — stale shadow or group entry.
                # Check if a group with this name exists and adopt it.
                gid_check = subprocess.run(
                    ["getent", "group", username],
                    capture_output=True, text=True,
                )
                if gid_check.returncode == 0:
                    existing_gid = gid_check.stdout.strip().split(":")[2]
                    r = subprocess.run(
                        base_cmd + ["-g", existing_gid, username],
                        capture_output=True,
                    )
                else:
                    # No group either — clean stale shadow entry and retry
                    subprocess.run(
                        ["sed", "-i", f"/^{username}:/d", "/etc/shadow"],
                        capture_output=True,
                    )
                    r = subprocess.run(
                        base_cmd + [username], capture_output=True,
                    )

        if r.returncode not in (0, 9):
            print(
                f"WARNING: useradd {username} failed: {r.stderr.decode().strip()}",
                file=sys.stderr,
            )
            continue

        if r.returncode == 0:
            created += 1

        # Add user to ag3ntum group
        subprocess.run(
            ["usermod", "-a", "-G", "ag3ntum", username],
            capture_output=True,
        )

        # Add ag3ntum_api to user's primary group (shared GID for 660/770 file access)
        subprocess.run(
            ["usermod", "-a", "-G", username, "ag3ntum_api"],
            capture_output=True,
        )

    if users:
        print(
            f"{prefix}Linux user sync: {len(users)} users "
            f"({created} created, {len(users) - created} existing)"
        )


if __name__ == "__main__":
    prefix = ""
    if len(sys.argv) > 1 and sys.argv[1] == "--prefix":
        prefix = sys.argv[2] if len(sys.argv) > 2 else ""
    sync_users(prefix)
