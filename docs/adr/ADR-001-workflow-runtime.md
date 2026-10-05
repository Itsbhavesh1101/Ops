# ADR-001: Workflow Runtime — Flowable Primary, Operaton Fallback

## Status
Accepted

## Date
2026-09-16

## Context
The IR Infotech E-7 Operations Engine requires a robust, standards-compliant workflow execution engine capable of executing BPMN 2.0 processes, human task lifecycle management, multi-stage approvals (sequential, parallel, quorum), boundary timers, DMN decision evaluation, and persistent execution state.

Earlier candidate discussions explored multiple alternatives, including Temporal, Operaton (the open-source Camunda 7 fork), and proprietary platforms such as Camunda 8. As documented in the *Open-Source Research Report* (Section 2 & 14) and *Complete Implementation Plan* (Section 4 & 8), Camunda 8 was rejected due to restrictive commercial licensing, and dual-runtime implementations add significant operational and architectural complexity. A single primary open-source workflow runtime is required for the MVP, isolated behind an IR-owned domain adapter.

## Decision
1. **Flowable Engine (Apache-2.0)** is selected as the primary workflow and decision execution runtime for the E-7 Operations Engine.
2. **Operaton (Apache-2.0)** is designated as a conditional fallback only. Dual-runtime support will **NOT** be implemented in the MVP.
3. All communication between the E-7 domain services and the Flowable runtime must pass through an IR-owned `workflow-adapter`. Flowable internal identifiers, database schemas, and Java-specific types must **never** leak into public E-7 API contracts or domain entities.
4. **Temporal (MIT)** is **NOT** part of this workflow runtime decision; it remains a conditional option evaluated solely for code-first, long-running orchestrations if Flowable service tasks prove insufficient.

## Decision Details
* **Deployment Pattern**: Flowable runs as a decoupled standalone service container (`flowable/flowable-rest:7.0.0`) backed by PostgreSQL.
* **Protocol Boundary**: E-7 interacts with Flowable exclusively via its REST API through asynchronous HTTP calls within `app/workflow/adapters/flowable_adapter.py`.
* **Process Authoring**: Workflows are authored as standard BPMN 2.0 XML using `bpmn-js` and deployed to Flowable via the adapter.
* **Single Runtime Mandate**: No code or configuration will be written to support simultaneous runtime execution across multiple workflow engines in Phase 3.

## Authority Boundary
* **Flowable Runtime Owns**:
  - Deterministic execution of BPMN 2.0 process graphs.
  - Native token state, sequence flows, boundary timer countdowns, and gateway evaluations.
  - Native human task lifecycle (created, assigned, completed).
  - Low-level execution retry mechanisms and dead-letter jobs.
* **IR / E-7 Operations Engine Owns**:
  - Canonical `workflow_definitions`, `workflow_instances`, and `workflow_tasks` entities.
  - Canonical public IDs and UUIDs (mapping Flowable IDs to E-7 IDs).
  - Multi-tenant perimeter resolution and tenant isolation policies.
  - Authorization, RBAC, and approval quorum policy calculations.
  - Public REST API contracts and OpenAPI specifications.
  - Correlation IDs, CloudEvents publication, and tamper-evident operational audit logs.

## Configuration / No-Hardcoding Implications
* Flowable endpoints, credentials, connection pool sizes, and request timeouts must be injected via environment variables (`FLOWABLE_BASE_URL`, `FLOWABLE_USERNAME`, `FLOWABLE_PASSWORD`, `FLOWABLE_TIMEOUT`) validated by Pydantic Settings.
* Process definition keys, tenant identifiers, and task definitions must be dynamically resolved from tenant configuration or database entities, never hardcoded.

## Consequences

### Positive
* **Permissive Licensing**: Flowable is licensed under Apache-2.0, presenting zero reciprocal copyleft risk for enterprise commercial deployment.
* **Standards Compliance**: Native BPMN 2.0, CMMN 1.1, and DMN 1.3 execution with extensive tooling compatibility.
* **Clean Decoupling**: The IR workflow adapter shields the core application from upstream runtime changes.
* **Simplicity**: Avoiding a dual-runtime setup eliminates synchronization overhead, schema divergences, and operator confusion.

### Negative / Trade-offs
* Requires maintaining translation logic between Flowable REST JSON schemas and canonical E-7 domain models.
* Adds a network hop between the Python FastAPI service and the Java Flowable REST engine container.

## Rejected or Deferred Alternatives
* **Camunda 8 Core**: Rejected due to the proprietary Camunda License restricting commercial SaaS usage.
* **Operaton as Co-Primary**: Deferred to fallback status. Operaton is a viable community fork of Camunda 7, but supporting dual runtimes in Phase 3 violates the minimum viable stack principle.
* **Temporal as General Workflow Runtime**: Deferred. Temporal is optimized for code-as-workflows rather than visual BPMN/DMN human approval orchestration.
* **n8n**: Rejected due to the Sustainable Use License restricting commercial operations.

## Conditions for Reconsideration
Reconsideration or migration to Operaton will only be evaluated if:
1. Flowable introduces upstream licensing changes incompatible with Apache-2.0.
2. A critical unresolvable defect or performance bottleneck is formally benchmarked in Flowable's REST API under expected tenant concurrency.
3. An Architecture Decision Record (ADR) is formally drafted and accepted detailing migration plans.

## Implementation Phase
* **Phase 3: Workflow MVP** (First implementation of Flowable adapter and runtime integration).
* **Phase 6: Unified Vertical Slice** (End-to-end integration with Task Intelligence and Scheduling).
* **Phase 8: Hardening** (Failover, crash-recovery, and tenant load testing).

## Validation / Evidence Required
* `tests/integration/workflow/test_flowable_adapter.py`: Successful deployment and execution of BPMN 2.0 XML via Flowable REST.
* `tests/contract/test_workflow_contract.py`: Proof that zero Flowable-specific IDs leak into public API responses.
* `tests/integration/workflow/test_crash_recovery.py`: Worker crash simulation confirming Flowable recovers running instances from PostgreSQL state without duplicate side effects.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///F:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) (Requirements `FR-WF-01`, `FR-WF-02`, `FR-WF-06`)
* *Complete Specification*, Section 13.2 & Section 15.
* *Open-Source Research Report*, Section 2.1 & Section 14.
