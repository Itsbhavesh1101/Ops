# IR Infotech — E-7 Operations Engine
## Phase DQ-4: Architecture Decision Package
### Integration Durability, Recipient Governance, and SLA State Persistence

**Prepared:** 28 October 2026  
**Repository:** `F:\operations-engine`  
**Target Base Branch:** `master`  
**Working Feature Branch:** `quality/dq4-integration-reliability`  
**Pull Request:** [PR #4](https://github.com/Itsbhavesh1101/Ops/pull/4) (Status: **OPEN**, unmerged)  
**Reviewed Remote Head Commit SHA:** `0410372df0dd72a0b16bbbc9c5889f641c6e5708`  
**Frozen Phase-8 RC Tag:** `phase8-rc-38d080fadab8` (`03e6305256db3d838b472aaac50893ddb513414e`) — *verified intact and unaltered*  
**Document Status:** `PROPOSED` / `APPROVAL_REQUIRED`

---

## 1. Executive Summary & Purpose

Following the empirical completion of Phase DQ-4 runtime integration corrections, unit testing, and GitHub Actions CI verification (100% green on run `40707712855`), three architectural concerns remain between the verified codebase and full production readiness:

1. **Provisional Recipient References:** Fallback recipient references (`urn:e7:cohort:faculty`, `urn:e7:cohort:faculty-all`, `urn:e7:role:task_manager`, etc.) conform to valid opaque-syntax rules, but lack demonstrated tenant-specific institutional authorization. If outbound dispatch is enabled, production can dispatch commands using these placeholders without confirmed institutional recipient endpoints.
2. **Blocked Notification Recovery:** When notifications are blocked under `DEC-E8-02`, the transactional consumer cleanly ACKs the broker message and appends an audit event to `audit_events`. However, **an executable recovery or replay mechanism is currently NOT IMPLEMENTED**. Querying audit logs provides diagnostic visibility but does not constitute an operational replay engine. Inbox deduplication prevents ordinary duplicate broker replay.
3. **SLA Tracking Durability & Concurrency:** The SLA engine's state projection (`_states`) resides entirely in worker memory. While commit-failure transactional snapshot rollback is verified within a single process, worker restarts cause complete loss of active SLA tracking states, and running multiple worker process replicas introduces concurrency hazards without distributed database row locks.
4. **ADR-012 Alignment:** The literal text of ADR-012 prescribes atomic persistence of `NotificationCommand` directly in the domain service transaction, whereas the runtime implementation utilizes a decoupled consumer pipeline (`Domain Event -> Outbox -> RabbitMQ -> NotificationWorker -> E-8`).

This document constitutes the formal architectural decision package resolving these items prior to production deployment.

---

## 2. Status Classification Taxonomy

In compliance with development quality guardrails, all capabilities, decisions, and proposals in this document are strictly classified using the following normative statuses:

| Status Code | Definition | Scope of Application |
|---|---|---|
| **`IMPLEMENTED`** | Code is present in the codebase. | Domain transactional outbox, transactional inbox deduplication, workflow BPMN parsing, tenant isolation checks, in-memory SLA evaluation & snapshot rollback. |
| **`TESTED`** | Verified by automated test suites in CI. | `TESTED_LIVE` (PostgreSQL unit/integration tests) vs. `TESTED_DOUBLES` (E-8 dispatcher, Google Calendar client, Flowable client, RabbitMQ broker runners). |
| **`PROPOSED`** | Architectural design, recommendation, or policy authored in documentation, but **NOT implemented in production code or database migrations**. Must never be claimed as operational. | `allow_provisional_recipients` gating setting, Two-Stage Notification Outbox (`AD-DQ4-01` Option C), administrative notification replay API, PostgreSQL SLA persistence (`AD-DQ4-02` Option 2). |
| **`APPROVAL_REQUIRED`** | Formal governance sign-off required (internal Architecture Review Board or external institutional IT/business authority) before adoption or implementation. | ADR-012 amendment for Option C (`ARB`), `task_sla_states` table migration (`ARB`), E-8 directory resolution ownership (`Institutional IT`), template ownership (`Institutional Communications`). |
| **`EXTERNAL_BLOCKED`** | Live verification is gated by missing enterprise infrastructure or credentials outside the repository boundary (`BLK-01`–`BLK-04`). | MySQL live ERP network access, Okta/Azure OIDC federation, live E-8 endpoints, production gateway mTLS. |
| **`NOT_IMPLEMENTED`** | The capability, endpoint, CLI, or persistence mechanism does not exist in the codebase. Must never be claimed as operational. | Blocked notification replay CLI/endpoint, durable `task_sla_states` table, automatic consumer DLQ reprocessing. |

### Explicit Proposal vs. Implementation Boundary Mapping

To avoid any ambiguity between current implementation and future architecture, the following specific items are explicitly classified:
- `allow_provisional_recipients: bool = False`: **`PROPOSED`** (not implemented; production safety currently relies on `NotificationSettings.enabled=False`).
- Two-stage notification outbox (`AD-DQ4-01` Option C): **`PROPOSED`** (not implemented; requires ARB approval and explicit roadmap approval).
- Notification replay API (`POST /api/v1/admin/notifications/replay`): **`PROPOSED`** / **`NOT_IMPLEMENTED`** (zero replay CLI or endpoint exists).
- PostgreSQL SLA persistence (`AD-DQ4-02` Option 2): **`PROPOSED`** (not implemented; requires ARB approval and explicit roadmap approval).
- E-8 directory and rendering ownership (`DEC-E8-02`, `DEC-E8-04` Option 1): **`PROPOSED`** / **`APPROVAL_REQUIRED`** (recommended architecture, awaiting institutional IT/business sign-off).
- Canonical External Blockers (`BLK-01` through `BLK-04`): **`EXTERNAL_BLOCKED`** (canonical identifiers preserved; Google Calendar sync and SLA durability tracked separately).

---

## 3. Decision 1: AD-DQ4-01 — Notification Command Durability & ADR-012 Alignment

**Classification:** `INTERNAL_DECISION_REQUIRED` / `INSTITUTIONAL_APPROVAL_REQUIRED`  
**Current Code Status:** `VERIFIED_WITH_TEST_DOUBLES` (Decoupled Consumer Pipeline)

### 3.1 Current Implementation
1. **Domain Transaction Boundary:** Triggering domain services (`SchedulingService`, `WorkflowService`, `TaskExecutionService`) commit business entity mutations atomically with an `EventEnvelope` in the PostgreSQL `outbox_events` table within a `SqlAlchemyUnitOfWork`.
2. **Broker Dispatch:** The background `OutboxDispatcher` polls `outbox_events` (`SELECT ... FOR UPDATE SKIP LOCKED`), publishes domain events to the RabbitMQ topic exchange `e7.events` using publisher confirms, and transitions event state to `DELIVERED`.
3. **Consumer Execution:** `NotificationWorker` consumes messages from RabbitMQ via `ConsumerEventProcessor`. Inside a local Unit of Work transaction, it records a deduplication marker in `inbox_messages`, translates the domain event into an in-memory `NotificationCommand`, and dispatches it via `NotificationDispatcherProtocol` (HTTP or RabbitMQ `e8.notifications`).
4. **Durability Gap:** The `NotificationCommand` exists solely as an in-memory DTO during consumer execution. It is **not** persisted independently into a database table. Network I/O occurs inside the consumer's open database transaction prior to `uow.commit()`.

### 3.2 Authoritative Requirement
- **ADR-012 Text:** *"Outbound notification commands are committed atomically within the E-7 PostgreSQL transactional outbox alongside the operational state change that caused it."*
- **E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md §4:** *"Future implementation MUST commit outbound notification commands atomically within the E-7 PostgreSQL transactional outbox alongside the operational state change that caused it (ADR-005)."*

### 3.3 Available Architecture Alternatives

```
[Option A: Status Quo Decoupled Consumer]
Domain Mutation + Domain Event -> outbox_events -> RabbitMQ -> Consumer (inbox_messages) -> [Network I/O to E-8]

[Option B: Domain Atomic Persist (Strict ADR-012)]
Domain Mutation + NotificationCommand -> outbox_events (Command direct) -> Outbox Dispatcher -> [Network I/O to E-8]

[Option C: Two-Stage Outbox Pipeline (Recommended)]
Stage 1: Domain Mutation + Domain Event -> outbox_events -> RabbitMQ
Stage 2: Consumer (inbox_messages + notification_outbox insert) -> DB Commit
Stage 3: Notification Outbox Worker -> [Network I/O to E-8 strictly outside DB transaction]
```

### 3.4 Exhaustive Evaluation Across 10 Dimensions

| # | Evaluation Dimension | Option A: Amend ADR-012 (Status Quo) | Option B: Domain Atomic Persist (Strict ADR-012) | Option C: Two-Stage Outbox (Recommended Target) |
|:---:|---|---|---|---|
| **1** | **ADR-012 Compliance** | Requires formal amendment of ADR-012 to permit decoupled event-driven notification dispatch. | Fully compliant with literal text of ADR-012. | Requires approved amendment of ADR-012 formalizing the two-stage outbox architecture. |
| **2** | **Transaction Cleanliness** | **Defective:** Holds open DB transaction/connection during external HTTP/AMQP dispatch in consumer. | **Clean:** Domain DB transaction commits before outbox background polling worker runs. | **Cleanest:** Consumer commits inbox + notification outbox inside DB transaction; delivery worker performs network I/O strictly outside DB transaction. |
| **3** | **Durability Guarantees** | Domain event durable in outbox; `NotificationCommand` transient in memory. | Both domain entity and `NotificationCommand` durable in initial domain transaction. | **Highest:** Domain event and `NotificationCommand` durable in dedicated PostgreSQL tables with distinct lifecycles. |
| **4** | **Failure Recovery & Retry** | RabbitMQ redelivers unacked message; risk of message queue head-of-line blocking. | Outbox background worker handles polling, exponential backoff, and DLQ retries. | Dedicated notification outbox worker retries failed dispatches without blocking RabbitMQ queues or holding DB locks. |
| **5** | **Idempotency Guarantees** | Dependent on deterministic `command_id` + downstream E-8 deduplication. | Dependent on outbox unique constraint + downstream E-8 deduplication. | **Complete:** Local DB deduplication in `notification_outbox` + deterministic `command_id` + downstream E-8 deduplication. |
| **6** | **Network I/O Isolation** | **Poor:** External HTTP/AMQP network call executed inside active SQLAlchemy session. | **High:** External network call executed by background outbox worker outside domain transaction. | **Complete:** External network call executed by notification worker outside any active database transaction. |
| **7** | **Domain Service Coupling** | **Zero coupling:** Domain services emit pure domain events (`schedule.published`, etc.). | **High coupling:** Domain services must assemble notification templates, recipients, and context DTOs. | **Zero coupling:** Domain services emit pure domain events; notification consumer translates to notification outbox. |
| **8** | **Migration Risk & Complexity** | **Lowest:** Zero code changes required; documentation amendment only. | **Highest:** Extensive refactoring across all domain services (scheduling, workflow, tasks). | **Moderate:** Additive `notification_outbox` table migration and worker separation; domain services untouched. |
| **9** | **Operational Observability** | Moderate; blocked notifications append audit records to `audit_events`. | High; outbox table tracks pending, sent, failed command states. | **Highest:** Distinct operational observability: domain outbox tracks internal events; notification outbox tracks external delivery. |
| **10** | **Multi-Tenant Scalability** | Risk of connection pool exhaustion under slow downstream E-8 responses. | Polling queries can isolate tenants via tenant-aware filters. | **Highest:** Decoupled outbox tables and queues support per-tenant rate limits, concurrency controls, and lease timeouts. |

### 3.5 Failure Modes
- **Option A Failure Mode:** Under slow downstream E-8 response times (e.g. 3–5 seconds), all database connections in the SQLAlchemy connection pool are held waiting on network I/O, starving core operational API routes of database connections.
- **Option B Failure Mode:** If notification command assembly fails (e.g., recipient resolution syntax error or template missing), the entire domain transaction rolls back, preventing an exam schedule from being published or a workflow task from completing.
- **Option C Failure Mode:** Worker failure between Stage 1 and Stage 2 is handled by RabbitMQ unacked redelivery; worker failure during Stage 3 is handled by `SELECT FOR UPDATE SKIP LOCKED` lease expiration in `notification_outbox`. Zero connection pool starvation.

### 3.6 Database Migration Implications
- **Option A:** Zero schema migrations.
- **Option B:** Alters `outbox_events` schema or payload format to accommodate `NotificationCommand` semantics directly.
- **Option C:** Requires a new Alembic migration:
  ```sql
  CREATE TABLE notification_outbox_commands (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id VARCHAR(255) NOT NULL,
      command_id VARCHAR(255) NOT NULL UNIQUE,
      correlation_reference VARCHAR(255) NOT NULL,
      causation_reference VARCHAR(255),
      trigger_kind VARCHAR(64) NOT NULL,
      source_event_name VARCHAR(128),
      source_entity_reference VARCHAR(255),
      recipient_references JSONB NOT NULL,
      notification_context JSONB NOT NULL,
      dispatch_state VARCHAR(32) NOT NULL DEFAULT 'PENDING',
      retry_count INT NOT NULL DEFAULT 0,
      next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      dispatched_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
  );
  CREATE INDEX ix_notif_outbox_pending ON notification_outbox_commands (dispatch_state, next_attempt_at);
  ```

### 3.7 Recommendation & Governance Action
- **Recommendation:** **Option C (Two-Stage Outbox Pipeline)** is **PROPOSED** as the architecturally superior long-term design for production reliability, strictly eliminating the anti-pattern of network I/O inside open database transactions while preserving domain service purity.
- **Current Operational Baseline:** **Option A remains active for Phase DQ-4 completion** (`IMPLEMENTED` / `TESTED` with test doubles).
- **Roadmap Governance & Dependencies:** Implementing Option C is **NOT** automatically assigned to Phase DQ-5. Adoption of Option C requires:
  1. Formal Architecture Review Board (ARB) approval amending ADR-012.
  2. Explicit product and engineering roadmap approval authorizing the migration and worker changes.
  3. Final acceptance of Phase DQ-4 depends on formal ARB alignment regarding ADR-012 compliance versus Option C adoption.

---

## 4. Decision 2: DEC-E8-02 — Recipient Resolution & Authorization Ownership

**Classification:** `INSTITUTIONAL_APPROVAL_REQUIRED` / `INTERNAL_DECISION_REQUIRED`  
**Current Code Status:** `VERIFIED_WITH_TEST_DOUBLES` (Provisional Placeholders Gated by `enabled=False`)

### 4.1 Current Implementation
In `app/integrations/engagement/handlers.py`:
- `SchedulePublishedNotificationHandler` defaults to `urn:e7:cohort:faculty`
- `ScheduleChangedNotificationHandler` defaults to `urn:e7:cohort:faculty-all`
- `TaskBlockedNotificationHandler` defaults to `urn:e7:role:task_manager`
- `WorkflowTaskCreatedNotificationHandler` defaults to `user:{assignee}` or `urn:e7:role:workflow_assignee`
- `WorkflowSlaWarningNotificationHandler` defaults to `urn:e7:role:escalation_manager`

`NotificationCommand.validate_recipients()` verifies that references are non-empty, contain no control characters, contain zero contact PII (no `@`, no phone numbers), and contain zero raw MySQL integer PKs. However, **it performs zero authorization check against tenant-approved recipient registries**.

### 4.2 Authoritative Requirement
- `E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md` Section 16: *"E-7 SHOULD use privacy-preserving canonical/opaque recipient references where the eventual E-8 integration supports reference resolution. Raw contact PII must not be included merely for convenience... The authoritative component and contract responsible for resolving approved recipient references into delivery endpoints is REQUIRES_BUSINESS_CONFIRMATION."*
- `DEC-E8-02`: *"Confirm the authoritative component and contract responsible for resolving approved recipient references into delivery endpoints. (Status: REQUIRES_BUSINESS_CONFIRMATION)."*

### 4.3 Available Options
1. **Option 1: E-8 Directory Ownership (Decoupled Enterprise Directory):**
   - E-7 emits purely canonical opaque URNs (`urn:e7:cohort:faculty`, `urn:e7:role:task_manager`).
   - E-8 connects to the institutional identity provider (Azure AD / Okta / SIS) and resolves the opaque URN into physical delivery endpoints based on tenant delivery preferences, quiet hours, and channel rules.
   - *Benefits:* Cleanest boundary; E-7 manages zero directory synchronization or user contact profiles.
   - *Trade-offs:* Requires E-8 to implement directory resolution logic and multi-tenant endpoint caching.
2. **Option 2: E-7 Tenant Recipient Registry (Local Database Resolution):**
   - E-7 maintains a tenant-scoped database table `notification_recipient_authorizations` defining authorized opaque URNs per tenant. Handlers reject or block dispatch if a URN is not registered for that tenant.
   - *Benefits:* Explicit local control and verification within E-7.
   - *Trade-offs:* Duplicates directory management responsibilities inside E-7; increases administrative maintenance.
3. **Option 3: Gated Safe Production Policy (Proposed Baseline):**
   - Retain opaque URN generation in E-7.
   - Introduce an explicit production setting `allow_provisional_recipients: bool = False` in `NotificationSettings`.
   - In production (`allow_provisional_recipients=False`), all provisional defaults (`urn:e7:cohort:*`, `urn:e7:role:*`) that lack explicit tenant-payload overrides are blocked under `DEC-E8-02` and audited with `outcome=DENIED`.
   - Only explicitly supplied, source-verified user identifiers (e.g. `user:<uuid>`) are dispatched.

### 4.4 Recommendation & Roadmap Status
- **Status Classification:**
  - Setting `allow_provisional_recipients: bool = False`: **`PROPOSED`** (not implemented in code; current production safety relies on `NotificationSettings.enabled=False`).
  - E-8 Directory Ownership (Option 1): **`PROPOSED`** / **`APPROVAL_REQUIRED`** (recommended architecture, awaiting institutional enterprise IT / directory team approval).
- **Roadmap Governance:** Building directory resolution adapters and tenant recipient authorization policies requires explicit roadmap approval and institutional IT federation, rather than an automatic assumption for Phase DQ-5.
- **Testable Acceptance Criteria (Upon Implementation):**
  1. When `allow_provisional_recipients=False`, events lacking explicit recipient payload fail closed, recording a `notification.dispatch.blocked` audit event with `outcome=DENIED` and zero external dispatch.
  2. When an explicit opaque recipient is provided in payload (e.g. `["user:550e8400-e29b-41d4-a716-446655440000"]`), dispatch proceeds normally.
  3. Plaintext emails and phone numbers remain strictly rejected by `NotificationCommand` validation.

---

## 5. Decision 3: DEC-E8-04 — Notification Template & Rendering Ownership

**Classification:** `PROPOSED` / `APPROVAL_REQUIRED`  
**Current Code Status:** `IMPLEMENTED` & `TESTED` (Template ID & Minimal Structured Context in E-7)

### 5.1 Current Implementation
E-7 notification handlers assemble typed `NotificationCommand` instances specifying:
- `template_id`: String constant (`EXAM_SCHEDULE_PUBLISHED`, `EXAM_SCHEDULE_CHANGED`, `TASK_BLOCKED_ALERT`, `WORKFLOW_TASK_ASSIGNED`, `WORKFLOW_SLA_WARNING`).
- `notification_context`: Minimal key-value operational facts (e.g. `schedule_id`, `version`, `task_id`, `workflow_instance_id`, `warning_at`, `breach_at`).
- **Zero human-facing text, HTML, email subjects, or localized body content is authored or stored in E-7.**

### 5.2 Authoritative Requirement
- `E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md` Section 19: *"E-7 MUST NOT become a provider-specific message-rendering/delivery implementation... E-7 provides notification intent and minimum structured operational context. Final rendering/template-selection ownership is assigned according to the E-8 contract."*
- `DEC-E8-04`: *"Confirm the responsibility boundary for message subject/title, rendering, template selection, and structured context interpretation. (Status: REQUIRES_BUSINESS_CONFIRMATION)."*

### 5.3 Available Options
1. **Option 1: E-8 Owns All Rendering, Localization, and Formatting (Recommended):**
   - E-7 provides solely the canonical `template_id` and raw operational context variables.
   - E-8 maintains institutional templates, branding, Jinja2/HTML rendering, SMS character limit truncation, WhatsApp message template pre-approvals, and language translation.
   - *Benefits:* Adheres strictly to the architectural boundary that E-7 is an orchestration engine, not a communication gateway.
   - *Trade-offs:* Requires E-8 team to maintain synchronization of template variables with E-7 context schemas.
2. **Option 2: E-7 Owns Human-Facing Subject / Title:**
   - E-7 supplies a rendered `subject` or title string in `notification_context`.
   - *Benefits:* Allows E-7 domain logic to customize subject lines directly.
   - *Trade-offs:* Violates separation of concerns; introduces localization and presentation formatting burdens into E-7 domain services.

### 5.4 Recommendation & Roadmap Status
- **Status Classification:** **Option 1** is **`PROPOSED`** / **`APPROVAL_REQUIRED`**. It is the recommended architecture, awaiting formal institutional communications team sign-off.
- **Roadmap Governance:** E-7 remains strictly an operational condition detection and intent engine. Template rendering and localization ownership is assigned to E-8; final integration requires institutional template governance approval.
- **Testable Acceptance Criteria:**
  1. `NotificationCommand` contains zero rendered HTML, CSS, or localized message text.
  2. All dates in `notification_context` are normalized ISO-8601 UTC strings.
  3. `notification_context` contains only operational IDs and dates matching the allowed template schema.

---

## 6. Decision 4: AD-DQ4-02 — Durable SLA Tracking & Concurrency Control

**Classification:** `PROPOSED` / `APPROVAL_REQUIRED`  
**Current Code Status:** `IMPLEMENTED` & `TESTED` (In-Memory Engine & Rollback Snapshot) / `NOT_IMPLEMENTED` (Restart Durability & Concurrency Locking)

### 6.1 Current Implementation & Durability Audit
1. `SlaService._states: dict[UUID, TaskSlaState]` stores SLA projections in Python process memory.
2. Transaction rollback atomicity within a single process execution is implemented and verified via `SlaService.transaction(tenant_id)`.
3. **Demonstrated Defect (Restart Loss):** If the E-7 worker process restarts, crashes, or is redeployed, `self._states` is reinitialized to empty. All active SLA tracking timers, accumulated paused durations, and warning fired states are permanently lost.
4. **Demonstrated Risk (Multi-Worker Concurrency):** In a production environment running multiple worker replicas (e.g. Kubernetes pods), each replica maintains independent in-memory state. Tasks attached on Pod 1 are invisible to Pod 2, and concurrent evaluations race without database row locks.

### 6.2 Authoritative Requirement
- `PHASE_3_SLA_ESCALATION.md`: SLA tracking must evaluate deadlines deterministically and emit warnings and breaches idempotently without state loss across worker restarts.
- `ADR-005`: All operational state changes must be committed atomically with outbox events in PostgreSQL.

### 6.3 Available Persistence Alternatives

| Evaluation Dimension | Option 1: Status Quo (In-Memory + Rollback Snapshot) | Option 2: Dedicated `task_sla_states` Table (Recommended) | Option 3: Extend `workflow_task_mappings` Table |
|---|---|---|---|
| **Restart Durability** | **NOT_IMPLEMENTED:** All tracking lost on process restart. | **Complete:** State persists in PostgreSQL; surviving crashes cleanly. | **Partial:** Survives restart, but limited strictly to workflow tasks. |
| **Concurrency Control** | **None:** Multi-replica execution leads to split-brain state. | **Row-Level Locking:** `SELECT FOR UPDATE SKIP LOCKED` ensures exactly-one worker evaluates a task. | Row-level locking on task mappings, but tightly couples workflow and SLA tables. |
| **Domain Scope** | Generic (any task UUID). | **Generic:** Tracks workflow tasks, scheduling jobs, or operational goals. | **Narrow:** Restricted strictly to workflow user tasks. |
| **Atomicity** | Snapshot rollback protects in-memory state on commit failure. | Database transaction guarantees atomic update of SLA state + outbox event. | Database transaction updates task mapping + outbox event. |
| **Database Migration** | Zero migrations. | Requires 1 new migration (additive, zero downtime). | Modifies existing core workflow table schema. |
| **Implementation Complexity** | Low (already implemented). | Moderate: SQLAlchemy model, repository, and service refactor. | Moderate: Schema alteration and model migration. |

### 6.4 Recommended Schema Specification for Option 2
```sql
CREATE TABLE task_sla_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(255) NOT NULL,
    task_id UUID NOT NULL,
    instance_id UUID NOT NULL,
    policy_key VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    created_at TIMESTAMPTZ NOT NULL,
    warning_at TIMESTAMPTZ NOT NULL,
    breach_at TIMESTAMPTZ NOT NULL,
    paused_at TIMESTAMPTZ,
    total_paused_seconds INT NOT NULL DEFAULT 0,
    remaining_warning_seconds INT,
    remaining_breach_seconds INT,
    warning_fired_at TIMESTAMPTZ,
    breach_fired_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_task_sla_tenant_task UNIQUE (tenant_id, task_id),
    CONSTRAINT ck_task_sla_status CHECK (status IN ('RUNNING', 'WARNING', 'BREACHED', 'PAUSED', 'COMPLETED'))
);
CREATE INDEX ix_task_sla_eval ON task_sla_states (tenant_id, status, warning_at, breach_at);
```

### 6.5 Recommendation & Acceptance Criteria
- **Recommendation & Status:** **Option 2 (Dedicated `task_sla_states` Table)** is **`PROPOSED`** (not implemented). Creating the dedicated `task_sla_states` table and refactoring `SlaService` to persist state via `SqlAlchemyUnitOfWork` is recommended to eliminate restart data loss and multi-worker split brain.
- **Roadmap Governance & Dependencies:** Implementing Option 2 is **NOT** automatically assigned to Phase DQ-5. It requires:
  1. Formal Architecture Review Board (ARB) approval for the new Alembic database migration and schema addition.
  2. Explicit roadmap authorization to schedule the database persistence refactor.
- **Testable Acceptance Criteria (Upon Implementation):**
  1. Terminating and restarting the worker process preserves all active SLA thresholds, paused durations, and fired flags.
  2. Concurrent evaluations across multiple worker processes acquire row-level locks without duplicate warning or breach event emissions.
  3. Failed outer PostgreSQL commits cleanly roll back both the `task_sla_states` update and the `outbox_events` row.

---

## 7. Blocked Notification Recovery Capability Audit

**Capability Status:** `NOT_IMPLEMENTED`

A comprehensive codebase audit of `app/events/inbox/`, `app/events/outbox/`, `app/integrations/engagement/`, `app/audit/`, and all API routes confirms the following empirical findings regarding blocked notifications:

```
[Incoming Broker Message]
          │
          ▼
   [ConsumerEventProcessor]
          ├── 1. Writes (consumer_name, event_id) to inbox_messages -> COMMITTED
          ├── 2. Handler appends notification.dispatch.blocked to audit_events -> COMMITTED
          └── 3. Returns cleanly -> RabbitMQ message.ack() -> REMOVED FROM QUEUE
```

### Detailed Capability Assessment Table

| Audit Point | Current Code Reality | Status Classification |
|---|---|---|
| **Original source event identity** | Preserved in `outbox_events` (status `DELIVERED`) and in `audit_events.details.source_event_id`. | `VERIFIED_IMPLEMENTED` |
| **Committed inbox deduplication marker** | Permanent record committed in `inbox_messages`. Subsequent delivery of identical `event_id` is recognized as duplicate and discarded. | `VERIFIED_IMPLEMENTED` |
| **Blocked audit record** | Append-only record committed in `audit_events` with `event_type="notification.dispatch.blocked"` and `outcome=DENIED`. | `VERIFIED_IMPLEMENTED` |
| **Durable notification intent** | **Not stored independently.** Original `NotificationCommand` was formulated in memory and discarded. | `NOT_IMPLEMENTED` |
| **Executable replay entry point** | **Zero CLI commands, zero API routes, and zero worker replay loops exist in the codebase.** | `NOT_IMPLEMENTED` |
| **Operator authorization controls** | Zero administrative replay permission grants or RBAC policies exist for notification resubmission. | `NOT_IMPLEMENTED` |
| **Stable command_id on replay** | Deterministic ID formula `notif-{type}-{event_id}` is supported in code, but no mechanism exists to trigger it. | `VERIFIED_WITH_TEST_DOUBLES` |
| **Duplicate prevention on replay** | Because `inbox_messages` already contains the `event_id`, any broker-level replay is rejected as duplicate. | `VERIFIED_IMPLEMENTED` |
| **Recovery after worker restart** | Blocked notification cannot be recovered or re-evaluated automatically after restart. | `NOT_IMPLEMENTED` |
| **Tenant isolation on replay** | Audit logs contain `tenant_id`, but no execution path exists to act upon it. | `NOT_IMPLEMENTED` |
| **E-8 delivery retry** | Dispatcher retries transient network errors (3 attempts), but permanent recipient blocks cannot be retried. | `NOT_IMPLEMENTED` |

> [!IMPORTANT]
> **Definitive Finding on Notification Replay:**
> - **Diagnostic Evidence Only:** Querying PostgreSQL `audit_events` provides essential compliance and diagnostic evidence, but **querying audit logs does NOT constitute an executable replay or recovery mechanism**.
> - **Inbox Deduplication Blocks Direct Broker Replay:** Because `inbox_messages` commits a deduplication record and ACKs the broker message when a notification is blocked under `DEC-E8-02`, republishing or replaying the raw event at the broker level results in duplicate suppression (`duplicate_ignored`).
> - **Zero Replay CLI or API Exists (`NOT_IMPLEMENTED`):** An administrative replay API route (`POST /api/v1/admin/notifications/replay`) or CLI is **PROPOSED** but **NOT IMPLEMENTED**. Zero replay CLI commands, zero API routes, and zero worker replay loops exist in the codebase today.
> - **Roadmap Governance:** Durable command persistence and authorized replay require architectural approval (`AD-DQ4-01` Option C, `DEC-E8-02`) and explicit roadmap authorization; they must NOT be claimed as operational or automatically assigned to Phase DQ-5 without formal approval.

---

## 8. Summary of Architectural Decisions & Action Items

| Reference | Scope | Core Proposal / Decision | Status Classification | Required Governance Action | Roadmap Implementation Dependency |
|---|---|---|:---:|---|:---:|
| **`AD-DQ4-01`** | Notification Outbox Architecture | Option C (Two-Stage Outbox Pipeline) to decouple external network I/O from database transactions. | `PROPOSED` / `APPROVAL_REQUIRED` | ARB approval to amend ADR-012 text. | Requires explicit roadmap approval & ARB sign-off (Not auto DQ-5). |
| **`DEC-E8-02`** | Recipient Resolution Authority | Assign directory resolution ownership to E-8 (Option 1); fail-closed gating for provisional defaults in E-7 (Option 3). | `PROPOSED` / `APPROVAL_REQUIRED` | Enterprise IT / SIS directory team confirmation. | Requires explicit roadmap approval & institutional IT federation. |
| **`DEC-E8-04`** | Template & Rendering Ownership | Assign template authoring, branding, HTML rendering, and localization strictly to E-8 (Option 1). | `PROPOSED` / `APPROVAL_REQUIRED` | Institutional communications team approval. | Requires institutional communications team sign-off. |
| **`AD-DQ4-02`** | SLA State Persistence | Introduce dedicated `task_sla_states` PostgreSQL table with row-level locking to guarantee restart durability. | `PROPOSED` / `APPROVAL_REQUIRED` | ARB approval for Alembic database migration. | Requires explicit roadmap approval & ARB sign-off (Not auto DQ-5). |
| **Recovery Engine** | Blocked Notification Replay | Administrative replay endpoint (`POST /api/v1/admin/notifications/replay`) operating on `notification_outbox`. | `PROPOSED` / `NOT_IMPLEMENTED` | Product & security approval for administrative replay role. | Requires explicit roadmap approval & implementation. |

---

## 9. Pull Request, Baseline Verification & External Blockers

### Baseline Verification Status
- **PR Number:** [#4](https://github.com/Itsbhavesh1101/Ops/pull/4)
- **Target Branch:** `master`
- **Source Branch:** `quality/dq4-integration-reliability`
- **Reviewed Remote Head SHA:** `0410372df0dd72a0b16bbbc9c5889f641c6e5708`
- **GitHub Actions CI Status:** **100% Green / Passing** (Run ID `40707712855`)
- **Phase-8 Frozen RC Tag:** `phase8-rc-38d080fadab8` (`03e6305256db3d838b472aaac50893ddb513414e`) — intact and unaltered.
- **Phase DQ-5 Status:** **NOT started**. Zero code changes or migrations for DQ-5 have been introduced.
- **Pull Request Integrity:** PR #4 remains open and unmerged.

### Test Evidence Dissection (Verified Against GitHub Actions CI Run #40707712855)
- **Pytest Global Discovery:** 2,293 tests collected (`pytest --collect-only -q`).
- **Backend Unit & Contract Suites:** 1,923 passed, 6 skipped (`tests/unit/`, `tests/contract/`).
- **Backend PostgreSQL Integration Tests:** 10 passed (`tests/integration/task_intelligence/`).
- **Total Backend Tests Executed in CI:** 1,933 passed, 6 skipped.
  *(Transparent disclosure: 354 specialized security, UAT, and live external tests are discovered in global collection but intentionally not executed in standard CI baseline without dedicated multi-service infrastructure. Do not claim all collected tests were executed.)*
- **Frontend Test Suite (Vitest):** 146 passed across 19 test files.
- **Static Analysis:**
  - `ruff check .`: All checks passed (0 errors).
  - `mypy app`: Success: no issues found in 265 source files.
  - Frontend: 0 ESLint errors, 0 TypeScript errors, clean Vite production build.

### Canonical External Blockers (Preserved Verbatim)
In strict alignment with `docs/development-quality/E7_Development_Completion_Remediation_Plan.md`, the four canonical blockers are preserved:
- **`BLK-01: MYSQL_LIVE_READ_VERIFICATION`**: Live institutional MySQL ERP read verification blocked pending institutional network access, VPN configuration, and read-only credentials.
- **`BLK-02: PRODUCTION_COMPANY_OIDC`**: Live institutional OIDC provider (Okta/Azure AD) configuration blocked pending enterprise IT federation. Local JWT mock used.
- **`BLK-03: E8_NOTIFICATION_INTEGRATION`**: Live E-8 Notification Engine external endpoint connectivity blocked pending E-8 deployment and mutual TLS certificate exchange. AMQP/HTTP test doubles used.
- **`BLK-04: PRODUCTION_GATEWAY`**: Production reverse proxy and API gateway mutual TLS routing blocked pending institutional cloud infrastructure provisioning. Local routing verified.

### Distinct Operational & Durability Concerns (Non-Blockers)
1. **Google Calendar Live Synchronization Boundary:** External integration boundary awaiting institutional Google Workspace service-account credentials and OAuth2 consent. Tracked separately under `docs/phase-4/PHASE_4_CALENDAR_SYNC.md`.
2. **SLA State Durability & Multi-Worker Concurrency (`AD-DQ4-02`):** Internal application state durability concern regarding in-memory `_states` and restart data loss. Tracked as an internal architecture decision requiring ARB approval and explicit roadmap authorization.

---

## 10. Conclusion & Final Verdict

The technical corrections and documentation reconciliation for Phase DQ-4 pass all linting, typing, unit testing, and GitHub Actions CI pipelines. The four principal architectural proposals (`AD-DQ4-01`, `DEC-E8-02`, `DEC-E8-04`, `AD-DQ4-02`) and the recovery engine status are formally distinguished, reconciled, and positioned for governance sign-off.

```
╔════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                ║
║             VERDICT: DQ4_DOCUMENTATION_RECONCILED_AWAITING_APPROVAL                            ║
║                                                                                                ║
║  1. Canonical external blockers verified: BLK-01, BLK-02, BLK-03, BLK-04 intact.               ║
║  2. Google Calendar and SLA durability concerns documented separately from canonical blockers. ║
║  3. Blocked notification recovery audited & verified as NOT IMPLEMENTED (no replay CLI/API).   ║
║  4. Audit logs documented as diagnostic evidence; inbox dedupe prevents duplicate replay.      ║
║  5. AD-DQ4-01 Option C, AD-DQ4-02, DEC-E8-02, and DEC-E8-04 classified as requiring explicit   ║
║     roadmap approval rather than automatically assigned to DQ-5.                               ║
║  6. Proposal vs. implementation clearly distinguished across all components.                   ║
║  7. Verified CI Run #40707712855: 2,293 collected; 1,933 passed backend; 6 skipped; 146 UI.     ║
║  8. Static analysis: Ruff clean (0 errors), Mypy clean (265 source files checked).             ║
║  9. PR #4 remains open at https://github.com/Itsbhavesh1101/Ops/pull/4 (unmerged).             ║
║ 10. Frozen Phase-8 RC tag (phase8-rc-38d080fadab8) unaltered.                                  ║
║ 11. Phase DQ-5 NOT started.                                                                    ║
║                                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════════════════════╝
```

