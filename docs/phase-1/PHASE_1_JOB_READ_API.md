# Phase 1 — Asynchronous Job Read & Polling API Specification

**Task:** Phase 1 Foundation Task 1I.4C  
**Module:** `app/api/v1/jobs.py`  
**Tests:** `tests/unit/test_api_jobs.py`  
**Status:** COMPLETE / FROZEN  
**Production Mount Status:** DEFERRED_PENDING_TRUSTED_HTTP_TENANT_AND_DB_DEPENDENCIES  

---

## 1. Purpose & Scope

Task 1I.4C establishes the canonical HTTP representation and provider-neutral polling API contract for asynchronous background jobs within the E-7 Operations Engine foundation.

### Core Capabilities:
- **Immutable Public Schema**: Defines `JobReadResponse` and `JobFailureResponse` with strict field isolation and privacy boundaries.
- **Pure Snapshot Mapper**: Pure translation function `job_snapshot_to_response` mapping domain `JobSnapshot` to HTTP `JobReadResponse`.
- **Port / Protocol**: `JobSnapshotReader` defining the minimal async query contract (`get(*, tenant_id, job_id) -> JobSnapshot | None`).
- **Safe Router Factory**: `create_job_read_router(*, tenant_dependency, reader_dependency)` constructing a pre-configured, relative `APIRouter(prefix="/jobs")`.
- **Polling & Cache Semantics**: `Cache-Control: no-store` on successful reads to prevent stale client caches during state changes.
- **Tenant Isolation**: Mandatory trusted-tenant resolution; indistinguishable 404 on missing or cross-tenant lookups.

---

## 2. Production Mount Deferral & Security Architecture

### Why the Router is NOT Mounted in Production:
The production application (`app.main.app`) and canonical v1 router (`app.api.v1.router.router`) do **NOT** mount the job router in Task 1I.4C.

**Rationale**:
1. The project has frozen provider-neutral authentication and tenant contracts (`TenantContext`, `AuthenticatedPrincipal`), but the complete HTTP authentication, tenant resolution, and database session dependency perimeter has not yet been wired into production FastAPI endpoints.
2. Exposing an unauthenticated or pseudo-authenticated job endpoint in production would represent a severe security vulnerability.
3. Task 1I.4C strictly prohibits resolving this gap via unverified client inputs (e.g., custom tenant headers, query parameters, route path segments, request bodies, or email domain inference).
4. The router factory enforces that callers must inject an explicit, trusted tenant dependency and reader dependency before routes become operational.

**Prerequisite for Production Mounting**:
Production mounting is deferred until the authenticated HTTP perimeter and unit-of-work/database session dependencies are established in subsequent Phase-1/Phase-2 increments.

---

## 3. Route Contract & Intended Endpoint

### Path & Operation:
- **Router Prefix**: `/jobs`
- **Composed v1 Path**: `GET /api/v1/jobs/{job_id}`
- **Operation**: Read-only job polling. Zero mutations (`POST`, `PUT`, `PATCH`, `DELETE`) or lifecycle transition triggers.

### Path Parameter Validation:
- `job_id`: Enforces canonical UUID version 4 syntax via `ValidatedJobIdPath` (`Annotated[uuid.UUID, ..., AfterValidator]`).
- Syntactically invalid strings, non-v4 UUIDs (e.g. UUIDv1), or nil UUIDs (`00000000-0000-0000-0000-000000000000`) are rejected immediately by FastAPI request validation, yielding canonical HTTP 422 (`request.validation_failed`).
- Malformed job IDs never trigger database lookups and are never translated to 404.

---

## 4. Response Schemas & Privacy Boundaries

### `JobReadResponse` (JSON Body):
```json
{
  "job_id": "b7906eb2-278a-4f42-9330-e72423f1ff5f",
  "job_type": "schedule.solve",
  "status": "running",
  "created_at": "2026-09-28T10:00:00Z",
  "started_at": "2026-09-28T10:01:00Z",
  "completed_at": null,
  "failure": null
}
```

### Exact Schema Fields:
| Field | Type | Description |
| :--- | :--- | :--- |
| `job_id` | `str` | Canonical UUIDv4 job identifier string. |
| `job_type` | `str` | Open namespaced logical job type (e.g. `schedule.solve`). |
| `status` | `JobStatus` | Canonical lifecycle status (`pending`, `running`, `succeeded`, `failed`). |
| `created_at` | `datetime` | Server-authored creation timestamp (ISO-8601). |
| `started_at` | `datetime \| None` | Execution start timestamp (ISO-8601, null when pending or failed pre-execution). |
| `completed_at` | `datetime \| None` | Terminal completion timestamp (ISO-8601, null when non-terminal). |
| `failure` | `JobFailureResponse \| None` | Sanitized failure metadata (null when non-failed). |

