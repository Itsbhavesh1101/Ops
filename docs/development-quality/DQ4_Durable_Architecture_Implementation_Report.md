# IR Infotech — E-7 Operations Engine
## Phase DQ-4: Durable Architecture Implementation Report
### Option C Two-Stage Notification Outbox, PostgreSQL-Backed SLA Persistence, Fail-Closed Recipient Governance, and Administrative Recovery

**Date:** 29 October 2026  
**Repository:** `F:\operations-engine`  
**Base Branch:** `quality/dq4-integration-reliability`  
**Existing Base PR:** [PR #4](https://github.com/Itsbhavesh1101/Ops/pull/4) (Status: **OPEN**, unmerged)  
**Verified Base Commit SHA:** `e97ec8a66402f54beb4e56bc1824b61fe7096af5`  
**Working Feature Branch:** `quality/dq4-durable-architecture`  
**Stacked PR:** [PR #5](https://github.com/Itsbhavesh1101/Ops/pull/5) (Status: **OPEN**, unmerged)  
**Feature Branch Baseline SHA:** `f01b8d76648e998d72ef58aea284957065b573f0`  
**Frozen Phase-8 RC Tag:** `phase8-rc-38d080fadab8` (`03e6305256db3d838b472aaac50893ddb513414e`) — *verified intact and unaltered*  
**Implementation Verdict:** `DQ4_ENGINEERING_DECISIONS_RECORDED_FORMAL_APPROVAL_PENDING`

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
- Durable Notification Outbox Pipeline: [`tests/unit/integrations/test_notification_outbox_pipeline.py`](file:///f:/operations-engine/tests/unit/integrations/test_notification_outbox_pipeline.py) -> **22 passed**
- PostgreSQL SLA Persistence & Multi-Instance Safety: [`tests/unit/workflow/test_sla_persistence.py`](file:///f:/operations-engine/tests/unit/workflow/test_sla_persistence.py) -> **15 passed**
- E-8 Notification Dispatcher: [`tests/unit/integrations/test_e8_notification_dispatcher.py`](file:///f:/operations-engine/tests/unit/integrations/test_e8_notification_dispatcher.py) -> **63 passed**
- SLA Engine & Workflow Service: [`tests/unit/workflow/test_sla_engine.py`](file:///f:/operations-engine/tests/unit/workflow/test_sla_engine.py), [`tests/unit/workflow/test_workflow_service.py`](file:///f:/operations-engine/tests/unit/workflow/test_workflow_service.py) -> **20 passed**
- Entire Workflow Domain Suite: [`tests/unit/workflow/`](file:///f:/operations-engine/tests/unit/workflow/) -> **146 passed**
- **Total Unit & Contract Execution:** **100% green**

### 4.2 Static Analysis & Quality Checks
- **Ruff Linter:** `ruff check .` -> **All checks passed! (0 errors)**
- **Mypy Static Typing:** `mypy app` -> **Success: no issues found in 272 source files**
- **Global Pytest Collection:** `pytest --collect-only -q` -> **2,343 tests collected**
- **Workflow & SLA Unit Suite:** `pytest tests/unit/workflow/` -> **146 passed (100% green)**
- **Notification Outbox Pipeline Suite:** `pytest tests/unit/integrations/test_notification_outbox_pipeline.py` -> **22 passed (100% green)**
- **Contract Test Suite:** `pytest tests/contract/` -> **17 passed (100% green)**
- **Durable Architecture PostgreSQL Integration Suite:** [`tests/integration/postgres/test_durable_integration_postgres.py`](file:///f:/operations-engine/tests/integration/postgres/test_durable_integration_postgres.py) -> **13 tests configured and verified in CI workflow**

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

## 6. Final Durable Architecture Verification (DQ-4 Phase 3)

Following tertiary independent verification, final reliability corrections and actual PostgreSQL CI integration were established:

### 6.1 SLA Transaction Consistency & Rollback Eviction
- **Authoritative Database State:** In `WorkflowService` and `SlaService`, PostgreSQL is authoritative. When an active session is provided (`session is not None`), `pause_task_sla_async()` and `resume_task_sla_async()` load domain states directly from `SlaRepository.get_task_sla(tenant_id, task_id)`. Multi-instance pods cannot overwrite newer database state with stale in-memory state.
- **Rollback Eviction:** If an outer transaction flush or commit fails, `self._sla_service.evict_task_sla(task_id)` is invoked across `attach_task_sla()`, `pause_task_sla()`, and `resume_task_sla()`, preventing phantom or stale records from polluting in-memory state.
- **Fail-Closed Synchronous Methods:** Synchronous legacy methods (`attach_task_sla_sync`, `pause_task_sla_sync`, `resume_task_sla_sync`) strictly verify `if self.persistence_mode == "database": raise RuntimeError(...)`, eliminating silent bypass of PostgreSQL persistence in production.

### 6.2 Strict Lease Ownership & Fail-Closed Transitions
- **Fail-Closed Lease Ownership:** In `NotificationOutboxRepository.mark_dispatched()` and `mark_failed()`, both `lease_owner` and `tenant_id` are strictly mandatory (`if not lease_owner or not lease_owner.strip() or not tenant_id or not tenant_id.strip(): return False`). Missing lease ownership or tenant scoping fails closed immediately.
- **State Protection:** Conditional updates enforce `dispatch_state == 'PROCESSING'` and `lease_owner == active_worker_id`. Stale workers whose lease expired or was reclaimed by another worker cannot complete or fail reclaimed commands. Terminal states (`DISPATCHED`, `FAILED`, `BLOCKED`) remain protected against invalid reversion.

### 6.3 Real PostgreSQL Integration Suite Execution (13/13)
The 13 durable integration tests in [`tests/integration/postgres/test_durable_integration_postgres.py`](file:///f:/operations-engine/tests/integration/postgres/test_durable_integration_postgres.py) were executed against disposable PostgreSQL 16 via `E7_RUN_POSTGRES_INTEGRATION=1`:
1. `test_pg_notification_command_persistence`
2. `test_pg_inbox_and_command_transactional_atomicity`
3. `test_pg_database_rollback_prevents_partial_commit`
4. `test_pg_concurrent_command_claims_skip_locked`
5. `test_pg_lease_expiration_and_stale_worker_rejection`
6. `test_pg_notification_retry_state_transitions`
7. `test_pg_tenant_scoped_recovery`
8. `test_pg_persisted_sla_registration`
9. `test_pg_persisted_pause_and_resume`
10. `test_pg_restart_recovery`
11. `test_pg_concurrent_sla_warning_evaluation`
12. `test_pg_cross_tenant_negative_access`
13. `test_pg_complete_trigger_provenance_round_trip`

All 13 tests execute with strict tenant isolation, isolated test database lifecycle (`operations_engine_test`), and zero mutation of core tables.

### 6.4 GitHub Actions CI Verification Gate
Updated [`.github/workflows/ci.yml`](file:///f:/operations-engine/.github/workflows/ci.yml) to add the step `Run Durable Architecture PostgreSQL Integration Tests`, configuring `E7_RUN_POSTGRES_INTEGRATION: "1"`, `POSTGRES_PORT: "5432"`, `POSTGRES_HOST: "localhost"`, `POSTGRES_USER: "postgres"`, `POSTGRES_PASSWORD: "postgres"`. This executes the 13 durable architecture integration tests against the disposable PostgreSQL 16 Alpine container in CI, providing verified automated gate enforcement.

CI Execution Evidence:
- **Pull Request CI Run:** [Run 40749815245](https://github.com/Itsbhavesh1101/Ops/actions/runs/40749815245) — **SUCCESS**
  - `Phase-1 Foundation & CI Baseline`: Completed successfully in 3m23s.
  - `Run Durable Architecture PostgreSQL Integration Tests`: **13 passed in 3.29s (100% green)**.
  - `Frontend Quality Gates & Tests`: Completed successfully in 1m12s.
- **Branch Push CI Run:** [Run 40749806954](https://github.com/Itsbhavesh1101/Ops/actions/runs/40749806954) — **SUCCESS**
  - `Phase-1 Foundation & CI Baseline`: Completed successfully in 3m12s.
  - `Frontend Quality Gates & Tests`: Completed successfully in 47s.

---

## 7. Final Concurrency, Lease Expiration & Transaction Closure (DQ-4 Phase 4)

Following quaternary independent engineering review, final concurrency controls, lease expiration semantics, and external transaction lifecycle bindings were implemented and verified:

### 7.1 Notification Lease Expiration Contract (DEC-LE-01)
- **Active Lease Completion:** Workers holding an active lease (`lease_expires_at > now`) retain exclusive authority to mark commands `DISPATCHED` or `FAILED`.
- **Expired Lease Without Reassignment:**
  - When `enforce_lease_expiration=True` (default in `NotificationSettings` and PostgreSQL queries), expiration strictly invalidates completion and failure rights (`mark_dispatched` and `mark_failed` return `False`). This eliminates split-brain races where a stale worker attempts to complete a command while background batch claimers (`claim_pending_batch`) are reclaiming the expired command.
  - When `enforce_lease_expiration=False` (grace period), completion is permitted only if the command remains in `PROCESSING` and `lease_owner` has not been reassigned.
- **Reclaimed Lease Rejection:** If another worker has reclaimed the expired lease (updating `lease_owner`), the stale worker is strictly rejected under both strict and grace modes.
- **Fail-Closed Identity Checks:** Missing or whitespace `lease_owner` or `tenant_id` fails closed (`False`). Wrong tenant or worker references fail closed.
- **Terminal State Protection:** Commands in `DISPATCHED`, `FAILED`, or `BLOCKED` cannot be updated or reverted.
- **Retry Counter Consistency:** Failed attempts by workers without valid active leases do not increment `retry_count`.

### 7.2 Concurrent SLA Pause/Resume Serialization via Row-Level Locking
- **Row-Level Locking:** In [`app/workflow/sla/repository.py`](file:///f:/operations-engine/app/workflow/sla/repository.py), `get_task_sla()` and `update_task_sla()` accept `for_update: bool = False`. On PostgreSQL, `for_update=True` applies `SELECT ... FOR UPDATE`, acquiring exclusive row locks on `task_sla_states`.
- **Deterministic Serialization:** In [`app/workflow/sla/service.py`](file:///f:/operations-engine/app/workflow/sla/service.py), `pause_task_sla_async()` and `resume_task_sla_async()` pass `for_update=True`. Concurrent pause or resume operations serialize cleanly in PostgreSQL.
- **Idempotent Transitions & Duration Preservation:** Subsequent concurrent pause requests detect `state.status == SlaStatus.PAUSED` and no-op without duplicating pause durations or corrupting remaining business-calendar metrics. Subsequent concurrent resume requests detect `state.status == SlaStatus.RUNNING` and no-op without double-accumulating pause durations.
- **Strict Cross-Tenant Isolation:** Row queries and locks are strictly qualified by `tenant_id`. Negative cross-tenant operations raise `SlaTaskStateNotFoundError` without locking or modifying other tenants' records.

### 7.3 Externally Managed Transaction Rollback & Authoritative PostgreSQL Reloading
- **Session Lifecycle Binding:** In [`app/workflow/sla/service.py`](file:///f:/operations-engine/app/workflow/sla/service.py), `_bind_session_lifecycle()` listens to SQLAlchemy session events (`after_rollback`, `after_commit`) on caller-owned sessions. If a caller rolls back externally without committing:
  - On `attach_task_sla()`, the uncommitted task is evicted from in-memory cache (`_states.pop(task_id)`).
  - On `pause_task_sla()` or `resume_task_sla()`, the in-memory cache is restored to the previous committed state.
- **Authoritative Database Reloading:** [`WorkflowService.get_task_sla()`](file:///f:/operations-engine/app/workflow/service.py) and `SlaService.get_task_sla_async()` reload domain states directly from `SlaRepository.get_task_sla(tenant_id, task_id)` when a session or UoW is used, keeping in-memory caches synchronized with authoritative PostgreSQL records. If a record was rolled back, lookups raise `SlaTaskStateNotFoundError`.

### 7.4 Real PostgreSQL Integration Suite Expansion (16/16)
The PostgreSQL integration test suite in [`tests/integration/postgres/test_durable_integration_postgres.py`](file:///f:/operations-engine/tests/integration/postgres/test_durable_integration_postgres.py) was expanded to 16 comprehensive production scenarios:
1. `test_pg_notification_command_persistence`
2. `test_pg_inbox_and_command_transactional_atomicity`
3. `test_pg_database_rollback_prevents_partial_commit`
4. `test_pg_concurrent_command_claims_skip_locked`
5. `test_pg_lease_expiration_and_stale_worker_rejection`
6. `test_pg_notification_retry_state_transitions`
7. `test_pg_tenant_scoped_recovery`
8. `test_pg_persisted_sla_registration`
9. `test_pg_persisted_pause_and_resume`
10. `test_pg_restart_recovery`
11. `test_pg_concurrent_sla_warning_evaluation`
12. `test_pg_cross_tenant_negative_access`
13. `test_pg_complete_trigger_provenance_round_trip`
14. `test_pg_lease_expiration_contract_and_unassigned_semantics`
15. `test_pg_concurrent_sla_pause_resume_row_locking`
16. `test_pg_external_session_rollback_authoritative_reloading`

### 7.5 Verified GitHub Actions CI Evidence (Runs 40757937011 & 40757925636)
- **Pull Request CI Run:** [Run 40757937011](https://github.com/Itsbhavesh1101/Ops/actions/runs/40757937011) — **SUCCESS** (2m44s)
  - `Phase-1 Foundation & CI Baseline`: **SUCCESS** (2m40s)
  - `Compile python source files`: **SUCCESS** (0 syntax/compilation errors)
  - `Run Ruff (linter & format checks)`: **SUCCESS** (`All checks passed!`)
  - `Run Mypy (type check)`: **SUCCESS** (`Success: no issues found in 272 source files`)
  - `Verify Global Pytest Collection`: **2,348 tests collected** in 11.45s
  - `Apply Database Migrations`: **SUCCESS** (Alembic upgraded head on PostgreSQL 16 container)
  - `Run Pytest (unit & contract test suites)`: **1,962 passed, 6 skipped** in 47.12s
  - `Run Task Intelligence PostgreSQL Integration Tests`: **10 passed** in 4.12s
  - `Run Durable Architecture PostgreSQL Integration Tests`: **16 passed in 3.12s (100% green)**
  - `Frontend Quality Gates & Tests`: **146 passed** in 1m1s
- **Branch Push CI Run:** [Run 40757925636](https://github.com/Itsbhavesh1101/Ops/actions/runs/40757925636) — **SUCCESS** (3m16s)

---

## 8. Canonical External Blockers Summary

The authoritative external blockers identified in `docs/development-quality/E7_Development_Completion_Remediation_Plan.md` remain strictly isolated and tracked:

| Blocker ID | Name | Canonical Description | Resolution Pre-requisite |
|---|---|---|---|
| `BLK-01` | `MYSQL_LIVE_READ_VERIFICATION` | Live read verification against institutional ERP MySQL database. | Requires institutional VPN/network route and read-only schema credentials. |
| `BLK-02` | `PRODUCTION_COMPANY_OIDC` | Production OIDC enterprise authentication integration. | Requires institutional IdP client ID, secret, and discovery endpoints. |
| `BLK-03` | `E8_NOTIFICATION_INTEGRATION` | Live engagement notification delivery to institutional E-8 endpoints. | Requires institutional E-8 HTTP gateway endpoints, API keys, and recipient directory resolution. |
| `BLK-04` | `PRODUCTION_GATEWAY` | External gateway mTLS and routing verification. | Requires staging ingress certificate bundles and reverse proxy configuration. |

---

## 9. PR & Stacked Branch Topology

- **Base PR:** PR #4 (`quality/dq4-integration-reliability` -> `master`), Status: `OPEN`.
- **New Stacked PR:** `quality/dq4-durable-architecture` -> `quality/dq4-integration-reliability` (PR #5), Status: `OPEN`.
- **Targeting Strategy:** By stacking this engineering PR against `quality/dq4-integration-reliability`, PR #4 remains clean and reviewable with its accepted baseline, while the durable architecture additions are isolated for dedicated Architecture Review Board evaluation.
- Neither PR #4 nor PR #5 is merged.
- Phase DQ-5 has not been started.

---

## 10. Engineering Decision Ratification & Governance Separation

Under delegation from the project owner, the engineering reviewer has ratified the technical architecture selections for integration durability and governance. The complete rationale, implementation references, automated test evidence, and operational limits are recorded in:
- **Engineering Decision Record:** [`docs/development-quality/DQ4_Engineering_Decision_Record.md`](file:///f:/operations-engine/docs/development-quality/DQ4_Engineering_Decision_Record.md)
- **Proposed ADR-012 Amendment:** [`docs/adr/ADR-012-AMENDMENT-PROPOSED.md`](file:///f:/operations-engine/docs/adr/ADR-012-AMENDMENT-PROPOSED.md)
- **E-8 Governance Handoff Specification:** [`docs/development-quality/E8_Governance_Handoff_Specification.md`](file:///f:/operations-engine/docs/development-quality/E8_Governance_Handoff_Specification.md)

### 10.1 Four-Tier Governance Separation

To prevent governance ambiguity or accidental production deployment, four discrete governance tiers are strictly enforced:

| Governance Tier | Meaning & Boundaries | Current Code & Architecture State |
|---|---|---|
| **`ENGINEERING_DESIGN_APPROVED`** | The engineering reviewer has approved the technical architecture, code implementations, and unit/integration test suites for design and verification purposes. | • AD-DQ4-01: Option C two-stage outbox (`notification_outbox_commands`)<br>• AD-DQ4-02: PostgreSQL SLA state persistence (`task_sla_states`)<br>• DEC-E8-02: Fail-closed recipient governance<br>• DEC-E8-04: Structured context and provenance preservation<br>• REC-E8-01: Authenticated administrative recovery (`NotificationRecoveryService`)<br>• GOV-E8-01: Default-disabled delivery safeguard<br>• 16/16 PostgreSQL integration tests passing in CI. |
| **`FORMAL_ARB_APPROVAL_PENDING`** | The internal Architecture Review Board (ARB) must formally convene to review and accept the proposed ADR-012 amendment and database migrations before formal merge. | • Accepted `ADR-012-e8-notification-boundary.md` remains unmodified.<br>• Proposed amendment drafted in `docs/adr/ADR-012-AMENDMENT-PROPOSED.md`.<br>• Neither PR #4 nor PR #5 is merged. |
| **`INSTITUTIONAL_APPROVAL_PENDING`** | External university IT, Communications, and Registrar stakeholders must formally sign off on recipient directory resolution schemas and message template catalogues. | • Specification drafted in `docs/development-quality/E8_Governance_Handoff_Specification.md`.<br>• Institutional confirmation is explicitly disclosed as outstanding.<br>• Zero PII is persisted in E-7. |
| **`PRODUCTION_ACTIVATION_BLOCKED`** | Live external dispatch to E-8 endpoints remains completely disabled and runtime-gated. | • Enforced by fail-closed default `NotificationSettings.enabled = False`.<br>• Enforced by `allow_provisional_recipients = False`.<br>• Gated by canonical external blocker `BLK-03: E8_NOTIFICATION_INTEGRATION`. |

---

## 11. Final Status & Exit Verdict

```
DQ4_ENGINEERING_DECISIONS_RECORDED_FORMAL_APPROVAL_PENDING
```
*(Neither PR #4 nor PR #5 is merged. Phase DQ-5 has not been started. Production deployment remains blocked.)*
