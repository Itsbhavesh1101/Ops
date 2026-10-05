# Phase 1 Task 1G.4A — Transactional Inbox & Consumer Deduplication Persistence

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1G.4A — Transactional Inbox & Consumer Deduplication Persistence  
**Status**: `COMPLETE` (Canonical Task 1G remains `IN PROGRESS`)  
**Scope**: Transactional Inbox Persistence and Consumer-Scoped Event Deduplication  

---

## 1. Purpose

Task 1G.4A implements the persistence primitive for consumer-scoped transactional event deduplication:
- Canonical PostgreSQL table: `inbox_messages`
- Declarative model: `InboxMessageRecord`
- Persistence repository: `InboxRepository`
- Database migration: `0005_create_inbox_messages.py`

The transactional Inbox is designed to solve the **at-least-once delivery duplicate processing problem** inherent in distributed message brokers like RabbitMQ.

---

## 2. The At-Least-Once Redelivery Problem

In an at-least-once messaging topology, broker message redelivery is inevitable:
1. A consumer receives a message containing a canonical `EventEnvelope`.
2. The consumer executes business state mutations and commits them to the database.
3. Before the consumer can acknowledge the message (`basic_ack`) back to the broker, a transient network disconnect occurs, the broker connection resets, or the consumer worker crashes.
4. RabbitMQ detects an unacknowledged channel closure and redelivers the message to another consumer instance.

Without deduplication, the redelivered message would cause business mutations to execute multiple times, violating domain integrity.

The transactional Inbox prevents duplicate processing by persisting an atomic deduplication marker **within the exact same database transaction** that applies the business mutation.

---

## 3. Logical Consumer Scope & Composite Key Design

Deduplication must never be global by `event_id` alone. A canonical domain event (e.g. `task.dispatched`) may legitimately be consumed and projected independently by multiple distinct services or internal projections (e.g., `workflow.projection` and `analytics.task_events`).

Therefore, deduplication is scoped by:
$$\text{Deduplication Identity} = (\text{consumer\_name}, \text{event\_id})$$

### Composite Primary Key
The table `inbox_messages` uses a composite primary key:
```sql
PRIMARY KEY (consumer_name, event_id)
```
- `consumer_name`: Bounded, provider-neutral logical consumer identifier (`VARCHAR(128)`).
- `event_id`: Canonical event UUID matching `EventEnvelope.event_id`.

No surrogate key (`inbox_id`) is used. PostgreSQL automatically creates a unique B-tree index over `(consumer_name, event_id)` backing the primary key, supporting atomic conflict detection without extra secondary indexes.

---

## 4. Consumer Name Semantics & Provider Neutrality

`consumer_name` represents the logical business consumer identity. It is provider-neutral:
- It is **not** a RabbitMQ queue name.
- It is **not** a broker `consumer_tag`.
- It is **not** a process ID, hostname, thread ID, or pod name.

### Canonical Namespaced Naming Format
A named database CheckConstraint and Python validation helper enforce canonical namespaced formatting:
- Regex: `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`
- Minimum of two lowercase dot-separated segments (e.g., `workflow.projection`, `analytics.task_events`).
- Bounded to a maximum length of 128 characters.
- Non-empty, no whitespace, no uppercase, no leading/trailing/consecutive dots.
- Open namespaced format without hardcoded consumer registries.

---

## 5. Canonical Event Identity Ownership

`InboxRepository` enforces strict identity invariants:
- **Never generates UUIDs**: Copies `envelope.event_id` unconditionally.
- **Never derives or rewrites IDs**: Preserves the authoritative UUID assigned at event creation.
- **Never uses transport identifiers**: Does not use broker delivery tags, message IDs, correlation IDs, or causation IDs for deduplication.

---

## 6. Minimal Privacy-Preserving Schema

Unlike the Transactional Outbox (which must store the complete canonical `EventEnvelope` in JSONB to later frame and publish it to the network), the Transactional Inbox exists **strictly to detect duplicates**.

