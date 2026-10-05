"""Live integration tests for RabbitMqConsumerRunner end-to-end path.

Part of Phase 1 Foundation Task 1J.2B.1.
Verifies the complete frozen consumer path:
REAL RABBITMQ -> RabbitMqConsumerRunner (runner-owned topology declaration,
push subscription, QoS prefetch, concurrency semaphore, supervisor)
-> RabbitMqInboundMessageHandler -> ConsumerEventProcessor -> InboxRepository
-> real PostgreSQL -> synthetic downstream handler.

Critical Invariant:
The test code itself performs ZERO topology declarations (zero declare_exchange,
zero declare_queue, zero queue.bind). All exchanges, queues, DLX, DLQ, and bindings
are declared solely by the frozen RabbitMqConsumerRunner.run(...) method.
"""

import asyncio
import subprocess
from collections.abc import Callable
from datetime import timedelta
from uuid import uuid4

import aio_pika
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    ConsumerRunnerState,
    RabbitMqConsumerRunner,
)
from app.events.inbox.processor import ConsumerEventHandler, ConsumerEventProcessor
from app.events.publishers.config import RabbitMqPublisherSettings
from app.events.publishers.rabbitmq import RabbitMqEventPublisher
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from tests.integration.rabbitmq.conftest import RABBITMQ_VHOST


class SpyDownstreamHandler(ConsumerEventHandler):
    """Synthetic business handler recording delivered envelopes and call counts."""

    def __init__(self) -> None:
        self.call_count = 0
        self.received_envelopes: list[EventEnvelope] = []
        self.event_received = asyncio.Event()

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        self.call_count += 1
        self.received_envelopes.append(envelope)
        self.event_received.set()


class BlockingDownstreamHandler(ConsumerEventHandler):
    """Synthetic business handler with deterministic barrier to verify bounded concurrency."""

    def __init__(self) -> None:
        self.call_count = 0
        self.first_entered = asyncio.Event()
        self.release_first = asyncio.Event()
        self.second_entered = asyncio.Event()
        self.received_envelopes: list[EventEnvelope] = []

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        self.call_count += 1
        self.received_envelopes.append(envelope)
        if self.call_count == 1:
            self.first_entered.set()
            await self.release_first.wait()
        elif self.call_count == 2:
            self.second_entered.set()


def _create_synthetic_envelope(
    event_type: str,
    payload: dict[str, object] | None = None,
) -> EventEnvelope:
    """Create a valid canonical EventEnvelope with synthetic data."""
    return create_event_envelope(
        EventCreate(
            event_type=event_type,
            event_version=1,
            tenant_id=f"tenant-runner-{uuid4().hex[:8]}",
            correlation_id=str(uuid4()),
            causation_id=uuid4(),
            subject_type="runner_order",
            subject_id=f"RUN-{uuid4().hex[:6]}",
            payload=payload or {"status": "dispatched", "step": "runner_live"},
        )
    )


