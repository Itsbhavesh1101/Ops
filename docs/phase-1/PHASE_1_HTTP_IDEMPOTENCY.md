# Phase 1 Task 1I.3 — HTTP Idempotency-Key Boundary & Conflict Presentation

**Status**: Task 1I.3 Complete / Task 1I IN PROGRESS / Phase 1 IN PROGRESS  
**Scope**: HTTP `Idempotency-Key` extraction, duplicate-header ambiguity rejection, frozen Task-1H raw-key validation, immediate SHA-256 digest conversion, raw-key non-retention, trusted-tenant + logical-operation identity construction, RequestFingerprint boundary enforcement, `IdempotencyConflictError` -> canonical HTTP 409 presentation, and production FastAPI application registration  
**Phase**: Phase 1 — Foundation  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)  
**Parent Tasks**:
- Task 1H: [PHASE_1_IDEMPOTENCY_CONTRACTS.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENCY_CONTRACTS.md)
- Task 1H: [PHASE_1_IDEMPOTENCY_PERSISTENCE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENCY_PERSISTENCE.md)
- Task 1H: [PHASE_1_IDEMPOTENT_EXECUTION.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENT_EXECUTION.md)
- Task 1I.1: [PHASE_1_API_ERROR_CONTRACT.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_API_ERROR_CONTRACT.md)
- Task 1I.2: [PHASE_1_API_V1_COMPOSITION.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_API_V1_COMPOSITION.md)

> [!NOTE]
> This document records the technical architecture, invariants, and boundaries of the Task 1I.3 HTTP idempotency adapter. This is an engineering specification and foundation document, not an Architectural Decision Record (ADR).

---

## 1. Purpose

Task 1I.3 establishes the transport-layer HTTP adapter between external incoming HTTP requests and the transport-neutral, transactional idempotency foundation frozen in Task 1H.

The adapter provides:
1. Canonical `Idempotency-Key` header extraction and ambiguity rejection.
2. Immediate raw-key validation and SHA-256 digesting with strict raw-key non-retention.
3. Safe construction of the tenant-scoped `IdempotencyIdentity` using strictly trusted server-side tenant context.
4. An immutable `HttpIdempotencyContext` container accepting caller-provided `RequestFingerprint` instances without ever reading raw HTTP request bodies.
5. Standardized mapping of `IdempotencyConflictError` to HTTP 409 Conflict using the canonical Task-1I.1 error envelope and correlation ID machinery.
6. Explicit, idempotent exception handler registration on the production FastAPI application.

---

## 2. Header Contract: Idempotency-Key

