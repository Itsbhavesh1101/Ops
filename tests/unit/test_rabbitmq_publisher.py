"""Unit tests for RabbitMQ event publisher transport foundation.

Part of Phase 1 Foundation Task 1G.3A / 1G.3A.1.
Validates:
- Provider-neutral EventPublisher protocol conformance and type isolation.
- Fail-closed runtime configuration: required URL, required exchange name,
  zero localhost/guest fallback.
- Technical timeout validation (positive, bounded <= 300.0, configurable).
- Settings security: SecretStr protection in repr, str, and error messages.
- Environment variable configuration: E7_ prefix support.
- Explicit settings injection into RabbitMqEventPublisher.
- Zero network I/O at import time and construction time.
- Connection and channel lifecycle: start, idempotent start, close, idempotent close.
- Channel configuration: publisher_confirms=True, on_return_raises=True.
- Exchange declaration: durable=True, type=topic, zero queue declarations.
- Start failure resource cleanup and safe error wrapping without credential leakage.
- Message framing: UTF-8 JSON body, persistent mode, routing_key=event_type, message_id=event_id.
- Publish before start and publish after close rejection.
- Broker publication failure wrapping in safe EventPublishError.
- Architectural boundary: zero database or SQLAlchemy dependencies.
- Documented deferral of live broker integration testing to Task 1J.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import aio_pika
import pytest
from pydantic import SecretStr, ValidationError

import app.db.models  # noqa: F401
from app.db.base import Base
from app.events.consumers.rabbitmq_message import (
    RabbitMqInboundMessageHandler,
    RabbitMqInboundPolicy,
    RabbitMqMessageHandlingOutcome,
)
from app.events.inbox.processor import (
    ConsumerProcessingOutcome,
    ConsumerProcessingResult,
)
from app.events.publishers import (
    EventPublisher,
    EventPublishError,
    PublisherConnectionError,
    PublisherNotStartedError,
    PublisherState,
    PublisherStateError,
    RabbitMqEventPublisher,
    RabbitMqPublisherSettings,
)
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from app.events.serialization import serialize_event_envelope

# Canonical verification boundary constant
RABBITMQ_PUBLISH_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


def _create_sample_settings(
    *,
    url: str = "amqp://test-user:test-pass@test-host:5672/test-vhost",
    exchange_name: str = "test.events",
    timeout: float = 10.0,
) -> RabbitMqPublisherSettings:
    """Helper to create valid synthetic settings for testing."""
    return RabbitMqPublisherSettings(
        rabbitmq_url=SecretStr(url),
        rabbitmq_exchange_name=exchange_name,
        rabbitmq_connect_timeout_seconds=timeout,
    )


def _create_sample_envelope(
    *,
    event_type: str = "task.blocked",
    correlation_id: str | None = "corr-test-123",
) -> EventEnvelope:
    """Helper to create a canonical validated EventEnvelope for testing."""
    event_create = EventCreate(
        event_type=event_type,
        event_version=1,
        tenant_id="tenant-acme",
        correlation_id=correlation_id,
        causation_id=UUID("44444444-4444-4444-4444-444444444444"),
        subject_type="task",
        subject_id="task-789",
        payload={"reason": "Waiting on upstream resource", "severity": 2},
    )
    return create_event_envelope(event_create)


def _create_mock_broker() -> tuple[MagicMock, MagicMock, MagicMock, AsyncMock]:
    """Helper to create a set of mock aio-pika broker objects and a connection factory."""
    mock_exchange = AsyncMock(spec=aio_pika.abc.AbstractRobustExchange)
    mock_exchange.publish = AsyncMock()

    mock_channel = AsyncMock(spec=aio_pika.abc.AbstractRobustChannel)
    mock_channel.declare_exchange = AsyncMock(return_value=mock_exchange)
    mock_channel.close = AsyncMock()

    mock_connection = AsyncMock(spec=aio_pika.abc.AbstractRobustConnection)
    mock_connection.channel = AsyncMock(return_value=mock_channel)
    mock_connection.close = AsyncMock()

    mock_factory = AsyncMock(return_value=mock_connection)
    return mock_exchange, mock_channel, mock_connection, mock_factory


# ==============================================================================
# 1. Provider-Neutral Protocol & Boundary Tests
# ==============================================================================


def test_provider_neutral_contract_exists_and_isolates_amqp_types() -> None:
    """Verify EventPublisher protocol does not leak aio-pika or AMQP types."""
    sig = inspect.signature(EventPublisher.publish)
    param = sig.parameters["envelope"]
    assert param.annotation is EventEnvelope

    # Protocol module must have no aio_pika imports in its public attributes
    import app.events.publishers.base as base_mod

    assert not hasattr(base_mod, "aio_pika")
    assert not hasattr(base_mod, "Message")
    assert not hasattr(base_mod, "Exchange")
    assert not hasattr(base_mod, "Channel")
    assert not hasattr(base_mod, "Connection")


def test_rabbitmq_publisher_conforms_to_protocol() -> None:
    """Verify RabbitMqEventPublisher implements provider-neutral EventPublisher."""
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(settings=settings)
    assert isinstance(publisher, EventPublisher)


def test_rabbitmq_publisher_has_zero_database_imports() -> None:
    """Verify RabbitMqEventPublisher module contains zero SQLAlchemy or outbox dependencies."""
    import app.events.publishers.rabbitmq as rmq_mod

    mod_dict = rmq_mod.__dict__
    forbidden_terms = {
        "sqlalchemy",
        "AsyncSession",
        "OutboxRepository",
        "OutboxEventRecord",
        "SqlAlchemyUnitOfWork",
    }
    assert forbidden_terms.isdisjoint(set(mod_dict.keys()))


# ==============================================================================
# 2. Fail-Closed Configuration & Secret Safety Tests (Task 1G.3A.1)
# ==============================================================================


def test_missing_rabbitmq_url_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify RabbitMqPublisherSettings fails validation when rabbitmq_url is omitted."""
    monkeypatch.delenv("E7_RABBITMQ_URL", raising=False)
    monkeypatch.delenv("E7_RABBITMQ_EXCHANGE_NAME", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        RabbitMqPublisherSettings(rabbitmq_exchange_name="test.events")

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("rabbitmq_url",) and e["type"] == "missing" for e in errors)


