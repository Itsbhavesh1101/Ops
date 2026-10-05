# Phase 1 — Observability Runtime Specification

**Status**: FROZEN / COMPLETE  
**Module**: Local Collector & Prometheus Runtime Topology  
**Task**: 1K.6 (Observability Runtime Profile)  
**Artifacts**: `docker-compose.yml`, `docker/observability/otel-collector.yaml`, `docker/observability/prometheus.yaml`

---

## 1. Overview

The E-7 Operations Engine includes an optional, zero-secret, local observability runtime profile for developer verification, load testing, and integration validation. The runtime consists of an OpenTelemetry Collector and Prometheus server, configured to ingest and export telemetry from the FastAPI API service and Background Outbox Worker.

---

## 2. Docker Compose Profile Boundary

Observability services are isolated behind the `observability` Compose profile:
```bash
# Default Compose excludes observability and worker containers:
docker compose up

# Observability profile activates Collector and Prometheus:
docker compose --profile observability up

# Full runtime verification activates API, Worker, Collector, and Prometheus:
docker compose --profile worker-runtime --profile observability up
```

### 2.1 Services Topology

| Service | Image | Profile | Internal Port | Host Port (Configurable) |
|:---|:---|:---|:---|:---|
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.100.0` | `observability` | `4318` (OTLP/HTTP), `8889` (Prometheus exporter) | `${OTEL_COLLECTOR_HTTP_PORT:-4318}` |
| `prometheus` | `prom/prometheus:v2.51.0` | `observability` | `9090` (Web UI & Scrape engine) | `${PROMETHEUS_PORT:-9090}` |

### 2.2 Intentional Deferrals
- **Grafana**: DEFERRED to later phases. No dashboards or Grafana containers in Phase 1.
- **Worker Direct Scrape**: FORBIDDEN. Worker runs headless with zero exposed HTTP ports; metrics are pushed via OTLP/HTTP to the Collector.
- **External SaaS Exporters**: FORBIDDEN. No cloud vendor endpoints or third-party keys are permitted.
- **Dedicated Telemetry Database**: FORBIDDEN. No timeseries DB (Timescale, InfluxDB, ClickHouse) added.

---

## 3. Component Configurations

### 3.1 OpenTelemetry Collector (`docker/observability/otel-collector.yaml`)
- **Receivers**: OTLP HTTP receiver listening on `0.0.0.0:4318`.
- **Processors**: Batch processor configured with `timeout: 1s` and `send_batch_size: 256` for responsive local testing.
- **Exporters**:
  - `debug`: Detailed logging of received spans and metrics to stdout for container log inspection.
  - `prometheus`: HTTP server on `0.0.0.0:8889` exposing Prometheus-compatible metrics scraped by the Prometheus service.
- **Pipelines**:
  - `traces`: `receivers: [otlp] -> processors: [batch] -> exporters: [debug]`
  - `metrics`: `receivers: [otlp] -> processors: [batch] -> exporters: [debug, prometheus]`

### 3.2 Prometheus (`docker/observability/prometheus.yaml`)
- **Global Settings**: `scrape_interval: 2s`, `evaluation_interval: 2s` for local testing agility.
- **Scrape Targets**: Exclusively scrapes `otel-collector:8889`.
- **Direct Application Scrapes**: None. API and worker are never scraped directly by Prometheus.

---

## 4. Application Configuration

Both `api` and `worker` services in `docker-compose.yml` configure telemetry via standard environment variables with safe defaults:

```yaml
E7_OTEL_ENABLED: ${E7_OTEL_ENABLED:-false}
E7_OTEL_EXPORTER_OTLP_ENDPOINT: ${E7_OTEL_EXPORTER_OTLP_ENDPOINT:-http://otel-collector:4318}
E7_OTEL_SERVICE_NAME: e7-api # (or e7-worker)
```

In default execution (`E7_OTEL_ENABLED=false`), the SDK initialization exits immediately, and no network connections to the collector are attempted.

---

## 5. Security & Privacy Guarantees

1. **Zero Secrets**: Neither configuration file nor the Compose topology contains API keys, passwords, or production credentials.
2. **Private Network Routing**: All OTLP and scrape traffic flows through the internal Compose bridge network (`operations-engine_default`).
3. **No External Egress**: Collector and Prometheus containers do not make external internet requests during runtime.

---

## 6. Observability Policy Constraints

The local observability runtime operates under strict Phase-1 constraints:
- `HTTP_QUERY_VALUE_EXPORT = FORBIDDEN`: Query parameter strings and values are completely absent from span and trace attributes.
- `OUTBOX_METRIC_RESOURCE_FIELDS_AS_LABELS = FORBIDDEN`: Resource attributes (`service.name`, `service.namespace`) belong exclusively to the Resource and are forbidden as per-measurement metric labels.
- `OUTBOX_ERROR_CATEGORY_VALUES = publish_error, timeout, database_error, unexpected`: Metric failure classifications are strictly constrained to this 4-value vocabulary.
