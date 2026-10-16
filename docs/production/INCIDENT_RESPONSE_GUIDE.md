# E-7 Operations Engine — Incident Response, Triage & Observability Guide

**Document Version:** 1.0.0  
**Phase:** Phase 8F Canonical Production Documentation  
**Escalation Lead:** `ON_CALL_CONTACT = ORGANIZATION_DEFINED`  
**Escalation Channel:** `COMMUNICATION_CHANNEL = ORGANIZATION_DEFINED`  

---

## 1. Incident Severity Definitions

**Governance Classification:** `PROPOSED_OPERATIONAL_PRACTICE`

Operational incidents are categorized by engineering severity based on system impact:

| Severity | Definition | Target First Response | Example Scenario |
|---|---|---|---|
| **SEV-1** | Critical complete system outage or active security breach affecting all tenants. | Immediate / Priority | PostgreSQL offline, API cluster down, credential compromise. |
| **SEV-2** | Major subsystem failure or degraded capacity affecting a subset of workflows/solvers. | Standard Operational | RabbitMQ partition, Flowable offline, outbox queue backlog > 1000. |
| **SEV-3** | Minor operational disruption with functioning graceful degradation. | Next Business Window | Non-blocking external sync failure (Google Calendar webhook backlog). |
| **SEV-4** | Informational anomaly, telemetry glitch, or isolated transient error. | Scheduled Triage | Transient OTel collector connection drop, single task retry. |

---

