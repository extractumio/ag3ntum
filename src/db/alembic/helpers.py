"""Shared helpers for Alembic migrations."""
from sqlalchemy import text


def table_exists(conn, table_name: str) -> bool:
    """Return True if the named table exists in the database."""
    result = conn.execute(
        text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name=:name"
        ),
        {"name": table_name},
    )
    return result.scalar() > 0
