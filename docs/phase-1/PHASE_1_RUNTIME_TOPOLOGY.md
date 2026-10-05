# Phase 1 Foundation: Docker Compose Runtime Topology & Configuration

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1J.1 — Docker Compose Runtime Topology & Configuration Foundation  
**Status**: `COMPLETE`  
**Application Root**: `app/`  

---

## 1. Purpose

The purpose of Task 1J.1 is to establish the canonical, reproducible local runtime topology required for Phase-1 multi-container integration verification while strictly preserving all frozen architectural and security boundaries established in Tasks 1A through 1I.

The canonical runtime topology comprises exactly five services:
1. **`api`**: FastAPI web application running under `uvicorn`.
2. **`worker`**: Background processing worker built from the identical application image.
3. **`postgres`**: PostgreSQL transactional system of record and event persistence store.
4. **`rabbitmq`**: Asynchronous message broker for domain event distribution.
5. **`flowable`**: Decoupled Flowable OSS REST workflow engine boundary (`LOCAL_INTEGRATION_BASELINE`).

This task implements infrastructure composition and configuration hygiene only. Live integration execution and behavioral assertions against live containers are strictly sequenced for Task 1J.2+.

---

## 2. Pre-Task Runtime File Inventory & Reconciliation

Before making edits, the repository runtime assets were audited:

| File Path | Pre-Task State | Analysis & Reconciliation Required |
| :--- | :--- | :--- |
| `docker-compose.yml` | Outdated draft from Phase 0 scaffold | **Reconciled**: Contained legacy service names (`db`, `broker`), un-prefixed environment keys (`DATABASE_URL`, `BROKER_URL`), two speculative non-existent worker modules (`task_worker`, `scheduler_worker`), shared database target for Flowable, and obsolete Compose `version: "3.8"` attribute. Replaced with canonical 5-service topology. |
| `docker/Dockerfile` | Minimal Python 3.11-slim image | **Reconciled**: Ran as root user, lacked deterministic package copying before editable install. Updated with dedicated non-root application user (`e7user`), system dependencies (`build-essential`, `libpq-dev`, `curl`), deterministic installation, and explicit directory permissions. |
| `scripts/.gitkeep` | Empty placeholder directory | **Preserved**: No script modifications needed for 1J.1. |
| `docker/postgres-init/` | Did not exist | **Created**: Added `01-create-flowable-db.sql` to initialize isolated `flowable_db` database on first PostgreSQL boot. |
| `.dockerignore` | Did not exist | **Created**: Added comprehensive build-context exclusions (.git, .venv, caches, local data, .env files, test artifacts). |
| `.env.example` | Draft with legacy variables | **Reconciled**: Removed legacy `BROKER_URL`; added Compose environment placeholders (`POSTGRES_*`, `RABBITMQ_*`, `FLOWABLE_*`, `PORT`); aligned with `E7_` settings prefix. |
| `.gitignore` | Existing gitignore | **Preserved**: `.env` and `.env.*` remain strictly ignored. Verified no `.env` file exists or is tracked. |

---

## 3. Service Topology

