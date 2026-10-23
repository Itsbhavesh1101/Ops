# E-7 Operations Engine — E-8 Engagement Platform Team Handover

**Document:** Outbound Notification Integration Contract & Wire Protocol  
**Target Audience:** Enterprise E-8 Notification / Engagement Platform Team  
**System:** IR Infotech E-7 Operations Engine  
**Classification Key:** `E8_NOTIFICATION_INTEGRATION`  
**Current Status:** `BLOCKED_EXTERNAL`  
**Authoritative Architectural Decisions:** [ADR-005 (Accepted)](file:///f:/operations-engine/docs/adr/ADR-005-event-broker.md), [ADR-012 (Accepted)](file:///f:/operations-engine/docs/adr/ADR-012-e8-notification-boundary.md)  
**Security Posture:** Zero PII Leakage — Opaque Tokenized References Only  

---

## 1. Executive Summary & Verification Matrix

Under functional requirement **FR-INT-01**, **ADR-005**, and accepted **ADR-012**, the E-7 Operations Engine interfaces with the E-8 Notification & Engagement Subsystem to dispatch notifications when significant operational milestones occur (e.g., initial schedule publication, schedule repair deltas, workflow SLA escalations).

E-7 maintains a strict division of responsibility:
* **E-7 Responsibility:** Determines *when* communication is warranted; compiles transport-neutral `NotificationCommand` payloads with minimal structured metadata and opaque recipient tokens; commits commands atomically within the PostgreSQL transactional outbox (`outbox_events`). E-7 never makes blocking external network calls during domain write operations.
* **E-8 Responsibility:** Owns all downstream engagement mechanics: channel selection (Email, SMS, WhatsApp, mobile push), template expansion, quiet-hours filtering, recipient contact resolution (mapping opaque tokens to phone/email), delivery retries, and delivery status tracking.
* **Clean-Room Boundary:** E-7 passes only opaque recipient references (`<opaque_staff_ref>`) and operational IDs. Raw personal contact info (personal emails, phone numbers) and raw MySQL primary keys never cross the boundary.
* **Non-Mocking Rule:** Zero fake or mock E-8 services will be placed in production paths.

### 1.1 Implementation & Verification Status Matrix

| Subsystem / Capability | Governance & Scope | Status | Verification Evidence / Reference |
| :--- | :--- | :---: | :--- |
| **`NotificationCommand` Pydantic Contract** | Typed payload schema, version `1.0.0` | `IMPLEMENTED` | `app/integrations/engagement/models.py` |
| **Data Minimization & PII Filter** | Rejection of student PII, salaries, passwords, raw MySQL IDs | `INTERNALLY_VERIFIED` | 14 test cases in `tests/unit/integrations/test_e8_notification_dispatcher.py` |
| **Transactional Outbox Durability** | Atomic DB enqueue, lease-based claim, settlement | `INTERNALLY_VERIFIED` | 33 tests in `tests/unit/test_outbox_dispatcher.py`, `tests/integration/rabbitmq/test_outbox.py` |
| **AMQP RabbitMQ Dispatcher** | Topic exchange `e8.notifications`, routing `{tenant}.notification.{kind}` | `INTERNALLY_VERIFIED` | `AmqpNotificationDispatcher` in `app/integrations/engagement/dispatcher.py` |
| **Publisher Confirms & Message Persistence** | AMQP persistent delivery mode (2), mandatory routing verification | `INTERNALLY_VERIFIED` | Unit tests in `test_e8_notification_dispatcher.py` |
| **HTTP REST Gateway Dispatcher** | Configurable alternative `POST /v1/notifications/commands` | `INTERNALLY_VERIFIED` | `HttpNotificationDispatcher` in `app/integrations/engagement/dispatcher.py` |
| **Dispatcher Factory & Configuration** | `get_notification_dispatcher()` with strict transport validation | `INTERNALLY_VERIFIED` | Factory tests in `test_e8_notification_dispatcher.py` |
| **Consumer Inbox Deduplication** | PostgreSQL `inbox_messages` idempotent processing | `INTERNALLY_VERIFIED` | Handlers in `test_phase6_components.py`, `test_phase6_vertical_slice.py` |
| **Opaque Recipient URN Defaults** | `urn:e7:cohort:faculty`, `urn:e7:cohort:faculty-all` | `INTERNALLY_VERIFIED` | Handlers in `app/integrations/engagement/handlers.py` |
| **Audit Log Emission** | `notification.dispatched` in `audit_events` on attempt outcome | `INTERNALLY_VERIFIED` | Shared `_record_dispatch_audit` in `dispatcher.py` |
| **Live Company RabbitMQ Broker** | Production corporate RabbitMQ cluster handshake | `BLOCKED_EXTERNAL` | Pending corporate broker URL, credentials, and vhost provisioning |
| **Live Company E-8 Gateway** | Production E-8 HTTP REST gateway handshake | `BLOCKED_EXTERNAL` | Pending corporate endpoint FQDN and API token / mTLS CA |
| **Downstream Template Expansion & Delivery** | E-8 internal email/SMS/WhatsApp/push delivery | `NOT_IMPLEMENTED` | Strictly E-8 owned functionality outside E-7 codebase boundary |
| **E-8 Delivery Receipts & Engagement Tracking** | Upstream webhook/callback to E-7 | `NOT_IMPLEMENTED` | Unresolved in Phase-0 contract (`UNRESOLVED_NOT_YET_REQUIRED`) |

---

## 2. Dual-Transport Architecture (ADR-012 Alignment)

E-7 implements a provider-neutral notification boundary behind [NotificationDispatcherProtocol](file:///f:/operations-engine/app/integrations/engagement/dispatcher.py), preserving PostgreSQL transactional outbox authority (`outbox_events`):

### 2.1 Default Production Transport: AMQP / RabbitMQ Message Broker (`AmqpNotificationDispatcher`)
* **Canonical Default Exchange:** `e8.notifications` (durable topic exchange).
* **Documented Alternative Exchange:** `e7.events` (under topic routing `{tenant_id}.notification.{trigger_kind}`).
* **Prohibited Ad-Hoc Names:** `notifications.events.topic` is NOT canonical and is prohibited without an accepted ADR.
* **Routing Key Pattern:** `{tenant_id}.notification.{trigger_kind}` (e.g., `tenant-prod-001.notification.schedule_published`).
  - Derived from: `command.trigger.source_event_name.replace('.', '_')` (e.g. `schedule_published`) or `command.trigger.business_condition.lower()`.
* **Broker Semantics:** Publisher confirms (`publisher_confirms=True`), persistent delivery mode (`delivery_mode=2`), and durable exchanges.
* **Advantages:** Decoupled asynchronous delivery, high throughput, durable message buffering during E-8 maintenance windows, zero network latency impact on user transactions.
* **Application Config:** `E7_NOTIFICATION_TRANSPORT="broker"`, `E7_NOTIFICATION_EXCHANGE="e8.notifications"`, `E7_NOTIFICATION_RABBITMQ_URL="amqp://user:pass@host:5672/vhost"`.

### 2.2 Configurable Alternative Transport: Authenticated HTTP / REST Gateway (`HttpNotificationDispatcher`)
* **Architecture:** When configured with an endpoint URL, the dispatcher delivers `NotificationCommand` payloads via HTTP `POST` to the E-8 REST gateway.
* **Actual Implemented Headers:**
  * `Content-Type`: `application/json`
  * `X-Command-ID`: `<command.command_id>` (for idempotent deduplication)
  * `X-Correlation-ID`: `<command.correlation_reference>` (for distributed tracing)
  * `Authorization`: `Bearer <E7_NOTIFICATION_API_KEY>` (if API key is configured)
* **Application Config:** `E7_NOTIFICATION_TRANSPORT="http"`, `E7_NOTIFICATION_ENDPOINT_URL="https://<e8-endpoint>/v1/notifications/commands"`, `E7_NOTIFICATION_API_KEY="<secret>"`.

---

## 3. Data Contract: `NotificationCommand` Specification

Outbound commands adhere strictly to the typed JSON payload structure:

```json
{
  "contract_version": "1.0.0",
  "command_id": "notif-cmd-550e8400-e29b-41d4-a716-446655440000",
  "correlation_reference": "corr-sch-pub-99887766",
  "causation_reference": "evt-sch-pub-11223344",
  "organization_scope_reference": "tenant-prod-001",
  "recipient_references": [
    "urn:e7:cohort:faculty",
    "urn:e7:tenant-prod-001:cohort:cs-2026-grade10a"
  ],
  "notification_context": {
    "template_id": "EXAM_SCHEDULE_PUBLISHED",
    "schedule_id": "sch-2026-fall-v1",
    "version": 1
  },
  "trigger": {
    "trigger_kind": "SOURCE_VERIFIED_EVENT",
    "source_event_name": "schedule.published",
    "source_entity_reference": "sch-2026-fall-v1"
  },
  "requested_at": "2026-10-23T00:30:00Z"
}
```

### 3.1 Field Definitions & Constraints:
* `contract_version` (string, mandatory): Schema semantic version (`"1.0.0"`).
* `command_id` (string, mandatory): Stable, deterministic logical identifier derived from causation event (e.g. `notif-pub-{event_id}`).
* `correlation_reference` (string, mandatory): Correlation ID linking the notification to the top-level business workflow.
* `causation_reference` (string, optional): ID of the specific event or action causing this dispatch.
* `organization_scope_reference` (string, mandatory): Canonical tenant ID ensuring strict multi-tenant boundary isolation.
* `recipient_references` (array of strings, mandatory, min_length=1): Opaque canonical URNs identifying target cohorts or roles. Zero plaintext personal emails or mobile phone numbers.
* `notification_context` (object, mandatory): Minimal structured metadata. Rejects student PII, employee compensation, authentication secrets, and raw upstream MySQL database IDs.
* `trigger` (object, mandatory): Provenance metadata containing `trigger_kind` (`SOURCE_VERIFIED_EVENT` or `SOURCE_BACKED_BUSINESS_CONDITION`), along with `source_event_name` or `business_condition`.
* `requested_at` (string, mandatory): ISO-8601 UTC timestamp.

---

## 4. Idempotency & Replay Protection

To ensure reliability across network retries, network partitions, and broker restarts:
1. **Deterministic `command_id`:** Every notification command generated from a specific operational event carries the same deterministic `command_id` (e.g., `notif-pub-{event_id}`).
2. **HTTP Deduplication:** If using HTTP transport, E-8 should respond with `HTTP 409 Conflict` (or `HTTP 200/201/202` with an idempotency indicator) when receiving a previously processed `command_id`. E-7 recognizes `HTTP 409` as successful deduplication and records the event as safely delivered (`return False`).
3. **AMQP Deduplication:** If using AMQP broker transport, E-8 consumer must track processed `command_id` entries to discard duplicate deliveries without duplicating user-facing messages.
4. **Durable At-Least-Once Delivery:** In accordance with ADR-005 transactional outbox guarantees, E-7 does not suppress outbound retries using volatile in-memory tracking. Deduplication is owned durably by the E-8 consumer using `command_id` (or HTTP 409 duplicate acceptance for HTTP transport). This ensures outbox recovery is never suppressed following database settlement failures or process restarts.

---

## 5. Failure Semantics & Outbox Recovery

* **Outage Resilience:** When E-8 or the broker is unreachable, outbound commands remain safely buffered in the PostgreSQL `outbox_events` table with status `PENDING` (`published_at IS NULL`).
* **Bounded Retries:** The outbox worker retries unacknowledged events with deterministic exponential backoff and maximum retry thresholds.
* **Audit Trail:** Every delivery attempt (success, timeout, HTTP error, or broker publish error) is immutably logged to `audit_events` under `event_type="notification.dispatched"`.

---

## 6. Exact Information Required from Company E-8 Platform Team

The E-8 Platform Team is requested to deliver the following 12 items to unblock production integration:

1. **Supported Transport:**
   - Confirmation of production transport selection: [ ] AMQP Message Broker (ADR-012 default) OR [ ] HTTP REST Webhook (configurable alternative).
2. **RabbitMQ Topology Details (if AMQP):**
   - Cluster FQDN and ports (`5671` TLS / `5672` non-TLS).
   - Dedicated Virtual Host (`vhost`).
   - Confirmation of exchange name (`e8.notifications`) and type (`topic`, durable).
   - Confirmation of routing key pattern: `{tenant_id}.notification.{trigger_kind}`.
3. **Broker Authentication Requirements:**
   - Dedicated service account credentials (username/password or x509 client certificate).
   - Publish-only authorization scoped to exchange `e8.notifications`.
4. **Network Access Requirements:**
   - VPC peering, private endpoint, or security group rules allowing egress from E-7 VPC CIDR to broker / gateway ports.
5. **NotificationCommand Schema Agreement:**
   - Formal sign-off on `NotificationCommand` schema version `1.0.0` for template binding.
6. **Consumer Idempotency Guarantees:**
   - Confirmation that the E-8 consumer deduplicates messages by `command_id` (or header `X-Command-ID`).
7. **Failure and Retry Contract:**
   - SLA for message processing acknowledgment; confirmation that duplicate submissions return idempotently without error.
8. **HTTP Gateway Details (if HTTP):**
   - Ingress endpoint URL (e.g. `https://<e8-gateway>/v1/notifications/commands`).
   - Expected status codes (`200`/`201`/`202` for queued/accepted, `409` for idempotent duplicate).
   - Response latency SLA (< 3000ms).
9. **Authentication & TLS Requirements:**
   - Bearer token / API secret (`E7_NOTIFICATION_API_KEY`) or institutional CA certificate bundle for mTLS.
   - Minimum TLS version (TLS 1.3 preferred, TLS 1.2 minimum).
10. **Observability & Acknowledgment Requirements:**
    - Confirmation of distributed tracing headers: `X-Command-ID`, `X-Correlation-ID`.
    - Logging and tracing convention alignment.
11. **Integration-Test Environment:**
    - Staging broker vhost or staging webhook endpoint for end-to-end integration verification prior to production cutover.
12. **Production Acceptance Criteria:**
    - Live end-to-end verification showing operational trigger in E-7 resulting in successful notification delivery in E-8 staging.

---

## 7. Status Transition Criteria

* Canonical blocker `E8_NOTIFICATION_INTEGRATION` remains `BLOCKED_EXTERNAL`.
* Delivery of transport confirmation, connection credentials, and successful staging handshake transitions `E8_NOTIFICATION_INTEGRATION` from `BLOCKED_EXTERNAL` to `PASS`.

