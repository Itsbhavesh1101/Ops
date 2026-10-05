"""Unit tests for Phase 4B schedule lifecycle transitions and publish policy.

Covers:
- Lifecycle state transitions: REVIEW -> PUBLISHED, PUBLISHED -> SUPERSEDED.
- Disallowed transitions: DRAFT -> PUBLISHED, FEASIBLE -> PUBLISHED, INFEASIBLE -> PUBLISHED.
- SchedulePublishPolicy fail-closed behavior on missing principal, tenant mismatch, bad action.
- validate_publish_preconditions structural checks.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db.models.scheduling import ScheduleAssignmentRecord, ScheduleRecord
from app.scheduling.policy import (
    DefaultSchedulePublishPolicy,
    PublishPreconditionFailedError,
    validate_publish_preconditions,
)
from app.scheduling.states import (
    InvalidStateTransitionError,
    ScheduleLifecycleStatus,
    validate_schedule_transition,
)


def test_allowed_schedule_transitions_phase_4b() -> None:
    """Test valid Phase 4B schedule lifecycle transitions."""
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


def test_disallowed_schedule_transitions() -> None:
    """Test disallowed lifecycle transitions fail closed."""
    # Cannot publish directly from DRAFT
    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.DRAFT, ScheduleLifecycleStatus.PUBLISHED
        )

    # Cannot publish directly from FEASIBLE without review
    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.FEASIBLE, ScheduleLifecycleStatus.PUBLISHED
        )

    # Cannot republish an already PUBLISHED schedule
    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.PUBLISHED, ScheduleLifecycleStatus.PUBLISHED
        )

    # Cannot publish a SUPERSEDED schedule
    with pytest.raises(InvalidStateTransitionError):
        validate_schedule_transition(
            ScheduleLifecycleStatus.SUPERSEDED, ScheduleLifecycleStatus.PUBLISHED
        )


def test_schedule_publish_policy_fail_closed() -> None:
    """Test provider-neutral SchedulePublishPolicy rules."""
    policy = DefaultSchedulePublishPolicy()

    sched = ScheduleRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        schedule_key="default",
        version=1,
        status="REVIEW",
        source_job_id=uuid4(),
        input_snapshot_hash="hash-123",
    )

    # 1. Missing principal fails closed
    assert not policy.authorize_publish(
        principal=None,
        tenant_id="tenant-alpha",
        schedule=sched,
        action="publish",
    )
    assert not policy.authorize_publish(
        principal="  ",
        tenant_id="tenant-alpha",
        schedule=sched,
        action="publish",
    )

    # 2. Cross-tenant mismatch fails closed
    assert not policy.authorize_publish(
        principal="user-1",
        tenant_id="tenant-beta",
        schedule=sched,
        action="publish",
    )

    # 3. Invalid action fails closed
    assert not policy.authorize_publish(
        principal="user-1",
        tenant_id="tenant-alpha",
        schedule=sched,
        action="invalid_action",
    )

    # 4. Ungranted principal fails closed by default
    assert not policy.authorize_publish(
        principal="user-1",
        tenant_id="tenant-alpha",
        schedule=sched,
        action="publish",
    )

    # 5. Explicitly granted principal succeeds
    granted_policy = DefaultSchedulePublishPolicy(granted_principals={"user-1"})
    assert granted_policy.authorize_publish(
        principal="user-1",
        tenant_id="tenant-alpha",
        schedule=sched,
        action="publish",
    )

    # 6. Granted permission capability succeeds
    perm_policy = DefaultSchedulePublishPolicy(granted_permissions={"schedule:publish"})
    assert perm_policy.authorize_publish(
        principal="user-2",
        tenant_id="tenant-alpha",
        schedule=sched,
        action="publish",
        permissions={"schedule:publish"},
    )
    assert not perm_policy.authorize_publish(
        principal="user-2",
        tenant_id="tenant-alpha",
        schedule=sched,
        action="publish",
        permissions={"schedule:view"},
    )


def test_validate_publish_preconditions() -> None:
    """Test precondition validation for candidate publication."""
    now = datetime.now(UTC)

    # 1. Precondition fails if status is not REVIEW
    sched_feasible = ScheduleRecord(
        id=uuid4(),
        tenant_id="tenant-1",
        schedule_key="default",
        version=1,
        status="FEASIBLE",
        source_job_id=uuid4(),
        input_snapshot_hash="hash-1",
        score_components={"hard_constraint_violations": 0},
    )
    with pytest.raises(
        PublishPreconditionFailedError,
        match="cannot be published from status 'FEASIBLE'",
    ):
        validate_publish_preconditions(sched_feasible)

    # 2. Precondition fails if assignments are empty
    sched_review = ScheduleRecord(
        id=uuid4(),
        tenant_id="tenant-1",
        schedule_key="default",
        version=1,
        status="REVIEW",
        source_job_id=uuid4(),
        input_snapshot_hash="hash-1",
        score_components={"hard_constraint_violations": 0},
    )
    sched_review.assignments = []
    with pytest.raises(PublishPreconditionFailedError, match="without assignments"):
        validate_publish_preconditions(sched_review)

    # 3. Precondition fails if hard violations > 0
    asgn = ScheduleAssignmentRecord(
        id=uuid4(),
        tenant_id="tenant-1",
        schedule_id=sched_review.id,
        activity_id="act-1",
        resource_id="res-1",
        start_at=now,
        end_at=now,
    )
    sched_review.assignments = [asgn]
    sched_review.score_components = {"hard_constraint_violations": 1}
    with pytest.raises(PublishPreconditionFailedError, match="hard constraint violations"):
        validate_publish_preconditions(sched_review)

    # 4. Precondition fails if input snapshot hash missing
    sched_review.score_components = {"hard_constraint_violations": 0}
    sched_review.input_snapshot_hash = ""
    with pytest.raises(PublishPreconditionFailedError, match="input snapshot hash"):
        validate_publish_preconditions(sched_review)

    # 5. Passes when all preconditions met
    sched_review.input_snapshot_hash = "valid-hash"
    validate_publish_preconditions(sched_review)


def test_publish_policy_module_isolation() -> None:
    """Verify production app.scheduling.policy contains NO test fixture allow/deny bypass."""
    import app.scheduling.policy as prod_policy

    assert not hasattr(prod_policy, "AllowAllPublishPolicy")
    assert not hasattr(prod_policy, "DenyAllPublishPolicy")

    from tests.fixtures.scheduling_policies import AllowAllPublishPolicy, DenyAllPublishPolicy

    sched = ScheduleRecord(
        id=uuid4(),
        tenant_id="tenant-alpha",
        schedule_key="default",
        version=1,
        status="REVIEW",
        source_job_id=uuid4(),
        input_snapshot_hash="hash-123",
    )
    allow_policy = AllowAllPublishPolicy()
    deny_policy = DenyAllPublishPolicy()
    assert allow_policy.authorize_publish(
        principal="planner-1", tenant_id="tenant-alpha", schedule=sched
    )
    assert not allow_policy.authorize_publish(
        principal="planner-1", tenant_id="tenant-beta", schedule=sched
    )
    assert not deny_policy.authorize_publish(
        principal="planner-1", tenant_id="tenant-alpha", schedule=sched
    )

