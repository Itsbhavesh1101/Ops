"""AI operational telemetry module for Task Intelligence (Phase 2B).

Provides safe OpenTelemetry metrics and tracing for AI planning:
- Metrics:
  * e7.planner.requests (Counter)
  * e7.planner.duration (Histogram, seconds)
  * e7.planner.repair_attempts (Counter)
  * e7.planner.fallback_used (Counter)
  * e7.planner.validation_outcomes (Counter)
  * e7.planner.tokens (Counter, if reported)

Strict privacy and cardinality guarantees:
- NO tenant_id, goal_id, plan_id, or task_id metric labels
- NO prompt or model output text
- NO exception message or stack trace in labels
- Closed attribute categories only (e.g. status: success, failure, repaired)
- Telemetry recording errors never alter business outcomes
"""

import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

_TRACER_NAME = "e7.task_intelligence.planner"
_METER_NAME = "e7.task_intelligence.planner"

VALID_OUTCOMES = frozenset({"success", "failure", "repaired", "exhausted"})


class PlannerTelemetry:
    """Encapsulates safe AI planning telemetry recording."""

    def __init__(self) -> None:
        self._tracer = trace.get_tracer(_TRACER_NAME)
        self._meter = metrics.get_meter(_METER_NAME)

        self._requests_counter = self._meter.create_counter(
            name="e7.planner.requests",
            unit="1",
            description="Total number of AI plan generation requests initiated",
        )
        self._duration_histogram = self._meter.create_histogram(
            name="e7.planner.duration",
            unit="s",
            description="End-to-end plan generation latency in seconds",
        )
        self._repair_counter = self._meter.create_counter(
            name="e7.planner.repair_attempts",
            unit="1",
            description="Total number of targeted validation repair attempts executed",
        )
        self._fallback_counter = self._meter.create_counter(
            name="e7.planner.fallback_used",
            unit="1",
            description="Total number of times a fallback model was invoked",
        )
        self._outcome_counter = self._meter.create_counter(
            name="e7.planner.validation_outcomes",
            unit="1",
            description="Deterministic validation outcome of AI proposals",
        )
        self._token_counter = self._meter.create_counter(
            name="e7.planner.tokens",
            unit="1",
            description="Total reported tokens consumed during AI plan generation",
        )

    @contextmanager
    def record_generation(
        self,
        provider: str = "openai_compatible",
    ) -> Generator[dict[str, Any], None, None]:
        """Context manager to measure latency and record planner outcomes safely."""
        start_time = time.perf_counter()
        span = self._tracer.start_span("e7.planner.generate")
        telemetry_ctx: dict[str, Any] = {
            "outcome": "failure",
            "fallback_used": False,
            "repair_count": 0,
            "tokens": 0,
        }

        try:
            yield telemetry_ctx
            span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, description=type(exc).__name__))
            raise
        finally:
            elapsed = time.perf_counter() - start_time
            span.end()

            try:
                # Record metrics with strictly bounded, low-cardinality attributes
                self._requests_counter.add(1, {"provider": provider})
                self._duration_histogram.record(elapsed, {"provider": provider})

                outcome = telemetry_ctx.get("outcome", "failure")
                if outcome not in VALID_OUTCOMES:
                    outcome = "failure"
                self._outcome_counter.add(1, {"outcome": outcome})

                if telemetry_ctx.get("fallback_used"):
                    self._fallback_counter.add(1, {"provider": provider})

                repairs = telemetry_ctx.get("repair_count", 0)
                if repairs > 0:
                    self._repair_counter.add(repairs)

                tokens = telemetry_ctx.get("tokens", 0)
                if tokens > 0:
                    self._token_counter.add(tokens)
            except Exception:
                # Telemetry failures must never break operational workflows
                pass


_telemetry_instance: PlannerTelemetry | None = None


def get_planner_telemetry() -> PlannerTelemetry:
    """Return singleton PlannerTelemetry instance."""
    global _telemetry_instance
    if _telemetry_instance is None:
        _telemetry_instance = PlannerTelemetry()
    return _telemetry_instance
