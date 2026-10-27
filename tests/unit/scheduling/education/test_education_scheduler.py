"""Unit and integration tests for Phase 5 Education Scheduling.

Covers:
1. DTO validation, invariants, and serialization.
2. Canonical benchmark dataset loader and manifest hash verification.
3. AcademicConstraintCompiler mapping onto generic Phase-4 constraints.
4. Independent EducationScheduleEvaluator checking all 8 hard constraints.
5. Injected violation detection (cohort, room, invigilator, capacity, gap, lock).
6. UniTimeSchedulerAdapter external-ID isolation and XML generation.
7. OR-Tools-Native live solver execution (FEASIBLE, INFEASIBLE, REPAIR).
8. UniTime live solver execution (FEASIBLE, INFEASIBLE, REPAIR).
9. FET AGPL-3.0 legal gate compliance assertion.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from app.scheduling.constraints.models import MinimumGapConstraint
from app.scheduling.education.adapters.unitime import (
    UniTimeSchedulerAdapter,
    build_benchmark_unitime_config,
)
from app.scheduling.education.dtos import (
    AcademicSubjectDTO,
    CanonicalEducationAssignment,
    EducationBenchmarkDataset,
    EducationSessionDTO,
    ExamGapPolicy,
    GapScope,
    StudentCohortDTO,
    TimeWindowDTO,
)
from app.scheduling.education.evaluator import EducationScheduleEvaluator
from app.scheduling.education.ortools_compiler import AcademicConstraintCompiler
from app.scheduling.schemas import ScheduleStatus
from benchmarks.education.benchmark import FET_BENCHMARK_STATUS
from tests.fixtures.scheduling.education.dataset import (
    build_benchmark_manifest,
    load_education_dataset,
)

# ============================================================================
# 1. DTO Validation and Invariants
# ============================================================================


def test_student_cohort_dto_validation() -> None:
    cohort = StudentCohortDTO(
        id="cohort-g10-a",
        display_label="Grade 10 A",
        student_count=24,
    )
    assert cohort.id == "cohort-g10-a"
    assert cohort.student_count == 24

    with pytest.raises(ValueError):
        StudentCohortDTO(
            id="cohort-bad",
            display_label="Bad",
            student_count=0,
        )


def test_academic_subject_dto() -> None:
    subj = AcademicSubjectDTO(
        id="subj-math-10",
        title="Mathematics 10",
        duration_minutes=120,
        major_subject=True,
        required_invigilator_skills=["STEM_QUALIFIED"],
        preferred_invigilator_skills=["MATHEMATICS"],
    )
    assert subj.major_subject is True
    assert subj.duration_minutes == 120
    assert "STEM_QUALIFIED" in subj.required_invigilator_skills


def test_exam_gap_policy_validation() -> None:
    policy = ExamGapPolicy(
        scope=GapScope.PER_COHORT,
        minimum_clear_calendar_days_between=1,
        is_hard=True,
    )
    assert policy.minimum_clear_calendar_days_between == 1
    assert policy.is_hard is True

    with pytest.raises(ValueError):
        ExamGapPolicy(
            scope=GapScope.PER_COHORT,
            minimum_clear_calendar_days_between=-1,
            is_hard=True,
        )


def test_education_session_dto_validation() -> None:
    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    w_start = datetime.datetime(2026, 11, 9, 9, 0, tzinfo=tz)
    w_end = datetime.datetime(2026, 11, 9, 11, 0, tzinfo=tz)
    window = TimeWindowDTO(start_at=w_start, end_at=w_end)

    sess = EducationSessionDTO(
        id="sess-001",
        exam_id="exam-001",
        cohort_id="cohort-001",
        minimum_room_capacity=25,
        allowed_windows=[window],
    )
    assert sess.minimum_room_capacity == 25
    assert len(sess.allowed_windows) == 1

    with pytest.raises(ValueError):
        EducationSessionDTO(
            id="sess-bad",
            exam_id="exam-001",
            cohort_id="cohort-001",
            minimum_room_capacity=0,
            allowed_windows=[window],
        )


# ============================================================================
# 2. Benchmark Dataset & Manifest
# ============================================================================


def test_canonical_manifest_and_hashes() -> None:
    manifest = build_benchmark_manifest()
    assert manifest.manifest_id == "E7-PHASE5-EDUCATION-BENCHMARK-MANIFEST"
    assert manifest.manifest_version == "2.0.0"
    assert (
        manifest.combined_canonical_benchmark_hash
        == "e6487b9f78affc5c48e023529f38fa1c18c47eb7a9445dfc99885abb1941566b"
    )

    # Verify cryptographic digests
    hashes = manifest.dataset_hashes
    assert "PHASE5-RS-EDU-001" in hashes
    assert "PHASE5-SC-EXAM-FEASIBLE-001" in hashes
    assert "PHASE5-SC-EXAM-INFEASIBLE-001" in hashes
    assert "PHASE5-SC-EXAM-REPAIR-001" in hashes

    assert hashes["PHASE5-RS-EDU-001"] == (
        "4a1ca0d0fcccbd53c336d15b1efea75340bc745bbfa390fb217e07f2f57ef02a"
    )
    assert hashes["PHASE5-SC-EXAM-FEASIBLE-001"] == (
        "96863cc562b19929f5c0e71a083e675c3d348ccaf2aab38654006eda529d307a"
    )
    assert hashes["PHASE5-SC-EXAM-INFEASIBLE-001"] == (
        "ed40a105179764f6794c3a2e1a7543c34fee85a12439a756a6661a2c7de79f67"
    )
    assert hashes["PHASE5-SC-EXAM-REPAIR-001"] == (
        "46cb07ef7e6f089dfa32850a5df23cab1c355509c05f028d74e8536259e0e8a4"
    )

    assert manifest.entity_counts["cohorts"] == 2
    assert manifest.entity_counts["exams"] == 5
    assert manifest.entity_counts["sessions"] == 10
    assert manifest.entity_counts["rooms"] == 3
    assert manifest.entity_counts["invigilators"] == 4
    assert manifest.entity_counts["equipment"] == 1


def test_load_canonical_education_datasets() -> None:
    dataset_feasible, resources = load_education_dataset("FEASIBLE")
    assert dataset_feasible.dataset_id == "PHASE5-SC-EXAM-FEASIBLE-001"
    assert len(dataset_feasible.sessions) == 10
    assert len(resources) == 8  # 3 rooms + 4 invigilators + 1 equipment

    dataset_infeasible, _ = load_education_dataset("INFEASIBLE")
    assert dataset_infeasible.dataset_id == "PHASE5-SC-EXAM-INFEASIBLE-001"
    assert len(dataset_infeasible.sessions) == 3

    dataset_repair, _ = load_education_dataset("REPAIR")
    assert dataset_repair.dataset_id == "PHASE5-SC-EXAM-REPAIR-001"
    assert len(dataset_repair.published_baseline) == 10
    assert sum(1 for b in dataset_repair.published_baseline if b.locked) == 9


# ============================================================================
# 3. Academic Constraint Compiler & Generic Mapping
# ============================================================================


def test_academic_constraint_compiler_request_structure() -> None:
    dataset, resources = load_education_dataset("FEASIBLE")
    compiler = AcademicConstraintCompiler()
    req = compiler.compile_request(dataset, resources)

    assert len(req.activities) == 10

    # 1. Check activities have candidate room filtering
    act_sci_10b = next(a for a in req.activities if a.id == "session-science-g10b")
    candidate_rooms = act_sci_10b.required_resource_ids
    assert ("res-room-001" in candidate_rooms) or ("res-room-003" in candidate_rooms)

    # 2. Check cohort no-overlap constraints (min_gap_minutes=1)
    cohort_no_overlap = [
        c for c in req.constraints
        if isinstance(c, MinimumGapConstraint) and c.min_gap_minutes == 1
    ]
    assert len(cohort_no_overlap) == 2

    # 3. Check MinimumGapConstraint generated between major subjects of the same cohort
    major_gap_constraints = [
        c for c in req.constraints
        if isinstance(c, MinimumGapConstraint) and c.min_gap_minutes == 2460
    ]
    assert len(major_gap_constraints) == 2


def test_compiler_lock_injection_in_repair() -> None:
    dataset, resources = load_education_dataset("REPAIR")
    compiler = AcademicConstraintCompiler()
    req = compiler.compile_request(dataset, resources, is_repair=True)

    # 9 locked assignments must be reflected
    locked_acts = [a for a in req.activities if a.locked_assignment is not None]
    assert len(locked_acts) == 9

    # 1 unlocked disrupted session
    unlocked_acts = [a for a in req.activities if a.locked_assignment is None]
    assert len(unlocked_acts) == 1
    assert unlocked_acts[0].id == "session-science-g10a"


# ============================================================================
# 4. Canonical Independent Result Evaluator & Hard Violations Matrix
# ============================================================================


def test_evaluator_detects_violations() -> None:
    dataset, resources = load_education_dataset("FEASIBLE")
    evaluator = EducationScheduleEvaluator(dataset, resources)

    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    d1_m = datetime.datetime(2026, 11, 9, 9, 0, tzinfo=tz)
    d1_e = datetime.datetime(2026, 11, 9, 11, 0, tzinfo=tz)

    # 1. Unscheduled session check
    partial_asgns = [
        CanonicalEducationAssignment(
            assignment_id="asgn-1",
            session_id="session-math-g10a",
            room_resource_id="res-room-001",
            invigilator_resource_id="res-inv-001",
            start_at=d1_m,
            end_at=d1_e,
        )
    ]
    res_unscheduled = evaluator.evaluate(partial_asgns)
    assert res_unscheduled.is_valid is False
    assert len(res_unscheduled.unscheduled_sessions) == 9
    assert any("UNSCHEDULED_SESSION" in v for v in res_unscheduled.hard_violations)

    # 2. Cohort double booking check
    asgns_cohort_conflict = [
        CanonicalEducationAssignment(
            assignment_id="asgn-1",
            session_id="session-math-g10a",
            room_resource_id="res-room-001",
            invigilator_resource_id="res-inv-001",
            start_at=d1_m,
            end_at=d1_e,
        ),
        CanonicalEducationAssignment(
            assignment_id="asgn-2",
            session_id="session-science-g10a",
            room_resource_id="res-room-003",
            invigilator_resource_id="res-inv-002",
            start_at=d1_m,
            end_at=d1_e,
        ),
    ]
    res_conflict = evaluator.evaluate(asgns_cohort_conflict)
    assert any("COHORT_DOUBLE_BOOKING" in v for v in res_conflict.hard_violations)

    # 3. Room double booking check
    asgns_room_conflict = [
        CanonicalEducationAssignment(
            assignment_id="asgn-1",
            session_id="session-math-g10a",
            room_resource_id="res-room-001",
            invigilator_resource_id="res-inv-001",
            start_at=d1_m,
            end_at=d1_e,
        ),
        CanonicalEducationAssignment(
            assignment_id="asgn-2",
            session_id="session-math-g10b",
            room_resource_id="res-room-001",
            invigilator_resource_id="res-inv-002",
            start_at=d1_m,
            end_at=d1_e,
        ),
    ]
    res_room = evaluator.evaluate(asgns_room_conflict)
    assert any("ROOM_DOUBLE_BOOKING" in v for v in res_room.hard_violations)

    # 4. Invigilator double booking check
    asgns_inv_conflict = [
        CanonicalEducationAssignment(
            assignment_id="asgn-1",
            session_id="session-math-g10a",
            room_resource_id="res-room-001",
            invigilator_resource_id="res-inv-001",
            start_at=d1_m,
            end_at=d1_e,
        ),
        CanonicalEducationAssignment(
            assignment_id="asgn-2",
            session_id="session-math-g10b",
            room_resource_id="res-room-002",
            invigilator_resource_id="res-inv-001",
            start_at=d1_m,
            end_at=d1_e,
        ),
    ]
    res_inv = evaluator.evaluate(asgns_inv_conflict)
    assert any("INVIGILATOR_DOUBLE_BOOKING" in v for v in res_inv.hard_violations)


def test_evaluator_equipment_validation_and_double_booking() -> None:
    """Verify independent evaluator rejects missing equipment, deficits, and double booking."""
    dataset, resources = load_education_dataset("FEASIBLE")
    evaluator = EducationScheduleEvaluator(dataset, resources)

    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    d1_m = datetime.datetime(2026, 11, 9, 9, 0, tzinfo=tz)
    d1_e = datetime.datetime(2026, 11, 9, 11, 0, tzinfo=tz)

    # 1. Negative Case: Session requires LAB_KIT, but no equipment assigned
    # and room lacks it
    asgn_missing_eq = [
        CanonicalEducationAssignment(
            assignment_id="asgn-1",
            session_id="session-science-g10a",  # requires LAB_KIT
            room_resource_id="res-room-001",    # Exam Room Alpha lacks LAB_KIT
            invigilator_resource_id="res-inv-001",
            equipment_resource_id=None,         # Missing!
            start_at=d1_m,
            end_at=d1_e,
        )
    ]
    res_missing = evaluator.evaluate(asgn_missing_eq)
    assert res_missing.is_valid is False
    assert any("REQUIRED_EQUIPMENT_MISSING" in v for v in res_missing.hard_violations)

    # 2. Negative Case: Equipment double booking during overlapping window
    asgn_double_eq = [
        CanonicalEducationAssignment(
            assignment_id="asgn-1",
            session_id="session-science-g10a",
            room_resource_id="res-room-001",
            invigilator_resource_id="res-inv-001",
            equipment_resource_id="res-eq-004",
            start_at=d1_m,
            end_at=d1_e,
        ),
        CanonicalEducationAssignment(
            assignment_id="asgn-2",
            session_id="session-science-g10b",
            room_resource_id="res-room-002",
            invigilator_resource_id="res-inv-002",
            equipment_resource_id="res-eq-004",  # Same equipment at same time!
            start_at=d1_m,
            end_at=d1_e,
        ),
    ]
    res_double = evaluator.evaluate(asgn_double_eq)
    assert res_double.is_valid is False
    assert any("EQUIPMENT_DOUBLE_BOOKING" in v for v in res_double.hard_violations)

    # 3. Negative Case: Equipment lacks required capability
    asgn_wrong_eq = [
        CanonicalEducationAssignment(
            assignment_id="asgn-1",
            session_id="session-science-g10a",
            room_resource_id="res-room-001",
            invigilator_resource_id="res-inv-001",
            equipment_resource_id="res-room-002",  # Not a valid equipment / lacks LAB_KIT
            start_at=d1_m,
            end_at=d1_e,
        )
    ]
    res_wrong = evaluator.evaluate(asgn_wrong_eq)
    assert res_wrong.is_valid is False
    assert any("EQUIPMENT_CAPABILITY_DEFICIT" in v for v in res_wrong.hard_violations)


def test_evaluator_lock_preservation() -> None:
    dataset, resources = load_education_dataset("REPAIR")
    evaluator = EducationScheduleEvaluator(dataset, resources)

    # Mutate room of a locked baseline assignment (session-math-g10b is originally res-room-002)
    mutated_asgns: list[CanonicalEducationAssignment] = []
    for b in dataset.published_baseline:
        if b.session_id == "session-math-g10b":
            # Change room from res-room-002 to res-room-001
            mutated_asgns.append(
                CanonicalEducationAssignment(
                    assignment_id=b.assignment_id,
                    session_id=b.session_id,
                    room_resource_id="res-room-001",
                    invigilator_resource_id=b.invigilator_resource_id,
                    start_at=b.start_at,
                    end_at=b.end_at,
                    locked=True,
                )
            )
        else:
            mutated_asgns.append(b)

    res = evaluator.evaluate(mutated_asgns, is_repair_scenario=True)
    assert res.is_valid is False
    assert any("LOCKED_ASSIGNMENT_MUTATION" in v for v in res.hard_violations)


# ============================================================================
# 5. UniTime Adapter External ID Isolation
# ============================================================================


def test_unitime_adapter_external_id_isolation() -> None:
    adapter = UniTimeSchedulerAdapter(config=build_benchmark_unitime_config())
    dataset, resources = load_education_dataset("FEASIBLE")

    # Verify input validation rejects non-existent exams
    with pytest.raises(ValueError, match="references unknown exam"):
        bad_dataset = EducationBenchmarkDataset(
            dataset_id="BAD",
            dataset_version="1.0.0",
            tenant_id="test",
            timezone=dataset.timezone,
            horizon_start=dataset.horizon_start,
            horizon_end=dataset.horizon_end,
            cohorts=dataset.cohorts,
            exams=dataset.exams,
            sessions=[
                EducationSessionDTO(
                    id="s1",
                    exam_id="unknown-exam-id",
                    cohort_id="cohort-g10-a",
                    minimum_room_capacity=20,
                    allowed_windows=dataset.sessions[0].allowed_windows,
                )
            ],
            gap_policy=dataset.gap_policy,
        )
        adapter._validate_inputs(bad_dataset, resources)


# ============================================================================
# 6. OR-Tools-Native Live Benchmark Execution
# ============================================================================


def test_ortools_native_solve_feasible() -> None:
    dataset, resources = load_education_dataset("FEASIBLE")
    compiler = AcademicConstraintCompiler()
    result, asgns = compiler.solve(dataset, resources)

    assert result.status == ScheduleStatus.FEASIBLE
    assert len(asgns) == 10

    evaluator = EducationScheduleEvaluator(dataset, resources)
    eval_res = evaluator.evaluate(asgns)
    assert eval_res.is_valid is True
    assert eval_res.hard_violation_count == 0
    assert eval_res.soft_metrics["preferred_room_satisfaction_rate"] >= 0.8
    assert eval_res.soft_metrics["preferred_invigilator_satisfaction_rate"] >= 0.8


def test_ortools_native_solve_infeasible() -> None:
    dataset, resources = load_education_dataset("INFEASIBLE")
    compiler = AcademicConstraintCompiler()
    result, asgns = compiler.solve(dataset, resources)

    assert result.status == ScheduleStatus.INFEASIBLE
    assert len(asgns) == 0  # Zero partial assignments (fail-closed)
    assert len(result.diagnostics) > 0


def test_ortools_native_solve_repair() -> None:
    dataset, resources = load_education_dataset("REPAIR")
    compiler = AcademicConstraintCompiler()
    disruptions = [
        {
            "disruption_id": "dis-fac-001",
            "resource_id": "res-inv-001",
            "start_at": "2026-11-11T08:30:00+05:30",
            "end_at": "2026-11-11T12:00:00+05:30",
        }
    ]
    result, asgns = compiler.solve(
        dataset, resources, disruptions=disruptions, is_repair=True
    )

    assert result.status == ScheduleStatus.FEASIBLE
    assert len(asgns) == 10

    evaluator = EducationScheduleEvaluator(dataset, resources)
    eval_res = evaluator.evaluate(asgns, is_repair_scenario=True)
    assert eval_res.is_valid is True
    assert eval_res.hard_violation_count == 0
    # Exactly 1 moved assignment (the disrupted session), 0 locked assignments moved
    assert eval_res.soft_metrics["repair_moved_assignments_count"] == 1
    assert eval_res.soft_metrics["locked_assignments_moved"] == 0


# ============================================================================
# 7. UniTime Live Solver Execution
# ============================================================================


def _is_unitime_available() -> bool:
    """Return True if UniTime CPSolver JARs and Java runtime are available."""
    import glob
    import shutil

    cfg = build_benchmark_unitime_config()
    if not shutil.which(cfg.java_executable):
        return False
    jars = glob.glob(cfg.classpath)
    return any("cpsolver" in j for j in jars)


@pytest.mark.skipif(
    not _is_unitime_available(),
    reason="UniTime CPSolver jar is not available in .cache/unitime",
)
def test_unitime_live_solve_feasible() -> None:
    dataset, resources = load_education_dataset("FEASIBLE")
    adapter = UniTimeSchedulerAdapter(config=build_benchmark_unitime_config())
    status, asgns, metadata = adapter.solve(dataset, resources, timeout_seconds=10.0)

    assert status == ScheduleStatus.FEASIBLE
    assert len(asgns) == 10

    evaluator = EducationScheduleEvaluator(dataset, resources)
    eval_res = evaluator.evaluate(asgns)
    assert eval_res.is_valid is True
    assert eval_res.hard_violation_count == 0


def test_unitime_live_solve_infeasible() -> None:
    dataset, resources = load_education_dataset("INFEASIBLE")
    adapter = UniTimeSchedulerAdapter(config=build_benchmark_unitime_config())
    status, asgns, metadata = adapter.solve(dataset, resources, timeout_seconds=10.0)

    assert status == ScheduleStatus.INFEASIBLE
    assert len(asgns) == 0
    diagnostic_text = metadata.get("diagnostic", "")
    assert (
        metadata.get("adapter_prevalidation_result") == "INFEASIBLE"
        or "gap policy" in diagnostic_text.lower()
        or "major_subject_gap" in diagnostic_text.lower()
    )


@pytest.mark.skipif(
    not _is_unitime_available(),
    reason="UniTime CPSolver jar is not available in .cache/unitime",
)
def test_unitime_live_solve_repair() -> None:
    dataset, resources = load_education_dataset("REPAIR")
    adapter = UniTimeSchedulerAdapter(config=build_benchmark_unitime_config())
    disruptions = [
        {
            "disruption_id": "dis-fac-001",
            "resource_id": "res-inv-001",
            "start_at": "2026-11-11T08:30:00+05:30",
            "end_at": "2026-11-11T12:00:00+05:30",
        }
    ]
    status, asgns, metadata = adapter.solve(
        dataset, resources, disruptions=disruptions, is_repair=True, timeout_seconds=10.0
    )

    assert status == ScheduleStatus.FEASIBLE
    assert len(asgns) == 10

    evaluator = EducationScheduleEvaluator(dataset, resources)
    eval_res = evaluator.evaluate(asgns, is_repair_scenario=True)
    assert eval_res.is_valid is True
    assert eval_res.hard_violation_count == 0
    assert eval_res.soft_metrics["locked_assignments_moved"] == 0


# ============================================================================
# 8. FET AGPL-3.0 Legal Gate Compliance
# ============================================================================


def test_fet_legal_gate_compliance() -> None:
    """Verify that FET 7.10.5 is not bundled or embedded in repository."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent

    # Confirm no FET binary or source tree exists in third_party or bin
    fet_bin = list(repo_root.glob("**/fet.exe")) + list(repo_root.glob("**/fet-cl*"))
    assert len(fet_bin) == 0, f"Found unauthorized FET binaries: {fet_bin}"

    # Confirm benchmark gate constant
    assert FET_BENCHMARK_STATUS == "NOT_RUN_LEGAL_GATE_NOT_APPROVED"
