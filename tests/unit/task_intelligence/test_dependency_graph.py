"""Unit tests for TaskDependencyGraph using NetworkX."""

import pytest

from app.task_intelligence.graph import (
    DuplicateDependencyError,
    GraphCycleError,
    SelfDependencyError,
    TaskDependencyGraph,
    UnknownTaskReferenceError,
)


def test_single_task() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T1", duration_minutes=60)
    assert graph.node_count == 1
    assert graph.edge_count == 0
    assert graph.topological_sort() == ["T1"]
    assert graph.get_roots() == ["T1"]
    assert graph.get_leaves() == ["T1"]
    cycle_res = graph.detect_cycle()
    assert not cycle_res.has_cycle
    assert cycle_res.cycle_path is None


def test_simple_chain() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T1", duration_minutes=30)
    graph.add_task("T2", duration_minutes=45)
    graph.add_task("T3", duration_minutes=60)
    graph.add_dependency("T1", "T2")
    graph.add_dependency("T2", "T3")

    assert graph.node_count == 3
    assert graph.edge_count == 2
    assert graph.topological_sort() == ["T1", "T2", "T3"]
    assert graph.get_roots() == ["T1"]
    assert graph.get_leaves() == ["T3"]
    assert graph.get_successors("T1") == ["T2"]
    assert graph.get_predecessors("T3") == ["T2"]


def test_diamond_graph() -> None:
    # T1 -> T2 -> T4
    # T1 -> T3 -> T4
    graph = TaskDependencyGraph()
    for tid in ["T1", "T2", "T3", "T4"]:
        graph.add_task(tid)
    graph.add_dependency("T1", "T2")
    graph.add_dependency("T1", "T3")
    graph.add_dependency("T2", "T4")
    graph.add_dependency("T3", "T4")

    topo = graph.topological_sort()
    assert topo.index("T1") < topo.index("T2") < topo.index("T4")
    assert topo.index("T1") < topo.index("T3") < topo.index("T4")
    assert graph.get_roots() == ["T1"]
    assert graph.get_leaves() == ["T4"]


def test_multiple_roots_and_leaves() -> None:
    # R1 -> M1 -> L1
    # R2 -> M1 -> L2
    graph = TaskDependencyGraph()
    for tid in ["R1", "R2", "M1", "L1", "L2"]:
        graph.add_task(tid)
    graph.add_dependency("R1", "M1")
    graph.add_dependency("R2", "M1")
    graph.add_dependency("M1", "L1")
    graph.add_dependency("M1", "L2")

    assert graph.get_roots() == ["R1", "R2"]
    assert graph.get_leaves() == ["L1", "L2"]
    topo = graph.topological_sort()
    assert topo.index("R1") < topo.index("M1")
    assert topo.index("R2") < topo.index("M1")
    assert topo.index("M1") < topo.index("L1")
    assert topo.index("M1") < topo.index("L2")


def test_parallel_branches() -> None:
    # T1 -> T2
    # T3 -> T4
    graph = TaskDependencyGraph()
    for tid in ["T1", "T2", "T3", "T4"]:
        graph.add_task(tid)
    graph.add_dependency("T1", "T2")
    graph.add_dependency("T3", "T4")

    assert graph.get_roots() == ["T1", "T3"]
    assert graph.get_leaves() == ["T2", "T4"]
    topo = graph.topological_sort()
    assert topo.index("T1") < topo.index("T2")
    assert topo.index("T3") < topo.index("T4")


def test_self_edge_rejection() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T1")
    with pytest.raises(SelfDependencyError, match="cannot depend on itself"):
        graph.add_dependency("T1", "T1")


def test_unknown_reference_rejection() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T1")
    with pytest.raises(UnknownTaskReferenceError, match="unknown task 'T2' as successor"):
        graph.add_dependency("T1", "T2")

    with pytest.raises(UnknownTaskReferenceError, match="unknown task 'T0' as predecessor"):
        graph.add_dependency("T0", "T1")


def test_duplicate_edge_rejection() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T1")
    graph.add_task("T2")
    graph.add_dependency("T1", "T2")
    with pytest.raises(DuplicateDependencyError, match="already exists"):
        graph.add_dependency("T1", "T2")


def test_two_node_cycle() -> None:
    graph = TaskDependencyGraph()
    graph.add_task("T1")
    graph.add_task("T2")
    graph.add_dependency("T1", "T2")
    graph.add_dependency("T2", "T1")

    res = graph.detect_cycle()
    assert res.has_cycle is True
    assert res.cycle_path == ["T1", "T2", "T1"] or res.cycle_path == ["T2", "T1", "T2"]

    with pytest.raises(GraphCycleError) as exc_info:
        graph.topological_sort()
    assert len(exc_info.value.cycle_path) == 3


def test_multi_node_cycle_and_explicit_path() -> None:
    # A -> B -> C -> D -> B (cycle in B-C-D)
    graph = TaskDependencyGraph()
    for tid in ["A", "B", "C", "D"]:
        graph.add_task(tid)
    graph.add_dependency("A", "B")
    graph.add_dependency("B", "C")
    graph.add_dependency("C", "D")
    graph.add_dependency("D", "B")

    res = graph.detect_cycle()
    assert res.has_cycle is True
    # The cycle path should be a closed loop: e.g., ['B', 'C', 'D', 'B']
    assert res.cycle_path is not None
    assert res.cycle_path[0] == res.cycle_path[-1]
    assert set(res.cycle_path) == {"B", "C", "D"}

    with pytest.raises(GraphCycleError) as exc_info:
        graph.topological_sort()
    assert exc_info.value.cycle_path[0] == exc_info.value.cycle_path[-1]


def test_stable_deterministic_topological_sort() -> None:
    # Nodes added in reverse order or arbitrary order
    graph1 = TaskDependencyGraph()
    for tid in ["D", "C", "B", "A"]:
        graph1.add_task(tid)
    graph1.add_dependency("A", "B")
    graph1.add_dependency("B", "D")
    graph1.add_dependency("A", "C")
    graph1.add_dependency("C", "D")

    graph2 = TaskDependencyGraph()
    for tid in ["A", "B", "C", "D"]:
        graph2.add_task(tid)
    graph2.add_dependency("A", "C")
    graph2.add_dependency("A", "B")
    graph2.add_dependency("C", "D")
    graph2.add_dependency("B", "D")

    # Lexicographical sort guarantees identical tie breaking
    assert graph1.topological_sort() == graph2.topological_sort()
    assert graph1.topological_sort() == ["A", "B", "C", "D"]
