# Phase 1 Task 1G.4B — Provider-Neutral Consumer Transaction Orchestration

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1G.4B — Provider-Neutral Consumer Transaction Orchestration  
**Status**: `COMPLETE` (Canonical Task 1G remains `IN PROGRESS`)  
**Scope**: Provider-Neutral Consumer Transaction Orchestration, Deduplication Coordination, and Business Handler Invocation  

---

## 1. Purpose & Overview

Task 1G.4B implements the provider-neutral consumer transaction orchestration layer bridging the frozen transactional Inbox persistence (`InboxRepository` from Task 1G.4A) with business domain handlers (`ConsumerEventHandler`).

The orchestrator guarantees:
1. **First-Delivery Atomic Mutation**: The Inbox deduplication marker (`inbox_messages`) and the business state mutation execute within the **exact same database transaction** using a single `UnitOfWork` / `AsyncSession`.
2. **Duplicate Skipping**: If the deduplication marker already exists (`try_record == False`), the business handler is bypassed, and the transaction is cleanly completed.
3. **Provisional Deduplication**: The `try_record == True` outcome is provisional. If the business handler fails or transaction commit fails, both the business mutation and the Inbox record roll back together.
4. **Clean Transaction Boundary**: A successful processing result (`PROCESSED` or `DUPLICATE`) is returned **strictly after** the database transaction has committed and the `UnitOfWork` context has exited.
5. **Provider Neutrality**: Zero knowledge of message brokers (RabbitMQ, `aio_pika`), AMQP framing, queue topologies, delivery tags, or broker acknowledgements (`basic_ack`, `basic_nack`, `basic_reject`).

---

## 2. Validated EventEnvelope Input

The consumer processor accepts only a pre-validated canonical `EventEnvelope` instance:
- `process(envelope: EventEnvelope) -> ConsumerProcessingResult`
- Accepts zero raw dictionaries, byte streams, strings, or transport message objects.
- Transport decoding, JSON deserialization, and schema validation are the responsibility of the transport consumer layer (deferred to Task 1G.4C).
- Identity invariant: The incoming `envelope` is passed unconditionally to both `InboxRepository.try_record` and `handler.handle`. No new UUIDs or timestamps are authored by the processor.

---

## 3. Logical Consumer Binding & Construction Invariants

Each `ConsumerEventProcessor` instance is statically bound to a single logical consumer:
```python
processor = ConsumerEventProcessor(
    consumer_name="workflow.projection",
    uow_factory=uow_factory,
    handler=handler,
)
```

### Invariants:
1. **Validated Consumer Identity**: `consumer_name` is validated at construction time using the frozen `validate_consumer_name` function from Task 1G.4A. Invalid consumer names fail fast with `ValueError` before any database connection or `UnitOfWork` is created.
2. **Zero Derivation from Transport**: The consumer name is never inferred from RabbitMQ queues, consumer tags, hostnames, container IDs, or event payload fields.
3. **Zero I/O on Construction**: Initializing `ConsumerEventProcessor` performs zero database queries, network calls, or broker operations.

---

## 4. Business Handler Contract (`ConsumerEventHandler`)

Business domain logic interacts with the consumer layer via a minimal, provider-neutral protocol:

```python
@runtime_checkable
class ConsumerEventHandler(Protocol):
    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Handle business event within the supplied transactional session."""
        ...
```

### Handler Contract Rules:
- **Exact Session Sharing**: The handler receives the transactional `AsyncSession` owned by the processor's `UnitOfWork`. Business repositories instantiated by the handler execute queries within the same transaction.
- **Session Lifecycle Non-Ownership**: The handler **must not** call `session.commit()`, `session.rollback()`, or `session.close()`. The processor and `UnitOfWork` maintain exclusive ownership of transaction boundaries.
- **Zero Transport Objects**: The handler receives no `aio_pika.IncomingMessage`, AMQP delivery tags, queue names, or raw JSON bytes.
- **Return Value**: The handler returns `None`. Business return values do not drive broker disposition.

---

## 5. Exact Same-Session Invariant

Atomicity between the Inbox deduplication marker and the business mutation depends entirely on session sharing:
$$\text{InboxRepository.session} \equiv \text{handler\_session} \equiv \text{uow.session}$$

