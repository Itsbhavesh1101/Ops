# Phase 1 Task 1H.1 — Idempotency Identity, Key-Digest & Request-Fingerprint Contracts

**Status**: Task 1H Complete (Tasks 1H.1, 1H.2, 1H.3, 1H.4 Frozen) / Phase 1 IN PROGRESS  
**Scope**: Provider-neutral idempotency identity and request-fingerprint contracts  
**Phase**: Phase 1 — Foundation  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)

---

## 1. Purpose and Overview

Task 1H.1 establishes the foundational, transport-neutral domain contracts for mutation idempotency in the E-7 Operations Engine.

Idempotency ensures that distributed clients retrying mutations across unstable networks do not induce duplicate side effects or business inconsistencies. To guarantee transport portability across HTTP mutation endpoints, future internal command processors, and asynchronous workflow initiators, these contracts remain decoupled from transport frameworks (FastAPI, Starlette, HTTP headers, request bodies, status codes) and persistence engines (SQLAlchemy, database tables, migrations).

---

## 2. Canonical Idempotency Identity Scope

Canonical idempotency identity is encapsulated in [IdempotencyIdentity](file:///f:/operations-engine/app/core/idempotency/contracts.py#L161-L245) and is strictly composed of three dimensions:

$$\text{IdempotencyIdentity} = (\text{tenant\_id}, \text{operation\_name}, \text{key\_digest})$$

```
+-----------------------------------------------------------------------------------+
| IdempotencyIdentity                                                               |
|                                                                                   |
|   tenant_id: str                     (e.g., 'tenant-acme-corp')                   |
|   operation_name: str                (e.g., 'tasks.create')                       |
|   key_digest: IdempotencyKeyDigest   (64-char lowercase SHA-256 hex digest)       |
+-----------------------------------------------------------------------------------+
```

### Why Principal ID is NOT Part of Canonical Identity

A critical architectural decision in E-7: **Principal ID is intentionally excluded from IdempotencyIdentity**.

- **Rationale**: An idempotency key represents an intent to perform a specific tenant-level business mutation. If Principal A submits a mutation with idempotency key `k-123`, and due to a failover, retry queue, or automated service token, Principal B (possessing authorized access within the same tenant) submits the same key `k-123` for the exact same operation, it must not execute a second mutation.
- **Authorization Separation**: Principal authentication and authorization are independently verified *prior* to mutation handling. Once authorized, the idempotency boundary protects the tenant's business state against duplicate execution.

---

## 3. Logical Operation Name Contract

The [OperationName](file:///f:/operations-engine/app/core/idempotency/contracts.py#L77-L97) value object defines the logical mutation boundary:

- **Format**: Lowercase dot-separated namespaced identifier matching `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`.
- **Segment Requirement**: Requires at least two dot-separated segments (e.g. `tasks.create`, `workflow.start`, `schedule.publish`, `billing.invoice.finalize`).
- **Validation**:
  - Rejects single segments (e.g. `create`).
  - Rejects uppercase characters (no silent lowercasing).
  - Rejects leading, trailing, and consecutive dots (`.tasks.create`, `tasks.create.`, `tasks..create`).
  - Bounded to $\le 128$ characters.
  - Rejects whitespace and control characters.
- **No Hardcoded Registry**: Does not restrict operation names to a closed enumeration, allowing modular expansion across bounded contexts.

---

## 4. Raw Idempotency Key Validation & Digesting

### Opaque Raw Key Validation

Before hashing, incoming client keys are validated via [validate_raw_idempotency_key](file:///f:/operations-engine/app/core/idempotency/hashing.py#L24-L56):
- Must be a string.
- Non-empty.
- No leading or trailing whitespace ambiguity.
- Bounded to $\le 255$ characters.
- Free of ASCII control characters (`ord < 32` or `ord == 127`).
- **Opaque**: Clients may use UUIDs, ULIDs, alphanumeric tokens, or domain-prefixed strings; the engine parses zero semantic meaning from the key format.

### Why Raw Keys Are Never Persisted

The E-7 architecture mandates that **raw client idempotency keys are never stored in persistence models**:
1. **Secret & Token Leakage Prevention**: Clients occasionally pass sensitive tokens, bearer credentials, or private identifiers as idempotency keys.
2. **Fixed-Width Indexing**: Hashing to SHA-256 yields a deterministic 64-character hex string, enabling uniform `VARCHAR(64)` database indexing and consistent query planning.
3. **Log & Trace Sanitization**: Raw keys are never retained in memory models after digesting; `repr(identity)`, `str(identity)`, and `model_dump(identity)` expose only `key_digest`.

### SHA-256 Key Digesting

Implemented via [digest_idempotency_key](file:///f:/operations-engine/app/core/idempotency/hashing.py#L59-L71):
- UTF-8 encodes the validated raw key.
- Computes `hashlib.sha256(key.encode('utf-8')).hexdigest().lower()`.
- Wraps the result in an immutable [IdempotencyKeyDigest](file:///f:/operations-engine/app/core/idempotency/contracts.py#L100-L127).
- Uses zero random salt (lookup identity must be strictly deterministic across nodes).
- Prohibits non-cryptographic hashes (`hash()`), deprecated algorithms (`MD5`, `SHA-1`), and salted password hashes (`bcrypt`, `Argon2`).

---

## 5. Request Fingerprinting Contract

To detect conflicts when a client accidentally reuses an idempotency key with differing payloads, the [RequestFingerprint](file:///f:/operations-engine/app/core/idempotency/contracts.py#L130-L158) value object encapsulates a 64-character lowercase SHA-256 hash.

### Conflict & Duplicate Handling Semantics

When idempotency persistence and execution orchestration are integrated:

| Identity Match | Fingerprint Match | Handling Semantics |
| :--- | :--- | :--- |
| $\text{Identity}_1 == \text{Identity}_2$ | $\text{Fingerprint}_1 == \text{Fingerprint}_2$ | **Duplicate Match**: Detected as `EXISTING_MATCH` -> returns `ALREADY_PROCESSED` without re-executing business mutation. Generic HTTP response payload replay is excluded by design. |
| $\text{Identity}_1 == \text{Identity}_2$ | $\text{Fingerprint}_1 \neq \text{Fingerprint}_2$ | **Idempotency Conflict**: Raises `IdempotencyConflictError`. Transport-level representation (e.g. HTTP 409) is owned by Task 1I. |

### Bytes-Only Fingerprint Helper

The helper [fingerprint_bytes](file:///f:/operations-engine/app/core/idempotency/hashing.py#L74-L88) computes:
$$\text{RequestFingerprint} = \text{SHA-256}(\text{data})$$

- **Bytes Only**: Accepts `bytes` strictly; rejects strings, dictionaries, or models.
- **Empty Payload Support**: Operations without payloads (e.g., parameterless command triggers) pass `b""`, producing the deterministic SHA-256 digest `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- **Why Dict/HTTP Canonicalization is Deferred**: Generic JSON dict hashing introduces subtle nondeterminism across key sorting, float representations, datetime formats, UUID serializations, and omitted vs. null fields. Exact canonicalization belongs to the transport/API boundary (Task 1I) or specific operation adapters.

---

## 6. Boundaries and Deferred Scope

### Explicitly Excluded from Task 1H.1:
- **Zero Database Persistence**: No SQLAlchemy models, no repositories, no table definitions.
- **Zero Schema Migrations**: `Base.metadata` remains `{audit_events, outbox_events, inbox_messages}`; Alembic head remains `0005`.
- **Zero Execution States**: No `PENDING`, `IN_PROGRESS`, `COMPLETED`, or `FAILED` state enums.
- **Zero Retention / TTL**: No expiration policies, lease durations, or cleanup cron jobs (`IDEMPOTENCY_RETENTION_POLICY = TO_BE_CONFIRMED`).
- **Zero Response Replay Storage**: No HTTP status codes, response headers, or cached response payloads (`IDEMPOTENCY_GENERIC_RESPONSE_REPLAY = NOT_IMPLEMENTED_BY_DESIGN`).
- **Zero HTTP Middleware**: No `Idempotency-Key` header extraction, FastAPI dependencies, or starlette middleware.
- **Zero Third-Party Dependencies**: Standard library `hashlib`, `re`, and existing `pydantic` only.

---

## 7. Downstream Task Evolution

The full Task 1H capability was established across four incremental steps:
1. **Task 1H.1**: Canonical transport-neutral identity and fingerprint contracts (`COMPLETE` / `FROZEN`).
2. **Task 1H.2**: Transactional idempotency persistence table (`idempotency_records`) and insert-first repository (`COMPLETE` / `FROZEN`).
3. **Task 1H.3**: Provider-neutral idempotent execution orchestration (`IdempotentMutationExecutor`) within a single UnitOfWork (`COMPLETE` / `FROZEN`).
4. **Task 1H.4**: Idempotency architecture closure: generic HTTP response replay is deliberately excluded by design to prevent coupling to transport serialization and PII (`FROZEN`).

---

## 8. Summary of Files Changed

| File Path | Action | Description |
| :--- | :---: | :--- |
| [app/core/idempotency/contracts.py](file:///f:/operations-engine/app/core/idempotency/contracts.py) | **Created** | Value objects `OperationName`, `IdempotencyKeyDigest`, `RequestFingerprint`, `IdempotencyIdentity`, and validation helpers. |
| [app/core/idempotency/hashing.py](file:///f:/operations-engine/app/core/idempotency/hashing.py) | **Created** | Cryptographic helpers `validate_raw_idempotency_key`, `digest_idempotency_key`, and `fingerprint_bytes`. |
| [app/core/idempotency/__init__.py](file:///f:/operations-engine/app/core/idempotency/__init__.py) | **Modified** | Package exports for all canonical idempotency contracts and helpers. |
| [tests/unit/test_idempotency_contracts.py](file:///f:/operations-engine/tests/unit/test_idempotency_contracts.py) | **Created** | Comprehensive unit test suite covering validation, hashing, immutability, security boundaries, and schema stability. |
| [docs/phase-1/PHASE_1_IDEMPOTENCY_CONTRACTS.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENCY_CONTRACTS.md) | **Created** | Architectural and technical documentation for Task 1H.1. |
| [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) | **Modified** | Updated foundation baseline recording Task 1H.1 completion with Task 1H IN PROGRESS. |

---

## 9. Verification Results

All required repository-wide verification gates were executed directly from the repository root (`F:\operations-engine`):

| Verification Command | Scope / Target | Result | Status |
| :--- | :--- | :--- | :---: |
| `python -m compileall app` | Bytecode compilation across all Python files in `app/` | Clean (all modules compiled, 0 errors) | **PASS** |
| `python -m ruff check .` | Repository-wide linter and import checker | Clean (0 errors, all checks passed) | **PASS** |
| `python -m mypy app` | Static type analysis across `app/` | Clean (97 source files, 0 issues) | **PASS** |
| `python -m pytest --collect-only -q` | Repository-wide test discovery | 667 tests collected across all test suites | **PASS** |
| `python -m pytest -q` | Repository-wide test suite execution | 667 passed in 7.48s (0 failures, 0 skipped) | **PASS** |

### Status Constants
- `TASK_1H_1_IMPLEMENTATION_STATUS = COMPLETE`
- `TASK_1H_1_VERIFICATION_STATUS = PASS`
- `TASK_1H_STATUS = COMPLETE`
- `TASK_1H_ARCHITECTURE_STATUS = FROZEN`
- `IDEMPOTENCY_GENERIC_RESPONSE_REPLAY = NOT_IMPLEMENTED_BY_DESIGN`
- `IDEMPOTENCY_HTTP_INTEGRATION = DEFERRED_TO_TASK_1I`

