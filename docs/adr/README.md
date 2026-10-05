# Architecture Decision Records (ADRs)

This directory contains the formal, authoritative Architecture Decision Records (ADRs) governing the **IR Infotech E-7 Operations Engine**.

---

## Architecture Decision Governance

All technical designs, component selections, service boundaries, and implementation patterns across all phases must strictly adhere to the accepted ADRs in this directory.

### Mandatory Rules for Architectural Evolution
1. **No Silent Reversals**: Developers and AI coding agents must **never** silently edit or modify an older Accepted ADR to reverse or alter an established decision.
2. **Superseding Procedure**: Any architectural change that contradicts or supersedes an Accepted ADR requires:
   - **Documented Evidence**: Empirical benchmark data, formal gap analysis, or external business requirement justification.
   - **A New Superseding ADR**: A new sequentially numbered ADR explicitly declaring `Supersedes: ADR-NNN` and documenting the justification.
   - **Explicit Review & Approval**: Formal acceptance before code implementation begins.
   - **Updated Traceability**: Alignment updates to [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///F:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) and [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md).

---

## ADR Master Index

| ADR Identifier | Title | Status | Decision Summary | Target Phase | Reconsideration Trigger |
| :---: | :--- | :---: | :--- | :---: | :--- |
| [`ADR-000`](file:///F:/operations-engine/docs/adr/ADR-000-source-of-truth-and-reconciliation.md) | Source of Truth, Precedence Rules, and Architecture Invariants | **Accepted** | Establishes document hierarchy (Spec = Level 1, Plan = Level 2, OSS Report = Level 3) and 11 non-negotiable architectural invariants. | Phase 0 | Master source document update by project stakeholders. |
| [`ADR-001`](file:///F:/operations-engine/docs/adr/ADR-001-workflow-runtime.md) | Workflow Runtime — Flowable Primary, Operaton Fallback | **Accepted** | Flowable is primary BPMN/DMN runtime behind IR adapter. Operaton is conditional fallback. No dual-runtime in MVP. Temporal remains conditional. | Phase 3 | Upstream Flowable license change or unresolvable benchmarked REST bottleneck. |
| [`ADR-002`](file:///F:/operations-engine/docs/adr/ADR-002-task-intelligence-orchestration.md) | Task Intelligence Orchestration — PydanticAI MVP, LangGraph Conditional | **Accepted** | PydanticAI selected for structured `PlanSchema` generation with bounded repair. LLM output untrusted until validated. LangGraph is conditional. | Phase 2 | Demonstrated failure of single-agent decomposition for complex multi-tier plans. |
| [`ADR-003`](file:///F:/operations-engine/docs/adr/ADR-003-scheduling-solver-strategy.md) | Scheduling Strategy — OR-Tools Primary with Education Solver PoC | **Accepted** | Google OR-Tools CP-SAT is primary general solver (primary, not exclusive). Phase-5 comparative PoC evaluates UniTime vs native OR-Tools vs FET. Timefold conditional. | Phase 4 / 5 | Phase-5 empirical benchmark data evaluating UniTime against native OR-Tools model. |
| [`ADR-004`](file:///F:/operations-engine/docs/adr/ADR-004-business-rules.md) | Business Rules — Flowable DMN First | **Accepted** | Flowable DMN selected as first deterministic rule engine with `dmn-js` visual authoring. Drools and Easy Rules deferred. | Phase 3 | Requirements for complex graph-based pattern matching exceeding DMN table capacity. |
| [`ADR-005`](file:///F:/operations-engine/docs/adr/ADR-005-event-broker.md) | Event Broker — RabbitMQ Initial, Kafka Conditional | **Accepted** | RabbitMQ is primary initial broker. Correctness guaranteed via PostgreSQL Transactional Outbox + Idempotent Consumer pattern. Kafka is conditional. | Phase 1 | IR platform Kafka mandate or published event volume exceeding RabbitMQ cluster limits. |
| [`ADR-006`](file:///F:/operations-engine/docs/adr/ADR-006-system-of-record.md) | Canonical System of Record — PostgreSQL | **Accepted** | PostgreSQL 16 is the single canonical system of record for all domain state, outbox events, and audit logs. External engine DBs are not public stores. | Phase 1 | Irrevocable non-negotiable core; sharding/read-replicas evaluated in Phase 8 if required. |
| [`ADR-007`](file:///F:/operations-engine/docs/adr/ADR-007-cache-and-coordination.md) | Cache and Coordination — Database First, Valkey Optional | **Accepted** | No distributed cache in initial stack. Concurrency and coordination use PostgreSQL transactions and advisory locks. Valkey is optional/conditional. | Phase 1 | Phase-8 benchmark showing PostgreSQL lock contention or heavy read degradation. |
| [`ADR-008`](file:///F:/operations-engine/docs/adr/ADR-008-llm-provider-strategy.md) | LLM Provider Strategy — Replaceable Provider with Sarvam-105B Benchmark | **Accepted** | IR-owned model abstraction layer. Sarvam-105B is a candidate/benchmark, not hardcoded. At least one fallback route. Zero vendor names in business logic. | Phase 2 | Model abstraction is permanent; production default locked after Phase-2 benchmark. |
| [`ADR-009`](file:///F:/operations-engine/docs/adr/ADR-009-workflow-authoring-toolkit.md) | Workflow Authoring — bpmn-js, dmn-js and form-js | **Accepted** | `bpmn-js`, `dmn-js`, and `form-js` embedded in UI for non-authoritative authoring. Must preserve visible `bpmn.io` logo watermark per license terms. | Phase 3 | License terms change or enterprise whitelabel requirement with commercial license. |

---

## Core Architecture Invariant Reminders

1. **AI ≠ Execution Authority**: LLMs propose, draft, and explain. Deterministic systems validate, execute, approve, enforce constraints, and advance SLA clocks.
2. **Third-Party Runtime ≠ Public E-7 Contract**: Vendor-specific IDs (Flowable process IDs, UniTime IDs, ERP primary keys) remain strictly internal adapter mappings.
3. **PoC ≠ Production Selection**: Decisions marked "PoC" (such as UniTime in ADR-003) represent an accepted decision to evaluate, not a premature commitment.
4. **Conditional ≠ Installed**: Conditional tools (Temporal, LangGraph, Timefold, Valkey, Kafka) must not be installed or imported until an accepted ADR authorizes them.
5. **Reference Architecture ≠ Runtime Dependency**: Systems like OpenProject, Plane, LibrePlan, Odoo, and ERPNext are architectural study references; zero code may be copied from them.
