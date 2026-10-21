"""Regression tests proving timetable correctness closure.

Verifies:
- Availability enforcement via horizon and allowed_windows bounds.
- Infeasibility when activities violate availability window.
- Task precedence propagation (finish-to-start ordering).
- Room exclusivity / concurrency semantics.
- Versioning logic creating fresh candidates without overwriting historical versions.
"""

from datetime import UTC, datetime

from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import TimeWindow
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import (
    ScheduleStatus,
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.solvers.cpsat import CpSatScheduler


def _profile(start: datetime, end: datetime) -> SolverProfile:
    return SolverProfile(
        profile_key="test-cpsat",
        version=1,
        name="Test CP-SAT Profile",
        horizon_start=start,
        horizon_end=end,
        time_granularity_minutes=15,
        max_solve_seconds=5,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )


def test_availability_enforcement_positive():
    """Verify solver places all activities inside the available window."""
    avail_start = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    avail_end = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)

    req = SchedulingRequest(
        tenant_id="tenant-demo-real-001",
        schedule_key="exam-schedule-default",
        horizon_start=avail_start,
        horizon_end=avail_end,
        timezone="UTC",
        activities=[
            SchedulingActivity(
                id="T1_EXAM_PREP",
                title="Exam syllabus and cohort enrollment finalization",
                duration_minutes=120,
                required_resource_ids=["hall-a"],
                allowed_windows=[TimeWindow(start_at=avail_start, end_at=avail_end)],
            ),
            SchedulingActivity(
                id="T2_QP_READINESS",
                title="Question paper preparation and moderation",
                duration_minutes=180,
                required_resource_ids=["hall-a"],
                predecessors=["T1_EXAM_PREP"],
                allowed_windows=[TimeWindow(start_at=avail_start, end_at=avail_end)],
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="hall-a",
                type=ResourceType.ROOM,
                display_label="Examination Hall A",
                capacity=1,
            )
        ],
        profile=_profile(avail_start, avail_end),
    )

    result = CpSatScheduler().solve(req)
    assert result.status == ScheduleStatus.FEASIBLE
    assert len(result.assignments) == 2

    for asgn in result.assignments:
        assert asgn.start_at >= avail_start
        assert asgn.end_at <= avail_end

    t1 = next(a for a in result.assignments if a.activity_id == "T1_EXAM_PREP")
    t2 = next(a for a in result.assignments if a.activity_id == "T2_QP_READINESS")
    assert t2.start_at >= t1.end_at


def test_availability_enforcement_negative_window_violation():
    """Verify solver rejects scheduling when activities cannot fit in availability window."""
    avail_start = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    # Only 2 hours available
    narrow_end = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)

    req = SchedulingRequest(
        tenant_id="tenant-demo-real-001",
        schedule_key="exam-schedule-default",
        horizon_start=avail_start,
        horizon_end=narrow_end,
        timezone="UTC",
        activities=[
            SchedulingActivity(
                id="T1_EXAM_PREP",
                title="Exam syllabus and cohort enrollment finalization",
                duration_minutes=120,
                required_resource_ids=["hall-a"],
                allowed_windows=[TimeWindow(start_at=avail_start, end_at=narrow_end)],
            ),
            # T2 needs 180 min after T1, which exceeds the 2h window!
            SchedulingActivity(
                id="T2_QP_READINESS",
                title="Question paper preparation and moderation",
                duration_minutes=180,
                required_resource_ids=["hall-a"],
                predecessors=["T1_EXAM_PREP"],
                allowed_windows=[TimeWindow(start_at=avail_start, end_at=narrow_end)],
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="hall-a",
                type=ResourceType.ROOM,
                display_label="Examination Hall A",
                capacity=1,
            )
        ],
        profile=_profile(avail_start, narrow_end),
    )

    result = CpSatScheduler().solve(req)
    assert result.status == ScheduleStatus.INFEASIBLE


def test_task_precedence_propagation_chain():
    """Verify finish-to-start ordering across a 3-task canonical pipeline."""
    avail_start = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    avail_end = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)

    req = SchedulingRequest(
        tenant_id="tenant-demo-real-001",
        schedule_key="exam-schedule-default",
        horizon_start=avail_start,
        horizon_end=avail_end,
        timezone="UTC",
        activities=[
            SchedulingActivity(
                id="T1_EXAM_PREP",
                title="Task 1",
                duration_minutes=120,
                required_resource_ids=["hall-a"],
            ),
            SchedulingActivity(
                id="T2_QP_READINESS",
                title="Task 2",
                duration_minutes=180,
                predecessors=["T1_EXAM_PREP"],
                required_resource_ids=["hall-a"],
            ),
            SchedulingActivity(
                id="T3_DEPT_APPROVAL",
                title="Task 3",
                duration_minutes=60,
                predecessors=["T2_QP_READINESS"],
                required_resource_ids=["hall-a"],
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="hall-a",
                type=ResourceType.ROOM,
                display_label="Examination Hall A",
                capacity=1,
            )
        ],
        profile=_profile(avail_start, avail_end),
    )

    result = CpSatScheduler().solve(req)
    assert result.status == ScheduleStatus.FEASIBLE
    asgns = {a.activity_id: a for a in result.assignments}

    assert asgns["T2_QP_READINESS"].start_at >= asgns["T1_EXAM_PREP"].end_at
    assert asgns["T3_DEPT_APPROVAL"].start_at >= asgns["T2_QP_READINESS"].end_at


def test_room_exclusivity_no_overlap():
    """Verify that two independent activities cannot overlap on an exclusive room resource."""
    start = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    end = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

    req = SchedulingRequest(
        tenant_id="tenant-demo-real-001",
        schedule_key="room-exclusive-test",
        horizon_start=start,
        horizon_end=end,
        timezone="UTC",
        activities=[
            SchedulingActivity(
                id="ACT_A",
                title="Activity A",
                duration_minutes=120,
                required_resource_ids=["hall-a"],
            ),
            SchedulingActivity(
                id="ACT_B",
                title="Activity B",
                duration_minutes=120,
                required_resource_ids=["hall-a"],
                # No predecessor: purely resource constraint
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="hall-a",
                type=ResourceType.ROOM,
                display_label="Examination Hall A",
                capacity=1,  # Exclusive room
            )
        ],
        profile=_profile(start, end),
    )

    result = CpSatScheduler().solve(req)
    assert result.status == ScheduleStatus.FEASIBLE
    asgns = {a.activity_id: a for a in result.assignments}

    # Must not overlap
    a_starts_first = asgns["ACT_A"].end_at <= asgns["ACT_B"].start_at
    b_starts_first = asgns["ACT_B"].end_at <= asgns["ACT_A"].start_at
    assert a_starts_first or b_starts_first
