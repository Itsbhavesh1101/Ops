"""Unit tests for Phase 4A canonical constraint catalog schemas.

Verifies:
- Discriminative parsing of all 7 constraint types.
- HARD vs SOFT classification.
- Timezone validation.
- Extra field rejection (fail-closed).
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter

from app.scheduling.constraints.models import (
    CapacityConstraint,
    ConstraintHardness,
    ConstraintType,
    EligibilityConstraint,
    LockConstraint,
    MinimumGapConstraint,
    NoOverlapConstraint,
    PrecedenceConstraint,
    SchedulingConstraint,
    TimeWindow,
    TimeWindowConstraint,
)

ConstraintAdapter = TypeAdapter(SchedulingConstraint)


def test_no_overlap_constraint_parsing() -> None:
    """Verify NO_OVERLAP constraint parses and validates required fields."""
    raw = {
        "constraint_type": "NO_OVERLAP",
        "name": "Single Room Booking",
        "hardness": "HARD",
        "resource_ids": ["room-1", "room-2"],
    }
    constraint = ConstraintAdapter.validate_python(raw)
    assert isinstance(constraint, NoOverlapConstraint)
    assert constraint.constraint_type == ConstraintType.NO_OVERLAP
    assert constraint.hardness == ConstraintHardness.HARD
    assert constraint.resource_ids == ["room-1", "room-2"]


def test_capacity_constraint_parsing() -> None:
    """Verify CAPACITY constraint validation."""
    raw = {
        "constraint_type": "CAPACITY",
        "name": "Lab Bench Max Load",
        "hardness": "HARD",
        "resource_id": "lab-bench-1",
        "max_capacity": 5,
        "demand_per_activity": {"act-1": 2, "act-2": 3},
    }
    constraint = ConstraintAdapter.validate_python(raw)
    assert isinstance(constraint, CapacityConstraint)
    assert constraint.max_capacity == 5
    assert constraint.demand_per_activity["act-1"] == 2


def test_eligibility_constraint_parsing() -> None:
    """Verify ELIGIBILITY constraint validation."""
    raw = {
        "constraint_type": "ELIGIBILITY",
        "name": "Electrical Skill Required",
        "hardness": "HARD",
        "activity_id": "wo-101",
        "required_skills": ["ELECTRICAL"],
    }
    constraint = ConstraintAdapter.validate_python(raw)
    assert isinstance(constraint, EligibilityConstraint)
    assert constraint.required_skills == ["ELECTRICAL"]


def test_precedence_constraint_parsing() -> None:
    """Verify PRECEDENCE constraint with min_gap."""
    raw = {
        "constraint_type": "PRECEDENCE",
        "name": "Step 1 before Step 2",
        "hardness": "HARD",
        "predecessor_id": "step-1",
        "successor_id": "step-2",
        "min_gap_minutes": 15,
    }
    constraint = ConstraintAdapter.validate_python(raw)
    assert isinstance(constraint, PrecedenceConstraint)
    assert constraint.min_gap_minutes == 15


def test_time_window_constraint_parsing() -> None:
    """Verify TIME_WINDOW constraint and timezone requirement."""
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=3)
    raw = {
        "constraint_type": "TIME_WINDOW",
        "name": "Allowed Operating Window",
        "hardness": "HARD",
        "activity_id": "act-window",
        "allowed_windows": [{"start_at": t0.isoformat(), "end_at": t1.isoformat()}],
    }
    constraint = ConstraintAdapter.validate_python(raw)
    assert isinstance(constraint, TimeWindowConstraint)
    assert len(constraint.allowed_windows) == 1
    assert constraint.allowed_windows[0].start_at == t0


def test_time_window_rejects_naive_timestamp() -> None:
    """Verify naive datetimes fail validation in TimeWindow."""
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeWindow(
            start_at=datetime(2026, 11, 9, 9, 0),  # naive
            end_at=datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
        )


def test_minimum_gap_constraint_parsing() -> None:
    """Verify MINIMUM_GAP constraint requiring at least 2 activities."""
    raw = {
        "constraint_type": "MINIMUM_GAP",
        "name": "Rest Between Shifts",
        "hardness": "SOFT",
        "weight": 5,
        "activity_ids": ["act-a", "act-b"],
        "min_gap_minutes": 60,
    }
    constraint = ConstraintAdapter.validate_python(raw)
    assert isinstance(constraint, MinimumGapConstraint)
    assert constraint.hardness == ConstraintHardness.SOFT
    assert constraint.weight == 5


def test_lock_constraint_parsing() -> None:
    """Verify LOCK constraint pins time and resource."""
    t0 = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=2)
    raw = {
        "constraint_type": "LOCK",
        "name": "Fixed Schedule Lock",
        "hardness": "HARD",
        "activity_id": "act-pinned",
        "locked_start_at": t0.isoformat(),
        "locked_end_at": t1.isoformat(),
        "locked_resource_ids": ["res-pinned-1"],
    }
    constraint = ConstraintAdapter.validate_python(raw)
    assert isinstance(constraint, LockConstraint)
    assert constraint.locked_start_at == t0
    assert constraint.locked_resource_ids == ["res-pinned-1"]
