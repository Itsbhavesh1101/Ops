# Phase 1 Live RabbitMQ, Outbox & Inbox Runtime Verification

**Document Version**: 1.1.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1J.2B & Task 1J.2B.1 — Live RabbitMQ, Outbox, Inbox & Consumer Runner Verification  
**Status**: `COMPLETE / FROZEN`  
**Date**: 2026-09-29  
**Runtime Targets**:
- Docker Compose Service `rabbitmq` (`e7-rabbitmq`, RabbitMQ 3.13.7 Alpine, Erlang/OTP 26.2.5.16)
- Docker Compose Service `postgres` (`e7-postgres`, PostgreSQL 16.15 Alpine)

---

## 1. Executive Summary & Purpose

Task 1J.2B and Task 1J.2B.1 establish complete empirical proof for all messaging, outbox, inbox, and consumer supervisor components created in Task 1G, running directly against live containerized RabbitMQ 3.13 and PostgreSQL 16 infrastructure with zero broker mocks.

Across the combined verification suites, 12 live integration tests executed and passed cleanly:
1. **Live Broker Startup & Health**: Verified `e7-rabbitmq` container health via broker-native healthcheck (`rabbitmq-diagnostics -q ping`) and recorded RabbitMQ `3.13.7` and Erlang/OTP `26.2.5.16`.
2. **Environment & Port Isolation**: Run on non-conflicting host AMQP port `5673` (management `15673`) and PostgreSQL host port `55432` without interfering with any unrelated Docker workload.
3. **Isolated Test Vhost**: Dedicated integration vhost `e7_integration` created with full permissions for test user `guest` and atomically dropped during session teardown.
4. **Isolated Test Database**: Dedicated test database `operations_engine_rabbitmq_test` created, migrated directly to Alembic head `0007`, and dropped cleanly on session teardown.
5. **Component-Level Verification**: Verified publisher start/publish/close lifecycle, AMQP metadata preservation (`content_type`, `content_encoding`, `message_id`, `type`, `correlation_id`), canonical body round-trip, transactional outbox claim & publish via `OutboxDispatcher`, outbox database settlement, and inbox deduplication at the component boundary.
6. **Full Frozen Consumer Runner Path (`CONSUMER_RUNNER_LIVE_VERIFIED = YES`)**: Verified top-level `RabbitMqConsumerRunner` supervising runner-owned topology declaration (exchanges, queues, DLX, DLQ, bindings), channel QoS prefetch, live push consumption (`queue.consume`), concurrency-bounded dispatch, full inbox deduplication with PostgreSQL, deterministic malformed-message rejection to DLQ, and clean graceful teardown.

> [!IMPORTANT]
> **Verification Boundary Constraints**:
> - **ZERO Production Code Changes**: No modifications made to `app/`.
> - **ZERO Runtime Dependencies Added**: Used existing `aio_pika`, `SQLAlchemy`, `asyncpg`, `pytest`.
> - **ZERO Unified Worker Implemented in 1J.2B**: Worker runtime was deferred during 1J.2B (subsequently implemented in Task 1J.3B and live-verified in Task 1J.3C; outbox-only).
> - **ZERO Flowable Integration**: Deferred to Phase 3 workflow runtime integration.
> - **Environment Isolation**: No external container was stopped, restarted, or reconfigured.

---

## 2. Docker & Broker Runtime Environment

| Dimension | Specification | Live Observed Value |
| :--- | :--- | :--- |
| **Compose Service** | `rabbitmq` | `rabbitmq` |
| **Container Name** | `e7-rabbitmq` | `e7-rabbitmq` |
| **Docker Image** | `rabbitmq:3-management-alpine` | `rabbitmq:3-management-alpine` |
| **Health Status** | `healthy` | `healthy` (`rabbitmq-diagnostics -q ping` -> `Ping succeeded`) |
| **RabbitMQ Version** | RabbitMQ 3.x | `3.13.7` |
| **Erlang / OTP Version** | Erlang 26.x | Erlang/OTP `26.2.5.16` |
| **Host AMQP Port** | Configurable via `${RABBITMQ_PORT:-5672}` | `5673` (`0.0.0.0:5673->5672/tcp`) |
| **Host Management Port** | Configurable via `${RABBITMQ_MANAGEMENT_PORT:-15672}` | `15673` (`0.0.0.0:15673->15672/tcp`) |
| **PostgreSQL Support Service**| `postgres` (`e7-postgres`) | PostgreSQL 16.15 on port `55432` (`0.0.0.0:55432->5432/tcp`) |

