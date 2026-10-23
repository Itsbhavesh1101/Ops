# E-7 Operations Engine — Stage 6 Production Readiness & Infrastructure Report

**Document:** Authoritative Stage 6 Production Infrastructure, Security, Observability & Disaster Recovery Report  
**System:** IR Infotech E-7 Operations Engine  
**Accepted Baseline Commits:**
- Stage 4 Baseline: `4f20b02fba95a4a942e57a3c759f09c89a068f1e`
- Stage 5 Baseline: `ec62a4e3fb6f1822171efe5b17039cf1ca0f64db`
- Frozen Phase-8 Release Candidate: `03e6305256db3d838b472aaac50893ddb513414e`  
**Classification:** Technical Readiness Assessment & External Operational Governance  
**Overall Readiness Verdict:** `STAGE_6_INTERNAL_ACCEPTED_BLOCKED_EXTERNAL`  

---

## 1. Approved Infrastructure Architecture

The E-7 Operations Engine architecture is governed by accepted Architectural Decision Records (ADRs) and source-of-truth specifications:

* **Backend Application Tier:** FastAPI + Python 3.11 / 3.12 running under Uvicorn multi-worker process model. Implements stateless REST APIs, transactional outbox dispatching, and asynchronous event consumption.
* **Canonical Relational Database:** PostgreSQL 16 (Engine >= 16.15) as the single authoritative ACID System of Record (ADR-006). All application state (26 application tables, Alembic head `0014`) is stored here. E-7 Alembic does not manage external or Flowable tables.
* **Event Broker:** RabbitMQ 3.13+ using AMQP 0-9-1 (ADR-005). Provides durable topic exchanges: canonical internal domain-event exchange `e7.events` (governed by ADR-005, configurable via `E7_RABBITMQ_EXCHANGE_NAME`) and downstream notification exchange `e8.notifications` (governed by ADR-012). Separate production broker credentials where required. Supports publisher confirms and dead-letter queue routing.
* **Workflow & Human Task Engine:** Flowable 7.0.0 (BPMN 2.0 / DMN 1.3) operating as an isolated boundary (ADR-001). Native state is stored in a dedicated database (`flowable_db`); E-7 communicates with Flowable exclusively via REST (`/flowable-rest/service`).
* **Frontend Web Application:** React + TypeScript + Vite SPA (`ui/operations-ui`). Connects to FastAPI via reverse proxy; holds tokens strictly in memory; zero client secrets.
* **Observability Architecture:** OpenTelemetry SDK -> OTLP HTTP -> OpenTelemetry Collector Contrib (0.162.0) -> Prometheus (3.15.0) -> Alertmanager (0.28.0). Strict absence of non-approved external dashboards (e.g. Grafana is absent from production configuration).
* **Production Identity:** Google Workspace / Google Cloud Identity via OpenID Connect (OIDC Core 1.0). Subject-first identity binding and server-controlled role mapping.
* **E-8 Engagement Integration:** Dual-transport boundary per ADR-012 (canonical AMQP topic exchange `e8.notifications` default; authenticated HTTP POST `/v1/notifications/commands` alternative). At-least-once E-8 publication; downstream consumers must implement idempotency. Clean-room data minimization (zero student PII, zero plain emails/phones).

---

## 2. Internally Verified Configurations

Every component has been verified under strict governance classifications:

