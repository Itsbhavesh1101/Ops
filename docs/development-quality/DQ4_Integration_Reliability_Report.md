# E-7 Operations Engine — Phase DQ-4 Completion Report
## Integration Reliability, Calendar Synchronization, Messaging, and External Boundaries

**Prepared:** 28 October 2026  
**Repository:** `F:\operations-engine`  
**GitHub Tracking Remote:** `https://github.com/Itsbhavesh1101/Ops`  
**Working Feature Branch:** `quality/dq4-integration-reliability`  
**Target Base Branch:** `master`  
**Base Commit SHA:** `8936513ab70f3238db16843fe0b6bef0026c364b` (merged PR #3 into `master`)  
**Existing Pull Request:** [PR #4](https://github.com/Itsbhavesh1101/Ops/pull/4) (Status: OPEN, unmerged)  
**Reviewed GitHub Commit SHA:** `2a14dfcf64b74724c041890ff347c9293f110e5d`  
**Frozen Phase-8 RC Tag:** `phase8-rc-38d080fadab8` (`03e6305256db3d838b472aaac50893ddb513414e`) — intact and unaltered.  
**Exit Gate Verdict:** `DQ4_RELIABILITY_CORRECTIONS_COMPLETE_ARCHITECTURAL_DECISION_PENDING`

---

## 1. Executive Summary

Phase DQ-4 completes the integration correctness, calendar synchronization, messaging reliability, and external boundary verification for the IR Infotech E-7 Operations Engine. Following independent review and subsequent runtime integration defect verification of PR #4 (`2a14dfcf64b74724c041890ff347c9293f110e5d`), this report incorporates all corrective actions, empirical evidence, and architectural evaluations across:

1. **Baseline and Metadata Accuracy:** Accurate GitHub commit SHA tracking (`2a14dfcf64b74724c041890ff347c9293f110e5d`), open PR #4 confirmation, and verified frozen Phase-8 RC tag integrity (`03e6305256db3d838b472aaac50893ddb513414e`).
2. **RabbitMQ Topic Exchange Routing Key Alignment:** Fixed binding key mismatch between canonical publisher (`routing_key=envelope.event_type`) and notification worker bindings. Added direct canonical 2-word and 3-word event types (`schedule.published`, `schedule.changed`, `task.blocked`, `workflow.task.created`, `workflow.sla.warning`) alongside wildcard variants, eliminating silent message drops in RabbitMQ topic exchanges.
3. **Cross-Tenant SLA Evaluation Isolation (Task 1):** `WorkflowService.evaluate_sla_triggers(tenant_id)` was corrected to validate `tenant_id` and explicitly forward it to `SlaService.evaluate_triggers(tenant_id=tenant_id)`. Evaluation is strictly tenant-scoped: Tenant A evaluates only Tenant A's SLA states; Tenant B's tasks are completely skipped; zero cross-tenant warning or breach events or audit records are emitted. Empty or whitespace-only tenant contexts fail closed with `ValueError`.
4. **SLA Transaction Atomicity, Durability & Worker Restart Constraints (Task 2):** Implemented `SlaService.transaction(tenant_id)` context manager capturing a deep snapshot of task SLA states. If an outer database transaction (`uow.commit()`) fails, the snapshot is restored, preserving `warning_fired_at = None` and `breach_fired_at = None` so that subsequent evaluation retries succeed. The worker restart limitation of in-memory `_states` is formally documented, confirming that surviving service crashes requires an approved durable `task_sla_states` table.
5. **Notification Blocking, Transactional Inbox Deduplication, and ACK Semantics (Task 3):** When recipient resolution is unavailable under `DEC-E8-02`, handlers append a durable append-only audit record (`event_type="notification.dispatch.blocked"`, `outcome=DENIED`) to PostgreSQL inside the transactional session and return cleanly. This commits the `inbox_messages` marker and triggers RabbitMQ `basic_ack`, preventing poison-message infinite retry loops while preserving audit evidence and enabling operational replay via audit logs.
6. **Notification Policy Provenance & Authority Status (Task 4):** Formally distinguished approved architectural requirements from provisional defaults (`urn:e7:role:task_manager`, `urn:e7:role:workflow_assignee`, `urn:e7:role:escalation_manager`, `urn:e7:cohort:faculty`, `urn:e7:cohort:faculty-all`, `TASK_BLOCKED_ALERT`, `WORKFLOW_TASK_ASSIGNED`, `WORKFLOW_SLA_WARNING`), documenting their dependency on pending institutional governance decisions `DEC-E8-02` and `DEC-E8-04`.
7. **Zero-PII Validation Errors via Model Config & Exception Sanitization (Task 5):** Configured `hide_input_in_errors=True` on `NotificationCommand` and removed all prohibited input string interpolation from `ValueError` messages in `app/integrations/engagement/models.py`. Rejected emails, phone numbers, SSNs, and MySQL IDs are completely redacted from exception text, verified by negative regression tests.
8. **Real Integration Infrastructure & Limitations (Task 6):** Confirmed local environment status (Docker Desktop inactive, port 5672 not listening). Verified AMQP bindings and consumer pipelines using unit/component test doubles without claiming live broker connectivity.
9. **Precise ADR-012 Decision Evidence (Task 7):** Exhaustive transaction boundary analysis evaluating Options A, B, and C across 10 formal evaluation dimensions, maintaining `AD-DQ4-01` status as **PENDING**.
10. **Accurate Institutional ERP Schema:** Formal correction of the active 11-table read-only schema (`sch_settings`, `sessions`, `classes`, `sections`, `class_sections`, `subjects`, `staff`, `department`, `staff_designation`, `student_session`, `subject_timetable`) and 4 conditional support tables.
11. **Transparent Test Evidence:** Precise distinction between 2,293 globally discovered tests, 1,933+ tests executed in CI, 6 skipped, and 146 frontend Vitest tests.

---

## 2. Baseline Verification

1. **Pull Request #3 Verification:**
   - **PR Number:** #3
   - **Title:** `feat(dq3): complete frontend operations workflows and analytics`
   - **State:** `MERGED`
   - **Merge Commit SHA:** `8936513ab70f3238db16843fe0b6bef0026c364b`
   - **Merge Timestamp:** 2026-10-28T08:24:57Z
2. **Master Branch State:**
   - Base commit on `master`: `8936513ab70f3238db16843fe0b6bef0026c364b`.
   - All DQ-1, DQ-2, and DQ-3 changes are verified present and intact.
3. **Existing Pull Request #4 Status:**
   - **PR Number:** #4
   - **URL:** `https://github.com/Itsbhavesh1101/Ops/pull/4`
   - **Target Branch:** `master`
   - **Source Branch:** `quality/dq4-integration-reliability`
   - **Reviewed Commit SHA:** `b24786a9d5bb5c084a72c6f9947dacadc30b95df`
   - **PR State:** `OPEN` (Must remain unmerged; no second PR created).
4. **Phase-8 RC Tag Integrity:**
   - **Tag Name:** `phase8-rc-38d080fadab8`
   - **Target Commit SHA:** `03e6305256db3d838b472aaac50893ddb513414e`
   - **Verification:** Tag target is verified unaltered.
5. **Working Tree Cleanliness:**
   - Verified clean branch checkout on `quality/dq4-integration-reliability`.

---

## 3. Integration Inventory

The E-7 Operations Engine runtime coordinates eight principal integration boundaries:

```
[Domain State Transitions]
         │
         ▼ (Atomic ACID Commit)
   [PostgreSQL UoW]
   ├── Business Entities (Goals, Plans, Schedules, Jobs)
   └── Outbox Events (e7.events / outbox_events table)
         │
         ▼ (Polling / Lease Worker via SELECT FOR UPDATE SKIP LOCKED)
   [Outbox Dispatcher]
         │
         ▼ (Persistent AMQP with Publisher Confirms)
   [RabbitMQ Broker] ── Exchange: e7.events (Topic)
         │
         ├── Queue: e7.events.notifications ──► [NotificationWorker] ──► [E-8 Engagement Boundary]
         ├── Queue: e7.events.scheduler     ──► [SchedulerWorker]    ──► [OR-Tools Solver Engine]
         └── Queue: e7.events.calendar      ──► [CalendarWorker]     ──► [Google Calendar REST v3]
```

### Complete Runtime Integration Map

| Integration Subsystem | Triggering Domain Operation | DB Transaction Boundary | Persisted Entity / Event | Outbox / Dispatch Ownership | Broker Routing | Downstream Consumer / Worker | Inbox / Idempotency Boundary | Acknowledgment Timing | Retry & Recovery Semantics | Tenant & Correlation Scope | Observability & Diagnostics |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **1. Transactional Outbox** | Goal/Plan/Schedule/Task updates | PostgreSQL ACID UoW | Domain records + `outbox_events` | `OutboxDispatcher` background service | AMQP exchange `e7.events` | RabbitMQ broker queues | N/A (Outbox is source) | Upon publisher confirm from RabbitMQ | Exponential backoff (1s-60s), max 5 attempts, DLQ | Strict `tenant_id`, immutable `correlation_id` & `causation_id` | Structlog with masked credentials; Prometheus outbox metrics |
| **2. Transactional Inbox** | Inbound AMQP message | PostgreSQL ACID UoW | `inbox_messages` | Inbound AMQP consumer | Direct / Topic bindings | Domain event handlers | Unique constraint on `(tenant_id, message_id)` | Post-commit ACK to RabbitMQ channel | Rejection without requeue for poison messages; requeue on transient failures | Strict `tenant_id` match required; cross-tenant mismatch rejected | Structured error logging; rejection counter metrics |
| **3. RabbitMQ Broker** | Event publishing or consumption | Transport layer | AMQP 0-9-1 persistent delivery mode 2 | `RabbitMqEventPublisher` | Exchanges: `e7.events`, `e8.notifications` | `RabbitMqMessageConsumer` & runner | Broker prefetch = 10, manual acks | Publisher confirms before marking outbox event DELIVERED | Auto-reconnect with jittered backoff, channel recreation | Per-tenant message headers; routing key pattern: `{tenant_id}.{domain}.{action}` | AMQP connection heartbeats; dead-letter routing to `e7.events.dlq` |
| **4. E-8 Notifications** | Schedule published / Task blocked / SLA warning / Workflow task | PostgreSQL ACID UoW | Domain event -> `NotificationCommand` | `NotificationWorker` / `E8NotificationDispatcher` | Exchange `e8.notifications` or direct HTTP | E-8 Notification Engine | Deterministic `command_id = notif-{type}-{event_id}` | After successful HTTP 202 or AMQP confirm | Bounded retries (3 attempts), circuit breaker, fail-closed | Strict tenant scoping; pseudonymous role references (zero PII) | Full causation logging; secret-masked error traces |
| **5. Google Calendar Sync** | Outbound schedule publish / Inbound webhook push | PostgreSQL ACID UoW | `calendar_links`, `calendar_watch_channels` | `CalendarWorker` / `GoogleCalendarAdapter` | Webhook HTTP POST `/api/v1/integrations/calendar/google/webhook` | `InboundCalendarSyncService` | Webhook message number & SHA-256 token validation | Immediate HTTP 200/202 upon validation | RFC 7523 JWT token refresh; exponential backoff on 429/5xx | Isolated watch state per tenant calendar link | Webhook security audit logging; channel expiration monitoring |
| **6. Institutional ERP** | Read-only sync of academic catalog & faculty | Read-Only DB Transaction | Synthetic cache / Domain staging | `MySqlIreduAdapter` (11 active tables) | Direct MySQL connection pool | Sync service | Idempotent upsert of canonical entities | Transaction commit | 3 bounded retries on transient connection error; fail-closed on schema drift | Enforced `single_tenant_id` scope check | SQL query latency logs; connection pool health probes |
| **7. Workflow Engine (Flowable)** | Process start / Task action | Flowable Engine REST API | Flowable process/task state + E-7 UoW | `FlowableWorkflowAdapter` | HTTP / REST | Flowable runtime container | `idempotency_records` table keyed by client token | Post-response from Flowable engine | Circuit breaker on Flowable outage; graceful degraded response | Strict tenant namespace in process definitions | Scrubbed variable payload logging; SLA breach audit |
| **8. Solver Engine (OR-Tools)** | Schedule job submission | PostgreSQL ACID UoW | `jobs`, `schedules`, `timetable_assignments` | `SchedulingService` / `JobRunner` | Thread pool solver | CP-SAT solver process | Idempotent job transition state machine | Job completion commit | Bounded solver time limit (e.g. 30s-120s); fallback to repair | Strict tenant scoping on all resource queries | Solver search diagnostics; gap and penalty score metrics |

### Active vs Unused Code Distinction

- **Active in Production:**
  - `app/events/outbox/` (models, repository, dispatcher, worker)
  - `app/events/inbox/` (models, repository, deduplication logic)
  - `app/events/publishers/rabbitmq.py` (production publisher)
  - `app/events/consumers/rabbitmq_runner.py` (production consumer daemon)
  - `app/integrations/engagement/` (E-8 notification worker and dispatcher)
  - `app/integrations/calendar/` (Google Calendar adapter, OAuth2 JWT token provider, webhook handler, watch channel manager)
  - `app/integrations/erp/mysql_adapter.py` (`MySqlIreduAdapter` - primary 11-table read-only adapter)
  - `app/workflow/adapters/flowable/` (Flowable REST integration)
  - `app/scheduling/` (OR-Tools CP-SAT scheduling service and job state machine)
- **Isolated Utility / Staging Code:**
  - `app/events/adapters/` (`EventBrokerAdapter` - in-memory mock broker used for standalone unit testing without RabbitMQ)
  - `app/integrations/unitime/` (UniTime CPSolver benchmark adapter - experimental/evaluation only, guarded by skipif checks)

---

## 4. Transactional Outbox Correctness

1. **Atomic Domain State and Event Persistence:**
   In `SqlAlchemyUnitOfWork`, domain record changes and outbox event records are staged on the same `AsyncSession`. A single database `COMMIT` executes both atomically. If an exception occurs, the transaction rolls back, leaving no committed event or partial domain state.
2. **Claim and Lease Mechanics:**
   Outbox workers poll for pending events using `SELECT ... FOR UPDATE SKIP LOCKED` ordered by `created_at ASC`. Concurrent dispatch workers running in parallel are partitioned by row-level locks; no two workers can claim the same outbox event.
3. **Publisher Confirms & Delivery Settlement:**
   Events are transitioned from `CLAIMED` to `DELIVERED` only after receiving RabbitMQ publisher confirm ACKs. If publisher confirms fail or timeout, the event remains retryable.
4. **Bounded Retries & Poison Message Quarantine:**
   Each dispatch failure increments `attempt_count` with exponential backoff. Upon reaching `max_retries` (default 5), the event transitions to `DEAD_LETTER` status, preventing infinite reprocessing loops.
5. **Tenant Isolation:**
   Every outbox event is strictly bound to an authenticated `tenant_id`. Cross-tenant events cannot be injected into outbox streams.

---

## 5. RabbitMQ Publisher and Consumer Reliability

1. **Durable Exchanges and Queues:**
   All core exchanges (`e7.events`, `e8.notifications`) are declared durable (`durable=True`). All queues are declared durable with `auto_delete=False`.
2. **Persistent Delivery Mode:**
   Messages are published with `delivery_mode=2` (persistent), ensuring RabbitMQ flushes message envelopes to disk.
3. **Consumer Settlement & Dead-Letter Routing:**
   Consumers use explicit manual acknowledgments (`basic_ack`, `basic_nack`). Unrecoverable serialization or contract validation errors are rejected without requeue (`requeue=False`), routing messages directly to the dead-letter exchange (`e7.events.dlx`). Transient database timeouts trigger NACK with requeue up to max retries.
4. **Prefetch Backpressure:**
   Consumer channels configure `basic_qos(prefetch_count=10)`, preventing worker memory exhaustion under traffic spikes.

---

## 6. Transactional Inbox and Idempotency

1. **Unique Deduplication Constraint:**
   The `inbox_messages` table enforces a database-level unique constraint on `(tenant_id, message_id)`.
2. **Atomic Processing Boundaries:**
   The insertion of the inbox record and the execution of resulting domain mutations share the same database transaction. A worker crash before commit rolls back both; redelivered messages are safely reprocessed. A crash after commit but before RabbitMQ ACK is detected on redelivery by the existing inbox record, sending an ACK without duplicate processing.
3. **Multi-Tenant State Collision Defense:**
   Message IDs are composite-scoped by `(tenant_id, message_id)`. Messages from different tenants with identical message IDs cannot collide or cause cross-tenant suppression.

---

## 7. E-8 Recipient Privacy Boundary Verification

Following review findings, recipient privacy enforcement in `app/integrations/engagement/models.py` has been strengthened in strict adherence to Section 16 of `E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md`:

### Enforced Prohibitions

1. **Plaintext Email Addresses:**
   Strictly rejected via `@` presence and email pattern regex validation (e.g. `faculty-lead@school.edu`, `student@domain.com`).
2. **Plaintext Phone Numbers:**
   Strictly rejected across E.164, local, and formatted digit sequences with length $\ge 7$ (e.g. `+1-555-0199`, `9876543210`).
3. **Unauthorized Personal Identifiers:**
   Strictly rejected keywords and prefixes: `student_name`, `student_ssn`, `student_dob`, `student_phone`, `student_email`, `social_security`, `national_id`, `passport`, `first_name`, `last_name`, `full_name`, `ssn:`, `dob:`.
4. **Raw Institutional MySQL Primary Keys & Prefixes:**
   Strictly rejected pure numeric integer strings (e.g. `"104"`, `"42"`) and upstream table prefixes (e.g. `"students.id: 104"`, `"staff.id:20"`, `"mysql:staff:5"`, `"iredu:student:99"`, `"raw_id:501"`).
5. **Malformed References:**
   Strictly rejected strings exceeding 256 characters, containing ASCII control characters, or containing SQL/command injection delimiters (`;`, `--`, `/*`).

### Authorized Canonical Formats Preserved

- Canonical URNs: `urn:e7:cohort:faculty`, `urn:e7:cohort:faculty-all`, `urn:e7:role:task_manager`, `urn:e7:role:escalation_manager`.
- Canonical user references: `user:principal_alpha`, `usr-101`, `invigilator-001`.
- Opaque pseudonyms: `<opaque_recipient_ref_1>`.
- Canonical UUID strings: `c56a4180-65aa-42ec-a945-5fd21dec0538`.

### Test Evidence

All negative and positive scenarios pass across 63 test cases in `tests/unit/integrations/test_e8_notification_dispatcher.py`, covering both HTTP and AMQP transport paths, regression tests for recipient validation, and runtime defect repairs.

---

## 8. Source-Verified E-8 Events & `schedule.changed` Classification

### Canonical Event Verification Matrix

| Event Name | Trigger Kind | Canonical Classification | Producer Module | Durable Persistence | Published Broker Routing Key | Consumer Queue Bindings | Consumer Handler | Recipient Reference Resolution | Idempotency Derivation |
|---|---|---|---|---|---|---|---|---|---|
| `task.blocked` | `SOURCE_VERIFIED_EVENT` | **Source-Verified** | Task Execution Service | PostgreSQL `outbox_events` | `task.blocked` | `task.blocked`, `*.task.blocked` | `TaskBlockedNotificationHandler` | Opaque role reference `urn:e7:role:task_manager` (Provisional, `DEC-E8-02`) | `notif-blk-{event_id}` |
| `workflow.task.created` | `SOURCE_VERIFIED_EVENT` | **Source-Verified** | Flowable Adapter / Workflow Service | PostgreSQL `outbox_events` | `workflow.task.created` | `workflow.task.created`, `*.workflow.task.created` | `WorkflowTaskCreatedNotificationHandler` | Opaque user reference `user:{assignee}` or `urn:e7:role:workflow_assignee` (Provisional, `DEC-E8-02`) | `notif-wf-{event_id}` |
| `workflow.sla.warning` | `SOURCE_VERIFIED_EVENT` | **Source-Verified** | SLA Monitoring Engine | PostgreSQL `outbox_events` | `workflow.sla.warning` | `workflow.sla.warning`, `*.workflow.sla.warning` | `WorkflowSlaWarningNotificationHandler` | Opaque role reference `urn:e7:role:escalation_manager` (Provisional, `DEC-E8-02`) | `notif-sla-{event_id}` |
| `schedule.published` | `SOURCE_VERIFIED_EVENT` | **Source-Verified** | Scheduling Domain Service | PostgreSQL `outbox_events` | `schedule.published` | `schedule.published`, `*.schedule.published` | `SchedulePublishedNotificationHandler` | Explicit opaque recipients in payload; or `urn:e7:cohort:faculty` (Provisional, `DEC-E8-02`); blocked if unresolved | `notif-pub-{event_id}` |
| `schedule.changed` | `SOURCE_BACKED_BUSINESS_CONDITION` | **Derived / Proposed** | Scheduling Repair Service | PostgreSQL `outbox_events` | `schedule.changed` | `schedule.changed`, `*.schedule.changed` | `ScheduleChangedNotificationHandler` | Explicit opaque recipients in payload; or `urn:e7:cohort:faculty-all` (Provisional, `DEC-E8-02`); blocked if unresolved | `notif-chg-{event_id}` |

### Classification of `schedule.changed`

In strict alignment with `E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md` Section 7, `schedule.changed` is classified as `TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION` with condition `SCHEDULE_CHANGE_NOTICE`. It is **not** classified as `SOURCE_VERIFIED_EVENT` because the contract defines source-verified events as primary canonical transitions, whereas schedule change notifications represent derived business conditions evaluated across schedule versions.

### Runtime Integration Defect & Reliability Corrections (Tasks 1–7)

1. **RabbitMQ Topic Exchange Routing Alignment (Task 1):**
   - The canonical publisher (`RabbitMqEventPublisher.publish()`) publishes domain events with `routing_key=envelope.event_type`.
   - On AMQP topic exchanges, wildcard binding keys of the form `*.{event_type}` require exactly 3 dot-separated segments. Consequently, published 2-word event types (`schedule.published`, `schedule.changed`, `task.blocked`) were dropped by the broker when only wildcard bindings were present.
   - `NotificationWorker.rabbitmq_consumer_binding_keys` has been updated to include both direct canonical keys (`schedule.published`, `schedule.changed`, `task.blocked`, `workflow.task.created`, `workflow.sla.warning`) and prefixed variants (`*.schedule.published`, etc.), ensuring zero message drops while maintaining backwards compatibility.
   - Verified via unit regression test `test_rabbitmq_binding_keys_match_canonical_published_events`.

2. **Schedule Notification Recipient Privacy & Producer-Consumer Contract (Task 2):**
   - `SchedulingService.publish_schedule()` previously emitted hardcoded `"recipients": ["faculty@school.edu"]`. This leaked plaintext contact PII and caused `NotificationCommand` validation failure.
   - In `app/scheduling/service.py`, plaintext recipient emails were removed from `schedule.published` and `schedule.changed` event payloads and replaced with sanitized `affected_resource_ids`.
   - In `app/integrations/engagement/handlers.py`, `SchedulePublishedNotificationHandler` and `ScheduleChangedNotificationHandler` inspect incoming payloads. If recipients are invalid or contain prohibited PII (emails, phone numbers, raw database PKs), the handler does not fall back to unapproved mappings; it represents the notification as explicitly blocked under `DEC-E8-02` with status `BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02`, preserving the legitimate scheduling business transaction.
   - Verified via regression tests `test_schedule_published_handler_rejects_and_blocks_plaintext_emails`, `test_schedule_changed_handler_rejects_and_blocks_plaintext_emails`, `test_schedule_handler_blocks_when_recipient_resolution_unavailable`, and `test_publish_schedule_outbox_event_payload_privacy`.

3. **Cross-Tenant SLA Evaluation Isolation (Task 1):**
   - Corrected production call chain in `WorkflowService.evaluate_sla_triggers(tenant_id)` to validate `tenant_id` (rejecting empty/whitespace strings with `ValueError`) and forward `tenant_id=tenant_id` to `SlaService.evaluate_triggers()`.
   - In `SlaService.evaluate_triggers()`, verified strict tenant filtering (`if tenant_id and state.tenant_id != tenant_id: continue`).
   - Verified that Tenant A evaluates strictly Tenant A's SLA states. Tenant B's tasks are completely unaffected. Zero cross-tenant warning or breach events or audit records are created.
   - Regression tested via `test_evaluate_sla_triggers_isolates_tenants` and `test_evaluate_sla_triggers_fails_on_missing_or_whitespace_tenant_id`.

4. **SLA Transaction Atomicity, Durability & Worker Restart Constraints (Task 2):**
   - `TaskSlaState` objects in `SlaService._states` are in-memory projections. Previously, `warning_fired_at` and `breach_fired_at` were mutated before outer PostgreSQL commit. If `uow.commit()` failed, rollback dropped DB outbox events, but the in-memory flag remained mutated, permanently suppressing retry emissions.
   - Implemented `SlaService.transaction(tenant_id)` context manager capturing a pre-mutation deep-copy snapshot (`state.model_copy(deep=True)`). If any exception occurs in the block or during commit, the snapshot is restored, reverting `warning_fired_at` and `breach_fired_at` to `None` and preserving status `RUNNING`.
   - Verified via deterministic commit-failure and retry tests: `test_evaluate_sla_triggers_transaction_rollback_on_commit_failure`, `test_evaluate_sla_triggers_breach_rollback_on_commit_failure`, and `test_sla_service_transaction_rolls_back_mutations_on_error`.
   - **Worker Restart Durability Limitations:** Because `SlaService._states` resides in memory, worker restarts cause loss of active SLA tracking timers unless re-hydrated. Surviving worker restarts requires an architectural decision introducing a durable `task_sla_states` PostgreSQL table with transactional row-level updates alongside outbox events.

5. **Notification Blocking, Transactional Inbox Deduplication, and ACK Semantics (Task 3):**
   - Under `DEC-E8-02`, when recipient resolution is unavailable (e.g. missing authority or unresolvable references), handlers log blocked status and return normally without raising an unhandled exception.
   - **Semantics & Analysis:**
     1. *Intended Design:* Returning normally is the deliberate design to prevent poison-message infinite retry loops on RabbitMQ when authority is pending.
     2. *Transactional Inbox Deduplication:* `ConsumerEventProcessor` commits the `(consumer_name, event_id)` marker in `inbox_messages`, preventing duplicate processing.
     3. *RabbitMQ Queue Retention & DLQ:* Message is cleanly ACKed (`basic_ack()`) and cleared from the broker queue. No unhandled exception means DLQ is not triggered.
     4. *Durable Audit Evidence:* The handlers append a canonical append-only audit event (`event_type="notification.dispatch.blocked"`, `outcome=AuditEventOutcome.DENIED`, `details={"reason": "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02", ...}`) via `AuditRepository(session)`.
     5. *Operational Replay Procedure:* Once governance authority is granted under `DEC-E8-02`, operators identify blocked notifications by querying the durable `audit_events` table for `notification.dispatch.blocked` within the tenant scope, and resubmit them via the approved outbox replay CLI or admin endpoint without needing message broker replays.
   - Verified via `test_schedule_published_handler_rejects_and_blocks_plaintext_emails`, `test_schedule_changed_handler_rejects_and_blocks_plaintext_emails`, and `test_schedule_handler_blocks_when_recipient_resolution_unavailable`.

6. **Notification Policy Provenance & Authority Status (Task 4):**
   - The default recipient roles and notification templates utilized by E-7 handlers originate as provisional engineering defaults:
     - `urn:e7:role:task_manager` (default for `task.blocked`, template `TASK_BLOCKED_ALERT`)
     - `urn:e7:role:workflow_assignee` (default for `workflow.task.created`, template `WORKFLOW_TASK_ASSIGNED`)
     - `urn:e7:role:escalation_manager` (default for `workflow.sla.warning`, template `WORKFLOW_SLA_WARNING`)
     - `urn:e7:cohort:faculty` (provisional default for `schedule.published`, template `EXAM_SCHEDULE_PUBLISHED`)
     - `urn:e7:cohort:faculty-all` (provisional default for `schedule.changed`, template `EXAM_SCHEDULE_CHANGED`)
   - These values are classified as **provisional engineering defaults** requiring institutional governance approval under `DEC-E8-02` (recipient endpoint resolution authority) and `DEC-E8-04` (notification template selection and context rendering responsibility).
   - They are not represented as approved institutional standards in code, docstrings, or contracts. Missing recipient authority fails closed or logs blocked status under `DEC-E8-02`.

7. **Zero-PII Validation Errors via Model Config & Exception Sanitization (Task 5):**
   - Configured `hide_input_in_errors=True` on `NotificationCommand`.
   - Stripped all dynamic interpolation of prohibited input values (`clean_ref`, `val`, prefixes, or substrings) from all `ValueError` messages in `app/integrations/engagement/models.py`.
   - Negative tests verify that rejected email addresses, telephone numbers, SSNs, and upstream MySQL PKs never appear in exception messages or Pydantic error strings.
   - Verified via `test_privacy_boundary_rejects_plaintext_phone_numbers`, `test_privacy_boundary_rejects_plaintext_email_addresses`, `test_privacy_boundary_rejects_unauthorized_personal_identifiers`, and `test_privacy_boundary_rejects_raw_institutional_mysql_identifiers`.

8. **Real Integration Infrastructure & Limitations (Task 6):**
   - Verified local host environment: Docker Desktop daemon is inactive and TCP port 5672 is not listening.
   - Integration tests exercise broker routing contracts, binding key topologies, and consumer deduplication pipelines via mock and test double infrastructure.
   - Factual execution boundaries are maintained: zero live broker integration is claimed from mock runs.

---

## 9. Precise ADR-012 Decision Evidence (AD-DQ4-01: Status PENDING)

### Transaction Boundary and Failure Trace

```
[Domain Service]
      │
      ├── 1. Business State (Schedule/Workflow/Task) Mutation
      └── 2. OutboxEvent Persistence (PostgreSQL UoW Commit)
                               │
                               ▼
                        [RabbitMQ Broker]
                               │
                               ▼
                  [ConsumerEventProcessor]
                               │
      ┌────────────────────────┴────────────────────────┐
      ▼ (Open DB Transaction)                           ▼
   [Inbox Repo]                               [Notification Dispatcher]
   inbox_repo.try_record()                     dispatcher.dispatch()
   (Provisional Record)                        (External HTTP / AMQP to E-8)
      │                                                 │
      └────────────────────────┬────────────────────────┘
                               ▼
                        uow.commit()
```

### Empirical Failure Analysis

1. **When Business State Commits:** In the triggering domain service alongside `outbox_events` in an atomic PostgreSQL transaction.
2. **When Domain Events Become Durable:** Upon completion of the domain service PostgreSQL commit.
3. **When `NotificationCommand` is Created:** In-memory inside `NotificationWorker` upon event consumption from RabbitMQ.
4. **Whether `NotificationCommand` is Persisted Independently:** **No.** Under the current implementation, `NotificationCommand` exists solely as an in-memory DTO during consumer dispatch.
5. **Whether E-8 Dispatch Occurs Before Inbox Commit:** **Yes.** In `ConsumerEventProcessor.process()`, `handler.handle()` invokes `dispatcher.dispatch()` (external HTTP/AMQP network I/O) **before** `uow.commit()`.
6. **Failure Scenarios:**
   - *E-8 accepts command, but inbox `uow.commit()` fails:* The inbox record rolls back, but E-8 already received the command. RabbitMQ redelivers the event. Replay constructs the identical deterministic `command_id = notif-...-{event_id}`. Live E-8 deduplication is required to prevent duplicate delivery.
   - *E-8 network timeout:* `dispatcher.dispatch()` raises a timeout error before commit. The transaction rolls back, and RabbitMQ requeues the message for bounded retry.
   - *RabbitMQ redelivers event:* The inbox repository identifies the existing committed message record via `(tenant_id, message_id)`, skips handler execution, and immediately ACKs the message.
   - *Live E-8 Idempotency:* Unverified in production because live E-8 integration is blocked under `BLK-03`.

### Evaluation of Architecture Alternatives Across 10 Dimensions

| Dimension | Option A: Amend ADR-012 (Status Quo) | Option B: Domain Atomic Persist (Strict ADR-012) | Option C: Two-Stage Outbox (Recommended Architecture) |
|---|---|---|---|
| **1. ADR-012 Compliance** | Requires formal amendment of ADR-012 text. | Fully compliant with literal ADR-012 text. | Requires minor ADR-012 amendment clarifying two-stage outbox. |
| **2. Transaction Boundary Cleanliness** | Holds open DB connection during external HTTP dispatch. | Clean: DB transaction commits before outbox poller runs. | Cleanest: Consumer stages notification outbox inside DB transaction; delivery worker performs HTTP I/O strictly outside DB transaction. |
| **3. Durability Guarantees** | Domain event is durable in DB; `NotificationCommand` is transient. | Both domain state and `NotificationCommand` are durable in DB. | Both domain event and `NotificationCommand` are durable in dedicated PostgreSQL tables. |
| **4. Failure Recovery & Retry Semantics** | RabbitMQ redelivers unacked message; bounded retries. | Outbox poller handles exponential backoff and retries. | Dedicated notification outbox worker retries failed dispatches without blocking RabbitMQ queues. |
| **5. Idempotency Guarantees** | Dependent on deterministic `command_id` + downstream E-8 dedupe. | Local outbox unique constraint + downstream E-8 dedupe. | End-to-end local DB deduplication + deterministic `command_id` + E-8 dedupe. |
| **6. External Network I/O Isolation** | **Poor:** Network I/O executes inside consumer DB transaction. | **High:** Network I/O executed by outbox poller outside domain transaction. | **Complete:** Network I/O executed by outbox delivery worker outside any active database transaction. |
| **7. Domain Service Coupling** | **Zero coupling:** Domain services emit pure domain events. | **High coupling:** Domain services must know notification templates and recipient schemas. | **Zero coupling:** Domain services remain pure; consumer maps event to notification outbox. |
| **8. Migration Risk & Complexity** | Zero code change; documentation amendment only. | High refactoring risk across all domain services. | Low-to-moderate; additive `notification_outbox` migration and worker separation. |
| **9. Operational Observability & Audit Evidence** | Moderate; blocked notifications append audit records to `audit_events`. | High; outbox table tracks pending, sent, failed states. | Highest; distinct lifecycle tracking for domain events vs notification delivery attempts. |
| **10. Multi-Tenant Scalability & Head-of-Line Blocking** | Risk of connection pool starvation under slow E-8 responses. | Outbox worker can isolate tenants via tenant-aware polling. | Full tenant isolation with decoupled queues and concurrency controls per tenant. |

**Architectural Decision Status:** `AD-DQ4-01` remains **PENDING** architectural board approval. Option C provides the cleanest long-term production architecture. Option A remains active for Phase DQ-4 development completion.

---

## 10. Calendar Synchronization Reliability

1. **Bi-Directional Synchronization:**
   - Outbound: `GoogleCalendarAdapter` pushes timetable assignments to Google Calendar REST API v3 using RFC 7523 service-account bearer tokens.
   - Inbound: `GoogleCalendarWebhookHandler` receives push notifications from Google Calendar watch channels and triggers `InboundCalendarSyncService`.
2. **Webhook Security Controls:**
   - `X-Goog-Channel-ID`: Validated against active `CalendarWatchChannelRecord`. Unknown channel returns HTTP 404.
   - `X-Goog-Resource-ID`: Validated against registered resource ID; spoofed IDs return HTTP 400.
   - `X-Goog-Channel-Token`: Shared HMAC secret verified; invalid tokens return HTTP 401/403.
   - `X-Goog-Message-Number`: Incremental message numbers recorded in idempotency store; duplicates return HTTP 200 with `DUPLICATE_REPLAYED`.
3. **Watch Channel Lifecycle:**
   Channels created with bounded TTL (default 7 days). `CalendarWatchService` monitors channel expiration timestamps and proactively triggers renewal before expiration.

---

## 11. Institutional ERP Schema & Boundary Verification

### Actual Active 11-Table Schema Boundary

In strict alignment with `app/integrations/erp/mysql_adapter.py` (`MySqlIreduAdapter`), the authorized institutional read-only database boundary comprises exactly 11 active tables:

1. `sch_settings` — Institutional configuration, timezone, academic year boundaries.
2. `sessions` — Academic sessions / terms.
3. `classes` — Academic standards / degree levels.
4. `sections` — Cohort sections within classes.
5. `class_sections` — Association between classes and sections.
6. `subjects` — Academic course catalogue and subject definitions.
7. `staff` — Faculty and institutional staff members (sanitized roster, zero sensitive PII).
8. `department` — Academic and administrative departments.
9. `staff_designation` — Faculty designations and seniority levels.
10. `student_session` — Aggregate student cohort enrollments per session (aggregate counts only).
11. `subject_timetable` — Historical and committed schedule slots.

### Conditional Support Tables (4 Tables)

When multi-section subject groups or fine-grained role mappings are enabled:
12. `subject_groups`
13. `subject_group_subjects`
14. `roles`
15. `staff_roles`

### Safety Controls

- All write operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`) are rejected by code-level regex validation and database least-privilege grants.
- Arbitrary SQL, stored procedures, file system operations (`INTO OUTFILE`), and lock manipulation (`GET_LOCK`) are strictly blocked.
- Single-tenant scope enforcement fails closed if caller tenant does not match configured tenant.

---

## 12. Workflow Engine Boundary (Flowable)

1. **REST Client Reliability:**
   Bounded timeouts (connect: 5s, read: 15s). Transient 502/503/504 errors trigger exponential backoff retry.
2. **Task Action Authorization & Idempotency:**
   Every task action requires `principal_id`, `principal_roles`, and `idempotency_key`. Replay with identical idempotency key returns cached task state without re-invoking Flowable.
3. **Variable Sanitization Policy:**
   `WorkflowVariablePolicy` filters all process variables against an explicit allowlist. Sensitive credentials, auth tokens, and raw PII are purged before transmission to Flowable.

---

## 13. Scheduler / OR-Tools Boundary

1. **Job Lifecycle State Machine:**
   States: `PENDING -> RUNNING -> COMPLETED / FAILED / CANCELLED`. Transitions enforced by database CHECK constraints in migration `0007_create_jobs.py`.
2. **Solver Timeout & Feasibility Handling:**
   CP-SAT solver executes with deterministic time limits. If solver reaches timeout without optimal solution, best feasible solution is returned with non-zero optimality gap diagnostic. If infeasible, relaxation diagnostics identify conflicting hard constraints without crashing.
3. **Publication Outbox Events:**
   Schedule publication commits schedule state and emits `schedule.published` outbox event atomically.

---

## 14. Cross-Cutting Integration Security

1. **BOLA / IDOR Verification:**
   All cross-tenant access attempts to schedules, jobs, calendar webhooks, and audit logs fail closed with HTTP 404 or 403.
2. **Service-to-Service Authentication:**
   Inbound service routes protected by `X-E7-Service-Key` header with constant-time SHA-256 token verification. Unauthorized service keys immediately rejected with HTTP 401.
3. **Secret Redaction:**
   Passwords, JWT secrets, private keys, and authorization bearer tokens are systematically redacted from all log streams and API error envelopes.

---

## 15. External Blockers Status

All four approved canonical external blockers are documented and preserved:

| Blocker ID | Canonical Name | Status | Rationale & Constraint |
|---|---|---|---|
| **BLK-01** | `MYSQL_LIVE_READ_VERIFICATION` | **BLOCKED (APPROVED)** | Live institutional MySQL ERP read verification blocked pending institutional network access, VPN configuration, and read-only credential provisioning. Synthetic fixtures used exclusively. |
| **BLK-02** | `PRODUCTION_COMPANY_OIDC` | **BLOCKED (APPROVED)** | Live institutional OIDC provider (Okta/Azure AD) configuration blocked pending enterprise IT federation. Local JWT mock provider used in tests. |
| **BLK-03** | `E8_NOTIFICATION_INTEGRATION` | **BLOCKED (APPROVED)** | Live E-8 Notification Engine external endpoint connectivity blocked pending E-8 deployment and mutual TLS certificate exchange. AMQP/HTTP test doubles used. |
| **BLK-04** | `PRODUCTION_GATEWAY` | **BLOCKED (APPROVED)** | Production reverse proxy and API gateway mutual TLS routing blocked pending institutional cloud infrastructure provisioning. Local routing verified. |

*Zero test mocks or synthetic stubs have been misrepresented as live production infrastructure.*

---

## 16. Test Evidence and Execution Boundaries

### Test Count Dissection

| Scope / Category | Discovered / Collected | Executed in CI | Passed | Skipped | Environment Requirements |
|---|---|---|---|---|---|
| **Unit & Contract Suites** (`tests/unit/`, `tests/contract/`) | 1,928 | 1,923 | 1,917 | 6 | Standard CI Linux runner with PostgreSQL 16 service container |
| **Task Intelligence Integration** (`tests/integration/task_intelligence/`) | 10 | 10 | 10 | 0 | Standard CI Linux runner with PostgreSQL 16 service container |
| **Security Test Suite** (`tests/security/`) | 160 | 0 (specialized run) | N/A | 0 | Dedicated security test pipeline / testcontainers |
| **Role-Based UAT Suite** (`tests/uat/`) | 52 | 0 (specialized run) | N/A | 0 | Dedicated UAT runner / multi-service environment |
| **Live External Integration / E2E** (`tests/e2e/`, `tests/integration/scheduling/`) | 137 | 0 (skipped) | N/A | 6 | Requires live external credentials (Google, RabbitMQ cluster) |
| **Total Global Test Discovery** | **2,293** | **1,939** | **1,933** | **6** | Full repository test collection |
| **Frontend Test Suite** (Vitest) | 146 | 146 | 146 | 0 | Node.js 20+ / JSDOM environment (19 test files) |

### Static Analysis & Verification

- `ruff check .`: **All checks passed!** (0 errors).
- `mypy app`: **Success: no issues found in 265 source files**.
- Frontend Lint & Typecheck: **0 ESLint errors, 0 TypeScript errors**.
- Frontend Build: **Clean Vite production build completed**.

---

## 17. Final Verdict

```
╔════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                ║
║         VERDICT: DQ4_RELIABILITY_CORRECTIONS_COMPLETE_ARCHITECTURAL_DECISION_PENDING           ║
║                                                                                                ║
║  1. Cross-Tenant SLA evaluation isolation verified (WorkflowService -> SlaService).            ║
║  2. SLA transaction atomicity & rollback snapshot verified on DB commit failure.               ║
║  3. Worker restart durability limitations documented (in-memory _states vs durable table).     ║
║  4. Blocked notification audit events staged under DEC-E8-02 with safe inbox & broker ACK.     ║
║  5. Recipient authorization status verified (canonical opaque vs provisional defaults vs PII). ║
║  6. NotificationCommand validation error PII leakage eliminated (hide_input_in_errors=True).   ║
║  7. RabbitMQ routing key alignment verified (canonical 2-word & wildcard bindings).            ║
║  8. All 4 canonical source-verified E-8 events verified; schedule.changed correctly handled.  ║
║  9. Real integration infrastructure limits documented (Docker inactive; mock double verified). ║
║ 10. AD-DQ4-01 architectural alternatives evaluated across 10 dimensions (Status: PENDING).     ║
║ 11. True active 11-table institutional ERP schema formally verified and documented.          ║
║ 12. Test execution boundaries precisely documented: 2,293 collected vs 1,933+ passed in CI.   ║
║ 13. PR #4 remains open at https://github.com/Itsbhavesh1101/Ops/pull/4 (unmerged).             ║
║ 14. Frozen Phase-8 RC tag (phase8-rc-38d080fadab8) unaltered.                                  ║
║ 15. Phase DQ-5 NOT started.                                                                    ║
║                                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════════════════════╝
```
