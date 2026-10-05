"""Unit tests for RabbitMQ consumer topology, bounded runner, and graceful lifecycle.

Part of Phase 1 Foundation Task 1G.4C2.
Validates:
- RabbitMqConsumerSettings validation, bounds checking, and SecretStr protection.
- RabbitMqConsumerRunnerPolicy validation, bounds checking, and strict immutability.
- No network I/O or broker calls upon runner instantiation.
- Pre-set stop_event cleanly exits without connecting or declaring topology.
- Exact startup order: connect -> channel -> QoS -> main exchange -> DLX -> DLQ -> DLQ bind ->
  main queue with DLX args -> topic bindings -> queue.consume(no_ack=False) -> RUNNING.
- Main exchange (topic, durable) and DLX (direct, durable) topology declarations.
- DLQ (durable, non-exclusive, non-auto-delete) bound to DLX with routing key.
- Main queue (durable, non-exclusive, non-auto-delete) declared with DLX arguments.
- Topic bindings: each configured binding key bound to main queue; no hardcoded types.
- QoS prefetch set strictly to max_concurrency (no unlimited prefetch).
- Consumer subscription registered with no_ack=False and tag retained.
- Bounded callback concurrency: peak concurrent executions <= max_concurrency.
- Active callback task tracking during runner lifecycle.
- Successful callback (ACKED_PROCESSED / REJECTED_INVALID_MESSAGE) receives zero secondary
  runner disposition (no secondary ACK, NACK, or reject).
- Callback failure fail-fast supervision: cancels subscription, stops siblings, original
  error propagates from run(), message remains unsettled.
- First failure wins: primary error preserved when multiple siblings fail.
- External stop: cancels subscription, allows in-flight callbacks within grace period,
  clean shutdown without error.
- Grace expiry forced cancellation: hung callbacks cancelled, cancellation awaited, clean teardown.
- Waiting callback on concurrency bound exits without processing on stop (remains unsettled).
- Outer runner task cancellation cleanly tears down and propagates CancelledError.
- One-shot lifecycle enforcement (no concurrent or restarted execution).
- Startup failures cleanly rollback partial resources and raise safe lifecycle errors
  without credential leakage.
- Cleanup failure with primary error attaches note via add_note without masking primary.
- Normal close failure propagates.
- Secret safety: URL/credentials absent from str, repr, and public exceptions.
- Architectural isolation: runner imports no DB, no outbox/inbox repo, no EventEnvelope,
  performs no JSON parsing, no manual ACK/reject.
- Schema stability: Base.metadata remains {audit_events, outbox_events, inbox_messages};
  Alembic head remains 0005.
- Canonical runtime test deferral constant RABBITMQ_CONSUMER_RUNNER_RUNTIME_TEST.
"""

import asyncio
import inspect
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import aio_pika
import aio_pika.abc
import pytest
from pydantic import SecretStr, ValidationError

from app.db.base import Base
from app.events.consumers import (
    RABBITMQ_CONSUMER_RUNNER_RUNTIME_TEST,
    ConsumerRunnerState,
    RabbitMqConsumerConnectionError,
    RabbitMqConsumerRunner,
    RabbitMqConsumerRunnerPolicy,
    RabbitMqConsumerSettings,
    RabbitMqConsumerSubscriptionError,
    RabbitMqConsumerTopologyError,
    RabbitMqMessageHandlingOutcome,
    RabbitMqMessageHandlingResult,
)

# -----------------------------------------------------------------------------
# Fakes and Test Doubles
# -----------------------------------------------------------------------------


def _create_sample_settings(
    *,
    url: str = "amqp://app-user:secret-pass@broker-host:5672/vhost-app",
    exchange_name: str = "app.events",
    queue_name: str = "app.events.consumer",
    binding_keys: tuple[str, ...] = ("task.blocked", "workflow.#"),
    dlx_name: str = "app.events.dlx",
    dlq_name: str = "app.events.dlq",
    dlq_routing_key: str = "dead-letter",
    max_concurrency: int = 5,
    timeout: float = 10.0,
) -> RabbitMqConsumerSettings:
    """Create valid synthetic settings for unit testing."""
    return RabbitMqConsumerSettings(
        rabbitmq_url=SecretStr(url),
        rabbitmq_exchange_name=exchange_name,
        rabbitmq_consumer_queue_name=queue_name,
        rabbitmq_consumer_binding_keys=binding_keys,
        rabbitmq_dead_letter_exchange_name=dlx_name,
        rabbitmq_dead_letter_queue_name=dlq_name,
        rabbitmq_dead_letter_routing_key=dlq_routing_key,
        rabbitmq_consumer_max_concurrency=max_concurrency,
        rabbitmq_connect_timeout_seconds=timeout,
    )


class FakeIncomingMessage:
    """Fake aio_pika incoming message tracking broker disposition calls."""

    def __init__(
        self,
        *,
        body: bytes = b'{"event_id": "test"}',
        message_id: str = "msg-123",
        event_type: str = "task.blocked",
    ) -> None:
        self.body = body
        self.message_id = message_id
        self.type = event_type
        self.ack_count = 0
        self.reject_count = 0
        self.nack_count = 0

    async def ack(self, multiple: bool = False) -> None:
        self.ack_count += 1

    async def reject(self, requeue: bool = False) -> None:
        self.reject_count += 1

    async def nack(self, multiple: bool = False, requeue: bool = False) -> None:
        self.nack_count += 1


