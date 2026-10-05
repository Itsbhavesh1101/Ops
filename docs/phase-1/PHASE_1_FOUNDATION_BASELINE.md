# Phase 1 Foundation Tooling & CI Baseline

**Document Version**: 1.1.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1A.1 — Foundation Dependency & Type-Check Baseline Correction  
**Status**: `COMPLETE`  
**Application Root**: `app/`  

---

## 1. Task Scope & Baseline Corrections

Task 1A established the engineering baseline, toolchain configuration, dependency hygiene, and CI pipeline for the **IR Infotech E-7 Operations Engine** before implementing runtime infrastructure. Task 1A.1 applies a targeted technical correction:

- **Repository & Packaging Baseline**: Validated `pyproject.toml` configuration, canonical `app/` application package discovery via `[tool.setuptools.packages.find]`, and reproducible local installation via `pip install -e .` and `pip install -e ".[dev]"`.
- **Python Runtime Declaration**: Standardized on Python `>=3.11` (target: Python 3.11) across `pyproject.toml`, Docker, and CI workflows.
- **Dependency Hygiene Correction**:
  - Re-audited runtime imports under `app/`. Active runtime dependencies contain **only** packages required by currently executable foundation code: `fastapi`, `uvicorn[standard]`, and `pydantic`.
  - `pydantic-settings` is classified as `DEFERRED_TO_TASK_1B_CONFIGURATION` (no Task-1A code imports it).
  - Logging framework selection is classified as `DEFERRED_TO_TASK_1B_LOGGING`; `loguru` was removed from active runtime dependencies, and no premature logging framework selection is made in Task 1A. Structured logging implementation is deferred to Task 1B.
  - `httpx` is used exclusively by `FastAPI`'s `TestClient` in tests and was moved to development/test dependencies (`[project.optional-dependencies].dev`).
- **Roadmap Classification Correction**:
  - `ortools` is correctly classified as **Phase 4 Generic Scheduling MVP** per the frozen roadmap (Phase 2 = Task Intelligence MVP, Phase 3 = Workflow MVP, Phase 4 = Generic Scheduling MVP, Phase 5 = Education Scheduling PoC).
- **Type Checking Baseline Correction**:
  - Replaced premature `strict = true` with an explicit moderate foundation configuration enforcing type safety on active code without global suppression.
- **Test Baseline**: Preserved minimal import-safety and smoke tests verifying that the application loads and serves endpoints without external infrastructure.
- **CI Workflow**: Preserved automated GitHub Actions workflow (`.github/workflows/ci.yml`) executing bytecode compilation, linting, type-checking, and unit tests on pushes and pull requests.

> [!NOTE]
> Task 1A / 1A.1 implements **zero business functionality** and **zero runtime infrastructure**. Subsequent Phase-1 tasks (1B through 1K) remain strictly sequenced.

---

## 2. Python & Packaging Baseline

| Dimension | Specification | Verification |
| :--- | :--- | :--- |
| **Supported Python Version** | `>=3.11` (Target: `3.11`) | Declared in `pyproject.toml`, `docker/Dockerfile`, and `.github/workflows/ci.yml`. |
| **Active Python Environment** | Python 3.13.2 | Compatible with `>=3.11` constraint. |
| **Build Backend** | `setuptools>=68.0`, `wheel` | Standard PEP 517/621 build system. |
| **Package Discovery** | `app*` (Flat layout exclusion) | Configured in `[tool.setuptools.packages.find]` to avoid packaging `docker`, `ui`, `workflows`, or `solver_profiles`. |
| **Application Package Root** | `app/` | Canonical root preserved; no alternative roots introduced. |

---

## 3. Dependency Audit & Categorization

In accordance with Phase-1 dependency hygiene rules, active dependencies are strictly confined to packages required by the currently executable foundation runtime.

### 3.1 Active Runtime Dependencies (`CURRENTLY_REQUIRED`)
Packages with actual, verifiable imports in the current executable scaffold (`app/main.py`, `app/core/config.py`, `app/core/logging.py`, `app/core/correlation.py`, `app/core/middleware.py`, `app/core/auth/`, `app/api/v1/router.py`):

