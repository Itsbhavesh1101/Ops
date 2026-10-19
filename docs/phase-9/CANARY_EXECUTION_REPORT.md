# Phase 9B — Canary Execution & Bounded Domain Slice Activation Report

**Execution Status**: `PASS`  
**Phase 9 Engineering Status**: `9B_COMPLETE_READY_FOR_9C`  
**Restricted Pilot Engineering Readiness**: `READY`  
**Phase 8 Production Readiness**: `NOT_READY`  

---

## 1. Executive Summary

Phase 9B activated and executed a bounded, deterministic canary slice for the **Semester Examination** domain on pilot tenant `tenant-pilot-001`. The canary exercised the real, integrated application services of the E-7 Operations Engine without bypassing business logic or manufacturing artificial database rows:

1. **Pilot Gateway Ingress & Authentication**:
   - Health and ready probes verified on accepted loopback ingress (`http://127.0.0.1:8088`).
   - Rejection of unauthenticated requests (`HTTP 401`).
   - Rejection of invalid/forged service keys (`HTTP 401`).
   - Rejection of stale/old unrotated service credentials (`HTTP 401`).
   - Authentication of rotated pilot service credential (`HTTP 201`).
2. **Goal Intake**: Ingested pilot goal via HTTP POST `/api/v1/goals` under correlation ID `corr-canary-9b-20261019104030`.
3. **Plan Generation & DAG Validation**: Generated deterministic Grade 10 exam plan proposal via `SemesterExamPlanner` (10 tasks, 3 milestones, 9 dependencies, acyclic DAG verified).
4. **Plan Approval & Baseline Freeze**: Transitioned plan to `APPROVED`, computed immutable baseline hash `e0163737f47f0302d67ae77642c26b1c0861b9044f049b59b8f79901f8e24463`, and emitted `plan.approved` event.
5. **Flowable BPMN & DMN Orchestration**:
   - Deployed DMN decision table `workflows/dmn/exam_readiness_rules_v1.dmn13.xml` and BPMN definition `workflows/bpmn/semester_exam_readiness.bpmn20.xml` to Flowable engine.
   - Initialized workflow instance idempotently on `plan.approved`.
   - Executed parallel user tasks for Science Department Review and Humanities Department Review (role: `chairs`).
   - Evaluated native Flowable DMN decision table returning deterministic outcome `PROCEED`.
   - Completed Academic Dean Moderation (role: `deans`) after inspecting configured boundary SLA timer metadata (`timer_warning_readiness_moderation`, `timer_breach_readiness_moderation`).
6. **OR-Tools CP-SAT Scheduling Solve**:
   - Ingested `PHASE5-SC-EXAM-FEASIBLE-001.json` via idempotent `task.completed` handler.
   - Executed CP-SAT solver through `SchedulingService`, achieving status `FEASIBLE` with **0 hard constraint violations**.
   - Validated 22 candidate assignments covering all 10 exam sessions across rooms, invigilators, and required equipment.
7. **Database-Level Resource Catalog Reconciliation**:
   - Extracted all 22 assignments and reconciled each directly against persisted `ResourceRecord` and `ResourceAvailabilityRecord` entries in PostgreSQL under `tenant-pilot-001`.
   - Reconciled 8 pilot resources: 4 `PERSON` (`res-inv-001..004`), 3 `ROOM` (`res-room-001..003`), 1 `EQUIPMENT` (`res-eq-004`).
   - Verified 0 missing resources, 0 foreign tenant resources, 0 type mismatches, 0 eligibility failures, and 0 availability window conflicts.
8. **Schedule Publication & Outbox Emission**:
   - Transitioned schedule lifecycle status from `REVIEW` to `PUBLISHED` under authorized credential `cred-pilot-exam-admin`.
   - Emitted canonical `schedule.published` outbox event transactionally (`eb3beec7-98ff-4e6f-8652-fb81d55f184b`).
