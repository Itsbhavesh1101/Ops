"""Canonical provider-neutral Data Transfer Objects (DTOs) for Calendar Integration.

Part of Phase 4B Generic Scheduling.
Enforces:
- Provider neutrality: Zero Google-specific fields (e.g. google_event_id, google_calendar_id).
- UTC normalized timestamps with explicit timezone metadata.
- Bounded input sizes and Defensive Pydantic configuration.
- Truthful synchronization status enumeration.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SyncStatus(StrEnum):
    """Truthful synchronization status classification for calendar mappings."""

    PENDING = "PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"
    CONFLICT = "CONFLICT"


class CalendarEventDTO(BaseModel):
    """Provider-neutral representation of a calendar event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_event_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Opaque external event identifier or mapping key",
    )
    external_calendar_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Provider-neutral external calendar identifier",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Event title or activity display label",
    )
    start_at: datetime = Field(
        ...,
        description="Event start instant (normalized to UTC or timezone-aware)",
    )
    end_at: datetime = Field(
        ...,
        description="Event end instant (normalized to UTC or timezone-aware)",
    )
    timezone: str = Field(
        default="UTC",
        max_length=64,
        description="IANA timezone identifier",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Event description or metadata summary",
    )
    location: str | None = Field(
        default=None,
        max_length=255,
        description="Physical or logical location reference",
    )
    schedule_id: UUID | None = Field(
        default=None,
        description="Internal canonical schedule reference",
    )
    assignment_id: UUID | None = Field(
        default=None,
        description="Internal canonical schedule assignment reference",
    )
    etag: str | None = Field(
        default=None,
        max_length=255,
        description="Entity tag / version signature for conditional updates",
    )
    sequence: int = Field(
        default=0,
        ge=0,
        description="RFC 5545 sequence revision number",
    )
    provider_version: int | None = Field(
        default=None,
        ge=0,
        description="Monotonically increasing provider version or sequence number",
    )
    provider_updated_at: datetime | None = Field(
        default=None,
        description="External provider timestamp indicating when the event was last updated",
    )

    @field_validator("external_calendar_id", "external_event_id")
    @classmethod
    def validate_non_empty_identifiers(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Identifier must not be empty or whitespace-only")
        return v.strip()

    @field_validator("start_at", "end_at")
    @classmethod
    def ensure_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


class CalendarEventResult(BaseModel):
    """Result of an outbound calendar provider operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_event_id: str
    status: SyncStatus
    etag: str | None = None
    synced_at: datetime | None = None
    error_message: str | None = None


class SyncChangesResult(BaseModel):
    """Result of an incremental provider synchronization query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    created_or_updated: list[CalendarEventDTO] = Field(default_factory=list)
    deleted_external_ids: list[str] = Field(default_factory=list)
    next_sync_token: str = Field(..., description="Opaque syncToken for next incremental query")


class ReconciliationResult(BaseModel):
    """Summary of a periodic reconciliation execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    created_count: int = Field(default=0, ge=0)
    updated_count: int = Field(default=0, ge=0)
    deleted_count: int = Field(default=0, ge=0)
    unchanged_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
