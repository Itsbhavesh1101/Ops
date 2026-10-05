# Phase 1 Task 1I.1 — Standardized API Error Contract & Safe FastAPI Exception Handlers

**Status**: Task 1I.1 Complete / Task 1I IN PROGRESS / Phase 1 IN PROGRESS  
**Scope**: Canonical API error response envelope, machine-readable namespaced error codes, safe field-validation error detail contracts, provider-neutral `ApiError` presentation exception, FastAPI / Starlette exception handlers, correlation ID propagation, and strict privacy protection  
**Phase**: Phase 1 — Foundation  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)  
**Parent Tasks**:
- Task 1B: [PHASE_1_CONFIG_LOGGING_HEALTH.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_CONFIG_LOGGING_HEALTH.md)
- Task 1H: [PHASE_1_IDEMPOTENT_EXECUTION.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_IDEMPOTENT_EXECUTION.md)

---

## 1. Purpose

The purpose of Task 1I.1 is to establish a single, version-independent, safe HTTP error representation for the IR Infotech E-7 Operations Engine API.

Prior to Task 1I.1, error responses were handled implicitly by framework defaults, risking inconsistent response envelopes, leaky stack traces or database details, exposed internal exceptions (`RuntimeError`), un-sanitized Pydantic validation inputs (which can echo secrets, tokens, or PII), and fragmented correlation tracking.

Task 1I.1 establishes:
1. One canonical, nested API error response model (`ApiErrorResponse` wrapping `ApiErrorBody`).
2. Machine-readable, lowercase namespaced error codes (`ErrorCode`) with bounded length and open extensibility.
3. Safe field-validation detail model (`ApiErrorDetail`) that strictly strips raw input and unvalidated context.
4. Provider-neutral `ApiError` exception for safe domain-to-API presentation.
5. FastAPI and Starlette exception handlers for `ApiError`, `RequestValidationError` (HTTP 422), routing errors (HTTP 404, HTTP 405), and unexpected exceptions (HTTP 500).
6. Correlation ID propagation reusing the Task 1B correlation infrastructure across all error responses.
7. Strict separation between internal structured diagnostic logging and public safe response representations.

---

## 2. Canonical API Error Response Envelope

All API errors return standard `application/json` responses adhering to the canonical nested `error` envelope:

```json
{
  "error": {
    "code": "request.validation_failed",
    "message": "Request validation failed.",
    "correlation_id": "req-12345-abc-67890",
    "details": [
      {
        "location": ["body", "name"],
        "code": "string_too_short",
        "message": "String should have at least 3 characters."
      }
    ]
  }
}
```

### 2.1 Model Invariants
- **`ApiErrorResponse`**: Outer wrapper containing exactly one field: `error: ApiErrorBody`. Configured with `frozen=True` and `extra="forbid"`.
- **`ApiErrorBody`**: Contains:
  - `code: str`: Validated machine-readable lowercase namespaced error code.
  - `message: str`: Safe, human-readable description (bounded to $\le 1024$ characters).
  - `correlation_id: str`: The canonical request correlation identifier.
  - `details: tuple[ApiErrorDetail, ...] | None`: Optional sequence of field/parameter-level issues.
- **`ApiErrorDetail`**: Structural error descriptor containing:
  - `location: tuple[str | int, ...]`: Safe path components indicating the source (e.g. `["body", "items", 0, "name"]`).
  - `code: str`: Machine-readable validation code (e.g. `string_too_short`, `missing`).
  - `message: str`: Safe human-readable explanation.

### 2.2 Details Optionality Choice
When an error does not have field-level or parameter-level details (such as HTTP 404, HTTP 405, HTTP 500, or simple `ApiError` instances), `details` defaults to `None` and is **omitted** from the serialized JSON response (`exclude_none=True`). This produces a clean, minimal envelope:
```json
{
  "error": {
    "code": "request.not_found",
    "message": "Resource not found.",
    "correlation_id": "req-12345-abc-67890"
  }
}
```
When `details` is present (such as in HTTP 422 validation errors or domain validation failures), `"details"` is serialized as a JSON list of objects.

---

## 3. Error Code Contract

Machine-readable error codes must use a bounded, lowercase namespaced format governed by the regular expression:
```regex
^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$
```