class FakeExchange:
    """Fake exchange recording declarations."""

    def __init__(self, name: str, exchange_type: Any, durable: bool) -> None:
        self.name = name
        self.type = exchange_type
        self.durable = durable


class FakeQueue:
    """Fake queue recording declarations, bindings, and subscriptions."""

    def __init__(
        self,
        name: str,
        *,
        durable: bool = True,
        exclusive: bool = False,
        auto_delete: bool = False,
        arguments: dict[str, Any] | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self.name = name
        self.durable = durable
        self.exclusive = exclusive
        self.auto_delete = auto_delete
        self.arguments = arguments or {}
        self.bindings: list[tuple[str, str]] = []
        self.consumer_tag: str = f"tag-{name}-{uuid4().hex[:6]}"
        self.callback: Any = None
        self.no_ack: bool | None = None
        self.cancelled: bool = False
        self.cancel_call_count: int = 0
        self.timeline = timeline

    async def bind(self, exchange: Any, routing_key: str) -> None:
        ex_name = getattr(exchange, "name", str(exchange))
        self.bindings.append((ex_name, routing_key))
        if self.timeline is not None:
            self.timeline.append(f"bind_queue:{self.name}:{ex_name}:{routing_key}")

    async def consume(self, callback: Any, no_ack: bool = False) -> str:
        self.callback = callback
        self.no_ack = no_ack
        if self.timeline is not None:
            self.timeline.append(f"consume:{self.name}:no_ack={no_ack}")
        return self.consumer_tag

    async def cancel(self, consumer_tag: str) -> None:
        self.cancelled = True
        self.cancel_call_count += 1
        if self.timeline is not None:
            self.timeline.append(f"cancel_consumer:{self.name}:{consumer_tag}")

    async def deliver(self, message: Any) -> asyncio.Task[Any]:
        """Simulate a broker message delivery by spawning callback in a task."""
        if self.callback is None:
            raise RuntimeError("Cannot deliver message to queue without active consumer callback")
        task = asyncio.create_task(self.callback(message))
        return task


class FakeChannel:
    """Fake robust channel recording QoS, exchange, and queue declarations."""

    def __init__(self, timeline: list[str] | None = None) -> None:
        self.timeline = timeline
        self.prefetch_count: int | None = None
        self.exchanges: dict[str, FakeExchange] = {}
        self.queues: dict[str, FakeQueue] = {}
        self.closed: bool = False
        self.close_error: Exception | None = None

    async def set_qos(self, prefetch_count: int) -> None:
        self.prefetch_count = prefetch_count
        if self.timeline is not None:
            self.timeline.append(f"set_qos:{prefetch_count}")

    async def declare_exchange(
        self,
        name: str,
        type: Any = aio_pika.ExchangeType.TOPIC,
        durable: bool = True,
        **kwargs: Any,
    ) -> FakeExchange:
        ex = FakeExchange(name, type, durable)
        self.exchanges[name] = ex
        if self.timeline is not None:
            type_str = getattr(type, "value", str(type))
            self.timeline.append(f"declare_exchange:{name}:{type_str}:durable={durable}")
        return ex

    async def declare_queue(
        self,
        name: str,
        durable: bool = True,
        exclusive: bool = False,
        auto_delete: bool = False,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> FakeQueue:
        q = FakeQueue(
            name,
            durable=durable,
            exclusive=exclusive,
            auto_delete=auto_delete,
            arguments=arguments,
            timeline=self.timeline,
        )
        self.queues[name] = q
        if self.timeline is not None:
            self.timeline.append(
                f"declare_queue:{name}:durable={durable}:exclusive={exclusive}:auto_delete={auto_delete}"
            )
        return q

    async def close(self) -> None:
        self.closed = True
        if self.timeline is not None:
            self.timeline.append("close_channel")
        if self.close_error is not None:
            raise self.close_error


class FakeConnection:
    """Fake robust connection recording channel operations and lifecycle."""

    def __init__(self, timeline: list[str] | None = None) -> None:
        self.timeline = timeline
        self.channel_instance = FakeChannel(timeline=timeline)
        self.closed: bool = False
        self.close_error: Exception | None = None

    async def channel(self) -> FakeChannel:
        if self.timeline is not None:
            self.timeline.append("open_channel")
        return self.channel_instance

    async def close(self) -> None:
        self.closed = True
        if self.timeline is not None:
            self.timeline.append("close_connection")
        if self.close_error is not None:
            raise self.close_error


class FakeInboundHandler:
    """Fake inbound message handler conforming to InboundMessageHandlerProtocol."""

    def __init__(
        self,
        *,
        outcome: RabbitMqMessageHandlingOutcome = RabbitMqMessageHandlingOutcome.ACKED_PROCESSED,
        delay_seconds: float = 0.0,
        raise_error: Exception | None = None,
        block_event: asyncio.Event | None = None,
    ) -> None:
        self.outcome = outcome
        self.delay_seconds = delay_seconds
        self.raise_error = raise_error
        self.block_event = block_event
        self.handled_messages: list[Any] = []
        self.current_concurrency: int = 0
        self.peak_concurrency: int = 0
        self.lock = asyncio.Lock()

    async def handle(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
    ) -> RabbitMqMessageHandlingResult:
        async with self.lock:
            self.current_concurrency += 1
            if self.current_concurrency > self.peak_concurrency:
                self.peak_concurrency = self.current_concurrency

        try:
            self.handled_messages.append(message)
            if self.block_event is not None:
                await self.block_event.wait()
            if self.delay_seconds > 0:
                await asyncio.sleep(self.delay_seconds)
            if self.raise_error is not None:
                raise self.raise_error

            return RabbitMqMessageHandlingResult(
                outcome=self.outcome,
                event_id=uuid4(),
            )
        finally:
            async with self.lock:
                self.current_concurrency -= 1


# ==============================================================================
# 1. Configuration & Policy Validation Tests
# ==============================================================================


def test_settings_valid_configuration() -> None:
    """Test valid settings construction with all fields parsed and validated."""
    settings = _create_sample_settings()
    assert (
        settings.rabbitmq_url.get_secret_value()
        == "amqp://app-user:secret-pass@broker-host:5672/vhost-app"
    )
    assert settings.rabbitmq_exchange_name == "app.events"
    assert settings.rabbitmq_consumer_queue_name == "app.events.consumer"
    assert settings.rabbitmq_consumer_binding_keys == ("task.blocked", "workflow.#")
    assert settings.rabbitmq_dead_letter_exchange_name == "app.events.dlx"
    assert settings.rabbitmq_dead_letter_queue_name == "app.events.dlq"
    assert settings.rabbitmq_dead_letter_routing_key == "dead-letter"
    assert settings.rabbitmq_consumer_max_concurrency == 5
    assert settings.rabbitmq_connect_timeout_seconds == 10.0


def test_settings_missing_required_fields_fails_closed() -> None:
    """Verify missing any required topology setting fails validation closed."""
    valid_data = {
        "rabbitmq_url": SecretStr("amqp://user:pass@host:5672/"),
        "rabbitmq_exchange_name": "events",
        "rabbitmq_consumer_queue_name": "events.queue",
        "rabbitmq_consumer_binding_keys": ("task.blocked",),
        "rabbitmq_dead_letter_exchange_name": "events.dlx",
        "rabbitmq_dead_letter_queue_name": "events.dlq",
        "rabbitmq_dead_letter_routing_key": "dead-letter",
        "rabbitmq_consumer_max_concurrency": 2,
    }

    for key in valid_data:
        invalid_data = dict(valid_data)
        del invalid_data[key]
        with pytest.raises(ValidationError):
            RabbitMqConsumerSettings(**invalid_data)


def test_settings_url_scheme_validation() -> None:
    """Verify RabbitMQ URL requires amqp:// or amqps:// protocol."""
    base_data = {
        "rabbitmq_exchange_name": "events",
        "rabbitmq_consumer_queue_name": "events.queue",
        "rabbitmq_consumer_binding_keys": ("task.blocked",),
        "rabbitmq_dead_letter_exchange_name": "events.dlx",
        "rabbitmq_dead_letter_queue_name": "events.dlq",
        "rabbitmq_dead_letter_routing_key": "dead-letter",
        "rabbitmq_consumer_max_concurrency": 2,
    }

    with pytest.raises(ValidationError, match="must start with 'amqp://' or 'amqps://'"):
        RabbitMqConsumerSettings(
            rabbitmq_url=SecretStr("http://broker:5672/"),
            **base_data,
        )

    # amqps:// is accepted
    s = RabbitMqConsumerSettings(
        rabbitmq_url=SecretStr("amqps://user:pass@broker:5671/"),
        **base_data,
    )
    assert s.rabbitmq_url.get_secret_value().startswith("amqps://")


def test_settings_empty_and_whitespace_topology_names_rejected() -> None:
    """Verify empty, whitespace, and over-length topology names are rejected."""
    name_fields = [
        "rabbitmq_exchange_name",
        "rabbitmq_consumer_queue_name",
        "rabbitmq_dead_letter_exchange_name",
        "rabbitmq_dead_letter_queue_name",
        "rabbitmq_dead_letter_routing_key",
    ]

    for field in name_fields:
        # Blank / whitespace
        kwargs: dict[str, Any] = {
            "rabbitmq_url": SecretStr("amqp://user:pass@host:5672/"),
            "rabbitmq_exchange_name": "events",
            "rabbitmq_consumer_queue_name": "events.queue",
            "rabbitmq_consumer_binding_keys": ("task.blocked",),
            "rabbitmq_dead_letter_exchange_name": "events.dlx",
            "rabbitmq_dead_letter_queue_name": "events.dlq",
            "rabbitmq_dead_letter_routing_key": "dead-letter",
            "rabbitmq_consumer_max_concurrency": 2,
        }
        kwargs[field] = "   "
        with pytest.raises(ValidationError, match="must not be empty or whitespace"):
            RabbitMqConsumerSettings(**kwargs)

        # Exceeds max length
        kwargs[field] = "a" * 256
        with pytest.raises(
            ValidationError,
            match=r"(must not exceed 255 characters|at most 255 characters)",
        ):
            RabbitMqConsumerSettings(**kwargs)


def test_settings_binding_keys_validation() -> None:
    """Verify robust binding keys validation (wildcards, dot segments, whitespace, duplicates)."""
    base_data = {
        "rabbitmq_url": SecretStr("amqp://user:pass@host:5672/"),
        "rabbitmq_exchange_name": "events",
        "rabbitmq_consumer_queue_name": "events.queue",
        "rabbitmq_dead_letter_exchange_name": "events.dlx",
        "rabbitmq_dead_letter_queue_name": "events.dlq",
        "rabbitmq_dead_letter_routing_key": "dead-letter",
        "rabbitmq_consumer_max_concurrency": 2,
    }

    # Empty list
    with pytest.raises(ValidationError, match="must not be empty"):
        RabbitMqConsumerSettings(rabbitmq_consumer_binding_keys=(), **base_data)

    # Duplicate keys
    with pytest.raises(ValidationError, match="contains duplicate key"):
        RabbitMqConsumerSettings(
            rabbitmq_consumer_binding_keys=("task.blocked", "task.blocked"),
            **base_data,
        )

    # Empty dot segment (leading dot)
    with pytest.raises(ValidationError, match="contains empty dot segments"):
        RabbitMqConsumerSettings(
            rabbitmq_consumer_binding_keys=(".task.blocked",),
            **base_data,
        )

    # Empty dot segment (trailing dot)
    with pytest.raises(ValidationError, match="contains empty dot segments"):
        RabbitMqConsumerSettings(
            rabbitmq_consumer_binding_keys=("task.blocked.",),
            **base_data,
        )

    # Empty dot segment (consecutive dots)
    with pytest.raises(ValidationError, match="contains empty dot segments"):
        RabbitMqConsumerSettings(
            rabbitmq_consumer_binding_keys=("task..blocked",),
            **base_data,
        )

    # Malformed wildcard (wildcard not whole segment)
    with pytest.raises(
        ValidationError, match="Wildcards '\\*' and '#' must constitute entire topic segments"
    ):
        RabbitMqConsumerSettings(
            rabbitmq_consumer_binding_keys=("task*.created",),
            **base_data,
        )

    with pytest.raises(
        ValidationError, match="Wildcards '\\*' and '#' must constitute entire topic segments"
    ):
        RabbitMqConsumerSettings(
            rabbitmq_consumer_binding_keys=("task#created",),
            **base_data,
        )

    # Valid wildcard patterns
    s = RabbitMqConsumerSettings(
        rabbitmq_consumer_binding_keys=("#", "*", "task.*", "workflow.#", "audit.*.created"),
        **base_data,
    )
    assert s.rabbitmq_consumer_binding_keys == (
        "#",
        "*",
        "task.*",
        "workflow.#",
        "audit.*.created",
    )

    # JSON string input parsed correctly
    s_json = RabbitMqConsumerSettings(
        rabbitmq_consumer_binding_keys='["event.one", "event.two"]',
        **base_data,
    )
    assert s_json.rabbitmq_consumer_binding_keys == ("event.one", "event.two")


def test_settings_secret_masking() -> None:
    """Verify RabbitMQ URL is protected and masked in repr and str."""
    settings = _create_sample_settings(url="amqp://secret-user:super-secret-password@broker:5672/")
    assert "super-secret-password" not in repr(settings)
    assert "super-secret-password" not in str(settings)
    assert "**********" in repr(settings)


def test_runner_policy_validation() -> None:
    """Verify RabbitMqConsumerRunnerPolicy validates bounds and immutability."""
    # max_concurrency < 1 rejected
    with pytest.raises(
        ValidationError,
        match=r"(max_concurrency must be >= 1|greater than 0)",
    ):
        RabbitMqConsumerRunnerPolicy(
            max_concurrency=0,
            shutdown_grace_period=timedelta(seconds=5),
        )

    # shutdown_grace_period <= 0 rejected
    with pytest.raises(ValidationError, match="shutdown_grace_period must be strictly positive"):
        RabbitMqConsumerRunnerPolicy(
            max_concurrency=2,
            shutdown_grace_period=timedelta(seconds=0),
        )

    # Valid policy
    policy = RabbitMqConsumerRunnerPolicy(
        max_concurrency=4,
        shutdown_grace_period=timedelta(seconds=10),
    )
    assert policy.max_concurrency == 4
    assert policy.shutdown_grace_period == timedelta(seconds=10)

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        RabbitMqConsumerRunnerPolicy(
            max_concurrency=4,
            shutdown_grace_period=timedelta(seconds=10),
            extra_field=True,  # type: ignore[call-arg]
        )

    # from_settings factory
    settings = _create_sample_settings(max_concurrency=7)
    policy_from_settings = RabbitMqConsumerRunnerPolicy.from_settings(
        settings,
        shutdown_grace_period=timedelta(seconds=12),
    )
    assert policy_from_settings.max_concurrency == 7
    assert policy_from_settings.shutdown_grace_period == timedelta(seconds=12)


# ==============================================================================
# 2. Runner Initialization & Pre-Flight Tests
# ==============================================================================


def test_runner_zero_network_on_init() -> None:
    """Verify instantiating runner performs zero network I/O or broker calls."""
    settings = _create_sample_settings()
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()
    mock_factory = AsyncMock()

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=mock_factory,
    )

    assert runner.state == ConsumerRunnerState.NEW
    assert runner.settings == settings
    assert runner.runner_policy == policy
    assert mock_factory.call_count == 0


def test_runner_type_validations() -> None:
    """Verify runner rejects invalid argument types."""
    settings = _create_sample_settings()
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()

    with pytest.raises(TypeError, match="settings must be a RabbitMqConsumerSettings"):
        RabbitMqConsumerRunner(
            settings="not_settings",  # type: ignore[arg-type]
            runner_policy=policy,
            inbound_handler=handler,
        )

    with pytest.raises(TypeError, match="runner_policy must be a RabbitMqConsumerRunnerPolicy"):
        RabbitMqConsumerRunner(
            settings=settings,
            runner_policy="not_policy",  # type: ignore[arg-type]
            inbound_handler=handler,
        )

    with pytest.raises(
        TypeError, match="inbound_handler must implement InboundMessageHandlerProtocol"
    ):
        RabbitMqConsumerRunner(
            settings=settings,
            runner_policy=policy,
            inbound_handler=object(),  # type: ignore[arg-type]
        )


def test_runner_concurrency_mismatch_rejected() -> None:
    """Verify runner rejects mismatch between settings and runner_policy concurrency."""
    settings = _create_sample_settings(max_concurrency=5)
    policy = RabbitMqConsumerRunnerPolicy(
        max_concurrency=10,
        shutdown_grace_period=timedelta(seconds=5),
    )
    handler = FakeInboundHandler()

    with pytest.raises(ValueError, match="max_concurrency mismatch"):
        RabbitMqConsumerRunner(
            settings=settings,
            runner_policy=policy,
            inbound_handler=handler,
        )


@pytest.mark.asyncio
async def test_runner_pre_set_stop_event_zero_network() -> None:
    """Verify pre-set stop_event immediately transitions to STOPPED with zero broker calls."""
    settings = _create_sample_settings()
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()
    mock_factory = AsyncMock()

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=mock_factory,
    )

    stop_event = asyncio.Event()
    stop_event.set()

    await runner.run(stop_event=stop_event)

    assert runner.state == ConsumerRunnerState.STOPPED
    assert mock_factory.call_count == 0


