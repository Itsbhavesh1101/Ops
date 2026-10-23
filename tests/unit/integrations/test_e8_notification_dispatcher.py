"""Unit and contract tests for E-8 Notification Boundary and Dispatcher.

Part of Phase 7D E-8 Production Notification Boundary.
Enforces:
- Outbound command formulation and HTTP dispatch.
- Duplicate command detection and idempotency.
- Timeout and temporary E-8 failure resilience.
- Audit emission on dispatch success and failure.
"""

from typing import Any

import aio_pika
import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.dispatcher import (
    AmqpNotificationDispatcher,
    HttpNotificationDispatcher,
    NotificationDispatchError,
    get_notification_dispatcher,
)
from app.integrations.engagement.models import (
    NotificationCommand,
    NotificationTrigger,
    TriggerKind,
)


class MockE8Transport(httpx.AsyncBaseTransport):
    """Mock HTTP transport simulating E-8 Notification Gateway."""

    def __init__(self) -> None:
        self.received_commands: list[dict[str, Any]] = []
        self.command_ids_seen: set[str] = set()
        self.simulate_503_error: bool = False
        self.simulate_timeout: bool = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.simulate_timeout:
            raise httpx.TimeoutException("E-8 gateway connection timed out", request=request)

        if self.simulate_503_error:
            return httpx.Response(503, text="Service Temporarily Unavailable", request=request)

        import json

        body = json.loads(request.content.decode("utf-8"))
        cmd_id = request.headers.get("x-command-id") or body.get("command_id")

        if cmd_id in self.command_ids_seen:
            # 409 Conflict / duplicate command already processed
            return httpx.Response(409, json={"error": "Duplicate command_id"}, request=request)

        if cmd_id:
            self.command_ids_seen.add(cmd_id)
        self.received_commands.append(body)

        return httpx.Response(202, json={"status": "QUEUED", "command_id": cmd_id}, request=request)


@pytest.fixture
def mock_e8_transport() -> MockE8Transport:
    return MockE8Transport()


@pytest.fixture
def notification_settings() -> NotificationSettings:
    return NotificationSettings(
        enabled=True,
        transport="http",
        endpoint_url="https://e8-gateway.internal/v1/notifications/commands",
        api_key=SecretStr("e8_secret_api_key_123"),
        timeout_seconds=2.0,
    )


@pytest.fixture
def dispatcher(
    notification_settings: NotificationSettings, mock_e8_transport: MockE8Transport
) -> HttpNotificationDispatcher:
    client = httpx.AsyncClient(transport=mock_e8_transport)
    return HttpNotificationDispatcher(settings=notification_settings, http_client=client)


def create_test_command(command_id: str = "cmd-pub-101") -> NotificationCommand:
    return NotificationCommand(
        command_id=command_id,
        correlation_reference="corr-operation-42",
        causation_reference="evt-published-99",
        organization_scope_reference="tenant-alpha",
        trigger=NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="schedule.published",
            source_entity_reference="sch-101",
        ),
        recipient_references=["faculty-lead@school.edu"],
        notification_context={"schedule_id": "sch-101", "version": 1},
    )


# =====================================================================
# 1. Successful Outbound Dispatch Tests
# =====================================================================