| Component / Layer | Governance Status | Verification Evidence | Operational Role |
| :--- | :---: | :--- | :--- |
| **FastAPI API Container** | `INTERNALLY_VERIFIED` | `docker/Dockerfile`, `tests/unit/test_docker_compose.py` | Multi-worker REST API on port 8000; non-root user `e7user`. |
| **Outbox Worker** | `INTERNALLY_VERIFIED` | `app/worker.py`, `tests/unit/test_outbox_dispatcher.py` | Leased event batch dispatcher; atomic DB commit; graceful SIGTERM. |
| **Scheduler Worker** | `INTERNALLY_VERIFIED` | `app/scheduling/worker.py`, `tests/unit/test_docker_compose.py` | Dedicated OR-Tools CP-SAT constraint optimization worker. |
| **Notification Worker** | `INTERNALLY_VERIFIED` | `app/integrations/engagement/worker.py` | Dedicated E-8 notification worker; at-least-once dispatch. |
| **PostgreSQL Engine Pool** | `INTERNALLY_VERIFIED` | `app/db/engine.py`, `tests/recovery/test_postgresql_recovery.py` | Asyncpg pool (`pool_size=10, max_overflow=20`), `pool_pre_ping=True`. |
| **Alembic Migrations** | `INTERNALLY_VERIFIED` | Linear chain 0001 -> 0014 (head), 26 tables | Idempotent startup checks; zero unapproved DDL migrations. |
| **Pilot Edge Gateway** | `INTERNALLY_VERIFIED` | `docker/pilot-gateway/nginx.conf`, compose pilot | Local reverse proxy, security headers, IP rate limit (10 r/s). |
| **OTel Collector Profile** | `INTERNALLY_VERIFIED` | `docker/observability/otel-collector.yaml` | Scrapes OTLP 4318; Prometheus exporter on 8889; runs under profile. |
| **Prometheus & Alert Rules**| `INTERNALLY_VERIFIED` | `deploy/prometheus/alert.rules.yml`, promtool | 6 operational rules + 11 external rules validated via promtool. |
| **Alertmanager Tree** | `INTERNALLY_VERIFIED` | `deploy/alertmanager/alertmanager.yml`, amtool | Routing tree, inhibition rules, receivers validated via amtool. |

---

## 3. Production Security Controls

1. **Least-Privilege Container Execution:** Multi-stage `docker/Dockerfile` based on pinned `python:3.11-slim@sha256:...`. Drops build tools (gcc, git), removes pip/setuptools/wheel from runtime, and runs as non-root user `e7user` (UID 10001).
2. **Network Perimeter & Ingress:**
   - Public/Private VPC separation: database, broker, collector, and workers run without public IP addresses.
   - HSTS (`Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`).
   - Content-Security-Policy (CSP) restricting frame ancestors (`frame-ancestors 'none'`), script sources, and object sources (`object-src 'none'`).
   - Frame Options (`X-Frame-Options: DENY`), MIME Sniffing protection (`X-Content-Type-Options: nosniff`), and Referrer Policy (`strict-origin-when-cross-origin`).
3. **Application Layer Protection:**
   - Request Body Size Limiter: `RequestBodySizeLimitMiddleware` strictly terminates payloads > 10MB with HTTP 413.
   - Monotonic Request Timing & Tracing: `CorrelationAndLoggingMiddleware` propagates `X-Correlation-ID` and OTel trace context without leaking query parameters or credentials.
   - CORS Policy: `CORSMiddleware` parses explicit allowed origins from `E7_CORS_ALLOWED_ORIGINS`; wildcards (`*`) are disallowed when credentials are enabled.
   - Capacity Quotas & Volumetric Rate Limiting: `TenantQuotaManager` rejects abusive tenant traffic with standard HTTP 429 and `Retry-After` headers.
4. **Institutional Database Protection:**
   - Read-only connection wrapper (`ReadOnlyDatabaseConnection`) executes `SET SESSION TRANSACTION READ ONLY` on acquisition.
   - In-process regex statement inspection blocks mutating keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `REVOKE`).
   - Zero student PII extraction; aggregate mathematical headcounts only.

---

## 4. Database Migration and Backup Procedures

1. **Alembic Schema Governance:**
   - Authoritative head: `0014` (`0014_service_api_credentials.py`). Total 26 application tables.
   - Linear upgrade procedure: `alembic upgrade head`. Preconditions verify database connectivity, active role permissions, and zero schema drift.
   - Rollback limitations: Schema downgrades in production are restricted because structural rollbacks risk data loss on populated transactional tables. When an interrupted migration occurs, deployment teams follow an approved roll-forward strategy or restore from a pre-migration snapshot.
   - Backup-before-migration mandatory requirement: Production deployments must create a verified snapshot or pg_dump archive before executing Alembic migrations.