9. **Infeasible Negative Control**:
   - Evaluated canonical negative control fixture `SC-EXAM-INFEASIBLE-001.json`.
   - Solver returned `INFEASIBLE` with **0 partial assignments** (fail-closed, zero silent partial success).
   - Produced actionable constraint conflict diagnostic (`RESOURCE_OVERLAP_CONFLICT`) with structured relaxation proposals.
10. **Audit & Outbox Integrity**:
    - 12 immutable audit events recorded covering complete lifecycle (`goal.created`, `plan.*`, `workflow.*`, `schedule.*`).
    - 4 canonical outbox events recorded with valid schemas and tenant scoping.
11. **Tenant Isolation**:
    - Zero leaks across goals, plans, workflow instances, or schedules.
    - Zero foreign resources assigned.

---

## 2. Canary Configuration & Baseline Context

| Parameter | Value |
|---|---|
| **Canary Run ID** | `canary-9b-20261019104030` |
| **Correlation ID** | `corr-canary-9b-20261019104030` |
| **Pilot Tenant ID** | `tenant-pilot-001` |
| **Pilot Domain** | Semester Examination |
| **Original Phase-9A Commit** | `0341c02e933f263e37f686ddfedd200870bb8195` |
| **Amended Phase-9A Commit (NEW)** | `8a803739fffdfe2c6136518c1ec510a964665052` |
| **Reachable History Secret Matches** | `0` |
| **Frozen Phase-8 RC Commit** | `03e6305256db3d838b472aaac50893ddb513414e` |
| **Frozen RC Tag** | `phase8-rc-38d080fadab8` |
| **Frozen Image** | `e7-operations-engine:rc-38d080fadab8` |
| **Ingress Boundary** | `http://127.0.0.1:8088` (Loopback only, rate limited) |
| **Service Credential ID** | `cred-pilot-exam-admin` |
| **Credential Rotation** | `ROTATED` (runtime-generated secret, never committed or displayed) |
| **Database Schema** | Alembic `0014 (head)`, 26 application tables, 27 public tables |

---

## 3. Targeted Defect Remediations

### Defect 1: Exposed Pilot Service Credential
- **Root Cause**: A deterministic static pilot service key was previously present in rehearsal scripts and test sources.
- **Remediation**:
  - Purged all hardcoded secret strings from the working tree (`scripts/pilot/run_phase9b_canary.py`, `tests/pilot/test_phase9b_canary.py`, `scripts/pilot/bootstrap_pilot_tenant.py`).
  - Active secrets in working tree = `0`.
  - Service credential secret is generated ephemerally at runtime using `secrets.token_urlsafe(32)`.
  - Secret is injected via runtime memory/environment and never printed, logged, committed, or included in JSON/Markdown reports.
  - Verified old unrotated credentials and forged credentials fail with `HTTP 401`.
  - Verified replacement credential successfully authenticates with `HTTP 201`.
  - Reachable `master` history was cleaned by authorized local amendment of Phase-9A commit to `8a803739fffdfe2c6136518c1ec510a964665052`. Verified `REACHABLE_MASTER_HISTORY_SECRET_MATCHES = 0`.

### Defect 2: Resource Source-of-Truth & Database Reconciliation
- **Root Cause**: Phase 9A bootstrap originally filtered `RS-SYNTH-001.json` (Phase 0 fixture, 14 resources) for `res-inv-` and `res-room-`, staging only 7 resources. The Phase 5 exam scenario legitimately requires `res-eq-004` (`Mobile Science Lab Kit`, skill `LAB_KIT`) for Grade 10 Science exam sessions.
- **Authoritative Fixture Finding**:
  - `RS-SYNTH-001.json` (SHA256: `ca3594e365571c69ee29ad8d49b17792a31a1fa60487c365d98db601ebbc0f91`): 14 resources, lacks `res-eq-004`.
  - `PHASE5-RS-EDU-001.json` (SHA256: `0f5cb47768b99840bd36efaf887ac99d994f764437910702b39ff77b0119768e`): 15 resources, contains `res-eq-004`.
