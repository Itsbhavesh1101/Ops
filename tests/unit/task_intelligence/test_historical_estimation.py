"""Comprehensive unit tests for Effort Estimation and Historical Recalibration (FR-TI-04).

Tests:
- Heuristic estimation precedence
- Manual override controls
- Database historical median calculation
- Closed-loop recalibration using completed task data
- Strict tenant isolation for historical benchmark statistics
- Honest fallback behavior when historical data is absent
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.task_intelligence.estimators import (
    DatabaseHistoricalMedianProvider,
    HeuristicEffortEstimator,
)


def test_heuristic_precedence_hierarchy() -> None:
    """Precedence hierarchy:
    1. Manual override > 2. Historical median > 3. Category heuristic > 4. Default fallback.
    """
    provider = DatabaseHistoricalMedianProvider(
        tenant_id="tenant-alpha",
        category_medians={"PLANNING": 150.0},
    )
    estimator = HeuristicEffortEstimator(historical_provider=provider)

    # 1. Human override takes precedence over historical median
    res_override = estimator.estimate(
        title="Override Task",
        work_category="PLANNING",
        human_override=45.0,
    )
    assert res_override.value == 45.0
    assert res_override.basis == "human_override"
    assert res_override.confidence == 1.0
    assert res_override.is_manual_override is True

    # 2. Historical median takes precedence over category heuristic
    res_hist = estimator.estimate(
        title="Historical Task",
        work_category="PLANNING",
    )
    assert res_hist.value == 150.0
    assert res_hist.basis == "historical_median"
    assert res_hist.confidence == 0.85
    assert res_hist.is_manual_override is False

    # 3. Category heuristic takes precedence over default fallback when category lacks history
    res_cat = estimator.estimate(
        title="Category Task",
        work_category="ROOM_PREPARATION",  # Default category duration is 90.0
    )
    assert res_cat.value == 90.0
    assert res_cat.basis == "category_heuristic:ROOM_PREPARATION"
    assert res_cat.confidence == 0.7
    assert res_cat.is_manual_override is False

    # 4. Fallback default used when category is unknown and has no historical data
    res_fallback = estimator.estimate(
        title="Unknown Task",
        work_category="NONEXISTENT_CATEGORY",
    )
    assert res_fallback.value == 60.0
    assert res_fallback.basis == "default_fallback"
    assert res_fallback.confidence == 0.5
    assert res_fallback.is_manual_override is False


def test_database_historical_median_calculation() -> None:
    """Mathematical median calculation across real sample distributions."""
    provider = DatabaseHistoricalMedianProvider(tenant_id="tenant-alpha")

    # Odd count of samples: exact middle
    provider.update_median("EXAM_SETUP", [60, 90, 120])
    assert provider.get_historical_median_duration("EXAM_SETUP") == 90.0

    # Even count of samples: mean of two middles
    provider.update_median("PAPER_PRINTING", [30, 45, 60, 90])
    assert provider.get_historical_median_duration("PAPER_PRINTING") == 52.5

    # Case-insensitivity and whitespace stripping
    assert provider.get_historical_median_duration("  paper_printing  ") == 52.5


def test_historical_median_absent_returns_none() -> None:
    """When historical execution data is absent, returns None without fabricating values."""
    provider = DatabaseHistoricalMedianProvider(tenant_id="tenant-alpha")
    assert provider.get_historical_median_duration("UNKNOWN_CAT") is None


def test_historical_median_tenant_isolation() -> None:
    """Completed tasks in tenant A do not contaminate tenant B historical medians."""
    provider_a = DatabaseHistoricalMedianProvider(
        tenant_id="tenant-alpha",
        category_medians={"SCHEDULING": 45.0},
    )
    provider_b = DatabaseHistoricalMedianProvider(
        tenant_id="tenant-beta",
        category_medians={"SCHEDULING": 180.0},
    )

    assert provider_a.get_historical_median_duration("SCHEDULING") == 45.0
    assert provider_b.get_historical_median_duration("SCHEDULING") == 180.0


def test_closed_loop_recalibration_with_completed_tasks() -> None:
    """Recalibrating median with newly recorded actual durations updates the estimate."""
    provider = DatabaseHistoricalMedianProvider(tenant_id="tenant-alpha")
    estimator = HeuristicEffortEstimator(historical_provider=provider)

    # Initially no historical data: falls back to category heuristic (120m for SCHEDULING)
    initial_estimate = estimator.estimate(title="Generate schedule", work_category="SCHEDULING")
    assert initial_estimate.value == 120.0
    assert initial_estimate.basis == "category_heuristic:SCHEDULING"

    # Tasks complete faster than anticipated: [50, 55, 60] minutes
    provider.update_median("SCHEDULING", [50, 55, 60])

    # Re-estimating with recalibrated historical data
    recalibrated_estimate = estimator.estimate(
        title="Generate schedule", work_category="SCHEDULING"
    )
    assert recalibrated_estimate.value == 55.0
    assert recalibrated_estimate.basis == "historical_median"
    assert recalibrated_estimate.confidence == 0.85


@pytest.mark.asyncio
async def test_load_for_tenant_query_execution() -> None:
    """load_for_tenant queries completed tasks with positive durations for the given tenant."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    # Simulated query rows: (work_category, actual_duration_minutes)
    mock_result.all.return_value = [
        ("PLANNING", 80),
        ("PLANNING", 100),
        ("PLANNING", 120),
        ("READINESS", 50),
    ]
    mock_session.execute.return_value = mock_result

    mock_repo = MagicMock()
    mock_repo.session = mock_session

    provider = await DatabaseHistoricalMedianProvider.load_for_tenant(
        task_repo=mock_repo,
        tenant_id="tenant-gamma",
    )

    assert provider.tenant_id == "tenant-gamma"
    assert provider.get_historical_median_duration("PLANNING") == 100.0
    assert provider.get_historical_median_duration("READINESS") == 50.0
    assert provider.get_historical_median_duration("OTHER") is None


