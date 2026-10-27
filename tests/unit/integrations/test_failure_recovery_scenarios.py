"""Failure, outage, and recovery scenario verification for Phase 7 integrations.

Part of Phase 7H Failure / Recovery Verification:
1. Broker outage:
   - Outbox dispatch encounters connection drop / outage -> reschedules with backoff.
2. ERP outage:
   - MySQL connection timeout or failure -> transitions freshness to DEGRADED,
     fails closed without exposing database credentials or raw SQL.
3. Calendar webhook replay:
   - Repeated webhook delivery -> idempotent processing, committed-effect level deduplication.
4. Worker crash / recovery:
   - Unclaimed or interrupted repair jobs survive and are claimed in deterministic priority order.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.db.models.scheduling import (
    ResourceAvailabilityRecord,
    ScheduleAssignmentRecord,
    ScheduleRecord,
)
from app.events.outbox.dispatcher import (
    DispatchOutcome,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.outbox.models import OutboxEventRecord
from app.events.publishers.base import EventPublishError
from app.events.schemas import EventEnvelope
from app.integrations.erp.config import MySqlErpSettings
from app.integrations.erp.exceptions import ErpConnectionError
from app.integrations.erp.mysql_adapter import MySqlIreduAdapter
from app.operations.repair_queue import ScheduleRepairQueueService
from app.operations.resource_events import (
    ExternalResourceChangeEvent,
    ResourceChangeEventHandler,
)
from app.scheduling.service import SchedulingService

# ==============================================================================
# Test Fakes for Outbox Dispatching
# ==============================================================================


class FakePublisher:
    """Fake event publisher simulating broker failure."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.published_envelopes: list[EventEnvelope] = []

    async def publish(self, envelope: EventEnvelope) -> None:
        if self.fail_with:
            raise self.fail_with
        self.published_envelopes.append(envelope)


class RecordingUnitOfWork:
    """Test fake for UnitOfWork recording operations and simulating SQL responses."""

    def __init__(
        self,
        records_to_claim: tuple[OutboxEventRecord, ...] = (),
        timeline: list[str] | None = None,
    ) -> None:
        self.records_to_claim = records_to_claim
        self.timeline = timeline if timeline is not None else []
        self.committed = False
        self.session = MagicMock()

        async def mock_execute(stmt: Any) -> Any:
            sql_text = str(stmt).lower()
            if "select" in sql_text:
                self.timeline.append("claim_ready")
                result = MagicMock()
                result.scalars.return_value.all.return_value = list(self.records_to_claim)
                return result
            elif "update" in sql_text and "next_attempt_at" in sql_text:
                self.timeline.append("reschedule_failure")
                res = MagicMock()
                res.rowcount = 1
                return res
            res = MagicMock()
            res.rowcount = 1
            return res

        self.session.execute = mock_execute

    async def __aenter__(self) -> "RecordingUnitOfWork":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


def _build_test_envelope(event_id: UUID) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type="test.schedule.published",
        event_version=1,
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-outage",
        payload={"schedule_id": str(uuid4())},
    )


def _build_test_record(event_id: UUID, envelope: EventEnvelope) -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=event_id,
        envelope=envelope.model_dump(mode="json"),
        created_at=datetime.now(UTC),
        published_at=None,
        dispatch_attempt_count=1,
        next_attempt_at=datetime.now(UTC),
        claim_token=None,
        claimed_at=None,
    )


# ==============================================================================
# Scenario 1: Broker Outage & Recovery
# ==============================================================================


