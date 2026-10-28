"""Consumer event handlers for notification engagement on canonical events.

Part of Phase 6 Pre-Live Evidence Closure.
Enforces:
- ConsumerEventHandler protocol implementation.
- Wrapped in ConsumerEventProcessor with transactional Inbox deduplication.
- Pure mapping from canonical EventEnvelope to typed NotificationCommand.
- Dispatches through NotificationDispatcherProtocol without duplicate emissions.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.auth.models import AuthenticationMethod, PrincipalType
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
from app.integrations.engagement.repository import NotificationOutboxRepository

logger = structlog.get_logger(__name__)


def _extract_valid_opaque_recipients(
    raw_recipients: list[Any] | None,
) -> tuple[list[str], bool]:
    """Extract valid opaque recipient references and detect prohibited contact PII."""
    import re

    valid_recipients: list[str] = []
    has_prohibited_pii = False
    if raw_recipients:
        for ref in raw_recipients:
            if not isinstance(ref, str) or not ref.strip():
                continue
            clean_ref = ref.strip()
            # Detect plaintext emails, phone numbers, or raw upstream database IDs
            digits_only = re.sub(r"[\s\-\(\)\.]", "", clean_ref)
            is_phone = (
                (
                    digits_only.startswith("+")
                    and digits_only[1:].isdigit()
                    and len(digits_only) >= 7
                )
                or (digits_only.isdigit() and len(digits_only) >= 7)
            )
            if (
                "@" in clean_ref
                or clean_ref.isdigit()
                or is_phone
                or any(
                    clean_ref.lower().startswith(p)
                    for p in ("mysql:", "iredu:", "students.id:", "staff.id:")
                )
            ):
                has_prohibited_pii = True
                continue
            valid_recipients.append(clean_ref)
    return valid_recipients, has_prohibited_pii


async def _dispatch_or_stage_command(
    *,
    command: NotificationCommand,
    session: AsyncSession,
    settings: NotificationSettings,
    dispatcher: NotificationDispatcherProtocol,
    is_provisional_recipient: bool = False,
    source_event_type: str = "",
    resource_id: str = "",
) -> None:
    """Route notification intent to durable outbox (Option C) or direct test dispatcher."""
    tenant_id = command.organization_scope_reference

    # Fail closed on provisional recipients if unauthorized under DEC-E8-02
    if is_provisional_recipient and not settings.allow_provisional_recipients:
        logger.warning(
            "notification_blocked_provisional_recipients_unauthorized",
            command_id=command.command_id,
            tenant_id=tenant_id,
            status="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
        )
        if settings.pipeline_mode == "outbox":
            outbox_repo = NotificationOutboxRepository(session)
            await outbox_repo.stage_command(
                command=command,
                dispatch_state="BLOCKED",
                block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
            )
        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type="notification.dispatch.blocked",
                actor_principal_id="system-notification-handler",
                actor_principal_type=PrincipalType.SERVICE.value,
                authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                resource_type="notification",
                resource_id=resource_id or command.command_id,
                outcome=AuditEventOutcome.DENIED,
                details={
                    "reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                    "source_event_type": source_event_type,
                    "command_id": command.command_id,
                    "provisional_recipients": command.recipient_references,
                },
            )
        )
        return

    # Authorized or explicit recipient references
    if settings.pipeline_mode == "outbox":
        outbox_repo = NotificationOutboxRepository(session)
        await outbox_repo.stage_command(
            command=command,
            dispatch_state="PENDING",
        )
    else:
        await dispatcher.dispatch(command=command, session=session)


class SchedulePublishedNotificationHandler:
    """Consumer handler for canonical schedule.published event to dispatch notification.

    Note:
    - Default recipient 'urn:e7:cohort:faculty' is a provisional engineering default
      pending institutional governance decision DEC-E8-02.
    - If event payload contains unresolvable recipients or prohibited contact PII,
      the notification is explicitly blocked under DEC-E8-02 without fallback.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        if settings is None:
            if dispatcher is not None:
                self.settings = NotificationSettings(
                    pipeline_mode="direct",
                    allow_provisional_recipients=True,
                )
            else:
                self.settings = NotificationSettings()
        else:
            self.settings = settings
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=self.settings)
        self.default_recipients = (
            default_recipients
            if default_recipients is not None
            else ["urn:e7:cohort:faculty"]
        )

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Construct canonical NotificationCommand and dispatch within transaction."""
        tenant_id = envelope.tenant_id
        if not tenant_id or not tenant_id.strip():
            logger.warning(
                "schedule_notification_dropped_missing_tenant",
                event_id=str(envelope.event_id),
                event_type=envelope.event_type,
            )
            return

        payload = envelope.payload
        schedule_id = str(payload.get("schedule_id", ""))
        version = int(payload.get("version", 1))

        # Check for recipient references in payload
        is_provisional = False
        if "recipients" in payload:
            raw_recipients = payload.get("recipients")
            valid_recipients, has_prohibited_pii = _extract_valid_opaque_recipients(raw_recipients)
            if not valid_recipients:
                # Event explicitly provided recipients, but they were unresolvable or contained PII.
                # Under DEC-E8-02, do not use unsupported fallbacks hiding missing authority.
                cmd_id = f"notif-pub-{envelope.event_id}"
                logger.warning(
                    "schedule_notification_blocked_unresolved_recipients",
                    event_id=str(envelope.event_id),
                    event_type=envelope.event_type,
                    schedule_id=schedule_id,
                    had_prohibited_pii=has_prohibited_pii,
                    status="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                )
                if self.settings.pipeline_mode == "outbox":
                    outbox_repo = NotificationOutboxRepository(session)
                    await outbox_repo.stage_command(
                        tenant_id=tenant_id,
                        command_id=cmd_id,
                        correlation_reference=envelope.correlation_id or str(envelope.event_id),
                        causation_reference=str(envelope.event_id),
                        trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT.value,
                        source_event_name="schedule.published",
                        source_entity_reference=schedule_id,
                        recipient_references=[],
                        notification_context={
                            "template_id": "EXAM_SCHEDULE_PUBLISHED",
                            "schedule_id": schedule_id,
                            "version": version,
                        },
                        dispatch_state="BLOCKED",
                        block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                    )
                audit_repo = AuditRepository(session)
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=tenant_id,
                        event_type="notification.dispatch.blocked",
                        actor_principal_id="system-notification-handler",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="notification",
                        resource_id=cmd_id,
                        outcome=AuditEventOutcome.DENIED,
                        details={
                            "reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                            "source_event_type": envelope.event_type,
                            "source_event_id": str(envelope.event_id),
                            "schedule_id": schedule_id,
                            "had_prohibited_pii": has_prohibited_pii,
                        },
                    )
                )
                return
        else:
            # Payload did not specify recipients; check configured default_recipients
            raw_recipients = self.default_recipients
            valid_recipients, has_prohibited_pii = _extract_valid_opaque_recipients(raw_recipients)
            if not valid_recipients:
                cmd_id = f"notif-pub-{envelope.event_id}"
                logger.warning(
                    "schedule_notification_blocked_unresolved_recipients",
                    event_id=str(envelope.event_id),
                    event_type=envelope.event_type,
                    schedule_id=schedule_id,
                    had_prohibited_pii=has_prohibited_pii,
                    status="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                )
                if self.settings.pipeline_mode == "outbox":
                    outbox_repo = NotificationOutboxRepository(session)
                    await outbox_repo.stage_command(
                        tenant_id=tenant_id,
                        command_id=cmd_id,
                        correlation_reference=envelope.correlation_id or str(envelope.event_id),
                        causation_reference=str(envelope.event_id),
                        trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT.value,
                        source_event_name="schedule.published",
                        source_entity_reference=schedule_id,
                        recipient_references=[],
                        notification_context={
                            "template_id": "EXAM_SCHEDULE_PUBLISHED",
                            "schedule_id": schedule_id,
                            "version": version,
                        },
                        dispatch_state="BLOCKED",
                        block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                    )
                audit_repo = AuditRepository(session)
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=tenant_id,
                        event_type="notification.dispatch.blocked",
                        actor_principal_id="system-notification-handler",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="notification",
                        resource_id=cmd_id,
                        outcome=AuditEventOutcome.DENIED,
                        details={
                            "reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                            "source_event_type": envelope.event_type,
                            "source_event_id": str(envelope.event_id),
                            "schedule_id": schedule_id,
                            "had_prohibited_pii": has_prohibited_pii,
                        },
                    )
                )
                return
            is_provisional = True

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
            recipient_references=valid_recipients,
            notification_context={
                "template_id": "EXAM_SCHEDULE_PUBLISHED",
                "schedule_id": schedule_id,
                "version": version,
            },
        )
        await _dispatch_or_stage_command(
            command=cmd,
            session=session,
            settings=self.settings,
            dispatcher=self.dispatcher,
            is_provisional_recipient=is_provisional,
            source_event_type=envelope.event_type,
            resource_id=f"notif-pub-{envelope.event_id}",
        )


class ScheduleChangedNotificationHandler:
    """Consumer handler for canonical schedule.changed event to dispatch notification.

    Note:
    - Default recipient 'urn:e7:cohort:faculty-all' is a provisional engineering default
      pending institutional governance decision DEC-E8-02.
    - If event payload contains unresolvable recipients or prohibited contact PII,
      the notification is explicitly blocked under DEC-E8-02 without fallback.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        if settings is None:
            if dispatcher is not None:
                self.settings = NotificationSettings(
                    pipeline_mode="direct",
                    allow_provisional_recipients=True,
                )
            else:
                self.settings = NotificationSettings()
        else:
            self.settings = settings
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=self.settings)
        self.default_recipients = (
            default_recipients
            if default_recipients is not None
            else ["urn:e7:cohort:faculty-all"]
        )

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Construct canonical NotificationCommand and dispatch within transaction."""
        tenant_id = envelope.tenant_id
        if not tenant_id or not tenant_id.strip():
            logger.warning(
                "schedule_changed_notification_dropped_missing_tenant",
                event_id=str(envelope.event_id),
                event_type=envelope.event_type,
            )
            return

        payload = envelope.payload
        schedule_id = str(payload.get("schedule_id", ""))
        old_version = int(payload.get("old_version", 1))
        new_version = int(payload.get("new_version", 2))
        repair_reason = str(payload.get("repair_reason", "resource.unavailable"))

        # Check for recipient references in payload
        is_provisional = False
        if "recipients" in payload:
            raw_recipients = payload.get("recipients")
            valid_recipients, has_prohibited_pii = _extract_valid_opaque_recipients(raw_recipients)
            if not valid_recipients:
                # Under DEC-E8-02, do not use unsupported fallbacks hiding missing authority.
                cmd_id = f"notif-chg-{envelope.event_id}"
                logger.warning(
                    "schedule_changed_notification_blocked_unresolved_recipients",
                    event_id=str(envelope.event_id),
                    event_type=envelope.event_type,
                    schedule_id=schedule_id,
                    had_prohibited_pii=has_prohibited_pii,
                    status="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                )
                if self.settings.pipeline_mode == "outbox":
                    outbox_repo = NotificationOutboxRepository(session)
                    await outbox_repo.stage_command(
                        tenant_id=tenant_id,
                        command_id=cmd_id,
                        correlation_reference=envelope.correlation_id or str(envelope.event_id),
                        causation_reference=str(envelope.event_id),
                        trigger_kind=TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION.value,
                        source_event_name="schedule.changed",
                        source_entity_reference=schedule_id,
                        recipient_references=[],
                        notification_context={
                            "template_id": "EXAM_SCHEDULE_CHANGED",
                            "schedule_id": schedule_id,
                            "old_version": old_version,
                            "new_version": new_version,
                            "repair_reason": repair_reason,
                        },
                        dispatch_state="BLOCKED",
                        block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                    )
                audit_repo = AuditRepository(session)
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=tenant_id,
                        event_type="notification.dispatch.blocked",
                        actor_principal_id="system-notification-handler",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="notification",
                        resource_id=cmd_id,
                        outcome=AuditEventOutcome.DENIED,
                        details={
                            "reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                            "source_event_type": envelope.event_type,
                            "source_event_id": str(envelope.event_id),
                            "schedule_id": schedule_id,
                            "had_prohibited_pii": has_prohibited_pii,
                        },
                    )
                )
                return
        else:
            raw_recipients = self.default_recipients
            valid_recipients, has_prohibited_pii = _extract_valid_opaque_recipients(raw_recipients)
            if not valid_recipients:
                cmd_id = f"notif-chg-{envelope.event_id}"
                logger.warning(
                    "schedule_changed_notification_blocked_unresolved_recipients",
                    event_id=str(envelope.event_id),
                    event_type=envelope.event_type,
                    schedule_id=schedule_id,
                    had_prohibited_pii=has_prohibited_pii,
                    status="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                )
                if self.settings.pipeline_mode == "outbox":
                    outbox_repo = NotificationOutboxRepository(session)
                    await outbox_repo.stage_command(
                        tenant_id=tenant_id,
                        command_id=cmd_id,
                        correlation_reference=envelope.correlation_id or str(envelope.event_id),
                        causation_reference=str(envelope.event_id),
                        trigger_kind=TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION.value,
                        source_event_name="schedule.changed",
                        source_entity_reference=schedule_id,
                        recipient_references=[],
                        notification_context={
                            "template_id": "EXAM_SCHEDULE_CHANGED",
                            "schedule_id": schedule_id,
                            "old_version": old_version,
                            "new_version": new_version,
                            "repair_reason": repair_reason,
                        },
                        dispatch_state="BLOCKED",
                        block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                    )
                audit_repo = AuditRepository(session)
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=tenant_id,
                        event_type="notification.dispatch.blocked",
                        actor_principal_id="system-notification-handler",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="notification",
                        resource_id=cmd_id,
                        outcome=AuditEventOutcome.DENIED,
                        details={
                            "reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                            "source_event_type": envelope.event_type,
                            "source_event_id": str(envelope.event_id),
                            "schedule_id": schedule_id,
                            "had_prohibited_pii": has_prohibited_pii,
                        },
                    )
                )
                return
            is_provisional = True

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
            recipient_references=valid_recipients,
            notification_context={
                "template_id": "EXAM_SCHEDULE_CHANGED",
                "schedule_id": schedule_id,
                "old_version": old_version,
                "new_version": new_version,
                "repair_reason": repair_reason,
            },
        )
        await _dispatch_or_stage_command(
            command=cmd,
            session=session,
            settings=self.settings,
            dispatcher=self.dispatcher,
            is_provisional_recipient=is_provisional,
            source_event_type=envelope.event_type,
            resource_id=f"notif-chg-{envelope.event_id}",
        )


class TaskBlockedNotificationHandler:
    """Consumer handler for canonical task.blocked event to dispatch notification.

    Note:
    - Default recipient 'urn:e7:role:task_manager' and template 'TASK_BLOCKED_ALERT' are
      provisional engineering defaults pending institutional governance decision DEC-E8-02.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        if settings is None:
            if dispatcher is not None:
                self.settings = NotificationSettings(
                    pipeline_mode="direct",
                    allow_provisional_recipients=True,
                )
            else:
                self.settings = NotificationSettings()
        else:
            self.settings = settings
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=self.settings)
        self.default_recipients = default_recipients or ["urn:e7:role:task_manager"]

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Construct canonical NotificationCommand and dispatch within transaction."""
        tenant_id = envelope.tenant_id
        if not tenant_id or not tenant_id.strip():
            raise ValueError(
                f"task.blocked event '{envelope.event_id}' has missing or empty tenant_id. "
                "Tenant identity is mandatory for organization_scope_reference."
            )

        payload = envelope.payload
        task_id = str(payload.get("task_id") or envelope.subject_id or "")
        plan_id = str(payload.get("plan_id", ""))
        temporary_id = str(payload.get("temporary_id", ""))
        blocked_reason = str(payload.get("blocked_reason", "Task progress blocked"))

        is_provisional = False
        if "recipients" in payload:
            raw_recipients = payload.get("recipients")
            valid_recipients, has_prohibited_pii = _extract_valid_opaque_recipients(raw_recipients)
            if not valid_recipients:
                cmd_id = f"notif-blk-{envelope.event_id}"
                if self.settings.pipeline_mode == "outbox":
                    outbox_repo = NotificationOutboxRepository(session)
                    await outbox_repo.stage_command(
                        tenant_id=tenant_id,
                        command_id=cmd_id,
                        correlation_reference=envelope.correlation_id or str(envelope.event_id),
                        causation_reference=str(envelope.event_id),
                        trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT.value,
                        source_event_name="task.blocked",
                        source_entity_reference=task_id,
                        recipient_references=[],
                        notification_context={
                            "template_id": "TASK_BLOCKED_ALERT",
                            "task_id": task_id,
                            "plan_id": plan_id,
                            "temporary_id": temporary_id,
                            "blocked_reason": blocked_reason,
                        },
                        dispatch_state="BLOCKED",
                        block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                    )
                audit_repo = AuditRepository(session)
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=tenant_id,
                        event_type="notification.dispatch.blocked",
                        actor_principal_id="system-notification-handler",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="notification",
                        resource_id=cmd_id,
                        outcome=AuditEventOutcome.DENIED,
                        details={
                            "reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                            "source_event_type": envelope.event_type,
                            "source_event_id": str(envelope.event_id),
                            "task_id": task_id,
                            "had_prohibited_pii": has_prohibited_pii,
                        },
                    )
                )
                return
        else:
            valid_recipients = self.default_recipients
            is_provisional = True

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
            recipient_references=list(valid_recipients),
            notification_context={
                "template_id": "TASK_BLOCKED_ALERT",
                "task_id": task_id,
                "plan_id": plan_id,
                "temporary_id": temporary_id,
                "blocked_reason": blocked_reason,
            },
        )
        await _dispatch_or_stage_command(
            command=cmd,
            session=session,
            settings=self.settings,
            dispatcher=self.dispatcher,
            is_provisional_recipient=is_provisional,
            source_event_type=envelope.event_type,
            resource_id=f"notif-blk-{envelope.event_id}",
        )


class WorkflowTaskCreatedNotificationHandler:
    """Consumer handler for canonical workflow.task.created event to dispatch notification.

    Note:
    - Default recipient 'urn:e7:role:workflow_assignee' and template 'WORKFLOW_TASK_ASSIGNED'
      are provisional engineering defaults pending institutional governance decision DEC-E8-02.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        if settings is None:
            if dispatcher is not None:
                self.settings = NotificationSettings(
                    pipeline_mode="direct",
                    allow_provisional_recipients=True,
                )
            else:
                self.settings = NotificationSettings()
        else:
            self.settings = settings
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=self.settings)
        self.default_recipients = default_recipients or ["urn:e7:role:workflow_assignee"]

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Construct canonical NotificationCommand and dispatch within transaction."""
        tenant_id = envelope.tenant_id
        if not tenant_id or not tenant_id.strip():
            raise ValueError(
                f"workflow.task.created event '{envelope.event_id}' has missing tenant_id. "
                "Tenant identity is mandatory for organization_scope_reference."
            )

        payload = envelope.payload
        task_id = str(payload.get("workflow_task_id") or envelope.subject_id or "")
        instance_id = str(payload.get("workflow_instance_id", ""))
        task_name = str(payload.get("task_name", "Workflow Task"))
        task_def_key = str(payload.get("task_definition_key", ""))
        due_at = payload.get("due_at")

        # Resolve recipient reference: prefer opaque assignee, then candidate roles, then default
        is_provisional = False
        recipients = payload.get("recipients")
        if recipients:
            valid_recipients, _ = _extract_valid_opaque_recipients(recipients)
            if not valid_recipients:
                cmd_id = f"notif-tsk-{envelope.event_id}"
                if self.settings.pipeline_mode == "outbox":
                    outbox_repo = NotificationOutboxRepository(session)
                    await outbox_repo.stage_command(
                        tenant_id=tenant_id,
                        command_id=cmd_id,
                        correlation_reference=envelope.correlation_id or str(envelope.event_id),
                        causation_reference=str(envelope.event_id),
                        trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT.value,
                        source_event_name="workflow.task.created",
                        source_entity_reference=task_id,
                        recipient_references=[],
                        notification_context={
                            "template_id": "WORKFLOW_TASK_ASSIGNED",
                            "workflow_task_id": task_id,
                            "workflow_instance_id": instance_id,
                            "task_name": task_name,
                            "task_definition_key": task_def_key,
                        },
                        dispatch_state="BLOCKED",
                        block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                    )
                audit_repo = AuditRepository(session)
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=tenant_id,
                        event_type="notification.dispatch.blocked",
                        actor_principal_id="system-notification-handler",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="notification",
                        resource_id=cmd_id,
                        outcome=AuditEventOutcome.DENIED,
                        details={
                            "reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                            "source_event_type": envelope.event_type,
                            "source_event_id": str(envelope.event_id),
                            "workflow_task_id": task_id,
                        },
                    )
                )
                return
        else:
            assignee = payload.get("assignee")
            if assignee and isinstance(assignee, str) and "@" not in assignee:
                clean_assignee = assignee.strip()
                if not (clean_assignee.startswith("user:") or clean_assignee.startswith("urn:")):
                    valid_recipients = [f"user:{clean_assignee}"]
                else:
                    valid_recipients = [clean_assignee]
                is_provisional = False
            elif payload.get("candidate_roles"):
                c_roles = payload.get("candidate_roles")
                if isinstance(c_roles, (list, tuple)):
                    valid_recipients = [f"urn:e7:role:{r}" for r in c_roles if isinstance(r, str)]
                else:
                    valid_recipients = self.default_recipients
                is_provisional = True
            else:
                valid_recipients = self.default_recipients
                is_provisional = True

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
            recipient_references=list(valid_recipients),
            notification_context=context,
        )
        await _dispatch_or_stage_command(
            command=cmd,
            session=session,
            settings=self.settings,
            dispatcher=self.dispatcher,
            is_provisional_recipient=is_provisional,
            source_event_type=envelope.event_type,
            resource_id=f"notif-tsk-{envelope.event_id}",
        )


class WorkflowSlaWarningNotificationHandler:
    """Consumer handler for canonical workflow.sla.warning event to dispatch notification.

    Note:
    - Default recipient 'urn:e7:role:escalation_manager' and template 'WORKFLOW_SLA_WARNING'
      are provisional engineering defaults pending institutional governance decision DEC-E8-02.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcherProtocol | None = None,
        default_recipients: list[str] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        if settings is None:
            if dispatcher is not None:
                self.settings = NotificationSettings(
                    pipeline_mode="direct",
                    allow_provisional_recipients=True,
                )
            else:
                self.settings = NotificationSettings()
        else:
            self.settings = settings
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=self.settings)
        self.default_recipients = default_recipients or ["urn:e7:role:escalation_manager"]

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Construct canonical NotificationCommand and dispatch within transaction."""
        tenant_id = envelope.tenant_id
        if not tenant_id or not tenant_id.strip():
            raise ValueError(
                f"workflow.sla.warning event '{envelope.event_id}' has missing or empty tenant_id. "
                "Tenant identity is mandatory for organization_scope_reference."
            )

        payload = envelope.payload
        task_id = str(payload.get("workflow_task_id") or envelope.subject_id or "")
        policy_key = str(payload.get("policy_key", "default_sla"))
        warning_at = str(payload.get("warning_at", ""))
        breach_at = str(payload.get("breach_at", ""))
        action = str(payload.get("action", "NOTIFY"))

        is_provisional = False
        if "recipients" in payload:
            raw_recipients = payload.get("recipients")
            valid_recipients, _ = _extract_valid_opaque_recipients(raw_recipients)
            if not valid_recipients:
                cmd_id = f"notif-sla-{envelope.event_id}"
                if self.settings.pipeline_mode == "outbox":
                    outbox_repo = NotificationOutboxRepository(session)
                    await outbox_repo.stage_command(
                        tenant_id=tenant_id,
                        command_id=cmd_id,
                        correlation_reference=envelope.correlation_id or str(envelope.event_id),
                        causation_reference=str(envelope.event_id),
                        trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT.value,
                        source_event_name="workflow.sla.warning",
                        source_entity_reference=task_id,
                        recipient_references=[],
                        notification_context={
                            "template_id": "WORKFLOW_SLA_WARNING",
                            "workflow_task_id": task_id,
                            "policy_key": policy_key,
                            "warning_at": warning_at,
                            "breach_at": breach_at,
                            "action": action,
                        },
                        dispatch_state="BLOCKED",
                        block_reason="BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                    )
                audit_repo = AuditRepository(session)
                await audit_repo.append(
                    AuditEventCreate(
                        tenant_id=tenant_id,
                        event_type="notification.dispatch.blocked",
                        actor_principal_id="system-notification-handler",
                        actor_principal_type=PrincipalType.SERVICE.value,
                        authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                        resource_type="notification",
                        resource_id=cmd_id,
                        outcome=AuditEventOutcome.DENIED,
                        details={
                            "reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02",
                            "source_event_type": envelope.event_type,
                            "source_event_id": str(envelope.event_id),
                            "workflow_task_id": task_id,
                        },
                    )
                )
                return
        else:
            valid_recipients = self.default_recipients
            is_provisional = True

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
            recipient_references=list(valid_recipients),
            notification_context={
                "template_id": "WORKFLOW_SLA_WARNING",
                "workflow_task_id": task_id,
                "policy_key": policy_key,
                "warning_at": warning_at,
                "breach_at": breach_at,
                "action": action,
            },
        )
        await _dispatch_or_stage_command(
            command=cmd,
            session=session,
            settings=self.settings,
            dispatcher=self.dispatcher,
            is_provisional_recipient=is_provisional,
            source_event_type=envelope.event_type,
            resource_id=f"notif-sla-{envelope.event_id}",
        )


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
        if settings is None:
            if dispatcher is not None:
                self.settings = NotificationSettings(
                    pipeline_mode="direct",
                    allow_provisional_recipients=True,
                )
            else:
                self.settings = NotificationSettings()
        else:
            self.settings = settings
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=self.settings)
        self.published_handler = published_handler or SchedulePublishedNotificationHandler(
            dispatcher=self.dispatcher, settings=self.settings
        )
        self.changed_handler = changed_handler or ScheduleChangedNotificationHandler(
            dispatcher=self.dispatcher, settings=self.settings
        )
        self.task_blocked_handler = task_blocked_handler or TaskBlockedNotificationHandler(
            dispatcher=self.dispatcher, settings=self.settings
        )
        self.task_created_handler = task_created_handler or WorkflowTaskCreatedNotificationHandler(
            dispatcher=self.dispatcher, settings=self.settings
        )
        self.sla_warning_handler = sla_warning_handler or WorkflowSlaWarningNotificationHandler(
            dispatcher=self.dispatcher, settings=self.settings
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
