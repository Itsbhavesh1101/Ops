"""Unit tests executing OR-Tools CP-SAT against authoritative Phase-0 scheduling benchmark fixtures.

Covers:
- SC-MAINT-FEASIBLE-001: 6 work orders, full feasibility witness satisfaction.
- SC-MAINT-INFEASIBLE-001: Unsatisfiable capability combination (PLUMBING + NETWORK),
  explicit conflict diagnostics and relaxation suggestions.
"""

import json
from datetime import datetime
from pathlib import Path

from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import TimeWindow
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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
RESOURCES_FILE = FIXTURES_DIR / "resources" / "RS-SYNTH-001.json"
MAINT_FEASIBLE_FILE = FIXTURES_DIR / "scheduling" / "maintenance" / "SC-MAINT-FEASIBLE-001.json"
MAINT_INFEASIBLE_FILE = FIXTURES_DIR / "scheduling" / "maintenance" / "SC-MAINT-INFEASIBLE-001.json"


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build_resources_snapshot(resource_ids: set[str]) -> list[SchedulingResourceSnapshot]:
    data = _load_json(RESOURCES_FILE)
    snapshots: list[SchedulingResourceSnapshot] = []
    for r in data["resources"]:
        if r["id"] in resource_ids:
            snapshots.append(
                SchedulingResourceSnapshot(
                    id=r["id"],
                    type=ResourceType(r["type"]),
                    display_label=r["display_label"],
                    capacity=r.get("capacity", 1),
                    skills=r.get("skills", []),
                )
            )
    return snapshots


def test_phase_0_sc_maint_feasible_001() -> None:
    """Verify SC-MAINT-FEASIBLE-001 solves to FEASIBLE with zero hard violations."""
    fixture = _load_json(MAINT_FEASIBLE_FILE)

    # Collect needed resources from witness or all maintenance resources
    needed_resource_ids = {
        "res-tech-001",
        "res-tech-002",
        "res-tech-003",
        "res-tech-004",
        "res-eq-001",
        "res-eq-002",
        "res-eq-003",
    }
    resources = _build_resources_snapshot(needed_resource_ids)

    activities: list[SchedulingActivity] = []
    for wo in fixture["work_orders"]:
        all_skills = list(wo.get("required_person_skills", [])) + list(
            wo.get("required_equipment_skills", [])
        )
        windows = [
            TimeWindow(
                start_at=datetime.fromisoformat(w["start_at"]),
                end_at=datetime.fromisoformat(w["end_at"]),
            )
            for w in wo.get("allowed_windows", [])
        ]
        pred_ids = [
            p["work_order_id"] if isinstance(p, dict) else str(p)
            for p in wo.get("predecessors", [])
        ]
        activities.append(
            SchedulingActivity(
                id=wo["id"],
                title=wo["title"],
                duration_minutes=wo["duration_minutes"],
                priority=wo.get("priority", "NORMAL"),
                allowed_windows=windows,
                required_skills=all_skills,
                predecessors=pred_ids,
                preferred_resource_ids=wo.get("preferred_person_ids", []),
            )
        )

    t0 = datetime.fromisoformat(fixture["horizon"]["start_at"])
    t1 = datetime.fromisoformat(fixture["horizon"]["end_at"])

    profile = SolverProfile(
        profile_key="maint-profile",
        name="Maintenance Optimization Profile",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=2,
            idle_gap_weight=1,
            fairness_weight=0,
            utilization_weight=1,
            movement_weight=0,
        ),
    )

    req = SchedulingRequest(
        tenant_id=fixture["tenant_id"],
        schedule_key="sched-maint-001",
        horizon_start=t0,
        horizon_end=t1,
        timezone=fixture["timezone"],
        activities=activities,
        resources=resources,
        profile=profile,
    )

    scheduler = CpSatScheduler()
    result = scheduler.solve(req)

    assert result.status == ScheduleStatus.FEASIBLE
    assert result.score_components.hard_constraint_violations == 0
    # 6 work orders scheduled, each with technician and where needed equipment
    assert len(result.assignments) >= 6

    # Verify no double booking across identical resources
    by_resource: dict[str, list] = {}
    for asgn in result.assignments:
        by_resource.setdefault(asgn.resource_id, []).append(asgn)

    for rid, asgns in by_resource.items():
        sorted_asgns = sorted(asgns, key=lambda a: a.start_at)
        for i in range(len(sorted_asgns) - 1):
            assert sorted_asgns[i].end_at <= sorted_asgns[i + 1].start_at, (
                f"Overlap detected on resource {rid}: {sorted_asgns[i]} and {sorted_asgns[i + 1]}"
            )


def test_phase_0_sc_maint_infeasible_001() -> None:
    """Verify SC-MAINT-INFEASIBLE-001 returns INFEASIBLE and identifies the skill conflict."""
    fixture = _load_json(MAINT_INFEASIBLE_FILE)

    needed_resource_ids = {
        "res-tech-001",
        "res-tech-002",
        "res-tech-003",
        "res-tech-004",
    }
    resources = _build_resources_snapshot(needed_resource_ids)

    wo = fixture["work_orders"][0]
    windows = [
        TimeWindow(
            start_at=datetime.fromisoformat(w["start_at"]),
            end_at=datetime.fromisoformat(w["end_at"]),
        )
        for w in wo["allowed_windows"]
    ]
    activities = [
        SchedulingActivity(
            id=wo["id"],
            title=wo["title"],
            duration_minutes=wo["duration_minutes"],
            allowed_windows=windows,
            required_skills=wo["required_person_skills"],  # ["PLUMBING", "NETWORK"]
        )
    ]

    t0 = datetime.fromisoformat(fixture["horizon"]["start_at"])
    t1 = datetime.fromisoformat(fixture["horizon"]["end_at"])

    profile = SolverProfile(
        profile_key="maint-inf-profile",
        name="Maintenance Profile",
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

    req = SchedulingRequest(
        tenant_id=fixture["tenant_id"],
        schedule_key="sched-maint-inf-001",
        horizon_start=t0,
        horizon_end=t1,
        timezone=fixture["timezone"],
        activities=activities,
        resources=resources,
        profile=profile,
    )

    scheduler = CpSatScheduler()
    result = scheduler.solve(req)

    assert result.status == ScheduleStatus.INFEASIBLE
    assert len(result.assignments) == 0
    assert len(result.diagnostics) > 0
    diag = result.diagnostics[0]
    assert diag.root_cause_type == "PERSON_CAPABILITY_COMBINATION_UNSATISFIABLE"
    assert "PLUMBING" in diag.human_readable_explanation
    assert len(diag.relaxation_candidates) >= 1
