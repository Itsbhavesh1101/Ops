# E-7 Operations Engine — E-8 Engagement Platform Team Handover

**Document:** Outbound Notification Integration Contract & Wire Protocol  
**Target Audience:** Enterprise E-8 Notification / Engagement Platform Team  
**System:** IR Infotech E-7 Operations Engine  
**Classification Key:** `E8_NOTIFICATION_INTEGRATION`  
**Current Status:** `BLOCKED_EXTERNAL`  
**Authoritative Architectural Decision:** [ADR-012 (Accepted)](file:///f:/operations-engine/docs/adr/ADR-012-e8-notification-boundary.md)  
**Security Posture:** Zero PII Leakage — Opaque Tokenized References Only  

---

## 1. Executive Summary & Architectural Boundary

Under functional requirement **FR-INT-01** and accepted **ADR-012**, the E-7 Operations Engine interfaces with the E-8 Notification & Engagement Subsystem to dispatch notifications when significant operational milestones occur (e.g. initial schedule publication, schedule repair deltas, workflow SLA escalations).

E-7 implements a strict division of responsibility:
* **E-7 Responsibility:** Determines *when* communication is warranted; compiles transport-neutral `NotificationCommand` payloads with minimal structured metadata and opaque recipient tokens; commits commands atomically within the PostgreSQL transactional outbox (`outbox_events`). E-7 never makes blocking external network calls during domain write operations.
* **E-8 Responsibility:** Owns all downstream engagement mechanics: channel selection (Email, SMS, WhatsApp, push), template expansion, quiet-hours filtering, recipient contact resolution (mapping opaque tokens to phone/email), delivery retries, and delivery status tracking.
* **Clean-Room Boundary:** E-7 passes only opaque recipient references (`<opaque_staff_ref>`) and operational IDs. Raw personal contact info (personal emails, phone numbers) and raw MySQL primary keys never cross the boundary.
* **Non-Mocking Rule:** Zero fake or mock E-8 services will be placed in production paths.

---

## 2. Dual-Transport Architecture (ADR-012 Alignment)

E-7 implements a provider-neutral notification boundary behind [NotificationDispatcherProtocol](file:///f:/operations-engine/app/integrations/engagement/dispatcher.py#L26-L40), preserving PostgreSQL transactional outbox authority (`outbox_events`):

### 2.1 Default Production Transport: AMQP / RabbitMQ Message Broker
* **Canonical Default Exchange:** `e8.notifications` (durable topic exchange).
* **Documented Alternative Exchange:** `e7.events` (under topic routing `{tenant_id}.notification.{trigger_kind}`).
* **Prohibited Ad-Hoc Names:** `notifications.events.topic` is NOT canonical and is prohibited without an accepted ADR.
* **Routing Key Pattern:** `{tenant_id}.notification.{trigger_kind}` (e.g. `tenant-prod-001.notification.schedule_published`).
* **Broker Semantics:** Publisher confirms (`publisher_confirms=True`), persistent delivery mode (`delivery_mode=2`), and durable exchanges.
* **Advantages:** Decoupled asynchronous delivery, high throughput, durable message buffering during E-8 maintenance windows, zero network latency impact on user transactions.
* **Application Config:** `E7_NOTIFICATION_TRANSPORT="broker"`, `E7_NOTIFICATION_EXCHANGE="e8.notifications"`.

### 2.2 Configurable Alternative Transport: Authenticated HTTP / REST Gateway
* **Architecture:** When configured with an endpoint URL, the outbox worker delivers `NotificationCommand` payloads via HTTP `POST` to the E-8 REST gateway using [HttpNotificationDispatcher](file:///f:/operations-engine/app/integrations/engagement/dispatcher.py#L42-L112).
* **Actual Implemented Headers:**
  * `Content-Type`: `application/json`
  * `X-Command-ID`: `<command.command_id>` (for idempotent deduplication)
  * `X-Correlation-ID`: `<command.correlation_reference>` (for distributed tracing)
  * `Authorization`: `Bearer <E7_NOTIFICATION_API_KEY>` (if API key is configured)
* **Proposed Enhancements Requiring E-8 Confirmation:**
  * HMAC request signing (`X-E7-Signature-SHA256`) and mutual TLS (mTLS) are **unsupported proposals** in the current baseline. If required by corporate SecOps, an ADR and code implementation will be formulated after E-8 team review.
* **Application Config:** `E7_NOTIFICATION_TRANSPORT="http"`, `E7_NOTIFICATION_ENDPOINT_URL="https://<e8-endpoint>/api/v2/notifications"`, `E7_NOTIFICATION_API_KEY="<secret>"`.

> [!IMPORTANT]
> **Action Required from E-8 Platform Team:**
> 1. Confirm whether E-8 will consume notifications via **AMQP topic exchange** (`e8.notifications` or `e7.events`) or via **HTTP REST webhook**.
> 2. Confirm if Bearer token authorization on HTTP is sufficient or if HMAC signing is required as a future enhancement.

---

## 3. Data Contract: `NotificationCommand` Specification

Outbound commands adhere strictly to the following typed JSON payload structure:

```json
{
  "command_id": "notif-cmd-550e8400-e29b-41d4-a716-446655440000",
  "correlation_reference": "corr-sch-pub-99887766",
  "organization_scope_reference": "tenant-prod-001",
  "recipient_references": [
    "urn:e7:tenant-prod-001:staff:fac-00142",
    "urn:e7:tenant-prod-001:cohort:cs-2026-grade10a"
  ],
  "notification_context": {
    "template_id": "tpl_schedule_published_v1",
    "schedule_id": "sch-2026-fall-v1",
    "academic_session": "2026-2027",
    "effective_date": "2026-10-01",
    "affected_periods_count": 42,
    "repair_delta_summary": null
  },
  "trigger": {
    "trigger_kind": "schedule_published",
    "source_event_name": "SchedulePublishedEvent",
    "source_entity_id": "sch-2026-fall-v1",
    "occurred_at": "2026-10-23T00:30:00Z"
  }
}
```

### Field Definitions:
* `command_id` (string, UUIDv4): Deterministic command identifier derived from the source causation event.
* `correlation_reference` (string): Distributed correlation ID for end-to-end tracing across E-7 and E-8.
* `organization_scope_reference` (string): Canonical tenant identifier.
* `recipient_references` (list of strings): Opaque canonical URNs identifying target faculty or cohort groups. Zero plaintext email addresses or phone numbers.
* `notification_context` (object): Minimal structured metadata required for template expansion.
* `trigger` (object): Event trigger details including trigger kind and timestamp.

---

## 4. Idempotency & Replay Protection

To ensure reliability across network retries, network partitions, and broker restarts:
1. **Deterministic `command_id`:** Every notification command generated from a specific operational event carries the same deterministic `command_id`.
2. **HTTP Deduplication:** If using HTTP transport, E-8 should respond with `HTTP 409 Conflict` (or `HTTP 200/201/202` with an idempotency indicator) when receiving a previously processed `command_id`. E-7 recognizes `HTTP 409` as successful deduplication and records the event as safely delivered.
3. **AMQP Deduplication:** If using AMQP broker transport, E-8 consumer must track processed `command_id` entries to discard duplicate deliveries without duplicating user-facing messages.

---

## 5. Failure Semantics & Outbox Recovery

* **Outage Resilience:** When E-8 or the broker is unreachable, outbound commands remain safely buffered in the PostgreSQL `outbox_events` table with status `PENDING`.
* **Bounded Retries:** The outbox worker retries unacknowledged events with exponential backoff and maximum retry thresholds.
* **Audit Trail:** Every delivery attempt (success, timeout, HTTP error, or broker publish error) is immutably logged to `audit_events` under `event_type="notification.dispatched"`.

---

## 6. Handover Checklist & Information Required

The E-8 Platform Team is requested to deliver:

1. **Transport Selection:** [ ] AMQP Broker OR [ ] HTTP REST Webhook.
2. **If AMQP:**
   - [ ] Cluster connection FQDN and port (`5671` TLS / `5672`).
   - [ ] Dedicated Virtual Host (`vhost`).
   - [ ] Dedicated publisher credentials (username/password).
   - [ ] Confirmation of exchange name (`e8.notifications`) and topic type.
3. **If HTTP REST:**
   - [ ] Ingress endpoint URL (e.g. `https://<e8-gateway>/api/v2/notifications`).
   - [ ] Authentication Bearer token or mTLS CA certificate.
   - [ ] SLA confirmation: latency < 3000ms; HTTP status codes (200/201/202 for accept, 409 for duplicate).
4. **Data Contract Sign-Off:**
   - [ ] Confirmation that `NotificationCommand` schema matches E-8 template expansion requirements.

---

## 7. Status Transition Criteria

* Delivery of transport confirmation and corresponding credentials/endpoints transitions:  
  `E8_NOTIFICATION_INTEGRATION` from `BLOCKED_EXTERNAL` to `PASS`.