The canonical local Docker Compose topology is declared in [`docker-compose.yml`](file:///f:/operations-engine/docker-compose.yml):

```mermaid
graph TD
    subgraph "Docker Compose Network"
        API["api (FastAPI / uvicorn:8000)"]
        Worker["worker (Background Processing)"]
        Flowable["flowable (Flowable REST:8080)"]
        Postgres["postgres (PostgreSQL 16:5432)"]
        RabbitMQ["rabbitmq (RabbitMQ 3 Management:5672/15672)"]
    end

    API -->|E7_DATABASE_URL| Postgres
    API -->|E7_RABBITMQ_URL| RabbitMQ
    API -.->|FLOWABLE_BASE_URL (Phase 3)| Flowable

    Worker -->|E7_DATABASE_URL| Postgres
    Worker -->|E7_RABBITMQ_URL| RabbitMQ

    Flowable -->|SPRING_DATASOURCE_URL (flowable_db)| Postgres
```

### Unapproved Services Enforced Absent
In strict accordance with Phase-1 boundaries, zero unnecessary infrastructure services are included:
- **No Redis / Valkey**: Valkey remains optional / measured-need only; no premature caching layer.
- **No Celery**: Incompatible with deterministic async Python event contracts.
- **No Temporal**: Flowable is the frozen primary workflow runtime (ADR-001).
- **No Kafka / NATS**: RabbitMQ is the frozen message broker (ADR-005).
- **No Elasticsearch / MinIO / nginx**: Not required for Phase-1 foundation.

---

## 4. API Service Runtime

- **Image & Build**: Built from `docker/Dockerfile` with context `.`.
- **Runtime Server**: `uvicorn` (approved ASGI runtime).
- **Command**: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- **Concurrency / Process Model**: Exactly one deterministic process without `--reload` and without multi-worker scaling for local integration.
- **Port Exposure**: Container port 8000 mapped to host `${PORT:-8000}`.
- **Healthcheck**: Uses Python standard library to probe the frozen liveness probe endpoint:
  ```yaml
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"]
  interval: 10s
  timeout: 5s
  retries: 3
  start_period: 10s
  ```

---

## 5. Worker Service Runtime (Task 1J.3B Outbox-Only Runtime)

- **Image & Build**: Uses the exact same project image and build context as `api` (`docker/Dockerfile`). No duplicate application images.
- **Runtime Command**: `python -m app.worker`.
- **Runtime Scope**:
  - Following the Task 1J.3A composition audit (`PRODUCTION_EVENT_HANDLER_STATUS = NOT_AVAILABLE`), the worker runtime is implemented as **outbox-only**.
  - Supervises [`OutboxDispatcherRunner`](file:///f:/operations-engine/app/events/outbox/runner.py) with bounded concurrency, exponential backoff, and graceful shutdown.
  - Inbound RabbitMQ consumption (`RabbitMqConsumerRunner`) remains deferred until legitimate business domain handlers are implemented.
- **Status Constants**:
  - `WORKER_RUNTIME_STATUS = OUTBOX_ONLY_LIVE_VERIFIED`
  - `INBOUND_CONSUMER_RUNTIME_STATUS = DEFERRED_MISSING_PRODUCTION_EVENT_HANDLER`
  - `WORKER_DEFAULT_COMPOSE_START = DISABLED_BY_PROFILE`
  - `WORKER_HEALTHCHECK = PROCESS_ONLY`
- **Compose Profile & Lifecycle Boundary**:
  - Normal `docker compose up` does **NOT** start a worker container because the service is placed behind `profiles: ["worker-runtime"]`.
  - Manual activation via `docker compose --profile worker-runtime up worker` starts the real `app.worker` process.
- **Dependencies & Restart**:
  - Dependencies: Health-aware dependencies on `postgres: condition: service_healthy` and `rabbitmq: condition: service_healthy`.
  - Restart: No restart policy (`restart: always` or `unless-stopped` omitted) in local Compose so development/integration crashes remain immediately visible.
- **Worker Healthcheck**: `WORKER_HEALTHCHECK = PROCESS_ONLY`. The worker is a headless asynchronous processing process with fail-fast supervision; process liveness represents the truthful health signal without fabricating HTTP probes.

---

## 6. PostgreSQL Service Configuration

- **Image**: `postgres:16-alpine` (official, pinned stable major version 16).
- **Container Name**: `e7-postgres`.
- **Credentials & Database**: Configured via environment variables with safe development placeholders:
  - `POSTGRES_USER`: `${POSTGRES_USER:-postgres}`
  - `POSTGRES_PASSWORD`: `${POSTGRES_PASSWORD:-postgres}`
  - `POSTGRES_DB`: `${POSTGRES_DB:-operations_engine}`
- **Persistence**: Named volume `postgres_data` mounted to `/var/lib/postgresql/data`.
- **Database Initialization**: Mounts `./docker/postgres-init:/docker-entrypoint-initdb.d:ro` executing `01-create-flowable-db.sql` on initial boot.
- **Healthcheck**: Official native healthcheck via `pg_isready`:
  ```yaml
  test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
  interval: 5s
  timeout: 5s
  retries: 5
  ```

---

## 7. RabbitMQ Service Configuration

- **Image**: `rabbitmq:3-management-alpine` (official stable 3-series management image).
- **Container Name**: `e7-rabbitmq`.
- **Credentials**: Configured via environment variables with safe development placeholders:
  - `RABBITMQ_DEFAULT_USER`: `${RABBITMQ_DEFAULT_USER:-guest}`
  - `RABBITMQ_DEFAULT_PASS`: `${RABBITMQ_DEFAULT_PASS:-guest}`
- **Persistence**: Named volume `rabbitmq_data` mounted to `/var/lib/rabbitmq`.
- **Port Exposure**:
  - AMQP transport: `${RABBITMQ_PORT:-5672}:5672`
  - Management Web UI: `${RABBITMQ_MANAGEMENT_PORT:-15672}:15672`
- **Healthcheck**: Broker-native healthcheck using `rabbitmq-diagnostics`:
  ```yaml
  test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
  interval: 10s
  timeout: 5s
  retries: 5
  ```
- **Topology Ownership**: Compose does **NOT** declare or configure application exchanges, queues, or bindings. Per Task 1G, the application's publisher and consumer runners own declaration of durable topic exchanges, queues, DLX, and DLQ.

---

## 8. Flowable Service & Boundary Isolation

- **Image**: `flowable/flowable-rest:7.0.0` (pinned explicit stable version per ADR-001; not `latest`).
- **Baseline Classification**: Documented as `LOCAL_INTEGRATION_BASELINE` for developer workflow, not a company production mandate.
- **Container Name**: `e7-flowable`.
- **Database Segregation**:
  - Flowable connects to `jdbc:postgresql://postgres:5432/${FLOWABLE_DB:-flowable_db}`.
  - Flowable native runtime tables (`ACT_RU_*`, `ACT_RE_*`, `ACT_HI_*`, etc.) are created strictly in the separate `flowable_db` database.
  - **E-7 Base.metadata tables are NEVER shared with Flowable**.
  - **E-7 Alembic migrations NEVER manage Flowable tables**.
- **Healthcheck**: `FLOWABLE_HEALTHCHECK = DEFERRED`. No unverified HTTP health endpoint is guessed.
- **Port Exposure**: `${FLOWABLE_PORT:-8080}:8080`.

---

## 9. Configuration Flow & Environment Alignment

All application container environment variables directly correspond to existing frozen Pydantic Settings classes without divergence:

| Component | Setting Class | Setting Attribute | Compose Environment Variable | Container Value |
| :--- | :--- | :--- | :--- | :--- |
| Core | `Settings` | `environment` | `E7_ENVIRONMENT` | `${E7_ENVIRONMENT:-local}` |
| Database | `Settings` | `database_url` | `E7_DATABASE_URL` | `postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}` |
| RabbitMQ Pub | `RabbitMqPublisherSettings` | `rabbitmq_url` | `E7_RABBITMQ_URL` | `amqp://${RABBITMQ_DEFAULT_USER}:${RABBITMQ_DEFAULT_PASS}@rabbitmq:5672/` |
| RabbitMQ Pub | `RabbitMqPublisherSettings` | `rabbitmq_exchange_name` | `E7_RABBITMQ_EXCHANGE_NAME` | `${E7_RABBITMQ_EXCHANGE_NAME:-events}` |
| RabbitMQ Sub | `RabbitMqConsumerSettings` | `rabbitmq_consumer_queue_name`| `E7_RABBITMQ_CONSUMER_QUEUE_NAME` | `${E7_RABBITMQ_CONSUMER_QUEUE_NAME:-events.inbox}` |
| Flowable | ADR-001 Client | `flowable_base_url` | `FLOWABLE_BASE_URL` | `http://flowable:8080/flowable-rest/service` |

---

## 10. Dependency Ordering & Startup Semantics

- **No Startup Hacks**: No `sleep 10` or arbitrary delay commands are used.
- **Health-Aware Ordering**:
  - `flowable` depends on `postgres` (`condition: service_healthy`).
  - `api` depends on `postgres` (`condition: service_healthy`).
  - `worker` (placeholder): `depends_on` entries are intentionally omitted in Task 1J.1A to prevent starting PostgreSQL and RabbitMQ merely for the unavailable placeholder to fail. Proper health-aware dependencies (`postgres: service_healthy`, `rabbitmq: service_healthy`) will be established when the real unified entrypoint is implemented.
- **Liveness vs. Readiness Invariant**:
  - Liveness (`/api/v1/health`) indicates process viability; it does not block on external dependency connectivity.
  - Readiness (`/api/v1/ready`) indicates traffic readiness. The application process can start and be live while readiness waits for external dependencies.

---

## 11. Migration Execution Strategy

- **Single Explicit Migration Owner**: Database migrations are **NOT** run automatically inside application container entrypoints (`uvicorn` or worker). This prevents migration race conditions across multiple containers.
- **Execution Workflow**: Database migrations are executed via a deterministic one-shot command:
  ```bash
  # Local Compose migration execution
  docker compose run --rm api alembic upgrade head
  ```
- Continuously running migration containers are strictly forbidden. Task 1J.2 will exercise migrations live.

---

## 12. Security & Boundaries

1. **Zero Real Secrets Committed**:
   - `docker-compose.yml` uses only parameter expansion with safe fallback placeholders.
   - `.env.example` provides explicit placeholder values only.
   - `.env` is ignored by `.gitignore` and is not committed.
2. **Job Read API Production Mount**:
   - `JOB_READ_API_PRODUCTION_MOUNT` remains `DEFERRED_PENDING_TRUSTED_HTTP_TENANT_AND_DB_DEPENDENCIES`.
   - The `/api/v1/jobs/{job_id}` router is strictly unmounted from `app.main.app`.
   - No `X-Tenant-ID`, anonymous tenant resolution, or dummy tenant workarounds were introduced.
3. **Identity & Tenant Persistence**:
   - `IDENTITY_TENANT_CREDENTIAL_PERSISTENCE` remains `DEFERRED_REQUIRES_EXPLICIT_DATA_MODEL_TASK`.
   - No tables for `users`, `tenants`, `roles`, or `service_credentials` were created.
   - `Base.metadata` remains strictly the 5 canonical Phase-1 tables (`audit_events`, `outbox_events`, `inbox_messages`, `idempotency_records`, `jobs`).
   - Alembic head remains strictly `0007`. Zero migration `0008` was added.
4. **Company MySQL ERP Database**:
   - The company MySQL database is an external enterprise system. It is **NOT** containerized in Docker Compose.
   - Live MySQL retrieval remains blocked under `BLK01` production tenant scope.
   - Zero company DB hostnames, IPs, or credentials exist in Compose or `.env.example`.
5. **Google OAuth / Credentials**:
   - No Google client secrets or service account keys are added to Compose or `.env.example`.

---

## 13. Validation Status

### Static & Configuration Validation (`PASS`)
- Verified `docker-compose.yml` syntax and variable resolution via `docker compose config`.
- Verified canonical 5-service structure with zero unapproved infrastructure services.
- Verified identical build context and image configuration for `api` and `worker`.
- Verified internal DNS hostnames (`postgres:5432`, `rabbitmq:5672`).
- Verified Flowable database isolation (`flowable_db` vs `operations_engine`).
- Verified healthcheck declarations on PostgreSQL, RabbitMQ, and API.
- Verified absence of committed secrets.
- 17 automated unit tests in [`tests/unit/test_docker_compose.py`](file:///f:/operations-engine/tests/unit/test_docker_compose.py) pass.
- 1085 total collected tests pass with zero regressions.

### Docker Build Validation
- Docker CLI version: `Docker version 29.7.2, build a7dcaa6`.
- Docker Compose version: `Docker Compose version v5.4.0`.
- Docker daemon: Running (`desktop-linux`, server version 29.7.2).
- Application image build executed via `docker compose build api worker`.

### Live Runtime Behavior (`LIVE_VERIFIED`)
- **Live PostgreSQL Runtime**: Fully verified in Task 1J.2A / 1J.2A.1 ([`docs/phase-1/PHASE_1_POSTGRES_RUNTIME_VERIFICATION.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_POSTGRES_RUNTIME_VERIFICATION.md)). Verified migrations through 0007, append-only audit triggers, UoW commit/rollback, idempotency race condition handling, outbox `SKIP LOCKED` claim semantics, and job lifecycle constraints against real PostgreSQL container `e7-postgres`.
- **Live RabbitMQ & Consumer Runner Runtime**: Fully verified in Task 1J.2B / 1J.2B.1 ([`docs/phase-1/PHASE_1_RABBITMQ_RUNTIME_VERIFICATION.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_RABBITMQ_RUNTIME_VERIFICATION.md)). Verified live broker health, publisher connection lifecycle, AMQP metadata and canonical body round-trip, runner-owned topology declaration, push consumption, ACK, duplicate ACK deduplication, dead-letter routing to DLQ, and graceful stop against real RabbitMQ container `e7-rabbitmq`.
- **Live Dockerized Outbox Worker Runtime**: Fully verified in Task 1J.3C ([`docs/phase-1/PHASE_1_WORKER_RUNTIME_VERIFICATION.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_WORKER_RUNTIME_VERIFICATION.md)). Verified real image build, real container startup with `python -m app.worker`, real event claim and publication, PostgreSQL settlement, outbox-only process isolation, and graceful SIGTERM shutdown with code 0 (`WORKER_RUNTIME_STATUS = OUTBOX_ONLY_LIVE_VERIFIED`).
- **Flowable REST API Probing**: Deferred to Phase 3 workflow engine integration.

---

## 14. Files Created & Modified

| File Path | Status | Purpose |
| :--- | :---: | :--- |
| [`docker-compose.yml`](file:///f:/operations-engine/docker-compose.yml) | **Modified** | Canonical 5-service topology (`api`, `worker`, `postgres`, `rabbitmq`, `flowable`). |
| [`docker/Dockerfile`](file:///f:/operations-engine/docker/Dockerfile) | **Modified** | Application build definition with non-root user `e7user` and deterministic install. |
| [`.dockerignore`](file:///f:/operations-engine/.dockerignore) | **Created** | Build context exclusion rules for clean, secure images. |
| [`docker/postgres-init/01-create-flowable-db.sql`](file:///f:/operations-engine/docker/postgres-init/01-create-flowable-db.sql) | **Created** | Postgres init script establishing isolated `flowable_db`. |
| [`.env.example`](file:///f:/operations-engine/.env.example) | **Modified** | Added Compose topology placeholders; aligned settings with `E7_` prefixes. |
| [`tests/unit/test_docker_compose.py`](file:///f:/operations-engine/tests/unit/test_docker_compose.py) | **Created** | 12 automated static topology and configuration validation tests. |
| [`docs/phase-1/PHASE_1_RUNTIME_TOPOLOGY.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_RUNTIME_TOPOLOGY.md) | **Created** | Canonical Phase-1 runtime topology documentation. |
| [`docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) | **Modified** | Foundation roadmap tracking updated for Task 1J / 1J.1. |
