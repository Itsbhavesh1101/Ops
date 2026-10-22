# E-7 Operations Engine — Production Readiness Master Plan & V1 Completion Checklist

**System:** IR Infotech E-7 Operations Engine  
**Classification:** Authoritative Technical Transition Plan: Demo-Complete to Production-Ready V1  
**Current Baseline Commit:** `5722d1cdd8210942d4f63e41362cec66ccca2101` (branch: `master`)  
**Frozen Phase-8 RC:** `03e6305256db3d838b472aaac50893ddb513414e`  
**Security Classification:** Public / Non-Confidential (Zero Secrets)  

---

## 1. Executive Summary & Baseline State

The E-7 Operations Engine has completed its foundational demonstration engineering baseline. All core subsystems—including CP-SAT constraint scheduling, BPMN/DMN workflow execution, transactional outbox messaging, relational persistence, role-based security, in-memory token authentication, and UI-V2 visual interfaces—are fully verified.

### 1.1 Current Accepted Engineering State
```yaml
CURRENT_STATE_ASSESSMENT:
  PROJECT_DEMO_ENGINEERING: COMPLETE
  DEMO_REHEARSAL_READY: YES
  SELF_CONTAINED_DEMO: PASS
  FULL_REAL_DATA_DEMO_FLOW: PASS
  TIMETABLE_DATA_CONSISTENCY: PASS
  UI_V2_VISUAL_POLISH: PASS
  RUNTIME_DATA_PURITY: PASS
  ALEMBIC_MIGRATION_HEAD: "0014 (head)"
  APPLICATION_TABLE_COUNT: 26
  REGRESSION_TEST_SUITE: PASS (491 passing, 20 skipped)
  FRONTEND_TEST_SUITE: PASS (79 passing across 14 test files)
  CODE_QUALITY_CHECKS: PASS (pip check clean, Ruff clean, mypy clean across 261 source files)
  ACTIVE_SECRETS_IN_REPO: 0
```

### 1.2 Known Production Blockers
```yaml
PRODUCTION_BLOCKERS:
  MYSQL_LIVE_READ_VERIFICATION: BLOCKED_ON_COMPANY_READ_ONLY_ACCESS
  PRODUCTION_COMPANY_OIDC: BLOCKED_EXTERNAL
  E8_NOTIFICATION_INTEGRATION: BLOCKED_EXTERNAL
  PRODUCTION_GATEWAY: BLOCKED_ON_PRODUCTION_INFRASTRUCTURE
  PHASE_8_PRODUCTION_READINESS: NOT_READY
```

---

## 2. Complete Production Gap Inventory

Every remaining production requirement is classified into one of five statuses:
- **`COMPLETE`**: Fully engineered, tested, verified, and operational in codebase.
- **`CAN_COMPLETE_NOW`**: Internal engineering or documentation preparation executable immediately without external dependencies.
- **`BLOCKED_ON_COMPANY`**: Gated strictly on institutional IT/DBA/business deliverables (credentials, SSO, E-8 endpoint, UAT sign-off).
- **`BLOCKED_ON_PRODUCTION_INFRASTRUCTURE`**: Gated on hosting environment provisioning (Ingress, WAF, TLS certificates, Cloud DB cluster).
- **`OPTIONAL_POST_PRODUCTION_PHASE_10`**: Explicitly deferred optimizations out of V1 production scope.