### Strict Exclusions:
The public schema strictly forbids and excludes:
- `tenant_id` (tenant scope is an authorization concern, not echoed publicly)
- `principal_id`, `user_id`, `email`
- `result`, `result_json`, `result_reference`, `resource_url` (speculative result fields excluded per Task 1I.4A)
- `progress`, `percentage`, `current_step` (lifecycle-only polling; no speculative progress tracking)
- `retry_count`, `priority`, `worker_id`, `cancel`

### `JobFailureResponse`:
```json
{
  "code": "solver.infeasible",
  "message": "No feasible schedule solution exists"
}
```
Exposes only pre-sanitized failure code and message. Zero stack traces, exception classes, SQL statements, credentials, or internal payload dumps.

---

## 5. Polling Semantics & Status Handling

### Status as Resource State:
- All four valid job states (`pending`, `running`, `succeeded`, `failed`) return **HTTP 200 OK** on GET.
- The asynchronous execution status is represented by `response.status`, NOT by altering the HTTP status code.
- **Never HTTP 202**: GET requests do not return 202 Accepted.
- **FAILED is Resource State**: Persisted `failed` jobs return HTTP 200 containing the sanitized failure object. They do **not** map to HTTP 500, 409, or 422.

### Cache Policy:
- **`Cache-Control: no-store`** is set on every successful 200 response to prevent client or proxy caching between polling cycles.
- **No `Retry-After` Header**: No arbitrary polling intervals are invented without business specification.
- **No ETag / Conditional GET**: Conditional validation headers (`If-None-Match`, `ETag`, `304`) are omitted.

---

## 6. Tenant Isolation & Error Handling

### Mandatory Tenant Scoping:
All lookups execute through `reader.get(tenant_id=trusted_tenant_id, job_id=canonical_job_id)`.
- The `tenant_id` is extracted strictly from the injected trusted dependency (`TenantContext`).
- Unverified client input is forbidden.

### Not-Found & Cross-Tenant Equivalence:
If `reader.get(...)` returns `None` (either because the job ID does not exist or belongs to another tenant), the endpoint raises:
```json
{
  "error": {
    "code": "job.not_found",
    "message": "Job not found.",
    "correlation_id": "...",
    "details": null
  }
}
```
This guarantees zero cross-tenant existence disclosure: an attacker cannot determine whether a job exists under a different tenant.

### Unmapped Errors:
- `JobStatusConflictError` belongs to conditional UPDATE mutations and is **not mapped** in this read-only polling API.
- `JobNotFoundError` is not raised during normal reads since `reader.get()` returns `None`.

---

## 7. Architectural Isolation Audit

`app/api/v1/jobs.py` strictly adheres to architectural boundaries:
- **Allowed Imports**: FastAPI (`APIRouter`, `Depends`, `Path`, `Response`), Pydantic, standard library (`uuid`, `datetime`, `typing`, `collections.abc`), core auth contracts (`TenantContext`), core job contracts (`JobId`, `JobType`, `JobStatus`, `JobFailure`, `JobSnapshot`), and API error models (`ApiError`, `ApiErrorResponse`).
- **Forbidden Imports (Verified 0)**: SQLAlchemy (`AsyncSession`, `engine`, `sessionmaker`), aio_pika, RabbitMQ, Celery, Redis.

---

## 8. Files Created / Modified

| File | Status | Description |
| :--- | :--- | :--- |
| `app/api/v1/jobs.py` | Created | HTTP job read contracts, response schemas, reader port, and router factory. |
| `tests/unit/test_api_jobs.py` | Created | Comprehensive unit test suite (30 test cases) verifying contracts, polling, isolation, and security. |
| `docs/phase-1/PHASE_1_JOB_READ_API.md` | Created | Canonical architecture and API specification document. |
| `app/api/v1/__init__.py` | Modified | Exported public job read models, reader protocol, and router factory. |
| `app/api/__init__.py` | Modified | Exported public job read models, reader protocol, and router factory. |
| `docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md` | Modified | Recorded completion of Task 1I.4C and closure of Canonical Task 1I.4 and Task 1I. |

---

## 9. Baseline & Roadmap Closure Status

- **Task 1I.4A**: COMPLETE / FROZEN (Job contracts, identity, lifecycle policy)
- **Task 1I.4B / 1I.4B.1**: COMPLETE / FROZEN (PostgreSQL persistence, atomic transitions, strict timezone-aware validation)
- **Task 1I.4C**: COMPLETE / FROZEN (HTTP job read/polling API contract & safe router factory)
- **Canonical Task 1I.4**: COMPLETE
- **Canonical Task 1I**: COMPLETE
- **Task 1J**: UNSTARTED (Runtime integration)
- **Task 1K**: UNSTARTED (Operational observability)
- **Phase 1**: IN_PROGRESS
