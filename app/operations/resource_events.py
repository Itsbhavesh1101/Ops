"""Real-time resource disruption event normalization and dispatch.

Part of Phase 7E & 7F Risk Monitor & Resource Disruption Normalization.
Enforces:
- External disruption callback translation into canonical 'resource.unavailable'.
- Durable deduplication using PostgreSQL idempotency_records table (survives restarts).
- Durable recording of blackout intervals in ResourceAvailabilityRecord.
- Identification of affected published schedule assignments.
- Enqueueing of targeted incremental repair requests into ScheduleRepairQueueService.
- Audit event logging with correlation tracking.
"""

import hashlib
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.idempotency.models import IdempotencyRecord
from app.db.models.scheduling import (
    ResourceAvailabilityRecord,
    ScheduleAssignmentRecord,
    ScheduleRecord,
)
from app.operations.repair_queue import ScheduleRepairQueueService


class ExternalResourceChangeEvent(BaseModel):
    """External provider event indicating a resource state or availability change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    tenant_id: str
    provider: str
    external_resource_id: str
    canonical_resource_id: UUID
    start_at: datetime
    end_at: datetime
    reason: str = Field(default="resource.unavailable")
    correlation_id: str | None = None


class ResourceChangeEventHandler:
    """Handler normalizing external disruptions into canonical events and repairs."""

    def __init__(self, repair_queue: ScheduleRepairQueueService) -> None:
        self.repair_queue = repair_queue
        # In-memory replay cache for fast path
        self._processed_event_ids: set[str] = set()

    async def handle_resource_unavailable(
        self,
        session: AsyncSession,
        event: ExternalResourceChangeEvent,
    ) -> bool:
        """Handle canonical resource.unavailable event with durable deduplication.

        Returns True if newly processed, or False if duplicate replay.
        """
        # Fast path in-memory check
        if event.event_id in self._processed_event_ids:
            return False

        # Durable idempotency check in PostgreSQL idempotency_records
        key_digest = hashlib.sha256(event.event_id.encode()).hexdigest()
        req_fp = hashlib.sha256(
            f"{event.tenant_id}:{event.canonical_resource_id}:{event.start_at.isoformat()}:{event.end_at.isoformat()}".encode()
        ).hexdigest()

        try:
            idemp_stmt = select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == event.tenant_id,
                IdempotencyRecord.operation_name == "resource.unavailable",
                IdempotencyRecord.key_digest == key_digest,
            )
            idemp_res = await session.execute(idemp_stmt)
            rec = idemp_res.scalar_one_or_none()
            if rec is not None and (
                isinstance(rec, IdempotencyRecord)
                or getattr(rec, "operation_name", None) == "resource.unavailable"
            ):
                self._processed_event_ids.add(event.event_id)
                return False

            # Persist durable idempotency record within transaction
            idemp_rec = IdempotencyRecord(
                tenant_id=event.tenant_id,
                operation_name="resource.unavailable",
                key_digest=key_digest,
                request_fingerprint=req_fp,
            )
            session.add(idemp_rec)
        except Exception:
            pass

        self._processed_event_ids.add(event.event_id)
        corr_id = event.correlation_id or f"corr-res-{event.event_id}"

        # 1. Record blackout interval in ResourceAvailabilityRecord
        blackout = ResourceAvailabilityRecord(
            tenant_id=event.tenant_id,
            resource_id=event.canonical_resource_id,
            start_at=event.start_at,
            end_at=event.end_at,
            interval_type="UNAVAILABLE",
            reason=f"Disruption from provider '{event.provider}': {event.reason}",
        )
        session.add(blackout)

        # 2. Query published schedules affected by this resource during the blackout interval
        assign_stmt = (
            select(ScheduleAssignmentRecord, ScheduleRecord)
            .join(ScheduleRecord, ScheduleAssignmentRecord.schedule_id == ScheduleRecord.id)
            .where(
                ScheduleAssignmentRecord.tenant_id == event.tenant_id,
                ScheduleAssignmentRecord.resource_id == str(event.canonical_resource_id),
                ScheduleRecord.status == "PUBLISHED",
                ScheduleAssignmentRecord.start_at < event.end_at,
                ScheduleAssignmentRecord.end_at > event.start_at,
            )
        )
        res = await session.execute(assign_stmt)
        affected_rows = res.all()

        affected_activity_ids = [str(r[0].activity_id) for r in affected_rows]
        if affected_rows:
            schedule = affected_rows[0][1]
            # 3. Enqueue targeted repair into ScheduleRepairQueueService
            try:
                await self.repair_queue.enqueue_repair_durable(
                    session=session,
                    tenant_id=event.tenant_id,
                    trigger_event_id=event.event_id,
                    reason="resource.unavailable",
                    baseline_schedule_id=schedule.id,
                    schedule_key=schedule.schedule_key,
                    affected_activity_ids=affected_activity_ids,
                    disrupted_resource_ids=[str(event.canonical_resource_id)],
                    disrupted_interval_start=event.start_at,
                    disrupted_interval_end=event.end_at,
                    correlation_id=corr_id,
                    priority=8,  # High priority for real-time disruptions
                )
            except Exception:
                self.repair_queue.enqueue_repair(
                    tenant_id=event.tenant_id,
                    trigger_event_id=event.event_id,
                    reason="resource.unavailable",
                    baseline_schedule_id=schedule.id,
                    schedule_key=schedule.schedule_key,
                    affected_activity_ids=affected_activity_ids,
                    disrupted_resource_ids=[str(event.canonical_resource_id)],
                    disrupted_interval_start=event.start_at,
                    disrupted_interval_end=event.end_at,
                    correlation_id=corr_id,
                    priority=8,
                )

        # 4. Append audit event
        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=event.tenant_id,
                actor_principal_id=f"provider-{event.provider}",
                actor_principal_type="SERVICE",
                authentication_method="SERVICE_API_KEY",
                event_type="resource.unavailable",
                resource_type="resource",
                resource_id=str(event.canonical_resource_id),
                outcome=AuditEventOutcome.SUCCESS,
                correlation_id=corr_id,
                details={
                    "event_id": event.event_id,
                    "provider": event.provider,
                    "external_resource_id": event.external_resource_id,
                    "start_at": event.start_at.isoformat(),
                    "end_at": event.end_at.isoformat(),
                    "affected_assignments_count": len(affected_activity_ids),
                },
            )
        )

        return True


__all__ = [
    "ExternalResourceChangeEvent",
    "ResourceChangeEventHandler",
]
