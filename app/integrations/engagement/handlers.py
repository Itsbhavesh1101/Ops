"""Consumer event handlers for notification engagement on canonical events.

Part of Phase 6 Pre-Live Evidence Closure.
Enforces:
- ConsumerEventHandler protocol implementation.
- Wrapped in ConsumerEventProcessor with transactional Inbox deduplication.
- Pure mapping from canonical EventEnvelope to typed NotificationCommand.
- Dispatches through NotificationDispatcherProtocol without duplicate emissions.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.schemas import EventEnvelope
from app.integrations.engagement.dispatcher import NotificationDispatcherProtocol
from app.integrations.engagement.models import (
    NotificationCommand,
    NotificationTrigger,
    TriggerKind,
)


class SchedulePublishedNotificationHandler:
    """Consumer handler for canonical schedule.published event to dispatch notification."""

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol,
        default_recipients: list[str] | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.default_recipients = default_recipients or ["faculty@school.edu"]

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Construct canonical NotificationCommand and dispatch within transaction."""
        tenant_id = envelope.tenant_id
        if not tenant_id:
            return

        payload = envelope.payload
        schedule_id = str(payload.get("schedule_id", ""))
        version = int(payload.get("version", 1))
        recipients = payload.get("recipients") or self.default_recipients

        cmd = NotificationCommand(
            command_id=f"notif-pub-{envelope.event_id}",
            correlation_reference=envelope.correlation_id or str(envelope.event_id),
            causation_reference=str(envelope.event_id),
            organization_scope_reference=tenant_id,
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="schedule.published",
                source_entity_reference=schedule_id,
            ),
            recipient_references=list(recipients),
            notification_context={
                "template_id": "EXAM_SCHEDULE_PUBLISHED",
                "schedule_id": schedule_id,
                "version": version,
            },
        )
        await self.dispatcher.dispatch(command=cmd, session=session)


class ScheduleChangedNotificationHandler:
    """Consumer handler for canonical schedule.changed event to dispatch notification."""

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol,
        default_recipients: list[str] | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.default_recipients = default_recipients or ["faculty-all@school.edu"]

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Construct canonical NotificationCommand and dispatch within transaction."""
        tenant_id = envelope.tenant_id
        if not tenant_id:
            return

        payload = envelope.payload
        schedule_id = str(payload.get("schedule_id", ""))
        old_version = int(payload.get("old_version", 1))
        new_version = int(payload.get("new_version", 2))
        repair_reason = str(payload.get("repair_reason", "resource.unavailable"))
        recipients = payload.get("recipients") or self.default_recipients

        cmd = NotificationCommand(
            command_id=f"notif-chg-{envelope.event_id}",
            correlation_reference=envelope.correlation_id or str(envelope.event_id),
            causation_reference=str(envelope.event_id),
            organization_scope_reference=tenant_id,
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="schedule.changed",
                source_entity_reference=schedule_id,
            ),
            recipient_references=list(recipients),
            notification_context={
                "template_id": "EXAM_SCHEDULE_CHANGED",
                "schedule_id": schedule_id,
                "old_version": old_version,
                "new_version": new_version,
                "repair_reason": repair_reason,
            },
        )
        await self.dispatcher.dispatch(command=cmd, session=session)


__all__ = [
    "SchedulePublishedNotificationHandler",
    "ScheduleChangedNotificationHandler",
]
