"""Background delivery worker for durable notification outbox commands (Phase DQ-4 Option C).

Enforces:
- Decouples remote E-8 notification dispatch from domain event consumption transactions.
- Periodic batch polling of claimed notification commands with lease management.
- Execution of network I/O (HTTP / RabbitMQ) strictly outside database transactions.
- Automatic exponential retry backoff on recoverable delivery failures.
- Append-only audit logging for dispatch successes and failures.
- Clean shutdown via asyncio.Event.
"""

import asyncio
from uuid import uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.auth.models import AuthenticationMethod, PrincipalType
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


class NotificationDeliveryWorker:
    """Dedicated background delivery worker that dispatches staged notification outbox commands."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        dispatcher: NotificationDispatcherProtocol | None = None,
        settings: NotificationSettings | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or NotificationSettings()
        self.dispatcher = dispatcher or get_notification_dispatcher(settings=self.settings)
        self.worker_id = worker_id or f"notif-worker-{uuid4().hex[:8]}"

    async def poll_and_deliver(self) -> int:
        """Poll a batch of pending commands and deliver them to E-8 outside DB transactions."""
        # Step 1: Claim batch in a short transactional block
        async with self.session_factory() as session:
            repo = NotificationOutboxRepository(session)
            records = await repo.claim_pending_batch(
                limit=self.settings.batch_size,
                lease_duration_seconds=self.settings.lease_duration_seconds,
                lease_owner=self.worker_id,
            )
            await session.commit()

        if not records:
            return 0

        logger.info(
            "notification_delivery_batch_claimed",
            worker_id=self.worker_id,
            count=len(records),
        )

        delivered_count = 0
        for record in records:
            try:
                # Reconstruct typed canonical command
                cmd = NotificationCommand(
                    command_id=record.command_id,
                    correlation_reference=record.correlation_reference,
                    causation_reference=record.causation_reference,
                    organization_scope_reference=record.tenant_id,
                    trigger=NotificationTrigger(
                        trigger_kind=TriggerKind(record.trigger_kind),
                        source_event_name=record.source_event_name,
                        source_entity_reference=record.source_entity_reference,
                    ),
                    recipient_references=record.recipient_references,
                    notification_context=record.notification_context,
                )

                # Step 2: Execute remote dispatch STRICTLY OUTSIDE any database session
                await self.dispatcher.dispatch(command=cmd, session=None)

                # Step 3: Mark dispatched in a dedicated short transaction
                async with self.session_factory() as mark_session:
                    mark_repo = NotificationOutboxRepository(mark_session)
                    await mark_repo.mark_dispatched(record.command_id)

                    audit_repo = AuditRepository(mark_session)
                    await audit_repo.append(
                        AuditEventCreate(
                            tenant_id=record.tenant_id,
                            event_type="notification.dispatched",
                            actor_principal_id=self.worker_id,
                            actor_principal_type=PrincipalType.SERVICE.value,
                            authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                            resource_type="notification_command",
                            resource_id=record.command_id,
                            outcome=AuditEventOutcome.SUCCESS,
                            correlation_id=record.correlation_reference,
                            details={
                                "command_id": record.command_id,
                                "transport": self.settings.transport,
                                "trigger_kind": record.trigger_kind,
                                "source_event": record.source_event_name,
                                "recipient_count": len(record.recipient_references),
                            },
                        )
                    )
                    await mark_session.commit()
                delivered_count += 1

            except Exception as exc:
                logger.error(
                    "notification_delivery_failed",
                    worker_id=self.worker_id,
                    command_id=record.command_id,
                    error=str(exc),
                )
                async with self.session_factory() as fail_session:
                    fail_repo = NotificationOutboxRepository(fail_session)
                    await fail_repo.mark_failed(
                        record.command_id,
                        error=str(exc),
                        retry_backoff_seconds=self.settings.retry_backoff_seconds,
                    )

                    audit_repo = AuditRepository(fail_session)
                    await audit_repo.append(
                        AuditEventCreate(
                            tenant_id=record.tenant_id,
                            event_type="notification.dispatch.failed",
                            actor_principal_id=self.worker_id,
                            actor_principal_type=PrincipalType.SERVICE.value,
                            authentication_method=AuthenticationMethod.SERVICE_API_KEY.value,
                            resource_type="notification_command",
                            resource_id=record.command_id,
                            outcome=AuditEventOutcome.FAILURE,
                            correlation_id=record.correlation_reference,
                            details={
                                "command_id": record.command_id,
                                "error": str(exc),
                                "retry_count": record.retry_count + 1,
                            },
                        )
                    )
                    await fail_session.commit()

        return delivered_count

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run delivery worker loop until stop_event is set."""
        logger.info("notification_delivery_worker_starting", worker_id=self.worker_id)
        while not stop_event.is_set():
            try:
                processed = await self.poll_and_deliver()
                if processed == 0:
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=self.settings.poll_interval_seconds,
                        )
                        break
                    except TimeoutError:
                        continue
            except asyncio.CancelledError:
                logger.info("notification_delivery_worker_cancelled", worker_id=self.worker_id)
                raise
            except Exception as exc:
                logger.error(
                    "notification_delivery_worker_loop_error",
                    worker_id=self.worker_id,
                    error=str(exc),
                )
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.settings.poll_interval_seconds,
                    )
                    break
                except TimeoutError:
                    continue

        logger.info("notification_delivery_worker_stopped", worker_id=self.worker_id)

    def start_background_tasks(self, stop_event: asyncio.Event) -> list[asyncio.Task[None]]:
        """Start delivery loop as an asyncio background task."""
        task = asyncio.create_task(
            self.run(stop_event),
            name=f"notification_delivery_worker_{self.worker_id}",
        )
        return [task]


__all__ = ["NotificationDeliveryWorker"]
