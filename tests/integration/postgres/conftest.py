"""Pytest configuration and shared fixtures for PostgreSQL live runtime integration tests.

Part of Phase 1 Foundation Task 1J.2A.
Requires a running Docker PostgreSQL container (e7-postgres) and the opt-in flag:
    E7_RUN_POSTGRES_INTEGRATION=1

When the flag is not set, all tests in this suite are safely skipped.
When the flag IS set, tests run against the isolated test database 'operations_engine_test'
inside the local Docker PostgreSQL service.
If PostgreSQL is unreachable when the flag is set, tests fail explicitly.
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.uow import SqlAlchemyUnitOfWork

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

# Database connection configuration for local integration
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(
    os.environ.get("E7_POSTGRES_INTEGRATION_PORT") or os.environ.get("POSTGRES_PORT") or "5432"
)
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")

ADMIN_DB = "postgres"
TEST_DB_NAME = "operations_engine_test"

ADMIN_PG_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{ADMIN_DB}"
)
TEST_DB_ASYNC_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{TEST_DB_NAME}"
TEST_DB_SYNC_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{TEST_DB_NAME}"


def mask_url(url: str) -> str:
    """Return a masked version of the connection string hiding the password."""
    parsed = urlparse(url)
    if parsed.password:
        return url.replace(f":{parsed.password}@", ":***@")
    return url


def is_integration_enabled() -> bool:
    """Check if PostgreSQL live integration tests are explicitly enabled."""
    return os.environ.get("E7_RUN_POSTGRES_INTEGRATION") == "1"


def pytest_configure(config: pytest.Config) -> None:
    """Register the integration marker if not already registered."""
    config.addinivalue_line(
        "markers", "integration: mark test as requiring live Docker PostgreSQL service"
    )


@pytest.fixture(scope="session", autouse=True)
def check_integration_opt_in() -> None:
    """Skip test suite if opt-in flag E7_RUN_POSTGRES_INTEGRATION=1 is not set."""
    if not is_integration_enabled():
        pytest.skip(
            "Live PostgreSQL integration tests require E7_RUN_POSTGRES_INTEGRATION=1",
            allow_module_level=True,
        )


@pytest_asyncio.fixture(scope="session")
async def postgres_service_check() -> dict[str, Any]:
    """Verify live PostgreSQL 16 container availability and healthy status.

    Fails explicitly if Docker PostgreSQL is unreachable when integration is enabled.
    Instructs the operator to configure a free port rather than mutating unrelated containers.
    """
    try:
        conn = await asyncpg.connect(ADMIN_PG_URL, timeout=5.0)
        version_str = await conn.fetchval("SELECT version();")
        server_version = await conn.fetchval("SHOW server_version;")
        await conn.close()
    except Exception as exc:
        masked = mask_url(ADMIN_PG_URL)
        pytest.fail(
            f"Failed to connect to local Docker PostgreSQL service at {masked}: {exc}. "
            f"Ensure E-7 postgres service is running on port {POSTGRES_PORT} "
            f"(e.g. '$env:POSTGRES_PORT=\"{POSTGRES_PORT}\"; docker compose up -d postgres'). "
            "If this port conflicts with an unrelated local workload, configure an alternative "
            "port via E7_POSTGRES_INTEGRATION_PORT or POSTGRES_PORT "
            "without stopping other workloads."
        )

    assert "PostgreSQL" in version_str, f"Unexpected database engine: {version_str}"
    assert "16" in server_version, f"Expected PostgreSQL 16, got {server_version}"

    return {
        "admin_url_masked": mask_url(ADMIN_PG_URL),
        "server_version": server_version,
        "version_str": version_str,
    }


async def _terminate_and_drop_db(conn: asyncpg.Connection, db_name: str) -> None:
    """Terminate all active connections to the test database and drop it."""
    await conn.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = $1 AND pid <> pg_backend_pid();
        """,
        db_name,
    )
    await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}";')


@pytest_asyncio.fixture(scope="session")
async def isolated_test_database(
    postgres_service_check: dict[str, Any],
) -> AsyncGenerator[str, None]:
    """Create a completely isolated 'operations_engine_test' database in PostgreSQL.

    Ensures zero interaction with developer's 'operations_engine' or 'flowable_db'.
    Drops the test database on session teardown.
    """
    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _terminate_and_drop_db(admin_conn, TEST_DB_NAME)
        await admin_conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}";')
    finally:
        await admin_conn.close()

    yield TEST_DB_ASYNC_URL

    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _terminate_and_drop_db(admin_conn, TEST_DB_NAME)
    finally:
        await admin_conn.close()


@pytest_asyncio.fixture(scope="session")
async def migrated_test_db(isolated_test_database: str) -> AsyncGenerator[str, None]:
    """Execute Alembic upgrade head against the isolated test database.

    Leaves the database at head revision 0007 for subsequent domain test suites.
    """
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", isolated_test_database)

    # Run Alembic upgrade head synchronously in a thread
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    yield isolated_test_database


@pytest_asyncio.fixture
async def test_engine(migrated_test_db: str) -> AsyncGenerator[AsyncEngine, None]:
    """Provide a function-scoped AsyncEngine bound to the migrated test database using NullPool."""
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        migrated_test_db,
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Provide an async_sessionmaker bound to the test database."""
    return async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest_asyncio.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a clean AsyncSession per test."""
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def uow_factory(session_factory: async_sessionmaker[AsyncSession]):
    """Provide a factory function returning fresh SqlAlchemyUnitOfWork instances."""

    def _create_uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return _create_uow
