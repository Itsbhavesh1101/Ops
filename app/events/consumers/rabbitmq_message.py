"""RabbitMQ inbound message boundary and single-message disposition.

Part of Phase 1 Foundation Task 1G.4C1.
Implements the safe transport trust boundary for consuming exactly one RabbitMQ
IncomingMessage:
- Untrusted wire validation (size, content_type, encoding, strict UTF-8).
- Strict JSON parsing with duplicate-key rejection and non-object root rejection.
- Canonical EventEnvelope reconstruction using authoritative model validation.
- Message-ID and event-type broker metadata consistency checks.
- Dispatches validated EventEnvelope to ConsumerEventProcessor.
- Acknowledges message with broker (basic_ack) strictly after database commit.
- Rejects deterministic malformed wire messages without requeue (requeue=False).
- Leaves broker messages unsettled on processor exception/cancellation.
- Zero connection, channel, or queue topology lifecycle ownership.
"""

import json
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import aio_pika
import aio_pika.abc
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.events.inbox.processor import (
    ConsumerProcessingOutcome,
    ConsumerProcessingResult,
)
from app.events.schemas import EventEnvelope


class RabbitMqConsumerTransportError(Exception):
    """Base exception for RabbitMQ consumer transport errors."""


class RabbitMqMessageAckError(RabbitMqConsumerTransportError):
    """Raised when acknowledging an IncomingMessage with the broker fails."""


class RabbitMqMessageRejectError(RabbitMqConsumerTransportError):
    """Raised when rejecting an invalid IncomingMessage with the broker fails."""


class InvalidMessageReason(StrEnum):
    """Reason classifications for rejecting malformed incoming wire messages."""

    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    INVALID_CONTENT_TYPE = "INVALID_CONTENT_TYPE"
    INVALID_CONTENT_ENCODING = "INVALID_CONTENT_ENCODING"
    INVALID_UTF8 = "INVALID_UTF8"
    INVALID_JSON = "INVALID_JSON"
    DUPLICATE_JSON_KEY = "DUPLICATE_JSON_KEY"
    INVALID_JSON_ROOT = "INVALID_JSON_ROOT"
    INVALID_EVENT_ENVELOPE = "INVALID_EVENT_ENVELOPE"
    MISSING_MESSAGE_ID = "MISSING_MESSAGE_ID"
    MESSAGE_ID_MISMATCH = "MESSAGE_ID_MISMATCH"
    MISSING_EVENT_TYPE = "MISSING_EVENT_TYPE"
    EVENT_TYPE_MISMATCH = "EVENT_TYPE_MISMATCH"


class RabbitMqMessageHandlingOutcome(StrEnum):
    """Outcome classifications for RabbitMQ inbound message disposition."""

    ACKED_PROCESSED = "ACKED_PROCESSED"
    ACKED_DUPLICATE = "ACKED_DUPLICATE"
    REJECTED_INVALID_MESSAGE = "REJECTED_INVALID_MESSAGE"


class RabbitMqInboundPolicy(BaseModel):
    """Configuration policy bounding RabbitMQ incoming message body size.

    Invariants:
    - max_message_bytes is required and must be strictly positive.
    - Zero hidden defaults.
    - Immutable (frozen=True) and forbids extra fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_message_bytes: int = Field(
        ...,
        gt=0,
        description="Maximum allowed message body size in bytes.",
    )


class RabbitMqMessageHandlingResult(BaseModel):
    """Immutable result of processing a single incoming RabbitMQ message.

    Invariants:
    - For ACKED outcomes: event_id is required; invalid_reason must be None.
    - For REJECTED outcome: invalid_reason is required.
    - Contains zero payloads, raw body bytes, exception traces, or broker credentials.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RabbitMqMessageHandlingOutcome
    event_id: UUID | None = None
    invalid_reason: InvalidMessageReason | None = None

    @model_validator(mode="after")
    def _validate_consistency(self) -> "RabbitMqMessageHandlingResult":
        if self.outcome in (
            RabbitMqMessageHandlingOutcome.ACKED_PROCESSED,
            RabbitMqMessageHandlingOutcome.ACKED_DUPLICATE,
        ):
            if self.event_id is None:
                raise ValueError(f"event_id is required for outcome '{self.outcome}'")
            if self.invalid_reason is not None:
                raise ValueError(f"invalid_reason must be None for outcome '{self.outcome}'")
        elif self.outcome == RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE:
            if self.invalid_reason is None:
                raise ValueError(
                    "invalid_reason is required for outcome 'REJECTED_INVALID_MESSAGE'"
                )
        return self


