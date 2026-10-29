"""Unit tests for runtime configuration module (app/core/config.py)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_default_settings_load() -> None:
    """Verify default technical configuration settings."""
    settings = Settings(_env_file=None)
    assert settings.app_name == "IR Infotech E-7 Operations Engine"
    assert settings.environment == "local"
    assert settings.debug is False
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.correlation_header_name == "X-Correlation-ID"
    assert settings.cors_allowed_origins == []


def test_cached_settings_accessor() -> None:
    """Verify get_settings returns cached single instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify environment variables override defaults with E7_ prefix."""
    monkeypatch.setenv("E7_APP_NAME", "Custom E7 Engine")
    monkeypatch.setenv("E7_ENVIRONMENT", "development")
    monkeypatch.setenv("E7_DEBUG", "true")
    monkeypatch.setenv("E7_LOG_LEVEL", "warning")
    monkeypatch.setenv("E7_LOG_FORMAT", "console")

    settings = Settings()
    assert settings.app_name == "Custom E7 Engine"
    assert settings.environment == "development"
    assert settings.debug is True
    assert settings.log_level == "WARNING"  # normalized to uppercase
    assert settings.log_format == "console"


def test_invalid_environment_rejected() -> None:
    """Verify unapproved environment names fail validation."""
    with pytest.raises(ValidationError):
        Settings(environment="invalid_env")  # type: ignore[arg-type]


def test_invalid_log_level_rejected() -> None:
    """Verify invalid log level values fail validation."""
    with pytest.raises(ValidationError):
        Settings(log_level="VERBOSE")  # type: ignore[arg-type]


def test_invalid_log_format_rejected() -> None:
    """Verify invalid log format values fail validation."""
    with pytest.raises(ValidationError):
        Settings(log_format="xml")  # type: ignore[arg-type]


def test_cors_origins_parsing() -> None:
    """Verify parsing CORS allowed origins from list, comma-delimited string, or JSON array."""
    # Comma-separated
    s1 = Settings(cors_allowed_origins="http://localhost:3000, https://app.example.com")  # type: ignore[arg-type]
    assert s1.cors_allowed_origins == ["http://localhost:3000", "https://app.example.com"]

    # JSON array string
    s2 = Settings(cors_allowed_origins='["https://portal.domain.com"]')  # type: ignore[arg-type]
    assert s2.cors_allowed_origins == ["https://portal.domain.com"]

    # List of strings
    s3 = Settings(cors_allowed_origins=["https://direct.org"])
    assert s3.cors_allowed_origins == ["https://direct.org"]


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_and_production_debug_rejected(env: str) -> None:
    """Verify debug=True is strictly rejected in staging and production."""
    with pytest.raises(ValidationError, match="debug must be strictly False"):
        Settings(environment=env, debug=True)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_and_production_default_tenant_rejected(env: str) -> None:
    """Verify oidc_default_tenant_id is rejected in staging and production (fail-closed)."""
    with pytest.raises(ValidationError, match="oidc_default_tenant_id must be None"):
        Settings(environment=env, debug=False, oidc_default_tenant_id="tenant-demo")


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_and_production_wildcard_cors_rejected(env: str) -> None:
    """Verify wildcard CORS is strictly rejected in staging and production."""
    with pytest.raises(ValidationError, match="Wildcard CORS"):
        Settings(environment=env, debug=False, cors_allowed_origins=["*"])


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_and_production_demo_idp_rejected(env: str) -> None:
    """Verify demo IdP providers are strictly rejected in staging and production."""
    with pytest.raises(ValidationError, match="prohibits demonstration identity provider"):
        Settings(environment=env, debug=False, oidc_provider_name="keycloak")


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_and_production_database_echo_rejected(env: str) -> None:
    """Verify database_echo=True is strictly rejected in staging and production."""
    with pytest.raises(ValidationError, match="database_echo must be strictly False"):
        Settings(environment=env, debug=False, database_echo=True)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_and_production_valid_configuration_accepted(env: str) -> None:
    """Verify valid hardened configuration passes validation in staging and production."""
    s = Settings(
        environment=env,
        debug=False,
        oidc_provider_name="google",
        cors_allowed_origins=["https://ops.example.com"],
        database_echo=False,
    )
    assert s.environment == env
    assert s.debug is False
    assert s.oidc_default_tenant_id is None
    assert s.cors_allowed_origins == ["https://ops.example.com"]
