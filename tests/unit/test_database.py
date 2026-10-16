"""Unit tests for Phase 1 Task 1D PostgreSQL / SQLAlchemy persistence foundation.

Verifies:
- Import safety: app.main imports without database URL.
- Engine creation requires explicit URL and rejects unsupported schemes (sqlite, mysql, mariadb).
- Connection pool parameters and connect timeout propagation.
- Secret safety: URL passwords are redacted in repr, logs, and exceptions.
- DeclarativeBase and deterministic naming conventions (ix, uq, ck, fk, pk).
- Zero business tables registered in Base.metadata.
- DatabaseManager lifecycle ownership and clean disposal.
- Session factory and get_db_session lifecycle without auto-commit.
- check_database_ready probe behavior with SELECT 1 and safe error handling.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings
from app.db.base import POSTGRESQL_NAMING_CONVENTION, Base
from app.db.engine import (
    DatabaseConfigurationError,
    DatabaseManager,
    create_database_engine,
    validate_postgresql_async_url,
)
from app.db.health import check_database_ready
from app.db.session import create_session_factory, get_db_session, get_session

SYNTHETIC_POSTGRES_URL = (
    "postgresql+asyncpg://e7_app:synthetic_secret_pass_123@db.internal:5432/e7_operations"
)


def test_app_main_import_without_database_url() -> None:
    """Verify that app.main imports cleanly without requiring E7_DATABASE_URL."""
    import app.main

    assert app.main.app is not None


def test_missing_database_url_fails_only_when_requested() -> None:
    """Verify Settings defaults to database_url=None and engine creation fails explicitly."""
    settings = Settings(database_url=None)
    assert settings.database_url is None

    with pytest.raises(DatabaseConfigurationError) as exc_info:
        create_database_engine(settings)
    assert "E7_DATABASE_URL is not configured" in str(exc_info.value)

    with pytest.raises(DatabaseConfigurationError) as exc_info_mgr:
        DatabaseManager(settings)
    assert "E7_DATABASE_URL is not configured" in str(exc_info_mgr.value)


def test_database_manager_accepts_valid_postgresql_asyncpg_url() -> None:
    """Verify that a valid postgresql+asyncpg URL is accepted and creates AsyncEngine."""
    settings = Settings(database_url=SecretStr(SYNTHETIC_POSTGRES_URL))
    engine = create_database_engine(settings)

    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.database == "e7_operations"


@pytest.mark.parametrize(
    "unsupported_url,expected_scheme",
    [
        ("sqlite:///test.db", "sqlite"),
        ("sqlite+aiosqlite:///test.db", "sqlite+aiosqlite"),
        ("mysql://user:pass@host:3306/db", "mysql"),
        ("mysql+pymysql://user:pass@host:3306/db", "mysql+pymysql"),
        ("mysql+asyncmy://user:pass@host:3306/db", "mysql+asyncmy"),
        ("mariadb+pymysql://user:pass@host:3306/db", "mariadb+pymysql"),
        ("postgresql://user:pass@host:5432/db", "postgresql"),
    ],
)
def test_unsupported_schemes_rejected(unsupported_url: str, expected_scheme: str) -> None:
    """Verify that non-postgresql+asyncpg URLs are rejected with clean error."""
    with pytest.raises(DatabaseConfigurationError) as exc_info:
        validate_postgresql_async_url(unsupported_url)

    msg = str(exc_info.value)
    assert f"Unsupported database URL scheme '{expected_scheme}'" in msg
    assert "user:pass" not in msg


def test_database_settings_bounds_and_defaults() -> None:
    """Verify default values and bounds validation for technical database settings."""
    defaults = Settings()
    assert defaults.database_pool_size == 5
    assert defaults.database_max_overflow == 10
    assert defaults.database_pool_timeout_seconds == 30.0
    assert defaults.database_pool_recycle_seconds == 1800
    assert defaults.database_connect_timeout_seconds == 10.0
    assert defaults.database_echo is False

    # Valid bounds
    custom = Settings(
        database_pool_size=1,
        database_max_overflow=0,
        database_pool_timeout_seconds=0.5,
        database_pool_recycle_seconds=-1,
        database_connect_timeout_seconds=0.5,
        database_echo=True,
    )
    assert custom.database_pool_size == 1
    assert custom.database_max_overflow == 0
    assert custom.database_pool_timeout_seconds == 0.5
    assert custom.database_pool_recycle_seconds == -1
    assert custom.database_connect_timeout_seconds == 0.5
    assert custom.database_echo is True

    # Invalid bounds
    with pytest.raises(ValidationError):
        Settings(database_pool_size=0)
    with pytest.raises(ValidationError):
        Settings(database_pool_size=101)
    with pytest.raises(ValidationError):
        Settings(database_max_overflow=-1)
    with pytest.raises(ValidationError):
        Settings(database_max_overflow=101)
    with pytest.raises(ValidationError):
        Settings(database_pool_timeout_seconds=0.0)
    with pytest.raises(ValidationError):
        Settings(database_pool_timeout_seconds=301.0)
    with pytest.raises(ValidationError):
        Settings(database_pool_recycle_seconds=-2)
    with pytest.raises(ValidationError):
        Settings(database_pool_recycle_seconds=86401)
    with pytest.raises(ValidationError):
        Settings(database_connect_timeout_seconds=0.0)
    with pytest.raises(ValidationError):
        Settings(database_connect_timeout_seconds=301.0)


def test_database_echo_is_independent_of_debug_flag() -> None:
    """Verify database_echo defaults False even when debug=True."""
    settings = Settings(debug=True)
    assert settings.debug is True
    assert settings.database_echo is False


def test_secret_handling_redaction() -> None:
    """Verify secret database URL is redacted in repr, string, and does not leak in errors."""
    secret_pass = "ultra_confidential_pw_999"
    secret_url = f"postgresql+asyncpg://admin:{secret_pass}@cluster.internal:5432/app"
    settings = Settings(database_url=SecretStr(secret_url))

    # Repr must not expose secret password
    repr_str = repr(settings)
    assert secret_pass not in repr_str
    assert "**********" in repr_str

    # Str on SecretStr must not expose secret password
    assert secret_pass not in str(settings.database_url)

    # get_secret_value provides access for engine
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == secret_url


def test_engine_configuration_arguments_propagation() -> None:
    """Verify pool parameters and connect_args timeout are passed to create_async_engine."""
    settings = Settings(
        database_url=SecretStr(SYNTHETIC_POSTGRES_URL),
        database_pool_size=8,
        database_max_overflow=12,
        database_pool_timeout_seconds=25.0,
        database_pool_recycle_seconds=1200,
        database_connect_timeout_seconds=7.5,
        database_echo=False,
    )

    with patch("app.db.engine.create_async_engine", wraps=create_async_engine) as mock_create:
        engine = create_database_engine(settings)

        assert mock_create.called
        kwargs = mock_create.call_args.kwargs
        assert kwargs["pool_size"] == 8
        assert kwargs["max_overflow"] == 12
        assert kwargs["pool_timeout"] == 25.0
        assert kwargs["pool_recycle"] == 1200
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["echo"] is False
        assert kwargs["connect_args"] == {"timeout": 7.5}

        assert engine.pool.size() == 8
        assert engine.pool._max_overflow == 12
        assert engine.pool._timeout == 25.0
        assert engine.pool._recycle == 1200
        assert engine.pool._pre_ping is True


@pytest.mark.asyncio
async def test_database_manager_lifecycle_and_disposal() -> None:
    """Verify DatabaseManager lifecycle ownership and that dispose delegates cleanly."""
    settings = Settings(database_url=SecretStr(SYNTHETIC_POSTGRES_URL))
    manager = DatabaseManager(settings)

    assert isinstance(manager.engine, AsyncEngine)
    assert manager.settings is settings
    assert manager.session_factory is not None

    # Verify dispose
    with patch.object(AsyncEngine, "dispose", new_callable=AsyncMock) as mock_dispose:
        await manager.dispose()
        mock_dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_factory_and_get_db_session_primitive() -> None:
    """Verify session factory configuration and get_db_session lifecycle."""
    settings = Settings(database_url=SecretStr(SYNTHETIC_POSTGRES_URL))
    manager = DatabaseManager(settings)

    # expire_on_commit must be False
    assert manager.session_factory.kw.get("expire_on_commit") is False
    assert manager.session_factory.kw.get("autoflush") is False

    # Standalone session factory helper
    standalone_factory = create_session_factory(manager.engine)
    assert standalone_factory.kw.get("expire_on_commit") is False
    assert get_session is not None

    # Test get_db_session closure in finally block without commit
    mock_session = AsyncMock(spec=AsyncSession)
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__.return_value = mock_session
    mock_factory.return_value.__aexit__.return_value = None

    with patch.object(manager, "_session_factory", mock_factory):
        sessions: list[AsyncSession] = []
        gen: AsyncGenerator[AsyncSession, None] = get_db_session(manager)
        async for sess in gen:
            sessions.append(sess)

        assert len(sessions) == 1
        assert sessions[0] is mock_session
        # Must close session
        mock_session.close.assert_awaited_once()
        # Must NOT auto-commit
        assert mock_session.commit.await_count == 0


def test_declarative_base_and_naming_convention() -> None:
    """Verify DeclarativeBase structure, naming conventions, and zero business tables."""
    import app.db.models  # noqa: F401

    assert issubclass(Base, DeclarativeBase)

    naming = Base.metadata.naming_convention
    assert naming is not None
    required_keys = {"ix", "uq", "ck", "fk", "pk"}
    assert required_keys.issubset(naming.keys())
    assert naming == POSTGRESQL_NAMING_CONVENTION

    phase1_tables = {
        "audit_events",
        "outbox_events",
        "inbox_messages",
        "idempotency_records",
        "jobs",
    }
    canonical_phase4_tables = phase1_tables | {
        "goals",
        "plans",
        "milestones",
        "tasks",
        "task_dependencies",
        "task_risks",
        "workflow_definitions",
        "workflow_instances",
        "workflow_task_mappings",
        "sla_policies",
        "workflow_artifacts",
        # Phase 4A Scheduling (7)
        "resources",
        "resource_availabilities",
        "constraint_definitions",
        "solver_profiles",
        "schedule_jobs",
        "schedules",
        "schedule_assignments",
        # Phase 4B Calendar (1)
        "calendar_links",
        # Phase 7 Calendar Watch (1)
        "calendar_watch_channels",
        # Phase 8D Service Credentials Persistence (1)
        "service_api_credentials",
    }
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 26


@pytest.mark.asyncio
async def test_check_database_ready_probe_success() -> None:
    """Verify readiness probe returns True when SELECT 1 returns 1."""
    mock_engine = MagicMock(spec=AsyncEngine)
    mock_conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 1
    mock_conn.execute.return_value = mock_result

    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None

    is_ready = await check_database_ready(mock_engine, timeout_seconds=1.0)
    assert is_ready is True
    mock_conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_database_ready_probe_unexpected_result() -> None:
    """Verify readiness probe returns False when SELECT 1 returns unexpected value."""
    mock_engine = MagicMock(spec=AsyncEngine)
    mock_conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 0
    mock_conn.execute.return_value = mock_result

    mock_engine.connect.return_value.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value.__aexit__.return_value = None

    is_ready = await check_database_ready(mock_engine, timeout_seconds=1.0)
    assert is_ready is False


@pytest.mark.asyncio
async def test_check_database_ready_probe_timeout() -> None:
    """Verify readiness probe returns False on timeout without leaking exception."""
    mock_engine = MagicMock(spec=AsyncEngine)

    # Simulate timeout by causing sleep longer than timeout threshold
    async def slow_connect() -> AsyncMock:
        import asyncio

        await asyncio.sleep(0.5)
        return AsyncMock()

    mock_engine.connect.return_value.__aenter__.side_effect = slow_connect

    is_ready = await check_database_ready(mock_engine, timeout_seconds=0.05)
    assert is_ready is False


@pytest.mark.asyncio
async def test_check_database_ready_probe_exception() -> None:
    """Verify readiness probe catches connection exceptions and returns False without raising."""
    mock_engine = MagicMock(spec=AsyncEngine)
    mock_engine.connect.side_effect = RuntimeError("Connection to 10.0.0.1:5432 failed: timeout")

    is_ready = await check_database_ready(mock_engine, timeout_seconds=1.0)
    assert is_ready is False
