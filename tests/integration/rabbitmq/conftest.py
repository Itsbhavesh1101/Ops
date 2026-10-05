"""Pytest configuration and shared fixtures for RabbitMQ live integration tests.

Part of Phase 1 Foundation Task 1J.2B.
Requires running Docker RabbitMQ and PostgreSQL containers and the opt-in flag:
    E7_RUN_RABBITMQ_INTEGRATION=1

When the flag is not set, all tests in this suite are safely skipped.
When the flag IS set, tests run against an isolated RabbitMQ vhost 'e7_integration'
and an isolated PostgreSQL test database 'operations_engine_rabbitmq_test'.
"""

import asyncio
import os
import subprocess
from collections.abc import AsyncGenerator, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aio_pika
import asyncpg
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

from app.db.uow import SqlAlchemyUnitOfWork
from app.events.consumers.config import (
    RabbitMqConsumerRunnerPolicy,
    RabbitMqConsumerSettings,
)
from app.events.publishers.config import RabbitMqPublisherSettings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

# Connection parameters
RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(
    os.environ.get("E7_RABBITMQ_INTEGRATION_PORT") or os.environ.get("RABBITMQ_PORT") or "5672"
)
RABBITMQ_USER = os.environ.get("RABBITMQ_DEFAULT_USER", "guest")
RABBITMQ_PASS = os.environ.get("RABBITMQ_DEFAULT_PASS", "guest")
RABBITMQ_VHOST = os.environ.get("E7_RABBITMQ_INTEGRATION_VHOST", "e7_integration")

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(
    os.environ.get("E7_POSTGRES_INTEGRATION_PORT") or os.environ.get("POSTGRES_PORT") or "5432"
)
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")

ADMIN_DB = "postgres"
TEST_DB_NAME = "operations_engine_rabbitmq_test"

ADMIN_PG_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{ADMIN_DB}"
)
TEST_DB_ASYNC_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
    f"{POSTGRES_HOST}:{POSTGRES_PORT}/{TEST_DB_NAME}"
)

# Integration topology names
INTEGRATION_EXCHANGE = "e7.integration.events"
INTEGRATION_QUEUE = "e7.integration.consumer"
INTEGRATION_DLX = "e7.integration.dlx"
INTEGRATION_DLQ = "e7.integration.dlq"
INTEGRATION_DLQ_ROUTING_KEY = "dead.letter"
INTEGRATION_BINDING_KEYS = ("integration.#", "audit.#")


def mask_url(url: str) -> str:
    """Return a masked version of a connection string hiding credentials."""
    parsed = urlparse(url)
    if parsed.password:
        return url.replace(f":{parsed.password}@", ":***@")
    return url


def is_integration_enabled() -> bool:
    """Check if RabbitMQ live integration tests are explicitly enabled."""
    return os.environ.get("E7_RUN_RABBITMQ_INTEGRATION") == "1"


def pytest_configure(config: pytest.Config) -> None:
    """Register integration marker."""
    config.addinivalue_line(
        "markers", "integration: mark test as requiring live Docker infrastructure"
    )


@pytest.fixture(scope="session", autouse=True)
def check_rabbitmq_opt_in() -> None:
    """Skip test suite if opt-in flag E7_RUN_RABBITMQ_INTEGRATION=1 is not set."""
    if not is_integration_enabled():
        pytest.skip(
            "Live RabbitMQ integration tests require E7_RUN_RABBITMQ_INTEGRATION=1",
            allow_module_level=True,
        )


@pytest.fixture(scope="session")
def rabbitmq_url() -> SecretStr:
    """Provide the full AMQP connection URL for the isolated integration vhost."""
    return SecretStr(
        f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/{RABBITMQ_VHOST}"
    )


