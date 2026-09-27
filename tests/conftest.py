"""
Shared pytest fixtures.

Two isolation strategies are provided, because they answer two different
questions:

1. ``client`` (default for integration tests)
    Every test runs inside one outer database transaction; the application's
    sessions join it via SAVEPOINTs (`join_transaction_mode="create_savepoint"`).
    The outer transaction is rolled back after each test, so tests are fast
    and leave no data behind. Note that all requests in such a test share a
    single connection, which is fine for sequential requests but means row
    locks would never actually contend -- so it must NOT be used for
    concurrency tests.

2. ``committed_client`` (used by the concurrency tests)
    Each request gets its own real connection and commits for real, so
    `SELECT ... FOR UPDATE`, advisory locks and lock waiting behave exactly
    as they do in production. Data is truncated after the test.

The schema itself is (re)created once per pytest session against the
dedicated test database (`settings.test_database_url`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401  -- populates Base.metadata
from app.core.config import get_settings
from app.core.database import Base, get_db_session
from app.main import app

settings = get_settings()

TEST_BASE_URL = "http://test"


def _assert_safe_test_database(url: str) -> None:
    """Refuse to touch a database that doesn't look like a throwaway one."""
    database = url.rsplit("/", 1)[-1].split("?")[0]
    if not database.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run tests against database {database!r}: "
            "TEST_DATABASE_URL must point at a database whose name ends in '_test'."
        )


def _make_engine(url: str) -> AsyncEngine:
    # NullPool: no background connection reuse, so every session gets a
    # genuinely independent connection (required for the concurrency tests
    # to exercise real lock contention) and teardown is deterministic.
    return create_async_engine(url, poolclass=NullPool)


async def _reset_schema(url: str) -> None:
    engine = _make_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def _truncate_all(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE ledger_entries, transfers, idempotency_keys, accounts "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture(scope="session")
def test_database() -> str:
    """Recreate the schema in the dedicated test database once per session."""
    url = settings.test_database_url
    _assert_safe_test_database(url)
    asyncio.run(_reset_schema(url))
    return url


def _install_override(factory: async_sessionmaker[AsyncSession]) -> None:
    """Point the app's `get_db_session` dependency at a test session factory.

    Behaviour mirrors the production dependency (commit on success, rollback
    on error, always close) so tests exercise the same transaction semantics.
    """

    async def override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db_session] = override


@pytest_asyncio.fixture
async def client(test_database: str) -> AsyncIterator[AsyncClient]:
    """HTTP client for tests that roll back after each test (fast, isolated)."""
    engine = _make_engine(test_database)
    connection = await engine.connect()
    await connection.begin()
    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    _install_override(factory)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=TEST_BASE_URL
        ) as async_client:
            yield async_client
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        await connection.rollback()
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def committed_client(test_database: str) -> AsyncIterator[AsyncClient]:
    """HTTP client with real commits on real connections.

    Used where lock behaviour matters (concurrency tests). Cleans up all rows
    afterwards so later tests still start from an empty database.
    """
    engine = _make_engine(test_database)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    _install_override(factory)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=TEST_BASE_URL
        ) as async_client:
            yield async_client
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        await _truncate_all(engine)
        await engine.dispose()
