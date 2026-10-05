"""Unit tests for effort estimation, priority engine, risk detection, and task progress."""

import pytest
from pydantic import ValidationError

from app.task_intelligence.estimators import (
    HeuristicEffortEstimator,
)
from app.task_intelligence.graph import CriticalPathCalculator, TaskDependencyGraph
from app.task_intelligence.priority import (
    PriorityEngine,
    PriorityFactors,
    PriorityWeights,
)
from app.task_intelligence.progress import (
    InvalidTaskProgressTransitionError,
    TaskProgressStatus,
    TaskProgressUpdate,
    assert_valid_task_progress_transition,
)
from app.task_intelligence.risks import (
    DeterministicRiskDetector,
    RiskCategory,
    RiskSeverity,
)
from app.task_intelligence.schemas import TaskPriority

# --- Effort Estimation Tests ---


class MockHistoricalProvider:
    def get_historical_median_duration(
        self,
        work_category: str,
        task_type: str | None = None,
    ) -> float | None:
        if work_category == "QUESTION_PAPER_READINESS":
            return 210.0
        return None


def test_effort_estimator_human_override() -> None:
    estimator = HeuristicEffortEstimator()
    res = estimator.estimate("Write test questions", work_category="PLANNING", human_override=95.0)
    assert res.value == 95.0
    assert res.unit == "minutes"
    assert res.basis == "human_override"
    assert res.confidence == 1.0
    assert res.is_manual_override is True


def test_effort_estimator_historical_median() -> None:
    provider = MockHistoricalProvider()
    estimator = HeuristicEffortEstimator(historical_provider=provider)
    res = estimator.estimate("Review drafts", work_category="QUESTION_PAPER_READINESS")
    assert res.value == 210.0
    assert res.basis == "historical_median"
    assert res.confidence == 0.85
    assert res.is_manual_override is False


def test_effort_estimator_category_heuristic() -> None:
    estimator = HeuristicEffortEstimator()
    res = estimator.estimate("Prepare rooms", work_category="ROOM_PREPARATION")
    assert res.value == 90.0
    assert res.basis == "category_heuristic:ROOM_PREPARATION"
    assert res.confidence == 0.7
    assert res.is_manual_override is False


def test_effort_estimator_default_fallback() -> None:
    estimator = HeuristicEffortEstimator(default_duration_minutes=45.0)
    res = estimator.estimate("Unknown Task", work_category="UNKNOWN_CAT")
    assert res.value == 45.0
    assert res.basis == "default_fallback"
    assert res.confidence == 0.5


# --- Priority Engine Tests ---


def test_priority_engine_calculation() -> None:
    # High urgency (1.0), high impact (1.0), high criticality (1.0), normal policy (0.5)
    factors = PriorityFactors(
        urgency_score=1.0,
        impact_score=1.0,
        dependency_criticality_score=1.0,
        policy_contribution_score=0.5,
    )
    # Caller provides explicit normalized policy weights
    policy = PriorityWeights(
        urgency=0.25,
        impact=0.25,
        dependency_criticality=0.25,
        policy_contribution=0.25,
    )
    result = PriorityEngine.calculate(factors, weights=policy)
    # composite = (1.0*0.25 + 1.0*0.25 + 1.0*0.25 + 0.5*0.25) = 0.875
    assert result.score == 0.875
    assert result.priority == TaskPriority.CRITICAL
    assert result.is_overridden is False
    assert result.human_override is None


def test_priority_engine_configurable_weights() -> None:
    factors = PriorityFactors(
        urgency_score=0.9,
        impact_score=0.1,
        dependency_criticality_score=0.1,
        policy_contribution_score=0.1,
    )
    # Give urgency 90% weight
    weights = PriorityWeights(
        urgency=0.9,
        impact=0.033,
        dependency_criticality=0.033,
        policy_contribution=0.034,
    )
    result = PriorityEngine.calculate(factors, weights=weights)
    assert result.score > 0.75
    assert result.priority == TaskPriority.CRITICAL


def test_priority_engine_no_hardcoded_defaults() -> None:
    factors = PriorityFactors()
    # PriorityEngine has no DEFAULT_WEIGHTS attribute
    assert not hasattr(PriorityEngine, "DEFAULT_WEIGHTS")
    # Calling calculate without weights must raise TypeError
    with pytest.raises(TypeError):
        PriorityEngine.calculate(factors)  # type: ignore[call-arg]


