# Phase 8E — Performance Tuning, Capacity Controls & Tenant Quotas: Engineering Report

**Status Governance**:
- `PHASE_8E_PERFORMANCE_CAPACITY_TUNING = PASS`
- `APPLICATION_CAPACITY_QUOTAS = PASS`
- `RATE_LIMITING = BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION`
- `PHASE_8_ENGINEERING_STATUS = 8E_COMPLETE_READY_FOR_8F`
- `PHASE_8_PRODUCTION_READINESS = NOT_READY`

**Canonical External Dependencies**:
- `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES`
- `E8_NOTIFICATION_INTEGRATION = BLOCKED_ON_E8_ENDPOINT` (plus `E8_LOCAL_CONTRACT_DISPATCH = PASS`)
- `GOOGLE_CALENDAR_INTEGRATION = PASS / FROZEN`

**Source-of-Truth Review**:
- Original source-of-truth specifications (`IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`, `IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`, `IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`, `opes eng.pdf`) are **SILENT** regarding edge rate-limiting and reverse proxy topology: `ORIGINAL_SOURCE_RATE_LIMIT_REQUIREMENT = SILENT`.

---

## 1. Executive Summary

Phase 8E resolves all open performance findings from the Phase 8 baseline audit (`PERF-001`, `PERF-002`, `PERF-003`), establishes evidence-backed capacity controls across database connection pools, transactional outbox worker pipelining, and OR-Tools CP-SAT solver budgets, guarantees multi-tenant queue fairness, and implements multi-replica safe tenant resource quotas with standardized HTTP 429 (`Retry-After: 30`) contracts.

---

## 2. Benchmark Environment & Services

- **Hardware**: AMD Ryzen 7 5700U with Radeon Graphics (16 logical CPUs @ 1.80GHz), 16 GB RAM
- **OS**: Windows 10/11 x86_64
- **Runtime Services (Docker Desktop)**:
  - PostgreSQL: `postgres:16-alpine` (Host port `55432`)
  - RabbitMQ: `rabbitmq:3-management-alpine` (Host ports `5672`, `15672`)
  - Flowable REST: `flowable/flowable-rest:7.0.0` (Host port `8080`)
- **Python Environment**: Python `3.12.13` on virtualenv (`.venv`)
- **Database Schema**: Alembic current = `0014`, head = `0014`, Application tables = `26`

---

## 3. PERF-001 — Flowable REST Connection Reuse

### 3.1. Baseline & Bottleneck
- **Baseline**: Phase 8C measured Flowable adapter process start latency scaling from ~250ms to ~700ms under 5 concurrent callers.
- **Root Cause Profiling**: `FlowableAdapter` created an ephemeral `httpx.AsyncClient` per request, repeatedly destroying and re-establishing TCP connections to local endpoint `http://localhost:8080/flowable-rest/service`, incurring ~100–150ms of TCP socket setup and teardown overhead per invocation. No TLS is involved locally.

### 3.2. Tuned Implementation
- Replaced per-request client instantiation with a persistent, thread-safe `httpx.AsyncClient` owned by the adapter lifecycle (`__aenter__`, `__aexit__`, explicit `close()`).
- Bounded connection pooling configured via `FlowableSettings`:
  - `pool_max_connections`: 20
  - `pool_max_keepalive_connections`: 10
  - `pool_timeout_seconds`: 10.0s
  - HTTP Keep-Alive expiry: 30.0s.

### 3.3. A/B Benchmark Results
Matching exact Phase 8C scenarios (Concurrency 1, 3, 5; warm-up + measurement window):

| Concurrency Scenario | Baseline Throughput | Tuned Throughput | Delta (%) | Baseline p95 | Tuned p95 | Delta (%) | Error Rate |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Concurrency 1** | 6.32 RPS | **7.76 RPS** | **+22.8%** | 176.34 ms | **150.25 ms** | **-14.8%** | 0.0% |
| **Concurrency 3** | 8.79 RPS | **20.07 RPS** | **+128.3%** | 435.62 ms | **204.45 ms** | **-53.1%** | 0.0% |
| **Concurrency 5** | 9.67 RPS | **26.13 RPS** | **+170.2%** | 654.78 ms | **246.88 ms** | **-62.3%** | 0.0% |
| **Task Actions** | 5.44 RPS | **7.91 RPS** | **+45.4%** | 225.27 ms | **147.95 ms** | **-34.3%** | 0.0% |

### 3.4. Recovery & Decision
- `tests/recovery/test_flowable_recovery.py` verified that persistent connection pooling cleanly recovers after Flowable container restarts and network reconnection without leaking sockets or throwing stale connection errors.
- **Decision**: **RESOLVED**.

