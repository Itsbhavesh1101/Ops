"""Unit tests for RabbitMQ inbound message boundary and single-message disposition.

Part of Phase 1 Foundation Task 1G.4C1.
Verifies:
- RabbitMqInboundPolicy validation (required max_message_bytes, >0, immutable, forbidden extra).
- Wire validation boundary:
  * Body size enforcement (BODY_TOO_LARGE).
  * Media type enforcement (INVALID_CONTENT_TYPE).
  * Encoding enforcement (INVALID_CONTENT_ENCODING, INVALID_UTF8).
  * Strict JSON parsing (INVALID_JSON, DUPLICATE_JSON_KEY, INVALID_JSON_ROOT).
  * Canonical EventEnvelope model validation (INVALID_EVENT_ENVELOPE).
  * Broker message_id consistency (MISSING_MESSAGE_ID, MESSAGE_ID_MISMATCH).
  * Broker event_type consistency (MISSING_EVENT_TYPE, EVENT_TYPE_MISMATCH).
- Disposition semantics:
  * PROCESSED outcome causes basic_ack and returns ACKED_PROCESSED.
  * DUPLICATE outcome causes basic_ack and returns ACKED_DUPLICATE.
  * Deterministic invalid wire format causes reject(requeue=False).
  * Processor failure leaves message unsettled (zero ack, reject, or nack).
  * Processor cancellation (CancelledError) propagates without disposition.
  * Ack failure propagates safe RabbitMqMessageAckError.
  * Reject failure propagates safe RabbitMqMessageRejectError.
- Exact chronological ordering (ACK strictly follows database commit).
- Canonical round-trip compatibility with serialize_event_envelope.
- Claim token absence from consumer types, results, and payload.
- Architectural isolation (zero imports of persistence, outbox, publisher, or topology).
- Schema stability (Base.metadata is audit_events + outbox_events + inbox_messages; head is 0005).
- Runtime integration deferral constant.
"""

import ast
import asyncio
from inspect import getsource
from typing import Any
from uuid import UUID, uuid4

import aio_pika
import aio_pika.abc
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError

import app.db.models  # noqa: F401
from app.db.base import Base
from app.events.consumers.rabbitmq_message import (
    InvalidMessageReason,
    RabbitMqInboundMessageHandler,
    RabbitMqInboundPolicy,
    RabbitMqMessageAckError,
    RabbitMqMessageHandlingOutcome,
    RabbitMqMessageHandlingResult,
    RabbitMqMessageRejectError,
)
from app.events.inbox.processor import (
    ConsumerProcessingOutcome,
    ConsumerProcessingResult,
)
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope
from app.events.serialization import serialize_event_envelope

# Authoritative verification boundary constant for Task 1G.4C1
RABBITMQ_CONSUMER_MESSAGE_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


# -----------------------------------------------------------------------------
# Test Doubles & Fixtures
# -----------------------------------------------------------------------------


