"""Live transactional outbox dispatch orchestration with real RabbitMQ broker.

Part of Phase 1 Foundation Task 1G.3 and Task 1J.2B.
Verifies the three-phase outbox execution:
1. DB Transaction A: enqueue EventEnvelope, commit.
2. Network Publication: dispatch_once claims, publishes through real RabbitMqEventPublisher.
3. DB Transaction B: mark_published settlements verified live in PostgreSQL.
"""

from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

import aio_pika
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.uow import SqlAlchemyUnitOfWork
from app.events.outbox.dispatcher import (
    DispatchOutcome,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.outbox.repository import OutboxRepository
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.publishers.rabbitmq import RabbitMqEventPublisher
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from tests.integration.rabbitmq.conftest import (
    INTEGRATION_EXCHANGE,
    INTEGRATION_QUEUE,
)


def _create_synthetic_envelope(
    event_type: str = "integration.outbox.dispatched",
) -> EventEnvelope:
    """Create a strictly valid synthetic EventEnvelope for outbox dispatch."""
    return create_event_envelope(
        EventCreate(
            event_type=event_type,
            event_version=1,
            tenant_id=f"tenant-outbox-{uuid4().hex[:8]}",
            correlation_id=str(uuid4()),
            causation_id=uuid4(),
            subject_type="test_task",
            subject_id="TSK-100",
            payload={"task_id": "TSK-100", "status": "completed"},
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transactional_outbox_publish_and_settlement(
    rabbitmq_url: SecretStr,
    publisher_settings: RabbitMqPublisherSettings,
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify outbox lifecycle: DB enqueue -> claim -> real publish -> DB settlement."""
    conn = await aio_pika.connect_robust(rabbitmq_url.get_secret_value())
    queue_name = f"{INTEGRATION_QUEUE}.outbox_test"
    binding_key = "integration.outbox.dispatched"

    try:
        # 1. Declare destination queue to capture published message
        channel = await conn.channel()
        exchange = await channel.declare_exchange(
            name=INTEGRATION_EXCHANGE,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        queue = await channel.declare_queue(queue_name, durable=True, auto_delete=True)
        await queue.bind(exchange=exchange, routing_key=binding_key)

        # 2. Enqueue event into outbox inside real database transaction
        envelope = _create_synthetic_envelope(event_type=binding_key)
        async with uow_factory() as uow:
            outbox_repo = OutboxRepository(uow.session)
            await outbox_repo.enqueue(envelope)
            await uow.commit()

        # Verify initial pending state in PostgreSQL
        async with session_factory() as session:
            row = await session.execute(
                text(
                    "SELECT event_id, published_at, claim_token "
                    "FROM outbox_events WHERE event_id = :eid"
                ),
                {"eid": envelope.event_id},
            )
            initial_record = row.mappings().one()
            assert initial_record["event_id"] == envelope.event_id
            assert initial_record["published_at"] is None
            assert initial_record["claim_token"] is None

        # 3. Instantiate real publisher and real dispatcher
        publisher = RabbitMqEventPublisher(publisher_settings)
        await publisher.start()

        dispatch_policy = OutboxDispatchPolicy(
            lease_duration=timedelta(seconds=60),
            publish_timeout=timedelta(seconds=10),
            retry_base_delay=timedelta(seconds=1),
            retry_max_delay=timedelta(seconds=60),
            retry_multiplier=2.0,
        )
        dispatcher = OutboxDispatcher(
            uow_factory=uow_factory,
            publisher=publisher,
            policy=dispatch_policy,
        )

        try:
            # 4. Dispatch the event
            result = await dispatcher.dispatch_once()
            assert result.outcome == DispatchOutcome.PUBLISHED
            assert result.event_id == envelope.event_id

            # 5. Verify database settlement state in fresh transaction
            async with session_factory() as session:
                settled_row = await session.execute(
                    text(
                        "SELECT published_at, claim_token, claimed_at, "
                        "dispatch_attempt_count FROM outbox_events WHERE event_id = :eid"
                    ),
                    {"eid": envelope.event_id},
                )
                settled = settled_row.mappings().one()
                assert settled["published_at"] is not None
                assert settled["claim_token"] is None
                assert settled["claimed_at"] is None
                assert settled["dispatch_attempt_count"] == 1

            # 6. Consume message from real broker and verify content
            incoming = await queue.get(timeout=5.0)
            assert incoming is not None, "Message not received by broker"

            assert incoming.message_id == str(envelope.event_id)
            assert incoming.type == envelope.event_type
            assert incoming.content_type == "application/json"
            assert incoming.content_encoding == "utf-8"

            reconstructed = EventEnvelope.model_validate_json(incoming.body)
            assert reconstructed.event_id == envelope.event_id
            assert reconstructed.payload == envelope.payload
            await incoming.ack()

        finally:
            await publisher.close()

    finally:
        await conn.close()
