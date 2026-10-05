# Phase 1 Task 1G.3B3 — Bounded Outbox Dispatcher Runner & Graceful Lifecycle

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1G.3B3 — Bounded Outbox Dispatcher Runner & Graceful Lifecycle  
**Status**: `COMPLETE` (Canonical Task 1G remains `IN PROGRESS`)  
**Scope**: Long-Running Outbox Dispatcher Runner and Operational Worker Lifecycle Supervision  

---

## 1. Purpose

Task 1G.3B3 implements the operational, long-running dispatcher runner (`OutboxDispatcherRunner`) supervising bounded concurrent workers around the frozen single-event dispatch unit of work:

```python
await dispatcher.dispatch_once()
```

The runner provides:
- **Managed Publisher Lifecycle**: Starts the provider-neutral event publisher before spawning worker tasks, and guarantees orderly closure after worker termination.
- **Bounded Worker Concurrency**: Spawns exactly `runner_policy.max_concurrency` worker tasks executing serial single-event dispatches.
- **Cadence & Stop-Aware Idle Polling**: Implements adaptive cadence—immediate continuation for non-idle outcomes (`PUBLISHED`, `RETRY_SCHEDULED`, `CLAIM_LOST_*`), and stop-aware sleeping during `IDLE` outcomes.
- **Graceful Shutdown & Bounded Draining**: Allows in-flight dispatch invocations to drain cleanly within `runner_policy.shutdown_grace_period` upon receiving an external shutdown signal.
- **Fail-Fast Worker Supervision**: Cancels sibling workers and aborts the runner loop if any worker raises an unhandled infrastructure, lifecycle, or programming error.
- **Primary Error Preservation**: Preserves primary worker failures without mask or replacement even if subsequent publisher cleanup encounters secondary exceptions.

The runner does **not** duplicate claim SQL, retry calculation, publication state transitions, envelope serialization, or broker framing—these remain encapsulated within the frozen Task 1G.3B2 `OutboxDispatcher`.

---

## 2. Relationship to Frozen `dispatch_once()`

`OutboxDispatcher.dispatch_once()` serves as the frozen, atomic unit of work for each worker:

1. **Claiming**: Opens a transaction, claims the next available outbox event matching lease and retry criteria (`SKIP LOCKED`), and commits the claim.
2. **Network Publication**: Publishes the event to the broker outside any database transaction, enforcing timeouts and classifying broker responses.
3. **Settlement**: Opens a fresh transaction to mark the event published (`published_at`, `dispatch_state="PUBLISHED"`) or reschedule retry (`next_retry_at`, exponential backoff).

The runner interacts with this subsystem solely via the provider-neutral `SingleEventDispatcher` protocol:
```python
class SingleEventDispatcher(Protocol):
    async def dispatch_once(self) -> DispatchResult: ...
```

The runner inspects only `DispatchResult.outcome` to guide worker loop scheduling. It never calls repository claim methods directly, never alters event records, and never manages database transactions.

---

## 3. Provider-Neutral Publisher Lifecycle

The runner interacts with the message broker transport exclusively through a provider-neutral lifecycle protocol:

```python
class ManagedEventPublisher(EventPublisher, Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
```

- **Architectural Neutrality**: The runner contains zero imports of `aio_pika`, `RabbitMqEventPublisher`, AMQP channels, exchanges, or connection URLs.
- **Structural Compatibility**: The existing `RabbitMqEventPublisher` (frozen in Task 1G.3A) structurally satisfies `ManagedEventPublisher` without modification.
- **Future Extensibility**: Any future publisher implementation (e.g., in-memory, AWS SNS/SQS, Kafka) exposing asynchronous `start()` and `close()` can be supervised by `OutboxDispatcherRunner`.

---

## 4. Operational Runner Policy (`OutboxRunnerPolicy`)

Runner operational bounds are defined via an immutable, strictly validated Pydantic model (`OutboxRunnerPolicy`):

```python
class OutboxRunnerPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    poll_interval: timedelta
    max_concurrency: int
    shutdown_grace_period: timedelta
```

