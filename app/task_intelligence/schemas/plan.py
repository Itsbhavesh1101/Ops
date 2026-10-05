"""Plan, task, dependency, and milestone schemas for Task Intelligence.

Defines Pydantic schemas for the non-authoritative planning domain,
including PlanSchema, TaskDraft, DependencyDraft, MilestoneDraft,
and the frozen plan lifecycle state machine.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PlanState(StrEnum):
    """Frozen canonical lifecycle for plans.

    States:
    DRAFT -> GENERATED -> VALIDATED -> APPROVAL_PENDING -> APPROVED
    Later phases: ACTIVE -> COMPLETED / CANCELLED.
    """

    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    VALIDATED = "VALIDATED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


# Explicit valid state transitions
VALID_PLAN_TRANSITIONS: dict[PlanState, set[PlanState]] = {
    PlanState.DRAFT: {PlanState.GENERATED, PlanState.CANCELLED},
    PlanState.GENERATED: {PlanState.VALIDATED, PlanState.GENERATED, PlanState.CANCELLED},
    PlanState.VALIDATED: {
        PlanState.APPROVAL_PENDING,
        PlanState.GENERATED,  # Invalidation back to generated for regeneration
        PlanState.CANCELLED,
    },
    PlanState.APPROVAL_PENDING: {
        PlanState.APPROVED,
        PlanState.VALIDATED,  # Rejection back to validated for review/rework
        PlanState.CANCELLED,
    },
    PlanState.APPROVED: {
        # APPROVED -> ACTIVE is deferred in Phase 2A (downstream workflow not active)
        PlanState.ACTIVE,
        PlanState.CANCELLED,
    },
    PlanState.ACTIVE: {PlanState.COMPLETED, PlanState.CANCELLED},
    PlanState.COMPLETED: set(),  # Terminal state
    PlanState.CANCELLED: set(),  # Terminal state
}


class InvalidPlanStateTransitionError(ValueError):
    """Raised when an illegal plan state transition is attempted."""

    def __init__(self, current_state: PlanState, target_state: PlanState):
        super().__init__(
            f"Illegal plan state transition from '{current_state}' to '{target_state}'."
        )
        self.current_state = current_state
        self.target_state = target_state


def assert_valid_plan_transition(current_state: PlanState, target_state: PlanState) -> None:
    """Assert that a state transition is legal according to the frozen policy.

    Raises:
        InvalidPlanStateTransitionError: If the transition is prohibited.
    """
    allowed = VALID_PLAN_TRANSITIONS.get(current_state, set())
    if target_state not in allowed:
        raise InvalidPlanStateTransitionError(current_state, target_state)


class TaskProgressStatus(StrEnum):
    """Canonical task progress states owned by E-7."""

    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"


class TaskPriority(StrEnum):
    """Canonical priority classifications."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DependencyType(StrEnum):
    """Standard CPM dependency relationship types."""

    FINISH_TO_START = "FINISH_TO_START"
    START_TO_START = "START_TO_START"
    FINISH_TO_FINISH = "FINISH_TO_FINISH"
    START_TO_FINISH = "START_TO_FINISH"


class EffortEstimateDraft(BaseModel):
    """Structured representation of task effort/duration estimate."""

    value: float = Field(
        ...,
        ge=0.0,
        description="Estimated duration magnitude (must be non-negative).",
    )
    unit: str = Field(
        default="minutes",
        description="Duration unit. Default normalized unit is 'minutes'.",
    )
    basis: str = Field(
        default="heuristic",
        description=(
            "Basis or source of estimate "
            "(e.g., 'human_override', 'heuristic_v1', 'historical_median')."
        ),
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence score bounded between 0.0 and 1.0.",
    )
    is_manual_override: bool = Field(
        default=False,
        description="True if manually overridden by a human planner.",
    )


