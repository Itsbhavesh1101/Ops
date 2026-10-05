"""Periodic calendar reconciliation service.

Part of Phase 4B Generic Scheduling.
Enforces:
- Callable reconciliation logic comparing canonical published assignments
  against calendar_links and provider state.
- Idempotent healing of missing, failed, or desynchronized mappings.
- Clean cleanup of orphaned external calendar events.
- Audit recording of reconciliation actions.
"""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.scheduling import (
    CalendarLinkRecord,
    ScheduleRecord,
)
from app.integrations.calendar.adapter import CalendarAdapter
from app.integrations.calendar.dtos import (
    CalendarEventDTO,
    ReconciliationResult,
    SyncStatus,
)


class CalendarReconciliationService:
    """Service providing periodic reconciliation between canonical assignments
    and external calendar.
    """

    def __init__(self, adapter: CalendarAdapter) -> None:
        self.adapter = adapter

    async def reconcile_published_schedule(
        self,
        session: AsyncSession,
        tenant_id: str,
        schedule_id: UUID,
        external_calendar_id: str,
        provider: str = "google",
    ) -> ReconciliationResult:
        """Reconcile all assignments of a published schedule against calendar_links and provider."""
        if not external_calendar_id or not str(external_calendar_id).strip():
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        external_calendar_id = str(external_calendar_id).strip()
        stmt = (
            select(ScheduleRecord)
            .options(selectinload(ScheduleRecord.assignments))
            .where(
                ScheduleRecord.id == schedule_id,
                ScheduleRecord.tenant_id == tenant_id,
            )
        )
        res = await session.execute(stmt)
        schedule = res.scalar_one_or_none()
        if not schedule or schedule.status != "PUBLISHED":
            return ReconciliationResult()

        created = 0
        updated = 0
        deleted = 0
        unchanged = 0
        failed = 0

        # Query existing links for this schedule, provider, and external_calendar_id
        links_stmt = select(CalendarLinkRecord).where(
            CalendarLinkRecord.tenant_id == tenant_id,
            CalendarLinkRecord.schedule_id == schedule_id,
            CalendarLinkRecord.provider == provider,
            CalendarLinkRecord.external_calendar_id == external_calendar_id,
        )
        existing_links = {
            link.assignment_id: link
            for link in (await session.execute(links_stmt)).scalars().all()
            if link.assignment_id is not None
        }

        current_asgn_ids = {asgn.id for asgn in schedule.assignments}

        # 1. Clean up links for assignments no longer present
        for asgn_id, link in list(existing_links.items()):
            if asgn_id not in current_asgn_ids:
                if link.external_event_id:
                    try:
                        del_ok = await self.adapter.delete_event(
                            tenant_id,
                            link.external_event_id,
                            expected_etag=link.etag,
                            external_calendar_id=external_calendar_id,
                        )
                    except TypeError:
                        del_ok = await self.adapter.delete_event(
                            tenant_id, link.external_event_id, expected_etag=link.etag
                        )
                    if del_ok:
                        await session.delete(link)
                        deleted += 1
                else:
                    await session.delete(link)
                    deleted += 1

        # 2. Reconcile current assignments
        for asgn in schedule.assignments:
            asgn_link: CalendarLinkRecord | None = existing_links.get(asgn.id)
            ext_event_id = (
                asgn_link.external_event_id
                if (asgn_link and asgn_link.external_event_id)
                else f"e7_ev_{asgn.id.hex}"
            )

            event_dto = CalendarEventDTO(
                external_event_id=ext_event_id,
                external_calendar_id=external_calendar_id,
                title=f"Activity {asgn.activity_id}",
                start_at=asgn.start_at,
                end_at=asgn.end_at,
                description=f"Resource: {asgn.resource_id} | Activity: {asgn.activity_id}",
                schedule_id=schedule.id,
                assignment_id=asgn.id,
                etag=asgn_link.etag if asgn_link else None,
            )

            if not asgn_link:
                # Missing link -> CREATE
                try:
                    result = await self.adapter.create_event(
                        tenant_id, event_dto, external_calendar_id=external_calendar_id
                    )
                except TypeError:
                    result = await self.adapter.create_event(tenant_id, event_dto)

                new_link = CalendarLinkRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    link_kind="EVENT",
                    schedule_id=schedule.id,
                    assignment_id=asgn.id,
                    provider=provider,
                    external_calendar_id=external_calendar_id,
                    external_event_id=result.external_event_id,
                    sync_status=result.status.value,
                    etag=result.etag,
                    last_synced_at=result.synced_at,
                    error_message=result.error_message,
                )
                session.add(new_link)
                if result.status == SyncStatus.SYNCED:
                    created += 1
                else:
                    failed += 1
            else:
                # Link exists; verify state with provider
                if asgn_link.external_event_id:
                    try:
                        ext_ev = await self.adapter.get_event(
                            tenant_id,
                            asgn_link.external_event_id,
                            external_calendar_id=external_calendar_id,
                        )
                    except TypeError:
                        ext_ev = await self.adapter.get_event(
                            tenant_id, asgn_link.external_event_id
                        )
                else:
                    ext_ev = None

                if not ext_ev or asgn_link.sync_status != SyncStatus.SYNCED.value:
                    # Provider missing or link was in failed state -> recreate/update
                    try:
                        result = await self.adapter.create_event(
                            tenant_id, event_dto, external_calendar_id=external_calendar_id
                        )
                    except TypeError:
                        result = await self.adapter.create_event(tenant_id, event_dto)
                    asgn_link.sync_status = result.status.value
                    asgn_link.etag = result.etag
                    asgn_link.last_synced_at = result.synced_at
                    asgn_link.error_message = result.error_message
                    if result.status == SyncStatus.SYNCED:
                        updated += 1
                    else:
                        failed += 1
                else:
                    unchanged += 1

        # 3. Record audit event
        audit_repo = AuditRepository(session)
        audit_outcome = AuditEventOutcome.SUCCESS if failed == 0 else AuditEventOutcome.FAILURE
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type="calendar.reconciliation_completed",
                resource_type="schedule",
                resource_id=str(schedule.id),
                outcome=audit_outcome,
                details={
                    "schedule_id": str(schedule.id),
                    "created_count": created,
                    "updated_count": updated,
                    "deleted_count": deleted,
                    "unchanged_count": unchanged,
                    "failed_count": failed,
                    "provider": provider,
                    "external_calendar_id": external_calendar_id,
                },
            )
        )
        await session.flush()
        return ReconciliationResult(
            created_count=created,
            updated_count=updated,
            deleted_count=deleted,
            unchanged_count=unchanged,
            failed_count=failed,
        )
