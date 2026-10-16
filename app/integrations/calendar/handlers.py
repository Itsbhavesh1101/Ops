"""Consumer event handler for calendar synchronization on schedule.published events.

Part of Phase 6 Pre-Live Evidence Closure.
Enforces:
- ConsumerEventHandler protocol implementation.
- Wrapped in ConsumerEventProcessor with transactional Inbox deduplication.
- Synchronizes schedule session assignments through CalendarAdapterProtocol.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.schemas import EventEnvelope
from app.integrations.calendar.adapter import CalendarAdapter
from app.integrations.calendar.dtos import CalendarEventDTO

CalendarAdapterProtocol = CalendarAdapter


class SchedulePublishedCalendarHandler:
    """Consumer handler for canonical schedule.published event to synchronize calendar."""

    def __init__(self, adapter: CalendarAdapter) -> None:
        self.adapter = adapter

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Process schedule.published event and synchronize assignments to calendar."""
        tenant_id = envelope.tenant_id
        if not tenant_id:
            return

        payload = envelope.payload
        assignments = payload.get("assignments") or []
        for asgn in assignments:
            session_id = asgn.get("session_id") or asgn.get("activity_id") or "session-unknown"
            room_id = asgn.get("room_id") or asgn.get("room_resource_id") or asgn.get("resource_id")

            start_val = asgn.get("start_at")
            end_val = asgn.get("end_at")

            if isinstance(start_val, str):
                start_at = datetime.fromisoformat(start_val)
            elif isinstance(start_val, datetime):
                start_at = start_val
            else:
                continue

            if isinstance(end_val, str):
                end_at = datetime.fromisoformat(end_val)
            elif isinstance(end_val, datetime):
                end_at = end_val
            else:
                continue

            event_dto = CalendarEventDTO(
                external_event_id=f"cal-{session_id}",
                external_calendar_id="primary",
                title=f"Grade 10 Exam: {session_id}",
                start_at=start_at,
                end_at=end_at,
                location=room_id,
            )
            await self.adapter.create_event(
                tenant_id=tenant_id,
                event=event_dto,
                external_calendar_id="primary",
            )


__all__ = ["SchedulePublishedCalendarHandler"]
