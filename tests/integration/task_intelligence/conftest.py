"""Pytest configuration and fixtures for Task Intelligence live integration tests.

Part of Phase 2A.
Requires running Docker PostgreSQL container (e7-postgres) and opt-in flag:
    E7_RUN_TASK_INTELLIGENCE_INTEGRATION=1

Invariants:
- Normal pytest skips all tests in this directory when the flag is not set.
- When flag IS set, fails explicitly if PostgreSQL is unavailable.
- Runs against isolated test database 'operations_engine_test_ti'.
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

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(
    os.environ.get("E7_POSTGRES_INTEGRATION_PORT") or os.environ.get("POSTGRES_PORT") or "55432"
)
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")

ADMIN_DB = "postgres"
TEST_DB_NAME = "operations_engine_test_ti"

ADMIN_PG_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{ADMIN_DB}"
)
TEST_DB_ASYNC_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{TEST_DB_NAME}"
TEST_DB_SYNC_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{TEST_DB_NAME}"


def mask_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.password:
        return url.replace(f":{parsed.password}@", ":***@")
    return url


def is_ti_integration_enabled() -> bool:
    return os.environ.get("E7_RUN_TASK_INTELLIGENCE_INTEGRATION") == "1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "ti_integration: mark test as requiring Task Intelligence integration runtime"
    )


@pytest.fixture(scope="session", autouse=True)
def check_ti_integration_opt_in() -> None:
    if not is_ti_integration_enabled():
        pytest.skip(
            "Live Task Intelligence integration tests require "
            "E7_RUN_TASK_INTELLIGENCE_INTEGRATION=1",
            allow_module_level=True,
        )


@pytest_asyncio.fixture(scope="session")
async def postgres_service_check() -> dict[str, Any]:
    try:
        conn = await asyncpg.connect(ADMIN_PG_URL, timeout=5.0)
        version_str = await conn.fetchval("SELECT version();")
        server_version = await conn.fetchval("SHOW server_version;")
        await conn.close()
    except Exception as exc:
        masked = mask_url(ADMIN_PG_URL)
        pytest.fail(
            f"Failed to connect to local Docker PostgreSQL service at {masked}: {exc}. "
            f"Ensure E-7 postgres service is running on port {POSTGRES_PORT}."
        )

    return {
        "admin_url_masked": mask_url(ADMIN_PG_URL),
        "server_version": server_version,
        "version_str": version_str,
    }


async def _terminate_and_drop_db(conn: asyncpg.Connection, db_name: str) -> None:
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
async def isolated_ti_test_database(
    postgres_service_check: dict[str, Any],
) -> AsyncGenerator[str, None]:
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
async def migrated_ti_test_db(isolated_ti_test_database: str) -> AsyncGenerator[str, None]:
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", isolated_ti_test_database)

    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    yield isolated_ti_test_database


@pytest_asyncio.fixture
async def test_engine(migrated_ti_test_db: str) -> AsyncGenerator[AsyncEngine, None]:
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        migrated_ti_test_db,
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest_asyncio.fixture
async def uow_factory(session_factory: async_sessionmaker[AsyncSession]):
    def _create_uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return _create_uow