@pytest_asyncio.fixture(scope="session")
async def broker_service_check(
    rabbitmq_url: SecretStr,
    isolated_vhost: str,
) -> dict[str, Any]:
    """Verify live RabbitMQ broker availability and healthy status.

    Fails explicitly if RabbitMQ is unreachable when integration is enabled.
    """
    amqp_url_str = rabbitmq_url.get_secret_value()
    # 1. Check via aio_pika connection
    try:
        conn = await asyncio.wait_for(
            aio_pika.connect_robust(amqp_url_str),
            timeout=10.0,
        )
        await conn.close()
    except Exception as exc:
        masked = mask_url(amqp_url_str)
        pytest.fail(
            f"Failed to connect to local Docker RabbitMQ service at {masked}: {exc}. "
            f"Ensure e7-rabbitmq is running on port {RABBITMQ_PORT}. "
            "If port conflicts with an unrelated workload, configure an alternative "
            "port via E7_RABBITMQ_INTEGRATION_PORT or RABBITMQ_PORT."
        )

    # 2. Query broker version via docker exec
    try:
        proc = subprocess.run(
            ["docker", "exec", "e7-rabbitmq", "rabbitmqctl", "version"],
            capture_output=True,
            text=True,
            check=True,
        )
        version_str = proc.stdout.strip()
    except Exception as exc:
        version_str = f"unknown ({exc})"

    return {
        "url_masked": mask_url(amqp_url_str),
        "version": version_str,
    }


@pytest.fixture(scope="session")
def isolated_vhost() -> Any:
    """Ensure dedicated isolated vhost exists in e7-rabbitmq and clean it up after tests."""
    # Create vhost and configure permissions
    subprocess.run(
        ["docker", "exec", "e7-rabbitmq", "rabbitmqctl", "add_vhost", RABBITMQ_VHOST],
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
            RABBITMQ_VHOST,
            RABBITMQ_USER,
            ".*",
            ".*",
            ".*",
        ],
        capture_output=True,
        check=False,
    )

    yield RABBITMQ_VHOST

    # Teardown: delete vhost atomically clearing all exchanges, queues, and bindings
    subprocess.run(
        ["docker", "exec", "e7-rabbitmq", "rabbitmqctl", "delete_vhost", RABBITMQ_VHOST],
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
async def isolated_postgres_database() -> AsyncGenerator[str, None]:
    """Create isolated test database for outbox/inbox RabbitMQ integration tests."""
    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _terminate_and_drop_db(admin_conn, TEST_DB_NAME)
        await admin_conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}";')
    finally:
        await admin_conn.close()

    # Migrate directly to head 0007
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DB_ASYNC_URL)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    yield TEST_DB_ASYNC_URL

    admin_conn = await asyncpg.connect(ADMIN_PG_URL)
    try:
        await _terminate_and_drop_db(admin_conn, TEST_DB_NAME)
    finally:
        await admin_conn.close()


@pytest_asyncio.fixture
async def test_engine(isolated_postgres_database: str) -> AsyncGenerator[AsyncEngine, None]:
    """Provide a function-scoped AsyncEngine bound to the migrated test database using NullPool."""
    engine = create_async_engine(
        isolated_postgres_database,
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


@pytest.fixture
def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], SqlAlchemyUnitOfWork]:
    """Provide a factory function creating fresh SqlAlchemyUnitOfWork instances."""
    return lambda: SqlAlchemyUnitOfWork(session_factory)


@pytest.fixture
def publisher_settings(rabbitmq_url: SecretStr) -> RabbitMqPublisherSettings:
    """Provide RabbitMqPublisherSettings pointing to isolated vhost."""
    return RabbitMqPublisherSettings(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_exchange_name=INTEGRATION_EXCHANGE,
        rabbitmq_connect_timeout_seconds=5.0,
    )


@pytest.fixture
def consumer_settings(rabbitmq_url: SecretStr) -> RabbitMqConsumerSettings:
    """Provide RabbitMqConsumerSettings pointing to isolated vhost with full topology."""
    return RabbitMqConsumerSettings(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_exchange_name=INTEGRATION_EXCHANGE,
        rabbitmq_consumer_queue_name=INTEGRATION_QUEUE,
        rabbitmq_consumer_binding_keys=INTEGRATION_BINDING_KEYS,
        rabbitmq_dead_letter_exchange_name=INTEGRATION_DLX,
        rabbitmq_dead_letter_queue_name=INTEGRATION_DLQ,
        rabbitmq_dead_letter_routing_key=INTEGRATION_DLQ_ROUTING_KEY,
        rabbitmq_consumer_max_concurrency=5,
        rabbitmq_connect_timeout_seconds=5.0,
    )


@pytest.fixture
def consumer_policy() -> RabbitMqConsumerRunnerPolicy:
    """Provide RabbitMqConsumerRunnerPolicy for consumer tests."""
    return RabbitMqConsumerRunnerPolicy(
        max_concurrency=5,
        shutdown_grace_period=timedelta(seconds=2),
    )
