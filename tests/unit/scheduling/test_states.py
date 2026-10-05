"""Unit tests for scheduling and job lifecycle state machine."""

import pytest

from app.scheduling.states import (
    InvalidStateTransitionError,
    ScheduleJobStatus,
    ScheduleLifecycleStatus,
    validate_job_transition,
    validate_schedule_transition,
)


def test_valid_job_transitions() -> None:
    """Verify all valid job transitions succeed."""
    assert (
        validate_job_transition(ScheduleJobStatus.REQUESTED, ScheduleJobStatus.SOLVING)
        == ScheduleJobStatus.SOLVING
    )
    assert (
        validate_job_transition(ScheduleJobStatus.SOLVING, ScheduleJobStatus.FEASIBLE)
        == ScheduleJobStatus.FEASIBLE
    )
    assert (
        validate_job_transition(ScheduleJobStatus.SOLVING, ScheduleJobStatus.INFEASIBLE)
        == ScheduleJobStatus.INFEASIBLE
    )
    assert (
        validate_job_transition(ScheduleJobStatus.SOLVING, ScheduleJobStatus.FAILED)
        == ScheduleJobStatus.FAILED
    )
    assert (
        validate_job_transition(ScheduleJobStatus.REQUESTED, ScheduleJobStatus.FAILED)
        == ScheduleJobStatus.FAILED
    )


def test_invalid_job_transitions() -> None:
    """Verify illegal job transitions raise InvalidStateTransitionError."""
    # Cannot jump REQUESTED -> FEASIBLE
    with pytest.raises(InvalidStateTransitionError) as exc:
        validate_job_transition(ScheduleJobStatus.REQUESTED, ScheduleJobStatus.FEASIBLE)
    assert "Invalid ScheduleJob lifecycle state transition" in str(exc.value)

    # Cannot jump REQUESTED -> INFEASIBLE
    with pytest.raises(InvalidStateTransitionError):
        validate_job_transition(ScheduleJobStatus.REQUESTED, ScheduleJobStatus.INFEASIBLE)

    # Terminal states cannot transition further
    with pytest.raises(InvalidStateTransitionError):
        validate_job_transition(ScheduleJobStatus.FEASIBLE, ScheduleJobStatus.SOLVING)

    with pytest.raises(InvalidStateTransitionError):
        validate_job_transition(ScheduleJobStatus.INFEASIBLE, ScheduleJobStatus.SOLVING)

    with pytest.raises(InvalidStateTransitionError):
        validate_job_transition(ScheduleJobStatus.FAILED, ScheduleJobStatus.REQUESTED)


def test_schedule_lifecycle_transitions() -> None:
    """Verify schedule lifecycle transitions: FEASIBLE -> REVIEW -> PUBLISHED -> SUPERSEDED."""
    assert (
        validate_schedule_transition(
            ScheduleLifecycleStatus.FEASIBLE, ScheduleLifecycleStatus.REVIEW
        )
        == ScheduleLifecycleStatus.REVIEW
    )
    assert (
        validate_schedule_transition(
            ScheduleLifecycleStatus.REVIEW, ScheduleLifecycleStatus.PUBLISHED
        )
        == ScheduleLifecycleStatus.PUBLISHED
    )
    assert (
        validate_schedule_transition(
            ScheduleLifecycleStatus.PUBLISHED, ScheduleLifecycleStatus.SUPERSEDED
        )
        == ScheduleLifecycleStatus.SUPERSEDED
    )

    # Illegal transitions
    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.FEASIBLE, ScheduleLifecycleStatus.PUBLISHED
        )

    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.PUBLISHED, ScheduleLifecycleStatus.REVIEW
        )

    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.SUPERSEDED, ScheduleLifecycleStatus.PUBLISHED
        )

