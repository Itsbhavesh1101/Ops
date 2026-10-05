"""Unit tests for PlanValidator pipeline."""

from app.task_intelligence.schemas import (
    DependencyDraft,
    MilestoneDraft,
    PlanSchema,
    TaskDraft,
    ValidationIssueCode,
    ValidationSeverity,
)
from app.task_intelligence.validators import PlanValidator


def test_validator_rejects_empty_plan() -> None:
    validator = PlanValidator()
    plan = PlanSchema(plan_name="Empty Plan", tasks=[])
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any(i.code == ValidationIssueCode.EMPTY_PLAN for i in result.issues)


def test_validator_valid_plan_passes() -> None:
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Valid Exam Plan",
        tasks=[
            TaskDraft(
                temporary_id="T1", title="Draft question papers", estimated_duration_minutes=120
            ),
            TaskDraft(
                temporary_id="T2", title="Review question papers", estimated_duration_minutes=60
            ),
            TaskDraft(
                temporary_id="T3", title="Print question papers", estimated_duration_minutes=90
            ),
        ],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
            DependencyDraft(predecessor_id="T2", successor_id="T3"),
        ],
        milestones=[
            MilestoneDraft(
                temporary_id="M1",
                name="Papers Ready",
                associated_task_ids=["T3"],
            )
        ],
    )
    result = validator.validate(plan)
    assert result.is_valid is True
    assert len(result.issues) == 0
    assert result.evaluated_task_count == 3
    assert result.evaluated_dependency_count == 2
    assert result.topological_order == ["T1", "T2", "T3"]
    assert result.critical_path == ["T1", "T2", "T3"]
    assert result.cycle_path is None


def test_validator_detects_duplicate_task_id() -> None:
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Duplicate IDs",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1"),
            TaskDraft(temporary_id="T1", title="Task 2 with same ID"),
        ],
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any(i.code == ValidationIssueCode.DUPLICATE_TASK_ID for i in result.issues)


def test_validator_detects_parent_self_reference_and_missing_parent() -> None:
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Bad Hierarchy",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1", parent_id="T1"),
            TaskDraft(temporary_id="T2", title="Task 2", parent_id="NON_EXISTENT"),
        ],
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any(i.code == ValidationIssueCode.PARENT_SELF_REFERENCE for i in result.issues)
    assert any(i.code == ValidationIssueCode.PARENT_NOT_FOUND for i in result.issues)


def test_validator_detects_parent_cycle() -> None:
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Parent Cycle",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1", parent_id="T2"),
            TaskDraft(temporary_id="T2", title="Task 2", parent_id="T1"),
        ],
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any(i.code == ValidationIssueCode.PARENT_CYCLE for i in result.issues)


def test_validator_detects_unknown_dependency_task() -> None:
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Unknown Dep",
        tasks=[TaskDraft(temporary_id="T1", title="Task 1")],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="GHOST"),
        ],
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any(i.code == ValidationIssueCode.UNKNOWN_TASK_REFERENCE for i in result.issues)


def test_validator_detects_duplicate_dependency() -> None:
    validator = PlanValidator()
    # Note: DependencyDraft itself validates self-dependency, but duplicate edges can happen in list
    plan = PlanSchema(
        plan_name="Dup Edge",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1"),
            TaskDraft(temporary_id="T2", title="Task 2"),
        ],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
        ],
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any(i.code == ValidationIssueCode.DUPLICATE_DEPENDENCY for i in result.issues)


def test_validator_detects_dag_cycle_and_returns_explicit_path() -> None:
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Cycle Plan",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1"),
            TaskDraft(temporary_id="T2", title="Task 2"),
            TaskDraft(temporary_id="T3", title="Task 3"),
        ],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
            DependencyDraft(predecessor_id="T2", successor_id="T3"),
            DependencyDraft(predecessor_id="T3", successor_id="T1"),
        ],
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    cycle_issue = next(
        (i for i in result.issues if i.code == ValidationIssueCode.CYCLE_DETECTED), None
    )
    assert cycle_issue is not None
    assert result.cycle_path is not None
    assert result.cycle_path[0] == result.cycle_path[-1]
    assert set(result.cycle_path) == {"T1", "T2", "T3"}


def test_validator_detects_unknown_milestone_task() -> None:
    validator = PlanValidator()
    plan = PlanSchema(
        plan_name="Bad Milestone",
        tasks=[TaskDraft(temporary_id="T1", title="Task 1")],
        milestones=[
            MilestoneDraft(temporary_id="M1", name="M1", associated_task_ids=["GHOST_TASK"]),
        ],
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any(i.code == ValidationIssueCode.UNKNOWN_MILESTONE_TASK for i in result.issues)


def test_validator_with_custom_policy_hook() -> None:
    def require_closure_task(plan: PlanSchema):
        has_closure = any(t.work_category == "CLOSURE" for t in plan.tasks)
        if not has_closure:
            from app.task_intelligence.schemas import ValidationIssue

            return [
                ValidationIssue(
                    code=ValidationIssueCode.POLICY_VIOLATION,
                    location="tasks",
                    message="Every exam plan must have at least one CLOSURE category task.",
                    severity=ValidationSeverity.ERROR,
                )
            ]
        return []

    validator = PlanValidator(policy_hooks=[require_closure_task])
    plan = PlanSchema(
        plan_name="No Closure Plan",
        tasks=[TaskDraft(temporary_id="T1", title="Task 1", work_category="PLANNING")],
    )
    result = validator.validate(plan)
    assert result.is_valid is False
    assert any(i.code == ValidationIssueCode.POLICY_VIOLATION for i in result.issues)
