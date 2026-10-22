# E-7 Operations Engine — Cloud Infrastructure & SRE Handover

**Document:** Production Infrastructure Architecture, Hosting & Operational Runbook Handover  
**Target Audience:** Cloud Infrastructure, DevOps & Site Reliability Engineering (SRE) Teams  
**System:** IR Infotech E-7 Operations Engine  
**Classification Key:** `PRODUCTION_GATEWAY` & Supporting Production Infrastructure  
**Current Status:** `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE`  
**Security Posture:** Zero Hardcoded Secrets — Defense-in-Depth Layer 7 Isolation  

---

## 1. Architecture Overview & Component Topology

The E-7 Operations Engine is composed of containerized microservices operating behind an enterprise Layer 7 reverse proxy/ingress controller.

```
                                  [ INTERNET / ENTERPRISE USERS ]
                                                 |
                                                 v
                       +---------------------------------------------------+
                       |    Layer 7 Ingress Controller & WAF (TLS 1.3)    |
                       |      - Envoy / Kong / AWS ALB / Cloud Armor      |
                       |      - OWASP Core Rule Set (CRS) Inspection       |
                       |      - Volumetric Edge Rate Limiting              |
                       +---------------------------------------------------+
                                         |               |
                         +---------------+               +---------------+
                         | (Static UI)                   | (API Calls)
                         v                               v
         +-------------------------------+ +-------------------------------+
         |    Frontend SPA (React/TS)    | |       e7-api (FastAPI)        |
         |  Cloud Storage / CDN Hosting  | |   Uvicorn Multi-Worker (x4)   |
         |  Dynamic Config / In-Memory   | |   Stateless REST / OpenID     |
         +-------------------------------+ +-------------------------------+
                                                           |
             +--------------------+------------------------+------------------------+
             |                    |                        |                        |
             v                    v                        v                        v
  +--------------------+ +--------------------+ +--------------------+ +--------------------+
  |   e7-worker        | | e7-scheduler-worker| | Flowable Engine    | | External Ingestion |
  | (Outbox Dispatcher)| | (CP-SAT Solver)    | | BPMN/DMN 7.0.0     | | - MySQL iredu (RO) |
  | Background Leases  | | CPU-Intensive      | | Dedicated Postgres | | - Google OIDC      |
  +--------------------+ +--------------------+ +--------------------+ +--------------------+
             |                    |                        |
             +--------------------+------------------------+
                                  |
                                  v
       +------------------------------------------------------+
       |             Managed Data Tier (VPC-Internal)         |
       |  - PostgreSQL 16.x HA (RDS / Cloud SQL) - ACID SoR   |
       |  - RabbitMQ 3.13 Clustered Broker - Event Bus        |
       +------------------------------------------------------+
```

---

## 2. Component Provisioning Specifications

### 2.1 Managed PostgreSQL 16 (System of Record — Approved ADR-006)
* **Approved Architecture:** PostgreSQL 16.x (Engine level `16.15` or higher) as the canonical ACID System of Record.
* **Schema Head:** Head migration `0014` (`alembic upgrade head`), exactly 26 application tables.
* **Application Connection Pooling:**
  * E-7 handles internal connection pooling via Async SQLAlchemy (`asyncpg`) without external pooling proxies.
  * Internal engine parameters: `pool_size=20`, `max_overflow=10`, `pool_timeout=30.0s`, `pool_recycle=1800s`, `pool_pre_ping=True`.
* **Proposed Operational Defaults (Subject to Enterprise DBA/SRE Ratification):**
  * *PgBouncer / External Pooler:* E-7 does not require PgBouncer; external proxy pooling remains a proposed enterprise option requiring DBA approval.
  * *High Availability Topology:* Multi-AZ deployment (e.g. AWS RDS Multi-AZ or GCP Cloud SQL HA) with automated standby failover.
  * *Storage Sizing:* Proposed baseline 100 GB SSD with storage auto-scaling.
  * *Database Server Parameters:* Proposed defaults `statement_timeout = 30000` (30s) and `idle_in_transaction_session_timeout = 60000` (60s).
  * *Continuous Archiving:* Proposed continuous Write-Ahead Logging (WAL) stream archiving to multi-region cloud object storage.

### 2.2 Clustered RabbitMQ 3.13 (Message Broker — Approved ADR-005)
* **Approved Architecture:** RabbitMQ 3.13+ with AMQP protocol support.
* **Exchanges Required:**
  * `events`: Durable topic exchange for internal outbox domain events.
  * `e8.notifications`: Canonical durable topic exchange for outbound E-8 notification commands (`ADR-012`).
  * `e7.events`: Documented alternative notification exchange.
* **Publisher Confirms:** E-7 publisher enforces broker confirms (`publisher_confirms=True`) and mandatory routing.
* **Proposed Operational Defaults (Subject to Middleware Team Ratification):**
  * *Cluster Topology:* 3-node broker cluster with quorum queues and mirrored exchanges.
  * *Protocols & Ports:* AMQPS (port `5671` TLS) or private VPC AMQP (port `5672`).

