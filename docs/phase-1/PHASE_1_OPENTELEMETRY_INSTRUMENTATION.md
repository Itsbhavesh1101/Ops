# Phase 1 — OpenTelemetry Instrumentation Specification

**Status**: FROZEN / COMPLETE  
**Module**: Core Telemetry & Runtime Instrumentation (`app.core.telemetry`, `app.main`, `app.events.outbox`, `app.events.publishers.rabbitmq`)  
**Tasks**: 1K.4 (FastAPI Tracing), 1K.5 (Outbox/Worker Telemetry)  
**Standard**: OpenTelemetry Python SDK (OTLP/HTTP Protobuf)

---

## 1. Executive Summary

Phase 1 completes foundational application telemetry for the IR Infotech E-7 Operations Engine. The telemetry architecture adopts an explicit, privacy-first, cardinality-safe approach:
1. **FastAPI Request Tracing**: Programmatic instrumentation using `opentelemetry-instrumentation-fastapi` with strict route template normalization, probe noise exclusion, and zero header/body/secret capture.
2. **Correlation ID Integration**: The canonical `e7.correlation_id` is attached to active server spans while preserving the existing correlation propagation mechanism.
3. **Outbox Runtime Telemetry**: Custom operational spans (`e7.outbox.dispatch`, `e7.event.publish`) around the outbox dispatcher and RabbitMQ publisher, with closed-vocabulary metric emissions.
4. **Derivative Execution Rule**: Telemetry failures (span creation, metric recording, export network timeout) are strictly isolated and never control or alter business state, database transaction boundaries, or outbox retry policies.

---

## 2. FastAPI Request Tracing (Task 1K.4)

### 2.1 Instrumentation Boundary
- **Package**: `opentelemetry-instrumentation-fastapi` (pinned in release family compatible with OpenTelemetry SDK 1.43+).
- **Activation**: Programmatic only within `app.main:lifespan` when `telemetry_runtime.is_enabled` is true. Automatic process bootstrap (`opentelemetry-instrument` CLI, `sitecustomize`) is strictly prohibited.
- **Deactivation**: `uninstrument_fastapi_app(app)` called during application shutdown in `lifespan` finally block.
- **Middleware Rebuild**: Following `FastAPIInstrumentor.instrument_app`, the Starlette middleware stack is explicitly rebuilt (`app.middleware_stack = app.build_middleware_stack()`) to ensure request interception within lifespan context.

### 2.2 Privacy & Cardinality Contracts
- **HTTP Query Value Export**:
  ```
  HTTP_QUERY_VALUE_EXPORT = FORBIDDEN
  ```
  Query parameter strings and values are strictly forbidden in telemetry. Request URL and target attributes contain path only, without `?query=value`. Query values are absent entirely, including URL-encoded equivalents.
- **Route Normalization**: Span names and the `http.route` attribute use route templates (e.g. `/`, `/api/v1/jobs/{job_id}`). Concrete URL paths containing identifiers are masked as `<unmatched>` if unmapped.
- **Query Parameter Exclusion**: Raw query parameter strings (`http.target`, `http.url`) are stripped; only the route template/path is recorded.
- **Header Capture**: Headers are NOT captured into span attributes. `Authorization`, `Cookie`, `X-API-Key`, and other security-sensitive headers are completely excluded.
- **Body Capture**: Neither request nor response payloads are recorded in traces.
- **Correlation ID**: The incoming or generated `X-Correlation-ID` is extracted by `CorrelationAndLoggingMiddleware` and attached to the active server span as `e7.correlation_id`.
- **Log Correlation**: Request logs automatically receive `correlation_id`, `trace_id`, and `span_id` through the Task 1K.2 logging processor.

### 2.3 Health Probe Exclusion
- `/api/v1/health` and `/api/v1/ready` endpoints are excluded from exported traces via `excluded_urls` configuration (`/api/v1/health,/api/v1/ready`).
- Endpoint availability and response semantics are completely untouched.

