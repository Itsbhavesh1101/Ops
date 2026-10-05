# Phase 1 Task 1I.2 — Versioned /api/v1 Router Composition & OpenAPI Foundation

**Status**: Task 1I.2 Complete / Task 1I IN PROGRESS / Phase 1 IN PROGRESS  
**Scope**: Canonical `/api/v1` route prefix, API contract version metadata, single-point v1 aggregation router, production application composition with frozen Task-1I.1 exception handlers, static OpenAPI metadata, preservation of operational health/readiness probes, and documentation URL compatibility  
**Phase**: Phase 1 — Foundation  
**Authority**: [IMPLEMENTATION_GUARDRAILS.md](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)  
**Parent Tasks**:
- Task 1B: [PHASE_1_CONFIG_LOGGING_HEALTH.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_CONFIG_LOGGING_HEALTH.md)
- Task 1I.1: [PHASE_1_API_ERROR_CONTRACT.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_API_ERROR_CONTRACT.md)

---

## 1. Purpose

Task 1I.2 establishes the canonical HTTP routing architecture, stable API version prefix, and OpenAPI metadata foundation for the IR Infotech E-7 Operations Engine.

It ensures that:
1. Versioning is governed by a single immutable constant (`API_V1_PREFIX = "/api/v1"`) and a stable contract identifier (`API_V1_NAME = "v1"`), rather than environment variables, request headers, or ad-hoc route strings.
2. A single canonical top-level `APIRouter` in `app/api/v1/router.py` serves as the central aggregation point for all version 1 endpoints.
3. The frozen standardized API exception handlers from Task 1I.1 are wired directly into the production FastAPI application in `app/main.py`.
4. Operational infrastructure probes (`/api/v1/health`, `/api/v1/ready`, and root `/`) established in Task 1B remain strictly preserved without accidental renaming, deletion, or double-prefixing.
5. OpenAPI documentation metadata (`title`, `version`, `description`) is standardized and safe, with zero exposure of deployment environments, database credentials, broker URLs, or speculative future tag registries.

---

## 2. Canonical API Prefix & Version Constants

The platform version prefix is frozen as a public contract invariant:

```python
API_V1_PREFIX: str = "/api/v1"
API_V1_NAME: str = "v1"
```