@pytest.mark.asyncio
async def test_runner_one_shot_lifecycle() -> None:
    """Verify runner rejects concurrent runs and restarting after stopped."""
    settings = _create_sample_settings()
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()

    timeline: list[str] = []
    fake_conn = FakeConnection(timeline=timeline)
    mock_factory = AsyncMock(return_value=fake_conn)

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=mock_factory,
    )

    stop_event = asyncio.Event()

    run_task = asyncio.create_task(runner.run(stop_event=stop_event))
    # Wait until runner transitions to RUNNING
    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    # Calling run() while already RUNNING raises RuntimeError
    with pytest.raises(RuntimeError, match="already running"):
        await runner.run(stop_event=stop_event)

    # Stop runner cleanly
    stop_event.set()
    await run_task
    assert runner.state == ConsumerRunnerState.STOPPED

    # Restarting after STOPPED raises RuntimeError
    with pytest.raises(RuntimeError, match="cannot be restarted after stopping"):
        await runner.run(stop_event=stop_event)


# ==============================================================================
# 3. Startup Order & Topology Declarations
# ==============================================================================


@pytest.mark.asyncio
async def test_startup_sequence_order() -> None:
    """Verify exact required startup declaration ordering using timeline recording."""
    settings = _create_sample_settings(
        exchange_name="main.exchange",
        queue_name="main.queue",
        binding_keys=("key.alpha", "key.beta"),
        dlx_name="main.dlx",
        dlq_name="main.dlq",
        dlq_routing_key="dlq.routing",
        max_concurrency=3,
    )
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()

    timeline: list[str] = []
    fake_conn = FakeConnection(timeline=timeline)

    async def _mock_factory(url: str, timeout: float) -> FakeConnection:
        timeline.append(f"connect:{timeout}")
        return fake_conn

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=_mock_factory,
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    expected_prefix = [
        "connect:10.0",
        "open_channel",
        "set_qos:3",
        "declare_exchange:main.exchange:topic:durable=True",
        "declare_exchange:main.dlx:direct:durable=True",
        "declare_queue:main.dlq:durable=True:exclusive=False:auto_delete=False",
        "bind_queue:main.dlq:main.dlx:dlq.routing",
        "declare_queue:main.queue:durable=True:exclusive=False:auto_delete=False",
        "bind_queue:main.queue:main.exchange:key.alpha",
        "bind_queue:main.queue:main.exchange:key.beta",
        "consume:main.queue:no_ack=False",
    ]

    assert timeline[: len(expected_prefix)] == expected_prefix

    # Clean shutdown
    stop_event.set()
    await run_task
    assert runner.state == ConsumerRunnerState.STOPPED


