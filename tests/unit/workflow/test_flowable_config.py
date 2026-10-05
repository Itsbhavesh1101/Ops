"""Unit tests for Flowable runtime configuration and fail-clearly validation."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.flowable.config import (
    FLOWABLE_SERVICE_AUTH_PRODUCTION,
    FlowableConfigurationError,
    FlowableSettings,
)


def test_flowable_settings_requires_explicit_arguments() -> None:
    """Verify that FlowableSettings has zero silent production defaults for core parameters."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValidationError) as exc_info:
            FlowableSettings()  # type: ignore[call-arg]

        errors = exc_info.value.errors()
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert "base_url" in missing_fields
        assert "timeout_seconds" in missing_fields
        assert "max_retries" in missing_fields


def test_flowable_settings_from_env_fails_clearly_when_missing() -> None:
    """Verify that from_env() raises typed FlowableConfigurationError
    when required env vars are absent.
    """
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(FlowableConfigurationError) as exc_info:
            FlowableSettings.from_env()

        assert "Missing or invalid required Flowable runtime configuration" in str(exc_info.value)


def test_flowable_adapter_fails_clearly_without_configuration() -> None:
    """Verify that constructing FlowableAdapter without settings raises typed error."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(FlowableConfigurationError):
            FlowableAdapter()


def test_flowable_settings_explicit_instantiation() -> None:
    """Verify explicit configuration passes validation and masks secrets."""
    settings = FlowableSettings(
        base_url="http://flowable-cluster:8080/flowable-rest/service",
        timeout_seconds=15.0,
        max_retries=3,
        username="workflow-svc",
        password="super-secret-password",
    )
    assert settings.base_url == "http://flowable-cluster:8080/flowable-rest/service"
    assert settings.timeout_seconds == 15.0
    assert settings.max_retries == 3
    assert settings.username == "workflow-svc"
    assert settings.password is not None
    assert settings.password.get_secret_value() == "super-secret-password"
    assert "super-secret-password" not in str(settings)
    assert "super-secret-password" not in repr(settings)


def test_flowable_settings_from_env_success() -> None:
    """Verify environment variables correctly populate FlowableSettings."""
    env = {
        "FLOWABLE_BASE_URL": "http://flowable-prod:8080/flowable-rest/service",
        "FLOWABLE_TIMEOUT_SECONDS": "8.5",
        "FLOWABLE_MAX_RETRIES": "2",
        "FLOWABLE_USERNAME": "prod-user",
        "FLOWABLE_PASSWORD": "prod-password",
    }
    with patch.dict(os.environ, env, clear=True):
        settings = FlowableSettings.from_env()
        assert settings.base_url == "http://flowable-prod:8080/flowable-rest/service"
        assert settings.timeout_seconds == 8.5
        assert settings.max_retries == 2
        assert settings.username == "prod-user"
        assert settings.password is not None
        assert settings.password.get_secret_value() == "prod-password"


def test_flowable_service_auth_production_marker() -> None:
    """Verify production service auth deferral marker."""
    assert FLOWABLE_SERVICE_AUTH_PRODUCTION == "DEFERRED_PENDING_DEPLOYMENT_SECRET_CONFIGURATION"


def test_flowable_settings_missing_all_auth_fails() -> None:
    """Verify that providing base_url, timeout, and retries without auth fails clearly."""
    with pytest.raises(ValidationError) as exc_info:
        FlowableSettings(
            base_url="http://flowable:8080/flowable-rest/service",
            timeout_seconds=10.0,
            max_retries=2,
        )
    assert "Flowable authentication is required" in str(exc_info.value)


def test_flowable_settings_username_without_password_fails() -> None:
    """Verify that providing username without password fails validation."""
    with pytest.raises(ValidationError) as exc_info:
        FlowableSettings(
            base_url="http://flowable:8080/flowable-rest/service",
            timeout_seconds=10.0,
            max_retries=2,
            username="admin-only",
        )
    assert "Flowable Basic auth requires both username and password" in str(exc_info.value)


def test_flowable_settings_password_without_username_fails() -> None:
    """Verify that providing password without username fails validation."""
    with pytest.raises(ValidationError) as exc_info:
        FlowableSettings(
            base_url="http://flowable:8080/flowable-rest/service",
            timeout_seconds=10.0,
            max_retries=2,
            password="secret-only",
        )
    assert "Flowable Basic auth requires both username and password" in str(exc_info.value)


def test_flowable_settings_valid_bearer_auth_passes() -> None:
    """Verify that providing valid Bearer token passes and sets auth_mode to 'bearer'."""
    settings = FlowableSettings(
        base_url="http://flowable:8080/flowable-rest/service",
        timeout_seconds=10.0,
        max_retries=2,
        token="my-secure-bearer-token",
    )
    assert settings.auth_mode == "bearer"
    assert settings.token is not None
    assert settings.token.get_secret_value() == "my-secure-bearer-token"
    assert settings.username is None
    assert settings.password is None


def test_flowable_settings_ambiguous_basic_and_bearer_fails() -> None:
    """Verify that providing both Basic auth and Bearer token fails validation."""
    with pytest.raises(ValidationError) as exc_info:
        FlowableSettings(
            base_url="http://flowable:8080/flowable-rest/service",
            timeout_seconds=10.0,
            max_retries=2,
            username="admin",
            password="pwd",
            token="token123",
        )
    assert "Ambiguous Flowable authentication configuration" in str(exc_info.value)


def test_flowable_settings_token_secret_masking() -> None:
    """Verify that Bearer tokens are masked in str and repr representations."""
    settings = FlowableSettings(
        base_url="http://flowable:8080/flowable-rest/service",
        timeout_seconds=10.0,
        max_retries=2,
        token="super-secret-token-xyz",
    )
    assert "super-secret-token-xyz" not in str(settings)
    assert "super-secret-token-xyz" not in repr(settings)
