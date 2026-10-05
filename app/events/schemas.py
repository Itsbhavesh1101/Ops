"""Immutable Pydantic schemas and contracts for canonical event envelopes.

Part of Phase 1 Foundation Task 1G.1 / Task 1G.1A.
Defines:
- EventCreate: caller input contract (omits event_id and occurred_at).
- EventEnvelope: canonical provider-neutral envelope with E-7 generated ID and timestamp.
- create_event_envelope: factory function with injectable clock and payload isolation.
"""

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.events.validation import freeze_payload, thaw_payload, validate_event_payload

# Namespaced event type regex: at least two dot-separated lowercase segments
EVENT_TYPE_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# Canonical subject type regex: dot or underscore-separated lowercase identifier
SUBJECT_TYPE_REGEX: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def _default_clock() -> datetime:
    """Default clock returning current UTC datetime with timezone awareness."""
    return datetime.now(UTC)


class EventCreate(BaseModel):
    """Immutable input contract for creating a canonical event envelope.

    Invariants:
    - Immutable: frozen=True, extra='forbid'.
    - Recursively immutable payload: MappingProxyType and tuples.
    - Transport metadata (event_id, occurred_at) are intentionally omitted from caller input.
    - event_type must follow lowercase dot-separated namespaced syntax with >=2 segments.
    - event_version must be a positive integer >= 1.
    - subject_type and subject_id enforce a strict both-or-none pairing invariant.
    - payload must satisfy JSON-compatibility, defensive security limits, and recursive freeze.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: str
    event_version: int = Field(default=1, ge=1)

    tenant_id: str | None = None

    correlation_id: str | None = None
    causation_id: UUID | None = None

    subject_type: str | None = None
    subject_id: str | None = None

    payload: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        cleaned = v.strip() if isinstance(v, str) else ""
        if not cleaned:
            raise ValueError("event_type must not be empty or whitespace-only")
        if len(cleaned) > 128:
            raise ValueError("event_type must not exceed 128 characters")
        if not EVENT_TYPE_REGEX.match(cleaned):
            raise ValueError(
                f"event_type '{cleaned}' is invalid; must be lowercase dot-separated segments "
                "with at least two segments (e.g. 'task.blocked', 'workflow.task.created')"
            )
        return cleaned

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("tenant_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 255:
            raise ValueError("tenant_id must not exceed 255 characters")
        return cleaned

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("correlation_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 128:
            raise ValueError("correlation_id must not exceed 128 characters")
        return cleaned

    @field_validator("subject_type")
    @classmethod
    def validate_subject_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("subject_type must not be empty or whitespace-only if provided")
        if len(cleaned) > 128:
            raise ValueError("subject_type must not exceed 128 characters")
        if not SUBJECT_TYPE_REGEX.match(cleaned):
            raise ValueError(
                f"subject_type '{cleaned}' must be a lowercase dot/underscore identifier; "
                "URLs, SQL, and whitespace are prohibited"
            )
        return cleaned

    @field_validator("subject_id")
    @classmethod
    def validate_subject_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("subject_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 255:
            raise ValueError("subject_id must not exceed 255 characters")
        return cleaned

    @field_validator("payload")
    @classmethod
    def validate_payload_field(cls, v: Any) -> MappingProxyType[str, Any]:
        return validate_event_payload(v)

    @field_serializer("payload", mode="plain")
    def serialize_payload(self, v: Mapping[str, Any]) -> dict[str, Any]:
        return thaw_payload(v)

    @model_validator(mode="after")
    def validate_subject_pairing(self) -> "EventCreate":
        has_type = self.subject_type is not None
        has_id = self.subject_id is not None
        if has_type != has_id:
            raise ValueError("subject_type and subject_id must both be provided or both be None")
        return self


class EventEnvelope(BaseModel):
    """Canonical provider-neutral event envelope.

    Invariants:
    - Immutable: frozen=True, extra='forbid'.
    - Recursively immutable payload: MappingProxyType and tuples.
    - event_id: E-7 generated canonical UUID primary key.
    - occurred_at: timezone-aware UTC datetime.
    - Provider-neutral: zero transport-specific RabbitMQ details (exchange, queue, delivery_tag).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    event_type: str
    event_version: int = Field(default=1, ge=1)
    occurred_at: datetime

    tenant_id: str | None = None

    correlation_id: str | None = None
    causation_id: UUID | None = None

    subject_type: str | None = None
    subject_id: str | None = None

    payload: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        cleaned = v.strip() if isinstance(v, str) else ""
        if not cleaned:
            raise ValueError("event_type must not be empty or whitespace-only")
        if len(cleaned) > 128:
            raise ValueError("event_type must not exceed 128 characters")
        if not EVENT_TYPE_REGEX.match(cleaned):
            raise ValueError(
                f"event_type '{cleaned}' is invalid; must be lowercase dot-separated segments "
                "with at least two segments (e.g. 'task.blocked', 'workflow.task.created')"
            )
        return cleaned

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("occurred_at must be a timezone-aware datetime")
        return v.astimezone(UTC)

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("tenant_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 255:
            raise ValueError("tenant_id must not exceed 255 characters")
        return cleaned

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("correlation_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 128:
            raise ValueError("correlation_id must not exceed 128 characters")
        return cleaned

    @field_validator("subject_type")
    @classmethod
    def validate_subject_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("subject_type must not be empty or whitespace-only if provided")
        if len(cleaned) > 128:
            raise ValueError("subject_type must not exceed 128 characters")
        if not SUBJECT_TYPE_REGEX.match(cleaned):
            raise ValueError(
                f"subject_type '{cleaned}' must be a lowercase dot/underscore identifier; "
                "URLs, SQL, and whitespace are prohibited"
            )
        return cleaned

    @field_validator("subject_id")
    @classmethod
    def validate_subject_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("subject_id must not be empty or whitespace-only if provided")
        if len(cleaned) > 255:
            raise ValueError("subject_id must not exceed 255 characters")
        return cleaned

    @field_validator("payload")
    @classmethod
    def validate_payload_field(cls, v: Any) -> MappingProxyType[str, Any]:
        return validate_event_payload(v)

    @field_serializer("payload", mode="plain")
    def serialize_payload(self, v: Mapping[str, Any]) -> dict[str, Any]:
        return thaw_payload(v)

    @model_validator(mode="after")
    def validate_subject_pairing(self) -> "EventEnvelope":
        has_type = self.subject_type is not None
        has_id = self.subject_id is not None
        if has_type != has_id:
            raise ValueError("subject_type and subject_id must both be provided or both be None")
        return self


def create_event_envelope(
    event: EventCreate,
    *,
    clock: Callable[[], datetime] = _default_clock,
) -> EventEnvelope:
    """Factory constructing an immutable canonical EventEnvelope from an EventCreate input.

    Responsibilities:
    - Generates uuid4 event_id unconditionally.
    - Generates timezone-aware UTC occurred_at via injectable clock.
    - Defensively isolates payload via freeze_payload to guarantee zero caller mutation.
    - Returns immutable EventEnvelope instance with recursively frozen payload.
    """
    raw_time = clock()
    if raw_time.tzinfo is None or raw_time.tzinfo.utcoffset(raw_time) is None:
        raise ValueError("Clock must produce a timezone-aware datetime")
    occurred_at = raw_time.astimezone(UTC)

    # Defensively isolate payload by re-freezing fresh internal mappings and tuples
    isolated_payload = freeze_payload(event.payload)

    return EventEnvelope(
        event_id=uuid4(),
        event_type=event.event_type,
        event_version=event.event_version,
        occurred_at=occurred_at,
        tenant_id=event.tenant_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        subject_type=event.subject_type,
        subject_id=event.subject_id,
        payload=isolated_payload,
    )
