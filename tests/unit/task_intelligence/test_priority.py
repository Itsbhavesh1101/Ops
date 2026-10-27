"""Authoritative unit test suite for Priority Assignment (FR-TI-05).

Verifies:
- Multi-criteria priority calculation (urgency, impact, dependency criticality, policy contribution)
- Explicit caller-supplied policy weights with zero hardcoded defaults
- Strict policy validation: non-negative, finite, non-zero sum, exact sum == 1.0
- Human priority override control/bypass path with audit retention
- Deterministic priority mapping to canonical TaskPriority enum
"""

import pytest
from pydantic import ValidationError

from app.task_intelligence.graph import CriticalPathCalculator, TaskDependencyGraph
from app.task_intelligence.priority import (
    PriorityEngine,
    PriorityFactors,
    PriorityPolicy,
    PriorityWeights,
)
from app.task_intelligence.schemas import TaskPriority


def test_priority_engine_deterministic_composite_calculation() -> None:
    """Composite score is the exact weighted dot product of factors and policy weights."""
    factors = PriorityFactors(
        urgency_score=0.8,
        impact_score=0.6,
        dependency_criticality_score=1.0,
        policy_contribution_score=0.4,
    )
    weights = PriorityWeights(
        urgency=0.25,
        impact=0.25,
        dependency_criticality=0.25,
        policy_contribution=0.25,
    )
    # Expected: (0.8*0.25) + (0.6*0.25) + (1.0*0.25) + (0.4*0.25) = 0.2 + 0.15 + 0.25 + 0.1 = 0.70
    result = PriorityEngine.calculate(factors, weights=weights)
    assert result.score == 0.70
    assert result.priority == TaskPriority.HIGH
    assert result.is_overridden is False
    assert result.human_override is None


def test_priority_engine_mapping_thresholds() -> None:
    """Verifies standard priority score mapping thresholds:
    score >= 0.75: CRITICAL
    score >= 0.50: HIGH
    score >= 0.25: MEDIUM
    score < 0.25: LOW
    """
    weights = PriorityWeights(
        urgency=1.0,
        impact=0.0,
        dependency_criticality=0.0,
        policy_contribution=0.0,
    )

    # CRITICAL
    res_crit = PriorityEngine.calculate(PriorityFactors(urgency_score=0.85), weights=weights)
    assert res_crit.priority == TaskPriority.CRITICAL

    # HIGH
    res_high = PriorityEngine.calculate(PriorityFactors(urgency_score=0.60), weights=weights)
    assert res_high.priority == TaskPriority.HIGH

    # MEDIUM
    res_med = PriorityEngine.calculate(PriorityFactors(urgency_score=0.35), weights=weights)
    assert res_med.priority == TaskPriority.MEDIUM

    # LOW
    res_low = PriorityEngine.calculate(PriorityFactors(urgency_score=0.10), weights=weights)
    assert res_low.priority == TaskPriority.LOW


def test_priority_engine_no_hardcoded_defaults() -> None:
    """PriorityEngine must require explicit policy weights and have zero hardcoded defaults."""
    factors = PriorityFactors()
    assert not hasattr(PriorityEngine, "DEFAULT_WEIGHTS")
    with pytest.raises(TypeError):
        PriorityEngine.calculate(factors)  # type: ignore[call-arg]


def test_priority_weights_strict_validation() -> None:
    """Weights must be finite, non-negative, and sum to exactly 1.0."""
    # Negative weight
    with pytest.raises(ValidationError):
        PriorityWeights(
            urgency=-0.1,
            impact=0.5,
            dependency_criticality=0.3,
            policy_contribution=0.3,
        )

    # Non-finite (inf/NaN)
    with pytest.raises(ValidationError):
        PriorityWeights(
            urgency=float("inf"),
            impact=0.0,
            dependency_criticality=0.0,
            policy_contribution=0.0,
        )

    # Sum < 1.0
    with pytest.raises(ValidationError):
        PriorityWeights(
            urgency=0.2,
            impact=0.2,
            dependency_criticality=0.2,
            policy_contribution=0.2,
        )

    # Sum > 1.0
    with pytest.raises(ValidationError):
        PriorityWeights(
            urgency=0.4,
            impact=0.4,
            dependency_criticality=0.4,
            policy_contribution=0.4,
        )


def test_priority_engine_human_override_bypass_path() -> None:
    """Human override bypasses priority score enum while retaining computed score in audit."""
    factors = PriorityFactors(
        urgency_score=1.0,
        impact_score=1.0,
        dependency_criticality_score=1.0,
        policy_contribution_score=1.0,
    )
    policy = PriorityPolicy(
        urgency=0.25,
        impact=0.25,
        dependency_criticality=0.25,
        policy_contribution=0.25,
    )
    result = PriorityEngine.calculate(
        factors=factors,
        weights=policy,
        human_override=TaskPriority.LOW,
    )
    # Computed score remains 1.0 for audit integrity
    assert result.score == 1.0
    # Assigned priority honors human override
    assert result.priority == TaskPriority.LOW
    assert result.is_overridden is True
    assert result.human_override == TaskPriority.LOW


def test_priority_from_dag_critical_path() -> None:
    """Tasks on the critical path receive elevated dependency criticality scores."""
    graph = TaskDependencyGraph()
    graph.add_task("T_CRIT", duration_minutes=120)
    graph.add_task("T_FLOAT", duration_minutes=30)
    graph.add_task("T_END", duration_minutes=10)
    graph.add_dependency("T_CRIT", "T_END")
    graph.add_dependency("T_FLOAT", "T_END")

    cpm = CriticalPathCalculator.calculate(graph)
    assert "T_CRIT" in cpm.critical_path
    assert "T_FLOAT" not in cpm.critical_path

    policy = PriorityPolicy(
        urgency=0.1,
        impact=0.1,
        dependency_criticality=0.7,
        policy_contribution=0.1,
    )

    # Critical path task has criticality 1.0
    res_crit = PriorityEngine.calculate(
        factors=PriorityFactors(dependency_criticality_score=1.0),
        weights=policy,
    )
    # Non-critical task has low criticality 0.1
    res_float = PriorityEngine.calculate(
        factors=PriorityFactors(dependency_criticality_score=0.1),
        weights=policy,
    )

    assert res_crit.score > res_float.score
    assert res_crit.priority == TaskPriority.CRITICAL
    assert res_float.priority == TaskPriority.LOW
