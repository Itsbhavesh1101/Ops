# PHASE 8C — FINAL EVIDENCE CONSISTENCY RECONCILIATION REPORT

**Execution Timestamp**: 2026-10-09T19:20:04.831612+00:00 (Reconciled: 2026-10-10T01:40:00Z)  
**Environment**: Windows 11 (AMD64), Python 3.12.13, 12 Logical Cores  
**Total Benchmark Wall-Clock Duration**: 61.158s  
**Status**: `PHASE_8C_PERFORMANCE_LOAD_TESTING = PASS`  
**Engineering Status**: `PHASE_8_ENGINEERING_STATUS = 8C_COMPLETE_READY_FOR_8D`  
**Production Readiness**: `PHASE_8_PRODUCTION_READINESS = NOT_READY`  

---

## 1. Documented Targets vs Measured Empirical Values

| Metric | Target Value | Target Type | Source | Observed Value | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Academic Scheduling Solve** | `< 60.0s` | **AUTHORITATIVE** | ADR-010 / Phase 5 | `0.411s` | **PASS** |
| **Academic Schedule Feasibility** | `0 Hard Violations` | **AUTHORITATIVE** | ADR-010 / Phase 5 | `0` | **PASS** |
| **Repair Invariance** | `0 Locked Moved` | **AUTHORITATIVE** | ADR-010 / Phase 7F | `0` | **PASS** |
| **Goal Create (POST /goals)** | `p95 < 250ms` | PROPOSED | Phase 8.0 Proposal | `400.43ms` (ASGI) / `699.81ms` (Real HTTP) | MEASURED |
| **Plan Critical Path Read** | `p95 < 100ms` | PROPOSED | Phase 8.0 Proposal | `523.68ms` (ASGI) / `160.13ms` (Real HTTP) | MEASURED |
| **Task Update (PATCH /tasks)** | `p95 < 250ms` | PROPOSED | Phase 8.0 Proposal | `127.13ms` (ASGI) / `153.29ms` (Real HTTP) | MEASURED |
| **Audit Search (GET /audit)** | `p95 < 100ms` | PROPOSED | Phase 8.0 Proposal | `40.15ms` (ASGI) / `135.72ms` (Real HTTP) | MEASURED |
| **Analytics (GET /operations)** | `p95 < 100ms` | PROPOSED | Phase 8.0 Proposal | `80.07ms` (ASGI) / `222.96ms` (Real HTTP) | MEASURED |
| **Outbox Dispatch Throughput** | `>= 150 events/s` | PROPOSED | Phase 8.0 Proposal | `39.41 events/s` (Canonical Real AMQP Broker; median of 3 measured runs) | MEASURED |
| **Workflow Instance Creation** | `p95 < 300ms` | PROPOSED | Phase 8.0 Proposal | `745.02ms` (Live Flowable REST) | MEASURED |

> [!NOTE]
> Per Performance Target Governance (Section 2), proposed targets are engineering baseline references and are NOT authoritative pass/fail gates. Empirical measurements are captured to guide systematic capacity tuning in Phase 8E.

---

## 2. Side-by-Side Baselines — Not Direct A/B Transport Overhead: IN_PROCESS_ASGI vs REAL_LOCAL_HTTP

The E-7 application boundary was benchmarked across two distinct execution modes:
1. `IN_PROCESS_ASGI_BENCHMARK`: In-process execution through `httpx.ASGITransport` directly calling the FastAPI application.
2. `REAL_LOCAL_HTTP_BENCHMARK`: Live Uvicorn API process listening on `localhost:8008` over real TCP sockets with full HTTP protocol parsing, middleware scheduling, and connection lifecycles.

> [!IMPORTANT]
> **Interpretation Notice (Side-by-Side Baselines — Not Direct A/B Transport Overhead)**:  
> These measurements represent separate architectural topologies, NOT an isolated network/TCP transport overhead experiment.
> - **In-Process ASGI**: The test runner and FastAPI application share the same Python process and Global Interpreter Lock (GIL). CPU-intensive CPM critical-path graph calculations and request handling compete on the same event loop thread.
> - **Real Local HTTP**: Multi-process topology. The live Uvicorn API runs in a dedicated external OS process (`e7_uvicorn_api`), while the benchmark client harness runs in a separate process (`benchmark_runner`). Under 12-core architecture, this provides true OS-level parallelism across cores. Furthermore, `httpx.AsyncClient` maintains persistent HTTP/1.1 connections with keep-alive socket reuse over localhost TCP.
> Consequently, certain real HTTP p50 latencies (e.g., `/api/v1/health` 3.01ms vs 13.14ms, and `/api/v1/plans/:id/critical-path` 50.94ms vs 447.50ms) are significantly lower than in-process ASGI. Differences between the two modes reflect process isolation, connection reuse, and concurrency scheduling—NOT simple network overhead.