2. **PostgreSQL Backup & Recovery:**
   - Logical Backup: `scripts/recovery/backup_logical.py` generates compressed custom-format archives (`pg_dump -Fc`) accompanied by SHA-256 integrity manifests (`.manifest.json`).
   - Restore Validation: `scripts/recovery/restore_logical.py` conducts isolated restores strictly into disposable drill databases (e.g. `e7_recovery_drill_disposable`) to verify schema integrity (head `0014`, 26 tables, and table row counts including `inbox_messages`) without risking primary data.
   - Physical Point-In-Time Recovery (PITR): Prerequisites include continuous WAL archiving (`archive_mode = on`, `archive_command` copying WAL segments to object storage) and periodic base backups (`pg_basebackup`).
   - Disaster Recovery Objective Classifications:
     - `APPROVED_TARGET`: Documented architectural targets approved for production SLA:
       - Recovery Time Objective (RTO): < 30 minutes for automated failover / logical restore.
       - Recovery Point Objective (RPO): < 5 minutes via continuous WAL stream replay.
     - `INTERNALLY_TESTED`: Measured outcomes executed and timed on local disposable infrastructure:
       - Logical Restore Drill (`test_logical_backup_and_restore_drill`): Restore duration ~0.3s - 1.2s; verified Alembic revision `0014`, 26 tables, and non-negative row counts across all key tables including `inbox_messages`.
       - Physical Base Backup + WAL PITR Drill (`test_pitr_physical_drill_isolated`): Base backup + continuous WAL archive replay to named restore point `restore_point_b` (excluding marker C).
       - Connection Pool Resilience (`test_connection_pool_recovery_and_pre_ping`): Verified socket termination eviction via `pool_pre_ping`.
     - `LIVE_INFRASTRUCTURE_VERIFIED`: Enterprise multi-AZ automated failover and cloud-bucket continuous WAL streaming are NOT live infrastructure verified; pending enterprise cloud database provisioning (`BLOCKED_EXTERNAL`).

---

## 5. RabbitMQ Reliability and DLQ Requirements

1. **Broker Topology Requirements:**
   - Exchanges: Durable topic exchanges declared with `durable=True`: canonical internal domain-event exchange `e7.events` (governed by ADR-005, configurable via `E7_RABBITMQ_EXCHANGE_NAME`) and downstream notification exchange `e8.notifications` (governed by ADR-012). Separate production broker credentials where required.
   - Message Publication: Persistent delivery mode (`delivery_mode=2`) and mandatory publisher confirms (`publisher_confirms=True`).
   - Queue Semantics: Quorum queues (`x-queue-type: quorum`) recommended for multi-node enterprise clusters with `delivery-limit` to prevent poison message loops.
   - Dead-Letter Handling: Direct dead-letter exchange (`dlx`) routing un-processable or expired messages to dedicated dead-letter queues (`*.dlq`) for offline inspection.
   - Prefetch & Flow Control: Consumer channel prefetch set to 10 (`prefetch_count=10`) with worker concurrency limits to prevent memory exhaustion.
   - Connection Recovery: `aio_pika.connect_robust` with exponential supervisory backoff (`retry_base=1.0s`, `max_delay=60.0s`, `multiplier=2.0`).
2. **Notification Reliability & Outbox Architecture Boundary:**
   - Governed by accepted Stage 5 at-least-once delivery guarantees (ADR-005, ADR-012).
   - Domain Outbox Publication: Domain events in `outbox_events` are claimed with leased tokens (`claimed_at`, `claim_token`) and marked published (`status='published'`) immediately upon publisher confirm from the internal domain exchange.
   - Downstream E-8 Notification Boundary: When downstream notification dispatch to E-8 (`e8.notifications` or HTTP) fails (e.g. transient broker outage or network timeout), the original domain-event outbox record in `outbox_events` has ALREADY been marked published. The notification consumer / dispatcher in E-7 maintains its own leased retry loop for downstream notification commands and reattempts delivery independently.
   - External Consumer Idempotency: Downstream consumers on E-8 must implement idempotency to handle potential duplicate notification deliveries under at-least-once guarantees.
   - Consumer failure disposition: unroutable or malformed messages are rejected (`basic_reject(requeue=False)`) into DLQ; transient failures raise exceptions to trigger supervisory redelivery.

---

## 6. Identity and Secrets Requirements

