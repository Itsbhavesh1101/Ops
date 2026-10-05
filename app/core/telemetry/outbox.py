"""Outbox telemetry foundation module for Phase-1 worker observability (Task 1K.5 / Stage B).

Provides reviewed custom telemetry for the transactional outbox dispatcher and publisher:
- Spans:
  * e7.outbox.dispatch
  * e7.event.publish
- Metrics:
  * e7.outbox.dispatch.attempts (Counter)
  * e7.outbox.dispatch.successes (Counter)
  * e7.outbox.dispatch.failures (Counter, with closed error_category label)
  * e7.outbox.dispatch.duration (Histogram, seconds)

Strict cardinality and privacy guarantees:
- NO event_type metric label
- NO tenant label
- NO event_id, job_id, correlation_id, trace_id, or span_id metric labels
- Failure metric uses ONLY a closed error_category vocabulary:
  publish_error, timeout, database_error, unexpected
- Zero event payloads, user IDs, or credentials attached to spans or metrics
- Telemetry recording errors NEVER fail business operations or alter state
"""

from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

VALID_ERROR_CATEGORIES = frozenset({"publish_error", "timeout", "database_error", "unexpected"})

_TRACER_NAME = "e7.outbox"
_METER_NAME = "e7.outbox"


class OutboxTelemetry:
    """Encapsulates outbox meters, counters, histograms, and safe recording logic."""

    def __init__(self) -> None:
        self._tracer = trace.get_tracer(_TRACER_NAME)
        self._meter = metrics.get_meter(_METER_NAME)

        self._attempts_counter = self._meter.create_counter(
            name="e7.outbox.dispatch.attempts",
            unit="1",
            description="Total number of outbox dispatch attempts initiated",
        )
        self._successes_counter = self._meter.create_counter(
            name="e7.outbox.dispatch.successes",
            unit="1",
            description="Total number of successful outbox dispatches",
        )
        self._failures_counter = self._meter.create_counter(
            name="e7.outbox.dispatch.failures",
            unit="1",
            description="Total number of failed outbox dispatches",
        )
        self._duration_histogram = self._meter.create_histogram(
            name="e7.outbox.dispatch.duration",
            unit="s",
            description="Duration of outbox dispatch attempts in seconds",
        )

    @property
    def tracer(self) -> trace.Tracer:
        return self._tracer

    def record_attempt(self) -> None:
        """Record an outbox dispatch attempt without metric dimensions."""
        try:
            self._attempts_counter.add(1)
        except BaseException:
            pass

    def record_success(self, duration_seconds: float) -> None:
        """Record a successful outbox dispatch and its duration."""
        try:
            self._successes_counter.add(1)
            if duration_seconds >= 0:
                self._duration_histogram.record(duration_seconds)
        except BaseException:
            pass

    def record_failure(self, error_category: str, duration_seconds: float) -> None:
        """Record a failed outbox dispatch with closed error_category and duration."""
        try:
            category = error_category if error_category in VALID_ERROR_CATEGORIES else "unexpected"
            self._failures_counter.add(1, {"error_category": category})
            if duration_seconds >= 0:
                self._duration_histogram.record(duration_seconds)
        except BaseException:
            pass

    @contextmanager
    def span_dispatch(
        self,
        *,
        event_type: str,
        retry_attempt_count: int,
        correlation_id: str | None = None,
    ) -> Generator[trace.Span, None, None]:
        """Context manager for the e7.outbox.dispatch operational span."""
        span = self._tracer.start_span("e7.outbox.dispatch")
        try:
            if span.is_recording():
                span.set_attribute("event_type", event_type)
                span.set_attribute("retry_attempt_count", retry_attempt_count)
                if correlation_id:
                    span.set_attribute("e7.correlation_id", correlation_id)
            yield span
        except BaseException as exc:
            if span.is_recording():
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            try:
                span.end()
            except BaseException:
                pass

    @contextmanager
    def span_publish(
        self,
        *,
        event_type: str,
        exchange_name: str,
        correlation_id: str | None = None,
    ) -> Generator[trace.Span, None, None]:
        """Context manager for the e7.event.publish operational span."""
        span = self._tracer.start_span("e7.event.publish")
        try:
            if span.is_recording():
                span.set_attribute("event_type", event_type)
                span.set_attribute("broker_exchange_name", exchange_name)
                if correlation_id:
                    span.set_attribute("e7.correlation_id", correlation_id)
            yield span
        except BaseException as exc:
            if span.is_recording():
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            try:
                span.end()
            except BaseException:
                pass


_OUTBOX_TELEMETRY: OutboxTelemetry | None = None


def get_outbox_telemetry() -> OutboxTelemetry:
    """Retrieve or initialize singleton OutboxTelemetry instance."""
    global _OUTBOX_TELEMETRY
    if _OUTBOX_TELEMETRY is None:
        _OUTBOX_TELEMETRY = OutboxTelemetry()
    return _OUTBOX_TELEMETRY
