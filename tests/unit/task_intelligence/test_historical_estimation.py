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