## 2. Comprehensive Incident Triage Matrix

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR` & `PROPOSED_OPERATIONAL_PRACTICE`

| Scenario ID | Symptom | Likely Impact | First Checks | Safe Mitigation | Runbook Link | Escalation Condition |
|---|---|---|---|---|---|---|
| **INC-01** | **API Unavailable** | Complete HTTP traffic loss | Check process supervisor, port 8000 listening state, and container health probes. | Restart Uvicorn workers; check memory/OOM events. | [DEPLOYMENT_RUNBOOK.md](file:///f:/operations-engine/docs/production/DEPLOYMENT_RUNBOOK.md) | Unresponsive > 5 min. |
| **INC-02** | **PostgreSQL Unavailable** | All API calls return 500/503 | Test `pg_isready -h <host>`, review PostgreSQL error logs and disk space. | Failover to standby replica; resolve disk volume saturation. | [POSTGRES_BACKUP_RESTORE.md](file:///f:/operations-engine/docs/phase-8/recovery/POSTGRES_BACKUP_RESTORE.md) | Database corruption. |
| **INC-03** | **RabbitMQ Unavailable** | Event publishing falls back to outbox DB buffer | Check broker port 5672, node memory alarms, and disk alarms. | Clear cluster node alarms; restart RabbitMQ node. | [RABBITMQ_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/RABBITMQ_RECOVERY.md) | Node partition split-brain. |
| **INC-04** | **Flowable Unavailable** | Workflow task completions fail (503) | Check Flowable port 8080 and Flowable PostgreSQL schema. | Restart Flowable container; E-7 API idempotently buffers actions. | [FLOWABLE_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/FLOWABLE_RECOVERY.md) | Engine database desync. |
| **INC-05** | **Worker Stalled** | Outbox events and solver jobs remain in `REQUESTED` | Check `app.worker` process status and worker CPU utilization. | Restart worker process; unexpired leases will reclaim cleanly after 30s. | [WORKER_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/WORKER_RECOVERY.md) | Persistent deadlock loop. |
| **INC-06** | **Outbox Backlog** | `outbox_events` table grows continuously | Query `SELECT count(*) FROM outbox_events WHERE published_at IS NULL`. | Verify RabbitMQ health; increase worker concurrency settings. | [WORKER_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/WORKER_RECOVERY.md) | Backlog > 10,000 events. |
| **INC-07** | **Inbox Message Errors** | Consumer handler exceptions in worker logs | Check DLQ (dead letter queue) and consumer error logs for poison messages. | Inspect failed payload; fix parsing bug; replay message. | [WORKER_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/WORKER_RECOVERY.md) | Repeated DLQ rejections. |
| **INC-08** | **Schedule Queue Backlog** | Solver jobs queue depth increases | Check `schedule_jobs` count in `REQUESTED` status. | Verify solver worker nodes; ensure CPU cores are not throttled. | [CONSTRAINT_CATALOG.md](file:///f:/operations-engine/docs/production/CONSTRAINT_CATALOG.md) | Solve wait time > 15 min. |
| **INC-09** | **Solver Budget Exhaustion** | Multiple jobs report `BUDGET_EXHAUSTED` | Check `max_solve_seconds` parameter and input problem size. | Relax tight time windows; provision additional worker CPU threads. | [CONSTRAINT_CATALOG.md](file:///f:/operations-engine/docs/production/CONSTRAINT_CATALOG.md) | Repeated benchmark timeouts. |
| **INC-10** | **Repair Queue Backlog** | Schedule repair requests queue up | Check `capacity_tenant_max_concurrent_repairs` quota metrics. | Wait for active repairs to complete; adjust repair time budget. | [CONSTRAINT_CATALOG.md](file:///f:/operations-engine/docs/production/CONSTRAINT_CATALOG.md) | Persistent queue saturation. |
| **INC-11** | **Quota Rejection Spike (429)** | Clients receive `429 Too Many Requests` | Review `e7.capacity.quota.rejections` metric. | Verify client batching behavior; confirm `Retry-After: 30` handling. | [API_REFERENCE.md](file:///f:/operations-engine/docs/production/API_REFERENCE.md) | Systemic quota exhaustion. |
| **INC-12** | **Auth Failure Spike (401)** | High rate of `auth.token_invalid` or `auth.service_key_invalid` | Check OIDC identity provider discovery endpoint and token expiration. | Verify Google OIDC JWKS reachability; inspect gateway clock skew. | [AUTH_RESTART_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/AUTH_RESTART_RECOVERY.md) | Global OIDC provider outage. |
| **INC-13** | **Service Credential Compromise** | Unauthorized requests detected with leaked API key | Identify `credential_id` from access logs or alert reports. | Execute immediate revocation via database update (see Sec 3.1). | [ERD.md](file:///f:/operations-engine/docs/production/ERD.md) | Key used across multiple IPs. |
| **INC-14** | **Google Calendar Outage** | Calendar sync push/pull failures | Check Google API status dashboard; check retry logs. | Engine gracefully pauses calendar sync; internal schedule remains intact. | [INTEGRATION_OUTAGE_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/INTEGRATION_OUTAGE_RECOVERY.md) | Google outage > 2 hours. |
| **INC-15** | **ERP / MySQL Outage** | Academic context read timeouts | Check MySQL host connectivity and network routing. | Engine uses cached context where available; planner falls back safely. | [INTEGRATION_OUTAGE_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/INTEGRATION_OUTAGE_RECOVERY.md) | Extended ERP disconnection. |
| **INC-16** | **E8 Notification Outage** | Notification dispatch failures | Verify `E8_NOTIFICATION_INTEGRATION` blocker status. | Confirm events remain safely buffered; no operational crash. | [INTEGRATION_OUTAGE_RECOVERY.md](file:///f:/operations-engine/docs/phase-8/recovery/INTEGRATION_OUTAGE_RECOVERY.md) | N/A (Gated external service). |
| **INC-17** | **OTel Failure** | Telemetry collector connection dropped | Check collector container status on port 4317/4318. | API and Worker buffer metrics locally; no user-facing degradation. | [DEPLOYMENT_RUNBOOK.md](file:///f:/operations-engine/docs/production/DEPLOYMENT_RUNBOOK.md) | Telemetry loss > 24 hours. |
| **INC-18** | **Cross-Tenant Alert** | Log alert: `auth.tenant_mismatch` or BOLA attempt | Inspect correlation ID, actor principal, and requested resource tenant. | Invalidate compromised session; block attacking IP at ingress gateway. | [API_REFERENCE.md](file:///f:/operations-engine/docs/production/API_REFERENCE.md) | Breach confirmed. |
| **INC-19** | **Backup Failure** | Scheduled `pg_dump` or WAL archive failed | Check backup storage disk volume, S3 permissions, and cron logs. | Rerun manual backup immediately; resolve storage quota bottleneck. | [POSTGRES_BACKUP_RESTORE.md](file:///f:/operations-engine/docs/phase-8/recovery/POSTGRES_BACKUP_RESTORE.md) | Missing backup > 24 hours. |
| **INC-20** | **PITR / Restore Incident** | Accidental data deletion requires point-in-time recovery | Review transaction logs to identify exact target recovery timestamp. | Execute PITR recovery runbook in an isolated environment first. | [POSTGRES_PITR.md](file:///f:/operations-engine/docs/phase-8/recovery/POSTGRES_PITR.md) | Production data loss. |

---

## 3. Security Incident Response Procedures

**Governance Classification:** `APPROVED_ADR_DECISION` (ADR-013) & `ORIGINAL_REQUIREMENT`

### 3.1 Compromised Service API Key Revocation (BREAK_GLASS / CONTROLLED_ADMIN_PROCEDURE)

Because no public admin HTTP endpoint is exposed to mutate production credentials, credential revocation is executed via a strictly governed **BREAK_GLASS / CONTROLLED_ADMIN_PROCEDURE**.

> [!CAUTION]
> **Operational Invariants:**
> - **Never delete rows from `service_api_credentials`:** Row deletion destroys immutable auditability and breaks foreign key traceability.
> - **No Plaintext Secret Handling:** Secrets are hashed using one-way SHA-256 digests (`secret_digest`). Operators must never attempt to view, log, paste, or manipulate plaintext secrets.
> - **Mandatory Transaction:** The operation must be executed within an explicit database transaction block with pre-verification and post-verification checks.

```sql
-- Step 1: Pre-Verification (Confirm credential ID, tenant ID, and current active status)
SELECT credential_id, tenant_id, service_subject, enabled, created_at, revoked_at
FROM service_api_credentials
WHERE credential_id = '<compromised_credential_id>';