def test_priority_weights_validation() -> None:
    # Valid exact sum == 1.0
    w = PriorityWeights(
        urgency=0.4,
        impact=0.3,
        dependency_criticality=0.2,
        policy_contribution=0.1,
    )
    assert w.urgency == 0.4

    # Negative weight rejected
    with pytest.raises(ValidationError):
        PriorityWeights(
            urgency=-0.1,
            impact=0.5,
            dependency_criticality=0.3,
            policy_contribution=0.3,
        )

    # Zero total rejected
    with pytest.raises(ValidationError):
        PriorityWeights(
            urgency=0.0,
            impact=0.0,
            dependency_criticality=0.0,
            policy_contribution=0.0,
        )

    # Sum not equal to 1.0 rejected
    with pytest.raises(ValidationError):
        PriorityWeights(
            urgency=0.2,
            impact=0.2,
            dependency_criticality=0.2,
            policy_contribution=0.2,
        )


def test_priority_engine_human_override_control_path() -> None:
    factors = PriorityFactors(
        urgency_score=1.0,
        impact_score=1.0,
        dependency_criticality_score=1.0,
        policy_contribution_score=1.0,
    )
    policy = PriorityWeights(
        urgency=0.25,
        impact=0.25,
        dependency_criticality=0.25,
        policy_contribution=0.25,
    )
    # Even though composite score is 1.0 (CRITICAL), human override forces LOW
    result = PriorityEngine.calculate(
        factors=factors,
        weights=policy,
        human_override=TaskPriority.LOW,
    )
    assert result.score == 1.0  # Audit retains computed score
    assert result.priority == TaskPriority.LOW  # Control path overrides priority
    assert result.is_overridden is True
    assert result.human_override == TaskPriority.LOW


# --- Risk Detection Tests ---


def test_risk_detector_critical_path_slippage() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T_crit", duration_minutes=60)
    graph.add_task("T_slack", duration_minutes=20)
    graph.add_task("End", duration_minutes=10)
    graph.add_dependency("T_crit", "End")
    graph.add_dependency("T_slack", "End")

    cpm = CriticalPathCalculator.calculate(graph)

    risks = DeterministicRiskDetector.detect_critical_path_slippage(
        cpm_result=cpm,
        delayed_task_ids={"T_crit", "T_slack"},
    )
    assert len(risks) == 2
    crit_risk = next(r for r in risks if r.task_id == "T_crit")
    assert crit_risk.category == RiskCategory.CRITICAL_PATH_SLIPPAGE
    assert crit_risk.severity == RiskSeverity.CRITICAL

    slack_risk = next(r for r in risks if r.task_id == "T_slack")
    assert slack_risk.category == RiskCategory.DEPENDENCY_SLIPPAGE
    assert slack_risk.severity == RiskSeverity.MEDIUM


def test_risk_detector_resource_capacity_unsupported() -> None:
    risk = DeterministicRiskDetector.evaluate_resource_capacity()
    assert risk.category == RiskCategory.RESOURCE_CAPACITY_UNSUPPORTED
    assert risk.is_evaluable is False


# --- Task Progress Tests ---


def test_task_progress_transitions_valid() -> None:
    assert_valid_task_progress_transition(TaskProgressStatus.TODO, TaskProgressStatus.IN_PROGRESS)
    assert_valid_task_progress_transition(
        TaskProgressStatus.IN_PROGRESS, TaskProgressStatus.COMPLETED
    )
    assert_valid_task_progress_transition(
        TaskProgressStatus.IN_PROGRESS, TaskProgressStatus.BLOCKED
    )
    assert_valid_task_progress_transition(
        TaskProgressStatus.BLOCKED, TaskProgressStatus.IN_PROGRESS
    )


def test_task_progress_transitions_invalid() -> None:
    with pytest.raises(InvalidTaskProgressTransitionError):
        # Completed task cannot directly go to TODO without reopening to IN_PROGRESS
        assert_valid_task_progress_transition(TaskProgressStatus.COMPLETED, TaskProgressStatus.TODO)


def test_task_progress_update_validation() -> None:
    # Completed requires 100%
    with pytest.raises(ValidationError):
        TaskProgressUpdate(status=TaskProgressStatus.COMPLETED, progress_percentage=80)

    # Blocked requires reason
    with pytest.raises(ValidationError):
        TaskProgressUpdate(status=TaskProgressStatus.BLOCKED, blocked_reason="")

    valid_blocked = TaskProgressUpdate(
        status=TaskProgressStatus.BLOCKED,
        blocked_reason="Waiting on question paper seal release",
    )
    assert valid_blocked.status == TaskProgressStatus.BLOCKED
    assert valid_blocked.blocked_reason == "Waiting on question paper seal release"
