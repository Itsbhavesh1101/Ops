# Phase 1 OpenTelemetry Core SDK & Configuration Foundation

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1K.2 — Core OpenTelemetry SDK, Configuration & Log-Correlation Foundation  
**Status**: `COMPLETE`  

---

## 1. Executive Summary

Task 1K.2 implements the core OpenTelemetry Python SDK foundation for the **IR Infotech E-7 Operations Engine**. It establishes the configuration models, Resource construction, TracerProvider and MeterProvider lifecycle management, OTLP/HTTP exporter setup, and structlog trace correlation pipeline while strictly preserving zero-overhead disabled defaults, application correlation independence, and strict privacy boundaries.

---

## 2. Frozen Architecture Reference

This implementation adheres strictly to the frozen decisions established in Task 1K.1 / Task 1K.1A:

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

## 3. Packages Adopted & Version Compatibility

Only the minimum direct runtime dependencies required for the core SDK and OTLP/HTTP export were added to `pyproject.toml`:

| Direct Dependency | Locked / Min Version | Upstream License | Role |
| :--- | :---: | :---: | :--- |
| `opentelemetry-api` | `>=1.43.0` | Apache-2.0 | Core OpenTelemetry API and tracer/meter protocols |
| `opentelemetry-sdk` | `>=1.43.0` | Apache-2.0 | Core SDK implementation, BatchSpanProcessor, Resource |
| `opentelemetry-exporter-otlp-proto-http` | `>=1.43.0` | Apache-2.0 | Protobuf over HTTP push exporters for traces & metrics |

**Transitive Dependencies Resolved**: `opentelemetry-proto==1.43.0`, `opentelemetry-semantic-conventions==0.64b0`, `opentelemetry-exporter-otlp-proto-common==1.43.0`, `protobuf`, `requests`.

**Strictly Excluded from Task 1K.2**:
- `opentelemetry-instrumentation-fastapi` (Deferred to Task 1K.3)
- `opentelemetry-instrumentation-sqlalchemy` (Deferred pending query privacy validation)
- `opentelemetry-instrumentation-aio-pika` (Not adopted; custom spans preferred)
- `prometheus-client` / `opentelemetry-exporter-prometheus` (Not required; Prometheus scrapes Collector)
- Collector and Prometheus service containers (Deferred to Task 1K Compose integration)

---

## 4. Telemetry Configuration Contract (`TelemetrySettings`)

Implemented in `app/core/telemetry/config.py`:

```python
class TelemetrySettings(BaseSettings):
    otel_enabled: bool = False
    otel_service_name: str | None = None
    otel_service_namespace: str = "e7"
    otel_exporter_otlp_endpoint: str | None = None
    otel_trace_sample_ratio: float = 1.0
    otel_export_timeout_seconds: float = 10.0
    otel_metric_export_interval_seconds: float = 60.0
```

### 4.1 Environment Mapping
- All fields map to environment variables prefixed with `E7_`.
- `E7_OTEL_ENABLED=false` by default for zero-overhead local development.
- When `E7_OTEL_ENABLED=true`:
  - `E7_OTEL_SERVICE_NAME` is mandatory (e.g. `e7-api` or `e7-worker`).
  - `E7_OTEL_EXPORTER_OTLP_ENDPOINT` is mandatory (e.g. `http://localhost:4318`).

### 4.2 Endpoint Validation & Signal Paths
- Scheme must be `http` or `https`.
- Netloc must be present.
- Userinfo / credentials in URLs are rejected and never printed in logs or errors (`hide_input_in_errors=True`).
- Base endpoint cannot end with `/v1/traces` or `/v1/metrics`.
- Signal-specific properties append deterministically:
  - `traces_endpoint` -> `<base>/v1/traces`
  - `metrics_endpoint` -> `<base>/v1/metrics`

---

## 5. Resource Construction & Attribute Privacy

Implemented in `app/core/telemetry/resource.py`:

Allowed Resource Attributes:
- `service.name`: Logical service identifier (`e7-api`, `e7-worker`)
- `service.namespace`: Platform namespace (`e7`)
- `service.version`: Semantic version string derived from package metadata via `importlib.metadata.version("operations-engine")` (fallback: `"0.1.0"`)
- `deployment.environment`: Derived from canonical application configuration (`Settings.environment`)

