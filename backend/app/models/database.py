"""
Database initialization and session management.

Provides:
- Database engine creation
- Session factory
- Connection pooling
- Schema creation
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.pool import NullPool, QueuePool

from app.config import config
from app.models.basket import Base

logger = logging.getLogger(__name__)

# Global engine and session factory
_engine: AsyncEngine = None
_SessionLocal = None


async def init_db() -> None:
    """Initialize database engine and create tables."""
    global _engine, _SessionLocal

    logger.info("Initializing database...")

    # Create async engine
    db_url = config.database.url
    logger.info(f"Connecting to: {db_url.replace(config.database.password, '***')}")

    _engine = create_async_engine(
        db_url,
        echo=config.platform.debug,  # SQL logging in debug mode
        poolclass=QueuePool,  # Connection pooling
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,  # Test connections before using
        connect_args={
            "timeout": 30,
            "command_timeout": 30,
        }
    )

    # Create session factory
    _SessionLocal = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Create all tables
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("✅ Database initialized")


async def close_db() -> None:
    """Close database connections."""
    global _engine

    if _engine:
        logger.info("Closing database connection...")
        await _engine.dispose()
        _engine = None
        logger.info("Database closed")


def get_session_factory():
    """Get the session factory."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionLocal


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for database sessions.

    Usage:
        async with get_session() as session:
            result = await session.execute(...)
    """
    SessionLocal = get_session_factory()
    session = SessionLocal()

    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency injection for FastAPI.

    Usage:
        @app.get("/items")
        async def read_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with get_session() as session:
        yield session