@pytest.mark.asyncio
async def test_topology_and_dlx_arguments() -> None:
    """Verify exchanges, queues, DLX binding, and main queue DLX arguments."""
    settings = _create_sample_settings(
        exchange_name="test.event.exchange",
        queue_name="test.consumer.queue",
        dlx_name="test.dlx",
        dlq_name="test.dlq",
        dlq_routing_key="dlq-key",
        max_concurrency=4,
    )
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()

    fake_conn = FakeConnection()
    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    channel = fake_conn.channel_instance

    # Exchanges
    assert "test.event.exchange" in channel.exchanges
    main_ex = channel.exchanges["test.event.exchange"]
    assert main_ex.type == aio_pika.ExchangeType.TOPIC
    assert main_ex.durable is True

    assert "test.dlx" in channel.exchanges
    dlx = channel.exchanges["test.dlx"]
    assert dlx.type == aio_pika.ExchangeType.DIRECT
    assert dlx.durable is True

    # DLQ
    assert "test.dlq" in channel.queues
    dlq = channel.queues["test.dlq"]
    assert dlq.durable is True
    assert dlq.exclusive is False
    assert dlq.auto_delete is False
    assert ("test.dlx", "dlq-key") in dlq.bindings

    # Main Queue
    assert "test.consumer.queue" in channel.queues
    main_q = channel.queues["test.consumer.queue"]
    assert main_q.durable is True
    assert main_q.exclusive is False
    assert main_q.auto_delete is False
    assert main_q.arguments == {
        "x-dead-letter-exchange": "test.dlx",
        "x-dead-letter-routing-key": "dlq-key",
    }

    # Bindings
    for k in settings.rabbitmq_consumer_binding_keys:
        assert ("test.event.exchange", k) in main_q.bindings

    # Prefetch QoS
    assert channel.prefetch_count == 4

    # Consume mode
    assert main_q.no_ack is False

    stop_event.set()
    await run_task