@pytest.mark.asyncio
async def test_dispatch_success(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Dispatches command successfully to E-8 gateway."""
    cmd = create_test_command("cmd-pub-001")
    result = await dispatcher.dispatch(cmd)
    assert result is True
    assert len(mock_e8_transport.received_commands) == 1
    assert mock_e8_transport.received_commands[0]["command_id"] == "cmd-pub-001"


# =====================================================================
# 2. Idempotency & Duplicate Replay Tests
# =====================================================================

@pytest.mark.asyncio
async def test_dispatch_duplicate_command_returns_false(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Replaying an identical command_id returns False without error (idempotent no-op)."""
    cmd = create_test_command("cmd-pub-duplicate")

    # First dispatch: accepted
    res1 = await dispatcher.dispatch(cmd)
    assert res1 is True

    # Second dispatch with identical command_id: duplicate recognized
    res2 = await dispatcher.dispatch(cmd)
    assert res2 is False
    # Upstream received only 1 new command
    assert len(mock_e8_transport.received_commands) == 1


# =====================================================================
# 3. Resiliency: Timeout & Temporary Outage Tests
# =====================================================================

@pytest.mark.asyncio
async def test_dispatch_timeout_raises_error(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Network timeout raises NotificationDispatchError."""
    mock_e8_transport.simulate_timeout = True
    cmd = create_test_command("cmd-pub-timeout")
    with pytest.raises(NotificationDispatchError, match="timed out"):
        await dispatcher.dispatch(cmd)


@pytest.mark.asyncio
async def test_dispatch_server_error_raises_error(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """HTTP 503 Service Unavailable raises NotificationDispatchError."""
    mock_e8_transport.simulate_503_error = True
    cmd = create_test_command("cmd-pub-503")
    with pytest.raises(NotificationDispatchError, match="HTTP error 503"):
        await dispatcher.dispatch(cmd)


@pytest.mark.asyncio
async def test_missing_endpoint_configuration_fails_closed() -> None:
    """Unconfigured endpoint URL raises NotificationDispatchError."""
    settings = NotificationSettings(enabled=True, endpoint_url="")
    disp = HttpNotificationDispatcher(settings=settings)
    cmd = create_test_command("cmd-unconfigured")
    with pytest.raises(NotificationDispatchError, match="endpoint_url is not configured"):
        await disp.dispatch(cmd)


@pytest.mark.asyncio
async def test_dispatch_temporary_failure_and_retry_recovery(
    dispatcher: HttpNotificationDispatcher, mock_e8_transport: MockE8Transport
) -> None:
    """Simulates temporary 503 failure followed by successful retry."""
    cmd = create_test_command("cmd-retry-01")

    # First attempt: temporary outage
    mock_e8_transport.simulate_503_error = True
    with pytest.raises(NotificationDispatchError, match="HTTP error 503"):
        await dispatcher.dispatch(cmd)

    # Second attempt: recovered
    mock_e8_transport.simulate_503_error = False
    result = await dispatcher.dispatch(cmd)
    assert result is True
    assert len(mock_e8_transport.received_commands) == 1


# =====================================================================
# 4. AMQP Topic Dispatcher Tests (ADR-012 Default Transport)
# =====================================================================

class MockAmqpExchange:
    """Mock AMQP Exchange simulating RabbitMQ topic exchange."""

    def __init__(self, name: str = "e8.notifications") -> None:
        self.name = name
        self.published_messages: list[tuple[aio_pika.Message, str, bool]] = []
        self.simulate_publish_error: bool = False

    async def publish(
        self,
        message: aio_pika.Message,
        routing_key: str,
        mandatory: bool = True,
    ) -> None:
        if self.simulate_publish_error:
            raise RuntimeError("Broker connection reset during publish")
        self.published_messages.append((message, routing_key, mandatory))


@pytest.fixture
def mock_amqp_exchange() -> MockAmqpExchange:
    return MockAmqpExchange()


@pytest.fixture
def amqp_dispatcher(mock_amqp_exchange: MockAmqpExchange) -> AmqpNotificationDispatcher:
    settings = NotificationSettings(
        enabled=True,
        transport="broker",
        exchange="e8.notifications",
    )
    return AmqpNotificationDispatcher(
        settings=settings,
        exchange=mock_amqp_exchange,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_amqp_dispatch_success_with_canonical_routing_key(
    amqp_dispatcher: AmqpNotificationDispatcher,
    mock_amqp_exchange: MockAmqpExchange,
) -> None:
    """AMQP dispatcher publishes to exchange with {tenant_id}.notification.{kind} routing key."""
    cmd = create_test_command("cmd-amqp-001")
    result = await amqp_dispatcher.dispatch(cmd)

    assert result is True
    assert len(mock_amqp_exchange.published_messages) == 1

    msg, r_key, mandatory = mock_amqp_exchange.published_messages[0]
    # Routing key format: {tenant_id}.notification.{trigger_kind}
    assert r_key == "tenant-alpha.notification.schedule_published"
    assert mandatory is True
    assert msg.message_id == "cmd-amqp-001"
    assert msg.correlation_id == "corr-operation-42"
    assert msg.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert msg.content_type == "application/json"
    assert msg.headers["x-command-id"] == "cmd-amqp-001"
    assert msg.headers["x-tenant-id"] == "tenant-alpha"
    assert msg.headers["x-contract-version"] == "1.0.0"


@pytest.mark.asyncio
async def test_amqp_dispatch_at_least_once_republish_on_retry(
    amqp_dispatcher: AmqpNotificationDispatcher,
    mock_amqp_exchange: MockAmqpExchange,
) -> None:
    """AMQP dispatcher does not suppress retries in-memory, preserving outbox guarantees."""
    cmd = create_test_command("cmd-amqp-retry")

    first_res = await amqp_dispatcher.dispatch(cmd)
    assert first_res is True

    # Retry delivery (e.g. outbox settlement failure in previous attempt)
    second_res = await amqp_dispatcher.dispatch(cmd)
    assert second_res is True
    # Message is published twice; E-8 consumer deduplicates idempotently
    assert len(mock_amqp_exchange.published_messages) == 2


@pytest.mark.asyncio
async def test_amqp_dispatch_broker_failure_raises_dispatch_error(
    amqp_dispatcher: AmqpNotificationDispatcher,
    mock_amqp_exchange: MockAmqpExchange,
) -> None:
    """Broker publish failure raises NotificationDispatchError cleanly."""
    mock_amqp_exchange.simulate_publish_error = True
    cmd = create_test_command("cmd-amqp-fail")

    with pytest.raises(NotificationDispatchError, match="Failed to publish notification"):
        await amqp_dispatcher.dispatch(cmd)


@pytest.mark.asyncio
async def test_amqp_dispatch_missing_rabbitmq_url_fails_closed() -> None:
    """AMQP dispatcher with unconfigured rabbitmq_url raises NotificationDispatchError."""
    settings = NotificationSettings(enabled=True, transport="broker", rabbitmq_url=SecretStr(""))
    disp = AmqpNotificationDispatcher(settings=settings)
    cmd = create_test_command("cmd-amqp-no-url")

    with pytest.raises(NotificationDispatchError, match="rabbitmq_url is not configured"):
        await disp.dispatch(cmd)



# =====================================================================
# 5. Dispatcher Factory Tests
# =====================================================================

def test_get_notification_dispatcher_broker_mode() -> None:
    """Factory returns AmqpNotificationDispatcher when transport is 'broker'."""
    settings = NotificationSettings(transport="broker", exchange="e8.notifications")
    disp = get_notification_dispatcher(settings=settings)
    assert isinstance(disp, AmqpNotificationDispatcher)


def test_get_notification_dispatcher_http_mode() -> None:
    """Factory returns HttpNotificationDispatcher when transport is 'http'."""
    settings = NotificationSettings(
        transport="http",
        endpoint_url="https://e8-gateway.internal/v1/notifications",
    )
    disp = get_notification_dispatcher(settings=settings)
    assert isinstance(disp, HttpNotificationDispatcher)


def test_invalid_transport_fails_validation() -> None:
    """Configuring an invalid transport fails validation."""
    with pytest.raises(ValueError, match="Invalid E-7 notification transport"):
        NotificationSettings(transport="unsupported_protocol")


# =====================================================================
# 6. Contract Validation & Data Minimization Tests
# =====================================================================

def test_command_requires_mandatory_tenant_and_correlation() -> None:
    """NotificationCommand strictly requires tenant and correlation references."""
    with pytest.raises(ValidationError):
        NotificationCommand(  # type: ignore[call-arg]
            command_id="cmd-1",
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="schedule.published",
            ),
            recipient_references=["usr-1"],
            notification_context={},
        )


def test_command_requires_non_empty_recipients() -> None:
    """NotificationCommand rejects empty recipient_references list."""
    pattern = r"(at least 1 item|recipient_references must contain at least one)"
    with pytest.raises(ValueError, match=pattern):
        NotificationCommand(
            command_id="cmd-1",
            correlation_reference="corr-1",
            organization_scope_reference="tenant-1",
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="schedule.published",
            ),
            recipient_references=[],
            notification_context={},
        )


def test_trigger_provenance_validation() -> None:
    """Trigger validation enforces source_event_name for events and conditions."""
    # SOURCE_VERIFIED_EVENT without source_event_name fails
    msg_event = "SOURCE_VERIFIED_EVENT requires non-empty 'source_event_name'"
    with pytest.raises(ValueError, match=msg_event):
        NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
            source_event_name="",
        )

    # SOURCE_BACKED_BUSINESS_CONDITION without business_condition fails
    msg_cond = "SOURCE_BACKED_BUSINESS_CONDITION requires non-empty 'business_condition'"
    with pytest.raises(ValueError, match=msg_cond):
        NotificationTrigger(
            trigger_kind=TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION,
            business_condition="",
        )


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "password",
        "api_key",
        "secret_token",
        "private_key",
        "bearer_token",
        "salary",
        "bank_account",
        "student_ssn",
        "student_name",
        "health_record",
        "disciplinary_record",
        "mysql_id",
        "iredu_id",
        "upstream_pk",
    ],
)
def test_privacy_boundary_rejects_sensitive_data_in_context(forbidden_key: str) -> None:
    """NotificationCommand rejects credentials, PII, salaries, or raw MySQL PKs."""
    err_pattern = "notification_context contains prohibited sensitive/PII field"
    with pytest.raises(ValueError, match=err_pattern):
        NotificationCommand(
            command_id="cmd-privacy-test",
            correlation_reference="corr-101",
            organization_scope_reference="tenant-alpha",
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="schedule.published",
            ),
            recipient_references=["urn:e7:cohort:faculty"],
            notification_context={
                "schedule_id": "sch-01",
                forbidden_key: "leaked_sensitive_value",
            },
        )


# =====================================================================
# 7. Handlers & Default Opaque Recipient Tests
# =====================================================================

@pytest.mark.asyncio
async def test_schedule_published_handler_constructs_privacy_preserving_command() -> None:
    """SchedulePublishedNotificationHandler defaults to opaque URN and preserves tenant."""
    from unittest.mock import AsyncMock, MagicMock

    from app.events.schemas import EventCreate, create_event_envelope
    from app.integrations.engagement.handlers import SchedulePublishedNotificationHandler
    from app.integrations.engagement.mock import MockNotificationDispatcher

    mock_disp = MockNotificationDispatcher()
    handler = SchedulePublishedNotificationHandler(dispatcher=mock_disp)

    envelope = create_event_envelope(
        EventCreate(
            event_type="schedule.published",
            tenant_id="tenant-audit-99",
            correlation_id="corr-pub-99",
            payload={"schedule_id": "sch-fall-2026", "version": 2},
        )
    )

    session = AsyncMock()
    session.add = MagicMock()
    await handler.handle(envelope=envelope, session=session)

    assert len(mock_disp.received_commands) == 1
    cmd = mock_disp.received_commands[0]
    assert cmd.organization_scope_reference == "tenant-audit-99"
    assert cmd.command_id == f"notif-pub-{envelope.event_id}"
    assert cmd.recipient_references == ["urn:e7:cohort:faculty"]
    assert cmd.trigger.trigger_kind == TriggerKind.SOURCE_VERIFIED_EVENT
    assert cmd.trigger.source_event_name == "schedule.published"
    assert cmd.notification_context["schedule_id"] == "sch-fall-2026"


# =====================================================================
# 8. Non-Duplicate 409, Credential Masking, & Privacy Hardening Tests
# =====================================================================

@pytest.mark.asyncio
async def test_http_dispatch_non_duplicate_409_raises_error() -> None:
    """HTTP 409 Conflict without duplicate contract raises NotificationDispatchError."""
    class ConflictTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            err_body = {"error": "StateConflict: entity locked"}
            return httpx.Response(409, json=err_body, request=request)

    client = httpx.AsyncClient(transport=ConflictTransport())
    custom_disp = HttpNotificationDispatcher(
        settings=NotificationSettings(endpoint_url="https://e8-gateway.internal/v1/notifications"),
        http_client=client,
    )
    cmd = create_test_command("cmd-conflict-409")
    with pytest.raises(NotificationDispatchError, match="E-8 HTTP 409 Conflict"):
        await custom_disp.dispatch(cmd)


def test_sanitize_error_message_masks_credentials() -> None:
    """_sanitize_error_message cleanly masks passwords inside connection URLs."""
    from app.integrations.engagement.dispatcher import _sanitize_error_message

    raw = "Failed to connect to amqp://e7_admin:super_secret_pw123@rabbitmq.prod:5672//vhost"
    sanitized = _sanitize_error_message(raw)
    assert "super_secret_pw123" not in sanitized
    assert "e7_admin:***@rabbitmq.prod:5672" in sanitized


def test_privacy_boundary_rejects_unexpected_fields_in_template() -> None:
    """Strictly validates allowed fields for known canonical notification templates."""
    with pytest.raises(ValueError, match="unexpected field 'unauthorized_field'"):
        NotificationCommand(
            command_id="cmd-template-privacy",
            correlation_reference="corr-201",
            organization_scope_reference="tenant-alpha",
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="schedule.published",
            ),
            recipient_references=["urn:e7:cohort:faculty"],
            notification_context={
                "template_id": "EXAM_SCHEDULE_PUBLISHED",
                "schedule_id": "sch-01",
                "version": 1,
                "unauthorized_field": "leak",
            },
        )


def test_privacy_boundary_rejects_credentials_in_values() -> None:
    """Rejects bearer tokens, private keys, or API secrets inside context values."""
    with pytest.raises(ValueError, match="prohibited credential or secret value"):
        NotificationCommand(
            command_id="cmd-secret-privacy",
            correlation_reference="corr-202",
            organization_scope_reference="tenant-alpha",
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="schedule.published",
            ),
            recipient_references=["urn:e7:cohort:faculty"],
            notification_context={
                "schedule_id": "sch-01",
                "auth_note": "Bearer eyJhbGciOi...",
            },
        )


def test_privacy_boundary_rejects_plaintext_phone_numbers() -> None:
    """Rejects plaintext phone numbers in recipient references."""
    with pytest.raises(ValueError, match="Plaintext phone number"):
        NotificationCommand(
            command_id="cmd-phone-privacy",
            correlation_reference="corr-203",
            organization_scope_reference="tenant-alpha",
            trigger=NotificationTrigger(
                trigger_kind=TriggerKind.SOURCE_VERIFIED_EVENT,
                source_event_name="schedule.published",
            ),
            recipient_references=["+14155552671"],
            notification_context={"schedule_id": "sch-01"},
        )


def test_handlers_support_default_dispatcher_resolution() -> None:
    """Handlers default to get_notification_dispatcher() when dispatcher is not passed."""
    from app.integrations.engagement.dispatcher import NotificationDispatcherProtocol
    from app.integrations.engagement.handlers import SchedulePublishedNotificationHandler

    handler = SchedulePublishedNotificationHandler()
    assert isinstance(handler.dispatcher, NotificationDispatcherProtocol)


@pytest.mark.asyncio
async def test_notification_consumer_handler_routes_published_and_changed() -> None:
    """NotificationConsumerHandler routes published and changed events to handlers."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from app.events.schemas import EventEnvelope
    from app.integrations.engagement.handlers import NotificationConsumerHandler

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=True)

    handler = NotificationConsumerHandler(dispatcher=mock_dispatcher)
    mock_session = MagicMock()

    # 1. schedule.published
    envelope_pub = EventEnvelope(
        event_id=uuid4(),
        event_type="schedule.published",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-alpha",
        correlation_id="corr-1",
        payload={"schedule_id": "sch-101", "version": 1},
    )
    await handler.handle(envelope=envelope_pub, session=mock_session)
    assert mock_dispatcher.dispatch.call_count == 1
    call_cmd = mock_dispatcher.dispatch.call_args[1]["command"]
    assert call_cmd.trigger.source_event_name == "schedule.published"
    assert call_cmd.notification_context["schedule_id"] == "sch-101"

    # 2. schedule.changed
    envelope_chg = EventEnvelope(
        event_id=uuid4(),
        event_type="schedule.changed",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-alpha",
        correlation_id="corr-2",
        payload={
            "schedule_id": "sch-101",
            "old_version": 1,
            "new_version": 2,
            "repair_reason": "room.conflict",
        },
    )
    await handler.handle(envelope=envelope_chg, session=mock_session)
    assert mock_dispatcher.dispatch.call_count == 2
    call_cmd_chg = mock_dispatcher.dispatch.call_args[1]["command"]
    assert call_cmd_chg.trigger.source_event_name == "schedule.changed"
    assert call_cmd_chg.notification_context["new_version"] == 2

    # 3. Unrelated event is safely ignored
    envelope_unrelated = EventEnvelope(
        event_id=uuid4(),
        event_type="user.created",
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-alpha",
        payload={"user_id": "u-1"},
    )
    await handler.handle(envelope=envelope_unrelated, session=mock_session)
    assert mock_dispatcher.dispatch.call_count == 2  # unchanged


@pytest.mark.asyncio
async def test_create_notification_consumer_processor_deduplication() -> None:
    """ConsumerEventProcessor created via factory executes handler and enforces inbox dedupe."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from app.events.inbox.processor import ConsumerProcessingOutcome
    from app.events.schemas import EventEnvelope
    from app.integrations.engagement.handlers import create_notification_consumer_processor

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=True)

    recorded_ids: set[str] = set()

    class FakeSession:
        pass

    class FakeUnitOfWork:
        def __init__(self) -> None:
            self.session = FakeSession()
            self.committed = False
            self.rolled_back = False

        async def __aenter__(self) -> "FakeUnitOfWork":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            if exc_type is not None:
                self.rolled_back = True

        async def commit(self) -> None:
            self.committed = True

    class FakeInboxRepo:
        def __init__(self, session: Any) -> None:
            pass

        async def try_record(self, consumer_name: str, envelope: Any) -> bool:
            key = f"{consumer_name}:{envelope.event_id}"
            if key in recorded_ids:
                return False
            recorded_ids.add(key)
            return True

    from unittest.mock import patch
    with patch("app.events.inbox.processor.InboxRepository", FakeInboxRepo):
        processor = create_notification_consumer_processor(
            uow_factory=lambda: FakeUnitOfWork(),
            dispatcher=mock_dispatcher,
        )

        envelope = EventEnvelope(
            event_id=uuid4(),
            event_type="schedule.published",
            occurred_at=datetime.now(UTC),
            tenant_id="tenant-alpha",
            payload={"schedule_id": "sch-202", "version": 1},
        )

        # First delivery -> PROCESSED
        res1 = await processor.process(envelope)
        assert res1.outcome == ConsumerProcessingOutcome.PROCESSED
        assert mock_dispatcher.dispatch.call_count == 1

        # Duplicate delivery -> DUPLICATE (handler skipped)
        res2 = await processor.process(envelope)
        assert res2.outcome == ConsumerProcessingOutcome.DUPLICATE
        assert mock_dispatcher.dispatch.call_count == 1


@pytest.mark.asyncio
async def test_inbox_handler_failure_rolls_back_and_allows_recovery() -> None:
    """When E-8 dispatch fails, inbox marker is rolled back so retrying succeeds."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from app.events.inbox.processor import ConsumerProcessingOutcome
    from app.events.schemas import EventEnvelope
    from app.integrations.engagement.handlers import create_notification_consumer_processor

    mock_dispatcher = MagicMock()
    # First attempt fails with network error
    mock_dispatcher.dispatch = AsyncMock(side_effect=[
        NotificationDispatchError("E-8 gateway unavailable"),
        True,
    ])

    recorded_ids: set[str] = set()
    provisional_key: str | None = None

    class FakeUnitOfWork:
        def __init__(self) -> None:
            self.session = MagicMock()
            self.committed = False
            self.rolled_back = False

        async def __aenter__(self) -> "FakeUnitOfWork":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            nonlocal provisional_key
            if exc_type is not None:
                self.rolled_back = True
                # Rollback discards provisional inbox insertion
                if provisional_key in recorded_ids:
                    recorded_ids.remove(provisional_key)
                provisional_key = None

        async def commit(self) -> None:
            self.committed = True

    class FakeInboxRepo:
        def __init__(self, session: Any) -> None:
            pass

        async def try_record(self, consumer_name: str, envelope: Any) -> bool:
            nonlocal provisional_key
            key = f"{consumer_name}:{envelope.event_id}"
            if key in recorded_ids:
                return False
            recorded_ids.add(key)
            provisional_key = key
            return True

    from unittest.mock import patch
    with patch("app.events.inbox.processor.InboxRepository", FakeInboxRepo):
        processor = create_notification_consumer_processor(
            uow_factory=lambda: FakeUnitOfWork(),
            dispatcher=mock_dispatcher,
        )

        envelope = EventEnvelope(
            event_id=uuid4(),
            event_type="schedule.published",
            occurred_at=datetime.now(UTC),
            tenant_id="tenant-alpha",
            payload={"schedule_id": "sch-recovery", "version": 1},
        )

        # 1. First attempt fails: exception escapes, transaction rolled back
        with pytest.raises(NotificationDispatchError, match="E-8 gateway unavailable"):
            await processor.process(envelope)

        assert mock_dispatcher.dispatch.call_count == 1
        # Provisional key was removed on rollback
        assert len(recorded_ids) == 0

        # 2. Recovery attempt on redelivery: succeeds and commits
        res = await processor.process(envelope)
        assert res.outcome == ConsumerProcessingOutcome.PROCESSED
        assert mock_dispatcher.dispatch.call_count == 2
        assert len(recorded_ids) == 1


@pytest.mark.asyncio
async def test_http_409_fails_closed_on_unrecognized_response() -> None:
    """HTTP 409 without recognized contract duplicate structure raises NotificationDispatchError."""
    mock_transport = httpx.MockTransport(
        lambda req: httpx.Response(409, text="Generic conflict occurred in downstream service")
    )
    client = httpx.AsyncClient(transport=mock_transport)
    dispatcher = HttpNotificationDispatcher(
        settings=NotificationSettings(
            enabled=True,
            transport="http",
            endpoint_url="https://e8-gateway.internal/v1/notifications/commands",
        ),
        http_client=client,
    )
    cmd = create_test_command("cmd-409-fail-closed")

    with pytest.raises(NotificationDispatchError, match="E-8 HTTP 409 Conflict"):
        await dispatcher.dispatch(cmd)


@pytest.mark.asyncio
async def test_missing_e8_broker_url_fails_closed_without_fallback() -> None:
    """AmqpNotificationDispatcher.start() fails closed when dedicated rabbitmq_url is empty."""
    dispatcher = AmqpNotificationDispatcher(
        settings=NotificationSettings(
            enabled=True,
            transport="broker",
            rabbitmq_url=SecretStr(""),
        )
    )

    with pytest.raises(NotificationDispatchError, match="Dedicated E-8 broker URL must be"):
        await dispatcher.start()


@pytest.mark.asyncio
async def test_worker_runtime_registers_notification_consumer_when_enabled() -> None:
    """Production run_worker wires and manages NotificationWorker background tasks when enabled."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.worker import run_worker

    stop_event = asyncio.Event()
    stop_event.set()

    mock_db = MagicMock()
    mock_db.dispose = AsyncMock()
    mock_pub = MagicMock()

    started_tasks = []

    class FakeNotificationWorker:
        def __init__(self, db_manager: Any, settings: Any) -> None:
            self.db_manager = db_manager
            self.settings = settings

        def start_background_tasks(self, stop_ev: Any) -> list[asyncio.Task[None]]:
            async def noop_loop() -> None:
                pass
            t = asyncio.create_task(noop_loop())
            started_tasks.append(t)
            return [t]

    notif_settings = NotificationSettings(
        enabled=True,
        transport="http",
        endpoint_url="https://e8.internal/v1/notifications/commands",
    )

    class FakeRunner:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, stop_event: Any) -> None:
            pass

    await run_worker(
        stop_event=stop_event,
        notification_settings=notif_settings,
        notification_worker_factory=FakeNotificationWorker,
        database_manager_factory=lambda s: mock_db,
        uow_factory_provider=lambda sf: lambda: None,
        publisher_factory=lambda ps: mock_pub,
        runner_factory=FakeRunner,
    )

    assert len(started_tasks) == 1
    assert started_tasks[0].done()


@pytest.mark.asyncio
async def test_notification_worker_supervisory_loop_retries_on_recoverable_failure() -> None:
    """NotificationWorker.run() retries with bounded backoff upon recoverable runner error."""
    import asyncio
    from unittest.mock import MagicMock

    from app.integrations.engagement.worker import NotificationWorker

    mock_db = MagicMock()
    run_attempts = 0
    stop_event = asyncio.Event()

    class FlakyRunner:
        def __init__(self, should_fail: bool) -> None:
            self.should_fail = should_fail

        async def run(self, stop_event: asyncio.Event) -> None:
            nonlocal run_attempts
            run_attempts += 1
            if self.should_fail:
                raise NotificationDispatchError("Simulated E-8 connection refused")
            # Second attempt succeeds: signal stop
            stop_event.set()

    runners = [FlakyRunner(should_fail=True), FlakyRunner(should_fail=False)]

    def runner_factory() -> Any:
        return runners.pop(0)

    worker = NotificationWorker(
        db_manager=mock_db,
        consumer_runner_factory=runner_factory,
        retry_backoff_seconds=0.05,
    )

    await worker.run(stop_event=stop_event)

    assert run_attempts == 2
    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_notification_worker_exits_when_stop_event_set_during_backoff() -> None:
    """NotificationWorker breaks out of backoff cleanly when stop_event is signaled."""
    import asyncio
    from unittest.mock import MagicMock

    from app.integrations.engagement.worker import NotificationWorker

    mock_db = MagicMock()
    stop_event = asyncio.Event()

    class FailingRunner:
        async def run(self, stop_event: asyncio.Event) -> None:
            raise NotificationDispatchError("Immediate failure")

    worker = NotificationWorker(
        db_manager=mock_db,
        consumer_runner_factory=lambda: FailingRunner(),
        retry_backoff_seconds=1.0,
    )

    async def trigger_stop_later() -> None:
        await asyncio.sleep(0.05)
        stop_event.set()

    stop_task = asyncio.create_task(trigger_stop_later())
    await worker.run(stop_event=stop_event)
    await stop_task

    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_notification_worker_propagates_cancellation_cleanly() -> None:
    """NotificationWorker.run() immediately re-raises asyncio.CancelledError."""
    import asyncio
    from unittest.mock import MagicMock

    from app.integrations.engagement.worker import NotificationWorker

    mock_db = MagicMock()
    stop_event = asyncio.Event()

    class CancelledRunner:
        async def run(self, stop_event: asyncio.Event) -> None:
            raise asyncio.CancelledError()

    worker = NotificationWorker(
        db_manager=mock_db,
        consumer_runner_factory=lambda: CancelledRunner(),
        retry_backoff_seconds=0.01,
    )

    with pytest.raises(asyncio.CancelledError):
        await worker.run(stop_event=stop_event)


@pytest.mark.asyncio
async def test_notification_worker_missing_broker_url_fails_closed() -> None:
    """NotificationWorker._build_consumer_runner() raises ValueError if no broker URL configured."""
    import os
    from unittest.mock import MagicMock

    from app.integrations.engagement.worker import NotificationWorker

    mock_db = MagicMock()
    worker = NotificationWorker(db_manager=mock_db)

    old_e7 = os.environ.pop("E7_RABBITMQ_URL", None)
    old_raw = os.environ.pop("RABBITMQ_URL", None)
    try:
        with pytest.raises(ValueError, match="required for notification consumer runner"):
            worker._build_consumer_runner()
    finally:
        if old_e7:
            os.environ["E7_RABBITMQ_URL"] = old_e7
        if old_raw:
            os.environ["RABBITMQ_URL"] = old_raw


@pytest.mark.asyncio
async def test_rabbitmq_inbound_handler_rejects_malformed_wire_messages_to_dlx() -> None:
    """RabbitMqInboundMessageHandler rejects malformed wire messages with requeue=False."""
    from unittest.mock import AsyncMock, MagicMock

    from app.events.consumers.rabbitmq_message import (
        InvalidMessageReason,
        RabbitMqInboundMessageHandler,
        RabbitMqInboundPolicy,
        RabbitMqMessageHandlingOutcome,
    )

    mock_processor = MagicMock()
    mock_processor.process = AsyncMock()
    policy = RabbitMqInboundPolicy(max_message_bytes=100)
    handler = RabbitMqInboundMessageHandler(processor=mock_processor, policy=policy)

    # 1. Body too large
    big_msg = MagicMock(spec=aio_pika.IncomingMessage)
    big_msg.body = b"x" * 101
    big_msg.reject = AsyncMock()
    big_msg.ack = AsyncMock()

    res = await handler.handle(big_msg)
    assert res.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE
    assert res.invalid_reason == InvalidMessageReason.BODY_TOO_LARGE
    big_msg.reject.assert_awaited_once_with(requeue=False)
    big_msg.ack.assert_not_called()

    # 2. Invalid content type
    type_msg = MagicMock(spec=aio_pika.IncomingMessage)
    type_msg.body = b'{"valid": "json"}'
    type_msg.content_type = "text/plain"
    type_msg.reject = AsyncMock()
    type_msg.ack = AsyncMock()

    res2 = await handler.handle(type_msg)
    assert res2.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE
    assert res2.invalid_reason == InvalidMessageReason.INVALID_CONTENT_TYPE
    type_msg.reject.assert_awaited_once_with(requeue=False)
    type_msg.ack.assert_not_called()


@pytest.mark.asyncio
async def test_rabbitmq_inbound_handler_leaves_message_unsettled_on_processor_exception() -> None:
    """RabbitMqInboundMessageHandler propagates processor exception without ack, nack, or reject."""
    import json
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from app.events.consumers.rabbitmq_message import (
        RabbitMqInboundMessageHandler,
        RabbitMqInboundPolicy,
    )

    ev_id = uuid4()
    envelope_dict = {
        "event_id": str(ev_id),
        "event_type": "schedule.published",
        "event_version": 1,
        "occurred_at": "2026-10-23T12:00:00Z",
        "tenant_id": "tenant-001",
        "payload": {"schedule_id": "sch-01"},
    }
    raw_bytes = json.dumps(envelope_dict).encode("utf-8")

    msg = MagicMock(spec=aio_pika.IncomingMessage)
    msg.body = raw_bytes
    msg.content_type = "application/json"
    msg.content_encoding = "utf-8"
    msg.message_id = str(ev_id)
    msg.type = "schedule.published"
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()

    mock_processor = MagicMock()
    mock_processor.process = AsyncMock(
        side_effect=NotificationDispatchError("E-8 gateway unreachable")
    )

    policy = RabbitMqInboundPolicy(max_message_bytes=1024)
    handler = RabbitMqInboundMessageHandler(processor=mock_processor, policy=policy)

    with pytest.raises(NotificationDispatchError, match="E-8 gateway unreachable"):
        await handler.handle(msg)

    # Invariant: Message MUST remain unsettled (no ACK, no REJECT, no NACK)
    msg.ack.assert_not_called()
    msg.reject.assert_not_called()


@pytest.mark.asyncio
async def test_consumer_event_processor_rollback_and_subsequent_retry_success() -> None:
    """ConsumerEventProcessor rolls back provisional inbox on dispatch error for retry."""
    from datetime import UTC, datetime
    from unittest.mock import MagicMock
    from uuid import uuid4

    from app.events.inbox.processor import (
        ConsumerEventHandler,
        ConsumerEventProcessor,
        ConsumerProcessingOutcome,
    )
    from app.events.schemas import EventEnvelope

    ev_id = uuid4()
    envelope = EventEnvelope(
        event_id=ev_id,
        event_type="schedule.published",
        event_version=1,
        occurred_at=datetime.now(UTC),
        tenant_id="tenant-alpha",
        payload={"schedule_id": "sch-777", "version": 1},
    )

    inbox_store: set[tuple[str, Any]] = set()

    class FakeSession:
        def __init__(self) -> None:
            self._provisional: set[tuple[str, Any]] = set()

        async def execute(self, stmt: Any) -> Any:
            mock_res = MagicMock()
            key = ("e7.notifications.consumer", ev_id)
            if key in inbox_store:
                mock_res.scalar_one_or_none.return_value = None
            else:
                self._provisional.add(key)
                mock_res.scalar_one_or_none.return_value = ev_id
            return mock_res

    class FakeUnitOfWork:
        def __init__(self) -> None:
            self.session = FakeSession()
            self.committed = False
            self.rolled_back = False

        async def __aenter__(self) -> "FakeUnitOfWork":
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            if exc_type is not None:
                await self.rollback()

        async def commit(self) -> None:
            inbox_store.update(self.session._provisional)
            self.committed = True

        async def rollback(self) -> None:
            self.session._provisional.clear()
            self.rolled_back = True

    should_fail_dispatch = True

    class FlakyHandler(ConsumerEventHandler):
        async def handle(self, *, envelope: EventEnvelope, session: Any) -> None:
            if should_fail_dispatch:
                raise NotificationDispatchError("E-8 gateway timed out")

    handler = FlakyHandler()
    processor = ConsumerEventProcessor(
        consumer_name="e7.notifications.consumer",
        uow_factory=lambda: FakeUnitOfWork(),
        handler=handler,
    )

    # First attempt: dispatch fails -> rolls back provisional inbox record
    with pytest.raises(NotificationDispatchError, match="timed out"):
        await processor.process(envelope)

    assert ("e7.notifications.consumer", ev_id) not in inbox_store

    # Second attempt: dispatch succeeds -> commits inbox record
    should_fail_dispatch = False
    res2 = await processor.process(envelope)
    assert res2.outcome == ConsumerProcessingOutcome.PROCESSED
    assert ("e7.notifications.consumer", ev_id) in inbox_store

    # Third attempt (duplicate): handler skipped, returns DUPLICATE
    handler_called_on_duplicate = False

    class SpyHandler(ConsumerEventHandler):
        async def handle(self, *, envelope: EventEnvelope, session: Any) -> None:
            nonlocal handler_called_on_duplicate
            handler_called_on_duplicate = True

    processor2 = ConsumerEventProcessor(
        consumer_name="e7.notifications.consumer",
        uow_factory=lambda: FakeUnitOfWork(),
        handler=SpyHandler(),
    )
    res3 = await processor2.process(envelope)
    assert res3.outcome == ConsumerProcessingOutcome.DUPLICATE
    assert not handler_called_on_duplicate





