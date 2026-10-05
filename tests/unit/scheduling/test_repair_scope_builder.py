"""Unit tests for RepairScopeBuilder.

Covers:
- Directly impacted assignments identified by resource and interval overlap.
- Non-overlapping assignments on disrupted resource remain unaffected.
- Explicit baseline locks preserved in locked_baseline_activity_ids.
- Propagation across tight precedence chains.
- Partition completeness: affected | unaffected == all activities.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.db.models.scheduling import ScheduleAssignmentRecord
from app.scheduling.repair.models import ScheduleRepairRequest
from app.scheduling.repair.scope import RepairScopeBuilder
from app.scheduling.schemas import SchedulingActivity


def test_repair_scope_builder_direct_impact_and_unaffected() -> None:
    """Test direct impact detection via resource and overlapping time window."""
    base_time = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)

    activities = [
        SchedulingActivity(id="act-1", title="Task 1", duration_minutes=60),
        SchedulingActivity(id="act-2", title="Task 2", duration_minutes=60),
        SchedulingActivity(id="act-3", title="Task 3", duration_minutes=60),
    ]

    # Baseline assignments:
    # act-1: res-A, 09:00 - 10:00 (locked: false)
    # act-2: res-B, 10:00 - 11:00 (locked: true)
    # act-3: res-A, 14:00 - 15:00 (locked: false)
    sched_id = uuid4()
    assignments = [
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-1",
            resource_id="res-A",
            start_at=base_time,
            end_at=base_time + timedelta(hours=1),
            is_locked=False,
        ),
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-2",
            resource_id="res-B",
            start_at=base_time + timedelta(hours=1),
            end_at=base_time + timedelta(hours=2),
            is_locked=True,
        ),
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-3",
            resource_id="res-A",
            start_at=base_time + timedelta(hours=5),
            end_at=base_time + timedelta(hours=6),
            is_locked=False,
        ),
    ]

    # Disruption: res-A is unavailable from 08:30 to 11:00
    # Overlaps act-1 (09:00-10:00), but NOT act-3 (14:00-15:00)
    repair_req = ScheduleRepairRequest(
        disrupted_resource_ids=["res-A"],
        disrupted_interval_start=base_time - timedelta(minutes=30),
        disrupted_interval_end=base_time + timedelta(hours=2),
        reason="Technician illness",
        movement_weight=5,
    )

    builder = RepairScopeBuilder()
    scope = builder.build_scope(
        activities=activities,
        baseline_assignments=assignments,
        constraints=[],
        repair_request=repair_req,
    )

    # act-1 is directly impacted
    assert scope.directly_impacted_activity_ids == {"act-1"}
    assert scope.affected_activity_ids == {"act-1"}

    # act-2 and act-3 are unaffected!
    assert scope.unaffected_activity_ids == {"act-2", "act-3"}

    # act-2 was explicitly locked in baseline
    assert scope.locked_baseline_activity_ids == {"act-2"}


def test_repair_scope_builder_precedence_propagation() -> None:
    """Test that tight precedence connections propagate into the repair scope."""
    base_time = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)

    # act-2 depends on act-1 (act-1 is predecessor of act-2)
    activities = [
        SchedulingActivity(id="act-1", title="Task 1", duration_minutes=60),
        SchedulingActivity(
            id="act-2",
            title="Task 2",
            duration_minutes=60,
            predecessors=["act-1"],
        ),
        SchedulingActivity(id="act-3", title="Task 3", duration_minutes=60),
    ]

    sched_id = uuid4()
    assignments = [
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-1",
            resource_id="res-A",
            start_at=base_time,
            end_at=base_time + timedelta(hours=1),
            is_locked=False,
        ),
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-2",
            resource_id="res-B",
            start_at=base_time + timedelta(hours=1),
            end_at=base_time + timedelta(hours=2),
            is_locked=False,
        ),
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-3",
            resource_id="res-C",
            start_at=base_time + timedelta(hours=3),
            end_at=base_time + timedelta(hours=4),
            is_locked=False,
        ),
    ]

    # Explicitly disrupt act-2
    repair_req = ScheduleRepairRequest(
        affected_activity_ids=["act-2"],
        reason="Equipment breakdown on Task 2",
        movement_weight=10,
    )

    builder = RepairScopeBuilder()
    scope = builder.build_scope(
        activities=activities,
        baseline_assignments=assignments,
        constraints=[],
        repair_request=repair_req,
    )

    assert "act-2" in scope.directly_impacted_activity_ids
    # act-1 propagates because it tightly precedes act-2
    assert "act-1" in scope.propagated_activity_ids
    assert "act-3" in scope.unaffected_activity_ids
    # Check propagation reasons recorded
    meta = scope.to_metadata_dict()
    assert "act-1" in meta["propagation_reasons"]
    assert "PRECEDENCE" in meta["propagation_reasons"]["act-1"]


def test_repair_request_explicit_movement_policy_validation() -> None:
    """Verify that ScheduleRepairRequest rejects missing movement policy and accepts explicit."""
    import pytest
    from pydantic import ValidationError

    # Missing movement weight and profile fails validation
    with pytest.raises(ValidationError, match="explicit movement policy"):
        ScheduleRepairRequest(
            affected_activity_ids=["act-1"],
            reason="Test disruption",
        )

    # Explicit movement weight >= 1 succeeds
    req = ScheduleRepairRequest(
        affected_activity_ids=["act-1"],
        reason="Test disruption",
        movement_weight=3,
    )
    assert req.movement_weight == 3


def test_repair_scope_downstream_successor_and_shared_resource_coupling() -> None:
    """Test downstream successor propagation and shared resource overlap coupling."""
    base_time = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)

    # act-1 is predecessor of act-2; act-3 shares resource res-A with act-1 at overlapping window
    activities = [
        SchedulingActivity(id="act-1", title="Task 1", duration_minutes=60),
        SchedulingActivity(id="act-2", title="Task 2", duration_minutes=60, predecessors=["act-1"]),
        SchedulingActivity(id="act-3", title="Task 3", duration_minutes=60),
        SchedulingActivity(id="act-4", title="Task 4 (unrelated)", duration_minutes=60),
    ]

    sched_id = uuid4()
    assignments = [
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-1",
            resource_id="res-A",
            start_at=base_time,
            end_at=base_time + timedelta(hours=1),
            is_locked=False,
        ),
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-2",
            resource_id="res-B",
            start_at=base_time + timedelta(minutes=45),  # tight / overlapping start
            end_at=base_time + timedelta(hours=2),
            is_locked=False,
        ),
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-3",
            resource_id="res-A",
            start_at=base_time + timedelta(minutes=30),  # overlaps on same resource
            end_at=base_time + timedelta(hours=1, minutes=30),
            is_locked=False,
        ),
        ScheduleAssignmentRecord(
            id=uuid4(),
            tenant_id="tenant-1",
            schedule_id=sched_id,
            activity_id="act-4",
            resource_id="res-Z",
            start_at=base_time + timedelta(days=2),
            end_at=base_time + timedelta(days=2, hours=1),
            is_locked=False,
        ),
    ]

    repair_req = ScheduleRepairRequest(
        affected_activity_ids=["act-1"],
        reason="Disruption on act-1",
        movement_weight=5,
    )

    builder = RepairScopeBuilder()
    scope = builder.build_scope(
        activities=activities,
        baseline_assignments=assignments,
        constraints=[],
        repair_request=repair_req,
    )

    # act-1 directly impacted
    assert scope.directly_impacted_activity_ids == {"act-1"}
    # act-2 propagates as tight successor
    assert "act-2" in scope.propagated_activity_ids
    # act-3 propagates due to shared resource overlap
    assert "act-3" in scope.propagated_activity_ids
    # act-4 remains unaffected
    assert "act-4" in scope.unaffected_activity_ids
    assert scope.affected_activity_ids == {"act-1", "act-2", "act-3"}
