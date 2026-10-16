"""Flowable client telemetry module for Phase 8C.

Provides custom OpenTelemetry metrics and histograms for Flowable REST roundtrips:
- Metrics:
  * e7.flowable.roundtrip.duration (Histogram, seconds, label: operation)
  * e7.flowable.requests.total (Counter, labels: operation, outcome in {'success', 'error'})

Strict cardinality guarantees:
- NO process_instance_id, task_id, tenant_id, execution_id, or definition_id as metric labels.
- Category labels strictly bounded to:
  {'deploy', 'start', 'task_query', 'task_complete', 'sync', 'other'}.
- Telemetry recording errors NEVER fail business operations.
"""

import time
from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

_TRACER_NAME = "e7.flowable"
_METER_NAME = "e7.flowable"

VALID_OPERATIONS = frozenset(
    {"deploy", "start", "task_query", "task_complete", "sync", "other"}
)


class FlowableTelemetry:
    """Encapsulates Flowable meters, histograms, and safe recording logic."""

    def __init__(self) -> None:
        self._tracer = trace.get_tracer(_TRACER_NAME)
        self._meter = metrics.get_meter(_METER_NAME)

        self._requests_counter = self._meter.create_counter(
            name="e7.flowable.requests.total",
            unit="1",
            description="Total count of Flowable REST API requests by operation and outcome",
        )
        self._duration_histogram = self._meter.create_histogram(
            name="e7.flowable.roundtrip.duration",
            unit="s",
            description="Duration of Flowable REST roundtrip operations in seconds",
        )

    def record_roundtrip(
        self,
        operation: str,
        duration_seconds: float,
        success: bool = True,
    ) -> None:
        """Safely record Flowable roundtrip duration and outcome."""
        try:
            op = operation if operation in VALID_OPERATIONS else "other"
            outcome = "success" if success else "error"
            self._requests_counter.add(1, {"operation": op, "outcome": outcome})
            self._duration_histogram.record(
                max(0.0, duration_seconds),
                {"operation": op, "outcome": outcome},
            )
        except Exception:
            pass

    @contextmanager
    def span_operation(self, operation: str) -> Generator[trace.Span, None, None]:
        """Context manager to measure and span a Flowable roundtrip operation."""
        op = operation if operation in VALID_OPERATIONS else "other"
        start_time = time.monotonic()
        with self._tracer.start_as_current_span(f"e7.flowable.{op}") as span:
            try:
                yield span
                duration = time.monotonic() - start_time
                self.record_roundtrip(op, duration, success=True)
            except Exception as exc:
                duration = time.monotonic() - start_time
                self.record_roundtrip(op, duration, success=False)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise


_flowable_telemetry_singleton: FlowableTelemetry | None = None


def get_flowable_telemetry() -> FlowableTelemetry:
    """Return the global FlowableTelemetry instance."""
    global _flowable_telemetry_singleton
    if _flowable_telemetry_singleton is None:
        _flowable_telemetry_singleton = FlowableTelemetry()
    return _flowable_telemetry_singleton
