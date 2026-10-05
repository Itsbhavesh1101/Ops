# Phase 1 Observability Architecture & Compliance Specification

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1K.1 / Task 1K.1A — OpenTelemetry Observability Readiness, Compliance & Contract Freeze  
**Status**: `FROZEN`  

---

## 1. Purpose & Observability Scope

This specification freezes the canonical observability architecture for the **IR Infotech E-7 Operations Engine** across API and worker runtimes. It establishes how OpenTelemetry integrates with the existing Phase-1 foundation without:
- Duplicating application correlation mechanisms (`correlation_id` remains authoritative).
- Leaking sensitive credentials, tenant secrets, personal identity data, or query bind parameters.
- Introducing a second logging stack (existing `structlog` pipeline is preserved and enriched).
- Hardcoding collector hostnames or exporter endpoints (all driven by Pydantic settings).
- Coupling E-7 to proprietary observability vendor backends.
- Prematurely instrumenting unimplemented domain entities (approval, solver, LLM metrics remain deferred).
- Violating frozen runtime semantics or breaking database isolation (zero telemetry database tables).

---

## 2. Verified License Compliance Baseline

All candidate telemetry components have been verified from official upstream repository `LICENSE` sources under Apache License 2.0 (`Apache-2.0`):

| Component | Upstream Repository | Verified License | Provenance File | Adoption Role |
| :--- | :--- | :---: | :--- | :--- |
| **OpenTelemetry Python API / SDK** | `open-telemetry/opentelemetry-python` | `Apache-2.0` | `third_party/licenses/LICENSE.opentelemetry.txt` | In-process core telemetry runtime (`opentelemetry-api`, `opentelemetry-sdk`) |
| **OpenTelemetry Python Contrib** | `open-telemetry/opentelemetry-python-contrib` | `Apache-2.0` | `third_party/licenses/LICENSE.opentelemetry.txt` | In-process FastAPI instrumentation (`opentelemetry-instrumentation-fastapi`) |
| **OpenTelemetry OTLP HTTP Exporter** | `open-telemetry/opentelemetry-python` | `Apache-2.0` | `third_party/licenses/LICENSE.opentelemetry.txt` | In-process protobuf/HTTP exporter (`opentelemetry-exporter-otlp-proto-http`) |
| **OpenTelemetry Collector Contrib** | `open-telemetry/opentelemetry-collector-contrib` | `Apache-2.0` | `third_party/licenses/LICENSE.opentelemetry.txt` | Standalone service container (`otel/opentelemetry-collector-contrib`) |
| **Prometheus Server** | `prometheus/prometheus` | `Apache-2.0` | `third_party/licenses/LICENSE.prometheus.txt` | Standalone time-series metrics container (`prom/prometheus`) |

> [!NOTE]
> Grafana visualization is NOT part of Phase 1 and remains deferred to Phase 7 (`Phase 7 — Analytics & Operations`).

---

## 3. Target Runtime Topology

```
+-------------------+      +----------------------+
| e7-api (FastAPI)  |      | e7-worker (Outbox)   |
|   OTel Python SDK |      |   OTel Python SDK    |
+---------+---------+      +----------+-----------+
          |                           |
          | OTLP / HTTP (Protobuf)    | OTLP / HTTP (Protobuf)
          +-------------+-------------+
                        |
                        v
         +------------------------------+
         |   OpenTelemetry Collector    |
         |  (otel-collector-contrib)    |
         +--------------+---------------+
                        |
          +-------------+-------------+
          |                           |
          v                           v
  [Local Trace Sink]         [Prometheus Exporter :8889]
  (Debug / File log)                  ^
                                      | Scrapes (:8889)
                             +--------+---------+
                             |   Prometheus     |
                             |   (Time-series)  |
                             +------------------+
```

1. **In-Process Applications**: Both `e7-api` and `e7-worker` initialize the OpenTelemetry Python SDK explicitly and emit traces and metrics via OTLP over HTTP/protobuf.
2. **Collector Service Boundary**: An OpenTelemetry Collector container receives OTLP on port `4318`, routes traces to a local debug sink, and exposes a Prometheus-compatible scrape endpoint on port `8889`.
3. **Prometheus Scraping**: A standalone Prometheus container scrapes the Collector. The headless worker process exposes zero inbound HTTP ports.
4. **Structured Logging**: Local application logging continues to be handled exclusively by `structlog`, formatted as JSON in production and enriched with `trace_id` and `span_id` when an active span is present.

---

## 4. Architecture Decisions & Contracts

### 4.1 OTLP Transport
- **Decision**: `OTLP_HTTP_PROTOBUF` via `opentelemetry-exporter-otlp-proto-http`.
- **Rationale**: Eliminates gRPC native C-extension dependencies, complex compilation steps, and async loop multiplexing friction. HTTP/protobuf is fully supported by the OTel SDK for both traces and metrics.

