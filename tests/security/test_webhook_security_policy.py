"""Regression tests for Webhook Security Infrastructure Policy (DQ-5 Hardening).

Validates:
1. When running in CI (CI=true or GITHUB_ACTIONS=true), PostgreSQL connection failure
   fails the test via pytest.fail() instead of skipping.
2. When running offline locally, connection refused skips gracefully.
3. Database authentication failure fails explicitly (never skips).
4. Database configuration error fails explicitly (never skips).
5. Database connection credentials/passwords are never leaked in error messages.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote_plus

import pytest
from pydantic import SecretStr

from tests.unit.integrations.calendar_db_guard import (
    is_ci_environment,
    mask_database_endpoint,
    verify_calendar_db_connection,
)


def test_webhook_security_ci_environment_detected() -> None:
    """Verify CI environment detection flags."""
    with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": ""}, clear=False):
        assert is_ci_environment() is True
    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": "1"}, clear=False):
        assert is_ci_environment() is True
    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False):
        assert is_ci_environment() is False


@pytest.mark.asyncio
async def test_webhook_security_ci_requires_postgres_fails() -> None:
    """In CI environment, PostgreSQL unavailability MUST fail explicitly."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = ConnectionRefusedError("[WinError 1225] Connection refused")
    mock_engine.dispose = AsyncMock()

    db_url = SecretStr("postgresql+asyncpg://admin:secret123@localhost:5432/e7_ops")

    with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": ""}, clear=False):
        with pytest.raises(pytest.fail.Exception, match="CI requires running PostgreSQL"):
            await verify_calendar_db_connection(mock_engine, db_url)


@pytest.mark.asyncio
async def test_webhook_security_local_offline_skips() -> None:
    """In local offline development, connection refused skips gracefully."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = ConnectionRefusedError("[WinError 1225] Connection refused")
    mock_engine.dispose = AsyncMock()

    db_url = SecretStr("postgresql+asyncpg://admin:secret123@localhost:5432/e7_ops")

    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False):
        with pytest.raises(pytest.skip.Exception, match="offline local development"):
            await verify_calendar_db_connection(mock_engine, db_url)


@pytest.mark.asyncio
async def test_webhook_security_auth_failure_always_fails() -> None:
    """Authentication errors MUST fail explicitly even in local development."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = RuntimeError("password authentication failed for user")
    mock_engine.dispose = AsyncMock()

    db_url = SecretStr("postgresql+asyncpg://admin:secret123@localhost:5432/e7_ops")

    with patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False):
        with pytest.raises(pytest.fail.Exception, match="authentication failed"):
            await verify_calendar_db_connection(mock_engine, db_url)


def test_webhook_security_credentials_never_exposed() -> None:
    """Database credentials and secrets are stripped and masked."""
    secret = "SuperSecretPassword123!"
    url = f"postgresql+asyncpg://user:{quote_plus(secret)}@db.internal.host:5433/prod_db"
    masked = mask_database_endpoint(url)
    assert secret not in masked
    assert "user" not in masked
    assert masked == "db.internal.host:5433"

