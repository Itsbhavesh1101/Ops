"""Canonical Calendar Integration boundary for E-7 Operations Engine."""

from app.integrations.calendar.adapter import CalendarAdapter, FakeCalendarAdapter
from app.integrations.calendar.dtos import (
    CalendarEventDTO,
    CalendarEventResult,
    ReconciliationResult,
    SyncChangesResult,
    SyncStatus,
)
from app.integrations.calendar.inbound import (
    GOOGLE_CALENDAR_WEBHOOK_RUNTIME,
    CalendarResourceResolver,
    DefaultCalendarResourceResolver,
    InboundCalendarSyncService,
)
from app.integrations.calendar.outbound import (
    CalendarPublishedEventHandler,
    OutboundCalendarSyncService,
)
from app.integrations.calendar.reconciliation import CalendarReconciliationService
from app.integrations.calendar.rfc5545 import parse_ical_events, serialize_to_ical

GOOGLE_CALENDAR_LIVE_RUNTIME: str = "NOT_RUN_NO_AUTHORIZED_DEPLOYMENT_CREDENTIALS"

__all__ = [
    "GOOGLE_CALENDAR_LIVE_RUNTIME",
    "GOOGLE_CALENDAR_WEBHOOK_RUNTIME",
    "CalendarAdapter",
    "CalendarEventDTO",
    "CalendarEventResult",
    "CalendarPublishedEventHandler",
    "CalendarReconciliationService",
    "CalendarResourceResolver",
    "DefaultCalendarResourceResolver",
    "FakeCalendarAdapter",
    "InboundCalendarSyncService",
    "OutboundCalendarSyncService",
    "ReconciliationResult",
    "SyncChangesResult",
    "SyncStatus",
    "parse_ical_events",
    "serialize_to_ical",
]
