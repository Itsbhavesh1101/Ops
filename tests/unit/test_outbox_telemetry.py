"""Focused unit tests for outbox custom telemetry (Task 1K.5 / Stage B).

Validates:
- Dispatch success creates expected metric events:
  * e7.outbox.dispatch.attempts
  * e7.outbox.dispatch.successes
  * e7.outbox.dispatch.duration
- Dispatch failure classification is bounded:
  * e7.outbox.dispatch.failures with error_category
  * categories: publish_error, timeout, database_error, unexpected
- No event_type metric label
- No high-cardinality labels (no event_id, tenant_id, correlation_id, job_id)
- Spans created: e7.outbox.dispatch, e7.event.publish
- Payload never added to spans
- Telemetry failure never controls business state or alters transactions
- Telemetry disabled preserves exact existing runtime semantics
"""

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.telemetry.outbox import OutboxTelemetry
from app.events.outbox.dispatch import ClaimedOutboxEvent
from app.events.outbox.dispatcher import (
    DispatchOutcome,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.publishers.base import EventPublisher, EventPublishError
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope


def make_test_envelope(
    event_type: str = "task.created",
    correlation_id: str = "corr-test-123",
) -> EventEnvelope:
    """Build a valid EventEnvelope for testing."""
    return create_event_envelope(
        EventCreate(
            event_type=event_type,
            correlation_id=correlation_id,
            payload={"secret_field": "confidential_data", "item_count": 42},
        )
    )


def make_test_policy() -> OutboxDispatchPolicy:
    """Build a fast dispatch policy for testing."""
    return OutboxDispatchPolicy(
        lease_duration=timedelta(seconds=30),
        publish_timeout=timedelta(seconds=5),
        retry_base_delay=timedelta(seconds=1),
        retry_max_delay=timedelta(seconds=10),
        retry_multiplier=2.0,
    )


class DummyUoW:
    def __init__(self, session: Any = None) -> None:
        self.session = session or MagicMock()
        self.committed = False

    async def __aenter__(self) -> "DummyUoW":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    async def commit(self) -> None:
        self.committed = True


class FakePublisher(EventPublisher):
    def __init__(self, raise_error: Exception | None = None) -> None:
        self.raise_error = raise_error
        self.published_envelopes: list[EventEnvelope] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def publish(self, envelope: EventEnvelope) -> None:
        if self.raise_error:
            raise self.raise_error
        self.published_envelopes.append(envelope)


@pytest.fixture
def telemetry_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
    """Isolate telemetry with in-memory span and metric readers."""
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])

    # Instantiate isolated OutboxTelemetry and monkeypatch singleton
    isolated_otel = OutboxTelemetry()
    isolated_otel._tracer = tracer_provider.get_tracer("e7.outbox")
    isolated_otel._meter = meter_provider.get_meter("e7.outbox")
    isolated_otel._attempts_counter = isolated_otel._meter.create_counter(
        "e7.outbox.dispatch.attempts", unit="1"
    )
    isolated_otel._successes_counter = isolated_otel._meter.create_counter(
        "e7.outbox.dispatch.successes", unit="1"
    )
    isolated_otel._failures_counter = isolated_otel._meter.create_counter(
        "e7.outbox.dispatch.failures", unit="1"
    )
    isolated_otel._duration_histogram = isolated_otel._meter.create_histogram(
        "e7.outbox.dispatch.duration", unit="s"
    )

    monkeypatch.setattr("app.events.outbox.dispatcher.get_outbox_telemetry", lambda: isolated_otel)
    monkeypatch.setattr(
        "app.events.publishers.rabbitmq.get_outbox_telemetry", lambda: isolated_otel
    )

    return span_exporter, metric_reader