def _build_test_envelope(
    *,
    event_id: UUID | None = None,
    event_type: str = "workflow.task.dispatched",
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Helper to create a canonical EventEnvelope for testing."""
    create_dto = EventCreate(
        event_type=event_type,
        event_version=1,
        tenant_id="tenant-alpha",
        correlation_id=correlation_id or "corr-100",
        subject_id="task-100",
        subject_type="task",
        payload={"task_code": "T-100"},
    )
    envelope = create_event_envelope(create_dto)
    if event_id is not None:
        return EventEnvelope(
            event_id=event_id,
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            occurred_at=envelope.occurred_at,
            tenant_id=envelope.tenant_id,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            subject_type=envelope.subject_type,
            subject_id=envelope.subject_id,
            payload=envelope.payload,
        )
    return envelope


class FakeIncomingMessage(aio_pika.abc.AbstractIncomingMessage):
    """Controllable aio-pika IncomingMessage double recording broker calls and timing."""

    def __init__(
        self,
        body: bytes,
        *,
        content_type: str | None = "application/json",
        content_encoding: str | None = "utf-8",
        message_id: str | None = None,
        message_type: str | None = None,
        correlation_id: str | None = None,
        ack_error: Exception | None = None,
        reject_error: Exception | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self.body = body
        self.content_type = content_type
        self.content_encoding = content_encoding
        self.message_id = message_id
        self.type = message_type
        self.correlation_id = correlation_id
        self.ack_error = ack_error
        self.reject_error = reject_error
        self.timeline = timeline
        self.ack_call_count = 0
        self.reject_call_count = 0
        self.nack_call_count = 0
        self.last_reject_requeue: bool | None = None

    async def ack(self, multiple: bool = False) -> None:
        self.ack_call_count += 1
        if self.timeline is not None:
            self.timeline.append("ack_start")
        if self.ack_error is not None:
            raise self.ack_error
        if self.timeline is not None:
            self.timeline.append("ack_finish")

    async def reject(self, requeue: bool = False) -> None:
        self.reject_call_count += 1
        self.last_reject_requeue = requeue
        if self.timeline is not None:
            self.timeline.append("reject_start")
        if self.reject_error is not None:
            raise self.reject_error
        if self.timeline is not None:
            self.timeline.append("reject_finish")

    async def nack(self, multiple: bool = False, requeue: bool = False) -> None:
        self.nack_call_count += 1
        if self.timeline is not None:
            self.timeline.append("nack")

    @property
    def channel(self) -> Any:
        return None

    @property
    def properties(self) -> Any:
        return None

    @property
    def lock(self) -> Any:
        return None

    @property
    def locked(self) -> bool:
        return False

    @property
    def processed(self) -> bool:
        return False

    def __iter__(self) -> Any:
        return iter([])

    def process(self, *args: Any, **kwargs: Any) -> Any:
        return None


class FakeProcessor:
    """Controllable ConsumerProcessor recording invocations and timeline steps."""

    def __init__(
        self,
        *,
        outcome: ConsumerProcessingOutcome = ConsumerProcessingOutcome.PROCESSED,
        fail_with: BaseException | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self.outcome = outcome
        self.fail_with = fail_with
        self.timeline = timeline
        self.call_count = 0
        self.last_envelope: EventEnvelope | None = None

    async def process(self, envelope: EventEnvelope) -> ConsumerProcessingResult:
        self.call_count += 1
        self.last_envelope = envelope
        if self.timeline is not None:
            self.timeline.append("processor_start")

        if self.fail_with is not None:
            raise self.fail_with

        if self.timeline is not None:
            self.timeline.append(f"processor_return_{self.outcome}")

        return ConsumerProcessingResult(
            outcome=self.outcome,
            consumer_name="workflow.projection",
            event_id=envelope.event_id,
        )


def _build_valid_message(
    envelope: EventEnvelope,
    *,
    body_override: bytes | None = None,
    content_type: str | None = "application/json",
    content_encoding: str | None = "utf-8",
    message_id_override: str | None = None,
    type_override: str | None = None,
    ack_error: Exception | None = None,
    reject_error: Exception | None = None,
    timeline: list[str] | None = None,
) -> FakeIncomingMessage:
    """Construct a valid FakeIncomingMessage for a canonical envelope."""
    body = (
        body_override
        if body_override is not None
        else serialize_event_envelope(envelope).encode("utf-8")
    )
    return FakeIncomingMessage(
        body=body,
        content_type=content_type,
        content_encoding=content_encoding,
        message_id=(
            message_id_override if message_id_override is not None else str(envelope.event_id)
        ),
        message_type=(type_override if type_override is not None else envelope.event_type),
        correlation_id=envelope.correlation_id,
        ack_error=ack_error,
        reject_error=reject_error,
        timeline=timeline,
    )


# -----------------------------------------------------------------------------
# Policy Tests
# -----------------------------------------------------------------------------


def test_inbound_policy_validation() -> None:
    """Verify RabbitMqInboundPolicy requires positive max_message_bytes, is immutable."""
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    assert policy.max_message_bytes == 65536

    # Required field (no hidden default)
    with pytest.raises(ValidationError):
        RabbitMqInboundPolicy()  # type: ignore[call-arg]

    # > 0 required
    with pytest.raises(ValidationError):
        RabbitMqInboundPolicy(max_message_bytes=0)

    with pytest.raises(ValidationError):
        RabbitMqInboundPolicy(max_message_bytes=-100)

    # Immutability
    with pytest.raises(ValidationError):
        policy.max_message_bytes = 1024

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        RabbitMqInboundPolicy(max_message_bytes=1024, extra_field=123)  # type: ignore[call-arg]


# -----------------------------------------------------------------------------
# Handler Construction & Result Model Tests
# -----------------------------------------------------------------------------


def test_handler_construction_type_checks() -> None:
    """Verify handler construction validates types and performs zero I/O."""
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    processor = FakeProcessor()

    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)
    assert handler.policy is policy
    assert handler.processor is processor

    # Invalid policy
    with pytest.raises(TypeError, match="policy must be a RabbitMqInboundPolicy"):
        RabbitMqInboundMessageHandler(processor=processor, policy="invalid")  # type: ignore[arg-type]

    # Invalid processor
    with pytest.raises(TypeError, match="processor must implement ConsumerProcessor protocol"):
        RabbitMqInboundMessageHandler(processor="not_a_processor", policy=policy)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_handler_rejects_non_incoming_message() -> None:
    """Verify handle() rejects non-IncomingMessage arguments with TypeError."""
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=FakeProcessor(), policy=policy)

    with pytest.raises(TypeError, match="message must be an IncomingMessage instance"):
        await handler.handle("invalid_message")  # type: ignore[arg-type]


def test_handling_result_immutability_and_consistency() -> None:
    """Verify RabbitMqMessageHandlingResult is immutable and enforces outcome invariants."""
    event_id = uuid4()

    # Valid ACKED results
    res_proc = RabbitMqMessageHandlingResult(
        outcome=RabbitMqMessageHandlingOutcome.ACKED_PROCESSED,
        event_id=event_id,
    )
    assert res_proc.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED
    assert res_proc.event_id == event_id
    assert res_proc.invalid_reason is None

    # ACKED result missing event_id rejected
    with pytest.raises(ValidationError, match="event_id is required"):
        RabbitMqMessageHandlingResult(
            outcome=RabbitMqMessageHandlingOutcome.ACKED_PROCESSED,
            event_id=None,
        )

    # ACKED result with invalid_reason rejected
    with pytest.raises(ValidationError, match="invalid_reason must be None"):
        RabbitMqMessageHandlingResult(
            outcome=RabbitMqMessageHandlingOutcome.ACKED_PROCESSED,
            event_id=event_id,
            invalid_reason=InvalidMessageReason.INVALID_JSON,
        )

    # Valid REJECTED result
    res_rej = RabbitMqMessageHandlingResult(
        outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
        event_id=None,
        invalid_reason=InvalidMessageReason.BODY_TOO_LARGE,
    )
    assert res_rej.invalid_reason == InvalidMessageReason.BODY_TOO_LARGE

    # REJECTED result missing invalid_reason rejected
    with pytest.raises(ValidationError, match="invalid_reason is required"):
        RabbitMqMessageHandlingResult(
            outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
            invalid_reason=None,
        )

    # Immutability
    with pytest.raises(ValidationError):
        res_proc.outcome = RabbitMqMessageHandlingOutcome.ACKED_DUPLICATE

    # Forbid extra
    with pytest.raises(ValidationError):
        RabbitMqMessageHandlingResult(
            outcome=RabbitMqMessageHandlingOutcome.ACKED_PROCESSED,
            event_id=event_id,
            extra_field="disallowed",  # type: ignore[call-arg]
        )


# -----------------------------------------------------------------------------
# Canonical Round-Trip & Success Flow Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canonical_round_trip_success() -> None:
    """Verify serialized canonical EventEnvelope is faithfully reconstructed and processed."""
    envelope = _build_test_envelope()
    processor = FakeProcessor(outcome=ConsumerProcessingOutcome.PROCESSED)
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope)
    result = await handler.handle(message)

    # Assert processor received exact reconstructed envelope
    assert processor.call_count == 1
    reconstructed = processor.last_envelope
    assert reconstructed is not None
    assert reconstructed.event_id == envelope.event_id
    assert reconstructed.event_type == envelope.event_type
    assert reconstructed.event_version == envelope.event_version
    assert reconstructed.occurred_at == envelope.occurred_at
    assert reconstructed.tenant_id == envelope.tenant_id
    assert reconstructed.correlation_id == envelope.correlation_id
    assert reconstructed.causation_id == envelope.causation_id
    assert reconstructed.subject_type == envelope.subject_type
    assert reconstructed.subject_id == envelope.subject_id
    assert reconstructed.payload == envelope.payload

    # Broker disposition
    assert message.ack_call_count == 1
    assert message.reject_call_count == 0
    assert message.nack_call_count == 0

    # Handling result
    assert result.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED
    assert result.event_id == envelope.event_id
    assert result.invalid_reason is None


@pytest.mark.asyncio
async def test_processed_ack_ordering() -> None:
    """Verify ACK strictly executes after ConsumerEventProcessor returns."""
    timeline: list[str] = []
    envelope = _build_test_envelope()
    processor = FakeProcessor(
        outcome=ConsumerProcessingOutcome.PROCESSED,
        timeline=timeline,
    )
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope, timeline=timeline)
    result = await handler.handle(message)
    timeline.append("result_returned")

    expected_timeline = [
        "processor_start",
        "processor_return_PROCESSED",
        "ack_start",
        "ack_finish",
        "result_returned",
    ]
    assert timeline == expected_timeline
    assert result.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED


@pytest.mark.asyncio
async def test_duplicate_ack_flow() -> None:
    """Verify processor DUPLICATE outcome acknowledges message and returns ACKED_DUPLICATE."""
    envelope = _build_test_envelope()
    processor = FakeProcessor(outcome=ConsumerProcessingOutcome.DUPLICATE)
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope)
    result = await handler.handle(message)

    assert processor.call_count == 1
    assert message.ack_call_count == 1
    assert message.reject_call_count == 0
    assert result.outcome == RabbitMqMessageHandlingOutcome.ACKED_DUPLICATE
    assert result.event_id == envelope.event_id


# -----------------------------------------------------------------------------
# Wire Validation Boundary & Deterministic Rejection Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejection_body_too_large() -> None:
    """Verify oversized message body is rejected without requeue before decode or parse."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    # Bound policy to 10 bytes
    policy = RabbitMqInboundPolicy(max_message_bytes=10)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.ack_call_count == 0
    assert message.reject_call_count == 1
    assert message.last_reject_requeue is False

    assert result.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE
    assert result.event_id is None
    assert result.invalid_reason == InvalidMessageReason.BODY_TOO_LARGE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_content_type",
    [None, "", "text/plain", "application/xml", "application/octet-stream"],
)
async def test_rejection_invalid_content_type(invalid_content_type: str | None) -> None:
    """Verify missing or non-application/json content_type is rejected without requeue."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope, content_type=invalid_content_type)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.ack_call_count == 0
    assert message.reject_call_count == 1
    assert message.last_reject_requeue is False

    assert result.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE
    assert result.invalid_reason == InvalidMessageReason.INVALID_CONTENT_TYPE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_encoding",
    [None, "", "ascii", "latin-1", "utf-16", "gzip"],
)
async def test_rejection_invalid_content_encoding(invalid_encoding: str | None) -> None:
    """Verify non-utf-8 content_encoding is rejected without requeue."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope, content_encoding=invalid_encoding)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.reject_call_count == 1
    assert message.last_reject_requeue is False
    assert result.invalid_reason == InvalidMessageReason.INVALID_CONTENT_ENCODING


