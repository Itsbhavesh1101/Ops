# Phase 1 Task 1I.4A — Provider-Neutral Asynchronous Job Contracts & Lifecycle

**Status**: Task 1I.4A Complete / Task 1I.4 IN PROGRESS / Task 1I IN PROGRESS / Phase 1 IN PROGRESS  
**Scope**: Provider-neutral asynchronous job identity (`JobId`, `JobType`, `JobIdentity`), canonical lifecycle status (`JobStatus`), deterministic transition validation (`can_transition_job_status`, `validate_job_status_transition`), failure metadata (`JobFailureCode`, `JobFailure`), and architectural isolation  
**Phase**: Phase 1 — Foundation  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)  
**Parent Tasks**:
- Task 1H: [PHASE_1_IDEMPOTENCY_CONTRACTS.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENCY_CONTRACTS.md)
- Task 1I.1: [PHASE_1_API_ERROR_CONTRACT.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_API_ERROR_CONTRACT.md)
- Task 1I.2: [PHASE_1_API_V1_COMPOSITION.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_API_V1_COMPOSITION.md)
- Task 1I.3: [PHASE_1_HTTP_IDEMPOTENCY.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_HTTP_IDEMPOTENCY.md)

> [!NOTE]
> This document records the technical architecture, invariants, and boundaries of the Task 1I.4A asynchronous job foundation contracts. This is an engineering specification and foundation document, not an Architectural Decision Record (ADR).

---

## 1. Purpose & Why Asynchronous Jobs Exist

In the IR Infotech E-7 Operations Engine, operations may span multiple duration classes:
1. **Synchronous mutations**: Executed within standard request/response cycles (e.g. creating records, simple state transitions) protected by idempotency markers.
2. **Long-running operations**: Work that exceeds reasonable HTTP gateway/client timeouts (e.g. complex Task Intelligence schedule generation, optimization solvers, workflow deployments, and bulk data synchronizations).

To decouple client request threads from long-running execution without introducing framework-specific lock-in, Task 1I.4 establishes a layered asynchronous job foundation:
- **Task 1I.4A (This Increment)**: Provider-neutral contracts, identity models, status definitions, and deterministic lifecycle policies.
- **Task 1I.4B (Deferred)**: PostgreSQL persistence schema, transaction management, and state machine updates.
- **Task 1I.4C (Deferred)**: HTTP read, status polling, and API representations conforming to the frozen contract.

Defining provider-neutral contracts first guarantees that database schemas and HTTP endpoints conform to an immutable, reviewed state machine rather than defining it accidentally.

---

## 2. Job Identifier Contract (JobId)

