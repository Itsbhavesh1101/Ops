"""Focused regression tests for Phase 6 live E2E database URL resolution.

Ensures:
1. When E7_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/operations_engine,
   business DB resolves to 'operations_engine' and is NEVER rewritten to administrative 'postgres'.
2. Dialect normalization changes only postgresql+asyncpg -> postgresql without altering host, port,
   credentials, or database path.
3. Fail-closed rejection when E7_DATABASE_URL points to administrative 'postgres' or unknown DB.
4. Fail-closed rejection when E7_DATABASE_URL is absent and no approved fallback exists.
5. Approved fallback to 'operations_engine' when POSTGRES_DB is explicitly 'operations_engine'.
"""

import os
from unittest.mock import patch

import pytest

from tests.e2e.phase6.conftest import (
    CANONICAL_APP_DB,
    extract_db_name,
    normalize_to_sync_probe_url,
    resolve_phase6_database_urls,
)


def test_resolve_canonical_db_url_from_e7_database_url() -> None:
    """Verify E7_DATABASE_URL resolves to operations_engine and never rewrites to postgres."""
    live_url = "postgresql+asyncpg://postgres:postgres@localhost:55432/operations_engine"
    with patch.dict(os.environ, {"E7_DATABASE_URL": live_url}, clear=True):
        canonical_async, probe_url, db_name = resolve_phase6_database_urls()

        assert db_name == CANONICAL_APP_DB
        assert db_name == "operations_engine"
        assert db_name != "postgres"

        assert canonical_async == live_url
        assert probe_url == "postgresql://postgres:postgres@localhost:55432/operations_engine"
        assert "postgres:55432/postgres" not in probe_url


def test_dialect_normalization_changes_only_driver() -> None:
    """Verify dialect normalization changes only postgresql+asyncpg -> postgresql."""
    input_url = "postgresql+asyncpg://admin_user:secret_pw@db.internal:55432/operations_engine?sslmode=disable"
    normalized = normalize_to_sync_probe_url(input_url)

    expected = "postgresql://admin_user:secret_pw@db.internal:55432/operations_engine?sslmode=disable"
    assert normalized == expected
    assert normalized.startswith("postgresql://")
    assert not normalized.startswith("postgresql+asyncpg://")

    # If already postgresql://, returns unmodified
    assert normalize_to_sync_probe_url(expected) == expected


def test_extract_db_name() -> None:
    """Verify database name extraction correctly isolates database identifier."""
    assert extract_db_name("postgresql://localhost:5432/operations_engine") == "operations_engine"
    assert (
        extract_db_name("postgresql+asyncpg://user:pass@host:5432/operations_engine?opt=1")
        == "operations_engine"
    )
    assert extract_db_name("postgresql://localhost:5432/postgres") == "postgres"


def test_fails_closed_when_pointing_to_admin_postgres_db() -> None:
    """Verify resolving administrative 'postgres' database is rejected fail-closed."""
    admin_url = "postgresql+asyncpg://postgres:postgres@localhost:55432/postgres"
    with patch.dict(os.environ, {"E7_DATABASE_URL": admin_url}, clear=True):
        with pytest.raises(ValueError, match="Resolved database is administrative 'postgres'"):
            resolve_phase6_database_urls()


def test_fails_closed_when_db_is_not_canonical_operations_engine() -> None:
    """Verify resolving any database other than operations_engine is rejected fail-closed."""
    wrong_db_url = "postgresql+asyncpg://postgres:postgres@localhost:55432/other_app_db"
    with patch.dict(os.environ, {"E7_DATABASE_URL": wrong_db_url}, clear=True):
        with pytest.raises(
            ValueError, match="does not match canonical application database 'operations_engine'"
        ):
            resolve_phase6_database_urls()


def test_fails_closed_when_e7_database_url_absent_and_no_fallback() -> None:
    """Verify resolution fails closed when E7_DATABASE_URL is absent and POSTGRES_DB is unset."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(
            ValueError, match="E7_DATABASE_URL is absent and no explicit approved fallback exists"
        ):
            resolve_phase6_database_urls()


def test_fallback_when_postgres_db_is_explicitly_operations_engine() -> None:
    """Verify fallback constructs canonical operations_engine URL when POSTGRES_DB is set."""
    env = {
        "POSTGRES_DB": "operations_engine",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "55432",
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "postgres",
    }
    with patch.dict(os.environ, env, clear=True):
        canonical_async, probe_url, db_name = resolve_phase6_database_urls()
        assert db_name == "operations_engine"
        assert canonical_async == "postgresql+asyncpg://postgres:postgres@localhost:55432/operations_engine"
        assert probe_url == "postgresql://postgres:postgres@localhost:55432/operations_engine"


def test_fails_closed_on_unsupported_scheme() -> None:
    """Verify unsupported scheme raises ValueError."""
    bad_url = "mysql+pymysql://root:pass@localhost:3306/operations_engine"
    with patch.dict(os.environ, {"E7_DATABASE_URL": bad_url}, clear=True):
        with pytest.raises(ValueError, match=r"Unsupported database URL scheme 'mysql\+pymysql'"):
            resolve_phase6_database_urls()