1. **Corporate Google Workspace OIDC:**
   - Asymmetric cryptographic verification via `GoogleOidcVerifier` against Google's public JWKS (`https://www.googleapis.com/oauth2/v3/certs`).
   - Subject-first identity resolution: users are keyed by immutable `sub` claim.
   - Recycled-email protection: email changes or domain reassignments reject conflicting principal mappings.
   - Hosted domain enforcement: `hd` claim verified against corporate domain (e.g. `irinfotech.com`).
   - Security Note: Custom Google ID-token implicit flow requires corporate IAM sign-off. Corporate alternatives (Backend OAuth 2.0 Authorization Code Flow with confidential client secret) are documented in `IDENTITY_TEAM_HANDOVER.md`.
2. **Secrets Governance:**
   - Zero committed plaintext secrets across the repository.
   - All credentials externalized via Pydantic `SecretStr` environment variables.
   - Secret Scanner (`scripts/release/scan_secrets.py`) verifies zero high-entropy keys or passwords in the working tree.
   - Secret rotation runbook documented for credentials (database passwords, broker credentials, service API keys).

---

## 7. Observability & Alerting Status

1. **Telemetry Pipeline:**
   - OpenTelemetry Python SDK instrumenting FastAPI (`FastAPIInstrumentor` with sanitizing server request hook to prevent query string capture).
   - Capacity and outbox metrics emitted by `CapacityTelemetry` (`app/core/telemetry/capacity.py`, `outbox.py`).
   - OpenTelemetry Collector exports Prometheus metrics on port 8889.
2. **Alert Rules Classification & Operational Status:**
   - **Offline PromQL Simulation (`promtool test rules`):** 6 alerting rules have been validated with offline simulated time-series unit tests (`tests/unit/test_alert_rules.py`). However, per engineering governance, offline simulation does not constitute live operational verification. Actual runtime operational status requires:
     1. Metric emission from running components
     2. Scrape compatibility with Prometheus
     3. Successful runtime rule evaluation
     4. Alert state transitions (`Inactive` -> `Pending` -> `Firing`)
     5. Alertmanager notification delivery
     Full live operational status remains pending live monitoring infrastructure deployment.
     - `E7OutboxBacklogAccumulating` (SEV-2)
     - `E7OutboxDeliveryFailuresSpike` (SEV-2)
     - `E7CapacityQuotaRejectionsSpike` (SEV-3)
     - `E7SolverQueueBacklogAccumulating` (SEV-3)
     - `E7SolverBudgetExhaustionSpike` (SEV-2)
     - `E7FlowableUnavailable` (SEV-1)
   - **11 External Infrastructure Rules:** Syntactically verified via `promtool check rules`; runtime operational status classified as `REQUIRES_CONFIGURATION` / `BLOCKED_EXTERNAL` pending enterprise exporter deployment:
     - `E7ApiUnavailable` (SEV-1)
     - `E7HttpHighErrorRate` (SEV-2)
     - `E7HttpExcessiveLatency` (SEV-3)
     - `E7SchedulingWorkerUnavailable` (SEV-1)
     - `E7AuthFailureSpike` (SEV-2)
     - `E7ExternalIntegrationDegraded` (SEV-3)
     - `E7PostgresDown` (SEV-1)
     - `E7PostgresPoolExhaustion` (SEV-2)
     - `E7RabbitMQDown` (SEV-1)
     - `E7RabbitMQConnectionFailure` (SEV-2)
     - `E7BackupMissingOrFailed` (SEV-1)
3. **Alertmanager Routing:**
   - Configuration in `deploy/alertmanager/alertmanager.yml` validated via `amtool check-config`.
   - Severities mapped to tier receivers (`pagerduty-sev1`, `slack-sev2`, `slack-sev3`, `null-receiver`).

---

## 8. Release Security Gates