The job identifier is encapsulated as an immutable value object in [app/core/jobs/contracts.py](file:///f:/operations-engine/app/core/jobs/contracts.py):

```python
class JobId(BaseModel):
    value: uuid.UUID
```

### 2.1 Generation & Invariants
- **Generation Policy**: Server-controlled via `generate_job_id() -> JobId` using Python's standard library `uuid.uuid4()`.
- **UUID Version 4**: Strictly enforces `uuid.UUID.version == 4`. Alternate versions (UUID1, UUID3, UUID5) and nil UUIDs (`00000000-0000-0000-0000-000000000000`) are rejected.
- **Canonical Serialization**: Standard lowercase hyphenated string representation (`xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`).
- **No Non-Standard IDs**: Rejects integer auto-increments, timestamps, hashes, or client-derived values.

---

## 3. Job Type Contract (JobType)

`JobType` defines the logical long-running operation category:

```python
class JobType(BaseModel):
    value: str
```

### 3.1 Open Namespaced Validation
- **Regex Pattern**: `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`
- **Invariants**:
  - Strictly lowercase; no silent lowercasing of invalid inputs.
  - Length bounded to `<= 128` characters.
  - At least two dot-separated segments (e.g. `planning.generate`, `schedule.solve`, `workflow.deploy`, `integration.sync`).
  - No leading, trailing, or consecutive dots; no whitespace or control characters.
- **Extensibility**: Defined as an open value object rather than a closed enum, enabling future domain modules (Task Intelligence, Workflow, Scheduling) to define their own job types without modifying the core foundation.

---

## 4. Job Identity Contract (JobIdentity)

`JobIdentity` uniquely identifies a tenant-scoped asynchronous job:

```python
class JobIdentity(BaseModel):
    job_id: JobId
    tenant_id: str
    job_type: JobType
```

### 4.1 Invariants & Security
- **Fields Exactly Three**: Contains `job_id`, `tenant_id`, and `job_type`. Extra fields are forbidden (`extra="forbid"`).
- **Tenant Scoping**: Jobs are strictly owned by an authenticated tenant. The `tenant_id` must be explicitly provided from trusted server-side context; never derived from client headers (such as `X-Tenant-ID`), query parameters, or request payloads.
- **Principal Exclusion**: Excludes user IDs, email addresses, or principal roles. A job belongs to the tenant organization; authorization is evaluated independently.
- **State Exclusion**: Contains zero lifecycle status, failure data, timestamps, payload bodies, or transport headers.

---

## 5. Job Status & Deterministic Lifecycle Policy

Job statuses are defined in [app/core/jobs/lifecycle.py](file:///f:/operations-engine/app/core/jobs/lifecycle.py) as a canonical `StrEnum`:

```python
class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
```

### 5.1 Status Semantics
- **PENDING**: Job record exists, but protected long-running execution has not commenced.
- **RUNNING**: Long-running execution is actively in progress.
- **SUCCEEDED**: Execution reached successful terminal completion.
- **FAILED**: Execution reached terminal failure.

### 5.2 Terminal States
- Exactly `SUCCEEDED` and `FAILED` are terminal (`status.is_terminal == True`).
- Terminal states are immutable: once reached, a job cannot transition to any other status.
- `PENDING` and `RUNNING` are non-terminal (`status.is_terminal == False`).

### 5.3 Lifecycle Transition Graph

```
           +-----------+
           |  PENDING  |
           +-----+-----+
                 |
        +--------+--------+
        |                 |
        v                 v
  +-----------+     +-----------+
  |  RUNNING  |     |  FAILED   | (Terminal)
  +-----+-----+     +-----------+
        |                 ^
        +--------+--------+
                 |
                 v
          +-------------+
          |  SUCCEEDED  | (Terminal)
          +-------------+
```

### 5.4 Allowed Transitions
Frozen in `ALLOWED_TRANSITIONS`:
1. `PENDING -> RUNNING`: Worker starts processing the queued job.
2. `PENDING -> FAILED`: Job fails before active execution begins (e.g. pre-execution validation failure, missing prerequisite, unrecoverable setup error).
3. `RUNNING -> SUCCEEDED`: Active execution completes successfully.
4. `RUNNING -> FAILED`: Active execution fails with a terminal error.

### 5.5 Forbidden Transitions
- `PENDING -> SUCCEEDED`: Jobs must enter active execution (`RUNNING`) before succeeding.
- `RUNNING -> PENDING`: Jobs cannot revert to pending once execution begins.
- `SUCCEEDED -> *`: Outbound transitions from terminal success are prohibited.
- `FAILED -> *`: Outbound transitions from terminal failure are prohibited.
- `SAME -> SAME`: Transition predicates (`can_transition_job_status`) return `False` for identical states.

### 5.6 Transition Validation
- `can_transition_job_status(current, target) -> bool`: Pure predicate returning boolean validity.
- `validate_job_status_transition(current, target) -> None`: Raises `InvalidJobStatusTransitionError(current=..., target=...)` on invalid transitions.
- `InvalidJobStatusTransitionError`: Exposes current and target statuses safely; contains zero tenant data, payload contents, or HTTP/database artifacts.

---

## 6. Job Failure Contract (JobFailureCode & JobFailure)

Failure metadata is encapsulated without coupling to HTTP errors or exception tracebacks:

### 6.1 JobFailureCode
- Open namespaced lowercase identifier matching `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`.
- Bounded to `<= 128` characters.
- Examples: `job.execution_failed`, `schedule.infeasible`, `integration.provider_unavailable`.

### 6.2 JobFailure
```python
class JobFailure(BaseModel):
    code: JobFailureCode
    message: str
```
- **Sanitized Message**: Non-empty string bounded to `<= 1024` characters.
- **Prohibition on Automatic Exception Serialization**: `JobFailure` does **not** provide `from_exception(exc)` or automatically call `str(exc)`/`repr(exc)`. Callers must supply sanitized, presentation-safe messages.
- **Zero Leakage**: Contains zero stack traces, SQL errors, internal payload dumps, or provider credentials.

---

## 7. Deferred & Absent Speculative Capabilities

In accordance with Phase-1 discipline, the following concepts are intentionally omitted from Task 1I.4A:
- **No Result Payloads**: Zero `result`, `result_json`, `result_reference`, `response_body`, `response_headers`, or `resource_url`.
- **No Progress Fields**: Zero `percentage`, `progress_message`, `current_step`, or `total_steps`.
- **No Retry Contracts**: Zero `retry_count`, `max_retries`, `next_retry_at`, `backoff`, or `RETRYING` status.
- **No Cancellation Contracts**: Zero `CANCELLED`, `cancel_requested`, or cancellation tokens.
- **No Priority Fields**: Zero `priority` or `queue_priority`.
- **No Execution Infrastructure**: Zero workers, runners, broker queues, or thread pools.
- **No Persistence**: Zero SQLAlchemy models, tables, or Alembic migrations.
- **No HTTP Layer**: Zero FastAPI routes, dependencies, or request/response schemas.

---

## 8. Architectural Isolation

[app/core/jobs/](file:///f:/operations-engine/app/core/jobs/) maintains complete isolation:
- **Zero HTTP imports**: No `fastapi`, `starlette`, `Request`, `Response`, or `HTTPException`.
- **Zero Database imports**: No `sqlalchemy`, `AsyncSession`, `Alembic`, or `app.db`.
- **Zero Messaging imports**: No `aio_pika`, `RabbitMQ`, or `app.events`.
- **Zero Domain imports**: Completely decoupled from Task Intelligence, Scheduling, and Workflow domains.

---

## 9. Files Created and Modified

### Created
- [app/core/jobs/__init__.py](file:///f:/operations-engine/app/core/jobs/__init__.py) — Package exports for contracts and lifecycle.
- [app/core/jobs/contracts.py](file:///f:/operations-engine/app/core/jobs/contracts.py) — `JobId`, `JobType`, `JobIdentity`, `JobFailureCode`, `JobFailure`.
- [app/core/jobs/lifecycle.py](file:///f:/operations-engine/app/core/jobs/lifecycle.py) — `JobStatus`, transition graph, validation functions, and error.
- [tests/unit/test_job_contracts.py](file:///f:/operations-engine/tests/unit/test_job_contracts.py) — 110 unit tests verifying contracts, transitions, and isolation.
- [docs/phase-1/PHASE_1_JOB_CONTRACTS.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_JOB_CONTRACTS.md) — Technical specification.

### Modified
- [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) — Foundation baseline update.

---

## 10. Next Steps in Task 1I.4 Sequence

- **Task 1I.4A**: Provider-Neutral Job Contracts & Lifecycle (`COMPLETE`).
- **Task 1I.4B**: PostgreSQL Job Persistence & State Transitions (`UNSTARTED`).
- **Task 1I.4C**: HTTP Job Read/Polling API Foundation (`UNSTARTED`).
- **Task 1I**: /api/v1 Conventions + OpenAPI + Error Model + Jobs (`IN PROGRESS`).
