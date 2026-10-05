"""Deterministic task dependency graph service implemented using NetworkX.

Provides cycle detection, explicit cycle path extraction, topological sorting,
and structural dependency queries.
"""

from typing import Any

import networkx as nx

from app.task_intelligence.graph.exceptions import (
    DuplicateDependencyError,
    GraphCycleError,
    SelfDependencyError,
    UnknownTaskReferenceError,
)
from app.task_intelligence.graph.models import CycleDetectionResult


class TaskDependencyGraph:
    """Directed dependency graph for WBS task items."""

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()

    @property
    def node_count(self) -> int:
        """Return total number of task nodes in the graph."""
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        """Return total number of dependency edges in the graph."""
        return self._graph.number_of_edges()

    def add_task(
        self,
        task_id: str,
        duration_minutes: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a task node to the graph.

        Args:
            task_id: Unique task identifier within the plan.
            duration_minutes: Normalized non-negative task duration in minutes.
            metadata: Optional arbitrary task metadata.
        """
        if duration_minutes < 0:
            raise ValueError(f"Task duration cannot be negative: {duration_minutes}")

        self._graph.add_node(
            task_id,
            duration=duration_minutes,
            metadata=metadata or {},
        )

    def add_dependency(
        self,
        predecessor_id: str,
        successor_id: str,
        lag_minutes: int = 0,
    ) -> None:
        """Add a directed predecessor -> successor dependency edge.

        Args:
            predecessor_id: Task that must complete before successor can start.
            successor_id: Dependent task.
            lag_minutes: Required non-negative buffer time between tasks.

        Raises:
            SelfDependencyError: If predecessor equals successor.
            UnknownTaskReferenceError: If predecessor or successor does not exist in graph.
            DuplicateDependencyError: If the dependency edge already exists.
            ValueError: If lag_minutes is negative.
        """
        if predecessor_id == successor_id:
            raise SelfDependencyError(predecessor_id)

        if predecessor_id not in self._graph:
            raise UnknownTaskReferenceError(predecessor_id, "predecessor")

        if successor_id not in self._graph:
            raise UnknownTaskReferenceError(successor_id, "successor")

        if self._graph.has_edge(predecessor_id, successor_id):
            raise DuplicateDependencyError(predecessor_id, successor_id)

        if lag_minutes < 0:
            raise ValueError(f"Lag minutes cannot be negative: {lag_minutes}")

        self._graph.add_edge(
            predecessor_id,
            successor_id,
            lag=lag_minutes,
        )

    def has_task(self, task_id: str) -> bool:
        """Check whether a task exists in the graph."""
        return self._graph.has_node(task_id)

    def get_task_duration(self, task_id: str) -> int:
        """Get the duration of a task in minutes."""
        if task_id not in self._graph:
            raise UnknownTaskReferenceError(task_id, "query")
        return int(self._graph.nodes[task_id].get("duration", 0))

    def detect_cycle(self) -> CycleDetectionResult:
        """Detect if the graph contains any cycles and return explicit cycle path.

        Returns:
            CycleDetectionResult with has_cycle and explicit cycle path list.
        """
        try:
            cycle_edges = list(nx.find_cycle(self._graph, orientation="original"))
            cycle_path = [edge[0] for edge in cycle_edges] + [cycle_edges[-1][1]]
            return CycleDetectionResult(has_cycle=True, cycle_path=cycle_path)
        except nx.NetworkXNoCycle:
            return CycleDetectionResult(has_cycle=False, cycle_path=None)

    def topological_sort(self) -> list[str]:
        """Compute a deterministic topological ordering of tasks.

        Uses lexicographical tie-breaking for guaranteed deterministic results.

        Returns:
            Ordered list of task IDs.

        Raises:
            GraphCycleError: If the graph contains a cycle.
        """
        try:
            return list(nx.lexicographical_topological_sort(self._graph, key=lambda n: n))
        except nx.NetworkXUnfeasible:
            cycle_res = self.detect_cycle()
            cycle_path = cycle_res.cycle_path or []
            raise GraphCycleError(cycle_path) from None

    def get_predecessors(self, task_id: str) -> list[str]:
        """Return deterministically sorted list of direct predecessor task IDs."""
        if task_id not in self._graph:
            raise UnknownTaskReferenceError(task_id, "query")
        return sorted(self._graph.predecessors(task_id))

    def get_successors(self, task_id: str) -> list[str]:
        """Return deterministically sorted list of direct successor task IDs."""
        if task_id not in self._graph:
            raise UnknownTaskReferenceError(task_id, "query")
        return sorted(self._graph.successors(task_id))

    def get_roots(self) -> list[str]:
        """Return deterministically sorted list of root tasks (in-degree == 0)."""
        return sorted([n for n in self._graph.nodes() if self._graph.in_degree(n) == 0])

    def get_leaves(self) -> list[str]:
        """Return deterministically sorted list of leaf tasks (out-degree == 0)."""
        return sorted([n for n in self._graph.nodes() if self._graph.out_degree(n) == 0])

    def get_nodes(self) -> list[str]:
        """Return deterministically sorted list of all task IDs in graph."""
        return sorted(self._graph.nodes())

    def get_edge_lag(self, predecessor_id: str, successor_id: str) -> int:
        """Return lag minutes between predecessor and successor."""
        if not self._graph.has_edge(predecessor_id, successor_id):
            raise ValueError(f"Edge {predecessor_id} -> {successor_id} does not exist.")
        return int(self._graph.edges[predecessor_id, successor_id].get("lag", 0))
