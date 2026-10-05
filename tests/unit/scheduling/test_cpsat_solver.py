"""Unit tests for Phase 4A OR-Tools CP-SAT scheduler.

Verifies:
- Feasible schedule generation with zero hard constraint violations.
- Infeasible detection returning status INFEASIBLE (not FAILED) and conflict diagnostics.
- No-overlap enforcement.
- Precedence finish-to-start ordering.
- Soft preference optimization.
- Deterministic reproducibility under controlled seed and single-threaded worker.
"""

from datetime import UTC, datetime, timedelta

from app.resources.schemas import ResourceType
from app.scheduling.profiles.models import (
    SoftObjectiveWeights,
    SolverProfile,
)
from app.scheduling.schemas import (
    ScheduleStatus,
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.solvers.cpsat import CpSatScheduler


def _create_base_request() -> SchedulingRequest:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=8)
    return SchedulingRequest(
        tenant_id="tenant-cpsat-test",
        schedule_key="test-solve-001",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="act-a",
                title="Task Alpha",
                duration_minutes=60,
                required_skills=["ELECTRICAL"],
            ),
            SchedulingActivity(
                id="act-b",
                title="Task Beta",
                duration_minutes=60,
                required_skills=["ELECTRICAL"],
                predecessors=["act-a"],
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="res-tech-1",
                type=ResourceType.PERSON,
                display_label="Technician One",
                capacity=1,
                skills=["ELECTRICAL"],
            )
        ],
        profile=SolverProfile(
            profile_key="test-profile",
            name="Test Profile",
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
        ),
    )


def test_cpsat_solver_feasible_case() -> None:
    """Verify solver finds feasible solution with precedence and no-overlap satisfied."""
    req = _create_base_request()
    scheduler = CpSatScheduler()
    result = scheduler.solve(req)

    assert result.status == ScheduleStatus.FEASIBLE
    assert result.score_components.hard_constraint_violations == 0
    assert len(result.assignments) == 2

    # Verify precedence: act-a finishes before act-b starts
    act_a_asgn = next(a for a in result.assignments if a.activity_id == "act-a")
    act_b_asgn = next(a for a in result.assignments if a.activity_id == "act-b")
    assert act_a_asgn.end_at <= act_b_asgn.start_at
    assert act_a_asgn.resource_id == "res-tech-1"
    assert act_b_asgn.resource_id == "res-tech-1"


def test_cpsat_solver_infeasible_conflict_case() -> None:
    """Verify impossible problem returns INFEASIBLE (not FAILED) with diagnostics."""
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=2)  # Tight 2-hour horizon

    # Two 90-minute tasks on single resource in 2-hour window -> impossible
    req = SchedulingRequest(
        tenant_id="tenant-cpsat-test",
        schedule_key="test-inf-001",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="act-long-1",
                title="Long Task 1",
                duration_minutes=90,
                required_skills=["PLUMBING"],
            ),
            SchedulingActivity(
                id="act-long-2",
                title="Long Task 2",
                duration_minutes=90,
                required_skills=["PLUMBING"],
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="res-plum-1",
                type=ResourceType.PERSON,
                display_label="Plumber",
                capacity=1,
                skills=["PLUMBING"],
            )
        ],
        profile=SolverProfile(
            profile_key="test-profile",
            name="Test Profile",
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
        ),
    )

    scheduler = CpSatScheduler()
    result = scheduler.solve(req)

    assert result.status == ScheduleStatus.INFEASIBLE
    assert len(result.assignments) == 0
    assert len(result.diagnostics) > 0
    diag = result.diagnostics[0]
    assert len(diag.human_readable_explanation) > 0
    assert len(diag.relaxation_candidates) > 0


def test_cpsat_soft_preference_optimization() -> None:
    """Verify solver favors preferred resource when soft preference objective is weighted."""
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)

    req = SchedulingRequest(
        tenant_id="tenant-cpsat-test",
        schedule_key="test-pref-001",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="act-pref",
                title="Preferred Task",
                duration_minutes=60,
                required_skills=["ELECTRICAL"],
                preferred_resource_ids=["res-tech-preferred"],
            )
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="res-tech-other",
                type=ResourceType.PERSON,
                display_label="Tech Other",
                capacity=1,
                skills=["ELECTRICAL"],
            ),
            SchedulingResourceSnapshot(
                id="res-tech-preferred",
                type=ResourceType.PERSON,
                display_label="Tech Favored",
                capacity=1,
                skills=["ELECTRICAL"],
            ),
        ],
        profile=SolverProfile(
            profile_key="test-profile",
            name="Test Profile",
            horizon_start=t0,
            horizon_end=t1,
            time_granularity_minutes=15,
            max_solve_seconds=5.0,
            random_seed=42,
            num_search_workers=1,
            soft_weights=SoftObjectiveWeights(
                preference_weight=5,
                idle_gap_weight=0,
                fairness_weight=0,
                utilization_weight=0,
                movement_weight=0,
            ),
        ),
    )

    scheduler = CpSatScheduler()
    result = scheduler.solve(req)

    assert result.status == ScheduleStatus.FEASIBLE
    assert len(result.assignments) == 1
    assert result.assignments[0].resource_id == "res-tech-preferred"
    assert result.score_components.preference_penalty == 0


def test_cpsat_reproducibility() -> None:
    """Verify same input, seed, and single worker produce identical assignments."""
    req = _create_base_request()
    scheduler = CpSatScheduler()

    res1 = scheduler.solve(req)
    res2 = scheduler.solve(req)

    assert res1.status == res2.status == ScheduleStatus.FEASIBLE
    assert len(res1.assignments) == len(res2.assignments)
    for a1, a2 in zip(res1.assignments, res2.assignments, strict=True):
        assert a1.activity_id == a2.activity_id
        assert a1.resource_id == a2.resource_id
        assert a1.start_at == a2.start_at
        assert a1.end_at == a2.end_at


def test_cpsat_budget_exhausted_case() -> None:
    """Verify that when solve budget is exhausted with no solution, status is BUDGET_EXHAUSTED."""
    from unittest.mock import patch

    from ortools.sat.python import cp_model

    req = _create_base_request()
    scheduler = CpSatScheduler()

    # Mock CpSolver.solve to return UNKNOWN (budget exhausted)
    with patch("ortools.sat.python.cp_model.CpSolver.solve", return_value=cp_model.UNKNOWN):
        result = scheduler.solve(req)

    assert result.status == ScheduleStatus.BUDGET_EXHAUSTED
    assert len(result.assignments) == 0
    assert len(result.diagnostics) > 0
    diag = result.diagnostics[0]
    assert diag.root_cause_type == "BUDGET_EXHAUSTED"
    assert "budget" in diag.human_readable_explanation.lower()