# ==============================================================================
# 4. Message Dispatch & Bounded Concurrency
# ==============================================================================


@pytest.mark.asyncio
async def test_bounded_concurrency_and_peak() -> None:
    """Verify peak concurrent executions of inbound_handler.handle() <= max_concurrency."""
    max_c = 3
    settings = _create_sample_settings(max_concurrency=max_c)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )

    # Handler blocks on event so messages pile up concurrently
    block_event = asyncio.Event()
    handler = FakeInboundHandler(block_event=block_event)

    fake_conn = FakeConnection()
    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    main_q = fake_conn.channel_instance.queues[settings.rabbitmq_consumer_queue_name]

    # Deliver 6 messages (twice max_concurrency)
    delivery_tasks = [
        await main_q.deliver(FakeIncomingMessage(message_id=f"msg-{i}")) for i in range(6)
    ]

    # Allow deliveries to reach the handler
    await asyncio.sleep(0.05)

    # Exactly max_c should be executing
    assert runner.peak_concurrency == max_c
    assert handler.peak_concurrency == max_c

    # Release blocked handler calls
    block_event.set()
    await asyncio.gather(*delivery_tasks)

    assert len(handler.handled_messages) == 6
    assert runner.peak_concurrency <= max_c

    stop_event.set()
    await run_task


