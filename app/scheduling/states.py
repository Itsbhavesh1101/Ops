"""Canonical schedule and job lifecycle state machine.

Enforces deterministic transitions for:
ScheduleJobStatus:
  REQUESTED -> SOLVING
  SOLVING -> FEASIBLE | INFEASIBLE | FAILED
  REQUESTED -> FAILED

ScheduleStatus:
  FEASIBLE -> REVIEW
  (Phase 4B: REVIEW -> PUBLISHED -> SUPERSEDED)

Disallowed transitions:
  REQUESTED -> FEASIBLE
  REQUESTED -> REVIEW
  SOLVING -> PUBLISHED
  INFEASIBLE -> PUBLISHED
"""

from enum import StrEnum


class InvalidStateTransitionError(ValueError):
    """Raised when an invalid lifecycle state transition is attempted."""

    def __init__(self, entity: str, current_state: str, target_state: str) -> None:
        msg = (
            f"Invalid {entity} lifecycle state transition "
            f"from '{current_state}' to '{target_state}'."
        )
        super().__init__(msg)
        self.entity = entity
        self.current_state = current_state
        self.target_state = target_state


class ScheduleJobStatus(StrEnum):
    """Lifecycle states for asynchronous solve jobs."""

    REQUESTED = "REQUESTED"
    SOLVING = "SOLVING"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"


class ScheduleLifecycleStatus(StrEnum):
    """Canonical lifecycle states for schedules."""

    DRAFT = "DRAFT"
    FEASIBLE = "FEASIBLE"
    REVIEW = "REVIEW"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"


# Allowed transitions for ScheduleJob
ALLOWED_JOB_TRANSITIONS: dict[ScheduleJobStatus, set[ScheduleJobStatus]] = {
    ScheduleJobStatus.REQUESTED: {
        ScheduleJobStatus.SOLVING,
        ScheduleJobStatus.FAILED,
    },
    ScheduleJobStatus.SOLVING: {
        ScheduleJobStatus.REQUESTED,  # Lease expiration / abandoned job recovery
        ScheduleJobStatus.FEASIBLE,
        ScheduleJobStatus.INFEASIBLE,
        ScheduleJobStatus.BUDGET_EXHAUSTED,
        ScheduleJobStatus.FAILED,
    },
    ScheduleJobStatus.FEASIBLE: set(),
    ScheduleJobStatus.INFEASIBLE: set(),
    ScheduleJobStatus.BUDGET_EXHAUSTED: set(),
    ScheduleJobStatus.FAILED: set(),
}

# Allowed transitions for Schedule in Phase 4B
ALLOWED_SCHEDULE_TRANSITIONS: dict[ScheduleLifecycleStatus, set[ScheduleLifecycleStatus]] = {
    ScheduleLifecycleStatus.DRAFT: {ScheduleLifecycleStatus.FEASIBLE},
    ScheduleLifecycleStatus.FEASIBLE: {ScheduleLifecycleStatus.REVIEW},
    ScheduleLifecycleStatus.REVIEW: {ScheduleLifecycleStatus.PUBLISHED},
    ScheduleLifecycleStatus.PUBLISHED: {ScheduleLifecycleStatus.SUPERSEDED},
    ScheduleLifecycleStatus.SUPERSEDED: set(),
}


def validate_job_transition(
    current: ScheduleJobStatus | str, target: ScheduleJobStatus | str
) -> ScheduleJobStatus:
    """Validate and return next job status or raise InvalidStateTransitionError."""
    curr_enum = ScheduleJobStatus(current)
    target_enum = ScheduleJobStatus(target)

    allowed = ALLOWED_JOB_TRANSITIONS.get(curr_enum, set())
    if target_enum not in allowed:
        raise InvalidStateTransitionError("ScheduleJob", curr_enum.value, target_enum.value)
    return target_enum


def validate_schedule_transition(
    current: ScheduleLifecycleStatus | str, target: ScheduleLifecycleStatus | str
) -> ScheduleLifecycleStatus:
    """Validate and return next schedule status or raise InvalidStateTransitionError."""
    curr_enum = ScheduleLifecycleStatus(current)
    target_enum = ScheduleLifecycleStatus(target)

    allowed = ALLOWED_SCHEDULE_TRANSITIONS.get(curr_enum, set())
    if target_enum not in allowed:
        raise InvalidStateTransitionError("Schedule", curr_enum.value, target_enum.value)
    return target_enum
