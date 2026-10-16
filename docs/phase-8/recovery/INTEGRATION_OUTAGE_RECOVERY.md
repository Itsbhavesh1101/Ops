# Operational Runbook: External Integration Outage & Boundary Recovery

## 1. Symptom
- External dependencies (Company MySQL ERP, Google Calendar API, E-8 Notification Gateway, AI Model Provider, or OpenTelemetry Collector) return errors, timeout, or become unreachable.

## 2. Impact
- ERP / MySQL: Read-only upstream synchronization halted. Local cached entities remain available; zero write mutations attempted.
- Google Calendar: Calendar event reconciliation pauses; outbound webhook sync queued.
- E-8 Notification: Outbound engagement notifications held in outbox; zero business logic rollback.
- Model Provider: Task intelligence planner triggers fallback model or returns clean classified error.
- OpenTelemetry: Telemetry export dropped; ZERO impact on business transactions.

## 3. Preconditions
- Local E-7 core runtime and PostgreSQL are operating normally.

## 4. Safe Recovery Steps by Subsystem

### 4.1 ERP / MySQL Outage Recovery
- **Architecture**: E-7 operates as a strictly read-only consumer of ERP state.
- **Recovery Action**:
  1. Verify connection status: `SELECT 1` via ERP adapter health probe.
  2. When ERP endpoint restores, trigger incremental sync: `app.integrations.erp.adapter.sync_incremental()`.
  3. Status governance remains: `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES`.

### 4.2 Google Calendar Outage Recovery
- **Architecture**: Authenticated transport with backoff and 410 full resync.
- **Recovery Action**:
  1. For HTTP 429 / 5xx: Backoff retry occurs automatically via `GoogleCalendarTransport`.
  2. For HTTP 410 Gone: The `GoogleCalendarAdapter` invalidates the expired `syncToken` and executes an automatic full resync to rebuild event ledger.
  3. Status governance remains: `GOOGLE_CALENDAR_INTEGRATION = PASS / FROZEN`.

### 4.3 E-8 Notification Gateway Outage Recovery
- **Architecture**: Provider-neutral `NotificationDispatcherProtocol` with durable outbox.
- **Recovery Action**:
  1. Dispatches failing with `NotificationDispatchError` remain in outbox retry queue.
  2. When E-8 endpoint returns, workers drain notification outbox.
  3. Status governance remains: `E8_NOTIFICATION_INTEGRATION = BLOCKED_ON_E8_ENDPOINT`.

### 4.4 Model Provider (LLM) Failure Recovery
- **Architecture**: `PydanticAiPlanner` with configured fallback provider.
- **Recovery Action**:
  1. Primary provider timeout/failure automatically invokes configured fallback model (`fallback_model`).
  2. If both fail, `PlannerInferenceError` is surfaced cleanly with zero secret leakage.
  3. Retry inference once upstream provider restores.

### 4.5 OpenTelemetry Collector Outage Recovery
- **Architecture**: Async background batch exporter with non-blocking buffer.
- **Recovery Action**:
  1. Restart OTel collector: `docker restart e7-otel-collector`.
  2. Verify collector port 4318 (HTTP) or 4317 (gRPC).
  3. Telemetry export automatically resumes without restarting E-7 API or worker.

## 5. Verification Steps
- ERP: Confirm `ErpConnectionError` clears and read queries succeed.
- Google Calendar: Check sync state and webhook reconciliation logs.
- E8: Check outbound notification queue count.
- Planner: Check telemetry meter `e7.planner.fallback_used`.
- OTel: Verify span exports appear in Jaeger / Prometheus.

## 6. Rollback / Escalation
- Never attempt manual database mutations on upstream ERP or Google Calendar.
- For prolonged model provider outages, switch default model profile in environment settings.

## 7. Data-Loss Considerations
- All integration boundaries decouple remote transport from canonical PostgreSQL storage. Outages cause delay, not data loss.

## 8. Observability Checks
- Telemetry meters:
  * `e7.calendar.sync.duration`
  * `e7.notifications.dispatched`
  * `e7.planner.inference.duration`

## 9. Secret-Handling Warning
> [!WARNING]
> External integration credentials (Google service account keys, ERP credentials, LLM API keys) must be loaded through secure environment secrets.
> Do NOT log request/response bodies from external providers containing PII or tokens.
