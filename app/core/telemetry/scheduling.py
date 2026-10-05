"""Scheduling solver telemetry module for Phase-4A (Stage M).

Provides reviewed custom OpenTelemetry metrics and spans for the scheduling solver and worker:
- Spans:
  * e7.scheduling.solve
- Metrics:
  * e7.scheduling.solve.attempts (Counter)
  * e7.scheduling.solve.results (Counter,
    label: result_status in {'FEASIBLE', 'INFEASIBLE', 'FAILED'})
  * e7.scheduling.solve.duration (Histogram, seconds)
  * e7.scheduling.assignments.generated (Counter)
  * e7.scheduling.infeasibility.occurrences (Counter)

Strict cardinality and privacy guarantees:
- NO tenant_id, schedule_id, job_id, activity_id, resource_id,
  constraint_id, correlation_id, or raw solver diagnostics as metric labels.
- Zero input snapshots, PII, or raw constraint payloads attached to spans or metrics.
- Telemetry recording errors NEVER fail business operations or alter state.
"""

import time
from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

_TRACER_NAME = "e7.scheduling"
_METER_NAME = "e7.scheduling"

VALID_RESULT_STATUSES = frozenset({"FEASIBLE", "INFEASIBLE", "BUDGET_EXHAUSTED", "FAILED"})


class SchedulingTelemetry:
    """Encapsulates scheduling meters, counters, histograms, and safe recording logic."""

    def __init__(self) -> None:
        self._tracer = trace.get_tracer(_TRACER_NAME)
        self._meter = metrics.get_meter(_METER_NAME)

        self._attempts_counter = self._meter.create_counter(
            name="e7.scheduling.solve.attempts",
            unit="1",
            description="Total number of schedule solve attempts initiated",
        )
        self._results_counter = self._meter.create_counter(
            name="e7.scheduling.solve.results",
            unit="1",
            description="Total number of schedule solve attempts by outcome status",
        )
        self._duration_histogram = self._meter.create_histogram(
            name="e7.scheduling.solve.duration",
            unit="s",
            description="Duration of schedule solves in seconds",
        )
        self._assignments_counter = self._meter.create_counter(
            name="e7.scheduling.assignments.generated",
            unit="1",
            description="Total number of candidate assignments generated in feasible solves",
        )
        self._infeasibility_counter = self._meter.create_counter(
            name="e7.scheduling.infeasibility.occurrences",
            unit="1",
            description="Total count of infeasible solver results",
        )

    def record_attempt(self) -> None:
        """Safely record a solve attempt."""
        try:
            self._attempts_counter.add(1)
        except Exception:
            pass

    def record_result(
        self,
        status: str,
        duration_seconds: float,
        assignment_count: int = 0,
    ) -> None:
        """Safely record solve outcome metrics with bounded categorical attributes."""
        try:
            res_status = status if status in VALID_RESULT_STATUSES else "FAILED"
            self._results_counter.add(1, {"result_status": res_status})
            self._duration_histogram.record(
                max(0.0, duration_seconds), {"result_status": res_status}
            )

            if res_status == "FEASIBLE" and assignment_count > 0:
                self._assignments_counter.add(assignment_count)
            elif res_status == "INFEASIBLE":
                self._infeasibility_counter.add(1)
        except Exception:
            pass

    @contextmanager
    def span_solve(self) -> Generator[trace.Span, None, None]:
        """Context manager to span a schedule solve execution."""
        start_time = time.monotonic()
        with self._tracer.start_as_current_span("e7.scheduling.solve") as span:
            self.record_attempt()
            try:
                yield span
            except Exception as exc:
                duration = time.monotonic() - start_time
                self.record_result("FAILED", duration)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise


_telemetry_singleton: SchedulingTelemetry | None = None


def get_scheduling_telemetry() -> SchedulingTelemetry:
    """Return the global SchedulingTelemetry instance."""
    global _telemetry_singleton
    if _telemetry_singleton is None:
        _telemetry_singleton = SchedulingTelemetry()
    return _telemetry_singleton