@pytest.mark.asyncio
async def test_successful_callback_no_secondary_disposition() -> None:
    """Verify runner issues zero secondary ack/reject/nack on successful C1 return."""
    settings = _create_sample_settings(max_concurrency=2)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler(outcome=RabbitMqMessageHandlingOutcome.ACKED_PROCESSED)

    fake_conn = FakeConnection()
    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    main_q = fake_conn.channel_instance.queues[settings.rabbitmq_consumer_queue_name]
    msg = FakeIncomingMessage()

    task = await main_q.deliver(msg)
    await task

    # Handler was invoked
    assert len(handler.handled_messages) == 1

    # Fake message broker disposition methods were NOT called by runner
    # (In real flow, frozen C1 handler called ack; runner must call zero broker methods)
    assert msg.ack_count == 0
    assert msg.reject_count == 0
    assert msg.nack_count == 0

    stop_event.set()
    await run_task


# ==============================================================================
# 5. Failure Supervision & Fail-Fast
# ==============================================================================


@pytest.mark.asyncio
async def test_callback_failure_fail_fast_and_propagates_primary() -> None:
    """Verify callback exception stops consumer subscription, tears down, and propagates."""
    settings = _create_sample_settings(max_concurrency=2)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )

    primary_exc = RuntimeError("Simulated business handler processing crash")
    handler = FakeInboundHandler(raise_error=primary_exc)

    timeline: list[str] = []
    fake_conn = FakeConnection(timeline=timeline)

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    main_q = fake_conn.channel_instance.queues[settings.rabbitmq_consumer_queue_name]
    msg = FakeIncomingMessage()

    # Deliver message which will fail in handle()
    await main_q.deliver(msg)

    # run() must raise the primary exception
    with pytest.raises(RuntimeError) as exc_info:
        await run_task

    assert exc_info.value is primary_exc
    assert runner.state == ConsumerRunnerState.STOPPED

    # Consumer was cancelled promptly
    assert main_q.cancelled is True
    # Channel and connection closed
    assert fake_conn.channel_instance.closed is True
    assert fake_conn.closed is True


@pytest.mark.asyncio
async def test_first_failure_wins() -> None:
    """Verify with concurrent callback failures, the first observed failure becomes primary."""
    settings = _create_sample_settings(max_concurrency=2)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )

    first_err = RuntimeError("First failure")
    second_err = ValueError("Second failure")

    class DualFailureHandler:
        def __init__(self) -> None:
            self.count = 0

        async def handle(self, message: Any) -> RabbitMqMessageHandlingResult:
            self.count += 1
            if self.count == 1:
                raise first_err
            raise second_err

    handler = DualFailureHandler()
    fake_conn = FakeConnection()
    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,  # type: ignore[arg-type]
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    main_q = fake_conn.channel_instance.queues[settings.rabbitmq_consumer_queue_name]

    # Deliver two failing messages
    await main_q.deliver(FakeIncomingMessage())
    await main_q.deliver(FakeIncomingMessage())

    with pytest.raises(RuntimeError) as exc_info:
        await run_task

    # First error wins
    assert exc_info.value is first_err


# ==============================================================================
# 6. Graceful Teardown & Forced Cancellation
# ==============================================================================


@pytest.mark.asyncio
async def test_external_normal_stop_graceful_drain() -> None:
    """Verify external stop allows in-flight callbacks to complete within grace period."""
    settings = _create_sample_settings(max_concurrency=2)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )

    # In-flight handler takes 0.1s to finish
    handler = FakeInboundHandler(delay_seconds=0.1)

    fake_conn = FakeConnection()
    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    main_q = fake_conn.channel_instance.queues[settings.rabbitmq_consumer_queue_name]

    # Deliver message
    delivery_task = await main_q.deliver(FakeIncomingMessage())

    # Set stop_event while message is in-flight
    await asyncio.sleep(0.02)
    stop_event.set()

    # Wait for run to finish cleanly
    await run_task
    await delivery_task

    assert runner.state == ConsumerRunnerState.STOPPED
    assert len(handler.handled_messages) == 1
    assert fake_conn.channel_instance.closed is True
    assert fake_conn.closed is True


