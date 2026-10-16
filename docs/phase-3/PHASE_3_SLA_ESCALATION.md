# Phase 3 SLA Engine, Timers, Pause/Resume & Escalations

## 1. SLA Architecture & Authority
- **Flowable Native Timer Authority**: Waiting timers and job schedules are owned authoritatively by Flowable (`management/timer-jobs`). No in-memory schedulers (`asyncio.sleep`, Celery ETA, APScheduler) are used for authoritative waiting state.
- **E-7 Projection & Threshold Computation**: E-7 computes timezone-aware `warning_at` and `breach_at` datetimes from versioned `SlaPolicy` and injected `BusinessCalendarPolicy`.
- **Timer Durability**: Timers persist in `flowable_db` and survive container restarts (`docker restart e7-flowable`).

## 2. Pause & Resume Semantics
- **Pause**: When a task is paused (e.g. waiting for external input), `paused_at` is recorded and remaining business duration is frozen.
- **Resume**: On resume, new `warning_at` and `breach_at` timestamps are deterministically shifted forward by the elapsed pause duration.

## 3. Warning Event & Escalation
- **Warning Event**: Emits canonical event `workflow.sla.warning` to outbox.
  - Payload contains minimal tracking metadata: `task_id`, `instance_id`, `policy_key`, `warning_at`, `breach_at`.
- **Breach Actions**: Closed typed policy: `REMINDER`, `REASSIGN`, `ESCALATE`, `SERVICE_ACTION`.
- **Append-Only Audit**: Warning and breach triggers record `workflow.sla.warning` and `workflow.sla.breached` in the append-only audit trail.