class TaskDraft(BaseModel):
    """Structured task draft within a proposed WBS."""

    temporary_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Plan-local temporary identifier (e.g., 'T1', 'task-101').",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Task title / headline.",
    )
    description: str = Field(
        default="",
        description="Detailed task description or instructions.",
    )
    parent_id: str | None = Field(
        default=None,
        description="Temporary ID of parent task in WBS hierarchy, or None for root tasks.",
    )
    wbs_code: str | None = Field(
        default=None,
        description="Hierarchical WBS code (e.g., '1.1', '1.1.2').",
    )
    owner_role: str | None = Field(
        default=None,
        description=(
            "Role required to perform the task (e.g., 'INVIGILATOR', 'HEAD_OF_DEPARTMENT')."
        ),
    )
    owner_reference: str | None = Field(
        default=None,
        description="Optional opaque provider-neutral reference to designated personnel.",
    )
    estimated_duration_minutes: int = Field(
        default=60,
        ge=0,
        description=(
            "Normalized duration in minutes (>= 0). "
            "0 indicates a zero-duration milestone-like task."
        ),
    )
    effort_estimate: EffortEstimateDraft | None = Field(
        default=None,
        description="Optional detailed effort metadata (basis, confidence, override).",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Actionable verification criteria for completion.",
    )
    milestone_reference: str | None = Field(
        default=None,
        description="Plan-local temporary ID of associated milestone.",
    )
    work_category: str | None = Field(
        default=None,
        description="High-level category (e.g., 'PLANNING', 'SCHEDULING', 'EXAM_EXECUTION').",
    )
    priority: TaskPriority = Field(
        default=TaskPriority.MEDIUM,
        description="Task priority classification.",
    )


class DependencyDraft(BaseModel):
    """Predecessor-successor relationship between tasks within a plan."""

    predecessor_id: str = Field(
        ...,
        min_length=1,
        description="Temporary ID of the predecessor task.",
    )
    successor_id: str = Field(
        ...,
        min_length=1,
        description="Temporary ID of the successor task.",
    )
    dependency_type: DependencyType = Field(
        default=DependencyType.FINISH_TO_START,
        description="Dependency relationship type.",
    )
    lag_minutes: int = Field(
        default=0,
        ge=0,
        description="Lag time in minutes between tasks (>= 0).",
    )

    @model_validator(mode="after")
    def reject_self_dependency(self) -> "DependencyDraft":
        if self.predecessor_id == self.successor_id:
            raise ValueError(
                f"Self-dependency rejected: predecessor '{self.predecessor_id}' "
                f"cannot equal successor."
            )
        return self


class MilestoneDraft(BaseModel):
    """Plan milestone representing a significant delivery checkpoint."""

    temporary_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Plan-local temporary milestone ID (e.g., 'M1', 'milestone-readiness').",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Milestone name / checkpoint title.",
    )
    description: str = Field(
        default="",
        description="Description of milestone deliverables.",
    )
    target_date: datetime | None = Field(
        default=None,
        description="Target completion datetime where known.",
    )
    associated_task_ids: list[str] = Field(
        default_factory=list,
        description="List of plan-local task temporary IDs associated with this milestone.",
    )


class PlanSchema(BaseModel):
    """Canonical typed WBS proposal generated by planner or constructed deterministically.

    Non-authoritative: Must undergo deterministic validation and approval before execution.
    """

    plan_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Descriptive name of the plan.",
    )
    goal_id: UUID | None = Field(
        default=None,
        description="Canonical UUID of the parent goal if bound.",
    )
    tasks: list[TaskDraft] = Field(
        ...,
        description="List of tasks in the Work Breakdown Structure.",
    )
    dependencies: list[DependencyDraft] = Field(
        default_factory=list,
        description="Directed dependency edges between tasks.",
    )
    milestones: list[MilestoneDraft] = Field(
        default_factory=list,
        description="Delivery milestones.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-neutral planning metadata (no raw prompts or secrets).",
    )