---

## 3. Isolation & Port Strategy

### 3.1 RabbitMQ Port Configuration
The repository's [docker-compose.yml](file:///f:/operations-engine/docker-compose.yml) already declares configurable host port mappings:
```yaml
ports:
  - "${RABBITMQ_PORT:-5672}:5672"
  - "${RABBITMQ_MANAGEMENT_PORT:-15672}:15672"
```
Zero changes were made to `docker-compose.yml`. For this verification session, non-conflicting host ports were selected:
- AMQP: `5673`
- Management: `15673`

### 3.2 Isolated RabbitMQ Vhost
To guarantee complete isolation from default broker objects and any concurrent workloads:
- **Vhost Name**: `e7_integration`
- **Creation**: Executed dynamically before test execution:
  `docker exec e7-rabbitmq rabbitmqctl add_vhost e7_integration`
  `docker exec e7-rabbitmq rabbitmqctl set_permissions -p e7_integration guest ".*" ".*" ".*"`
- **Cleanup**: Executed on session teardown via pytest fixture:
  `docker exec e7-rabbitmq rabbitmqctl delete_vhost e7_integration`
  Atomic vhost deletion guarantees zero leftover queues, exchanges, or bindings.

### 3.3 Isolated PostgreSQL Database
For outbox and inbox database-backed tests:
- **Database Name**: `operations_engine_rabbitmq_test`
- **Port**: Host port `55432` on `e7-postgres`
- **Migration**: Directly upgraded to Alembic head `0007` (`alembic upgrade head`)
- **Cleanup**: Ephemeral database dropped on session teardown via `DROP DATABASE IF EXISTS operations_engine_rabbitmq_test`.

---

## 4. Live Component Verification Results Matrix (Task 1J.2B)

Every item below is marked **`LIVE_RABBITMQ_VERIFIED`** based on actual execution against the containerized RabbitMQ and PostgreSQL instances in `test_broker.py`, `test_publisher.py`, `test_consumer.py`, and `test_outbox.py`.

