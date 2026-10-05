# Phase 1 Foundation: Live Outbox Worker Runtime Container Verification

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1J.3C — Live Outbox Worker Container Verification  
**Status**: `COMPLETE`  
**Application Root**: `app/`  
**Worker Module**: `python -m app.worker`  
**Verification Result**: `OUTBOX_WORKER_LIVE_VERIFIED = YES`  

---

## 1. Purpose

The purpose of Task 1J.3C is to provide definitive, end-to-end live proof of the real Dockerized background worker runtime (`app.worker`) running inside a real Docker container managed via Docker Compose (`--profile worker-runtime`), against live PostgreSQL (`e7-postgres`) and live RabbitMQ (`e7-rabbitmq`) services.

The verification proves that:
1. The real application Docker image builds successfully without relaxing pyproject dependencies.
2. The worker container starts using the canonical entrypoint `python -m app.worker`.
3. The worker establishes real network connections to PostgreSQL and RabbitMQ inside the Compose network.
4. A canonical synthetic event staged into an isolated test database is claimed by the real `OutboxDispatcherRunner`.
5. The real `RabbitMqEventPublisher` publishes the event across the live AMQP transport.
6. The transactional outbox row in PostgreSQL is settled (`published_at` populated, leases cleared, `dispatch_attempt_count == 1`).
7. A dedicated test-harness observation queue receives the exact event with identical payload, event metadata, and AMQP message headers.
8. The worker process remains strictly outbox-only (zero consumer queues declared, zero inbound subscriptions, zero inbox records created).
9. The worker process remains healthy and running after dispatch.
10. A standard container `SIGTERM` issued via `docker stop` triggers a graceful shutdown through `app.worker` signal handlers, disposing the database engine and exiting cleanly with code 0 before Docker's kill timeout.
11. The container remains stopped without automatic restart.

---

## 2. Docker Image Build Evidence

The application Docker image was built strictly via the canonical Dockerfile and Compose definition:

```bash
docker compose build worker
```