### 3.1 Validation Invariants
- **String Type**: Must be a non-empty string.
- **Bounded Length**: Maximum 128 characters (`MAX_ERROR_CODE_LENGTH = 128`).
- **Lowercase Only**: Must be strictly lowercase. Uppercase characters are rejected (no silent lowercasing).
- **Namespaced**: Must contain at least two dot-separated segments (e.g. `request.validation_failed`). Single-segment codes (e.g. `validation_failed`) are rejected.
- **Segment Formatting**: Each segment must start with a lowercase ASCII letter `[a-z]` followed by lowercase alphanumeric characters or underscores `[a-z0-9_]*`.
- **No Pathological Punctuation**: Leading dots, trailing dots, consecutive dots (`..`), and whitespace or control characters are rejected.

### 3.2 Extensibility & Open Hierarchy
Rather than maintaining a massive, rigid, closed enum for every future domain error across the platform, Task 1I.1 defines an **open, validated `ErrorCode` value object** alongside canonical foundation constants:

```python
class FoundationErrorCode:
    REQUEST_VALIDATION_FAILED = "request.validation_failed"
    REQUEST_NOT_FOUND = "request.not_found"
    REQUEST_METHOD_NOT_ALLOWED = "request.method_not_allowed"
    REQUEST_UNSUPPORTED_MEDIA_TYPE = "request.unsupported_media_type"
    INTERNAL_SERVER_ERROR = "internal.server_error"
```

Future modules (Task Intelligence, Workflow, Scheduling) can define domain codes such as:
- `task.not_found`
- `workflow.invalid_transition`
- `scheduling.solver_timeout`

without modifying foundation contracts or central enums.

---

## 4. Public Safe Messages vs. Internal Logging

### 4.1 Safe Public Message Contract
Public error responses contain sanitized, safe messages intended for clients. They **MUST NOT** expose:
- Exception strings (`str(exc)`, `repr(exc)`)
- Exception class names (`RuntimeError`, `OperationalError`)
- Tracebacks or source code file lines
- Raw SQL queries or database constraint names
- RabbitMQ connection URLs or broker topology
- Raw request bodies or sensitive input payloads
- JWTs, bearer tokens, or API keys

### 4.2 Generic HTTP 500 Handling
Unexpected internal exceptions (e.g. unhandled `Exception`, `RuntimeError`, database disconnection) are caught by `unhandled_exception_handler`.
The public HTTP response is always:
- **HTTP Status**: 500
- **Body**:
  ```json
  {
    "error": {
      "code": "internal.server_error",
      "message": "An internal server error occurred.",
      "correlation_id": "..."
    }
  }
  ```
- **Internal Logging**: The full exception, class name, traceback, HTTP method, route template, and correlation ID are logged internally via `structlog.get_logger(__name__).error("unhandled_server_error", exc_info=exc, ...)`.

### 4.3 Why Arbitrary `HTTPException.detail` Is Not Public Contract
Framework `HTTPException(detail=...)` instances can contain raw internal messages or strings unintended for public API consumption. Task 1I.1 normalizes framework HTTP exceptions:
- **404 Not Found**: Maps to `code="request.not_found"`, `message="Resource not found."`.
- **405 Method Not Allowed**: Maps to `code="request.method_not_allowed"`, `message="Method not allowed."`, preserving `Allow` headers.
- **415 Unsupported Media Type**: Maps to `code="request.unsupported_media_type"`, `message="Unsupported media type."`.
- **Other Statuses**: Maps to `code="http.error_<status_code>"` with the standard HTTP status phrase (e.g. `"Forbidden."` for 403), suppressing raw `detail` strings.

---

## 5. Request Validation Error Sanitization & Privacy

FastAPI and Pydantic v2 raise `RequestValidationError` on schema mismatches. By default, Pydantic includes an `input` field containing the raw user payload, which could leak passwords, API keys, social security numbers, or sensitive tokens.

### 5.1 Privacy-Safe Sanitization (`sanitize_validation_errors`)
The validation error handler processes each Pydantic error item:
1. **Location**: Converts scalar components into a clean tuple (`loc: tuple[str | int, ...]`).
2. **Code**: Extracts the validation error type (e.g. `string_too_short`, `missing`).
3. **Message**: Extracts the standard validation message (e.g. `"Field required"`).
4. **Input Stripping**: The `input` field is **completely discarded**.
5. **Context Stripping**: Arbitrary `ctx` dictionaries are **completely discarded**.
6. **Headers**: For header validation failures, the header name (e.g. `"x-api-token"`) is preserved in `location`, but the header value is never echoed.

---

## 6. Provider-Neutral `ApiError` Exception