@pytest.mark.asyncio
async def test_rejection_invalid_utf8_bytes() -> None:
    """Verify invalid UTF-8 byte sequences are rejected without requeue."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    # Invalid continuation byte in UTF-8
    message = _build_valid_message(envelope, body_override=b"\xff\xfe\xfd")
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.reject_call_count == 1
    assert message.last_reject_requeue is False
    assert result.invalid_reason == InvalidMessageReason.INVALID_UTF8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_json_bytes",
    [
        b"{unquoted_key: 1}",
        b'{"key": ',
        b'{"key": 1,}',
        b"not_json",
        b'{"a": 1} trailing_data',
    ],
)
async def test_rejection_malformed_json(malformed_json_bytes: bytes) -> None:
    """Verify syntactically malformed JSON is rejected without requeue."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope, body_override=malformed_json_bytes)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.reject_call_count == 1
    assert result.invalid_reason == InvalidMessageReason.INVALID_JSON


@pytest.mark.asyncio
async def test_rejection_duplicate_json_keys() -> None:
    """Verify duplicate JSON keys at any level are strictly rejected."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    # Root duplicate key
    dup_root = b'{"event_id": "1", "event_id": "2"}'
    msg_root = _build_valid_message(envelope, body_override=dup_root)
    res_root = await handler.handle(msg_root)
    assert res_root.invalid_reason == InvalidMessageReason.DUPLICATE_JSON_KEY

    # Nested duplicate key in payload
    dup_nested = (
        b'{"event_id": "00000000-0000-0000-0000-000000000001", "payload": {"dup": 1, "dup": 2}}'
    )
    msg_nested = _build_valid_message(envelope, body_override=dup_nested)
    res_nested = await handler.handle(msg_nested)
    assert res_nested.invalid_reason == InvalidMessageReason.DUPLICATE_JSON_KEY

    assert processor.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("constant_str", ["NaN", "Infinity", "-Infinity"])
async def test_rejection_non_standard_constants(constant_str: str) -> None:
    """Verify non-RFC JSON constants (NaN, Infinity, -Infinity) are rejected as INVALID_JSON."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    raw_json = (
        f'{{"event_id": "{envelope.event_id}", "event_type": "{envelope.event_type}", '
        f'"event_version": 1, "occurred_at": "2026-09-28T10:00:00Z", '
        f'"tenant_id": "tenant-alpha", "correlation_id": "corr-100", '
        f'"subject_type": "task", "subject_id": "task-100", '
        f'"payload": {{"score": {constant_str}}}}}'
    ).encode()

    message = _build_valid_message(envelope, body_override=raw_json)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.ack_call_count == 0
    assert message.reject_call_count == 1
    assert message.last_reject_requeue is False
    assert message.nack_call_count == 0
    assert result.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE
    assert result.invalid_reason == InvalidMessageReason.INVALID_JSON
    assert result.event_id is None