| Endpoint | Method | ASGI p50 | Real HTTP p50 | ASGI p95 | Real HTTP p95 | ASGI Throughput | Real HTTP Throughput | Real HTTP Errors |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/api/v1/health` | GET | `13.14ms` | `3.01ms` | `28.12ms` | `4.64ms` | `665.0 RPS` | `293.6 RPS` | `0` |
| `/api/v1/ready` | GET | `11.85ms` | `3.11ms` | `20.36ms` | `5.68ms` | `803.4 RPS` | `282.4 RPS` | `0` |
| `/api/v1/goals` | POST | `53.99ms` | `69.61ms` | `400.43ms` | `699.81ms` | `52.7 RPS` | `20.1 RPS` | `0` |
| `/api/v1/plans/:id/critical-path` | GET | `447.50ms` | `50.94ms` | `523.68ms` | `160.13ms` | `27.7 RPS` | `63.3 RPS` | `0` |
| `/api/v1/tasks/:id` | PATCH | `52.97ms` | `74.10ms` | `127.13ms` | `153.29ms` | `81.7 RPS` | `50.1 RPS` | `0` |
| `/api/v1/audit` | GET | `35.20ms` | `47.83ms` | `40.15ms` | `135.72ms` | `143.0 RPS` | `69.7 RPS` | `0` |
| `/api/v1/analytics/operations` | GET | `55.50ms` | `70.68ms` | `80.07ms` | `222.96ms` | `82.9 RPS` | `47.9 RPS` | `0` |

- **Headless Locust Verification**: Exit code `0` (`VERIFIED = True`). Locust ran with 5 concurrent users at spawn rate 5/s over localhost:8008 against health, ready, goals, audit, and analytics tasks.

---

## 3. Repeatability Evidence & Canonical Outbox Baseline (1 Warm-up + 3 Measured Repetitions)

Per Section 5 repeatability governance, key scenarios were executed with 1 warm-up run followed by at least 3 measured repetitions to verify stability and eliminate lucky single-measurement bias.

| Scenario | Metric Unit | Run 1 | Run 2 | Run 3 | Median | Min | Max | Std Dev | CV (%) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Real HTTP Mixed CRUD** | Throughput (RPS) | `202.49` | `181.86` | `240.17` | `202.49` | `181.86` | `240.17` | `24.14` | `11.60%` |
| **Goal Mutation Load** | p95 Latency (ms) | `44.48` | `34.48` | `31.90` | `34.48` | `31.90` | `44.48` | `5.43` | `14.68%` |
| **Plan Critical Path Read** | p95 Latency (ms) | `39.98` | `21.89` | `39.96` | `39.96` | `21.89` | `39.98` | `8.52` | `25.10%` |
| **Scheduler Canonical Solve** | Duration (s) | `0.445` | `0.476` | `0.445` | `0.445` | `0.445` | `0.476` | `0.015` | `3.28%` |
| **Flowable Concurrent Start** | p95 Latency (ms) | `269.41` | `295.07` | `255.00` | `269.41` | `255.00` | `295.07` | `16.57` | `6.07%` |
| **Outbox Real AMQP Dispatch** | Throughput (ev/s) | `38.80` | `44.12` | `39.41` | `39.41` | `38.80` | `44.12` | `2.38` | `5.83%` |

### Canonical Outbox Baseline Establishment
- **Run 1**: `38.80 events/sec` (38.798)
- **Run 2**: `44.12 events/sec` (44.115)
- **Run 3**: `39.41 events/sec` (39.407)
- **Median**: `39.41 events/sec`
- **Mean**: `40.77 events/sec`
- **Standard Deviation**: `2.38 events/sec`
- **Coefficient of Variation (CV)**: `5.83%`
- **Canonical Baseline Value**: **`39.41 events/sec`**  
  (Established by median of the 3 final `REAL_RABBITMQ` measured repetitions across real PostgreSQL `SKIP LOCKED` claim -> AMQP publish confirm -> PostgreSQL mark-dispatched commit).

---

## 4. Continuous Resource Sampling Evidence

Continuous resource sampling was conducted using `psutil` (host process level) and `docker stats --no-stream` (container level).

### A. Host Process Utilization (psutil)
> [!NOTE]
> **Host CPU Sampler Audit & Resolution**:  
> An earlier draft reported `0.00%` host CPU for `e7_uvicorn_api` and `benchmark_runner`. Audit revealed a defect in `tests/performance/resource_sampler.py`: `psutil.Process` instances were being reinstantiated on each tick without retaining persistent objects, and in `psutil`, an initial call to `cpu_percent(interval=None)` on a newly instantiated process always returns `0.0`. In addition, synchronous execution of `docker stats` stalled sampling.  
> The sampler was corrected to maintain persistent `psutil.Process` objects primed at startup, with Docker polling decoupled into a background task. Representative load verification confirmed non-zero host CPU under workload:

| Process | Mean CPU (%) | Peak Observed CPU (%) | Mean RSS (MB) | Peak RSS (MB) | Measurement Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **e7_uvicorn_api** (Live API & Workers) | `26.53%` | `60.10%` | `195.69 MB` | `195.73 MB` | **EMPIRICAL_MEASURED_VALID** |
| **benchmark_runner** (Pytest / Harness) | `25.27%` | `40.10%` | `193.79 MB` | `193.84 MB` | **EMPIRICAL_MEASURED_VALID** |

### B. Docker Container Utilization (docker stats)
> [!IMPORTANT]
> **Docker Multi-Core CPU Metric Interpretation**:  
> Docker stats CPU percentages represent aggregate utilization across all available CPU cores (100% = 1 logical core). On this 12-core host system (total machine capacity = 1200%), the peak RabbitMQ observation of `262.95%` represents ~2.63 cores active out of 12 (~21.9% total host machine capacity). It does NOT represent 263% host saturation or machine resource exhaustion.

| Container | Mean CPU (%) | Peak Observed CPU (%) | Mean Memory (MB) | Peak Memory (MB) | Multi-Core Allocation Note |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **e7-postgres** (PostgreSQL 16) | `1.61%` | `4.97%` | `106.28 MB` | `108.60 MB` | Nominal DB resource footprint |
| **e7-rabbitmq** (RabbitMQ 3.13) | `44.48%` | `262.95%` | `204.95 MB` | `207.20 MB` | Peak ~2.63 cores during AMQP broker confirmation bursts |
| **e7-flowable** (Flowable 7.0.0) | `2.89%` | `15.96%` | `360.20 MB` | `361.20 MB` | JVM workflow engine baseline |

---

## 5. Mathematical Correction: Academic Solver Scaling Analysis

The initial draft claim of "linear multi-core CPU scaling across thread pool without degradation" has been audited and mathematically corrected:

- **Single Solve (Concurrency 1)**: `465.61ms` per job = `2.15 solves/sec`.
- **Concurrent 4 Solves (Concurrency 4)**: Wall-clock duration `1.420s` for 4 jobs = `2.82 aggregate solves/sec`.
- **Aggregate Throughput Gain**: `+31.2%` throughput improvement (`2.82` vs `2.15` solves/sec).
- **Per-Job Latency Impact**: Average solve duration rose from `465.61ms` to `540.0ms` (`+16.0%` per-job latency increase due to shared CPU contention).
- **Correctness Invariance**: `0` hard constraint violations across all solves. Zero thread contention deadlock.
- **Authoritative Conclusion**:  
  > *"Concurrent execution improves aggregate throughput, with moderate per-job latency increase and no correctness degradation."*

---

## 6. Flowable SLA Timer Latency Claim Reconciliation

Per Section 6 governance reconciliation, the earlier claim that *"Flowable timer polling latency mean < 50ms"* was reviewed for evidentiary provenance:
- An end-to-end timer-fire latency benchmark recording scheduled timestamp vs actual fire timestamp was not instrumented in Phase 8C.
- **Authoritative Statement**:  
  > *"Flowable SLA timer registration and processing path verified; precise timer-fire latency not benchmarked in Phase 8C."*
- The unsupported numeric `<50ms` claim has been completely removed from the performance baseline.

---

## 7. Correctness Finding PERF-CORR-001 (Task Intelligence Detached Instance)

During the performance run, accessing model attributes on the result returned by `PlanService.update_task_progress()` failed with SQLAlchemy `DetachedInstanceError`.
- **Finding ID**: `PERF-CORR-001`
- **Classification**: **PRODUCTION CORRECTNESS DEFECT** (not a performance optimization).
- **Root Cause**: The UnitOfWork context committed the transaction prior to refreshing the updated `TaskRecord`, expiring instance attributes upon session closure.
- **Remediation**: Added `await uow.session.flush()` and `await uow.session.refresh(updated)` prior to `await uow.commit()` in `app/task_intelligence/service.py`.
- **Semantics Audit**: Transaction boundaries remain unchanged; all mutations occur in a single atomic transaction. Rollback semantics verified upon error.
- **Regression Test**: Added dedicated regression test `tests/unit/task_intelligence/test_task_update_regression.py` (`2 passed`).
- **Status**: **RESOLVED / REGRESSION_TESTED**.

---

## 8. Synthetic Dataset Tier Classification & Versioning

### Dataset Contradiction Resolution
Audit of `docs/phase-8/performance/dataset_manifest.json`, `tests/performance/datasets.py`, and project logs identified the origin of the discrepancy between reports:
1. **Initial Draft Specification (`SYNTHETIC_TARGET_V1`)**: Conceived in early Phase 8.0/8C planning with smaller entity counts (10 tenants, 30 users, 20 goals, 20 plans, 120 tasks, 25 resources, 20 jobs, 15 workflow instances, 200 outbox records).
2. **Expanded Generator (`SYNTHETIC_TARGET_V2`)**: When implementing `tests/performance/datasets.py` and the dataset manifest for multi-tenant stress and scaling evaluation, the dataset was intentionally expanded by ~5x-8x (10 tenants, 50 users, 100 goals, 100 plans, 1000 tasks, 200 resources, 50 schedule jobs, 50 workflow instances, 1000 audit records, 1000 outbox records, 1000 inbox records).
3. **Reconciliation**: The phrase "Entity Counts Preserved" was factually incorrect. The dataset was intentionally expanded from V1 to V2. Both versions are now explicitly versioned and tracked.
4. **Scale Governance**: Neither tier is customer scale; both are classified as `SYNTHETIC_TARGET_TIER_NOT_AUTHORITATIVE_CUSTOMER_SCALE`.

### Version Comparison Table
| Attribute | `SYNTHETIC_TARGET_V1` | `SYNTHETIC_TARGET_V2` | Change Type | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Classification** | NOT_AUTHORITATIVE_CUSTOMER_SCALE | NOT_AUTHORITATIVE_CUSTOMER_SCALE | Preserved | Governance Enforced |
| **Seed Mode** | Deterministic (seed=42) | Deterministic (seed=42) | Preserved | Cryptographic Seed |
| **Tenants** | `10` | `10` | Identical | Active |
| **Users / Principals** | `30` | `50` | Expanded (+66.7%) | Active |
| **Goals** | `20` | `100` | Expanded (+400%) | Active |
| **Plans** | `20` | `100` | Expanded (+400%) | Active |
| **Tasks** | `120` | `1000` | Expanded (+733%) | Active |
| **Resources** | `25` | `200` | Expanded (+700%) | Active |
| **Schedule Jobs** | `20` | `50` | Expanded (+150%) | Active |
| **Workflow Instances** | `15` | `50` | Expanded (+233%) | Active |
| **Workflow Tasks** | `30` | `250` | Expanded (+733%) | Active |
| **Audit Events** | `200` | `1000` | Expanded (+400%) | Active |
| **Outbox Records** | `200` | `1000` | Expanded (+400%) | Active |
| **Inbox Records** | `200` | `1000` | Expanded (+400%) | Active |
| **Calendar Availability**| `100` | `500` | Expanded (+400%) | Active |
| **Status** | `SUPERSEDED_INITIAL_SPEC` | `ACTIVE_MEASURED_TARGET` | Formally Tracked | Active Baseline |

### Benchmark to Dataset Mapping
| Benchmark Suite | Dataset / Version Used | Depends on TARGET Tier? | Tenants | Request / Event Count | Concurrency |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **HTTP CRUD (ASGI)** | `ISOLATED_FIXTURE_TENANTS` | No (Fixed probe/mutation batches) | 5 | 260 | 1 - 5 |
| **HTTP CRUD (Real HTTP)** | `ISOLATED_FIXTURE_TENANTS` | No (Uvicorn TCP localhost:8008) | 5 | 260 | 1 - 5 |
| **Analytics Scaling** | `SYNTHETIC_TARGET_V2` | **Yes (Directly queries V2)** | 3 | 30 | 1 |
| **Planner Context Builder**| `ISOLATED_PLANNER_FIXTURE` | No (SyntheticContextAdapter) | 1 | 50 | 1 |
| **Planner Generation** | `ISOLATED_PLANNER_FIXTURE` | No (SemesterExamPlanner DAG) | 1 | 30 | 1 |
| **Scheduler Solvers** | `ADR_010_EDUCATION_DATASET_V1` | No (Phase 5 Feasible/Repair) | 1 | 3 | 1 |
| **Scheduler Concurrency** | `ADR_010_EDUCATION_DATASET_V1` | No (Phase 5 Feasible) | 4 | 4 | 4 |
| **Workflow Flowable** | `ISOLATED_WORKFLOW_FIXTURE` | No (BPMN two_step_approval) | 1 | 9 | 1, 3, 5 |
| **Outbox Throughput** | `ISOLATED_OUTBOX_BATCH` | No (100 events to real AMQP) | 1 | 100 | 1 |
| **Outbox Repeatability** | `ISOLATED_OUTBOX_BATCH` | No (60 events across 4 runs) | 1 | 60 | 1 |
| **Inbox Consumer** | `ISOLATED_INBOX_BATCH` | No (50 original + 50 dupes) | 1 | 100 | 5 |
| **Repair / Risk Queue** | `ISOLATED_REPAIR_FIXTURE` | No (Durable repair enqueue) | 1 | 20 | 1 |

---

## 9. Transport & Broker Path Clarity Proofs

1. **Transactional Outbox Benchmark Path**:
   - The measured dispatch throughput (`38.80 - 44.12 events/sec`, median **`39.41 events/sec`**) was benchmarked against the real AMQP broker: `LOCAL_REAL_RABBITMQ` (`amqp://guest:guest@localhost:5672/`, exchange `e7.domain.events`) using `RabbitMqEventPublisher` with AMQP publisher confirms enabled and transactional `SKIP LOCKED` lease-and-settle in `e7-postgres`.
   - Distinct from the mock publisher path (`BenchmarkPublisher`), which achieves purely in-memory throughput (~454 events/sec).
   - Proven path: PostgreSQL claim (`SELECT FOR UPDATE SKIP LOCKED`) -> `RabbitMqEventPublisher` -> AMQP publisher confirmation ack -> PostgreSQL mark-dispatched commit (`UPDATE outbox SET status = 'DISPATCHED'`).
