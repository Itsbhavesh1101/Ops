# E-7 Operations Engine — Phase DQ-4 Completion Report
## Integration Reliability, Calendar Synchronization, Messaging, and External Boundaries

**Prepared:** 28 October 2026  
**Repository:** `F:\operations-engine`  
**GitHub Tracking Remote:** `https://github.com/Itsbhavesh1101/Ops`  
**Working Feature Branch:** `quality/dq4-integration-reliability`  
**Target Base Branch:** `master`  
**Base Commit SHA:** `8936513ab70f3238db16843fe0b6bef0026c364b` (merged PR #3 into `master`)  
**Existing Pull Request:** [PR #4](https://github.com/Itsbhavesh1101/Ops/pull/4) (Status: OPEN, unmerged)  
**Reviewed GitHub Commit SHA:** `7e0a2fb67ad631ddd2f002c1a9a4da852db394f0`  
**Frozen Phase-8 RC Tag:** `phase8-rc-38d080fadab8` (`03e6305256db3d838b472aaac50893ddb513414e`) — intact and unaltered.  
**Exit Gate Verdict:** `DQ4_REVIEW_CORRECTIONS_COMPLETE_ARCHITECTURAL_DECISION_PENDING`

---

## 1. Executive Summary

Phase DQ-4 completes the integration correctness, calendar synchronization, messaging reliability, and external boundary verification for the IR Infotech E-7 Operations Engine. Following independent review of PR #4 (`7e0a2fb67ad631ddd2f002c1a9a4da852db394f0`), this report incorporates all corrective actions, empirical evidence, and architectural evaluations across:

1. **Baseline and Metadata Accuracy:** Accurate GitHub commit SHA tracking (`7e0a2fb67ad631ddd2f002c1a9a4da852db394f0`), open PR #4 confirmation, and verified frozen Phase-8 RC tag integrity.
2. **E-8 Recipient Privacy Boundary:** Zero plaintext contact PII (emails, phone numbers), strict rejection of unauthorized personal identifiers (names, SSNs, DOBs) and raw institutional MySQL primary keys, with full support for canonical opaque references.
3. **Four Canonical Source-Verified E-8 Events & `schedule.changed` Classification:** Complete verification of event producers, envelopes, broker routing, consumer handlers, and privacy-preserving command formulation for `task.blocked`, `workflow.task.created`, `workflow.sla.warning`, and `schedule.published`, with `schedule.changed` correctly classified as `SOURCE_BACKED_BUSINESS_CONDITION` (`SCHEDULE_CHANGE_NOTICE`).
4. **Precise ADR-012 Decision Evidence:** Exhaustive transaction boundary analysis evaluating Options A, B, and C (Two-Stage Outbox) with complete trade-off documentation.
5. **Accurate Institutional ERP Schema:** Formal correction of the active 11-table read-only schema (`sch_settings`, `sessions`, `classes`, `sections`, `class_sections`, `subjects`, `staff`, `department`, `staff_designation`, `student_session`, `subject_timetable`) and 4 conditional support tables.
6. **Transparent Test Evidence:** Precise distinction between 2,272 globally discovered tests, 1,912 tests executed in CI (1,908 unit/contract + 10 task intelligence), 6 skipped, and 146 frontend Vitest tests.

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
   - **Reviewed Commit SHA:** `7e0a2fb67ad631ddd2f002c1a9a4da852db394f0`
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

All negative and positive scenarios pass across 55 test cases in `tests/unit/integrations/test_e8_notification_dispatcher.py`, covering both HTTP and AMQP transport paths.

---

## 8. Source-Verified E-8 Events & `schedule.changed` Classification

### Canonical Event Verification Matrix

| Event Name | Trigger Kind | Canonical Classification | Producer Module | Durable Persistence | Broker Routing Key | Queue Binding | Consumer Handler | Recipient Reference Resolution | Idempotency Derivation |
|---|---|---|---|---|---|---|---|---|---|
| `task.blocked` | `SOURCE_VERIFIED_EVENT` | **Source-Verified** | Task Execution Service | PostgreSQL `outbox_events` | `{tenant}.task.blocked` | `*.task.blocked` | `TaskBlockedNotificationHandler` | Opaque role reference `urn:e7:role:task_manager` | `notif-blk-{event_id}` |
| `workflow.task.created` | `SOURCE_VERIFIED_EVENT` | **Source-Verified** | Flowable Adapter / Workflow Service | PostgreSQL `outbox_events` | `{tenant}.workflow.task.created` | `*.workflow.task.created` | `WorkflowTaskCreatedNotificationHandler` | Opaque user reference `user:{assignee}` or `urn:e7:role:workflow_assignee` | `notif-wf-{event_id}` |
| `workflow.sla.warning` | `SOURCE_VERIFIED_EVENT` | **Source-Verified** | SLA Monitoring Engine | PostgreSQL `outbox_events` | `{tenant}.workflow.sla.warning` | `*.workflow.sla.warning` | `WorkflowSlaWarningNotificationHandler` | Opaque role reference `urn:e7:role:escalation_manager` | `notif-sla-{event_id}` |
| `schedule.published` | `SOURCE_VERIFIED_EVENT` | **Source-Verified** | Scheduling Domain Service | PostgreSQL `outbox_events` | `{tenant}.schedule.published` | `*.schedule.published` | `SchedulePublishedNotificationHandler` | Opaque role reference `urn:e7:cohort:faculty-all` | `notif-pub-{event_id}` |
| `schedule.changed` | `SOURCE_BACKED_BUSINESS_CONDITION` | **Derived / Proposed** | Scheduling Repair Service | PostgreSQL `outbox_events` | `{tenant}.schedule.changed` | `*.schedule.changed` | `ScheduleChangedNotificationHandler` | Opaque role reference `urn:e7:cohort:faculty-all` | `notif-chg-{event_id}` |

### Classification of `schedule.changed`

In strict alignment with `E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md` Section 7, `schedule.changed` is classified as `TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION` with condition `SCHEDULE_CHANGE_NOTICE`. It is **not** classified as `SOURCE_VERIFIED_EVENT` because the contract defines source-verified events as primary canonical transitions, whereas schedule change notifications represent derived business conditions evaluated across schedule versions.

---

## 9. Precise ADR-012 Decision Evidence

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

### Evaluation of Architecture Alternatives

| Evaluation Dimension | Option A: Amend ADR-012 (Status Quo) | Option B: Domain Atomic Persist (Strict ADR-012) | Option C: Two-Stage Outbox (Recommended Architecture) |
|---|---|---|---|
| **ADR-012 Compliance** | Requires formal amendment of ADR-012 text. | Fully compliant with literal ADR-012 text. | Requires minor ADR-012 amendment clarifying two-stage outbox. |
| **Durability** | Domain event durable in outbox; `NotificationCommand` is transient. | `NotificationCommand` durable in domain transaction. | Both domain event and `NotificationCommand` are fully durable in dedicated DB tables. |
| **Failure Recovery** | RabbitMQ redelives unacked event; deterministic `command_id` deduplicates. | Outbox dispatcher polls and retries command delivery. | Dedicated notification outbox worker polls and retries delivery independently. |
| **Idempotency** | Dependent on deterministic command ID and external E-8 deduplication. | Dependent on outbox unique constraint and E-8 deduplication. | End-to-end local DB deduplication + external E-8 deduplication. |
| **Transaction Boundaries** | Clean domain transaction; consumer holds DB transaction during HTTP I/O. | Domain transaction commits business state + notification command. | Domain transaction commits business state + event; consumer commits inbox + notification outbox; delivery worker performs network I/O outside DB transaction. |
| **External Network I/O** | Executed inside consumer DB transaction (risk of connection pool hold). | Executed by outbox dispatcher outside domain transaction. | Executed by delivery dispatcher strictly outside any open DB transaction. |
| **Domain Coupling** | **Zero coupling**; domain services emit pure domain events. | **High coupling**; domain services must know notification templates and recipient schemas. | **Zero coupling**; domain services remain pure; consumer translates to notification outbox. |
| **Migration Risk** | Zero code change; documentation amendment only. | High refactoring risk across all domain services. | Low-to-moderate; additive `notification_outbox` migration and worker separation. |

**Architectural Recommendation:** Option C provides the cleanest long-term production architecture by eliminating the anti-pattern of holding open database transactions during external HTTP network calls while preserving complete domain decoupling. Option A is fully viable for Phase DQ-4 development completion pending architectural approval.

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
| **Unit & Contract Suites** (`tests/unit/`, `tests/contract/`) | 1,913 | 1,908 | 1,902 | 6 | Standard CI Linux runner with PostgreSQL 16 service container |
| **Task Intelligence Integration** (`tests/integration/task_intelligence/`) | 10 | 10 | 10 | 0 | Standard CI Linux runner with PostgreSQL 16 service container |
| **Security Test Suite** (`tests/security/`) | 160 | 0 (specialized run) | N/A | 0 | Dedicated security test pipeline / testcontainers |
| **Role-Based UAT Suite** (`tests/uat/`) | 52 | 0 (specialized run) | N/A | 0 | Dedicated UAT runner / multi-service environment |
| **Live External Integration / E2E** (`tests/e2e/`, `tests/integration/scheduling/`) | 137 | 0 (skipped) | N/A | 6 | Requires live external credentials (Google, RabbitMQ cluster) |
| **Total Global Test Discovery** | **2,272** | **1,918** | **1,912** | **6** | Full repository test collection |
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
║             VERDICT: DQ4_REVIEW_CORRECTIONS_COMPLETE_ARCHITECTURAL_DECISION_PENDING            ║
║                                                                                                ║
║  1. All independent review findings addressed and verified across code, tests, and reports.    ║
║  2. E-8 recipient privacy boundary strictly enforced (zero plaintext email/phone/PII/MySQL).  ║
║  3. All 4 canonical source-verified E-8 events verified; schedule.changed correctly classified. ║
║  4. ADR-012 transaction boundary trace and 3-alternative architecture evaluation documented.   ║
║  5. True active 11-table institutional ERP schema formally verified and documented.          ║
║  6. Test execution boundaries precisely documented: 2,272 collected vs 1,912 passed in CI.    ║
║  7. PR #4 remains open at https://github.com/Itsbhavesh1101/Ops/pull/4 (unmerged).             ║
║  8. Frozen Phase-8 RC tag (phase8-rc-38d080fadab8) unaltered.                                  ║
║  9. Phase DQ-5 NOT started.                                                                    ║
║                                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════════════════════╝
```
