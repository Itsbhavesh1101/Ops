# Source of Truth & Governance Framework

This document establishes the authoritative source-of-truth governance, document hierarchy, precedence rules, and architectural invariants for the **IR Infotech E-7 Operations Engine**.

---

## 1. Document Inventory

The authoritative project documentation resides in the [`docs/source-of-truth/`](file:///F:/operations-engine/docs/source-of-truth/) directory. These files are **immutable project references** and must not be altered, overwritten, or deleted.

| File Name | Document Title & Version | Size & Pages | Role & Authority |
| :--- | :--- | :--- | :--- |
| [`IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`](file:///F:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf) | Complete Product, Research, Architecture & Implementation Specification (v1.0, Sept 2026) | 863 KB<br>26 pages | **Authoritative for Product & Functional Scope.** Defines functional requirements, domain entities, state models, public APIs, domain events, security boundaries, and acceptance criteria. |
| [`IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`](file:///F:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf) | Complete Implementation Architecture, Open-Source Integration & Step-by-Step Delivery Plan (v2.0, Sept 2026) | 970 KB<br>31 pages | **Authoritative for Delivery & Architecture Sequence.** Reconciles source documents, establishes implementation roadmap, component boundaries, service contracts, and verification plans. |
| [`IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`](file:///F:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf) | Open-Source Project Research, Selection & Adoption Report (Sept 2026) | 896 KB<br>19 pages | **Authoritative for Technology Selection & Licensing.** Governs the 9-dimension OSS evaluation, license compliance screen, direct dependencies, adapter isolation, and rejected alternatives. |
| [`opes eng.pdf`](file:///F:/operations-engine/docs/source-of-truth/opes%20eng.pdf) | E-7 Operations Engine: Comprehensive Implementation Plan (Foundational Working Draft) | 89 KB<br>4 pages | **Foundational Reference.** Contains initial scoping and candidate technologies, superseded where later reconciled by the Complete Specification, Plan, and OSS Report. |

---

## 2. Precedence Rules

To resolve any potential ambiguities or variations between documents, the following strict precedence rules apply:

1. **Functional Requirements & Acceptance Criteria**: Preserved from the IR source documents and the latest *Complete Specification* (`IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`). The OSS Research Report cannot remove or dilute functional requirements; it only selects how to implement them.
2. **Implementation Sequence & Architecture**: Defined and reconciled by the latest *Complete Implementation Plan* (`IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`).
3. **Open-Source Technology Selection**: Controlled by the latest *Open-Source Research Report* (`IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`) whenever an older candidate document mentions a different tool.
4. **No Reversion to Superseded Proposals**: An older candidate technology suggestion (e.g., from `opes eng.pdf` or early draft notes) must **NOT** override a later reconciled decision.
5. **No Requirement Dilution**: Never silently change, skip, or compromise a functional requirement because another technology would be easier to implement.
6. **Conflict Resolution Mandate**: If two documents genuinely appear contradictory and the precedence rules do not resolve the issue, developers and agents must **STOP and report the conflict** instead of choosing an arbitrary direction.

---

## 3. Architecture Invariants

All designs, implementations, and refactorings across all phases must strictly preserve these architectural invariants:

1. **API-First**: All engine capabilities are exposed via strongly typed, documented REST and event interfaces before any UI or external consumption.
2. **Platform-Agnostic & Intelligence-Layer Only**: E-7 is not a replacement ERP, LMS, HRMS, or calendar suite. It operates strictly as an orchestration and intelligence layer.
3. **Dual Access Modes**:
   - *Universal Mode*: Standalone operational interface for general planning, workflows, and scheduling.
   - *Embedded/Integrated Mode*: Seamless API-driven integration into host institutional and enterprise systems.
4. **Strict Multi-Tenancy**: Tenant context is resolved at the transport/authentication boundary and enforced across all database queries, event topics, cache keys, and worker jobs.
5. **Event-Driven**: Asynchronous decoupling and state propagation via versioned domain events utilizing the Transactional Outbox pattern.
6. **Immutable Auditability**: Every state-changing operational action, approval, schedule publication, and automated transition must generate an auditable, append-only event trail.
7. **Non-Authoritative AI Reasoning**: LLMs (Generative AI) are strictly confined to planning, WBS draft generation, and task intelligence. LLM outputs never directly mutate workflow states, trigger financial/governance approvals, run SLA clocks, bypass permissions, or act as the constraint solver.
8. **Deterministic Workflow & SLA State**: Business process state transitions, approvals, and SLA timer computations must execute through deterministic, verifiable rule and workflow engines.
9. **Deterministic Constraint-Based Scheduling**: Schedule generation and conflict resolution must be calculated by exact mathematical constraint solvers (Google OR-Tools CP-SAT), never by probabilistic language models.
10. **PostgreSQL as Canonical System of Record**: PostgreSQL is the single source of truth for canonical application state, outbox tables, and audit logs.
11. **Adapter Boundary Isolation**: All external open-source runtimes (Flowable, UniTime, etc.) and third-party integrations must reside behind IR-owned domain interfaces and adapters. Foreign engine IDs or SDK types must never leak into the domain core or public API contracts.

---

## 4. Current Reconciled Open-Source Decisions

| Functional Domain | Reconciled OSS Technology | Status | Role & Integration Boundary |
| :--- | :--- | :--- | :--- |
| **API & Core Orchestration** | **FastAPI + Pydantic** | Primary | IR-owned core service handling REST APIs, auth, multi-tenant resolution, idempotency, and dispatch. |
| **Task Intelligence Planner** | **PydanticAI** | Primary (MVP) | Structured prompt execution, Pydantic `PlanSchema` generation, and repair loops. Model provider remains pluggable and replaceable. |
| **Dependency & Critical Path** | **NetworkX + IR CPM Logic** | Primary | NetworkX handles DAG validation, cycle detection, and topological sorting; IR implements business Critical Path Method (CPM) and slack policies. |
| **Workflow Engine** | **Flowable Engine** | Primary | BPMN 2.0 execution, human tasks, multi-stage approvals, and timer lifecycles accessed via REST adapter. |
| **Process Authoring UI** | **bpmn-js / dmn-js / form-js** | Primary UI | Visual modelers embedded in frontend. Must retain mandatory `bpmn.io` visible watermark in canvas. |
| **Business Rules** | **Flowable DMN** | Primary | Deterministic decision tables. Avoids unnecessary external rule engine dependencies for MVP. |
| **Generic Scheduling** | **Google OR-Tools (CP-SAT)** | Primary Solver | Deterministic constraint optimization for multi-resource allocation, time slots, and minimal-churn disruption repair. |
| **Academic Timetabling** | **UniTime** | Primary PoC | Specialized institutional course and exam timetabling adapter evaluated against native OR-Tools models. |
| **System of Record** | **PostgreSQL 16** | Primary Store | ACID relational persistence, row-level tenant filtering, JSONB attributes, and transactional outbox. |
| **Message Broker** | **RabbitMQ** | Primary Broker | AMQP message broker for domain event distribution and background task queues. |
| **Observability** | **OpenTelemetry** | Standard | Vendor-neutral distributed tracing and metrics instrumentation across services and workers. |

### Conditional Components (Gated by Architecture Decision Records)
The following components were analyzed during research but **MUST NOT** become initial dependencies until their specific operational need is proven and formally approved via an ADR:
* **Temporal**: Conditional for code-first durable execution if long-running steps exceed Flowable service-task capabilities.
* **LangGraph**: Conditional for complex, persistent multi-agent planning graphs if single-agent PydanticAI proves insufficient.
* **Timefold Solver**: Conditional if Java-based domain constraint modeling provides measurable advantages over OR-Tools for specific rostering domains.
* **Valkey**: Optional cache/coordination layer; PostgreSQL advisory locks and database-backed state are prioritized first.
* **Apache Kafka**: Enterprise streaming alternative to RabbitMQ, deferred until platform scale mandates event streaming.

### Benchmark & Fallback Tools
* **FET 7.10.5**: Educational timetabling benchmark and fallback. AGPL-3.0 licensed; must remain strictly behind an isolated network boundary if deployed.
* **Operaton**: Workflow engine fallback if migration away from Flowable is required.

---

## 5. Immutability of Source Documents

The PDF documents located in [`docs/source-of-truth/`](file:///F:/operations-engine/docs/source-of-truth/) represent the agreed baseline for the IR Infotech E-7 Operations Engine. 
* Any changes to project scope, architectural contracts, or licensing policies must be recorded through formal Architecture Decision Records (ADRs) under [`docs/adr/`](file:///F:/operations-engine/docs/adr/) referencing the source documents.
* Developers and AI agents must never modify, delete, or replace the original PDF files.

---

## 6. Unresolved Conflicts

At the present stage (Phase 0), **no unresolved contradictions exist** across the source documentation. 
* Variations between early exploratory notes (e.g., in `opes eng.pdf`) and the master architecture have been systematically resolved and documented in the *Complete Implementation Plan* (Section 1) and *Open-Source Research Report* (Section 1 and 14).
* The established Precedence Rules provide a deterministic resolution path for all technical selections.