### 2.3 Flowable BPMN/DMN Engine (Approved ADR-001)
* **Approved Architecture:** Dedicated Flowable 7.0.0+ REST process engine.
* **Database Boundary:** Flowable manages its own workflow persistence schema independently; E-7 Alembic migrations never touch Flowable tables.
* **Endpoints:** Port `8080`, health probe `/actuator/health`.
* **Security:** Internal basic auth or OAuth token.

### 2.4 Application Container Services (Approved Baseline)
* **`e7-api`:** Container image `python:3.11-slim` based on [docker/Dockerfile](file:///f:/operations-engine/docker/Dockerfile). Stateless, horizontal autoscaling (min 2, max 10 replicas based on CPU/memory).
* **`e7-worker`:** Background transactional outbox worker. Runs `app.worker.runner`. Independent scaling.
* **`e7-scheduler-worker`:** Background constraint optimization worker. Runs OR-Tools CP-SAT solver (`app.scheduling.worker`). Allocates dedicated CPU threads per worker.

### 2.5 Frontend SPA Hosting (Approved Baseline)
* **Artifacts:** Static production bundle built in `ui/operations-ui/dist/`.
* **Proposed Hosting Platform (Subject to Cloud SRE Choice):** Cloud Storage bucket behind Cloud CDN (e.g. AWS S3 + CloudFront, GCP Cloud Storage + Cloud CDN, or Cloudflare Pages).
* **SPA Routing:** Configured for HTML5 pushState fallback (`index.html` on 404).

---

## 3. Network Edge, Ingress, TLS & WAF

*(All edge and network specifications below are proposed operational defaults requiring Enterprise SecOps confirmation)*

### 3.1 Layer 7 Reverse Proxy / Ingress Controller (Proposed Options)
* **Technology Choice:** Envoy, Kong, AWS ALB, GCP Cloud Armor, or Cloudflare (to be determined by Cloud SRE).
* **OWASP WAF Protection:** Proposed deployment of OWASP Core Rule Set (CRS) v3.3+ to inspect incoming payloads.
* **Header Forwarding:** Forward `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Real-IP`, and `X-Correlation-ID`.

### 3.2 TLS Termination & DNS (Proposed Defaults)
* **TLS Policy:** Proposed strict TLS 1.3 / 1.2 with modern cipher suites. TLS 1.0/1.1 disabled. HTTP/2 enabled.
* **Certificates:** Automated certificate renewal (Let's Encrypt or Cloud Certificate Manager).
* **Security Headers:** Ingress enforces HSTS (`Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`).
* **DNS Hostnames (Illustrative Examples Only — Gated on Corporate IT Approval):**
  * `operations.irinfotech.com` -> Frontend SPA
  * `api.operations.irinfotech.com` -> Backend API

### 3.3 Volumetric Rate Limiting (Proposed Baseline Defaults)
*(Illustrative baseline defaults — subject to corporate SecOps ratification):*
* **Per-IP Rate Limit:** Proposed 100 requests/second burst allowance.
* **Per-Tenant Rate Limit:** Proposed 500 requests/second with `HTTP 429 Too Many Requests` and standard `Retry-After: 30` header.

---

## 4. Secrets Management & Configuration Injection

Under institutional security policy, **zero plaintext secrets** exist in version control:
* All secrets must be provisioned via enterprise secret stores (HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager, or Kubernetes Secrets).
* Secrets are injected into container environments via environment variables:
  * `E7_DATABASE_URL`: Asynchronous PostgreSQL connection string with password.
  * `E7_RABBITMQ_URL`: AMQPS connection string with broker credentials.
  * `E7_FLOWABLE_PASSWORD`: Credentials for Flowable REST engine.
  * `E7_GOOGLE_OIDC_CLIENT_SECRET`: OAuth2 client secret (if confidential client).
  * `E7_NOTIFICATION_API_KEY`: E-8 gateway authorization key (if HTTP transport).
  * `E7_MYSQL_ERP_PASSWORD`: Dedicated SELECT-only account password.

---

## 5. Monitoring, Alerting & Observability

### 5.1 Telemetry Pipeline
* **OpenTelemetry Collector:** Receives OTLP/HTTP traces and metrics on port `4318`. Configured via [docker/observability/otel-collector.yaml](file:///f:/operations-engine/docker/observability/otel-collector.yaml).
* **Prometheus Scraping:** Prometheus scrapes OTel Collector metrics on port `8889` every 15s.

### 5.2 Production Alerting Rules & Notification Routing
* **Prometheus Alert Rules:** Delivered in [deploy/prometheus/alert.rules.yml](file:///f:/operations-engine/deploy/prometheus/alert.rules.yml) (mirrored in `deploy/alertmanager/rules.yml`), evaluated by Prometheus via `rule_files`.
* **Alertmanager Routing Configuration:** Delivered in [deploy/alertmanager/alertmanager.yml](file:///f:/operations-engine/deploy/alertmanager/alertmanager.yml), routing alerts by severity and service.
* **Alert Classifications & Incident Response Runbooks:**
  * **OPERATIONAL Alerts (Passed Offline PromQL Simulation via `promtool`):**
    * `E7OutboxBacklogAccumulating` (SEV-2, `INC-05`)
    * `E7OutboxDeliveryFailuresSpike` (SEV-2, `INC-06`)
    * `E7CapacityQuotaRejectionsSpike` (SEV-3, `INC-14`)
    * `E7SolverQueueBacklogAccumulating` (SEV-3, `INC-08`)
    * `E7SolverBudgetExhaustionSpike` (SEV-2, `INC-09`)
    * `E7FlowableUnavailable` (SEV-1, `INC-10`)
    * *Note on Verification Status:* These six rules have passed syntactic validation (`promtool check rules`) and offline PromQL time-series state-transition simulation (`promtool test rules`). Live runtime verification—including live application metric emission, OpenTelemetry export, Prometheus live scraping, active alert firing, and live notification delivery to external channels—is formally recorded as **PENDING VERIFICATION** until deployment to a live cluster environment with running Prometheus and Alertmanager instances.
  * **NOT_OPERATIONAL Alerts (Classified by Actual Technical Dependency):**
    * *Internal Application Instrumentation Gaps (Engineering Responsibility):*
      * `E7HttpHighErrorRate` (`INC-02`): Requires FastAPI HTTP request counter middleware in `app/core/telemetry/fastapi.py`.
      * `E7HttpExcessiveLatency` (`INC-03`): Requires FastAPI HTTP duration histogram middleware in `app/core/telemetry/fastapi.py`.
      * `E7AuthFailureSpike` (`INC-15`): Requires HTTP 401 unauthorized counter in `app/core/auth/`.
      * `E7ExternalIntegrationDegraded` (`INC-16`): Requires OTel query error/timeout counter in `app/integrations/erp/mysql_adapter.py`.
    * *Missing Exporter / Scrape Target Gaps (SRE / DevOps Responsibility):*
      * `E7ApiUnavailable` (`INC-01`): Requires Prometheus scrape job targeting `/api/v1/health`.
      * `E7SchedulingWorkerUnavailable` (`INC-07`): Requires process supervisor exporter or liveness probe scraper for scheduler worker.
      * `E7PostgresDown` (`INC-04`): Requires `postgres_exporter` container and Prometheus scrape configuration.
      * `E7PostgresPoolExhaustion` (`INC-11`): Requires `postgres_exporter` `pg_stat_activity` metric or internal pool polling gauge.
      * `E7RabbitMQDown` (`INC-12`): Requires Prometheus scrape job on RabbitMQ port 15692.
      * `E7RabbitMQConnectionFailure` (`INC-13`): Requires Prometheus scrape job on RabbitMQ port 15692.
    * *External Infrastructure / Backup Telemetry Gaps (DBA / Cloud Infra Responsibility):*
      * `E7BackupMissingOrFailed` (`INC-17`): Requires external scheduled backup cron writing completion timestamp to node_exporter textfile collector.

---

## 6. Backup, Recovery & Disaster Recovery

* **RPO Target:** < 5 minutes (via continuous WAL archiving).
* **RTO Target:** < 4 hours (via automated database snapshot and PITR restoration).
* **Backup Strategy:**
  1. Automated daily full physical database snapshots.
  2. Continuous WAL stream archiving to multi-region cloud object storage.
  3. Logical `pg_dump` backups executed weekly to independent cold storage.
* **Recovery Runbooks:**
  * [POSTGRES_BACKUP_RESTORE.md](file:///f:/operations-engine/docs/phase-8/recovery/POSTGRES_BACKUP_RESTORE.md)
  * [POSTGRES_PITR.md](file:///f:/operations-engine/docs/phase-8/recovery/POSTGRES_PITR.md)

---

## 7. Deployment, Health Checks & Rollback

### 7.1 Health & Readiness Probes
* **Liveness Probe:** `GET /api/v1/health` (HTTP 200 when Uvicorn is healthy).
* **Readiness Probe:** `GET /api/v1/ready` (HTTP 200 when PostgreSQL and RabbitMQ are reachable).

### 7.2 Deployment Strategy
* **Zero-Downtime Rolling Deployment:** Deploy new container pods; wait for readiness probe pass before terminating old pods.
* **Pre-Flight Migration Gate:** Run `alembic upgrade head` in a Kubernetes Pre-Install Hook or container pre-flight step prior to routing live traffic.
* **Rollback Procedure:** Documented in [DEPLOYMENT_RUNBOOK.md](file:///f:/operations-engine/docs/production/DEPLOYMENT_RUNBOOK.md). Roll back to previous immutable container image tag.

---

## 8. Unresolved Infrastructure Decisions (Gated on Enterprise Architecture)

The following parameters require formal institutional determination by the Enterprise Architecture Board:
* [ ] Final production domain names and DNS hosting provider.
* [ ] Specific Layer 7 Ingress appliance (Envoy vs Kong vs AWS ALB vs GCP Cloud Armor).
* [ ] Enterprise SIEM log destination (Datadog, Splunk, Elastic, or CloudWatch).
* [ ] Production Alertmanager routing destinations (PagerDuty escalation policies, Slack channels).
* [ ] Cloud hosting region and secondary disaster recovery region.