-- Step 2: Atomic Revocation Transaction
BEGIN;

UPDATE service_api_credentials
SET enabled = false,
    revoked_at = NOW(),
    updated_at = NOW()
WHERE credential_id = '<compromised_credential_id>'
  AND enabled = true;

-- Step 3: Post-Verification Audit Check
SELECT credential_id, tenant_id, service_subject, enabled, revoked_at, updated_at
FROM service_api_credentials
WHERE credential_id = '<compromised_credential_id>';

COMMIT;
```
Because verifiers check `enabled == true` and `revoked_at IS NULL` on every incoming API request against PostgreSQL, revocation takes effect immediately across all distributed API replicas without requiring an application restart.

> [!NOTE]
> **Audit Trail Governance:** Direct break-glass database mutation is a controlled emergency procedure. The operator must record the corresponding incident identifier, operator identity, and justification in the canonical incident response tracking system and operational change log. Do not rely on ad-hoc database column overloading for incident governance.

### 3.2 OIDC / JWKS Provider Communication Failure
If Google OIDC JWKS keys cannot be fetched from the configured OIDC provider:
1. Verify egress DNS and HTTPS connectivity from API pods to `accounts.google.com`.
2. The internal authenticator uses a cached public key fallback (`google_oidc_cache_fallback_seconds: 3600`).
3. If public certificates have rotated upstream, restart API pods to force metadata refresh once network connectivity is restored.

### 3.3 Suspected Cross-Tenant Access Attempt
1. Retrieve the full audit trail for the `correlation_id`:
   ```bash
   GET /api/v1/audit?correlation_id=<alert_correlation_id>
   ```
2. Check `actor_id`, `actor_type`, and `resource_id`.
3. If confirmed malicious, disable the offending user account in the identity provider or revoke the service key.

---

## 4. Production Observability Guide

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR` & `PROPOSED_DASHBOARD_SIGNAL`