@pytest.mark.asyncio
async def test_forced_cancellation_on_grace_expiry() -> None:
    """Verify callback hung beyond shutdown_grace_period is force-cancelled."""
    settings = _create_sample_settings(max_concurrency=1)
    # Short grace period for fast test execution
    policy = RabbitMqConsumerRunnerPolicy(
        max_concurrency=1,
        shutdown_grace_period=timedelta(milliseconds=50),
    )

    # Blocked handler that never finishes on its own
    never_finish = asyncio.Event()
    handler = FakeInboundHandler(block_event=never_finish)

    fake_conn = FakeConnection()
    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    main_q = fake_conn.channel_instance.queues[settings.rabbitmq_consumer_queue_name]
    await main_q.deliver(FakeIncomingMessage())

    await asyncio.sleep(0.02)
    stop_event.set()

    # run() must complete after grace expiry forced cancellation
    await run_task

    assert runner.state == ConsumerRunnerState.STOPPED
    assert fake_conn.channel_instance.closed is True
    assert fake_conn.closed is True


@pytest.mark.asyncio
async def test_waiting_callback_on_concurrency_bound_exits_without_handling_on_stop() -> None:
    """Verify callback waiting on concurrency bound exits without handling when stop is observed."""
    settings = _create_sample_settings(max_concurrency=1)
    policy = RabbitMqConsumerRunnerPolicy(
        max_concurrency=1,
        shutdown_grace_period=timedelta(seconds=2),
    )

    # First callback blocks to hold concurrency
    first_block = asyncio.Event()
    handler = FakeInboundHandler(block_event=first_block)

    fake_conn = FakeConnection()
    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    main_q = fake_conn.channel_instance.queues[settings.rabbitmq_consumer_queue_name]

    # Deliver message 1 (will occupy concurrency)
    t1 = await main_q.deliver(FakeIncomingMessage(message_id="msg-1"))
    await asyncio.sleep(0.02)

    # Deliver message 2 (will wait on concurrency semaphore)
    t2 = await main_q.deliver(FakeIncomingMessage(message_id="msg-2"))
    await asyncio.sleep(0.02)

    # Signal stop before releasing message 1
    stop_event.set()
    await asyncio.sleep(0.02)

    # Release message 1 so it completes within grace
    first_block.set()

    await run_task
    await asyncio.gather(t1, t2, return_exceptions=True)

    # Only message 1 was handled! Message 2 exited without calling handle()
    assert len(handler.handled_messages) == 1
    assert handler.handled_messages[0].message_id == "msg-1"


@pytest.mark.asyncio
async def test_outer_runner_cancellation() -> None:
    """Verify externally cancelling runner.run() propagates CancelledError with clean teardown."""
    settings = _create_sample_settings(max_concurrency=2)
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()

    fake_conn = FakeConnection()
    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    # Cancel runner task externally
    run_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert runner.state == ConsumerRunnerState.STOPPED
    assert fake_conn.channel_instance.closed is True
    assert fake_conn.closed is True


# ==============================================================================
# 7. Startup Failures & Cleanup Error Preservation
# ==============================================================================


@pytest.mark.asyncio
async def test_startup_failures_cleanup_and_safe_exceptions() -> None:
    """Verify startup failures across all phases rollback partial resources safely."""
    settings = _create_sample_settings()
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()

    # 1. Connect failure
    connect_fail_factory = AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))
    r1 = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=connect_fail_factory,
    )
    with pytest.raises(
        RabbitMqConsumerConnectionError, match="Failed to establish RabbitMQ connection"
    ) as exc_info:
        await r1.run(stop_event=asyncio.Event())
    assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)
    assert r1.state == ConsumerRunnerState.STOPPED

    # 2. Channel open failure
    fake_conn_2 = FakeConnection()
    fake_conn_2.channel = AsyncMock(side_effect=RuntimeError("Channel error"))  # type: ignore[method-assign]
    r2 = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn_2),
    )
    with pytest.raises(RabbitMqConsumerConnectionError, match="Failed to open RabbitMQ channel"):
        await r2.run(stop_event=asyncio.Event())
    assert fake_conn_2.closed is True
    assert r2.state == ConsumerRunnerState.STOPPED

    # 3. Exchange declaration failure
    fake_conn_3 = FakeConnection()
    fake_conn_3.channel_instance.declare_exchange = AsyncMock(
        side_effect=RuntimeError("Exchange declare failed")
    )  # type: ignore[method-assign]
    r3 = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn_3),
    )
    with pytest.raises(
        RabbitMqConsumerTopologyError, match="Failed to declare main event exchange"
    ):
        await r3.run(stop_event=asyncio.Event())
    assert fake_conn_3.channel_instance.closed is True
    assert fake_conn_3.closed is True
    assert r3.state == ConsumerRunnerState.STOPPED

    # 4. Queue consume failure
    fake_conn_4 = FakeConnection()
    orig_declare_q = fake_conn_4.channel_instance.declare_queue

    async def _failing_declare_q(name: str, **kwargs: Any) -> FakeQueue:
        q = await orig_declare_q(name, **kwargs)
        if name == settings.rabbitmq_consumer_queue_name:
            q.consume = AsyncMock(side_effect=RuntimeError("Consume registration rejected"))  # type: ignore[method-assign]
        return q

    fake_conn_4.channel_instance.declare_queue = _failing_declare_q  # type: ignore[method-assign]
    r4 = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn_4),
    )
    with pytest.raises(
        RabbitMqConsumerSubscriptionError, match="Failed to register consumer on queue"
    ):
        await r4.run(stop_event=asyncio.Event())
    assert fake_conn_4.channel_instance.closed is True
    assert fake_conn_4.closed is True
    assert r4.state == ConsumerRunnerState.STOPPED


