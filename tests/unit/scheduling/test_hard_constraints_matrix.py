"""Unit tests proving positive and negative cases for all 7 canonical hard constraints.

Matrix:
1. NO_OVERLAP (positive & negative)
2. CAPACITY (positive & negative)
3. ELIGIBILITY (positive & negative)
4. PRECEDENCE (positive & negative)
5. TIME_WINDOW (positive & negative)
6. MINIMUM_GAP (positive & negative)
7. LOCK (positive & negative)
"""

from datetime import UTC, datetime, timedelta

from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import (
    MinimumGapConstraint,
    TimeWindow,
)
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import (
    LockedAssignment,
    ScheduleStatus,
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.solvers.cpsat import CpSatScheduler


def _profile(t0: datetime, t1: datetime) -> SolverProfile:
    return SolverProfile(
        profile_key="test-mat",
        name="Matrix Profile",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=5.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )


# 1. NO_OVERLAP
def test_matrix_no_overlap_positive() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="no-ov-pos",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(id="a1", title="A1", duration_minutes=60, required_skills=["S"]),
            SchedulingActivity(id="a2", title="A2", duration_minutes=60, required_skills=["S"]),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=1, skills=["S"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.FEASIBLE
    assert len(res.assignments) == 2
    asgn1 = next(a for a in res.assignments if a.activity_id == "a1")
    asgn2 = next(a for a in res.assignments if a.activity_id == "a2")
    assert asgn1.end_at <= asgn2.start_at or asgn2.end_at <= asgn1.start_at


def test_matrix_no_overlap_negative() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)  # Only 1 hour available
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="no-ov-neg",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            # Two 45 min activities cannot fit sequentially in 60 mins
            SchedulingActivity(id="a1", title="A1", duration_minutes=45, required_skills=["S"]),
            SchedulingActivity(id="a2", title="A2", duration_minutes=45, required_skills=["S"]),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=1, skills=["S"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.INFEASIBLE


# 2. CAPACITY
def test_matrix_capacity_positive() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="cap-pos",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(id="a1", title="A1", duration_minutes=60, required_skills=["SEAT"]),
            SchedulingActivity(id="a2", title="A2", duration_minutes=60, required_skills=["SEAT"]),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="room", type=ResourceType.ROOM, display_label="Room", capacity=2, skills=["SEAT"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.FEASIBLE


def test_matrix_capacity_negative() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="cap-neg",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(id="a1", title="A1", duration_minutes=60, required_skills=["SEAT"]),
            SchedulingActivity(id="a2", title="A2", duration_minutes=60, required_skills=["SEAT"]),
            SchedulingActivity(id="a3", title="A3", duration_minutes=60, required_skills=["SEAT"]),
        ],
        resources=[
            # Only capacity 2, but 3 concurrent activities needed in 1 hour
            SchedulingResourceSnapshot(
                id="room", type=ResourceType.ROOM, display_label="Room", capacity=2, skills=["SEAT"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.INFEASIBLE


# 3. ELIGIBILITY
def test_matrix_eligibility_positive() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=2)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="elig-pos",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(id="a1", title="A1", duration_minutes=60, required_skills=["HVAC"])
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="Tech", capacity=1, skills=["HVAC"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.FEASIBLE
    assert res.assignments[0].resource_id == "r1"


def test_matrix_eligibility_negative() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=2)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="elig-neg",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            # Missing skill
            SchedulingActivity(
                id="a1", title="A1", duration_minutes=60, required_skills=["WELDING"]
            )
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="Tech", capacity=1, skills=["HVAC"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.INFEASIBLE
    assert res.diagnostics[0].root_cause_type == "PERSON_CAPABILITY_COMBINATION_UNSATISFIABLE"


# 4. PRECEDENCE
def test_matrix_precedence_positive() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="prec-pos",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(id="a1", title="A1", duration_minutes=60, required_skills=["S"]),
            SchedulingActivity(
                id="a2", title="A2", duration_minutes=60, required_skills=["S"], predecessors=["a1"]
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=1, skills=["S"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.FEASIBLE
    a1 = next(a for a in res.assignments if a.activity_id == "a1")
    a2 = next(a for a in res.assignments if a.activity_id == "a2")
    assert a1.end_at <= a2.start_at


def test_matrix_precedence_negative() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=2)
    # A1 precedes A2, but A2 is locked to start before A1
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="prec-neg",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(id="a1", title="A1", duration_minutes=60, required_skills=["S"]),
            SchedulingActivity(
                id="a2",
                title="A2",
                duration_minutes=60,
                required_skills=["S"],
                predecessors=["a1"],
                locked_assignment=LockedAssignment(start_at=t0, end_at=t0 + timedelta(hours=1)),
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=2, skills=["S"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.INFEASIBLE


# 5. TIME_WINDOW
def test_matrix_time_window_positive() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=6)
    win_start = t0 + timedelta(hours=2)
    win_end = t0 + timedelta(hours=4)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="win-pos",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="a1",
                title="A1",
                duration_minutes=60,
                required_skills=["S"],
                allowed_windows=[TimeWindow(start_at=win_start, end_at=win_end)],
            )
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=1, skills=["S"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.FEASIBLE
    assert res.assignments[0].start_at >= win_start
    assert res.assignments[0].end_at <= win_end


def test_matrix_time_window_negative() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=6)
    win_start = t0 + timedelta(hours=2)
    win_end = t0 + timedelta(hours=2, minutes=30)  # 30-min window for 60-min task
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="win-neg",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="a1",
                title="A1",
                duration_minutes=60,
                required_skills=["S"],
                allowed_windows=[TimeWindow(start_at=win_start, end_at=win_end)],
            )
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=1, skills=["S"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.INFEASIBLE


# 6. MINIMUM_GAP
def test_matrix_minimum_gap_positive() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=6)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="gap-pos",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(id="a1", title="A1", duration_minutes=60, required_skills=["S"]),
            SchedulingActivity(id="a2", title="A2", duration_minutes=60, required_skills=["S"]),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=2, skills=["S"]
            )
        ],
        constraints=[
            MinimumGapConstraint(name="Gap", activity_ids=["a1", "a2"], min_gap_minutes=60)
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.FEASIBLE
    a1 = next(a for a in res.assignments if a.activity_id == "a1")
    a2 = next(a for a in res.assignments if a.activity_id == "a2")
    gap = (
        abs((a2.start_at - a1.end_at).total_seconds())
        if a2.start_at >= a1.end_at
        else abs((a1.start_at - a2.end_at).total_seconds())
    )
    assert gap >= 3600  # >= 60 minutes


def test_matrix_minimum_gap_negative() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=90)  # Only 90 minutes total
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="gap-neg",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            # 30m + 60m gap + 30m = 120m > 90m
            SchedulingActivity(id="a1", title="A1", duration_minutes=30, required_skills=["S"]),
            SchedulingActivity(id="a2", title="A2", duration_minutes=30, required_skills=["S"]),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=2, skills=["S"]
            )
        ],
        constraints=[
            MinimumGapConstraint(name="Gap", activity_ids=["a1", "a2"], min_gap_minutes=60)
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.INFEASIBLE


# 7. LOCK
def test_matrix_lock_positive() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)
    lock_start = t0 + timedelta(hours=1)
    lock_end = t0 + timedelta(hours=2)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="lock-pos",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="a1",
                title="A1",
                duration_minutes=60,
                required_skills=["S"],
                locked_assignment=LockedAssignment(
                    start_at=lock_start, end_at=lock_end, resource_ids=["r1"]
                ),
            )
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="r1", type=ResourceType.PERSON, display_label="R1", capacity=1, skills=["S"]
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.FEASIBLE
    assert res.assignments[0].start_at == lock_start
    assert res.assignments[0].end_at == lock_end
    assert res.assignments[0].resource_id == "r1"
    assert res.assignments[0].is_locked is True


def test_matrix_lock_negative() -> None:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)
    lock_start = t0 + timedelta(hours=1)
    lock_end = t0 + timedelta(hours=2)
    req = SchedulingRequest(
        tenant_id="t1",
        schedule_key="lock-neg",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="a1",
                title="A1",
                duration_minutes=60,
                required_skills=["S"],
                # Locked to r1 at 10:00-11:00
                locked_assignment=LockedAssignment(
                    start_at=lock_start, end_at=lock_end, resource_ids=["r1"]
                ),
            )
        ],
        resources=[
            # But r1 has blackout at 10:00-11:00!
            SchedulingResourceSnapshot(
                id="r1",
                type=ResourceType.PERSON,
                display_label="R1",
                capacity=1,
                skills=["S"],
                unavailability_windows=[TimeWindow(start_at=lock_start, end_at=lock_end)],
            )
        ],
        profile=_profile(t0, t1),
    )
    res = CpSatScheduler().solve(req)
    assert res.status == ScheduleStatus.INFEASIBLE
