# E-7 Operations Engine — Scheduler Constraint Catalog & Solver Status Guide

**Document Version:** 1.0.0  
**Phase:** Phase 8F Canonical Production Documentation  
**Solver Engine:** Google OR-Tools CP-SAT (v9.15+)  
**Domain Profiles:** Generic Resource Optimization & Academic Education Timetabling  

---

## 1. Solver Architectural Overview

**Governance Classification:** `ORIGINAL_REQUIREMENT` & `APPROVED_ADR_DECISION` (ADR-003, ADR-010)

The E-7 scheduling engine formulates scheduling problems as Constraint Satisfaction Problems (CSP) and Mixed-Integer Linear Programs (MILP) solved via Google OR-Tools CP-SAT.

### 1.1 Core Solver Invariants

1. **Zero Hard Violations Guarantee:** A schedule is marked `FEASIBLE` if and only if **proven hard violations == 0**.
2. **Deterministic Time Discretization:** The scheduling horizon is discretized into uniform discrete ticks based on `time_granularity_minutes` (default: 15 minutes).
3. **Multi-Component Objective Scoring:** Soft constraints produce normalized penalty scores (`preference_penalty`, `movement_penalty`, `utilization_penalty`).
4. **Authoritative Performance Thresholds:**
   - Standard academic benchmark solve time: **< 60 seconds** (`capacity_solver_time_budget_seconds: 60.0`).
   - Targeted incremental repair time: **< 30 seconds** (`capacity_solver_repair_time_budget_seconds: 30.0`).
   - Repair lock invariance: **`locked_moved == 0`** (previously locked assignments must never shift).

---

