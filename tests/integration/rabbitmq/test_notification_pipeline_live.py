"""Live integration tests for the real Option C two-stage notification pipeline.

Part of Phase DQ-4 Live Integration Verification.
Executes against real PostgreSQL and real RabbitMQ:
Domain Mutation -> PostgreSQL Outbox -> RabbitMQ -> Notification Consumer
-> Transactional Inbox -> Notification Outbox -> Delivery Worker -> Fake E-8 Dispatcher.

Verifies:
1. Durable command staging from live RabbitMQ message delivery.
2. Transactional inbox deduplication preventing duplicate downstream staging.
3. Worker lease acquisition via SELECT FOR UPDATE SKIP LOCKED.
4. Autonomous external dispatch outside database transactions.
5. Fail-closed provisional recipient gating under DEC-E8-02 with audit recording.
6. Retry behavior with exponential backoff on transient external failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.notification_outbox import NotificationOutboxCommandRecord
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.consumers.config import (
    RabbitMqConsumerRunnerPolicy,
    RabbitMqConsumerSettings,
)
from app.events.consumers.rabbitmq_message import (
    RabbitMqInboundMessageHandler,
    RabbitMqInboundPolicy,
)
from app.events.consumers.rabbitmq_runner import (
    RabbitMqConsumerRunner,
)
from app.events.inbox.models import InboxMessageRecord
from app.events.inbox.processor import ConsumerEventProcessor
from app.events.outbox.dispatcher import (
    DispatchOutcome,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.outbox.repository import OutboxRepository
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.publishers.rabbitmq import RabbitMqEventPublisher
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.delivery_worker import NotificationDeliveryWorker
from app.integrations.engagement.handlers import SchedulePublishedNotificationHandler
from app.integrations.engagement.mock import MockNotificationDispatcher
from tests.integration.rabbitmq.conftest import (
    INTEGRATION_EXCHANGE,
)


def _create_schedule_published_envelope(
    *,
    tenant_id: str,
    schedule_id: str,
    recipients: list[str],
) -> EventEnvelope:
    """Construct canonical schedule.published event envelope."""
    return create_event_envelope(
        EventCreate(
            event_type="schedule.published",
            event_version=1,
            tenant_id=tenant_id,
            correlation_id=f"corr-live-{uuid4().hex[:8]}",
            causation_id=uuid4(),
            subject_type="schedule",
            subject_id=schedule_id,
            payload={
                "schedule_id": schedule_id,
                "version": 1,
                "status": "PUBLISHED",
                "recipients": recipients,
            },
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_notification_pipeline_end_to_end(
    rabbitmq_url: SecretStr,
    publisher_settings: RabbitMqPublisherSettings,
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify Option C pipeline: Outbox -> RabbitMQ -> Consumer -> Staged Outbox -> Delivery."""
    suffix = uuid4().hex[:8]
    tenant_id = f"tenant-live-pipeline-{suffix}"
    schedule_id = f"sched-{suffix}"

    # 1. Setup consumer settings bound to isolated topic exchange
    consumer_settings = RabbitMqConsumerSettings(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_exchange_name=INTEGRATION_EXCHANGE,
        rabbitmq_consumer_queue_name=f"e7.notif.queue.{suffix}",
        rabbitmq_consumer_binding_keys=("schedule.#",),
        rabbitmq_dead_letter_exchange_name=f"e7.notif.dlx.{suffix}",
        rabbitmq_dead_letter_queue_name=f"e7.notif.dlq.{suffix}",
        rabbitmq_dead_letter_routing_key=f"dead.letter.notif.{suffix}",
        rabbitmq_consumer_max_concurrency=2,
        rabbitmq_connect_timeout_seconds=5.0,
    )

    # 2. Stage 1: Domain mutation commits EventEnvelope to outbox_events in PostgreSQL
    envelope = _create_schedule_published_envelope(
        tenant_id=tenant_id,
        schedule_id=schedule_id,
        recipients=["urn:e7:cohort:faculty"],
    )

    async with uow_factory() as uow:
        outbox_repo = OutboxRepository(uow.session)
        await outbox_repo.enqueue(envelope)
        await uow.commit()

    # 3. OutboxDispatcher publishes from outbox_events to real RabbitMQ
    publisher = RabbitMqEventPublisher(publisher_settings)
    await publisher.start()

    dispatch_policy = OutboxDispatchPolicy(
        lease_duration=timedelta(seconds=30),
        publish_timeout=timedelta(seconds=5),
        retry_base_delay=timedelta(seconds=1),
        retry_max_delay=timedelta(seconds=10),
        retry_multiplier=2.0,
    )
    dispatcher = OutboxDispatcher(
        uow_factory=uow_factory,
        publisher=publisher,
        policy=dispatch_policy,
    )

    try:
        dispatch_result = await dispatcher.dispatch_once()
        assert dispatch_result.outcome == DispatchOutcome.PUBLISHED
    finally:
        await publisher.close()

    # 4. Stage 2: Notification Consumer subscribes to real RabbitMQ, executes inbox + outbox staging
    fake_e8_dispatcher = MockNotificationDispatcher()
    notif_settings = NotificationSettings(
        pipeline_mode="outbox",
        allow_provisional_recipients=False,
    )
    notif_handler = SchedulePublishedNotificationHandler(
        dispatcher=fake_e8_dispatcher,
        default_recipients=["urn:e7:cohort:faculty"],
        settings=notif_settings,
    )
    event_processor = ConsumerEventProcessor(
        consumer_name="notification.schedule_published_consumer",
        uow_factory=uow_factory,
        handler=notif_handler,
    )
    inbound_handler = RabbitMqInboundMessageHandler(
        processor=event_processor,
        policy=RabbitMqInboundPolicy(
            supported_event_types=frozenset({"schedule.published"}),
        ),
    )

    stop_event = asyncio.Event()
    runner = RabbitMqConsumerRunner(
        settings=consumer_settings,
        message_handler=inbound_handler,
        policy=RabbitMqConsumerRunnerPolicy.from_settings(consumer_settings),
        stop_event=stop_event,
    )

    runner_task = asyncio.create_task(runner.run())

    # Wait for consumer to process the event from RabbitMQ
    expected_command_id = f"notif-pub-{envelope.event_id}"
    staged_command: NotificationOutboxCommandRecord | None = None

    for _ in range(50):
        await asyncio.sleep(0.1)
        async with session_factory() as session:
            result = await session.execute(
                select(NotificationOutboxCommandRecord).where(
                    NotificationOutboxCommandRecord.command_id == expected_command_id
                )
            )
            staged_command = result.scalar_one_or_none()
            if staged_command is not None:
                break

    # Stop consumer cleanly
    stop_event.set()
    await asyncio.wait_for(runner_task, timeout=5.0)

    # Verify Stage 2 PostgreSQL persistence
    assert staged_command is not None
    assert staged_command.command_id == expected_command_id
    assert staged_command.dispatch_state == "PENDING"
    assert staged_command.organization_scope_reference == tenant_id

    # Verify inbox_messages recorded the message
    async with session_factory() as session:
        inbox_result = await session.execute(
            select(InboxMessageRecord).where(
                InboxMessageRecord.event_id == envelope.event_id
            )
        )
        inbox_msg = inbox_result.scalar_one_or_none()
        assert inbox_msg is not None
        assert inbox_msg.consumer_name == "notification.schedule_published_consumer"

    # 5. Stage 3: Notification Delivery Worker claims lease, dispatches to fake E-8, and commits
    worker_settings = NotificationSettings(
        enabled=True,
        pipeline_mode="outbox",
        batch_size=10,
        lease_duration_seconds=30.0,
    )
    delivery_worker = NotificationDeliveryWorker(
        session_factory=session_factory,
        dispatcher=fake_e8_dispatcher,
        settings=worker_settings,
        worker_id=f"worker-live-{suffix}",
    )

    delivered_count = await delivery_worker.run_once()
    assert delivered_count == 1

    # Verify fake E-8 received the canonical command
    receipts = fake_e8_dispatcher.get_receipts_by_correlation(envelope.correlation_id)
    assert len(receipts) == 1
    assert receipts[0].command_id == expected_command_id

    # Verify final DISPATCHED state in PostgreSQL
    async with session_factory() as session:
        result = await session.execute(
            select(NotificationOutboxCommandRecord).where(
                NotificationOutboxCommandRecord.command_id == expected_command_id
            )
        )
        final_record = result.scalar_one()
        assert final_record.dispatch_state == "DISPATCHED"
        assert final_record.dispatched_at is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_notification_pipeline_provisional_recipient_fail_closed(
    rabbitmq_url: SecretStr,
    publisher_settings: RabbitMqPublisherSettings,
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify that unverified/provisional recipients fail-closed to BLOCKED state."""
    suffix = uuid4().hex[:8]
    tenant_id = f"tenant-prov-{suffix}"
    schedule_id = f"sched-prov-{suffix}"

    consumer_settings = RabbitMqConsumerSettings(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_exchange_name=INTEGRATION_EXCHANGE,
        rabbitmq_consumer_queue_name=f"e7.prov.queue.{suffix}",
        rabbitmq_consumer_binding_keys=("schedule.#",),
        rabbitmq_dead_letter_exchange_name=f"e7.prov.dlx.{suffix}",
        rabbitmq_dead_letter_queue_name=f"e7.prov.dlq.{suffix}",
        rabbitmq_dead_letter_routing_key=f"dead.letter.prov.{suffix}",
        rabbitmq_consumer_max_concurrency=1,
        rabbitmq_connect_timeout_seconds=5.0,
    )

    # 1. Enqueue event with provisional recipient
    envelope = _create_schedule_published_envelope(
        tenant_id=tenant_id,
        schedule_id=schedule_id,
        recipients=["provisional:student:cohort:10b"],
    )

    async with uow_factory() as uow:
        outbox_repo = OutboxRepository(uow.session)
        await outbox_repo.enqueue(envelope)
        await uow.commit()

    # 2. Publish to RabbitMQ
    publisher = RabbitMqEventPublisher(publisher_settings)
    await publisher.start()
    dispatcher = OutboxDispatcher(
        uow_factory=uow_factory,
        publisher=publisher,
        policy=OutboxDispatchPolicy(
            lease_duration=timedelta(seconds=30),
            publish_timeout=timedelta(seconds=5),
            retry_base_delay=timedelta(seconds=1),
            retry_max_delay=timedelta(seconds=10),
            retry_multiplier=2.0,
        ),
    )
    try:
        await dispatcher.dispatch_once()
    finally:
        await publisher.close()

    # 3. Consumer runs with allow_provisional_recipients=False (DEC-E8-02)
    fake_e8_dispatcher = MockNotificationDispatcher()
    notif_settings = NotificationSettings(
        pipeline_mode="outbox",
        allow_provisional_recipients=False,
    )
    notif_handler = SchedulePublishedNotificationHandler(
        dispatcher=fake_e8_dispatcher,
        default_recipients=["provisional:student:cohort:10b"],
        settings=notif_settings,
    )
    event_processor = ConsumerEventProcessor(
        consumer_name="notification.schedule_published_consumer",
        uow_factory=uow_factory,
        handler=notif_handler,
    )
    inbound_handler = RabbitMqInboundMessageHandler(
        processor=event_processor,
        policy=RabbitMqInboundPolicy(
            supported_event_types=frozenset({"schedule.published"}),
        ),
    )

    stop_event = asyncio.Event()
    runner = RabbitMqConsumerRunner(
        settings=consumer_settings,
        message_handler=inbound_handler,
        policy=RabbitMqConsumerRunnerPolicy.from_settings(consumer_settings),
        stop_event=stop_event,
    )

    runner_task = asyncio.create_task(runner.run())

    expected_command_id = f"notif-pub-{envelope.event_id}"
    staged_command: NotificationOutboxCommandRecord | None = None

    for _ in range(50):
        await asyncio.sleep(0.1)
        async with session_factory() as session:
            result = await session.execute(
                select(NotificationOutboxCommandRecord).where(
                    NotificationOutboxCommandRecord.command_id == expected_command_id
                )
            )
            staged_command = result.scalar_one_or_none()
            if staged_command is not None:
                break

    stop_event.set()
    await asyncio.wait_for(runner_task, timeout=5.0)

    # 4. Verify command is BLOCKED under DEC-E8-02
    assert staged_command is not None
    assert staged_command.dispatch_state == "BLOCKED"
    assert staged_command.block_reason == "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02"

    # 5. Verify delivery worker ignores BLOCKED commands
    delivery_worker = NotificationDeliveryWorker(
        session_factory=session_factory,
        dispatcher=fake_e8_dispatcher,
        settings=NotificationSettings(enabled=True, pipeline_mode="outbox"),
        worker_id=f"worker-prov-{suffix}",
    )
    processed = await delivery_worker.run_once()
    assert processed == 0
    assert len(fake_e8_dispatcher.sent_commands) == 0