| # | Production Domain | Canonical Status | Current Implementation State | Remaining Work to Declare Complete |
|---|---|---|---|---|
| 1 | **Identity / OIDC** | `BLOCKED_ON_COMPANY` | Provider-neutral verifiers (`OidcVerifier`, `GoogleOidcVerifier`), PKCE flow, in-memory token state, local Keycloak demo realm. | Company Google Workspace OAuth client ID, discovery URL, and tenant claim mapping provisioned by IT. |
| 2 | **PostgreSQL** | `COMPLETE` / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | Async SQLAlchemy pool, pre-ping, statement timeouts, 14 migrations, 26 application tables, PITR procedures drilled. | Provision managed production PostgreSQL 16 HA instance (RDS/Cloud SQL) and execute migration. |
| 3 | **MySQL ERP** | `BLOCKED_ON_COMPANY` | `MySqlIreduAdapter` with `SET SESSION TRANSACTION READ ONLY`, regex statement blocker, mathematical student headcount queries (zero PII). | Company DBA provisions dedicated `SELECT`-only service account (`e7_ro_sync`) with least privilege on verified 11 active tables (and up to 15 mapped support tables). |
| 4 | **RabbitMQ** | `COMPLETE` / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | `RabbitMqEventPublisher` with durable topic exchange, publisher confirms, mandatory routing, transactional outbox dispatcher. | Provision managed clustered RabbitMQ 3.13 broker in production VPC. |
| 5 | **Flowable** | `COMPLETE` / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | Flowable REST adapter with BPMN/DMN runtime, process versioning, connection pooling, fail-closed credential validation. | Provision dedicated production Flowable instance with dedicated PostgreSQL database and secure credentials. |
| 6 | **E8 Notifications** | `BLOCKED_ON_COMPANY` | Dual-transport per ADR-012: protocol `NotificationDispatcherProtocol`, AMQP broker default (`e8.notifications`), configurable `HttpNotificationDispatcher` alternative. | Company E-8 Platform team confirms supported integration transport (AMQP broker topic vs HTTP REST gateway), delivers credentials/endpoints, and confirms delivery contract. |
| 7 | **API Deployment** | `COMPLETE` | Containerized FastAPI under Uvicorn, multi-worker model, graceful lifespan shutdown, security middleware. | Deploy container image with production env vars to production container platform. |
| 8 | **Frontend Deployment** | `COMPLETE` / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | Modern React/TypeScript SPA in `ui/operations-ui`, optimized build (`dist/`), dynamic API URL & OIDC client injection, in-memory auth. | Deploy static bundle to production CDN / Cloud Storage behind edge ingress. |
| 9 | **Reverse Proxy / Ingress** | `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | `e7-pilot-gateway` (nginx 1.31) provides local reverse proxy and rate limiting for local pilot testing. | Configure enterprise Layer 7 API Gateway / Ingress (Envoy/Kong/ALB) with OWASP WAF rules. |
| 10 | **TLS** | `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | Application injects HSTS headers (`E7_HSTS_ENABLED=true`); requires TLS-terminating ingress. | Issue production TLS 1.3 certificate (Let's Encrypt / ACM) and enforce HTTPS redirect at ingress. |
| 11 | **DNS** | `BLOCKED_ON_COMPANY` | Application configured for FQDN hostnames via `E7_CORS_ALLOWED_ORIGINS` and Ingress Host headers. | Company IT creates DNS A/CNAME records (e.g. `operations.irinfotech.com`, `api.operations.irinfotech.com`). |
| 12 | **Secrets Management** | `COMPLETE` / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | All secrets externalized via Pydantic `SecretStr` environment variables; zero plaintext in git. | Configure enterprise secret store (Vault / AWS Secrets Manager / Kubernetes Secrets) to inject env vars. |
| 13 | **Backups** | `COMPLETE` / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | Logical backup script (`pg_dump` custom format) and physical continuous WAL archiving documented and drilled. | Schedule automated daily snapshot & WAL archiving cron jobs on production cloud DB. |
| 14 | **Restore** | `COMPLETE` | Restore procedures verified across recovery drills (Alembic 0014, 26 tables validated). | Ready for operational execution during disaster recovery events. |
| 15 | **Monitoring** | `COMPLETE` | OpenTelemetry tracing and metric instrumentation, OTLP HTTP exporter, Prometheus scraping endpoints. | Connect production OTel Collector to enterprise metrics backend (Prometheus/Grafana/Datadog). |
| 16 | **Alerting** | `COMPLETE` | Production alert rules defined in `deploy/alertmanager/rules.yml`, 16 rules covering SEV-1 through SEV-4, verified by automated unit tests. | Enterprise SRE connects Alertmanager to corporate notification channels (PagerDuty, Slack). |
| 17 | **Logs** | `COMPLETE` | Structured JSON logging (`structlog`), correlation ID propagation (`X-Correlation-ID`), credential masking. | Configure log forwarding (Fluentbit/Logstash/CloudWatch) to central SIEM. |
| 18 | **Audit Retention** | `COMPLETE` | Append-only `audit_events` table in PostgreSQL recording principal, action, resource, outcome, and timestamp. | Define database retention / partitioning policy (e.g. 7-year immutable cold storage). |
| 19 | **Worker Deployment** | `COMPLETE` | Containerized background workers: `e7-worker` (outbox dispatcher) and `e7-scheduler-worker` (solver consumer). | Deploy worker containers with process supervision and independent horizontal replica scaling. |
| 20 | **Scheduler** | `COMPLETE` | OR-Tools CP-SAT constraint engine with bounded CPU budget (<60s), tenant concurrency controls, deterministic seeding. | Operational; ready for production traffic. |
| 21 | **Rate Limiting** | `COMPLETE` (App) / `BLOCKED_ON_INFRA` (Edge) | Application capacity quotas (`TenantQuotaManager` with HTTP 429 Retry-After) fully active. | Configure network-edge volumetric rate limits (100 req/s IP burst, 500 req/s tenant) at ingress. |
| 22 | **Security Headers** | `COMPLETE` | `SecurityHeadersMiddleware` enforcing nosniff, DENY, XSS disable, CSP, and HSTS. | Operational; ready for production traffic. |
| 23 | **CORS** | `COMPLETE` | Strict origin parsing from `E7_CORS_ALLOWED_ORIGINS`; wildcard origins disallowed when credentials enabled. | Inject approved production domain origins at deployment. |
| 24 | **Database Migrations** | `COMPLETE` | Alembic version tree 0001–0014, idempotent startup verification, schema matches 26 application tables. | Execute `alembic upgrade head` in production pre-flight stage. |
| 25 | **Rollback** | `COMPLETE` | Container image rollback and PostgreSQL PITR procedures documented in `DEPLOYMENT_RUNBOOK.md` and `POSTGRES_PITR.md`. | Verified; operational runbook ready. |
| 26 | **Disaster Recovery** | `COMPLETE` | 5 disaster recovery drills executed and documented in `RECOVERY_DRILL_REPORT.md`. | Verified; disaster recovery runbook ratified. |
| 27 | **UAT** | `COMPLETE` (Eng) / `BLOCKED_ON_COMPANY` (User) | 49/49 automated role-based UAT scenarios passing (`UAT_RESULTS.md`). Comprehensive business UAT checklist prepared. | Institutional end-users (Planner, Approver, Scheduler, Admin) execute manual UAT and sign off. |
| 28 | **Release Procedure** | `CAN_COMPLETE_NOW` | Version tagging, container build, SBOM generation, vulnerability scanning, canary deployment steps defined. | Execute release pipeline upon resolution of external blockers. |
| 29 | **Operations Handover** | `CAN_COMPLETE_NOW` | Complete runbooks (`DEPLOYMENT_RUNBOOK.md`, `INCIDENT_RESPONSE_GUIDE.md`, recovery docs) prepared. | Conduct formal handover walkthrough with enterprise operations/SRE team. |
| 30 | **Documentation** | `COMPLETE` | Complete architecture, API contracts, ERD, and runbooks ratified under baseline version control. | Documentation complete. |
| 31 | **Licensing** | `COMPLETE` | `OPEN_SOURCE_COMPLIANCE_REGISTER.md` and `LICENSES.md` verify 0 copyleft/GPL conflicts. | Audit complete. |
| 32 | **SBOM / Vuln Scan** | `COMPLETE` | CycloneDX/SPDX SBOMs and Trivy/Grype scans confirm 0 release-blocking Critical/High application CVEs. | Audit complete. |

---

## 3. Separation of Demo and Production Configuration

The E-7 Operations Engine enforces strict isolation between demonstration environments and production deployment contracts:

```
+-------------------------------------------------------+-------------------------------------------------------+
|                 DEMO / LOCAL CONTRACT                 |                 PRODUCTION CONTRACT                   |
+-------------------------------------------------------+-------------------------------------------------------+
| - Identity Provider: Self-hosted Keycloak 24.0.5      | - Identity Provider: Institutional Google Workspace   |
| - Realm: operations-engine-demo                       | - Issuer: https://accounts.google.com                 |
| - Demo Users: planner, approver, scheduler, admin     | - Real Corporate Directory Users & Groups             |
| - Keycloak Port: 127.0.0.1:8180 (loopback only)       | - Enterprise SSO Discovery Endpoint                   |
| - Default Demo Tenant: tenant-demo-real-001           | - Canonical Institutional Tenant UUIDs                |
| - Loopback IP Bindings: 127.0.0.1                     | - VPC / Private Subnet Non-Loopback Binding           |
| - Database: postgres:54332 (local container)          | - Managed HA PostgreSQL 16 (RDS / Cloud SQL)          |
| - Broker: rabbitmq:5672 (local container)             | - Clustered Production RabbitMQ Cluster               |
| - Compose Overrides: docker-compose.demo.yml          | - Kubernetes Manifests / Production Compose / Helm    |
| - Flowable Admin: rest-admin:test (local only)        | - Secret-Injected Dedicated Service Account           |
| - Test / Browser Fixtures: tests/fixtures/            | - Authoritative Master Data & Canonical PostgreSQL    |
+-------------------------------------------------------+-------------------------------------------------------+
```

### Invariants:
1. **Zero Demo Credentials in Production:** Default credentials (`admin:admin`, `rest-admin:test`) are rejected at startup when `E7_ENVIRONMENT=production`.
2. **Zero Hardcoded Localhost:** All service endpoints (`FLOWABLE_BASE_URL`, `E7_DATABASE_URL`, `E7_RABBITMQ_URL`) must be explicitly injected via environment variables.
3. **Zero Demo Tenant Fallback:** In production, requests without a valid authenticated tenant mapping fail closed (`HTTP 401 Unauthorized`); the system never falls back to `tenant-demo-real-001`.

---

## 4. Authoritative Production Configuration Contract

Every runtime environment variable is categorized and documented with strict validation rules:

### 4.1 Application Core Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `E7_ENVIRONMENT` | Yes | No | Yes (`local`) | No (Must be `production`) | Environment mode identifier |
| `E7_APP_NAME` | No | No | Yes | Yes | Public platform title |
| `E7_LOG_LEVEL` | No | No | Yes (`INFO`) | Yes (`INFO` or `WARNING`) | Logging threshold level |
| `E7_LOG_FORMAT` | No | No | Yes (`json`) | Yes (`json` required) | Structured log formatting |
| `E7_DEBUG` | No | No | Yes (`false`) | No (Strictly `false`) | Debug diagnostics flag |
| `E7_DOCS_ENABLED` | No | No | Yes (`true`) | No (Strictly `false` for prod) | Exposes `/docs`, `/redoc`, `/openapi.json` |
| `E7_CORRELATION_HEADER_NAME` | No | No | Yes (`X-Correlation-ID`) | Yes | HTTP header for correlation tracking |
| `E7_MAX_REQUEST_BODY_BYTES` | No | No | Yes (`10485760`) | Yes (10MB) | Maximum permitted HTTP body size |
| `E7_MOUNT_DOMAIN_ROUTERS` | Yes | No | Yes (`false`) | No (Strictly `true` for API) | Mounts `/api/v1` domain endpoints |

### 4.2 PostgreSQL Persistence Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `E7_DATABASE_URL` | Yes | Yes | No | No | Async connection URL (`postgresql+asyncpg://...`) |
| `E7_DATABASE_POOL_SIZE` | No | No | Yes (`5`) | Yes (`20–50` recommended) | SQLAlchemy connection pool size |
| `E7_DATABASE_MAX_OVERFLOW` | No | No | Yes (`5`) | Yes (`10–20` recommended) | Max overflow connections beyond pool |
| `E7_DATABASE_POOL_TIMEOUT_SECONDS` | No | No | Yes (`30.0`) | Yes | Timeout waiting for pool connection |
| `E7_DATABASE_POOL_RECYCLE_SECONDS` | No | No | Yes (`1800`) | Yes | Pool recycle connection lifetime |
| `E7_DATABASE_CONNECT_TIMEOUT_SECONDS` | No | No | Yes (`10.0`) | Yes | asyncpg connection establishment timeout |
| `E7_DATABASE_ECHO` | No | No | Yes (`false`) | No (Strictly `false` for prod) | SQLAlchemy SQL statement echo logging |

### 4.3 RabbitMQ Messaging Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `E7_RABBITMQ_URL` | Yes | Yes | No | No | AMQP broker URL (`amqp://user:pass@host:5672/vhost`) |
| `E7_RABBITMQ_EXCHANGE_NAME` | No | No | Yes (`events`) | Yes | Canonical durable topic exchange name |
| `E7_RABBITMQ_CONNECT_TIMEOUT_SECONDS`| No | No | Yes (`10.0`) | Yes | AMQP connection establishment timeout |
| `E7_OUTBOX_POLL_INTERVAL_SECONDS` | No | No | Yes (`0.5`) | Yes | Outbox dispatcher idle poll interval |
| `E7_OUTBOX_MAX_CONCURRENCY` | No | No | Yes (`1`) | Yes | Outbox worker claim pipeline limit |
| `E7_OUTBOX_LEASE_DURATION_SECONDS` | No | No | Yes (`30.0`) | Yes | Outbox claim lease duration |
| `E7_OUTBOX_PUBLISH_TIMEOUT_SECONDS` | No | No | Yes (`5.0`) | Yes | Bounded timeout for AMQP publish confirms |

### 4.4 Flowable Workflow Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `FLOWABLE_BASE_URL` | Yes | No | No | No | Base REST URL (`http://<host>:8080/flowable-rest/service`) |
| `FLOWABLE_TIMEOUT_SECONDS` | Yes | No | No | No (`10.0` recommended) | HTTP client request timeout |
| `FLOWABLE_MAX_RETRIES` | Yes | No | No | No (`3` recommended) | Idempotent transport retry budget |
| `FLOWABLE_USERNAME` | Yes | No | No | No (No default 'admin'/'rest-admin') | Service account username |
| `FLOWABLE_PASSWORD` | Yes | Yes | No | No (No default 'test'/'admin') | Service account password |
| `FLOWABLE_TOKEN` | Optional | Yes | No | No | Alternative Bearer token (mutually exclusive with basic) |
| `FLOWABLE_POOL_MAX_CONNECTIONS` | No | No | Yes (`20`) | Yes | Flowable HTTP client connection pool |

### 4.5 OpenID Connect (OIDC) Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `E7_OIDC_PROVIDER_NAME` | Yes | No | Yes (`google`) | Yes (`google`) | Identity provider identifier |
| `E7_GOOGLE_OIDC_DISCOVERY_URL` | No | No | Yes (accounts.google.com) | Yes | Pinned discovery endpoint URL |
| `E7_GOOGLE_OIDC_CLIENT_IDS` | Yes | No | No | No | Approved OAuth2 client ID audiences |
| `E7_GOOGLE_OIDC_ALLOWED_HOSTED_DOMAINS` | Yes | No | No | No (`irinfotech.com`) | Enforced hosted domain allowlist |
| `E7_OIDC_DEFAULT_TENANT_ID` | No | No | No | No (Fails closed if unset & no explicit mapping) | Fallback tenant for SSO principals |
| `E7_GOOGLE_OIDC_CLOCK_SKEW_SECONDS` | No | No | Yes (`60`) | Yes | Clock skew allowance for JWT validation |

### 4.6 MySQL ERP Integration Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `E7_MYSQL_ERP_ENABLED` | Yes | No | Yes (`false`) | Yes (`true` when DBA ready) | Live ERP synchronization flag |
| `E7_MYSQL_ERP_HOST` | Conditional | No | No | No | Private host/VPC endpoint for MySQL |
| `E7_MYSQL_ERP_PORT` | No | No | Yes (`3306`) | Yes | MySQL port |
| `E7_MYSQL_ERP_USER` | Conditional | No | No | No | Dedicated `SELECT`-only username |
| `E7_MYSQL_ERP_PASSWORD` | Conditional | Yes | No | No | Dedicated `SELECT`-only password |
| `E7_MYSQL_ERP_DATABASE` | No | No | Yes (`iredu`) | Yes | Database name |
| `E7_MYSQL_ERP_TIMEOUT_SECONDS` | No | No | Yes (`5.0`) | Yes | Query and connection timeout |

### 4.7 E-8 Notification Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `E7_NOTIFICATION_ENABLED` | Yes | No | Yes (`false`) | Yes (`true` when E8 ready) | Outbound E-8 notification dispatch flag |
| `E7_NOTIFICATION_TRANSPORT` | No | No | Yes (`http`) | Yes (`http` or `broker`) | Dispatch transport protocol |
| `E7_NOTIFICATION_ENDPOINT_URL` | Conditional | No | No | No | E-8 HTTPS gateway endpoint URL |
| `E7_NOTIFICATION_API_KEY` | Conditional | Yes | No | No | Gateway Bearer token / auth credential |
| `E7_NOTIFICATION_TIMEOUT_SECONDS` | No | No | Yes (`5.0`) | Yes | Dispatch HTTP timeout |

### 4.8 Security & Ingress Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `E7_CORS_ALLOWED_ORIGINS` | Yes | No | No | No (e.g. `https://operations.irinfotech.com`) | Approved CORS origins (no wildcards) |
| `E7_SECURITY_HEADERS_ENABLED` | No | No | Yes (`true`) | Yes (`true` required) | Defensive HTTP security headers |
| `E7_HSTS_ENABLED` | Yes | No | Yes (`false`) | No (Must be `true` behind HTTPS) | Strict-Transport-Security header |
| `E7_HSTS_MAX_AGE_SECONDS` | No | No | Yes (`31536000`) | Yes (1 year) | HSTS max-age duration |

### 4.9 Observability & Telemetry Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `E7_OTEL_ENABLED` | No | No | Yes (`false`) | Yes (`true` recommended) | OpenTelemetry tracing and metrics flag |
| `E7_OTEL_EXPORTER_OTLP_ENDPOINT`| Conditional | No | Yes (`http://otel-collector:4318`) | No (Production collector URL) | OTLP HTTP receiver endpoint |
| `E7_OTEL_SERVICE_NAME` | No | No | Yes (`e7-api` / `e7-worker`) | Yes | Service resource identifier |
| `E7_OTEL_METRIC_EXPORT_INTERVAL_SECONDS` | No | No | Yes (`60.0`) | Yes | Metric export flush period |

### 4.10 Frontend Runtime Settings
| Variable Name | Required | Secret | Default Allowed | Production Default Allowed | Description |
|---|---|---|---|---|---|
| `VITE_API_BASE_URL` | Yes | No | No | No (e.g. `https://api.operations.irinfotech.com`) | Backend API base URL |
| `VITE_OIDC_PROVIDER` | Yes | No | Yes (`google`) | Yes (`google`) | Frontend OIDC provider |
| `VITE_OIDC_CLIENT_ID` | Yes | No | No | No (Corporate client ID) | Public OAuth2 client ID |
| `VITE_DEFAULT_TENANT_ID` | Yes | No | No | No (Canonical production tenant) | Primary institutional tenant |

---

## 5. Fail-Closed Production Startup Validation

The application lifecycle enforces strict fail-closed behavior at startup:

```mermaid
graph TD
    Start[Application Boot: lifespan] --> EnvCheck{E7_ENVIRONMENT == 'production'?}
    EnvCheck -->|No: Local/Test| LocalBoot[Allow Technical Defaults & Local Keycloak]
    EnvCheck -->|Yes: Production| CheckDB{E7_DATABASE_URL configured?}
    CheckDB -->|No| FailDB[Abort Startup: DatabaseConfigurationError]
    CheckDB -->|Yes| CheckFlowable{Flowable credentials insecure?}
    CheckFlowable -->|Yes: admin/test| FailFlowable[Abort Startup: Insecure default credentials prohibited]
    CheckFlowable -->|No| CheckOIDC{OIDC Client IDs configured?}
    CheckOIDC -->|No| FailOIDC[Abort Startup: GoogleOidcVerifier requires client ID]
    CheckOIDC -->|Yes| CheckCORS{CORS origins include '*'}
    CheckCORS -->|Yes| FailCORS[Abort Startup: Wildcard origin prohibited with credentials]
    CheckCORS -->|No| Ready[Proceed to Database Migration & Service Startup]
```

---

## 6. Subsystem Hardening & Verification Results

### 6.1 Production OIDC Preparation
- **Provider-Agnostic Verification:** Verified via `OidcVerifier` and `GoogleOidcVerifier`.
- **Cryptographic Security:** Strict `RS256` asymmetric keys; prohibited algorithms (`none`, `HS256`, `HS384`) rejected at parser level.
- **Clock Skew:** Enforced 60-second bounded leeway.
- **In-Memory Token Security:** Frontend stores tokens exclusively in React state memory; zero tokens written to `localStorage` or `sessionStorage` (preventing persistent XSS extraction).
- **Status:** `PRODUCTION_COMPANY_OIDC = BLOCKED_EXTERNAL`.

### 6.2 Production MySQL Preparation
- **Least-Privilege Verification:** `SHOW GRANTS FOR 'e7_ro_sync'@'%'` must verify `SELECT` only on verified minimum 11 active tables (and up to 15 mapped support tables). Non-existent tables (`academic_session`, `student_cohort`, `examination_session`) formally removed.
- **Application Read-Only Guard:** `SET SESSION TRANSACTION READ ONLY` issued upon connection acquisition; regex statement gate blocks mutations.
- **Privacy Enforcement:** Mathematical student headcount queries (`AGGREGATE_ONLY`); zero individual student records or PII extracted.
- **Status:** `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_COMPANY_READ_ONLY_ACCESS`.

### 6.3 E8 Notification Preparation
- **Dual-Transport Architecture:** Per ADR-012, default production transport is AMQP RabbitMQ topic exchange `e8.notifications` (`transport="broker"`); authenticated HTTP via `HttpNotificationDispatcher` is supported as a configurable alternative.
- **Sender Boundary:** Transactional outbox pattern in PostgreSQL guarantees at-least-once persistence with zero domain write latency.
- **Auditing:** Every dispatch attempt appends an immutable record to `audit_events`.
- **Status:** `E8_NOTIFICATION_INTEGRATION = BLOCKED_EXTERNAL`.

### 6.4 PostgreSQL Production Hardening
- **Current Head:** `0014 (head)`.
- **Table Count:** Exactly 26 application tables.
- **Pool Dimensions:** `pool_size=20`, `max_overflow=10`, `pool_timeout=30.0s`, `pool_recycle=1800s`, `pool_pre_ping=True`.
- **Statement Timeout:** Configurable query timeout prevents runaway transactions.
- **Status:** `COMPLETE` (Engine & Schema) / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` (Cloud DB Provisioning).

### 6.5 RabbitMQ Production Hardening
- **Topology:** Durable topic exchange `events`, durable transactional outbox queue.
- **Delivery Guarantees:** Publisher confirms enabled; mandatory routing verification; persistent delivery mode (`delivery_mode=2`).
- **Connection Recovery:** `aio-pika.connect_robust` with exponential retry backoff.
- **Status:** `COMPLETE` (Engine) / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` (Clustered Broker Provisioning).

### 6.6 Flowable Production Hardening
- **Engine Version:** Flowable REST 7.0.0.
- **Credential Validation:** Hardened `FlowableSettings` model validator rejects default credentials (`admin:test`, `rest-admin:test`) when `E7_ENVIRONMENT=production`.
- **Process Versioning:** BPMN models version-controlled and deployed with deterministic keys.
- **Status:** `COMPLETE` (Engine) / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` (Dedicated Flowable Service Provisioning).

### 6.7 API Hardening
- **Server:** FastAPI running under Uvicorn with 4 workers.
- **Middleware Chain:**
  1. `SecurityHeadersMiddleware`: Injects security response headers.
  2. `RequestBodySizeLimitMiddleware`: Blocks payloads exceeding 10MB (`HTTP 413`).
  3. `CorrelationAndLoggingMiddleware`: Attaches correlation IDs and logs structured JSON.
  4. `CORSMiddleware`: Configuration-driven origin allowlist.
- **Health Probes:** `/api/v1/health` (liveness), `/api/v1/ready` (readiness with database probe).
- **Status:** `COMPLETE`.

### 6.8 Frontend Hardening
- **Build:** Vite 6.4.3 production bundle; tree-shaken, minified JavaScript (`dist/assets/`).
- **Routing:** React Router v6 with SPA client-side fallback handling.
- **Authentication:** In-memory token management with automatic background refresh.
- **Status:** `COMPLETE` (Application) / `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` (Static Ingress/CDN).

---

## 7. Operations Monitoring & Alerting Matrix

| Component | Monitored Signal | Health Probe / Metric | Recommended Alert Threshold | Severity | Recommended Operator Action |
|---|---|---|---|---|---|
| **E-7 API** | Process Liveness | `GET /api/v1/health` | 3 consecutive failures (30s) | SEV-1 | Check container logs, restart container, inspect memory |
| **E-7 API** | Database Readiness | `GET /api/v1/ready` | HTTP 503 for > 30s | SEV-1 | Check PostgreSQL connectivity, pool exhaustion, network |
| **E-7 API** | Request Error Rate | `http_requests_total{status=~"5.."}` | > 1.0% error rate over 5 min | SEV-2 | Inspect structured JSON logs for unhandled exceptions |
| **E-7 API** | P99 Latency | `http_request_duration_seconds{quantile="0.99"}` | > 2000ms over 5 min | SEV-3 | Inspect slow database queries or lock contention |
| **PostgreSQL** | Connection Pool | `db_pool_active_connections` | > 85% pool utilization | SEV-2 | Increase pool size or inspect long-running transactions |
| **RabbitMQ** | Broker Liveness | `rabbitmq-diagnostics ping` | Broker unreachable for > 15s | SEV-1 | Inspect cluster nodes, network partitions, disk alarms |
| **Outbox Dispatcher** | Outbox Queue Lag | `e7_outbox_unprocessed_count` | > 500 unprocessed events for > 5 min | SEV-2 | Inspect `e7-worker` container health and broker latency |
| **Scheduler Worker** | Solver Queue Saturation | `e7_scheduler_queue_depth` | > 20 pending solve jobs | SEV-3 | Scale `e7-scheduler-worker` replicas or increase time budget |
| **Flowable** | Engine Connectivity | `GET /management/engine` | Connection timeout > 10s | SEV-2 | Inspect Flowable container health and Flowable DB pool |
| **MySQL ERP** | Live Read Latency | `e7_mysql_erp_query_duration_seconds` | > 5.0s query latency | SEV-3 | Verify network latency to cloud host, inspect query plan |
| **Edge Gateway** | HTTP 429 Rate Throttling | `gateway_requests_429_total` | > 100 req/min throttled | SEV-3 | Evaluate if legitimate traffic burst or abusive traffic |

---

## 8. Backup, Restore, and Disaster Recovery Procedures

### 8.1 PostgreSQL Backup Procedure
```bash
# 1. Execute logical custom-format backup
pg_dump -Fc -h <host> -p 5432 -U <user> -d operations_engine -f /backups/e7_prod_$(date +%Y%m%d_%H%M%S).dump

# 2. Verify backup file non-empty and valid
pg_restore -l /backups/e7_prod_$(date +%Y%m%d_%H%M%S).dump > /dev/null && echo "BACKUP_VALID"
```

### 8.2 PostgreSQL Restore Procedure (Drill-Verified)
```bash
# 1. Create fresh target database
createdb -h <host> -U <user> operations_engine_restored

# 2. Restore schema and data from dump
pg_restore -h <host> -U <user> -d operations_engine_restored --clean --if-exists /backups/<dump_file>

# 3. Verify Alembic migration head and table count
python -c "
import asyncio, sqlalchemy as sa
from app.db.engine import DatabaseManager
async def check():
    db = DatabaseManager(database_url='postgresql+asyncpg://<user>:<pass>@<host>:5432/operations_engine_restored')
    async with db.engine.connect() as c:
        r = await c.execute(sa.text(\"SELECT tablename FROM pg_tables WHERE schemaname='public'\"))
        tables = [row[0] for row in r.fetchall() if row[0] != 'alembic_version']
        assert len(tables) == 26, f'Expected 26 tables, got {len(tables)}'
        print('RESTORE_VERIFICATION_PASS: Exact 26 application tables restored.')
    await db.engine.dispose()
asyncio.run(check())
"
```

### 8.3 Rollback Procedure
1. **API / Worker Rollback:** Revert container image tag to previous stable digest; restart deployment pods. Idempotent outbox leases guarantee zero message duplication.
2. **Database Rollback Policy:** **Never execute automatic `alembic downgrade` in production.** If a migration fails mid-flight, execute Point-In-Time Recovery to the pre-migration restore point per [`POSTGRES_PITR.md`](file:///f:/operations-engine/docs/phase-8/recovery/POSTGRES_PITR.md).

---

## 9. Company User Acceptance Testing (UAT) Package

The production UAT package defines the mandatory end-to-end business acceptance journey across all four canonical operational roles:

```
[ Planner ]          1. Login via Corporate SSO -> View Real Dashboard
                     2. Create Academic Examination Goal -> Set Planning Horizon
                     3. Generate Plan & Work Breakdown Structure (WBS)
                     4. Review Task Dependencies & Critical Path Method (CPM)
                     5. Submit Plan for Institutional Approval
                          |
[ Approver ]         6. Login via Corporate SSO -> Open Workflow Inbox
                     7. Review Plan Tasks, Resource Requirements & Constraints
                     8. Approve Examination Plan via BPMN/DMN Gateway
                          |
[ Scheduler ]        9. Login via Corporate SSO -> View Approved Scheduling Demand
                     10. Verify Real Faculty Roster (iredu staff) & Registered Rooms
                     11. Trigger CP-SAT Optimization Solver (<60s time budget)
                     12. Inspect Generated Timetable Matrix & Resource Allocations
                     13. Publish Authoritative Schedule -> Triggers Notification Outbox
                          |
[ Tenant Admin ]     14. Login via Corporate SSO -> Open Audit Log
                     15. Verify Non-Repudiation Audit Trail Across All Operations
                     16. Review Analytics, Resource Utilization, and System Health
```

---

## 10. Operations Handover Package

The following runbooks provide full operational coverage for production maintenance:
1. **Deployment Runbook:** [`docs/production/DEPLOYMENT_RUNBOOK.md`](file:///f:/operations-engine/docs/production/DEPLOYMENT_RUNBOOK.md) (Startup sequence, pre-flight checks, zero credential echoing).
2. **Incident Response Guide:** [`docs/production/INCIDENT_RESPONSE_GUIDE.md`](file:///f:/operations-engine/docs/production/INCIDENT_RESPONSE_GUIDE.md) (SEV-1 to SEV-4 definitions, triage, escalation).
3. **Disaster Recovery Drills:** [`docs/phase-8/recovery/RECOVERY_DRILL_REPORT.md`](file:///f:/operations-engine/docs/phase-8/recovery/RECOVERY_DRILL_REPORT.md) (Drill evidence across DB, broker, Flowable, and worker outages).
4. **Data Governance & Policy:** [`docs/data/RUNTIME_DATA_POLICY.md`](file:///f:/operations-engine/docs/data/RUNTIME_DATA_POLICY.md) (Zero synthetic data in production runtime).
5. **API Contract Reference:** [`docs/production/API_REFERENCE.md`](file:///f:/operations-engine/docs/production/API_REFERENCE.md) and [`openapi.json`](file:///f:/operations-engine/docs/production/openapi.json).

---

## 11. Deferred Phase 10 Features Registry

Under strict project governance, advanced optimization and alternate architectures are explicitly classified as **`POST_PRODUCTION / PHASE_10`** and are deferred from V1 production scope:

| Feature Identifier | Proposed Technology | Reason for Phase 10 Deferral |
|---|---|---|
| `OPT-P10-001` | **XGBoost / LightGBM** | Machine learning duration estimators deferred; heuristic estimators provide deterministic, explainable estimates in V1. |
| `OPT-P10-002` | **LangGraph Expansion** | Advanced multi-agent planning loops deferred; linear task intelligence planner satisfies all current requirements. |
| `OPT-P10-003` | **Temporal Workflow Engine** | Replacing Flowable 7.0 BPMN/DMN runtime is unnecessary; Flowable is fully verified and certified. |
| `OPT-P10-004` | **Timefold Migration** | Migrating from Google OR-Tools CP-SAT to Timefold solver is deferred; CP-SAT meets all solving performance SLA thresholds (<60s). |
| `OPT-P10-005` | **Valkey / Redis Distributed Caching** | External caching layer deferred; PostgreSQL and in-memory caches satisfy V1 throughput requirements. |
| `OPT-P10-006` | **Separate Vector Database (pgvector / Qdrant)** | Semantic vector search deferred; relational and keyword matching sufficient for V1 operational goals. |
| `OPT-P10-007` | **New ERP / SIS Adapters** | Banner, Canvas, or Moodle adapters deferred; MySQL iredu adapter satisfies primary institutional contract. |
| `OPT-P10-008` | **Multi-Provider Calendar Sync** | Outlook / Office 365 calendar sync deferred; Google Calendar integration satisfies primary requirement. |

---

## 12. Final Release & Sign-Off Criteria for V1 Production

To transition from Stage 1 to formal V1 Production Release Tag, the following criteria must be satisfied:

```
[ ] 1. Company DBA provisions dedicated SELECT-only MySQL account ('e7_ro_sync') with verified SHOW GRANTS on verified 11 active tables.
[ ] 2. Corporate IT provisions Google Workspace OIDC Client Registration and authorizes production redirect URIs.
[ ] 3. E-8 Platform team confirms supported integration transport (AMQP broker topic vs HTTP REST gateway), delivers credentials/endpoints, and confirms delivery contract.
[ ] 4. Cloud Infrastructure team provisions Layer 7 Ingress Gateway with TLS 1.3 certificate and WAF rate limiting.
[ ] 5. Cloud Infrastructure team provisions managed PostgreSQL 16 and clustered RabbitMQ 3.13 in production VPC.
[ ] 6. Production pre-flight checklist passes with zero credential echoing.
[ ] 7. Alembic migration head '0014' executes successfully on production PostgreSQL.
[ ] 8. End-to-end smoke test verifies login, goal creation, solving, and audit logging on production endpoints.
[ ] 9. Formal institutional UAT sign-off executed by designated Planner, Approver, Scheduler, and Admin representatives.
[ ] 10. Executive Architecture Board executes final V1 Production Sign-Off and issues immutable release tag.
```

---

*Document Author: IR Infotech Operations Architecture Team*<br/>
*Current Baseline HEAD:* `5722d1cdd8210942d4f63e41362cec66ccca2101`<br/>
*Status: Ratified & Maintained under Version Control*