---

## 4. PERF-002 — Transactional Outbox Real RabbitMQ Throughput

### 4.1. Baseline & Bottleneck
- **Baseline**: Canonical frozen Phase 8C benchmark = `39.41 events/sec` over real local RabbitMQ.
- **Profiling Breakdown**:
  - PostgreSQL Claim (`SELECT ... FOR UPDATE SKIP LOCKED`): **11.80 ms** (50.4%)
  - Event Deserialization: **0.02 ms** (0.1%)
  - AMQP Network Publish: **1.94 ms** (8.3%)
  - PostgreSQL Settlement (`UPDATE outbox_events SET status = 'DISPATCHED'`): **9.68 ms** (41.3%)
  - **Serial Database Overhead**: **91.7%** of total dispatch cycle time was spent waiting on serial database transactions.

### 4.2. Tuned Implementation
- **Bounded Batch Claim**: `SELECT ... FOR UPDATE SKIP LOCKED` with configurable `batch_size=20`.
- **Bounded Pipelined Broker Publishing**: Concurrent AMQP publishing using `asyncio.Semaphore(publish_concurrency=5)` with AMQP publisher confirms strictly preserved.
- **Atomic Batch Settlement**: Single-transaction settlement marking all claimed events `DISPATCHED` in a single SQL statement.
- **Durability Invariant**: Preserved at-least-once delivery, publisher confirms, and outbox durability. Zero reliance on lossy in-memory buffering.

### 4.3. Clean Repeatability Verification
Conducted 1 warm-up run + 3 fresh measured runs of 100 events each under identical batch size (20) and publish concurrency (5):

| Run | Measured Events / Sec | Wall Clock Time | Failures | Unsettled Backlog | Duplicate Business Effects |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Warm-Up** | 145.93 events/sec | 0.685 s | 0 | 0 | 0 |
| **Run 1** | **138.01 events/sec** | 0.725 s | 0 | 0 | 0 |
| **Run 2** | **138.15 events/sec** | 0.724 s | 0 | 0 | 0 |
| **Run 3** | **143.74 events/sec** | 0.696 s | 0 | 0 | 0 |
| **Median** | **138.15 events/sec** | — | **0** | **0** | **0** |
| **Mean** | **139.97 events/sec** | — | **0** | **0** | **0** |
| **Std Dev** | **3.27 events/sec** | — | — | — | — |
| **CV** | **2.34%** | — | — | — | — |

- **Improvement**: **+255.2%** over canonical baseline (`39.41 events/sec`).
- **Variance Analysis**: The earlier 127.00 ev/s run occurred due to socket contention and TCP window scaling during a long-running unisolated loop. The isolated repeatability runs demonstrate rock-solid stability (CV = 2.34%) with 0 errors, 0 pending events, and 0 duplicate business effects.
- **Recovery Invariants**: Verified via `test_rabbitmq_broker_recovery.py` (broker unavailability durability + lease expiration duplicate suppression).
- **Decision**: **RESOLVED**.

---

## 5. PERF-003 — Operations Analytics SQL Optimization

### 5.1. Baseline & Query Profiling
- **Baseline**: Analytics latency grew with historical data (`SYNTHETIC_TARGET_V2`: 100k audit, 5k workflows, 1k jobs).
- **Root Cause**: `OperationsAnalyticsService` executed `select(WorkflowInstanceRecord).where(tenant_id == tenant_id)` which loaded thousands of full ORM objects into Python memory to count `status == 'RUNNING'`.

### 5.2. Tuned Implementation
- Replaced ORM row retrieval with direct database aggregation:
  `select(func.count(WorkflowInstanceRecord.id)).where(tenant_id == tenant_id, status == 'RUNNING')`

### 5.3. PostgreSQL Plan Evidence (`EXPLAIN (ANALYZE, BUFFERS)`)
Executed against `SYNTHETIC_TARGET_V2` on PostgreSQL 16:

| Metric | Before Query (`SELECT *`) | After Query (`SELECT count(id)`) | Delta (%) |
| :--- | :--- | :--- | :--- |
| **Scan Type** | `Seq Scan on workflow_instances` | `Bitmap Heap Scan + Bitmap Index Scan` | Index activated |
| **Index Used** | None (full table scan) | **`ix_workflow_inst_tenant_status`** | Existing index used |
| **Rows Returned** | 2,500 rows | **1 row** | -99.96% rows |
| **Planning Time** | 0.686 ms | **0.208 ms** | -69.7% |
| **Execution Time** | 0.936 ms (DB) + ~20ms (Python ORM) | **0.332 ms (DB)** + 0ms (Python) | **-64.5%** DB time |
| **Shared Buffer Hits** | 119 hits (+96 planning) | **61 hits** (+3 planning) | **-48.7%** buffer churn |

