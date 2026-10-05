# Phase 1 Foundation — Worker Runtime Architecture (Task 1J.3B)

## 1. Readiness Audit Decision & Worker Scope
In **Task 1J.3A**, a comprehensive composition readiness audit evaluated whether existing frozen Phase-1 components could be safely composed into a unified worker process:
- `PRODUCTION_EVENT_HANDLER_STATUS = NOT_AVAILABLE`
- `WORKER_COMPOSITION_READINESS = BLOCKED_MISSING_PRODUCTION_EVENT_HANDLER`

Inspection of the production source tree (`app/`) confirmed that zero implementations of `ConsumerEventHandler`, event routing tables, or handler registries exist. In accordance with frozen Phase-1 guardrails, a production worker must never acknowledge arbitrary events with a no-op handler, silently discard events, or invent synthetic domain handlers.

Therefore, **Task 1J.3B** implements an **outbox-only** background worker runtime (`app.worker`). Inbound RabbitMQ consumption (`RabbitMqConsumerRunner`) remains explicitly disabled until legitimate business event handlers are introduced in subsequent domain phases.

---

## 2. Worker Runtime Composition
The worker entrypoint is located at [`app/worker.py`](file:///f:/operations-engine/app/worker.py) and is runnable via:
```bash
python -m app.worker
```

The module provides a thin runtime-composition layer over frozen Phase-1 infrastructure components:
1. **DatabaseManager**: Reuses canonical `DatabaseManager` from [`app/db/engine.py`](file:///f:/operations-engine/app/db/engine.py), providing explicit lifecycle ownership for the SQLAlchemy `AsyncEngine` connection pool.
2. **UnitOfWork Factory**: Reuses canonical `create_unit_of_work_factory` from [`app/db/uow.py`](file:///f:/operations-engine/app/db/uow.py), producing fresh `SqlAlchemyUnitOfWork` instances for each transactional outbox claim and settlement phase.
3. **RabbitMqEventPublisher**: Instantiated from validated [`RabbitMqPublisherSettings`](file:///f:/operations-engine/app/events/publishers/config.py) and passed **unstarted** to the runner.
4. **OutboxDispatcherRunner**: Instantiated with the unstarted publisher, operational [`OutboxRunnerPolicy`](file:///f:/operations-engine/app/events/outbox/runner.py), technical [`OutboxDispatchPolicy`](file:///f:/operations-engine/app/events/outbox/dispatcher.py), and `uow_factory`.

---

## 3. Publisher & Database Lifecycle Ownership
- **Publisher Lifecycle Invariant**: [`OutboxDispatcherRunner`](file:///f:/operations-engine/app/events/outbox/runner.py) strictly owns `publisher.start()` on startup and `publisher.close()` on teardown. The worker composition layer never calls `start()` or `close()` on the publisher, preventing duplicate lifecycle errors (`PublisherStateError`).
- **Database Disposal Invariant**: The worker runtime guarantees `await db_manager.dispose()` on every exit path (normal shutdown, task cancellation, or runner crash). If a runner error occurs and database disposal subsequently encounters a secondary failure, the primary runner error is preserved and the secondary failure is recorded safely via `.add_note()` without masking the primary exception.

---

## 4. Settings & Operational Configuration
All operational durations, concurrency bounds, and transport URLs are environment-driven with zero hardcoded literals:
- **Application & Database**: [`Settings`](file:///f:/operations-engine/app/core/config.py) (`E7_DATABASE_URL`, pool dimensions, recycle, timeouts).
- **Event Publisher Transport**: [`RabbitMqPublisherSettings`](file:///f:/operations-engine/app/events/publishers/config.py) (`E7_RABBITMQ_URL`, `E7_RABBITMQ_EXCHANGE_NAME`, `E7_RABBITMQ_CONNECT_TIMEOUT_SECONDS`).
- **Outbox Worker Operational Policy**: [`OutboxWorkerSettings`](file:///f:/operations-engine/app/events/outbox/config.py):
  - `E7_OUTBOX_POLL_INTERVAL_SECONDS` (default: `1.0s`)
  - `E7_OUTBOX_MAX_CONCURRENCY` (default: `1`)
  - `E7_OUTBOX_SHUTDOWN_GRACE_SECONDS` (default: `10.0s`)
  - `E7_OUTBOX_LEASE_DURATION_SECONDS` (default: `30.0s`)
  - `E7_OUTBOX_PUBLISH_TIMEOUT_SECONDS` (default: `5.0s`, must be `< lease_duration`)
  - `E7_OUTBOX_RETRY_BASE_DELAY_SECONDS` (default: `1.0s`)
  - `E7_OUTBOX_RETRY_MAX_DELAY_SECONDS` (default: `60.0s`, must be `>= retry_base_delay`)
  - `E7_OUTBOX_RETRY_MULTIPLIER` (default: `2.0`, must be `>= 1.0`)

---

## 5. Signal Handling, Graceful Shutdown & Fail-Fast Behavior
- **POSIX Signal Interception**: On Linux container runtimes, standard library signal handlers intercept `SIGTERM` and `SIGINT` via `loop.add_signal_handler(sig, stop_event.set)`. Signal registration is platform-guarded against Windows to ensure seamless local developer testing.
- **Graceful Shutdown**: Setting `stop_event` causes `OutboxDispatcherRunner` to transition to `STOPPING`, wake up idle polling immediately, allow in-flight worker dispatches to complete within `shutdown_grace_period`, force-cancel remaining workers if the timeout expires, close the publisher, and transition to `STOPPED`.
- **Fail-Fast Supervision**: If publisher connection fails, database operations raise, or a dispatch worker crashes unexpectedly, the worker does not enter an infinite retry loop or mask errors. The exception propagates out of `run_worker()`, cleanly disposes database connections, and causes `main()` to exit with a non-zero exit code.

---

## 6. Docker Compose Integration
In [`docker-compose.yml`](file:///f:/operations-engine/docker-compose.yml), the `worker` service is updated from the fail-fast placeholder to the real runtime:
- **Profile**: Retained under `profiles: ["worker-runtime"]` so default `docker compose up` starts only API, PostgreSQL, RabbitMQ, and Flowable.
- **Command**: `python -m app.worker`.
- **Dependencies**: Explicit health-aware dependencies:
  ```yaml
  depends_on:
    postgres:
      condition: service_healthy
    rabbitmq:
      condition: service_healthy
  ```
- **Restart Policy**: No restart policy (`restart: always` or `unless-stopped` omitted) in local Compose so development/integration crashes remain immediately visible.
- **Healthcheck**: `WORKER_HEALTHCHECK = PROCESS_ONLY`. No HTTP server or synthetic sidecar probe is attached. Process liveness and exit status represent the truthful health signal.

---

## 7. Deferred Inbound Consumer & Live Verification
- **Inbound Consumer Runtime**: `INBOUND_CONSUMER_RUNTIME_STATUS = DEFERRED_MISSING_PRODUCTION_EVENT_HANDLER`. Zero consumer components or queues are active in the Phase-1 worker.
- **Live Verification**: `Task 1J.3B` implemented and unit-verified the worker runtime. Live multi-container execution against live PostgreSQL and RabbitMQ was subsequently proven in **Task 1J.3C** ([`docs/phase-1/PHASE_1_WORKER_RUNTIME_VERIFICATION.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_WORKER_RUNTIME_VERIFICATION.md)), establishing `OUTBOX_WORKER_LIVE_VERIFIED = YES` and `WORKER_RUNTIME_STATUS = OUTBOX_ONLY_LIVE_VERIFIED`.

---

## 8. Summary of Files Changed
- **Created**:
  - `app/events/outbox/config.py`: `OutboxWorkerSettings` BaseSettings model with validation and policy factory methods.
  - `app/worker.py`: Outbox-only asynchronous worker runtime entrypoint and CLI `main()`.
  - `tests/unit/test_worker_entrypoint.py`: Comprehensive unit tests verifying composition, publisher ownership, fail-fast, signal handling, and settings validation.
  - `docs/phase-1/PHASE_1_WORKER_RUNTIME.md`: This architecture and design documentation.
- **Modified**:
  - `docker-compose.yml`: Configured `worker` service with `python -m app.worker`, outbox environment variables, and healthy service dependencies.
  - `.env.example`: Added outbox worker runtime configuration templates and safe local defaults.
  - `tests/unit/test_docker_compose.py`: Updated compose static tests for real worker module, dependencies, and absence of consumer settings.
