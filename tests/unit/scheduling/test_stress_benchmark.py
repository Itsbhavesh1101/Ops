"""Phase-4 Synthetic Engineering Stress Benchmark.

Clearly labelled: SYNTHETIC_ENGINEERING_STRESS_BENCHMARK
Notice: This is an engineering regression benchmark, NOT a Phase-0 frozen fixture.
Evaluates the primary CP-SAT solver against a scaled scheduling instance with multiple
constraint families, precedence chains, capacity, and soft objectives.
"""

import logging
from datetime import UTC, datetime, timedelta

from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import (
    PrecedenceConstraint,
    SchedulingConstraint,
    TimeWindow,
)
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

logger = logging.getLogger(__name__)


def test_synthetic_engineering_stress_benchmark() -> None:
    """Execute synthetic engineering stress benchmark and record key metrics."""
    base_time = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
    horizon_start = base_time
    horizon_end = base_time + timedelta(days=5)

    # 1. Create 12 resources across 3 types
    resources: list[SchedulingResourceSnapshot] = []
    skills_pool = ["ELECTRICAL", "MECHANICAL", "INSPECTION", "SAFETY"]
    for i in range(1, 9):
        # Technicians
        skill = skills_pool[(i - 1) % len(skills_pool)]
        resources.append(
            SchedulingResourceSnapshot(
                id=f"stress-res-tech-{i:03d}",
                type=ResourceType.PERSON,
                display_label=f"Stress Technician {i}",
                capacity=1,
                skills=[skill, "GENERAL"],
                unavailability_windows=[
                    # Night blackouts: 18:00 to 08:00 next day
                    TimeWindow(
                        start_at=base_time + timedelta(days=d, hours=10),
                        end_at=base_time + timedelta(days=d + 1),
                    )
                    for d in range(5)
                ],
            )
        )
    for i in range(1, 5):
        # Workbenches / Rooms
        resources.append(
            SchedulingResourceSnapshot(
                id=f"stress-res-room-{i:03d}",
                type=ResourceType.ROOM,
                display_label=f"Stress Workbench {i}",
                capacity=2,
                skills=["EQUIPMENT_ZONE"],
            )
        )

    # 2. Create 40 activities across the 5 days (8 per day)
    activities: list[SchedulingActivity] = []
    for i in range(1, 41):
        target_day = (i - 1) // 8
        day_start = base_time + timedelta(days=target_day, hours=1)
        day_end = base_time + timedelta(days=target_day, hours=9)
        needed_skill = skills_pool[(i - 1) % len(skills_pool)]

        activities.append(
            SchedulingActivity(
                id=f"stress-act-{i:03d}",
                title=f"Stress Activity {i:03d}",
                duration_minutes=45,
                priority="HIGH" if i % 4 == 0 else "NORMAL",
                allowed_windows=[
                    TimeWindow(start_at=day_start, end_at=day_end),
                ],
                required_skills=[needed_skill],
                preferred_resource_ids=[f"stress-res-tech-{(i % 8) + 1:03d}"],
            )
        )

    # 3. Add explicit precedence constraints (5 chains of 2 activities)
    constraints: list[SchedulingConstraint] = []
    for c in range(1, 9):
        pred_id = f"stress-act-{c * 2 - 1:03d}"
        succ_id = f"stress-act-{c * 2:03d}"
        constraints.append(
            PrecedenceConstraint(
                id=f"prec-stress-{c:03d}",
                name=f"Precedence {pred_id} -> {succ_id}",
                predecessor_id=pred_id,
                successor_id=succ_id,
                min_gap_minutes=15,
            )
        )

    # 4. Configure solver profile
    profile = SolverProfile(
        profile_key="stress_benchmark_profile",
        version=1,
        name="Synthetic Engineering Stress Benchmark Profile",
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        time_granularity_minutes=15,
        max_solve_seconds=15,
        num_search_workers=4,
        random_seed=42,
        soft_weights=SoftObjectiveWeights(
            preference_weight=3,
            idle_gap_weight=1,
            fairness_weight=1,
            utilization_weight=1,
            movement_weight=5,
        ),
    )

    request = SchedulingRequest(
        tenant_id="tenant-stress-benchmark",
        schedule_key="SYNTHETIC_ENGINEERING_STRESS_BENCHMARK",
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        timezone="UTC",
        activities=activities,
        resources=resources,
        constraints=constraints,
        profile=profile,
    )

    # 5. Execute Solve and measure
    scheduler = CpSatScheduler()
    result = scheduler.solve(request)

    # Assertions
    assert result.status == ScheduleStatus.FEASIBLE
    assert len(result.assignments) == len(activities)
    assert result.score_components is not None
    assert result.score_components.hard_constraint_violations == 0

    # Record benchmark metrics
    metrics = {
        "benchmark_label": "SYNTHETIC_ENGINEERING_STRESS_BENCHMARK",
        "problem_size": {
            "num_activities": len(activities),
            "num_resources": len(resources),
            "horizon_days": 5,
        },
        "constraint_count": {
            "precedence_constraints": len(constraints),
            "total_activities": len(activities),
        },
        "solve_budget_seconds": profile.max_solve_seconds,
        "result_status": result.status.value,
        "wall_time_seconds": result.solver_metadata.get("wall_time_seconds"),
        "hard_violations": result.score_components.hard_constraint_violations,
        "objective_components": {
            "total_score": result.score_components.total_score,
            "preference_penalty": result.score_components.preference_penalty,
            "idle_gap_penalty": result.score_components.idle_gap_penalty,
            "fairness_penalty": result.score_components.fairness_penalty,
            "utilization_penalty": result.score_components.utilization_penalty,
            "movement_penalty": result.score_components.movement_penalty,
        },
    }

    # Print for test output verification
    print(f"\nSTRESS_BENCHMARK_METRICS: {metrics}")
