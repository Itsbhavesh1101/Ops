"""Canonical constraint catalog and typed constraint models.

Part of Phase 4A Generic Scheduling Core.
Enforces:
- Discriminative typed constraint hierarchy.
- Closed constraint vocabulary (NO_OVERLAP, CAPACITY, ELIGIBILITY, PRECEDENCE,
  TIME_WINDOW, MINIMUM_GAP, LOCK).
- HARD vs SOFT classification.
- Immutable, deterministic serialization without arbitrary Python eval.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConstraintHardness(StrEnum):
    """Classification of constraint enforcement."""

    HARD = "HARD"
    SOFT = "SOFT"


class ConstraintType(StrEnum):
    """Closed vocabulary of generic scheduling constraints."""

    NO_OVERLAP = "NO_OVERLAP"
    CAPACITY = "CAPACITY"
    ELIGIBILITY = "ELIGIBILITY"
    PRECEDENCE = "PRECEDENCE"
    TIME_WINDOW = "TIME_WINDOW"
    MINIMUM_GAP = "MINIMUM_GAP"
    LOCK = "LOCK"


class TimeWindow(BaseModel):
    """Timezone-aware half-open scheduling window [start_at, end_at)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_at: datetime = Field(..., description="Window start timestamp (timezone-aware)")
    end_at: datetime = Field(..., description="Window end timestamp (timezone-aware)")

    @field_validator("start_at", "end_at")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Time window timestamps must be timezone-aware.")
        return v

    @field_validator("end_at")
    @classmethod
    def validate_ordering(cls, v: datetime, info: Any) -> datetime:
        start = info.data.get("start_at")
        if start is not None and v < start:
            raise ValueError(f"end_at ({v}) cannot be before start_at ({start}).")
        return v


class BaseConstraint(BaseModel):
    """Base schema for all typed canonical constraints."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        default_factory=lambda: f"c-{uuid4().hex[:8]}",
        description="Canonical stable constraint ID",
    )
    name: str = Field(..., min_length=1, max_length=128, description="Human-readable title")
    hardness: ConstraintHardness = Field(
        default=ConstraintHardness.HARD, description="HARD (mandatory) or SOFT (objective)"
    )
    weight: int = Field(
        default=1, ge=1, description="Penalty weight when hardness is SOFT (ignored for HARD)"
    )
    description: str = Field(default="", description="Safe human-readable explanation")
    source_reference: str | None = Field(
        default=None, description="Policy, regulatory, or contract reference"
    )


class NoOverlapConstraint(BaseConstraint):
    """Ensures a resource is never double-booked across concurrent activities."""

    constraint_type: Literal[ConstraintType.NO_OVERLAP] = ConstraintType.NO_OVERLAP
    resource_ids: list[str] = Field(
        ..., min_length=1, description="Resource IDs requiring exclusive assignment"
    )
    activity_ids: list[str] | None = Field(
        default=None, description="Optional restricted activity scope; None means all activities"
    )


class CapacityConstraint(BaseConstraint):
    """Ensures simultaneous resource usage never exceeds maximum capacity."""

    constraint_type: Literal[ConstraintType.CAPACITY] = ConstraintType.CAPACITY
    resource_id: str = Field(..., description="Capacity-bearing resource ID")
    max_capacity: int = Field(..., ge=0, description="Upper bound capacity")
    demand_per_activity: dict[str, int] = Field(
        default_factory=dict, description="Capacity consumed per activity ID (defaults to 1)"
    )


class EligibilityConstraint(BaseConstraint):
    """Restricts eligible resources for an activity based on required skills or allowlists."""

    constraint_type: Literal[ConstraintType.ELIGIBILITY] = ConstraintType.ELIGIBILITY
    activity_id: str = Field(..., description="Target activity ID")
    required_skills: list[str] = Field(
        default_factory=list, description="Skills or capability tags required"
    )
    allowed_resource_ids: list[str] | None = Field(
        default=None, description="Explicit allowed resource whitelist"
    )


class PrecedenceConstraint(BaseConstraint):
    """Enforces that predecessor activity finishes before successor activity starts."""

    constraint_type: Literal[ConstraintType.PRECEDENCE] = ConstraintType.PRECEDENCE
    predecessor_id: str = Field(..., description="Activity that must finish first")
    successor_id: str = Field(..., description="Activity that must start after")
    min_gap_minutes: int = Field(
        default=0, ge=0, description="Minimum separation gap in minutes"
    )


class TimeWindowConstraint(BaseConstraint):
    """Enforces that activity start and finish fall strictly within allowed windows."""

    constraint_type: Literal[ConstraintType.TIME_WINDOW] = ConstraintType.TIME_WINDOW
    activity_id: str = Field(..., description="Target activity ID")
    allowed_windows: list[TimeWindow] = Field(
        ..., min_length=1, description="Set of valid half-open scheduling windows"
    )


class MinimumGapConstraint(BaseConstraint):
    """Enforces a minimum non-overlapping gap between any pair in a set of activities."""

    constraint_type: Literal[ConstraintType.MINIMUM_GAP] = ConstraintType.MINIMUM_GAP
    activity_ids: list[str] = Field(
        ..., min_length=2, description="Activities requiring mutual separation"
    )
    min_gap_minutes: int = Field(..., ge=1, description="Minimum gap duration in minutes")


class LockConstraint(BaseConstraint):
    """Locks an activity's time window, assigned resources, or both."""

    constraint_type: Literal[ConstraintType.LOCK] = ConstraintType.LOCK
    activity_id: str = Field(..., description="Activity ID to lock")
    locked_start_at: datetime | None = Field(default=None, description="Exact locked start time")
    locked_end_at: datetime | None = Field(default=None, description="Exact locked end time")
    locked_resource_ids: list[str] = Field(
        default_factory=list, description="Exact resources pinned to this activity"
    )

    @field_validator("locked_start_at", "locked_end_at")
    @classmethod
    def validate_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("Locked timestamps must be timezone-aware.")
        return v


# Discriminated union of all typed constraints
SchedulingConstraint = Annotated[
    NoOverlapConstraint
    | CapacityConstraint
    | EligibilityConstraint
    | PrecedenceConstraint
    | TimeWindowConstraint
    | MinimumGapConstraint
    | LockConstraint,
    Field(discriminator="constraint_type"),
]
