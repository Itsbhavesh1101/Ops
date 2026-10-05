"""General soft-objective weight normalization for CP-SAT integer optimization.

Part of Phase 4 Generic Scheduling.
Enforces:
- Every accepted positive soft-objective weight (> 0) yields an integer coefficient > 0.
  No accepted business objective is silently disabled.
- Universal normalization across ALL soft objective components (preference, idle_gap,
  fairness, utilization, movement).
- Preserves relative ordering and exact ratios to bounded decimal precision.
- Simplification via greatest common divisor (GCD) to keep integer coefficients minimal.
- Verification that normalized coefficients stay safely within CP-SAT 32-bit bounds.
- Full reproducibility metadata recording for immutable solve snapshots.
"""

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.scheduling.profiles.models import (
    MAX_WEIGHT_DECIMAL_PLACES,
    SoftObjectiveWeights,
    validate_weight_precision,
)

# Technical maximum decimal precision supported (up to 6 decimal places, e.g. 0.000001)
MAX_DECIMAL_PLACES: int = MAX_WEIGHT_DECIMAL_PLACES

# CP-SAT safe 32-bit signed integer coefficient ceiling
MAX_CPSAT_COEFFICIENT: int = 2_000_000_000


@dataclass(frozen=True)
class NormalizedObjectiveWeights:
    """Deterministic integer objective coefficients for CP-SAT solver."""

    preference_weight: int
    idle_gap_weight: int
    fairness_weight: int
    utilization_weight: int
    movement_weight: int
    scale_factor: int
    gcd_divisor: int

    def to_metadata_dict(self) -> dict[str, Any]:
        """Serialize normalization parameters for audit and reproducibility."""
        return {
            "preference_weight": self.preference_weight,
            "idle_gap_weight": self.idle_gap_weight,
            "fairness_weight": self.fairness_weight,
            "utilization_weight": self.utilization_weight,
            "movement_weight": self.movement_weight,
            "scale_factor": self.scale_factor,
            "gcd_divisor": self.gcd_divisor,
        }


def _decimal_places(val: Decimal) -> int:
    """Compute number of fractional decimal places for a Decimal value."""
    if val == val.to_integral():
        return 0
    exponent = val.as_tuple().exponent
    return abs(exponent) if isinstance(exponent, int) and exponent < 0 else 0


def normalize_soft_objective_weights(
    weights: SoftObjectiveWeights,
) -> NormalizedObjectiveWeights:
    """Normalize all soft objective weights into strictly positive integer coefficients for CP-SAT.

    Guarantees:
    - If weight == 0 -> coefficient == 0
    - If weight > 0 -> coefficient >= 1 (strictly positive)
    - If w1 < w2 -> coefficient(w1) < coefficient(w2) (relative ordering preserved)
    - If w2 = k * w1 -> coefficient(w2) = k * coefficient(w1) (exact ratios preserved)
    """
    field_names = [
        "preference_weight",
        "idle_gap_weight",
        "fairness_weight",
        "utilization_weight",
        "movement_weight",
    ]

    decimals: dict[str, Decimal] = {}
    for name in field_names:
        raw_val = getattr(weights, name)
        # Defense-in-depth: enforce canonical precision contract (no silent truncation)
        validate_weight_precision(raw_val, allow_zero=True)
        try:
            if isinstance(raw_val, Decimal):
                d = raw_val
            else:
                d = Decimal(str(raw_val))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError(f"Invalid numeric value for '{name}': {raw_val}") from exc
        decimals[name] = d

    # Collect positive active weights
    positive_decimals = [d for d in decimals.values() if d > 0]
    if not positive_decimals:
        # All weights are zero
        return NormalizedObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
            scale_factor=1,
            gcd_divisor=1,
        )

    # 1. Determine common integer scale factor (10^max_places)
    max_places = 0
    for d in positive_decimals:
        places = min(_decimal_places(d), MAX_DECIMAL_PLACES)
        if places > max_places:
            max_places = places

    scale_factor = 10**max_places

    # 2. Scale all weights uniformly to integer values
    scaled_ints: dict[str, int] = {}
    for name, d in decimals.items():
        if d == 0:
            scaled_ints[name] = 0
        else:
            scaled = int(round(d * scale_factor))
            # Invariant: positive weight must never become 0
            scaled_ints[name] = max(1, scaled)

    # 3. Simplify by common GCD across all positive scaled weights
    positive_scaled = [val for val in scaled_ints.values() if val > 0]
    common_gcd = math.gcd(*positive_scaled) if len(positive_scaled) > 1 else positive_scaled[0]

    final_ints: dict[str, int] = {}
    for name, val in scaled_ints.items():
        if val == 0:
            final_ints[name] = 0
        else:
            reduced = val // common_gcd
            if reduced > MAX_CPSAT_COEFFICIENT:
                raise ValueError(
                    f"Normalized objective coefficient for '{name}' exceeds CP-SAT bound: "
                    f"{reduced} > {MAX_CPSAT_COEFFICIENT}"
                )
            final_ints[name] = max(1, reduced)

    return NormalizedObjectiveWeights(
        preference_weight=final_ints["preference_weight"],
        idle_gap_weight=final_ints["idle_gap_weight"],
        fairness_weight=final_ints["fairness_weight"],
        utilization_weight=final_ints["utilization_weight"],
        movement_weight=final_ints["movement_weight"],
        scale_factor=scale_factor,
        gcd_divisor=common_gcd,
    )
