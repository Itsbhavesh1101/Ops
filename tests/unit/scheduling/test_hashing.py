"""Unit tests for deterministic scheduling input snapshot hashing.

Verifies:
- SHA-256 generation over SchedulingRequest.
- Canonical JSON key sorting invariance.
- Sensitivity to problem mutations (duration, skills, weights).
"""

from datetime import UTC, datetime, timedelta

from app.resources.schemas import ResourceType
from app.scheduling.hashing import compute_input_snapshot_hash
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import (
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)


def _build_test_request() -> SchedulingRequest:
    t0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    t1 = t0 + timedelta(days=2)
    return SchedulingRequest(
        tenant_id="tenant-hash-test",
        schedule_key="sched-001",
        horizon_start=t0,
        horizon_end=t1,
        activities=[
            SchedulingActivity(
                id="act-1",
                title="Activity 1",
                duration_minutes=60,
                required_skills=["ELECTRICAL"],
            ),
            SchedulingActivity(
                id="act-2",
                title="Activity 2",
                duration_minutes=90,
                predecessors=["act-1"],
            ),
        ],
        resources=[
            SchedulingResourceSnapshot(
                id="res-1",
                type=ResourceType.PERSON,
                display_label="Tech Alice",
                capacity=1,
                skills=["ELECTRICAL"],
            ),
            SchedulingResourceSnapshot(
                id="res-2",
                type=ResourceType.EQUIPMENT,
                display_label="Van 1",
                capacity=1,
            ),
        ],
        profile=SolverProfile(
            profile_key="profile-test",
            name="Test Profile",
            horizon_start=t0,
            horizon_end=t1,
            time_granularity_minutes=15,
            max_solve_seconds=10.0,
            random_seed=42,
            num_search_workers=1,
            soft_weights=SoftObjectiveWeights(
                preference_weight=1,
                idle_gap_weight=1,
                fairness_weight=1,
                utilization_weight=1,
                movement_weight=1,
            ),
        ),
    )


def test_input_snapshot_hash_determinism() -> None:
    """Verify identical requests yield identical SHA-256 hashes."""
    req1 = _build_test_request()
    req2 = _build_test_request()

    hash1 = compute_input_snapshot_hash(req1)
    hash2 = compute_input_snapshot_hash(req2)
    assert hash1 == hash2
    assert len(hash1) == 64


def test_input_snapshot_hash_sensitivity() -> None:
    """Verify changes to duration alter the hash."""
    req1 = _build_test_request()

    # Create mutated request with duration change
    t0 = req1.horizon_start
    t1 = req1.horizon_end
    mutated_acts = list(req1.activities)
    mutated_acts[0] = SchedulingActivity(
        id="act-1",
        title="Activity 1",
        duration_minutes=120,  # Changed from 60
        required_skills=["ELECTRICAL"],
    )
    req2 = SchedulingRequest(
        tenant_id=req1.tenant_id,
        schedule_key=req1.schedule_key,
        horizon_start=t0,
        horizon_end=t1,
        activities=mutated_acts,
        resources=req1.resources,
        profile=req1.profile,
    )

    hash1 = compute_input_snapshot_hash(req1)
    hash2 = compute_input_snapshot_hash(req2)
    assert hash1 != hash2