@pytest.mark.asyncio
async def test_calibrate_plan_effort_runtime_pipeline() -> None:
    """calibrate_plan_effort recalibrates all tasks according to approved precedence."""
    from app.task_intelligence.schemas import EffortEstimateDraft, PlanSchema, TaskDraft
    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("PLANNING", 70),
        ("PLANNING", 80),
        ("PLANNING", 90),
    ]
    mock_session.execute.return_value = mock_result
    mock_repo = MagicMock()
    mock_repo.session = mock_session

    service = PlanService(uow_factory=MagicMock())

    plan = PlanSchema(
        plan_name="Test Calibration Plan",
        tasks=[
            # 1. Historical median should apply (PLANNING median = 80)
            TaskDraft(
                temporary_id="T1",
                title="Planning Task",
                work_category="PLANNING",
                estimated_duration_minutes=120,
            ),
            # 2. Manual override should be preserved (confidence 1.0, basis='human_override')
            TaskDraft(
                temporary_id="T2",
                title="Override Task",
                work_category="PLANNING",
                estimated_duration_minutes=250,
                effort_estimate=EffortEstimateDraft(
                    value=250.0,
                    unit="minutes",
                    basis="human_override",
                    confidence=1.0,
                    is_manual_override=True,
                ),
            ),
            # 3. Category heuristic fallback (ROOM_PREPARATION default = 90)
            TaskDraft(
                temporary_id="T3",
                title="Room Task",
                work_category="ROOM_PREPARATION",
                estimated_duration_minutes=60,
            ),
            # 4. Global fallback (UNKNOWN_CATEGORY default = 60)
            TaskDraft(
                temporary_id="T4",
                title="Unknown Task",
                work_category="UNKNOWN_CATEGORY",
                estimated_duration_minutes=15,
            ),
        ],
    )

    calibrated = await service.calibrate_plan_effort("tenant-alpha", plan, mock_repo)

    # T1: calibrated to historical median
    t1 = calibrated.tasks[0]
    assert t1.estimated_duration_minutes == 80
    assert t1.effort_estimate is not None
    assert t1.effort_estimate.basis == "historical_median"
    assert t1.effort_estimate.confidence == 0.85
    assert t1.effort_estimate.is_manual_override is False

    # T2: preserved manual override
    t2 = calibrated.tasks[1]
    assert t2.estimated_duration_minutes == 250
    assert t2.effort_estimate is not None
    assert t2.effort_estimate.basis == "human_override"
    assert t2.effort_estimate.confidence == 1.0
    assert t2.effort_estimate.is_manual_override is True

    # T3: category heuristic
    t3 = calibrated.tasks[2]
    assert t3.estimated_duration_minutes == 90
    assert t3.effort_estimate is not None
    assert t3.effort_estimate.basis == "category_heuristic:ROOM_PREPARATION"
    assert t3.effort_estimate.confidence == 0.7

    # T4: default fallback
    t4 = calibrated.tasks[3]
    assert t4.estimated_duration_minutes == 60
    assert t4.effort_estimate is not None
    assert t4.effort_estimate.basis == "default_fallback"
    assert t4.effort_estimate.confidence == 0.5


@pytest.mark.asyncio
async def test_calibrate_plan_effort_tenant_isolation_fallback() -> None:
    """Tenant B with no history falls back to category heuristic, ignoring Tenant A's median."""
    from app.task_intelligence.schemas import PlanSchema, TaskDraft
    from app.task_intelligence.service import PlanService

    mock_session = AsyncMock()
    mock_result = MagicMock()
    # Tenant Beta has zero completed tasks
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result
    mock_repo = MagicMock()
    mock_repo.session = mock_session

    service = PlanService(uow_factory=MagicMock())

    plan = PlanSchema(
        plan_name="Tenant B Plan",
        tasks=[
            TaskDraft(
                temporary_id="T1",
                title="Planning Task",
                work_category="PLANNING",
                estimated_duration_minutes=30,
            )
        ],
    )

    calibrated = await service.calibrate_plan_effort("tenant-beta", plan, mock_repo)
    t1 = calibrated.tasks[0]
    # Falls back to category heuristic (PLANNING default = 120m), not any other tenant's data
    assert t1.estimated_duration_minutes == 120
    assert t1.effort_estimate is not None
    assert t1.effort_estimate.basis == "category_heuristic:PLANNING"
    assert t1.effort_estimate.confidence == 0.7

