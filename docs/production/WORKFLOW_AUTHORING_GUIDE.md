# E-7 Operations Engine — Workflow Authoring & Operations Guide

**Document Version:** 1.0.0  
**Phase:** Phase 8F Canonical Production Documentation  
**Workflow Engine:** Flowable 7.x (REST API Adapter)  
**Supported Standards:** BPMN 2.0, DMN 1.3, JSON Forms Schema  

---

## 1. Architecture & Engine Integration

**Governance Classification:** `ORIGINAL_REQUIREMENT` & `APPROVED_ADR_DECISION` (ADR-001)

The E-7 Operations Engine integrates with Flowable 7.x via a decoupled, resilient HTTP adapter (`FlowableAdapter`). Business services interact with workflows through the canonical `WorkflowService` and API v1 endpoints (`/api/v1/workflows/*` and singular `/api/v1/workflow/*` aliases).

### 1.1 Key Architectural Responsibilities

1. **State Dual-Master Pattern:**
   - **Flowable:** Orchestrates BPMN execution state, execution tokens, active user tasks, boundary timers, and DMN rule evaluations.
   - **E-7 Database:** Persists authoritative business entities (`workflow_definitions`, `workflow_instances`, `workflow_task_mappings`, `sla_policies`).
2. **Strict Tenant Scoping:**
   - Flowable deployments and process instances are created with `tenantId: <tenant_id>`.
   - Engine calls include the tenant identifier in request headers / URLs.
   - Requests attempting cross-tenant task claiming or action execution fail closed (`403 Forbidden`).
3. **Separation of Duties (SoD):**
   - Built-in authorization policy verifies that task completers belong to required candidate roles and prevents process initiators from approving their own submissions (`tests/security/test_separation_of_duties.py`).

---

## 2. Real Production Examples in Repository

The repository includes tested reference definitions used in vertical-slice testing and Phase-6 UAT:

