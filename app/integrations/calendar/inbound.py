"""Inbound calendar availability synchronization and callback deduplication.

Part of Phase 4B Generic Scheduling.
Enforces:
- Provider-neutral inbound translation from external calendar events to canonical
  resource availability.
- Strict UTC / timezone normalization.
- Safe resource resolution via CalendarResourceResolver.
- Incremental syncToken consumption and callback deduplication via ETag.
- No inference of staff qualification, eligibility, or role authority from calendar occupancy.
"""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.scheduling import (
    CalendarLinkRecord,
    ResourceAvailabilityRecord,
    ResourceRecord,
)
from app.integrations.calendar.adapter import CalendarAdapter
from app.integrations.calendar.dtos import SyncChangesResult

GOOGLE_CALENDAR_WEBHOOK_RUNTIME: str = "DEFERRED_TO_PHASE_7_EXTERNAL_INTEGRATION"


@runtime_checkable
class CalendarResourceResolver(Protocol):
    """Protocol for resolving external calendar references to canonical E-7 resource UUIDs."""

    async def resolve_resource_id(
        self, tenant_id: str, external_ref: str, session: AsyncSession
    ) -> UUID | None:
        """Resolve external calendar email, ID, or user reference to an internal resource UUID."""
        ...


class DefaultCalendarResourceResolver:
    """Canonical resolver mapping external calendar identifiers to E-7 resources."""

    def __init__(self, synthetic_mappings: dict[str, UUID] | None = None) -> None:
        self._synthetic_mappings = synthetic_mappings or {}

    async def resolve_resource_id(
        self, tenant_id: str, external_ref: str, session: AsyncSession
    ) -> UUID | None:
        # Check synthetic mapping first
        if external_ref in self._synthetic_mappings:
            return self._synthetic_mappings[external_ref]

        # Query resource by resource_key or attributes
        stmt = select(ResourceRecord).where(
            ResourceRecord.tenant_id == tenant_id,
            ResourceRecord.resource_key == external_ref,
        )
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()
        if record:
            return record.id

        return None


