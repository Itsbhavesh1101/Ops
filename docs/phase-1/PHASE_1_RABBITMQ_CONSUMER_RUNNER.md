# Phase 1 Task 1G.4C2 — RabbitMQ Consumer Topology, Bounded Runner & Graceful Lifecycle

**Status**: Implementation Complete (`TASK_1G_IMPLEMENTATION_STATUS = COMPLETE`)  
**Live Integration**: `DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION`  
**Phase**: Phase 1 — Foundation (Final increment of Canonical Task 1G)  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)

---

## 1. Overview and Purpose

Task 1G.4C2 completes the operational transport and supervision layer surrounding the frozen single-message boundary adapter (`RabbitMqInboundMessageHandler`, established in Task 1G.4C1 / 1G.4C1A).

Where Task 1G.4C1 established the one-message trust boundary (wire payload validation, canonical `EventEnvelope` model validation, strict RFC-compliant JSON parsing, dispatch to `ConsumerEventProcessor`, and broker `basic_ack` / `basic_reject`), Task 1G.4C2 provides the long-running broker connection, durable topology declaration, channel QoS prefetch, topic bindings, dead-letter routing, bounded concurrency, fail-fast supervision, and graceful shutdown lifecycle.

```
+---------------------------------------------------------------------------------------+
| RabbitMqConsumerRunner (Task 1G.4C2)                                                  |
|                                                                                       |
|   connect_robust() -> channel() -> set_qos(prefetch=max_concurrency)                  |
|   -> declare main topic exchange (durable)                                            |
|   -> declare DLX (direct, durable) + DLQ (durable) + DLQ bind                         |
|   -> declare main queue (durable, x-dead-letter-exchange, x-dead-letter-routing-key)   |
|   -> bind main queue for each configured topic key                                    |
|   -> queue.consume(on_message, no_ack=False)                                          |
|                                                                                       |
|   +-------------------------------------------------------------------------------+   |
|   | Delivery Callback & Concurrency Bound: asyncio.Semaphore(max_concurrency)     |   |
|   |                                                                               |   |
|   |   +-----------------------------------------------------------------------+   |   |
|   |   | RabbitMqInboundMessageHandler.handle(message) (FROZEN C1)             |   |   |
|   |   |   Wire validation -> EventEnvelope -> ConsumerEventProcessor.process() |   |   |
|   |   |   -> ACK on PROCESSED/DUPLICATE                                       |   |   |
|   |   |   -> reject(requeue=False) on invalid wire format (routed to DLQ)     |   |   |
|   |   |   -> unhandled exception leaves message unsettled                     |   |   |
|   |   +-----------------------------------------------------------------------+   |   |
|   |                                                                               |   |
|   |   If handle() raises: fail-fast supervisor cancels subscription, initiates    |   |
|   |   shutdown, preserves first primary error, and message remains unsettled.     |   |
|   +-------------------------------------------------------------------------------+   |
|                                                                                       |
|   Graceful Teardown:                                                                  |
|   cancel subscription -> drain active callbacks (up to grace) -> force cancel         |
|   -> close channel -> close connection -> propagate primary or cleanup error         |
+---------------------------------------------------------------------------------------+
```

---

## 2. Configuration Boundaries & Fail-Closed Settings

Consumer configuration is encapsulated in `RabbitMqConsumerSettings`, inheriting from `pydantic_settings.BaseSettings` with prefix `E7_`:

- `rabbitmq_url`: `SecretStr`, required. Validated to start with `amqp://` or `amqps://`. Masked in `repr` and `str`; never logged or exposed in exceptions.
- `rabbitmq_exchange_name`: `str`, required, non-empty, stripped, max 255 characters.
- `rabbitmq_consumer_queue_name`: `str`, required, non-empty, stripped, max 255 characters.
- `rabbitmq_consumer_binding_keys`: `tuple[str, ...]`, required. Must be non-empty. Rejects duplicate keys, empty dot segments, leading/trailing whitespace, and malformed wildcard segments. Wildcards `*` and `#` must constitute entire topic segments.
- `rabbitmq_dead_letter_exchange_name`: `str`, required, non-empty, stripped, max 255 characters.
- `rabbitmq_dead_letter_queue_name`: `str`, required, non-empty, stripped, max 255 characters.
- `rabbitmq_dead_letter_routing_key`: `str`, required, non-empty, stripped, max 255 characters.
- `rabbitmq_consumer_max_concurrency`: `int`, required, bounded `>= 1`.
- `rabbitmq_connect_timeout_seconds`: `float`, default `10.0`, bounded `(0.0, 300.0]`.

**Fail-Closed Invariant**: Zero production defaults for URLs, credentials, exchange names, queue names, DLX names, DLQ names, dead-letter routing keys, or concurrency. Missing topology configuration fails immediately at validation.

