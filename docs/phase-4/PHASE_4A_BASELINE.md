# Phase 4A Baseline & Technical Specification

---

## 1. Scope and Status

Phase 4A introduces the generic scheduling core for the IR Infotech E-7 Operations Engine:
- **Phase 4A Status**: COMPLETE / FROZEN
- **Canonical Phase 4 Status**: IN_PROGRESS
- **Phase 4B Readiness**: READY (publish, locking operations, incremental repair, schedule events, calendar adapter command path)
- **Canonical Phase 2 Status**: IN_PROGRESS_PENDING_PROVIDER_BENCHMARK
- **Database Baseline**: Exactly 23 application tables in `Base.metadata`; Alembic head `0011` (`0011_create_generic_scheduling_core.py`, `down_revision = "0010"`).

---

## 2. Implemented Components

1. **Generic Resource Registry (`app/resources/`)**:
   - Types: `PERSON`, `ROOM`, `EQUIPMENT`, `OTHER` (zero education specialization).
   - Timezone-aware half-open availability intervals `[start_at, end_at)`.
   - Explicit `AVAILABLE` and `UNAVAILABLE` (blackout) intervals.
2. **Canonical Constraint Catalog (`app/scheduling/constraints/`)**:
   - Discriminated union of typed constraints: `NO_OVERLAP`, `CAPACITY`, `ELIGIBILITY`, `PRECEDENCE`, `TIME_WINDOW`, `MINIMUM_GAP`, `LOCK`.
   - Distinct classification of `HARD` (mandatory feasibility) vs `SOFT` (penalized objectives).
3. **Solver Profile & Time Discretization (`app/scheduling/profiles/`)**:
   - `SolverProfile` with configurable horizon, `time_granularity_minutes`, solve time budget, seed, search workers, and soft objective weights.
   - Deterministic integer tick conversion functions (`datetime_to_tick`, `tick_to_datetime`).
4. **Immutable Input Snapshots & Hashing (`app/scheduling/hashing.py`)**:
   - Provider-neutral `SchedulingRequest` with normalized canonical serialization.
   - SHA-256 `input_snapshot_hash` for audit and replay reproducibility.
5. **OR-Tools CP-SAT Solver (`app/scheduling/solvers/cpsat.py`)**:
   - CP-SAT interval variables, assignment booleans, and optional intervals.
   - Zero hard constraint violations enforced on all `FEASIBLE` candidates.
   - Distinct score component breakdown: `preference_penalty`, `movement_penalty`, `idle_gap_penalty`, `fairness_penalty`, `utilization_penalty`.
6. **Infeasibility Diagnostics (`app/scheduling/diagnostics/`)**:
   - Assumption-based conflict extraction via `solver.SufficientAssumptionsForInfeasibility()`.
   - Non-LLM deterministic explanations mapped to canonical constraint, activity, and resource IDs.
   - Suggested relaxation candidates for human operator review only.
   - Normal `INFEASIBLE` lifecycle classification (never `FAILED`, never empty success).
7. **Canonical Persistence Model (`app/db/models/scheduling.py` & Migration `0011`)**:
   - 7 new tables: `resources`, `resource_availabilities`, `constraint_definitions`, `solver_profiles`, `schedule_jobs`, `schedules`, `schedule_assignments`.
8. **Scheduling Lifecycle State Machine (`app/scheduling/states.py`)**:
   - Job: `REQUESTED` -> `SOLVING` -> `FEASIBLE` / `INFEASIBLE` / `FAILED`.
   - Schedule: `FEASIBLE` -> `REVIEW`.
9. **Dedicated Asynchronous Worker (`app/scheduling/worker.py`)**:
   - Atomic job claiming via PostgreSQL `SELECT FOR UPDATE SKIP LOCKED`.
   - Multi-worker concurrent claim safety proven.
   - Outbox worker preserved as `OUTBOX_ONLY`.
10. **Decoupled API Contracts (`app/api/v1/scheduling.py`)**:
    - `POST /schedule-jobs` (with canonical Phase-1 `Idempotency-Key`).
    - `GET /schedule-jobs/{job_id}`.
    - `GET /resources/availability`.
    - Production mount deferred: `SCHEDULING_API_PRODUCTION_MOUNT = "DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY"`.
11. **OpenTelemetry Telemetry (`app/core/telemetry/scheduling.py`)**:
    - Low-cardinality metrics: attempts, results by categorical status, durations, assignment counts, and infeasibility occurrences.
    - Strict prohibition on high-cardinality labels (no tenant ID, job ID, activity ID, etc.).

---

## 3. Phase 4B Deferred Scope

Phase 4B will implement:
- Schedule publication and supersession lifecycle (`REVIEW` -> `PUBLISHED` -> `SUPERSEDED`).
- Locking operations (manual human pins and soft/hard locks).
- Incremental rescheduling and minimal-churn repair algorithms.
- Schedule domain events (`schedule.generated`, `schedule.published`, `schedule.repaired`).
- Calendar adapter integration (RFC 5545 / CalDAV synchronization).
- Stress and benchmark repair regression suites.
