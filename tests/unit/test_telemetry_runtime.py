"""Unit tests for TelemetryRuntime lifecycle, provider construction, and shutdown."""

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased

import app.core.telemetry.runtime as runtime_mod
from app.core.telemetry.config import TelemetrySettings
from app.core.telemetry.runtime import (
    TelemetryInitializationError,
    TelemetryRuntime,
    build_telemetry_runtime,
    get_telemetry_runtime,
    initialize_telemetry,
    shutdown_telemetry,
)


class FakeSpanExporter(SpanExporter):
    """In-memory fake span exporter for testing without network I/O."""

    def __init__(self, endpoint: str | None = None, timeout: float | None = None) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.exported_spans: list[Any] = []
        self.is_shutdown = False
        self.flush_called = False

    def export(self, spans: Any) -> SpanExportResult:
        self.exported_spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.is_shutdown = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.flush_called = True
        return True


class FakeMetricExporter(MetricExporter):
    """In-memory fake metric exporter for testing without network I/O."""

    def __init__(self, endpoint: str | None = None, timeout: float | None = None) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.timeout = timeout
        self.is_shutdown = False
        self.flush_called = False

    def export(
        self, metrics_data: Any, timeout_millis: float = 10000, **kwargs: Any
    ) -> MetricExportResult:
        return MetricExportResult.SUCCESS

    def shutdown(self, timeout_millis: float = 30000, **kwargs: Any) -> None:
        self.is_shutdown = True

    def force_flush(self, timeout_millis: float = 10000) -> bool:
        self.flush_called = True
        return True


@pytest.fixture(autouse=True)
def reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure runtime module state is isolated between test runs."""
    monkeypatch.setattr(runtime_mod, "_ACTIVE_RUNTIME", None)
    monkeypatch.setattr(runtime_mod, "_INIT_LOCK", threading.Lock())


def test_disabled_telemetry_build_runtime() -> None:
    """Verify disabled telemetry returns a safe no-op runtime with zero providers."""
    settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=False,
    )
    runtime = build_telemetry_runtime(settings)
    assert runtime.is_enabled is False
    assert runtime.tracer_provider is None
    assert runtime.meter_provider is None
    assert runtime.is_shutdown is False

    # Shutdown on disabled runtime is a safe no-op
    runtime.shutdown()
    assert runtime.is_shutdown is True


def test_enabled_telemetry_build_runtime_composition() -> None:
    """Verify enabled telemetry constructs TracerProvider, MeterProvider, and pipelines."""
    settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-api",
        otel_service_namespace="e7",
        otel_exporter_otlp_endpoint="http://localhost:4318",
        otel_trace_sample_ratio=0.5,
        otel_export_timeout_seconds=5.0,
        otel_metric_export_interval_seconds=15.0,
    )

    created_span_exporters: list[FakeSpanExporter] = []
    created_metric_exporters: list[FakeMetricExporter] = []

    def fake_span_exporter_factory(endpoint: str, timeout: float) -> FakeSpanExporter:
        exporter = FakeSpanExporter(endpoint=endpoint, timeout=timeout)
        created_span_exporters.append(exporter)
        return exporter

    def fake_metric_exporter_factory(endpoint: str, timeout: float) -> FakeMetricExporter:
        exporter = FakeMetricExporter(endpoint=endpoint, timeout=timeout)
        created_metric_exporters.append(exporter)
        return exporter

    runtime = build_telemetry_runtime(
        settings,
        environment="staging",
        span_exporter_factory=fake_span_exporter_factory,
        metric_exporter_factory=fake_metric_exporter_factory,
    )

    assert runtime.is_enabled is True
    assert runtime.tracer_provider is not None
    assert runtime.meter_provider is not None

    # Exporters received correct endpoints and timeouts
    assert len(created_span_exporters) == 1
    assert created_span_exporters[0].endpoint == "http://localhost:4318/v1/traces"
    assert created_span_exporters[0].timeout == 5.0

    assert len(created_metric_exporters) == 1
    assert created_metric_exporters[0].endpoint == "http://localhost:4318/v1/metrics"
    assert created_metric_exporters[0].timeout == 5.0

    # Resource verification on providers
    trace_res = runtime.tracer_provider.resource.attributes
    assert trace_res["service.name"] == "e7-api"
    assert trace_res["service.namespace"] == "e7"
    assert trace_res["deployment.environment"] == "staging"

    # Sampler verification
    sampler = runtime.tracer_provider.sampler
    assert isinstance(sampler, ParentBased)
    assert "TraceIdRatioBased{0.5}" in sampler.get_description()

    # Clean shutdown
    runtime.shutdown()
    assert runtime.is_shutdown is True
    assert created_span_exporters[0].is_shutdown is True
    assert created_metric_exporters[0].is_shutdown is True


def test_initialize_telemetry_registers_providers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify initialize_telemetry registers providers and guards against duplicate calls."""
    mock_set_tracer = MagicMock()
    mock_set_meter = MagicMock()
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", mock_set_tracer)
    monkeypatch.setattr("opentelemetry.metrics.set_meter_provider", mock_set_meter)

    settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=True,
        otel_service_name="e7-worker",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )

    runtime = initialize_telemetry(
        settings,
        environment="production",
        span_exporter_factory=lambda **kw: FakeSpanExporter(),
        metric_exporter_factory=lambda **kw: FakeMetricExporter(),
    )

    assert runtime is get_telemetry_runtime()
    assert mock_set_tracer.call_count == 1
    assert mock_set_meter.call_count == 1

    # Accidental duplicate call raises TelemetryInitializationError
    with pytest.raises(TelemetryInitializationError, match="already been initialized"):
        initialize_telemetry(settings)

    # Global providers were not replaced
    assert mock_set_tracer.call_count == 1
    assert mock_set_meter.call_count == 1

    # Shutdown helper delegates cleanly
    shutdown_telemetry()
    assert runtime.is_shutdown is True