- **Remediation**:
  - Authoritative education fixture `tests/fixtures/scheduling/education/phase5/PHASE5-RS-EDU-001.json` adopted as bootstrap source.
  - Staged pilot resources under `tenant-pilot-001` updated to **8 resources** (4 PERSON, 3 ROOM, 1 EQUIPMENT) and **40 availability windows**.
  - Onboarding documentation updated accordingly ([docs/phase-9/PILOT_TENANT_ONBOARDING.md](file:///F:/operations-engine/docs/phase-9/PILOT_TENANT_ONBOARDING.md)).
  - Automated database-level assertion proves every schedule-assigned resource exists in PostgreSQL under `tenant-pilot-001` with matching type, eligibility, and availability (`all_assigned_resources_persisted = true`).

### Defect 3: Canonical Education Fixture/Hash Regression & Reproducibility Closure
- **Root Cause**:
  1. Deselection in previous runs bypassed canonical manifest hash validation.
  2. The canonical benchmark manifest (`MANIFEST.json`) records SHA-256 hashes computed on the raw binary bytes of the JSON fixtures (`compute_file_sha256` reads with `"rb"`). Because the repository previously had `* text=auto eol=lf`, checkouts on Linux or CI environments converted line endings to LF, causing hash mismatches against the CRLF benchmark bytes unless a developer-local `.git/info/attributes` override was present.
- **Authoritative Contract Determination**:
  The benchmark fixtures in Git blobs are canonical byte-for-byte artifacts. The repository contract requires deterministic byte preservation across Windows, Linux, CI, and fresh checkouts without relying on unversioned developer-local `.git/info/attributes`.
- **Remediation**:
  - Removed the developer-local Phase-5 override from `.git/info/attributes` (`LOCAL_PHASE5_ATTRIBUTE_OVERRIDE_REMOVED = YES`, `LOCAL_INFO_ATTRIBUTES_REQUIRED = NO`).
  - Added repository-controlled, tracked rule to `.gitattributes`:
    `tests/fixtures/scheduling/education/phase5/*.json text eol=crlf`
  - Restored canonical fixture validation files `MANIFEST.json` and `test_education_scheduler.py` completely unmodified from HEAD (`git diff = 0`).
  - Executed complete suite `pytest -q tests/unit/scheduling/education/test_education_scheduler.py` without `-k` deselection: **19 passed in 5.48s** (100% pass, `PASS_WITH_UNMODIFIED_TEST_AND_MANIFEST`).
  - Proved clean-clone reproducibility: Created an isolated fresh clone from the repository into a temporary directory without `.git/info/attributes`, ran the suite, and verified **19 passed, 0 failed, 0 deselected** (`CLEAN_CLONE_CANONICAL_SUITE = PASS`).

---

## 4. Execution Stage Results

```
Stage 1:  [PASS] Pilot Gateway Ingress & Authentication (Health 200, Ready 200, 401 Rejections Verified, Rotated Key Authorized)
Stage 2:  [PASS] Goal Intake via HTTP API (Goal ID: 9fc1cdb8-cd4d-445d-8ad0-609ceab1a796)
Stage 3:  [PASS] Plan Generation & DAG Validation (Plan ID: b0dadcae-97d3-4820-9b58-81d7c396862e, 10 tasks, 0 issues)
Stage 4:  [PASS] Plan Approval & Baseline Freeze (Status: APPROVED, Baseline Hash Verified)
Stage 5:  [PASS] Workflow & DMN Deployment to Flowable (Engine Def: exam-operations-v1:8:832641d0-cba9-11f1-9c9c-5e7867d890d2)
Stage 6:  [PASS] Workflow Execution & SLA Boundary Timer Verification (Instance: 837a0721-cba9-11f1-9c9c-5e7867d890d2)
Stage 7:  [PASS] Feasible Scheduling Solve (Job ID: c90a3ecc-9601-4235-90e7-aca43fc57d89, 0 Hard Violations, 22 Assignments)
Stage 8:  [PASS] Resource Catalog Database Reconciliation (22/22 Assignments Reconciled, 0 Missing, 0 Foreign)
Stage 9:  [PASS] Schedule Publication & Outbox Emission (Schedule ID: a49a67b0-0fa5-4684-b2ff-a8899176561b, Status: PUBLISHED)
Stage 10: [PASS] Infeasible Negative Control (Status: INFEASIBLE, 0 Partial Assignments, Diagnostics Captured)
Stage 11: [PASS] Transactional Audit & Outbox Evidence Chain (12 Audit Events, 4 Outbox Events)
Stage 12: [PASS] Strict Tenant Isolation & Contamination Check (0 Leaks Detected)
```

---

## 5. Database-Level Resource Reconciliation Table

All 22 schedule assignments were verified against the actual persisted PostgreSQL records in `resources` and `resource_availabilities` for `tenant-pilot-001`:

| Resource ID | Assignment Role | Persisted in Catalog | Tenant Match | Type Match | Eligibility Match | Availability Match |
|---|---|---|---|---|---|---|
| `res-inv-004` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-003` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-inv-003` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-002` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-inv-003` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-003` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-inv-004` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-001` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-inv-001` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-002` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-inv-004` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-003` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-inv-002` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-002` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-eq-004` | EQUIPMENT | `true` | `true` | `true` (EQUIPMENT) | `true` (LAB_KIT) | `true` |
| `res-inv-001` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-003` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-inv-002` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-003` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |
| `res-eq-004` | EQUIPMENT | `true` | `true` | `true` (EQUIPMENT) | `true` (LAB_KIT) | `true` |
| `res-inv-001` | INVIGILATOR | `true` | `true` | `true` (PERSON) | `true` (INVIGILATION_GENERAL) | `true` |
| `res-room-002` | ROOM | `true` | `true` | `true` (ROOM) | `true` (capacity >= 24) | `true` |

### Reconciliation Summary Metrics
- **Persisted Catalog Resources**: 8 (4 PERSON, 3 ROOM, 1 EQUIPMENT)
- **Persisted Catalog Availability Windows**: 40
- **Total Assignments Reconciled**: 22
- **Missing Assigned Resources**: 0
- **Foreign Tenant Resources**: 0
- **Type Mismatches**: 0
- **Eligibility Failures**: 0
- **Availability Failures**: 0
- **All Assigned Resources Persisted**: `true`

---

## 6. Workflow State Transitions

The Flowable BPMN 2.0 execution trace recorded the following deterministic transitions:

| Step | Engine Task ID / Activity | Role / Actor | State Before | Action | State After |
|---|---|---|---|---|---|
| `start_instance` | `StartEvent_Readiness` | Engine Event | `NONE` | `START_PROCESS` (`plan.approved`) | `ACTIVE_PARALLEL_REVIEWS` |
| `science_review` | `838ad017-cba9-11f1-9c9c-5e7867d890d2` | `chairs` | `PENDING` | `APPROVE` (`science_approved=true`) | `COMPLETED` |
| `humanities_review` | `838b454a-cba9-11f1-9c9c-5e7867d890d2` | `chairs` | `PENDING` | `APPROVE` (`humanities_approved=true`) | `COMPLETED` |
| `dmn_decision` | `exam_readiness_rules_v1` | Flowable DMN | `EVALUATING` | Rule 1 matched (`coverage=100`) | Outcome: `PROCEED` |
| `dean_moderation` | `841f9628-cba9-11f1-9c9c-5e7867d890d2` | `deans` | `PENDING_MODERATION` | `APPROVE` (`moderation_approved=true`) | `READINESS_CERTIFIED_END` |

### Boundary SLA Timers Verified
- `timer_warning_readiness_moderation`: Configured on `readiness_moderation` user task using variable `${sla_warning_at}`.
- `timer_breach_readiness_moderation`: Configured on `readiness_moderation` user task using variable `${sla_breach_at}`.

---

## 7. Solver & Scheduling Details

### Feasible Canary Solve (`PHASE5-SC-EXAM-FEASIBLE-001.json`)
- **Solver Engine**: Google OR-Tools CP-SAT via `AcademicConstraintCompiler` and `CpSatScheduler`.
- **Status**: `FEASIBLE`
- **Hard Constraint Violations**: `0`
- **Total Assignments**: `22` (10 exam sessions across rooms, invigilators, and lab equipment)
- **Exam Sessions Scheduled**: `10 / 10`
- **Assigned Resources**:
  - Rooms: `res-room-001`, `res-room-002`, `res-room-003`
  - Invigilators: `res-inv-001`, `res-inv-002`, `res-inv-003`, `res-inv-004`
  - Equipment: `res-eq-004`
- **Foreign / Unauthorized Resources**: `0`

### Infeasible Negative Control (`SC-EXAM-INFEASIBLE-001.json`)
- **Status**: `INFEASIBLE`
- **Partial Assignments**: `0` (Zero silent partial success; strict fail-closed contract enforced)
- **Diagnostics Count**: `1`
- **Root Cause**: `RESOURCE_OVERLAP_CONFLICT`
- **Diagnostic Explanation**:
  > One or more activities cannot fit within their allowed time windows. Activities compete for exclusive access to shared resources without sufficient separation.
- **Structured Relaxation Candidates**:
  - `expand activity time windows`
  - `assign alternate eligible resources`
  - `shift non-critical activities to alternative windows`

---

## 8. Idempotency & Replay Verification

| Operation | Trigger Event / Key | First Execution Result | Replay Execution Result | Verified Status |
|---|---|---|---|---|
| **Workflow Instance Creation** | `plan.approved` (`corr-canary-9b-20261019104030`) | Instance `837a0721-cba9-11f1-9c9c-5e7867d890d2` created | Returned identical instance ID; 0 duplicate instances created | `PASS` |
| **Scheduling Job Submission** | `task.completed` (`T4_RESOURCE_READINESS`) | Job `c90a3ecc-9601-4235-90e7-aca43fc57d89` created | Returned identical job ID; 0 duplicate jobs created | `PASS` |

---

## 9. Audit & Outbox Evidence Chains

### Audit Evidence (12 Events Recorded)
All audit records were persisted transactionally with tenant binding `tenant-pilot-001` and correlation ID `corr-canary-9b-20261019104030`:
1. `goal.created`
2. `plan.generated`
3. `plan.validated`
4. `plan.approval_submitted`
5. `plan.approved`
6. `workflow.instance_started`
7. `workflow.instance.started`
8. `workflow.task_completed` (`science_dept_review`)
9. `workflow.task_completed` (`humanities_dept_review`)
10. `workflow.task_completed` (`readiness_moderation`)
11. `schedule.demand_created`
12. `schedule.published`

### Outbox Evidence (4 Events Recorded)
All outbox events adhere to canonical event schemas with `event_id`, `event_version`, `tenant_id`, and `occurred_at`:
1. `goal.created` (subject: `goal`)
2. `plan.generated` (subject: `plan`)
3. `plan.approved` (subject: `plan`)
4. `schedule.published` (subject: `schedule`, version 1, 22 assignments, outbox event ID: `eb3beec7-98ff-4e6f-8652-fb81d55f184b`)

---

## 10. Tenant Isolation & Contamination Check

Zero records leaked to any foreign tenant during canary activation:

| Entity Type | Scoped to `tenant-pilot-001` | Foreign Tenant Contamination | Isolation Status |
|---|---|---|---|
| **Goals** | 1 | 0 | `PASS` |
| **Plans** | 1 | 0 | `PASS` |
| **Workflow Instances** | 1 | 0 | `PASS` |
| **Schedule Jobs** | 1 | 0 | `PASS` |
| **Schedules** | 1 | 0 | `PASS` |
| **Schedule Assignments** | 22 | 0 | `PASS` |
| **Audit Events** | 12 | 0 | `PASS` |
| **Outbox Events** | 4 | 0 | `PASS` |

---

## 11. Preserved Restricted Pilot Scope Exceptions

As accepted in Phase 8I and Phase 9A, the restricted pilot scope explicitly defers external dependencies that are blocked on institutional infrastructure:

1. `MYSQL_LIVE_READ_VERIFICATION = DEFERRED_FROM_RESTRICTED_PILOT_SCOPE`  
   Blocked on institutional production MySQL read-only credential provisioning.
2. `E8_NOTIFICATION_INTEGRATION = DEFERRED_FROM_RESTRICTED_PILOT_SCOPE`  
   Blocked on external E8 notification endpoint provisioning. Local contract dispatch verified (`E8_LOCAL_CONTRACT_DISPATCH = PASS`).

---

## 12. Regression Verification Suite Results

| Test Suite / Verification Gate | Command | Result |
|---|---|---|
| **Pilot Canary & Isolation Tests** | `pytest -q tests/pilot` | `10 passed in 4.91s` |
| **Canonical Education Scheduler Suite** | `pytest -q tests/unit/scheduling/education/test_education_scheduler.py` | `19 passed in 5.48s` (zero deselected) |
| **Clean-Clone Canonical Suite (Isolated)** | `git clone file:///F:/operations-engine %TEMP% && pytest` | `19 passed in 4.98s` (`LOCAL_INFO_ATTRIBUTES_REQUIRED = NO`) |
| **Workflow & Approval Compiler Regressions** | `pytest -q tests/unit/workflow/test_dmn_runtime.py tests/unit/workflow/test_approval_compiler.py` | `11 passed in 3.36s` |
| **Audit & Outbox Regressions** | `pytest -q tests/unit/test_audit_schema.py tests/unit/test_outbox_model.py` | `68 passed in 2.00s` |
| **Dependency Integrity** | `python -m pip check` | `No broken requirements found.` |
| **Linter Quality Gate** | `ruff check .` | `All checks passed!` |
| **Core Typing Quality Gate** | `mypy app` | `Success: no issues found in 261 source files` |
| **Pilot Typing Quality Gate** | `mypy tests/pilot scripts/pilot` | `Success: no issues found in 6 source files` |
| **Secret Scanning (Working Tree)** | Safe scan across repository | `Raw active service secrets = 0` |
| **Reachable Master History Secret Matches** | `git log -S SyntheticPilot master` | `Matches = 0` |
| **Database Baseline** | Alembic current / heads check | `0014 (head)`, 26 app tables, 27 public tables, no migration 0015 |

---

## 13. Conclusion & Phase Gate Sign-Off

The bounded Phase-9B canary remediation and reproducibility closure has satisfied all gates:
1. The exposed pilot service credential was revoked, rotated to an ephemeral runtime secret, verified non-authenticating, and cleaned from reachable Git history.
2. The pilot resource catalog and schedule assignments were reconciled with zero discrepancies against the authoritative education fixture (`res-eq-004` staged under `tenant-pilot-001`).
3. Cross-platform reproducibility was established at the repository level in `.gitattributes`, removing reliance on local `.git/info/attributes` and passing all 19 tests in an isolated clean clone.
4. The fresh post-fix canary run (`canary-9b-20261019104030`) fully executed and verified all 12 stages with 0 hard constraint violations and complete audit/outbox evidence chains.

```
PHASE_9A_PILOT_INFRASTRUCTURE_AND_STAGING = PASS
PHASE_9B_CANARY_EXECUTION = PASS
PHASE_9B_REPRODUCIBILITY = PASS
PHASE_9_ENGINEERING_STATUS = 9B_COMPLETE_READY_FOR_9C
RESTRICTED_PILOT_ENGINEERING_READINESS = READY
PHASE_8_PRODUCTION_READINESS = NOT_READY
```
