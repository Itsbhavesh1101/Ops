"""Unit tests for OpenTelemetry configuration, settings, and Resource creation."""

import pytest
from pydantic import ValidationError

from app.core.telemetry.config import TelemetrySettings
from app.core.telemetry.resource import (
    FORBIDDEN_RESOURCE_ATTRIBUTES,
    build_resource,
    get_application_version,
)


def test_telemetry_settings_disabled_default() -> None:
    """Verify telemetry is safely disabled by default with zero required external config."""
    settings = TelemetrySettings(
        _env_file=None,  # Do not read local .env file
    )
    assert settings.otel_enabled is False
    assert settings.otel_service_name is None
    assert settings.otel_service_namespace == "e7"
    assert settings.otel_exporter_otlp_endpoint is None
    assert settings.otel_trace_sample_ratio == 1.0
    assert settings.otel_export_timeout_seconds == 10.0
    assert settings.otel_metric_export_interval_seconds == 60.0


def test_telemetry_settings_environment_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify E7_OTEL_* environment variables map correctly to TelemetrySettings."""
    monkeypatch.setenv("E7_OTEL_ENABLED", "true")
    monkeypatch.setenv("E7_OTEL_SERVICE_NAME", "custom-api")
    monkeypatch.setenv("E7_OTEL_SERVICE_NAMESPACE", "custom-namespace")
    monkeypatch.setenv("E7_OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.local:4318")
    monkeypatch.setenv("E7_OTEL_TRACE_SAMPLE_RATIO", "0.25")
    monkeypatch.setenv("E7_OTEL_EXPORT_TIMEOUT_SECONDS", "5.5")
    monkeypatch.setenv("E7_OTEL_METRIC_EXPORT_INTERVAL_SECONDS", "30.0")

    settings = TelemetrySettings(_env_file=None)
    assert settings.otel_enabled is True
    assert settings.otel_service_name == "custom-api"
    assert settings.otel_service_namespace == "custom-namespace"
    assert settings.otel_exporter_otlp_endpoint == "http://collector.local:4318"
    assert settings.otel_trace_sample_ratio == 0.25
    assert settings.otel_export_timeout_seconds == 5.5
    assert settings.otel_metric_export_interval_seconds == 30.0


def test_telemetry_enabled_requires_service_name() -> None:
    """Verify enabled telemetry fails fast if service name is missing or blank."""
    with pytest.raises(ValidationError) as exc_info:
        TelemetrySettings(
            _env_file=None,
            otel_enabled=True,
            otel_service_name=None,
            otel_exporter_otlp_endpoint="http://localhost:4318",
        )
    assert "otel_service_name is required when otel_enabled is True" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info_empty:
        TelemetrySettings(
            _env_file=None,
            otel_enabled=True,
            otel_service_name="   ",
            otel_exporter_otlp_endpoint="http://localhost:4318",
        )
    assert "otel_service_name cannot be an empty or whitespace-only string" in str(
        exc_info_empty.value
    )


def test_telemetry_enabled_requires_otlp_endpoint() -> None:
    """Verify enabled telemetry fails fast if collector endpoint is missing or blank."""
    with pytest.raises(ValidationError) as exc_info:
        TelemetrySettings(
            _env_file=None,
            otel_enabled=True,
            otel_service_name="e7-api",
            otel_exporter_otlp_endpoint=None,
        )
    assert "otel_exporter_otlp_endpoint is required when otel_enabled is True" in str(
        exc_info.value
    )


def test_telemetry_invalid_url_scheme_rejected() -> None:
    """Verify only http and https URL schemes are accepted for OTLP collector."""
    for invalid_scheme in ["ftp://localhost:4318", "grpc://localhost:4317", "localhost:4318"]:
        with pytest.raises(ValidationError) as exc_info:
            TelemetrySettings(
                _env_file=None,
                otel_enabled=True,
                otel_service_name="e7-api",
                otel_exporter_otlp_endpoint=invalid_scheme,
            )
        assert "scheme" in str(exc_info.value).lower() or "host" in str(exc_info.value).lower()


def test_telemetry_userinfo_credentials_in_url_rejected() -> None:
    """Verify embedded credentials in OTLP endpoint URLs are strictly rejected."""
    with pytest.raises(ValidationError) as exc_info:
        TelemetrySettings(
            _env_file=None,
            otel_enabled=True,
            otel_service_name="e7-api",
            otel_exporter_otlp_endpoint="http://user:secretpassword@localhost:4318",
        )
    assert "user credentials" in str(exc_info.value)
    # Ensure raw secret is not leaked in the validation message
    assert "secretpassword" not in str(exc_info.value)


def test_telemetry_signal_specific_endpoints() -> None:
    """Verify traces_endpoint and metrics_endpoint append signals deterministically."""
    settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-api",
        otel_exporter_otlp_endpoint="http://localhost:4318/",
    )
    # Trailing slash stripped
    assert settings.traces_endpoint == "http://localhost:4318/v1/traces"
    assert settings.metrics_endpoint == "http://localhost:4318/v1/metrics"


def test_telemetry_rejects_already_appended_signal_paths() -> None:
    """Verify OTLP base endpoint rejects URLs that already end with /v1/traces or /v1/metrics."""
    for invalid_url in [
        "http://localhost:4318/v1/traces",
        "http://localhost:4318/v1/traces/",
        "http://localhost:4318/v1/metrics",
    ]:
        with pytest.raises(ValidationError) as exc_info:
            TelemetrySettings(
                _env_file=None,
                otel_enabled=True,
                otel_service_name="e7-api",
                otel_exporter_otlp_endpoint=invalid_url,
            )
        assert "base collector url without signal paths" in str(exc_info.value).lower()


def test_telemetry_sample_ratio_bounds() -> None:
    """Verify trace sample ratio is constrained strictly to [0.0, 1.0]."""
    valid_settings = TelemetrySettings(
        _env_file=None,
        otel_trace_sample_ratio=0.0,
    )
    assert valid_settings.otel_trace_sample_ratio == 0.0

    valid_one = TelemetrySettings(
        _env_file=None,
        otel_trace_sample_ratio=1.0,
    )
    assert valid_one.otel_trace_sample_ratio == 1.0

    with pytest.raises(ValidationError) as exc_low:
        TelemetrySettings(_env_file=None, otel_trace_sample_ratio=-0.01)
    assert "between 0.0 and 1.0" in str(exc_low.value)

    with pytest.raises(ValidationError) as exc_high:
        TelemetrySettings(_env_file=None, otel_trace_sample_ratio=1.001)
    assert "between 0.0 and 1.0" in str(exc_high.value)


def test_telemetry_positive_timeout_and_interval() -> None:
    """Verify export timeout and metric interval must be strictly positive."""
    with pytest.raises(ValidationError) as exc_t_zero:
        TelemetrySettings(_env_file=None, otel_export_timeout_seconds=0.0)
    assert "strictly positive" in str(exc_t_zero.value)

    with pytest.raises(ValidationError) as exc_t_neg:
        TelemetrySettings(_env_file=None, otel_export_timeout_seconds=-5.0)
    assert "strictly positive" in str(exc_t_neg.value)

    with pytest.raises(ValidationError) as exc_m_zero:
        TelemetrySettings(_env_file=None, otel_metric_export_interval_seconds=0.0)
    assert "strictly positive" in str(exc_m_zero.value)


def test_build_resource_success() -> None:
    """Verify build_resource includes only safe platform and service attributes."""
    resource = build_resource(
        service_name="e7-api",
        service_namespace="e7",
        service_version="0.1.0",
        environment="staging",
    )
    attrs = resource.attributes

    assert attrs["service.name"] == "e7-api"
    assert attrs["service.namespace"] == "e7"
    assert attrs["service.version"] == "0.1.0"
    assert attrs["deployment.environment"] == "staging"

    # Verify absence of forbidden cardinality / privacy attributes
    for forbidden in FORBIDDEN_RESOURCE_ATTRIBUTES:
        assert forbidden not in attrs
        assert f"e7.{forbidden}" not in attrs


def test_build_resource_defaults_version_from_metadata() -> None:
    """Verify build_resource resolves default version via get_application_version."""
    resource = build_resource(
        service_name="e7-worker",
        environment="production",
    )
    assert resource.attributes["service.name"] == "e7-worker"
    assert resource.attributes["service.version"] == get_application_version()
    assert resource.attributes["deployment.environment"] == "production"


def test_build_resource_rejects_empty_service_name() -> None:
    """Verify build_resource rejects blank or missing service name."""
    with pytest.raises(ValueError, match="service_name is required"):
        build_resource(service_name="")

    with pytest.raises(ValueError, match="service_name is required"):
        build_resource(service_name="   ")
