"""Capacity, queue depth, and quota telemetry module for Phase-8E.

Provides low-cardinality OpenTelemetry metrics for system backpressure and capacity controls:
- Counters:
  * e7.capacity.quota.rejections (labels: solver_jobs, plan_generation, workflow_burst, analytics)
- Gauges / Histograms:
  * e7.capacity.outbox.pending_depth
  * e7.capacity.outbox.oldest_age_seconds
  * e7.capacity.schedule_queue.depth
  * e7.capacity.schedule_queue.oldest_age_seconds
  * e7.capacity.repair_queue.depth
  * e7.capacity.workflow.pressure

Strict cardinality and privacy guarantees:
- NO tenant_id as metric labels (prevents unbounded cardinality explosion in Prometheus).
- Zero PII attached to metrics.
- Telemetry recording errors NEVER fail business operations.
"""

from opentelemetry import metrics

_METER_NAME = "e7.capacity"

VALID_RESOURCE_CLASSES = frozenset(
    {"solver_jobs", "plan_generation", "workflow_burst", "analytics", "repair_jobs"}
)


class CapacityTelemetry:
    """Encapsulates low-cardinality capacity controls and queue telemetry."""

    def __init__(self) -> None:
        self._meter = metrics.get_meter(_METER_NAME)

        self._quota_rejections = self._meter.create_counter(
            name="e7.capacity.quota.rejections",
            unit="1",
            description="Total number of tenant quota and capacity control rejections",
        )
        self._outbox_pending_gauge = self._meter.create_up_down_counter(
            name="e7.capacity.outbox.pending_depth",
            unit="1",
            description="High-water mark depth of pending outbox events",
        )
        self._outbox_oldest_age = self._meter.create_histogram(
            name="e7.capacity.outbox.oldest_age_seconds",
            unit="s",
            description="Age of oldest pending outbox event in seconds",
        )
        self._schedule_queue_depth = self._meter.create_up_down_counter(
            name="e7.capacity.schedule_queue.depth",
            unit="1",
            description="High-water mark depth of pending schedule solver jobs",
        )
        self._schedule_oldest_age = self._meter.create_histogram(
            name="e7.capacity.schedule_queue.oldest_age_seconds",
            unit="s",
            description="Age of oldest requested schedule solver job in seconds",
        )
        self._repair_queue_depth = self._meter.create_up_down_counter(
            name="e7.capacity.repair_queue.depth",
            unit="1",
            description="High-water mark depth of pending schedule repair queue",
        )
        self._workflow_pressure = self._meter.create_up_down_counter(
            name="e7.capacity.workflow.pressure",
            unit="1",
            description="Concurrent active workflow process creation pressure",
        )

    def record_quota_rejection(self, resource_class: str) -> None:
        """Safely record a tenant quota rejection without exposing tenant ID."""
        r_cls = resource_class if resource_class in VALID_RESOURCE_CLASSES else "other"
        try:
            self._quota_rejections.add(1, {"resource_class": r_cls})
        except BaseException:
            pass

    def record_queue_depths(
        self,
        *,
        outbox_depth: int | None = None,
        schedule_depth: int | None = None,
        repair_depth: int | None = None,
        workflow_pressure: int | None = None,
    ) -> None:
        """Record queue depth gauges safely."""
        try:
            if outbox_depth is not None:
                self._outbox_pending_gauge.add(outbox_depth)
            if schedule_depth is not None:
                self._schedule_queue_depth.add(schedule_depth)
            if repair_depth is not None:
                self._repair_queue_depth.add(repair_depth)
            if workflow_pressure is not None:
                self._workflow_pressure.add(workflow_pressure)
        except BaseException:
            pass

    def record_oldest_ages(
        self,
        *,
        outbox_age_seconds: float | None = None,
        schedule_age_seconds: float | None = None,
    ) -> None:
        """Record oldest pending item ages safely."""
        try:
            if outbox_age_seconds is not None and outbox_age_seconds >= 0.0:
                self._outbox_oldest_age.record(outbox_age_seconds)
            if schedule_age_seconds is not None and schedule_age_seconds >= 0.0:
                self._schedule_oldest_age.record(schedule_age_seconds)
        except BaseException:
            pass


_telemetry_instance: CapacityTelemetry | None = None


def get_capacity_telemetry() -> CapacityTelemetry:
    """Return process-global CapacityTelemetry instance."""
    global _telemetry_instance
    if _telemetry_instance is None:
        _telemetry_instance = CapacityTelemetry()
    return _telemetry_instance
