"""Unit tests for Task Intelligence Pydantic schemas and state machine."""

import pytest
from pydantic import ValidationError

from app.task_intelligence.schemas import (
    DependencyDraft,
    DependencyType,
    GoalCreate,
    InvalidPlanStateTransitionError,
    MilestoneDraft,
    PlanSchema,
    PlanState,
    TaskDraft,
    TaskPriority,
    assert_valid_plan_transition,
)


def test_goal_create_valid() -> None:
    goal = GoalCreate(
        objective="Plan semester exams",
        scope="Grade 10 cohort",
        constraints=["1 day gap between exams"],
        resource_scope=["ROOM", "INVIGILATOR"],
        context_references=["opaque-ref-1"],
    )
    assert goal.objective == "Plan semester exams"
    assert goal.scope == "Grade 10 cohort"
    assert len(goal.constraints) == 1
    assert goal.context_references == ["opaque-ref-1"]


def test_goal_create_short_objective_fails() -> None:
    with pytest.raises(ValidationError):
        GoalCreate(objective="ab")


def test_task_draft_defaults() -> None:
    task = TaskDraft(
        temporary_id="T1",
        title="Prepare syllabus",
    )
    assert task.temporary_id == "T1"
    assert task.estimated_duration_minutes == 60
    assert task.priority == TaskPriority.MEDIUM
    assert task.parent_id is None


def test_task_draft_negative_duration_fails() -> None:
    with pytest.raises(ValidationError):
        TaskDraft(
            temporary_id="T1",
            title="Invalid",
            estimated_duration_minutes=-10,
        )


def test_dependency_draft_self_dependency_rejected() -> None:
    with pytest.raises(ValidationError, match="Self-dependency rejected"):
        DependencyDraft(
            predecessor_id="T1",
            successor_id="T1",
        )


def test_dependency_draft_valid() -> None:
    dep = DependencyDraft(
        predecessor_id="T1",
        successor_id="T2",
        dependency_type=DependencyType.FINISH_TO_START,
        lag_minutes=15,
    )
    assert dep.predecessor_id == "T1"
    assert dep.successor_id == "T2"
    assert dep.lag_minutes == 15


def test_milestone_draft_valid() -> None:
    milestone = MilestoneDraft(
        temporary_id="M1",
        name="Question paper readiness",
        associated_task_ids=["T1", "T2"],
    )
    assert milestone.temporary_id == "M1"
    assert len(milestone.associated_task_ids) == 2


def test_plan_schema_valid() -> None:
    plan = PlanSchema(
        plan_name="Exam Schedule Plan",
        tasks=[
            TaskDraft(temporary_id="T1", title="Task 1"),
            TaskDraft(temporary_id="T2", title="Task 2"),
        ],
        dependencies=[
            DependencyDraft(predecessor_id="T1", successor_id="T2"),
        ],
        milestones=[
            MilestoneDraft(temporary_id="M1", name="Milestone 1", associated_task_ids=["T1"]),
        ],
    )
    assert plan.plan_name == "Exam Schedule Plan"
    assert len(plan.tasks) == 2
    assert len(plan.dependencies) == 1
    assert len(plan.milestones) == 1


def test_plan_state_transitions_valid() -> None:
    # DRAFT -> GENERATED allowed
    assert_valid_plan_transition(PlanState.DRAFT, PlanState.GENERATED)
    # GENERATED -> VALIDATED allowed
    assert_valid_plan_transition(PlanState.GENERATED, PlanState.VALIDATED)
    # VALIDATED -> APPROVAL_PENDING allowed
    assert_valid_plan_transition(PlanState.VALIDATED, PlanState.APPROVAL_PENDING)
    # APPROVAL_PENDING -> APPROVED allowed
    assert_valid_plan_transition(PlanState.APPROVAL_PENDING, PlanState.APPROVED)
    # APPROVED -> ACTIVE is structurally allowed for future explicit operation
    assert_valid_plan_transition(PlanState.APPROVED, PlanState.ACTIVE)
    # ACTIVE -> COMPLETED allowed
    assert_valid_plan_transition(PlanState.ACTIVE, PlanState.COMPLETED)
    # Rejection: APPROVAL_PENDING -> VALIDATED allowed
    assert_valid_plan_transition(PlanState.APPROVAL_PENDING, PlanState.VALIDATED)
    # Invalidation: VALIDATED -> GENERATED allowed
    assert_valid_plan_transition(PlanState.VALIDATED, PlanState.GENERATED)


def test_plan_state_transitions_invalid() -> None:
    # DRAFT -> VALIDATED rejected
    with pytest.raises(InvalidPlanStateTransitionError) as exc_info:
        assert_valid_plan_transition(PlanState.DRAFT, PlanState.VALIDATED)
    assert exc_info.value.current_state == PlanState.DRAFT
    assert exc_info.value.target_state == PlanState.VALIDATED

    # DRAFT -> APPROVAL_PENDING rejected
    with pytest.raises(InvalidPlanStateTransitionError):
        assert_valid_plan_transition(PlanState.DRAFT, PlanState.APPROVAL_PENDING)

    # DRAFT -> APPROVED rejected
    with pytest.raises(InvalidPlanStateTransitionError):
        assert_valid_plan_transition(PlanState.DRAFT, PlanState.APPROVED)

    # GENERATED -> APPROVAL_PENDING rejected
    with pytest.raises(InvalidPlanStateTransitionError):
        assert_valid_plan_transition(PlanState.GENERATED, PlanState.APPROVAL_PENDING)

    # GENERATED -> APPROVED rejected
    with pytest.raises(InvalidPlanStateTransitionError):
        assert_valid_plan_transition(PlanState.GENERATED, PlanState.APPROVED)

    # VALIDATED -> APPROVED rejected (must go through APPROVAL_PENDING)
    with pytest.raises(InvalidPlanStateTransitionError):
        assert_valid_plan_transition(PlanState.VALIDATED, PlanState.APPROVED)

    # APPROVAL_PENDING -> ACTIVE rejected (must go through APPROVED)
    with pytest.raises(InvalidPlanStateTransitionError):
        assert_valid_plan_transition(PlanState.APPROVAL_PENDING, PlanState.ACTIVE)

    # Terminal COMPLETED cannot transition
    with pytest.raises(InvalidPlanStateTransitionError):
        assert_valid_plan_transition(PlanState.COMPLETED, PlanState.ACTIVE)

    # Terminal CANCELLED cannot transition
    with pytest.raises(InvalidPlanStateTransitionError):
        assert_valid_plan_transition(PlanState.CANCELLED, PlanState.ACTIVE)
