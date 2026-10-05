"""Solver profile schema and configuration models.

Part of Phase 4A Generic Scheduling Core.
Enforces:
- Versioned solver profile definitions.
- Explicit non-hardcoded budgets and weights.
- Timezone-aware horizon boundaries.
- Reproducibility settings.
"""

import math
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.scheduling.constraints.models import ConstraintType

MAX_WEIGHT_DECIMAL_PLACES: int = 6
MIN_POSITIVE_WEIGHT_QUANTUM: Decimal = Decimal("0.000001")


def validate_weight_precision(v: Any, allow_zero: bool = True) -> float:
    """Validate that a soft-objective weight conforms to the canonical precision contract.

    Contract:
    - Must be a finite numeric value (not NaN, not Inf).
    - Must be non-negative (>= 0).
    - If zero: valid if allow_zero=True; rejected if allow_zero=False.
    - If non-zero positive:
      - Must be >= 0.000001 (minimum positive quantum).
      - Must have at most 6 decimal places (no silent rounding; excess precision rejected).
    """
    if v is None:
        raise ValueError("Weight cannot be None")
    try:
        val = float(v)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Weight must be numeric, got {v!r}") from exc

    if math.isnan(val) or math.isinf(val):
        raise ValueError("Weight must be a finite number (not NaN or Infinity)")
    if val < 0:
        raise ValueError(f"Weight must be non-negative, got {val}")

    d = Decimal(str(v if not isinstance(v, float) or not math.isnan(val) else val))
    if d < 0:
        raise ValueError(f"Weight must be non-negative, got {d}")

    if d == 0:
        if not allow_zero:
            raise ValueError("Weight must be strictly positive (greater than 0)")
        return 0.0

    if d < MIN_POSITIVE_WEIGHT_QUANTUM:
        raise ValueError(
            f"Positive weight {d} cannot be smaller than minimum quantum "
            f"{MIN_POSITIVE_WEIGHT_QUANTUM}"
        )

    d_norm = d.normalize()
    exp = d_norm.as_tuple().exponent
    if isinstance(exp, int) and exp < -MAX_WEIGHT_DECIMAL_PLACES:
        raise ValueError(
            f"Weight {d} exceeds maximum precision of {MAX_WEIGHT_DECIMAL_PLACES} decimal places"
        )

    return float(d)


class SoftObjectiveWeights(BaseModel):
    """Configurable weights for soft optimization objective components.

    Enforces explicit policy parameters (no silent production defaults)
    and bounded precision (maximum 6 decimal places, minimum positive quantum 0.000001).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    preference_weight: float = Field(
        ..., ge=0, description="Penalty weight for non-preferred resource assignments"
    )
    idle_gap_weight: float = Field(
        ..., ge=0, description="Penalty for idle holes/gaps between assigned activities"
    )
    fairness_weight: float = Field(
        ..., ge=0, description="Penalty weight for workload variance across resources"
    )
    utilization_weight: float = Field(
        ..., ge=0, description="Weight encouraging compact resource utilization"
    )
    movement_weight: float = Field(
        ..., ge=0, description="Penalty weight for changing baseline assignment (repair)"
    )

    @field_validator(
        "preference_weight",
        "idle_gap_weight",
        "fairness_weight",
        "utilization_weight",
        "movement_weight",
        mode="before",
    )
    @classmethod
    def validate_weights_precision(cls, v: Any) -> float:
        return validate_weight_precision(v, allow_zero=True)


class SolverProfile(BaseModel):
    """Complete versioned solver configuration profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_key: str = Field(
        ..., min_length=1, max_length=128, description="Unique profile identifier"
    )
    version: int = Field(default=1, ge=1, description="Version number")
    name: str = Field(..., min_length=1, max_length=255, description="Human-readable profile name")
    description: str = Field(default="", description="Operational profile documentation")

    horizon_start: datetime = Field(..., description="Planning horizon start (timezone-aware)")
    horizon_end: datetime = Field(..., description="Planning horizon end (timezone-aware)")
    time_granularity_minutes: int = Field(
        ..., ge=1, le=1440, description="Discretization step in minutes (explicit required)"
    )

    max_solve_seconds: float = Field(
        ..., gt=0, le=3600.0, description="Maximum solver CPU time budget (explicit required)"
    )
    random_seed: int | None = Field(
        default=None, description="RNG seed for reproducible search (optional explicit)"
    )
    num_search_workers: int = Field(
        ...,
        ge=1,
        le=64,
        description="Search worker threads (explicit required, 1 for strict determinism)",
    )

    enabled_hard_constraints: list[ConstraintType] = Field(
        default_factory=lambda: list(ConstraintType),
        description="List of active hard constraint types",
    )
    soft_weights: SoftObjectiveWeights = Field(
        ...,
        description="Configurable weights for soft objective functions (explicit required)",
    )

    @field_validator("horizon_start", "horizon_end")
    @classmethod
    def validate_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Horizon boundary must be timezone-aware.")
        return v

    @field_validator("horizon_end")
    @classmethod
    def validate_horizon_order(cls, v: datetime, info: Any) -> datetime:
        start = info.data.get("horizon_start")
        if start is not None and v <= start:
            raise ValueError(f"horizon_end ({v}) must be strictly after horizon_start ({start}).")
        return v
