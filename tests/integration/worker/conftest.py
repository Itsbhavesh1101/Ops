"""Pytest configuration and shared fixtures for Live Outbox Worker Runtime Integration.

Part of Phase 1 Foundation Task 1J.3C.
Exercises the real Dockerized background worker runtime (app.worker) against:
- Live PostgreSQL container (e7-postgres) with isolated database 'operations_engine_worker_test'
- Live RabbitMQ container (e7-rabbitmq) with isolated vhost 'e7_worker_integration'
- Real Docker container execution via 'docker compose --profile worker-runtime up -d worker'

Requires the opt-in environment variable:
    E7_RUN_WORKER_INTEGRATION=1

When the flag is not set, all tests in this suite are safely skipped.
When the flag IS set, any failure in Docker build, container startup, dispatch,
broker receipt, DB settlement, or graceful shutdown will fail explicitly.
"""

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncGenerator, Callable, Generator
from pathlib import Path
from typing import Any

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
from sqlalchemy.pool import NullPool

from app.db.uow import SqlAlchemyUnitOfWork

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

# Port isolation adhering to Task 1J.2A.1 and Task 1J.2B
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "55432"))
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")

WORKER_TEST_DB = "operations_engine_worker_test"
ADMIN_DB = "postgres"

ADMIN_PG_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{ADMIN_DB}"
)
TEST_DB_ASYNC_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
    f"{POSTGRES_HOST}:{POSTGRES_PORT}/{WORKER_TEST_DB}"
)

# RabbitMQ configuration
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", "5673"))
RABBITMQ_MANAGEMENT_PORT = int(os.environ.get("RABBITMQ_MANAGEMENT_PORT", "15673"))
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASSWORD = os.environ.get("RABBITMQ_PASSWORD", "guest")

WORKER_VHOST = "e7_worker_integration"
WORKER_EXCHANGE = "e7.worker.integration.events"
WORKER_OBSERVER_QUEUE = "e7.worker.integration.observer"

AMQP_HOST_URL = (
    f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/{WORKER_VHOST}"
)

EXTERNAL_WORKLOAD_CONTAINER = "hischolar-cv-suite-postgres-1"


def is_worker_integration_enabled() -> bool:
    """Check if worker container live integration tests are explicitly enabled."""
    return os.environ.get("E7_RUN_WORKER_INTEGRATION") == "1"


def pytest_configure(config: pytest.Config) -> None:
    """Register the integration marker if not already registered."""
    config.addinivalue_line("markers", "integration: mark test as requiring live Docker services")


@pytest.fixture(scope="session", autouse=True)
def check_worker_integration_opt_in() -> None:
    """Skip test suite if opt-in flag E7_RUN_WORKER_INTEGRATION=1 is not set."""
    if not is_worker_integration_enabled():
        pytest.skip(
            "Live worker container integration tests require E7_RUN_WORKER_INTEGRATION=1",
            allow_module_level=True,
        )


def _get_external_workload_state() -> dict[str, Any] | None:
    """Read-only capture of external workload container state."""
    try:
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                EXTERNAL_WORKLOAD_CONTAINER,
                "--format",
                "{{json .}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            return {
                "id": data.get("Id"),
                "status": data.get("State", {}).get("Status"),
                "restart_policy": data.get("HostConfig", {}).get("RestartPolicy", {}).get("Name"),
            }
    except Exception:
        pass
    return None


@pytest.fixture(scope="session", autouse=True)
def external_workload_guard() -> Generator[dict[str, Any] | None, None, None]:
    """Capture external workload metadata before/after to prove complete isolation."""
    before_state = _get_external_workload_state()
    yield before_state
    after_state = _get_external_workload_state()

    if before_state is not None:
        assert after_state is not None, "External workload container disappeared during test"
        assert after_state["id"] == before_state["id"], (
            "External workload container ID changed (was recreated or replaced)"
        )
        assert after_state["status"] == before_state["status"], (
            f"External workload status changed: {before_state['status']} -> {after_state['status']}"
        )
        assert after_state["restart_policy"] == before_state["restart_policy"], (
            "External workload restart policy mutated"
        )