| # | Verification Aspect | Execution Method | Observed Result | Evidence Status |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **Broker Ping Health** | `rabbitmq-diagnostics -q ping` | Container returns `Ping succeeded` (exit code 0) | `LIVE_RABBITMQ_VERIFIED` |
| 2 | **Broker Version Reporting** | `rabbitmqctl version` & `rabbitmq-diagnostics erlang_version` | RabbitMQ `3.13.7`, Erlang/OTP `26.2.5.16` | `LIVE_RABBITMQ_VERIFIED` |
| 3 | **Isolated Vhost Setup** | `rabbitmqctl add_vhost e7_integration` | Vhost created; full permissions granted to `guest` | `LIVE_RABBITMQ_VERIFIED` |
| 4 | **Exchange Declaration** | Declare topic exchange `e7.integration.events` | Created durable topic exchange in `e7_integration` | `LIVE_RABBITMQ_VERIFIED` |
| 5 | **DLX & DLQ Declaration** | Declare direct exchange `e7.integration.dlx` & queue `e7.integration.dlq` | Durable DLX and DLQ created with routing key `dead.letter` | `LIVE_RABBITMQ_VERIFIED` |
| 6 | **Queue & Binding Declaration**| Declare work queue `e7.integration.consumer` with DLX arguments | Queue declared with `x-dead-letter-exchange` arguments; bound to `integration.#` and `audit.#` | `LIVE_RABBITMQ_VERIFIED` |
| 7 | **Native Topology Inspection** | `rabbitmqctl list_exchanges`, `list_queues`, `list_bindings` | Verified native broker table contains all declared exchanges, queues, DLX, DLQ, and bindings | `LIVE_RABBITMQ_VERIFIED` |
| 8 | **Publisher Lifecycle** | Real `RabbitMqEventPublisher.start()` and `close()` | Connection and channel established, topic exchange ensured, resources closed cleanly | `LIVE_RABBITMQ_VERIFIED` |
| 9 | **Canonical Event Publication**| `publisher.publish(envelope, routing_key)` | Live broker accepted canonical `EventEnvelope` publication | `LIVE_RABBITMQ_VERIFIED` |
| 10 | **AMQP Metadata Round-Trip** | Read incoming message properties from broker | `content_type="application/json"`, `content_encoding="utf-8"`, `message_id=str(event_id)`, `type=event_type`, `correlation_id` preserved | `LIVE_RABBITMQ_VERIFIED` |
| 11 | **Canonical Body Round-Trip** | Decode payload with `EventEnvelope.model_validate_json(incoming.body)` | Identical `event_id`, `event_type`, `event_version`, `tenant_id`, `correlation_id`, `payload` | `LIVE_RABBITMQ_VERIFIED` |
| 12 | **Consumer Delivery & ACK** | Bounded consumption from work queue | Message received, downstream processor called once, positive ACK sent, queue unacked count returns to 0 | `LIVE_RABBITMQ_VERIFIED` |
| 13 | **Inbox Deduplication (Live DB)**| Publish duplicate canonical event; process via `InboxRepository` | First message inserted and processed; second message flagged duplicate (`record_message=False`); processor called exactly once; exactly 1 row in `inbox_messages` | `LIVE_RABBITMQ_VERIFIED` |
| 14 | **Malformed Message Rejection**| Publish raw message with `content_type="text/plain"` | `RabbitMqInboundMessageParser` raised `InvalidContentTypeError`; `basic_reject(requeue=False)` called | `LIVE_RABBITMQ_VERIFIED` |
| 15 | **Dead-Letter Queue Arrival** | Consume from `e7.integration.dlq` | Poison message arrived in DLQ; original queue cleared; business processor call count = 0 | `LIVE_RABBITMQ_VERIFIED` |
| 16 | **Outbox Record DB Persistence**| Insert `OutboxEventRecord` into PostgreSQL via UoW | Row committed in `outbox_events` with `published_at=NULL`, `dispatch_attempt_count=0` | `LIVE_RABBITMQ_VERIFIED` |
| 17 | **Outbox Claim & Dispatch** | `OutboxDispatcher.dispatch_once()` with real publisher | Record claimed (`claimed_at`, `claim_token`), published to RabbitMQ broker, outcome `PUBLISHED` | `LIVE_RABBITMQ_VERIFIED` |
| 18 | **Outbox Database Settlement** | Query `outbox_events` in fresh transaction | `published_at` populated, `claim_token=NULL`, `claimed_at=NULL`, `dispatch_attempt_count=1` | `LIVE_RABBITMQ_VERIFIED` |
| 19 | **Dispatcher Message Validation**| Consume dispatched message from RabbitMQ queue | Reconstructed `EventEnvelope` matches original outbox event exactly | `LIVE_RABBITMQ_VERIFIED` |
| 20 | **Atomicity Boundary Verified** | Architectural audit of UoW commit, dispatch, settlement | Database transaction commits outbox record; network publish occurs outside DB tx; settlement commits in subsequent DB tx | `LIVE_RABBITMQ_VERIFIED` |
| 21 | **Isolated Vhost Cleanup** | `rabbitmqctl delete_vhost e7_integration` | Vhost deleted cleanly; broker vhosts revert to `/` only | `LIVE_RABBITMQ_VERIFIED` |
| 22 | **Isolated DB Cleanup** | `DROP DATABASE operations_engine_rabbitmq_test` | Test database dropped completely; 0 rows in `pg_database` | `LIVE_RABBITMQ_VERIFIED` |

---

## 5. Frozen Consumer Runner Path Verification (Task 1J.2B.1)

