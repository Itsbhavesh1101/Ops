# Phase 5 Baseline & Verification Audit

## 1. Overview & Architecture Scope
Phase 5 delivers the Education Scheduling Proof-of-Concept (PoC), comparative solver benchmarking (UniTime vs. OR-Tools-Native), AGPL legal gate enforcement for FET, and publication of the authoritative production path ADR (ADR-010).

All Phase 5 components maintain strict architectural adherence to the frozen Phase 4 generic scheduling engine, canonical PostgreSQL system of record, and decoupled adapter boundaries:
- **Canonical Authority**: E-7 PostgreSQL retains authoritative ownership of all schedule IDs, resources, constraints, publication lifecycle, repair lineage, audit history, and external event synchronization.
- **Provider-Neutral Domain Models**: Abstract DTOs (`EducationSessionDTO`, `StudentCohortDTO`, `AcademicSubjectDTO`, `ExamGapPolicy`, `CanonicalEducationAssignment`, `EducationBenchmarkDataset`) isolate education domain logic from concrete solver libraries.
- **Independent Evaluator**: `EducationScheduleEvaluator` independently verifies 8 hard constraints without relying on solver self-certification.
- **Production Choice (ADR-010)**: Selected **OR-Tools-Native Academic Profile** (`AcademicConstraintCompiler`) as primary production education scheduler; retained UniTime as optional reference adapter; preserved FET AGPL-3.0 exclusion (`NOT_RUN_LEGAL_GATE_NOT_APPROVED`).

---

## 2. Phase Frozen Status Ledger
| Phase | Status | Notes |
| :--- | :---: | :--- |
| **Phase 0** | **COMPLETE / FROZEN** | Architecture, fixtures, compliance framework |
| **Phase 1** | **COMPLETE / FROZEN** | Foundation, outbox/inbox, telemetry, auth |
| **Phase 2A** | **COMPLETE / FROZEN** | Task intelligence domain, goals, plans, CPM |
| **Phase 2B Core** | **COMPLETE / FROZEN** | AI planner, risk scoring, evaluation |
| **Canonical Phase 2** | **IN_PROGRESS_PENDING_PROVIDER_BENCHMARK** | Maintained pending provider benchmark |
| **Phase 3** | **COMPLETE / FROZEN** | Flowable workflow runtime, approvals, DMN |
| **Phase 4A** | **COMPLETE / FROZEN** | Generic scheduling core, CP-SAT solver, profiles |
| **Phase 4B** | **COMPLETE / FROZEN** | Schedule publication, baseline locks, repair, calendar sync |
| **Canonical Phase 4** | **COMPLETE / FROZEN** | Generic scheduling engine complete |
| **Phase 5** | **COMPLETE / FROZEN** | Education scheduling PoC, UniTime vs OR-Tools, ADR-010 |
| **Phase 6** | **UNSTARTED** | Explicitly confirmed UNSTARTED |

---

## 3. Database Schema & Alembic Lineage
- **Application Tables**: Exactly 24 tables in `Base.metadata`.
  - Phase 1 (5): `audit_events`, `outbox_events`, `inbox_messages`, `idempotency_records`, `jobs`
  - Phase 2A (6): `goals`, `plans`, `milestones`, `tasks`, `task_dependencies`, `task_risks`
  - Phase 3A (3): `workflow_definitions`, `workflow_instances`, `workflow_task_mappings`
  - Phase 3B (2): `sla_policies`, `workflow_artifacts`
  - Phase 4A (7): `resources`, `resource_availabilities`, `constraint_definitions`, `solver_profiles`, `schedule_jobs`, `schedules`, `schedule_assignments`
  - Phase 4B (1): `calendar_links`
- **Alembic Single Head**: `0012` (`0012_schedule_publish_repair_calendar.py`) over down revision `0011`.
- **Migration Policy**: **Zero schema changes in Phase 5**. No migration 0013 was created. Educational entities are compiled directly into Phase 4 scheduling entities and persisted in existing tables.

---

## 4. Key Verification Artifacts
- **ADR-010**: `docs/adr/ADR-010-education-scheduler-production-choice.md` (Accepted).
- **Benchmark Manifest**: `E7-PHASE5-EDUCATION-BENCHMARK-MANIFEST@1.0.0` with verified SHA-256 digests.
- **Empirical Results**: `scratch/phase5_benchmark_results.json` and `docs/phase-5/PHASE_5_BENCHMARK_REPORT.md`.
- **Compliance Register**: `docs/OPEN_SOURCE_COMPLIANCE_REGISTER.md` updated with UniTime Apache-2.0 notices.
- **License File**: `third_party/licenses/LICENSE.unitime.txt`.
- **Test Suite**: `tests/unit/scheduling/education/test_education_scheduler.py` covering DTOs, compiler, evaluator, UniTime adapter, and live benchmark assertions.
