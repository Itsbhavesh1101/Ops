"""Unit tests for workflow task actor eligibility and authorization enforcement.

Covers:
- Strict tenant boundary verification
- Assignee exact match and mismatch
- Candidate role eligibility and mismatch
- Fail-closed behavior for unassigned tasks with no metadata
- Action-specific eligibility and unsupported action denial
- Explicitly injected privileged role override
- Removal of universal magic admin override
- Unauthorized error properties
"""

import uuid
from datetime import UTC, datetime

from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
)
from app.workflow.authorization import (
    DefaultTaskEligibilityPolicy,
    WorkflowTaskUnauthorizedError,
)
from app.workflow.models import (
    WorkflowTaskMappingRecord,
)
from app.workflow.schemas import (
    WorkflowTaskStatus,
)


def _make_task(
    tenant_id: str = "tenant-alpha",
    assignee_id: str | None = None,
    candidate_role: str | None = None,
) -> WorkflowTaskMappingRecord:
    return WorkflowTaskMappingRecord(
        id=uuid.uuid4(),
        workflow_instance_id=uuid.uuid4(),
        tenant_id=tenant_id,
        engine_task_id="flowable-task-123",
        task_definition_key="approval_step",
        task_name="Approval Step",
        assignee=assignee_id,
        candidate_roles=[candidate_role] if candidate_role else [],
        status=WorkflowTaskStatus.PENDING.value,
        variables={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_assignee_matches_principal():
    policy = DefaultTaskEligibilityPolicy()
    principal = AuthenticatedPrincipal(
        principal_id="user-1",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"analyst"}),
    )
    task = _make_task(tenant_id="tenant-alpha", assignee_id="user-1")

    assert policy.is_eligible(principal, task, "complete") is True


def test_assignee_mismatch_rejected():
    policy = DefaultTaskEligibilityPolicy()
    principal = AuthenticatedPrincipal(
        principal_id="user-2",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"analyst"}),
    )
    task = _make_task(tenant_id="tenant-alpha", assignee_id="user-1")

    assert policy.is_eligible(principal, task, "complete") is False


def test_candidate_role_matches_principal_role():
    policy = DefaultTaskEligibilityPolicy()
    principal = AuthenticatedPrincipal(
        principal_id="user-3",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"finance_approver", "user"}),
    )
    task = _make_task(tenant_id="tenant-alpha", candidate_role="finance_approver")

    assert policy.is_eligible(principal, task, "approve") is True


def test_candidate_role_mismatch_rejected():
    policy = DefaultTaskEligibilityPolicy()
    principal = AuthenticatedPrincipal(
        principal_id="user-4",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"hr_approver"}),
    )
    task = _make_task(tenant_id="tenant-alpha", candidate_role="finance_approver")

    assert policy.is_eligible(principal, task, "approve") is False


def test_tenant_mismatch_strictly_rejected():
    policy = DefaultTaskEligibilityPolicy()
    principal = AuthenticatedPrincipal(
        principal_id="user-1",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-beta",
        roles=frozenset({"admin"}),
    )
    task = _make_task(tenant_id="tenant-alpha", assignee_id="user-1")

    assert policy.is_eligible(principal, task, "complete") is False


def test_unassigned_task_without_metadata_fails_closed():
    """Verify that a task with no assignee and no candidate roles strictly fails closed."""
    policy = DefaultTaskEligibilityPolicy()
    principal = AuthenticatedPrincipal(
        principal_id="user-any",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"employee"}),
    )
    # Task with no assignee and no candidate roles
    task = _make_task(tenant_id="tenant-alpha", assignee_id=None, candidate_role=None)

    assert policy.is_eligible(principal, task, "complete") is False


def test_unsupported_action_denied():
    """Verify that actions outside the allowed action set are strictly denied."""
    policy = DefaultTaskEligibilityPolicy(allowed_actions=frozenset({"APPROVE", "REJECT"}))
    principal = AuthenticatedPrincipal(
        principal_id="user-1",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"analyst"}),
    )
    task = _make_task(tenant_id="tenant-alpha", assignee_id="user-1")

    # COMPLETE is not in allowed_actions
    assert policy.is_eligible(principal, task, "COMPLETE") is False
    # DELEGATE is not in allowed_actions
    assert policy.is_eligible(principal, task, "DELEGATE") is False
    # APPROVE is allowed
    assert policy.is_eligible(principal, task, "APPROVE") is True


def test_action_specific_role_grant_enforcement():
    """Verify that fine-grained action grants require the exact granted role."""
    policy = DefaultTaskEligibilityPolicy(
        action_grants={"APPROVE": frozenset({"executive_signer"})},
    )
    regular_user = AuthenticatedPrincipal(
        principal_id="user-regular",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"finance_team"}),
    )
    exec_user = AuthenticatedPrincipal(
        principal_id="user-exec",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"finance_team", "executive_signer"}),
    )
    task = _make_task(tenant_id="tenant-alpha", candidate_role="finance_team")

    # Regular user has candidate_role but lacks executive_signer for APPROVE
    assert policy.is_eligible(regular_user, task, "APPROVE") is False
    # Executive user has executive_signer
    assert policy.is_eligible(exec_user, task, "APPROVE") is True


def test_admin_role_without_injected_policy_is_not_privileged():
    """Verify that 'admin' is NOT a magic universal override unless explicitly injected."""
    policy = DefaultTaskEligibilityPolicy()
    principal = AuthenticatedPrincipal(
        principal_id="admin-user",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"admin"}),
    )
    task = _make_task(
        tenant_id="tenant-alpha",
        assignee_id="someone-else",
        candidate_role="special_team",
    )

    # In Phase 3A generic policy, 'admin' has NO magic power by default!
    assert policy.is_eligible(principal, task, "reject") is False


def test_injected_privileged_role_override_within_tenant():
    """Verify explicitly injected privileged roles grant override within the tenant."""
    policy = DefaultTaskEligibilityPolicy(privileged_roles=frozenset({"fixture_supervisor"}))
    principal = AuthenticatedPrincipal(
        principal_id="supervisor-user",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id="tenant-alpha",
        roles=frozenset({"fixture_supervisor"}),
    )
    task = _make_task(
        tenant_id="tenant-alpha",
        assignee_id="someone-else",
        candidate_role="special_team",
    )

    assert policy.is_eligible(principal, task, "reject") is True


def test_unauthorized_error_properties():
    err = WorkflowTaskUnauthorizedError(
        task_id="task-100",
        principal_id="user-x",
        reason="Actor not eligible for role finance_approver",
    )
    assert err.task_id == "task-100"
    assert err.principal_id == "user-x"
    assert "Principal 'user-x' is unauthorized" in str(err)