| Package | Specification | Current Runtime Justification |
| :--- | :--- | :--- |
| `fastapi` | `>=0.115.0` | Core web framework, routing (`APIRouter`), and application lifecycle (`FastAPI`) |
| `uvicorn[standard]` | `>=0.30.0` | Production ASGI web server runner |
| `pydantic` | `>=2.8.0` | Core schema definitions, request/response validation in FastAPI |
| `pydantic-settings` | `>=2.4.0` | Strongly typed application runtime configuration (`BaseSettings`), activated in Task 1B |
| `structlog` | `>=24.1.0` | Structured key/value application and HTTP request event logging, activated in Task 1B |
| `httpx` | `>=0.27.0` | Asynchronous HTTP transport for dynamic OIDC discovery and JWKS retrieval, activated in Task 1C.2 |
| `pyjwt[crypto]` | `>=2.9.0` | Cryptographic OpenID Connect ID-token signature verification and claims validation, activated in Task 1C.2 |
| `sqlalchemy[asyncio]` | `>=2.0.0` | Asynchronous PostgreSQL persistence engine, connection pooling, and DeclarativeBase metadata, activated in Task 1D |
| `asyncpg` | `>=0.29.0` | High-performance asynchronous PostgreSQL DBAPI driver, activated in Task 1D |
| `alembic` | `>=1.13.0` | Database schema migrations and revision management, activated in Task 1D |

*(Note: Task 1C.3 Service API-key verification uses Python standard library `hashlib`, `hmac`, and `secrets`; zero new external runtime dependencies added.)*

### 3.2 Development & Test Dependencies (`DEV_TOOLING`)
Dependencies required for local verification and automated CI quality checks:

| Package | Specification | Purpose |
| :--- | :--- | :--- |
| `pytest` | `>=8.3.0` | Test framework and runner |
| `pytest-asyncio` | `>=0.24.0` | Asynchronous test execution support |
| `pytest-cov` | `>=5.0.0` | Code coverage measurement |
| `ruff` | `>=0.6.0` | Fast Python linter and code formatter |
| `mypy` | `>=1.11.0` | Static type checker |
| `pre-commit` | `>=3.8.0` | Git pre-commit hook runner |

### 3.3 Deferred Dependencies (`DEFERRED_TO_LATER_PHASE`)
Dependencies categorized and deferred to their designated sequence task or roadmap phase:

| Package | Target Task / Phase | Rationale for Deferral |
| :--- | :---: | :--- |
| `pydantic-settings` | Phase 1 (Task 1B) | *ACTIVATED IN TASK 1B*: Runtime configuration model and cached settings accessor. |
| `structlog` | Phase 1 (Task 1B) | *ACTIVATED IN TASK 1B*: Structured event logging engine (standard logging + structlog). |
| `pyjwt[crypto]` / `httpx` | Phase 1 (Task 1C.2) | *ACTIVATED IN TASK 1C.2*: Google OIDC cryptographic verification and dynamic JWKS caching. |
| `passlib[bcrypt]` | Phase 1 (Task 1C) | Password hashing; deferred to authentication foundation |
| `python-multipart` | Phase 1 (Task 1C) | Form parsing for auth flows; deferred to Task 1C |
| `sqlalchemy[asyncio]` | Phase 1 (Task 1D) | *ACTIVATED IN TASK 1D*: Asynchronous PostgreSQL engine, pooling, and DeclarativeBase metadata. |
| `asyncpg` | Phase 1 (Task 1D) | *ACTIVATED IN TASK 1D*: High-performance asynchronous PostgreSQL driver. |
| `psycopg[binary]` | Phase 1 (Task 1D) | Unactivated; asyncpg selected as canonical async PostgreSQL driver. |
| `alembic` | Phase 1 (Task 1D) | *ACTIVATED IN TASK 1D*: Database migration framework and baseline revision. |
| `aio-pika` | Phase 1 (Task 1G) | RabbitMQ event broker driver; deferred to messaging foundation |
| `opentelemetry-api` | Phase 1 (Task 1K) | Distributed tracing API; deferred to observability task |
| `opentelemetry-sdk` | Phase 1 (Task 1K) | OpenTelemetry SDK runtime; deferred to observability task |
| `opentelemetry-instrumentation-fastapi` | Phase 1 (Task 1K) | Automatic FastAPI instrumentation; deferred to observability task |
| `prometheus-client` | Phase 1 (Task 1K) | Prometheus metrics export; deferred to observability task |
| `pydantic-ai` | Phase 2 (Task Intelligence MVP) | LLM-based Task Intelligence; not permitted in Phase 1 foundation |
| `networkx` | Phase 2 (Task Intelligence MVP) | WBS graph decomposition & CPM slack calculations |
| *(workflow runtime)* | Phase 3 (Workflow MVP) | Workflow engine adapters and DMN runtime |
| `ortools` | Phase 4 (Generic Scheduling MVP) | Exact mathematical constraint solver (Google OR-Tools CP-SAT) |
| *(unitime client)* | Phase 5 (Education Scheduling PoC) | Timetabling adapter for education scheduling evaluation |
| `deepeval` / `locust` | Future Benchmarks | LLM evaluation and performance stress benchmarking |

