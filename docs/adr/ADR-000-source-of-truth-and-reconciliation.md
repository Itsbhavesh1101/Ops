# ADR-000: Source of Truth, Precedence Rules, and Architecture Invariants

## Status
Accepted

## Context
The IR Infotech E-7 Operations Engine unifies three historically separate systems:
1. Task Intelligence Engine (Engine 2)
2. Workflow Engine (Engine 13)
3. Scheduling Engine (Engine 24)

Multiple research documents, architecture specifications, and implementation plans have been produced during the project lifecycle. In earlier scoping notes (such as `opes eng.pdf` / S07), exploratory technologies such as LangGraph, Temporal, Drools, and Apache Kafka were mentioned as candidates. Subsequent research and synthesis—culminating in the *Complete Specification* (S08), the *Open-Source Research Report* (S09), and the *Complete Implementation Plan*—conducted formal 9-dimension open-source evaluations, technical licensing screens, and architectural reconciliations.

Without formal document governance and precedence rules, development runs the risk of re-introducing superseded technologies, violating intellectual property boundaries, or silently modifying functional requirements.

## Decision
We formally adopt the governance framework defined in [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md) and establish the authoritative hierarchy for all engineering phases.

### 1. Document Precedence Hierarchy
* **Level 1 (Functional Truth)**: The *Complete Specification* (`IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`) and IR source documents define all functional scope, domain models, business behaviors, and acceptance criteria.
* **Level 2 (Implementation & Sequence Truth)**: The *Complete Implementation Plan* (`IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`) controls delivery phasing, service contracts, and architecture sequencing.
* **Level 3 (Technology Selection & Licensing Truth)**: The *Open-Source Research Report* (`IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`) controls tool selection, component boundaries, and dependency vetting. Later decisions override older candidate notes.

### 2. Architecture Invariants
Every component, module, and API in the E-7 Operations Engine must uphold the following non-negotiable invariants:
1. **API-First & Platform-Agnostic**: Core engine exposed via clean REST and event contracts.
2. **Intelligence Layer Only**: E-7 is an orchestration and optimization layer, not a replacement ERP or HRMS.
3. **Dual Access Modes**: Parity across Universal Mode (standalone web interface) and Embedded/Integrated Mode (headless third-party API integration).
4. **Strict Multi-Tenancy**: Tenant context resolved at the authentication perimeter; zero tenant data leakage.
5. **Event-Driven Architecture**: Transactional outbox pattern with asynchronous event dissemination.
6. **Immutable Audit Trail**: Append-only tamper-evident operational history.
7. **Strict Separation of AI Reasoning from Execution**: LLMs are restricted to non-authoritative planning, drafting WBS structures, and unstructured context synthesis. LLMs never mutate workflow state, trigger approvals, manage SLA timers, or perform constraint scheduling.
8. **Deterministic Workflow & SLA State**: Workflow progression and SLA timers are strictly deterministic.
9. **Deterministic Constraint-Based Scheduling**: Multi-resource scheduling is solved exclusively via exact constraint solvers (Google OR-Tools CP-SAT).
10. **PostgreSQL as Canonical System of Record**: ACID relational persistence is the single authoritative source of truth.
11. **Adapter Boundary Isolation**: All open-source engines and third-party services reside strictly behind IR-owned adapter interfaces.

### 3. Primary Reconciled Stack
* **Web/API**: FastAPI + Pydantic
* **Task Intelligence**: PydanticAI (pluggable model provider)
* **Graph & CPM**: NetworkX + IR Critical Path Method logic
* **Workflow Engine**: Flowable Engine (REST integration)
* **Workflow UI**: bpmn-js, dmn-js, form-js (bpmn.io license compliant)
* **Business Rules**: Flowable DMN
* **General Scheduling**: Google OR-Tools (CP-SAT)
* **Academic Timetabling**: UniTime (PoC adapter evaluated against native OR-Tools model)
* **System of Record**: PostgreSQL 16
* **Message Broker**: RabbitMQ
* **Observability**: OpenTelemetry

## Consequences

### Positive
* Eliminates ambiguity regarding tool selection and architectural boundaries.
* Prevents copyleft license contamination (GPL/AGPL) by establishing strict clean-room and network boundary rules.
* Ensures system reliability by prohibiting probabilistic models (LLMs) from making authoritative state mutations or schedule assignments.
* Keeps the MVP lightweight by gating heavy distributed technologies (Temporal, Kafka, Valkey) until proven necessary.

### Negative / Trade-offs
* Requires maintaining abstraction adapters around third-party runtimes (e.g., Flowable, UniTime) to prevent foreign identifier leakage.
* Prevents developers from taking "shortcuts" such as direct database access or embedding external copyleft code.

## Rejected Alternatives
* **Camunda 8 as embedded core**: Rejected due to restrictive proprietary Camunda License for commercial SaaS distribution.
* **n8n for core orchestration**: Rejected due to Sustainable Use License restricting commercial use.
* **LangGraph as initial MVP dependency**: Deferred; PydanticAI provides a simpler, typed single-agent planner for MVP without graph engine overhead.
* **Temporal as initial MVP dependency**: Deferred; Flowable service tasks satisfy initial workflow execution needs.
* **Apache Kafka as initial broker**: Deferred in favor of RabbitMQ, which has lower operational overhead for initial deployment slices.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/IMPLEMENTATION_GUARDRAILS.md`](file:///F:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)
* [`docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`](file:///F:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf)
* [`docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`](file:///F:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf)
* [`docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`](file:///F:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf)
