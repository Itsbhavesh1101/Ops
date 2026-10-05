# Phase 4A Scheduling Domain Architecture

---

## 1. Executive Summary

Phase 4A introduces the **Generic Scheduling Core** for the IR Infotech E-7 Operations Engine. The scheduling engine formulates conflict-free schedules and explainable diagnostics using **Google OR-Tools CP-SAT**.

PostgreSQL serves as the authoritative system of record. OR-Tools operates strictly as a transient optimization and constraint satisfaction solver.

---

## 2. Resource Model & Registry

The E-7 resource registry is strictly generic and decoupled from domain specializations:
- **Resource Types**: `PERSON`, `ROOM`, `EQUIPMENT`, `OTHER`.
- **Identifiers**: Canonical UUIDv4 with tenant-scoped unique resource keys (`resource_key`).
- **Attributes**:
  - `capacity`: Non-negative integer (e.g., 1 for single-occupancy room or worker; >1 for cumulative rooms/resources).
  - `skills`: List of capability tags or certification strings.
  - `attributes`: Structured JSON metadata dictionary.
  - `active`: Boolean activation flag.
- **Tenant Scope**: Strict tenant partition on all queries and mutations.

Academic specialization (teachers, classrooms, courses, cohorts) is explicitly reserved for Phase 5.

---

## 3. Availability and Blackout Windows

Resource availability is modeled using half-open intervals:
- **Interval Semantics**: `[start_at, end_at)` where `end_at >= start_at`.
- **Timezone Awareness**: Strictly requires timezone-aware `datetime` objects; naive datetimes are rejected with 422/ValidationError.
- **Interval Types**:
  - `AVAILABLE`: Explicit permissible operational window.
  - `UNAVAILABLE`: Resource blackout window (vacation, shift maintenance, offline).
- **Calendar Agnosticism**: No hardcoded weekends, bank holidays, or operating shifts exist in the generic core. All calendar patterns are supplied as policy inputs.

---

## 4. Constraint Catalog & Typed Input

Constraints are modeled using a strongly-typed Pydantic discriminated union with stable canonical IDs:

| Constraint Type | Hardness | Mathematical Semantics |
| :--- | :--- | :--- |
| **`NO_OVERLAP`** | HARD | Exclusive resource booking: zero simultaneous activity overlaps on capacity-1 resources (`AddNoOverlap`). |
| **`CAPACITY`** | HARD | Cumulative resource limit: sum of concurrent activity demands must not exceed configured capacity (`AddCumulative`). |
| **`ELIGIBILITY`** | HARD | Activity-to-resource capability matching: activity requires specific skills or explicit resource allowlists. |
| **`PRECEDENCE`** | HARD | Directed ordering: $End(A) + \text{gap} \le Start(B)$ between explicit predecessor-successor pairs. |
| **`TIME_WINDOW`** | HARD | Window clamping: activities must start and finish entirely within allowed intervals. |
| **`MINIMUM_GAP`** | HARD / SOFT | Separation gap: minimum idle interval between disjoint activity pairs. |
| **`LOCK`** | HARD | Pinned assignment: activity is locked to fixed start/end times and/or assigned resources. |

**Invariants**:
- Hard constraints are **never** converted into soft penalties.
- If any hard constraint cannot be satisfied, the result is `INFEASIBLE`.
- Soft constraints are assigned weights exclusively from the versioned `SolverProfile`.

---

## 5. Solver Profile & Time Discretization

The `SolverProfile` defines operational solver parameters and time discretization:
- `horizon_start` / `horizon_end`: Strict planning window boundary.
- `time_granularity_minutes`: Conversion factor between wall-clock datetimes and integer CP-SAT ticks:
  $$\text{tick} = \left\lfloor \frac{t - \text{horizon\_start}}{\text{granularity}} \right\rfloor$$
- `max_solve_seconds`: Explicit execution timeout budget.
- `random_seed`: Deterministic solver pseudo-random generator seed.
- `num_search_workers`: Parallel search worker thread count (single-worker for deterministic reproducibility tests).