### 2.1 Invariants
- **Single Source of Truth**: Defined in [app/api/v1/router.py](file:///f:/operations-engine/app/api/v1/router.py) and re-exported in `app/api/v1/` and `app/api/`. No scattered string literals for `"/api/v1"` exist across application code.
- **Contract Version vs. Build Version**: `API_V1_NAME = "v1"` identifies the HTTP API specification contract generation. It is completely decoupled from application build numbers, container tags, deployment dates, or Git commit SHAs.
- **No Dynamic Version Derivation**: The API version is never derived from headers, query parameters, hostnames, or tenant context.

---

## 3. Canonical V1 Router Composition

The top-level v1 router is defined in [app/api/v1/router.py](file:///f:/operations-engine/app/api/v1/router.py):

```python
router = APIRouter(prefix=API_V1_PREFIX)
api_router = router  # Backwards-compatible alias for existing imports
```

### 3.1 Aggregation Structure
- `router` applies `prefix=API_V1_PREFIX` (`/api/v1`) directly.
- Future domain modules (Task Intelligence, Workflow, Scheduling) will include their sub-routers into `router`.
- In [app/main.py](file:///f:/operations-engine/app/main.py), the router is mounted without an additional prefix:
  ```python
  app.include_router(api_v1_router)
  ```
- **No Double Prefixing**: Route paths are strictly `/api/v1/...`, never `/api/v1/api/v1/...`.

---

## 4. Main Application Composition & Error Handler Wiring

The root FastAPI application in [app/main.py](file:///f:/operations-engine/app/main.py) incorporates the frozen Task-1I.1 error handling and Task-1I.2 routing:

```python
app = FastAPI(
    title=OPENAPI_TITLE,
    description=OPENAPI_DESCRIPTION,
    version=OPENAPI_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Register standardized API error handlers (Task 1I.1)
register_api_exception_handlers(app)

# Correlation and structured request logging middleware (Task 1B)
app.add_middleware(CorrelationAndLoggingMiddleware)

# Configuration-driven CORS policy (Task 1B)
if settings.cors_allowed_origins:
    allow_credentials = "*" not in settings.cors_allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_v1_router)
```

### 4.1 Wiring Verification
- Exception handlers registered on `app` resolve directly to:
  - `ApiError` -> `api_error_handler`
  - `RequestValidationError` -> `validation_error_handler` (HTTP 422 with privacy-sanitized details)
  - `StarletteHTTPException` & `FastAPIHTTPException` -> `http_exception_handler` (HTTP 404/405/etc.)
  - `Exception` -> `unhandled_exception_handler` (HTTP 500 with internal logging and safe generic masking)
- Actual application 404 responses return the canonical `ApiErrorResponse` envelope containing `request.not_found` and the active request correlation ID in both body and `X-Correlation-ID` header.

---

## 5. OpenAPI Metadata & Documentation Conventions

### 5.1 Metadata Specification
- **Title**: `"E-7 Operations Engine API"` (`OPENAPI_TITLE`). Service identity only; contains no tenant names, environments, or database hostnames.
- **Version**: `"1.0.0"` (`OPENAPI_VERSION`). Semantic API contract generation.
- **Description**: `"E-7 Operations Engine HTTP API"` (`OPENAPI_DESCRIPTION`). Concise, factual description with zero speculative claims or unimplemented features.

### 5.2 Server URLs Unset
- `servers` in OpenAPI remains unset (`None` / empty).
- Hardcoding `localhost`, staging hostnames, or production URLs in the OpenAPI definition is strictly forbidden. Server host binding is an external infrastructure concern.

### 5.3 Documentation URLs
- Standard FastAPI developer documentation endpoints are preserved:
  - Swagger UI: `/docs`
  - ReDoc: `/redoc`
  - OpenAPI JSON schema: `/openapi.json`

### 5.4 No Speculative Tag Pre-Registration
- In accordance with foundation guardrails, future feature tags (`tasks`, `workflows`, `scheduling`, `jobs`) are **NOT** pre-registered on `app.openapi_tags`. Tags appear naturally when real endpoints and routers are introduced.
- Existing tags (`Health`) belonging to active endpoints are preserved.

---

## 6. Preservation of Operational Probes vs. Business API

### 6.1 Distinction Between Operational Probes and Versioned Business Resources
- **Operational Probes** (`/api/v1/health`, `/api/v1/ready`) are infrastructure liveness and traffic readiness signals for Kubernetes, load balancers, and process orchestrators.
- **Root Status** (`/`) provides lightweight process identification (`engine`, `status`, `docs`).
- These endpoints were established in Task 1B and **MUST NOT** be relocated, deleted, or wrapped in the business error/mutation envelope.

### 6.2 Pre-Task and Post-Task Route Inventory
The complete route inventory on `app.main.app` was verified before and after Task 1I.2 implementation:

| Path | Methods | Handler | Description | Status |
| :--- | :---: | :--- | :--- | :---: |
| `/openapi.json` | GET, HEAD | `app.openapi()` | OpenAPI 3.1.0 schema document | **Preserved** |
| `/docs` | GET, HEAD | Swagger UI | Interactive API documentation | **Preserved** |
| `/docs/oauth2-redirect` | GET, HEAD | Swagger redirect | OAuth2 redirect helper | **Preserved** |
| `/redoc` | GET, HEAD | ReDoc | Alternative API documentation | **Preserved** |
| `/api/v1/health` | GET | `health_check()` | Task-1B process liveness probe | **Preserved** |
| `/api/v1/ready` | GET | `readiness_check()` | Task-1B traffic readiness probe | **Preserved** |
| `/` | GET | `root()` | Task-1B root engine status | **Preserved** |

Zero routes were added, removed, or duplicated.

---

## 7. Middleware & Security Preservation

- **Correlation Middleware**: [CorrelationAndLoggingMiddleware](file:///f:/operations-engine/app/core/middleware.py#L39) is preserved in its exact execution position. No second correlation ContextVar or middleware was created.
- **CORS Configuration**: The configuration-driven CORS policy is preserved without modification. Wildcard origins cannot use credentials, and allowed origins are parsed from settings.
- **Authentication**: Zero authentication perimeter changes were made. Task 1C token verification and service key hashing contracts remain frozen. No dummy security schemes were added to OpenAPI.

---

## 8. Architectural Isolation & Stability

- **Presentation Boundary Only**: `app/api/v1/router.py` and `app/main.py` import zero ORM, database, or messaging dependencies:
  - Zero `sqlalchemy` / `AsyncSession` / `DeclarativeBase`
  - Zero `OutboxRepository` / `InboxRepository` / `IdempotencyRepository`
  - Zero `aio_pika` / `rabbitmq`
- **Import Purity**: Importing `app.api.v1.router` or `app.main` performs zero database connections, zero broker handshakes, and zero external network calls.
- **Database Schema Stability**: Reconfirmed that `Base.metadata.tables.keys()` contains exactly the 4 canonical tables (`audit_events`, `outbox_events`, `inbox_messages`, `idempotency_records`). Alembic revision head remains `0006`. No migration 0007 exists.

---

## 9. Scope Boundaries and Deferred Work

Task 1I.2 is strictly restricted to router composition and OpenAPI foundation. The following capabilities are explicitly deferred:

| Deferred Capability | Target Increment / Task |
| :--- | :--- |
| `Idempotency-Key` HTTP header extraction, validation & conflict mapping | Task 1I.3 |
| Asynchronous job persistence, status polling, and job endpoints | Task 1I.4 |
| Docker Compose runtime topology (API / worker / PG / RabbitMQ / Flowable) | Task 1J |
| Task Intelligence domain endpoints (`/api/v1/tasks`) | Phase 2 |
| Workflow domain endpoints (`/api/v1/workflows`) | Phase 3 |
| Scheduling domain endpoints (`/api/v1/schedules`) | Phase 4 |

---

## 10. Files Created and Modified

### Created:
- [tests/unit/test_api_v1_composition.py](file:///f:/operations-engine/tests/unit/test_api_v1_composition.py): 23 unit tests verifying prefix constants, router composition, handler wiring, route preservation, OpenAPI metadata, architectural isolation, and schema stability.
- [docs/phase-1/PHASE_1_API_V1_COMPOSITION.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_API_V1_COMPOSITION.md): This technical specification.

### Modified:
- [app/main.py](file:///f:/operations-engine/app/main.py): Registered frozen Task-1I.1 exception handlers, applied canonical OpenAPI metadata, and included `api_v1_router`.
- [app/api/v1/router.py](file:///f:/operations-engine/app/api/v1/router.py): Defined `API_V1_PREFIX`, `API_V1_NAME`, OpenAPI constants, and configured `router = APIRouter(prefix=API_V1_PREFIX)`.
- [app/api/v1/__init__.py](file:///f:/operations-engine/app/api/v1/__init__.py): Re-exported canonical prefix constants, metadata, and router.
- [app/api/__init__.py](file:///f:/operations-engine/app/api/__init__.py): Re-exported canonical prefix constants and metadata.
- [tests/unit/test_smoke.py](file:///f:/operations-engine/tests/unit/test_smoke.py): Updated FastAPI metadata assertions to match canonical OpenAPI title and version.
- [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md): Updated baseline tracking with Task 1I.2 completion.
