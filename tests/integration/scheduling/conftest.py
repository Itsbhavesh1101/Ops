"""Pytest configuration and shared fixtures for live scheduling database integration tests.

Requires a running Docker PostgreSQL container and the opt-in flag:
    E7_RUN_SCHEDULING_INTEGRATION=1 or E7_RUN_POSTGRES_INTEGRATION=1

When the flag is not set, live database integration tests are safely skipped.
Subdirectory tests (e.g. education benchmark) maintain their own independent opt-in gates.
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(
    os.environ.get("E7_POSTGRES_INTEGRATION_PORT")
    or os.environ.get("POSTGRES_PORT")
    or "55432"
)
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
SCHEDULING_TEST_DB = "operations_engine_test_scheduling"
ADMIN_PG_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
    f"{POSTGRES_HOST}:{POSTGRES_PORT}/postgres"
)
SCHEDULING_TEST_DB_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
    f"{POSTGRES_HOST}:{POSTGRES_PORT}/{SCHEDULING_TEST_DB}"
)


def is_scheduling_integration_enabled() -> bool:
    """Check if scheduling live database integration tests are explicitly enabled."""
    return (
        os.environ.get("E7_RUN_SCHEDULING_INTEGRATION") == "1"
        or os.environ.get("E7_RUN_POSTGRES_INTEGRATION") == "1"
    )


@pytest.fixture(autouse=True)
def check_scheduling_integration_opt_in(request: pytest.FixtureRequest) -> None:
    """Skip live database tests if opt-in flag is not set."""
    # Never skip education tests (they use E7_RUN_EDUCATION_SOLVER_BENCHMARK)
    if "education" in str(request.fspath):
        return

    if not is_scheduling_integration_enabled():
        pytest.skip(
            "Live scheduling database integration tests require "
            "E7_RUN_SCHEDULING_INTEGRATION=1 or E7_RUN_POSTGRES_INTEGRATION=1"
        )


async def _drop_database(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = $1 AND pid <> pg_backend_pid()
        """,
        SCHEDULING_TEST_DB,
    )
    await conn.execute(f'DROP DATABASE IF EXISTS "{SCHEDULING_TEST_DB}"')


@pytest_asyncio.fixture(scope="session", autouse=True)
async def isolated_scheduling_database() -> AsyncGenerator[None, None]:
    """Provision the schema used by live scheduling tests without touching admin DBs."""
    if not is_scheduling_integration_enabled():
        yield
        return

    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _drop_database(admin_conn)
        await admin_conn.execute(f'CREATE DATABASE "{SCHEDULING_TEST_DB}"')
    finally:
        await admin_conn.close()

    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", SCHEDULING_TEST_DB_URL)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    yield

    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _drop_database(admin_conn)
    finally:
        await admin_conn.close()