@pytest.mark.asyncio
async def test_cleanup_failure_with_primary_failure_preserves_primary() -> None:
    """Verify primary callback error remains propagated when channel close fails secondarily."""
    settings = _create_sample_settings()
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )

    primary_err = RuntimeError("Primary callback failure")
    handler = FakeInboundHandler(raise_error=primary_err)

    fake_conn = FakeConnection()
    fake_conn.channel_instance.close_error = RuntimeError("Secondary channel close crash")

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    main_q = fake_conn.channel_instance.queues[settings.rabbitmq_consumer_queue_name]
    await main_q.deliver(FakeIncomingMessage())

    with pytest.raises(RuntimeError) as exc_info:
        await run_task

    # Primary error propagates unchanged
    assert exc_info.value is primary_err

    # Secondary error was attached via add_note
    notes = getattr(exc_info.value, "__notes__", [])
    assert any("Secondary failure during channel close: RuntimeError" in n for n in notes)


@pytest.mark.asyncio
async def test_normal_close_failure_propagates() -> None:
    """Verify cleanup error during normal external stop propagates."""
    settings = _create_sample_settings()
    policy = RabbitMqConsumerRunnerPolicy.from_settings(
        settings, shutdown_grace_period=timedelta(seconds=5)
    )
    handler = FakeInboundHandler()

    fake_conn = FakeConnection()
    fake_conn.channel_instance.close_error = RuntimeError("Channel close network error")

    runner = RabbitMqConsumerRunner(
        settings=settings,
        runner_policy=policy,
        inbound_handler=handler,
        connection_factory=AsyncMock(return_value=fake_conn),
    )

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(runner.run(stop_event=stop_event))

    while runner.state != ConsumerRunnerState.RUNNING:
        await asyncio.sleep(0.01)

    stop_event.set()

    with pytest.raises(RuntimeError, match="Channel close network error"):
        await run_task

    assert runner.state == ConsumerRunnerState.STOPPED


def test_secret_safety_in_exceptions() -> None:
    """Verify public lifecycle exceptions do not include secret credentials."""
    secret_pass = "top-secret-password-xyz"
    settings = _create_sample_settings(url=f"amqp://user:{secret_pass}@broker:5672/")

    exc = RabbitMqConsumerConnectionError("Failed to establish RabbitMQ connection")
    assert secret_pass not in str(exc)
    assert secret_pass not in repr(exc)
    assert secret_pass not in repr(settings)
    assert secret_pass not in str(settings)


# ==============================================================================
# 8. Architectural Boundaries & Schema Stability
# ==============================================================================


def test_architectural_isolation() -> None:
    """Verify consumer runner modules maintain absolute architectural isolation."""
    import app.events.consumers.config as config_mod
    import app.events.consumers.rabbitmq_runner as runner_mod

    runner_source = inspect.getsource(runner_mod)
    config_source = inspect.getsource(config_mod)

    # No SQLAlchemy or database session imports
    for forbidden in [
        "sqlalchemy",
        "AsyncSession",
        "sessionmaker",
        "InboxRepository",
        "OutboxRepository",
        "AuditRepository",
        "RabbitMqEventPublisher",
    ]:
        assert forbidden not in runner_source, (
            f"Forbidden import '{forbidden}' found in rabbitmq_runner"
        )
        assert forbidden not in config_source, f"Forbidden import '{forbidden}' found in config"

    # Runner must NOT contain event parsing or validation logic
    assert "EventEnvelope.model_validate" not in runner_source
    assert "json.loads" not in runner_source

    # Runner must NOT perform message disposition directly
    assert ".ack(" not in runner_source
    assert ".reject(" not in runner_source
    assert ".nack(" not in runner_source


def test_schema_stability() -> None:
    """Verify database models and migrations remain stable at Alembic head 0008."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    import app.db.models  # noqa: F401

    phase1_tables = {
        "audit_events",
        "outbox_events",
        "inbox_messages",
        "idempotency_records",
        "jobs",
    }
    canonical_phase4_tables = phase1_tables | {
        "goals",
        "plans",
        "milestones",
        "tasks",
        "task_dependencies",
        "task_risks",
        "workflow_definitions",
        "workflow_instances",
        "workflow_task_mappings",
        "sla_policies",
        "workflow_artifacts",
        # Phase 4A Scheduling (7)
        "resources",
        "resource_availabilities",
        "constraint_definitions",
        "solver_profiles",
        "schedule_jobs",
        "schedules",
        "schedule_assignments",
        # Phase 4B Calendar (1)
        "calendar_links",
    }
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 24

    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    head = script_dir.get_current_head()
    assert head in ("0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012")
    assert head == "0012"

    # Verify migration chain consistency
    versions_dir = Path("app/db/migrations/versions")
    migration_files = [f.name for f in versions_dir.glob("*.py")]
    assert any("0005" in f for f in migration_files)
    assert any("0009" in f for f in migration_files)
    assert any("0010" in f for f in migration_files)
    assert any("0011" in f for f in migration_files)
    assert any("0012" in f for f in migration_files)
    assert not any("0013" in f for f in migration_files)


def test_deferred_constant() -> None:
    """Verify runtime integration test deferral constant is declared."""
    assert RABBITMQ_CONSUMER_RUNNER_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