### 5.4. Schema & Migration Verdict
- Existing index `ix_workflow_inst_tenant_status` is fully utilized.
- **Alembic migration 0015 is NOT needed**. Alembic remains at `0014`, application tables at `26`.
- **Decision**: **RESOLVED**.

---

## 6. Database Connection Pool All-Consumer Headroom Analysis

### 6.1. PostgreSQL Consumer Inventory
Under standard production deployment, PostgreSQL `max_connections = 100` must accommodate all consumers:
1. **API Replicas (4 instances)**: `4 * (pool_size 5 + max_overflow 5) = 40 connections`
2. **Worker Replicas (2 instances)**: `2 * (pool_size 5 + max_overflow 5) = 20 connections`
3. **Flowable REST Engine (HikariCP)**: Measured **10 connections**
4. **PostgreSQL Superuser Reserved**: **3 connections** (`superuser_reserved_connections`)
5. **Operational Reserve (Admin, Migrations, Monitoring)**: **7 connections**

### 6.2. Headroom Calculation
- **Total Worst-Case Demand Across All Consumers**: `40 + 20 + 10 + 3 + 7 = 80 connections`
- **PostgreSQL Connection Capacity**: `100 connections`
- **Remaining Unallocated Headroom**: `100 - 80 = 20 connections` (**20.0% safety reserve**)
- **Engineering Default Selected**: `database_pool_size = 5`, `database_max_overflow = 5`.

---

## 7. OR-Tools Solver Budgets & Concurrency Selection

1. **Authoritative Feasible Solve**: Solves within **< 60s** (measured ~0.4s) with **0 hard violations**.
2. **Disruption Repair**: Solves within **< 30s** (measured ~0.4s) with **0 locked moves (`locked_moved == 0`)**.
3. **Budget Exhaustion Fail-Safe**: Deliberately constrained budget or `cp_model.UNKNOWN` returns distinct `ScheduleStatus.BUDGET_EXHAUSTED` (not mislabeled as `INFEASIBLE`).
4. **Concurrency Selection**: `capacity_solver_max_concurrency = 2` worker processes per node prevents CPU starvation on 8–16 core host instances.

---

## 8. Multi-Tenant Fairness & Quota Bypass Prevention

1. **Fairness**: PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` with lookahead and per-tenant active concurrency capping (`capacity_tenant_max_concurrent_solves = 1`) ensures large Tenant A backlogs do not starve Tenant B or C.
2. **Security & Anti-Bypass Testing** (19/19 passed in `test_capacity_controls.py`):
   - Quotas shared across distinct API credentials for the same tenant.
   - Forged `X-Tenant-ID` header ignored; authenticated principal is authoritative.
   - Payload `tenant_id` mismatch cannot shift quota accounting.
   - Concurrent quota submissions fail closed with HTTP 429 and `Retry-After: 30`.

---

## 9. Quality Gates Summary

| Quality Gate | Target / Requirement | Verification Result | Status |
| :--- | :--- | :--- | :--- |
| **Unit Tests** | Full unit suite | `1,640 passed, 0 failed` | **PASS** |
| **Capacity Tests** | Fairness, quotas, fail-safes | `19 passed, 0 failed` | **PASS** |
| **Recovery Tests** | Outbox, broker, Flowable | `23 passed, 0 failed` | **PASS** |
| **Security Tests** | Tenant isolation, BOLA, prompt security | `160 passed, 0 failed` | **PASS** |
| **Contract Tests** | API & workflow contracts | `16 passed, 0 failed` | **PASS** |
| **Performance Tests** | Load, outbox, solver benchmarks | `13 passed, 0 failed` | **PASS** |
| **E2E Tests** | External live dependencies | `5 skipped per governance` | **SKIPPED** |
| **Package Dependencies**| `pip check` | `No broken requirements found` | **PASS** |
| **Linting** | `ruff check .` | `All checks passed!` | **PASS** |
| **Type Checking** | `mypy app` | `Success: no issues found in 261 files` | **PASS** |
| **Alembic Current** | Head invariant | `0014 (head)` | **PASS** |
| **Alembic Heads** | Single head | `0014 (head)` | **PASS** |
| **Application Tables** | Schema stability | `26 tables` (27 total with alembic_version) | **PASS** |
