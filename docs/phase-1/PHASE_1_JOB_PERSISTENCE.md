# Phase 1 Task 1I.4B — PostgreSQL Job Persistence & Atomic State Transitions

**Status**: Task 1I.4B Complete / Task 1I.4 IN PROGRESS / Phase 1 IN PROGRESS  
**Scope**: Canonical PostgreSQL `jobs` table persistence, provider-neutral `JobSnapshot`, tenant-scoped operations, and atomic compare-and-transition lifecycle state machine  
**Phase**: Phase 1 — Foundation  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)

---

## 1. Purpose and Overview

Task 1I.4B establishes the PostgreSQL persistence layer and atomic transition semantics for the frozen asynchronous job contracts defined in Task 1I.4A.

Building directly upon the provider-neutral models ([JobId](file:///f:/operations-engine/app/core/jobs/contracts.py), [JobType](file:///f:/operations-engine/app/core/jobs/contracts.py), [JobIdentity](file:///f:/operations-engine/app/core/jobs/contracts.py), [JobStatus](file:///f:/operations-engine/app/core/jobs/lifecycle.py), [JobFailure](file:///f:/operations-engine/app/core/jobs/contracts.py), and [validate_job_status_transition](file:///f:/operations-engine/app/core/jobs/lifecycle.py)), Task 1I.4B introduces:
1. The canonical PostgreSQL [jobs](file:///f:/operations-engine/app/core/jobs/models.py) table.
2. The immutable, provider-neutral [JobSnapshot](file:///f:/operations-engine/app/core/jobs/repository.py) representation.
3. The [JobRepository](file:///f:/operations-engine/app/core/jobs/repository.py) providing tenant-scoped creation, retrieval, and compare-and-transition status updates.
4. Alembic migration `0007_create_jobs.py` advancing the database migration chain from `0006` to `0007`.

The database is the single authoritative source of truth for persisted asynchronous job lifecycle state. No execution workers, queue dispatchers, or HTTP endpoints exist in this increment.

---

## 2. Canonical `jobs` Database Table

The `jobs` table is the 5th canonical foundation table registered in `Base.metadata` alongside `audit_events`, `outbox_events`, `inbox_messages`, and `idempotency_records`.

```
+-----------------------------------------------------------------------------------+
| jobs                                                                              |
|                                                                                   |
|   job_id: UUID NOT NULL                              <- PRIMARY KEY               |
|   tenant_id: VARCHAR(128) NOT NULL                   <- TENANT SCOPE              |
|   job_type: VARCHAR(128) NOT NULL                    <- OPEN NAMESPACED TYPE      |
|   status: VARCHAR(32) NOT NULL DEFAULT 'pending'     <- 4 CANONICAL STATUSES      |
|   failure_code: VARCHAR(128) NULL                    <- NAMESPACED ERROR CODE     |
|   failure_message: VARCHAR(1024) NULL                <- SANITIZED ERROR MESSAGE   |
|   created_at: TIMESTAMPTZ NOT NULL DEFAULT now()     <- SERVER-AUTHORED TIMESTAMP |
|   started_at: TIMESTAMPTZ NULL                       <- SERVER-AUTHORED TIMESTAMP |
|   completed_at: TIMESTAMPTZ NULL                     <- SERVER-AUTHORED TIMESTAMP |
+-----------------------------------------------------------------------------------+
```

### Table Definition & Column Specifications:

| Column | Type | Nullable | Default | Invariants & Constraints |
| :--- | :--- | :--- | :--- | :--- |
| `job_id` | `UUID` (as_uuid=True) | `False` | *None* | Primary key (`pk_jobs`). UUIDv4 format, non-nil. Globally unique. |
| `tenant_id` | `VARCHAR(128)` | `False` | *None* | Immutable tenant scope. Must not be empty or whitespace-only (`ck_jobs_tenant_id_nonblank`). |
| `job_type` | `VARCHAR(128)` | `False` | *None* | Open namespaced lowercase string matching `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$` (`ck_jobs_job_type`). |
| `status` | `VARCHAR(32)` | `False` | `'pending'` | String storage. Constrained strictly to `('pending', 'running', 'succeeded', 'failed')` (`ck_jobs_status`). Zero PostgreSQL enum. |
| `failure_code` | `VARCHAR(128)` | `True` | *None* | Nullable namespaced lowercase error code matching `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$` (`ck_jobs_failure_code_format`). |
| `failure_message` | `VARCHAR(1024)` | `True` | *None* | Nullable sanitized human-readable failure summary. Never contains raw tracebacks or SQL strings. |
| `created_at` | `TIMESTAMPTZ` | `False` | `now()` | Authoritative server creation timestamp. Never generated via host clock. Immutable after insertion. |
| `started_at` | `TIMESTAMPTZ` | `True` | *None* | Authoritative execution start timestamp. Populated only when job enters `running` status. |
| `completed_at` | `TIMESTAMPTZ` | `True` | *None* | Authoritative termination timestamp. Populated when job reaches `succeeded` or `failed`. |

---

## 3. UUID Primary Key & Identity Immutability

1. **UUID Primary Key**: The canonical `JobId` is stored directly as a PostgreSQL `UUID` primary key (`pk_jobs`).
2. **Zero Surrogate ID**: No integer surrogate ID (`id`), second UUID (`record_id`), or autoincrement sequence is used.
3. **Identity Immutability**:
   - `job_id`, `tenant_id`, `job_type`, and `created_at` constitute immutable creation metadata.
   - The repository exposes no method capable of updating any of these four fields.
   - No generic ORM `update(job, **fields)` method exists.

---

## 4. Tenant Scoping & Isolation

Tenant isolation is explicit and mandatory in the persistence layer:
- `tenant_id` is persisted on every row and indexed via primary key lookup.
- `JobRepository.get(*, tenant_id: str, job_id: JobId)` requires both `tenant_id` and `job_id`.
- `JobRepository.transition(*, tenant_id: str, job_id: JobId, ...)` requires both `tenant_id` and `job_id` in the conditional `UPDATE` predicate.
- Querying by `job_id` alone without `tenant_id` is forbidden and impossible through the repository contract.

---

## 5. Status Persistence & Database CHECK Constraints

Status is persisted as standard `VARCHAR(32)` strings rather than a PostgreSQL `ENUM` type. This avoids migration locking overhead and schema rigidity while preserving strict consistency through named database CHECK constraints:

1. **`ck_jobs_status`**: Enforces status values strictly within `('pending', 'running', 'succeeded', 'failed')`.
2. **`ck_jobs_job_type`**: Enforces open namespaced format matching `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`.
3. **`ck_jobs_tenant_id_nonblank`**: Enforces `length(trim(tenant_id)) > 0`.
4. **`ck_jobs_failure_code_format`**: Enforces failure code formatting when non-null.
5. **`ck_jobs_failure_consistency`**: Enforces that `failure_code` and `failure_message` are both `NOT NULL` when `status = 'failed'`, and both `NULL` when `status != 'failed'`.
6. **`ck_jobs_timestamp_state_consistency`**:
   - `pending`: `started_at IS NULL AND completed_at IS NULL`
   - `running`: `started_at IS NOT NULL AND completed_at IS NULL`
   - `succeeded`: `started_at IS NOT NULL AND completed_at IS NOT NULL`
   - `failed`: `completed_at IS NOT NULL` (supports pre-execution failure where `started_at IS NULL`, or execution failure where `started_at IS NOT NULL`).
7. **`ck_jobs_timestamp_ordering`**: Enforces `started_at >= created_at`, `completed_at >= created_at`, and `completed_at >= started_at` (when `started_at` is non-null).

---

## 6. Provider-Neutral `JobSnapshot`

The persistence layer exposes an immutable Pydantic model, [JobSnapshot](file:///f:/operations-engine/app/core/jobs/repository.py):

```python
class JobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: JobIdentity
    status: JobStatus
    failure: JobFailure | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
```

### Snapshot Invariants:
- **Timezone Awareness**: All persisted snapshot timestamps must be timezone-aware according to Python datetime semantics: tzinfo must be present and utcoffset() must not be None. Naive/effectively-naive values are rejected and are never silently normalized.
- **Status State Invariants**: Identical to database constraints; validated on model instantiation.
- **Timestamp Ordering Invariants**: Validated in Python before instantiation.
- **Zero Framework Leakage**: Does not expose SQLAlchemy model objects, HTTP types, request references, or result payloads.

---

## 7. Job Creation Semantics

Job creation is handled by `JobRepository.create(*, identity: JobIdentity)`:
- **Caller-Supplied JobId**: The repository does NOT generate the `JobId`. It receives an already-instantiated `JobIdentity` from the caller. This enables deterministic idempotency orchestration and multi-entity UnitOfWork transactions.
- **Always PENDING**: Creation hardcodes `status = JobStatus.PENDING`. No initial status argument is accepted.
- **Server-Authored Timestamp**: `created_at` is authored by PostgreSQL via `DEFAULT now()`.
- **Atomic Insertion**: Executes `INSERT INTO jobs (...) VALUES (...) RETURNING ...`.
- **No Transaction Control**: Does not call `commit()`, `rollback()`, or `close()`.

---

## 8. Atomic Status Transitions (Compare-and-Transition)

### Critical Requirement: Zero SELECT-before-UPDATE

Status transitions must NEVER execute `SELECT status` followed by `UPDATE status`. Doing so introduces severe concurrency races where two workers read `PENDING` simultaneously and both transition the row.

### Canonical Algorithm:

```
                  Caller invokes transition(expected, target)
                                     │
                 [1] Static lifecycle policy validation
                     (validate_job_status_transition)
                                     │ (valid)
                 [2] Failure argument validation
                     (target == FAILED <=> failure present)
                                     │ (valid)
                 [3] Atomic conditional UPDATE:
                     UPDATE jobs SET ...
                     WHERE job_id = :jid
                       AND tenant_id = :tid
                       AND status = :expected_status
                     RETURNING ...
                                     │
                     ┌───────────────┴───────────────┐
                 [Row returned]              [Zero rows returned]
                     │                               │
             Return JobSnapshot              [4] Targeted SELECT:
                                                 SELECT status FROM jobs
                                                 WHERE job_id = :jid
                                                   AND tenant_id = :tid
                                                     │
                                     ┌───────────────┴───────────────┐
                                 [Row missing]               [Row present]
                                     │                               │
                             Raise JobNotFoundError      Raise JobStatusConflictError
```

### Transition Timestamp Rules:
- **`PENDING -> RUNNING`**: `started_at = func.now()`, `completed_at = NULL`, `failure = NULL`.
- **`PENDING -> FAILED`** (pre-execution failure): `started_at` remains `NULL`, `completed_at = func.now()`, failure fields populated.
- **`RUNNING -> SUCCEEDED`**: `started_at` preserved (not overwritten), `completed_at = func.now()`, `failure = NULL`.
- **`RUNNING -> FAILED`**: `started_at` preserved, `completed_at = func.now()`, failure fields populated.

### Conditional Miss Handling:
- Only AFTER conditional UPDATE returns zero rows does the repository execute a single targeted `SELECT status` query.
- If the row is not found: raises `JobNotFoundError(tenant_id, job_id)`.
- If the row is found with actual status differing from `expected_status`: raises `JobStatusConflictError(job_id, expected_status, actual_status)`.
- Zero automatic retries: conflict is deterministic. Execution infrastructure may determine retry strategy.

---

## 9. Transaction & UnitOfWork Ownership

Adheres strictly to the foundation architecture:
- Receives an externally supplied `AsyncSession` bound to caller-owned `SqlAlchemyUnitOfWork`.
- Never creates or owns an engine.
- Never creates an independent session.
- Never calls `session.commit()`, `session.rollback()`, or `session.close()`.
- Never opens nested transactions.
- All operations participate in the surrounding business transaction.

---

## 10. Deliberately Excluded Machinery (Frozen Scope)

The following capabilities are deliberately absent from Task 1I.4B:
- **Zero HTTP Endpoints**: No `/api/v1/jobs` routes or polling APIs (deferred to Task 1I.4C).
- **Zero Result Payloads**: No `result`, `result_json`, `resource_id`, or `response_body` columns.
- **Zero Progress Fields**: No `percentage`, `step`, or progress message columns.
- **Zero Retry Machinery**: No `attempt_count`, `retry_count`, or `next_retry_at` columns.
- **Zero Cancellation**: No `cancelled_at` or `cancel_requested` columns.
- **Zero Worker Claiming**: No `claim_token`, `lease_until`, `worker_id`, or `heartbeat_at` columns.
- **Zero Broker Queue**: No RabbitMQ, Celery, or broker dispatch logic.
- **Zero Delete Operations**: Jobs are durable audit records; no deletion API is exposed.

---

## 11. Alembic Migration `0007_create_jobs.py`

- **Revision**: `0007`
- **Revises**: `0006`
- **Upgrade**: Creates table `jobs` with UUID primary key, 9 canonical columns, and 7 named check constraints.
- **Downgrade**: Drops table `jobs`.
- **Head**: Advances Alembic head from `0006` to `0007`.

---

## 12. Verification & Testing Boundary

### Static & Mock Unit Tests (Task 1I.4B):
- Verified via unit test suites (`test_job_model.py`, `test_job_repository.py`, `test_job_migration.py`).
- 1031 passing unit tests across the entire repository.
- Zero mock tests are claimed as live PostgreSQL verification.

### Authoritative Runtime Verification Deferral:
```python
POSTGRES_JOB_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
```
Task 1J runtime integration will verify against live PostgreSQL:
- Migration `0007` upgrade and downgrade execution.
- Native UUID storage and constraints.
- Database server-authored timestamps (`now()`).
- Database CHECK constraint enforcement and violation rejection.
- Concurrency isolation: competing conditional transitions cannot both succeed.
- Rollback semantics under active UnitOfWork failure.

---

## 13. Files Changed in Task 1I.4B

### Created:
- `app/core/jobs/models.py`: SQLAlchemy Declarative model [JobModel](file:///f:/operations-engine/app/core/jobs/models.py).
- `app/core/jobs/repository.py`: Provider-neutral [JobSnapshot](file:///f:/operations-engine/app/core/jobs/repository.py), [JobRepository](file:///f:/operations-engine/app/core/jobs/repository.py), [JobNotFoundError](file:///f:/operations-engine/app/core/jobs/repository.py), and [JobStatusConflictError](file:///f:/operations-engine/app/core/jobs/repository.py).
- `app/db/migrations/versions/0007_create_jobs.py`: Alembic migration `0007`.
- `tests/unit/test_job_model.py`: Schema, column, and constraint unit tests.
- `tests/unit/test_job_repository.py`: Repository lifecycle, atomic transition, and conditional miss unit tests.
- `tests/unit/test_job_migration.py`: Migration revision chain and AST operation unit tests.
- `docs/phase-1/PHASE_1_JOB_PERSISTENCE.md`: This documentation document.

### Modified:
- `app/core/jobs/__init__.py`: Re-exports `JobModel`, `JobSnapshot`, `JobRepository`, `JobNotFoundError`, `JobStatusConflictError`, `POSTGRES_JOB_RUNTIME_TEST`.
- `app/db/models/__init__.py`: Registers `JobModel` in canonical foundation models.
- `tests/unit/test_alembic.py`: Updated Alembic head assertion to `0007`.
- `tests/unit/test_api_errors.py`: Updated schema stability assertions to 5 tables and head `0007`.
- `tests/unit/test_api_idempotency.py`: Updated schema stability assertions to 5 tables and head `0007`.
- `tests/unit/test_api_v1_composition.py`: Updated schema stability assertions to 5 tables and head `0007`.
- `tests/unit/test_audit_migration.py`: Allowed head `0007`.
- `tests/unit/test_database.py`: Added `jobs` to allowed tables.
- `tests/unit/test_event_envelope.py`: Updated schema stability to 5 tables and head `0007`.
- `tests/unit/test_idempotency_contracts.py`: Updated schema stability to 5 tables and head `0007`.
- `tests/unit/test_idempotency_executor.py`: Updated schema stability to 5 tables and head `0007`.
- `tests/unit/test_idempotency_migration.py`: Allowed head `0007`.
- `tests/unit/test_idempotency_model.py`: Updated canonical tables set to include `jobs`.
- `tests/unit/test_inbox_migration.py`: Allowed head `0007`.
- `tests/unit/test_inbox_model.py`: Updated canonical tables set to include `jobs`.
- `tests/unit/test_inbox_processor.py`: Allowed head `0007`.
- `tests/unit/test_job_contracts.py`: Updated schema stability assertions to 5 tables and head `0007`.
- `tests/unit/test_outbox_dispatch_migration.py`: Allowed head `0007`.
- `tests/unit/test_outbox_dispatch_model.py`: Updated canonical tables set to include `jobs`.
- `tests/unit/test_outbox_migration.py`: Allowed head `0007`.
- `tests/unit/test_outbox_model.py`: Updated canonical tables set to include `jobs`.
- `tests/unit/test_outbox_runner.py`: Allowed head `0007`.
- `tests/unit/test_rabbitmq_consumer_message.py`: Allowed head `0007`.
- `tests/unit/test_rabbitmq_consumer_runner.py`: Allowed head `0007`.
- `tests/unit/test_rabbitmq_publisher.py`: Updated canonical tables set to include `jobs`.
- `docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md`: Updated baseline recording Task 1I.4B completion.