### 4.1 Telemetry Classification & Canonical Metrics

| Signal | Metric Name | Type | Telemetry Classification | Description |
|---|---|---|---|---|
| **Health** | `http_requests_total` | Counter | `PROPOSED_DASHBOARD_SIGNAL` | Gateway / Collector HTTP request counter (labels: `route`, `method`, `status_code`) |
| **Latency** | `http_request_duration_seconds` | Histogram | `PROPOSED_DASHBOARD_SIGNAL` | Gateway / Collector request latency distribution |
| **DB Pool Diagnostics** | `DatabaseManager.get_pool_status()` | Diagnostic Source | `IMPLEMENTED_DIAGNOSTIC_SOURCE` | Diagnostic Python method returning pool status dictionary (`size`, `checked_out`, `overflow`, `checked_in`) |
| **Quotas** | `e7.capacity.quota.rejections` | Counter | `IMPLEMENTED` | 429 quota rejections (labels: `solver_jobs`, `repair_jobs`, `plan_generation`, `workflow_burst`) |
| **Outbox Depth** | `e7.capacity.outbox.pending_depth` | UpDownCounter | `IMPLEMENTED` | Pending outbox backlog high-water mark |
| **Solver Queue** | `e7.capacity.schedule_queue.depth` | UpDownCounter | `IMPLEMENTED` | Depth of pending schedule solver jobs |
| **Solver Time** | `e7.scheduling.cpsat.duration` | Histogram | `IMPLEMENTED` | CP-SAT solver search duration in seconds |
| **Solver Out** | `e7.scheduling.solve.results` | Counter | `IMPLEMENTED` | Solves by outcome (`FEASIBLE`, `INFEASIBLE`, `BUDGET_EXHAUSTED`, `FAILED`) |
| **Outbox OK** | `e7.outbox.dispatch.successes` | Counter | `IMPLEMENTED` | Successful outbox message dispatches |
| **Outbox Err** | `e7.outbox.dispatch.failures` | Counter | `IMPLEMENTED` | Failed outbox dispatches (labels: `publish_error`, `timeout`, `database_error`, `unexpected`) |
| **Flowable Ops**| `e7.flowable.requests.total` | Counter | `IMPLEMENTED` | Flowable REST requests by operation and outcome |
| **Flowable RT** | `e7.flowable.roundtrip.duration` | Histogram | `IMPLEMENTED` | Flowable REST roundtrip duration in seconds |

### 4.2 The High-Cardinality Rule

> [!CAUTION]
> **Zero High-Cardinality Labels:** Prometheus and OpenTelemetry metric labels must **never** contain:
> - `tenant_id`
> - `user_id` / `principal_id`
> - `job_id` / `plan_id` / dynamic UUIDs
> - Arbitrary input strings or query parameters
>
> Adding dynamic identifiers to metric labels causes time-series explosion and degrades metric storage. Tenant-specific debugging must be conducted via structured logs and the `/api/v1/audit` endpoint.

### 4.3 Proposed Production Dashboards (Marked PROPOSED)
- `PROPOSED_DASHBOARD_1`: **API Gateway & Ingress Health** — HTTP throughput, 5xx rate, 429 quota rejections.
- `PROPOSED_DASHBOARD_2`: **Scheduling & Solver Engine** — Solve queue depth, solver duration percentiles, FEASIBLE vs BUDGET_EXHAUSTED ratio.
- `PROPOSED_DASHBOARD_3`: **Asynchronous Event Pipeline** — Outbox buffer count, RabbitMQ consumer lag, inbox processing rates.
- `PROPOSED_DASHBOARD_4`: **Resource & Database Capacity** — PostgreSQL connection pool saturation, Flowable connection pool usage.