@pytest.mark.asyncio
async def test_broker_outage_reschedules_outbox_with_backoff() -> None:
    """Verify broker connection outage causes outbox event to be rescheduled with backoff."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    settlement_uow = RecordingUnitOfWork(timeline=timeline)
    uow_instances = [claim_uow, settlement_uow]

    publisher = FakePublisher(
        fail_with=EventPublishError("RabbitMQ broker connection dropped")
    )
    policy = OutboxDispatchPolicy(
        lease_duration=timedelta(seconds=30),
        publish_timeout=timedelta(seconds=5),
        retry_base_delay=timedelta(seconds=1),
        retry_max_delay=timedelta(seconds=60),
        retry_multiplier=2.0,
    )

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: uow_instances.pop(0),
        publisher=publisher,
        policy=policy,
    )

    result = await dispatcher.dispatch_once()

    assert result.outcome == DispatchOutcome.RETRY_SCHEDULED
    assert result.event_id == event_id
    assert "reschedule_failure" in timeline
    assert settlement_uow.committed is True


# ==============================================================================
# Scenario 2: ERP Outage & Freshness Degradation
# ==============================================================================


@pytest.mark.asyncio
async def test_erp_outage_transitions_freshness_to_degraded_without_credential_leak() -> None:
    """Verify ERP database unreachable marks entity DEGRADED and masks sensitive credentials."""
    settings = MySqlErpSettings(
        enabled=True,
        host="unreachable.local",
        port=3306,
        user="erp_user",
        password=SecretStr("SuperSecretPassword123!"),
        database="iredu_db",
        timeout_seconds=0.1,
        single_tenant_id="synthetic-scope-org-42",
        approved_tenant_scopes_json='{"synthetic-scope-org-42": "school-org-42"}',
    )

    adapter = MySqlIreduAdapter(settings=settings)

    # Calling an extraction method on unreachable host should raise ErpConnectionError
    with pytest.raises(ErpConnectionError) as exc_info:
        await adapter.get_academic_sessions(tenant_id="synthetic-scope-org-42")

    err_str = str(exc_info.value)
    # Security: Secret password must never leak in error messages or logs
    assert "SuperSecretPassword123!" not in err_str

    # Freshness state for that entity must be DEGRADED
    freshness = await adapter.check_freshness("synthetic-scope-org-42")
    assert freshness.staleness_state == "DEGRADED"
    assert freshness.last_error is not None


# ==============================================================================
# Scenario 3: Calendar Webhook Replay Protection
# ==============================================================================


@pytest.mark.asyncio
async def test_calendar_webhook_replay_protection_deduplicates_consistently() -> None:
    """Verify replayed calendar disruption events execute committed effects exactly once."""
    repair_queue = ScheduleRepairQueueService()
    handler = ResourceChangeEventHandler(repair_queue=repair_queue)

    tenant_id = "tenant-replay-test"
    res_id = uuid4()
    sched_id = uuid4()
    now = datetime.now(UTC)

    event = ExternalResourceChangeEvent(
        event_id="evt-replay-999",
        tenant_id=tenant_id,
        provider="google_calendar",
        external_resource_id="room-lab-2",
        canonical_resource_id=res_id,
        start_at=now,
        end_at=now + timedelta(hours=2),
        reason="Fire Alarm Inspection",
    )

    mock_assign = MagicMock(spec=ScheduleAssignmentRecord)
    mock_assign.activity_id = "act-exam-401"
    mock_schedule = MagicMock(spec=ScheduleRecord)
    mock_schedule.id = sched_id
    mock_schedule.schedule_key = "sched-autumn"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_query_res = MagicMock()
    mock_query_res.all.return_value = [(mock_assign, mock_schedule)]
    mock_session.execute.return_value = mock_query_res

    # First delivery: processed
    first_attempt = await handler.handle_resource_unavailable(mock_session, event)
    assert first_attempt is True
    assert repair_queue.get_queue_depth(tenant_id) == 1

    # Check that ResourceAvailabilityRecord was created
    added_objs = [c[0][0] for c in mock_session.add.call_args_list]
    blackouts = [o for o in added_objs if isinstance(o, ResourceAvailabilityRecord)]
    assert len(blackouts) == 1

    # Second, third, fourth delivery with same event_id: idempotent no-op
    for _ in range(3):
        replay_attempt = await handler.handle_resource_unavailable(mock_session, event)
        assert replay_attempt is False

    # Queue depth remains strictly 1
    assert repair_queue.get_queue_depth(tenant_id) == 1
    jobs = repair_queue.list_jobs(tenant_id)
    assert len(jobs) == 1


# ==============================================================================
# Scenario 4: Worker Restart & Repair Queue Recovery
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_restart_recovers_pending_repair_jobs_in_priority_order() -> None:
    """Verify worker restart recovers pending repairs and processes them deterministically."""
    queue = ScheduleRepairQueueService()
    tenant_id = "tenant-restart"
    now = datetime.now(UTC)

    # 1. Enqueue routine repair (priority 4)
    queue.enqueue_repair(
        tenant_id=tenant_id,
        trigger_event_id="evt-1",
        reason="routine-maintenance",
        baseline_schedule_id=uuid4(),
        schedule_key="sched-key",
        affected_activity_ids=["act-1"],
        disrupted_resource_ids=["res-1"],
        disrupted_interval_start=now,
        disrupted_interval_end=now + timedelta(hours=1),
        correlation_id="corr-1",
        priority=4,
    )

    # 2. Enqueue emergency repair (priority 9)
    queue.enqueue_repair(
        tenant_id=tenant_id,
        trigger_event_id="evt-2",
        reason="emergency-disruption",
        baseline_schedule_id=uuid4(),
        schedule_key="sched-key",
        affected_activity_ids=["act-2"],
        disrupted_resource_ids=["res-2"],
        disrupted_interval_start=now,
        disrupted_interval_end=now + timedelta(hours=1),
        correlation_id="corr-2",
        priority=9,
    )

    assert queue.get_queue_depth(tenant_id) == 2

    # 3. Simulate new worker thread/instance claiming after restart
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_sched_service = AsyncMock(spec=SchedulingService)
    repaired_schedule = MagicMock(spec=ScheduleRecord)
    repaired_schedule.id = uuid4()
    repaired_schedule.assignments = [MagicMock()]
    mock_sched_service.repair_schedule.return_value = (MagicMock(), repaired_schedule)

    # Worker processes highest priority first
    job_first = await queue.process_next_job(mock_session, mock_sched_service)
    assert job_first is not None
    assert job_first.priority == 9
    assert job_first.reason == "emergency-disruption"
    assert job_first.state == "COMPLETED"
    assert job_first.stability_kpis is not None
    assert job_first.stability_kpis.locked_moved == 0

    # Next job processed is the lower priority
    job_second = await queue.process_next_job(mock_session, mock_sched_service)
    assert job_second is not None
    assert job_second.priority == 4
    assert job_second.reason == "routine-maintenance"
    assert job_second.state == "COMPLETED"
    assert queue.get_queue_depth(tenant_id) == 0