---

## 3. Outbox / Worker Custom Telemetry (Task 1K.5)

### 3.1 Custom Operational Spans
Explicit custom spans wrap high-value operational boundaries rather than using untargeted monkeypatching or broker auto-instrumentation:

| Span Name | Boundary | Safe Attributes | Forbidden Attributes |
|:---|:---|:---|:---|
| `e7.outbox.dispatch` | `OutboxDispatcher.dispatch_once` | `event_type`, `retry_attempt_count`, `e7.correlation_id`, `dispatch.outcome` | `payload`, `tenant_id`, `subject_id`, credentials, raw exception message |
| `e7.event.publish` | `RabbitMqEventPublisher.publish` | `event_type`, `broker_exchange_name`, `e7.correlation_id` | `payload`, `tenant_id`, broker credentials, amqp url |

### 3.2 Closed Metric Vocabulary & Label Contract
All metric families adhere strictly to the frozen Phase-1 metric contract:

```
OUTBOX_METRIC_RESOURCE_FIELDS_AS_LABELS = FORBIDDEN
OUTBOX_ERROR_CATEGORY_VALUES = publish_error, timeout, database_error, unexpected
```

Measurement attributes are strictly bounded:
- `e7.outbox.dispatch.attempts`: `{}` (NO metric labels)
- `e7.outbox.dispatch.successes`: `{}` (NO metric labels)
- `e7.outbox.dispatch.duration`: `{}` (NO metric labels)
- `e7.outbox.dispatch.failures`: either `{}` OR `{"error_category": <closed-value>}`

Resource attributes (`service.name`, `service.namespace`, `service.version`, `deployment.environment`) belong strictly to the OpenTelemetry Resource and must NEVER be attached as per-measurement metric dimensions.

| Metric Name | Instrument | Description | Allowed Attributes |
|:---|:---|:---|:---|
| `e7.outbox.dispatch.attempts` | Counter | Count of dispatch attempts initiated | None (`{}`) |
| `e7.outbox.dispatch.successes` | Counter | Count of successfully settled dispatches | None (`{}`) |
| `e7.outbox.dispatch.failures` | Counter | Count of failed dispatch attempts | `error_category` |
| `e7.outbox.dispatch.duration` | Histogram | Latency in seconds of complete dispatch | None (`{}`) |

#### Error Category Vocabulary
The `error_category` attribute on `e7.outbox.dispatch.failures` is strictly bounded to the following closed set:
- `publish_error`: Broker connection, channel, or exchange publication error.
- `timeout`: Network publication timeout exceeded.
- `database_error`: Database settlement or commit error.
- `unexpected`: Any other unexpected error during dispatch.

Raw exception class names, error strings, stack traces, and dynamic identifiers are forbidden as metric labels.

---

## 4. Telemetry Independence Invariant

Instrumentation is strictly derivative. Under all conditions:
1. If metric recording or span creation raises an exception, it is caught and suppressed.
2. A telemetry failure never marks an outbox event as failed.
3. A telemetry failure never causes duplicate publication.
4. Outbox database transactions and lease management proceed unaltered regardless of telemetry state.
5. In disabled mode (`E7_OTEL_ENABLED=false`), zero tracer/meter provider overhead is incurred and all wrappers execute as no-ops.

---

## 5. Architectural Deferrals Retained

| Capability | Phase 1 Status | Rationale |
|:---|:---|:---|
| SQLAlchemy Auto-Instrumentation | DEFERRED | Pending comprehensive SQL query privacy validation |
| W3C Broker Trace Context Propagation | DEFERRED | Requires cross-service event contract modification |
| Outbox `event_type` Metric Label | FORBIDDEN | Cardinality restriction in Phase 1 baseline |
| Worker `/metrics` HTTP Endpoint | FORBIDDEN | Worker remains headless; metrics exported via OTLP |
