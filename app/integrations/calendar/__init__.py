"""Canonical Calendar Integration boundary for E-7 Operations Engine."""

from app.integrations.calendar.adapter import CalendarAdapter, FakeCalendarAdapter
from app.integrations.calendar.auth import (
    CalendarTokenProvider,
    GoogleADCTokenProvider,
    GoogleCalendarAuthError,
    GoogleTokenProvider,
    create_token_provider,
)
from app.integrations.calendar.codec import (
    GOOGLE_EVENT_ID_PATTERN,
    google_calendar_event_id,
    is_encoded_e7_google_event_id,
    is_legal_google_calendar_event_id,
)
from app.integrations.calendar.config import GoogleCalendarSettings
from app.integrations.calendar.dtos import (
    CalendarEventDTO,
    CalendarEventResult,
    ReconciliationResult,
    SyncChangesResult,
    SyncStatus,
)
from app.integrations.calendar.google_adapter import GoogleCalendarAdapter
from app.integrations.calendar.handlers import SchedulePublishedCalendarHandler
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
from app.integrations.calendar.transport import (
    GoogleCalendarApiError,
    GoogleCalendarTransport,
)
from app.integrations.calendar.watch import (
    CalendarWatchRepository,
    CalendarWatchService,
)
from app.integrations.calendar.webhook import (
    GoogleCalendarWebhookHandler,
    WebhookProcessingResult,
)
from app.integrations.calendar.worker import CalendarWorker

GOOGLE_CALENDAR_LIVE_RUNTIME: str = "PHASE_7_IMPLEMENTED"

__all__ = [
    "GOOGLE_CALENDAR_LIVE_RUNTIME",
    "GOOGLE_CALENDAR_WEBHOOK_RUNTIME",
    "GOOGLE_EVENT_ID_PATTERN",
    "CalendarAdapter",
    "CalendarEventDTO",
    "CalendarEventResult",
    "CalendarPublishedEventHandler",
    "CalendarReconciliationService",
    "CalendarResourceResolver",
    "CalendarTokenProvider",
    "CalendarWatchRepository",
    "CalendarWatchService",
    "CalendarWorker",
    "DefaultCalendarResourceResolver",
    "FakeCalendarAdapter",
    "GoogleADCTokenProvider",
    "GoogleCalendarAdapter",
    "GoogleCalendarApiError",
    "GoogleCalendarAuthError",
    "GoogleCalendarSettings",
    "GoogleCalendarTransport",
    "GoogleCalendarWebhookHandler",
    "GoogleTokenProvider",
    "InboundCalendarSyncService",
    "OutboundCalendarSyncService",
    "ReconciliationResult",
    "SchedulePublishedCalendarHandler",
    "SyncChangesResult",
    "SyncStatus",
    "WebhookProcessingResult",
    "create_token_provider",
    "google_calendar_event_id",
    "is_encoded_e7_google_event_id",
    "is_legal_google_calendar_event_id",
    "parse_ical_events",
    "serialize_to_ical",
]
