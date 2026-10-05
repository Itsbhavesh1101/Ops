"""app.task_intelligence.schemas package — Canonical planning schemas.

Re-exports typed schemas for goals, plans, tasks, dependencies, milestones,
and deterministic validation.
"""

from app.task_intelligence.schemas.goal import (
    GoalCreate,
    GoalResponse,
    TargetWindow,
)
from app.task_intelligence.schemas.plan import (
    VALID_PLAN_TRANSITIONS,
    DependencyDraft,
    DependencyType,
    EffortEstimateDraft,
    InvalidPlanStateTransitionError,
    MilestoneDraft,
    PlanSchema,
    PlanState,
    TaskDraft,
    TaskPriority,
    TaskProgressStatus,
    assert_valid_plan_transition,
)
from app.task_intelligence.schemas.validation import (
    ValidationIssue,
    ValidationIssueCode,
    ValidationResult,
    ValidationSeverity,
)

__all__ = [
    "VALID_PLAN_TRANSITIONS",
    "DependencyDraft",
    "DependencyType",
    "EffortEstimateDraft",
    "GoalCreate",
    "GoalResponse",
    "InvalidPlanStateTransitionError",
    "MilestoneDraft",
    "PlanSchema",
    "PlanState",
    "TargetWindow",
    "TaskDraft",
    "TaskPriority",
    "TaskProgressStatus",
    "ValidationIssue",
    "ValidationIssueCode",
    "ValidationResult",
    "ValidationSeverity",
    "assert_valid_plan_transition",
]
