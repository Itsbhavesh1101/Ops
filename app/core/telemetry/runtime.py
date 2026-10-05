"""OpenTelemetry runtime lifecycle and provider abstraction module.

Encapsulates TracerProvider and MeterProvider construction, OTLP/HTTP batch
export pipelines, and bounded shutdown. Enforces process-global initialization
boundaries and guards against duplicate active initialization.
"""

import threading
from collections.abc import Callable

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.core.telemetry.config import TelemetrySettings
from app.core.telemetry.resource import build_resource


class TelemetryInitializationError(RuntimeError):
    """Raised when telemetry is initialized more than once or with invalid runtime state."""


class TelemetryRuntime:
    """Encapsulates the lifecycle, providers, and shutdown of OpenTelemetry.

    Exposes no internal exporter network logic to business modules.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        tracer_provider: TracerProvider | None = None,
        meter_provider: MeterProvider | None = None,
        settings: TelemetrySettings | None = None,
    ) -> None:
        self._enabled = enabled
        self._tracer_provider = tracer_provider
        self._meter_provider = meter_provider
        self._settings = settings
        self._shutdown = False
        self._lock = threading.Lock()

    @property
    def is_enabled(self) -> bool:
        """Indicate whether telemetry is actively enabled with live providers."""
        return self._enabled

    @property
    def enabled(self) -> bool:
        """Alias for is_enabled."""
        return self._enabled

    @property
    def tracer_provider(self) -> TracerProvider | None:
        """The configured TracerProvider, or None if disabled."""
        return self._tracer_provider

    @property
    def meter_provider(self) -> MeterProvider | None:
        """The configured MeterProvider, or None if disabled."""
        return self._meter_provider

    @property
    def is_shutdown(self) -> bool:
        """Indicate whether this runtime has been shut down."""
        return self._shutdown

    def shutdown(self, timeout_seconds: float | None = None) -> None:
        """Flush and shut down telemetry providers within a bounded timeout.

        Preserves primary exceptions if secondary cleanup operations fail.
        """
        with self._lock:
            if not self._enabled or self._shutdown:
                self._shutdown = True
                return

            timeout = (
                timeout_seconds
                if timeout_seconds is not None
                else (self._settings.otel_export_timeout_seconds if self._settings else 10.0)
            )
            timeout_millis = max(1, int(timeout * 1000))

            errors: list[Exception] = []

            # 1. Force flush and shut down TracerProvider
            if self._tracer_provider is not None:
                try:
                    self._tracer_provider.force_flush(timeout_millis=timeout_millis)
                except Exception as exc:
                    errors.append(exc)
                try:
                    self._tracer_provider.shutdown()
                except Exception as exc:
                    errors.append(exc)

            # 2. Force flush and shut down MeterProvider
            if self._meter_provider is not None:
                try:
                    self._meter_provider.force_flush(timeout_millis=timeout_millis)
                except Exception as exc:
                    errors.append(exc)
                try:
                    self._meter_provider.shutdown(timeout_millis=timeout_millis)
                except Exception as exc:
                    errors.append(exc)

            self._shutdown = True

            if errors:
                if len(errors) == 1:
                    raise errors[0]
                raise RuntimeError(
                    f"Multiple errors encountered during telemetry shutdown: {errors}"
                )


_ACTIVE_RUNTIME: TelemetryRuntime | None = None
_INIT_LOCK = threading.Lock()


def build_telemetry_runtime(
    settings: TelemetrySettings,
    *,
    environment: str = "local",
    span_exporter_factory: Callable[..., SpanExporter] | None = None,
    metric_exporter_factory: Callable[..., MetricExporter] | None = None,
) -> TelemetryRuntime:
    """Pure factory constructing TelemetryRuntime without mutating process-global state.

    Allows unit testing and factory composition without registering providers globally.
    """
    if not settings.otel_enabled:
        return TelemetryRuntime(enabled=False, settings=settings)

    # 1. Build canonical safe Resource
    resource = build_resource(
        service_name=settings.otel_service_name or "unknown",
        service_namespace=settings.otel_service_namespace,
        environment=environment,
    )

    # 2. Build root sampler: ParentBased(TraceIdRatioBased(ratio))
    sampler = ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio))

    # 3. Build TracerProvider with BatchSpanProcessor
    if span_exporter_factory is not None:
        span_exporter = span_exporter_factory(
            endpoint=settings.traces_endpoint,
            timeout=settings.otel_export_timeout_seconds,
        )
    else:
        span_exporter = OTLPSpanExporter(
            endpoint=settings.traces_endpoint,
            timeout=settings.otel_export_timeout_seconds,
        )

    export_timeout_millis = max(1, int(settings.otel_export_timeout_seconds * 1000))
    span_processor = BatchSpanProcessor(
        span_exporter,
        export_timeout_millis=export_timeout_millis,
    )

    tracer_provider = TracerProvider(
        resource=resource,
        sampler=sampler,
    )
    tracer_provider.add_span_processor(span_processor)

    # 4. Build MeterProvider with PeriodicExportingMetricReader
    if metric_exporter_factory is not None:
        metric_exporter = metric_exporter_factory(
            endpoint=settings.metrics_endpoint,
            timeout=settings.otel_export_timeout_seconds,
        )
    else:
        metric_exporter = OTLPMetricExporter(
            endpoint=settings.metrics_endpoint,
            timeout=settings.otel_export_timeout_seconds,
        )

    export_interval_millis = max(1.0, settings.otel_metric_export_interval_seconds * 1000.0)
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=export_interval_millis,
        export_timeout_millis=float(export_timeout_millis),
    )

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
    )

    return TelemetryRuntime(
        enabled=True,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        settings=settings,
    )


def initialize_telemetry(
    settings: TelemetrySettings,
    *,
    environment: str = "local",
    span_exporter_factory: Callable[..., SpanExporter] | None = None,
    metric_exporter_factory: Callable[..., MetricExporter] | None = None,
) -> TelemetryRuntime:
    """Explicitly initialize telemetry and register process-global providers once.

    Guards against duplicate active initialization by raising TelemetryInitializationError.
    """
    global _ACTIVE_RUNTIME
    with _INIT_LOCK:
        if _ACTIVE_RUNTIME is not None:
            raise TelemetryInitializationError(
                "OpenTelemetry runtime has already been initialized in this process"
            )

        runtime = build_telemetry_runtime(
            settings,
            environment=environment,
            span_exporter_factory=span_exporter_factory,
            metric_exporter_factory=metric_exporter_factory,
        )

        if runtime.is_enabled:
            if runtime.tracer_provider is not None:
                trace.set_tracer_provider(runtime.tracer_provider)
            if runtime.meter_provider is not None:
                metrics.set_meter_provider(runtime.meter_provider)

        _ACTIVE_RUNTIME = runtime
        return runtime


def get_telemetry_runtime() -> TelemetryRuntime | None:
    """Return the active TelemetryRuntime, or None if uninitialized."""
    return _ACTIVE_RUNTIME


def shutdown_telemetry(timeout_seconds: float | None = None) -> None:
    """Shut down the active TelemetryRuntime if initialized."""
    global _ACTIVE_RUNTIME
    with _INIT_LOCK:
        if _ACTIVE_RUNTIME is not None:
            _ACTIVE_RUNTIME.shutdown(timeout_seconds=timeout_seconds)
