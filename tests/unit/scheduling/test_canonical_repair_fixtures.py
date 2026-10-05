"""Verification of Phase-0 authoritative repair fixtures and infeasible repair cases.

Covers:
- SC-MAINT-REPAIR-001: 1 technician unavailability disruption, 1 affected assignment,
  5 unaffected assignments preserved without movement, 0 hard violations.
- SC-EXAM-REPAIR-001: 1 invigilator unavailability disruption, 1 affected assignment,
  9 unaffected assignments preserved without movement, 0 hard violations.
- INFEASIBLE repair test: Disruption conflicting with an explicit hard lock produces INFEASIBLE
  with diagnostics without relaxing the lock or mutating the published baseline.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.db.models.scheduling import ScheduleAssignmentRecord
from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import TimeWindow
from app.scheduling.profiles.models import (
    SoftObjectiveWeights,
    SolverProfile,
)
from app.scheduling.repair.models import ScheduleRepairRequest
from app.scheduling.repair.scope import RepairScopeBuilder
from app.scheduling.schemas import (
    LockedAssignment,
    ScheduleStatus,
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.solvers.cpsat import CpSatScheduler

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
RESOURCES_FILE = FIXTURES_DIR / "resources" / "RS-SYNTH-001.json"
MAINT_REPAIR_FILE = (
    FIXTURES_DIR / "scheduling" / "maintenance" / "SC-MAINT-REPAIR-001.json"
)
EXAM_REPAIR_FILE = FIXTURES_DIR / "scheduling" / "exam" / "SC-EXAM-REPAIR-001.json"


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


def test_canonical_fixture_sc_maint_repair_001() -> None:
    """Verify SC-MAINT-REPAIR-001 targeted repair behavior."""
    fixture = _load_json(MAINT_REPAIR_FILE)

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

    # 1. Build activities from work orders
    activities: list[SchedulingActivity] = []
    for wo in fixture["work_orders"]:
        person_skills = list(wo.get("required_person_skills", []))
        windows = [
            TimeWindow(
                start_at=datetime.fromisoformat(w["start_at"]),
                end_at=datetime.fromisoformat(w["end_at"]),
            )
            for w in wo.get("allowed_windows", [])
        ]
        activities.append(
            SchedulingActivity(
                id=wo["id"],
                title=wo["title"],
                duration_minutes=wo["duration_minutes"],
                priority=wo.get("priority", "NORMAL"),
                allowed_windows=windows,
                required_skills=person_skills,
                preferred_resource_ids=wo.get("preferred_person_ids", []),
            )
        )

    # 2. Reconstruct published baseline assignments
    baseline_assignments: list[ScheduleAssignmentRecord] = []
    sched_id = uuid4()
    for item in fixture["published_baseline"]:
        baseline_assignments.append(
            ScheduleAssignmentRecord(
                id=uuid4(),
                tenant_id=fixture["tenant_id"],
                schedule_id=sched_id,
                activity_id=item["work_order_id"],
                resource_id=item["person_resource_id"],
                start_at=datetime.fromisoformat(item["start_at"]),
                end_at=datetime.fromisoformat(item["end_at"]),
                is_locked=item.get("locked", False),
            )
        )

    # 3. Disruption
    disruption = fixture["disruptions"][0]
    repair_req = ScheduleRepairRequest(
        disrupted_resource_ids=[disruption["resource_id"]],
        disrupted_interval_start=datetime.fromisoformat(disruption["start_at"]),
        disrupted_interval_end=datetime.fromisoformat(disruption["end_at"]),
        reason=disruption["reason"],
        movement_weight=10,
    )

    # 4. Scope Builder verification
    builder = RepairScopeBuilder()
    scope = builder.build_scope(
        activities=activities,
        baseline_assignments=baseline_assignments,
        constraints=[],
        repair_request=repair_req,
    )

    # Expectation: exactly wo-maint-005 is affected
    assert scope.affected_activity_ids == {"wo-maint-005"}
    assert scope.unaffected_activity_ids == {
        "wo-maint-001",
        "wo-maint-002",
        "wo-maint-003",
        "wo-maint-004",
        "wo-maint-006",
    }

    # 5. Build solve request with frozen unaffected assignments
    asgn_map = {a.activity_id: a for a in baseline_assignments}
    updated_activities = []
    for act in activities:
        base_asgn = asgn_map[act.id]
        if act.id in scope.unaffected_activity_ids:
            # FREEZE
            updated_activities.append(
                act.model_copy(
                    update={
                        "locked_assignment": LockedAssignment(
                            start_at=base_asgn.start_at,
                            end_at=base_asgn.end_at,
                            resource_ids=[base_asgn.resource_id],
                        ),
                        "allowed_windows": [
                            TimeWindow(
                                start_at=base_asgn.start_at, end_at=base_asgn.end_at
                            )
                        ],
                        "required_resource_ids": [base_asgn.resource_id],
                    }
                )
            )
        else:
            # Movable affected assignment
            updated_activities.append(
                act.model_copy(
                    update={
                        "locked_assignment": None,
                        "baseline_assignment": LockedAssignment(
                            resource_ids=[base_asgn.resource_id]
                        ),
                    }
                )
            )

    # Update resource with blackout
    updated_resources = []
    for r in resources:
        unavail = list(r.unavailability_windows)
        if r.id == disruption["resource_id"]:
            unavail.append(
                TimeWindow(
                    start_at=datetime.fromisoformat(disruption["start_at"]),
                    end_at=datetime.fromisoformat(disruption["end_at"]),
                )
            )
        updated_resources.append(
            r.model_copy(update={"unavailability_windows": unavail})
        )

    h_start = datetime.fromisoformat(fixture["horizon"]["start_at"])
    h_end = datetime.fromisoformat(fixture["horizon"]["end_at"])

    profile = SolverProfile(
        profile_key="repair_profile",
        version=1,
        name="Repair Profile",
        horizon_start=h_start,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=10,
        num_search_workers=4,
        random_seed=42,
        soft_weights=SoftObjectiveWeights(
            preference_weight=5,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=1,
            movement_weight=10,
        ),
    )

    request = SchedulingRequest(
        tenant_id=fixture["tenant_id"],
        schedule_key="maint_repair",
        horizon_start=h_start,
        horizon_end=h_end,
        timezone=fixture["timezone"],
        activities=updated_activities,
        resources=updated_resources,
        constraints=[],
        profile=profile,
    )

    scheduler = CpSatScheduler()
    result = scheduler.solve(request)

    assert result.status == ScheduleStatus.FEASIBLE
    assert result.score_components is not None
    assert result.score_components.hard_constraint_violations == 0

    # Verify unaffected assignments did NOT move
    result_asgn_map = {a.activity_id: a for a in result.assignments}
    for unaffect_id in scope.unaffected_activity_ids:
        orig = asgn_map[unaffect_id]
        repaired = result_asgn_map[unaffect_id]
        assert orig.start_at == repaired.start_at
        assert orig.end_at == repaired.end_at
        assert orig.resource_id == repaired.resource_id

    # Verify affected assignment was successfully reassigned away from disrupted technician
    repaired_maint_005 = result_asgn_map["wo-maint-005"]
    assert repaired_maint_005.resource_id != disruption["resource_id"]


def test_canonical_fixture_sc_exam_repair_001() -> None:
    """Verify SC-EXAM-REPAIR-001 targeted repair behavior."""
    fixture = _load_json(EXAM_REPAIR_FILE)

    needed_resource_ids = {
        "res-inv-001",
        "res-inv-002",
        "res-inv-003",
        "res-inv-004",
        "res-room-001",
        "res-room-002",
        "res-room-003",
    }
    resources = _build_resources_snapshot(needed_resource_ids)

    # 1. Build activities from sessions
    activities: list[SchedulingActivity] = []
    for sess in fixture["exam_sessions"]:
        windows = [
            TimeWindow(
                start_at=datetime.fromisoformat(w["start_at"]),
                end_at=datetime.fromisoformat(w["end_at"]),
            )
            for w in sess.get("allowed_windows", [])
        ]
        activities.append(
            SchedulingActivity(
                id=sess["id"],
                title=f"Exam Session {sess['id']}",
                duration_minutes=120,
                allowed_windows=windows,
                required_skills=["INVIGILATION_GENERAL"],
            )
        )

    # 2. Reconstruct published baseline assignments
    baseline_assignments: list[ScheduleAssignmentRecord] = []
    sched_id = uuid4()
    for item in fixture["published_baseline"]:
        baseline_assignments.append(
            ScheduleAssignmentRecord(
                id=uuid4(),
                tenant_id=fixture["tenant_id"],
                schedule_id=sched_id,
                activity_id=item["session_id"],
                resource_id=item["invigilator_resource_id"],
                start_at=datetime.fromisoformat(item["start_at"]),
                end_at=datetime.fromisoformat(item["end_at"]),
                is_locked=item.get("locked", False),
            )
        )

    # 3. Disruption
    disruption = fixture["disruptions"][0]
    repair_req = ScheduleRepairRequest(
        disrupted_resource_ids=[disruption["resource_id"]],
        disrupted_interval_start=datetime.fromisoformat(disruption["start_at"]),
        disrupted_interval_end=datetime.fromisoformat(disruption["end_at"]),
        reason=disruption["reason"],
        movement_weight=10,
    )

    # 4. Scope Builder verification
    builder = RepairScopeBuilder()
    scope = builder.build_scope(
        activities=activities,
        baseline_assignments=baseline_assignments,
        constraints=[],
        repair_request=repair_req,
    )

    # Expectation: exactly assign-exam-005 session is affected
    assert scope.affected_activity_ids == {"session-science-g10a"}
    assert len(scope.unaffected_activity_ids) == 9

    # 5. Build solve request with frozen unaffected assignments
    asgn_map = {a.activity_id: a for a in baseline_assignments}
    updated_activities = []
    for act in activities:
        base_asgn = asgn_map[act.id]
        if act.id in scope.unaffected_activity_ids:
            # FREEZE
            updated_activities.append(
                act.model_copy(
                    update={
                        "locked_assignment": LockedAssignment(
                            start_at=base_asgn.start_at,
                            end_at=base_asgn.end_at,
                            resource_ids=[base_asgn.resource_id],
                        ),
                        "allowed_windows": [
                            TimeWindow(
                                start_at=base_asgn.start_at, end_at=base_asgn.end_at
                            )
                        ],
                        "required_resource_ids": [base_asgn.resource_id],
                    }
                )
            )
        else:
            # Movable affected assignment
            updated_activities.append(
                act.model_copy(
                    update={
                        "locked_assignment": None,
                        "baseline_assignment": LockedAssignment(
                            start_at=base_asgn.start_at,
                            end_at=base_asgn.end_at,
                            resource_ids=[base_asgn.resource_id],
                        ),
                    }
                )
            )

    # Update resource with blackout
    updated_resources = []
    for r in resources:
        unavail = list(r.unavailability_windows)
        if r.id == disruption["resource_id"]:
            unavail.append(
                TimeWindow(
                    start_at=datetime.fromisoformat(disruption["start_at"]),
                    end_at=datetime.fromisoformat(disruption["end_at"]),
                )
            )
        updated_resources.append(
            r.model_copy(update={"unavailability_windows": unavail})
        )

    h_start = datetime.fromisoformat(fixture["horizon"]["start_at"])
    h_end = datetime.fromisoformat(fixture["horizon"]["end_at"])

    profile = SolverProfile(
        profile_key="exam_repair_profile",
        version=1,
        name="Exam Repair Profile",
        horizon_start=h_start,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=10,
        num_search_workers=4,
        random_seed=42,
        soft_weights=SoftObjectiveWeights(
            preference_weight=2,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=10,
        ),
    )

    request = SchedulingRequest(
        tenant_id=fixture["tenant_id"],
        schedule_key="exam_repair",
        horizon_start=h_start,
        horizon_end=h_end,
        timezone=fixture["timezone"],
        activities=updated_activities,
        resources=updated_resources,
        constraints=[],
        profile=profile,
    )

    scheduler = CpSatScheduler()
    result = scheduler.solve(request)

    assert result.status == ScheduleStatus.FEASIBLE
    assert result.score_components is not None
    assert result.score_components.hard_constraint_violations == 0

    # Verify unaffected assignments did NOT move
    result_asgn_map = {a.activity_id: a for a in result.assignments}
    for unaffect_id in scope.unaffected_activity_ids:
        orig = asgn_map[unaffect_id]
        repaired = result_asgn_map[unaffect_id]
        assert orig.start_at == repaired.start_at
        assert orig.end_at == repaired.end_at
        assert orig.resource_id == repaired.resource_id

    # Verify affected assignment was successfully reassigned away from disrupted invigilator
    repaired_exam_005 = result_asgn_map["session-science-g10a"]
    assert repaired_exam_005.resource_id != disruption["resource_id"]


def test_infeasible_repair_conflicting_hard_lock() -> None:
    """Test that a disruption on an explicit hard lock produces INFEASIBLE
    with conflict diagnostics.
    """
    base_time = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)

    # Single specialized activity requiring res-specialist with locked assignment
    act = SchedulingActivity(
        id="act-critical",
        title="Critical Nuclear Valve Service",
        duration_minutes=120,
        required_skills=["NUCLEAR_CERTIFIED"],
        required_resource_ids=["res-specialist"],
        allowed_windows=[
            TimeWindow(start_at=base_time, end_at=base_time + timedelta(hours=2))
        ],
        locked_assignment=LockedAssignment(
            start_at=base_time,
            end_at=base_time + timedelta(hours=2),
            resource_ids=["res-specialist"],
        ),
    )

    # Specialist resource
    res = SchedulingResourceSnapshot(
        id="res-specialist",
        type=ResourceType.PERSON,
        display_label="Dr. Specialist",
        capacity=1,
        skills=["NUCLEAR_CERTIFIED"],
        unavailability_windows=[
            # Disruption: specialist is unavailable during the exact locked window!
            TimeWindow(
                start_at=base_time - timedelta(minutes=30),
                end_at=base_time + timedelta(hours=3),
            )
        ],
    )

    h_start = base_time - timedelta(days=1)
    h_end = base_time + timedelta(days=1)

    profile = SolverProfile(
        profile_key="default",
        version=1,
        name="Default Profile",
        horizon_start=h_start,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=5,
        num_search_workers=2,
        random_seed=42,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=10,
        ),
    )

    request = SchedulingRequest(
        tenant_id="tenant-1",
        schedule_key="test_infeasible",
        horizon_start=h_start,
        horizon_end=h_end,
        timezone="UTC",
        activities=[act],
        resources=[res],
        constraints=[],
        profile=profile,
    )

    scheduler = CpSatScheduler()
    result = scheduler.solve(request)

    assert result.status == ScheduleStatus.INFEASIBLE
    assert len(result.assignments) == 0
    assert len(result.diagnostics) > 0
    diag = result.diagnostics[0]
    assert "LOCK" in diag.root_cause_type or "UNSATISFIABLE" in diag.root_cause_type
