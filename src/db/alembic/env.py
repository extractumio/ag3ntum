"""
Alembic environment configuration for Ag3ntum.

Supports both offline (--sql) and online (live engine) migration modes.
Uses the async aiosqlite engine via run_sync for Alembic compatibility.
"""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# ---------------------------------------------------------------------------
# Import all models so Alembic can detect schema changes automatically.
# The import order matters: Base must be imported before any model that
# references it, and Reseller must exist before User (circular FK).
# ---------------------------------------------------------------------------
from src.db.database import Base  # noqa: F401
import src.db.models  # noqa: F401 — registers all ORM models on Base.metadata

# Alembic Config object, which provides access to the .ini file
config = context.config

# Apply Python logging configuration from the .ini file (if present)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata — Alembic uses this to generate autogenerate diffs
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Database URL resolution
# Priority: environment variable > alembic.ini sqlalchemy.url > default path
# ---------------------------------------------------------------------------
_DEFAULT_DB_URL = "sqlite+aiosqlite:///data/ag3ntum.db"


def get_database_url() -> str:
    """Return the database URL, preferring runtime environment over config."""
    # 1. Explicit environment variable
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url

    # 2. alembic.ini sqlalchemy.url (if non-empty)
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url

    # 3. Try to import project config for the canonical path
    try:
        from src.db.database import DATABASE_URL as project_url
        return project_url
    except Exception:
        pass

    return _DEFAULT_DB_URL


# ---------------------------------------------------------------------------
# Offline mode — emit SQL to stdout without connecting to the database
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations in offline mode (--sql flag).

    Emits DDL statements as SQL without requiring a live database connection.
    Useful for generating migration scripts to review or apply manually.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite batch mode required for ALTER TABLE support
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect to the database and run migrations directly
# ---------------------------------------------------------------------------

def do_run_migrations(connection: Connection) -> None:
    """Execute migrations on a synchronous connection (called via run_sync)."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite does not support ALTER TABLE natively — batch mode rewrites
        # the table using a copy-rename cycle to apply schema changes.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations through a sync connection."""
    url = get_database_url()
    connectable = create_async_engine(
        url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migration mode."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
