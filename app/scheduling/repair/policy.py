"""Repair policy resolution contract and implementations for automated disruption rescheduling.

Enforces:
- Trusted injected/configured policy resolution per tenant/context.
- No hardcoded movement weights in handlers or workers.
- Fail-closed / audit reporting when repair policy is unconfigured.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.scheduling.profiles.models import SolverProfile


@dataclass(frozen=True)
class TenantRepairPolicy:
    """Resolved repair configuration for a tenant/plan."""

    movement_weight: float
    solver_profile: SolverProfile | None = None
    solver_profile_version: int | None = None


@runtime_checkable
class RepairPolicyResolver(Protocol):
    """Protocol for resolving versioned repair policy configuration for a tenant."""

    async def resolve_repair_policy(
        self,
        tenant_id: str,
        plan_id: UUID | None = None,
        session: AsyncSession | None = None,
    ) -> TenantRepairPolicy | None:
        """Resolve repair policy for the tenant. Returns None if unconfigured."""
        ...


class DefaultRepairPolicyResolver:
    """Configurable repair policy resolver storing tenant-specific policies."""

    def __init__(
        self, tenant_policies: dict[str, TenantRepairPolicy] | None = None
    ) -> None:
        self._tenant_policies: dict[str, TenantRepairPolicy] = tenant_policies or {}

    def set_tenant_policy(self, tenant_id: str, policy: TenantRepairPolicy) -> None:
        """Configure a tenant's repair policy."""
        self._tenant_policies[tenant_id] = policy

    async def resolve_repair_policy(
        self,
        tenant_id: str,
        plan_id: UUID | None = None,
        session: AsyncSession | None = None,
    ) -> TenantRepairPolicy | None:
        return self._tenant_policies.get(tenant_id)