Persisting full envelopes in the Inbox would:
- Duplicate event payload data already stored in business aggregates and the Outbox.
- Unnecessarily increase the database storage footprint.
- Retain potentially sensitive tenant, student, or operational data (PII) in an operational index.
- Complicate privacy, GDPR, and data retention compliance.

### Table Schema (`inbox_messages`)
| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `consumer_name` | `VARCHAR(128)` | `NOT NULL`, `PRIMARY KEY (1)` | Bounded logical consumer namespace. |
| `event_id` | `UUID` | `NOT NULL`, `PRIMARY KEY (2)` | Canonical UUID from `EventEnvelope.event_id`. |
| `recorded_at` | `TIMESTAMPTZ` | `NOT NULL`, `DEFAULT now()` | Server-authored timestamp of durable dedupe marker. |

Zero `envelope` JSONB, zero `payload`, zero `tenant_id`, zero `status` enum, and zero `error_message` columns are created.

---

## 7. Atomic Insert-on-Conflict Deduplication Semantics

To eliminate read-then-write race conditions under concurrent deliveries of the same message, `InboxRepository.try_record()` executes a single atomic SQL statement:

```sql
INSERT INTO inbox_messages (consumer_name, event_id)
VALUES (:consumer_name, :event_id)
ON CONFLICT (consumer_name, event_id) DO NOTHING
RETURNING inbox_messages.event_id;
```

### Execution Flow
1. **First Processing**:
   - No conflicting row exists for `(consumer_name, event_id)`.
   - The row is inserted; `RETURNING` produces the inserted `event_id`.
   - `try_record()` returns `True`.
2. **Duplicate Delivery**:
   - A conflicting row already exists.
   - `ON CONFLICT DO NOTHING` prevents row insertion without raising a SQL unique violation.
   - Zero rows are returned by the `RETURNING` clause.
   - `try_record()` returns `False`.
   - No exception is raised; the caller skips business mutation.

---

## 8. Same-Transaction Atomicity & Rollback Semantics

The Inbox deduplication insert and the consumer's protected business domain mutations **must share the exact same PostgreSQL transaction**:

```python
async with uow:
    inbox_repo = InboxRepository(uow.session)
    business_repo = BusinessRepository(uow.session)

    first_processing = await inbox_repo.try_record(
        consumer_name="workflow.projection",
        envelope=envelope,
    )

    if first_processing:
        await business_repo.apply_mutation(envelope)

    await uow.commit()
```

### Critical Transaction Invariants
- **Provisional `True` Result**: A `True` return from `try_record()` is strictly provisional until the surrounding `UnitOfWork` successfully commits.
- **Rollback on Business Failure**: If downstream business logic fails, the `UnitOfWork` rolls back. Because the `inbox_messages` row was inserted in the same transaction, the inbox marker is rolled back as well.
- **Subsequent Redelivery Recovery**: When RabbitMQ redelivers the event after the rollback, a subsequent attempt to call `try_record()` will once again find no conflicting row, return `True`, and retry the business logic.
- **Clean Duplicate Handling**: If the previous transaction committed successfully, `try_record()` returns `False`, allowing the consumer to acknowledge the redelivered message without re-executing the mutation.

---

## 9. Zero Transport & Scope Boundaries

Task 1G.4A is strictly limited to persistence and repository contracts:
- **No RabbitMQ Transport**: Zero connections, zero queue declarations, zero bindings, zero exchanges.
- **No Message Consumption**: Zero consumer loops, zero task runners, zero event listeners.
- **No Acknowledgements**: Zero `basic_ack`, `basic_nack`, or `basic_reject` calls.
- **No Status Machine**: Zero `PROCESSING`, `PROCESSED`, or `FAILED` states.
- **No Retention Policies**: Retention duration is `TO_BE_CONFIRMED`; zero auto-deletion jobs.
- **No Partitioning**: Table is unpartitioned.

---

## 10. Live PostgreSQL Verification Boundary

```
POSTGRES_INBOX_DEDUPE_RUNTIME_TEST = DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION
```

