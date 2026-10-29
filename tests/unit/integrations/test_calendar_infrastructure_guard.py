"""Regression tests for calendar PostgreSQL infrastructure guard and CI failure policy.

Verifies:
1. CI environments fail explicitly if PostgreSQL is unreachable (no silent skips).
2. Offline local development skips gracefully on network unreachable / connection refused.
3. Authentication errors (InvalidPasswordError, etc.) NEVER silently skip.
4. Database configuration errors (InvalidCatalogNameError, etc.) NEVER silently skip.
5. Raw database URLs and credentials are NEVER leaked in logs or messages.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote_plus

import pytest
from pydantic import SecretStr

from tests.unit.integrations.calendar_db_guard import (
    is_ci_environment,
    mask_database_endpoint,
    verify_calendar_db_connection,
)

try:
    from asyncpg.exceptions import (
        InvalidCatalogNameError,
        InvalidPasswordError,
    )
except ImportError:  # pragma: no cover
    class InvalidCatalogNameError(Exception):  # type: ignore[no-redef]
        pass

    class InvalidPasswordError(Exception):  # type: ignore[no-redef]
        pass


def test_is_ci_environment_detection() -> None:
    """Verify CI environment variable detection."""
    with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": ""}, clear=False):
        assert is_ci_environment() is True

    with patch.dict(os.environ, {"CI": "1", "GITHUB_ACTIONS": ""}, clear=False):
        assert is_ci_environment() is True

    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": "true"}, clear=False):
        assert is_ci_environment() is True

    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False):
        assert is_ci_environment() is False


def test_mask_database_endpoint_redaction() -> None:
    """Verify endpoint masking strips all credentials, db names, and query parameters."""
    secret_pass = "SuperP@ssw0rd!#%^&"
    raw_url = f"postgresql+asyncpg://admin:{quote_plus(secret_pass)}@db-internal.host:5433/production_db?ssl=require"

    masked = mask_database_endpoint(raw_url)
    assert masked == "db-internal.host:5433"
    assert secret_pass not in masked
    assert "admin" not in masked
    assert "production_db" not in masked

    # SecretStr support
    masked_secret = mask_database_endpoint(SecretStr(raw_url))
    assert masked_secret == "db-internal.host:5433"
    assert secret_pass not in masked_secret


class MockConnectionContext:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc

    async def __aenter__(self) -> AsyncMock:
        if self.exc:
            raise self.exc
        return AsyncMock()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


def create_mock_engine(exc: Exception | None = None) -> MagicMock:
    engine = MagicMock()
    engine.connect.return_value = MockConnectionContext(exc)
    engine.dispose = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_ci_failure_policy_on_unreachable_db() -> None:
    """In CI environment, PostgreSQL connectivity failure must call pytest.fail()."""
    mock_engine = create_mock_engine(ConnectionRefusedError("[WinError 1225] Connection refused"))
    secret_url = "postgresql+asyncpg://ops_user:super_secret_pw@localhost:5432/testdb"

    with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": ""}, clear=False):
        with pytest.raises(pytest.fail.Exception) as exc_info:
            await verify_calendar_db_connection(mock_engine, secret_url)

        msg = str(exc_info.value)
        assert "CI requires running PostgreSQL service" in msg
        assert "localhost:5432" in msg
        assert "super_secret_pw" not in msg
        assert "ops_user" not in msg
        mock_engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_dev_skip_policy_on_network_offline() -> None:
    """In local offline development, network unreachable / refused calls pytest.skip()."""
    mock_engine = create_mock_engine(ConnectionRefusedError("Connection refused"))
    secret_url = "postgresql+asyncpg://ops_user:super_secret_pw@localhost:5432/testdb"

    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False):
        with pytest.raises(pytest.skip.Exception) as exc_info:
            await verify_calendar_db_connection(mock_engine, secret_url)

        msg = str(exc_info.value)
        assert "PostgreSQL service unreachable" in msg
        assert "localhost:5432" in msg
        assert "super_secret_pw" not in msg
        mock_engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_authentication_error_never_skips_even_locally() -> None:
    """Authentication failure (e.g. InvalidPasswordError) must fail, never skip."""
    auth_err = InvalidPasswordError("password authentication failed for user 'ops_user'")
    mock_engine = create_mock_engine(auth_err)
    secret_url = "postgresql+asyncpg://ops_user:wrong_password@localhost:5432/testdb"

    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False):
        with pytest.raises(pytest.fail.Exception) as exc_info:
            await verify_calendar_db_connection(mock_engine, secret_url)

        msg = str(exc_info.value)
        assert "PostgreSQL authentication failed" in msg
        assert "localhost:5432" in msg
        assert "wrong_password" not in msg


@pytest.mark.asyncio
async def test_configuration_error_never_skips_even_locally() -> None:
    """Database configuration error (e.g. database does not exist) must fail, never skip."""
    config_err = InvalidCatalogNameError('database "nonexistent_db" does not exist')
    mock_engine = create_mock_engine(config_err)
    secret_url = "postgresql+asyncpg://ops_user:password@localhost:5432/nonexistent_db"

    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False):
        with pytest.raises(pytest.fail.Exception) as exc_info:
            await verify_calendar_db_connection(mock_engine, secret_url)

        msg = str(exc_info.value)
        assert "PostgreSQL configuration error" in msg
        assert "localhost:5432" in msg


@pytest.mark.asyncio
async def test_unclassified_error_never_skips() -> None:
    """Unclassified runtime exceptions must fail, never skip."""
    mock_engine = create_mock_engine(RuntimeError("Corrupted memory segment"))
    secret_url = "postgresql+asyncpg://ops_user:password@localhost:5432/testdb"

    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False):
        with pytest.raises(pytest.fail.Exception) as exc_info:
            await verify_calendar_db_connection(mock_engine, secret_url)

        msg = str(exc_info.value)
        assert "unclassified error" in msg
        assert "localhost:5432" in msg
