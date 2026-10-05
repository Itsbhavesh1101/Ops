"""Live RabbitMQ publisher connection and canonical message publication tests.

Part of Phase 1 Foundation Task 1J.2B.
Verifies RabbitMqEventPublisher against real broker with publisher confirms and mandatory routing.
"""

from uuid import uuid4

import aio_pika
import pytest
from pydantic import SecretStr

from app.events.publishers.base import PublisherState
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.publishers.rabbitmq import RabbitMqEventPublisher
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from tests.integration.rabbitmq.conftest import (
    INTEGRATION_EXCHANGE,
    INTEGRATION_QUEUE,
)


def _create_synthetic_envelope(
    event_type: str = "integration.test.created",
) -> EventEnvelope:
    """Create a strictly valid synthetic EventEnvelope for live testing."""
    return create_event_envelope(
        EventCreate(
            event_type=event_type,
            event_version=1,
            tenant_id=f"tenant-live-{uuid4().hex[:8]}",
            correlation_id=str(uuid4()),
            causation_id=uuid4(),
            subject_type="test_subject",
            subject_id=str(uuid4()),
            payload={"action": "test_run", "count": 42, "status": "active"},
        )
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_publisher_lifecycle(
    publisher_settings: RabbitMqPublisherSettings,
    isolated_vhost: str,
) -> None:
    """Verify RabbitMqEventPublisher connection lifecycle from NEW to STARTED to CLOSED."""
    publisher = RabbitMqEventPublisher(publisher_settings)
    assert publisher.state == PublisherState.NEW
    assert publisher.exchange_name == INTEGRATION_EXCHANGE

    # 1. Start publisher
    await publisher.start()
    assert publisher.state == PublisherState.STARTED

    # 2. Idempotent start
    await publisher.start()
    assert publisher.state == PublisherState.STARTED

    # 3. Close publisher
    await publisher.close()
    assert publisher.state == PublisherState.CLOSED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_canonical_event_publication_and_metadata_roundtrip(
    rabbitmq_url: SecretStr,
    publisher_settings: RabbitMqPublisherSettings,
    isolated_vhost: str,
) -> None:
    """Verify published EventEnvelope preserves content_type, encoding, message_id, type."""
    # 1. Ensure test queue is declared and bound to receive the message
    conn = await aio_pika.connect_robust(rabbitmq_url.get_secret_value())
    queue_name = f"{INTEGRATION_QUEUE}.pub_test"
    binding_key = "integration.audit.created"

    try:
        channel = await conn.channel()
        exchange = await channel.declare_exchange(
            name=INTEGRATION_EXCHANGE,
            type=aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        queue = await channel.declare_queue(queue_name, durable=True, auto_delete=True)
        await queue.bind(exchange=exchange, routing_key=binding_key)

        # 2. Publish canonical message using real RabbitMqEventPublisher
        publisher = RabbitMqEventPublisher(publisher_settings)
        await publisher.start()

        envelope = _create_synthetic_envelope(event_type=binding_key)
        try:
            await publisher.publish(envelope)
        finally:
            await publisher.close()

        # 3. Consume message and verify all AMQP headers and body
        incoming = await queue.get(timeout=5.0)
        assert incoming is not None, "Message not received from queue"

        # Verify AMQP transport metadata
        assert incoming.content_type == "application/json"
        assert incoming.content_encoding == "utf-8"
        assert incoming.message_id == str(envelope.event_id)
        assert incoming.type == envelope.event_type
        assert incoming.correlation_id == str(envelope.correlation_id)
        assert incoming.delivery_mode == aio_pika.DeliveryMode.PERSISTENT

        # Verify body round-trip
        reconstructed = EventEnvelope.model_validate_json(incoming.body)
        assert reconstructed.event_id == envelope.event_id
        assert reconstructed.event_type == envelope.event_type
        assert reconstructed.tenant_id == envelope.tenant_id
        assert reconstructed.correlation_id == envelope.correlation_id
        assert reconstructed.payload == envelope.payload

        await incoming.ack()

    finally:
        await conn.close()
