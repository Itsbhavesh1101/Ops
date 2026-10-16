# Phase 6: User Acceptance Testing (UAT) & Demonstration Script
**Grade 10 Semester Examination Operations**

## 1. Scope & Execution Rules
This script guides an operator or evaluator through the complete Grade 10 Semester Examination scenario on IR Infotech E-7 Operations Engine.

### Invariants:
- **Zero Manual Database Edits**: Never run SQL `INSERT`, `UPDATE`, or `psql` patches to advance state.
- **Single Root Correlation ID**: Propagated across all steps.
- **All 3 E-7 Modes Exercised**: Plan -> Automate -> Schedule -> Monitor.

---

## 2. Step-by-Step Operator UAT Procedures

### Step 1: Submit Operational Goal
- **Operator Action**: Submit goal payload to `POST /api/v1/goals`.
  ```json
  {
    "objective": "Conduct Grade 10 Semester Examinations",
    "scope": "Grade 10 cohort across all academic subjects",
    "deadline": "2026-11-15T18:00:00Z"
  }
  ```
- **Expected State**: Goal record created with status `DRAFT`.
- **Expected Canonical Event**: `goal.created`
- **Expected Audit Evidence**: Event type `goal.created`, resource type `goal`, matching `goal_id`.
- **Failure Condition**: HTTP 4xx/5xx or missing `goal_id`.

---

### Step 2: Generate Structured Plan
- **Operator Action**: Request plan generation via `POST /api/v1/goals/{goal_id}/plans`.
- **Expected State**: Plan generated with 10 tasks, 3 milestones, acyclic dependencies. Plan status `GENERATED`.
- **Expected Canonical Event**: `plan.generated`
- **Expected Audit Evidence**: Event type `plan.generated`, details containing `task_count: 10`, `milestone_count: 3`.
- **Failure Condition**: Validation error, missing milestones, or circular dependency error.

---

### Step 3: Validate Plan & Calculate CPM
- **Operator Action**: Trigger validation pipeline `POST /api/v1/plans/{plan_id}/validate`.
- **Expected State**: Plan status becomes `VALIDATED`. Critical path and slack computed.
- **Expected Audit Evidence**: Event type `plan.validated`, details containing `project_duration_minutes`.
- **Failure Condition**: `is_valid` is `false` or DAG cycles detected.

---

### Step 4: Approve Plan Baseline
- **Operator Action**: Authorized operator submits approval to `POST /api/v1/plans/{plan_id}/approve`.
- **Expected State**: Plan transitions to `APPROVED`. Baseline hash locked.
- **Expected Canonical Event**: `plan.approved`
- **Expected Audit Evidence**: Event type `plan.approved`, `actor_principal_id` matching operator.
- **Failure Condition**: Unauthorized access (HTTP 403) or missing `plan.approved` event in outbox.

---

### Step 5: Start Readiness Workflow
- **Operator Action**: Inbound consumer processes `plan.approved` event.
- **Expected State**: Flowable process instance starts (`exam-operations-v1`). Status `RUNNING`.
- **Expected Canonical Event**: `workflow.task.created` (for Science & Humanities department reviews).
- **Expected Audit Evidence**: Event type `workflow.instance_started`, engine process ID recorded.
- **Failure Condition**: Workflow not started, or duplicate workflow started on replay.

---

### Step 6: Department Approvals & DMN Evaluation
- **Operator Action**: Department chairs complete assigned user tasks via `POST /api/v1/workflows/tasks/{task_id}/action`.
  - Science Chair: Action `COMPLETE`, Decision `APPROVED`.
  - Humanities Chair: Action `COMPLETE`, Decision `APPROVED`.
- **Expected State**: Both user tasks completed. DMN decision table evaluates to `PROCEED_TO_SCHEDULING`.
- **Expected Audit Evidence**: Event types `workflow.task_completed` and `workflow.dmn_evaluated`.
- **Failure Condition**: Task rejected or DMN hit policy violation.

---

