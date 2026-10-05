"""Deterministic lifecycle status and transition policy for asynchronous jobs.

Part of Phase 1 Foundation Task 1I.4A.
Provides:
- JobStatus: canonical StrEnum (PENDING, RUNNING, SUCCEEDED, FAILED).
- is_terminal property: exactly SUCCEEDED and FAILED are terminal.
- can_transition_job_status: pure transition predicate.
- validate_job_status_transition: pure validation raising InvalidJobStatusTransitionError.
- InvalidJobStatusTransitionError: provider-neutral transition rejection exception.

Invariants & Scope Boundaries:
- Exactly four statuses: zero cancelled, retrying, paused, queued, or expired states.
- Allowed transitions:
    PENDING -> RUNNING
    PENDING -> FAILED
    RUNNING -> SUCCEEDED
    RUNNING -> FAILED
- Forbidden transitions:
    PENDING -> SUCCEEDED (jobs cannot bypass active execution)
    RUNNING -> PENDING (jobs cannot revert to pending)
    SUCCEEDED -> * (terminal states are immutable)
    FAILED -> * (terminal states are immutable)
    SAME -> SAME (status transitions require a change of state)
- Pure functions: zero database queries, zero I/O, zero HTTP coupling.
"""

from enum import StrEnum


class JobStatus(StrEnum):
    """Canonical lifecycle statuses for asynchronous jobs.

    Invariants:
    - PENDING: Job record exists but protected long-running work has not entered active execution.
    - RUNNING: Long-running work is actively executing.
    - SUCCEEDED: Work reached successful terminal completion.
    - FAILED: Work reached terminal failure.
    - Exactly 4 statuses; zero cancelled, retrying, paused, or queued states.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Return True if status represents an immutable terminal state."""
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED)


# Frozen allowed lifecycle transitions
ALLOWED_TRANSITIONS: frozenset[tuple[JobStatus, JobStatus]] = frozenset(
    {
        (JobStatus.PENDING, JobStatus.RUNNING),
        (JobStatus.PENDING, JobStatus.FAILED),
        (JobStatus.RUNNING, JobStatus.SUCCEEDED),
        (JobStatus.RUNNING, JobStatus.FAILED),
    }
)


def can_transition_job_status(current: JobStatus, target: JobStatus) -> bool:
    """Determine whether a job status transition is valid according to frozen policy.

    Invariants:
    - Valid transitions: PENDING -> RUNNING, PENDING -> FAILED,
      RUNNING -> SUCCEEDED, RUNNING -> FAILED.
    - Same-state transitions (e.g. PENDING -> PENDING) return False.
    - Terminal states (SUCCEEDED, FAILED) have zero outbound transitions.
    - Pure function; zero database access or I/O.
    """
    if not isinstance(current, JobStatus) or not isinstance(target, JobStatus):
        return False
    if current == target:
        return False
    return (current, target) in ALLOWED_TRANSITIONS


class InvalidJobStatusTransitionError(Exception):
    """Raised when an invalid job status transition is attempted.

    Invariants:
    - Safely exposes current and target status.
    - Never exposes tenant IDs, payloads, tracebacks, HTTP statuses, or database state.
    """

    def __init__(self, *, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Cannot transition job status from '{current.value}' to '{target.value}'."
        )


def validate_job_status_transition(current: JobStatus, target: JobStatus) -> None:
    """Validate a job status transition, raising InvalidJobStatusTransitionError if invalid."""
    if not isinstance(current, JobStatus):
        raise TypeError(f"current must be a JobStatus instance, got '{type(current).__name__}'")
    if not isinstance(target, JobStatus):
        raise TypeError(f"target must be a JobStatus instance, got '{type(target).__name__}'")
    if not can_transition_job_status(current, target):
        raise InvalidJobStatusTransitionError(current=current, target=target)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "InvalidJobStatusTransitionError",
    "JobStatus",
    "can_transition_job_status",
    "validate_job_status_transition",
]