def test_missing_exchange_name_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify RabbitMqPublisherSettings fails validation when exchange name is omitted."""
    monkeypatch.delenv("E7_RABBITMQ_URL", raising=False)
    monkeypatch.delenv("E7_RABBITMQ_EXCHANGE_NAME", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        RabbitMqPublisherSettings(rabbitmq_url=SecretStr("amqp://host:5672/"))

    errors = exc_info.value.errors()
    assert any(e["loc"] == ("rabbitmq_exchange_name",) and e["type"] == "missing" for e in errors)


def test_no_guest_localhost_fallback_when_environment_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify no localhost/guest fallback exists when environment variables are omitted."""
    monkeypatch.delenv("E7_RABBITMQ_URL", raising=False)
    monkeypatch.delenv("E7_RABBITMQ_EXCHANGE_NAME", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        RabbitMqPublisherSettings()

    errors = exc_info.value.errors()
    field_names = {e["loc"][0] for e in errors}
    assert "rabbitmq_url" in field_names
    assert "rabbitmq_exchange_name" in field_names


def test_explicit_synthetic_settings_succeeds() -> None:
    """Verify explicit valid settings construction succeeds with expected values."""
    settings = RabbitMqPublisherSettings(
        rabbitmq_url=SecretStr("amqp://my-user:my-pass@my-broker:5672/my-vhost"),
        rabbitmq_exchange_name="ops.events",
        rabbitmq_connect_timeout_seconds=15.0,
    )
    assert (
        settings.rabbitmq_url.get_secret_value() == "amqp://my-user:my-pass@my-broker:5672/my-vhost"
    )
    assert settings.rabbitmq_exchange_name == "ops.events"
    assert settings.rabbitmq_connect_timeout_seconds == 15.0


def test_settings_loaded_from_e7_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify settings are cleanly loaded from E7_ prefixed environment variables."""
    monkeypatch.setenv("E7_RABBITMQ_URL", "amqps://env-user:env-pass@cloud-broker:5671/secure")
    monkeypatch.setenv("E7_RABBITMQ_EXCHANGE_NAME", "cloud.events")
    monkeypatch.setenv("E7_RABBITMQ_CONNECT_TIMEOUT_SECONDS", "25.0")

    settings = RabbitMqPublisherSettings()
    assert (
        settings.rabbitmq_url.get_secret_value()
        == "amqps://env-user:env-pass@cloud-broker:5671/secure"
    )
    assert settings.rabbitmq_exchange_name == "cloud.events"
    assert settings.rabbitmq_connect_timeout_seconds == 25.0


def test_settings_url_masked_in_repr_and_str() -> None:
    """Verify RabbitMQ URL containing sensitive credentials is masked in repr and str."""
    fake_secret = "super_secret_password_123"
    settings = RabbitMqPublisherSettings(
        rabbitmq_url=SecretStr(f"amqp://app_user:{fake_secret}@rabbitmq.internal:5672//"),
        rabbitmq_exchange_name="custom.events",
    )

    repr_str = repr(settings)
    str_str = str(settings)

    assert fake_secret not in repr_str
    assert "**********" in repr_str
    assert fake_secret not in str_str
    assert "**********" in str_str


def test_settings_validation_rejects_empty_exchange_and_invalid_scheme() -> None:
    """Verify settings validation enforces non-empty exchange and amqp/amqps schemes."""
    valid_url = SecretStr("amqp://localhost:5672/")
    with pytest.raises(ValueError, match="rabbitmq_exchange_name must not be empty"):
        RabbitMqPublisherSettings(rabbitmq_url=valid_url, rabbitmq_exchange_name="   ")

    with pytest.raises(ValueError, match="rabbitmq_url must start with 'amqp://' or 'amqps://'"):
        RabbitMqPublisherSettings(
            rabbitmq_url=SecretStr("http://localhost:5672/"),
            rabbitmq_exchange_name="test.events",
        )


def test_timeout_validation_positive_and_bounded() -> None:
    """Verify timeout validation accepts positive values and rejects zero and negative."""
    valid_url = SecretStr("amqp://localhost:5672/")
    valid_exchange = "test.events"

    # Positive accepted
    s = RabbitMqPublisherSettings(
        rabbitmq_url=valid_url,
        rabbitmq_exchange_name=valid_exchange,
        rabbitmq_connect_timeout_seconds=0.1,
    )
    assert s.rabbitmq_connect_timeout_seconds == 0.1

    # Zero rejected (gt=0.0)
    with pytest.raises(ValidationError):
        RabbitMqPublisherSettings(
            rabbitmq_url=valid_url,
            rabbitmq_exchange_name=valid_exchange,
            rabbitmq_connect_timeout_seconds=0.0,
        )

    # Negative rejected (gt=0.0)
    with pytest.raises(ValidationError):
        RabbitMqPublisherSettings(
            rabbitmq_url=valid_url,
            rabbitmq_exchange_name=valid_exchange,
            rabbitmq_connect_timeout_seconds=-5.0,
        )

    # Oversized rejected (le=300.0)
    with pytest.raises(ValidationError):
        RabbitMqPublisherSettings(
            rabbitmq_url=valid_url,
            rabbitmq_exchange_name=valid_exchange,
            rabbitmq_connect_timeout_seconds=300.1,
        )


def test_publisher_requires_explicit_settings_injection() -> None:
    """Verify RabbitMqEventPublisher requires settings and fails if omitted."""
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'settings'"):
        RabbitMqEventPublisher()  # type: ignore[call-arg]


# ==============================================================================
# 3. Import & Construction Zero Network I/O Tests
# ==============================================================================


def test_import_and_construction_performs_zero_network_calls() -> None:
    """Verify importing and constructing RabbitMqEventPublisher does not invoke connector."""
    mock_factory = AsyncMock()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    assert publisher.state == PublisherState.NEW
    assert mock_factory.call_count == 0


# ==============================================================================
# 4. Lifecycle & Start Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_start_creates_robust_connection_confirm_channel_and_topic_exchange() -> None:
    """Verify start() initializes confirm channel, on_return_raises, and durable topic exchange."""
    mock_exchange, mock_channel, mock_connection, mock_factory = _create_mock_broker()

    settings = RabbitMqPublisherSettings(
        rabbitmq_url=SecretStr("amqp://custom-user:custom-pass@custom-host:5672/"),
        rabbitmq_exchange_name="test.ops.events",
        rabbitmq_connect_timeout_seconds=5.0,
    )
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    await publisher.start()

    assert publisher.state == PublisherState.STARTED
    mock_factory.assert_awaited_once_with(
        settings.rabbitmq_url.get_secret_value(),
        5.0,
    )
    mock_connection.channel.assert_awaited_once_with(
        publisher_confirms=True,
        on_return_raises=True,
    )
    mock_channel.declare_exchange.assert_awaited_once_with(
        name="test.ops.events",
        type=aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    # Critical negative invariant: zero queue declarations in Task 1G.3A
    assert not hasattr(mock_channel, "declare_queue") or mock_channel.declare_queue.call_count == 0


@pytest.mark.asyncio
async def test_duplicate_start_is_idempotent() -> None:
    """Verify calling start() on an already STARTED publisher is a safe no-op."""
    _, _, _, mock_factory = _create_mock_broker()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    await publisher.start()
    assert publisher.state == PublisherState.STARTED
    assert mock_factory.call_count == 1

    # Second start must not re-open connection
    await publisher.start()
    assert publisher.state == PublisherState.STARTED
    assert mock_factory.call_count == 1


@pytest.mark.asyncio
async def test_start_after_close_raises_publisher_state_error() -> None:
    """Verify calling start() after close() raises PublisherStateError."""
    _, _, _, mock_factory = _create_mock_broker()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    await publisher.start()
    await publisher.close()
    assert publisher.state == PublisherState.CLOSED

    with pytest.raises(PublisherStateError, match="publisher is closed"):
        await publisher.start()


# ==============================================================================
# 5. Start Failure & Cleanup Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_start_connection_failure_cleans_up_and_masks_credentials() -> None:
    """Verify connection failure leaves state in NEW and raises safe PublisherConnectionError."""
    fake_password = "very_secret_amqp_pass"
    settings = RabbitMqPublisherSettings(
        rabbitmq_url=SecretStr(f"amqp://guest:{fake_password}@broker.invalid:5672/"),
        rabbitmq_exchange_name="test.events",
    )
    failing_factory = AsyncMock(side_effect=ConnectionError("Cannot connect to broker"))

    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=failing_factory,
    )

    with pytest.raises(PublisherConnectionError) as exc_info:
        await publisher.start()

    assert publisher.state == PublisherState.NEW
    error_str = str(exc_info.value)
    assert fake_password not in error_str
    assert "Failed to establish RabbitMQ publisher connection" in error_str


@pytest.mark.asyncio
async def test_start_channel_failure_cleans_up_connection() -> None:
    """Verify failure during channel creation closes the opened connection."""
    _, _, mock_connection, mock_factory = _create_mock_broker()
    mock_connection.channel.side_effect = RuntimeError("Channel creation failed")

    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    with pytest.raises(PublisherConnectionError):
        await publisher.start()

    assert publisher.state == PublisherState.NEW
    mock_connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_exchange_failure_cleans_up_channel_and_connection() -> None:
    """Verify failure during exchange declaration closes both channel and connection."""
    _, mock_channel, mock_connection, mock_factory = _create_mock_broker()
    mock_channel.declare_exchange.side_effect = RuntimeError("Exchange declaration failed")

    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    with pytest.raises(PublisherConnectionError):
        await publisher.start()

    assert publisher.state == PublisherState.NEW
    mock_channel.close.assert_awaited_once()
    mock_connection.close.assert_awaited_once()


# ==============================================================================
# 6. Close Lifecycle Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_close_cleans_up_resources_and_is_idempotent() -> None:
    """Verify close() closes channel and connection cleanly, and repeated close is safe."""
    _, mock_channel, mock_connection, mock_factory = _create_mock_broker()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    await publisher.start()
    assert publisher.state == PublisherState.STARTED

    await publisher.close()
    assert publisher.state == PublisherState.CLOSED
    mock_channel.close.assert_awaited_once()
    mock_connection.close.assert_awaited_once()

    # Repeated close call must be a safe no-op
    await publisher.close()
    assert publisher.state == PublisherState.CLOSED
    assert mock_channel.close.call_count == 1
    assert mock_connection.close.call_count == 1


@pytest.mark.asyncio
async def test_async_context_manager_lifecycle() -> None:
    """Verify async with publisher enters STARTED and exits CLOSED."""
    _, mock_channel, mock_connection, mock_factory = _create_mock_broker()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    async with publisher as pub:
        assert pub is publisher
        assert pub.state == PublisherState.STARTED

    assert publisher.state == PublisherState.CLOSED
    mock_channel.close.assert_awaited_once()
    mock_connection.close.assert_awaited_once()


# ==============================================================================
# 7. Publish Framing & Mandatory Routing Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_publish_frames_canonical_message_correctly() -> None:
    """Verify message framing: serialize_event_envelope body, persistent delivery, exact headers."""
    mock_exchange, _, _, mock_factory = _create_mock_broker()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )
    await publisher.start()

    envelope = _create_sample_envelope(
        event_type="workflow.task.created",
        correlation_id="corr-xyz-789",
    )

    await publisher.publish(envelope)

    mock_exchange.publish.assert_awaited_once()
    call_args, call_kwargs = mock_exchange.publish.call_args

    published_message: aio_pika.Message = call_kwargs["message"]
    published_routing_key: str = call_kwargs["routing_key"]
    mandatory_flag: bool = call_kwargs["mandatory"]

    # 1. Routing key strictly equals event_type
    assert published_routing_key == "workflow.task.created"
    assert mandatory_flag is True

    # 2. Body matches canonical serialize_event_envelope bytes
    expected_body = serialize_event_envelope(envelope).encode("utf-8")
    assert published_message.body == expected_body

    # 3. Message headers and metadata
    assert published_message.content_type == "application/json"
    assert published_message.content_encoding == "utf-8"
    assert published_message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert published_message.message_id == str(envelope.event_id)
    assert published_message.type == "workflow.task.created"
    assert published_message.correlation_id == "corr-xyz-789"
    assert published_message.timestamp == envelope.occurred_at


@pytest.mark.asyncio
async def test_publish_without_correlation_id_sets_none() -> None:
    """Verify publishing an envelope without correlation_id passes None cleanly."""
    mock_exchange, _, _, mock_factory = _create_mock_broker()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )
    await publisher.start()

    envelope = _create_sample_envelope(correlation_id=None)
    await publisher.publish(envelope)

    call_kwargs = mock_exchange.publish.call_args[1]
    published_message: aio_pika.Message = call_kwargs["message"]
    assert published_message.correlation_id is None


@pytest.mark.asyncio
async def test_producer_consumer_amqp_metadata_cross_contract() -> None:
    """Cross-contract audit: verify produced AMQP message metadata satisfies consumer acceptance.

    Verifies all four required AMQP metadata fields:
    1. content_type: "application/json"
    2. content_encoding: "utf-8"
    3. message_id: str(envelope.event_id)
    4. type: envelope.event_type
    And proves that feeding the published message into RabbitMqInboundMessageHandler
    results in successful wire validation and ACKED_PROCESSED disposition.
    """
    mock_exchange, _, _, mock_factory = _create_mock_broker()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )
    await publisher.start()

    envelope = _create_sample_envelope(
        event_type="workflow.task.dispatched",
        correlation_id="corr-crosscheck-1",
    )

    await publisher.publish(envelope)

    mock_exchange.publish.assert_awaited_once()
    published_message: aio_pika.Message = mock_exchange.publish.call_args[1]["message"]
    published_routing_key: str = mock_exchange.publish.call_args[1]["routing_key"]

    # 1. Producer AMQP metadata validation
    assert published_message.content_type == "application/json"
    assert published_message.content_encoding == "utf-8"
    assert published_message.message_id == str(envelope.event_id)
    assert published_message.type == envelope.event_type
    assert published_routing_key == envelope.event_type
    assert published_message.body == serialize_event_envelope(envelope).encode("utf-8")

    # 2. Inbound consumer acceptance validation
    incoming_msg = AsyncMock(spec=aio_pika.abc.AbstractIncomingMessage)
    incoming_msg.body = published_message.body
    incoming_msg.content_type = published_message.content_type
    incoming_msg.content_encoding = published_message.content_encoding
    incoming_msg.message_id = published_message.message_id
    incoming_msg.type = published_message.type
    incoming_msg.correlation_id = published_message.correlation_id

    class _MockProcessor:
        async def process(self, env: EventEnvelope) -> ConsumerProcessingResult:
            return ConsumerProcessingResult(
                consumer_name="crosscheck-consumer",
                outcome=ConsumerProcessingOutcome.PROCESSED,
                event_id=env.event_id,
            )

    handler = RabbitMqInboundMessageHandler(
        processor=_MockProcessor(),
        policy=RabbitMqInboundPolicy(max_message_bytes=65536),
    )

    result = await handler.handle(incoming_msg)
    assert result.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED
    assert result.event_id == envelope.event_id
    assert incoming_msg.ack.call_count == 1
    assert incoming_msg.reject.call_count == 0


# ==============================================================================
# 8. Publish Guard & Failure Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_publish_before_start_rejected() -> None:
    """Verify calling publish() when state is NEW raises PublisherNotStartedError."""
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(settings=settings)
    envelope = _create_sample_envelope()

    with pytest.raises(PublisherNotStartedError, match="Publisher has not been started"):
        await publisher.publish(envelope)


@pytest.mark.asyncio
async def test_publish_after_close_rejected() -> None:
    """Verify calling publish() when state is CLOSED raises PublisherNotStartedError."""
    _, _, _, mock_factory = _create_mock_broker()
    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )

    await publisher.start()
    await publisher.close()

    envelope = _create_sample_envelope()
    with pytest.raises(PublisherNotStartedError, match="Publisher is closed"):
        await publisher.publish(envelope)


@pytest.mark.asyncio
async def test_publish_broker_failure_raises_safe_event_publish_error() -> None:
    """Verify broker publish failure raises safe EventPublishError without altering envelope."""
    mock_exchange, _, _, mock_factory = _create_mock_broker()
    mock_exchange.publish.side_effect = RuntimeError("Broker connection dropped unexpectedly")

    settings = _create_sample_settings()
    publisher = RabbitMqEventPublisher(
        settings=settings,
        connection_factory=mock_factory,
    )
    await publisher.start()

    envelope = _create_sample_envelope(event_type="schedule.published")
    original_event_id = envelope.event_id

    match_pattern = "Failed to publish event 'schedule.published'"
    with pytest.raises(EventPublishError, match=match_pattern) as exc:
        await publisher.publish(envelope)

    # Envelope must not be mutated
    assert envelope.event_id == original_event_id
    # Underlying exception preserved via cause
    assert isinstance(exc.value.__cause__, RuntimeError)


# ==============================================================================
# 9. Architectural Invariant & Database Isolation Tests
# ==============================================================================


def test_base_metadata_remains_strictly_canonical_tables() -> None:
    """Verify Base.metadata contains canonical foundation tables."""
    phase1_tables = {
        "audit_events",
        "outbox_events",
        "inbox_messages",
        "idempotency_records",
        "jobs",
    }
    canonical_phase4b_tables = phase1_tables | {
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
    assert set(Base.metadata.tables.keys()) == canonical_phase4b_tables
    assert len(Base.metadata.tables) == 24


def test_deferred_runtime_integration_constant() -> None:
    """Verify runtime RabbitMQ verification is officially documented as deferred to Task 1J."""
    assert RABBITMQ_PUBLISH_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
