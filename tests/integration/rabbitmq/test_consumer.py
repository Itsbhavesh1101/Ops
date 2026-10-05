"""Live RabbitMQ consumer, ACK behavior, inbox deduplication, and DLQ routing tests.

Part of Phase 1 Foundation Task 1G.4 and Task 1J.2B.
Verifies inbound message wire validation, ACK semantics, and dead-letter queue routing.
"""

from collections.abc import Callable
from uuid import uuid4

import aio_pika
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.uow import SqlAlchemyUnitOfWork
from app.events.consumers.rabbitmq_message import (
    InvalidMessageReason,
    RabbitMqInboundMessageHandler,
    RabbitMqInboundPolicy,
    RabbitMqMessageHandlingOutcome,
)
from app.events.inbox.processor import ConsumerEventHandler, ConsumerEventProcessor
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.publishers.rabbitmq import RabbitMqEventPublisher
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from tests.integration.rabbitmq.conftest import (
    INTEGRATION_DLQ,
    INTEGRATION_DLQ_ROUTING_KEY,
    INTEGRATION_DLX,
    INTEGRATION_EXCHANGE,
    INTEGRATION_QUEUE,
)


class SpyConsumerHandler(ConsumerEventHandler):
    """Test handler tracking execution calls and delivered envelopes."""

    def __init__(self) -> None:
        self.call_count = 0
        self.received_envelopes: list[EventEnvelope] = []

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        self.call_count += 1
        self.received_envelopes.append(envelope)


