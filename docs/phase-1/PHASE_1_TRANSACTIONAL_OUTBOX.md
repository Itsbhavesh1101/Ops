# Phase 1 Task 1G.2 — Transactional Outbox Persistence Foundation

## 1. Executive Summary & Purpose

Task 1G.2 implements the **Transactional Outbox Persistence Foundation** for the E-7 Operations Engine.
The transactional outbox eliminates the classic distributed dual-write failure mode:
- **Failure Mode A**: Business state commits to PostgreSQL, but network I/O or broker unavailability causes event publication to fail, leaving downstream consumers permanently unaware of the state transition.
- **Failure Mode B**: An event is published to RabbitMQ, but the subsequent database transaction fails or rolls back, leading downstream consumers to act on phantom state that does not exist in the primary datastore.

Under the E-7 architecture, domain mutations, audit logs (if required), and outbox event staging are committed **atomically** within a single PostgreSQL transaction managed by a [`UnitOfWork`](file:///f:/operations-engine/app/db/uow.py) / `AsyncSession`.
Only after the database transaction successfully commits does an asynchronous dispatcher publish the staged events to the message broker.

Task 1G.2 establishes **durable staging persistence only**. Broker connectivity, dispatcher loops, and message consumption are deferred.

---

## 2. Authority & Source-of-Truth Boundary

- **PostgreSQL is Canonical**: PostgreSQL is the authoritative source of truth for whether an event was created by E-7.
- **RabbitMQ is Transport Only**: RabbitMQ (introduced in Task 1G.3) will serve solely as a transient message transport and delivery mechanism. It is never the canonical system of record.
- **Event Contract Preservation**: The outbox stores the canonical [`EventEnvelope`](file:///f:/operations-engine/app/events/schemas.py) contract established in Task 1G.1 / 1G.1A. Zero secondary or competing event schemas are introduced.

---

## 3. Canonical Table: `outbox_events`

The persistence entity is implemented via [`OutboxEventRecord`](file:///f:/operations-engine/app/events/outbox/models.py) inheriting from canonical [`Base`](file:///f:/operations-engine/app/db/base.py) and registered in [`app.db.models`](file:///f:/operations-engine/app/db/models/__init__.py).

### 3.1 Schema Specification

| Column Name | SQL Data Type | Nullable | Default | Description |
| :--- | :--- | :---: | :---: | :--- |
| `event_id` | `UUID` | **NO** | *None* | **Primary Key**. Copied directly from canonical `EventEnvelope.event_id`. Never regenerated. |
| `envelope` | `JSONB` | **NO** | *None* | Complete canonical `EventEnvelope` JSON representation. Immutable after insertion. |
| `created_at` | `TIMESTAMPTZ` | **NO** | `now()` | Database/server-authored timestamp recording when the outbox row was staged. |
| `published_at` | `TIMESTAMPTZ` | YES | `NULL` | Completion marker. `NULL` means pending; populated with broker confirmation timestamp. |

### 3.2 Primary Key & Zero Redundant Identifiers
- The outbox table uses `event_id` (UUID) directly as the primary key.
- There is a strict 1:1 relationship between an `EventEnvelope` and an `outbox_events` row.
- Zero surrogate identifiers (`outbox_id`, `id`) are introduced.

### 3.3 Complete Envelope JSONB Storage
- `envelope` stores the entire canonical `EventEnvelope` produced via `envelope.model_dump(mode="json")`.
- Contains: `event_id`, `event_type`, `event_version`, `occurred_at`, `tenant_id`, `correlation_id`, `causation_id`, `subject_type`, `subject_id`, `payload`.
- Does not store raw Python object references, pickle dumps, or broker wire bytes.
- The stored JSON structure supports direct reconstruction via `EventEnvelope.model_validate(record.envelope)`.

### 3.4 Timestamp Semantics: `created_at` vs `occurred_at`
- `EventEnvelope.occurred_at`: Application-authored UTC datetime recording when the domain event occurred. Stored inside the JSONB `envelope`.
- `outbox_events.created_at`: Database-authored `TIMESTAMPTZ` (`server_default=func.now()`) recording when the outbox row was persisted into PostgreSQL.
- Callers cannot set or backdate `created_at`.

### 3.5 Publication State: `published_at`
- `published_at IS NULL`: Staged event is pending publication.
- `published_at IS NOT NULL`: Staged event has been confirmed published to the message broker.
- No separate status enum or flag exists, eliminating inconsistent states (e.g. `status = PUBLISHED` with `published_at = NULL`).

---

## 4. Constraints & Database-Level Immutability

### 4.1 Consistency Constraints
1. **`ck_outbox_events_event_id_matches_envelope`**:
   `COALESCE(envelope ->> 'event_id' = event_id::text, FALSE)`
   Guarantees that the relational primary key strictly matches the event identity inside the JSON payload.
   > [!IMPORTANT]
   > **PostgreSQL CHECK Constraint NULL-Safety (Task 1G.2A)**:
   > In PostgreSQL, a `CHECK` constraint evaluates expressions where rows pass if the result is `TRUE` or `NULL` (`UNKNOWN`).
   > A naive equality `envelope ->> 'event_id' = event_id::text` would evaluate to `NULL` if `event_id` is missing from the JSON envelope or set to JSON `null`, allowing malformed records to satisfy the constraint.
   > By wrapping the expression in `COALESCE(..., FALSE)`, any missing or null `event_id` evaluates to `FALSE` and is strictly rejected by the database.
2. **`ck_outbox_events_envelope_is_object`**:
   `jsonb_typeof(envelope) = 'object'`
   Guarantees that the root JSONB node is a valid JSON object.

### 4.2 Partial Dispatcher Index
- **`ix_outbox_events_pending_created_at`**:
  `CREATE INDEX ix_outbox_events_pending_created_at ON outbox_events (created_at) WHERE published_at IS NULL;`
  Enables high-efficiency polling of pending events in chronological order without scanning previously published rows.
- No speculative GIN indexes or relational column projections are added.

### 4.3 Database Immutability Trigger
To prevent application bugs or rogue operations from altering staged events, PostgreSQL migration `0003_create_outbox_events` installs a row-level `BEFORE UPDATE` trigger:
- **Function**: `fn_protect_outbox_event_content()`
- **Trigger**: `trg_outbox_events_protect_content`
- **Enforcement Rule**:
  ```sql
  IF NEW.event_id IS DISTINCT FROM OLD.event_id OR
     NEW.envelope IS DISTINCT FROM OLD.envelope OR
     NEW.created_at IS DISTINCT FROM OLD.created_at THEN
      RAISE EXCEPTION 'outbox_events immutable content cannot be updated'
          USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
  ```
- Protects `event_id`, `envelope`, and `created_at` from modification.
- Explicitly permits operational updates to `published_at` (required for Task 1G.3).
- **Trigger Safety**: The exception string is completely static; zero payload content, row values, or credentials are leaked in error messages.

### 4.4 Immutability Claim Boundary
Canonical outbox event content is protected from normal application `UPDATE` mutation by the repository contract and PostgreSQL trigger. This is **operational immutability**.
It does **not** claim:
- Cryptographic hash chaining or Merkle proofs.
- Tamper-proofing against privileged database administrators with `SUPERUSER` permissions.
- Permanent data retention (outbox is operational staging, not compliance audit).

---

## 5. Repository & Same-UoW Transaction Ownership

The staging API is provided by [`OutboxRepository`](file:///f:/operations-engine/app/events/outbox/repository.py):

```python
class OutboxRepository(SqlAlchemyRepository):
    async def enqueue(self, envelope: EventEnvelope) -> UUID:
        ...
```

### 5.1 Same-UoW Atomicity Contract
```python
async with uow:
    # 1. Mutate business state
    # 2. Append audit event (if applicable)
    audit_repo = AuditRepository(uow.session)
    await audit_repo.append(audit_event)

    # 3. Stage outbox event in the SAME transaction
    outbox_repo = OutboxRepository(uow.session)
    staged_event_id = await outbox_repo.enqueue(envelope)

    # 4. Atomic single commit
    await uow.commit()
```
- Both [`AuditRepository`](file:///f:/operations-engine/app/audit/repository.py) and [`OutboxRepository`](file:///f:/operations-engine/app/events/outbox/repository.py) accept the same transactional `AsyncSession` from [`UnitOfWork`](file:///f:/operations-engine/app/db/uow.py).
- If `uow.commit()` succeeds, both the audit trail and outbox staging are durably stored.
- If an exception occurs, `uow` automatically rolls back all changes, preventing phantom event dispatch.

### 5.2 Strict Repository Boundaries
- [`OutboxRepository.enqueue`](file:///f:/operations-engine/app/events/outbox/repository.py#L22-L61) calls `self.session.add(record)`.
- It **never** calls `session.flush()`, `session.commit()`, `session.rollback()`, or `session.close()`.
- It **never** generates a new UUID (`event_id` is copied from `envelope.event_id`).
- It **never** performs network I/O, invokes webhooks, or interacts with RabbitMQ.
- Returns the canonical `UUID` (`envelope.event_id`), preventing mutable ORM entity leakage to domain callers.

---

## 6. Migration Baseline: `0003_create_outbox_events`

Alembic migration [`0003_create_outbox_events.py`](file:///f:/operations-engine/app/db/migrations/versions/0003_create_outbox_events.py) advances the persistence baseline:
- **`revision`**: `"0003"`
- **`down_revision`**: `"0002"`
- **Head**: Current Alembic head is `"0003"`.
- **Registered Tables**: `Base.metadata.tables.keys()` consists strictly of:
  `{"audit_events", "outbox_events"}`
- **Downgrade**: Cleanly removes the trigger, trigger function, partial index, and table in safe reverse dependency order.

---

## 7. Deferred Work & Unresolved Policies

Task 1G remains **IN PROGRESS**. The following capabilities are explicitly deferred:
- **Task 1G.3**: RabbitMQ Integration (AMQP client, publisher worker, polling loop, retry/backoff, exchange/queue topology, routing key convention, marking `published_at`).
- **Task 1G.4**: Inbox & Consumer Deduplication (PostgreSQL `inbox_messages` table, consumer worker, deduplication window, idempotent handling).
- **Outbox Retention**: Outbox row cleanup/pruning duration is classified as `TO_BE_CONFIRMED`. No automatic deletion or archiving is implemented in Task 1G.2.
- **Partitioning**: Outbox table is unpartitioned. Partitioning is deferred until tenant scale profiles are resolved.
- **Idempotency Framework**: Full idempotency key mapping is deferred to Task 1H.
- **Live PostgreSQL Integration Testing**: Documented under verification constant:
  `POSTGRES_OUTBOX_IMMUTABILITY_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"`
  Task 1J live PostgreSQL integration will eventually verify:
  - **A**. Valid matching `event_id` insert succeeds.
  - **B**. Mismatched envelope `event_id` is rejected.
  - **C**. Envelope missing `event_id` is rejected.
  - **D**. Envelope `event_id = JSON null` is rejected.
  - **E**. Non-object envelope is rejected.
  - **F**. Immutable `event_id` UPDATE rejected.
  - **G**. Immutable `envelope` UPDATE rejected.
  - **H**. Immutable `created_at` UPDATE rejected.
  - **I**. Operational `published_at` UPDATE allowed.

---

## 8. Summary of Files Created & Modified

| File Path | Action | Description |
| :--- | :---: | :--- |
| [`app/events/outbox/models.py`](file:///f:/operations-engine/app/events/outbox/models.py) | **Created** | SQLAlchemy model `OutboxEventRecord` for canonical table `outbox_events`. |
| [`app/events/outbox/repository.py`](file:///f:/operations-engine/app/events/outbox/repository.py) | **Created** | `OutboxRepository` providing `enqueue()` bound to UoW `AsyncSession`. |
| [`app/events/outbox/__init__.py`](file:///f:/operations-engine/app/events/outbox/__init__.py) | **Modified** | Exported `OutboxEventRecord` and `OutboxRepository`. |
| [`app/db/models/__init__.py`](file:///f:/operations-engine/app/db/models/__init__.py) | **Modified** | Registered `OutboxEventRecord` alongside `AuditEventRecord`. |
| [`app/db/migrations/versions/0003_create_outbox_events.py`](file:///f:/operations-engine/app/db/migrations/versions/0003_create_outbox_events.py) | **Created** | Alembic migration 0003 for `outbox_events`, checks, partial index, and trigger. |
| [`tests/unit/test_outbox_model.py`](file:///f:/operations-engine/tests/unit/test_outbox_model.py) | **Created** | Unit tests for `OutboxEventRecord`, columns, constraints, and partial index. |
| [`tests/unit/test_outbox_repository.py`](file:///f:/operations-engine/tests/unit/test_outbox_repository.py) | **Created** | Unit tests for `OutboxRepository`, session boundaries, same-UoW contract, and round-trip validation. |
| [`tests/unit/test_outbox_migration.py`](file:///f:/operations-engine/tests/unit/test_outbox_migration.py) | **Created** | Static tests for migration 0003 AST, trigger safety, downgrade, and schema consistency. |
| [`tests/unit/test_alembic.py`](file:///f:/operations-engine/tests/unit/test_alembic.py) | **Modified** | Updated migration chain assertions to reflect head `0003`. |
| [`tests/unit/test_database.py`](file:///f:/operations-engine/tests/unit/test_database.py) | **Modified** | Updated `Base.metadata` table expectation to `{"audit_events", "outbox_events"}`. |
| [`tests/unit/test_audit_migration.py`](file:///f:/operations-engine/tests/unit/test_audit_migration.py) | **Modified** | Updated revision chain assertion to permit head `0003`. |
| [`tests/unit/test_event_envelope.py`](file:///f:/operations-engine/tests/unit/test_event_envelope.py) | **Modified** | Updated metadata and head assertions for Task 1G.2 baseline. |
| [`docs/phase-1/PHASE_1_TRANSACTIONAL_OUTBOX.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_TRANSACTIONAL_OUTBOX.md) | **Created** | Architectural and persistence specification for the transactional outbox foundation. |
| [`docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md`](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) | **Modified** | Recorded Task 1G.2 completion; retained canonical Task 1G status as IN PROGRESS. |
