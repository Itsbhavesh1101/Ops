# Phase 6: Canonical Baseline & Freezing Manifest

## 1. Phase Status Summary
- **Phase 0 (Foundation Contracts & PoC Datasets)**: COMPLETE / FROZEN
- **Phase 1 (Data Layer & Infrastructure Core)**: COMPLETE / FROZEN
- **Phase 2A (Task Intelligence Deterministic Core)**: COMPLETE / FROZEN
- **Phase 2B Core (Context & Verification Engine)**: COMPLETE / FROZEN
- **Canonical Phase 2 (AI Planner Model Selection)**: `IN_PROGRESS_PENDING_PROVIDER_BENCHMARK`
- **Phase 3 (Workflow Engine & Flowable Integration)**: COMPLETE / FROZEN
- **Phase 4 (OR-Tools Scheduling Engine & Repair)**: COMPLETE / FROZEN
- **Phase 5 (Education Scheduling Benchmark & ADR-010)**: COMPLETE / FROZEN
- **Phase 6 (Unified Semester-Exam Vertical Slice)**: **IMPLEMENTED / BLOCKED_ON_LIVE_INFRASTRUCTURE_GATE**
- **Phase 7 (Enterprise Integrations & Risk Monitoring)**: **UNSTARTED / NOT_READY**
- **Phase 7 Engineering Readiness**: **BLOCKED_PENDING_PHASE6_LIVE_GATE**

---

## 2. Database Schema Stability Invariant
- **Application Tables in `Base.metadata`**: Strictly 24 tables
  1. `audit_events`
  2. `calendar_links`
  3. `constraint_definitions`
  4. `goals`
  5. `idempotency_records`
  6. `inbox_messages`
  7. `jobs`
  8. `milestones`
  9. `outbox_events`
  10. `plans`
  11. `resource_availabilities`
  12. `resources`
  13. `schedule_assignments`
  14. `schedule_jobs`
  15. `schedules`
  16. `sla_policies`
  17. `solver_profiles`
  18. `task_dependencies`
  19. `task_risks`
  20. `tasks`
  21. `workflow_artifacts`
  22. `workflow_definitions`
  23. `workflow_instances`
  24. `workflow_task_mappings`
- **Alembic Migration Head**: `0012_schedule_publish_repair_calendar.py`
- **Migration 0013**: **NONE** (Zero schema gaps required for Phase-6 vertical slice).

---

## 3. Strict Boundary Conformance
1. **Canonical Phase 2 Unresolved**: The Phase-6 vertical slice utilized the deterministic `SemesterExamPlanner` fixture. It did NOT choose OpenAI, Gemini, Sarvam, or any production LLM provider.
2. **Phase 7 Not Started**:
   - Zero real Google Calendar runtime calls (uses `FakeCalendarAdapter`).
   - Zero real ERP/SIS provider adapters.
   - Zero real external notification transport (uses `MockNotificationDispatcher`).
   - Zero predictive risk monitor.
   - Zero historical utilization trend engine.

---

## 4. Verification Deliverables
- **Unit Component Suite**: `tests/unit/test_phase6_components.py` (10 tests, all passed, covering planner, validator, CPM, mock dispatcher, audit queries, analytics, handlers, plan lifecycle state machine, approval preconditions, and BPMN SLA boundary timers).
- **Vertical Slice Integration Suite**: `tests/integration/vertical_slice/test_phase6_vertical_slice.py` (passed, proving Plan -> Automate -> Schedule -> Monitor loop with zero manual DB edits).
- **Opt-In Live E2E Suite**: `tests/e2e/phase6/test_semester_exam_e2e.py` (gated via `E7_RUN_PHASE6_E2E=1`, strictly asserting live PostgreSQL and Flowable availability).
- **Operations Dashboard UI**: `ui/operations-ui/src/components/OperationsDashboard.tsx` with typecheck, unit tests, and production build validated.