### Policy Validation Rules
- **No Hidden Production Defaults**: All fields are mandatory and must be explicitly configured by composition.
- `poll_interval > timedelta(0)`: Strictly positive duration between idle polls.
- `max_concurrency >= 1`: Strictly positive integer bounding concurrent worker tasks.
- `shutdown_grace_period > timedelta(0)`: Strictly positive duration bounding graceful shutdown drain.
- **Immutability**: `frozen=True` and `extra="forbid"` prevent runtime tampering or injection of undeclared options.

---

## 5. Deterministic Lifecycle State Machine

The runner implements a deterministic one-shot state model:

```
[ NEW ]
   │
   │  run(stop_event) invoked
   ▼
[ RUNNING ] ──(worker unhandled failure)──┐
   │                                      │
   │  stop_event.set()                    ▼
   ▼                                 [ STOPPING ]
[ STOPPING ]                              │
   │                                      │
   │  workers terminate & close()         │
   ▼                                      ▼
[ STOPPED ] <─────────────────────────────┘
```

- **`NEW`**: Runner instance initialized; zero network connections, zero database sessions, zero background tasks.
- **`RUNNING`**: Publisher successfully started (`await publisher.start()`); exactly `max_concurrency` worker tasks executing.
- **`STOPPING`**: External stop signal observed or worker failure triggered; workers completing in-flight work or being cancelled.
- **`STOPPED`**: All worker tasks terminated, publisher closed (`await publisher.close()`).

### One-Shot Invariants
- Duplicate concurrent `run()` invocations raise `RuntimeError("OutboxDispatcherRunner is already running")`.
- Restarting a previously stopped runner raises `RuntimeError("OutboxDispatcherRunner cannot be restarted; instantiate a new runner")`.

---

## 6. Worker & Concurrency Model

- **Exact Worker Count**: The runner spawns exactly `runner_policy.max_concurrency` long-running `asyncio.Task` workers via `asyncio.create_task`.
- **Bounded Concurrency**: Concurrency is bounded by the fixed number of worker loops. No dynamic task-per-event spawning or autoscaling is performed.
- **Serial Execution per Worker**: Each worker executes serial iterations of `await dispatcher.dispatch_once()`.
- **Concurrency Origin**: Concurrency stems from multiple workers independently executing single-event claims; database row-level locking (`FOR UPDATE SKIP LOCKED`) prevents claim collisions.
- **No Shared UnitOfWork or Database Sessions**: Neither the runner nor workers maintain persistent database sessions. Each `dispatch_once()` invocation manages isolated transactions using the injected `uow_factory`.

---

## 7. Cadence, Non-Idle Continuation, & Stop-Aware Idle Polling

Each worker loop adheres to an adaptive execution cadence:

```python
while not stop_event.is_set():
    result = await dispatcher.dispatch_once()

    if result.outcome == DispatchOutcome.IDLE:
        # Bounded sleep with prompt awakening upon stop_event
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
            break  # stop_event was signaled during idle sleep
        except TimeoutError:
            pass  # Interval expired without stop; continue loop
    else:
        # PUBLISHED, RETRY_SCHEDULED, CLAIM_LOST_*
        continue
```

- **Non-Idle Continuation**: If `dispatch_once()` returns work (`PUBLISHED`, `RETRY_SCHEDULED`, or claim-lost outcomes), the worker immediately loops to process the next eligible event without arbitrary sleeping.
- **Idle Cadence**: When `dispatch_once()` returns `IDLE`, the worker enters a stop-aware wait bounded by `runner_policy.poll_interval`.
- **Prompt Wakeup**: If `stop_event` is signaled while a worker is sleeping on an empty outbox, `stop_event.wait()` completes immediately via `asyncio.wait_for`, terminating the loop without waiting for the remainder of the polling interval. Busy-spinning on an empty outbox is eliminated.

---

## 8. Fail-Fast Worker Supervision & Error Preservation

The runner supervises worker tasks to detect and propagate unhandled exceptions immediately:

1. **No Broad Swallowing**: The runner does not wrap `dispatch_once()` in `except Exception: continue`. Any exception escaping `dispatch_once()` indicates an infrastructure defect (database failure, unrecoverable broker error, schema corruption, or programming bug).
2. **Sibling Cancellation**: Upon detecting an unhandled exception in any worker task, the runner signals stop, cancels all sibling worker tasks, and awaits their completion.
3. **Publisher Closure**: The runner awaits `publisher.close()` after all worker tasks have halted.
4. **Primary Error Preservation**:
   - The unhandled worker exception is the **primary error** and is propagated out of `run()`.
   - If `publisher.close()` subsequently raises a secondary exception, the secondary error is recorded on the primary exception via `BaseException.add_note(...)` (without exposing credentials) before re-raising the primary exception.
   - If normal shutdown encounters a close failure in the absence of a primary error, the close error propagates normally.

---

## 9. External Stop Handling & Graceful Shutdown

- **External Stop Event**: The runner accepts an `asyncio.Event` via `await runner.run(stop_event=stop_event)`. No OS signal handlers (`SIGINT`, `SIGTERM`) are installed within the runner; signal registration is the responsibility of the application composition root.
- **Pre-Set Stop Event**: If `stop_event.is_set()` before `run()` starts, the runner exits immediately:
  - `publisher.start()` is never called.
  - Zero worker tasks are spawned.
  - State transitions deterministically to `STOPPED`.
- **Graceful Drain Phase**:
  1. Once `stop_event` is set, workers complete their active `dispatch_once()` invocation but start no new work.
  2. The runner awaits all workers for up to `runner_policy.shutdown_grace_period`.
- **Forced Cancellation Phase**:
  1. If any workers remain active after `shutdown_grace_period` expires, the runner explicitly cancels remaining worker tasks.
  2. The runner awaits cancellation completion (`asyncio.gather(*workers, return_exceptions=True)`).
  3. Only after all workers have terminated does the runner invoke `await publisher.close()`.
- **Lease Expiry Recovery**: When a worker is cancelled during an in-flight dispatch, the database claim lock is abandoned safely. The frozen outbox design relies on lease expiration (`lease_duration`) to recover the row on subsequent runs; the runner performs no unsafe manual settlement.

---

## 10. Outer Runner Cancellation

If the external task running `runner.run()` is cancelled via `asyncio.CancelledError`:
1. The runner transitions to `STOPPING`.
2. All running worker tasks are cancelled and awaited.
3. The publisher is closed cleanly (`await publisher.close()`).
4. The state transitions to `STOPPED`.
5. The `CancelledError` is re-raised to maintain standard asyncio cancellation semantics.

---

## 11. Architectural Isolation & Boundaries

To preserve strict separation of concerns across the E-7 architecture:
- **Zero SQLAlchemy in Runner**: The runner does not import SQLAlchemy, `AsyncSession`, engine primitives, or ORM models.
- **Zero RabbitMQ Imports in Runner**: The runner does not import `aio_pika` or `RabbitMqEventPublisher`.
- **Zero Database Models / Migrations**: No database tables or schema alterations were created. Base metadata remains strictly `{"audit_events", "outbox_events"}` and Alembic revision remains `0004`.
- **Zero OS Signal Handlers**: No `signal.signal` or `loop.add_signal_handler` calls exist in runner code.
- **Zero Main/API/Docker Wiring**: `app/main.py`, health endpoints, and Docker Compose remain untouched for Task 1J.
- **Zero Inbox/Consumer**: Task 1G.4 (Inbox table, deduplication, consumers) remains strictly unstarted.

---

## 12. Verification & Test Coverage