@runtime_checkable
class ConsumerProcessor(Protocol):
    """Protocol for consumer event processing coordination."""

    async def process(self, envelope: EventEnvelope) -> ConsumerProcessingResult:
        """Process an EventEnvelope and return a ConsumerProcessingResult."""
        ...


class DuplicateKeyError(ValueError):
    """Raised when duplicate keys are encountered while parsing a JSON object."""


class InvalidJsonConstantError(ValueError):
    """Raised when non-standard JSON constants (NaN, Infinity, -Infinity) are encountered."""


def _reject_json_constant(val: str) -> Any:
    """Reject non-standard JSON numeric constants (NaN, Infinity, -Infinity)."""
    raise InvalidJsonConstantError(f"Non-standard JSON constant '{val}' is forbidden")


def _strict_object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Parse JSON object pairs enforcing strict key uniqueness."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"Duplicate key '{key}' in JSON object")
        result[key] = value
    return result


class RabbitMqInboundMessageHandler:
    """RabbitMQ inbound message adapter for safe wire validation and single-message disposition.

    Guarantees:
    - Transport boundary only: processes exactly one IncomingMessage.
    - Zero RabbitMQ connection, channel, or topology ownership.
    - Rejects invalid wire format without requeue (requeue=False).
    - Reconstructs canonical EventEnvelope using authoritative model validation.
    - Verifies message_id and event_type broker metadata consistency.
    - Invokes ConsumerEventProcessor only after wire validation succeeds.
    - Acknowledges message with broker (basic_ack) ONLY after successful processor return.
    - Leaves message unsettled upon processor exception or cancellation
      (never ACKs, NACKs, or rejects).
    """

    def __init__(
        self,
        *,
        processor: ConsumerProcessor,
        policy: RabbitMqInboundPolicy,
    ) -> None:
        """Initialize handler with processor and inbound policy.

        Args:
            processor: Implementation of ConsumerProcessor protocol (or ConsumerEventProcessor).
            policy: Configuration policy bounding message size.

        Raises:
            TypeError: If arguments do not conform to expected types.
        """
        if not isinstance(policy, RabbitMqInboundPolicy):
            raise TypeError(
                f"policy must be a RabbitMqInboundPolicy instance, got '{type(policy).__name__}'"
            )
        if not isinstance(processor, ConsumerProcessor):
            raise TypeError(
                f"processor must implement ConsumerProcessor protocol, "
                f"got '{type(processor).__name__}'"
            )
        self._processor = processor
        self._policy = policy

    @property
    def processor(self) -> ConsumerProcessor:
        """Return the bound consumer processor."""
        return self._processor

    @property
    def policy(self) -> RabbitMqInboundPolicy:
        """Return the bound inbound policy."""
        return self._policy

    async def _reject(
        self,
        message: aio_pika.IncomingMessage | aio_pika.abc.AbstractIncomingMessage,
    ) -> None:
        """Reject invalid message without requeue, wrapping broker errors safely."""
        try:
            await message.reject(requeue=False)
        except Exception as exc:
            raise RabbitMqMessageRejectError(
                "Failed to reject invalid message with RabbitMQ broker."
            ) from exc

    async def handle(
        self,
        message: aio_pika.IncomingMessage | aio_pika.abc.AbstractIncomingMessage,
    ) -> RabbitMqMessageHandlingResult:
        """Process an incoming RabbitMQ message through the wire validation boundary.

        Args:
            message: Untrusted incoming RabbitMQ message from transport.

        Returns:
            RabbitMqMessageHandlingResult with outcome ACKED_PROCESSED, ACKED_DUPLICATE,
            or REJECTED_INVALID_MESSAGE.

        Raises:
            TypeError: If message is not an aio_pika IncomingMessage instance.
            RabbitMqMessageAckError: If message.ack() fails.
            RabbitMqMessageRejectError: If message.reject() fails.
            Exception: Any exception raised by processor.process() propagates directly,
                leaving the message unsettled.
            asyncio.CancelledError: Propagates directly without disposition.
        """
        if not isinstance(
            message, (aio_pika.IncomingMessage, aio_pika.abc.AbstractIncomingMessage)
        ):
            raise TypeError(
                f"message must be an IncomingMessage instance, got '{type(message).__name__}'"
            )

        # 1. Body size check (before decode or JSON parse)
        if len(message.body) > self._policy.max_message_bytes:
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=None,
                invalid_reason=InvalidMessageReason.BODY_TOO_LARGE,
            )

        # 2. Content type check (canonical: application/json)
        content_type = message.content_type
        if not content_type or content_type.strip().lower() != "application/json":
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=None,
                invalid_reason=InvalidMessageReason.INVALID_CONTENT_TYPE,
            )

        # 3. Content encoding check (canonical: utf-8)
        content_encoding = message.content_encoding
        if not content_encoding or content_encoding.strip().lower() not in (
            "utf-8",
            "utf8",
        ):
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=None,
                invalid_reason=InvalidMessageReason.INVALID_CONTENT_ENCODING,
            )

        # 4. Strict UTF-8 decoding
        try:
            body_str = message.body.decode("utf-8")
        except UnicodeDecodeError:
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=None,
                invalid_reason=InvalidMessageReason.INVALID_UTF8,
            )

        # 5. Strict JSON parsing with duplicate-key, non-standard constant, and recursion rejection
        try:
            parsed_json = json.loads(
                body_str,
                object_pairs_hook=_strict_object_pairs_hook,
                parse_constant=_reject_json_constant,
            )
        except DuplicateKeyError:
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=None,
                invalid_reason=InvalidMessageReason.DUPLICATE_JSON_KEY,
            )
        except (
            json.JSONDecodeError,
            InvalidJsonConstantError,
            RecursionError,
            ValueError,
        ):
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=None,
                invalid_reason=InvalidMessageReason.INVALID_JSON,
            )

        # 6. JSON root check (must be a JSON object / dict)
        if not isinstance(parsed_json, dict):
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=None,
                invalid_reason=InvalidMessageReason.INVALID_JSON_ROOT,
            )

        # 7. Canonical EventEnvelope validation
        try:
            envelope = EventEnvelope.model_validate(parsed_json)
        except ValidationError:
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=None,
                invalid_reason=InvalidMessageReason.INVALID_EVENT_ENVELOPE,
            )

        # 8. Broker message_id integrity check
        if not message.message_id:
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=envelope.event_id,
                invalid_reason=InvalidMessageReason.MISSING_MESSAGE_ID,
            )
        if message.message_id != str(envelope.event_id):
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=envelope.event_id,
                invalid_reason=InvalidMessageReason.MESSAGE_ID_MISMATCH,
            )

        # 9. Broker event-type integrity check
        if not message.type:
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=envelope.event_id,
                invalid_reason=InvalidMessageReason.MISSING_EVENT_TYPE,
            )
        if message.type != envelope.event_type:
            await self._reject(message)
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.REJECTED_INVALID_MESSAGE,
                event_id=envelope.event_id,
                invalid_reason=InvalidMessageReason.EVENT_TYPE_MISMATCH,
            )

        # 10. Wire validation succeeded: dispatch to ConsumerEventProcessor
        # Processor exceptions or cancellation propagate directly; message remains unsettled.
        processor_result = await self._processor.process(envelope)

        # 11. Broker disposition based on processor outcome
        if processor_result.outcome == ConsumerProcessingOutcome.PROCESSED:
            try:
                await message.ack()
            except Exception as exc:
                raise RabbitMqMessageAckError(
                    "Failed to acknowledge processed message with RabbitMQ broker."
                ) from exc
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.ACKED_PROCESSED,
                event_id=envelope.event_id,
                invalid_reason=None,
            )

        if processor_result.outcome == ConsumerProcessingOutcome.DUPLICATE:
            try:
                await message.ack()
            except Exception as exc:
                raise RabbitMqMessageAckError(
                    "Failed to acknowledge duplicate message with RabbitMQ broker."
                ) from exc
            return RabbitMqMessageHandlingResult(
                outcome=RabbitMqMessageHandlingOutcome.ACKED_DUPLICATE,
                event_id=envelope.event_id,
                invalid_reason=None,
            )

        raise RuntimeError(
            f"Unexpected processor outcome '{processor_result.outcome}'. Message remains unsettled."
        )