Task 1J.2B.1 closed the verification gap identified in Task 1J.2B-CROSSCHECK-01 by testing the top-level frozen supervisor, [RabbitMqConsumerRunner](file:///f:/operations-engine/app/events/consumers/rabbitmq_runner.py), against real infrastructure without bypassing runner-owned topology declaration or push subscription mechanisms.

Marker: **`CONSUMER_RUNNER_LIVE_VERIFIED = YES`**

| Aspect | Verification Implementation | Live Empirical Evidence | Status |
| :--- | :--- | :--- | :---: |
| **Runner Topology Ownership** | Zero test-side topology declarations (`declare_exchange`, `declare_queue`, `bind` strictly absent from test source). Runner instantiated and started with `run(stop_event=stop_event)`. | Native inspection via `rabbitmqctl` confirmed runner declared: main topic exchange, direct DLX, durable DLQ bound to DLX, durable work queue with `x-dead-letter-exchange` args, and topic bindings. | **PASS** |
| **Runner State Transition** | Monitored lifecycle states through `runner.state`. | State transitioned deterministically from `NEW` to `RUNNING` within 100ms of startup. | **PASS** |
| **Push Consumption** | Canonical event published via `RabbitMqEventPublisher`. Message delivered to consumer via RabbitMQ broker push callback (`main_queue.consume(self._on_message, no_ack=False)`). Zero polling / `queue.get()` used. | `SpyDownstreamHandler` received event; `call_count == 1`; received event ID matched published envelope exactly. | **PASS** |
| **Full Inbox Dedupe Path** | Same canonical event published twice to the runner-supervised exchange. | First delivery recorded in `inbox_messages` and invoked downstream handler; second delivery flagged duplicate by `ConsumerEventProcessor`, downstream handler NOT called (`call_count == 1`); exactly 1 row in PostgreSQL; work queue drained. | **PASS** |
| **Duplicate ACK Behavior** | Verified message removal from broker ready/unacked queue states for duplicate delivery. | Work queue message count returned to 0; ACK issued by frozen `RabbitMqInboundMessageHandler` upon receiving `ConsumerProcessingOutcome.DUPLICATE`. | **PASS** |
| **Malformed Message Full Path** | Raw malformed JSON message published to runner-declared exchange. Push delivered to runner. | `RabbitMqInboundMessageHandler` caught `INVALID_JSON`, invoked `reject(requeue=False)`. Downstream handler call count = 0. Work queue cleared (0 ready, 0 unacked). Poison message arrived in runner-declared DLQ. | **PASS** |
| **DLQ Arrival & Drain** | Passively retrieved dead-letter message from runner-declared DLQ (`channel.get_queue(..., ensure=False)`). | Message payload matched malformed input bytes exactly; positive ACK drained DLQ. | **PASS** |
| **Configured QoS & Concurrency Bound** | Configured `max_concurrency = 1` and `prefetch_count = 1`. Handler A blocked on barrier; Message B published while A blocked. | Message B did NOT enter handler while A was executing (`call_count == 1`). `peak_concurrency` remained strictly <= 1. Barrier released: A finished, then B entered and finished (`call_count == 2`, `peak_concurrency == 1`). | **PASS** |
| **Graceful Teardown** | Signaled `stop_event.set()`, awaited runner task. | Runner cancelled consumer tag on broker, drained active callbacks, closed channel, closed connection, and transitioned state to `STOPPED` with zero lingering tasks or leaked exceptions. | **PASS** |

---

## 6. Distributed Transaction Boundary Documentation

In accordance with Phase-1 transactional outbox architecture:
- **No Distributed Transactions (XA/2PC)**: RabbitMQ publish and PostgreSQL persistence are never joined in a single distributed transaction.
- **Phase 1 (Domain Atomicity)**: The application transaction inserts business data and writes the canonical `EventEnvelope` to `outbox_events` within the same atomic PostgreSQL transaction.
- **Phase 2 (Dispatcher Claim)**: A separate worker or dispatcher polls `outbox_events` using `SELECT ... FOR UPDATE SKIP LOCKED` and claims a batch by setting `claim_token` and `claimed_at`.
- **Phase 3 (Network Transport)**: The dispatcher publishes each claimed event over AMQP using `RabbitMqEventPublisher.publish()`. This network I/O executes outside of any database transaction.
- **Phase 4 (Settlement Transaction)**: Upon receiving publisher confirmation from RabbitMQ, the dispatcher marks the outbox row as published (`published_at = func.now()`, clears `claim_token` and `claimed_at`) in a fresh database transaction.
- **At-Least-Once Delivery**: In the event of a dispatcher crash between publish confirmation and database settlement, the lease expires (`claimed_at + lease_duration`), allowing another dispatcher attempt. Downstream consumers guarantee idempotent processing via `inbox_messages` unique constraint on `(consumer_name, event_id)`.

---

## 7. External Workload Isolation Proof

External container metadata may have been observed read-only for isolation proof. No external container was stopped, restarted, reconfigured, or mutated during corrected verification runs, and no external application/database data was queried.

Metadata observed for unrelated container `hischolar-cv-suite-postgres-1`:
- **Observed State During Task 1J.2B.1 Run**:
  ```json
  {"id": "9bdf29fab3914923f32044769e4c264868e0af69e12b41b29d704edb7a5fdf54", "status": "running", "running": true, "restart_policy": "unless-stopped"}
  ```
- **Post-Run Verification**:
  ```json
  {"id": "9bdf29fab3914923f32044769e4c264868e0af69e12b41b29d704edb7a5fdf54", "status": "running", "running": true, "restart_policy": "unless-stopped"}
  ```

Result: External container operated undisturbed on host port `5432` while E-7 ran on dedicated isolated host ports `55432` and `5673`.

---

## 8. Deferred Items & Worker Runtime Reconciliation

- **Worker Runtime (Historical Boundary & Reconciled Status)**: At the time of Task 1J.2B execution, the worker runtime was unbuilt (`WORKER_RUNTIME_STATUS = BLOCKED_PENDING_UNIFIED_WORKER_ENTRYPOINT`). Subsequently, Task 1J.3B implemented `app.worker` (outbox-only) and Task 1J.3C live-verified it inside real Docker containers (`WORKER_RUNTIME_STATUS = OUTBOX_ONLY_LIVE_VERIFIED`). Inbound consumption remains deferred (`INBOUND_CONSUMER_RUNTIME_STATUS = DEFERRED_MISSING_PRODUCTION_EVENT_HANDLER`).
- **Job Read API Mount**: `JOB_READ_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_TENANT_AND_DB_DEPENDENCIES`.
- **Flowable Verification**: Workflow engine integration deferred to Phase 3.
- **Task 1K**: Observability foundation unstarted.

---

## 9. Summary of Created & Modified Files

### Created Files
- [tests/integration/rabbitmq/__init__.py](file:///f:/operations-engine/tests/integration/rabbitmq/__init__.py)
- [tests/integration/rabbitmq/conftest.py](file:///f:/operations-engine/tests/integration/rabbitmq/conftest.py): Opt-in fixture `E7_RUN_RABBITMQ_INTEGRATION`, dynamic port configuration, session vhost lifecycle (`e7_integration`), test database lifecycle (`operations_engine_rabbitmq_test`), NullPool async sessions, UoW and publisher settings factories.
- [tests/integration/rabbitmq/test_broker.py](file:///f:/operations-engine/tests/integration/rabbitmq/test_broker.py): Broker ping, version reporting, exchange/queue/DLX/DLQ topology declaration and native `rabbitmqctl` inspection.
- [tests/integration/rabbitmq/test_publisher.py](file:///f:/operations-engine/tests/integration/rabbitmq/test_publisher.py): Publisher start/publish/close lifecycle, AMQP metadata round-trip (`content_type`, `content_encoding`, `message_id`, `type`, `correlation_id`), canonical body round-trip.
- [tests/integration/rabbitmq/test_consumer.py](file:///f:/operations-engine/tests/integration/rabbitmq/test_consumer.py): Component-level happy-path consumer ACK, PostgreSQL-backed inbox deduplication on `(consumer_name, event_id)`, malformed message rejection (`basic_reject(requeue=False)`), dead-letter queue routing.
- [tests/integration/rabbitmq/test_consumer_runner.py](file:///f:/operations-engine/tests/integration/rabbitmq/test_consumer_runner.py): End-to-end frozen `RabbitMqConsumerRunner` verification: runner-owned topology declaration, push consumption, inbox deduplication with PostgreSQL, malformed message rejection to DLQ, bounded concurrency (`max_concurrency=1`, `prefetch=1`), and graceful teardown.
- [tests/integration/rabbitmq/test_outbox.py](file:///f:/operations-engine/tests/integration/rabbitmq/test_outbox.py): Transactional outbox DB persistence, dispatcher claim, real broker publish, DB settlement (`published_at`, `dispatch_attempt_count=1`), broker payload validation.
- [docs/phase-1/PHASE_1_RABBITMQ_RUNTIME_VERIFICATION.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_RABBITMQ_RUNTIME_VERIFICATION.md)

### Modified Files
- [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md): Updated status for Task 1J.1, 1J.2A, 1J.2B, 1J.2B.1, and canonical Task 1J.2.
