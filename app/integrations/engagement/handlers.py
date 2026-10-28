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
                trigger_kind=TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION,
                business_condition="SCHEDULE_CHANGE_NOTICE",
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


class TaskBlockedNotificationHandler:
    """Consumer handler for canonical task.blocked event to dispatch notification."""

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=settings)
        self.default_recipients = default_recipients or ["urn:e7:role:task_manager"]

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
        task_id = str(payload.get("task_id") or envelope.subject_id or "")
        plan_id = str(payload.get("plan_id", ""))
        temporary_id = str(payload.get("temporary_id", ""))
        blocked_reason = str(payload.get("blocked_reason", "Task progress blocked"))
        recipients = payload.get("recipients") or self.default_recipients

        cmd = NotificationCommand(
            command_id=f"notif-blk-{envelope.event_id}",
            correlation_reference=envelope.correlation_id or str(envelope.event_id),
            causation_reference=str(envelope.event_id),
            organization_scope_reference=tenant_id,
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="task.blocked",
                source_entity_reference=task_id,
            ),
            recipient_references=list(recipients),
            notification_context={
                "template_id": "TASK_BLOCKED_ALERT",
                "task_id": task_id,
                "plan_id": plan_id,
                "temporary_id": temporary_id,
                "blocked_reason": blocked_reason,
            },
        )
        await self.dispatcher.dispatch(command=cmd, session=session)


class WorkflowTaskCreatedNotificationHandler:
    """Consumer handler for canonical workflow.task.created event to dispatch notification."""

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=settings)
        self.default_recipients = default_recipients or ["urn:e7:role:workflow_assignee"]

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
        task_id = str(payload.get("workflow_task_id") or envelope.subject_id or "")
        instance_id = str(payload.get("workflow_instance_id", ""))
        task_name = str(payload.get("task_name", "Workflow Task"))
        task_def_key = str(payload.get("task_definition_key", ""))
        due_at = payload.get("due_at")

        # Resolve recipient reference: prefer opaque assignee, then candidate roles, then default
        recipients = payload.get("recipients")
        if not recipients:
            assignee = payload.get("assignee")
            if assignee and isinstance(assignee, str) and "@" not in assignee:
                clean_assignee = assignee.strip()
                if not (clean_assignee.startswith("user:") or clean_assignee.startswith("urn:")):
                    recipients = [f"user:{clean_assignee}"]
                else:
                    recipients = [clean_assignee]
            elif payload.get("candidate_roles"):
                c_roles = payload.get("candidate_roles")
                if isinstance(c_roles, (list, tuple)):
                    recipients = [f"urn:e7:role:{r}" for r in c_roles if isinstance(r, str)]
            if not recipients:
                recipients = self.default_recipients

        context: dict[str, Any] = {
            "template_id": "WORKFLOW_TASK_ASSIGNED",
            "workflow_task_id": task_id,
            "workflow_instance_id": instance_id,
            "task_name": task_name,
            "task_definition_key": task_def_key,
        }
        if due_at:
            context["due_at"] = str(due_at)

        cmd = NotificationCommand(
            command_id=f"notif-tsk-{envelope.event_id}",
            correlation_reference=envelope.correlation_id or str(envelope.event_id),
            causation_reference=str(envelope.event_id),
            organization_scope_reference=tenant_id,
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="workflow.task.created",
                source_entity_reference=task_id,
            ),
            recipient_references=list(recipients),
            notification_context=context,
        )
        await self.dispatcher.dispatch(command=cmd, session=session)


class WorkflowSlaWarningNotificationHandler:
    """Consumer handler for canonical workflow.sla.warning event to dispatch notification."""

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=settings)
        self.default_recipients = default_recipients or ["urn:e7:role:escalation_manager"]

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
        task_id = str(payload.get("workflow_task_id") or envelope.subject_id or "")
        policy_key = str(payload.get("policy_key", "default_sla"))
        warning_at = str(payload.get("warning_at", ""))
        breach_at = str(payload.get("breach_at", ""))
        action = str(payload.get("action", "NOTIFY"))
        recipients = payload.get("recipients") or self.default_recipients

        cmd = NotificationCommand(
            command_id=f"notif-sla-{envelope.event_id}",
            correlation_reference=envelope.correlation_id or str(envelope.event_id),
            causation_reference=str(envelope.event_id),
            organization_scope_reference=tenant_id,
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="workflow.sla.warning",
                source_entity_reference=task_id,
            ),
            recipient_references=list(recipients),
            notification_context={
                "template_id": "WORKFLOW_SLA_WARNING",
                "workflow_task_id": task_id,
                "policy_key": policy_key,
                "warning_at": warning_at,
                "breach_at": breach_at,
                "action": action,
            },
        )
        await self.dispatcher.dispatch(command=cmd, session=session)


class NotificationConsumerHandler:
    """Composite ConsumerEventHandler routing domain events to notification handlers.

    Enforces:
    - Implements ConsumerEventHandler protocol.
    - Routes 'schedule.published' to SchedulePublishedNotificationHandler.
    - Routes 'schedule.changed' to ScheduleChangedNotificationHandler.
    - Routes 'task.blocked' to TaskBlockedNotificationHandler.
    - Routes 'workflow.task.created' to WorkflowTaskCreatedNotificationHandler.
    - Routes 'workflow.sla.warning' to WorkflowSlaWarningNotificationHandler.
    - Safe no-op on non-notification events.
    - Preserves single transactional AsyncSession across inbox deduplication and dispatch.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        settings: NotificationSettings | None = None,
        published_handler: SchedulePublishedNotificationHandler | None = None,
        changed_handler: ScheduleChangedNotificationHandler | None = None,
        task_blocked_handler: TaskBlockedNotificationHandler | None = None,
        task_created_handler: WorkflowTaskCreatedNotificationHandler | None = None,
        sla_warning_handler: WorkflowSlaWarningNotificationHandler | None = None,
    ) -> None:
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=settings)
        self.published_handler = published_handler or SchedulePublishedNotificationHandler(
            dispatcher=self.dispatcher, settings=settings
        )
        self.changed_handler = changed_handler or ScheduleChangedNotificationHandler(
            dispatcher=self.dispatcher, settings=settings
        )
        self.task_blocked_handler = task_blocked_handler or TaskBlockedNotificationHandler(
            dispatcher=self.dispatcher, settings=settings
        )
        self.task_created_handler = task_created_handler or WorkflowTaskCreatedNotificationHandler(
            dispatcher=self.dispatcher, settings=settings
        )
        self.sla_warning_handler = sla_warning_handler or WorkflowSlaWarningNotificationHandler(
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
        elif envelope.event_type == "task.blocked":
            await self.task_blocked_handler.handle(envelope=envelope, session=session)
        elif envelope.event_type == "workflow.task.created":
            await self.task_created_handler.handle(envelope=envelope, session=session)
        elif envelope.event_type == "workflow.sla.warning":
            await self.sla_warning_handler.handle(envelope=envelope, session=session)


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
    "TaskBlockedNotificationHandler",
    "WorkflowSlaWarningNotificationHandler",
    "WorkflowTaskCreatedNotificationHandler",
    "create_notification_consumer_processor",
]
