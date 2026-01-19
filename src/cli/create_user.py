#!/usr/bin/env python3
"""CLI tool for creating Ag3ntum users."""
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


async def create_user(
    username: str,
    email: str,
    password: str,
    admin: bool = False,
) -> None:
    """Create a new user."""
    try:
        # Ensure database tables exist
        await init_db()

        role = "admin" if admin else "user"

        async with AsyncSessionLocal() as db:
            user = await user_service.create_user(
                db=db,
                username=username,
                email=email,
                password=password,
                role=role,
            )

            logger.info(f"User created successfully:")
            logger.info(f"  ID: {user.id}")
            logger.info(f"  Username: {user.username}")
            logger.info(f"  Email: {user.email}")
            logger.info(f"  Role: {user.role}")
            logger.info(f"  Linux UID: {user.linux_uid}")

    except ValueError as e:
        logger.error(f"Failed to create user: {e}")
        raise
    finally:
        # Always clean up database connections and wait for background tasks
        await engine.dispose()
        
        # Give aiosqlite threads time to clean up
        await asyncio.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description="Create Ag3ntum user")
    parser.add_argument("--username", required=True, help="Username (3-32 chars)")
    parser.add_argument("--email", required=True, help="Email address")
    parser.add_argument("--password", required=True, help="Password")
    parser.add_argument("--admin", action="store_true", help="Create as admin user")

    args = parser.parse_args()

    # Use asyncio.run with explicit cleanup
    try:
        asyncio.run(create_user(
            username=args.username,
            email=args.email,
            password=args.password,
            admin=args.admin,
        ))
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
