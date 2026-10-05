# Phase 4A OR-Tools CP-SAT Solver Architecture

---

## 1. Solver Authority & Lifecycle

- **Non-authoritative Computation**: Google OR-Tools CP-SAT operates strictly as an optimization engine. It does not publish schedules, authorize policy relaxations, or alter external calendars.
- **Deterministic Outcomes**:
  - `FEASIBLE`: A valid assignment schedule satisfying 100% of hard constraints.
  - `INFEASIBLE`: An over-constrained or conflicting request. Explored using CP-SAT assumption literals.
  - `FAILED`: Reserved strictly for unhandled system/runtime crashes or timeouts.

---

## 2. Model Variable Formulation

For each activity $i \in \mathcal{A}$ with duration $d_i$:
- $\text{start}_i \in [0, H]$
- $\text{end}_i \in [d_i, H + d_i]$
- $\text{interval}_i = \text{IntervalVar}(\text{start}_i, d_i, \text{end}_i)$

For each eligible resource $r \in \mathcal{R}_i$:
- $x_{i, r} \in \{0, 1\}$: Assignment boolean.
- $\text{opt\_interval}_{i, r} = \text{OptionalIntervalVar}(\text{start}_i, d_i, \text{end}_i, x_{i, r})$
- Selection constraint: $\sum_{r \in \mathcal{R}_i} x_{i, r} = 1$

Resource blackout windows are modeled as fixed intervals:
- $\text{NewFixedSizeIntervalVar}(\text{start\_tick}, \text{duration\_ticks})$ added to the resource's no-overlap or cumulative interval list.

---

## 3. Hard Constraint Builders

Each hard constraint is associated with an assumption literal $a_c$ to enable conflict extraction on unsatisfiable problems:

1. **NO_OVERLAP**:
   $$\text{model.AddNoOverlap}(\text{intervals}_r).\text{OnlyEnforceIf}(a_c)$$
2. **CAPACITY**:
   $$\text{model.AddCumulative}(\text{intervals}_r, \text{demands}_r, C_r).\text{OnlyEnforceIf}(a_c)$$
3. **PRECEDENCE**:
   $$(\text{end}_u + \text{gap} \le \text{start}_v).\text{OnlyEnforceIf}(a_c)$$
4. **TIME_WINDOW**:
   $$(\text{start}_i \ge S \land \text{end}_i \le E).\text{OnlyEnforceIf}(a_c)$$
5. **LOCK**:
   $$(\text{start}_i == S_{\text{lock}} \land x_{i, r_{\text{lock}}} == 1).\text{OnlyEnforceIf}(a_c)$$

---

## 4. Soft Objectives & Score Components

The solver objective minimizes the weighted sum of penalties:
$$\min \sum w_k \cdot P_k$$

Score components are extracted individually and persisted:
- `preference_penalty`: Penalizes assignments to non-preferred resources.
- `movement_penalty`: Penalizes displacing existing or baseline assignments (prepared for Phase 4B repair).
- `utilization_penalty`: Encourages schedule compactness.
- `hard_constraint_violations`: Strictly $0$ for every `FEASIBLE` solution.

---

## 5. Reproducibility & Input Hashing

Every solve persists:
- `input_snapshot_hash`: Canonical SHA-256 hash of normalized request JSON (excluding correlation IDs and random ordering).
- `solver_metadata`:
  - `ortools_version`
  - `random_seed`
  - `num_search_workers`
  - `profile_version`
  - `wall_time_seconds`
  - `num_conflicts` / `num_branches`
