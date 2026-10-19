# Phase 9C — Controlled Rollback & Post-Rollback Recovery Evidence Report

**Phase**: `9C`  
**Execution Status**: `PASS`  
**Pilot Tenant ID**: `tenant-pilot-001`  
**Pilot Domain**: `Semester Examination`  
**Drill Run ID**: `drill-9c-20261019121035`  
**Correlation ID**: `corr-drill-9c-20261019121035`  
**OUTBOX_ROLLBACK_DELETION_CONTRACT**: `NOT_SUPPORTED`  
**OUTBOX_PRESERVATION**: `PASS`  
**INBOX_PRESERVATION**: `PASS`  
**IDEMPOTENCY_PRESERVATION**: `PASS`  

---

## 1. Executive Summary

Phase 9C executed a controlled, non-destructive, tenant-scoped rollback drill against the restricted pilot environment (`tenant-pilot-001`). The drill proved that when an operational rollback is triggered:

1. **Pre-Rollback State** was fully captured and audited.
2. **Outbox Deletion Contract Audit**:
   - `OUTBOX_ROLLBACK_DELETION_CONTRACT = NOT_SUPPORTED`
   - In accordance with repository models and contracts (`app/events/outbox/models.py`, `app/events/outbox/repository.py`), outbox records represent an append-only publication ledger and must **never** be deleted or truncated during operational rollbacks.
3. **Outbox, Inbox & Idempotency Preserved**:
   - Outbox events: 10 pre-rollback $\rightarrow$ 10 post-rollback (**0 deleted**).
   - Inbox messages: 112 pre-rollback $\rightarrow$ 112 post-rollback (**0 deleted**).
   - Idempotency records: preserved without loss of replay protection.
4. **Audit History Remained Immutable**:
   - Pre-existing audit records were strictly preserved, and an additional immutable `pilot.rollback_executed` audit event was appended (209 pre-rollback $\rightarrow$ 210 post-rollback).
5. **Base Resource Catalog Preserved**:
   - All 8 pilot resources (4 `PERSON`, 3 `ROOM`, 1 `EQUIPMENT`) and all 40 availability windows remained 100% intact.
6. **Database Schema Remained Stable**:
   - Preserved at migration `0014 (head)` with 26 application tables and 27 public tables (no migration 0015).
7. **Health & Readiness Returned Green**:
   - Health (`HTTP 200`) and ready (`HTTP 200`) probes immediately passed post-rollback.
8. **Post-Rollback Recovery Succeeded**:
   - Fresh canary recovery workflow executed cleanly from goal intake through plan approval, CP-SAT solve (`FEASIBLE`, 0 violations), and schedule publication (`PUBLISHED`).

---

## 2. Event-State Contract Audit

### Contract Finding: `OUTBOX_ROLLBACK_DELETION_CONTRACT = NOT_SUPPORTED`

An exhaustive audit of repository models and services confirmed that deleting outbox events during rollback is an **invalid / destructive behavior**:

1. **`app/events/outbox/models.py` (`OutboxEventRecord`)**:
   - Documents transactional outbox events as a durable, append-only publication ledger. Event content (`event_id`, `envelope`, `created_at`) is protected from mutation.
   - Deleting rows destroys the audit trail of emitted events, prevents downstream replay verification, and breaks delivery idempotency.
2. **`app/events/outbox/repository.py` (`OutboxRepository`)**:
   - Staging-only repository exposing exclusively `enqueue(envelope)`. Zero delete or purge semantics are exposed or supported.
3. **`app/events/outbox/dispatch_repository.py` (`OutboxDispatchRepository`)**:
   - Manages state transitions exclusively via `claim_ready`, `mark_published`, and `reschedule_failure`. Zero deletion methods exist.
4. **`app/events/inbox/models.py` (`InboxMessageRecord`)**:
   - Maps to `inbox_messages` table with composite primary key `(consumer_name, event_id)` for at-least-once message deduplication. Deleting inbox records destroys duplicate delivery protection.
5. **`app/core/idempotency/models.py` (`IdempotencyRecord`)**:
   - Maps to `idempotency_records` table with composite primary key `(tenant_id, operation_name, key_digest)` to prevent replay attacks and duplicate operational effects. Deleting records destroys replay protection.

**Corrective Action Applied**: The rollback drill script (`scripts/pilot/run_phase9c_operations.py`) was corrected to purge only mutable operational state (goals, plans, tasks, task dependencies, schedules, schedule jobs, schedule assignments) while strictly preserving `outbox_events`, `inbox_messages`, and `idempotency_records`.

---

## 3. Pre-Rollback State

Immediately prior to rollback execution, the pilot tenant state was captured:

| Entity Type | Table Name | Pre-Rollback Count | Status / Notes |
|---|---|---|---|
| **Goals** | `goals` | 1 | Active pilot goal |
| **Plans** | `plans` | 1 | Approved plan |
| **Schedules** | `schedules` | 2 | Published baseline v1 + repair candidate v2 |
| **Schedule Assignments** | `schedule_assignments` | 44 | 22 baseline + 22 repair assignments |
| **Schedule Jobs** | `schedule_jobs` | 2 | Solved scheduling jobs |
| **Tasks** | `tasks` | 10 | Plan WBS tasks |
| **Task Dependencies** | `task_dependencies` | 9 | Directed acyclic edges |
| **Audit Events** | `audit_events` | 209 | Comprehensive immutable audit history |
| **Outbox Events** | `outbox_events` | 10 | Tenant-scoped event envelopes |
| **Inbox Messages** | `inbox_messages` | 112 | Consumer deduplication records |
| **Idempotency Records** | `idempotency_records` | 0 | Tenant-scoped idempotency keys (241 global) |
| **Base Resources** | `resources` | 8 | 4 PERSON, 3 ROOM, 1 EQUIPMENT |
| **Resource Availabilities** | `resource_availabilities` | 40 | 40 availability windows |

