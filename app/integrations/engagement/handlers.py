"""Consumer event handlers for notification engagement on canonical events.

Part of Phase 6 Pre-Live Evidence Closure.
Enforces:
- ConsumerEventHandler protocol implementation.
- Wrapped in ConsumerEventProcessor with transactional Inbox deduplication.
- Pure mapping from canonical EventEnvelope to typed NotificationCommand.
- Dispatches through NotificationDispatcherProtocol without duplicate emissions.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.schemas import EventEnvelope
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.dispatcher import (
    NotificationDispatcherProtocol,
    get_notification_dispatcher,
)
from app.integrations.engagement.models import (
    NotificationCommand,
    NotificationTrigger,
    TriggerKind,
)


class SchedulePublishedNotificationHandler:
    """Consumer handler for canonical schedule.published event to dispatch notification."""

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=settings)
        self.default_recipients = default_recipients or ["urn:e7:cohort:faculty"]

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
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=settings)
        self.default_recipients = default_recipients or ["urn:e7:cohort:faculty-all"]

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


class NotificationConsumerHandler:
    """Composite ConsumerEventHandler routing domain events to notification handlers.

    Enforces:
    - Implements ConsumerEventHandler protocol.
    - Routes 'schedule.published' to SchedulePublishedNotificationHandler.
    - Routes 'schedule.changed' to ScheduleChangedNotificationHandler.
    - Safe no-op on non-notification events.
    - Preserves single transactional AsyncSession across inbox deduplication and dispatch.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        settings: NotificationSettings | None = None,
        published_handler: SchedulePublishedNotificationHandler | None = None,
        changed_handler: ScheduleChangedNotificationHandler | None = None,
    ) -> None:
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=settings)
        self.published_handler = published_handler or SchedulePublishedNotificationHandler(
            dispatcher=self.dispatcher, settings=settings
        )
        self.changed_handler = changed_handler or ScheduleChangedNotificationHandler(
            dispatcher=self.dispatcher, settings=settings
        )

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Route event envelope to appropriate domain notification handler."""
        if envelope.event_type == "schedule.published":
            await self.published_handler.handle(envelope=envelope, session=session)
        elif envelope.event_type == "schedule.changed":
            await self.changed_handler.handle(envelope=envelope, session=session)


def create_notification_consumer_processor(
    uow_factory: Any,
    dispatcher: NotificationDispatcherProtocol | None = None,
    settings: NotificationSettings | None = None,
    consumer_name: str = "e7.notifications.consumer",
) -> Any:
    """Construct canonical ConsumerEventProcessor for transactional notification dispatch."""
    from app.events.inbox.processor import ConsumerEventProcessor

    handler = NotificationConsumerHandler(dispatcher=dispatcher, settings=settings)
    return ConsumerEventProcessor(
        consumer_name=consumer_name,
        uow_factory=uow_factory,
        handler=handler,
    )


__all__ = [
    "NotificationConsumerHandler",
    "ScheduleChangedNotificationHandler",
    "SchedulePublishedNotificationHandler",
    "create_notification_consumer_processor",
]
