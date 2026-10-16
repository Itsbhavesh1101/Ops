"""PostgreSQL async engine factory and DatabaseManager lifecycle owner.

Part of Phase 1 Foundation Task 1D.
Provides explicit lifecycle ownership, safe connection configuration,
and connection pooling without importing or connecting on module load.
"""

from typing import TYPE_CHECKING, Any

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    pass


class DatabaseConfigurationError(ValueError):
    """Raised when database settings or connection URLs are missing or invalid.

    Inherits from ValueError for broad compatibility with configuration validation.
    Guarantees that sensitive credentials or connection strings are never echoed in messages.
    """


class DatabaseUnavailableError(RuntimeError):
    """Raised when the database cannot be reached or fails readiness verification."""


def validate_postgresql_async_url(raw_url: str) -> None:
    """Validate that the database URL scheme is strictly 'postgresql+asyncpg'.

    Rejects SQLite, MySQL, MariaDB, and synchronous PostgreSQL drivers.
    Does not echo credential-bearing connection strings in error messages.
    """
    if not raw_url or not raw_url.strip():
        raise DatabaseConfigurationError(
            "E7_DATABASE_URL is not configured. A valid 'postgresql+asyncpg://' URL is required."
        )

    scheme = raw_url.split("://", 1)[0].lower() if "://" in raw_url else ""
    if scheme != "postgresql+asyncpg":
        raise DatabaseConfigurationError(
            f"Unsupported database URL scheme '{scheme}'. Only 'postgresql+asyncpg' is supported."
        )


def create_database_engine(
    settings: Settings | None = None,
    database_url: str | SecretStr | None = None,
) -> AsyncEngine:
    """Create a configured SQLAlchemy AsyncEngine bound to PostgreSQL.

    Configures connection pool dimensions, recycle, timeouts, and pre-ping.
    Does NOT initiate an active connection to the database at call time.

    Args:
        settings: Application Settings instance. Defaults to get_settings().
        database_url: Optional explicit URL override (string or SecretStr).

    Returns:
        AsyncEngine: Configured async SQLAlchemy engine.

    Raises:
        DatabaseConfigurationError: If database URL is missing or scheme is unsupported.
    """
    cfg = settings or get_settings()

    raw_url: str | None = None
    if database_url is not None:
        if isinstance(database_url, SecretStr):
            raw_url = database_url.get_secret_value()
        else:
            raw_url = str(database_url)
    elif cfg.database_url is not None:
        raw_url = cfg.database_url.get_secret_value()

    if not raw_url:
        raise DatabaseConfigurationError(
            "E7_DATABASE_URL is not configured. A valid 'postgresql+asyncpg://' URL is required."
        )

    validate_postgresql_async_url(raw_url)

    connect_args: dict[str, Any] = {
        "timeout": cfg.database_connect_timeout_seconds,
    }

    engine = create_async_engine(
        raw_url,
        pool_size=cfg.database_pool_size,
        max_overflow=cfg.database_max_overflow,
        pool_timeout=cfg.database_pool_timeout_seconds,
        pool_recycle=cfg.database_pool_recycle_seconds,
        pool_pre_ping=True,
        echo=cfg.database_echo,
        connect_args=connect_args,
    )
    return engine


class DatabaseManager:
    """Explicit lifecycle owner for SQLAlchemy AsyncEngine and sessionmaker.

    Responsibilities:
    - Owns AsyncEngine and manages its disposal.
    - Owns async_sessionmaker and provides fresh AsyncSession creation.
    - Exposes non-leaking readiness check.
    - Guarantees no engine or connection exists at module import time.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        database_url: str | SecretStr | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._engine: AsyncEngine = create_database_engine(
            settings=self._settings,
            database_url=database_url,
        )
        # Avoid circular import by creating sessionmaker directly
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        """Return the managed AsyncEngine instance."""
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the managed async_sessionmaker instance."""
        return self._session_factory

    @property
    def settings(self) -> Settings:
        """Return the Settings instance used to construct this manager."""
        return self._settings

    async def dispose(self) -> None:
        """Dispose of the underlying AsyncEngine connection pool cleanly."""
        await self._engine.dispose()

    async def check_readiness(self, timeout_seconds: float = 3.0) -> bool:
        """Execute low-level SELECT 1 readiness probe."""
        from app.db.health import check_database_ready

        return await check_database_ready(self._engine, timeout_seconds=timeout_seconds)

    def get_pool_status(self) -> dict[str, int]:
        """Return low-cardinality pool status metrics for observability.

        Reports pool size, checked out connections, overflow count, and idle connections.
        Zero exposure of credentials, database URLs, or tenant identities.
        """
        pool = self._engine.sync_engine.pool
        size_fn = getattr(pool, "size", None)
        checkedout_fn = getattr(pool, "checkedout", None)
        overflow_fn = getattr(pool, "overflow", None)
        checkedin_fn = getattr(pool, "checkedin", None)
        return {
            "size": int(size_fn()) if callable(size_fn) else 0,
            "checked_out": int(checkedout_fn()) if callable(checkedout_fn) else 0,
            "overflow": int(overflow_fn()) if callable(overflow_fn) else 0,
            "checked_in": int(checkedin_fn()) if callable(checkedin_fn) else 0,
        }
