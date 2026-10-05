"""Workflow Task Action Eligibility and Authorization Boundary (Phase 3A).

Enforces:
1. Multi-tenant isolation: principal.tenant_id must match task.tenant_id strictly.
2. Trusted principal context: actor identity originates from AuthenticatedPrincipal,
   never untrusted client payloads.
3. Provider-neutral authorization policy: tasks evaluate assignee, candidate roles,
   and action-specific grants.
4. Pre-execution authorization: unauthorized actions are blocked BEFORE calling Flowable.
5. Fail-closed default: human tasks without explicit assignee, candidate roles, or
   injected grants strictly fail closed (DENIED).
"""

from typing import Protocol, runtime_checkable

from app.core.auth.models import AuthenticatedPrincipal
from app.db.models.workflow import WorkflowTaskMappingRecord

TASK_ELIGIBILITY_DEFAULT: str = "FAIL_CLOSED"


class WorkflowAuthorizationError(Exception):
    """Base exception for workflow authorization failures."""


class WorkflowTaskUnauthorizedError(WorkflowAuthorizationError):
    """Raised when an actor attempts an action on a task they are not eligible to perform."""

    def __init__(self, principal_id: str, task_id: str, reason: str) -> None:
        msg = f"Principal '{principal_id}' is unauthorized for task '{task_id}': {reason}"
        super().__init__(msg)
        self.principal_id = principal_id
        self.task_id = task_id
        self.reason = reason


@runtime_checkable
class TaskEligibilityPolicyProtocol(Protocol):
    """Protocol for evaluating whether an actor is authorized to act on a workflow task."""

    def is_eligible(
        self,
        principal: AuthenticatedPrincipal,
        task: WorkflowTaskMappingRecord,
        action: str,
    ) -> bool:
        """Evaluate actor eligibility for a specific task and action."""
        ...


class DefaultTaskEligibilityPolicy(TaskEligibilityPolicyProtocol):
    """Fail-closed role, assignment, and action-based eligibility policy.

    Enforces:
    - Multi-tenant boundary: principal and task must belong to the exact same tenant.
    - Action validation: requested action must be in supported allowed_actions.
    - Action grants: optional fine-grained role grants required for specific actions.
    - Optional injected privileged roles: evaluated only if explicitly injected into constructor.
    - Direct assignee match: principal matches task.assignee.
    - Candidate roles: principal roles intersect with task.candidate_roles.
    - Fail-closed default: tasks with neither assignee, candidate roles, nor injected grant
      strictly fail closed (DENIED).
    - Zero universal hardcoded role names (no 'admin', 'superadmin', 'dean', 'chair', etc.).
    """

    def __init__(
        self,
        privileged_roles: frozenset[str] | None = None,
        allowed_actions: frozenset[str] | None = None,
        action_grants: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self._privileged_roles = privileged_roles or frozenset()
        self._allowed_actions = allowed_actions or frozenset({"APPROVE", "REJECT", "COMPLETE"})
        self._action_grants = action_grants or {}

    def is_eligible(
        self,
        principal: AuthenticatedPrincipal,
        task: WorkflowTaskMappingRecord,
        action: str,
    ) -> bool:
        """Verify actor eligibility against fail-closed rules."""
        # 1. Tenant boundary: principal and task must belong to the exact same tenant
        if principal.tenant_id != task.tenant_id:
            return False

        # 2. Action validation: requested action must be permitted
        norm_action = action.upper()
        if norm_action not in self._allowed_actions:
            return False

        # 3. Action-specific role restriction (if configured for this exact action)
        if norm_action in self._action_grants:
            required_roles = self._action_grants[norm_action]
            if not any(principal.has_role(r) for r in required_roles):
                return False

        # 4. Injected privileged policy override (if explicitly configured for this instance)
        if self._privileged_roles and any(principal.has_role(r) for r in self._privileged_roles):
            return True

        # 5. Direct Assignee Match
        if task.assignee:
            return principal.principal_id == task.assignee

        # 6. Candidate Roles / Groups Match
        if task.candidate_roles:
            return any(principal.has_role(r) for r in task.candidate_roles)

        # 7. Fail-closed default: absence of assignment or candidate group metadata is DENIED
        return False
