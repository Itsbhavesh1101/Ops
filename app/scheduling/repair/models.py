"""Data models and request contracts for targeted incremental schedule repair.

Part of Phase 4B Generic Scheduling.
Enforces:
- Generic disruption representation (resource unavailability, interval, affected activities).
- Explicit movement penalty weight requirement.
- No client-authoritative tenant_id.
- Deterministic scope classification (directly impacted vs propagated vs unaffected).
"""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.scheduling.profiles.models import SolverProfile, validate_weight_precision


class ScheduleRepairRequest(BaseModel):
    """Client request for targeted incremental rescheduling following a disruption.

    Tenant ID is strictly derived from trusted context, never client-supplied.
    Requires an explicit movement policy (movement_weight > 0 or profile with movement_weight > 0).
    No silent defaults (e.g. 1.0, 10, 100) are permitted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    disrupted_resource_ids: list[str] = Field(
        default_factory=list,
        description="Canonical resource IDs undergoing unavailability or absence",
    )
    disrupted_interval_start: datetime | None = Field(
        default=None,
        description="Start time of the disruption interval (timezone-aware)",
    )
    disrupted_interval_end: datetime | None = Field(
        default=None,
        description="End time of the disruption interval (timezone-aware)",
    )
    affected_activity_ids: list[str] = Field(
        default_factory=list,
        description="Explicit activity IDs directly impacted by disruption",
    )
    reason: str | None = Field(
        default=None,
        max_length=255,
        description="Operational reason or source reference for disruption",
    )
    movement_weight: float | None = Field(
        default=None,
        gt=0,
        description="Explicit movement penalty weight for repair solve (must be > 0)",
    )
    profile: SolverProfile | None = Field(
        default=None,
        description="Optional custom solver profile override; must have movement_weight > 0",
    )

    @field_validator("movement_weight", mode="before")
    @classmethod
    def validate_movement_weight_precision(cls, v: Any) -> float | None:
        if v is None:
            return None
        return validate_weight_precision(v, allow_zero=False)

    @model_validator(mode="after")
    def validate_explicit_movement_policy(self) -> Self:
        has_req_weight = self.movement_weight is not None and self.movement_weight > 0
        has_profile_weight = (
            self.profile is not None
            and self.profile.soft_weights.movement_weight is not None
            and self.profile.soft_weights.movement_weight > 0
        )
        if not has_req_weight and not has_profile_weight:
            raise ValueError(
                "Repair request requires an explicit movement policy: provide either "
                "'movement_weight' (> 0) or 'profile' with soft_weights.movement_weight > 0. "
                "Silent default movement weights are prohibited."
            )
        if self.movement_weight is not None and self.movement_weight <= 0:
            raise ValueError("Repair movement_weight must be strictly positive (greater than 0)")
        if (
            self.profile is not None
            and self.profile.soft_weights.movement_weight <= 0
            and not has_req_weight
        ):
            raise ValueError(
                "Repair profile soft_weights.movement_weight must be strictly positive "
                "(greater than 0)"
            )
        return self


class RepairScope(BaseModel):
    """Deterministic scope partition for targeted repair solve.

    Partitions published baseline assignments into:
    - affected_activity_ids: movable during repair to resolve disruption
    - unaffected_activity_ids: frozen as hard constraints
    - locked_baseline_activity_ids: explicitly locked assignments from baseline
      that must remain pinned
    - propagation_reasons: deterministic audit trace explaining why each
      propagated activity was included in the repair scope
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    directly_impacted_activity_ids: set[str] = Field(default_factory=set)
    propagated_activity_ids: set[str] = Field(default_factory=set)
    affected_activity_ids: set[str] = Field(default_factory=set)
    unaffected_activity_ids: set[str] = Field(default_factory=set)
    locked_baseline_activity_ids: set[str] = Field(default_factory=set)
    propagation_reasons: dict[str, str] = Field(default_factory=dict)

    def to_metadata_dict(self) -> dict[str, Any]:
        """Convert scope to JSON-serializable dictionary for audit and lineage storage."""
        return {
            "directly_impacted": sorted(self.directly_impacted_activity_ids),
            "propagated": sorted(self.propagated_activity_ids),
            "affected_count": len(self.affected_activity_ids),
            "unaffected_count": len(self.unaffected_activity_ids),
            "locked_count": len(self.locked_baseline_activity_ids),
            "propagation_reasons": dict(sorted(self.propagation_reasons.items())),
        }
