"""Unit tests for soft-objective weight normalization and CP-SAT integer precision.

Verifies:
1. Every positive soft weight (> 0) produces an integer coefficient > 0.
2. Explicit test matrix: 0.001, 0.01, 0.1, 0.5, 1, 1.5, 15, 33.7.
3. Universal normalization across all objective components.
4. Relative ordering and exact ratios are preserved.
5. Zero weights remain 0.
6. Negative weights are rejected.
7. Reproducibility metadata is correctly exported.
8. CP-SAT solve with tiny fractional weight (0.001) produces valid solve and records metadata.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import TimeWindow
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import (
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.solvers.cpsat import CpSatScheduler
from app.scheduling.solvers.objective_normalizer import normalize_soft_objective_weights


def test_objective_normalizer_test_matrix_positive_coefficients() -> None:
    """Verify that every positive weight in the required test matrix yields coefficient > 0."""
    matrix = [0.001, 0.01, 0.1, 0.5, 1.0, 1.5, 15.0, 33.7]

    for val in matrix:
        weights = SoftObjectiveWeights(
            preference_weight=val,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=val,
        )
        norm = normalize_soft_objective_weights(weights)
        assert (
            norm.preference_weight > 0
        ), f"Expected > 0 for input {val}, got {norm.preference_weight}"
        assert norm.movement_weight > 0, f"Expected > 0 for input {val}, got {norm.movement_weight}"


def test_objective_normalizer_preserves_relative_ordering() -> None:
    """Verify relative ordering is preserved across disparate fractional weights."""
    weights = SoftObjectiveWeights(
        preference_weight=0.001,
        idle_gap_weight=0.01,
        fairness_weight=0.1,
        utilization_weight=0.5,
        movement_weight=1.5,
    )
    norm = normalize_soft_objective_weights(weights)

    # Check strict monotonicity across all 5 soft objective weights
    assert norm.preference_weight < norm.idle_gap_weight
    assert norm.idle_gap_weight < norm.fairness_weight
    assert norm.fairness_weight < norm.utilization_weight
    assert norm.utilization_weight < norm.movement_weight

    # Exact expected normalized integers: 1, 10, 100, 500, 1500
    assert norm.preference_weight == 1
    assert norm.idle_gap_weight == 10
    assert norm.fairness_weight == 100
    assert norm.utilization_weight == 500
    assert norm.movement_weight == 1500

    # Also test higher values: 1.5, 15.0, 33.7
    weights_high = SoftObjectiveWeights(
        preference_weight=1.5,
        idle_gap_weight=15.0,
        fairness_weight=33.7,
        utilization_weight=0,
        movement_weight=0,
    )
    norm_high = normalize_soft_objective_weights(weights_high)
    assert norm_high.preference_weight < norm_high.idle_gap_weight < norm_high.fairness_weight
    assert norm_high.preference_weight == 15
    assert norm_high.idle_gap_weight == 150
    assert norm_high.fairness_weight == 337


def test_objective_normalizer_multi_weight_profile_preserves_ratios() -> None:
    """Verify multi-weight normalization preserves exact ratios and relative priorities."""
    weights = SoftObjectiveWeights(
        preference_weight=0.2,
        idle_gap_weight=0.4,
        fairness_weight=1.0,
        utilization_weight=0.0,
        movement_weight=3.0,
    )
    norm = normalize_soft_objective_weights(weights)

    # All positive weights must be > 0
    assert norm.preference_weight > 0
    assert norm.idle_gap_weight > 0
    assert norm.fairness_weight > 0
    assert norm.movement_weight > 0
    # Zero weight remains 0
    assert norm.utilization_weight == 0

    # Relative ordering: preference (0.2) < idle_gap (0.4) < fairness (1.0) < movement (3.0)
    assert norm.preference_weight < norm.idle_gap_weight
    assert norm.idle_gap_weight < norm.fairness_weight
    assert norm.fairness_weight < norm.movement_weight

    # Exact ratio check: idle_gap / preference == 0.4 / 0.2 == 2.0
    assert norm.idle_gap_weight / norm.preference_weight == 2.0
    # fairness / preference == 1.0 / 0.2 == 5.0
    assert norm.fairness_weight / norm.preference_weight == 5.0
    # movement / preference == 3.0 / 0.2 == 15.0
    assert norm.movement_weight / norm.preference_weight == 15.0


def test_objective_normalizer_all_zero_weights() -> None:
    """Verify that all-zero weights safely normalize to zero coefficients."""
    weights = SoftObjectiveWeights(
        preference_weight=0,
        idle_gap_weight=0,
        fairness_weight=0,
        utilization_weight=0,
        movement_weight=0,
    )
    norm = normalize_soft_objective_weights(weights)
    assert norm.preference_weight == 0
    assert norm.idle_gap_weight == 0
    assert norm.fairness_weight == 0
    assert norm.utilization_weight == 0
    assert norm.movement_weight == 0


def test_objective_normalizer_rejects_negative_weights() -> None:
    """Verify that negative weights fail validation."""
    with pytest.raises(ValidationError):
        SoftObjectiveWeights(
            preference_weight=-1,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        )


def test_cpsat_solver_solves_with_tiny_fractional_movement_weight() -> None:
    """Verify CP-SAT solver successfully executes with tiny movement weight (0.001)."""
    t0 = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    h_end = t0 + timedelta(days=1)

    profile = SolverProfile(
        profile_key="micro_weight_profile",
        version=1,
        name="Micro Weight Profile",
        horizon_start=t0,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=10,
        num_search_workers=2,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0.005,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0.001,
            movement_weight=0.001,
        ),
    )

    request = SchedulingRequest(
        tenant_id="tenant-test-micro",
        schedule_key="sched-micro",
        horizon_start=t0,
        horizon_end=h_end,
        timezone="UTC",
        activities=[
            SchedulingActivity(
                id="act-1",
                title="Activity 1",
                duration_minutes=60,
                allowed_windows=[TimeWindow(start_at=t0, end_at=h_end)],
                required_skills=["SKILL_A"],
            )
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="res-1",
                type=ResourceType.PERSON,
                display_label="Worker 1",
                skills=["SKILL_A"],
            )
        ],
        constraints=[],
        profile=profile,
    )

    scheduler = CpSatScheduler()
    result = scheduler.solve(request)

    assert result.status.value == "FEASIBLE"
    assert len(result.assignments) == 1
    # Check that solver metadata recorded normalized objective weights
    assert "normalized_objective_weights" in result.solver_metadata
    meta = result.solver_metadata["normalized_objective_weights"]
    assert meta["movement_weight"] > 0
    assert meta["preference_weight"] > 0
    assert meta["utilization_weight"] > 0
    # 0.005 vs 0.001 -> ratio 5:1
    assert meta["preference_weight"] == 5
    assert meta["movement_weight"] == 1


def test_weight_precision_canonical_contract_valid_matrix() -> None:
    """Verify Section 13 test matrix: 0, 0.000001, 0.001, 0.01, 0.1, 0.5, 1, 1.5, 33.7.

    Every accepted positive value must yield an integer coefficient > 0.
    """
    valid_values = [0, 0.000001, 0.001, 0.01, 0.1, 0.5, 1, 1.5, 33.7]

    for v in valid_values:
        w = SoftObjectiveWeights(
            preference_weight=v,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=v,
        )
        norm = normalize_soft_objective_weights(w)
        if v == 0:
            assert norm.preference_weight == 0
            assert norm.movement_weight == 0
        else:
            assert norm.preference_weight > 0, f"Expected > 0 for {v}, got {norm.preference_weight}"
            assert norm.movement_weight > 0, f"Expected > 0 for {v}, got {norm.movement_weight}"


def test_weight_precision_canonical_contract_invalid_matrix() -> None:
    """Verify Section 13 invalid examples: 0.0000001, 0.1234567, negative values, NaN, Infinity."""
    invalid_values = [
        0.0000001,  # 7 decimals / smaller than 1e-6
        0.1234567,  # 7 decimals
        -0.01,
        -1.0,
        float("nan"),
        float("inf"),
        float("-inf"),
    ]

    for inv in invalid_values:
        with pytest.raises(ValidationError):
            SoftObjectiveWeights(
                preference_weight=inv,
                idle_gap_weight=0,
                fairness_weight=0,
                utilization_weight=0,
                movement_weight=0,
            )


def test_repair_movement_weight_rejects_zero_and_excess_precision() -> None:
    """Verify ScheduleRepairRequest forbids zero and excess precision for movement_weight."""
    from app.scheduling.repair.models import ScheduleRepairRequest

    # Valid positive movement_weight
    req_valid = ScheduleRepairRequest(movement_weight=0.000001)
    assert req_valid.movement_weight == 0.000001

    req_valid_2 = ScheduleRepairRequest(movement_weight=1.5)
    assert req_valid_2.movement_weight == 1.5

    # Zero is strictly forbidden for repair movement_weight
    with pytest.raises(ValidationError):
        ScheduleRepairRequest(movement_weight=0)

    with pytest.raises(ValidationError):
        ScheduleRepairRequest(movement_weight=0.0)

    # Excess precision is rejected
    with pytest.raises(ValidationError):
        ScheduleRepairRequest(movement_weight=0.0000001)

    with pytest.raises(ValidationError):
        ScheduleRepairRequest(movement_weight=0.1234567)

    with pytest.raises(ValidationError):
        ScheduleRepairRequest(movement_weight=float("nan"))

    with pytest.raises(ValidationError):
        ScheduleRepairRequest(movement_weight=float("inf"))


def test_fingerprint_snapshot_normalization_consistency() -> None:
    """Verify Section 12: validation, fingerprint, snapshot, and solve match."""
    from app.scheduling.hashing import compute_input_snapshot_hash

    t0 = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    h_end = t0 + timedelta(days=1)

    profile = SolverProfile(
        profile_key="consistent_precision_profile",
        version=1,
        name="Consistent Precision Profile",
        horizon_start=t0,
        horizon_end=h_end,
        time_granularity_minutes=15,
        max_solve_seconds=10,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0.000001,
            idle_gap_weight=0.001,
            fairness_weight=0.01,
            utilization_weight=0.5,
            movement_weight=1.5,
        ),
    )

    request = SchedulingRequest(
        tenant_id="tenant-consistent",
        schedule_key="sched-consistent",
        horizon_start=t0,
        horizon_end=h_end,
        timezone="UTC",
        activities=[
            SchedulingActivity(
                id="act-1",
                title="Activity 1",
                duration_minutes=60,
                allowed_windows=[TimeWindow(start_at=t0, end_at=h_end)],
                required_skills=["SKILL_A"],
            )
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="res-1",
                type=ResourceType.PERSON,
                display_label="Worker 1",
                skills=["SKILL_A"],
            )
        ],
        constraints=[],
        profile=profile,
    )

    # 1. Fingerprint is deterministic
    h1 = compute_input_snapshot_hash(request)
    h2 = compute_input_snapshot_hash(request)
    assert h1 == h2

    # 2. Snapshot model_dump preserves identical float values
    dumped = request.model_dump(mode="json")
    assert dumped["profile"]["soft_weights"]["preference_weight"] == 0.000001
    assert dumped["profile"]["soft_weights"]["movement_weight"] == 1.5

    # 3. Solver normalization produces identical coefficients
    norm = normalize_soft_objective_weights(request.profile.soft_weights)
    assert norm.preference_weight == 1
    assert norm.movement_weight == 1500000
    assert norm.preference_weight > 0
    assert norm.movement_weight > 0