def _create_synthetic_envelope(
    event_type: str = "integration.consumer.event",
) -> EventEnvelope:
    """Create synthetic canonical event envelope."""
    return create_event_envelope(
        EventCreate(
            event_type=event_type,
            event_version=1,
            tenant_id=f"tenant-cons-{uuid4().hex[:8]}",
            correlation_id=str(uuid4()),
            causation_id=uuid4(),
            subject_type="test_order",
            subject_id="ORD-999",
            payload={"order_id": "ORD-999", "amount": 100},
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_happy_path_consumer_ack(
    rabbitmq_url: SecretStr,
    publisher_settings: RabbitMqPublisherSettings,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify consumer receives canonical event, invokes processor once, and ACKs message."""
    conn = await aio_pika.connect_robust(rabbitmq_url.get_secret_value())
    queue_name = f"{INTEGRATION_QUEUE}.ack_test"
    binding_key = "integration.consumer.event"

    try:
        channel = await conn.channel()
        exchange = await channel.declare_exchange(
            name=INTEGRATION_EXCHANGE,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        queue = await channel.declare_queue(queue_name, durable=True, auto_delete=True)
        await queue.bind(exchange=exchange, routing_key=binding_key)

        # 1. Publish valid message
        envelope = _create_synthetic_envelope(event_type=binding_key)
        publisher = RabbitMqEventPublisher(publisher_settings)
        await publisher.start()
        try:
            await publisher.publish(envelope)
        finally:
            await publisher.close()

        # 2. Setup handler and processor
        spy_handler = SpyConsumerHandler()
        processor = ConsumerEventProcessor(
            consumer_name="integration.consumer.happy",
            uow_factory=uow_factory,
            handler=spy_handler,
        )
        inbound_handler = RabbitMqInboundMessageHandler(
            processor=processor,
            policy=RabbitMqInboundPolicy(max_message_bytes=65536),
        )

        # 3. Consume message
        incoming = await queue.get(timeout=5.0)
        assert incoming is not None, "Message not received from queue"

        result = await inbound_handler.handle(incoming)
        assert result.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED
        assert result.event_id == envelope.event_id
        assert spy_handler.call_count == 1

        # 4. Verify message removed from queue (count is 0)
        q_state = await queue.declare()
        assert q_state.message_count == 0

    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_inbox_deduplication_live_with_postgres(
    rabbitmq_url: SecretStr,
    publisher_settings: RabbitMqPublisherSettings,
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify duplicate event is acknowledged but downstream handler is executed only once."""
    conn = await aio_pika.connect_robust(rabbitmq_url.get_secret_value())
    queue_name = f"{INTEGRATION_QUEUE}.dedupe_test"
    binding_key = "integration.consumer.dedupe"
    consumer_name = "integration.consumer.dedupe_service"

    try:
        channel = await conn.channel()
        exchange = await channel.declare_exchange(
            name=INTEGRATION_EXCHANGE,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        queue = await channel.declare_queue(queue_name, durable=True, auto_delete=True)
        await queue.bind(exchange=exchange, routing_key=binding_key)

        # Publish the same envelope twice
        envelope = _create_synthetic_envelope(event_type=binding_key)
        publisher = RabbitMqEventPublisher(publisher_settings)
        await publisher.start()
        try:
            await publisher.publish(envelope)
            await publisher.publish(envelope)
        finally:
            await publisher.close()

        spy_handler = SpyConsumerHandler()
        processor = ConsumerEventProcessor(
            consumer_name=consumer_name,
            uow_factory=uow_factory,
            handler=spy_handler,
        )
        inbound_handler = RabbitMqInboundMessageHandler(
            processor=processor,
            policy=RabbitMqInboundPolicy(max_message_bytes=65536),
        )

        # First message -> PROCESSED
        msg1 = await queue.get(timeout=5.0)
        assert msg1 is not None
        res1 = await inbound_handler.handle(msg1)
        assert res1.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED
        assert spy_handler.call_count == 1

        # Second message -> DUPLICATE (handler call_count remains 1)
        msg2 = await queue.get(timeout=5.0)
        assert msg2 is not None
        res2 = await inbound_handler.handle(msg2)
        assert res2.outcome == RabbitMqMessageHandlingOutcome.ACKED_DUPLICATE
        assert spy_handler.call_count == 1

        # Verify real PostgreSQL inbox table contains exactly 1 row
        async with session_factory() as session:
            count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM inbox_messages "
                    "WHERE consumer_name = :cname AND event_id = :eid"
                ),
                {"cname": consumer_name, "eid": envelope.event_id},
            )
            assert count == 1, f"Expected exactly 1 inbox row, found {count}"

    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_malformed_message_rejection_and_dlq(
    rabbitmq_url: SecretStr,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify malformed wire message is rejected without requeue and routed to the DLQ."""
    conn = await aio_pika.connect_robust(rabbitmq_url.get_secret_value())
    main_q_name = f"{INTEGRATION_QUEUE}.malformed_test"
    dlq_name = f"{INTEGRATION_DLQ}.malformed_test"
    dlx_name = f"{INTEGRATION_DLX}.malformed_test"
    dlq_routing_key = INTEGRATION_DLQ_ROUTING_KEY

    try:
        channel = await conn.channel()

        # Declare DLX and DLQ
        dlx = await channel.declare_exchange(dlx_name, aio_pika.ExchangeType.DIRECT, durable=True)
        dlq = await channel.declare_queue(dlq_name, durable=True, auto_delete=True)
        await dlq.bind(exchange=dlx, routing_key=dlq_routing_key)

        # Declare main queue with DLX routing arguments
        main_queue = await channel.declare_queue(
            main_q_name,
            durable=True,
            auto_delete=True,
            arguments={
                "x-dead-letter-exchange": dlx_name,
                "x-dead-letter-routing-key": dlq_routing_key,
            },
        )

        # Publish a deliberately malformed message directly (invalid JSON)
        malformed_bytes = b'{"event_id": "bad-json", incomplete...'
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=malformed_bytes,
                content_type="application/json",
                content_encoding="utf-8",
                message_id=str(uuid4()),
                type="some.event.type",
            ),
            routing_key=main_q_name,
        )

        # Setup inbound handler
        spy_handler = SpyConsumerHandler()
        processor = ConsumerEventProcessor(
            consumer_name="integration.consumer.malformed",
            uow_factory=uow_factory,
            handler=spy_handler,
        )
        inbound_handler = RabbitMqInboundMessageHandler(
            processor=processor,
            policy=RabbitMqInboundPolicy(max_message_bytes=65536),
        )

        # Consume from main queue and process
        incoming = await main_queue.get(timeout=5.0)
        assert incoming is not None

        result = await inbound_handler.handle(incoming)
        assert result.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE
        assert result.invalid_reason == InvalidMessageReason.INVALID_JSON
        assert spy_handler.call_count == 0

        # Verify main queue has 0 messages (not requeued)
        q_state = await main_queue.declare()
        assert q_state.message_count == 0

        # Verify DLQ received the rejected message
        dlq_msg = await dlq.get(timeout=5.0)
        assert dlq_msg is not None, "Rejected message did not reach DLQ"
        assert dlq_msg.body == malformed_bytes
        await dlq_msg.ack()

    finally:
        await conn.close()