```python
async with self._uow_factory() as uow:
    session = uow.session
    inbox_repo = InboxRepository(session)
    ...
    await self._handler.handle(envelope=envelope, session=session)
```

If separate sessions or nested transactions were used, a failure in the handler after the Inbox insert could leave a permanent dedupe record without applying the business state change, causing permanent data loss on redelivery. Session identity (`is`) is enforced and verified by unit tests.

---

## 6. One-UoW Transaction Architecture

Each invocation of `processor.process(envelope)` creates **exactly one** `UnitOfWork` instance:
- No separate "dedupe transaction".
- No separate "business transaction".
- No separate "completion transaction".
- No nested transaction wrappers (`ConsumerUnitOfWork`, `InboxUnitOfWork`).

Reusing the frozen `UnitOfWork` abstraction preserves repository conventions and avoids bespoke transaction managers.

---

## 7. First-Delivery Transaction Flow (`PROCESSED`)

When an event arrives for the first time for a given logical consumer (`try_record == True`):

```
uow_enter
   ↓
inbox_repo.try_record() -> True (provisional insert)
   ↓
handler.handle(envelope, session) (business mutation)
   ↓
uow.commit() (atomic commit of inbox marker + business mutation)
   ↓
uow_exit
   ↓
return ConsumerProcessingResult(outcome=PROCESSED, ...)
```

1. Enter `UnitOfWork` context.
2. Call `inbox_repo.try_record(consumer_name, envelope)`.
3. Receive `True` (first delivery).
4. Invoke `handler.handle(envelope=envelope, session=session)` before commit.
5. Await handler completion.
6. Commit `UnitOfWork`.
7. Exit `UnitOfWork` context.
8. Return `ConsumerProcessingResult` with outcome `ConsumerProcessingOutcome.PROCESSED`.

`process()` returns **strictly after** `uow_exit`. The `UnitOfWork` is never active when the caller receives the result.

---

## 8. Duplicate Delivery Flow (`DUPLICATE`)

When a previously recorded event is redelivered to the same logical consumer (`try_record == False`):

```
uow_enter
   ↓
inbox_repo.try_record() -> False (duplicate detected via ON CONFLICT DO NOTHING)
   ↓
[Handler is NOT invoked]
   ↓
uow.commit() (cleanly completes database transaction boundary)
   ↓
uow_exit
   ↓
return ConsumerProcessingResult(outcome=DUPLICATE, ...)
```

1. Enter `UnitOfWork` context.
2. Call `inbox_repo.try_record(consumer_name, envelope)`.
3. Receive `False` (conflict detected; zero rows inserted).
4. Bypass handler invocation (`handler.call_count == 0`).
5. Complete the transaction via `uow.commit()`.
6. Exit `UnitOfWork` context.
7. Return `ConsumerProcessingResult` with outcome `ConsumerProcessingOutcome.DUPLICATE`.

Completing the transaction cleanly ensures the database connection is returned to the pool in a clean state and satisfies the invariant that duplicate determination successfully finished.

---

## 9. Provisional Inbox Marker & Failure Semantics

### 9.1 Provisional Deduplication
`try_record() == True` inserts a row into `inbox_messages` under PostgreSQL transaction isolation. This row is **provisional** until the transaction commits. It is visible only to the current transaction.

### 9.2 Handler Failure Rollback
If the business handler raises an unhandled exception:
1. Execution halts before `uow.commit()`.
2. The `async with uow` block catches the exception and executes `uow.rollback()`.
3. Both the business mutation and the provisional `inbox_messages` row roll back atomically.
4. The exception propagates out of `process()`.
5. No `ConsumerProcessingResult` is returned.
6. The event is **not** permanently marked as processed. When RabbitMQ redelivers the message, `try_record()` will succeed again.

### 9.3 Commit Failure
If `uow.commit()` fails (e.g. database serialization failure, connection drop):
1. `uow` exits with error; transaction is rolled back.
2. The exception propagates out of `process()`.
3. Neither `PROCESSED` nor `DUPLICATE` is returned.
4. Transport cannot generate an ACK for an uncommitted transaction.

### 9.4 Duplicate Commit Failure
If duplicate determination occurs but `uow.commit()` fails, the persistence error propagates and `DUPLICATE` is not returned.