A comprehensive unit test suite was implemented in [tests/unit/test_outbox_runner.py](file:///f:/operations-engine/tests/unit/test_outbox_runner.py) covering 25 test scenarios:

| Category | Test Case | Verified Behavior |
| :--- | :--- | :--- |
| **Policy Validation** | `test_runner_policy_valid` | Valid policies initialize correctly. |
| | `test_runner_policy_rejections` | Rejects non-positive intervals, non-positive grace periods, and concurrency < 1. |
| | `test_runner_policy_immutable` | Enforces immutability and forbids undeclared fields. |
| **Lifecycle & Init** | `test_runner_construction_no_network_io` | No publisher calls or tasks created on construction. |
| | `test_runner_pre_set_stop_event_clean_exit` | Pre-set stop exits cleanly without starting publisher or workers. |
| | `test_runner_startup_order` | `publisher.start()` completes before any worker begins dispatch. |
| | `test_runner_startup_failure_propagates` | Publisher startup error propagates; zero workers spawned. |
| | `test_runner_one_shot_lifecycle_restrictions` | Rejects duplicate concurrent `run()` and restarts after stop. |
| **Concurrency & Loop** | `test_runner_bounded_concurrency` | Exactly `max_concurrency` workers run concurrently. |
| | `test_runner_idle_polling_cadence_and_stop_aware_wakeup` | Workers sleep on `IDLE` and wake promptly when stop is set. |
| | `test_runner_non_idle_continuation` | Workers continue immediately without sleep on non-idle outcomes. |
| **Shutdown & Close** | `test_runner_publisher_close_order` | Publisher closes strictly after workers have finished. |
| | `test_runner_graceful_shutdown_within_grace` | In-flight dispatch finishes cleanly within grace period. |
| | `test_runner_grace_period_expiry_cancels_worker` | Workers exceeding grace period are force-cancelled. |
| | `test_runner_worker_failure_cancels_siblings_and_propagates` | Worker crash cancels siblings and propagates error. |
| | `test_runner_primary_error_preservation_over_secondary_close_failure` | Primary worker crash preserved when `publisher.close()` also fails. |
| | `test_runner_close_failure_on_normal_stop_propagates` | Close failure during normal shutdown propagates. |
| | `test_runner_outer_cancellation_propagates` | Outer cancellation cancels workers, closes publisher, and re-raises. |
| **Guardrails & Invariants** | `test_runner_dispatch_error_does_not_convert_to_retry` | Unhandled dispatch exceptions fail fast without conversion to retry. |
| | `test_runner_architectural_isolation` | Verifies zero imports of `aio_pika`, `RabbitMqEventPublisher`, or `sqlalchemy` in runner. |
| | `test_runner_no_os_signal_handlers_registered` | Verifies runner registers no OS signal handlers. |
| | `test_no_database_metadata_or_migration_changes` | Base metadata remains `{"audit_events", "outbox_events"}` and head is `0004`. |
| | `test_no_inbox_or_consumer_artifacts` | Confirms zero inbox tables, consumers, or queue declarations. |

---

## 13. Live Integration Status

```
OUTBOX_DISPATCH_RUNNER_RUNTIME_TEST = DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION
```

Live operational behavior across real PostgreSQL and RabbitMQ instances is formally deferred to **Task 1J (Runtime Integration & Topology)**. Task 1J will validate:
- Real publisher connection establishment before worker initialization.
- Concurrent multi-worker claiming across PostgreSQL rows with row-level locks.
- Real RabbitMQ publication throughput and exchange routing under concurrency.
- Broker outage recovery, network disconnect handling, and fail-fast worker supervision.
- Graceful shutdown against live running transactions.
- Claim recovery via lease expiration after worker crash or forced cancellation.

---

## 14. Changes Summary

| File | Action | Purpose |
| :--- | :---: | :--- |
| `app/events/outbox/runner.py` | **Created** | Operational runner supervising bounded workers, provider-neutral publisher lifecycle, idle polling, and graceful shutdown. |
| `tests/unit/test_outbox_runner.py` | **Created** | Comprehensive unit test suite covering runner lifecycle, concurrency, supervision, error preservation, and isolation. |
| `app/events/outbox/__init__.py` | **Modified** | Exported `OutboxDispatcherRunner`, `OutboxRunnerPolicy`, `RunnerState`, and `ManagedEventPublisher`. |
| `docs/phase-1/PHASE_1_OUTBOX_DISPATCH_RUNNER.md` | **Created** | Architectural documentation for Task 1G.3B3. |
| `docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md` | **Modified** | Updated baseline status recording Task 1G.3B3 completion. |

---

## 15. Deferred Task 1G.4 Work

Canonical Task 1G remains **`IN PROGRESS`**.

The following components belong to **Task 1G.4 (Inbox Persistence & Consumer Deduplication)** and remain strictly unstarted:
- `inbox_messages` table and Alembic migration `0005`.
- `InboxRepository` and idempotency deduplication checking.
- Consumer worker loops and RabbitMQ queue declarations / consumer bindings.
- Message acknowledgement (`basic_ack`, `basic_nack`, dead-letter queues).
