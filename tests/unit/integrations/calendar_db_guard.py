"""PostgreSQL connection guard for calendar integration tests.

Enforces strict test reliability policy:
1. CI environments (CI=true or GITHUB_ACTIONS=true) must fail when PostgreSQL is unreachable.
2. Authentication and configuration errors must NEVER silently become skips.
3. Raw database URLs or credentials must NEVER be logged or leaked in skip messages.
4. Only offline local development may skip on network unreachable / connection refused.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

try:
    import asyncpg.exceptions as pg_exc

    PG_AUTH_EXCEPTIONS: tuple[type[BaseException], ...] = (
        pg_exc.InvalidPasswordError,
        pg_exc.InvalidAuthorizationSpecificationError,
    )
    PG_CONFIG_EXCEPTIONS: tuple[type[BaseException], ...] = (
        pg_exc.InvalidCatalogNameError,
    )
except ImportError:  # pragma: no cover
    PG_AUTH_EXCEPTIONS = ()
    PG_CONFIG_EXCEPTIONS = ()


def is_ci_environment() -> bool:
    """Check if execution is running in a continuous integration environment."""
    return (
        os.getenv("CI", "").strip().lower() in ("true", "1")
        or bool(os.getenv("GITHUB_ACTIONS"))
    )


def mask_database_endpoint(url: str | SecretStr) -> str:
    """Extract host and port from database URL without exposing credentials."""
    raw = url.get_secret_value() if isinstance(url, SecretStr) else str(url)
    try:
        parsed = urlparse(raw)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        return f"{host}:{port}"
    except Exception:
        return "localhost:5432"


async def verify_calendar_db_connection(
    engine: AsyncEngine,
    db_url: str | SecretStr,
) -> None:
    """Verify database connectivity with strict failure policy.

    Rules:
    1. If in CI, connection failure MUST fail the test via pytest.fail().
    2. Authentication and configuration errors must NEVER silently skip, even locally.
    3. Only connection/network unreachable errors in offline local environments may skip.
    4. Raw database URLs or credentials must NEVER be logged or included in messages.
    """
    endpoint = mask_database_endpoint(db_url)
    in_ci = is_ci_environment()

    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        exc_type = type(exc).__name__

        # Unwrap SQLAlchemy wrappers
        orig = getattr(exc, "orig", None)
        cause = getattr(exc, "__cause__", None)
        root_exc = orig or cause or exc

        # 1. Detect authentication or configuration errors
        is_auth_error = (
            (bool(PG_AUTH_EXCEPTIONS) and isinstance(root_exc, PG_AUTH_EXCEPTIONS))
            or "password" in str(root_exc).lower()
            or "authentication" in str(root_exc).lower()
        )

        is_config_error = (
            (bool(PG_CONFIG_EXCEPTIONS) and isinstance(root_exc, PG_CONFIG_EXCEPTIONS))
            or isinstance(root_exc, (ValueError, KeyError))
            or ("database" in str(root_exc).lower() and "does not exist" in str(root_exc).lower())
        )

        if is_auth_error:
            pytest.fail(
                f"PostgreSQL authentication failed at {endpoint}: {type(root_exc).__name__}"
            )

        if is_config_error:
            pytest.fail(
                f"PostgreSQL configuration error at {endpoint}: {type(root_exc).__name__}"
            )

        # 2. In CI environment, PostgreSQL is required and must not be skipped
        if in_ci:
            pytest.fail(
                f"CI requires running PostgreSQL service, but connection failed at "
                f"{endpoint}: {exc_type}"
            )

        # 3. Offline local development: check for network unreachable / connection refused
        is_network_offline = (
            isinstance(
                root_exc,
                (
                    ConnectionRefusedError,
                    OSError,
                ),
            )
            or any(
                phrase in str(root_exc).lower()
                for phrase in (
                    "connection refused",
                    "cannot connect",
                    "network connection",
                    "winerror 1225",
                    "errno 111",
                )
            )
        )

        if is_network_offline:
            pytest.skip(
                f"PostgreSQL service unreachable at {endpoint} in offline local development"
            )

        # Any other unclassified error must fail rather than silently skip
        pytest.fail(
            f"PostgreSQL connection failed with unclassified error at {endpoint}: {exc_type}"
        )
