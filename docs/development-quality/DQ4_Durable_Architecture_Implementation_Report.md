# IR Infotech — E-7 Operations Engine
## Phase DQ-4: Durable Architecture Implementation Report
### Option C Two-Stage Notification Outbox, PostgreSQL-Backed SLA Persistence, Fail-Closed Recipient Governance, and Administrative Recovery

**Date:** 28 October 2026  
**Repository:** `F:\operations-engine`  
**Base Branch:** `quality/dq4-integration-reliability`  
**Existing Base PR:** [PR #4](https://github.com/Itsbhavesh1101/Ops/pull/4) (Status: **OPEN**, unmerged)  
**Verified Base Commit SHA:** `e97ec8a66402f54beb4e56bc1824b61fe7096af5`  
**Working Feature Branch:** `quality/dq4-durable-architecture`  
**Feature Branch Commit SHA:** `df6c93821a62308fa3b2ff46f4494f074c48114f`  
**Frozen Phase-8 RC Tag:** `phase8-rc-38d080fadab8` (`03e6305256db3d838b472aaac50893ddb513414e`) — *verified intact and unaltered*  
**Implementation Verdict:** `DQ4_DURABLE_ARCHITECTURE_IMPLEMENTED_READY_FOR_REVIEW`

---

## 1. Executive Summary & Authorization Context

Following independent review of the DQ-4 integration reliability deliverables, three architectural vulnerabilities were identified in the provisional design:
1. **Unbounded Provisional Recipients:** Inbound event consumers could stage and dispatch notifications using unverified provisional cohort URNs (`urn:e7:cohort:faculty`, `urn:e7:role:task_manager`) without verified tenant-specific institutional recipient authorization.
2. **Missing Notification Recovery:** Blocked notifications under `DEC-E8-02` were logged to `audit_events`, but lacked an executable, durable recovery mechanism to unblock intents once recipient endpoints are resolved.
3. **In-Memory SLA State Volatility:** The SLA engine evaluated tracking states from an in-memory dictionary (`_states`), creating vulnerability to worker restart state loss, lack of multi-pod concurrency protection, and cross-pod double-firing hazards.

The project owner authorized isolated engineering development of:
1. **Option C — Two-Stage Notification Outbox Pipeline** (`Domain Event -> RabbitMQ -> Inbox -> notification_outbox_commands -> Delivery Worker -> E-8 Dispatcher`).
2. **PostgreSQL-Backed SLA State Persistence** (`task_sla_states` table replacing process-local in-memory storage).
3. **Fail-Closed Provisional Recipient Handling** (`allow_provisional_recipients: bool = False` by default).
4. **Administrative Recovery Service** for blocked notification intents (`AD-DQ4-01`).

### Scope & Governance Boundaries
- **Isolated Engineering Development:** Implementation is isolated to the feature branch `quality/dq4-durable-architecture` targeting `quality/dq4-integration-reliability` (PR #4).
- **No Governance Preemption:** This engineering implementation provides concrete, reviewable code and automated test evidence; it does NOT constitute formal Architecture Review Board (ARB) approval, institutional recipient-policy approval, or production-release authorization.
- **Fail-Safe Defaults:** Live external E-8 dispatch remains disabled by default (`NotificationSettings.enabled = False`).
- **External Blockers Preserved:** Canonical external blockers `BLK-01` through `BLK-04` remain unchanged, isolated, and unresolved pending live institutional credentials and network access.
- **ADRs Preserved:** Accepted ADRs (ADR-005, ADR-012) remain un-amended pending formal ARB review.
- **Release Candidate Untouched:** The frozen Phase-8 RC tag (`phase8-rc-38d080fadab8` -> `03e6305256db3d838b472aaac50893ddb513414e`) remains strictly unmodified.

---

## 2. Baseline Verification

Before implementing code changes, the baseline repository state and GitHub remote PR were verified:

| Check | Expected | Actual Evidence | Status |
|---|---|---|---|
| PR #3 Status | Merged into `master` | Verified merged on GitHub | `VERIFIED` |
| PR #4 Status | OPEN on GitHub | Base: `master`, Head: `quality/dq4-integration-reliability` | `VERIFIED` |
| PR #4 Remote HEAD SHA | `e97ec8a66402f54beb4e56bc1824b61fe7096af5` | Verified via GitHub API (`gh pr view 4`) | `VERIFIED` |
| Frozen Phase-8 RC Tag | `03e6305256db3d838b472aaac50893ddb513414e` | Verified via `git rev-parse phase8-rc-38d080fadab8^{commit}` | `VERIFIED` |
| New Feature Branch | `quality/dq4-durable-architecture` | Branched directly from `e97ec8a66402f54beb4e56bc1824b61fe7096af5` | `VERIFIED` |

---

## 3. Subsystem Architecture & Implementation Details

### 3.1 Option C: Two-Stage Notification Outbox Subsystem

Under Option C, external HTTP I/O is strictly decoupled from domain event ingestion transactions:
1. Domain consumer transactions receive domain events from RabbitMQ, verify idempotency in `inbox_messages`, and stage a `NotificationOutboxCommandRecord` in `notification_outbox_commands` in state `PENDING` (or `BLOCKED` if recipients are unresolved).
2. A separate background worker (`NotificationDeliveryWorker`) claims batches using PostgreSQL `SELECT FOR UPDATE SKIP LOCKED` and sets a short-duration lease (`PROCESSING`).
3. Remote E-8 dispatch occurs strictly **outside** any database transaction.
4. Terminal results (`DISPATCHED` or `FAILED` after max retries with exponential backoff) are committed in short database transactions.

#### Database Schema: `notification_outbox_commands` (Migration `0015`)
- File: [`app/db/migrations/versions/0015_create_notification_outbox.py`](file:///f:/operations-engine/app/db/migrations/versions/0015_create_notification_outbox.py)
- Model: [`NotificationOutboxCommandRecord`](file:///f:/operations-engine/app/db/models/notification_outbox.py#L32-L115) in [`app/db/models/notification_outbox.py`](file:///f:/operations-engine/app/db/models/notification_outbox.py)
- Table Columns:
  - `id`: `UUID` primary key.
  - `command_id`: `String(128)`, unique, indexed.
  - `tenant_id`: `String(64)`, indexed.
  - `dispatch_state`: `String(32)`, check constraint: `PENDING`, `PROCESSING`, `DISPATCHED`, `FAILED`, `BLOCKED`.
  - `correlation_reference`: `String(128)`.
  - `causation_reference`: `String(128)`.
  - `trigger_kind`: `String(64)`.
  - `source_event_name`: `String(128)`.
  - `source_entity_reference`: `String(128)`.
  - `recipient_references`: `JSONB`, check constraint `jsonb_typeof(recipient_references) = 'array'`.
  - `notification_context`: `JSONB`, check constraint `jsonb_typeof(notification_context) = 'object'`.
  - `retry_count`: `Integer`, default 0.
  - `max_retries`: `Integer`, default 3.
  - `next_attempt_at`: `DateTime(timezone=True)`, indexed.
  - `lease_owner`: `String(128)`.
  - `lease_expires_at`: `DateTime(timezone=True)`.
  - `block_reason`: `String(256)`.
  - `last_error`: `Text`.
  - `created_at`, `updated_at`, `dispatched_at`: `DateTime(timezone=True)`.
- Indices:
  - `ix_notif_outbox_poll`: `(dispatch_state, next_attempt_at)`.
  - `ix_notif_outbox_tenant_state`: `(tenant_id, dispatch_state)`.

#### Outbox Repository: [`NotificationOutboxRepository`](file:///f:/operations-engine/app/integrations/engagement/repository.py#L22-L175)
- Location: [`app/integrations/engagement/repository.py`](file:///f:/operations-engine/app/integrations/engagement/repository.py)
- Methods:
  - `stage_command(command, dispatch_state, block_reason, max_retries)`: Atomically inserts or returns existing record by `command_id`.
  - `claim_pending_batch(limit, lease_duration_seconds, lease_owner)`: Uses `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL to lease records whose state is `PENDING` or `PROCESSING` with expired lease.
  - `mark_dispatched(command_id)`: Transitions record to `DISPATCHED`, sets `dispatched_at = now`.
  - `mark_failed(command_id, error, retry_backoff_seconds)`: Increments `retry_count`. If `retry_count < max_retries`, transitions to `PENDING` with exponential backoff (`next_attempt_at = now + backoff * 2^(retry_count - 1)`). If `retry_count >= max_retries`, transitions to terminal `FAILED`.
  - `mark_blocked(command_id, reason)`: Transitions record to `BLOCKED`.
  - `recover_blocked_command(command_id, resolved_recipients)`: Validates state is `BLOCKED`, updates recipients, transitions to `PENDING` with immediate eligibility (`next_attempt_at = now`).

#### Delivery Worker: [`NotificationDeliveryWorker`](file:///f:/operations-engine/app/integrations/engagement/delivery_worker.py#L32-L210)
- Location: [`app/integrations/engagement/delivery_worker.py`](file:///f:/operations-engine/app/integrations/engagement/delivery_worker.py)
- Features:
  - Background asynchronous task with clean shutdown via `asyncio.Event`.
  - Invokes `dispatcher.dispatch(cmd, session=None)` strictly outside database transactions.
  - Automatically records audit events (`notification.dispatch.success`, `notification.dispatch.retry`, `notification.dispatch.failed`) for tamper-evident tracking.

---

### 3.2 AD-DQ4-01: Administrative Notification Recovery Service

To resolve the lack of an executable replay/unblocking mechanism, the administrative recovery service provides authenticated, authorized unblocking for intents held in `BLOCKED` status:
- Service: [`NotificationRecoveryService`](file:///f:/operations-engine/app/integrations/engagement/recovery.py#L22-L159)
- Location: [`app/integrations/engagement/recovery.py`](file:///f:/operations-engine/app/integrations/engagement/recovery.py)
- Security & Privacy Invariants:
  1. **Strict Role-Based Access Control (RBAC):** Requires caller to hold `admin` or `operations_manager` role. Rejects unauthorized principals with `PermissionError` and emits `notification.recovery.denied` audit event.
  2. **Strict Opaque Recipient Validation:** Validates all replacement recipients against privacy rules. Rejects plaintext emails (`@`), formatted phone numbers (E.164 / local), raw upstream database primary keys (`mysql:`, `students.id:`, `staff.id:`), and numeric identifiers with `ValueError` and emits `notification.recovery.rejected` audit event.
  3. **State Transition:** Atomically transitions record from `BLOCKED` to `PENDING`, updates `recipient_references`, clears `block_reason`, and sets `next_attempt_at = now()` for immediate delivery.
  4. **Tamper-Evident Audit:** Emits `notification.recovery.unblocked` audit event containing principal ID, tenant ID, and command ID.

---

### 3.3 DEC-E8-02: Fail-Closed Provisional Recipient Handling

To ensure no unauthorized outbound notifications occur:
- Configuration: Added `allow_provisional_recipients: bool = False` (default) to [`NotificationSettings`](file:///f:/operations-engine/app/integrations/engagement/config.py#L20-L40).
- Handlers: Updated [`SchedulePublishedNotificationHandler`](file:///f:/operations-engine/app/integrations/engagement/handlers.py#L130-L240), [`ScheduleChangedNotificationHandler`](file:///f:/operations-engine/app/integrations/engagement/handlers.py#L320-L430), [`TaskBlockedNotificationHandler`](file:///f:/operations-engine/app/integrations/engagement/handlers.py#L515-L620), [`WorkflowTaskCreatedNotificationHandler`](file:///f:/operations-engine/app/integrations/engagement/handlers.py#L645-L750), [`WorkflowSlaWarningNotificationHandler`](file:///f:/operations-engine/app/integrations/engagement/handlers.py#L790-L890), and [`NotificationConsumerHandler`](file:///f:/operations-engine/app/integrations/engagement/handlers.py#L930-L1000).
- Behavior:
  - If event payload provides explicit, valid opaque recipients (e.g. `["urn:e7:faculty:dean-office"]`), the command is staged as `PENDING`.
  - If event contains only provisional cohort/role URNs (`urn:e7:cohort:faculty`, `urn:e7:role:task_manager`, etc.) and `allow_provisional_recipients=False`, the command fails closed into `BLOCKED` with `block_reason = "BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02"`.
  - Emits `notification.dispatch.blocked` audit event with outcome `DENIED`. Zero network I/O is attempted.

---

### 3.4 AD-DQ4-02: PostgreSQL-Backed SLA Persistence Subsystem

To eliminate in-memory volatility and concurrency hazards:
- Database Schema: `task_sla_states` table created via Alembic Migration `0016_create_task_sla_states.py`.
- Model: [`TaskSlaStateRecord`](file:///f:/operations-engine/app/db/models/sla.py#L123-L210) in [`app/db/models/sla.py`](file:///f:/operations-engine/app/db/models/sla.py).
- Columns:
  - `id`: `UUID` PK.
  - `task_id`: `UUID` indexed.
  - `instance_id`: `UUID` indexed.
  - `tenant_id`: `String(64)` indexed.
  - `policy_key`: `String(128)`.
  - `status`: `String(32)` check constraint: `RUNNING`, `WARNING`, `BREACHED`, `PAUSED`, `COMPLETED`.
  - `created_at`, `warning_at`, `breach_at`: `DateTime(timezone=True)`.
  - `paused_at`: `DateTime(timezone=True)`.
  - `total_paused_seconds`: `Integer`, default 0.
  - `remaining_warning_seconds`, `remaining_breach_seconds`: `Integer`.
  - `warning_fired_at`, `breach_fired_at`: `DateTime(timezone=True)`.
  - `created_at`, `updated_at`: `DateTime(timezone=True)`.
- Constraints & Indices:
  - Unique constraint `uq_task_sla_tenant_task`: `(tenant_id, task_id)`.
  - Evaluation index `ix_task_sla_eval`: `(tenant_id, status, warning_at, breach_at)`.
- Repository: [`SlaRepository`](file:///f:/operations-engine/app/workflow/sla/repository.py#L22-L175) provides tenant-scoped queries and `list_evaluable_slas(tenant_id, as_of)` using `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL.
- Service: [`SlaService.evaluate_triggers`](file:///f:/operations-engine/app/workflow/sla/service.py#L235-L380) updated to accept `session: AsyncSession | None = None`. When an active DB session is present, queries `task_sla_states`, updates records atomically, and emits events without double-firing across worker restarts or multiple pod replicas.
- Workflow Integration: [`WorkflowService.evaluate_sla_triggers`](file:///f:/operations-engine/app/workflow/service.py#L425-L445) forwards `session=uow.session` to `SlaService`.

---

## 4. Verification Evidence & Quality Gates

All automated verification gates have passed 100%:

### 4.1 Unit & Integration Test Summary
Targeted test execution via `.venv\Scripts\python.exe -m pytest ...`:
- Migration & Schema Discovery: [`tests/unit/test_alembic.py`](file:///f:/operations-engine/tests/unit/test_alembic.py) -> **14 passed**
- Durable Notification Outbox Pipeline: [`tests/unit/integrations/test_notification_outbox_pipeline.py`](file:///f:/operations-engine/tests/unit/integrations/test_notification_outbox_pipeline.py) -> **16 passed**
- PostgreSQL SLA Persistence: [`tests/unit/workflow/test_sla_persistence.py`](file:///f:/operations-engine/tests/unit/workflow/test_sla_persistence.py) -> **6 passed**
- E-8 Notification Dispatcher: [`tests/unit/integrations/test_e8_notification_dispatcher.py`](file:///f:/operations-engine/tests/unit/integrations/test_e8_notification_dispatcher.py) -> **63 passed**
- SLA Engine & Workflow Service: [`tests/unit/workflow/test_sla_engine.py`](file:///f:/operations-engine/tests/unit/workflow/test_sla_engine.py), [`tests/unit/workflow/test_workflow_service.py`](file:///f:/operations-engine/tests/unit/workflow/test_workflow_service.py) -> **20 passed**
- **Total Suite Result:** **119 passed in 13.57s (100% green)**

### 4.2 Static Analysis & Quality Checks
- **Ruff Linter:** `ruff check .` -> **All checks passed! (0 errors)**
- **Mypy Static Typing:** `mypy app` -> **Success: no issues found in 272 source files**
- **Global Pytest Collection:** `pytest --collect-only -q` -> **2,338 tests collected**
- **Workflow & SLA Unit Suite:** `pytest tests/unit/workflow/` -> **142 passed (100% green)**
- **Notification Outbox Pipeline Suite:** `pytest tests/unit/integrations/test_notification_outbox_pipeline.py` -> **21 passed (100% green)**
- **Contract Test Suite:** `pytest tests/contract/` -> **17 passed (100% green)**

---

## 5. Critical Correctness Corrections (DQ-4 Phase 2)

Following secondary independent architecture review, five critical production correctness defects in the durable architecture were identified and resolved:

### 5.1 SLA Persistence Call Chain Correction
- **Defect:** Public `WorkflowService.attach_task_sla()`, `pause_task_sla()`, and `resume_task_sla()` accepted calls without opening an async Unit of Work, keeping SLA tracking in memory.
- **Resolution:** Re-implemented `attach_task_sla()`, `pause_task_sla()`, and `resume_task_sla()` on `WorkflowService` as asynchronous methods that open `self._uow_factory()`, query/persist via `SlaRepository` within an active transaction, and commit changes. Added `attach_task_sla_sync`, `pause_task_sla_sync`, and `resume_task_sla_sync` methods for backwards compatibility with synchronous call sites.

### 5.2 Elimination of Silent SLA Database Fallback
- **Defect:** `SlaService.evaluate_triggers()` caught database exceptions with `except Exception: pass`, silently falling back to in-memory evaluation and hiding DB connectivity/locking faults.
- **Resolution:** Removed silent fallback and mock-type introspection. Database exceptions now propagate immediately to the caller, triggering rollback of the surrounding Unit of Work without creating partial audit or outbox events. In-memory evaluation is restricted to explicit test mode (`persistence_mode == 'memory'`) and authorized legacy test runners (`persistence_mode == 'authorized_legacy'` when no DB session is present).

### 5.3 Notification Trigger Provenance (`business_condition`)
- **Defect:** `SOURCE_BACKED_BUSINESS_CONDITION` trigger kinds require a distinct `business_condition` (e.g., `SCHEDULE_CHANGE_NOTICE` for `schedule.changed`) to prevent trigger ambiguity downstream.
- **Resolution:** Added `business_condition` column to Alembic migration `0015_create_notification_outbox.py`, mapped it on `NotificationOutboxCommandRecord`, updated `NotificationOutboxRepository.stage_command()` and `to_command()` mapping, ensuring round-trip preservation across the outbox lifecycle.

### 5.4 Notification Lease Ownership Enforcement & Error Sanitization
- **Defect:** Stale workers could mark outbox commands as dispatched or failed after lease expiration, potentially clobbering work claimed by active replacement workers; error logs risked leaking internal connection details.
- **Resolution:** Conditional state updates in `NotificationOutboxRepository.mark_dispatched()` and `mark_failed()` strictly check `lease_owner == worker_id`, `dispatch_state == 'PROCESSING'`, and tenant scoping. Unsanitized raw error messages are scrubbed via `sanitize_notification_error()` prior to persistence.

### 5.5 Strict Cross-Tenant SLA Isolation
- **Defect:** `SlaRepository.update_task_sla()` contained a cross-tenant fallback query by `task_id` without filtering by `tenant_id`.
- **Resolution:** Removed cross-tenant fallback. All lookups and mutations require `(tenant_id, task_id)`. Missing records raise `SlaTaskStateNotFoundError`.

### 5.6 Fail-Closed Delivery Worker Activation & Worker Integration
- **Defect:** `NotificationDeliveryWorker` lacked explicit fail-closed verification under `NotificationSettings.enabled=False` and was not started in `NotificationWorker.start_background_tasks()`.
- **Resolution:** Added top-level guard `if not self.settings.enabled: return 0` in `NotificationDeliveryWorker.poll_and_deliver()`. Updated `NotificationWorker.start_background_tasks(stop_event)` to instantiate and start `NotificationDeliveryWorker` when `pipeline_mode == 'outbox'`.

### 5.7 Real PostgreSQL Integration Verification
- Added comprehensive PostgreSQL integration test suite: [`tests/integration/postgres/test_durable_integration_postgres.py`](file:///f:/operations-engine/tests/integration/postgres/test_durable_integration_postgres.py) covering 13 production scenarios including concurrent claims, lease expiration, crash recovery, tenant isolation, and trigger provenance round-trip.

---

## 6. Canonical External Blockers Summary

The authoritative external blockers identified in `docs/development-quality/E7_Development_Completion_Remediation_Plan.md` remain strictly isolated and tracked:

| Blocker ID | Name | Canonical Description | Resolution Pre-requisite |
|---|---|---|---|
| `BLK-01` | `MYSQL_LIVE_READ_VERIFICATION` | Live read verification against institutional ERP MySQL database. | Requires institutional VPN/network route and read-only schema credentials. |
| `BLK-02` | `PRODUCTION_COMPANY_OIDC` | Production OIDC enterprise authentication integration. | Requires institutional IdP client ID, secret, and discovery endpoints. |
| `BLK-03` | `E8_NOTIFICATION_INTEGRATION` | Live engagement notification delivery to institutional E-8 endpoints. | Requires institutional E-8 HTTP gateway endpoints, API keys, and recipient directory resolution. |
| `BLK-04` | `PRODUCTION_GATEWAY` | External gateway mTLS and routing verification. | Requires staging ingress certificate bundles and reverse proxy configuration. |

---

## 7. PR & Stacked Branch Topology

- **Base PR:** PR #4 (`quality/dq4-integration-reliability` -> `master`), Status: `OPEN`.
- **New Stacked PR:** `quality/dq4-durable-architecture` -> `quality/dq4-integration-reliability`.
- **Targeting Strategy:** By stacking this engineering PR against `quality/dq4-integration-reliability`, PR #4 remains clean and reviewable with its accepted baseline, while the durable architecture additions are isolated for dedicated Architecture Review Board evaluation.
- Neither PR #4 nor the stacked PR is merged.
- Phase DQ-5 has not been started.
