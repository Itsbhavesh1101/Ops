# Phase 8G: Detailed User Acceptance Testing (UAT) Scenarios

## 1. Overview

This document specifies the complete catalog of 31 User Acceptance Testing scenarios executed in Phase 8G. Each scenario represents a distinct operational requirement validated across the canonical roles and security boundaries of the IR Infotech E-7 Operations Engine.

---

## 2. Scenario Catalog

### 2.1 Planner Acceptance Scenarios (`UAT-PLANNER-*`)

#### `UAT-PLANNER-001`: Operational Goal Authoring & Tenant Context Binding
- **Persona**: `UAT_PLANNER` (`user:uat_planner_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-TI-01`, `FR-SEC-01`
- **Description**: Authenticated planner submits a new operational goal. The engine creates the goal, emits an audit event (`goal.created`), and binds the goal strictly to the caller's trusted tenant context, completely ignoring any malicious `tenant_id` supplied in the JSON body. A foreign tenant attempting to access the goal receives HTTP 404 (`goal.not_found`).
- **Input**:
  ```json
  POST /api/v1/goals
  {
    "objective": "Grade 10 Semester Examination Readiness and Timetabling",
    "scope": "Academic Operations 2026-Q4",
    "tenant_id": "malicious-foreign-tenant"
  }
  ```
- **Expected Outcome**: HTTP 201 Created. `tenant_id` == `uat-tenant-alpha`. Audit record emitted. Foreign tenant probe returns HTTP 404.

#### `UAT-PLANNER-002`: WBS Plan Proposal Generation & Typed Schema Validation
- **Persona**: `UAT_PLANNER` (`user:uat_planner_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-TI-02`, `FR-TI-03`
- **Description**: Planner constructs/receives a structured Work Breakdown Structure proposal (`PlanSchema`) containing tasks, duration estimates, roles, and dependency edges. The proposal passes deterministic acyclic validation with 0 issues. The plan proposal remains non-authoritative (`is_baseline == False`) until formal review.
- **Input**: `PlanSchema` with 2 tasks ("Prepare Exam Timetable", "Review Exam Schedule") and 1 FINISH_TO_START dependency.
- **Expected Outcome**: `validator.validate()` returns `is_valid == True`, `issues == []`. Baseline unapproved.

#### `UAT-PLANNER-003`: Deterministic Invalid Model Output (Cycle) Rejection
- **Persona**: `UAT_PLANNER` (`user:uat_planner_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-TI-03`, `FR-TI-04`
- **Description**: When a cyclic dependency graph is proposed (e.g. Task A -> Task B -> Task A), deterministic validation fails closed before persisting.
- **Input**: `PlanSchema` with circular FINISH_TO_START dependencies.
- **Expected Outcome**: `is_valid == False`, `cycle_path` or error code contains `CYCLE`. No corrupt baseline created.

---

### 2.2 Approver Acceptance Scenarios (`UAT-APPROVER-*`)

#### `UAT-APPROVER-001`: Critical Path Inspection & Operational Role Boundary
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-TI-05`, `FR-SEC-02`
- **Description**: Approver reviews an authored plan proposal and retrieves the calculated Critical Path Method (CPM) schedule. Approver attempting an unauthorized administrative deployment (`POST /api/v1/workflows/definitions/{id}/deploy`) is denied with HTTP 403 `workflow.unauthorized`.
- **Input**: `GET /api/v1/plans/{id}/critical-path`, `POST /api/v1/workflows/definitions/{id}/deploy`.
- **Expected Outcome**: CPM calculation returns HTTP 200 with critical path tasks. Deploy route returns HTTP 403.

#### `UAT-APPROVER-002`: Canonical Plan Approval, Baseline Freeze & Separation of Duties (SoD)
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`) vs `UAT_PLANNER`
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-TI-06`, `FR-SEC-03`
- **Description**: Planner attempting to self-approve their own plan proposal is rejected with HTTP 403 `plan.sod_violation`. Scheduler attempting approval is rejected with HTTP 403 `plan.unauthorized`. Legitimate Approver successfully approves the plan: status transitions to `APPROVED`, `is_baseline` becomes `True`, and a cryptographic `baseline_hash` is assigned. Repeating approval is safely idempotent (HTTP 200).
- **Input**: `POST /api/v1/plans/{id}/approve` across Planner, Scheduler, Approver.
- **Expected Outcome**: Planner receives 403 (SoD violation), Scheduler receives 403, Approver receives 200 (`is_baseline == True`). Idempotent retry returns 200.

#### `UAT-APPROVER-003`: Validation Feedback & Correction Path
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-TI-07`
- **Description**: Demonstrates validation workflow feedback. Prior to approval, validate endpoint is checked to verify zero remaining domain errors.
- **Input**: `POST /api/v1/plans/{id}/validate`.
- **Expected Outcome**: HTTP 200 OK, `is_valid == True`.

---

### 2.3 Workflow Runtime Scenarios (`UAT-WORKFLOW-*`)

#### `UAT-WORKFLOW-001`: Process Instance Startup from Approved Plan
- **Persona**: `UAT_PLANNER` / `UAT_SCHEDULER`
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-WF-01`
- **Description**: Starts a BPMN process instance (`semester_exam_readiness`) to orchestrate multi-departmental readiness.
- **Input**: `POST /api/v1/workflows/instances` with `workflow_key: "semester_exam_readiness"`.
- **Expected Outcome**: HTTP 201 Created. Status `RUNNING`, bound to `uat-tenant-alpha`.

#### `UAT-WORKFLOW-002`: Human Task Action Authorization & Idempotency
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`) vs Ineligible Actor
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-WF-02`, `FR-WF-03`
- **Description**: Ineligible actor attempting to complete a human task receives HTTP 403 `workflow.task_unauthorized`. Legitimate approver completes the task with `Idempotency-Key`. Duplicate submission with identical idempotency key returns identical successful response without duplicate side-effects.
- **Input**: `POST /api/v1/workflows/tasks/{id}/actions` with `Idempotency-Key`.
- **Expected Outcome**: Ineligible actor gets 403. Eligible approver gets 200 OK. Idempotent retry returns 200 OK.

#### `UAT-WORKFLOW-003`: SLA Timer & Escalation Experience
- **Persona**: System / Scheduler
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-WF-04`
- **Description**: Validates SLA tracking engine. When a task exceeds warning or breach duration thresholds, SLA status updates to `BREACHED` and triggers deterministic notification escalation actions.
- **Input**: `SlaService.evaluate_instance()` with past deadline timestamps.
- **Expected Outcome**: Evaluates to `SlaStatus.BREACHED` and fires `NOTIFY_SUPERVISOR` escalation action.

---

### 2.4 Scheduler Acceptance Scenarios (`UAT-SCHEDULER-*`)

#### `UAT-SCHEDULER-001`: Timetabling Solve Job Submission
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SC-01`
- **Description**: Submits a complex academic scheduling problem request. Validates authorization and receives asynchronous acceptance (HTTP 202). Planner attempting submission is rejected with HTTP 403 `schedule.unauthorized`.
- **Input**: `POST /api/v1/schedule-jobs` with valid problem payload.
- **Expected Outcome**: Planner gets 403. Scheduler gets 202 Accepted.

#### `UAT-SCHEDULER-002`: Feasible Schedule Benchmark on Academic Dataset
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SC-02`, `FR-SC-03`
- **Description**: Compiles and solves the standard 10-activity exam timetabling problem via CP-SAT solver. Verifies zero hard constraint violations and optimal resource utilization.
- **Input**: `AcademicConstraintCompiler.solve(dataset, resources)` on `"BENCHMARK"` scenario.
- **Expected Outcome**: `ScheduleStatus.FEASIBLE` or `OPTIMAL`. Total hard violations == 0. Solver wall-time < 5000ms.

#### `UAT-SCHEDULER-003`: Infeasible Timetable Diagnostic Feedback
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SC-04`
- **Description**: Submits an intentionally over-constrained scenario (e.g. 10 exams competing for 1 room in a single time slot). Engine evaluates model, detects infeasibility, and returns structured diagnostics identifying bottleneck resources.
- **Input**: Solve request with severe room shortages.
- **Expected Outcome**: `ScheduleStatus.INFEASIBLE`. Diagnostic list identifies over-constrained resource `res-hall-001`.

#### `UAT-SCHEDULER-004`: Distinguishability: Budget Exhaustion vs Infeasibility
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SC-05`
- **Description**: When search time budget expires before proving optimality or infeasibility (CP-SAT returns `UNKNOWN`), the engine marks status as `BUDGET_EXHAUSTED` (never confusing it with `INFEASIBLE`).
- **Input**: Solver execution returning `cp_model.UNKNOWN`.
- **Expected Outcome**: Status == `ScheduleStatus.BUDGET_EXHAUSTED` != `INFEASIBLE`. Diagnostic reports `BUDGET_EXHAUSTED`.

#### `UAT-SCHEDULER-005`: Publication of Feasible Schedule
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`) vs `UAT_PLANNER`
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SC-06`
- **Description**: Publishing an approved, feasible schedule transitions it to `PUBLISHED`. Planner attempting to publish is rejected with HTTP 403 `schedule_publish.unauthorized`.
- **Input**: `POST /api/v1/schedule-jobs/{id}/publish`.
- **Expected Outcome**: Planner gets 403. Scheduler gets 200 OK (`status == "PUBLISHED"`).

#### `UAT-SCHEDULER-006`: Disruption Repair: `locked_assignments_moved == 0`
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SC-07`, `FR-SC-08`
- **Description**: A published timetable experiences an operational disruption: Faculty Invigilator `res-inv-001` becomes suddenly unavailable. The repair engine solves a constrained differential schedule.
- **Core Domain Invariant**: `locked_assignments_moved == 0`. Zero confirmed, unaffected exams move. Only the disrupted assignment is shifted to an alternate qualified invigilator.
- **Input**: Disruption payload specifying resource outage on `res-inv-001`.
- **Expected Outcome**: `ScheduleStatus.FEASIBLE`. `hard_violation_count == 0`. `locked_assignments_moved == 0`. `repair_moved_assignments_count == 1`.

#### `UAT-SCHEDULER-007`: Capacity Control Rejection UX (HTTP 429)
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha` vs `uat-tenant-beta`
- **Traceability**: `FR-RES-01`
- **Description**: When a tenant exceeds concurrent solve quotas, subsequent submissions are rejected with HTTP 429 and `Retry-After` header. Tenant Alpha quota saturation does NOT affect Tenant Beta.
- **Input**: Solve submission when Tenant Alpha queue is full.
- **Expected Outcome**: HTTP 429 `capacity.quota_exceeded`, `Retry-After: 30`. Tenant Beta succeeds normally.

---

### 2.5 Auditor Acceptance Scenarios (`UAT-AUDITOR-*`)

#### `UAT-AUDITOR-001`: Chronological Audit Trail Reconstruction
- **Persona**: `UAT_AUDITOR` (`user:uat_auditor_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SEC-04`
- **Description**: Auditor queries the append-only audit trail to inspect historical operations (goal creation, plan approval, job submission, publication). Verifies who, what, when, and tenant binding.
- **Input**: `GET /api/v1/audit`.
- **Expected Outcome**: HTTP 200 OK. Events list contains complete actor and action metadata for `uat-tenant-alpha`.

#### `UAT-AUDITOR-002`: Strict Mutation Prohibition Across All Operations
- **Persona**: `UAT_AUDITOR` (`user:uat_auditor_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SEC-05`
- **Description**: The Auditor persona is strictly read-only. Attempts to mutate state across all functional areas (Goal creation, Task patch, Plan approve, Solve submit, Publish, Repair, Workflow deploy) are uniformly rejected with HTTP 403 Forbidden.
- **Input**: 7 distinct mutation endpoints invoked with Auditor credentials.
- **Expected Outcome**: 100% of mutation attempts return HTTP 403 Forbidden.

#### `UAT-AUDITOR-003`: Cross-Tenant Audit Isolation
- **Persona**: `UAT_AUDITOR` (`user:uat_auditor_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SEC-06`
- **Description**: Auditor queries audit trail; engine strictly filters records so zero events belonging to `uat-tenant-beta` are returned.
- **Input**: `GET /api/v1/audit`.
- **Expected Outcome**: Every returned event has `tenant_id == "uat-tenant-alpha"`. Zero Tenant Beta records leaked.

---

### 2.6 Tenant Admin Acceptance Scenarios (`UAT-TENANT-ADMIN-*`)

#### `UAT-TENANT-ADMIN-001`: BPMN Workflow Definition Deployment
- **Persona**: `UAT_TENANT_ADMIN` (`user:uat_admin_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-WF-05`
- **Description**: Tenant Admin uploads and deploys a validated BPMN XML workflow definition.
- **Input**: `POST /api/v1/workflows/definitions/{id}/deploy`.
- **Expected Outcome**: HTTP 200 OK. `is_active == True`, valid `deployment_id`.

#### `UAT-TENANT-ADMIN-002`: Service Credential Lifecycle & Instant Revocation (Operational Admin Procedure)
- **Persona**: `UAT_TENANT_ADMIN` (`user:uat_admin_alpha`) / Platform Operator
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SEC-07`
- **Capability Classification**: Controlled Operational/Admin Procedure (Option B). Service credentials are managed directly via `PostgresServiceApiKeyCredentialStore`; there is no tenant-facing HTTP product API endpoint.
- **Description**: Demonstrates service API key lifecycle: key creation, one-way SHA-256 digest computation, successful authentication, and instant rejection upon revocation (`enabled=False`).
- **Input**: `e7sk_<credential_id>.<secret>`, key verification, status toggle, re-verification.
- **Expected Outcome**: Active key authenticates. Revoked key immediately raises `CredentialDisabledError`.

#### `UAT-TENANT-ADMIN-003`: Operational Capacity Visibility
- **Persona**: `UAT_TENANT_ADMIN` (`user:uat_admin_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-RES-02`
- **Description**: Observes active concurrency and solve queue capacity settings.
- **Input**: Inspect `TenantQuotaManager` configuration.
- **Expected Outcome**: Quota thresholds strictly positive and enforced.

---

### 2.7 Security & Isolation Scenarios (`UAT-AUTH-*`, `UAT-ISOLATION-*`)

#### `UAT-AUTH-NEG-001`: Unauthenticated Request (Missing Credentials)
- **Input**: `POST /api/v1/goals` without `Authorization` or `X-E7-Service-Key`.
- **Expected Outcome**: HTTP 401 Unauthorized (`auth.credentials_missing`).

#### `UAT-AUTH-NEG-002`: Malformed Bearer Token
- **Input**: `POST /api/v1/goals` with `Authorization: NotBearer token`.
- **Expected Outcome**: HTTP 401 Unauthorized (`auth.invalid_authorization_header`).

#### `UAT-AUTH-NEG-003`: Conflicting Dual Credentials Fail Closed
- **Input**: `POST /api/v1/goals` with both `Authorization: Bearer ...` and `X-E7-Service-Key: ...`.
- **Expected Outcome**: HTTP 400 Bad Request (`auth.conflicting_credentials`).

#### `UAT-ISOLATION-001`: Cross-Tenant Anti-Enumeration (Uniform HTTP 404)
- **Persona**: `UAT_FOREIGN_USER` (Tenant Beta)
- **Target**: Tenant Alpha Resources (Plans, Baselines, Workflows, Jobs).
- **Description**: Accessing resources across tenants returns HTTP 404 (never 403), preventing existence enumeration.
- **Expected Outcome**: 100% of cross-tenant resource probes return HTTP 404 Not Found.

---

### 2.8 External Boundary Scenarios (`UAT-BOUNDARIES-*`)

#### `UAT-BOUNDARIES-001`: MySQL Live Read Boundary
- **Status**: `BLOCKED_ON_MYSQL_READ_ONLY_ACCOUNT_PRIVILEGES`.
- **Validation**: Boundary flag verified and preserved without regression.

#### `UAT-BOUNDARIES-002`: E8 Notification Boundary
- **Status**: `BLOCKED_ON_E8_ENDPOINT` (live) / `PASS` (local contract dispatch).
- **Validation**: Outbox event contract valid; external network call safely guarded.

#### `UAT-BOUNDARIES-003`: Google Calendar Boundary
- **Status**: `PASS / FROZEN`.
- **Validation**: Existing contract validated and frozen.

#### `UAT-BOUNDARIES-004`: Rate Limiting Boundary
- **Status**: `BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION`.
- **Validation**: Gateway boundary confirmed.

---

### 2.9 End-to-End Traceable Story (`UAT-E2E-001`)

#### `UAT-E2E-001`: Complete Academic Operations Cycle
- **Description**: Executes a unified end-to-end operational narrative across all 5 personas:
  1. `UAT_PLANNER` authors "Grade 10 Semester Exam Readiness Cycle" goal.
  2. Plan proposal generated and validated.
  3. `UAT_APPROVER` reviews critical path and approves baseline (`is_baseline == True`).
  4. BPMN workflow instance started.
  5. `UAT_APPROVER` completes human sign-off task with idempotency key.
  6. `UAT_SCHEDULER` submits CP-SAT timetabling job.
  7. Feasible timetable published.
  8. Unscheduled invigilator absence repaired with `locked_assignments_moved == 0`.
  9. `UAT_AUDITOR` reviews complete chronological trail.
- **Expected Outcome**: All 9 steps pass in continuous sequence.

---

### 2.10 Dedicated Idempotency Scenarios (`UAT-IDEMPOTENCY-*`)

#### `UAT-IDEMPOTENCY-001`: Plan Approval Natural State Idempotency (`STATE_IDEMPOTENT_RETRY`)
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-TI-06`, `FR-IDEM-01`
- **Semantic Classification**: `STATE_IDEMPOTENT_RETRY` (The plan approval endpoint does not consume or enforce `Idempotency-Key`; repeated requests are safe because the plan has reached terminal `APPROVED` baseline state).
- **Description**: Approver submits baseline approval. First request freezes baseline and transitions plan to `APPROVED` (HTTP 200). A repeated request returns HTTP 200 OK without creating duplicate baseline records or altering the baseline hash.
- **Input**: `POST /api/v1/plans/{id}/approve`.
- **Expected Outcome**: Both calls return HTTP 200. Persisted state retains exactly 1 approved baseline record.

#### `UAT-IDEMPOTENCY-002`: Workflow Task Action Keyed Idempotency (`KEYED_IDEMPOTENCY_ENFORCED`)
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-WF-03`, `FR-IDEM-02`
- **Semantic Classification**: `KEYED_IDEMPOTENCY_ENFORCED` (The endpoint strictly requires the `Idempotency-Key` header; enforces replay matching and raises `IdempotencyConflictError` on conflicting payloads).
- **Description**: Approver completes human review task with required `Idempotency-Key` (`task-action-idem-key-002`). First request transitions task to `COMPLETED` (HTTP 200). Repeated request with the same idempotency key returns identical HTTP 200 response without duplicate task completion events or side effects.
- **Input**: `POST /api/v1/workflows/tasks/{id}/actions` with `Idempotency-Key: task-action-idem-key-002`.
- **Expected Outcome**: Both calls return HTTP 200. Persisted state retains exactly 1 task completion record.

#### `UAT-IDEMPOTENCY-003`: Schedule Publication Duplicate Suppression (`DUPLICATE_SUPPRESSION_BY_DOMAIN_RULE`)
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SC-06`, `FR-IDEM-03`
- **Semantic Classification**: `DUPLICATE_SUPPRESSION_BY_DOMAIN_RULE` (The schedule publication endpoint suppresses duplicate records by resolving repeat requests to the already-published active schedule record).
- **Description**: Scheduler submits publish request for completed schedule solve job. First request transitions schedule to `PUBLISHED` (HTTP 200). Repeated publish request returns identical HTTP 200 OK without creating multiple published schedule records.
- **Input**: `POST /api/v1/schedule-jobs/{id}/publish`.
- **Expected Outcome**: Both calls return HTTP 200. Exactly 1 published schedule record exists for the tenant.

---

### 2.11 Dedicated Concurrency Scenarios (`UAT-CONCURRENCY-*`)

#### `UAT-CONCURRENCY-001`: Concurrent Plan Approval Race
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-TI-06`, `FR-CONC-01`
- **Description**: 4 concurrent threads attempt to approve the same plan simultaneously. All concurrent requests complete deterministically with HTTP 200. The plan transitions safely to `APPROVED` with `is_baseline == True` without database deadlocks or state corruption.
- **Expected Outcome**: All 4 threads receive HTTP 200. Exactly 1 approved baseline plan is persisted.

#### `UAT-CONCURRENCY-002`: Concurrent Workflow Task Action
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-WF-03`, `FR-CONC-02`
- **Description**: 4 concurrent threads submit task completion actions with the same idempotency key. All concurrent calls resolve cleanly with HTTP 200 with zero duplicate mutations.
- **Expected Outcome**: All threads receive HTTP 200. Exactly 1 task completion side-effect recorded.

#### `UAT-CONCURRENCY-003`: Concurrent Schedule Publication
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-SC-06`, `FR-CONC-03`
- **Description**: 4 concurrent threads publish the same solve job simultaneously. All threads resolve with HTTP 200. The database retains exactly 1 published schedule record for the tenant.
- **Expected Outcome**: All threads receive HTTP 200. Exactly 1 published schedule record persisted.

#### `UAT-CONCURRENCY-004`: Concurrent Quota Boundary Backpressure
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`)
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-RES-01`, `FR-CONC-04`
- **Description**: 8 concurrent solve job requests arrive when only 3 slots remain in the tenant's solve capacity quota. Exactly 3 requests are admitted; the remaining 5 concurrent requests fail closed with HTTP 429 quota backpressure.
- **Expected Outcome**: Deterministic quota boundary enforcement under concurrency. Zero quota over-allocation.

---

### 2.12 Degraded-Mode Outage Scenarios (`UAT-DEGRADED-*`)

#### `UAT-DEGRADED-RABBITMQ-001`: Broker Outage Mutation Durability, Outbox Backlog & Post-Recovery Drain
- **Persona**: `UAT_SCHEDULER` (`user:uat_scheduler_alpha`) / System
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-RES-03`, `NFR-REL-01`
- **Description**: Operational mutation persists durably to the transactional outbox table in PostgreSQL. When the RabbitMQ broker becomes unavailable (simulated via unreachable broker port), the event publisher raises `PublisherConnectionError` cleanly. The client write succeeds durably because the transactional outbox decouples persistence from asynchronous broker delivery. The mutation remains durably enqueued in the outbox table (`published_at IS NULL`) with zero event loss. Upon broker restoration, reconnecting to the live RabbitMQ broker (port 5672) and executing `OutboxDispatcher.dispatch_once()` successfully drains the event (`outcome == DispatchOutcome.PUBLISHED`), settling `published_at` in the database. A subsequent dispatch returns `IDLE`, proving downstream idempotency with zero duplicate business effect.
- **Expected Outcome**: Outbox event persisted durably in DB; broker connection error handled cleanly; broker restored and dispatcher drains event with settled timestamp; zero event loss; zero duplicate business effect.

#### `UAT-DEGRADED-FLOWABLE-001`: Flowable Engine Outage Clean Error Envelope & Post-Recovery Retry Success
- **Persona**: `UAT_APPROVER` (`user:uat_approver_alpha`) / System
- **Tenant**: `uat-tenant-alpha`
- **Traceability**: `FR-WF-06`, `NFR-REL-02`
- **Description**: A workflow operation is attempted while the Flowable BPMN engine REST endpoint is unreachable (simulated via unreachable port). The adapter fails closed, raising `WorkflowEngineConnectionError` cleanly. No partially corrupted process mappings are stored in PostgreSQL. Upon endpoint restoration (reconnecting to live Flowable engine on port 8080 with administrative credentials), repeating the workflow task query succeeds with a valid task list and zero duplicate process or task side effects.
- **Expected Outcome**: Deterministic connection failure raised; persistent state uncorrupted; clean post-recovery retry succeeds against live engine with zero duplicate mappings.

---

### 2.13 Expanded Operational Domain Cross-Tenant Isolation (`UAT-ISOLATION-*`)

#### `UAT-ISOLATION-GOAL-001`: Cross-Tenant Goal Plan Generation Anti-Enumeration
- **Persona**: `UAT_FOREIGN_USER` (Tenant Beta)
- **Target**: Tenant Alpha Goal (`11111111-0000-4000-a000-000000000001`)
- **Traceability**: `FR-SEC-08`, `FR-TI-01`
- **Input**: `POST /api/v1/goals/{alpha_goal_id}/generate-plan`.
- **Expected Outcome**: HTTP 404 Not Found anti-enumeration. Zero foreign plans created.

#### `UAT-ISOLATION-PLAN-001`: Cross-Tenant Plan Critical Path Anti-Enumeration
- **Persona**: `UAT_FOREIGN_USER` (Tenant Beta)
- **Target**: Tenant Alpha Plan (`22222222-0000-4000-a000-000000000001`)
- **Traceability**: `FR-SEC-08`, `FR-TI-05`
- **Input**: `GET /api/v1/plans/{alpha_plan_id}/critical-path`.
- **Expected Outcome**: HTTP 404 Not Found anti-enumeration. Zero critical path data exposed.

#### `UAT-ISOLATION-TASK-001`: Cross-Tenant Task Mutation Denial & DB Integrity
- **Persona**: `UAT_FOREIGN_USER` (Tenant Beta)
- **Target**: Tenant Alpha Task (`33333333-0000-4000-a000-000000000001`)
- **Traceability**: `FR-SEC-08`, `FR-TI-02`
- **Input**: `PATCH /api/v1/tasks/{alpha_task_id}` with `progress_percentage: 100`.
- **Expected Outcome**: HTTP 404 Not Found anti-enumeration. Database state verified: progress remains 60% (unmodified).

#### `UAT-ISOLATION-WORKFLOW-001`: Cross-Tenant Workflow Instance Anti-Enumeration
- **Persona**: `UAT_FOREIGN_USER` (Tenant Beta)
- **Target**: Tenant Alpha Workflow Instance (`55555555-0000-4000-a000-000000000001`)
- **Traceability**: `FR-SEC-08`, `FR-WF-01`
- **Input**: `GET /api/v1/workflows/instances/{alpha_instance_id}`.
- **Expected Outcome**: HTTP 404 Not Found anti-enumeration. Zero execution data exposed.

#### `UAT-ISOLATION-SCHEDULE-001`: Cross-Tenant Schedule Job Anti-Enumeration
- **Persona**: `UAT_FOREIGN_USER` (Tenant Beta)
- **Target**: Tenant Alpha Schedule Job (`77777777-0000-4000-a000-000000000001`)
- **Traceability**: `FR-SEC-08`, `FR-SC-01`
- **Input**: `GET /api/v1/schedule-jobs/{alpha_job_id}`.
- **Expected Outcome**: HTTP 404 Not Found anti-enumeration. Zero solver metadata or diagnostics exposed.

#### `UAT-ISOLATION-AUDIT-001`: Cross-Tenant Audit Log Isolation
- **Persona**: `UAT_FOREIGN_USER` (Tenant Beta Auditor)
- **Traceability**: `FR-SEC-06`, `FR-SEC-08`
- **Input**: `GET /api/v1/audit`.
- **Expected Outcome**: HTTP 200 OK. Exactly 0 Tenant Alpha audit records visible to Tenant Beta caller.

---

### 2.14 Negative Service-Key Authentication Scenarios (`UAT-AUTH-SERVICE-KEY-*`)

#### `UAT-AUTH-SERVICE-KEY-INVALID`: Non-Existent / Invalid Service API Key
- **Input**: `GET /api/v1/plans/{id}/critical-path` with `X-E7-Service-Key: e7sk_00000000000000000000000000000000.invalidsecret`.
- **Expected Outcome**: HTTP 401 Unauthorized (`auth.invalid_service_key`). Fail-closed behavior.

#### `UAT-AUTH-SERVICE-KEY-REVOKED`: Revoked Service API Key
- **Input**: `GET /api/v1/plans/{id}/critical-path` with a service key revoked in database (`enabled = False`).
- **Expected Outcome**: HTTP 401 Unauthorized. Key revocation takes effect immediately.

#### `UAT-AUTH-SERVICE-KEY-EXPIRED`: Expired Service API Key
- **Input**: `GET /api/v1/plans/{id}/critical-path` with an expired service key (`expires_at` in the past).
- **Expected Outcome**: HTTP 401 Unauthorized. Expired credential fails closed immediately.