2. **Workflow Engine Benchmark Path**:
   - The measured workflow creation latency (`~250-700ms`) was benchmarked against `LOCAL_REAL_FLOWABLE` (port 8080 `http://localhost:8080/flowable-rest/service`).
   - Note on connection lifecycle: No TLS is involved in this local endpoint. Each request experiences a fresh TCP connection / HTTP client lifecycle without persistent connection pooling.
   - Root cause classification: Ephemeral `AsyncClient` creation without pooling is classified as **`PRIMARY OPTIMIZATION HYPOTHESIS` / `LIKELY CONTRIBUTING BOTTLENECK`** (not an experimentally proven direct root cause). Phase 8E will implement persistent connection pooling and conduct a formal A/B comparative benchmark.

---

## 10. Performance Findings Catalog

| Finding ID | Component | Scenario | Dataset / Version | Concurrency | Measurement Mode | Canonical Observed Result | Severity | Phase 8E Recommendation | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **PERF-001** | Flowable REST Adapter | Concurrent Process Starts | `ISOLATED_WORKFLOW_FIXTURE` | 1, 3, 5 | `LOCAL_REAL_FLOWABLE` (HTTP) | Latency scales ~250ms to ~700ms under 5x concurrency | LOW | Primary hypothesis: fresh TCP connection / client lifecycle. Implement connection pooling and execute A/B benchmark in Phase 8E. | OPEN |
| **PERF-002** | Transactional Outbox | Single-Event Dispatch Loop | `ISOLATED_OUTBOX_BATCH` | 1 | `LOCAL_REAL_RABBITMQ_BENCHMARK` | Canonical baseline = **`39.41 events/sec`** (median of 38.80, 44.12, 39.41; mean 40.77, std dev 2.38, CV 5.83%) vs 150 proposed | MEDIUM | Evaluate batch claim (`SELECT FOR UPDATE SKIP LOCKED` batching) & batch AMQP publisher pipeline in Phase 8E capacity controls. | OPEN |
| **PERF-003** | Operations Analytics | Deep Table Aggregation | `SYNTHETIC_TARGET_V2` | 1 | `SYNTHETIC_PROVIDER` / PostgreSQL | Query p95 increases as historical tasks and audit records scale | LOW | Review composite indexes on (tenant_id, created_at) and (tenant_id, status) in Phase 8E. | OPEN |
| **PERF-CORR-001** | Task Intelligence | Task Progress Update | Unit regression fixture | 1 | PostgreSQL UnitOfWork | DetachedInstanceError on post-commit task attribute access | MEDIUM | Resolved via flush and refresh before commit; atomic transaction and rollback-on-error verified. | **RESOLVED / REGRESSION_TESTED** |