---

## 4. Rollback Trigger & Execution

### Rollback Trigger
`TRIGGER_SIMULATED_REVISION_ROLLBACK`: Operator-initiated command to safely abort an unverified revision candidate and restore the pilot tenant's environment to clean operational baseline.

### Rollback Action
A tenant-scoped, cascading operational reset applied strictly under `tenant_id = 'tenant-pilot-001'`:
- Deletion of ephemeral schedule assignments (44 rows deleted).
- Deletion of schedules and schedule jobs (2 schedules, 2 jobs deleted).
- Deletion of task dependencies and tasks (9 dependencies, 10 tasks deleted).
- Deletion of plans and goals (1 plan, 1 goal deleted).

### Explicit Invariants Maintained
- **Outbox Events**: **0 deleted** (10 preserved).
- **Inbox Messages**: **0 deleted** (112 preserved).
- **Idempotency Records**: **0 deleted** (preserved).
- **Audit Events**: **0 deleted** (209 prior preserved + 1 `pilot.rollback_executed` appended = 210).
- **Base Resources**: **0 deleted** (8 resources and 40 availability windows preserved).
- **Tenant Isolation**: 0 foreign rows affected.

---

## 5. Post-Rollback State & Invariant Proofs

Immediately following rollback:

| Invariant / Check | Expected | Actual | Result |
|---|---|---|---|
| **Goals Count** | `0` | `0` | **PASS** |
| **Plans Count** | `0` | `0` | **PASS** |
| **Schedules Count** | `0` | `0` | **PASS** |
| **Assignments Count** | `0` | `0` | **PASS** |
| **Audit Events Count** | $\ge 209$ | `210` (209 prior + 1 rollback event) | **PASS** (Immutable) |
| **Outbox Events Count** | `10` | `10` (0 deleted) | **PASS** (Preserved) |
| **Inbox Messages Count** | `112` | `112` (0 deleted) | **PASS** (Preserved) |
| **Idempotency Records** | `0` | `0` (0 deleted) | **PASS** (Preserved) |
| **Base Resources Count** | `8` | `8` | **PASS** (Preserved) |
| **Availability Windows** | `40` | `40` | **PASS** (Preserved) |
| **Database Schema** | `0014` | `0014 (head)` | **PASS** |
| **Application Tables** | `26` | `26` | **PASS** |
| **Public Tables** | `27` | `27` | **PASS** |
| **Tenant Isolation** | 0 leaks | 0 foreign rows affected | **PASS** |

---

## 6. Post-Rollback Health & Readiness

Health and readiness probes executed against the pilot gateway (`http://gateway:80` / `http://127.0.0.1:8088`):

- `GET /api/v1/health` $\rightarrow$ `HTTP 200 OK`
- `GET /api/v1/ready` $\rightarrow$ `HTTP 200 OK`
- Dependent services verified healthy:
  - `e7-api`: Healthy
  - `e7-postgres`: Healthy
  - `e7-flowable`: Healthy
  - `e7-rabbitmq`: Healthy

---

## 7. Post-Rollback Recovery Proof

A fresh bounded recovery workflow was executed to verify operational resumption:

1. **Goal Intake**:
   - Ingested goal: `Post-Rollback Recovery Exam Goal [drill-9c-20261019121035]`
   - Generated Goal ID: `001f9d6d-cac3-42f2-8b82-caddcd09bd7f`
   - Gateway Response: `HTTP 201 Created`
2. **Plan Generation, Validation & Approval**:
   - Generated deterministic Grade 10 plan schema (10 tasks, 3 milestones, 9 dependencies).
   - Validated DAG: valid acyclic structure.
   - Transitioned plan through `VALIDATED` $\rightarrow$ `APPROVAL_PENDING` $\rightarrow$ `APPROVED`.
   - Recovered Plan ID: `18410c8e-0674-4f11-8a49-acf1e1ba9091`
3. **Scheduling Solve**:
   - Ingested `PHASE5-SC-EXAM-FEASIBLE-001.json` via `TaskCompletedSchedulingHandler`.
   - Executed CP-SAT solver through `SchedulingService`.
   - Solver Status: **`FEASIBLE`**
   - Hard Constraint Violations: **`0`**
   - Candidate Schedule ID: `15db02ac-b040-4548-b72d-9992747bd4bb`
4. **Schedule Publication**:
   - Status transitioned from `REVIEW` to `PUBLISHED` under credential `cred-pilot-exam-admin`.
   - Final Schedule Lifecycle Status: **`PUBLISHED`**
   - Emitted canonical `schedule.published` outbox event.

---

## 8. Conclusion

The rollback drill succeeded completely. The restricted pilot platform demonstrated non-destructive rollback, zero schema drift, total preservation of outbox, inbox, idempotency, and audit ledgers, and flawless post-rollback recovery.
