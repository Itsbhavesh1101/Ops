"""Comprehensive unit tests for Risk & Blocker Detection (FR-TI-07).

Tests:
- Critical-path delay vs float consumption detection
- Declared task blocker detection and dependency chain propagation
- Authoritative resource capacity shortfall evaluation
- Explicit not-evaluable status when authoritative resource data is missing
- Risk recalculation following task progress transitions
"""

from typing import Any
from uuid import uuid4

from app.task_intelligence.graph import CriticalPathCalculator, TaskDependencyGraph
from app.task_intelligence.risks import (
    DeterministicRiskDetector,
    RiskCategory,
    RiskSeverity,
)


class DummyTask:
    def __init__(
        self,
        temporary_id: str,
        status: str = "TODO",
        blocked_reason: str | None = None,
    ) -> None:
        self.id = uuid4()
        self.temporary_id = temporary_id
        self.status = status
        self.blocked_reason = blocked_reason


class DummyDependency:
    def __init__(self, predecessor_id: Any, successor_id: Any) -> None:
        self.predecessor_id = predecessor_id
        self.successor_id = successor_id


def test_critical_path_vs_slack_delay_detection() -> None:
    """Delay on critical path is CRITICAL; delay on slack task is MEDIUM float consumption."""
    graph = TaskDependencyGraph()
    graph.add_task("T_CRIT", duration_minutes=120)
    graph.add_task("T_FLOAT", duration_minutes=30)
    graph.add_task("T_END", duration_minutes=15)
    graph.add_dependency("T_CRIT", "T_END")
    graph.add_dependency("T_FLOAT", "T_END")

    cpm = CriticalPathCalculator.calculate(graph)

    risks = DeterministicRiskDetector.detect_critical_path_slippage(
        cpm_result=cpm,
        delayed_task_ids={"T_CRIT", "T_FLOAT"},
    )
    assert len(risks) == 2

    crit_risk = next(r for r in risks if r.task_id == "T_CRIT")
    assert crit_risk.category == RiskCategory.CRITICAL_PATH_SLIPPAGE
    assert crit_risk.severity == RiskSeverity.CRITICAL
    assert crit_risk.is_evaluable is True
    assert "critical path (zero slack)" in crit_risk.description

    float_risk = next(r for r in risks if r.task_id == "T_FLOAT")
    assert float_risk.category == RiskCategory.DEPENDENCY_SLIPPAGE
    assert float_risk.severity == RiskSeverity.MEDIUM
    assert float_risk.is_evaluable is True
    assert "total slack" in float_risk.description


def test_declared_blocker_and_dependency_propagation() -> None:
    """Declared blockers are detected and propagated down dependency chains."""
    t1 = DummyTask("T1", status="BLOCKED", blocked_reason="Awaiting question paper moderation")
    t2 = DummyTask("T2", status="TODO")
    t3 = DummyTask("T3", status="TODO")

    dep1 = DummyDependency(t1.id, t2.id)
    dep2 = DummyDependency(t2.id, t3.id)

    risks = DeterministicRiskDetector.detect_blocked_dependencies(
        tasks=[t1, t2, t3],
        dependencies=[dep1, dep2],
    )

    # T1 is directly blocked; T2 is waiting on blocked T1
    assert len(risks) == 2

    direct_risk = next(r for r in risks if r.task_id == "T1")
    assert direct_risk.category == RiskCategory.DECLARED_BLOCKER
    assert direct_risk.severity == RiskSeverity.HIGH
    assert "Awaiting question paper moderation" in direct_risk.description

    downstream_risk = next(r for r in risks if r.task_id == "T2")
    assert downstream_risk.category == RiskCategory.DEPENDENCY_SLIPPAGE
    assert downstream_risk.severity == RiskSeverity.HIGH
    assert "waiting on predecessor 'T1' which is BLOCKED" in downstream_risk.description


def test_authoritative_resource_capacity_shortfall() -> None:
    """Overloaded resources generate RESOURCE_CAPACITY_SHORTFALL risks."""
    required = {
        "ROOM:EXAM_HALL_A": 1,
        "INVIGILATOR": 10,
        "PROJECTOR": 2,
    }
    available = {
        "ROOM:EXAM_HALL_A": 1,
        "INVIGILATOR": 6,  # Shortfall: 10 required, 6 available
        "PROJECTOR": 1,    # Shortfall: 2 required, 1 available
    }

    risks = DeterministicRiskDetector.evaluate_resource_shortfalls(
        required_capacities=required,
        available_capacities=available,
    )

    assert len(risks) == 2
    invig_risk = next(r for r in risks if r.task_id == "RESOURCE:INVIGILATOR")
    assert invig_risk.category == RiskCategory.RESOURCE_CAPACITY_SHORTFALL
    assert invig_risk.severity == RiskSeverity.HIGH
    assert invig_risk.is_evaluable is True
    assert "requires 10 units but only 6 are available" in invig_risk.description

    proj_risk = next(r for r in risks if r.task_id == "RESOURCE:PROJECTOR")
    assert proj_risk.category == RiskCategory.RESOURCE_CAPACITY_SHORTFALL
    assert proj_risk.severity == RiskSeverity.HIGH
    assert "requires 2 units but only 1 are available" in proj_risk.description


def test_sufficient_resource_capacity_generates_zero_risks() -> None:
    """When available capacity meets or exceeds demand, zero risks are emitted."""
    required = {"ROOM:101": 1, "PROCTOR": 4}
    available = {"ROOM:101": 2, "PROCTOR": 8}

    risks = DeterministicRiskDetector.evaluate_resource_shortfalls(
        required_capacities=required,
        available_capacities=available,
    )
    assert len(risks) == 0


def test_missing_resource_data_returns_explicit_unevaluable_status() -> None:
    """When authoritative resource data is unavailable, return explicit unevaluable status.

    Never fabricates data.
    """
    risk = DeterministicRiskDetector.evaluate_resource_capacity()
    assert risk.category == RiskCategory.RESOURCE_CAPACITY_UNSUPPORTED
    assert risk.is_evaluable is False
    assert "cannot be evaluated without authoritative" in risk.description


def test_risk_recalculation_after_progress_update() -> None:
    """Risks update dynamically when a task transitions from BLOCKED to COMPLETED."""
    t1 = DummyTask("T1", status="BLOCKED", blocked_reason="Security seal missing")
    t2 = DummyTask("T2", status="TODO")
    dep = DummyDependency(t1.id, t2.id)

    # Initial state: risks present
    initial_risks = DeterministicRiskDetector.detect_blocked_dependencies(
        tasks=[t1, t2],
        dependencies=[dep],
    )
    assert len(initial_risks) == 2

    # Task progress update: unblocked and completed
    t1.status = "COMPLETED"
    t1.blocked_reason = None

    # Recalculated state: zero blocker risks
    updated_risks = DeterministicRiskDetector.detect_blocked_dependencies(
        tasks=[t1, t2],
        dependencies=[dep],
    )
    assert len(updated_risks) == 0
