"""Unit tests for Phase 4A solver profile and time discretization.

Verifies:
- SolverProfile validation and explicit weights.
- Time discretization to integer ticks.
- Out-of-bounds rejection.
- Bidirectional round-trip tick <-> datetime conversion.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.scheduling.profiles.discretization import (
    TimeDiscretizationError,
    datetime_to_tick,
    minutes_to_ticks,
    tick_to_datetime,
)
from app.scheduling.profiles.models import (
    SoftObjectiveWeights,
    SolverProfile,
)


def test_solver_profile_validation() -> None:
    """Verify SolverProfile validates horizon and requires explicit parameters."""
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(days=3)
    profile = SolverProfile(
        profile_key="balanced-v1",
        version=1,
        name="Balanced Operations Profile",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=123,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=3,
            idle_gap_weight=2,
            fairness_weight=1,
            utilization_weight=1,
            movement_weight=5,
        ),
    )
    assert profile.profile_key == "balanced-v1"
    assert profile.max_solve_seconds == 10.0
    assert profile.soft_weights.preference_weight == 3


def test_solver_profile_rejects_inverted_horizon() -> None:
    """Verify horizon_end <= horizon_start fails validation."""
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="must be strictly after"):
        SolverProfile(
            profile_key="invalid-h",
            name="Invalid",
            horizon_start=t0,
            horizon_end=t0 - timedelta(hours=1),
            time_granularity_minutes=15,
            max_solve_seconds=10.0,
            num_search_workers=1,
            soft_weights=SoftObjectiveWeights(
                preference_weight=0,
                idle_gap_weight=0,
                fairness_weight=0,
                utilization_weight=0,
                movement_weight=0,
            ),
        )


def test_time_discretization_conversions() -> None:
    """Verify datetime_to_tick converts accurately and tick_to_datetime round-trips."""
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t_end = t0 + timedelta(days=2)
    granularity = 15  # minutes

    # At start -> tick 0
    assert datetime_to_tick(t0, t0, t_end, granularity) == 0

    # 45 minutes later -> tick 3
    t_45m = t0 + timedelta(minutes=45)
    tick = datetime_to_tick(t_45m, t0, t_end, granularity)
    assert tick == 3

    # Round trip
    reconstructed = tick_to_datetime(tick, t0, granularity)
    assert reconstructed == t_45m


def test_discretization_rejects_outside_horizon() -> None:
    """Verify timestamps outside [horizon_start, horizon_end] are rejected."""
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t_end = t0 + timedelta(hours=8)
    granularity = 15

    # Before start
    with pytest.raises(TimeDiscretizationError, match="earlier than horizon start"):
        datetime_to_tick(t0 - timedelta(minutes=1), t0, t_end, granularity)

    # After end
    with pytest.raises(TimeDiscretizationError, match="exceeds horizon end"):
        datetime_to_tick(t_end + timedelta(minutes=1), t0, t_end, granularity)


def test_minutes_to_ticks() -> None:
    """Verify duration minutes to ticks mapping."""
    granularity = 15
    assert minutes_to_ticks(0, granularity) == 0
    assert minutes_to_ticks(10, granularity) == 1  # Rounds to 1
    assert minutes_to_ticks(30, granularity) == 2
    assert minutes_to_ticks(120, granularity) == 8
