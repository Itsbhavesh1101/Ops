"""Pytest configuration and live infrastructure preconditions for Phase 6 E2E suite.

Part of Phase 6 Stage P (Live E2E Integration).
Controlled by opt-in environment flag:
    E7_RUN_PHASE6_E2E=1

Invariants:
- When E7_RUN_PHASE6_E2E is NOT set: skips cleanly at module level.
- When E7_RUN_PHASE6_E2E IS set: requires live PostgreSQL and Flowable.
  If required infrastructure is unavailable: FAILS explicitly (never silently skipped).
"""

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

CANONICAL_APP_DB = "operations_engine"
ADMIN_DB = "postgres"

FLOWABLE_BASE_URL = os.environ.get(
    "FLOWABLE_BASE_URL", "http://localhost:8080/flowable-rest/service"
)
FLOWABLE_USER = os.environ.get("FLOWABLE_USER", "rest-admin")
FLOWABLE_PASSWORD = os.environ.get("FLOWABLE_PASSWORD", "test")

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(
    os.environ.get("E7_RABBITMQ_INTEGRATION_PORT") or os.environ.get("RABBITMQ_PORT") or "5672"
)


def normalize_to_sync_probe_url(url: str) -> str:
    """Normalize postgresql+asyncpg URL to postgresql for probe without changing db."""
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql://"):
        return url
    raise ValueError(
        f"Unsupported scheme for PostgreSQL probe in URL: {url.split('://', 1)[0]}"
    )


def extract_db_name(url: str) -> str:
    """Extract database name from a database URL."""
    parsed = urlsplit(url)
    return parsed.path.lstrip("/").split("?")[0]


def resolve_phase6_database_urls() -> tuple[str, str, str]:
    """Resolve the canonical application database URLs for Phase 6 live E2E.

    Authority hierarchy:
    1. E7_DATABASE_URL (preferred authority)
    2. Explicit canonical fallback: If E7_DATABASE_URL is absent, but POSTGRES_DB
       is 'operations_engine', construct canonical URL from POSTGRES_* environment variables.

    Fails closed if:
    - E7_DATABASE_URL is absent and no explicit approved fallback exists.
    - The resolved database is not 'operations_engine' (e.g. administrative 'postgres').

    Returns:
        tuple[canonical_async_url, probe_url, db_name]
    """
    raw_url = os.environ.get("E7_DATABASE_URL")
    if raw_url and raw_url.strip():
        url = raw_url.strip()
        db_name = extract_db_name(url)
        if not db_name:
            raise ValueError("E7_DATABASE_URL has empty database name.")
        if db_name == ADMIN_DB:
            raise ValueError(
                f"Resolved database is administrative '{db_name}'. "
                f"Phase 6 live E2E requires canonical application database '{CANONICAL_APP_DB}'."
            )
        if db_name != CANONICAL_APP_DB:
            raise ValueError(
                f"Resolved database '{db_name}' does not match "
                f"canonical application database '{CANONICAL_APP_DB}'."
            )

        if url.startswith("postgresql+asyncpg://"):
            canonical_async_url = url
            probe_url = normalize_to_sync_probe_url(url)
        elif url.startswith("postgresql://"):
            canonical_async_url = "postgresql+asyncpg://" + url[len("postgresql://") :]
            probe_url = url
        else:
            raise ValueError(
                f"Unsupported database URL scheme '{url.split('://', 1)[0]}'. "
                f"Expected 'postgresql+asyncpg' or 'postgresql'."
            )

        return canonical_async_url, probe_url, db_name

    postgres_db = os.environ.get("POSTGRES_DB")
    if postgres_db == CANONICAL_APP_DB:
        host = os.environ.get("POSTGRES_HOST", "localhost")
        port = int(
            os.environ.get("E7_POSTGRES_INTEGRATION_PORT")
            or os.environ.get("POSTGRES_PORT")
            or "55432"
        )
        user = os.environ.get("POSTGRES_USER", "postgres")
        password = os.environ.get("POSTGRES_PASSWORD", "postgres")
        canonical_async_url = (
            f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{CANONICAL_APP_DB}"
        )
        probe_url = f"postgresql://{user}:{password}@{host}:{port}/{CANONICAL_APP_DB}"
        return canonical_async_url, probe_url, CANONICAL_APP_DB

    raise ValueError(
        "E7_DATABASE_URL is absent and no explicit approved fallback exists. "
        f"Phase 6 live E2E requires canonical application database '{CANONICAL_APP_DB}'."
    )


def is_phase6_e2e_enabled() -> bool:
    """Check if Phase 6 E2E live suite is opted in."""
    return os.environ.get("E7_RUN_PHASE6_E2E") == "1"


@pytest.fixture(scope="session", autouse=True)
def check_phase6_e2e_opt_in() -> None:
    """Skip suite if E7_RUN_PHASE6_E2E=1 is not set."""
    if not is_phase6_e2e_enabled():
        pytest.skip(
            "Phase 6 live E2E tests require E7_RUN_PHASE6_E2E=1",
            allow_module_level=True,
        )


@pytest_asyncio.fixture(scope="session")
async def require_live_infrastructure() -> dict[str, Any]:
    """Verify live PostgreSQL, Flowable, and RabbitMQ availability when opted in.

    Per Phase-6 Rule 61: If required infrastructure is unavailable: FAIL.
    Do not silently skip requested E2E execution.
    """
    # 0. Resolve canonical application database URLs fail-closed
    try:
        canonical_async_url, probe_url, db_name = resolve_phase6_database_urls()
    except Exception as exc:
        pytest.fail(f"Phase 6 live E2E database configuration failure: {exc}")

    # 1. PostgreSQL check against canonical application database
    try:
        conn = await asyncpg.connect(probe_url, timeout=5.0)
        await conn.execute("SELECT 1;")
        await conn.close()
    except Exception as exc:
        pytest.fail(
            f"Phase 6 E2E requested (E7_RUN_PHASE6_E2E=1) but PostgreSQL application database "
            f"unavailable at {probe_url}: {exc}"
        )

    # 2. Flowable REST check
    url = f"{FLOWABLE_BASE_URL.rstrip('/')}/repository/deployments"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, auth=(FLOWABLE_USER, FLOWABLE_PASSWORD))
            if resp.status_code != 200:
                pytest.fail(
                    f"Phase 6 E2E requested (E7_RUN_PHASE6_E2E=1) but Flowable returned "
                    f"HTTP {resp.status_code} at {url}"
                )
    except Exception as exc:
        pytest.fail(
            f"Phase 6 E2E requested (E7_RUN_PHASE6_E2E=1) but Flowable unavailable at {url}: {exc}"
        )

    # 3. RabbitMQ check (required for production-shaped event transport)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(RABBITMQ_HOST, RABBITMQ_PORT),
            timeout=5.0,
        )
        writer.close()
        await writer.wait_closed()
    except Exception as exc:
        pytest.fail(
            f"Phase 6 E2E requested (E7_RUN_PHASE6_E2E=1) but RabbitMQ unavailable "
            f"at {RABBITMQ_HOST}:{RABBITMQ_PORT}: {exc}"
        )

    return {
        "postgres": probe_url,
        "postgres_async": canonical_async_url,
        "database_name": db_name,
        "flowable": FLOWABLE_BASE_URL,
        "rabbitmq": os.environ.get(
            "E7_RABBITMQ_URL", f"amqp://guest:guest@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
        ),
    }