### Build Result
- **Build Outcome**: `SUCCESS` (exited with code `0`).
- **Image Name & Tag**: `operations-engine-worker:latest`.
- **Image ID**: `158334364996` (`sha256:1583343649962d7a97965d3dc99e1c16dfda18109888d9a161f14cb57bdbd112`).
- **Disk Usage / Content Size**: 378MB / 87.3MB.
- **Dependency Hygiene**:
  - ZERO dependency versions changed in `pyproject.toml`.
  - ZERO requirements relaxed.
  - ZERO unapproved mirrors or TLS bypasses used.
  - Successfully installed all frozen dependencies: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `structlog`, `httpx`, `pyjwt[crypto]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `aio-pika`.

---

## 3. Actual Services & Topology Used

Live verification was executed against the running Docker Compose infrastructure with strict port isolation:

| Service | Container Name | Host Port | Internal Port | Status During Verification |
| :--- | :--- | :--- | :--- | :--- |
| **PostgreSQL** | `e7-postgres` | `55432` | `5432` | `Up (healthy)` |
| **RabbitMQ** | `e7-rabbitmq` | `5673` (AMQP) / `15673` (Mgmt) | `5672` / `15672` | `Up (healthy)` |
| **Worker** | `e7-worker` | N/A (headless) | N/A | Started via profile `worker-runtime` |

Unrelated local workloads (such as `hischolar-cv-suite-postgres-1` bound to host port `5432`) were untouched and unmolested.

---

## 4. Environment & Resource Isolation Strategy

Adhering strictly to Task 1J.2A.1 and Task 1J.2B isolation rules:
- **Dedicated PostgreSQL Database**:
  - Name: `operations_engine_worker_test`.
  - Initialized on `e7-postgres` through an administrative session.
  - Upgraded directly to Alembic head `0007` via `alembic upgrade head`.
  - Dropped and connection-terminated automatically during test teardown.
  - Normal databases (`operations_engine`, `flowable_db`, and external databases) were completely untouched.
- **Dedicated RabbitMQ Virtual Host**:
  - Vhost: `e7_worker_integration`.
  - Created inside `e7-rabbitmq` via `rabbitmqctl add_vhost`.
  - Permissions granted to local `guest` user via `rabbitmqctl set_permissions`.
  - Completely purged and deleted via `rabbitmqctl delete_vhost` during test teardown.
- **Dedicated Isolated Exchange**:
  - Name: `e7.worker.integration.events` (Topic exchange).
- **Test-Harness Observation Queue**:
  - Name: `e7.worker.integration.observer`.
  - Bound to `e7.worker.integration.events` with routing key `worker.integration.test`.
  - Owned strictly by the test harness; not part of any production consumer topology.

---

## 5. Worker Runtime Configuration

The real worker container was launched with test-specific environment injection targeting internal Compose DNS hostnames:

| Environment Variable | Injected Container Value | Description |
| :--- | :--- | :--- |
| `E7_DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@postgres:5432/operations_engine_worker_test` | Internal DNS connection to isolated test DB |
| `E7_RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/e7_worker_integration` | Internal DNS connection to isolated vhost |
| `E7_RABBITMQ_EXCHANGE_NAME` | `e7.worker.integration.events` | Dedicated topic exchange name |
| `E7_OUTBOX_POLL_INTERVAL_SECONDS` | `0.2` | Fast polling interval for verification |
| `E7_OUTBOX_MAX_CONCURRENCY` | `1` | Strict sequential dispatch |
| `E7_OUTBOX_SHUTDOWN_GRACE_SECONDS` | `5.0` | Bounded shutdown grace period |

---

## 6. Synthetic Outbox Event & Initial State

A canonical synthetic event was created via frozen production schemas:

```python
envelope = create_event_envelope(
    EventCreate(
        event_type="worker.integration.test",
        event_version=1,
        tenant_id=f"synthetic-tenant-{uuid4().hex[:8]}",
        correlation_id=str(uuid4()),
        subject_type="worker_task",
        subject_id="WKR-001",
        payload={"worker_test": "synthetic_payload_value", "attempt": 1},
    )
)
```

The event was enqueued into `operations_engine_worker_test` using `SqlAlchemyUnitOfWork` and `OutboxRepository.enqueue(envelope)`, followed by explicit transaction commit.

### Initial Database State Verified
- `event_id`: Identical to `envelope.event_id`.
- `published_at`: `NULL` (pending publication).
- `claim_token`: `NULL` (unclaimed).
- `claimed_at`: `NULL` (unclaimed).
- `dispatch_attempt_count`: `0` (initial default).

---

## 7. Container Startup Verification

The real worker container was started via:

```bash
docker compose -f docker-compose.yml -f docker-compose.worker-test.override.yml --profile worker-runtime up -d --no-deps worker
```

### Verification Findings
- **Container Name**: `e7-worker`.
- **Running State**: `State.Running == True`.
- **Command Executed**: `["python", "-m", "app.worker"]`.
- **Startup Logs**:
  - `OutboxWorkerSettings` successfully validated all configuration fields.
  - Zero traceback or unhandled exceptions.
  - Zero database connection errors.
  - Zero AMQP connection errors.
  - `OutboxDispatcherRunner` started loop.

---

## 8. Real Dispatch & Broker Observation

The test harness polled the observation queue `e7.worker.integration.observer` and captured the published message.

### Message Content Assertions (`PASS`)
- `event_id`: Exactly matching original synthetic UUID (`str(envelope.event_id)`).
- `event_type`: `"worker.integration.test"`.
- `event_version`: `1`.
- `tenant_id`: Matching synthetic tenant identifier.
- `correlation_id`: Matching synthetic correlation identifier.
- `payload`: Exactly matching `{"worker_test": "synthetic_payload_value", "attempt": 1}`.

### AMQP Transport Metadata Assertions (`PASS`)
- `content_type`: `"application/json"`.
- `content_encoding`: `"utf-8"`.
- `message_id`: Exactly matching `str(envelope.event_id)`.
- `type`: Exactly matching `"worker.integration.test"`.

---

## 9. Transactional Outbox Settlement

Immediately following broker receipt, the isolated database was queried in a fresh transaction session.

### Settled Database State Assertions (`PASS`)
- `published_at`: Populated with valid server timestamp (`IS NOT NULL`).
- `claim_token`: Cleared to `NULL` (lease released upon settlement).
- `claimed_at`: Cleared to `NULL`.
- `dispatch_attempt_count`: Exactly `1` (first-attempt success semantics).

---

## 10. Consumer-Runtime Exclusion Verification

To prove that the worker operates strictly in an **outbox-only** mode without rogue consumer activity:
1. **Broker Queue Inventory**:
   - `rabbitmqctl list_queues -p e7_worker_integration` returned exactly ONE queue:
     `e7.worker.integration.observer` (the test-harness queue).
   - Zero consumer queues, zero dead-letter queues, and zero inbound subscriptions were declared.
2. **Inbox Table Integrity**:
   - `SELECT COUNT(*) FROM inbox_messages` in `operations_engine_worker_test` returned `0`.
   - The worker produced zero inbox rows.

---

## 11. Worker Process Health

Prior to shutdown initiation, container liveness was re-verified:
- `docker inspect e7-worker --format "{{.State.Running}}"` returned `true`.
- The worker remained actively running and polling after successful event dispatch.

---

## 12. Live SIGTERM & Graceful Shutdown Verification

A live container SIGTERM was issued via Docker's official stop command:

```bash
docker stop --time 10 e7-worker
```

### Shutdown Findings (`PASS`)
- **Stop Duration**: ~1.3 seconds (well below the 10-second timeout, proving SIGTERM was handled gracefully and SIGKILL was never invoked).
- **Exit Code**: `0` (`State.ExitCode == 0`).
- **Container State**: `State.Status == "exited"`, `State.Running == False`.
- **Auto-Restart Behavior**: Container remained stopped; zero automatic restarts occurred (`RestartCount == 0`).
- **Shutdown Logs**:
  - Signal received (`SIGTERM`).
  - Worker set stop event.
  - `OutboxDispatcherRunner` completed active cycle and terminated.
  - `RabbitMqEventPublisher` closed channel and robust connection.
  - `DatabaseManager` disposed SQLAlchemy engine and connection pool.
  - Clean exit with zero tracebacks.

---

## 13. External Workload Isolation Proof

External container metadata may have been observed read-only for isolation proof. No external container was stopped, restarted, reconfigured, or mutated during corrected verification runs, and no external application/database data was queried.

The external container `hischolar-cv-suite-postgres-1` (running on host port 5432) was monitored before and after test execution:

| Property | Before Test | After Test | Status |
| :--- | :--- | :--- | :--- |
| **Container ID** | `9bdf29fab3914923f32044769e4c264868e0af69e12b41b29d704edb7a5fdf54` | `9bdf29fab3914923f32044769e4c264868e0af69e12b41b29d704edb7a5fdf54` | **IDENTICAL** |
| **State.Status** | `running` | `running` | **IDENTICAL** |
| **RestartPolicy** | `unless-stopped` | `unless-stopped` | **IDENTICAL** |

The external workload experienced zero disruption, zero port collisions, and zero state changes.

---

## 14. Verification Summary

```text
======================================================================
LIVE OUTBOX WORKER CONTAINER VERIFICATION (TASK 1J.3C): ALL PASS
======================================================================
1. Worker Docker image builds: PASS (operations-engine-worker:latest)
2. Worker container starts with real module: PASS (python -m app.worker)
3. Worker connects to PostgreSQL and RabbitMQ: PASS
4. Synthetic event inserted into isolated DB: PASS
5. Real worker claims event: PASS
6. Real worker publishes to RabbitMQ: PASS
7. Outbox row settled as published: PASS (published_at != NULL, attempts=1)
8. Broker receives exact event and AMQP metadata: PASS
9. Worker remains strictly outbox-only: PASS (zero consumer queues/inbox)
10. Worker process remains healthy after dispatch: PASS
11. SIGTERM triggers graceful shutdown: PASS (duration ~1.3s < 10s)
12. Container exits cleanly: PASS (exit code 0, no auto-restart)
======================================================================
OUTBOX_WORKER_LIVE_VERIFIED = YES
======================================================================
```

---

## 15. Files Created & Modified

| File Path | Action | Description |
| :--- | :---: | :--- |
| [`tests/integration/worker/__init__.py`](file:///f:/operations-engine/tests/integration/worker/__init__.py) | **Created** | Worker runtime integration test package. |
| [`tests/integration/worker/conftest.py`](file:///f:/operations-engine/tests/integration/worker/conftest.py) | **Created** | Isolated test fixtures for DB, vhost, compose override, and external container guard. |
| [`tests/integration/worker/test_worker_runtime.py`](file:///f:/operations-engine/tests/integration/worker/test_worker_runtime.py) | **Created** | Live multi-stage worker container lifecycle and graceful shutdown test. |
| [`docs/phase-1/PHASE_1_WORKER_RUNTIME_VERIFICATION.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_WORKER_RUNTIME_VERIFICATION.md) | **Created** | Canonical Task 1J.3C live verification evidence document. |
| [`docs/phase-1/PHASE_1_RUNTIME_TOPOLOGY.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_RUNTIME_TOPOLOGY.md) | **Modified** | Updated `WORKER_RUNTIME_STATUS = OUTBOX_ONLY_LIVE_VERIFIED`. |
| [`docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) | **Modified** | Recorded Task 1J.3C COMPLETE, Canonical Task 1J.3 COMPLETE / FROZEN. |
