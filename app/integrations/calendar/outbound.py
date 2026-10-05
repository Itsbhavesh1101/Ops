"""Outbound calendar synchronization service and schedule.published event consumer.

Part of Phase 4B Generic Scheduling.
Enforces:
- Idempotent create/update/delete synchronization of published schedule assignments.
- Secondary mapping management in calendar_links table.
- Separation of concerns: E-7 publication is canonical and never rolled back
  on external sync failure.
- Truthful status reporting (never reporting SUCCESS if external provider call failed).
- Diffing against superseded schedules to remove deleted assignments.
"""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.scheduling import (
    CalendarLinkRecord,
    ScheduleAssignmentRecord,
    ScheduleRecord,
)
from app.events.schemas import EventEnvelope
from app.integrations.calendar.adapter import CalendarAdapter
from app.integrations.calendar.dtos import (
    CalendarEventDTO,
    SyncStatus,
)


class OutboundCalendarSyncService:
    """Service synchronizing canonical published schedule assignments to external calendars."""

    def __init__(self, adapter: CalendarAdapter) -> None:
        self.adapter = adapter

    async def sync_published_schedule(
        self,
        session: AsyncSession,
        tenant_id: str,
        schedule_id: UUID,
        external_calendar_id: str,
        provider: str = "google",
    ) -> list[CalendarLinkRecord]:
        """Synchronize assignments of a published schedule to external calendar provider.

        Flow:
        1. Fetch published schedule with assignments.
        2. Detect removed assignments if schedule supersedes an older version, and call delete.
        3. For current assignments, perform idempotent CREATE or UPDATE.
        4. Persist truthful sync_status (SYNCED or FAILED) in calendar_links.
        5. Record append-only audit event for calendar synchronization.
        """
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
        if not schedule:
            return []

        # 1. Fetch existing calendar links for tenant, provider, calendar, and schedule_key
        existing_links_stmt = select(CalendarLinkRecord).where(
            CalendarLinkRecord.tenant_id == tenant_id,
            CalendarLinkRecord.provider == provider,
            CalendarLinkRecord.external_calendar_id == external_calendar_id,
            CalendarLinkRecord.schedule_key == schedule.schedule_key,
            CalendarLinkRecord.sync_status != "DELETED",
            CalendarLinkRecord.activity_id.is_not(None),
        )
        existing_links = list((await session.execute(existing_links_stmt)).scalars().all())
        link_by_activity = {lnk.activity_id: lnk for lnk in existing_links if lnk.activity_id}

        current_act_ids = {asgn.activity_id for asgn in schedule.assignments}
        created_count = 0
        updated_count = 0
        failed_count = 0
        noop_count = 0

        # 2. Deletions: if an activity was previously synced for this schedule_key
        # but not present in current assignments
        for old_act_id, old_link in link_by_activity.items():
            if old_act_id not in current_act_ids:
                if old_link.external_event_id:
                    try:
                        del_ok = await self.adapter.delete_event(
                            tenant_id,
                            old_link.external_event_id,
                            expected_etag=old_link.etag,
                            external_calendar_id=external_calendar_id,
                        )
                    except TypeError:
                        del_ok = await self.adapter.delete_event(
                            tenant_id,
                            old_link.external_event_id,
                            expected_etag=old_link.etag,
                        )
                    if del_ok:
                        old_link.sync_status = "DELETED"
                else:
                    old_link.sync_status = "DELETED"

        # 3. Synchronize current assignments using stable logical activity identity
        results: list[CalendarLinkRecord] = []
        for asgn in schedule.assignments:
            link = link_by_activity.get(asgn.activity_id)

            if not link or link.sync_status == "DELETED":
                # CREATE new external event
                ext_event_id = f"e7_ev_{tenant_id}_{schedule.schedule_key}_{asgn.activity_id}"
                event_dto = CalendarEventDTO(
                    external_event_id=ext_event_id,
                    external_calendar_id=external_calendar_id,
                    title=f"Activity {asgn.activity_id}",
                    start_at=asgn.start_at,
                    end_at=asgn.end_at,
                    description=f"Resource: {asgn.resource_id} | Activity: {asgn.activity_id}",
                    schedule_id=schedule.id,
                    assignment_id=asgn.id,
                    etag=None,
                )
                try:
                    call_res = await self.adapter.create_event(
                        tenant_id, event_dto, external_calendar_id=external_calendar_id
                    )
                except TypeError:
                    call_res = await self.adapter.create_event(tenant_id, event_dto)

                if call_res.status == SyncStatus.SYNCED:
                    created_count += 1
                else:
                    failed_count += 1

                link = CalendarLinkRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    provider=provider,
                    external_calendar_id=external_calendar_id,
                    schedule_id=schedule.id,
                    assignment_id=asgn.id,
                    activity_id=asgn.activity_id,
                    schedule_key=schedule.schedule_key,
                    link_kind="EVENT",
                    external_event_id=call_res.external_event_id,
                    sync_status=call_res.status.value,
                    etag=call_res.etag,
                    last_synced_at=call_res.synced_at,
                    error_message=call_res.error_message,
                )
                session.add(link)
                results.append(link)
            else:
                # Link exists: check if assignment changed or needs retry
                prev_asgn: ScheduleAssignmentRecord | None = None
                if link.assignment_id:
                    prev_asgn_stmt = select(ScheduleAssignmentRecord).where(
                        ScheduleAssignmentRecord.id == link.assignment_id,
                        ScheduleAssignmentRecord.tenant_id == tenant_id,
                    )
                    prev_asgn = (await session.execute(prev_asgn_stmt)).scalar_one_or_none()

                time_or_res_changed = (
                    prev_asgn is None
                    or prev_asgn.start_at != asgn.start_at
                    or prev_asgn.end_at != asgn.end_at
                    or prev_asgn.resource_id != asgn.resource_id
                )
                needs_update = time_or_res_changed or link.sync_status != SyncStatus.SYNCED.value

                # Re-point link to current schedule_id and assignment_id
                link.schedule_id = schedule.id
                link.assignment_id = asgn.id

                if needs_update and link.external_event_id:
                    # UPDATE external event
                    event_dto = CalendarEventDTO(
                        external_event_id=link.external_event_id,
                        external_calendar_id=external_calendar_id,
                        title=f"Activity {asgn.activity_id}",
                        start_at=asgn.start_at,
                        end_at=asgn.end_at,
                        description=f"Resource: {asgn.resource_id} | Activity: {asgn.activity_id}",
                        schedule_id=schedule.id,
                        assignment_id=asgn.id,
                        etag=link.etag,
                    )
                    try:
                        call_res = await self.adapter.update_event(
                            tenant_id,
                            link.external_event_id,
                            event_dto,
                            expected_etag=link.etag,
                            external_calendar_id=external_calendar_id,
                        )
                    except TypeError:
                        call_res = await self.adapter.update_event(
                            tenant_id,
                            link.external_event_id,
                            event_dto,
                            expected_etag=link.etag,
                        )
                    link.sync_status = call_res.status.value
                    link.etag = call_res.etag
                    link.last_synced_at = call_res.synced_at
                    link.error_message = call_res.error_message
                    if call_res.status == SyncStatus.SYNCED:
                        updated_count += 1
                    else:
                        failed_count += 1
                else:
                    # Unchanged logical assignment: NO OP
                    noop_count += 1

                results.append(link)

        # 4. Record append-only audit event
        audit_repo = AuditRepository(session)
        audit_outcome = (
            AuditEventOutcome.SUCCESS if failed_count == 0 else AuditEventOutcome.FAILURE
        )
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type="calendar.sync_attempted",
                resource_type="schedule",
                resource_id=str(schedule.id),
                outcome=audit_outcome,
                details={
                    "schedule_id": str(schedule.id),
                    "created_count": created_count,
                    "updated_count": updated_count,
                    "failed_count": failed_count,
                    "noop_count": noop_count,
                    "provider": provider,
                    "external_calendar_id": external_calendar_id,
                },
            )
        )
        await session.flush()
        return results


class CalendarPublishedEventHandler:
    """Consumer handler for canonical schedule.published event to trigger calendar sync."""

    def __init__(self, sync_service: OutboundCalendarSyncService) -> None:
        self.sync_service = sync_service

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Handle schedule.published event by executing outbound calendar sync."""
        tenant_id = envelope.tenant_id
        if not tenant_id:
            return

        schedule_id_raw = envelope.payload.get("schedule_id")
        if not schedule_id_raw:
            return

        schedule_id = UUID(str(schedule_id_raw))
        raw_cal_id = envelope.payload.get("external_calendar_id")
        if not raw_cal_id or not str(raw_cal_id).strip():
            raise ValueError(
                "external_calendar_id is required in schedule.published "
                "payload for calendar synchronization"
            )
        external_calendar_id = str(raw_cal_id).strip()
        provider = str(envelope.payload.get("provider") or "google")
        await self.sync_service.sync_published_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            external_calendar_id=external_calendar_id,
            provider=provider,
        )
