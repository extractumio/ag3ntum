#!/usr/bin/env python3
"""CLI tool for deleting Ag3ntum users.

This removes the user from the Ag3ntum database and cleans up their
user directory. The Linux user account is NOT deleted to avoid
affecting host users (especially in direct UID mapping mode).
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.database import AsyncSessionLocal, init_db, engine
from src.db import models  # noqa: F401 - Import models to register with Base.metadata
from src.services.user_service import user_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def delete_user(
    username: str,
    force: bool = False,
) -> None:
    """Delete a user from Ag3ntum (database + user directory only)."""
    try:
        # Ensure database tables exist
        await init_db()

        async with AsyncSessionLocal() as db:
            # First check if user exists
            from sqlalchemy import select
            from src.db.models import User

            result = await db.execute(
                select(User).where(User.username == username)
            )
            user = result.scalar_one_or_none()

            if not user:
                logger.error(f"User '{username}' not found")
                raise ValueError(f"User '{username}' not found")

            if not force:
                logger.info(f"User to delete:")
                logger.info(f"  ID: {user.id}")
                logger.info(f"  Username: {user.username}")
                logger.info(f"  Email: {user.email}")
                logger.info(f"  Linux UID: {user.linux_uid}")
                logger.info("")
                logger.info("Use --force to confirm deletion")
                return

            # Always keep Linux user to avoid affecting host users
            deleted = await user_service.delete_user(
                db=db,
                username=username,
                delete_linux_user=False,
            )

            if deleted:
                logger.info(f"User '{username}' deleted successfully")
                logger.info("  (Linux user account preserved)")
            else:
                logger.error(f"Failed to delete user '{username}'")
                raise ValueError(f"Failed to delete user '{username}'")

    except ValueError:
        raise
    finally:
        # Always clean up database connections and wait for background tasks
        await engine.dispose()

        # Give aiosqlite threads time to clean up
        await asyncio.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(
        description="Delete Ag3ntum user (removes from database, preserves Linux user)"
    )
    parser.add_argument("--username", required=True, help="Username to delete")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Confirm deletion (required to actually delete)",
    )

    args = parser.parse_args()

    # Use asyncio.run with explicit cleanup
    try:
        asyncio.run(
            delete_user(
                username=args.username,
                force=args.force,
            )
        )
        # Force clean exit for CLI tool (aiosqlite may have lingering threads)
        sys.exit(0)
    except ValueError:
        # Errors already logged
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