| Artifact Path | Standard | Key / ID | Description |
|---|---|---|---|
| [`workflows/bpmn/semester_exam_readiness.bpmn20.xml`](file:///f:/operations-engine/workflows/bpmn/semester_exam_readiness.bpmn20.xml) | BPMN 2.0 | `semesterExamReadinessProcess` | End-to-end exam readiness workflow with multi-role reviews and checklist actions. |
| [`workflows/bpmn/two_step_approval.bpmn20.xml`](file:///f:/operations-engine/workflows/bpmn/two_step_approval.bpmn20.xml) | BPMN 2.0 | `twoStepApprovalProcess` | Sequential manager and department head approval process. |
| [`workflows/dmn/approval_routing.dmn13.xml`](file:///f:/operations-engine/workflows/dmn/approval_routing.dmn13.xml) | DMN 1.3 | `approvalRoutingTable` | Decision table mapping budget limits to required approval tiers. |
| [`workflows/dmn/exam_readiness_rules_v1.dmn13.xml`](file:///f:/operations-engine/workflows/dmn/exam_readiness_rules_v1.dmn13.xml) | DMN 1.3 | `examReadinessRules` | Evaluation rules verifying teacher rosters and room seating readiness. |
| [`workflows/forms/expense_approval_form.json`](file:///f:/operations-engine/workflows/forms/expense_approval_form.json) | JSON Form | `expenseApprovalForm` | User task UI form schema with typed field definitions. |

---

## 3. Workflow Authoring Rules & Invariants

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR`

Authors must adhere to these invariants when drafting BPMN 2.0 XML:

### Rule 1: Stable Process Definition Keys
- The BPMN root `<process id="..." name="...">` must have an immutable, kebab-case or camelCase process ID (e.g. `semesterExamReadinessProcess`).
- Changing the process ID registers a *new* workflow definition rather than updating existing versions.

### Rule 2: Explicit Task Definition Keys
- Every `<userTask id="..." name="...">` must specify a deterministic, human-readable ID (e.g. `reviewCurriculumReadinessTask`).
- Task IDs are referenced by frontend UI forms and API task synchronization mappings.

### Rule 3: Candidate Groups & Candidate Users
- Configure candidate groups matching authorized E-7 roles:
  ```xml
  <userTask id="examApprovalTask" name="Exam Readiness Approval"
            flowable:candidateGroups="curriculum_head,academic_dean">
  </userTask>
  ```
- Do not hardcode specific personal user IDs in XML; use dynamic expressions (`${assigneeUserId}`) or role candidate groups.

### Rule 4: Variable Filtering & Security Scrubbing
- All variables passed to `/api/v1/workflows/instances` or `/api/v1/workflows/tasks/{task_id}/actions` pass through `filter_workflow_variables`.
- Plaintext passwords, database credentials, and raw tokens are stripped by default. Only JSON-serializable primitives and approved nested structures are stored.

---

## 4. Workflow Lifecycle & Operations

### 4.1 Step 1: Pre-Deployment Validation
Before deploying to production, validate the BPMN XML against local validation tools:
```bash
# Using E-7 API validation endpoint:
POST /api/v1/workflows/validate-bpmn
Content-Type: application/xml
<definitions ...>
```
The validator (`BpmnValidator`) checks:
- Well-formed XML and schema compliance.
- Exactly one none-start event.
- At least one end event.
- Connectivity: No disconnected tasks or unreachable gateway branches.
- Absence of executable script tasks containing arbitrary system commands.

### 4.2 Step 2: Deployment
Deploying creates an immutable record in `workflow_definitions` and submits the artifact to Flowable:
```http
POST /api/v1/workflows/definitions
Authorization: Bearer <token>
Content-Type: application/json

{
  "workflow_key": "semester-exam-readiness",
  "name": "Semester Examination Readiness Workflow",
  "version": 1,
  "artifact_xml": "<definitions ...>...</definitions>"
}
```
Then deploy to Flowable runtime:
```http
POST /api/v1/workflows/definitions/{definition_id}/deploy
```

### 4.3 Step 3: Starting a Process Instance
```http
POST /api/v1/workflows/instances
Authorization: Bearer <token>
Content-Type: application/json

{
  "workflow_key": "semester-exam-readiness",
  "business_reference": "EXAM-2026-SPRING",
  "input_variables": {
    "exam_period": "2026-05",
    "cohort_count": 12
  }
}
```

### 4.4 Step 4: Human Task Synchronization & Actions
1. Query active tasks:
   ```http
   GET /api/v1/workflows/tasks?status=ACTIVE
   ```
2. Perform task action with Idempotency Key:
   ```http
   POST /api/v1/workflows/tasks/{task_id}/actions
   Authorization: Bearer <token>
   Idempotency-Key: e7-task-act-98765
   Content-Type: application/json

   {
     "action": "COMPLETE",
     "variables": {
       "approved": true,
       "approval_comments": "Curriculum verification complete."
     }
   }
   ```

---

## 5. Timers, SLA Management & Escalation

**Governance Classification:** `ORIGINAL_REQUIREMENT`

### 5.1 SLA Policies (`sla_policies` table)
Workflows can bind tasks to predefined SLA policies:
- `target_duration_seconds`: Maximum acceptable completion duration.
- `warning_threshold_seconds`: Warning trigger before breach.
- `escalation_action`: Action dispatched upon breach (e.g. `NOTIFY_SUPERVISOR`, `AUTO_REASSIGN`).

### 5.2 Flowable Boundary Timers
In BPMN, boundary timer events attach to user tasks:
```xml
<boundaryEvent id="slaTimerEvent" attachedToRef="reviewCurriculumReadinessTask" cancelActivity="false">
    <timerDuration>P2D</timerDuration>
</boundaryEvent>
```
When triggered, Flowable invokes notification service tasks or escalation paths without crashing the primary task token.

---

## 6. Failure Recovery & Engine Restarts

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR`

### 6.1 Flowable Service Interruption
- If Flowable is temporarily unreachable (`503 Service Unavailable`), the E-7 API returns `503` with a structured `workflow.engine_unavailable` error.
- Mutating task requests submitted with `Idempotency-Key` can be safely retried once Flowable recovers without duplicate task executions.

### 6.2 Worker Reconciliation Loop
- Upon worker reboot or network recovery, the background workflow reconciliation runner compares active tasks in `workflow_task_mappings` with Flowable's active tasks and syncs overdue SLA states.
- Reference runbook: [`docs/phase-8/recovery/FLOWABLE_RECOVERY.md`](file:///f:/operations-engine/docs/phase-8/recovery/FLOWABLE_RECOVERY.md).
