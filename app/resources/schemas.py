"""Canonical resource registry and availability interval schemas.

Part of Phase 4A Generic Scheduling Core.
Enforces:
- Canonical UUIDv4 identifiers.
- Generic resource types (PERSON, ROOM, EQUIPMENT, OTHER) without academic specialization.
- Timezone-aware half-open [start_at, end_at) intervals.
- Non-negative interval duration.
- Tenant isolation.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResourceType(StrEnum):
    """Generic E-7 resource categories."""

    PERSON = "PERSON"
    ROOM = "ROOM"
    EQUIPMENT = "EQUIPMENT"
    OTHER = "OTHER"


class IntervalType(StrEnum):
    """Availability interval semantics."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"  # Blackout window


class AvailabilityIntervalBase(BaseModel):
    """Timezone-aware half-open interval [start_at, end_at) for resource availability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_at: datetime = Field(..., description="Interval start timestamp (timezone-aware)")
    end_at: datetime = Field(..., description="Interval end timestamp (timezone-aware)")
    interval_type: IntervalType = Field(
        default=IntervalType.UNAVAILABLE,
        description="Whether this represents an available window or blackout window",
    )
    reason: str | None = Field(default=None, description="Optional explanation for blackout/shift")

    @field_validator("start_at", "end_at")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        """Enforce timezone-aware datetimes."""
        if v.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware.")
        return v

    @field_validator("end_at")
    @classmethod
    def validate_interval_order(cls, v: datetime, info: Any) -> datetime:
        """Enforce half-open duration >= 0."""
        start = info.data.get("start_at")
        if start is not None and v < start:
            raise ValueError(f"end_at ({v}) cannot be earlier than start_at ({start}).")
        return v


class AvailabilityIntervalCreate(AvailabilityIntervalBase):
    """Payload to record an availability or blackout interval."""

    resource_id: UUID = Field(..., description="Canonical resource UUID")


class AvailabilityIntervalRead(AvailabilityIntervalBase):
    """Read representation of a persisted availability interval."""

    id: UUID = Field(default_factory=uuid4, description="Interval record UUID")
    tenant_id: str = Field(..., description="Tenant scope")
    resource_id: UUID = Field(..., description="Canonical resource UUID")
    created_at: datetime = Field(..., description="Creation timestamp")


class ResourceBase(BaseModel):
    """Generic resource definition attributes."""

    model_config = ConfigDict(extra="forbid")

    resource_key: str = Field(..., min_length=1, max_length=128, description="Stable resource key")
    type: ResourceType = Field(..., description="Resource category")
    display_label: str = Field(..., min_length=1, max_length=255, description="Human-readable name")
    capacity: int = Field(default=1, ge=0, description="Available capacity (e.g. seats, units)")
    skills: list[str] = Field(
        default_factory=list, description="Capability tags or skill certifications"
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict, description="Structured attributes (metadata only)"
    )
    active: bool = Field(default=True, description="Whether resource is active for scheduling")


class ResourceCreate(ResourceBase):
    """Payload to register a new canonical resource."""

    pass


class ResourceRead(ResourceBase):
    """Read projection of a canonical resource."""

    id: UUID = Field(..., description="Canonical resource UUID")
    tenant_id: str = Field(..., description="Tenant scope")
    created_at: datetime = Field(..., description="Registration timestamp")
    updated_at: datetime = Field(..., description="Last modification timestamp")
