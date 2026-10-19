# Phase 9C — Monitoring & SLO Baseline Report

**Phase**: `9C`  
**Execution Status**: `PASS`  
**Environment**: `restricted_pilot`  
**Pilot Tenant ID**: `tenant-pilot-001`  
**Pilot Domain**: `Semester Examination`  
**Canary Run ID**: `canary-9b-20261019104030`  
**Drill Run ID**: `drill-9c-20261019113808`  
**Correlation ID**: `corr-drill-9c-20261019113808`  

---

## 1. Executive Summary

This document establishes the operational telemetry and bounded service level objective (SLO) baseline for the restricted pilot of tenant `tenant-pilot-001`. The baseline captures observed performance characteristics from real pilot execution runs, audits available telemetry instrumentation, distinguishes approved thresholds from observed empirical values, and documents failure/recovery behavior.

---

## 2. Available Telemetry Instrumentation

The operations engine integrates structured logging and metrics through the following native components:

1. **HTTP Ingress Latency & Access Logging**:
   - `CorrelationAndLoggingMiddleware` ([app/core/middleware.py](file:///F:/operations-engine/app/core/middleware.py)): Computes monotonic elapsed request execution time in milliseconds (`duration_ms`), injects `X-Correlation-ID`, and records structured JSON log events `http_request_completed`.
   - OpenTelemetry `FastAPIInstrumentor`: Captures `http.server.duration`, status codes, and active spans.
2. **Gateway Ingress Rate Limiting**:
   - `pilot-gateway` ([docker/pilot-gateway/nginx.conf](file:///F:/operations-engine/docker/pilot-gateway/nginx.conf)): Enforces rate limiting at 10 requests/second with a burst allowance of 20 requests. Rejections return `HTTP 429 Too Many Requests` with `Retry-After: 1`.
3. **Workflow Execution Metrics**:
   - `app/core/telemetry/flowable.py`: Emits metrics for Flowable round-trip execution duration and status code outcomes.
4. **Scheduling Solver Metrics**:
   - `app/core/telemetry/scheduling.py`: Measures CP-SAT solver wall-clock duration (`e7.scheduling.solve.duration`), total solve attempts, and result status distributions.
5. **Outbox Processing Metrics**:
   - `app/core/telemetry/outbox.py`: Tracks event emission attempts, successful transactional commits, and broker publish latency.
6. **Immutable Audit Storage**:
   - PostgreSQL `audit_events` table: Captures security, lifecycle, and operational feedback events with cryptographic hash-chaining.

### Currently Unavailable Measurements

- **Centralized Prometheus / OTel Collector Daemon**:
  - The restricted pilot compose profile runs without an external Prometheus scraper or OpenTelemetry collector container. Native instrumentation buffers metrics in memory and structured logs.
- **Cross-Service Distributed Tracing**:
  - External microservice distributed trace propagation is deferred from the restricted pilot scope.

---

## 3. Observed Operational & Business Metrics

Measurements collected during Phase 9B canary and Phase 9C operational drills:

| Metric Category | Metric | Observed Value | Approved Target / Policy |
|---|---|---|---|
| **API Availability** | Request Success Rate | `100.0%` (non-rate-limited) | `99.9%` (Design target) |
| **API Latency** | Min Latency | `7.01 ms` | Observed baseline |
| **API Latency** | Mean Latency | `10.10 ms` | Observed baseline |
| **API Latency** | p50 Latency | `10.47 ms` | Observed baseline |
| **API Latency** | p95 Latency | `13.89 ms` | Observed baseline |
| **API Latency** | Max Latency | `13.89 ms` | Observed baseline |
| **API Errors** | Unexpected 5xx | `0` | `0` (Zero tolerance) |
| **Rate Limiting** | Burst 429 Throttling | Observed on 8+ req burst | Approved (10 rps, burst=20) |
| **Scheduling** | Solver Runtime | `0.15 s` | Observed baseline |
| **Scheduling** | Constraint Violations | `0` | `0` (Hard invariant) |
| **Scheduling** | Result Status | `FEASIBLE` | `FEASIBLE` (Required) |
| **Scheduling** | Sessions Scheduled | `10` | 10 Grade 10 exam sessions |
| **Scheduling** | Assignments Reconciled | `22` | 22 database-persisted assignments |
| **Workflow** | Approvals Completed | `3` | Science, Humanities, Moderation |
| **Publication** | Schedule Lifecycle Status | `PUBLISHED` | `PUBLISHED` |
| **Audit** | Audit Events Stored | `171` | Immutable, non-decreasing |
| **Outbox** | Outbox Events Emitted | `5` | Transactionally coherent |
| **Health** | Gateway Health Probe | `HTTP 200` | `HTTP 200` |
| **Health** | Gateway Ready Probe | `HTTP 200` | `HTTP 200` |

---

## 4. Manual Baseline Comparison Status

In accordance with Phase 9C guidelines, business-value percentages or time savings must not be fabricated without authoritative business owner data:

```
MANUAL_BASELINE_COMPARISON = NOT_AVAILABLE_PENDING_BUSINESS_OWNER_INPUT
```

---

## 5. Approved Thresholds vs Observed Values

To prevent premature production SLO commitments:

1. **Approved Thresholds**:
   - Ingress Rate Limit: 10 req/s, burst=20 (`docker/pilot-gateway/nginx.conf`).
   - Solver Hard Constraints: Exactly 0 violations allowed for schedule publication.
   - Database Schema: Exactly Alembic migration `0014 (head)`.
   - Tenant Isolation: Zero cross-tenant data access or resource sharing.
2. **Merely Observed Empirical Baselines** (Non-contractual):
   - API p50 latency: ~10.5 ms.
   - API p95 latency: ~13.9 ms.
   - Solver CP-SAT execution duration: ~0.15 seconds.

---

## 6. Recovery & Failure Telemetry

During failure injection (rate-limit burst testing and simulated revision rollback):
- Gateway throttled excess requests returning `HTTP 429` with `Retry-After: 1`.
- Rollback execution recorded `pilot.rollback_executed` in immutable audit log.
- Post-rollback recovery successfully returned system to `HEALTHY` (`health=200`, `ready=200`).
