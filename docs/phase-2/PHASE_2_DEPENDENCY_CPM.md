# Phase 2 Directed Acyclic Graph (DAG) & Critical Path Method (CPM)

**Status**: FROZEN (Phase 2A Baseline)  
**Implementation**: `app/task_intelligence/graph/`  
**Dependencies**: `networkx>=3.0`

---

## 1. Mathematical Foundation

The Task Intelligence scheduling engine models every operational plan as an edge-weighted, directed graph:
$$G = (V, E)$$
where:
- Each vertex $v \in V$ represents an atomic task with non-negative integer duration $d(v) \in \mathbb{N}_{\ge 0}$ in minutes.
- Each directed edge $(u, v) \in E$ represents a finish-to-start precedence constraint with lag $L(u, v) \ge 0$, requiring that task $u$ complete and lag elapsed before task $v$ can begin.

---

## 2. Directed Acyclic Graph (`TaskDependencyGraph`)

- **Class**: `app.task_intelligence.graph.dependency_graph.TaskDependencyGraph`
- **Internal Structure**: `networkx.DiGraph` storing task node attributes (`duration_minutes`) and edge attributes (`lag_minutes`, `dependency_type`).

### 2.1 Cycle Detection
- Evaluated via `nx.simple_cycles(graph)`.
- If one or more cycles exist:
  - `is_acyclic()` returns `False`.
  - `find_cycle()` returns the first cycle as a closed list of node identifiers, ending with the start node: e.g. `["T1", "T2", "T3", "T1"]`.
- When acyclic, `topological_sort()` returns a deterministic, lexicographically sorted linear ordering:
  - Sorts candidates at each step using Python's stable alphabetical order so identical graph topologies produce identical sequence outputs across all environments.

---

## 3. Critical Path Method (`CriticalPathCalculator`)

- **Class**: `app.task_intelligence.graph.cpm.CriticalPathCalculator`
- **Schedule Metrics**:
  - **Earliest Start ($ES$)**: Earliest time a task can begin after all predecessors finish.
  - **Earliest Finish ($EF$)**: $ES(v) + d(v)$.
  - **Latest Finish ($LF$)**: Latest time a task can finish without delaying overall project completion.
  - **Latest Start ($LS$)**: $LF(v) - d(v)$.
  - **Total Slack ($S$)**: $LS(v) - ES(v) = LF(v) - EF(v)$.
  - **Free Slack**: Slack available without delaying any immediate successor's earliest start.

### 3.1 Forward Pass (Earliest Times)
For each task $v$ in topological order:
$$ES(v) = \begin{cases} 0 & \text{if } \text{Pred}(v) = \emptyset \\ \max_{u \in \text{Pred}(v)} \left( EF(u) + L(u, v) \right) & \text{otherwise} \end{cases}$$
$$EF(v) = ES(v) + d(v)$$

Project Duration $T$:
$$T = \max_{v \in V} EF(v)$$

### 3.2 Backward Pass (Latest Times)
For each task $v$ in reverse topological order:
$$LF(v) = \begin{cases} T & \text{if } \text{Succ}(v) = \emptyset \\ \min_{w \in \text{Succ}(v)} \left( LS(w) - L(v, w) \right) & \text{otherwise} \end{cases}$$
$$LS(v) = LF(v) - d(v)$$

### 3.3 Total Slack and Critical Path Identification
$$S(v) = LS(v) - ES(v) = LF(v) - EF(v)$$

A task $v$ is on the **critical path** if and only if:
$$S(v) = 0$$

The critical path sequence is traced from start to finish through zero-slack tasks where $EF(u) + L(u, v) = ES(v)$.

---

## 4. Complexity & Performance Bounds

| Operation | Time Complexity | Space Complexity |
| :--- | :--- | :--- |
| Node & Edge Ingestion | $\mathcal{O}(\|V\| + \|E\|)$ | $\mathcal{O}(\|V\| + \|E\|)$ |
| Cycle Detection | $\mathcal{O}(\|V\| + \|E\|)$ | $\mathcal{O}(\|V\|)$ |
| Topological Sort | $\mathcal{O}(\|V\| \log \|V\| + \|E\|)$ | $\mathcal{O}(\|V\|)$ |
| Forward & Backward CPM Pass | $\mathcal{O}(\|V\| + \|E\|)$ | $\mathcal{O}(\|V\|)$ |
| End-to-End Schedule Computation | $\mathcal{O}(\|V\| \log \|V\| + \|E\|)$ | $\mathcal{O}(\|V\| + \|E\|)$ |

For standard exam operations plans ($\|V\| \le 500$, $\|E\| \le 1500$), total computation time is $< 5$ milliseconds on modern CPUs.
