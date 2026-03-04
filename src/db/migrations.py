"""
Programmatic Alembic migration runner for Ag3ntum.

Handles three scenarios at startup:
1. Fresh DB (no tables)         → create_all() + stamp head
2. Existing DB without alembic  → stamp "001" + upgrade head
3. Existing DB with alembic     → upgrade head

Called from entrypoint-api.sh and entrypoint-test.sh (as root, before
privilege drop) to ensure the database schema is current before the
API process starts.
"""
import logging
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Paths — PYTHONPATH=/ is set in Dockerfile so src.* imports work
_ALEMBIC_INI = os.path.join(os.sep, "src", "db", "alembic.ini")
_DATA_DIR = os.path.join(os.sep, "data")
_DB_PATH = os.path.join(_DATA_DIR, "ag3ntum.db")


def _get_alembic_config() -> Config:
    """Build Alembic Config pointing at our alembic.ini."""
    cfg = Config(_ALEMBIC_INI)
    # Override sqlalchemy.url — env.py uses create_async_engine so we
    # must provide the async aiosqlite driver URL.
    cfg.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{_DB_PATH}",
    )
    return cfg


def _table_exists(connection, table_name: str) -> bool:
    """Check if a table exists in the SQLite database."""
    result = connection.execute(
        text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name=:name"
        ),
        {"name": table_name},
    )
    return result.scalar() > 0


def run_migrations_sync() -> None:
    """
    Run database migrations synchronously.

    Safe to call from entrypoint scripts (no event loop running).
    Handles fresh, legacy, and already-managed databases.
    """
    from sqlalchemy import create_engine

    os.makedirs(_DATA_DIR, exist_ok=True)

    engine = create_engine(f"sqlite:///{_DB_PATH}")
    cfg = _get_alembic_config()

    with engine.connect() as conn:
        has_alembic = _table_exists(conn, "alembic_version")
        has_users = _table_exists(conn, "users")

    if not has_users:
        # --- Case 1: Fresh database — no tables at all ---
        logger.info("Fresh database detected — creating schema via create_all()")
        _create_all_sync(engine)
        command.stamp(cfg, "head")
        logger.info("Database stamped at head revision")

    elif not has_alembic:
        # --- Case 2: Existing DB created by create_all(), no Alembic tracking ---
        # Stamp at "001" (the first migration that matches the existing schema)
        # then upgrade to head to apply any subsequent migrations.
        logger.info(
            "Existing database without Alembic tracking — "
            "stamping at 001 and upgrading to head"
        )
        command.stamp(cfg, "001")
        command.upgrade(cfg, "head")
        logger.info("Database migrated to head revision")

    else:
        # --- Case 3: Alembic-managed DB — run pending migrations ---
        logger.info("Running pending database migrations...")
        command.upgrade(cfg, "head")
        logger.info("Database migrations complete")

    engine.dispose()


def _create_all_sync(engine) -> None:
    """Create all tables using SQLAlchemy metadata (sync engine)."""
    # Import Base and all models so metadata is populated
    from src.db.database import Base  # noqa: F401
    import src.db.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
