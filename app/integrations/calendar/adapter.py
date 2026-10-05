"""Provider-neutral CalendarAdapter protocol and deterministic test implementation.

Part of Phase 4B Generic Scheduling.
Enforces:
- Provider-neutral calendar protocol boundary.
- Deterministic failure simulation for outage and degraded behavior testing.
- Truthful reporting: provider failure NEVER reports SYNCED or SUCCESS.
- Incremental change tracking via syncTokens and ETags.
- Provider-neutral external calendar/collection scoping per (tenant, provider, calendar).
"""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

from app.integrations.calendar.dtos import (
    CalendarEventDTO,
    CalendarEventResult,
    SyncChangesResult,
    SyncStatus,
)


@runtime_checkable
class CalendarAdapter(Protocol):
    """Protocol defining provider-neutral calendar operations."""

    async def create_event(
        self,
        tenant_id: str,
        event: CalendarEventDTO,
        external_calendar_id: str | None = None,
    ) -> CalendarEventResult:
        """Create a new event in the external calendar provider."""
        ...

    async def update_event(
        self,
        tenant_id: str,
        external_event_id: str,
        event: CalendarEventDTO,
        expected_etag: str | None = None,
        external_calendar_id: str | None = None,
    ) -> CalendarEventResult:
        """Update an existing event in the external calendar provider."""
        ...

    async def delete_event(
        self,
        tenant_id: str,
        external_event_id: str,
        expected_etag: str | None = None,
        external_calendar_id: str | None = None,
    ) -> bool:
        """Delete an event from the external calendar provider."""
        ...

    async def fetch_incremental_changes(
        self,
        tenant_id: str,
        sync_token: str | None = None,
        external_calendar_id: str | None = None,
    ) -> SyncChangesResult:
        """Fetch changes (created/updated/deleted) since the supplied syncToken."""
        ...

    async def get_event(
        self,
        tenant_id: str,
        external_event_id: str,
        external_calendar_id: str | None = None,
    ) -> CalendarEventDTO | None:
        """Fetch a single event by external ID."""
        ...

    async def list_events(
        self,
        tenant_id: str,
        external_calendar_id: str | None = None,
    ) -> list[CalendarEventDTO]:
        """List all current events in the external provider for the tenant."""
        ...


