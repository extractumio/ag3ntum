"""
Async SQLite database connection for Ag3ntum API.

Uses SQLAlchemy async with aiosqlite for non-blocking database operations.
"""
import logging
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import AGENT_DIR

logger = logging.getLogger(__name__)

# Database directory and file
DATA_DIR: Path = AGENT_DIR / "data"
DATABASE_PATH: Path = DATA_DIR / "ag3ntum.db"

# SQLAlchemy async engine (SQLite with aiosqlite driver)
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
    pool_recycle=3600,
)

# Session factory for dependency injection
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""
    pass


async def init_db() -> None:
    """
    Initialize the database.

    Creates the database directory and all tables if they don't exist.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info(f"Database initialized at {DATABASE_PATH}")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency that yields database sessions.

    Usage in FastAPI:
        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

