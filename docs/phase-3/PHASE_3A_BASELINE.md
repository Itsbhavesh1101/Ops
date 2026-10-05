# Phase 3A Baseline & Verification Record

## 1. Scope & Implementation Summary

Phase 3A delivered the deterministic workflow runtime core, provider-neutral Flowable adapter, canonical E-7 business mappings, actor eligibility boundary, Phase-1 idempotency integration, and container restart durability.

- **Pinned Flowable Container**: `flowable/flowable-rest:7.0.0` (ADR-001 approved).
- **Pinned Flowable Container**: `flowable/flowable-rest:7.0.0` (ADR-001 approved).
- **Open-Source Compliance**: Registered in `docs/OPEN_SOURCE_COMPLIANCE_REGISTER.md` (line 59) under Apache-2.0 container boundary.
- **Alembic Revision**: Revision `0009` (down_revision: `0008`), head = `0009` (no `0010`).
- **Canonical Table Inventory**:
  - Pre-Phase-3A Frozen Baseline (11 application tables):
    `audit_events`, `goals`, `idempotency_records`, `inbox_messages`, `jobs`, `milestones`, `outbox_events`, `plans`, `task_dependencies`, `task_risks`, `tasks`
  - Phase-3A Added Tables (3 canonical tables):
    `workflow_definitions`, `workflow_instances`, `workflow_task_mappings`
  - Total `Base.metadata.tables`: Exactly 14 application tables.
- **Architectural Authority Invariant**:
  - `Base.metadata.tables` contains exactly 14 canonical tables.
  - Zero `ACT_*` or Liquibase tables are managed by Alembic.
  - Flowable manages native runtime state in `flowable_db`.

## 2. Test Verification Matrix

### Unit Test Suite (75 / 75 Passed)
- `tests/unit/workflow/test_bpmn_validator.py` (11 tests): XML security, XXE rejection, scriptTask prohibition, expression rejection.
- `tests/unit/workflow/test_flowable_adapter.py` (9 tests): Protocol implementation, deployment, execution, error translation.
- `tests/unit/workflow/test_flowable_config.py` (12 tests): Explicit configuration, environment parsing, fail-fast behavior, Basic & Bearer auth modes, secret masking.
- `tests/unit/workflow/test_workflow_api.py` (9 tests): API routes, 403 authorization, 409 conflict, 422 validation, deferred mount marker.
- `tests/unit/workflow/test_workflow_authorization.py` (11 tests): Assignee matching, candidate role checks, tenant boundary, fail-closed enforcement, action-specific checks.
- `tests/unit/workflow/test_workflow_idempotency.py` (7 tests): All Cases A through G for Phase-1 idempotency contracts.
- `tests/unit/workflow/test_workflow_service.py` (9 tests): Complete service orchestration, audit sync, transactional outbox, fail-closed default variable policy.
- `tests/unit/workflow/test_workflow_variables.py` (7 tests): Allowlist enforcement, credential scrubbing, size bounds.

### Full Repository Quality Gates
- `python -m compileall app`: Exit code 0
- `python -m ruff check .`: All checks passed (Exit code 0)
- `python -m mypy app`: Success: no issues found in 165 source files (Exit code 0)
- `python -m pytest --collect-only -q`: 1368 tests collected (Exit code 0)
- `python -m pytest -q`: 1324 passed, 44 skipped, 0 failures (Exit code 0)

### Phase-1 Idempotency Regression (53 / 53 Passed)
- `tests/unit/test_idempotency_repository.py` (18 tests)
- `tests/unit/test_idempotency_executor.py` (35 tests)

### Live Integration Test Suite (2 / 2 Passed)
- `tests/integration/workflow/test_flowable_live.py::test_full_workflow_lifecycle_live_flowable`
  - End-to-end 2-step sequential approval (`workflows/bpmn/two_step_approval.bpmn20.xml`).
  - Pre-engine unauthorized actor rejection.
  - Authorized chair approval.
  - Idempotent duplicate replay without second engine call.
  - Idempotent conflict rejection with modified variables.
  - Automatic advance to Task 2 (Dean Review).
  - Authorized dean approval.
  - Normalized history projection.
- `tests/integration/workflow/test_flowable_live.py::test_flowable_container_restart_durability`
  - Real container restart (`docker restart e7-flowable`).
  - Reconstructed adapter and service query.
  - Survives restart intact, completes Task 1, replays safely, and completes process instance.

## 3. Boundary Affirmation

Phase 3B was NOT started.
No visual designer (bpmn-js), DMN editor, form-js UI, or full SLA policy engine was introduced.
Canonical Phase 2 remains `IN_PROGRESS_PENDING_PROVIDER_BENCHMARK`.