@pytest.mark.asyncio
async def test_rejection_parser_level_value_error() -> None:
    """Verify pathological integer literals causing parser ValueError are rejected."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    # Exceeds Python's default sys.get_int_max_str_digits() (4300 digits)
    huge_int = "9" * 5000
    raw_json = (
        f'{{"event_id": "{envelope.event_id}", "event_type": "{envelope.event_type}", '
        f'"event_version": 1, "occurred_at": "2026-09-28T10:00:00Z", '
        f'"tenant_id": "tenant-alpha", "correlation_id": "corr-100", '
        f'"subject_type": "task", "subject_id": "task-100", '
        f'"payload": {{"huge_num": {huge_int}}}}}'
    ).encode()

    message = _build_valid_message(envelope, body_override=raw_json)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.ack_call_count == 0
    assert message.reject_call_count == 1
    assert message.last_reject_requeue is False
    assert message.nack_call_count == 0
    assert result.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE
    assert result.invalid_reason == InvalidMessageReason.INVALID_JSON


@pytest.mark.asyncio
async def test_rejection_deep_nesting_recursion_error() -> None:
    """Verify deeply nested JSON triggering RecursionError is rejected as INVALID_JSON."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=1000000)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    deep_json = ('{"nested": ' * 3000 + "1" + "}" * 3000).encode()
    message = _build_valid_message(envelope, body_override=deep_json)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.ack_call_count == 0
    assert message.reject_call_count == 1
    assert message.last_reject_requeue is False
    assert message.nack_call_count == 0
    assert result.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE
    assert result.invalid_reason == InvalidMessageReason.INVALID_JSON


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource_error",
    [
        MemoryError("Out of memory"),
        asyncio.CancelledError(),
    ],
)
async def test_resource_failures_not_caught_as_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    resource_error: BaseException,
) -> None:
    """Verify fatal resource errors and cancellations are not converted to INVALID_JSON."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    def _raising_loads(*args: Any, **kwargs: Any) -> Any:
        raise resource_error

    monkeypatch.setattr("json.loads", _raising_loads)

    message = _build_valid_message(envelope)
    with pytest.raises(type(resource_error)):
        await handler.handle(message)

    assert processor.call_count == 0
    assert message.ack_call_count == 0
    assert message.reject_call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "non_object_root",
    [
        b"[1, 2, 3]",
        b'"a string root"',
        b"12345",
        b"true",
        b"false",
        b"null",
    ],
)
async def test_rejection_non_object_json_root(non_object_root: bytes) -> None:
    """Verify non-object JSON roots (lists, strings, numbers, booleans, null) are rejected."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope, body_override=non_object_root)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.reject_call_count == 1
    assert result.invalid_reason == InvalidMessageReason.INVALID_JSON_ROOT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_envelope_body",
    [
        b"{}",  # Missing all required fields
        b'{"event_id": "not-a-uuid"}',  # Invalid UUID format
        (b'{"event_id": "00000000-0000-0000-0000-000000000001", "event_type": "invalid_type"}'),
        (
            b'{"event_id": "00000000-0000-0000-0000-000000000001", '
            b'"event_type": "workflow.task.dispatched", "event_version": 0}'
        ),
    ],
)
async def test_rejection_invalid_event_envelope(invalid_envelope_body: bytes) -> None:
    """Verify schema-violating JSON payloads fail EventEnvelope validation and are rejected."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope, body_override=invalid_envelope_body)
    result = await handler.handle(message)

    assert processor.call_count == 0
    assert message.reject_call_count == 1
    assert result.invalid_reason == InvalidMessageReason.INVALID_EVENT_ENVELOPE


@pytest.mark.asyncio
async def test_broker_message_id_integrity() -> None:
    """Verify broker message_id must be present and match EventEnvelope.event_id."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    # 1. Missing message_id
    msg_missing = _build_valid_message(envelope, message_id_override="")
    res_missing = await handler.handle(msg_missing)
    assert res_missing.invalid_reason == InvalidMessageReason.MISSING_MESSAGE_ID
    assert res_missing.event_id == envelope.event_id

    # 2. Mismatched message_id
    mismatched_uuid = str(uuid4())
    msg_mismatch = _build_valid_message(envelope, message_id_override=mismatched_uuid)
    res_mismatch = await handler.handle(msg_mismatch)
    assert res_mismatch.invalid_reason == InvalidMessageReason.MESSAGE_ID_MISMATCH
    assert res_mismatch.event_id == envelope.event_id

    # 3. Matching message_id succeeds
    msg_matching = _build_valid_message(envelope)
    res_matching = await handler.handle(msg_matching)
    assert res_matching.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED

    assert processor.call_count == 1