class FakeCalendarAdapter:
    """Deterministic in-memory calendar adapter supporting failure injection and scoping."""

    def __init__(self) -> None:
        self.simulate_outage: bool = False
        self.failure_error: str = "External calendar service unavailable"
        self._events: dict[str, dict[str, CalendarEventDTO]] = {}
        self._etags: dict[str, dict[str, str]] = {}
        self._versions: dict[str, dict[str, int]] = {}
        self._deleted_events: dict[str, set[str]] = {}
        self._sync_tokens: dict[str, str] = {}

        # Multi-calendar collection stores: key = (tenant_id, external_calendar_id)
        self._cal_events: dict[tuple[str, str], dict[str, CalendarEventDTO]] = {}
        self._cal_etags: dict[tuple[str, str], dict[str, str]] = {}
        self._cal_versions: dict[tuple[str, str], dict[str, int]] = {}
        self._cal_deleted: dict[tuple[str, str], set[str]] = {}
        self._cal_sync_tokens: dict[tuple[str, str], str] = {}

        self._call_counts: dict[str, int] = {
            "create": 0,
            "update": 0,
            "delete": 0,
            "fetch": 0,
        }

    def _ensure_tenant(self, tenant_id: str) -> None:
        if tenant_id not in self._events:
            self._events[tenant_id] = {}
            self._etags[tenant_id] = {}
            self._versions[tenant_id] = {}
            self._deleted_events[tenant_id] = set()
            self._sync_tokens[tenant_id] = f"token-{uuid4().hex[:8]}"

    def _ensure_scope(
        self, tenant_id: str, external_calendar_id: str
    ) -> tuple[str, str]:
        if not external_calendar_id or not external_calendar_id.strip():
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        cal_id = external_calendar_id.strip()
        key = (tenant_id, cal_id)
        if key not in self._cal_events:
            self._cal_events[key] = {}
            self._cal_etags[key] = {}
            self._cal_versions[key] = {}
            self._cal_deleted[key] = set()
            self._cal_sync_tokens[key] = f"token-{cal_id}-{uuid4().hex[:8]}"
        self._ensure_tenant(tenant_id)
        return key

    async def create_event(
        self,
        tenant_id: str,
        event: CalendarEventDTO,
        external_calendar_id: str | None = None,
    ) -> CalendarEventResult:
        self._call_counts["create"] += 1
        if self.simulate_outage:
            return CalendarEventResult(
                external_event_id=event.external_event_id,
                status=SyncStatus.FAILED,
                error_message=self.failure_error,
                etag=None,
                synced_at=None,
            )

        cal_id = (external_calendar_id or event.external_calendar_id or "").strip()
        if not cal_id:
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        key = self._ensure_scope(tenant_id, cal_id)
        now = datetime.now(UTC)
        etag = f"etag_{uuid4().hex[:8]}"
        version = event.provider_version if event.provider_version is not None else 1

        stored_event = CalendarEventDTO(
            external_event_id=event.external_event_id,
            external_calendar_id=cal_id,
            title=event.title,
            start_at=event.start_at,
            end_at=event.end_at,
            timezone=event.timezone,
            description=event.description,
            location=event.location,
            schedule_id=event.schedule_id,
            assignment_id=event.assignment_id,
            etag=etag,
            sequence=event.sequence,
            provider_version=version,
            provider_updated_at=event.provider_updated_at or now,
        )
        self._cal_events[key][event.external_event_id] = stored_event
        self._cal_etags[key][event.external_event_id] = etag
        self._cal_versions[key][event.external_event_id] = version
        self._cal_deleted[key].discard(event.external_event_id)
        self._cal_sync_tokens[key] = f"token-{cal_id}-{uuid4().hex[:8]}"

        # Maintain tenant-level map for backward compatibility
        self._events[tenant_id][event.external_event_id] = stored_event
        self._etags[tenant_id][event.external_event_id] = etag
        self._versions[tenant_id][event.external_event_id] = version
        self._deleted_events[tenant_id].discard(event.external_event_id)
        self._sync_tokens[tenant_id] = self._cal_sync_tokens[key]

        return CalendarEventResult(
            external_event_id=event.external_event_id,
            status=SyncStatus.SYNCED,
            etag=etag,
            synced_at=now,
            error_message=None,
        )

    async def update_event(
        self,
        tenant_id: str,
        external_event_id: str,
        event: CalendarEventDTO,
        expected_etag: str | None = None,
        external_calendar_id: str | None = None,
    ) -> CalendarEventResult:
        self._call_counts["update"] += 1
        if self.simulate_outage:
            return CalendarEventResult(
                external_event_id=external_event_id,
                status=SyncStatus.FAILED,
                error_message=self.failure_error,
                etag=None,
                synced_at=None,
            )

        cal_id = (external_calendar_id or event.external_calendar_id or "").strip()
        if not cal_id:
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        key = self._ensure_scope(tenant_id, cal_id)

        if external_event_id not in self._cal_events[key]:
            return CalendarEventResult(
                external_event_id=external_event_id,
                status=SyncStatus.FAILED,
                error_message=f"Event {external_event_id} not found in provider calendar {cal_id}",
                etag=None,
                synced_at=None,
            )

        current_etag = self._cal_etags[key].get(external_event_id)
        if expected_etag is not None and current_etag and expected_etag != current_etag:
            return CalendarEventResult(
                external_event_id=external_event_id,
                status=SyncStatus.CONFLICT,
                error_message=(
                    f"Precondition failed: expected {expected_etag} != {current_etag}"
                ),
                etag=current_etag,
                synced_at=None,
            )

        now = datetime.now(UTC)
        new_etag = f"etag_{uuid4().hex[:8]}"
        curr_ver = self._cal_versions[key].get(external_event_id, 1)
        next_ver = event.provider_version if event.provider_version is not None else curr_ver + 1

        updated_event = CalendarEventDTO(
            external_event_id=external_event_id,
            external_calendar_id=cal_id,
            title=event.title,
            start_at=event.start_at,
            end_at=event.end_at,
            timezone=event.timezone,
            description=event.description,
            location=event.location,
            schedule_id=event.schedule_id,
            assignment_id=event.assignment_id,
            etag=new_etag,
            sequence=event.sequence + 1,
            provider_version=next_ver,
            provider_updated_at=event.provider_updated_at or now,
        )
        self._cal_events[key][external_event_id] = updated_event
        self._cal_etags[key][external_event_id] = new_etag
        self._cal_versions[key][external_event_id] = next_ver
        self._cal_sync_tokens[key] = f"token-{cal_id}-{uuid4().hex[:8]}"

        self._events[tenant_id][external_event_id] = updated_event
        self._etags[tenant_id][external_event_id] = new_etag
        self._versions[tenant_id][external_event_id] = next_ver
        self._sync_tokens[tenant_id] = self._cal_sync_tokens[key]

        return CalendarEventResult(
            external_event_id=external_event_id,
            status=SyncStatus.SYNCED,
            etag=new_etag,
            synced_at=now,
            error_message=None,
        )

    async def delete_event(
        self,
        tenant_id: str,
        external_event_id: str,
        expected_etag: str | None = None,
        external_calendar_id: str | None = None,
    ) -> bool:
        self._call_counts["delete"] += 1
        if self.simulate_outage:
            return False

        if not external_calendar_id or not external_calendar_id.strip():
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        cal_id = external_calendar_id.strip()
        key = self._ensure_scope(tenant_id, cal_id)
        current_etag = self._cal_etags[key].get(external_event_id)
        if expected_etag is not None and current_etag and expected_etag != current_etag:
            return False

        if external_event_id in self._cal_events[key]:
            del self._cal_events[key][external_event_id]
            self._cal_etags[key].pop(external_event_id, None)
            self._cal_versions[key].pop(external_event_id, None)
            self._cal_deleted[key].add(external_event_id)
            self._cal_sync_tokens[key] = f"token-{cal_id}-{uuid4().hex[:8]}"

            self._events[tenant_id].pop(external_event_id, None)
            self._etags[tenant_id].pop(external_event_id, None)
            self._versions[tenant_id].pop(external_event_id, None)
            self._deleted_events[tenant_id].add(external_event_id)
            self._sync_tokens[tenant_id] = self._cal_sync_tokens[key]
            return True
        return False

    async def fetch_incremental_changes(
        self,
        tenant_id: str,
        sync_token: str | None = None,
        external_calendar_id: str | None = None,
    ) -> SyncChangesResult:
        self._call_counts["fetch"] += 1
        if not external_calendar_id or not external_calendar_id.strip():
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        cal_id = external_calendar_id.strip()
        key = self._ensure_scope(tenant_id, cal_id)

        current_token = self._cal_sync_tokens[key]
        if sync_token and sync_token == current_token:
            # Token is up to date; no incremental changes to report
            return SyncChangesResult(
                created_or_updated=[],
                deleted_external_ids=[],
                next_sync_token=current_token,
            )

        all_events = list(self._cal_events[key].values())
        deleted = list(self._cal_deleted[key])

        return SyncChangesResult(
            created_or_updated=all_events,
            deleted_external_ids=deleted,
            next_sync_token=current_token,
        )

    async def get_event(
        self,
        tenant_id: str,
        external_event_id: str,
        external_calendar_id: str | None = None,
    ) -> CalendarEventDTO | None:
        if not external_calendar_id or not external_calendar_id.strip():
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        cal_id = external_calendar_id.strip()
        key = self._ensure_scope(tenant_id, cal_id)
        return self._cal_events[key].get(external_event_id)

    async def list_events(
        self,
        tenant_id: str,
        external_calendar_id: str | None = None,
    ) -> list[CalendarEventDTO]:
        if not external_calendar_id or not external_calendar_id.strip():
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        cal_id = external_calendar_id.strip()
        key = self._ensure_scope(tenant_id, cal_id)
        return list(self._cal_events[key].values())

    def set_sync_token(
        self, tenant_id: str, token: str, external_calendar_id: str
    ) -> None:
        """Explicitly configure the sync token for a given tenant and calendar scope."""
        if not external_calendar_id or not external_calendar_id.strip():
            raise ValueError(
                "external_calendar_id is required and cannot be empty or whitespace"
            )
        key = self._ensure_scope(tenant_id, external_calendar_id.strip())
        self._cal_sync_tokens[key] = token
        self._sync_tokens[tenant_id] = token

