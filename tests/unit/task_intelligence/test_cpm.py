"""Unit tests for Critical Path Method (CPM) calculator with hand-computed fixtures."""

import pytest

from app.task_intelligence.graph import (
    CriticalPathCalculator,
    GraphCycleError,
    TaskDependencyGraph,
)


def test_cpm_single_task() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T1", duration_minutes=45)

    result = CriticalPathCalculator.calculate(graph)
    assert result.project_duration_minutes == 45
    assert result.critical_path == ["T1"]
    node = result.schedule["T1"]
    assert node.early_start == 0
    assert node.early_finish == 45
    assert node.late_start == 0
    assert node.late_finish == 45
    assert node.total_slack == 0
    assert node.is_critical is True


def test_cpm_linear_chain() -> None:
    # T1 (10m) -> T2 (20m) -> T3 (15m)
    # Total duration = 45m
    graph = TaskDependencyGraph()
    graph.add_task("T1", duration_minutes=10)
    graph.add_task("T2", duration_minutes=20)
    graph.add_task("T3", duration_minutes=15)
    graph.add_dependency("T1", "T2")
    graph.add_dependency("T2", "T3")

    result = CriticalPathCalculator.calculate(graph)
    assert result.project_duration_minutes == 45
    assert result.critical_path == ["T1", "T2", "T3"]

    s = result.schedule
    assert (s["T1"].early_start, s["T1"].early_finish, s["T1"].total_slack) == (0, 10, 0)
    assert (s["T2"].early_start, s["T2"].early_finish, s["T2"].total_slack) == (10, 30, 0)
    assert (s["T3"].early_start, s["T3"].early_finish, s["T3"].total_slack) == (30, 45, 0)


def test_cpm_diamond_with_positive_slack() -> None:
    # Classic diamond:
    # S (10m) -> A (20m) -> T (10m) => path length = 10 + 20 + 10 = 40m
    # S (10m) -> B (40m) -> T (10m) => path length = 10 + 40 + 10 = 60m (Critical)
    # Project duration = 60m
    # For A: ES=10, EF=30. T must start at 50 (since B finishes at 10+40=50).
    # So A's LF=50, LS=30. Total slack = LS - ES = 30 - 10 = 20m.
    graph = TaskDependencyGraph()
    graph.add_task("S", duration_minutes=10)
    graph.add_task("A", duration_minutes=20)
    graph.add_task("B", duration_minutes=40)
    graph.add_task("T", duration_minutes=10)

    graph.add_dependency("S", "A")
    graph.add_dependency("S", "B")
    graph.add_dependency("A", "T")
    graph.add_dependency("B", "T")

    result = CriticalPathCalculator.calculate(graph)
    assert result.project_duration_minutes == 60
    assert result.critical_path == ["S", "B", "T"]

    s = result.schedule
    # S is critical
    assert s["S"].is_critical is True
    assert s["S"].total_slack == 0

    # B is critical
    assert s["B"].is_critical is True
    assert s["B"].total_slack == 0
    assert (s["B"].early_start, s["B"].early_finish) == (10, 50)
    assert (s["B"].late_start, s["B"].late_finish) == (10, 50)

    # T is critical
    assert s["T"].is_critical is True
    assert s["T"].total_slack == 0
    assert (s["T"].early_start, s["T"].early_finish) == (50, 60)

    # A is NOT critical, has 20 minutes slack
    assert s["A"].is_critical is False
    assert s["A"].total_slack == 20
    assert (s["A"].early_start, s["A"].early_finish) == (10, 30)
    assert (s["A"].late_start, s["A"].late_finish) == (30, 50)
    assert s["A"].free_slack == 20


def test_cpm_multiple_critical_paths() -> None:
    # S (10m) -> Branch1 (30m) -> T (10m) => 50m
    # S (10m) -> Branch2 (30m) -> T (10m) => 50m
    # Both paths are critical!
    graph = TaskDependencyGraph()
    graph.add_task("S", duration_minutes=10)
    graph.add_task("B1", duration_minutes=30)
    graph.add_task("B2", duration_minutes=30)
    graph.add_task("T", duration_minutes=10)

    graph.add_dependency("S", "B1")
    graph.add_dependency("S", "B2")
    graph.add_dependency("B1", "T")
    graph.add_dependency("B2", "T")

    result = CriticalPathCalculator.calculate(graph)
    assert result.project_duration_minutes == 50
    assert len(result.critical_paths) == 2
    assert ["S", "B1", "T"] in result.critical_paths
    assert ["S", "B2", "T"] in result.critical_paths

    for tid in ["S", "B1", "B2", "T"]:
        assert result.schedule[tid].is_critical is True
        assert result.schedule[tid].total_slack == 0


def test_cpm_zero_duration_milestone_task() -> None:
    # T1 (30m) -> M1 (0m milestone) -> T2 (20m)
    # Total duration = 50m
    graph = TaskDependencyGraph()
    graph.add_task("T1", duration_minutes=30)
    graph.add_task("M1", duration_minutes=0)
    graph.add_task("T2", duration_minutes=20)
    graph.add_dependency("T1", "M1")
    graph.add_dependency("M1", "T2")

    result = CriticalPathCalculator.calculate(graph)
    assert result.project_duration_minutes == 50
    m = result.schedule["M1"]
    assert m.duration_minutes == 0
    assert m.early_start == 30
    assert m.early_finish == 30
    assert m.late_start == 30
    assert m.late_finish == 30
    assert m.total_slack == 0
    assert m.is_critical is True


def test_cpm_raises_on_cycle() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T1", duration_minutes=10)
    graph.add_task("T2", duration_minutes=10)
    graph.add_dependency("T1", "T2")
    graph.add_dependency("T2", "T1")

    with pytest.raises(GraphCycleError):
        CriticalPathCalculator.calculate(graph)
