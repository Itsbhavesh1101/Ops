"""Pytest configuration and shared fixtures for live Flowable & PostgreSQL integration tests.

Part of Phase 3A Workflow Runtime.
Requires a running Docker PostgreSQL container (e7-postgres) and Flowable container (e7-flowable),
activated via the opt-in flag:
    E7_RUN_WORKFLOW_INTEGRATION=1
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.db.uow import SqlAlchemyUnitOfWorkFactory
from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import FlowableSettings
from app.workflow.authorization import DefaultTaskEligibilityPolicy
from app.workflow.service import WorkflowService
from app.workflow.variables import (
    PHASE_3A_FIXTURE_ALLOWED_VARIABLES,
    WorkflowVariablePolicy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

# Database connection configuration for local integration
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(
    os.environ.get("E7_POSTGRES_INTEGRATION_PORT") or os.environ.get("POSTGRES_PORT") or "55432"
)
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")

ADMIN_DB = "postgres"
TEST_DB_NAME = "operations_engine_workflow_test"

ADMIN_PG_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{ADMIN_DB}"
)
TEST_DB_ASYNC_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{TEST_DB_NAME}"
TEST_DB_SYNC_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{TEST_DB_NAME}"

FLOWABLE_BASE_URL = os.environ.get(
    "FLOWABLE_BASE_URL", "http://localhost:8080/flowable-rest/service"
)
FLOWABLE_USER = os.environ.get("FLOWABLE_USER", "rest-admin")
FLOWABLE_PASSWORD = os.environ.get("FLOWABLE_PASSWORD", "test")


def is_workflow_integration_enabled() -> bool:
    """Check if workflow live integration tests are explicitly enabled."""
    return os.environ.get("E7_RUN_WORKFLOW_INTEGRATION") == "1"


def pytest_configure(config: pytest.Config) -> None:
    """Register workflow integration marker."""
    config.addinivalue_line(
        "markers",
        "workflow_integration: mark test as requiring live Flowable and PostgreSQL services",
    )


@pytest.fixture(scope="session", autouse=True)
def check_integration_opt_in() -> None:
    """Skip test suite if opt-in flag E7_RUN_WORKFLOW_INTEGRATION=1 is not set."""
    if not is_workflow_integration_enabled():
        pytest.skip(
            "Live workflow integration tests require E7_RUN_WORKFLOW_INTEGRATION=1",
            allow_module_level=True,
        )


@pytest_asyncio.fixture(scope="session")
async def postgres_and_flowable_check() -> dict[str, Any]:
    """Verify live PostgreSQL and Flowable service availability."""
    # Check PostgreSQL
    try:
        conn = await asyncpg.connect(ADMIN_PG_URL, timeout=5.0)
        await conn.execute("SELECT 1;")
        await conn.close()
    except Exception as exc:
        pytest.fail(f"Failed to connect to Docker PostgreSQL service at {ADMIN_PG_URL}: {exc}")

    # Check Flowable REST endpoint
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{FLOWABLE_BASE_URL.rstrip('/')}/repository/deployments",
                auth=(FLOWABLE_USER, FLOWABLE_PASSWORD),
            )
            if resp.status_code != 200:
                pytest.fail(
                    f"Flowable REST returned non-200 status {resp.status_code}: {resp.text}"
                )
    except Exception as exc:
        pytest.fail(f"Failed to connect to Flowable service at {FLOWABLE_BASE_URL}: {exc}")

    return {"postgres": True, "flowable": True}


async def _terminate_and_drop_db(conn: asyncpg.Connection, db_name: str) -> None:
    """Safely terminate connections and drop a database."""
    await conn.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = $1 AND pid <> pg_backend_pid();
        """,
        db_name,
    )
    await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}";')


@pytest.fixture(scope="session")
def migrated_test_database(postgres_and_flowable_check: dict[str, Any]) -> str:
    """Create isolated test database and apply all Alembic migrations up to head (0010)."""

    async def _setup() -> None:
        conn = await asyncpg.connect(ADMIN_PG_URL)
        await _terminate_and_drop_db(conn, TEST_DB_NAME)
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}";')
        await conn.close()

    asyncio.run(_setup())

    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DB_ASYNC_URL)
    alembic_cfg.set_main_option("script_location", str(REPO_ROOT / "app" / "db" / "migrations"))
    command.upgrade(alembic_cfg, "head")

    yield TEST_DB_NAME

    async def _teardown() -> None:
        conn = await asyncpg.connect(ADMIN_PG_URL)
        await _terminate_and_drop_db(conn, TEST_DB_NAME)
        await conn.close()

    asyncio.run(_teardown())


@pytest_asyncio.fixture(scope="session")
async def async_engine(
    migrated_test_database: str,
) -> AsyncGenerator[AsyncEngine, None]:
    """Provide session-scoped AsyncEngine bound to migrated test database with NullPool."""
    engine = create_async_engine(
        TEST_DB_ASYNC_URL,
        poolclass=NullPool,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def session_factory(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Provide sessionmaker bound to test engine."""
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture
def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyUnitOfWorkFactory:
    """Provide UoW factory for live workflow service operations."""
    return SqlAlchemyUnitOfWorkFactory(session_factory)


@pytest_asyncio.fixture
async def flowable_adapter() -> AsyncGenerator[FlowableAdapter, None]:
    """Provide FlowableAdapter configured against running container."""
    settings = FlowableSettings(
        base_url=FLOWABLE_BASE_URL,
        username=FLOWABLE_USER,
        password=SecretStr(FLOWABLE_PASSWORD),
        timeout_seconds=10.0,
        max_retries=1,
    )
    adapter = FlowableAdapter(settings=settings)
    yield adapter
    await adapter.close()


@pytest.fixture
def workflow_service(
    uow_factory: SqlAlchemyUnitOfWorkFactory,
    flowable_adapter: FlowableAdapter,
) -> WorkflowService:
    """Provide WorkflowService connected to live test DB and Flowable engine."""
    return WorkflowService(
        uow_factory=uow_factory,
        adapter=flowable_adapter,
        eligibility_policy=DefaultTaskEligibilityPolicy(),
        variable_policy=WorkflowVariablePolicy(allowed_keys=PHASE_3A_FIXTURE_ALLOWED_VARIABLES),
    )
