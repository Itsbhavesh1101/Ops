# Phase 2A Baseline Summary & Freezing Report

**Status**: FROZEN / COMPLETED  
**Baseline Date**: October 2026  
**Alembic Head**: `0008_create_planning_domain`  
**Phase Invariant**: Phase 2A delivers the complete deterministic task intelligence core, NetworkX DAG/CPM engine, PostgreSQL planning persistence, and human approval baseline. All model/LLM integration is strictly deferred to Phase 2B.

---

## 1. Architectural Scope & Accomplishments

Phase 2A delivers the foundational core that guarantees operational planning integrity before any probabilistic generative model is introduced:

1. **Domain Contract & NetworkX DAG / CPM Engine (Stage A)**:
   - Added `networkx>=3.0` to project dependencies (`networkx==3.4.2`).
   - Domain schemas: `GoalCreate`, `GoalResponse`, `PlanSchema`, `TaskDraft`, `DependencyDraft`, `MilestoneDraft`, `PlanState`.
   - Graph engine: `TaskDependencyGraph` with cycle detection, explicit cycle path return, stable lexicographical topological sort.
   - Schedule calculator: `CriticalPathCalculator` (ES, EF, LS, LF, total slack, critical path).
2. **Deterministic Validation & Priority / Risk Intelligence (Stage B)**:
   - `PlanValidator` pipeline validating task structure, parent-child hierarchies, dependency existence, acyclicity, milestone integrity, and duration.
   - `PriorityEngine`: multi-criteria scoring with required caller-supplied policy weights (`PriorityWeights` / `PriorityPolicy`) summing to 1.0; zero hardcoded production default weights; human priority override implemented as an explicit control/bypass path.
   - `DeterministicRiskDetector`: detects critical path slippage, dependency delay bottlenecks, and unsupported capacity signals.
   - `TaskProgressStatus` & `TaskProgressUpdate`: operational progress state machine (`TODO`, `IN_PROGRESS`, `BLOCKED`, `COMPLETED`).
   - `HeuristicEffortEstimator`: rule-based estimation with historical median protocol hook.
3. **PostgreSQL Planning Domain (Stage C)**:
   - Migration `0008_create_planning_domain.py` with `down_revision = "0007"`.
   - 6 new tables: `goals`, `plans`, `milestones`, `tasks`, `task_dependencies`, `task_risks`. Total canonical tables: exactly 11.
   - Native versioning (`UNIQUE (tenant_id, goal_id, version_number)`).
   - Bidirectional migration verification: empty DB -> 0008, idempotent reapplication, downgrade 0008 -> 0007, re-upgrade 0007 -> 0008, and semantic schema check.
4. **Repositories, Unit of Work & PlanService (Stage D)**:
   - `GoalRepository`, `PlanRepository`, `TaskRepository` adhering to Phase 1 UoW contracts.
   - `PlanService` managing goal creation, plan persistence, validation, approval submission, baseline freezing (with SHA256 baseline hash), CPM computation, and task progress.
   - Plan lifecycle state machine strictly enforces `DRAFT -> GENERATED -> VALIDATED -> APPROVAL_PENDING -> APPROVED`. Prohibits `DRAFT -> VALIDATED` skips. Approval ends at `APPROVED` (no auto-activation).
   - Atomic transaction guarantees: plan state mutations, append-only audit trail (`audit_events`), and transactional outbox events (`plan.generated`, `plan.approved`) commit atomically.
5. **Contract Router & Production Mount Deferral (Stage E)**:
   - Router factory `create_planning_router` with injected tenant dependency, plan service, and planner.
   - Unmounted API contract: `TASK_INTELLIGENCE_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY`.
   - Zero modifications to production `app.main` or `app/api/v1/router.py`.
   - Contract test suite: 7 tests validating all endpoints using test client with mock tenant dependency.
6. **Live End-to-End Integration Verification (Stage F)**:
   - Live PostgreSQL integration test suite:
     - `test_representative_end_to_end_deterministic_flow`
     - `test_cycle_negative_flow`
     - `test_cross_tenant_negative_isolation`
     - `test_plan_generation_atomicity_commit`
     - `test_plan_generation_atomicity_rollback_on_failure`
     - `test_plan_approval_freezes_baseline_and_emits_event`
   - All 6 tests pass cleanly against Docker PostgreSQL container.

---

## 2. Invariants & Guardrails

- **Zero Tenant Tables**: Phase 2A introduces zero tenant-management tables or tenant persistence. All multi-tenancy is enforced via row-level `tenant_id` columns and injected dependencies.
- **Non-Authoritative Generative Output**: No plan can bypass deterministic validation or human review.
- **Unmounted Production API**: The planning router is fully covered by contract tests, but remains unmounted from production routes until trusted HTTP tenant identification is wired in Phase 3.
- **Canonical Table Baseline**: Exactly 11 application tables (`audit_events`, `outbox_events`, `inbox_messages`, `idempotency_records`, `jobs`, `goals`, `plans`, `milestones`, `tasks`, `task_dependencies`, `task_risks`).
- **Alembic Single Head**: Alembic head is strictly `0008`. No migration `0009` exists. Historical chain 0001 -> ... -> 0007 remains intact.
- **Strict Plan Lifecycle State Machine**: Enforces `DRAFT -> GENERATED -> VALIDATED -> APPROVAL_PENDING -> APPROVED -> ACTIVE -> COMPLETED`. `DRAFT -> VALIDATED` is prohibited. Approval stops at `APPROVED`.
- **Policy-Driven Priority Scoring**: Zero hardcoded business weights in production. Caller-provided `PriorityWeights` normalized to 1.0. Human override is an explicit control path.
- **Phase-Aware Historical Invariants**: Reconciled historical Phase 1 test assertions so they assert Phase 1 invariants (tables present, chain intact) without falsely forbidding Phase 2A evolution.

---

## 3. Phase 2B Scope Demarcation

Phase 2B is **STRICTLY NOT STARTED**. The following components belong exclusively to Phase 2B:
- Generative AI / LLM provider abstraction and adapters (e.g. Gemini, OpenAI, Anthropic, Sarvam).
- PydanticAI planner agents.
- Privacy-safe Context Builder and context injection.
- Context redaction, masking, and sanitization for external LLM calls.
- Historical data retrieval from MySQL / legacy operational sources.