---

## 3. Queue Identity vs. Logical Consumer Identity

The RabbitMQ consumer queue name is transport-level deployment configuration (`rabbitmq_consumer_queue_name`).

- **Queue Name**: Represents an operational AMQP broker entity receiving routed topic messages.
- **Logical Consumer Name**: Represents the transactional Inbox deduplication boundary (`consumer_name` in `inbox_messages`).

The runner maintains strict separation: it never derives queue names from `consumer_name`, and never asserts or requires equality. The `ConsumerEventProcessor` injected into the C1 handler already possesses its own logical identity.

---

## 4. Robust Connection & Channel Lifecycle

- **Client**: `aio_pika.connect_robust(...)` via an injectable `connection_factory` (defaults to `aio_pika.connect_robust`).
- **Seam**: Allows deterministic testing with fake connections without network I/O.
- **Construction Safety**: Runner and settings instantiation performs zero network I/O. Network operations commence only when `await runner.run(stop_event=stop_event)` is called.
- **Pre-set Stop Check**: If `stop_event.is_set()` prior to `run()`, the runner immediately transitions to `ConsumerRunnerState.STOPPED` and returns cleanly without opening channels or connections.
- **One-Shot Lifecycle**: Runner transitions deterministically through `NEW -> STARTING -> RUNNING -> STOPPING -> STOPPED`. Re-running or restarting after stopping raises `RuntimeError`.

---

## 5. Main Exchange, Consumer Queue & Topic Bindings

1. **Main Event Exchange**: Declared as `type = aio_pika.ExchangeType.TOPIC`, `durable = True`. Matches publisher transport declaration.
2. **Main Consumer Queue**: Declared as `durable = True`, `exclusive = False`, `auto_delete = False`.
3. **Topic Bindings**: The main queue is bound to the main exchange for every key in `rabbitmq_consumer_binding_keys`. Exactly one `bind` operation is executed per configured binding key. No hardcoded event registry exists.

---

## 6. Dead-Letter Exchange (DLX) & Dead-Letter Queue (DLQ)

Task 1G.4C1 message rejection calls `message.reject(requeue=False)` upon encountering invalid wire formatting, malformed JSON, or invalid envelopes. Without a configured dead-letter exchange, RabbitMQ drops rejected messages.

To prevent message loss:
1. **DLX Declaration**: Declared as `type = aio_pika.ExchangeType.DIRECT`, `durable = True`.
2. **DLQ Declaration**: Declared as `durable = True`, `exclusive = False`, `auto_delete = False`.
3. **DLQ Binding**: DLQ is bound to DLX using `rabbitmq_dead_letter_routing_key`.
4. **Main Queue DLX Arguments**: Main consumer queue is declared with AMQP arguments:
   - `x-dead-letter-exchange`: configured DLX name
   - `x-dead-letter-routing-key`: configured dead-letter routing key
5. **Declaration Order**: DLX and DLQ topology is established and bound *prior* to declaring the main queue and *prior* to registering the consumer subscription.

**No Arbitrary Retry Queue**: No retry exchanges, TTL queues, or exponential retry loops are declared. Unhandled processing errors remain unsettled and redeliver upon connection close.

---

## 7. Prefetch QoS & Bounded Concurrency

- **Channel QoS**: Configured as `channel.set_qos(prefetch_count=runner_policy.max_concurrency)`. This bounds unacknowledged deliveries at the broker level. Unlimited prefetch (`0`) is strictly prohibited.
- **Client-Side Bound**: Enforced via `asyncio.Semaphore(runner_policy.max_concurrency)` before invoking the C1 handler.
- **Single Source of Truth**: Concurrency is unified across transport settings, runner policy, channel QoS, and semaphore.
- **Peak Concurrency Guarantee**: Peak concurrent executions of `handle()` are proven never to exceed `max_concurrency`.

---

## 8. Consumer Registration & Callback Responsibility

- **Registration**: Registered via `queue.consume(self._on_message, no_ack=False)`.
- **Consumer Tag**: Retained for clean cancellation during shutdown.
- **No Secondary Disposition**: The callback's sole operation is `await self._inbound_handler.handle(message)`. The runner executes zero secondary `message.ack()`, `message.reject()`, or `message.nack()` calls.
- **Task Tracking**: Active callback tasks are tracked in an internal set so shutdown supervisor knows when all processing has concluded.

---

## 9. Fail-Fast Supervision & Processing Failure Semantics

