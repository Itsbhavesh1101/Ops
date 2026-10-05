# Phase 1 Task 1G.3A — RabbitMQ Publisher Transport Foundation

## 1. Executive Summary & Purpose

Task 1G.3A establishes the **RabbitMQ Publisher Transport Foundation** for the IR Infotech E-7 Operations Engine.
It introduces the provider-neutral event publishing abstraction and a robust, async RabbitMQ transport implementation capable of publishing canonical [`EventEnvelope`](file:///f:/operations-engine/app/events/schemas.py#L38) instances.

This task delivers **transport capabilities only**:
- Provider-neutral [`EventPublisher`](file:///f:/operations-engine/app/events/publishers/base.py#L39) protocol.
- Async RabbitMQ adapter ([`RabbitMqEventPublisher`](file:///f:/operations-engine/app/events/publishers/rabbitmq.py#L42)) using `aio-pika`.
- Settings-driven configuration with `SecretStr` credential masking.
- Explicit connection and channel lifecycle management (`NEW` $\rightarrow$ `STARTED` $\rightarrow$ `CLOSED`).
- Durable `topic` exchange declaration with zero queue definitions.
- Publisher confirms with mandatory routing enforcement.
- Canonical message framing reusing [`serialize_event_envelope()`](file:///f:/operations-engine/app/events/serialization.py#L12).
- Complete isolation from PostgreSQL, SQLAlchemy, and Outbox dispatcher loops.

---

## 2. Authority & Source-of-Truth Boundary

- **PostgreSQL is Canonical**: PostgreSQL [`outbox_events`](file:///f:/operations-engine/app/events/outbox/models.py#L21) is the durable source of truth recording whether an event was staged by E-7.
- **RabbitMQ is Transport Only**: RabbitMQ serves strictly as a message distribution and routing pipeline. It is never the authoritative system of record.
- **Contract Integrity**: The transport transmits the frozen, canonical [`EventEnvelope`](file:///f:/operations-engine/app/events/schemas.py#L38) contract established in Task 1G.1/1G.1A. No secondary or competing event schema is introduced.

---

## 3. Provider-Neutral Publisher Boundary

The core event publication interface is defined in [`app/events/publishers/base.py`](file:///f:/operations-engine/app/events/publishers/base.py):

```python
@runtime_checkable
class EventPublisher(Protocol):
    async def publish(self, envelope: EventEnvelope) -> None: ...
```

### 3.1 Design Principles
1. **Zero AMQP Leakage**: The protocol does not expose `aio_pika.Message`, `Exchange`, `Channel`, `Connection`, or delivery tags.
2. **Zero Persistence Coupling**: The protocol accepts only [`EventEnvelope`](file:///f:/operations-engine/app/events/schemas.py#L38); it has no dependency on ORM sessions or outbox records.
3. **Safe Error Hierarchy**: Defines [`EventPublishError`](file:///f:/operations-engine/app/events/publishers/base.py#L23), [`PublisherNotStartedError`](file:///f:/operations-engine/app/events/publishers/base.py#L27), [`PublisherConnectionError`](file:///f:/operations-engine/app/events/publishers/base.py#L31), and [`PublisherStateError`](file:///f:/operations-engine/app/events/publishers/base.py#L35).

---

## 4. RabbitMQ Client Library & Configuration

### 4.1 Client Selection: `aio-pika`
- **Library**: `aio-pika>=9.4.0,<10.0.0`
- **Rationale**: Fully async AMQP 0-9-1 client built on `asyncio` and `aiormq`. Native support for `connect_robust` connection auto-reconnect, publisher confirms, and async context managers.
- Single AMQP library policy: No secondary libraries (`pika`, `kombu`, `celery`) introduced.

### 4.2 Fail-Closed Configuration: [`RabbitMqPublisherSettings`](file:///f:/operations-engine/app/events/publishers/config.py#L11)
- Reads settings using `env_prefix="E7_"`.
- **`E7_RABBITMQ_URL`**: **Required**. Stored as `SecretStr`. Masked automatically in `repr(settings)` and error strings as `'**********'`. Zero production-code default or fallback to localhost/guest. Missing URL fails closed before any network initialization.
- **`E7_RABBITMQ_EXCHANGE_NAME`**: **Required**. Configurable exchange identifier (validated non-empty, stripped, $\le 255$ chars). Zero hardcoded fallback in production Python code.
- **`E7_RABBITMQ_CONNECT_TIMEOUT_SECONDS`**: Technical connection timeout (default: `10.0`, bounded by `gt=0.0` and `le=300.0`).
- **Placeholder Guarantee**: `.env.example` provides syntax-only placeholders (`amqp://<username>:<password>@<host>:5672/<vhost>`); zero real or development defaults are fabricated in code.
- **Explicit Injection**: [`RabbitMqEventPublisher`](file:///f:/operations-engine/app/events/publishers/rabbitmq.py#L42) requires explicit `settings: RabbitMqPublisherSettings` injection in its constructor. Missing settings raises `TypeError`.

---

## 5. Lifecycle Management & Zero Import-Time Network

### 5.1 Explicit State Model
[`RabbitMqEventPublisher`](file:///f:/operations-engine/app/events/publishers/rabbitmq.py#L42) enforces a three-state lifecycle:
`NEW` $\rightarrow$ `STARTED` $\rightarrow$ `CLOSED`.

1. **Zero Import-Time Network**: Module import and constructor invocation (`RabbitMqEventPublisher()`) perform **zero** network I/O.
2. **Explicit Startup (`await publisher.start()`)**:
   - Invokes `connection_factory` (production default: `aio_pika.connect_robust`).
   - Opens channel with `publisher_confirms=True` and `on_return_raises=True`.
   - Declares durable `topic` exchange.
   - Idempotent: Calling `start()` while already `STARTED` is a safe no-op.
   - Transitions to `STARTED` strictly upon successful initialization.
3. **Start Failure & Cleanup**:
   - If connection, channel, or exchange declaration fails, any partially acquired channel or connection is closed immediately.
   - State remains `NEW`.
   - Raises [`PublisherConnectionError`](file:///f:/operations-engine/app/events/publishers/base.py#L31) without leaking credentials.
4. **Shutdown (`await publisher.close()`)**:
   - Closes open channel and connection resources safely.
   - Transitions to `CLOSED`.
   - Repeated calls are safe no-ops.
5. **Async Context Manager**:
   - `async with publisher:` automatically starts and closes the publisher.

---

## 6. Exchange & Routing Topology

### 6.1 Topic Exchange
- **Type**: `aio_pika.ExchangeType.TOPIC` (`topic`).
- **Durable**: `True` (persists broker restarts).
- **Routing Key**: Evaluates strictly to [`EventEnvelope.event_type`](file:///f:/operations-engine/app/events/schemas.py#L43) (e.g. `task.blocked`, `workflow.task.created`, `schedule.published`).
- **Zero Queue Declarations**: Task 1G.3A does **not** declare or bind queues. Queue topology is owned by downstream consumers (Task 1G.4 for E-7 Inbox; external systems for other subscribers).

---

## 7. Message Framing & Delivery Guarantees

### 7.1 Message Framing
| Property | Value / Mapping |
| :--- | :--- |
| `body` | `serialize_event_envelope(envelope).encode("utf-8")` |
| `routing_key` | `envelope.event_type` |
| `message_id` | `str(envelope.event_id)` (canonical identity preserved end-to-end) |
| `content_type` | `"application/json"` |
| `content_encoding` | `"utf-8"` |
| `type` | `envelope.event_type` |
| `correlation_id` | `envelope.correlation_id` (propagated when present) |
| `timestamp` | `envelope.occurred_at` |
| `delivery_mode` | `aio_pika.DeliveryMode.PERSISTENT` (persisted to disk by broker) |

### 7.2 Publisher Confirms & Mandatory Routing
- **Publisher Confirms**: Enabled on the channel. `exchange.publish()` awaits broker acknowledgement. Success is never reported merely because bytes left the socket.
- **Mandatory Routing (`mandatory=True`, `on_return_raises=True`)**:
  - Unroutable messages returned by the broker (`basic.return`) trigger an error.
  - Wrapped into safe [`EventPublishError`](file:///f:/operations-engine/app/events/publishers/base.py#L23).
  - *Note*: Because Task 1G.3A declares no queues, live mandatory publication requires at least one queue bound to the exchange topic pattern.

### 7.3 Delivery Semantics: At-Least-Once
- Durable exchange + persistent message + publisher confirms provide the transport durability foundation.
- **Why Exactly-Once is NOT Claimed**: If network partitions or crashes occur after broker confirmation but before Task 1G.3B updates `outbox_events.published_at`, the dispatcher will republish the event upon restart.
- End-to-end delivery guarantee is **at-least-once**.
- Consumers must deduplicate incoming events using `message_id` / `event_id` (Task 1G.4).

---

## 8. Database Isolation & Non-Activation

- **Zero Database Access**: The publisher has zero imports or dependencies on SQLAlchemy, `AsyncSession`, `OutboxRepository`, or `OutboxEventRecord`.
- **Zero Dispatcher Logic**: No polling loop, `SKIP LOCKED` queries, retry counters, or background worker tasks exist in Task 1G.3A (deferred to Task 1G.3B).
- **Not Production-Activated**: The adapter is not wired to FastAPI startup or outbox polling.

---

## 9. Verification & Live Status

- **Unit Testing**: All tests in [`tests/unit/test_rabbitmq_publisher.py`](file:///f:/operations-engine/tests/unit/test_rabbitmq_publisher.py) use mocks/fakes via `connection_factory` injection seam.
- **Live Integration Deferred**:
  ```python
  RABBITMQ_PUBLISH_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
  ```
  Task 1J live integration will verify:
  1. Real broker connection and reconnect behavior.
  2. Durable topic exchange creation.
  3. Persistent message confirmation.
  4. Routing key matching with consumer queue bindings.
  5. Mandatory unroutable publication handling.

---

## 10. Summary of Files Created & Modified

| File Path | Action | Description |
| :--- | :---: | :--- |
| [`app/events/publishers/base.py`](file:///f:/operations-engine/app/events/publishers/base.py) | **Created** | Provider-neutral `EventPublisher` protocol, `PublisherState`, and error hierarchy. |
| [`app/events/publishers/config.py`](file:///f:/operations-engine/app/events/publishers/config.py) | **Created** | `RabbitMqPublisherSettings` with `SecretStr` credential masking. |
| [`app/events/publishers/rabbitmq.py`](file:///f:/operations-engine/app/events/publishers/rabbitmq.py) | **Created** | Concrete `RabbitMqEventPublisher` adapter using `aio-pika`. |
| [`app/events/publishers/__init__.py`](file:///f:/operations-engine/app/events/publishers/__init__.py) | **Modified** | Exported publisher abstractions and implementations. |
| [`pyproject.toml`](file:///f:/operations-engine/pyproject.toml) | **Modified** | Added `aio-pika>=9.4.0,<10.0.0` runtime dependency. |
| [`.env.example`](file:///f:/operations-engine/.env.example) | **Modified** | Added safe `E7_RABBITMQ_*` configuration placeholders. |
| [`tests/unit/test_rabbitmq_publisher.py`](file:///f:/operations-engine/tests/unit/test_rabbitmq_publisher.py) | **Created** | Comprehensive unit test suite (16 tests) for publisher transport. |
| [`docs/phase-1/PHASE_1_RABBITMQ_PUBLISHER_TRANSPORT.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_RABBITMQ_PUBLISHER_TRANSPORT.md) | **Created** | Technical architecture and implementation documentation. |
| [`docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) | **Modified** | Recorded Task 1G.3A completion; retained canonical Task 1G as IN PROGRESS. |

---

## 11. Deferred Work

Canonical Task 1G remains **IN PROGRESS**:
- **Task 1G.3B**: Outbox Dispatcher (polling loop, row claiming via `SELECT FOR UPDATE SKIP LOCKED`, retry/backoff, calling publisher, transitioning `published_at`).
- **Task 1G.4**: Inbox & Consumer Deduplication (`inbox_messages` table, consumer worker, idempotent deduplication, DLQ).