---

## 11. Database Connection Pool Observability
- **Initial Pool Status**: Size `5`, Checked Out `0`, Overflow `-5`
- **Final Pool Status**: Size `5`, Checked Out `0`, Overflow `-5`
- **Pool Exhaustion / Timeout Events**: `0`

---

## 12. Closure Rule Verification & Governance

- [x] **Dataset-Version Contradiction Resolved**: `SYNTHETIC_TARGET_V1` (initial spec) and `SYNTHETIC_TARGET_V2` (measured expanded target) formally versioned and tracked.
- [x] **Benchmark-to-Dataset Mapping Complete**: Every benchmark mapped to its respective dataset/fixture and dependency on TARGET tier explicitly declared.
- [x] **Canonical Real Outbox Baseline Established**: Median of 3 final REAL_RABBITMQ repetitions (**`39.41 events/sec`**) established as the single canonical value.
- [x] **PERF-002 & Target Provenance Reconciled**: Canonical value `39.41 events/sec` used consistently across all artifacts.
- [x] **Mock vs Real Outbox Disambiguated**: Real RabbitMQ confirmed path proven and separated from mock publisher.
- [x] **Flowable Connection Wording Corrected**: "TLS handshake" removed; accurate "fresh TCP connection / HTTP client lifecycle" wording used.
- [x] **Flowable Root Cause Downgraded**: Classified as `PRIMARY OPTIMIZATION HYPOTHESIS` / `LIKELY CONTRIBUTING BOTTLENECK`, deferred to Phase 8E A/B benchmark.
- [x] **Host Process CPU Sampler Defect Fixed & Verified**: Sampler updated with persistent primed psutil.Process objects; non-zero empirical CPU samples verified and recorded.
- [x] **Docker Resource Metrics Interpreted**: Docker multi-core CPU percentages documented (peak RabbitMQ 262.95% = ~2.63 cores out of 12 cores, not host saturation).
- [x] **ASGI vs Real HTTP Interpretation Reconciled**: Re-labeled as `SIDE-BY-SIDE BASELINES — NOT DIRECT A/B TRANSPORT OVERHEAD`, removing causal attribution to network overhead.
- [x] **Locust Dependency Declared**: `locust>=2.31.0` and `psutil>=5.9.0` declared in `pyproject.toml` under `[project.optional-dependencies] eval`.
- [x] **Authoritative Requirements Passing**: Academic scheduling solve `< 60s` (`0.411s`), feasibility `0 hard violations`, repair invariance `0 moved`.
- [x] **PERF-CORR-001 Resolved & Tested**: Regression test `tests/unit/task_intelligence/test_task_update_regression.py` passes (`2 passed`).
- [x] **No Unresolved Correctness Defects Open**: Full suite green.