A single canonical header constant is defined in [app/api/idempotency.py](file:///f:/operations-engine/app/api/idempotency.py):

```python
IDEMPOTENCY_KEY_HEADER: str = "Idempotency-Key"
```

### 2.1 Invariants
- **Exact Name**: Matches `"Idempotency-Key"`. HTTP header lookup is case-insensitive as provided by Starlette.
- **No Aliases**: Alternate header names (such as `X-Idempotency-Key`, `Request-Id`, or `X-Request-Id`) are strictly forbidden and not supported.
- **Single Source of Truth**: The header name constant is exported in `app/api` and must not be hardcoded or scattered across route handlers.

---

## 3. Header Extraction, Validation & Ambiguity Rejection

Header resolution is performed by `resolve_idempotency_key_digest(request: Request) -> IdempotencyKeyDigest`. It inspects header field occurrences using Starlette's `request.headers.getlist(IDEMPOTENCY_KEY_HEADER)`.

### 3.1 Three Cardinal Extraction Cases

| Case | Header Count | Result / HTTP Error | Code | Public Message |
| :--- | :--- | :--- | :--- | :--- |
| **Missing** | 0 values | HTTP 400 Bad Request | `idempotency.key_required` | `"Idempotency-Key header is required."` |
| **Ambiguous** | > 1 values | HTTP 400 Bad Request | `idempotency.key_ambiguous` | `"Multiple Idempotency-Key headers are not allowed."` |
| **Single** | Exactly 1 value | Validated & Digested | — | (Proceeds to raw-key validation) |

### 3.2 Duplicate Header Ambiguity
If multiple `Idempotency-Key` header fields are present in a single request (e.g. repeated header lines), the adapter rejects the request immediately with HTTP 400 `idempotency.key_ambiguous`.
- It does **not** silently select the first occurrence.
- It does **not** silently select the last occurrence.
- It does **not** join occurrences with commas.
- Neither value is digested.

### 3.3 Raw-Key Validation & Invalid Header Handling
When exactly one header field is present, the raw string is validated against frozen Task-1H rules via `validate_raw_idempotency_key(raw_key)`:
- Non-empty, non-whitespace.
- No leading or trailing whitespace.
- No ASCII control characters (code `< 32` or `== 127`).
- Length bounded to `<= 255` characters.

If validation fails for any reason, the adapter raises an `ApiError`:
- **HTTP Status**: 400 Bad Request
- **Error Code**: `idempotency.key_invalid`
- **Public Message**: `"Idempotency-Key header is invalid."`
- **Privacy Minimization**: Zero raw input, offending characters, or internal validation exception text is leaked in the public message or details.

---

## 4. Immediate SHA-256 Digesting & Raw-Key Non-Retention

The raw client key is strictly transient:
1. Extracted from the request header as a local string variable.
2. Validated with `validate_raw_idempotency_key(raw_key)`.
3. Digested into a deterministic 64-character lowercase hex SHA-256 digest via `digest_idempotency_key(raw_key) -> IdempotencyKeyDigest`.
4. Discarded immediately.

### 4.1 Strict Non-Retention Invariants
The raw key is **NEVER** retained in:
- `request.state`
- `HttpIdempotencyContext`
- `IdempotencyIdentity`
- Pydantic models or dataclasses
- Exceptions (`ApiError`, `IdempotencyConflictError`)
- Structured application logs
- Public error responses or details
- Database records or cache markers

Only the SHA-256 `IdempotencyKeyDigest` survives beyond the initial header resolution step.

---

## 5. Trusted Tenant Boundary & Operation Identity Construction

### 5.1 Prohibition on Header/Query/Path Tenant Derivation
Task 1C froze the core security invariant:
> Tenant identity must originate strictly from authenticated and authorized principal credentials or approved service identities. It must NEVER be inferred from unverified client inputs such as `X-Tenant-ID`, query parameters, URL path segments, or request payloads.

The Task 1I.3 adapter enforces this rule:
- Zero `X-Tenant-ID` header inspection exists.
- The identity builder helper does not take a `Request` object.

### 5.2 Identity Builder
The canonical identity is constructed via `build_http_idempotency_identity`:

```python
def build_http_idempotency_identity(
    *,
    tenant_id: str | TenantContext,
    operation_name: str | OperationName,
    key_digest: IdempotencyKeyDigest,
) -> IdempotencyIdentity
```

Invariants:
- `tenant_id`: Accepted only as an explicit argument from the trusted server-side authentication context (either as a validated `TenantContext` or non-empty `str`).
- `operation_name`: Bound to a logical, namespaced `OperationName` (e.g. `OperationName("tasks.create")`). Never bound to dynamic HTTP paths (e.g. `/api/v1/tasks/123`) or HTTP verbs (`POST`).
- `key_digest`: Validated `IdempotencyKeyDigest`.
- Zero principal ID: Mutation idempotency protects the tenant resource; re-submissions by any authorized principal within the same tenant resolve identically.
- Reuses the frozen `IdempotencyIdentity` from Task 1H without introducing competing identity models.

---

## 6. Request Fingerprint Boundary & Payload Independence

### 6.1 No Generic Request Body Hashing
Task 1H explicitly froze `fingerprint_bytes()` for **already-canonicalized** bytes supplied by concrete operation adapters.

Task 1I.3 enforces strict payload independence:
- The adapter does **NOT** call `await request.body()`.
- The adapter does **NOT** parse JSON (`json.loads`) or serialize models (`json.dumps`).
- The adapter does **NOT** attempt generic canonicalization of multipart, form, or JSON payloads.
- The adapter does **NOT** call `fingerprint_bytes()`.

### 6.2 HttpIdempotencyContext Container
```python
class HttpIdempotencyContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    identity: IdempotencyIdentity
    request_fingerprint: RequestFingerprint | None = None
```
- Holds the canonical `IdempotencyIdentity` and an optional caller-supplied `RequestFingerprint`.
- If a route operation enforces payload fingerprinting, the route adapter must compute and validate the canonical `RequestFingerprint` and supply it to the context.

---

## 7. Conflict Presentation (HTTP 409 Conflict)

When a client submits an idempotency key matching an existing committed mutation for the same tenant and logical operation, but with differing request payload bytes, the domain executor raises `IdempotencyConflictError`.

### 7.1 Canonical Conflict Handler
`idempotency_conflict_handler(request: Request, exc: IdempotencyConflictError) -> JSONResponse`:
- Constructs a canonical `ApiError(status_code=409, code="idempotency.conflict", message="Idempotency key was already used for a different request.")`.
- Delegates directly to the frozen Task-1I.1 `api_error_handler(request, api_error)` seam.
- Produces the canonical nested error envelope:
  ```json
  {
    "error": {
      "code": "idempotency.conflict",
      "message": "Idempotency key was already used for a different request.",
      "correlation_id": "c1f7a012-38d1-4b72-9b27-0cf7df57b469"
    }
  }
  ```
- Emits the `X-Correlation-ID` HTTP response header.
- Privacy Guarantees: Contains zero raw client keys, request bodies, stack traces, internal fingerprints, or database contents.

### 7.2 Application Registration
Registered explicitly on the FastAPI application via:
```python
def register_idempotency_exception_handler(app: FastAPI) -> None:
    app.add_exception_handler(IdempotencyConflictError, idempotency_conflict_handler)
```
- In [app/main.py](file:///f:/operations-engine/app/main.py), wired alongside Task-1I.1 exception handlers.
- Idempotent: safe to invoke multiple times without registering duplicate handlers.
- Resolves `IdempotencyConflictError` specifically rather than falling through to generic unhandled `Exception` -> 500.

---

## 8. Success Policy & No Generic Response Replay

### 8.1 Frozen Decision: IDEMPOTENCY_GENERIC_RESPONSE_REPLAY = NOT_IMPLEMENTED_BY_DESIGN
Task 1H deliberately stores zero HTTP response bodies, headers, status codes, or serialized result references in the database. Consequently, generic transport infrastructure cannot reconstruct a prior HTTP response.

### 8.2 Transport Rule
- **EXECUTED**: The concrete route handler returns its operation-specific HTTP response (e.g. 201 Created, 202 Accepted, 200 OK, or 204 No Content). Task 1I.3 defines no generic status mapping.
- **ALREADY_PROCESSED**: The concrete route handler must use its documented canonical resource/job recovery strategy (e.g. fetching the existing entity by natural key or returning an operation-specific acknowledgment). It is not an error and must not be mapped generically to 200, 202, or 409.
- **IdempotencyConflictError**: Mapped generically to HTTP 409 Conflict.

---

## 9. Authorization Separation

Possession of a valid idempotency key grants **zero** authorization:
- Task 1I.3 does not authenticate, authorize, or evaluate permissions.
- Concrete route handlers must authenticate and authorize the caller independently before attempting any idempotent mutation execution.

---

## 10. Architectural Isolation & Stability

### 10.1 Zero Database or Messaging Dependencies
[app/api/idempotency.py](file:///f:/operations-engine/app/api/idempotency.py) imports zero:
- `sqlalchemy` / `AsyncSession`
- `IdempotencyRepository` / `UnitOfWork`
- `OutboxRepository` / `InboxRepository`
- `aio_pika` / RabbitMQ

It strictly imports value contracts and hashing helpers from `app.core.idempotency`, authentication models from `app.core.auth`, and error handlers from `app.api.errors`.

### 10.2 Database Schema Stability
- Metadata tables remain exactly the canonical four:
  - `audit_events`
  - `outbox_events`
  - `inbox_messages`
  - `idempotency_records`
- Alembic migration head remains strictly `0006`. Zero new migrations were added.

---

## 11. Files Created and Modified

### Created
- [app/api/idempotency.py](file:///f:/operations-engine/app/api/idempotency.py) — Canonical HTTP idempotency boundary adapter.
- [tests/unit/test_api_idempotency.py](file:///f:/operations-engine/tests/unit/test_api_idempotency.py) — 47 comprehensive unit tests covering headers, privacy, identity, context, conflict presentation, and isolation.
- [docs/phase-1/PHASE_1_HTTP_IDEMPOTENCY.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_HTTP_IDEMPOTENCY.md) — Architectural specification.

### Modified
- [app/main.py](file:///f:/operations-engine/app/main.py) — Explicit registration of `IdempotencyConflictError` exception handler.
- [app/api/__init__.py](file:///f:/operations-engine/app/api/__init__.py) — Export of canonical idempotency adapter symbols.
- [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) — Foundation baseline update.

---

## 12. Deferred Work (Task 1I.4)

Task 1I.4 — Asynchronous Job Foundation (including provider-neutral job identity/contracts, job persistence, lifecycle semantics, and job polling/read API contracts) remains **UNSTARTED**.

Canonical Task 1I remains **IN PROGRESS**.  
Phase 1 Foundation remains **IN PROGRESS**.