---

## 10. Cancellation & Unexpected Error Propagation

- **Asyncio Cancellation**: `asyncio.CancelledError` is never caught or suppressed. If cancellation occurs during `try_record`, handler execution, or commit, the task cancels, the UoW rolls back, and `CancelledError` propagates.
- **Unexpected Exceptions**: `TypeError`, `ValueError`, and unexpected runtime errors are never caught or converted to `PROCESSED` or `DUPLICATE`. Infrastructure and programming bugs propagate immediately to fail fast.

---

## 11. Processing Result Contract & Outcome Semantics

### 11.1 Immutable Result Model
```python
class ConsumerProcessingResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ConsumerProcessingOutcome
    consumer_name: str
    event_id: UUID
```

### 11.2 Outcome Enum
```python
class ConsumerProcessingOutcome(StrEnum):
    PROCESSED = "PROCESSED"
    DUPLICATE = "DUPLICATE"
```

### 11.3 ACK Eligibility
Both `PROCESSED` and `DUPLICATE` are successful consumer outcomes:
- `PROCESSED`: New event successfully handled and committed.
- `DUPLICATE`: Redelivered event previously committed; safe to discard.
- In Task 1G.4C, the transport layer will map both `PROCESSED` and `DUPLICATE` to broker message acknowledgements (`basic_ack`).
- Unhandled exceptions escaping `process()` will be mapped by the transport layer to nack/requeue or dead-letter handling.

---

## 12. Architectural Boundaries & Provider Neutrality

| Dimension | ConsumerEventProcessor Boundary |
| :--- | :--- |
| **Broker Transport** | Zero RabbitMQ, `aio_pika`, or AMQP imports. |
| **AMQP Operations** | Zero `ack`, `nack`, `reject`, or `requeue` calls. |
| **Topology** | Zero queue declarations, exchange bindings, or routing keys. |
| **Lifecycle** | Zero worker loops, background polling, or signal handlers. |
| **Payload Decoding** | Validated `EventEnvelope` accepted directly; zero JSON decoding. |
| **Error Hierarchy** | Zero consumer error classification (`RetryableConsumerError`, etc.) in 1G.4B. |

---

## 13. Files Created & Modified

### Created:
- `app/events/inbox/processor.py`: Implementation of `ConsumerProcessingOutcome`, `ConsumerProcessingResult`, `ConsumerEventHandler`, and `ConsumerEventProcessor`.
- `tests/unit/test_inbox_processor.py`: Comprehensive test suite (33 unit tests) verifying ordering, atomicity, session sharing, error propagation, immutability, and architectural isolation.
- `docs/phase-1/PHASE_1_CONSUMER_PROCESSING_ORCHESTRATION.md`: This architecture specification.

### Modified:
- `app/events/inbox/__init__.py`: Exported processor types (`ConsumerEventProcessor`, `ConsumerEventHandler`, `ConsumerProcessingOutcome`, `ConsumerProcessingResult`).
- `docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md`: Recorded Task 1G.4B completion.

---

## 14. Live Integration Status & Deferral

```python
CONSUMER_PROCESSING_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
```

Unit tests use mock sessions and controllable fakes to verify orchestration logic and sequence. Full end-to-end multi-process verification with a running PostgreSQL engine is deferred to Task 1J, where it will verify:
- Atomic database commit of `inbox_messages` and business entities.
- Transactional rollback on business handler failure.
- Concurrent duplicate delivery serialization via PostgreSQL unique index.
- Multi-consumer independent processing of the same `event_id`.

---

## 15. Deferred Task 1G.4C Work

Task 1G.4B intentionally stops at the provider-neutral orchestration boundary. The following components are deferred to Task 1G.4C:
- RabbitMQ consumer transport (`aio_pika` message consumer).
- AMQP message framing, correlation header extraction, and JSON deserialization into `EventEnvelope`.
- Acknowledgement mapping: `PROCESSED` / `DUPLICATE` $\to$ `message.ack()`.
- Error handling & disposition: exceptions $\to$ `message.nack(requeue=...)` or dead-letter routing.
- Consumer runner lifecycle, connection management, channel prefetch (`qos`), and graceful shutdown.
