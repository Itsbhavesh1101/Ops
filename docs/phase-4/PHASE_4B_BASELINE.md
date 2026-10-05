# Phase 4B Baseline & Verification Audit

## 1. Overview & Architecture Scope
Phase 4B completes the Generic Scheduling MVP by delivering:
- **Authorized Schedule Publication**: Transitioning candidate schedules from `REVIEW` to `PUBLISHED` via provider-neutral policy (`SchedulePublishPolicy`, `DefaultSchedulePublishPolicy`).
- **Baseline Locking & Freeze Semantics**: Published schedules are completely immutable; once published, modifications occur only by creating a new schedule version via repair.
- **Atomic Version Superseding**: Publishing a repaired schedule atomically marks the new schedule `PUBLISHED` and previous active versions `SUPERSEDED` within a single PostgreSQL transaction.
- **Targeted Incremental Repair**: Deterministic `RepairScopeBuilder` isolating directly disrupted activities/resources and connected constraints, while freezing unaffected work with hard locks and applying weighted movement penalties.
- **Canonical Domain Events**: Outbox emission of `schedule.generated` (when candidate created in REVIEW), `schedule.published`, `schedule.changed`, and inbox-deduplicated consumption of `resource.unavailable`.
- **Calendar Boundary Adapter**: RFC 5545 serialization/parsing with zero external dependencies, secondary `calendar_links` mapping table, idempotent synchronization, truthful degraded outage recording (`sync_status = "FAILED"`), and periodic reconciliation.

## 2. Database Schema & Alembic Lineage
- **Application Tables**: Exactly 24 tables in `Base.metadata`.
  - Phase 1 (5): `audit_events`, `outbox_events`, `inbox_messages`, `idempotency_records`, `jobs`
  - Phase 2A (6): `goals`, `plans`, `milestones`, `tasks`, `task_dependencies`, `task_risks`
  - Phase 3A (3): `workflow_definitions`, `workflow_instances`, `workflow_task_mappings`
  - Phase 3B (2): `sla_policies`, `workflow_artifacts`
  - Phase 4A (7): `resources`, `resource_availabilities`, `constraint_definitions`, `solver_profiles`, `schedule_jobs`, `schedules`, `schedule_assignments`
  - Phase 4B (1): `calendar_links`
- **Alembic Single Head**: `0012` (`0012_schedule_publish_repair_calendar.py`) over down revision `0011` (`0011_create_generic_scheduling_core.py`).
- **Rollback / Rollforward Verification**:
  - `0011 -> 0012 -> 0011 -> 0012 -> base -> head -> repeat head` verified against live PostgreSQL (`localhost:55432`).

## 3. Operational Requirements & Invariants
- **Fail-Closed Authorization**: Schedule publication validates authorization using `SchedulePublishPolicy` and rejects unauthenticated/unauthorized calls.
- **Phase-1 Idempotency**: `IdempotencyRepository` markers are provisionally acquired; replaying the same request with identical payload returns the existing entity; altered payloads raise `IdempotencyConflictError`.
- **Deterministic Repair Scoping**: Scope builder deterministically identifies disrupted tasks and only broadens scope along precedence/resource chains, hard-locking all other tasks.
- **True Degradation Reporting**: Calendar service outages immediately set `sync_status = "FAILED"` with explicit error message; no fake success or premature rollbacks of the primary schedule transaction.
- **Phase 5 Boundary**: Academic features (UniTime, academic profiles, student sections) remain strictly UNSTARTED.