### 4.2 Explicit Programmatic Instrumentation
- **Decision**: Explicit programmatic initialization inside application startup lifecycles (`app/main.py` lifespan and `app/worker.py` startup).
- **Rationale**: Strictly forbids CLI magic (`opentelemetry-instrument`) or `sitecustomize.py` monkey patching. Guarantees deterministic lifecycle ordering, fine-grained attribute privacy, and API/worker parity.

### 4.3 Telemetry Configuration Source
- **Decision**: Project-prefixed Pydantic settings (`E7_OTEL_*`) loaded through `app/core/config.py`.
- **Rationale**: Avoids dual conflicting sources of truth. Pydantic settings validate and cleanly map `E7_OTEL_*` into SDK configuration.

### 4.4 Resource & Service Identity
- **API Service Name**: `e7-api` (configurable via `E7_OTEL_SERVICE_NAME`).
- **Worker Service Name**: `e7-worker` (configurable via `E7_OTEL_SERVICE_NAME` or runtime composition override).
- **Safe Resource Attributes**: `service.name`, `service.namespace` (`e7`), `service.version`, `deployment.environment`.
- **Forbidden in Resource**: `tenant_id`, `user_id`, `email`, credentials, `job_id`, `event_id`, request payloads.

### 4.5 Trace Sampling Policy
- **Decision**: `ParentBased(root=TraceIdRatioBased(ratio))` governed by `E7_OTEL_TRACE_SAMPLE_RATIO` (default `1.0` in local dev).
- **Rationale**: Generic ratio-based root sampling. Strictly no tenant-specific or business-specific sampling rules.

### 4.6 Correlation ID & Trace ID Separation
- `correlation_id` = Application-level cross-component correlation reference (frozen in Task 1B; extracted from `X-Correlation-ID` header or generated as UUIDv4; stored in `ContextVar`).
- `trace_id` = OpenTelemetry distributed 128-bit trace identifier.
- **Rule**: `trace_id` does NOT replace `correlation_id`. The active request span receives attribute `e7.correlation_id`. Structured logs receive `trace_id` and `span_id` when an active span exists.

### 4.7 Privacy & Redaction Boundaries
Telemetry spans and logs MUST NEVER capture:
- Headers: `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key`.
- Payloads: HTTP request/response bodies, `EventEnvelope` payloads.
- Database: Raw SQL query bind parameters.
- Secrets: Personal records, tenant API keys, credentials.
- AI Context: LLM prompts, completions, and model context.

### 4.8 FastAPI Instrumentation
- Package: `opentelemetry-instrumentation-fastapi`.
- Spans capture HTTP method, route template (e.g., `/api/v1/health`), and status code.
- Custom hook attaches `e7.correlation_id` from `get_correlation_id()`.
- Request and response body capture is disabled.

### 4.9 Database Instrumentation
- **Current Decision**: `DATABASE_INSTRUMENTATION = DEFERRED_PENDING_QUERY_PRIVACY_VALIDATION`.
- **Rationale**: Task 1K.2 focuses exclusively on core SDK, configuration, and log correlation. Automatic SQL statement instrumentation is deferred until a dedicated increment validates that SQL bind parameters are completely omitted and query construction embeds no sensitive literals.

### 4.10 RabbitMQ Instrumentation
- **Current Decision**: `RABBITMQ_INSTRUMENTATION = EXPLICIT_CUSTOM_SPANS`.
- **Rationale**: Phase 1 does not adopt RabbitMQ auto-instrumentation. E-7 prefers explicit custom spans around reviewed publisher (`RabbitMqEventPublisher.publish`), dispatcher (`OutboxDispatcher.dispatch_once`), and consumer boundaries because this preserves lifecycle ownership, privacy controls, frozen AMQP contracts, and explicit testability.

### 4.11 Broker Trace Context Propagation
- **Decision**: `TRACE_CONTEXT_BROKER_PROPAGATION = DEFERRED_CONTRACT_CHANGE`.
- **Rationale**: Frozen AMQP metadata and `EventEnvelope` contracts will not be silently modified in Phase 1. The existing `correlation_id` in `EventEnvelope.metadata` remains authoritative and sufficient for cross-service correlation in Phase 1.

---

## 5. Metric Cardinality & Label Contracts

### 5.1 Cardinality Principles
1. Metric label values must belong to closed, low-cardinality vocabularies.
2. **Strictly Forbidden Metric Labels**:
   - `tenant_id`, `principal_id`, `user_id`, `email`
   - `event_id`, `job_id`, `correlation_id`, `trace_id`, `span_id`
   - `event_type` (open namespaced string; not allowed in Phase 1 baseline)
   - Raw Python exception strings (`str(exc)`, `repr(exc)`)
   - Raw URL paths containing arbitrary IDs (route templates must be used instead)

### 5.2 Outbox Metric Contract (Phase-1 Baseline)
To prevent cardinality explosion from open event names, outbox metrics do NOT use `event_type` as a default label:

| Metric Name | Instrument | Labels | Semantics |
| :--- | :--- | :--- | :--- |
| `e7.outbox.dispatch.attempts` | Counter | *None* (or closed outcome) | Total count of dispatch attempts |
| `e7.outbox.dispatch.successes` | Counter | *None* | Count of successfully published events |
| `e7.outbox.dispatch.failures` | Counter | `error_category` *(Optional)* | Count of dispatch failures |
| `e7.outbox.dispatch.duration` | Histogram | *None* | Dispatch duration in seconds |

### 5.3 Error Label Policy
When an error-related label is retained on `e7.outbox.dispatch.failures`, it must draw strictly from a closed, low-cardinality enum:
- `publish_error`: Broker connection or publishing error
- `timeout`: Network or lock acquisition timeout
- `database_error`: Database transaction or claim error
- `unexpected`: Unclassified fatal error

Raw exception messages or class names are strictly forbidden.

### 5.4 API Metric Contract
Standard OpenTelemetry semantic conventions provided by FastAPI instrumentation:
- `http.server.request.duration`: Histogram (labels: `http.request.method`, `http.response.status_code`, `http.route`)
- `http.server.active_requests`: UpDownCounter (labels: `http.request.method`)

### 5.5 Deferred Domain Metrics
The following metric families are explicitly DEFERRED to subsequent phases:
- Approval turnaround time, milestone rate, blocker duration (Phase 3 Workflow)
- Schedule stability, solver execution duration, constraint violations (Phase 4 Scheduling)
- LLM token counts, costs, model version trackers (Phase 2 Task Intelligence)
- Workflow SLA breaches (Phase 3 Workflow)
Zero empty placeholder metrics will be created.

---

## 6. Docker Compose Observability Profile

Observability containers are isolated behind the optional `observability` Compose profile:
- `otel-collector`: Receives OTLP/HTTP on port `4318`; exposes metrics on port `8889`.
- `prometheus`: Scrapes `otel-collector:8889`.
- **Default Execution**: Standard `docker compose up` starts ONLY API, PostgreSQL, and RabbitMQ (and optional worker under `worker-runtime`). Zero observability containers run by default.
- **Grafana**: Strictly NOT included in Phase 1.

---

## 7. Startup & Shutdown Semantics

1. **Disabled Mode** (`E7_OTEL_ENABLED=false`):
   - Telemetry initializes as a no-op provider.
   - Zero background threads, zero network exporter sockets.
   - Existing `structlog` logging continues working without modification.
2. **Enabled Mode** (`E7_OTEL_ENABLED=true`):
   - Initializes `TracerProvider`, `MeterProvider`, and batch exporters.
   - Configuration errors fail fast during application startup.
   - Exporter network unreachability is non-blocking: background exporter retries and drops spans according to SDK buffer limits without blocking application transactions.
3. **Shutdown Ordering**:
   - API: FastAPI lifespan shutdown triggers bounded telemetry flush and shutdown (5-second timeout).
   - Worker: `OutboxDispatcherRunner.stop()` completes first -> Telemetry providers flush and shut down -> Database engine disposes -> Process exits.

---

## 8. Summary of Frozen Architecture Decisions

```
OBSERVABILITY_SDK = OPENTELEMETRY_PYTHON_SDK
TRACE_EXPORT = OTLP_HTTP_PROTOBUF
METRIC_EXPORT = OTLP_HTTP_PROTOBUF
LOG_EXPORT = DEFERRED_LOCAL_STRUCTURED_ONLY
LOCAL_COLLECTOR = OPENTELEMETRY_COLLECTOR_CONTAINER
LOCAL_PROMETHEUS = PROMETHEUS_CONTAINER
LOCAL_GRAFANA = DEFERRED_LATER_PHASE
OTLP_TRANSPORT = PROTOBUF_OVER_HTTP
TELEMETRY_CONFIGURATION_SOURCE = PROJECT_PREFIXED_PYDANTIC_SETTINGS
TRACE_SAMPLING_POLICY = PARENT_BASED_TRACE_ID_RATIO
DATABASE_INSTRUMENTATION = DEFERRED_PENDING_QUERY_PRIVACY_VALIDATION
RABBITMQ_INSTRUMENTATION = EXPLICIT_CUSTOM_SPANS
TRACE_CONTEXT_BROKER_PROPAGATION = DEFERRED_CONTRACT_CHANGE
OUTBOX_EVENT_TYPE_METRIC_LABEL = NOT_ALLOWED_IN_PHASE_1_BASELINE
```

---

## 9. Next Implementation Scope (Task 1K.2)

**TASK 1K.2 — CORE OPENTELEMETRY SDK, CONFIGURATION & LOG-CORRELATION FOUNDATION**
Scope is strictly confined to:
1. Adding core OpenTelemetry dependencies (`opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`).
2. Implementing `app/core/telemetry/` configuration, providers, and initialization lifecycle.
3. Implementing the structlog trace/span correlation processor.
4. Comprehensive unit tests covering settings, no-op behavior, provider lifecycle, and log correlation.

Task 1K.2 will NOT implement:
- FastAPI route auto-instrumentation.
- Database query instrumentation.
- RabbitMQ custom spans.
- Outbox metrics.
- Docker Compose Collector or Prometheus containers.
