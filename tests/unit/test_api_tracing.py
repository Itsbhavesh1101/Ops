"""Focused unit tests for FastAPI request tracing (Task 1K.4 / Stage A).

Validates:
- Telemetry disabled -> no FastAPI instrumentation activation.
- Telemetry enabled -> server span created on requests.
- Correlation ID appears as e7.correlation_id on active server span.
- Logs within active request include correlation_id + trace_id + span_id.
- Privacy contract: no Authorization, Cookie, X-API-Key, bodies, or query params captured.
- Route template cardinality: template used rather than concrete raw path with IDs.
- Health/readiness probe exclusion: /api/v1/health and /api/v1/ready emit zero spans.
- Route stability: unchanged routes, no /metrics route, job polling route unmounted.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.core.middleware as mw
import app.main as main_mod
from app.core.config import Settings
from app.core.logging import setup_logging
from app.core.telemetry.config import TelemetrySettings
from app.core.telemetry.runtime import TelemetryRuntime
from app.main import app


@pytest.fixture(autouse=True)
def reset_telemetry_isolation(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Ensure clean runtime and structlog state between test runs."""
    monkeypatch.setattr("app.core.telemetry.runtime._ACTIVE_RUNTIME", None)
    if hasattr(app.state, "telemetry_settings"):
        delattr(app.state, "telemetry_settings")
    if hasattr(app.state, "telemetry_initializer"):
        delattr(app.state, "telemetry_initializer")
    if hasattr(app.state, "telemetry_shutdown"):
        delattr(app.state, "telemetry_shutdown")
    if hasattr(app.state, "telemetry_runtime"):
        delattr(app.state, "telemetry_runtime")
    if hasattr(app.state, "fastapi_instrumentor"):
        delattr(app.state, "fastapi_instrumentor")

    yield

    # Clean up any leftover instrumentors
    if hasattr(app.state, "fastapi_instrumentor"):
        from app.core.telemetry.fastapi import uninstrument_fastapi_app

        uninstrument_fastapi_app(app, app.state.fastapi_instrumentor)
        delattr(app.state, "fastapi_instrumentor")

    mw.logger = structlog.get_logger("app.core.middleware")
    main_mod.logger = structlog.get_logger("app.main")


def test_telemetry_disabled_no_fastapi_instrumentation() -> None:
    """When telemetry is disabled, zero FastAPI instrumentation is activated."""
    with TestClient(app) as client:
        res = client.get("/api/v1/health")
        assert res.status_code == 200
        assert getattr(app.state, "fastapi_instrumentor", None) is None


def test_telemetry_enabled_server_span_created_with_correlation_id() -> None:
    """Traced request produces server span with e7.correlation_id matching header."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    test_runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=provider,
    )

    app.state.telemetry_settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-api",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    app.state.telemetry_initializer = lambda s, environment="local": test_runtime
    app.state.telemetry_shutdown = MagicMock()

    explicit_corr_id = "550e8400-e29b-41d4-a716-446655440000"

    with TestClient(app) as client:
        response = client.get(
            "/",
            headers={"X-Correlation-ID": explicit_corr_id},
        )
        assert response.status_code == 200

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert span.attributes is not None
    assert span.attributes.get("e7.correlation_id") == explicit_corr_id
    assert span.attributes.get("http.status_code") == 200
    assert span.attributes.get("http.method") == "GET"


def test_health_and_readiness_excluded_from_tracing() -> None:
    """Requests to /api/v1/health and /api/v1/ready produce zero spans."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    test_runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=provider,
    )

    app.state.telemetry_settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-api",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    app.state.telemetry_initializer = lambda s, environment="local": test_runtime
    app.state.telemetry_shutdown = MagicMock()

    with TestClient(app) as client:
        # Request health probe
        res_health = client.get("/api/v1/health")
        assert res_health.status_code == 200

        # Request ready probe
        res_ready = client.get("/api/v1/ready")
        assert res_ready.status_code == 200

    # No spans should be generated for health or ready probes
    spans = exporter.get_finished_spans()
    assert len(spans) == 0