Live database validation across real PostgreSQL instances is formally deferred to **Task 1J (Runtime Integration & Topology)**. Task 1J will verify:
- Atomic row insertion and `RETURNING` behavior.
- Conflict suppression via `ON CONFLICT DO NOTHING`.
- Multi-worker concurrent insertion race convergence on a single committed winner.
- Independent deduplication across different consumer names for the same `event_id`.
- Atomic rollback of provisional inbox markers upon business mutation failure.
- Successful re-processing after transaction rollback upon simulated broker redelivery.

---

## 11. Migration & Baseline Status

- **Migration**: `app/db/migrations/versions/0005_create_inbox_messages.py`
  - `revision = "0005"`
  - `down_revision = "0004"`
  - Alembic head: `"0005"`
- **Table Registry**: `Base.metadata.tables.keys()` contains exactly:
  `{"audit_events", "outbox_events", "inbox_messages"}`

---

## 12. Changes Summary

| File Path | Action | Description |
| :--- | :---: | :--- |
| `app/events/inbox/models.py` | **Created** | Declarative `InboxMessageRecord` model with composite PK `(consumer_name, event_id)` and CheckConstraint. |
| `app/events/inbox/repository.py` | **Created** | `InboxRepository` providing atomic `try_record` using PostgreSQL `INSERT ... ON CONFLICT DO NOTHING RETURNING`. |
| `app/events/inbox/__init__.py` | **Modified** | Exported `InboxMessageRecord`, `InboxRepository`, and `validate_consumer_name`. |
| `app/db/models/__init__.py` | **Modified** | Registered `InboxMessageRecord` in canonical model registry. |
| `app/db/migrations/versions/0005_create_inbox_messages.py` | **Created** | Alembic migration 0005 creating `inbox_messages` table and constraints. |
| `tests/unit/test_inbox_model.py` | **Created** | Unit test suite for `InboxMessageRecord` schema, columns, constraints, and privacy boundaries. |
| `tests/unit/test_inbox_repository.py` | **Created** | Unit test suite for `InboxRepository`, consumer validation, dedupe semantics, and same-UoW contract. |
| `tests/unit/test_inbox_migration.py` | **Created** | Unit test suite for migration 0005, revision chain, and AST verification. |
| `tests/unit/test_database.py` | **Modified** | Updated table assertion to include `inbox_messages`. |
| `tests/unit/test_alembic.py` | **Modified** | Updated Alembic head assertion to `0005` and verified revision `0005`. |
| `tests/unit/test_rabbitmq_publisher.py` | **Modified** | Updated table assertion to include `inbox_messages`. |
| `tests/unit/test_outbox_model.py` | **Modified** | Updated table assertion to include `inbox_messages`. |
| `tests/unit/test_outbox_dispatch_model.py` | **Modified** | Updated table assertion to include `inbox_messages`. |
| `tests/unit/test_outbox_migration.py` | **Modified** | Updated Alembic head assertion to `0005`. |
| `tests/unit/test_outbox_dispatch_migration.py` | **Modified** | Updated Alembic head assertion to `0005`. |
| `tests/unit/test_audit_migration.py` | **Modified** | Updated Alembic head tuple to include `0005`. |
| `tests/unit/test_event_envelope.py` | **Modified** | Updated table and Alembic head assertions to include `inbox_messages` and `0005`. |
| `tests/unit/test_outbox_runner.py` | **Modified** | Updated table and Alembic head assertions to include `inbox_messages` and `0005`. |
| `docs/phase-1/PHASE_1_TRANSACTIONAL_INBOX.md` | **Created** | Architectural documentation for Task 1G.4A. |
| `docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md` | **Modified** | Updated Task 1G status recording Task 1G.4A completion. |

---

## 13. Deferred Increments

Canonical Task 1G remains **`IN PROGRESS`**.

- **Task 1G.4B**: Provider-neutral consumer processing orchestration, transaction management, handler invocation, and ack-decision pipeline.
- **Task 1G.4C**: RabbitMQ consumer transport, queue/exchange topology and bindings, message acknowledgements (`basic_ack`, `basic_nack`, dead-letter queues), and bounded consumer runner.