1. **Release Packaging Workflow (`build_production_release.py`):**
   - Working Tree Cleanliness: Enforces clean Git tree or explicit `--allow-uncommitted` for diagnostic testing.
   - Pinned Base Images: Enforces digest pinning (`python:3.11-slim@sha256:...`).
   - Software Bill of Materials (SBOM): Generates CycloneDX JSON (`dist/release/SBOM.cyclonedx.json`) covering all 75 components (51 runtime required, 24 dev/optional).
   - Vulnerability Scanning: Fails closed when scanners (Trivy CLI) are missing; classifies results as `DRY_RUN_PASSED`, `RELEASE_READY`, or `RELEASE_GATED`.
   - Secret Scanning: Mandatory `scan_secrets.py` pass; fails release if unredacted credentials exist.
   - Integrity Hash & Manifest: Generates `release_manifest.json` with SHA-256 digests.

---

## 9. Operational Runbooks & Incident Readiness

Documented in `docs/production/INCIDENT_RESPONSE_GUIDE.md` and `DEPLOYMENT_RUNBOOK.md`:

* **INC-01 (API Outage):** Pod restart procedures, container log inspection, upstream ingress health-check verification.
* **INC-04 (PostgreSQL Failure):** Standby promotion, connection pool eviction verification, read-only replica failover.
* **INC-12 (RabbitMQ Outage):** Broker restart, outbox lease recovery, connection reconnection backoff observation.
* **INC-10 (Flowable Outage):** Engine container health verification, Flowable DB connection recovery, circuit breaker cooldown.
* **INC-05 (Outbox/Notification Backlog):** Worker horizontal scaling, lease expiration verification, DLQ depth inspection.
* **INC-17 (Backup Failure):** Backup cron log analysis, disk space check, manual on-demand backup execution.
* **Security Incident Escalation:** Credential rotation protocol, session invalidation, audit event inspection (`audit_events`).

---

## 10. Disaster Recovery Readiness

1. **Logical Backup & Restore Verification:** Drilled using `backup_logical.py` and `restore_logical.py`. Verified creation of compressed `.dump` files, SHA-256 integrity calculation, and successful restoration into disposable database `e7_recovery_drill_disposable` validating Alembic revision `0014`, 26 application tables, and verified non-negative row counts across key tables including `inbox_messages`.
2. **Point-In-Time Recovery Architecture:** Verified via isolated physical base backup and continuous WAL replay drill (`test_pitr_physical_drill_isolated`) on dedicated disposable container. Replays continuous WAL archives up to target transaction restore point `restore_point_b`.
3. **Connection Pool Resilience:** Verified via unit tests (`test_connection_pool_recovery_and_pre_ping`) confirming that server-side socket terminations are transparently evicted via `pool_pre_ping` without poisoning the application connection pool.
4. **Transaction Atomicity:** Domain mutations and outbox events commit in a single atomic transaction; any failure safely rolls back both.
5. **DR Status Classifications:**
   - `APPROVED_TARGET`: Documented architectural targets approved for production SLA (RPO < 5 minutes via continuous WAL replay; RTO < 30 minutes for automated failover / logical restore).
   - `INTERNALLY_TESTED`: Measured outcomes executed and timed on local disposable infrastructure (Logical restore duration ~0.3s - 1.2s; isolated physical WAL replay drill).
   - `LIVE_INFRASTRUCTURE_VERIFIED`: Enterprise multi-AZ automated failover and cloud-bucket continuous WAL streaming are NOT live infrastructure verified; pending enterprise cloud database provisioning (`BLOCKED_EXTERNAL`).

---

## 11. Outstanding Internal Engineering Gaps

* **Zero Outstanding Internal Engineering Defects:** All 9 previous test failures (7 security, 2 recovery) and 1 skipped test have been diagnosed, resolved, and verified passing:
  - **7 Security Failures (Webhooks & BOLA):** Environmental root cause; resolved by starting the authorized Docker Desktop daemon. All 160 security tests pass.
  - **Flowable Live Reconnect Failure:** Resolved by binding loopback port mapping `127.0.0.1:8080:8080` in `docker-compose.demo.yml` without exposing ports on production templates.
  - **RabbitMQ Recovery Test Interference:** Resolved by adding optional `event_id` targeting to `OutboxDispatchRepository.claim_ready`, ensuring recovery tests target their specific event envelopes without interfering with unrelated database records.
  - **Logical Restore Script Correction:** Corrected obsolete `inbox_records` reference to `inbox_messages` in `scripts/recovery/restore_logical.py`, and added regression coverage in `tests/recovery/test_postgresql_recovery.py`.
  - **PITR Physical Drill:** Executed isolated physical base backup and WAL replay drill `test_pitr_physical_drill_isolated` on disposable container port 55533 (passed).
  - **Complete Test Suite Success:** 160/160 security tests pass; 24/24 recovery tests pass (0 skipped, 0 failed).