---

## 4. Lint, Type-Check & Test Tooling Configuration

All configuration is consolidated in [pyproject.toml](file:///f:/operations-engine/pyproject.toml):

### 4.1 Ruff Linter Configuration
```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N"]
ignore = ["B008"]
```
- **Rules Enforced**: Pyflakes (`F`), pycodestyle errors (`E`), isort import sorting (`I`), flake8-bugbear (`B`), pyupgrade (`UP`), pep8-naming (`N`).
- **Target Version**: Python 3.11.

### 4.2 Mypy Static Type Checker Configuration
```toml
[tool.mypy]
python_version = "3.11"
check_untyped_defs = true
disallow_untyped_defs = true
no_implicit_optional = true
warn_unused_ignores = true
warn_redundant_casts = true
ignore_missing_imports = true
```
- **Target**: `app/`
- **Baseline Philosophy**: Explicit moderate foundation configuration. Catches meaningful typing errors (`disallow_untyped_defs`, `check_untyped_defs`, `no_implicit_optional`, `warn_unused_ignores`, `warn_redundant_casts`) without premature full strict-mode compliance (`strict = true` removed) and without global suppression (`ignore_errors = true` prohibited).

### 4.3 Pytest Configuration
```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
asyncio_mode = "auto"
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
```

---

## 5. Test Baseline & Import-Safety

A dedicated smoke test suite is maintained at [tests/unit/test_smoke.py](file:///f:/operations-engine/tests/unit/test_smoke.py) covering 5 foundational assertions:

1. **`test_import_app_package`**: Confirms `app` and `app.main` import cleanly without exceptions.
2. **`test_fastapi_app_instance`**: Confirms the `FastAPI` application instance initializes with correct metadata (`title`, `version`, `docs_url`).
3. **`test_root_endpoint`**: Verifies that `GET /` responds with `200 OK` and expected online status payload.
4. **`test_health_check_endpoint`**: Verifies that `GET /api/v1/health` responds with `200 OK` and healthy service status.
5. **`test_no_external_services_required_at_import`**: Verifies that importing and starting the application requires zero external connections, databases, message brokers, external IdPs, or solvers.

---

## 6. Continuous Integration (CI) Baseline

A GitHub Actions CI workflow is maintained at [.github/workflows/ci.yml](file:///f:/operations-engine/.github/workflows/ci.yml):

- **Triggers**: Automated execution on `push` and `pull_request`.
- **Environment**: Ubuntu Linux runner with Python `3.11` and pip caching.
- **Sequential Pipeline Steps**:
  1. Repository checkout (`actions/checkout@v4`).
  2. Python 3.11 setup (`actions/setup-python@v5`).
  3. Editable installation with development dependencies (`pip install -e ".[dev]"`).
  4. Bytecode compilation verification (`python -m compileall app`).
  5. Static linting and import formatting verification (`ruff check .`).
  6. Static type analysis (`mypy app`).
  7. Automated unit and smoke tests (`pytest -q`).
- **Security Invariant**: CI runs completely isolated; zero connections to external production databases, message brokers, or external IdP services.

---

## 7. Change Scope & Summary of Modified Files

| File Path | Action | Description of Changes |
| :--- | :---: | :--- |
| [pyproject.toml](file:///f:/operations-engine/pyproject.toml) | **Modified** | Refined active runtime dependencies to `fastapi`, `uvicorn[standard]`, `pydantic`; moved `httpx` to dev group; replaced `strict = true` with explicit moderate mypy configuration. |
| [docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md](file:///f:/operations-engine/docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md) | **Modified** | Updated dependency audit, corrected logging classification to deferred, corrected OR-Tools to Phase 4, documented moderate mypy baseline. |
| [README.md](file:///f:/operations-engine/README.md) | **Preserved** | Contains concise local development quality and test execution instructions with zero premature framework claims. |
| [tests/unit/test_smoke.py](file:///f:/operations-engine/tests/unit/test_smoke.py) | **Preserved** | Foundation smoke and import-safety test suite (5 passing tests). |
| [.github/workflows/ci.yml](file:///f:/operations-engine/.github/workflows/ci.yml) | **Preserved** | GitHub Actions CI workflow running compilation, linting, type-checking, and pytest. |

---

## 8. Verification Results

All quality and packaging commands were revalidated locally and passed with zero errors:

| Quality Command | Scope | Result | Status |
| :--- | :--- | :---: | :---: |
| `pip install -e .` | Minimal runtime package installation (fastapi, uvicorn, pydantic) | Clean | **PASS** |
| `pip install -e ".[dev]"` | Full development and test tooling installation (including httpx) | Clean | **PASS** |
| `python -m compileall app` | Bytecode syntax across all 53 files in `app/` | Clean | **PASS** |
| `ruff check .` | Linting, import sorting, syntax checks | 0 errors | **PASS** |
| `mypy app` | Moderate static type checking on `app/` | 0 errors (53 files) | **PASS** |
| `pytest -q` | Foundation smoke and import-safety tests | 5 passed in 0.25s | **PASS** |

---

## 9. Deferred Phase-1 Work

Task 1A.1 is strictly restricted to foundation tooling correction. Future tasks in the frozen Phase-1 sequence remain sequenced as follows:

- **Task 1B**: Configuration management (`app/core/config`, `pydantic-settings`), structured logging implementation (framework selection deferred to Task 1B), request correlation IDs, health checks.
- **Task 1C**: Authentication perimeter, Google OIDC token validation (Task 1C.2/1C.2A complete), service API key verification (Task 1C.3/1C.3A complete), tenant resolution, RBAC foundation.
- **Task 1D**: SQLAlchemy 2.0 async engine, session factory, base model, and Alembic migrations.
- **Task 1E**: Repository pattern + Unit of Work + explicit transaction boundaries (*COMPLETE in Task 1E / 1E.1 / 1E.2*; infrastructure/abstractions only; zero business/domain tables).
- **Task 1F**: Append-only audit (*COMPLETE in Task 1F / 1F.1 / 1F.2*; canonical `audit_events` schema, actor integrity, strict JSON float validation, persistence-owned pre-flush UUID guarantee, append-only database trigger, `AuditRepository`, zero outbox/inbox/RabbitMQ).
- **Task 1G**: Transactional Outbox + RabbitMQ + versioned event envelope + Inbox/deduplication (*Task 1G implementation increments complete*: Task 1G.1/1G.1A canonical event envelope contract, Task 1G.2/1G.2A transactional Outbox persistence, Task 1G.3A/1G.3A.1 RabbitMQ publisher transport foundation, Task 1G.3B1 outbox dispatch claim & retry-state persistence, Task 1G.3B2/1G.3B2A single-event outbox dispatch orchestration, Task 1G.3B3 bounded dispatcher runner, Task 1G.4A transactional inbox persistence, Task 1G.4B provider-neutral consumer transaction orchestration, Task 1G.4C1/1G.4C1A RabbitMQ inbound message boundary & parser hardening, and Task 1G.4C2 RabbitMQ consumer topology and bounded runner complete; `TASK_1G_IMPLEMENTATION_STATUS = COMPLETE`; `TASK_1G_RUNTIME_INTEGRATION_STATUS = DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION`).

- **Task 1H**: Idempotency (*COMPLETE* / *FROZEN* — Task 1H.1 identity contracts complete; Task 1H.2 transactional idempotency marker persistence and conflict detection complete; Task 1H.3 provider-neutral execution orchestration complete; Task 1H.4 architecture closure complete; `TASK_1H_IMPLEMENTATION_STATUS = COMPLETE`; `TASK_1H_ARCHITECTURE_STATUS = FROZEN`; `IDEMPOTENCY_GENERIC_RESPONSE_REPLAY = NOT_IMPLEMENTED_BY_DESIGN`; `IDEMPOTENCY_HTTP_INTEGRATION = DEFERRED_TO_TASK_1I`; `IDEMPOTENCY_RUNTIME_INTEGRATION = DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION`; `IDEMPOTENCY_RETENTION_POLICY = TO_BE_CONFIRMED`).
- **Task 1J**: Docker Compose runtime topology: API / worker / PostgreSQL / RabbitMQ / Flowable placeholder (*COMPLETE / FROZEN* — Task 1J.1 Runtime Topology & Configuration Foundation *COMPLETE / FROZEN*; Task 1J.1A Worker Runtime Fail-Closed Correction *COMPLETE / FROZEN*; Canonical Task 1J.2 Live Integration = `COMPLETE / FROZEN`; Task 1J.2A PostgreSQL Runtime Verification = `COMPLETE / FROZEN`; Task 1J.2B RabbitMQ Runtime Verification = `COMPLETE / FROZEN`; Task 1J.2B.1 Consumer Runner Runtime Verification = `COMPLETE / FROZEN`; Task 1J.3A Worker Composition Readiness Audit = `COMPLETE / FROZEN`; Task 1J.3B Outbox-Only Worker Runtime = `COMPLETE / FROZEN`; Task 1J.3C Live Outbox Worker Verification = `COMPLETE / FROZEN`; Canonical Task 1J.3 = `COMPLETE / FROZEN`; Task 1J.4 Runtime Closure & Evidence Reconciliation = `COMPLETE`; Canonical Task 1J = `COMPLETE / FROZEN`).
- **Task 1K**: OpenTelemetry / observability foundation (*COMPLETE / FROZEN* — Task 1K.1 Observability Readiness Audit = `COMPLETE / FROZEN`; Task 1K.1A Observability Compliance Persistence & Cardinality Correction = `COMPLETE / FROZEN`; Task 1K.2 Core OpenTelemetry SDK, Configuration & Log Correlation = `COMPLETE / FROZEN`; Task 1K.3 API & Worker Telemetry Lifecycle Integration = `COMPLETE / FROZEN`; Task 1K.4 FastAPI Request Tracing = `COMPLETE / FROZEN`; Task 1K.5 Outbox / Worker Custom Telemetry = `COMPLETE / FROZEN`; Task 1K.6 Collector + Prometheus Local Observability Runtime = `COMPLETE / FROZEN`; Canonical Task 1K = `COMPLETE / FROZEN`).

> [!NOTE]
> **Phase 1 Overall Status**: `COMPLETE / FROZEN` (Tasks 1A–1K COMPLETE / FROZEN; Phase 1 Foundation Lifecycle = COMPLETE / FROZEN).
> - `PRODUCTION_EVENT_HANDLER_STATUS = NOT_AVAILABLE`
> - `INBOUND_CONSUMER_RUNTIME_STATUS = DEFERRED_MISSING_PRODUCTION_EVENT_HANDLER`
> - `JOB_READ_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_TENANT_AND_DB_DEPENDENCIES`
> - `IDENTITY_TENANT_CREDENTIAL_PERSISTENCE = DEFERRED_REQUIRES_EXPLICIT_DATA_MODEL_TASK`
> - `WORKER_RUNTIME_STATUS = OUTBOX_ONLY_LIVE_VERIFIED`
> - `WORKER_HEALTHCHECK = PROCESS_ONLY`
> - `WORKER_DEFAULT_COMPOSE_START = DISABLED_BY_PROFILE`
> - `OBSERVABILITY_RUNTIME_STATUS = LOCAL_LIVE_VERIFIED`
> - `HTTP_QUERY_VALUE_EXPORT = FORBIDDEN`
> - `OUTBOX_METRIC_RESOURCE_FIELDS_AS_LABELS = FORBIDDEN`
> - `OUTBOX_ERROR_CATEGORY_VALUES = publish_error, timeout, database_error, unexpected`
> - `PHASE_2_ENGINEERING_READINESS = READY`
>
> *(Note: Phase 2 was NOT started in this batch. Phase 1 foundation is cleanly closed and frozen.)*

> [!IMPORTANT]
> **Identity / Tenant Persistence Models**: Persistence models for tenants, principals, external identity mappings, service credentials, and roles/permissions are classified as:
> `DEFERRED — REQUIRES EXPLICIT DATA-MODEL TASK`
> They are NOT assigned to Task 1E, 1F, or 1G, and will be introduced through a separately reviewed implementation task when their canonical data model is ready.
