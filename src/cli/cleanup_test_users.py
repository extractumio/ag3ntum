#!/usr/bin/env python3
"""CLI tool for cleaning up test users from Ag3ntum.

This script removes test user directories and Linux users created during testing.
Run this after tests to clean up any leftover test artifacts.

Usage:
    python -m src.cli.cleanup_test_users [--dry-run]

Options:
    --dry-run    Show what would be deleted without actually deleting
"""
import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Default users directory
USERS_DIR = Path("/users")

# Test user patterns to clean up
TEST_PATTERNS = ["testuser_", "testuser2_", "e2e_user_", "mount_e2e_", "realtest_"]


def cleanup_test_user_directories(dry_run: bool = False) -> int:
    """
    Clean up test user directories from /users/.

    Args:
        dry_run: If True, only show what would be deleted

    Returns:
        Number of directories removed/would be removed
    """
    removed = 0

    if not USERS_DIR.exists():
        logger.info(f"Users directory {USERS_DIR} does not exist")
        return 0

    for pattern in TEST_PATTERNS:
        for user_dir in USERS_DIR.glob(f"{pattern}*"):
            if user_dir.is_dir():
                if dry_run:
                    logger.info(f"Would remove directory: {user_dir}")
                    removed += 1
                else:
                    try:
                        shutil.rmtree(user_dir)
                        logger.info(f"Removed directory: {user_dir}")
                        removed += 1
                    except PermissionError:
                        # Try with sudo
                        try:
                            subprocess.run(
                                ["sudo", "rm", "-rf", str(user_dir)],
                                check=True,
                                capture_output=True,
                            )
                            logger.info(f"Removed directory (sudo): {user_dir}")
                            removed += 1
                        except subprocess.CalledProcessError as e:
                            logger.error(f"Failed to remove {user_dir}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to remove {user_dir}: {e}")

    return removed


def cleanup_test_linux_users(dry_run: bool = False) -> int:
    """
    Clean up test Linux users.

    Args:
        dry_run: If True, only show what would be deleted

    Returns:
        Number of users removed/would be removed
    """
    removed = 0

    try:
        result = subprocess.run(
            ["getent", "passwd"],
            capture_output=True,
            text=True,
        )

        for line in result.stdout.splitlines():
            username = line.split(":")[0]
            for pattern in TEST_PATTERNS:
                if username.startswith(pattern):
                    if dry_run:
                        logger.info(f"Would delete Linux user: {username}")
                        removed += 1
                    else:
                        try:
                            subprocess.run(
                                ["sudo", "userdel", username],
                                check=True,
                                capture_output=True,
                            )
                            logger.info(f"Deleted Linux user: {username}")
                            removed += 1
                        except subprocess.CalledProcessError as e:
                            if e.returncode == 6:
                                # User doesn't exist
                                pass
                            else:
                                logger.error(f"Failed to delete user {username}: {e}")
                    break

    except FileNotFoundError:
        logger.debug("getent command not available")
    except Exception as e:
        logger.error(f"Failed to enumerate Linux users: {e}")

    return removed


def cleanup_test_db_entries(dry_run: bool = False) -> int:
    """
    Clean up test user entries from the database.

    Removes DB rows for users matching TEST_PATTERNS.

    Args:
        dry_run: If True, only show what would be deleted

    Returns:
        Number of entries removed/would be removed
    """
    import sqlite3

    db_path = Path("/data/ag3ntum.db")
    if not db_path.exists():
        logger.debug("Database not found at /data/ag3ntum.db")
        return 0

    removed = 0
    try:
        with sqlite3.connect(str(db_path)) as conn:
            users = conn.execute("SELECT username FROM users").fetchall()

            for (username,) in users:
                for pattern in TEST_PATTERNS:
                    if username.startswith(pattern):
                        if dry_run:
                            logger.info(f"Would delete DB entry: {username}")
                            removed += 1
                        else:
                            try:
                                conn.execute("DELETE FROM users WHERE username = ?", (username,))
                                logger.info(f"Deleted DB entry: {username}")
                                removed += 1
                            except Exception as e:
                                logger.error(f"Failed to delete DB entry {username}: {e}")
                        break

            if not dry_run:
                conn.commit()

    except Exception as e:
        logger.error(f"Failed to clean up database entries: {e}")

    return removed


def main():
    parser = argparse.ArgumentParser(
        description="Clean up test users from Ag3ntum",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Show what would be deleted
    python -m src.cli.cleanup_test_users --dry-run

    # Actually delete test users
    python -m src.cli.cleanup_test_users

    # Run inside Docker
    docker exec project-ag3ntum-api-1 python -m src.cli.cleanup_test_users
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )

    args = parser.parse_args()

    logger.info("=== Ag3ntum Test User Cleanup ===")
    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    logger.info("")
    logger.info("Cleaning up test user directories...")
    dir_count = cleanup_test_user_directories(dry_run=args.dry_run)

    logger.info("")
    logger.info("Cleaning up test Linux users...")
    user_count = cleanup_test_linux_users(dry_run=args.dry_run)

    logger.info("")
    logger.info("Cleaning up test database entries...")
    db_count = cleanup_test_db_entries(dry_run=args.dry_run)

    logger.info("")
    logger.info("=== Summary ===")
    if args.dry_run:
        logger.info(f"Would remove {dir_count} directories")
        logger.info(f"Would delete {user_count} Linux users")
        logger.info(f"Would delete {db_count} database entries")
    else:
        logger.info(f"Removed {dir_count} directories")
        logger.info(f"Deleted {user_count} Linux users")
        logger.info(f"Deleted {db_count} database entries")

    return 0


if __name__ == "__main__":
    sys.exit(main())