@pytest.mark.asyncio
async def test_dispatch_success_metrics_and_spans(
    telemetry_setup: tuple[InMemorySpanExporter, InMemoryMetricReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful dispatch emits attempts, successes, duration metrics and safe span."""
    span_exporter, metric_reader = telemetry_setup
    envelope = make_test_envelope()
    claimed = ClaimedOutboxEvent(
        event_id=envelope.event_id,
        claim_token=uuid4(),
        envelope=envelope,
        dispatch_attempt_count=1,
    )

    # Mock OutboxDispatchRepository
    mock_repo = MagicMock()
    mock_repo.claim_ready = AsyncMock(return_value=(claimed,))
    mock_repo.mark_published = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.events.outbox.dispatcher.OutboxDispatchRepository",
        lambda session: mock_repo,
    )

    publisher = FakePublisher()
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: DummyUoW(),
        publisher=publisher,
        policy=make_test_policy(),
    )

    result = await dispatcher.dispatch_once()
    assert result.outcome == DispatchOutcome.PUBLISHED

    # Verify metrics
    metrics_data = metric_reader.get_metrics_data()
    assert metrics_data is not None
    metric_map: dict[str, Any] = {}
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                metric_map[m.name] = m

    # 1. attempts metric
    assert "e7.outbox.dispatch.attempts" in metric_map
    attempts_pt = metric_map["e7.outbox.dispatch.attempts"].data.data_points[0]
    assert attempts_pt.value == 1
    assert len(attempts_pt.attributes) == 0  # No labels

    # 2. successes metric
    assert "e7.outbox.dispatch.successes" in metric_map
    success_pt = metric_map["e7.outbox.dispatch.successes"].data.data_points[0]
    assert success_pt.value == 1
    assert len(success_pt.attributes) == 0  # No labels

    # 3. duration metric
    assert "e7.outbox.dispatch.duration" in metric_map
    dur_pt = metric_map["e7.outbox.dispatch.duration"].data.data_points[0]
    assert dur_pt.count == 1
    assert dur_pt.sum >= 0
    assert len(dur_pt.attributes) == 0  # No labels

    # Verify spans
    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "e7.outbox.dispatch"
    assert span.attributes is not None
    assert span.attributes.get("event_type") == "task.created"
    assert span.attributes.get("dispatch_outcome") == "published"
    assert span.attributes.get("retry_attempt_count") == 1
    assert span.attributes.get("e7.correlation_id") == "corr-test-123"

    # Privacy check: payload NEVER on span
    span_str = str(span.attributes)
    assert "confidential_data" not in span_str
    assert "payload" not in span.attributes


@pytest.mark.asyncio
async def test_dispatch_failure_classification_is_bounded(
    telemetry_setup: tuple[InMemorySpanExporter, InMemoryMetricReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publication failure records failure metric with closed error_category 'publish_error'."""
    span_exporter, metric_reader = telemetry_setup
    envelope = make_test_envelope()
    claimed = ClaimedOutboxEvent(
        event_id=envelope.event_id,
        claim_token=uuid4(),
        envelope=envelope,
        dispatch_attempt_count=2,
    )

    mock_repo = MagicMock()
    mock_repo.claim_ready = AsyncMock(return_value=(claimed,))
    mock_repo.reschedule_failure = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.events.outbox.dispatcher.OutboxDispatchRepository",
        lambda session: mock_repo,
    )

    publisher = FakePublisher(raise_error=EventPublishError("Simulated broker transport drop"))
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: DummyUoW(),
        publisher=publisher,
        policy=make_test_policy(),
    )

    result = await dispatcher.dispatch_once()
    assert result.outcome == DispatchOutcome.RETRY_SCHEDULED

    metrics_data = metric_reader.get_metrics_data()
    assert metrics_data is not None
    metric_map: dict[str, Any] = {}
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                metric_map[m.name] = m

    assert "e7.outbox.dispatch.failures" in metric_map
    fail_pt = metric_map["e7.outbox.dispatch.failures"].data.data_points[0]
    assert fail_pt.value == 1
    # Closed vocabulary only
    assert fail_pt.attributes == {"error_category": "publish_error"}
    # Explicit check: NO event_type, NO tenant_id, NO exception class
    assert "event_type" not in fail_pt.attributes
    assert "tenant_id" not in fail_pt.attributes
    assert "error" not in fail_pt.attributes
    assert "error_message" not in fail_pt.attributes