For intentional, safe domain-to-API presentation errors, Task 1I.1 introduces [ApiError](file:///f:/operations-engine/app/api/errors.py):

```python
class ApiError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str | ErrorCode,
        message: str,
        details: Sequence[ApiErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        ...
```

### 6.1 Requirements & Invariants
- **`status_code`**: Must be an HTTP error status code in the range $400 \le \text{status\_code} \le 599$. Non-error status codes ($< 400$ or $> 599$) and non-integer types are rejected.
- **`code`**: Validated against `validate_error_code`.
- **`message`**: Non-empty string bounded to 1024 characters.
- **`details`**: Optional sequence converted to an immutable `tuple[ApiErrorDetail, ...]`.
- **`headers`**: Optional dictionary of HTTP response headers (e.g. `WWW-Authenticate`, custom headers).
- **Presentation Only**: `ApiError` owns zero database, transaction, outbox, inbox, or retry logic. Domain services raise domain exceptions, and future route/service adapters translate them into `ApiError`.

---

## 7. Correlation ID Propagation

Task 1I.1 strictly reuses the frozen Task 1B correlation infrastructure:
- Accesses `get_correlation_id()` from `app.core.correlation`.
- Checks `request.state.correlation_id` if contextvar is unset.
- Falls back to `validate_correlation_id(None)` if context is missing, generating a fresh, valid UUID4.
- **No second correlation ContextVar is created.**
- All error responses include the correlation ID in:
  1. The JSON response body (`error.correlation_id`).
  2. The HTTP response header (`X-Correlation-ID`).

---

## 8. Exception Handler Registration

Exception handlers are registered explicitly via:
```python
def register_api_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(FastAPIHTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
```

### Registration Properties:
- **Explicit**: Invoked by application bootstrap in Task 1I.2.
- **Idempotent**: Safe to call multiple times without duplicating handlers.
- **Pure Import**: Importing `app.api.errors` performs zero application mutation, creates no FastAPI app, registers no routes, opens no database sessions, and starts no network sockets.

---

## 9. Architectural Isolation & Boundaries

The API error module (`app/api/errors.py`) is strictly presentation-tier code:
- **Allowed Imports**: `fastapi`, `starlette`, `pydantic`, `structlog`, and Task 1B correlation/middleware utilities (`get_correlation_id`, `validate_correlation_id`, `resolve_route_template`).
- **Forbidden Imports (Zero Present)**:
  - `sqlalchemy` / `AsyncSession` / `DeclarativeBase`
  - `OutboxRepository` / `InboxRepository` / `IdempotencyRepository`
  - `rabbitmq` / `aio_pika`
  - `alembic`
- **Database Stability**: Database schema is completely untouched. Base metadata contains exactly 4 tables (`audit_events`, `outbox_events`, `inbox_messages`, `idempotency_records`). Alembic head remains `0006`. No migration 0007 exists.

---

## 10. Scope Boundaries and Deferred Work

Task 1I.1 implements standardized API error contracts and safe exception handlers only. The following capabilities are explicitly deferred:

| Deferred Capability | Target Increment / Task |
| :--- | :--- |
| Versioned `/api/v1` router composition and OpenAPI tags | Task 1I.2 |
| Business domain endpoints (tasks, workflows, schedules) | Phase 2+ |
| `Idempotency-Key` header extraction & idempotency HTTP mapping | Task 1I.3 |
| Asynchronous job persistence, polling, and job endpoints | Task 1I.4 |
| Docker Compose runtime wiring | Task 1J |

---

## 11. Files Created and Modified

### Created:
- [app/api/errors.py](file:///f:/operations-engine/app/api/errors.py): Canonical API error response models, error code validator, `ErrorCode`, `ApiErrorDetail`, `ApiErrorBody`, `ApiErrorResponse`, `ApiError` exception, sanitized validation error converter, exception handlers, and `register_api_exception_handlers`.
- [tests/unit/test_api_errors.py](file:///f:/operations-engine/tests/unit/test_api_errors.py): 63 comprehensive unit tests validating error codes, models, immutability, privacy sanitization, 404/405/415 handling, 500 masking, correlation propagation, architectural isolation, and schema stability.
- [docs/phase-1/PHASE_1_API_ERROR_CONTRACT.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_API_ERROR_CONTRACT.md): This technical specification.

### Modified:
- [app/api/__init__.py](file:///f:/operations-engine/app/api/__init__.py): Exported canonical error models, exception, and registration function.
- [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md): Updated to record Task 1H complete/frozen, Task 1I.1 complete, and Task 1I `IN_PROGRESS`.
