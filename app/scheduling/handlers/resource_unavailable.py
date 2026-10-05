"""Business event handler consuming canonical resource.unavailable events.

Part of Phase 4B Generic Scheduling.
Enforces:
- Canonical ConsumerEventHandler protocol implementation.
- Derives targeted repair scope from published baseline assignments.
- Participates in consumer transaction with Inbox deduplication.
- Preserves event correlation_id.
- Never directly mutates a published schedule.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.scheduling import ScheduleRecord
from app.events.schemas import EventEnvelope
from app.scheduling.repair.models import ScheduleRepairRequest
from app.scheduling.repair.policy import DefaultRepairPolicyResolver, RepairPolicyResolver
from app.scheduling.service import SchedulingService

RESOURCE_UNAVAILABLE_HANDLER: str = "IMPLEMENTED"


def _parse_iso(val: Any) -> datetime | None:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val.strip():
        return datetime.fromisoformat(val)
    return None


class ResourceUnavailableHandler:
    """Consumer handler for canonical resource.unavailable event."""

    def __init__(
        self,
        scheduling_service: SchedulingService | None = None,
        repair_policy_resolver: RepairPolicyResolver | None = None,
    ) -> None:
        self.scheduling_service = scheduling_service or SchedulingService()
        self.repair_policy_resolver = (
            repair_policy_resolver or DefaultRepairPolicyResolver()
        )

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Handle resource.unavailable event idempotently within transaction.

        Flow:
        1. Validate tenant context and resource reference from payload.
        2. Query current PUBLISHED schedule for the tenant.
        3. Resolve trusted repair policy (movement weight, profile) for tenant.
        4. Trigger targeted repair via SchedulingService.
        """
        tenant_id = envelope.tenant_id
        if not tenant_id:
            return

        payload = envelope.payload
        resource_id_raw = payload.get("resource_id")
        if not resource_id_raw:
            return

        resource_key = str(resource_id_raw)
        start_at = _parse_iso(payload.get("start_at"))
        end_at = _parse_iso(payload.get("end_at"))
        reason = payload.get("reason", "resource.unavailable disruption")

        # Find latest PUBLISHED schedule for this tenant
        stmt = (
            select(ScheduleRecord)
            .where(
                ScheduleRecord.tenant_id == tenant_id,
                ScheduleRecord.status == "PUBLISHED",
            )
            .order_by(ScheduleRecord.version.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        baseline = res.scalar_one_or_none()
        if not baseline:
            # No published schedule currently active for this tenant
            return

        # Resolve trusted tenant repair policy
        policy = await self.repair_policy_resolver.resolve_repair_policy(
            tenant_id=tenant_id,
            plan_id=getattr(baseline, "plan_id", None),
            session=session,
        )
        if not policy or policy.movement_weight is None or policy.movement_weight <= 0:
            audit_repo = AuditRepository(session)
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="schedule.repair_policy_unresolved",
                    resource_type="schedule",
                    resource_id=str(baseline.id),
                    outcome=AuditEventOutcome.FAILURE,
                    correlation_id=envelope.correlation_id,
                    details={
                        "reason": (
                            "No valid repair policy configured for tenant. "
                            "Automatic repair skipped."
                        ),
                        "disrupted_resource_key": resource_key,
                    },
                )
            )
            return

        repair_request = ScheduleRepairRequest(
            disrupted_resource_ids=[resource_key],
            disrupted_interval_start=start_at,
            disrupted_interval_end=end_at,
            reason=f"resource.unavailable: {reason}",
            movement_weight=policy.movement_weight,
            profile=policy.solver_profile,
        )

        await self.scheduling_service.repair_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=baseline.id,
            request=repair_request,
            correlation_id=envelope.correlation_id,
        )
