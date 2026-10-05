# Phase 1 Task 1H.3 — Provider-Neutral Idempotent Mutation Execution Orchestration

**Status**: Task 1H Complete (Tasks 1H.1, 1H.2, 1H.3, 1H.4 Frozen) / Phase 1 IN PROGRESS  
**Scope**: Provider-neutral idempotent mutation execution orchestration, single-UnitOfWork coordination, provisional acquisition semantics, conflict handling, and transactional Outbox compatibility  
**Phase**: Phase 1 — Foundation  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)  
**Parent Tasks**:
- Task 1H.1: [PHASE_1_IDEMPOTENCY_CONTRACTS.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENCY_CONTRACTS.md)
- Task 1H.2: [PHASE_1_IDEMPOTENCY_PERSISTENCE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENCY_PERSISTENCE.md)

---

## 1. Purpose and Overview

Task 1H.3 connects the transport-neutral idempotency contracts established in Task 1H.1 (`IdempotencyIdentity`, `RequestFingerprint`) and the PostgreSQL marker persistence established in Task 1H.2 (`IdempotencyRepository`) with protected business mutations.

The orchestration layer is implemented in [IdempotentMutationExecutor](file:///f:/operations-engine/app/core/idempotency/executor.py).

The core invariant of this architecture is **Single-Transaction Atomicity**:
$$\text{Idempotency Marker} + \text{Business Database Mutations} + \text{Transactional Outbox Staging (Optional)} \subseteq \text{ONE PostgreSQL UnitOfWork}$$

If the business transaction fails, all transactional effects roll back together. There is no second transaction around business execution, no separate reservation transaction, and no pre-committed state.

---

## 2. Core Contracts & Protocols

### 2.1 The Protected Handler Protocol
The business mutation logic is supplied via the [IdempotentMutationHandler](file:///f:/operations-engine/app/core/idempotency/executor.py) asynchronous protocol:

```python
@runtime_checkable
class IdempotentMutationHandler(Protocol[T_co]):
    async def execute(
        self,
        *,
        session: AsyncSession,
    ) -> T_co:
        ...
```

#### Handler Transaction Ownership Restrictions
- The handler receives the exact same `AsyncSession` managed by the enclosing `UnitOfWork`.
- The handler **MUST NOT** call `session.commit()`, `session.rollback()`, or `session.close()`.
- Transaction boundaries are owned strictly by [IdempotentMutationExecutor](file:///f:/operations-engine/app/core/idempotency/executor.py) and the caller-provided `UnitOfWork`.

#### Prohibition on Direct Irreversible External Side Effects
- The protected handler **MUST NOT** perform irreversible external side effects directly, including:
  - Outgoing HTTP webhooks or external API calls
  - Direct message broker publications (e.g. RabbitMQ / `aio_pika`)
  - Outbound email notifications
  - Calendar provider mutations
- **Rationale**: PostgreSQL rollback cannot undo external network calls.
- **Allowed inside handler**:
  - PostgreSQL business mutations using the supplied `AsyncSession`.
  - Transactional Outbox staging via `OutboxRepository(session)` using the exact same session.
- Irreversible side effects are dispatched durably and asynchronously post-commit via the Transactional Outbox architecture (Task 1G).

### 2.2 Execution Outcome Enum
The provider-neutral outcome classification contains strictly two outcomes:

```python
class IdempotentExecutionOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
```

No transport or HTTP terms (`ACK`, `HTTP_200`, `HTTP_409`, `REPLAYED_RESPONSE`) exist in this layer.

### 2.3 Immutable Execution Result
The result contract is represented by the generic, frozen dataclass [IdempotentExecutionResult[T]](file:///f:/operations-engine/app/core/idempotency/executor.py):

```python
@dataclass(frozen=True, slots=True)
class IdempotentExecutionResult(Generic[T]):
    outcome: IdempotentExecutionOutcome
    identity: IdempotencyIdentity
    request_fingerprint: RequestFingerprint
    value: T | None = None
```

- When `outcome == EXECUTED`, `value` retains the exact in-memory return value of the business handler.
- When `outcome == ALREADY_PROCESSED`, `value` is strictly `None`.
- Contains zero raw client keys, HTTP responses, status codes, headers, or raw request bodies.

#### Explicit Absence of Prior Result Replay
Task 1H.2 intentionally does not store business results or HTTP response payloads. Therefore:
- `ALREADY_PROCESSED` indicates that the matching mutation was previously committed and must not execute again.
- It does **NOT** mean that a previous application or HTTP response is replayed.
- The executor does not fabricate or cache previous responses.

### 2.4 Provider-Neutral Conflict Exception
When a caller reuses an idempotency identity with a differing request fingerprint, the executor raises [IdempotencyConflictError](file:///f:/operations-engine/app/core/idempotency/executor.py):

```python
class IdempotencyConflictError(Exception):
    identity: IdempotencyIdentity
    request_fingerprint: RequestFingerprint
    existing_fingerprint: RequestFingerprint
```

- Safely exposes only validated identifiers and digests (`identity`, `request_fingerprint`, `existing_fingerprint`).
- Excludes raw client keys, request bodies, credentials, and HTTP status codes.
- API presentation (e.g., HTTP 409 Conflict) is handled upstream at the API adapter layer (Task 1I).

---

## 3. Execution Flows & State Transitions

The execution lifecycle coordinates one `UnitOfWork` per `execute()` call:

```
                          execute(identity, request_fingerprint)
                                            |
                                            v
                                Validate Input Types
                                (Strict Domain Check)
                                            |
                                            v
                                  Enter ONE UnitOfWork
                                            |
                                            v
                      IdempotencyRepository.acquire_or_observe(...)
                                            |
                    +-----------------------+-----------------------+
                    |                                               |
                    v                                               v
                 ACQUIRED                                     EXISTING_MATCH
                    |                                               |
         Invoke Handler(session)                              Skip Handler
                    |                                               |
              Await Handler                                   await uow.commit()
                    |                                               |
             await uow.commit()                               Exit UnitOfWork
                    |                                               |
             Exit UnitOfWork                              return ALREADY_PROCESSED
                    |                                         (value is None)
             return EXECUTED
          (value = handler_result)                                  |
                                                                    v
                                                            EXISTING_CONFLICT
                                                                    |
                                                              Skip Handler
                                                                    |
                                                              await uow.commit()
                                                                    |
                                                              Exit UnitOfWork
                                                                    |
                                                       raise IdempotencyConflictError
                                                        (strictly outside UoW context)
```

### 3.1 ACQUIRED Flow
1. Input types are strictly validated (`IdempotencyIdentity`, `RequestFingerprint`).
2. Exactly one `UnitOfWork` is entered.
3. `IdempotencyRepository(session).acquire_or_observe(...)` attempts insert-first acquisition.
4. On `ACQUIRED`, the marker is provisionally staged in PostgreSQL.
5. The executor invokes `handler.execute(session=session)` using the exact same session.
6. The handler return value is retained in memory.
7. `await uow.commit()` is called, committing both marker and business mutations together.
8. The `UnitOfWork` context exits cleanly.
9. `IdempotentExecutionResult(outcome=EXECUTED, value=handler_value)` is returned strictly after UoW exit.

#### ACQUIRED Provisional Semantics
Receiving `ACQUIRED` is **provisional** and does not authorize returning success. Successful execution requires:
$$\text{Handler Success} + \text{UnitOfWork Commit Success} + \text{UnitOfWork Context Exit}$$

### 3.2 EXISTING_MATCH Flow
1. `acquire_or_observe` detects an existing committed record whose `existing_fingerprint == request_fingerprint`.
2. The business handler is **not** called (execution count = 0).
3. The database transaction completes normally via `await uow.commit()`.
4. The `UnitOfWork` context exits cleanly.
5. `IdempotentExecutionResult(outcome=ALREADY_PROCESSED, value=None)` is returned strictly after UoW exit.

### 3.3 EXISTING_CONFLICT Flow
1. `acquire_or_observe` detects an existing committed record whose `existing_fingerprint != request_fingerprint`.
2. The business handler is **not** called (execution count = 0).
3. The database read transaction completes normally via `await uow.commit()`.
4. The `UnitOfWork` context exits cleanly.
5. [IdempotencyConflictError](file:///f:/operations-engine/app/core/idempotency/executor.py) is raised strictly outside the UnitOfWork context.

---

## 4. Failure, Rollback & Error Semantics

| Scenario | Behavior | Commit Called? | Handler Retried? | Result |
| :--- | :--- | :---: | :---: | :--- |
| **Handler Raises Business Error** | Original exception propagates; UoW rolls back marker + business mutation | No | No | Exception propagates; zero success result |
| **Commit Fails (After Handler)** | Persistence exception propagates; UoW cleans up session | Yes (raised) | No | Persistence exception propagates |
| **Commit Fails (EXISTING_MATCH)** | Persistence exception propagates | Yes (raised) | No | Persistence exception propagates |
| **Commit Fails (EXISTING_CONFLICT)** | Persistence exception propagates; does NOT raise conflict error | Yes (raised) | No | Persistence exception propagates |
| **Cancellation (`CancelledError`)** | Propagates immediately; UoW rolls back | No | No | `asyncio.CancelledError` propagates |
| **Unexpected TypeError / ValueError** | Propagates directly; no catch-all masking | No | No | Exception propagates |

---

## 5. Architectural Isolation & Boundaries

The implementation strictly maintains domain isolation:
- **No HTTP Imports**: Zero `fastapi`, `starlette`, `Request`, `Response`, `HTTPException`, or header-parsing logic.
- **No Messaging Imports**: Zero `aio_pika`, `RabbitMqEventPublisher`, or broker connection code.
- **No Key Hashing or Fingerprint Recalculation**: The executor accepts pre-calculated canonical `IdempotencyIdentity` and `RequestFingerprint` contracts. It never calls `digest_idempotency_key()` or `fingerprint_bytes()`.
- **No Response Caching or Storage**: No response cache tables, serialized response columns, or TTL/expiration fields.

---

## 6. Architecture Closure & Generic Response-Replay Decision (Task 1H.4)

Task 1H.4 finalized the architecture closure for the Task 1H foundation:

### 6.1 Final Decision: Generic Response Replay Excluded by Design
The generic E-7 idempotency foundation **does NOT** persist or replay complete HTTP responses.
- **Excluded by Design**: No byte-for-byte response body storage, no HTTP status storage, no response header caching, and no serialized response models.
- **Rationale**: Persisting generic HTTP responses would severely violate domain purity by coupling low-level transactional idempotency to:
  1. HTTP serialization formats and content-type negotiations.
  2. Transport-level headers and status code conventions.
  3. Endpoint-specific response schema evolution.
  4. Response privacy, sensitive PII retention, and regulatory compliance.
  5. Cache retention policies.
- Idempotency exists to guarantee **safe mutation execution**, not to operate as an HTTP response cache.

### 6.2 Duplicate Semantics Frozen
The outcome `IdempotentExecutionOutcome.ALREADY_PROCESSED` strictly guarantees:
- The matching canonical `IdempotencyIdentity` and `RequestFingerprint` were previously committed.
- The protected business mutation is skipped and will not execute again.
- It does **NOT** mean the framework possesses or replays the prior application or HTTP response.

### 6.3 Lost-Response Recovery Delegated to Operation / API Design
- Recovering from lost responses during client-side network disconnects is an operation and API design responsibility (Task 1I).
- Specific endpoints may return stable canonical resource identifiers, stable asynchronous job identifiers, or provide query endpoints for resulting entities.
- Task 1H does not mandate a universal mechanism or speculative schema.

### 6.4 No Speculative Result Reference
- The `idempotency_records` table intentionally contains **no** `result_reference` column.
- If a future concrete domain operation demonstrates a durable reference requirement that cannot be satisfied by canonical domain state, it must be introduced explicitly through an independent data-model and migration review with defined ownership, privacy, and retention.

### 6.5 Four Conceptual Layers of Task 1H
The complete, frozen Task 1H architecture is structured into four distinct layers:
1. **Contracts** ([`app/core/idempotency/contracts.py`](file:///f:/operations-engine/app/core/idempotency/contracts.py)): `OperationName`, `IdempotencyKeyDigest`, `IdempotencyIdentity`, `RequestFingerprint`.
2. **Persistence** ([`app/core/idempotency/models.py`](file:///f:/operations-engine/app/core/idempotency/models.py), [`app/core/idempotency/repository.py`](file:///f:/operations-engine/app/core/idempotency/repository.py)): `idempotency_records` table, INSERT-first acquisition, post-conflict fingerprint observation.
3. **Execution Orchestration** ([`app/core/idempotency/executor.py`](file:///f:/operations-engine/app/core/idempotency/executor.py)): `IdempotentMutationExecutor`, single `UnitOfWork`, provisional `ACQUIRED`, `EXISTING_MATCH`, `EXISTING_CONFLICT`, transactional Outbox compatibility.
4. **Deferred Transport Policy**: HTTP header parsing, API error representations, and status code presentation deferred strictly to Task 1I.

### 6.6 Retention Policy Status
`IDEMPOTENCY_RETENTION_POLICY = TO_BE_CONFIRMED`. No background cleanup jobs or TTL expiration columns exist in Task 1H.

---

## 7. Live PostgreSQL Integration Boundary

```python
IDEMPOTENT_EXECUTION_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
```

Unit tests prove ordering, mocks, session sharing, protocol conformance, and exception propagation. True live PostgreSQL atomicity and concurrent serialization will be tested against Dockerized PostgreSQL in Task 1J:
1. Atomicity: Marker and business mutation commit together or roll back together.
2. Outbox Atomicity: Marker, business mutation, and outbox record commit atomically in one transaction.
3. Concurrency: Competing transactions for the same key serialize via PostgreSQL row-level locks on primary key `(tenant_id, operation_name, key_digest)`.
4. Stale/Rollback Re-acquisition: An aborted transaction allows a subsequent transaction to acquire the key.

---

## 8. Summary of Files Changed

| File Path | Action | Description |
| :--- | :---: | :--- |
| [app/core/idempotency/executor.py](file:///f:/operations-engine/app/core/idempotency/executor.py) | **Created** | Provider-neutral execution orchestration, handler protocol, result dataclass, outcome enum, conflict exception, and executor class. |
| [app/core/idempotency/__init__.py](file:///f:/operations-engine/app/core/idempotency/__init__.py) | **Modified** | Exported executor types in the package interface. |
| [tests/unit/test_idempotency_executor.py](file:///f:/operations-engine/tests/unit/test_idempotency_executor.py) | **Created** | 35 comprehensive unit tests covering all execution paths, timelines, session identity, Outbox compatibility, failure modes, and isolation. |
| [docs/phase-1/PHASE_1_IDEMPOTENT_EXECUTION.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENT_EXECUTION.md) | **Created** | Technical documentation for Task 1H.3 execution orchestration and Task 1H.4 architecture closure. |
| [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) | **Modified** | Updated foundation baseline recording Task 1H completion and frozen architecture status. |

---

## 9. Verification Results

- `python -m compileall app`: **PASS**
- `python -m ruff check .`: **PASS**
- `python -m mypy app`: **PASS** (101 source files)
- `python -m pytest tests/unit/test_idempotency_executor.py -v`: **35 passed**
- Alembic head: **0006**
- Canonical tables: **4 tables** (`audit_events`, `outbox_events`, `inbox_messages`, `idempotency_records`)
- Status: `TASK_1H_STATUS = COMPLETE`, `TASK_1H_ARCHITECTURE_STATUS = FROZEN`, `TASK_1I_STATUS = UNSTARTED`
