# ADR-003: Scheduling Strategy — OR-Tools Primary with Education Solver PoC

## Status
Accepted

## Date
2026-09-16

## Context
The Scheduling Engine (Engine 24) is responsible for generating conflict-free, multi-resource operational schedules (rooms, staff, equipment, time slots), optimizing soft objectives (preferences, gaps, fairness), and performing incremental rescheduling during disruptions.

A key architectural tension identified during research is the balance between general-purpose mathematical constraint programming and specialized institutional timetabling:
1. General resource scheduling requires a flexible, high-performance, open-source constraint programming solver.
2. Academic course and examination timetabling involves highly domain-specific constraint patterns (curriculum conflicts, student group unicity, invigilator quotas) where dedicated solvers exist.

Earlier notes mentioned FET and Timefold. The *Open-Source Research Report* (Section 2.3, 2.5 & Section 14) and *Complete Implementation Plan* (Section 4, 9 & 18) reconciled this by adopting Google OR-Tools CP-SAT as the primary general solver, while establishing a formal Phase-5 comparative Proof-of-Concept (PoC) for education scheduling evaluating UniTime, a native OR-Tools model, and FET as a benchmark.

## Decision
1. **Google OR-Tools CP-SAT (Apache-2.0)** is selected as the **primary** general-purpose constraint programming and optimization solver for the E-7 Operations Engine. OR-Tools is designated as **primary, not exclusive**.
2. **Academic & Educational Timetabling** will follow a formal **Phase-5 comparative PoC** to evaluate:
   - **UniTime (Apache-2.0)**: Specialized institutional course/exam timetabling adapter.
   - **Native OR-Tools Model**: Dedicated academic constraint profile implemented directly in CP-SAT.
   - **FET 7.10.5 (AGPL-3.0)**: Benchmark and fallback reference only; subject to strict network isolation and legal review.
3. **Timefold Solver (Apache-2.0)** is classified as **conditional** and will **NOT** be included in the initial implementation.
4. **Canonical Ownership**: E-7 remains the single canonical system of record for all scheduling requests, constraint configurations, resource entities, assignments, published/locked states, disruption repair semantics, and conflict diagnostics.
5. **Adapter Isolation**: Solver-specific objects (such as OR-Tools `IntVar`, `Constraint`, `CpModel`, or UniTime XML/REST entities) must **never** leak into public E-7 API contracts or canonical domain entities.
6. **Strict Non-LLM Invariant**: Generative AI (LLMs) must **NEVER** act as an authoritative scheduling solver, assign time slots, or override constraint calculations.

## Decision Details
* **Execution Boundary**: Scheduling runs asynchronously inside `scheduling-worker` processes triggered by job records in PostgreSQL.
* **Solver Tuning**: Objective penalty weights, search heuristics, and solve time limits are encapsulated in declarative `solver_profiles` (e.g., `fast`, `balanced`, `exact`).
* **Infeasibility Handling**: When no feasible solution exists, the solver returns status `INFEASIBLE` along with the identified conflicting constraint subset (using assumptions / sufficient conflict set).
* **Disruption Repair**: Incremental repair models penalize assignment changes relative to the locked baseline, ensuring minimal churn.

## Authority Boundary
* **Solvers (OR-Tools / UniTime PoC) Own**:
  - Raw mathematical satisfaction and optimization algorithms.
  - Feasibility search and objective score calculations.
* **IR / E-7 Operations Engine Owns**:
  - Canonical `schedule_jobs`, `schedule_assignments`, `constraints`, and `resources`.
  - Schedule lifecycle state machine (`REQUESTED`, `SOLVING`, `FEASIBLE`, `REVIEW`, `PUBLISHED`, `SUPERSEDED`).
  - Authority to approve, lock, publish, and distribute schedules.
  - Calendar synchronization and external ERP mapping.

## Configuration / No-Hardcoding Implications
* Solver maximum duration, thread count, and objective weights must be configurable via environment variables (`SOLVER_MAX_SECONDS`, `SOLVER_NUM_WORKERS`) and database-backed `solver_profiles`.
* Specific institution calendars, periods, and room capacities must be ingested from database entities, never hardcoded in solver scripts.

## Consequences

### Positive
* **Proven Performance**: Google OR-Tools CP-SAT is widely recognized as the industry standard open-source constraint programming solver with excellent Python bindings.
* **Zero Contamination**: Permissive Apache-2.0 license protects proprietary IP.
* **Objective Evidence**: The Phase-5 PoC ensures that specialized academic timetabling decisions are driven by empirical benchmark data rather than assumptions.

### Negative / Trade-offs
* Complex academic timetabling modeled in native OR-Tools requires extensive custom constraint engineering compared to out-of-the-box timetabling suites.
* Maintaining UniTime adapter mappings introduces secondary schema transformation overhead during the PoC.

## Rejected or Deferred Alternatives
* **FET as Primary Production Solver**: Rejected due to AGPL-3.0 viral copyleft licensing risks. Restricted to isolated benchmark/fallback status.
* **Timefold Solver for Initial Implementation**: Deferred. Java-based solver adds cross-runtime complexity in the MVP where Python-native OR-Tools is fully capable.
* **Probabilistic LLM Scheduling**: Strictly rejected. Language models hallucinate resource availability and cannot mathematically guarantee zero hard-constraint violations.

## Conditions for Reconsideration
* UniTime will only be promoted to production status if Phase-5 benchmark results demonstrate superior solve times, constraint fidelity, or operator maintainability compared to the native OR-Tools academic model, supported by an accepted Phase-5 ADR.
* Timefold will only be evaluated if Phase-8 load testing reveals complex rostering constraints that OR-Tools cannot solve within acceptable operational budgets.

## Implementation Phase
* **Phase 4: Generic Scheduling MVP** (OR-Tools CP-SAT builder, constraint catalog, basic repair).
* **Phase 5: Education Scheduling PoC** (UniTime adapter vs. native OR-Tools academic profile vs. FET benchmark).
* **Phase 6: Unified Vertical Slice** (Semester examination end-to-end integration).
* **Phase 8: Hardening** (Benchmark regression suite, solver timeout and memory isolation).

## Validation / Evidence Required
* `tests/unit/scheduling/test_ortools_solver.py`: 100% hard-constraint satisfaction on standard benchmark datasets.
* `tests/integration/scheduling/test_conflict_explainer.py`: Infeasible problem returns status `INFEASIBLE` with exact conflicting constraint IDs.
* `tests/performance/test_solver_benchmarks.py`: Solve execution respects configured `SOLVER_MAX_SECONDS` timeout under heavy load.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///F:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) (Requirements `FR-SC-01` through `FR-SC-06`)
* *Complete Specification*, Section 7, Section 16.3 & Section 31.
* *Open-Source Research Report*, Section 2.3, 2.5 & Section 14.