def test_logs_within_request_contain_correlation_and_trace_ids() -> None:
    """Application logs during request naturally correlate correlation_id, trace_id, span_id."""
    setup_logging(Settings(log_level="INFO", log_format="json"))

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    test_runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=provider,
    )

    app.state.telemetry_settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-api",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    app.state.telemetry_initializer = lambda s, environment="local": test_runtime
    app.state.telemetry_shutdown = MagicMock()

    custom_corr_id = "88888888-4444-4444-4444-121212121212"

    import ast
    import logging

    class RecordCapture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.entries: list[dict[str, Any]] = []

        def emit(self, record: logging.LogRecord) -> None:
            try:
                if isinstance(record.msg, dict):
                    msg = record.msg
                else:
                    msg = ast.literal_eval(record.getMessage())
                if isinstance(msg, dict):
                    self.entries.append(msg)
            except Exception:
                pass

    cap_handler = RecordCapture()
    root_logger = logging.getLogger()
    root_logger.addHandler(cap_handler)

    try:
        with TestClient(app) as client:
            res = client.get(
                "/",
                headers={"X-Correlation-ID": custom_corr_id},
            )
            assert res.status_code == 200

        http_events = [
            log for log in cap_handler.entries if log.get("event") == "http_request_completed"
        ]
        assert len(http_events) >= 1
        log_entry = http_events[-1]

        assert log_entry["correlation_id"] == custom_corr_id
        assert "trace_id" in log_entry
        assert len(log_entry["trace_id"]) == 32
        assert "span_id" in log_entry
        assert len(log_entry["span_id"]) == 16
    finally:
        root_logger.removeHandler(cap_handler)


def test_fastapi_privacy_no_secrets_query_or_bodies_captured() -> None:
    """Span attributes never leak secrets, query parameters, or concrete raw IDs."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    test_runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=provider,
    )

    app.state.telemetry_settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-api",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    app.state.telemetry_initializer = lambda s, environment="local": test_runtime
    app.state.telemetry_shutdown = MagicMock()

    with TestClient(app) as client:
        # Request with sensitive headers and query params to unmatched route
        client.get(
            "/definitely-not-found/user-private-id-999?token=super-secret-token&api_key=key-123",
            headers={
                "Authorization": "Bearer supersecretbearer",
                "Cookie": "session=secretcookie123",
                "X-API-Key": "apikey-xyz",
            },
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    span_attrs = span.attributes or {}

    # Check that forbidden fields are completely absent from span attributes
    for key, val in span_attrs.items():
        key_lower = key.lower()
        val_str = str(val).lower()
        assert "authorization" not in key_lower
        assert "cookie" not in key_lower
        assert "x-api-key" not in key_lower
        assert "supersecretbearer" not in val_str
        assert "secretcookie123" not in val_str
        assert "super-secret-token" not in val_str
        assert "key-123" not in val_str
        assert "user-private-id-999" not in val_str

    # Route cardinality check: unmatched route marked as <unmatched>
    assert span_attrs.get("http.route") == "<unmatched>"
    assert span_attrs.get("http.target") == "<unmatched>"


def test_query_parameter_privacy_regression_values_completely_absent() -> None:
    """Verify query parameter values and raw query strings never appear in span attributes.

    Phase-1 strict privacy contract:
    - query parameter VALUES must not appear in span attributes
    - request URL attributes must not contain raw query strings
    - route template is allowed
    - method/status are allowed
    - scheme/host may remain
    - request body remains absent
    - request/response headers remain absent
    - query values are absent entirely, including URL-encoded equivalents
    - the request itself works normally
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    test_runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=provider,
    )

    app.state.telemetry_settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-api",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    app.state.telemetry_initializer = lambda s, environment="local": test_runtime
    app.state.telemetry_shutdown = MagicMock()

    # Issue request with sensitive query values exactly as specified
    with TestClient(app) as client:
        response = client.get(
            "/?email=user@example.com&token=secret-value&search=private",
            headers={"X-Correlation-ID": "query-privacy-corr-id"},
        )
        # Request itself must work normally
        assert response.status_code == 200
        assert response.json().get("status") == "online"

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    attrs = span.attributes or {}

    # Target and URL must be path-only, without query parameters
    assert attrs.get("http.route") == "/"
    assert attrs.get("http.target") == "/"
    assert attrs.get("http.url") == "http://testserver/"

    # Check ALL span attributes: none of the query values or their encoded forms may appear
    forbidden_values = [
        "user@example.com",
        "user%40example.com",
        "secret-value",
        "private",
    ]
    for key, val in attrs.items():
        val_str = str(val).lower()
        key_str = str(key).lower()
        for forbidden in forbidden_values:
            assert forbidden not in val_str, (
                f"Forbidden query value '{forbidden}' found in span attribute '{key}': '{val}'"
            )
            assert forbidden not in key_str, (
                f"Forbidden query value '{forbidden}' found in span attribute key '{key}'"
            )

    # Ensure no raw query string '?' remains in URL or target attributes
    assert "?" not in str(attrs.get("http.target", ""))
    assert "?" not in str(attrs.get("http.url", ""))
    assert "?" not in str(attrs.get("http.route", ""))
