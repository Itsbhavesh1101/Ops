# IR INFOTECH — E-7 OPERATIONS ENGINE
## Phase DQ-4: Engineering Decision Record
### Integration Durability, Recipient Governance, and SLA State Persistence

**Document Path:** `docs/development-quality/DQ4_Engineering_Decision_Record.md`  
**Date:** 29 October 2026  
**Repository:** `F:\operations-engine`  
**Base Pull Request:** [PR #4](https://github.com/Itsbhavesh1101/Ops/pull/4) (`quality/dq4-integration-reliability` -> `master`), Status: **OPEN**, unmerged  
**Target Pull Request:** [PR #5](https://github.com/Itsbhavesh1101/Ops/pull/5) (`quality/dq4-durable-architecture` -> `quality/dq4-integration-reliability`), Status: **OPEN**, unmerged  
**Engineering Baseline SHA:** `f01b8d76648e998d72ef58aea284957065b573f0`  
**Frozen Phase-8 RC Tag:** `phase8-rc-38d080fadab8` (`03e6305256db3d838b472aaac50893ddb513414e`) — *verified intact and unaltered*  
**Overall Status:** `ENGINEERING_DESIGN_APPROVED — FORMAL_GOVERNANCE_PENDING`

---

## 1. Executive Summary & Governance Scope

Under delegation from the project owner, the engineering reviewer has evaluated and selected the technical architectures for the remaining DQ-4 integration reliability and durability decisions.

### 1.1 Authority and Boundary Statement
> [!IMPORTANT]
> The architectural selections documented in this record are **approved exclusively for engineering design, implementation, and automated testing purposes**.  
> They **DO NOT** constitute formal Architecture Review Board (ARB) approval, institutional business approval, or production deployment authorization.
> - Accepted ADRs (including ADR-005 and ADR-012) remain formally un-amended pending scheduled ARB convene.
> - All live external notification delivery remains strictly disabled in production (`NotificationSettings.enabled = False`).
> - Neither PR #4 nor stacked PR #5 has been merged.
> - Phase DQ-5 has not been initiated.

---

## 2. Engineering Decisions Summary Matrix

| Decision ID | Area | Selected Technical Architecture | Engineering Status | Formal Governance Status | Production Activation Status |
|---|---|---|---|---|---|
| **AD-DQ4-01** | Notification Outbox Durability | Option C: Two-Stage Notification Outbox (`notification_outbox_commands`) | `ENGINEERING_DESIGN_APPROVED` | `FORMAL_ARB_APPROVAL_PENDING` | `PRODUCTION_ACTIVATION_BLOCKED` (gated by `BLK-03`) |
| **AD-DQ4-02** | SLA Tracking Durability | PostgreSQL Persistence with Row-Level Locking (`task_sla_states`) | `ENGINEERING_DESIGN_APPROVED` | `FORMAL_ARB_APPROVAL_PENDING` | `PRODUCTION_ACTIVATION_READY` (requires migration run) |
| **DEC-E8-02** | Recipient Contact Governance | E-8 Directory Contact Resolution with Fail-Closed E-7 Governance | `ENGINEERING_DESIGN_APPROVED` | `INSTITUTIONAL_APPROVAL_PENDING` | `PRODUCTION_ACTIVATION_BLOCKED` (`allow_provisional_recipients=False`) |
| **DEC-E8-04** | Template & Rendering Ownership | E-8 Message-Template, Rendering, and Channel Ownership | `ENGINEERING_DESIGN_APPROVED` | `INSTITUTIONAL_APPROVAL_PENDING` | `PRODUCTION_ACTIVATION_BLOCKED` (gated by E-8 template catalogue) |
| **REC-E8-01** | Blocked Notification Recovery | Durable Outbox Recovery with Authenticated Administrative RBAC | `ENGINEERING_DESIGN_APPROVED` | `FORMAL_ARB_APPROVAL_PENDING` | `PRODUCTION_ACTIVATION_BLOCKED` (gated by gateway infrastructure) |
| **GOV-E8-01** | Production Delivery Safeguard | Default-Disabled Outbound Notification Delivery (`enabled=False`) | `ENGINEERING_DESIGN_APPROVED` | `FORMAL_ARB_APPROVAL_PENDING` | `PRODUCTION_ACTIVATION_ENFORCED` (fail-closed default) |

---

## 3. Decision 1: AD-DQ4-01 — Two-Stage Notification Outbox (Option C)

### 3.1 Selected Architecture
**Option C: Two-Stage Notification Outbox Pipeline**
- **Stage 1 (Domain Event Ingestion):** Internal domain services commit business mutations atomically with a pure domain event (`EventEnvelope`) into `outbox_events` in PostgreSQL. The `OutboxDispatcher` publishes events to RabbitMQ topic exchange `e7.events`.
- **Stage 2 (Notification Staging & Inbox Deduplication):** The notification consumer (`NotificationWorker`) processes messages from RabbitMQ inside a short ACID database transaction. It verifies idempotency via `inbox_messages` and atomically stages a `NotificationOutboxCommandRecord` into `notification_outbox_commands` in `PENDING` (or `BLOCKED`) state.
- **Stage 3 (Decoupled Background Delivery):** A separate background worker (`NotificationDeliveryWorker`) polls `notification_outbox_commands` using `SELECT ... FOR UPDATE SKIP LOCKED` and sets a short-duration execution lease (`PROCESSING`). All remote network I/O (AMQP or HTTP) to E-8 is executed strictly **outside** any active database transaction.
- **Stage 4 (Terminal State Commitment):** Results are recorded in short database transactions: `DISPATCHED` on success, or exponential retry backoff transitioning to terminal `FAILED` after reaching `max_retries`.

### 3.2 Technical Rationale
1. **Network I/O Isolation:** Eliminates holding open database connections during remote HTTP/AMQP network requests to E-8, completely preventing database connection pool exhaustion under downstream latency.
2. **Decoupled Lifecycle:** Internal domain event publishing (`e7.events`) is decoupled from external delivery retry cycles, preventing slow or failing notifications from head-of-line blocking internal event delivery.
3. **Queue Health & Durability:** RabbitMQ consumer transactions acknowledge messages immediately upon database staging. Failed external deliveries retry from PostgreSQL with exponential backoff without congesting broker queues.
4. **Idempotency & Replay Safety:** Eliminates duplicate dispatch risks through three independent layers: broker idempotency via `inbox_messages`, deterministic `command_id` deduplication in `notification_outbox_commands`, and downstream deduplication in E-8.

### 3.3 Reviewed Implementation
- **Schema Migration:** [`app/db/migrations/versions/0015_create_notification_outbox.py`](file:///f:/operations-engine/app/db/migrations/versions/0015_create_notification_outbox.py) (creates `notification_outbox_commands`).
- **ORM Model:** [`app/db/models/notification_outbox.py`](file:///f:/operations-engine/app/db/models/notification_outbox.py) (`NotificationOutboxCommandRecord`).
- **Repository:** [`app/integrations/engagement/repository.py`](file:///f:/operations-engine/app/integrations/engagement/repository.py) (`NotificationOutboxRepository`).
- **Delivery Worker:** [`app/integrations/engagement/delivery_worker.py`](file:///f:/operations-engine/app/integrations/engagement/delivery_worker.py) (`NotificationDeliveryWorker`).
- **Lease Expiration Invalidation (`DEC-LE-01`):** `mark_dispatched()` and `mark_failed()` strictly check `NotificationOutboxCommandRecord.lease_expires_at > now`, invalidating completion rights upon lease expiration to eliminate split-brain races with concurrent polling workers.

### 3.4 CI & Automated Test Evidence
- **PostgreSQL Integration Tests (6 tests):**
  - `test_pg_notification_command_persistence`
  - `test_pg_inbox_and_command_transactional_atomicity`
  - `test_pg_database_rollback_prevents_partial_commit`
  - `test_pg_concurrent_command_claims_skip_locked`
  - `test_pg_lease_expiration_and_stale_worker_rejection`
  - `test_pg_lease_expiration_contract_and_unassigned_semantics`
  - `test_pg_notification_retry_state_transitions`
  - `test_pg_complete_trigger_provenance_round_trip`
- **CI Execution:** Verified passing in GitHub Actions CI (PR Run `40757937011`, Push Run `40757925636`).

### 3.5 Remaining Limitations
- Operational monitoring dashboard (Prometheus metrics and Grafana alerts for outbox queue depth and lease expiration lag) requires deployment configuration in staging.

### 3.6 Required Formal Approvals
- Formal ARB approval of proposed ADR-012 amendment (`docs/adr/ADR-012-AMENDMENT-PROPOSED.md`).

### 3.7 Production Activation Restrictions
- Outbound delivery to E-8 is disabled by default (`NotificationSettings.enabled = False`). Activation requires formal sign-off and resolution of `BLK-03`.

---

## 4. Decision 2: AD-DQ4-02 — PostgreSQL-Backed SLA State Persistence

### 4.1 Selected Architecture
**PostgreSQL Persistence for Task SLA State with Distributed Row-Level Locking**
- **Authoritative Database Source of Truth:** Replaced ephemeral in-memory state tracking with persistent storage in PostgreSQL table `task_sla_states`.
- **Distributed Concurrency Serialization:** Point lookups and state mutations in `SlaRepository.get_task_sla()` and `update_task_sla()` apply PostgreSQL row-level locks (`SELECT ... FOR UPDATE`), serializing concurrent pause and resume operations deterministically across multi-instance pods.
- **External Transaction Lifecycle Binding:** Implemented `_bind_session_lifecycle()` connecting in-memory cache updates to SQLAlchemy session events (`after_rollback`, `after_commit`). If an external caller rolls back a session without committing, newly attached tasks are evicted and paused/resumed tasks are restored to their previously committed state.
- **Authoritative Reloading:** `WorkflowService.get_task_sla()` and `SlaService.get_task_sla_async()` reload domain states directly from PostgreSQL, keeping in-memory state synchronized with the authoritative database record.

### 4.2 Technical Rationale
1. **Restart Recovery:** Worker process restarts, autoscaling terminations, or crashes do not lose running or paused SLA projections.
2. **Race-Condition Elimination:** Eliminates lost updates when multiple worker pods or concurrent workflows attempt to pause or resume the same task SLA simultaneously.
3. **Business Calendar Duration Preservation:** Row-level serialization guarantees that concurrent pauses do not multiply paused intervals or corrupt remaining warning/breach deadlines.
4. **Tenant Isolation:** All SLA tables and row locks are strictly scoped by `tenant_id`. Cross-tenant point lookups fail closed (`SlaTaskStateNotFoundError`).

### 4.3 Reviewed Implementation
- **Schema Migration:** [`app/db/migrations/versions/0016_create_task_sla_states.py`](file:///f:/operations-engine/app/db/migrations/versions/0016_create_task_sla_states.py) (creates `task_sla_states`).
- **ORM Model:** [`app/db/models/sla.py`](file:///f:/operations-engine/app/db/models/sla.py) (`TaskSlaStateRecord`).
- **Repository:** [`app/workflow/sla/repository.py`](file:///f:/operations-engine/app/workflow/sla/repository.py) (`SlaRepository`).
- **Service Integration:** [`app/workflow/sla/service.py`](file:///f:/operations-engine/app/workflow/sla/service.py) and [`app/workflow/service.py`](file:///f:/operations-engine/app/workflow/service.py).
- **Fail-Closed Legacy Guards:** Synchronous methods (`attach_task_sla_sync`, etc.) raise `RuntimeError` when `persistence_mode="database"`.

### 4.4 CI & Automated Test Evidence
- **PostgreSQL Integration Tests (6 tests):**
  - `test_pg_persisted_sla_registration`
  - `test_pg_persisted_pause_and_resume`
  - `test_pg_restart_recovery`
  - `test_pg_concurrent_sla_warning_evaluation`
  - `test_pg_cross_tenant_negative_access`
  - `test_pg_concurrent_sla_pause_resume_row_locking`
  - `test_pg_external_session_rollback_authoritative_reloading`
- **CI Execution:** Verified 100% green in GitHub Actions CI (PR Run `40757937011`).

### 4.5 Remaining Limitations
- The periodic SLA evaluation loop (`evaluate_sla_triggers()`) requires a scheduled timer or background daemon runner in production deployment.

### 4.6 Required Formal Approvals
- Formal ARB schema and architecture sign-off on migration `0016` and SLA database persistence mode.

### 4.7 Production Activation Restrictions
- Migration `0016` must be applied in the staging and production release pipelines before enabling `persistence_mode="database"`.

---

## 5. Decision 3: DEC-E8-02 — E-8 Recipient Contact Resolution & Fail-Closed Governance

### 5.1 Selected Architecture
**E-8 Recipient Contact Directory Resolution with Fail-Closed E-7 Governance**
- **Strict Boundary Separation:** E-7 formulates only canonical opaque recipient references (`urn:e7:cohort:...`, `urn:e7:role:...`). Resolution of opaque references to individual contact endpoints (emails, phone numbers, mobile push tokens) is owned exclusively by downstream E-8 directory services.
- **Fail-Closed Recipient Governance:** In compliance with DEC-E8-02, E-7 defaults `allow_provisional_recipients: bool = False`. If a command carries provisional or unverified cohort references without confirmed resolution, E-7 marks the command `BLOCKED` in `notification_outbox_commands` and emits an audit event (`notification.dispatch.blocked`).
- **Zero Raw PII Storage:** Raw emails, mobile numbers, and personal identifiers are strictly prohibited from being persisted in E-7 outbox tables.

### 5.2 Technical Rationale
1. **Data Minimization & Compliance:** Preserves FERPA, GDPR, and institutional privacy compliance by eliminating personal contact data replication into E-7.
2. **Defensive Governance:** Prevents accidental or unauthorized mass communications to student or faculty cohorts before institutional directory authorization is confirmed.

### 5.3 Reviewed Implementation
- **Configuration:** [`app/integrations/engagement/config.py`](file:///f:/operations-engine/app/integrations/engagement/config.py) (`allow_provisional_recipients: bool = False`).
- **Dispatch Governance Handlers:** [`app/integrations/engagement/handlers.py`](file:///f:/operations-engine/app/integrations/engagement/handlers.py).
- **Outbox Staging Enforcement:** [`app/integrations/engagement/repository.py`](file:///f:/operations-engine/app/integrations/engagement/repository.py) (`stage_command()` persists state as `BLOCKED` with reason `BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02`).

### 5.4 CI & Automated Test Evidence
- **Automated Tests:**
  - `test_notification_disabled_mode_prevents_dispatch`
  - `test_provisional_recipients_fail_closed_under_dec_e8_02`
  - `test_pg_tenant_scoped_recovery`
- **CI Execution:** Verified 100% green in GitHub Actions CI (PR Run `40757937011`).

### 5.5 Remaining Limitations
- E-8 directory endpoint contracts and institutional LDAP/Active Directory resolution protocols remain unverified pending institutional IT engagement.

### 5.6 Required Formal Approvals
- Formal institutional business and IT confirmation of directory resolution ownership (`Institutional Confirmation Pending`).

### 5.7 Production Activation Restrictions
- `allow_provisional_recipients` must remain `False` in production until formal institutional sign-off.

---

## 6. Decision 4: DEC-E8-04 — E-8 Message Template & Rendering Ownership

### 6.1 Selected Architecture
**E-8 Message-Template, Multi-Channel Rendering, and Content Ownership**
- **E-7 Responsibility:** E-7 formulates operational triggers (`trigger_kind`), typed business conditions (`business_condition`, e.g. `SCHEDULE_CHANGE_NOTICE`, `EXAM_SCHEDULE_CHANGED`), and structured contextual parameters (schedule IDs, dates, room numbers, revision counters).
- **E-8 Responsibility:** E-8 owns template catalogs, message copy, HTML email rendering, SMS character truncation, localized translations, and legal disclaimers.
- **Trigger Provenance Preservation:** E-7 preserves full trigger provenance through the staging and dispatch pipeline, ensuring E-8 receives complete context for template variable substitution.

### 6.2 Technical Rationale
1. **Separation of Concerns:** Core operations engine releases are not coupled to marketing, communications, or formatting changes.
2. **Multi-Channel Agility:** E-8 can adjust channel selection (SMS vs. Email vs. WhatsApp) and template branding without redeploying E-7 domain services.

### 6.3 Reviewed Implementation
- **Data Contracts:** [`app/integrations/engagement/models.py`](file:///f:/operations-engine/app/integrations/engagement/models.py) (`NotificationTrigger`, `NotificationCommand`, `TriggerKind`).
- **Provenance Round-Trip:** Model serialization and deserialization in `NotificationOutboxRepository.to_command()`.

### 6.4 CI & Automated Test Evidence
- **PostgreSQL Integration Test:** `test_pg_complete_trigger_provenance_round_trip` verified round-trip preservation of `business_condition` and context metadata into mock delivery.
- **CI Execution:** Verified 100% green in GitHub Actions CI (PR Run `40757937011`).

### 6.5 Remaining Limitations
- Specific template identifiers (e.g. `EXAM_SCHEDULE_CHANGED`) are defined as engineering conventions and require institutional catalog confirmation.

### 6.6 Required Formal Approvals
- Institutional Communications and Student Affairs approval of message template inventory and channel dispatch rules.

### 6.7 Production Activation Restrictions
- Outbound production delivery remains disabled until template catalog mapping is formally approved.

---

## 7. Decision 5: REC-E8-01 — Durable Blocked-Notification Recovery

### 7.1 Selected Architecture
**Durable Outbox Recovery with Authenticated Administrative RBAC Controls**
- **Persistent Blocked Queue:** Blocked notification commands are durably retained in `notification_outbox_commands` in state `BLOCKED`.
- **Administrative Recovery Engine:** Implemented [`NotificationRecoveryService`](file:///f:/operations-engine/app/integrations/engagement/recovery.py):
  - Strictly requires administrative caller credentials (`admin` or `operations_manager` principal roles).
  - Validates replacement recipient references against syntax rules and rejects prohibited PII (raw emails, phone numbers).
  - Atomically transitions command state from `BLOCKED` to `PENDING` with immediate `next_attempt_at = now`.
  - Appends audit events (`notification.command.recovered` or `notification.recovery.denied`) with tenant scoping.

### 7.2 Technical Rationale
1. **Executable Recovery:** Replaces passive audit log queries with a concrete, reviewable, executable recovery path.
2. **Operational Safety:** Prevents unauthenticated users or non-admin roles from releasing blocked communications.

### 7.3 Reviewed Implementation
- **Service:** [`app/integrations/engagement/recovery.py`](file:///f:/operations-engine/app/integrations/engagement/recovery.py).
- **Repository Methods:** `NotificationOutboxRepository.recover_blocked_command()` and `list_by_state()`.

### 7.4 CI & Automated Test Evidence
- **PostgreSQL Integration Test:** `test_pg_tenant_scoped_recovery` (verifies administrative recovery, tenant scoping, and state transition).
- **Unit Tests:** `test_notification_recovery_service_rbac_and_pii_guards`.
- **CI Execution:** Verified 100% green in GitHub Actions CI (PR Run `40757937011`).

### 7.5 Remaining Limitations
- HTTP API mount for administrative recovery (`POST /api/v1/admin/notifications/{id}/unblock`) is deferred pending production gateway infrastructure (`BLK-04`).

### 7.6 Required Formal Approvals
- Information Security and Governance approval of recovery RBAC policy.

### 7.7 Production Activation Restrictions
- Administrative recovery must require authenticated enterprise gateway credentials and mTLS when mounted.

---

## 8. Decision 6: GOV-E8-01 — Default-Disabled Production Notification Delivery

### 8.1 Selected Architecture
**Fail-Closed Default Notification Delivery Invariant**
- In `NotificationSettings`, outbound delivery is disabled by default (`enabled: bool = Field(default=False)`).
- When `enabled=False`, `NotificationDeliveryWorker.poll_and_deliver()` immediately returns 0 without claiming records, leasing rows, or executing network I/O.
- Even if outbox commands are staged by domain consumers, no outbound AMQP publish or HTTP request can occur until explicitly enabled via environment configuration.

### 8.2 Technical Rationale
- Defense-in-depth governance invariant preventing accidental dispatch during development, testing, staging deployment, or credential configuration.

### 8.3 Reviewed Implementation
- [`app/integrations/engagement/config.py`](file:///f:/operations-engine/app/integrations/engagement/config.py) and [`app/integrations/engagement/delivery_worker.py`](file:///f:/operations-engine/app/integrations/engagement/delivery_worker.py).

### 8.4 CI & Automated Test Evidence
- Verified in unit and integration test suites (`test_notification_disabled_mode_prevents_dispatch`).
- Verified 100% green in GitHub Actions CI (PR Run `40757937011`).

### 8.5 Required Formal Approvals
- Formal Production Readiness Review (PRR) sign-off prior to setting `E7_NOTIFICATION_ENABLED=True`.

---

## 9. Verification & Quality Gates Summary

All six approved engineering designs are implemented and verified in the codebase:
- **Ruff Checks:** 0 errors, 0 warnings (`ruff check .`).
- **Mypy Type Checks:** 0 errors across 272 source files (`mypy app`).
- **Pytest Collection:** 2,348 tests collected with 0 discovery errors.
- **Unit & Contract Tests:** 1,962 passed, 6 skipped in CI.
- **Durable PostgreSQL Integration Tests:** **16 passed in 3.12s (100% green)** in CI.
- **Frontend Vitest Suite:** 146 passed in CI.
- **GitHub Actions PR CI:** [Run 40757937011](https://github.com/Itsbhavesh1101/Ops/actions/runs/40757937011) — **SUCCESS** (2m44s).
- **GitHub Actions Push CI:** [Run 40757925636](https://github.com/Itsbhavesh1101/Ops/actions/runs/40757925636) — **SUCCESS** (3m16s).

---

## 10. Canonical External Blockers Status

The authoritative external blockers identified in `docs/development-quality/E7_Development_Completion_Remediation_Plan.md` remain isolated and unresolved pending institutional infrastructure and credentials:

| Blocker ID | Name | Canonical Description | Pre-requisite |
|---|---|---|---|
| `BLK-01` | `MYSQL_LIVE_READ_VERIFICATION` | Live read verification against institutional ERP MySQL database. | Requires institutional VPN/network route and read-only schema credentials. |
| `BLK-02` | `PRODUCTION_COMPANY_OIDC` | Production OIDC enterprise authentication integration. | Requires institutional IdP client ID, secret, and discovery endpoints. |
| `BLK-03` | `E8_NOTIFICATION_INTEGRATION` | Live engagement notification delivery to institutional E-8 endpoints. | Requires institutional E-8 HTTP gateway endpoints, API keys, and recipient directory resolution. |
| `BLK-04` | `PRODUCTION_GATEWAY` | External gateway mTLS and routing verification. | Requires staging ingress certificate bundles and reverse proxy configuration. |

---

## 11. Final Status & Exit Verdict

```
ENGINEERING_DESIGN_APPROVED — FORMAL_GOVERNANCE_PENDING
```
*(Neither PR #4 nor PR #5 is merged. Phase DQ-5 has not been started. Production deployment remains blocked.)*
