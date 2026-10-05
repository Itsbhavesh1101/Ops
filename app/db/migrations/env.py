"""Alembic async migration environment.

Part of Phase 1 Foundation Task 1D.
Dynamically resolves runtime database URL from Settings / E7_DATABASE_URL environment,
targets Base.metadata, and supports both offline and online async migrations.
"""

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from pydantic import SecretStr
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.db.models  # noqa: F401
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.engine import DatabaseConfigurationError, validate_postgresql_async_url


def is_context_active() -> bool:
    """Return True if running within an active Alembic runner EnvironmentContext."""
    try:
        return getattr(context, "_proxy", None) is not None
    except Exception:
        return False


# Alembic Config object providing access to values within alembic.ini (when executed via runner)
if is_context_active():
    config: Any = context.config
    if config is not None and getattr(config, "config_file_name", None) is not None:
        fileConfig(config.config_file_name)
else:
    config = None

# Model MetaData object for autogenerate support
target_metadata = Base.metadata


def get_url(alembic_config: Any = None) -> str:
    """Retrieve canonical PostgreSQL async database URL for Alembic migrations.

    Resolves URL strictly via application Settings and validates against the
    canonical PostgreSQL async scheme validation policy (rejecting sqlite, mysql, mariadb).
    Ensures no real database URL or credentials are ever stored in alembic.ini.
    """
    raw_url: str | None = None

    # 1. Direct option passed via alembic CLI / config (-x url=... or alembic.ini override)
    active_config = alembic_config or config
    if active_config is not None:
        url_opt = active_config.get_main_option("sqlalchemy.url")
        if (
            url_opt
            and url_opt.strip()
            and not url_opt.startswith("<")
            and "placeholder" not in url_opt.lower()
        ):
            settings_override = Settings(database_url=SecretStr(url_opt.strip()))
            if settings_override.database_url:
                raw_url = settings_override.database_url.get_secret_value()

    # 2. Application settings (canonical source, reads E7_DATABASE_URL from env)
    if not raw_url:
        try:
            settings = get_settings()
            if settings.database_url:
                raw_url = settings.database_url.get_secret_value()
        except Exception:
            pass

    # 3. Environment variable fallback (in case Settings was cached prior to env modification)
    if not raw_url:
        env_url = os.environ.get("E7_DATABASE_URL")
        if (
            env_url
            and env_url.strip()
            and not env_url.startswith("<")
            and "placeholder" not in env_url.lower()
        ):
            env_settings = Settings(database_url=SecretStr(env_url.strip()))
            if env_settings.database_url:
                raw_url = env_settings.database_url.get_secret_value()

    if not raw_url:
        raise DatabaseConfigurationError(
            "Database URL is not configured for Alembic migrations. "
            "Set E7_DATABASE_URL or configure Settings.database_url."
        )

    # Canonical PostgreSQL async validation policy (rejects sqlite, mysql, mariadb)
    validate_postgresql_async_url(raw_url)
    return raw_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and emits SQL statements to script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations within an active synchronous connection context."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an AsyncEngine and associate a connection with the context for online execution."""
    url = get_url()
    connectable = create_async_engine(
        url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


# Only execute migrations when invoked by the active Alembic runner
if is_context_active():
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
