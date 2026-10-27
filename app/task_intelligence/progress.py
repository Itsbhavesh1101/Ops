"""Canonical task progress tracking contracts (FR-TI-08).

Defines validated task progress transitions, percentage completion bounds,
actual duration, and blocker declarations owned strictly by E-7.
"""

from pydantic import BaseModel, Field, model_validator

from app.task_intelligence.schemas import TaskProgressStatus

VALID_TASK_PROGRESS_TRANSITIONS: dict[TaskProgressStatus, set[TaskProgressStatus]] = {
    TaskProgressStatus.TODO: {
        TaskProgressStatus.IN_PROGRESS,
        TaskProgressStatus.BLOCKED,
        TaskProgressStatus.COMPLETED,  # Can directly complete instant tasks
    },
    TaskProgressStatus.IN_PROGRESS: {
        TaskProgressStatus.BLOCKED,
        TaskProgressStatus.COMPLETED,
        TaskProgressStatus.TODO,  # Reverted if cancelled before work done
    },
    TaskProgressStatus.BLOCKED: {
        TaskProgressStatus.IN_PROGRESS,
        TaskProgressStatus.TODO,
    },
    TaskProgressStatus.COMPLETED: {
        # Reopening a completed task requires explicit move back to IN_PROGRESS
        TaskProgressStatus.IN_PROGRESS,
    },
}


class InvalidTaskProgressTransitionError(ValueError):
    """Raised when an illegal task progress transition is attempted."""

    def __init__(self, current: TaskProgressStatus, target: TaskProgressStatus):
        super().__init__(f"Illegal task progress transition from '{current}' to '{target}'.")
        self.current = current
        self.target = target


class TaskProgressUpdate(BaseModel):
    """Payload for updating canonical task progress."""

    status: TaskProgressStatus | None = Field(
        default=None,
        description="New progress status.",
    )
    progress_percentage: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Completion percentage between 0 and 100.",
    )
    actual_duration_minutes: int | None = Field(
        default=None,
        ge=0,
        description="Recorded actual execution duration in minutes.",
    )
    blocked_reason: str | None = Field(
        default=None,
        description="Reason if status is BLOCKED.",
    )
    completion_evidence: str | None = Field(
        default=None,
        description="Verifiable completion evidence or artifact reference.",
    )

    @model_validator(mode="after")
    def validate_status_and_progress(self) -> "TaskProgressUpdate":
        if self.status == TaskProgressStatus.COMPLETED and self.progress_percentage is not None:
            if self.progress_percentage != 100:
                raise ValueError("Completed tasks must have 100% progress_percentage.")
        if self.status == TaskProgressStatus.BLOCKED and not self.blocked_reason:
            raise ValueError("Blocked tasks must include a non-empty blocked_reason.")
        return self


def assert_valid_task_progress_transition(
    current_status: TaskProgressStatus,
    target_status: TaskProgressStatus,
) -> None:
    """Validate that task status transition is permitted."""
    allowed = VALID_TASK_PROGRESS_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise InvalidTaskProgressTransitionError(current_status, target_status)
