"""Delegation Policy and authority resolution boundary (Phase 3B).

Enforces:
- Time-bounded validity windows (valid_from <= now <= valid_until)
- Scope bounds: tenant-scoped, workflow-key-scoped, and role-scoped
- Immutable auditability: delegations are evaluated deterministically
"""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.workflow.approval.models import DelegationGrant

WORKFLOW_DELEGATION_AUTHORITY_SOURCE: str = "DEFERRED_PENDING_TENANT_POLICY_CONFIGURATION"


class DelegationPolicyProtocol(Protocol):
    """Protocol for checking active, scoped authority delegations."""

    def is_delegated(
        self,
        tenant_id: str,
        delegator_id: str,
        delegatee_id: str,
        workflow_key: str | None = None,
        role: str | None = None,
        as_of: datetime | None = None,
    ) -> bool:
        """Return True if delegatee currently holds valid delegated authority from delegator."""
        ...


class DefaultDelegationPolicy:
    """In-memory reference delegation registry evaluating active grants."""

    def __init__(self, initial_grants: list[DelegationGrant] | None = None) -> None:
        self._grants: list[DelegationGrant] = list(initial_grants or [])

    def register_grant(self, grant: DelegationGrant) -> None:
        """Register a new active delegation grant."""
        self._grants.append(grant)

    def revoke_grant(self, grant_id: UUID) -> None:
        """Revoke an active delegation grant by ID."""
        for grant in self._grants:
            if grant.id == grant_id or grant.grant_id == grant_id:
                grant.is_active = False

    def is_delegated(
        self,
        tenant_id: str,
        delegator_id: str,
        delegatee_id: str,
        workflow_key: str | None = None,
        role: str | None = None,
        as_of: datetime | None = None,
    ) -> bool:
        """Evaluate whether delegatee can act on behalf of delegator."""
        now = as_of or datetime.now(UTC)

        for grant in self._grants:
            if not grant.is_active:
                continue
            if grant.tenant_id != tenant_id:
                continue
            if grant.delegator_id != delegator_id or grant.delegatee_id != delegatee_id:
                continue
            if not (grant.valid_from <= now <= grant.valid_until):
                continue
            if grant.workflow_key and workflow_key and grant.workflow_key != workflow_key:
                continue
            if grant.workflow_key and not workflow_key:
                continue
            if (
                grant.allowed_workflow_keys
                and workflow_key
                and workflow_key not in grant.allowed_workflow_keys
            ):
                continue
            if role and grant.allowed_roles and role not in grant.allowed_roles:
                continue
            return True

        return False
