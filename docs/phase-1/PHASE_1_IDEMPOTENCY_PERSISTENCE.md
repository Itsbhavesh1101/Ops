# Phase 1 Task 1H.2 — Transactional Idempotency Marker Persistence & Conflict Detection

**Status**: Task 1H Complete (Tasks 1H.1, 1H.2, 1H.3, 1H.4 Frozen) / Phase 1 IN PROGRESS  
**Scope**: Transactional idempotency marker persistence, insert-first acquisition, same-key request-fingerprint conflict detection, and UnitOfWork transaction sharing  
**Phase**: Phase 1 — Foundation  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)

---

## 1. Purpose and Overview

Task 1H.2 establishes the transactional persistence and conflict detection layer for mutation idempotency in the E-7 Operations Engine.

Building upon the transport-neutral identity contracts defined in Task 1H.1, Task 1H.2 introduces the canonical PostgreSQL `idempotency_records` table and the [IdempotencyRepository](file:///f:/operations-engine/app/core/idempotency/repository.py).

The core architecture follows a **transactional idempotency-marker pattern**:
- The idempotency marker and the protected business mutation execute within the **same** PostgreSQL transaction, `AsyncSession`, and `UnitOfWork`.
- An acquisition (`ACQUIRED`) is **provisional** until the surrounding caller-owned `UnitOfWork` successfully commits.
- If the business mutation fails or an unhandled exception occurs, the transaction rolls back, automatically rolling back the idempotency marker with it. Future retries may therefore acquire the key again without manual intervention.

---

## 2. Canonical Composite Identity & Primary Key

The `idempotency_records` table uses the canonical Task 1H.1 composite identity directly as its primary key:

$$\text{Primary Key} = (\text{tenant\_id}, \text{operation\_name}, \text{key\_digest})$$

```
+-----------------------------------------------------------------------------------+
| idempotency_records                                                               |
|                                                                                   |
|   tenant_id: VARCHAR(128) NOT NULL          \                                     |
|   operation_name: VARCHAR(128) NOT NULL      +- COMPOSITE PRIMARY KEY             |
|   key_digest: VARCHAR(64) NOT NULL          /                                     |
|   request_fingerprint: VARCHAR(64) NOT NULL                                       |
|   created_at: TIMESTAMPTZ NOT NULL (server_default now())                         |
+-----------------------------------------------------------------------------------+
```

### Key Schema Decisions:
- **No Surrogate Primary Key**: Zero `idempotency_id`, `record_id`, or surrogate UUID. The composite identity is authoritative.
- **No Raw Idempotency Key**: Raw client tokens are never stored in the database. Only the 64-character lowercase hexadecimal SHA-256 `key_digest` is persisted, preserving strict privacy invariants.
- **No Principal ID**: Principal identity is excluded. Idempotency protects tenant-level business state against duplicate execution across retries. Upstream authorization independently verifies caller permissions prior to mutation execution.
- **Request Fingerprint**: Persists strictly the 64-character hexadecimal SHA-256 `request_fingerprint`. No raw request bodies, JSONB, or HTTP request objects are persisted.
- **Server-Authored Timestamp**: `created_at` is a timezone-aware `TIMESTAMPTZ` with `server_default=func.now()`. Application host clocks are never used.
- **Zero Speculative Indexes**: The composite primary key indexes `(tenant_id, operation_name, key_digest)` natively; no extra secondary indexes are added without an established query pattern.

---

## 3. Why No Pre-Committed In-Progress Reservations

A common distributed system antipattern involves reserving a key in an initial committed transaction, executing business logic in a second transaction, and completing the record in a third transaction.

This creates severe crash-recovery problems:
1. If the worker crashes after reserving the key, the key remains indefinitely locked or stuck in `IN_PROGRESS`.
2. Resolving stuck keys requires complex heartbeats, lease timeouts, owner tokens, and background sweeper jobs.

Instead, Task 1H.2 leverages **PostgreSQL transaction atomicity**:
- The marker is inserted in the *same* transaction as the business mutation.
- The marker becomes durably visible *only* when the transaction commits.
- If the transaction crashes or rolls back, the marker vanishes completely.
- Consequently, Task 1H.2 requires **no** `IN_PROGRESS`, `PROCESSING`, `COMPLETED`, or `FAILED` states, **no** `lease_token`, **no** `claimed_at`, and **no** stale-owner recovery.

---

## 4. Acquisition & Observation Algorithm (Insert-First)

The [IdempotencyRepository.acquire_or_observe](file:///f:/operations-engine/app/core/idempotency/repository.py) method enforces a strict **INSERT-first** algorithm:

```
                  Caller invokes acquire_or_observe(identity, fingerprint)
                                             |
                                             v
                      +---------------------------------------------+
                      | INSERT INTO idempotency_records             |
                      | ON CONFLICT (tenant, op, digest) DO NOTHING |
                      | RETURNING key_digest                        |
                      +---------------------------------------------+
                                             |
                          +------------------+------------------+
                          |                                     |
                    Row returned?                         No row returned?
                          |                                     |
                          v                                     v
                   return ACQUIRED                      Conflict detected!
             (zero SELECT, provisional)                         |
                                                                v
                                                +--------------------------------+
                                                | SELECT request_fingerprint     |
                                                | WHERE tenant, op, digest match |
                                                +--------------------------------+
                                                                |
                                             +------------------+------------------+
                                             |                                     |
                                  stored_fp == request_fp?              stored_fp != request_fp?
                                             |                                     |
                                             v                                     v
                                   return EXISTING_MATCH                 return EXISTING_CONFLICT
```

### Why Never SELECT-Before-INSERT:
Performing a `SELECT` before `INSERT` introduces a classic read-then-write race condition under concurrent requests. Two parallel requests could both observe "no existing record" and then race to insert. By issuing `INSERT ... ON CONFLICT DO NOTHING RETURNING` first, PostgreSQL's row-level lock and unique constraint authoritatively serialize competing transactions.

---

## 5. Decision Semantics

The [IdempotencyDecision](file:///f:/operations-engine/app/core/idempotency/repository.py) value object represents the acquisition outcome:

1. **`ACQUIRED`**:
   - Indicates this transaction provisionally inserted the idempotency marker.
   - `existing_fingerprint` is strictly `None`.
   - **Provisional Nature**: Caller must perform the protected mutation and commit the surrounding UnitOfWork.
2. **`EXISTING_MATCH`**:
   - A committed marker already exists for the identical `(tenant_id, operation_name, key_digest)` and has the identical `request_fingerprint`.
   - The caller must **not** execute the business mutation again.
   - `existing_fingerprint` is populated and equals `request_fingerprint`.
3. **`EXISTING_CONFLICT`**:
   - A committed marker already exists for the identical composite key, but with a **different** `request_fingerprint`.
   - Represents an attempt to reuse an idempotency key for differing mutation payloads.
   - The caller must reject the request. Provider-neutral HTTP status code mapping (e.g. 409 Conflict) is handled at the API layer (Task 1I).

---

## 6. Same-UnitOfWork Transaction Contract

The [IdempotencyRepository](file:///f:/operations-engine/app/core/idempotency/repository.py) is a domain-level SQLAlchemy repository that adheres to E-7 Unit of Work guidelines:
- Receives an externally managed `AsyncSession`.
- Does **not** create an engine, create a session, commit, rollback, or close.
- Executes within the caller's active transaction:

```python
async with uow:
    idem_repo = IdempotencyRepository(uow.session)
    business_repo = BusinessRepository(uow.session)

    # Both repositories share identical session reference
    assert idem_repo.session is business_repo.session is uow.session

    decision = await idem_repo.acquire_or_observe(
        identity=identity,
        request_fingerprint=fingerprint,
    )

    if decision.decision is IdempotencyDecisionType.ACQUIRED:
        await business_repo.apply_mutation(...)

    await uow.commit()
```

---

## 7. PostgreSQL Concurrent Uniqueness & Runtime Testing

Under real PostgreSQL:
- If Transaction A inserts an uncommitted marker, and Transaction B issues `INSERT ... ON CONFLICT DO NOTHING`, PostgreSQL causes Transaction B to wait until Transaction A resolves.
- If Transaction A commits, Transaction B's `ON CONFLICT` triggers, inserts nothing, and its subsequent `SELECT` reads Transaction A's committed fingerprint.
- If Transaction A rolls back, Transaction B proceeds to insert the marker and returns `ACQUIRED`.

Unit tests verify the compilation, algorithm ordering, and mock mechanics. True multi-connection concurrency and transaction isolation testing is deferred:

```python
POSTGRES_IDEMPOTENCY_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
```

---

## 8. Summary of Files Changed

| File Path | Action | Description |
| :--- | :---: | :--- |
| [app/core/idempotency/models.py](file:///f:/operations-engine/app/core/idempotency/models.py) | **Created** | Canonical mapped model [IdempotencyRecord](file:///f:/operations-engine/app/core/idempotency/models.py#L15-L75) with composite PK and check constraints. |
| [app/core/idempotency/repository.py](file:///f:/operations-engine/app/core/idempotency/repository.py) | **Created** | [IdempotencyRepository](file:///f:/operations-engine/app/core/idempotency/repository.py#L90-L195), [IdempotencyDecision](file:///f:/operations-engine/app/core/idempotency/repository.py#L46-L88), and [IdempotencyDecisionType](file:///f:/operations-engine/app/core/idempotency/repository.py#L32-L44). |
| [app/core/idempotency/__init__.py](file:///f:/operations-engine/app/core/idempotency/__init__.py) | **Modified** | Exported persistence model, repository, and decision types. |
| [app/db/models/__init__.py](file:///f:/operations-engine/app/db/models/__init__.py) | **Modified** | Registered `IdempotencyRecord` with `Base.metadata`. |
| [app/db/migrations/versions/0006_create_idempotency_records.py](file:///f:/operations-engine/app/db/migrations/versions/0006_create_idempotency_records.py) | **Created** | Alembic migration 0006 creating `idempotency_records` table and check constraints. |
| [tests/unit/test_idempotency_model.py](file:///f:/operations-engine/tests/unit/test_idempotency_model.py) | **Created** | Unit tests for table mapping, columns, composite PK, check constraints, and negative assertions. |
| [tests/unit/test_idempotency_repository.py](file:///f:/operations-engine/tests/unit/test_idempotency_repository.py) | **Created** | Unit tests for insert-first algorithm, ACQUIRED, EXISTING_MATCH, EXISTING_CONFLICT, SQL compilation, UoW sharing, and rollback semantics. |
| [tests/unit/test_idempotency_migration.py](file:///f:/operations-engine/tests/unit/test_idempotency_migration.py) | **Created** | Unit tests for migration 0006 revision chain, AST operations, and deferred constant. |
| [docs/phase-1/PHASE_1_IDEMPOTENCY_PERSISTENCE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENCY_PERSISTENCE.md) | **Created** | Technical documentation for Task 1H.2 persistence and conflict detection. |
| [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) | **Modified** | Updated foundation baseline recording Task 1H.2 completion with Task 1H IN PROGRESS. |

---

## 9. Response-Replay Decision & Architectural Closure

Task 1H.4 finalized the architectural question regarding generic HTTP response replay storage:

### Decision: Generic HTTP Response Replay is Excluded by Design
- **No Response Payload Storage**: The `idempotency_records` table intentionally contains **zero** `response_body`, `response_json`, `response_headers`, `http_status`, `content_type`, `serialized_result`, or `result_reference` columns.
- **Rationale**: Generic response persistence couples domain idempotency to transport framing, content-type serialization, HTTP status conventions, endpoint schema evolution, and PII/data retention risks. Idempotency guarantees safe mutation execution, not HTTP response caching.
- **Lost-Response Recovery**: Delegated to operation/API design (Task 1I). Concrete endpoints may return stable resource/job identifiers, or provide query endpoints for resulting entities.
- **No Speculative Result Reference**: Future requirements for durable result references must be introduced with concrete use cases, explicit ownership, privacy, retention, and migration review.
- **Retention Unresolved**: Retention policy remains `IDEMPOTENCY_RETENTION_POLICY = TO_BE_CONFIRMED`. No cleanup worker or TTL exists at this stage.

---

## 10. Verification Results

All required repository-wide verification gates were executed directly from the repository root (`F:\operations-engine`):

| Verification Command | Scope / Target | Result | Status |
| :--- | :--- | :--- | :---: |
| `python -m compileall app` | Bytecode compilation across all Python files in `app/` | Clean (all modules compiled, 0 errors) | **PASS** |
| `python -m ruff check .` | Repository-wide linter and import checker | Clean (0 errors, all checks passed) | **PASS** |
| `python -m mypy app` | Static type analysis across `app/` | Clean (100 source files, 0 issues) | **PASS** |
| `python -m pytest --collect-only -q` | Repository-wide test discovery | 696 tests collected across all test suites | **PASS** |
| `python -m pytest -q` | Repository-wide test suite execution | 696 passed in 15.52s (0 failures, 0 skipped) | **PASS** |

### Status Constants
- `TASK_1H_2_IMPLEMENTATION_STATUS = COMPLETE`
- `TASK_1H_2_VERIFICATION_STATUS = PASS`
- `TASK_1H_STATUS = COMPLETE`
- `TASK_1H_ARCHITECTURE_STATUS = FROZEN`
- `IDEMPOTENCY_GENERIC_RESPONSE_REPLAY = NOT_IMPLEMENTED_BY_DESIGN`
- `IDEMPOTENCY_HTTP_INTEGRATION = DEFERRED_TO_TASK_1I`
- `IDEMPOTENCY_RETENTION_POLICY = TO_BE_CONFIRMED`