---

## 12. External Infrastructure Requirements

The following external infrastructure resources must be provisioned by enterprise infrastructure teams:

1. **Edge Gateway / Ingress:** Layer 7 API Gateway with TLS 1.3 termination, DNS A/CNAME records, OWASP Core Rule Set WAF inspection, and DDoS rate limiting.
2. **Managed Cloud PostgreSQL 16:** High-availability cluster (AWS RDS / GCP Cloud SQL) with automated backup snapshots, continuous WAL streaming, and dedicated network peering.
3. **Managed Clustered RabbitMQ 3.13:** Clustered message broker with quorum queues and mirrored topic exchanges (`e7.events`, `e8.notifications`).
4. **Enterprise Secret Manager:** Vault, AWS Secrets Manager, or Google Secret Manager for injecting runtime environment variables without plaintext storage.
5. **Centralized Log Forwarder:** Fluentbit / Logstash agent forwarding JSON structured logs to enterprise SIEM.

---

## 13. Responsible Team Handovers

Formal handover documents are prepared and ratified in `docs/production/handover/`:

1. **Institutional DBA Team:** `DATABASE_ADMINISTRATION_HANDOVER.md` (Governs `MYSQL_LIVE_READ_VERIFICATION`). Initial grant scope is strictly the 11 approved active tables. The four conditional support tables are not part of the initial grant scope.
2. **Corporate IT / Identity Team:** `IDENTITY_TEAM_HANDOVER.md` (Governs `PRODUCTION_COMPANY_OIDC`).
3. **E-8 Platform Team:** `E8_PLATFORM_TEAM_HANDOVER.md` (Governs `E8_NOTIFICATION_INTEGRATION`).
4. **Cloud Infrastructure / SRE Team:** `CLOUD_INFRASTRUCTURE_SRE_HANDOVER.md` (Governs `PRODUCTION_GATEWAY`).

---

## 14. Production Acceptance Gates

| Gate ID | Domain | Gate Description | Status |
| :---: | :--- | :--- | :---: |
| **GATE-01** | Architecture | E-7 core architecture, ADR compliance, 26 tables, Alembic head `0014` | **APPROVED / INTERNALLY_ACCEPTED** |
| **GATE-02** | Security | Secret scanning clean, non-root containers, security headers, CORS (160/160 tests pass) | **APPROVED / INTERNALLY_ACCEPTED** |
| **GATE-03** | Observability | OTel collector, 6 offline PromQL rules validated, Alertmanager routing tree | **APPROVED / INTERNALLY_ACCEPTED** |
| **GATE-04** | Disaster Recovery | Logical backup/restore drilled (inbox_messages verified), isolated PITR drilled (24/24 tests pass) | **APPROVED / INTERNALLY_ACCEPTED** |
| **GATE-05** | Release Packaging | CycloneDX SBOM generated (75 components), dry-run release gating validated | **APPROVED / INTERNALLY_ACCEPTED** |
| **GATE-06** | Institutional DB | Company MySQL `SELECT`-only account on 11 approved active tables (`MYSQL_LIVE_READ_VERIFICATION`) | `BLOCKED_EXTERNAL` |
| **GATE-07** | Identity SSO | Corporate Google Workspace OAuth client ID & domain verification (`PRODUCTION_COMPANY_OIDC`) | `BLOCKED_EXTERNAL` |
| **GATE-08** | Notifications | Enterprise E-8 broker credentials and queue provisioning (`E8_NOTIFICATION_INTEGRATION`) | `BLOCKED_EXTERNAL` |
| **GATE-09** | Gateway & WAF | Enterprise Layer 7 ingress, TLS 1.3 certificates, public DNS (`PRODUCTION_GATEWAY`) | `BLOCKED_EXTERNAL` |

---

## Explicit Final Verdict

```
STAGE_6_INTERNAL_ACCEPTED_BLOCKED_EXTERNAL
```
