# Phase 6: Canonical Event Chain & Vocabulary Specification

## 1. Event Vocabulary Invariant
The E-7 Operations Engine enforces a strict, frozen 12-event canonical vocabulary. No synthetic orchestration events were invented for Phase 6.

### Approved Canonical Event Inventory
1. `goal.created`
2. `plan.generated`
3. `plan.approved`
4. `task.blocked`
5. `task.completed`
6. `workflow.task.created`
7. `workflow.sla.warning`
8. `workflow.sla.breached`
9. `resource.unavailable`
10. `schedule.generated`
11. `schedule.published`
12. `schedule.changed`

### Explicit Negative Invariant
The following non-canonical event names are **PROHIBITED** and were **NOT INVENTED**:
- `vertical.started`
- `workflow.completed` (scheduling demand unlocked via canonical `task.completed`)
- `exam.ready`
- `schedule.requested`
- `repair.completed`
- `dashboard.updated`

---

## 2. Chronological Phase-6 Event Execution Chain

| Step | Canonical Event Name | Emitting Service / Subsystem | Primary Entity Payload | Purpose / Downstream Effect |
| :--- | :--- | :--- | :--- | :--- |
| **1** | `goal.created` | Goal Intake (`GoalService`) | `goal_id`, `objective`, `scope` | Records operator intent to conduct Grade 10 semester exams. |
| **2** | `plan.generated` | Task Intelligence (`PlanService`) | `plan_id`, `goal_id`, `baseline_hash` | Structured PlanSchema produced by planner fixture. |
| **3** | `plan.approved` | Task Intelligence (`PlanApprovalService`) | `plan_id`, `goal_id`, `workflow_key` | Plan baseline approved; triggers Flowable workflow handler. |
| **4** | `workflow.task.created` | Workflow Engine (`WorkflowService`) | `workflow_task_id`, `role`, `task_name` | Parallel department review task (Science Department Chair). |
| **5** | `workflow.task.created` | Workflow Engine (`WorkflowService`) | `workflow_task_id`, `role`, `task_name` | Parallel department review task (Humanities Department Chair). |
| **6** | `task.completed` | Task Intelligence (`PlanService`) | `task_id` (`T4_RESOURCE_READINESS`), `plan_id` | Question paper and resource readiness verified; unlocks scheduling. |
| **7** | `schedule.generated` | Scheduling Worker (`CpSatScheduler`) | `schedule_id` (v1), `schedule_key`, `score` | OR-Tools solves initial timetable (status: `REVIEW`, 0 hard violations). |
| **8** | `schedule.published` | Scheduling Service (`PublishPolicy`) | `schedule_id` (v1), `version`: 1 | Reviewer approves timetable; syncs calendar and dispatches E-8 notif. |
| **9** | `resource.unavailable` | Resource Monitoring / External | `resource_id`: `INV-001`, `start_at`, `end_at` | Simulated proctor medical emergency; triggers minimal-churn repair. |
| **10**| `schedule.generated` | Scheduling Worker (`CpSatScheduler`) | `schedule_id` (v2), `supersedes_id` (v1) | OR-Tools solves targeted repair (status: `REVIEW`, 0 hard violations). |
| **11**| `schedule.published` | Scheduling Service (`PublishPolicy`) | `schedule_id` (v2), `version`: 2 | Repaired schedule published; marks v1 as `SUPERSEDED`. |
| **12**| `schedule.changed` | Scheduling Service (`PublishPolicy`) | `old_schedule_id` (v1), `new_schedule_id` (v2) | Emitted upon republish; triggers calendar update and diff notifications. |

---

## 3. Event Envelope & Transport Semantics
- **Atomicity**: State mutation + outbox event commit occur within a single database transaction.
- **Envelope Schema**: Strict conformance to `EventEnvelope` with E-7 generated UUID `event_id`, timezone-aware UTC `occurred_at`, and frozen payload.
- **Deduplication**: Inbound event consumers record message delivery IDs in PostgreSQL `inbox_messages` preventing duplicate side effects.
- **Transport Independence**: RabbitMQ serves purely as a delivery transport; PostgreSQL is the single authoritative system-of-record.
