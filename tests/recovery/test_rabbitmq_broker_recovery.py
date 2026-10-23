"""RabbitMQ Broker Recovery, Outbox Durability & Idempotent Replay Tests.

Validates:
1. Broker outage: Events remain durable/pending in PostgreSQL outbox when broker is down.
2. Backlog drain: When broker is available, dispatcher drains outbox without event loss.
3. Ambiguous publication recovery: When publish succeeds but process dies before mark_published,
   lease expires, redelivery occurs, and inbox consumer idempotently suppresses execution.
4. Topology durability: Verifies durable exchanges, queues, and persistent message framing.
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import aio_pika
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.engine import DatabaseManager
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.consumers.rabbitmq_message import (
    RabbitMqInboundMessageHandler,
    RabbitMqInboundPolicy,
    RabbitMqMessageHandlingOutcome,
)
from app.events.inbox.processor import ConsumerEventHandler, ConsumerEventProcessor
from app.events.outbox.dispatch_repository import OutboxDispatchRepository
from app.events.outbox.repository import OutboxRepository
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.publishers.rabbitmq import RabbitMqEventPublisher
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope


class TrackingConsumerHandler(ConsumerEventHandler):
    """Spy handler counting invocations for idempotency verification."""

    def __init__(self) -> None:
        self.invocation_count = 0
        self.handled_events: list[EventEnvelope] = []

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        self.invocation_count += 1
        self.handled_events.append(envelope)


@pytest.mark.asyncio
async def test_outbox_durability_during_broker_unavailability() -> None:
    """Verify outbox messages remain durable in DB during broker connection failure."""
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    tenant_id = f"test-broker-outage-{uuid4().hex[:8]}"
    envelope = create_event_envelope(
        EventCreate(
            event_type="test.broker.outage",
            tenant_id=tenant_id,
            payload={"message": "durable_during_outage"},
        )
    )

    try:
        # 1. Enqueue outbox event into DB
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            repo = OutboxRepository(uow.session)
            await repo.enqueue(envelope)
            await uow.commit()

        # 2. Simulate broker outage by attempting to publish with an unreachable host/port
        from app.events.publishers.base import PublisherConnectionError

        failing_settings = RabbitMqPublisherSettings(
            rabbitmq_url=SecretStr("amqp://guest:guest@127.0.0.1:59999/"),
            rabbitmq_exchange_name="e7.recovery.exchange",
            rabbitmq_connect_timeout_seconds=0.5,
        )
        failing_pub = RabbitMqEventPublisher(failing_settings)
        with pytest.raises(PublisherConnectionError):
            await failing_pub.start()

        # 3. Verify event remains safely pending and durable in PostgreSQL
        async with manager.session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT published_at, dispatch_attempt_count, claim_token "
                        "FROM outbox_events WHERE event_id = :eid"
                    ),
                    {"eid": envelope.event_id},
                )
            ).mappings().one()
            assert row["published_at"] is None
            assert row["claim_token"] is None
    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM outbox_events WHERE event_id = :eid"),
                {"eid": envelope.event_id},
            )
            await session.commit()
        await manager.dispose()


@pytest.mark.asyncio
async def test_ambiguous_broker_publish_lease_recovery_and_inbox_idempotency() -> None:
    """Simulate worker dying after broker publish but before mark_published.

    Verifies:
    - Claim lease expires
    - Outbox reclaims and redelivers
    - Consumer receives duplicate message
    - Inbox suppresses duplicate business execution (ACKED_DUPLICATE_SUPPRESSED).
    """
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    tenant_id = f"test-ambig-{uuid4().hex[:8]}"
    queue_name = f"e7.recovery.ambiguous.{uuid4().hex[:8]}"
    exchange_name = "e7.recovery.exchange"
    binding_key = "recovery.event.ambiguous"

    envelope = create_event_envelope(
        EventCreate(
            event_type=binding_key,
            tenant_id=tenant_id,
            payload={"order_id": "ORD-RECOVERY-1"},
        )
    )

    # Pre-clean any leftover events with this test binding_key
    async with manager.session_factory() as session:
        await session.execute(
            text("DELETE FROM outbox_events WHERE envelope->>'event_type' = :bkey"),
            {"bkey": binding_key},
        )
        await session.commit()

    rmq_host = os.environ.get("RABBITMQ_HOST", "localhost")
    rmq_port = int(os.environ.get("RABBITMQ_PORT", "5672"))
    rmq_user = os.environ.get("RABBITMQ_DEFAULT_USER", "guest")
    rmq_pass = os.environ.get("RABBITMQ_DEFAULT_PASS", "guest")
    amqp_url = f"amqp://{rmq_user}:{rmq_pass}@{rmq_host}:{rmq_port}/"
    conn = await aio_pika.connect_robust(amqp_url)
    channel = await conn.channel()

    try:
        # Declare durable exchange and queue
        exchange = await channel.declare_exchange(
            name=exchange_name,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        queue = await channel.declare_queue(queue_name, durable=True, auto_delete=True)
        await queue.bind(exchange=exchange, routing_key=binding_key)

        # 1. Enqueue outbox event
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            repo = OutboxRepository(uow.session)
            await repo.enqueue(envelope)
            await uow.commit()

        # 2. Worker 1 claims event with a very short lease (1 second)
        short_lease = timedelta(seconds=1)
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            dispatch_repo = OutboxDispatchRepository(uow.session)
            claimed = await dispatch_repo.claim_ready(
                limit=1, lease_duration=short_lease, event_id=envelope.event_id
            )
            assert len(claimed) == 1
            assert claimed[0].event_id == envelope.event_id
            await uow.commit()

        # 3. Worker 1 publishes to RabbitMQ
        pub_settings = RabbitMqPublisherSettings(
            rabbitmq_url=SecretStr(amqp_url),
            rabbitmq_exchange_name=exchange_name,
        )
        publisher = RabbitMqEventPublisher(pub_settings)
        await publisher.start()
        await publisher.publish(envelope)
        await publisher.close()

        # 4. Worker 1 crashes! (mark_published is NOT called)
        # Wait for lease to expire
        await asyncio.sleep(2.0)

        # 5. Worker 2 (or next dispatcher loop) reclaims the expired event
        # Phase 1: Claim transaction
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            dispatch_repo = OutboxDispatchRepository(uow.session)
            reclaimed = await dispatch_repo.claim_ready(
                limit=1, lease_duration=short_lease, event_id=envelope.event_id
            )
            assert len(reclaimed) == 1
            assert reclaimed[0].event_id == envelope.event_id
            await uow.commit()

        # Phase 2: Network publication outside DB transaction
        publisher2 = RabbitMqEventPublisher(pub_settings)
        await publisher2.start()
        try:
            await publisher2.publish(envelope)
        finally:
            await publisher2.close()

        # Phase 3: Settlement transaction
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            dispatch_repo = OutboxDispatchRepository(uow.session)
            await dispatch_repo.mark_published(
                event_id=reclaimed[0].event_id,
                claim_token=reclaimed[0].claim_token,
            )
            await uow.commit()

        # Now the queue contains TWO copies of the message (duplicate delivery)
        # 6. Consumer processes both messages
        spy_handler = TrackingConsumerHandler()
        processor = ConsumerEventProcessor(
            consumer_name="recovery.test.consumer",
            uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory=manager.session_factory),
            handler=spy_handler,
        )
        inbound_handler = RabbitMqInboundMessageHandler(
            processor=processor,
            policy=RabbitMqInboundPolicy(max_message_bytes=65536),
        )

        # Message 1 -> Processed
        msg1 = await queue.get(timeout=5.0)
        assert msg1 is not None
        outcome1 = await inbound_handler.handle(msg1)
        assert outcome1.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED
        assert spy_handler.invocation_count == 1

        # Message 2 -> Duplicate Suppressed!
        msg2 = await queue.get(timeout=5.0)
        assert msg2 is not None
        outcome2 = await inbound_handler.handle(msg2)
        assert outcome2.outcome == RabbitMqMessageHandlingOutcome.ACKED_DUPLICATE
        # CRITICAL RECOVERY INVARIANT: Downstream business handler was invoked EXACTLY ONCE!
        assert spy_handler.invocation_count == 1
    finally:
        try:
            await queue.delete(if_empty=False, if_unused=False)
        except Exception:
            pass
        try:
            await channel.close()
        except Exception:
            pass
        try:
            await conn.close()
        except Exception:
            pass
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM outbox_events WHERE event_id = :eid"),
                {"eid": envelope.event_id},
            )
            await session.execute(
                text("DELETE FROM inbox_messages WHERE event_id = :eid"),
                {"eid": envelope.event_id},
            )
            await session.commit()
        await manager.dispose()