class InboundCalendarSyncService:
    """Service importing external calendar events as canonical resource availability intervals.

    Persists incremental syncToken durably in calendar_links across restarts and enforces
    provider-neutral freshness ordering to safely ignore stale or duplicate webhook/sync events.
    """

    def __init__(
        self,
        resolver: CalendarResourceResolver | None = None,
    ) -> None:
        self.resolver = resolver or DefaultCalendarResourceResolver()

    async def import_incremental_changes(
        self,
        session: AsyncSession,
        tenant_id: str,
        adapter: CalendarAdapter,
        external_calendar_id: str,
        sync_token: str | None = None,
        provider: str = "google",
    ) -> SyncChangesResult:
        """Fetch external calendar changes and update canonical resource availability."""
        if not external_calendar_id or not str(external_calendar_id).strip():
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        external_calendar_id = str(external_calendar_id).strip()

        # 1. Resolve durable sync token from provider-neutral cursor record
        effective_sync_token = sync_token
        cursor_stmt = select(CalendarLinkRecord).where(
            CalendarLinkRecord.tenant_id == tenant_id,
            CalendarLinkRecord.provider == provider,
            CalendarLinkRecord.external_calendar_id == external_calendar_id,
            CalendarLinkRecord.link_kind == "SYNC_CURSOR",
        )
        cursor_record = (await session.execute(cursor_stmt)).scalar_one_or_none()
        if effective_sync_token is None and cursor_record:
            effective_sync_token = cursor_record.sync_token

        # 2. Fetch delta changes from adapter scoped by external_calendar_id
        try:
            changes = await adapter.fetch_incremental_changes(
                tenant_id, effective_sync_token, external_calendar_id=external_calendar_id
            )
        except TypeError:
            changes = await adapter.fetch_incremental_changes(tenant_id, effective_sync_token)

        # 3. Process changes with ETag deduplication and provider freshness ordering
        imported_count = 0
        for ev in changes.created_or_updated:
            ref = ev.location or ev.title
            res_id = await self.resolver.resolve_resource_id(tenant_id, ref, session)
            if not res_id:
                continue

            cal_scope = ev.external_calendar_id or external_calendar_id

            # Query existing link for this external event to check ETag / freshness
            link_stmt = select(CalendarLinkRecord).where(
                CalendarLinkRecord.tenant_id == tenant_id,
                CalendarLinkRecord.provider == provider,
                CalendarLinkRecord.external_calendar_id == cal_scope,
                CalendarLinkRecord.external_event_id == ev.external_event_id,
                CalendarLinkRecord.link_kind == "EVENT",
            )
            existing_link = (await session.execute(link_stmt)).scalar_one_or_none()

            if existing_link:
                # ETag exact equality: identical representation -> duplicate/no mutation
                if ev.etag and existing_link.etag and ev.etag == existing_link.etag:
                    continue

                # Inbound Freshness Ordering: check provider metadata (version/updated_at)
                is_stale = False
                if ev.provider_version is not None and existing_link.provider_version is not None:
                    if ev.provider_version < existing_link.provider_version:
                        is_stale = True
                elif (
                    ev.provider_updated_at is not None
                    and existing_link.provider_updated_at is not None
                ):
                    if ev.provider_updated_at < existing_link.provider_updated_at:
                        is_stale = True
                elif ev.sequence < (existing_link.provider_version or 0):
                    is_stale = True

                if is_stale:
                    continue

                # Apply update
                existing_link.etag = ev.etag
                existing_link.provider_version = ev.provider_version
                existing_link.provider_updated_at = ev.provider_updated_at
                existing_link.last_synced_at = datetime.now(UTC)
            else:
                new_link = CalendarLinkRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    provider=provider,
                    external_calendar_id=cal_scope,
                    link_kind="EVENT",
                    external_event_id=ev.external_event_id,
                    sync_status="SYNCED",
                    etag=ev.etag,
                    provider_version=ev.provider_version,
                    provider_updated_at=ev.provider_updated_at,
                    last_synced_at=datetime.now(UTC),
                )
                session.add(new_link)

            # Ensure UTC times
            start_utc = ev.start_at.astimezone(UTC)
            end_utc = ev.end_at.astimezone(UTC)

            # Upsert availability interval to maintain one logical availability state
            event_marker = f"(Cal: {cal_scope}, ID: {ev.external_event_id})"
            avail_match = f"%Cal: {cal_scope}, ID: {ev.external_event_id}%"

            avail_stmt = select(ResourceAvailabilityRecord).where(
                ResourceAvailabilityRecord.tenant_id == tenant_id,
                ResourceAvailabilityRecord.resource_id == res_id,
                ResourceAvailabilityRecord.reason.like(avail_match),
            )
            existing_avail = (await session.execute(avail_stmt)).scalar_one_or_none()
            event_reason = f"External calendar: {ev.title} {event_marker}"
            if existing_avail:
                existing_avail.start_at = start_utc
                existing_avail.end_at = end_utc
                existing_avail.reason = event_reason
            else:
                avail = ResourceAvailabilityRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    resource_id=res_id,
                    start_at=start_utc,
                    end_at=end_utc,
                    interval_type="UNAVAILABLE",
                    reason=event_reason,
                )
                session.add(avail)
            imported_count += 1

        # Process external deletions
        for del_id in changes.deleted_external_ids:
            del_match = f"%Cal: {external_calendar_id}, ID: {del_id}%"
            del_stmt = select(ResourceAvailabilityRecord).where(
                ResourceAvailabilityRecord.tenant_id == tenant_id,
                ResourceAvailabilityRecord.reason.like(del_match),
            )
            del_records = (await session.execute(del_stmt)).scalars().all()
            for rec in del_records:
                await session.delete(rec)

        # 4. Durably persist the new sync token in provider-neutral cursor record
        if changes.next_sync_token:
            if cursor_record:
                cursor_record.sync_token = changes.next_sync_token
                cursor_record.last_synced_at = datetime.now(UTC)
            else:
                cursor_record = CalendarLinkRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    provider=provider,
                    external_calendar_id=external_calendar_id,
                    link_kind="SYNC_CURSOR",
                    external_event_id=None,
                    assignment_id=None,
                    activity_id=None,
                    schedule_key=None,
                    sync_token=changes.next_sync_token,
                    sync_status="SYNCED",
                    last_synced_at=datetime.now(UTC),
                )
                session.add(cursor_record)

        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type="calendar.availability_imported",
                resource_type="resource_availability",
                resource_id=tenant_id,
                outcome=AuditEventOutcome.SUCCESS,
                details={
                    "imported_count": imported_count,
                    "next_sync_token": changes.next_sync_token,
                },
            )
        )
        await session.flush()
        return changes
