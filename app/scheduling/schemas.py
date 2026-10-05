"""Canonical scheduling request, activity, resource snapshot, and solution schemas.

Part of Phase 4A Generic Scheduling Core.
Enforces:
- Provider-neutral scheduling requests.
- Stable activity and resource identities.
- Immutable snapshot representation.
- Structured candidate assignments and score components.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import SchedulingConstraint, TimeWindow
from app.scheduling.profiles.models import SolverProfile


class ScheduleStatus(StrEnum):
    """Canonical lifecycle states for schedules and solve jobs."""

    REQUESTED = "REQUESTED"
    SOLVING = "SOLVING"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    REVIEW = "REVIEW"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"  # Reserved for genuine system/solver crash only


class LockedAssignment(BaseModel):
    """Pinned time or resource assignment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_at: datetime | None = Field(default=None, description="Pinned start time")
    end_at: datetime | None = Field(default=None, description="Pinned end time")
    resource_ids: list[str] = Field(default_factory=list, description="Pinned resource IDs")


class SchedulingActivity(BaseModel):
    """Discrete operational item or work order to be scheduled."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=1, max_length=128, description="Canonical stable activity ID")
    title: str = Field(..., min_length=1, max_length=255, description="Human-readable title")
    duration_minutes: int = Field(..., ge=1, description="Execution duration in minutes")
    priority: str = Field(default="NORMAL", description="Priority tier (e.g. HIGH, MEDIUM, NORMAL)")

    allowed_windows: list[TimeWindow] = Field(
        default_factory=list, description="Permissible scheduling time windows"
    )
    required_skills: list[str] = Field(
        default_factory=list, description="Capability or certification requirements"
    )
    required_resource_ids: list[str] = Field(
        default_factory=list, description="Explicitly mandated resource IDs"
    )
    predecessors: list[str] = Field(
        default_factory=list, description="Activity IDs that must finish before this starts"
    )
    preferred_resource_ids: list[str] = Field(
        default_factory=list, description="Resource IDs favored by soft objective"
    )
    locked_assignment: LockedAssignment | None = Field(
        default=None, description="Existing locked baseline assignment (for repair or fixed locks)"
    )
    baseline_assignment: LockedAssignment | None = Field(
        default=None,
        description="Previous published baseline assignment for movement penalty in repair",
    )


class SchedulingResourceSnapshot(BaseModel):
    """Immutable snapshot of an available resource for a solve job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=1, max_length=128, description="Canonical resource ID")
    type: ResourceType = Field(..., description="Resource category")
    display_label: str = Field(..., description="Human-readable label")
    capacity: int = Field(default=1, ge=0, description="Available capacity")
    skills: list[str] = Field(default_factory=list, description="Capability tags")
    unavailability_windows: list[TimeWindow] = Field(
        default_factory=list, description="Blackout intervals where resource is unavailable"
    )


class SchedulingProblemDefinition(BaseModel):
    """Client problem definition without client-authoritative tenant_id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule_key: str = Field(..., min_length=1, max_length=128, description="Schedule key")
    horizon_start: datetime = Field(..., description="Planning horizon start (timezone-aware)")
    horizon_end: datetime = Field(..., description="Planning horizon end (timezone-aware)")
    timezone: str = Field(default="UTC", description="IANA timezone identifier")

    activities: list[SchedulingActivity] = Field(
        ..., min_length=1, description="Activities to schedule"
    )
    resources: list[SchedulingResourceSnapshot] = Field(
        ..., min_length=1, description="Available resource catalog snapshot"
    )
    constraints: list[SchedulingConstraint] = Field(
        default_factory=list, description="Explicit constraints to enforce"
    )
    profile: SolverProfile = Field(..., description="Solver execution and tuning profile")


class SchedulingRequest(SchedulingProblemDefinition):
    """Provider-neutral scheduling problem submission payload with trusted tenant scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(..., min_length=1, description="Tenant scope")


class CandidateAssignment(BaseModel):
    """Proposed assignment of an activity to a resource over a time interval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignment_id: str = Field(
        default_factory=lambda: f"asgn-{uuid4().hex[:8]}", description="Unique assignment ID"
    )
    activity_id: str = Field(..., description="Scheduled activity ID")
    resource_id: str = Field(..., description="Assigned resource ID")
    start_at: datetime = Field(..., description="Scheduled start timestamp")
    end_at: datetime = Field(..., description="Scheduled end timestamp")
    is_locked: bool = Field(default=False, description="Whether assignment is locked")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Diagnostic/score metadata")


class ScheduleScoreComponents(BaseModel):
    """Breakdown of optimization objective components and hard violation count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    preference_penalty: int = Field(default=0, ge=0, description="Non-preferred penalty")
    idle_gap_penalty: int = Field(default=0, ge=0, description="Idle gap penalty")
    fairness_penalty: int = Field(default=0, ge=0, description="Workload imbalance penalty")
    utilization_penalty: int = Field(default=0, ge=0, description="Underutilization penalty")
    movement_penalty: int = Field(default=0, ge=0, description="Movement penalty from baseline")
    hard_constraint_violations: int = Field(
        default=0, ge=0, description="Must be strictly 0 for FEASIBLE"
    )
    total_score: int = Field(default=0, ge=0, description="Weighted aggregate penalty score")