## 2. Solver Status Catalog

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR`

The solver reports one of four discrete terminal status outcomes:

| Solver Status | Meaning | Satisfaction Proven? | Actionable Guidance |
|---|---|---|---|
| `FEASIBLE` | Valid schedule found with **0 hard constraint violations**. | Yes (Feasible / Optimal) | Ready for human preview and publication (`/publish`). |
| `INFEASIBLE` | Mathematical proof that no valid schedule exists under the given hard constraints. | Yes (Unsatisfiable) | Inspect `diagnostics` conflict set; relax constraints. |
| `BUDGET_EXHAUSTED` | Solver reached the maximum time budget (e.g. 60s) without finding a solution or proving unsatisfiability. | **No** (Search Incomplete) | **Do NOT treat as INFEASIBLE.** Increase time budget or reduce search space. |
| `FAILED` | Model construction error or unexpected solver failure. | N/A | Check input schema validity and server logs. |

### 2.1 Critical Distinction: `BUDGET_EXHAUSTED != INFEASIBLE`

> [!IMPORTANT]
> `BUDGET_EXHAUSTED` does **not** mean the problem has no solution. It indicates that the search space was too complex to exhaust within the configured time budget (`max_solve_seconds`). Treating `BUDGET_EXHAUSTED` as `INFEASIBLE` is an operational error. Diagnostic recommendations include relaxing tightly coupled time windows or increasing `capacity_solver_time_budget_seconds`.

---

## 3. Constraint Catalog

### 3.1 Generic Resource Constraints

#### C-01: NO_OVERLAP (Exclusive Resource Availability)
- **Constraint ID:** `C-01-NO-OVERLAP`
- **Classification:** **HARD**
- **Domain:** Generic Resource & Room Timetabling
- **Description:** For an exclusive resource (e.g. room, individual staff member), no two scheduled intervals may overlap in time.
- **Formulation:** `model.AddNoOverlap(interval_vars)`
- **Input Fields:** `resource_id`, list of activity `interval_var`
- **Location:** `app/scheduling/constraints/builders.py::build_resource_availability_and_no_overlap`
- **Violation Behavior:** Causes `INFEASIBLE` status.
- **Diagnostic Code:** `CONFLICT_EXCLUSIVE_RESOURCE_OVERLAP`
- **Repair Behavior:** Locked assignments preserve intervals; other activities relocate.
- **Evidence Reference:** `tests/unit/scheduling/test_phase_4a_baseline.py`

#### C-02: CAPACITY (Cumulative Resource Allocation)
- **Constraint ID:** `C-02-CUMULATIVE-CAPACITY`
- **Classification:** **HARD**
- **Domain:** Cumulative Equipment & Fleet Resources
- **Description:** At any point in time, the sum of resource demands across concurrent activities must not exceed total resource capacity.
- **Formulation:** `model.AddCumulative(intervals, demands, capacity)`
- **Input Fields:** `resource.capacity`, activity `resource_demands`
- **Location:** `app/scheduling/constraints/builders.py::build_resource_availability_and_no_overlap`
- **Violation Behavior:** Causes `INFEASIBLE` status.
- **Diagnostic Code:** `CONFLICT_CAPACITY_EXCEEDED`

#### C-03: ELIGIBILITY (Skill & Capability Requirements)
- **Constraint ID:** `C-03-RESOURCE-ELIGIBILITY`
- **Classification:** **HARD**
- **Domain:** Staff Qualification & Room Requirements
- **Description:** An activity can only be assigned to a resource that possesses all mandatory required skills and capabilities.
- **Formulation:** Boolean assignment variables $x_{a, r} = 0$ for ineligible resources. Pre-solve filter strips ineligible pairs.
- **Input Fields:** `activity.required_skills`, `resource.skills`
- **Location:** `app/scheduling/constraints/builders.py::build_resource_assignments_and_eligibility`
- **Violation Behavior:** Pre-solve detection or `INFEASIBLE`.
- **Diagnostic Code:** `CONFLICT_UNSATISFIABLE_SKILL_REQUIREMENT`

#### C-04: PRECEDENCE (Finish-to-Start Dependencies)
- **Constraint ID:** `C-04-PRECEDENCE-ORDERING`
- **Classification:** **HARD**
- **Domain:** Project Tasks & Course Sequences
- **Description:** Activity $B$ cannot begin until Activity $A$ has completed, plus an optional minimum lag time.
- **Formulation:** $start\_tick(B) \ge end\_tick(A) + lag\_ticks$
- **Input Fields:** `predecessor_id`, `successor_id`, `min_lag_minutes`
- **Location:** `app/scheduling/constraints/builders.py::build_precedence_constraints`
- **Violation Behavior:** Causes `INFEASIBLE` status.
- **Diagnostic Code:** `CONFLICT_PRECEDENCE_CYCLE` / `CONFLICT_PRECEDENCE_HORIZON_OVERFLOW`

#### C-05: TIME_WINDOW (Earliest Start, Latest Finish & Blackouts)
- **Constraint ID:** `C-05-TIME-WINDOW`
- **Classification:** **HARD**
- **Domain:** Operational Hours & Calendar Blackouts
- **Description:** Activity must execute entirely within an allowed window and avoid resource blackout intervals.
- **Formulation:** $start\_tick \ge window\_start$, $end\_tick \le window\_end$
- **Input Fields:** `earliest_start`, `latest_finish`, `unavailability_windows`
- **Location:** `app/scheduling/constraints/builders.py::build_time_window_constraints`
- **Violation Behavior:** Causes `INFEASIBLE` status.
- **Diagnostic Code:** `CONFLICT_TIME_WINDOW_VIOLATION`

#### C-06: LOCK (Pinned Assignment Invariance)
- **Constraint ID:** `C-06-LOCK-PIN`
- **Classification:** **HARD**
- **Domain:** Schedule Publishing & Targeted Repair
- **Description:** Pinned activity times and resource assignments must not change under any circumstances during schedule repair.
- **Formulation:** $start\_tick == locked\_tick$, $x_{a, r\_locked} == 1$
- **Location:** `app/scheduling/constraints/builders.py::build_lock_constraints`
- **Violation Behavior:** Invariance guaranteed; `locked_moved == 0`.

---

### 3.2 Academic Timetabling & Education Constraints

**Governance Classification:** `APPROVED_ADR_DECISION` (ADR-010)

#### C-07: COHORT_NO_OVERLAP (Student Group Conflict Avoidance)
- **Constraint ID:** `C-07-COHORT-NO-OVERLAP`
- **Classification:** **HARD**
- **Domain:** Academic Education Scheduling
- **Description:** A student cohort cannot be scheduled for two concurrent examinations or classes simultaneously.
- **Formulation:** Virtual exclusive cohort resource assigned to all sessions belonging to the cohort.
- **Location:** `app/scheduling/education/ortools_compiler.py`
- **Evidence Reference:** Phase 5 Education Benchmark (`docs/phase-5/PHASE_5_BENCHMARK_REPORT.md`)

#### C-08: EXAM_CALENDAR_GAP (Minimum Days Spacing)
- **Constraint ID:** `C-08-EXAM-CALENDAR-GAP`
- **Classification:** **HARD** (configurable)
- **Domain:** Academic Education Scheduling
- **Description:** Major examinations for a student cohort must be separated by at least $N$ full calendar days (default: 1 clear day).
- **Formulation:** $start\_tick(B) \ge end\_tick(A) + gap\_ticks$ for major exam pairs in the same cohort.
- **Location:** `app/scheduling/education/ortools_compiler.py`
- **Evidence Reference:** `tests/unit/scheduling/education/test_education_scheduler.py`

#### C-09: ROOM_SEATING_CAPACITY (Physical Capacity)
- **Constraint ID:** `C-09-ROOM-SEATING-CAPACITY`
- **Classification:** **HARD**
- **Domain:** Classroom & Exam Hall Allocation
- **Description:** Exam cohort student count must not exceed physical room seating capacity.
- **Formulation:** Room eligibility filter: rooms with $capacity < cohort.student\_count$ are excluded.
- **Location:** `app/scheduling/education/ortools_compiler.py`
- **Evidence Reference:** `tests/unit/scheduling/education/test_education_scheduler.py`

---

## 4. Soft Objectives & Normalization

Soft constraints contribute to the scalar minimization objective function:
$$\min \sum \text{penalties}$$

1. **Resource Preference Penalty:** Penalizes assignment of resources outside the activity's `preferred_resource_ids`.
2. **Movement Penalty (Repair):** Penalizes shifting previously assigned activities to new start times or resources.
3. **Compactness / Utilization:** Encourages earlier completion and tighter resource packing.

---

## 5. Capacity & Quota Defaults

**Governance Classification:** `ENGINEERING_DEFAULT` (Phase 8E)

| Parameter | Configuration Key | Default Value | Description |
|---|---|---|---|
| **Solve Time Budget** | `capacity_solver_time_budget_seconds` | `60.0s` | Maximum wall-clock CPU time for complete solve |
| **Repair Time Budget** | `capacity_solver_repair_time_budget_seconds`| `30.0s` | Maximum wall-clock CPU time for repair solve |
| **Worker Concurrency** | `capacity_solver_max_concurrency` | `2` | Number of simultaneous solver worker processes |
| **Worker Poll Interval**| `capacity_solver_worker_poll_interval_seconds`| `1.0s` | Idle sleep duration between job queue polls |
| **Batch Claim Size** | `capacity_solver_claim_batch_size` | `5` | Maximum jobs claimed in one atomic SELECT FOR UPDATE |
| **Tenant Queued Solves**| `capacity_tenant_max_queued_solves` | `5` | Maximum active + requested solve jobs per tenant |
| **Tenant Concurrency** | `capacity_tenant_max_concurrent_solves` | `2` | Maximum concurrent active solves per tenant |
| **Tenant Repair Quota** | `capacity_tenant_max_concurrent_repairs` | `3` | Maximum concurrent repair jobs per tenant |
| **Backpressure Action**| `HTTP 429` | `Retry-After: 30` | Returns `capacity.quota_exceeded` upon breach |
