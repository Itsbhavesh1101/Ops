"""Unit tests for delegation policies and time-bounded actor grant evaluation."""

from datetime import UTC, datetime, timedelta

from app.workflow.approval.delegation import DefaultDelegationPolicy
from app.workflow.approval.models import DelegationGrant


def test_delegation_active_grant_authorizes():
    policy = DefaultDelegationPolicy()
    now = datetime.now(UTC)

    grant = DelegationGrant(
        tenant_id="tenant_alpha",
        delegator_id="manager_alice",
        delegatee_id="deputy_bob",
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=8),
        workflow_key=None,  # All workflows
    )
    policy.register_grant(grant)

    authorized = policy.is_delegated(
        tenant_id="tenant_alpha",
        delegator_id="manager_alice",
        delegatee_id="deputy_bob",
        workflow_key="expense_approval",
        as_of=now,
    )
    assert authorized is True


def test_delegation_expired_grant_denies():
    policy = DefaultDelegationPolicy()
    now = datetime.now(UTC)

    grant = DelegationGrant(
        tenant_id="tenant_alpha",
        delegator_id="manager_alice",
        delegatee_id="deputy_bob",
        valid_from=now - timedelta(days=2),
        valid_until=now - timedelta(hours=1),  # Expired
    )
    policy.register_grant(grant)

    authorized = policy.is_delegated(
        tenant_id="tenant_alpha",
        delegator_id="manager_alice",
        delegatee_id="deputy_bob",
        workflow_key="expense_approval",
        as_of=now,
    )
    assert authorized is False


def test_delegation_cross_tenant_grant_strictly_denied():
    policy = DefaultDelegationPolicy()
    now = datetime.now(UTC)

    grant = DelegationGrant(
        tenant_id="tenant_alpha",
        delegator_id="manager_alice",
        delegatee_id="deputy_bob",
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=8),
    )
    policy.register_grant(grant)

    # Attempting to use grant in tenant_beta
    authorized = policy.is_delegated(
        tenant_id="tenant_beta",
        delegator_id="manager_alice",
        delegatee_id="deputy_bob",
        workflow_key="expense_approval",
        as_of=now,
    )
    assert authorized is False


def test_delegation_workflow_scope_restriction():
    policy = DefaultDelegationPolicy()
    now = datetime.now(UTC)

    grant = DelegationGrant(
        tenant_id="tenant_alpha",
        delegator_id="manager_alice",
        delegatee_id="deputy_bob",
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=8),
        workflow_key="scoped_proc",  # Only scoped_proc
    )
    policy.register_grant(grant)

    # Allowed on scoped_proc
    assert (
        policy.is_delegated(
            tenant_id="tenant_alpha",
            delegator_id="manager_alice",
            delegatee_id="deputy_bob",
            workflow_key="scoped_proc",
            as_of=now,
        )
        is True
    )

    # Denied on other proc
    assert (
        policy.is_delegated(
            tenant_id="tenant_alpha",
            delegator_id="manager_alice",
            delegatee_id="deputy_bob",
            workflow_key="unauthorized_proc",
            as_of=now,
        )
        is False
    )


def test_delegation_revocation():
    policy = DefaultDelegationPolicy()
    now = datetime.now(UTC)

    grant = DelegationGrant(
        tenant_id="tenant_alpha",
        delegator_id="manager_alice",
        delegatee_id="deputy_bob",
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=8),
    )
    policy.register_grant(grant)
    assert (
        policy.is_delegated(
            tenant_id="tenant_alpha",
            delegator_id="manager_alice",
            delegatee_id="deputy_bob",
            as_of=now,
        )
        is True
    )

    policy.revoke_grant(grant.grant_id)
    assert (
        policy.is_delegated(
            tenant_id="tenant_alpha",
            delegator_id="manager_alice",
            delegatee_id="deputy_bob",
            as_of=now,
        )
        is False
    )
