# Phase 4: Authorized Schedule Publication & Baseline Locking

## 1. Lifecycle State Machine
Schedules progress strictly through the following states:
```
[FEASIBLE] -> [REVIEW] -> [PUBLISHED] -> [SUPERSEDED]
```
- **FEASIBLE**: Candidate schedule successfully computed by solver worker, containing zero hard-constraint violations.
- **REVIEW**: Solver job completion transitions the candidate schedule to `REVIEW`. The schedule is inspectable by operations personnel but not yet authoritative. Emits `schedule.generated`.
- **PUBLISHED**: Authorized operator or service actor approves publication via `POST /api/v1/schedule-jobs/{id}/publish`. The schedule becomes authoritative and locked. Emits `schedule.published`.
- **SUPERSEDED**: When a newer repaired version is published, the prior active `PUBLISHED` schedule is atomically marked `SUPERSEDED`. Emits `schedule.changed`.

Direct jumps such as `FEASIBLE -> PUBLISHED`, or attempting to publish an already `SUPERSEDED` or `PUBLISHED` schedule, raise `InvalidStateTransitionError`.

## 2. Authorization & Policy Contract
Publication is guarded by the `SchedulePublishPolicy` Protocol:
```python
class SchedulePublishPolicy(Protocol):
    def can_publish(self, *, tenant_id: str, principal: str, schedule: ScheduleRecord) -> bool:
        ...
```
- **Default Implementation**: `DefaultSchedulePublishPolicy` provides a fail-closed, provider-neutral check.
  - Requires valid non-empty tenant, principal, and schedule.
  - Verifies schedule status is `REVIEW` and contains assignments.
  - Validates that the schedule belongs to the authenticated tenant.
  - Configurable for organizational role checks without hardcoding vendor-specific claim structures.

## 3. Atomic Database Transactions
Publication executes within a single PostgreSQL transaction managed by `UnitOfWork`:
1. Acquire row-level locks on the target schedule and existing active `PUBLISHED` schedules for the schedule key.
2. Verify preconditions via `validate_publish_preconditions(...)`.
3. Transition target schedule: `status = PUBLISHED`, `published_at = utc_now()`, `published_by = principal`.
4. Transition older active schedules: `status = SUPERSEDED`.
5. Enqueue outbox events:
   - `schedule.published` for the newly active schedule.
   - `schedule.changed` if an older schedule was superseded.
6. Insert audit trail record in `audit_events` with actor metadata and publish evidence.
7. Trigger asynchronous calendar synchronization via `OutboundCalendarSyncService`.