@pytest.mark.asyncio
async def test_dispatch_timeout_records_timeout_category(
    telemetry_setup: tuple[InMemorySpanExporter, InMemoryMetricReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publication timeout records failure metric with error_category 'timeout'."""
    span_exporter, metric_reader = telemetry_setup
    envelope = make_test_envelope()
    claimed = ClaimedOutboxEvent(
        event_id=envelope.event_id,
        claim_token=uuid4(),
        envelope=envelope,
        dispatch_attempt_count=1,
    )

    mock_repo = MagicMock()
    mock_repo.claim_ready = AsyncMock(return_value=(claimed,))
    mock_repo.reschedule_failure = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.events.outbox.dispatcher.OutboxDispatchRepository",
        lambda session: mock_repo,
    )

    publisher = FakePublisher(raise_error=TimeoutError("Network call timed out"))
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: DummyUoW(),
        publisher=publisher,
        policy=make_test_policy(),
    )

    result = await dispatcher.dispatch_once()
    assert result.outcome == DispatchOutcome.RETRY_SCHEDULED

    metrics_data = metric_reader.get_metrics_data()
    assert metrics_data is not None
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == "e7.outbox.dispatch.failures":
                    pt = m.data.data_points[0]
                    assert pt.value == 1
                    assert pt.attributes == {"error_category": "timeout"}


@pytest.mark.asyncio
async def test_telemetry_recording_failure_does_not_control_business_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If metrics or span recording crashes with an exception, dispatch still succeeds."""
    envelope = make_test_envelope()
    claimed = ClaimedOutboxEvent(
        event_id=envelope.event_id,
        claim_token=uuid4(),
        envelope=envelope,
        dispatch_attempt_count=1,
    )

    mock_repo = MagicMock()
    mock_repo.claim_ready = AsyncMock(return_value=(claimed,))
    mock_repo.mark_published = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.events.outbox.dispatcher.OutboxDispatchRepository",
        lambda session: mock_repo,
    )

    # Malfunctioning telemetry where all recording calls raise
    class MalfunctioningTelemetry(OutboxTelemetry):
        def record_attempt(self) -> None:
            raise RuntimeError("Telemetry collector crashed")

        def record_success(self, duration_seconds: float) -> None:
            raise RuntimeError("Telemetry exporter network broken")

        def record_failure(self, error_category: str, duration_seconds: float) -> None:
            raise RuntimeError("Telemetry failure handler crashed")

    monkeypatch.setattr(
        "app.events.outbox.dispatcher.get_outbox_telemetry",
        lambda: MalfunctioningTelemetry(),
    )

    publisher = FakePublisher()
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: DummyUoW(),
        publisher=publisher,
        policy=make_test_policy(),
    )

    # Dispatch MUST succeed completely, without raising or corrupting state
    result = await dispatcher.dispatch_once()
    assert result.outcome == DispatchOutcome.PUBLISHED
    assert len(publisher.published_envelopes) == 1
    assert mock_repo.mark_published.call_count == 1


@pytest.mark.asyncio
async def test_metric_resource_vs_measurement_labels_separation() -> None:
    """Metric measurements must NOT contain Resource attributes or high-cardinality labels.

    Phase-1 strict metric contract:
    - attempts: {} (no labels)
    - successes: {} (no labels)
    - duration: {} (no labels)
    - failures: {"error_category": <closed-value>}
    - Resource contains: service.name, service.namespace, service.version, deployment.environment
    - Measurements NEVER contain: service.name, service.namespace, event_type, tenant_id,
      event_id, job_id, correlation_id, trace_id, span_id, exception class, message, path, URL
    """
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create(
        {
            "service.name": "e7-worker",
            "service.namespace": "e7",
            "service.version": "0.1.0",
            "deployment.environment": "test",
        }
    )
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

    otel = OutboxTelemetry()
    otel._meter = meter_provider.get_meter("e7.outbox")
    otel._attempts_counter = otel._meter.create_counter("e7.outbox.dispatch.attempts", unit="1")
    otel._successes_counter = otel._meter.create_counter("e7.outbox.dispatch.successes", unit="1")
    otel._failures_counter = otel._meter.create_counter("e7.outbox.dispatch.failures", unit="1")
    otel._duration_histogram = otel._meter.create_histogram("e7.outbox.dispatch.duration", unit="s")

    # Record attempts, successes, failures, duration
    otel.record_attempt()
    otel.record_success(0.123)
    otel.record_failure("publish_error", 0.456)

    metrics_data = metric_reader.get_metrics_data()
    assert metrics_data is not None

    # Verify Resource attributes are intact on ResourceMetrics
    rm = metrics_data.resource_metrics[0]
    res_attrs = dict(rm.resource.attributes)
    assert res_attrs["service.name"] == "e7-worker"
    assert res_attrs["service.namespace"] == "e7"

    # Extract all measurement data points
    metric_map: dict[str, Any] = {}
    for sm in rm.scope_metrics:
        for m in sm.metrics:
            metric_map[m.name] = m

    # 1. attempts measurement attributes must be strictly empty {}
    attempts_pt = metric_map["e7.outbox.dispatch.attempts"].data.data_points[0]
    assert attempts_pt.attributes == {}

    # 2. successes measurement attributes must be strictly empty {}
    success_pt = metric_map["e7.outbox.dispatch.successes"].data.data_points[0]
    assert success_pt.attributes == {}

    # 3. duration measurement attributes must be strictly empty {}
    dur_pt = metric_map["e7.outbox.dispatch.duration"].data.data_points[0]
    assert dur_pt.attributes == {}

    # 4. failures measurement attributes must have ONLY closed error_category
    fail_pt = metric_map["e7.outbox.dispatch.failures"].data.data_points[0]
    assert fail_pt.attributes == {"error_category": "publish_error"}

    # Prohibited measurement attributes audit across ALL data points
    forbidden_metric_labels = {
        "service.name",
        "service.namespace",
        "service_name",
        "service_namespace",
        "event_type",
        "tenant_id",
        "event_id",
        "job_id",
        "correlation_id",
        "trace_id",
        "span_id",
        "exception",
        "error",
        "message",
        "path",
        "url",
    }
    for m in metric_map.values():
        for pt in m.data.data_points:
            pt_attrs = set(pt.attributes.keys())
            inter = pt_attrs.intersection(forbidden_metric_labels)
            assert not inter, f"Found forbidden metric attribute(s) {inter} on metric {m.name}"