### Step 7: Verify Readiness & Unlock Scheduling
- **Operator Action**: Complete task `T4_RESOURCE_READINESS` via `PUT /api/v1/plans/{plan_id}/tasks/{task_id}/progress`.
- **Expected State**: Task status `COMPLETED`. Milestone `M1_READINESS` completed.
- **Expected Canonical Event**: `task.completed`
- **Expected Audit Evidence**: Event type `task.completed`, details `unlocks_scheduling: true`.
- **Failure Condition**: Task fails to complete or scheduling demand not unlocked.

---

### Step 8: Solve Timetable with OR-Tools
- **Operator Action**: Background worker consumes scheduling demand and solves with CP-SAT academic profile.
- **Expected State**: Solve status `FEASIBLE`. Hard violations = 0. Schedule status `REVIEW` (Version 1).
- **Expected Canonical Event**: `schedule.generated`
- **Expected Audit Evidence**: Event type `schedule.generated`, `version: 1`, `hard_violations: 0`.
- **Failure Condition**: Solver timeout, `INFEASIBLE`, or hard constraint violations > 0.

---

### Step 9: Review & Publish Schedule
- **Operator Action**: Operator reviews candidate and publishes via `POST /api/v1/schedules/{schedule_id}/publish`.
- **Expected State**: Schedule status transitions `REVIEW` -> `PUBLISHED`.
- **Expected Canonical Event**: `schedule.published`
- **Expected Integrations Side Effects**:
  - `FakeCalendarAdapter`: 10 calendar events created with status `SYNCED`.
  - `MockNotificationDispatcher`: `NotificationCommand` received with template `EXAM_TIMETABLE_PUBLISHED`.
- **Expected Audit Evidence**: Event type `schedule.published`, followed by `calendar.synced` and `notification.dispatched`.
- **Failure Condition**: Auto-publish without human review, or calendar sync reported false SUCCESS.

---

### Step 10: Simulate Disruption (Proctor Unavailable)
- **Operator Action**: Emit canonical event `resource.unavailable` for proctor `INV-001` during exam window.
  ```json
  {
    "event_type": "resource.unavailable",
    "resource_id": "INV-001",
    "start_at": "2026-11-02T08:00:00Z",
    "end_at": "2026-11-02T18:00:00Z"
  }
  ```
- **Expected State**: Targeted minimal-churn repair job created. Old published baseline v1 untouched.
- **Expected Audit Evidence**: Event type `resource.unavailable`.
- **Failure Condition**: Immediate destructive mutation of published schedule v1.

---

### Step 11: Solve Targeted Repair & Republish
- **Operator Action**: Solver generates candidate v2; operator reviews and republishes via `POST /api/v1/schedules/{repair_id}/publish`.
- **Expected State**:
  - Schedule v1 transitions `PUBLISHED` -> `SUPERSEDED`.
  - Schedule v2 transitions `REVIEW` -> `PUBLISHED`.
  - Unaffected sessions remain locked in place.
- **Expected Canonical Events**: `schedule.published` and `schedule.changed`.
- **Expected Integrations Side Effects**: Calendar resyncs modified sessions; delta notification dispatched.
- **Expected Audit Evidence**: Event types `schedule.superseded` and `schedule.changed`.
- **Failure Condition**: Locked assignments moved or `schedule.changed` omitted.

---

### Step 12: Audit & Dashboard Verification
- **Operator Action**:
  1. Inspect `GET /api/v1/audit?correlation_id={root_corr}`: Complete chronological trace returned.
  2. Inspect `GET /api/v1/analytics/operations`:
     - Plan progress: 10 total tasks, 0 blockers.
     - SLA state: `HEALTHY` or `IDLE`.
     - Schedule health: `PUBLISHED`, version 2, hard violations 0, calendar `SYNCED`.
     - Repair movement: Moved count recorded, locked moved = 0.
     - Recent events: Activity stream populated.
- **Expected State**: Visual Operations Dashboard renders green across all 5 operational widgets.
- **Failure Condition**: Missing correlation links or discrepancy in dashboard metrics.