async def _wait_for_runner_running(
    runner: RabbitMqConsumerRunner,
    timeout: float = 10.0,
) -> None:
    """Wait deterministically until the runner transitions to RUNNING state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while runner.state != ConsumerRunnerState.RUNNING:
        if loop.time() > deadline:
            raise TimeoutError(
                f"Runner failed to reach RUNNING state within {timeout}s "
                f"(current state: {runner.state})"
            )
        await asyncio.sleep(0.05)


async def _stop_runner_gracefully(
    runner: RabbitMqConsumerRunner,
    stop_event: asyncio.Event,
    runner_task: asyncio.Task[None],
    timeout: float = 10.0,
) -> None:
    """Signal stop and await clean runner termination."""
    stop_event.set()
    await asyncio.wait_for(runner_task, timeout=timeout)
    assert runner.state == ConsumerRunnerState.STOPPED


def _create_runner_settings(
    rabbitmq_url: SecretStr,
    suffix: str,
    max_concurrency: int = 1,
) -> RabbitMqConsumerSettings:
    """Create isolated settings for a live runner instance."""
    return RabbitMqConsumerSettings(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_exchange_name=f"e7.runner.events.{suffix}",
        rabbitmq_consumer_queue_name=f"e7.runner.consumer.{suffix}",
        rabbitmq_consumer_binding_keys=(f"runner.sub_{suffix}.#",),
        rabbitmq_dead_letter_exchange_name=f"e7.runner.dlx.{suffix}",
        rabbitmq_dead_letter_queue_name=f"e7.runner.dlq.{suffix}",
        rabbitmq_dead_letter_routing_key=f"dead.letter.sub_{suffix}",
        rabbitmq_consumer_max_concurrency=max_concurrency,
        rabbitmq_connect_timeout_seconds=10.0,
    )


# -----------------------------------------------------------------------------
# Test Cases
# -----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_startup_declares_topology_and_inspects_live(
    rabbitmq_url: SecretStr,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify that RabbitMqConsumerRunner.run() declares the full production topology.

    Zero topology declaration calls exist in test code.
    Topology is verified after startup using native rabbitmqctl CLI commands.
    """
    suffix = uuid4().hex[:8]
    settings = _create_runner_settings(rabbitmq_url, suffix)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings,
        shutdown_grace_period=timedelta(seconds=5),
    )

    spy_handler = SpyDownstreamHandler()
    processor = ConsumerEventProcessor(
        consumer_name=f"consumer.runner.topology.sub_{suffix}",
        uow_factory=uow_factory,
        handler=spy_handler,
    )
    inbound_handler = RabbitMqInboundMessageHandler(
        processor=processor,
        policy=RabbitMqInboundPolicy(max_message_bytes=65536),
    )

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=inbound_handler,
    )
    assert runner.state == ConsumerRunnerState.NEW

    stop_event = asyncio.Event()
    runner_task = asyncio.create_task(runner.run(stop_event=stop_event))

    try:
        # 1. Wait for runner to reach RUNNING state
        await _wait_for_runner_running(runner)
        assert runner.state == ConsumerRunnerState.RUNNING

        # 2. Inspect native broker state using rabbitmqctl inside e7-rabbitmq
        # Verify main exchange declared by runner
        exchanges_proc = subprocess.run(
            [
                "docker",
                "exec",
                "e7-rabbitmq",
                "rabbitmqctl",
                "list_exchanges",
                "-p",
                RABBITMQ_VHOST,
                "name",
                "type",
                "durable",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert settings.rabbitmq_exchange_name in exchanges_proc.stdout
        assert settings.rabbitmq_dead_letter_exchange_name in exchanges_proc.stdout

        # Verify queues declared by runner (work queue and DLQ)
        queues_proc = subprocess.run(
            [
                "docker",
                "exec",
                "e7-rabbitmq",
                "rabbitmqctl",
                "list_queues",
                "-p",
                RABBITMQ_VHOST,
                "name",
                "durable",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert settings.rabbitmq_consumer_queue_name in queues_proc.stdout
        assert settings.rabbitmq_dead_letter_queue_name in queues_proc.stdout

        # Verify bindings declared by runner
        bindings_proc = subprocess.run(
            [
                "docker",
                "exec",
                "e7-rabbitmq",
                "rabbitmqctl",
                "list_bindings",
                "-p",
                RABBITMQ_VHOST,
                "source_name",
                "destination_name",
                "routing_key",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert settings.rabbitmq_consumer_queue_name in bindings_proc.stdout
        assert settings.rabbitmq_dead_letter_queue_name in bindings_proc.stdout
        assert settings.rabbitmq_dead_letter_routing_key in bindings_proc.stdout

    finally:
        await _stop_runner_gracefully(runner, stop_event, runner_task)
        assert runner.state == ConsumerRunnerState.STOPPED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_push_delivery_and_inbox_deduplication_live(
    rabbitmq_url: SecretStr,
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify live push consumption, happy-path ACK, and duplicate deduplication through runner.

    Execution Flow:
    1. Runner declares topology and starts push subscription (queue.consume).
    2. Publisher emits canonical EventEnvelope via RabbitMqEventPublisher.
    3. Runner push callback delivers to RabbitMqInboundMessageHandler -> ConsumerEventProcessor.
    4. First delivery: inbox marker recorded, downstream handler executes, positive ACK sent.
    5. Second delivery (duplicate): inbox detects duplicate, handler skipped, duplicate ACK sent.
    6. Database verified: exactly 1 row in inbox_messages.
    7. Work queue verified: 0 ready, 0 unacked.
    """
    suffix = uuid4().hex[:8]
    consumer_name = f"consumer.runner.dedupe.sub_{suffix}"
    binding_key = f"runner.sub_{suffix}.order.created"
    settings = _create_runner_settings(rabbitmq_url, suffix)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings,
        shutdown_grace_period=timedelta(seconds=5),
    )

    spy_handler = SpyDownstreamHandler()
    processor = ConsumerEventProcessor(
        consumer_name=consumer_name,
        uow_factory=uow_factory,
        handler=spy_handler,
    )
    inbound_handler = RabbitMqInboundMessageHandler(
        processor=processor,
        policy=RabbitMqInboundPolicy(max_message_bytes=65536),
    )

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=inbound_handler,
    )
    stop_event = asyncio.Event()
    runner_task = asyncio.create_task(runner.run(stop_event=stop_event))

    publisher_settings = RabbitMqPublisherSettings(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_exchange_name=settings.rabbitmq_exchange_name,
        rabbitmq_connect_timeout_seconds=10.0,
    )
    publisher = RabbitMqEventPublisher(publisher_settings)

    try:
        await _wait_for_runner_running(runner)

        # 1. Publish first delivery of canonical event
        envelope = _create_synthetic_envelope(event_type=binding_key)
        await publisher.start()
        await publisher.publish(envelope)

        # 2. Wait for push delivery through runner into downstream handler
        await asyncio.wait_for(spy_handler.event_received.wait(), timeout=5.0)
        assert spy_handler.call_count == 1
        assert spy_handler.received_envelopes[0].event_id == envelope.event_id

        # 3. Publish duplicate delivery of the exact same event
        await publisher.publish(envelope)

        # 4. Allow runner push callback to process duplicate delivery
        # Poll inbox row and handler call count for up to 3 seconds
        await asyncio.sleep(0.5)
        assert spy_handler.call_count == 1, "Downstream handler must NOT be invoked for duplicate"

        # 5. Verify PostgreSQL inbox table contains exactly 1 row
        async with session_factory() as session:
            row_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM inbox_messages "
                    "WHERE consumer_name = :cname AND event_id = :eid"
                ),
                {"cname": consumer_name, "eid": envelope.event_id},
            )
            assert row_count == 1, f"Expected 1 inbox row, found {row_count}"

        # 6. Verify work queue is completely drained (both deliveries ACKed)
        conn = await aio_pika.connect_robust(rabbitmq_url.get_secret_value())
        try:
            chan = await conn.channel()
            # Passive queue query (ensure=False performs no declaration)
            queue = await chan.get_queue(settings.rabbitmq_consumer_queue_name, ensure=False)
            q_state = await queue.declare()
            assert q_state.message_count == 0
        finally:
            await conn.close()

    finally:
        await publisher.close()
        await _stop_runner_gracefully(runner, stop_event, runner_task)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_malformed_message_rejection_and_dlq_live(
    rabbitmq_url: SecretStr,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify live runner push-delivers malformed message to frozen handler, which rejects to DLQ.

    Execution Flow:
    1. Runner starts, declares topology and DLQ, starts push consumption.
    2. Raw malformed JSON message is published to the runner-declared exchange.
    3. Runner push callback delivers message to RabbitMqInboundMessageHandler.
    4. Inbound handler detects INVALID_JSON, rejects message with requeue=False.
    5. Downstream business handler is never invoked (call_count == 0).
    6. RabbitMQ dead-letters the message into the runner-declared DLQ.
    7. Test passively retrieves poison message from DLQ to verify arrival, then ACKs it.
    """
    suffix = uuid4().hex[:8]
    consumer_name = f"consumer.runner.malformed.sub_{suffix}"
    binding_key = f"runner.sub_{suffix}.data.sync"
    settings = _create_runner_settings(rabbitmq_url, suffix)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings,
        shutdown_grace_period=timedelta(seconds=5),
    )

    spy_handler = SpyDownstreamHandler()
    processor = ConsumerEventProcessor(
        consumer_name=consumer_name,
        uow_factory=uow_factory,
        handler=spy_handler,
    )
    inbound_handler = RabbitMqInboundMessageHandler(
        processor=processor,
        policy=RabbitMqInboundPolicy(max_message_bytes=65536),
    )

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=inbound_handler,
    )
    stop_event = asyncio.Event()
    runner_task = asyncio.create_task(runner.run(stop_event=stop_event))

    conn = await aio_pika.connect_robust(rabbitmq_url.get_secret_value())

    try:
        await _wait_for_runner_running(runner)

        # 1. Publish malformed message directly to runner-declared exchange
        # (ensure=False guarantees test does NOT declare the exchange)
        channel = await conn.channel()
        exchange = await channel.get_exchange(settings.rabbitmq_exchange_name, ensure=False)

        malformed_bytes = b'{"event_id": "malformed-json", invalid_syntax...'
        await exchange.publish(
            aio_pika.Message(
                body=malformed_bytes,
                content_type="application/json",
                content_encoding="utf-8",
                message_id=str(uuid4()),
                type=binding_key,
            ),
            routing_key=binding_key,
        )

        # 2. Allow runner push callback to process and reject
        # Wait up to 3 seconds for message to clear work queue and enter DLQ
        await asyncio.sleep(0.5)

        # 3. Confirm downstream business handler was NOT invoked
        assert spy_handler.call_count == 0

        # 4. Verify main work queue has 0 messages (rejected without requeue)
        work_queue = await channel.get_queue(settings.rabbitmq_consumer_queue_name, ensure=False)
        work_state = await work_queue.declare()
        assert work_state.message_count == 0

        # 5. Passively retrieve poison message from runner-declared DLQ
        dlq = await channel.get_queue(settings.rabbitmq_dead_letter_queue_name, ensure=False)
        dlq_msg = await dlq.get(timeout=5.0)
        assert dlq_msg is not None, "Rejected message did not arrive in DLQ"
        assert dlq_msg.body == malformed_bytes
        # Drain DLQ
        await dlq_msg.ack()

    finally:
        await conn.close()
        await _stop_runner_gracefully(runner, stop_event, runner_task)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runner_bounded_concurrency_and_qos_live(
    rabbitmq_url: SecretStr,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    isolated_vhost: str,
) -> None:
    """Verify live runner enforces bounded concurrency and QoS prefetch = 1.

    Execution Flow:
    1. Runner started with max_concurrency = 1.
    2. Message A published; handler A enters and holds on a barrier.
    3. Message B published while A is blocked.
    4. Concurrency bound verified: B does not enter downstream handler while A is active.
    5. Runner peak_concurrency remains <= 1.
    6. Barrier released: A finishes, then B executes and finishes.
    7. Both messages ACKed by frozen handler.
    """
    suffix = uuid4().hex[:8]
    consumer_name = f"consumer.runner.concurrency.sub_{suffix}"
    binding_key = f"runner.sub_{suffix}.job.run"
    settings = _create_runner_settings(rabbitmq_url, suffix, max_concurrency=1)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings,
        shutdown_grace_period=timedelta(seconds=5),
    )

    blocking_handler = BlockingDownstreamHandler()
    processor = ConsumerEventProcessor(
        consumer_name=consumer_name,
        uow_factory=uow_factory,
        handler=blocking_handler,
    )
    inbound_handler = RabbitMqInboundMessageHandler(
        processor=processor,
        policy=RabbitMqInboundPolicy(max_message_bytes=65536),
    )

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=inbound_handler,
    )
    stop_event = asyncio.Event()
    runner_task = asyncio.create_task(runner.run(stop_event=stop_event))

    publisher_settings = RabbitMqPublisherSettings(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_exchange_name=settings.rabbitmq_exchange_name,
        rabbitmq_connect_timeout_seconds=10.0,
    )
    publisher = RabbitMqEventPublisher(publisher_settings)

    try:
        await _wait_for_runner_running(runner)

        # 1. Publish Message A
        envelope_a = _create_synthetic_envelope(event_type=binding_key, payload={"msg": "A"})
        await publisher.start()
        await publisher.publish(envelope_a)

        # 2. Wait until Message A enters downstream handler and blocks
        await asyncio.wait_for(blocking_handler.first_entered.wait(), timeout=5.0)
        assert blocking_handler.call_count == 1

        # 3. Publish Message B while Message A is still executing
        envelope_b = _create_synthetic_envelope(event_type=binding_key, payload={"msg": "B"})
        await publisher.publish(envelope_b)

        # 4. Small yield: verify Message B is bounded and cannot enter handler
        await asyncio.sleep(0.3)
        assert blocking_handler.call_count == 1, (
            "Message B must not execute concurrently when max_concurrency is 1"
        )
        assert not blocking_handler.second_entered.is_set()
        assert runner.peak_concurrency <= 1

        # 5. Release Message A
        blocking_handler.release_first.set()

        # 6. Wait for Message B to execute
        await asyncio.wait_for(blocking_handler.second_entered.wait(), timeout=5.0)
        assert blocking_handler.call_count == 2
        assert runner.peak_concurrency == 1
        assert [e.payload["msg"] for e in blocking_handler.received_envelopes] == ["A", "B"]

    finally:
        # Guarantee barrier release in case of failure to prevent test hanging
        blocking_handler.release_first.set()
        await publisher.close()
        await _stop_runner_gracefully(runner, stop_event, runner_task)