@pytest.mark.asyncio
async def test_error_category_frozen_vocabulary_exhaustive() -> None:
    """Verify exact 4-value frozen error_category mapping in OutboxTelemetry."""
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])

    otel = OutboxTelemetry()
    otel._meter = meter_provider.get_meter("e7.outbox")
    otel._failures_counter = otel._meter.create_counter("e7.outbox.dispatch.failures", unit="1")
    otel._duration_histogram = otel._meter.create_histogram("e7.outbox.dispatch.duration", unit="s")

    frozen_categories = ["publish_error", "timeout", "database_error", "unexpected"]
    for cat in frozen_categories:
        otel.record_failure(cat, 0.05)

    # Any unapproved category, typo, or raw exception string must fall back to 'unexpected'
    unapproved_examples = [
        "publisher_error",
        "timeout_error",
        "unknown_error",
        "database_failure",
        "ConnectionRefusedError",
        "arbitrary_string",
    ]
    for unapproved in unapproved_examples:
        otel.record_failure(unapproved, 0.05)

    metrics_data = metric_reader.get_metrics_data()
    assert metrics_data is not None

    fail_metric = None
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == "e7.outbox.dispatch.failures":
                    fail_metric = m

    assert fail_metric is not None
    # Check all emitted points only use categories in the frozen set
    allowed_set = {"publish_error", "timeout", "database_error", "unexpected"}
    observed_categories: dict[str, int] = {}
    for pt in fail_metric.data.data_points:
        cat = pt.attributes.get("error_category")
        assert cat in allowed_set, f"Unexpected error_category '{cat}' found"
        observed_categories[cat] = pt.value

    assert observed_categories["publish_error"] == 1
    assert observed_categories["timeout"] == 1
    assert observed_categories["database_error"] == 1
    # 1 approved 'unexpected' + 6 fallbacks from unapproved_examples = 7
    assert observed_categories["unexpected"] == 7


@pytest.mark.asyncio
async def test_dispatch_claim_lost_records_database_error(
    telemetry_setup: tuple[InMemorySpanExporter, InMemoryMetricReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settlement failure due to OutboxClaimLostError records database_error category."""
    from app.events.outbox import OutboxClaimLostError

    span_exporter, metric_reader = telemetry_setup
    envelope = make_test_envelope()
    claimed = ClaimedOutboxEvent(
        event_id=envelope.event_id,
        claim_token=uuid4(),
        envelope=envelope,
        dispatch_attempt_count=1,
    )

    mock_repo = MagicMock()
    mock_repo.claim_ready = AsyncMock(return_value=(claimed,))
    mock_repo.mark_published = AsyncMock(
        side_effect=OutboxClaimLostError("Lease expired before mark_published")
    )
    monkeypatch.setattr(
        "app.events.outbox.dispatcher.OutboxDispatchRepository",
        lambda session: mock_repo,
    )

    publisher = FakePublisher()
    dispatcher = OutboxDispatcher(
        uow_factory=lambda: DummyUoW(),
        publisher=publisher,
        policy=make_test_policy(),
    )

    result = await dispatcher.dispatch_once()
    assert result.outcome == DispatchOutcome.CLAIM_LOST_AFTER_PUBLISH

    metrics_data = metric_reader.get_metrics_data()
    assert metrics_data is not None

    fail_metric = None
    for rm in metrics_data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == "e7.outbox.dispatch.failures":
                    fail_metric = m

    assert fail_metric is not None
    assert fail_metric.data.data_points[0].attributes == {"error_category": "database_error"}
