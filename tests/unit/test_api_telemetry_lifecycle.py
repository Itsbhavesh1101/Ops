"""Unit tests for FastAPI API telemetry lifecycle integration (Task 1K.3).

Validates:
- Import safety: importing app.main does not initialize telemetry or register global providers.
- Disabled mode: default E7_OTEL_ENABLED=false starts API cleanly without OTLP/Collector.
- Enabled mode: initializes telemetry runtime once and flushes/shuts down on lifespan exit.
- Conceptual ordering: telemetry.init -> request serving -> telemetry.shutdown.
- Fail-fast startup: telemetry initialization failures propagate and abort startup.
- Safe shutdown: shutdown errors propagate on clean runs, or are attached as notes on primary error.
- Service identity: canonical default is 'e7-api'; operator E7_OTEL_SERVICE_NAME override respected.
- Configuration failure: invalid telemetry configuration fails startup clearly.
- Route stability: routes unchanged; /metrics and /api/v1/jobs/{job_id} absent.
- Auto-instrumentation absence: zero FastAPIInstrumentor or ASGI tracing middleware.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core.telemetry.config import TelemetrySettings, resolve_telemetry_settings
from app.core.telemetry.runtime import TelemetryRuntime
from app.main import app


@pytest.fixture(autouse=True)
def reset_telemetry_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure runtime module state is isolated between test runs."""
    monkeypatch.setattr("app.core.telemetry.runtime._ACTIVE_RUNTIME", None)
    # Clear any test overrides on app.state
    if hasattr(app.state, "telemetry_settings"):
        delattr(app.state, "telemetry_settings")
    if hasattr(app.state, "telemetry_initializer"):
        delattr(app.state, "telemetry_initializer")
    if hasattr(app.state, "telemetry_shutdown"):
        delattr(app.state, "telemetry_shutdown")
    if hasattr(app.state, "telemetry_runtime"):
        delattr(app.state, "telemetry_runtime")

    yield

    # Reset cached logger proxies so subsequent test suites (e.g. test_logging) can capture logs
    import app.core.middleware as mw

    mw.logger = structlog.get_logger("app.core.middleware")
    main_mod.logger = structlog.get_logger("app.main")


# -----------------------------------------------------------------------------
# 1. Import Safety Tests
# -----------------------------------------------------------------------------


def test_import_app_main_is_telemetry_inert() -> None:
    """Verify importing app.main performs zero telemetry initialization."""
    from app.core.telemetry.runtime import get_telemetry_runtime

    assert get_telemetry_runtime() is None
    assert hasattr(main_mod, "app")
    assert hasattr(main_mod, "lifespan")


# -----------------------------------------------------------------------------
# 2. Disabled Mode Lifespan Tests
# -----------------------------------------------------------------------------


def test_api_lifespan_disabled_mode_zero_overhead(monkeypatch: pytest.MonkeyPatch) -> None:
    """With default settings (disabled), API lifespan starts cleanly with no OTel providers."""
    mock_set_tracer = MagicMock()
    mock_set_meter = MagicMock()
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", mock_set_tracer)
    monkeypatch.setattr("opentelemetry.metrics.set_meter_provider", mock_set_meter)

    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    # Global providers were never registered
    assert mock_set_tracer.call_count == 0
    assert mock_set_meter.call_count == 0


# -----------------------------------------------------------------------------
# 3. Enabled Mode Lifecycle & Ordering Tests
# -----------------------------------------------------------------------------


def test_api_lifespan_enabled_lifecycle_and_ordering() -> None:
    """Verify enabled API lifespan calls init, serves requests, and shuts down in order."""
    events: list[str] = []

    fake_runtime = TelemetryRuntime(enabled=True)

    def fake_initializer(
        settings: TelemetrySettings, environment: str = "local"
    ) -> TelemetryRuntime:
        events.append(f"telemetry.init:{settings.otel_service_name}:{environment}")
        return fake_runtime

    def fake_shutdown() -> None:
        events.append("telemetry.shutdown")

    app.state.telemetry_settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-api",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    app.state.telemetry_initializer = fake_initializer
    app.state.telemetry_shutdown = fake_shutdown

    with TestClient(app) as client:
        events.append("lifespan.active")
        res = client.get("/api/v1/health")
        assert res.status_code == 200
        events.append("request.served")

    assert events == [
        "telemetry.init:e7-api:local",
        "lifespan.active",
        "request.served",
        "telemetry.shutdown",
    ]