def test_initialize_telemetry_disabled_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify initialize_telemetry when disabled does not register global providers."""
    mock_set_tracer = MagicMock()
    mock_set_meter = MagicMock()
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", mock_set_tracer)
    monkeypatch.setattr("opentelemetry.metrics.set_meter_provider", mock_set_meter)

    settings = TelemetrySettings(
        _env_file=None,
        otel_enabled=False,
    )
    runtime = initialize_telemetry(settings)
    assert runtime.is_enabled is False
    assert mock_set_tracer.call_count == 0
    assert mock_set_meter.call_count == 0
    assert get_telemetry_runtime() is runtime


def test_shutdown_telemetry_propagates_failures() -> None:
    """Verify provider shutdown failures propagate and do not swallow errors silently."""
    mock_tracer_provider = MagicMock()
    mock_tracer_provider.force_flush.side_effect = RuntimeError("Flush timeout")
    mock_tracer_provider.shutdown.return_value = None

    mock_meter_provider = MagicMock()
    mock_meter_provider.force_flush.return_value = True
    mock_meter_provider.shutdown.return_value = None

    runtime = TelemetryRuntime(
        enabled=True,
        tracer_provider=mock_tracer_provider,
        meter_provider=mock_meter_provider,
    )

    with pytest.raises(RuntimeError, match="Flush timeout"):
        runtime.shutdown(timeout_seconds=2.0)

    assert runtime.is_shutdown is True


def test_import_telemetry_modules_has_zero_side_effects() -> None:
    """Verify importing telemetry modules performs zero provider initialization or network calls."""
    import app.core.logging
    import app.core.telemetry
    import app.main
    import app.worker

    # Assert that importing did not set an active runtime
    assert runtime_mod._ACTIVE_RUNTIME is None
    # Assert module attributes are present
    assert hasattr(app.core.telemetry, "TelemetrySettings")
    assert hasattr(app.core.telemetry, "TelemetryRuntime")
    assert hasattr(app.core.logging, "setup_logging")
    assert hasattr(app.main, "app")
    assert hasattr(app.worker, "run_worker")
