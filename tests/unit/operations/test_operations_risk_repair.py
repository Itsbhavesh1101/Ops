"""Unit and contract tests for Real-Time Operations, Risk Monitor, Freshness, and Repair Queue.

Part of Phase 7E & 7F:
- Freshness tracking and state transitions (FRESH -> STALE -> DEGRADED -> OFFLINE).
- Deterministic risk monitor (stale tasks, estimate overruns, outages, stale data).
- Controlled schedule repair queue and stability KPIs (locked_moved == 0 invariant).
- Resource change event normalization, deduplication, and replay protection.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models.planning import TaskRecord
from app.db.models.scheduling import (
    ResourceAvailabilityRecord,
    ResourceRecord,
    ScheduleAssignmentRecord,
    ScheduleRecord,
)
from app.operations.freshness import ResourceFreshnessTracker
from app.operations.repair_queue import ScheduleRepairQueueService
from app.operations.resource_events import (
    ExternalResourceChangeEvent,
    ResourceChangeEventHandler,
)
from app.operations.risk_monitor import (
    RiskMonitorService,
    RiskSeverity,
    RiskSignalDTO,
    RiskType,
)
from app.scheduling.service import SchedulingService

# ==============================================================================
# 1. Freshness Tracker Tests
# ==============================================================================


def test_freshness_tracker_initial_offline_and_record_fetch() -> None:
    """Verify initial state is OFFLINE and record_fetch transitions to FRESH."""
    tracker = ResourceFreshnessTracker(default_threshold_seconds=60.0)

    # Unknown resource defaults to OFFLINE
    rec = tracker.get_freshness("tenant-a", "google_calendar", "cal-100")
    assert rec.staleness_state == "OFFLINE"
    assert rec.is_stale is True

    # Record successful fetch
    fresh = tracker.record_fetch(
        tenant_id="tenant-a",
        provider="google_calendar",
        external_id="cal-100",
        source_version="v1",
        threshold_seconds=120.0,
    )
    assert fresh.staleness_state == "FRESH"
    assert fresh.is_stale is False
    assert fresh.source_version == "v1"

    # Query again
    queried = tracker.get_freshness("tenant-a", "google_calendar", "cal-100")
    assert queried.staleness_state == "FRESH"
    assert queried.is_stale is False


def test_freshness_tracker_transitions_to_stale_after_threshold() -> None:
    """Verify resource transitions from FRESH to STALE when elapsed time exceeds threshold."""
    tracker = ResourceFreshnessTracker(default_threshold_seconds=1.0)
    tracker.record_fetch("tenant-a", "mysql_iredu", "staff-1", threshold_seconds=0.01)

    import time

    time.sleep(0.05)  # Let threshold lapse

    rec = tracker.get_freshness("tenant-a", "mysql_iredu", "staff-1")
    assert rec.staleness_state == "STALE"
    assert rec.is_stale is True


def test_freshness_tracker_records_failure_as_degraded() -> None:
    """Verify fetch failure transitions state to DEGRADED with error message."""
    tracker = ResourceFreshnessTracker()
    tracker.record_fetch("tenant-a", "google_calendar", "cal-1")
    failed = tracker.record_failure(
        "tenant-a", "google_calendar", "cal-1", error_message="API Rate Limited"
    )

    assert failed.staleness_state == "DEGRADED"
    assert failed.last_failure == "API Rate Limited"
    assert failed.is_stale is True


def test_freshness_tracker_tenant_isolation() -> None:
    """Verify listings and lookups strictly isolate tenants."""
    tracker = ResourceFreshnessTracker()
    tracker.record_fetch("tenant-a", "p1", "e1")
    tracker.record_fetch("tenant-b", "p1", "e2")
    tracker.record_failure("tenant-a", "p1", "e3", error_message="err")

    tenant_a_stale = tracker.list_stale_resources("tenant-a")
    assert len(tenant_a_stale) == 1
    assert tenant_a_stale[0].external_id == "e3"

    tenant_b_all = tracker.list_all_resources("tenant-b")
    assert len(tenant_b_all) == 1
    assert tenant_b_all[0].external_id == "e2"


# ==============================================================================
# 2. Deterministic Risk Monitor Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_risk_monitor_detects_stale_task_in_progress() -> None:
    """Verify task in progress with 0% progress for >24h generates STALE_TASK_NO_PROGRESS risk."""
    tracker = ResourceFreshnessTracker()
    service = RiskMonitorService(freshness_tracker=tracker)

    plan_id = uuid4()
    tenant_id = "tenant-risk-test"

    stale_task = TaskRecord(
        id=uuid4(),
        plan_id=plan_id,
        tenant_id=tenant_id,
        temporary_id="task-stale",
        title="Stale Research Task",
        status="IN_PROGRESS",
        progress_percentage=0,
        estimated_duration_minutes=60,
        updated_at=datetime.now(UTC) - timedelta(hours=30),
    )

    mock_session = AsyncMock()
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value.all.return_value = [stale_task]
    mock_session.execute.return_value = mock_execute_result

    risks = await service.evaluate_plan_risks(mock_session, tenant_id, plan_id)

    assert len(risks) == 1
    assert risks[0].risk_type == RiskType.STALE_TASK_NO_PROGRESS
    assert risks[0].severity == RiskSeverity.MEDIUM
    assert risks[0].entity_scope == str(stale_task.id)
    assert "hours_stale" in risks[0].evidence


@pytest.mark.asyncio
async def test_risk_monitor_detects_estimate_variance_overrun() -> None:
    """Verify task actual duration exceeding estimate by >50% generates ESTIMATE_VARIANCE risk."""
    service = RiskMonitorService()
    plan_id = uuid4()
    tenant_id = "tenant-risk-test"

    overrun_task = TaskRecord(
        id=uuid4(),
        plan_id=plan_id,
        tenant_id=tenant_id,
        temporary_id="task-overrun",
        title="Overrun Task",
        status="IN_PROGRESS",
        progress_percentage=50,
        estimated_duration_minutes=60,
        actual_duration_minutes=150,  # 150% overrun (>100% -> HIGH severity)
        updated_at=datetime.now(UTC),
    )

    mock_session = AsyncMock()
    mock_execute_result = MagicMock()
    mock_execute_result.scalars.return_value.all.return_value = [overrun_task]
    mock_session.execute.return_value = mock_execute_result

    risks = await service.evaluate_plan_risks(mock_session, tenant_id, plan_id)

    assert len(risks) == 1
    assert risks[0].risk_type == RiskType.ESTIMATE_VARIANCE
    assert risks[0].severity == RiskSeverity.HIGH
    assert risks[0].evidence["variance_ratio"] == 1.5


@pytest.mark.asyncio
async def test_risk_monitor_incorporates_stale_upstream_resources() -> None:
    """Verify freshness tracker degraded resources surface as STALE_UPSTREAM_RESOURCE risk."""
    tracker = ResourceFreshnessTracker()
    tracker.record_failure("tenant-a", "mysql_iredu", "room-101", error_message="DB Conn Timeout")

    service = RiskMonitorService(freshness_tracker=tracker)
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_res

    risks = await service.evaluate_plan_risks(mock_session, "tenant-a", uuid4())

    assert len(risks) == 1
    assert risks[0].risk_type == RiskType.STALE_UPSTREAM_RESOURCE
    assert risks[0].severity == RiskSeverity.HIGH
    assert risks[0].entity_scope == "mysql_iredu:room-101"


@pytest.mark.asyncio
async def test_risk_monitor_persist_risks_and_audit() -> None:
    """Verify persist_risks_and_audit creates audit records without schema violations."""
    service = RiskMonitorService()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    risks = [
        RiskSignalDTO(
            tenant_id="tenant-audit-test",
            entity_scope="task-999",
            risk_type=RiskType.STALE_TASK_NO_PROGRESS,
            severity=RiskSeverity.MEDIUM,
            rule_name="rule_stale_task",
            description="Task is stale",
        )
    ]

    await service.persist_risks_and_audit(mock_session, risks)
    assert mock_session.add.called
    added = mock_session.add.call_args[0][0]
    assert added.event_type == "risk.detected"
    assert added.resource_type == "stale_task_no_progress"
    assert added.tenant_id == "tenant-audit-test"


@pytest.mark.asyncio
async def test_risk_monitor_detects_resource_blackout_and_inactivity() -> None:
    """Verify inactive resources and active blackouts produce RESOURCE_ABSENCE risks."""
    service = RiskMonitorService()
    res_id = uuid4()
    tenant_id = "tenant-a"
    now = datetime.now(UTC)

    inactive_resource = ResourceRecord(
        id=res_id,
        tenant_id=tenant_id,
        resource_key="res-offline",
        display_label="Lab Unit A",
        type="FACILITY",
        active=False,
    )

    active_blackout = ResourceAvailabilityRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        resource_id=res_id,
        start_at=now - timedelta(minutes=10),
        end_at=now + timedelta(minutes=50),
        interval_type="UNAVAILABLE",
        reason="Power outage",
    )

    mock_session = AsyncMock()
    mock_res_query = MagicMock()
    mock_res_query.scalar_one_or_none.return_value = inactive_resource

    mock_avail_query = MagicMock()
    mock_avail_query.scalars.return_value.all.return_value = [active_blackout]

    mock_session.execute.side_effect = [mock_res_query, mock_avail_query]

    risks = await service.evaluate_resource_availability_risks(mock_session, tenant_id, res_id)

    assert len(risks) == 2
    types = [r.risk_type for r in risks]
    assert types == [RiskType.RESOURCE_ABSENCE, RiskType.RESOURCE_ABSENCE]
    severities = [r.severity for r in risks]
    assert RiskSeverity.HIGH in severities  # Inactive
    assert RiskSeverity.CRITICAL in severities  # Active blackout


# ==============================================================================
# 3. Schedule Repair Queue & Stability KPIs Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_repair_queue_priority_ordering_and_depth() -> None:
    """Verify repair queue respects priority and calculates depth accurately."""
    queue = ScheduleRepairQueueService()
    tenant_id = "tenant-sched"
    base_id = uuid4()
    now = datetime.now(UTC)

    # Enqueue low priority job
    job_low = queue.enqueue_repair(
        tenant_id=tenant_id,
        trigger_event_id="trig-1",
        reason="routine",
        baseline_schedule_id=base_id,
        schedule_key="sched-main",
        affected_activity_ids=["act-1"],
        disrupted_resource_ids=["res-1"],
        disrupted_interval_start=now,
        disrupted_interval_end=now + timedelta(hours=1),
        correlation_id="corr-low",
        priority=3,
    )

    # Enqueue high priority job
    job_high = queue.enqueue_repair(
        tenant_id=tenant_id,
        trigger_event_id="trig-2",
        reason="emergency",
        baseline_schedule_id=base_id,
        schedule_key="sched-main",
        affected_activity_ids=["act-2"],
        disrupted_resource_ids=["res-2"],
        disrupted_interval_start=now,
        disrupted_interval_end=now + timedelta(hours=1),
        correlation_id="corr-high",
        priority=9,
    )

    assert queue.get_queue_depth(tenant_id) == 2
    assert len(queue.list_jobs(tenant_id, state="PENDING")) == 2

    # Mock SchedulingService to process
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_sched_service = AsyncMock(spec=SchedulingService)
    repaired_schedule = MagicMock(spec=ScheduleRecord)
    repaired_schedule.id = uuid4()
    repaired_schedule.assignments = [MagicMock(), MagicMock()]  # 2 assignments
    mock_sched_service.repair_schedule.return_value = (MagicMock(), repaired_schedule)

    # High priority should be processed first
    processed = await queue.process_next_job(mock_session, mock_sched_service)
    assert processed is not None
    assert processed.job_id == job_high.job_id
    assert processed.state == "COMPLETED"
    assert processed.stability_kpis is not None
    assert processed.stability_kpis.locked_moved == 0  # Strict invariant
    assert processed.stability_kpis.assignments_moved == 1
    assert processed.stability_kpis.percentage_moved == 50.0  # 1 / 2 * 100

    # Low priority should be processed next
    processed_second = await queue.process_next_job(mock_session, mock_sched_service)
    assert processed_second is not None
    assert processed_second.job_id == job_low.job_id
    assert processed_second.state == "COMPLETED"
    assert queue.get_queue_depth(tenant_id) == 0


@pytest.mark.asyncio
async def test_repair_queue_handles_repair_failure_gracefully() -> None:
    """Verify failing repair schedule records FAILED state and error message."""
    queue = ScheduleRepairQueueService()
    tenant_id = "tenant-fail"
    now = datetime.now(UTC)

    queue.enqueue_repair(
        tenant_id=tenant_id,
        trigger_event_id="trig-fail",
        reason="disruption",
        baseline_schedule_id=uuid4(),
        schedule_key="sched-key",
        affected_activity_ids=["act-fail"],
        disrupted_resource_ids=["res-fail"],
        disrupted_interval_start=now,
        disrupted_interval_end=now + timedelta(hours=1),
        correlation_id="corr-fail",
        priority=5,
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_sched_service = AsyncMock(spec=SchedulingService)
    mock_sched_service.repair_schedule.side_effect = RuntimeError("OR-Tools infeasible model")

    processed = await queue.process_next_job(mock_session, mock_sched_service)
    assert processed is not None
    assert processed.state == "FAILED"
    assert "infeasible model" in (processed.error_message or "")


# ==============================================================================
# 4. Resource Change Event Normalization & Replay Protection Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_resource_change_event_normalizes_and_enqueues_repair() -> None:
    """Verify disruption creates blackout, finds affected assignments, and enqueues repair."""
    queue = ScheduleRepairQueueService()
    handler = ResourceChangeEventHandler(repair_queue=queue)

    tenant_id = "tenant-event-test"
    res_id = uuid4()
    sched_id = uuid4()
    now = datetime.now(UTC)

    event = ExternalResourceChangeEvent(
        event_id="evt-unique-001",
        tenant_id=tenant_id,
        provider="google_calendar",
        external_resource_id="room-12",
        canonical_resource_id=res_id,
        start_at=now,
        end_at=now + timedelta(hours=2),
        reason="Room Maintenance",
    )

    # Mock DB query for affected assignments
    mock_assign = MagicMock(spec=ScheduleAssignmentRecord)
    mock_assign.activity_id = "act-101"
    mock_schedule = MagicMock(spec=ScheduleRecord)
    mock_schedule.id = sched_id
    mock_schedule.schedule_key = "spring-2026"

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_query_res = MagicMock()
    mock_query_res.all.return_value = [(mock_assign, mock_schedule)]
    mock_session.execute.return_value = mock_query_res

    handled = await handler.handle_resource_unavailable(mock_session, event)
    assert handled is True

    # Check blackout record was added to session
    added_objects = [c[0][0] for c in mock_session.add.call_args_list]
    blackouts = [o for o in added_objects if isinstance(o, ResourceAvailabilityRecord)]
    assert len(blackouts) == 1
    assert blackouts[0].interval_type == "UNAVAILABLE"
    assert blackouts[0].resource_id == res_id

    # Check repair was enqueued
    depth = queue.get_queue_depth(tenant_id)
    assert depth == 1
    jobs = queue.list_jobs(tenant_id)
    assert len(jobs) == 1
    assert jobs[0].affected_activity_ids == ["act-101"]
    assert jobs[0].priority == 8

    # Replay protection: re-sending the same event returns False and does not re-add
    replayed = await handler.handle_resource_unavailable(mock_session, event)
    assert replayed is False
    assert queue.get_queue_depth(tenant_id) == 1  # Still 1, not 2