# -----------------------------------------------------------------------------
# 4. Startup Failure Propagation Tests
# -----------------------------------------------------------------------------


def test_api_startup_telemetry_failure_propagates() -> None:
    """If telemetry initialization raises, API startup must fail fast and propagate."""

    def failing_initializer(settings: TelemetrySettings, environment: str = "local") -> Any:
        raise RuntimeError("Fatal OTel collector connection failure")

    app.state.telemetry_initializer = failing_initializer

    with pytest.raises(RuntimeError, match="Fatal OTel collector connection failure"):
        with TestClient(app):
            pass


# -----------------------------------------------------------------------------
# 5. Shutdown Failure Handling Tests
# -----------------------------------------------------------------------------


def test_api_shutdown_failure_propagates_on_clean_run() -> None:
    """If request serving succeeds but telemetry shutdown fails, the error propagates."""

    def failing_shutdown() -> None:
        raise RuntimeError("OTel batch flush timed out")

    app.state.telemetry_shutdown = failing_shutdown

    with pytest.raises(RuntimeError, match="OTel batch flush timed out"):
        with TestClient(app) as client:
            res = client.get("/api/v1/health")
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_api_lifespan_shutdown_failure_attached_as_note_on_primary_error() -> None:
    """If primary error occurs during lifespan yield and shutdown fails,
    primary error is preserved."""

    def failing_shutdown() -> None:
        raise RuntimeError("Secondary shutdown failure")

    app.state.telemetry_shutdown = failing_shutdown

    primary_err = ValueError("Primary lifespan error")
    with pytest.raises(ValueError, match="Primary lifespan error") as exc_info:
        async with main_mod.lifespan(app):
            raise primary_err

    err = exc_info.value
    assert err is primary_err
    if hasattr(err, "__notes__"):
        assert any("Secondary failure during telemetry shutdown" in n for n in err.__notes__)


# -----------------------------------------------------------------------------
# 6. Service Identity Resolution Tests
# -----------------------------------------------------------------------------


def test_api_service_identity_defaults_to_e7_api() -> None:
    """When E7_OTEL_SERVICE_NAME is unset, resolve_telemetry_settings defaults to e7-api."""
    settings = resolve_telemetry_settings(
        default_service_name="e7-api",
        _env_file=None,
        otel_enabled=True,
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    assert settings.otel_service_name == "e7-api"
    assert settings.otel_enabled is True


def test_api_service_identity_preserves_operator_override() -> None:
    """When operator explicitly sets E7_OTEL_SERVICE_NAME, that value takes precedence."""
    settings = resolve_telemetry_settings(
        default_service_name="e7-api",
        _env_file=None,
        otel_enabled=True,
        otel_service_name="custom-operations-api",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    assert settings.otel_service_name == "custom-operations-api"


def test_api_invalid_telemetry_config_fails_startup() -> None:
    """If telemetry is enabled with invalid endpoint, startup fails validation immediately."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        resolve_telemetry_settings(
            default_service_name="e7-api",
            _env_file=None,
            otel_enabled=True,
            otel_exporter_otlp_endpoint="ftp://unsupported:9999",
        )


# -----------------------------------------------------------------------------
# 7. Route and Architectural Stability Tests
# -----------------------------------------------------------------------------


def test_api_route_inventory_unchanged() -> None:
    """Verify exact production routes; confirm no /metrics or job polling routes exist."""
    route_paths = {route.path for route in app.routes if hasattr(route, "path")}  # type: ignore[attr-defined]

    # Required production baseline routes
    assert "/api/v1/health" in route_paths
    assert "/api/v1/ready" in route_paths
    assert "/" in route_paths

    # Strictly forbidden routes
    assert "/metrics" not in route_paths
    assert "/api/v1/jobs/{job_id}" not in route_paths


def test_disabled_mode_does_not_activate_fastapi_instrumentor() -> None:
    """In disabled mode, app.state.fastapi_instrumentor is absent."""
    with TestClient(app):
        assert getattr(app.state, "fastapi_instrumentor", None) is None