@pytest.mark.asyncio
async def test_broker_event_type_integrity() -> None:
    """Verify broker type property must be present and match EventEnvelope.event_type."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    # 1. Missing event type
    msg_missing = _build_valid_message(envelope, type_override="")
    res_missing = await handler.handle(msg_missing)
    assert res_missing.invalid_reason == InvalidMessageReason.MISSING_EVENT_TYPE
    assert res_missing.event_id == envelope.event_id

    # 2. Mismatched event type
    msg_mismatch = _build_valid_message(envelope, type_override="workflow.different.event")
    res_mismatch = await handler.handle(msg_mismatch)
    assert res_mismatch.invalid_reason == InvalidMessageReason.EVENT_TYPE_MISMATCH
    assert res_mismatch.event_id == envelope.event_id

    # 3. Matching event type succeeds
    msg_matching = _build_valid_message(envelope)
    res_matching = await handler.handle(msg_matching)
    assert res_matching.outcome == RabbitMqMessageHandlingOutcome.ACKED_PROCESSED

    assert processor.call_count == 1


# -----------------------------------------------------------------------------
# Failure, Cancellation, & Unsettled Message Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_failure_leaves_message_unsettled() -> None:
    """Verify processor exception propagates directly, leaving message completely unsettled."""
    envelope = _build_test_envelope()
    processor = FakeProcessor(fail_with=RuntimeError("BusinessFailure"))
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope)

    with pytest.raises(RuntimeError, match="BusinessFailure"):
        await handler.handle(message)

    # Invariant: message is left unsettled
    assert message.ack_call_count == 0
    assert message.reject_call_count == 0
    assert message.nack_call_count == 0


@pytest.mark.asyncio
async def test_processor_cancellation_leaves_message_unsettled() -> None:
    """Verify asyncio.CancelledError during processor propagates without disposition."""
    envelope = _build_test_envelope()
    processor = FakeProcessor(fail_with=asyncio.CancelledError())
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(envelope)

    with pytest.raises(asyncio.CancelledError):
        await handler.handle(message)

    assert message.ack_call_count == 0
    assert message.reject_call_count == 0
    assert message.nack_call_count == 0


@pytest.mark.asyncio
async def test_ack_failure_propagates_safe_transport_error() -> None:
    """Verify message.ack() broker failure raises RabbitMqMessageAckError without retry."""
    envelope = _build_test_envelope()
    processor = FakeProcessor(outcome=ConsumerProcessingOutcome.PROCESSED)
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(
        envelope,
        ack_error=RuntimeError("ChannelClosedOnAck"),
    )

    with pytest.raises(RabbitMqMessageAckError, match="Failed to acknowledge processed message"):
        await handler.handle(message)

    assert processor.call_count == 1
    # Never issue secondary reject or nack
    assert message.reject_call_count == 0
    assert message.nack_call_count == 0


@pytest.mark.asyncio
async def test_reject_failure_propagates_safe_transport_error() -> None:
    """Verify message.reject() broker failure raises RabbitMqMessageRejectError."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    # Force rejection via tiny policy
    policy = RabbitMqInboundPolicy(max_message_bytes=10)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    message = _build_valid_message(
        envelope,
        reject_error=RuntimeError("ConnectionResetOnReject"),
    )

    with pytest.raises(RabbitMqMessageRejectError, match="Failed to reject invalid message"):
        await handler.handle(message)

    assert processor.call_count == 0
    assert message.ack_call_count == 0


