"""Critical Path Method (CPM) service.

Computes forward pass (ES, EF), backward pass (LS, LF), total/free float,
and critical paths for a validated task dependency DAG.
"""

from app.task_intelligence.graph.dependency_graph import TaskDependencyGraph
from app.task_intelligence.graph.models import (
    CPMScheduleNode,
    CPMScheduleResult,
)


class CriticalPathCalculator:
    """Calculates Critical Path Method metrics for a TaskDependencyGraph."""

    @staticmethod
    def calculate(graph: TaskDependencyGraph) -> CPMScheduleResult:
        """Compute CPM schedule metrics for the given dependency graph.

        Args:
            graph: A validated acyclic TaskDependencyGraph.

        Returns:
            CPMScheduleResult containing task schedule nodes, project duration,
            and critical path sequences.

        Raises:
            GraphCycleError: If the graph contains a cycle.
        """
        topological_order = graph.topological_sort()

        if not topological_order:
            return CPMScheduleResult(
                project_duration_minutes=0,
                schedule={},
                critical_path=[],
                critical_paths=[],
            )

        early_start: dict[str, int] = {}
        early_finish: dict[str, int] = {}

        # Forward Pass: calculate Early Start (ES) and Early Finish (EF)
        for task_id in topological_order:
            duration = graph.get_task_duration(task_id)
            preds = graph.get_predecessors(task_id)

            if not preds:
                es = 0
            else:
                es = max(early_finish[p] + graph.get_edge_lag(p, task_id) for p in preds)

            early_start[task_id] = es
            early_finish[task_id] = es + duration

        project_duration = max(early_finish.values(), default=0)

        # Backward Pass: calculate Late Finish (LF) and Late Start (LS)
        late_start: dict[str, int] = {}
        late_finish: dict[str, int] = {}

        for task_id in reversed(topological_order):
            duration = graph.get_task_duration(task_id)
            succs = graph.get_successors(task_id)

            if not succs:
                lf = project_duration
            else:
                lf = min(late_start[s] - graph.get_edge_lag(task_id, s) for s in succs)

            late_finish[task_id] = lf
            late_start[task_id] = lf - duration

        # Calculate Slack and build Schedule Nodes
        schedule_nodes: dict[str, CPMScheduleNode] = {}
        critical_nodes: set[str] = set()

        for task_id in topological_order:
            duration = graph.get_task_duration(task_id)
            es = early_start[task_id]
            ef = early_finish[task_id]
            ls = late_start[task_id]
            lf = late_finish[task_id]
            total_slack = ls - es

            succs = graph.get_successors(task_id)
            if not succs:
                free_slack = project_duration - ef
            else:
                min_succ_es = min(early_start[s] - graph.get_edge_lag(task_id, s) for s in succs)
                free_slack = max(0, min_succ_es - ef)

            is_critical = total_slack == 0
            if is_critical:
                critical_nodes.add(task_id)

            schedule_nodes[task_id] = CPMScheduleNode(
                task_id=task_id,
                duration_minutes=duration,
                early_start=es,
                early_finish=ef,
                late_start=ls,
                late_finish=lf,
                total_slack=total_slack,
                free_slack=free_slack,
                is_critical=is_critical,
            )

        # Extract Critical Paths (chains of zero-slack tasks from roots to leaves)
        critical_paths = CriticalPathCalculator._find_critical_paths(
            graph=graph,
            critical_nodes=critical_nodes,
            early_finish=early_finish,
            early_start=early_start,
        )

        primary_critical_path = critical_paths[0] if critical_paths else sorted(critical_nodes)

        return CPMScheduleResult(
            project_duration_minutes=project_duration,
            schedule=schedule_nodes,
            critical_path=primary_critical_path,
            critical_paths=critical_paths,
        )

    @staticmethod
    def _find_critical_paths(
        graph: TaskDependencyGraph,
        critical_nodes: set[str],
        early_finish: dict[str, int],
        early_start: dict[str, int],
    ) -> list[list[str]]:
        """Find all unbroken paths consisting entirely of critical nodes from root to leaf."""
        critical_roots = sorted([n for n in graph.get_roots() if n in critical_nodes])
        critical_leaves = set([n for n in graph.get_leaves() if n in critical_nodes])

        if not critical_roots or not critical_leaves:
            return []

        all_paths: list[list[str]] = []

        def dfs(current: str, current_path: list[str]) -> None:
            if current in critical_leaves:
                all_paths.append(list(current_path))
                return

            succs = graph.get_successors(current)
            for s in sorted(succs):
                if s in critical_nodes:
                    # An edge (u, v) is critical if EF(u) + lag == ES(v)
                    lag = graph.get_edge_lag(current, s)
                    if early_finish[current] + lag == early_start[s]:
                        current_path.append(s)
                        dfs(s, current_path)
                        current_path.pop()

        for root in critical_roots:
            dfs(root, [root])

        all_paths.sort()
        return all_paths