@pytest.fixture(scope="session")
def docker_env() -> dict[str, str]:
    """Environment dictionary passed to docker compose commands."""
    env = os.environ.copy()
    env["POSTGRES_PORT"] = str(POSTGRES_PORT)
    env["RABBITMQ_PORT"] = str(RABBITMQ_PORT)
    env["RABBITMQ_MANAGEMENT_PORT"] = str(RABBITMQ_MANAGEMENT_PORT)
    return env


@pytest.fixture(scope="session")
def compose_override_file() -> Generator[Path, None, None]:
    """Create a temporary Compose override file for worker test environment injection."""
    override_path = REPO_ROOT / "docker-compose.worker-test.override.yml"
    override_content = f"""services:
  worker:
    environment:
      E7_DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/{WORKER_TEST_DB}
      E7_RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/{WORKER_VHOST}
      E7_RABBITMQ_EXCHANGE_NAME: {WORKER_EXCHANGE}
      E7_OUTBOX_POLL_INTERVAL_SECONDS: "0.2"
      E7_OUTBOX_MAX_CONCURRENCY: "1"
      E7_OUTBOX_SHUTDOWN_GRACE_SECONDS: "5.0"
"""
    override_path.write_text(override_content, encoding="utf-8")
    try:
        yield override_path
    finally:
        if override_path.exists():
            override_path.unlink()


@pytest.fixture(scope="session")
def isolated_worker_vhost() -> Generator[str, None, None]:
    """Ensure dedicated isolated vhost exists in e7-rabbitmq and clean it up after tests."""
    # Create vhost and configure permissions
    subprocess.run(
        ["docker", "exec", "e7-rabbitmq", "rabbitmqctl", "add_vhost", WORKER_VHOST],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        [
            "docker",
            "exec",
            "e7-rabbitmq",
            "rabbitmqctl",
            "set_permissions",
            "-p",
            WORKER_VHOST,
            RABBITMQ_USER,
            ".*",
            ".*",
            ".*",
        ],
        capture_output=True,
        check=False,
    )

    yield WORKER_VHOST

    # Teardown: delete vhost atomically clearing all exchanges, queues, and bindings
    subprocess.run(
        ["docker", "exec", "e7-rabbitmq", "rabbitmqctl", "delete_vhost", WORKER_VHOST],
        capture_output=True,
        check=False,
    )


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
async def isolated_worker_db() -> AsyncGenerator[str, None]:
    """Create isolated test database for worker integration tests and migrate to head 0007."""
    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _terminate_and_drop_db(admin_conn, WORKER_TEST_DB)
        await admin_conn.execute(f'CREATE DATABASE "{WORKER_TEST_DB}";')
    finally:
        await admin_conn.close()

    # Migrate directly to head 0007
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DB_ASYNC_URL)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    # Verify head revision
    test_conn = await asyncpg.connect(
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
        f"{POSTGRES_HOST}:{POSTGRES_PORT}/{WORKER_TEST_DB}"
    )
    try:
        rev = await test_conn.fetchval("SELECT version_num FROM alembic_version;")
        assert rev == "0007", f"Expected alembic_version '0007', got '{rev}'"
    finally:
        await test_conn.close()

    yield TEST_DB_ASYNC_URL

    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _terminate_and_drop_db(admin_conn, WORKER_TEST_DB)
    finally:
        await admin_conn.close()


@pytest_asyncio.fixture
async def test_engine(isolated_worker_db: str) -> AsyncGenerator[AsyncEngine, None]:
    """Provide a function-scoped AsyncEngine bound to the migrated test database using NullPool."""
    engine = create_async_engine(
        isolated_worker_db,
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Provide an async_sessionmaker bound to the test engine."""
    return async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


@pytest.fixture
def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], SqlAlchemyUnitOfWork]:
    """Provide a UoW factory callable bound to the test database session factory."""
    return lambda: SqlAlchemyUnitOfWork(session_factory)
