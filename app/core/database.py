"""
Async database engine and session management.

A single async engine is created for the process lifetime; sessions are
created per-request via the `get_db_session` FastAPI dependency, which also
guarantees commit-on-success / rollback-on-error semantics so callers never
need to remember to do it themselves.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency that yields a transactional session per request.

    Commits on clean exit, rolls back on any exception, and always closes
    the session. Business/service code should not call commit() itself for
    the *outer* request transaction; it may still use nested SAVEPOINTs via
    `session.begin_nested()` where required (e.g. idempotency races).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager form of `get_db_session`, for use outside FastAPI DI
    (e.g. scripts, background tasks, tests)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