If `inbound_handler.handle(message)` raises an unhandled exception:
1. **Immediate Notification**: The callback records the exception and signals `self._failure_event`.
2. **First Failure Wins**: The first observed exception becomes `self._primary_error`. Sibling errors do not overwrite it.
3. **Stop New Deliveries**: The supervisor cancels the consumer subscription (`queue.cancel(consumer_tag)`).
4. **Message Remains Unsettled**: The runner issues no ACK, NACK, or reject. The unacknowledged message will be recovered by RabbitMQ upon channel teardown.
5. **Teardown**: Active sibling callbacks are awaited within the grace window; channel and connection are closed in order.
6. **Propagation**: The original primary exception propagates out of `run()`.

---

## 10. External Stop Handling & Graceful Teardown

When an external `stop_event` is set:
1. **State Transition**: State becomes `ConsumerRunnerState.STOPPING`.
2. **Subscription Cancellation**: `await queue.cancel(consumer_tag)` prevents new deliveries.
3. **Rejection of Waiting Callbacks**: Callbacks delivered to client memory but waiting on concurrency semaphore exit immediately without calling `handle()`, leaving their messages unsettled for broker redelivery.
4. **Graceful Completion**: Callbacks currently executing `handle()` are allowed to complete up to `shutdown_grace_period`.
5. **Forced Cancellation on Timeout**: If callbacks remain active after the grace period expires, they are cancelled via `task.cancel()` and awaited.
6. **Resource Cleanup Order**:
   1. Consumer subscription cancelled.
   2. Active callbacks drained / cancelled.
   3. Channel closed.
   4. Connection closed.
   5. State transitions to `ConsumerRunnerState.STOPPED`.
7. **Clean Exit**: `run()` returns cleanly without error.

---

## 11. Startup & Cleanup Error Handling

- **Startup Rollback**: If an error occurs during connection, channel creation, QoS configuration, topology declaration, binding, or subscription registration, any opened channel and connection are closed safely. The runner transitions to `STOPPED` and propagates a safe lifecycle error (`RabbitMqConsumerConnectionError`, `RabbitMqConsumerTopologyError`, or `RabbitMqConsumerSubscriptionError`).
- **Primary Error Preservation**: If callback processing failed and subsequent channel/connection close also fails, the primary callback failure remains propagated. Close failures are attached via `primary_error.add_note(...)`.
- **Normal Close Failure**: If a normal external stop encounters channel or connection close failure, the close error propagates rather than reporting clean shutdown.
- **Secret Safety**: No broker URL, username, password, or payload text is included in public exception messages or notes.

---

## 12. Architectural Isolation & Boundaries

- **Zero DB Access**: Runner imports zero SQLAlchemy, `AsyncSession`, `InboxRepository`, or `OutboxRepository`.
- **Zero Event Parsing**: Runner performs zero JSON parsing, byte decoding, or `EventEnvelope` model validation.
- **Zero Disposition Logic**: Runner contains zero direct calls to `message.ack()`, `message.reject()`, or `message.nack()`.
- **No App / Docker Wiring**: `app/main.py`, FastAPI dependencies, Dockerfile, and docker-compose remain untouched until Task 1J.
- **No Dependencies Added**: Uses existing `aio-pika`.
- **Schema Stability**: `Base.metadata` remains `{audit_events, outbox_events, inbox_messages}`. Alembic head remains `0005`. Zero migrations created.

---

## 13. Runtime Integration Status

```python
RABBITMQ_CONSUMER_RUNNER_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
```

Live broker integration with real RabbitMQ and PostgreSQL instances is deferred to Task 1J runtime integration testing, covering:
- Real broker durable topic exchange and queue creation.
- Live message routing across topic patterns.
- Dead-letter routing of rejected invalid messages.
- Real prefetch enforcement across multiple workers.
- Connection drop recovery via `connect_robust`.
- Broker restart topology recovery.
- Transactional Inbox deduplication after redelivery.

---

## 14. Files Created and Modified

### Created
- `app/events/consumers/config.py`: `RabbitMqConsumerSettings` and `RabbitMqConsumerRunnerPolicy`.
- `app/events/consumers/rabbitmq_runner.py`: `RabbitMqConsumerRunner`, `ConsumerRunnerState`, lifecycle exceptions, and protocol.
- `tests/unit/test_rabbitmq_consumer_runner.py`: 29 unit tests covering settings, topology, QoS, bounded concurrency, fail-fast supervision, graceful teardown, and error preservation.
- `docs/phase-1/PHASE_1_RABBITMQ_CONSUMER_RUNNER.md`: Architectural documentation.

### Modified
- `app/events/consumers/__init__.py`: Exported new consumer configuration, runner, exceptions, and lifecycle states.
- `.env.example`: Added safe non-production syntax placeholders for consumer queue, binding keys, and DLX/DLQ settings.
- `docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md`: Updated to declare Task 1G implementation increments complete.