**Strictly Forbidden in Resource Attributes**:
`tenant_id`, `user_id`, `email`, `principal_id`, `api_key`, `job_id`, `event_id`, `correlation_id`, request payloads.

---

## 6. Trace Sampling Policy

Configured via `otel_trace_sample_ratio` (`0.0 <= ratio <= 1.0`):
- Root sampler: `ParentBased(TraceIdRatioBased(ratio))`
- When remote parent span is sampled, child is sampled (`remoteParentSampled: AlwaysOnSampler`).
- Root spans sample according to configured ratio (1.0 = 100% sampling).
- Zero tenant or user-specific sampling rules.

---

## 7. Runtime Lifecycle & Provider Ownership

Implemented in `app/core/telemetry/runtime.py`:

### 7.1 Encapsulation
`TelemetryRuntime` encapsulates:
- `is_enabled`: Boolean flag
- `tracer_provider`: Configured `TracerProvider` with `BatchSpanProcessor`
- `meter_provider`: Configured `MeterProvider` with `PeriodicExportingMetricReader`
- `is_shutdown`: Boolean flag tracking shutdown state

### 7.2 Disabled Mode
When `E7_OTEL_ENABLED=false`:
- Zero exporters constructed.
- Zero network I/O or background threads.
- Global OTel providers are NOT registered.
- Safe disabled `TelemetryRuntime` returned.

### 7.3 Global Provider Registration & Duplicate Guard
- `initialize_telemetry` explicitly sets `trace.set_tracer_provider` and `metrics.set_meter_provider` exactly once.
- Subsequent calls raise `TelemetryInitializationError("OpenTelemetry runtime has already been initialized in this process")`.
- `build_telemetry_runtime` acts as a pure factory without mutating process-global state, enabling safe unit testing.

### 7.4 Bounded Shutdown
- `shutdown()` force-flushes and shuts down `TracerProvider` and `MeterProvider` within a bounded timeout (`otel_export_timeout_seconds`).
- Preserves primary exceptions and raises them on cleanup failures.

---

## 8. Structlog Trace Correlation Pipeline

Implemented in `app/core/telemetry/logging.py` and integrated into `app/core/logging.py`:

### 8.1 Processor Order
```
1. structlog.contextvars.merge_contextvars   (merges correlation_id)
2. structlog.stdlib.add_logger_name
3. structlog.stdlib.add_log_level
4. structlog.stdlib.PositionalArgumentsFormatter()
5. structlog.processors.TimeStamper(fmt="iso", utc=True)
6. structlog.processors.StackInfoRenderer()
7. structlog.processors.format_exc_info
8. add_otel_trace_context                    (injects trace_id and span_id if span active)
9. censor_sensitive_data_processor           (redacts passwords, tokens, API keys)
10. JSONRenderer / ConsoleRenderer           (single final serialization)
```

### 8.2 Trace ID vs Correlation ID Separation
- `correlation_id`: Application cross-service reference (UUIDv4 or client header; stored in `correlation_id_ctx`).
- `trace_id`: OpenTelemetry distributed trace identifier (32-character lowercase hex string).
- `span_id`: OpenTelemetry span identifier (16-character lowercase hex string).
- Logs can output all three fields concurrently. `correlation_id` is never replaced or derived from `trace_id`.

### 8.3 Privacy Guarantees
- `add_otel_trace_context` injects ONLY `trace_id` and `span_id`.
- Never creates spans.
- Never injects headers, payloads, baggage, or span attributes into logs.
- Sensitive data redaction (`censor_sensitive_data_processor`) executes after trace enrichment.

---

## 9. What is NOT Implemented Yet (Deferred to Subsequent Tasks)

- **Task 1K.3**: FastAPI HTTP route instrumentation & middleware integration.
- **Task 1K.4**: Database query instrumentation validation.
- **Task 1K.5**: Outbox custom dispatch spans & low-cardinality metrics.
- **Task 1K.6**: Docker Compose Collector and Prometheus container profile.
- **Application Entrypoints**: `app/main.py` and `app/worker.py` remain untouched; telemetry initialization will be wired during entrypoint composition tasks.

---

## 10. Database & Migration Stability

- Database tables: Exactly 5 (`audit_events`, `outbox_events`, `inbox_messages`, `idempotency_records`, `jobs`).
- Alembic head: Exactly `0007`. Zero database migrations added. Zero telemetry data stored in PostgreSQL.