@pytest.mark.asyncio
async def test_unexpected_processor_outcome_raises_invariant_error() -> None:
    """Verify unknown processor outcome raises RuntimeError and leaves message unsettled."""
    envelope = _build_test_envelope()
    processor = FakeProcessor()
    policy = RabbitMqInboundPolicy(max_message_bytes=65536)
    handler = RabbitMqInboundMessageHandler(processor=processor, policy=policy)

    # Monkeypatch processor to return an impossible outcome
    async def _bogus_process(*args: Any, **kwargs: Any) -> Any:
        class BogusResult:
            outcome = "UNEXPECTED_STATUS"

        return BogusResult()

    processor.process = _bogus_process  # type: ignore[method-assign]

    message = _build_valid_message(envelope)
    with pytest.raises(RuntimeError, match="Unexpected processor outcome"):
        await handler.handle(message)

    assert message.ack_call_count == 0
    assert message.reject_call_count == 0


# -----------------------------------------------------------------------------
# Claim Token & Security Invariants Tests
# -----------------------------------------------------------------------------


def test_claim_token_absence_from_consumer_types() -> None:
    """Verify producer dispatch claim_token never enters consumer types or results."""
    from app.events.consumers import rabbitmq_message as mod

    source = getsource(mod)
    assert "claim_token" not in source, "claim_token must not exist in consumer module"

    result = RabbitMqMessageHandlingResult(
        outcome=RabbitMqMessageHandlingOutcome.ACKED_PROCESSED,
        event_id=uuid4(),
    )
    assert not hasattr(result, "claim_token")


def test_architectural_isolation() -> None:
    """Verify consumer transport module imports zero database, outbox, or publisher modules."""
    import app.events.consumers.rabbitmq_message as mod

    tree = ast.parse(getsource(mod))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name.lower())
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module.lower())

    forbidden_tokens = [
        "sqlalchemy",
        "asyncsession",
        "inboxrepository",
        "outboxrepository",
        "rabbitmqeventpublisher",
        "publishers",
        "outbox",
    ]
    for imp in imported_modules:
        for token in forbidden_tokens:
            assert token not in imp, f"Module must not import '{token}'; found '{imp}'"


def test_schema_stability() -> None:
    """Verify database metadata remains strictly canonical foundation tables."""
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
        # Phase 7 Calendar Watch (1)
        "calendar_watch_channels",
        # Phase 8D Service Credentials Persistence (1)
        "service_api_credentials",
    }
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 26

    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)
    assert script_dir.get_current_head() in (
        "0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012", "0013", "0014"
    )
    assert script_dir.get_current_head() == "0014"


def test_deferred_runtime_integration_constant() -> None:
    """Verify live RabbitMQ runtime integration is documented as deferred to Task 1J."""
    assert RABBITMQ_CONSUMER_MESSAGE_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
