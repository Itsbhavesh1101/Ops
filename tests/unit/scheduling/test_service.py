"""Unit tests for SchedulingService."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.models.scheduling import ScheduleJobRecord
from app.resources.schemas import ResourceType
from app.scheduling.constraints.models import NoOverlapConstraint
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import (
    ScheduleScoreComponents,
    ScheduleStatus,
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.service import (
    HardConstraintViolationError,
    SchedulingService,
)
from app.scheduling.solvers.models import SolverResult


@pytest.fixture
def dummy_request() -> SchedulingRequest:
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)
    profile = SolverProfile(
        profile_key="std",
        version=1,
        name="Standard",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )
    res = SchedulingResourceSnapshot(
        id="tech-1",
        type=ResourceType.PERSON,
        display_label="Tech 1",
        capacity=1,
    )
    act = SchedulingActivity(
        id="act-1",
        title="Task 1",
        duration_minutes=60,
        required_resource_ids=["tech-1"],
    )
    return SchedulingRequest(
        tenant_id="tenant-123",
        schedule_key="maint",
        horizon_start=t0,
        horizon_end=t1,
        activities=[act],
        resources=[res],
        constraints=[NoOverlapConstraint(id="c1", name="No overlap", resource_ids=["tech-1"])],
        profile=profile,
    )


@pytest.mark.asyncio
async def test_submit_job(dummy_request: SchedulingRequest) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    service = SchedulingService()

    job = await service.submit_job(session, "tenant-123", dummy_request, schedule_key="maint")
    assert job.tenant_id == "tenant-123"
    assert job.schedule_key == "maint"
    assert job.status == "REQUESTED"
    assert len(job.input_snapshot_hash) == 64
    session.add.assert_called_once_with(job)
    session.flush.assert_called_once()


@pytest.mark.asyncio
async def test_execute_job_solve_hard_violation_raises(dummy_request: SchedulingRequest) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    mock_scheduler = MagicMock()
    # Simulate a solver that falsely returns FEASIBLE with hard violations > 0
    mock_scheduler.solve.return_value = SolverResult(
        status=ScheduleStatus.FEASIBLE,
        assignments=[],
        score_components=ScheduleScoreComponents(
            hard_constraint_violations=1,  # ILLEGAL
            total_score=999,
        ),
        solver_metadata={},
    )
    service = SchedulingService(scheduler=mock_scheduler)

    job_id = uuid4()
    job = ScheduleJobRecord(
        id=job_id,
        tenant_id="tenant-123",
        schedule_key="maint",
        status="SOLVING",
        input_snapshot=dummy_request.model_dump(mode="json"),
        input_snapshot_hash="hash-123",
        solver_metadata={},
        diagnostics=[],
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = job
    session.execute.return_value = mock_res

    with pytest.raises(HardConstraintViolationError):
        await service.execute_job_solve(session, job_id)

    assert job.status == "FAILED"


@pytest.mark.asyncio
async def test_execute_job_solve_infeasible_creates_no_candidate_schedule(
    dummy_request: SchedulingRequest,
) -> None:
    """Verify INFEASIBLE solve updates job with diagnostics but creates NO candidate schedule."""
    from app.db.models.scheduling import ScheduleRecord
    from app.scheduling.diagnostics.models import InfeasibilityDiagnostic

    session = AsyncMock()
    session.add = MagicMock()
    mock_scheduler = MagicMock()
    mock_scheduler.solve.return_value = SolverResult(
        status=ScheduleStatus.INFEASIBLE,
        diagnostics=[
            InfeasibilityDiagnostic(
                root_cause_type="RESOURCE_UNAVAILABLE",
                human_readable_explanation="Resource tech-1 not available.",
                conflicting_constraint_ids=["c1"],
                relaxation_candidates=["extend window"],
            )
        ],
        solver_metadata={"status_string": "INFEASIBLE"},
    )
    service = SchedulingService(scheduler=mock_scheduler)

    job_id = uuid4()
    job = ScheduleJobRecord(
        id=job_id,
        tenant_id="tenant-123",
        schedule_key="maint",
        status="SOLVING",
        input_snapshot=dummy_request.model_dump(mode="json"),
        input_snapshot_hash="hash-123",
        solver_metadata={},
        diagnostics=[],
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = job
    session.execute.return_value = mock_res

    result = await service.execute_job_solve(session, job_id)

    assert result.status == ScheduleStatus.INFEASIBLE
    assert job.status == "INFEASIBLE"
    assert len(job.diagnostics) == 1
    assert job.diagnostics[0]["root_cause_type"] == "RESOURCE_UNAVAILABLE"

    # CRITICAL: Verify session.add was NEVER called with a ScheduleRecord
    for call in session.add.call_args_list:
        added_obj = call[0][0]
        assert not isinstance(added_obj, ScheduleRecord), (
            "No ScheduleRecord should be created on INFEASIBLE"
        )


@pytest.mark.asyncio
async def test_execute_job_solve_budget_exhausted_creates_no_candidate_schedule(
    dummy_request: SchedulingRequest,
) -> None:
    """Verify BUDGET_EXHAUSTED solve updates job but creates NO candidate schedule."""
    from app.db.models.scheduling import ScheduleRecord
    from app.scheduling.diagnostics.models import InfeasibilityDiagnostic

    session = AsyncMock()
    session.add = MagicMock()
    mock_scheduler = MagicMock()
    mock_scheduler.solve.return_value = SolverResult(
        status=ScheduleStatus.BUDGET_EXHAUSTED,
        diagnostics=[
            InfeasibilityDiagnostic(
                root_cause_type="BUDGET_EXHAUSTED",
                human_readable_explanation="Solve time budget exhausted.",
                conflicting_constraint_ids=[],
                relaxation_candidates=["increase max_solve_seconds"],
            )
        ],
        solver_metadata={"status_string": "UNKNOWN"},
    )
    service = SchedulingService(scheduler=mock_scheduler)

    job_id = uuid4()
    job = ScheduleJobRecord(
        id=job_id,
        tenant_id="tenant-123",
        schedule_key="maint",
        status="SOLVING",
        input_snapshot=dummy_request.model_dump(mode="json"),
        input_snapshot_hash="hash-123",
        solver_metadata={},
        diagnostics=[],
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = job
    session.execute.return_value = mock_res

    result = await service.execute_job_solve(session, job_id)

    assert result.status == ScheduleStatus.BUDGET_EXHAUSTED
    assert job.status == "BUDGET_EXHAUSTED"
    assert "budget exhausted" in job.failure_message.lower()

    # CRITICAL: Verify session.add was NEVER called with a ScheduleRecord
    for call in session.add.call_args_list:
        added_obj = call[0][0]
        assert not isinstance(added_obj, ScheduleRecord), (
            "No ScheduleRecord should be created on BUDGET_EXHAUSTED"
        )

