# E-7 Phase-0 SLO, Retention & Scale Evidence Audit

## 1. Purpose

This document establishes the official evidence audit for the remaining Phase-0 non-functional baseline of the E-7 Operations Engine:
1. **Service Level Objective (SLO) Candidates**
2. **Data Retention Requirements**
3. **First-Tenant Scale Envelope**

This task is **EVIDENCE COLLECTION AND CLASSIFICATION ONLY**. In accordance with Phase-0 governance:
- **No Final Numeric SLOs Established**: Contractual service-level agreements and objectives are not finalized here.
- **No Final Retention Periods Established**: Mandatory durations are not fabricated; customer/legal policy dependencies are preserved.
- **No Final Capacity Limits Established**: Production infrastructure limits are not predetermined without business workload inputs.

The goal of this audit is to catalog what accessible project source documents literally require, what metric families are named but unquantified, what quantitative thresholds exist as proposed engineering targets, what policies depend on business/platform/compliance confirmation, and what decision gaps must be resolved before candidate baselines can be proposed in Task 0H.2.

---

## 2. Source Corpus

The audit was conducted strictly against physically accessible project source documents and frozen governance artifacts located in the repository:

### Physically Available Compiled Sources
1. [`docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`](file:///f:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf) (26 pages)
2. [`docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`](file:///f:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf) (31 pages)
3. [`docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`](file:///f:/operations-engine/docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf) (19 pages)
4. [`docs/source-of-truth/opes eng.pdf`](file:///f:/operations-engine/docs/source-of-truth/opes%20eng.pdf) (4 pages)

### Supporting Governance Artifacts & Test Fixtures
- [`docs/SOURCE_OF_TRUTH.md`](file:///f:/operations-engine/docs/SOURCE_OF_TRUTH.md) (Source precedence rules)
- [`docs/IMPLEMENTATION_GUARDRAILS.md`](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md) (Security, tenancy, and operational invariants)
- [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///f:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) (Requirements mapping baseline)
- [`docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md`](file:///f:/operations-engine/docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md) (PostgreSQL SoR and domain boundaries)
- [`docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md`](file:///f:/operations-engine/docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md) (Task 0G.3 Task Intelligence data mapping)
- [`docs/integrations/AI_CONTEXT_ACCESS_POLICY.md`](file:///f:/operations-engine/docs/integrations/AI_CONTEXT_ACCESS_POLICY.md) (Task 0G.3 aggregate-only context access policy)
- [`docs/poc/REFERENCE_USE_CASES.md`](file:///f:/operations-engine/docs/poc/REFERENCE_USE_CASES.md) (Benchmark and scenario specifications)
- [`tests/fixtures/MANIFEST.json`](file:///f:/operations-engine/tests/fixtures/MANIFEST.json) (18 Phase-0 synthetic test datasets)

### Re-Audited ADR Inventory (`docs/adr/`)
Every Architecture Decision Record currently present in `docs/adr/` was systematically audited for evidence relevant to SLOs, latency, availability, timeouts, retries, solver budgets, retention, cache lifecycles, event lifecycles, recovery, scale, and concurrency:

| ADR File | Title | Audit Finding for Task 0H |
| :--- | :--- | :--- |
| [`ADR-000`](file:///f:/operations-engine/docs/adr/ADR-000-source-of-truth-and-reconciliation.md) | Source of Truth, Precedence Rules, and Architecture Invariants | **REVIEWED** — Architectural invariants confirmed (PostgreSQL SoR, outbox pattern, PoC != production selection). |
| [`ADR-001`](file:///f:/operations-engine/docs/adr/ADR-001-workflow-runtime.md) | Workflow Runtime — Flowable Primary, Operaton Fallback | **REVIEWED** — Flowable runtime persistence, DB schema isolation confirmed. |
| [`ADR-002`](file:///f:/operations-engine/docs/adr/ADR-002-task-intelligence-orchestration.md) | Task Intelligence Orchestration — PydanticAI MVP, LangGraph Conditional | **REVIEWED** — Bounded LLM retry, context minimization, and aggregate-only student context boundary confirmed. |
| [`ADR-003`](file:///f:/operations-engine/docs/adr/ADR-003-scheduling-solver-strategy.md) | Scheduling Strategy — OR-Tools Primary with Education Solver PoC | **REVIEWED** — Solver queue/worker isolation, benchmark 100% hard constraint satisfaction confirmed. |
| [`ADR-004`](file:///f:/operations-engine/docs/adr/ADR-004-business-rules.md) | Business Rules — Flowable DMN First | **REVIEWED** — Deterministic in-process / REST rule evaluation confirmed. |
| [`ADR-005`](file:///f:/operations-engine/docs/adr/ADR-005-event-broker.md) | Event Broker — RabbitMQ Initial, Kafka Conditional | **REVIEWED** — Transactional outbox durability, broker outage survival, consumer deduplication confirmed. |
| [`ADR-006`](file:///f:/operations-engine/docs/adr/ADR-006-system-of-record.md) | Canonical System of Record — PostgreSQL | **REVIEWED** — PostgreSQL 16 canonical SoR, transactional consistency, append-only audit logging confirmed. |
| [`ADR-007`](file:///f:/operations-engine/docs/adr/ADR-007-cache-and-coordination.md) | Cache and Coordination — Database First, Valkey Optional | **REVIEWED** — Concurrency and coordination via PostgreSQL advisory locks/transactions confirmed. |
| [`ADR-008`](file:///f:/operations-engine/docs/adr/ADR-008-llm-provider-strategy.md) | LLM Provider Strategy — Replaceable Provider with Sarvam-105B Benchmark | **REVIEWED** — Replaceable model abstraction layer, at least one fallback route confirmed. |
| [`ADR-009`](file:///f:/operations-engine/docs/adr/ADR-009-workflow-authoring-toolkit.md) | Workflow Authoring — bpmn-js, dmn-js and form-js | **REVIEWED — NO 0H-RELEVANT REQUIREMENT FOUND** (Governs visual canvas authoring toolkits and visible watermark licensing compliance). |
| [`README.md`](file:///f:/operations-engine/docs/adr/README.md) | Architecture Decision Records Master Index | **REVIEWED** — Master index and core architecture invariant reminders confirmed. |

> [!NOTE]
> **Source Corpus Limitation**: The audit is strictly bounded to the documents above. Original historical working files (such as raw product backlog spreadsheets or uncompiled draft notes) are not present in the local repository. Statements in this audit do not assert provider or requirement absence across uninspected historical repositories.

---

## 3. Evidence Classification Rules

Every finding in this audit is classified under one of seven rigorous governance labels:

1. **`SOURCE_REQUIREMENT`**: Mandatory functional, architectural, or isolation rule literally mandated by authoritative sources.
2. **`SOURCE_METRIC_WITHOUT_TARGET`**: Metric family explicitly identified in source documents for monitoring/telemetry, but without any associated numeric threshold.
3. **`SOURCE_NUMERIC_TARGET`**: Contractual quantitative threshold literally mandated by authoritative source documents.
4. **`APPROVED_ARCHITECTURE_CONSTRAINT`**: Architectural invariant, design boundary, or governance rule approved in Phase-0 ADRs or guardrails.
5. **`PHASE_0_ENGINEERING_PROPOSAL`**: Quantitative number or operational target proposed by engineering in technical specifications to validate during PoC/benchmarking rather than an original contractual source requirement.
6. **`REQUIRES_BUSINESS_CONFIRMATION`**: Operational parameter, scale profile, or policy requiring explicit business, customer, platform, or legal confirmation.
7. **`NOT_FOUND_IN_AVAILABLE_SOURCES`**: Concept, parameter, or objective completely unmentioned in accessible project source documents.

---

## 4. SLO Evidence Summary

A critical governance finding of this audit is that **the original functional source material does not define contractual numeric SLOs**. 

This fact is explicitly recorded in `Complete Specification`, Section 10 (p. 8):
> *"Proposed implementation detail: The source requires secure, scalable and extensible AI capabilities but does not provide numeric SLOs. The following are implementation targets to validate during PoC and adjust after load testing."*

Consequently:
- Observable telemetry metrics are broadly enumerated across specification sections 10, 14, 24 and implementation plan sections 15, 16.
- All discovered numeric targets represent **proposed engineering targets for PoC validation** (`PHASE_0_ENGINEERING_PROPOSAL`) or **configuration placeholders** (`CONFIG PLACEHOLDER`).
- Contractual production SLOs require formal confirmation in Task 0H.2 and subsequent business reviews.
- **`SOURCE_NUMERIC_TARGET` count remains exactly 0**.

---

## 5. API / Service Metrics

The API and service layer metric families named in source documents were audited:

| Metric Family | Classification | Source Basis | Location | Audit Findings & Observed Text |
| :--- | :---: | :---: | :---: | :--- |
| **Core API Availability** | `PHASE_0_ENGINEERING_PROPOSAL` | Complete Specification | Sec. 10 (p. 8) | Proposed target: *"99.9% monthly after production hardening"*. Introduced explicitly as an implementation target to validate during PoC, not an original contractual SLO. |
| **CRUD / API p95 Latency** | `PHASE_0_ENGINEERING_PROPOSAL` | Complete Specification | Sec. 10 (p. 8) | Proposed target: *"CRUD/API p95 < 500 ms excluding LLM/solver work; asynchronous jobs return job IDs quickly."* |
| **API p50 Latency** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named under Reliability metric family (*"API p50/p95/p99"*); no numeric target specified. |
| **API p99 Latency** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named under Reliability metric family (*"API p50/p95/p99"*); no numeric target specified. |
| **API Error Rate** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named under Reliability minimum telemetry (*"error rate"*); no numeric threshold specified. |
| **API Throughput** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 14 (p. 12) | Named as an operational KPI exposed via Analytics API (*"throughput"*); unquantified. |
| **Worker Concurrency** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Specification | Sec. 10 (p. 8) | Architectural rule: *"Stateless API workers scale horizontally; solver jobs isolated by queue/worker pool; tenant-aware quotas."* Numeric concurrency unquantified. |

---

## 6. Task Intelligence Metrics

Metric families governing the AI planning and Task Intelligence workstream:

| Metric Family | Classification | Source Basis | Location | Audit Findings & Observed Text |
| :--- | :---: | :---: | :---: | :--- |
| **Plan Generation Latency** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as planning metric (*"plan generation latency"*). Explicitly exempted from the CRUD < 500 ms target via asynchronous job dispatch. Target latency unquantified. |
| **Model Tokens & Cost** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Telemetry includes *"model cost/tokens"*. Low-cost accuracy-first mandate established (Plan Sec. 12), but cost ceilings remain unquantified. |
| **Structured Schema Validity Rate**| `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Telemetry includes *"validation failures"*; Plan Sec. 16 lists *"structured-validity rate"*. No minimum percentage threshold specified. |
| **Human Correction Rate** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as observable KPI tracking how frequently generated tasks require human editing (*"human correction rate"*); unquantified. |
| **Accepted-vs-Edited Task Ratio** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as observable KPI (*"accepted-vs-edited task ratio"*); target ratio unquantified. |
| **Inference Resilience** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Implementation Plan | Sec. 15 (p. 16) | Invariant: *"Bounded retry; configured fallback model; persist job failure, never malformed plan."* Bounded by `LLM_TIMEOUT` and `MAX_RETRIES`. |

---

## 7. Workflow Metrics

Metric families governing Flowable BPMN workflow execution and human task lifecycles:

| Metric Family | Classification | Source Basis | Location | Audit Findings & Observed Text |
| :--- | :---: | :---: | :---: | :--- |
| **Process Lifecycle Rates** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as *"instances started/completed/failed"*; no numeric throughput or completion rate targets specified. |
| **User Task Queue Age** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as observable operational metric (*"task queue age"*); maximum acceptable age unquantified. |
| **SLA Warning & Breach Rate** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as workflow telemetry (*"SLA warning/breach rate"*). Implementation Guardrails mandate that SLA durations must never be hardcoded magic values. |
| **Stuck Workflow Detection** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Implementation Plan | Sec. 16 (p. 16) | Named under workflow telemetry as *"stuck instances"*; detection timeout unquantified. |
| **Crash Recovery & Idempotency** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Implementation Plan | Sec. 15 (p. 16) | Invariant: *"Durable runtime resumes; external service actions are idempotent."* Flowable engine state persists across worker crashes. |

---

## 8. Scheduling Metrics

Metric families governing Google OR-Tools CP-SAT and timetabling optimization:

| Metric Family | Classification | Source Basis | Location | Audit Findings & Observed Text |
| :--- | :---: | :---: | :---: | :--- |
| **Solve Duration** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as observable metric (*"solve duration"*). Solvers run asynchronously behind worker pools; bounded by configurable `SOLVER_MAX_SECONDS`. |
| **Feasibility Rate** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as scheduling metric (*"feasibility rate"*). Target feasibility percentage unquantified. |
| **Constraint Violation Counts** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Implementation Plan | Sec. 16 (p. 16) | Minimum telemetry includes *"hard/soft violations"* and *"conflict count"*. Hard constraints must be 0 for feasible schedules. |
| **Schedule Stability & Movement**| `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as *"number of moved assignments in repair"*; objective is to minimize disruptions (*"schedule stability"*), but numeric movement limit is unquantified. |
| **Solver Timeout Handling** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Implementation Plan | Sec. 15 (p. 16) | Invariant: *"Return best feasible solution only if policy permits; mark optimality unknown; otherwise unresolved with diagnostics."* |

---

## 9. Events / Broker Metrics

Metric families governing RabbitMQ message transport and event-driven integration:

| Metric Family | Classification | Source Basis | Location | Audit Findings & Observed Text |
| :--- | :---: | :---: | :---: | :--- |
| **Outbox Backlog** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Implementation Plan | Sec. 16 (p. 16) | Minimum telemetry literally includes *"outbox backlog"*. Maximum allowed publication lag unquantified. |
| **Broker Consumer Lag** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named under Reliability literally as *"broker lag"*; threshold unquantified. |
| **Dead-Letter Count** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named under Reliability literally as *"dead-letter count"*; threshold unquantified. |
| **Broker Outage Resilience** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Implementation Plan | Sec. 15 (p. 16) | Invariant: *"Persist outbox; publish when broker recovers."* Guarantees zero message loss during broker downtime. |
| **Consumer Deduplication** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Specification | Sec. 21 (p. 16) | Invariant: *"Consumers should deduplicate by event ID and handle schema versions."* |

---

## 10. Database Metrics

Metric families governing PostgreSQL canonical data access:

| Metric Family | Classification | Source Basis | Location | Audit Findings & Observed Text |
| :--- | :---: | :---: | :---: | :--- |
| **Connection Pool Saturation** | `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Named as observable metric (*"DB pool saturation"*); threshold unquantified. |
| **Query Latency** | `PHASE_0_ENGINEERING_PROPOSAL` | Complete Specification | Sec. 10 (p. 8) | Subsumed under CRUD/API p95 < 500 ms target; individual SQL query latency thresholds are unquantified. |
| **State Persistence Invariant** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Specification | Sec. 10 (p. 8) | Invariant: *"PostgreSQL is system of record; state-changing APIs are transactional and idempotent. Persistence must survive process restarts."* |
| **Point-in-Time Recovery** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Specification | Sec. 10 (p. 8) | Invariant: Requires point-in-time database backups and crash-recovery procedures. Target recovery windows (RTO/RPO) unquantified. |

---

## 11. External Integration Metrics

Metric families governing external adapters (ERP, Calendar, E-8):

| Metric Family | Classification | Source Basis | Location | Audit Findings & Observed Text |
| :--- | :---: | :---: | :---: | :--- |
| **ERP Adapter Resilience** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Implementation Plan | Sec. 15 (p. 16) | Invariant: *"Retry/backoff + degraded integration state; no fake success."* Availability percentage unquantified. |
| **Calendar Sync Deduplication** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Implementation Plan | Sec. 15 (p. 16) | Invariant: *"Deduplicate by provider/version + idempotency key."* Synchronization latency unquantified. |
| **E-8 Command Dispatch** | `APPROVED_ARCHITECTURE_CONSTRAINT` | Complete Specification | Sec. 21 (p. 16) | Invariant: Outbox-backed notification command publication. Acceptance latency unquantified. |
| **Availability Freshness**| `SOURCE_METRIC_WITHOUT_TARGET` | Complete Specification | Sec. 24 (p. 17) | Literally named under Resources metric family (*"availability freshness"*); maximum stale data age unquantified. |

---

## 12. Existing Numeric Targets Audit

Every concrete numeric value discovered across the source corpus and Phase-0 artifacts was audited and classified:

| Value ID | Discovered Number / Target | Context | Classification | Governance Rationale |
| :--- | :--- | :--- | :---: | :--- |
| **NUM-01** | **`99.9% monthly uptime`** | Core API availability | **`PHASE_0_ENGINEERING_PROPOSAL`** | Explicitly stated in Spec Sec. 10 as an implementation target to validate during PoC rather than an original contractual SLO. |
| **NUM-02** | **`< 500 ms`** | CRUD / API p95 latency | **`PHASE_0_ENGINEERING_PROPOSAL`** | Proposed PoC target in Spec Sec. 10. Excludes LLM and solver workloads. |
| **NUM-03** | **`SOLVER_MAX_SECONDS`** | Solver execution time limit | **`CONFIG PLACEHOLDER`** | Plan Sec. 17.2 names this configuration variable for solver budgets; concrete numeric seconds remain unstated. |
| **NUM-04** | **`AUDIT_RETENTION_DAYS`** | Audit trail retention | **`CONFIG PLACEHOLDER`** | Plan Sec. 17.2 names this configuration variable as tenant policy default; concrete duration remains unstated. Does not establish an approved default duration. |
| **NUM-05** | **`LLM_TIMEOUT` / `MAX_RETRIES`**| AI inference resilience | **`CONFIG PLACEHOLDER`** | Plan Sec. 17.2 names configuration variables for bounded inference; concrete numeric thresholds remain unstated. |
| **NUM-06** | **`100% hard constraint satisfaction`**| Benchmark acceptance | **`DERIVED PHASE-0 TEST INTERPRETATION`**| ADR-003 and Reference Scenarios establish 100% hard constraint satisfaction on synthetic benchmark test suites. |
| **NUM-07** | **`14 resources, 70 availability slots`**| Test fixture scale | **`FIXTURE VALUE`** | Defined in `RS-SYNTH-001.json`. Strictly a test dataset value, NOT a production capacity requirement. |
| **NUM-08** | **`2 cohorts, 5 exams, 10 sessions`** | Test fixture problem size | **`FIXTURE VALUE`** | Defined in `SC-EXAM-FEASIBLE-001.json`. Strictly a test benchmark size, NOT a production scale target. |

**Audit Confirmation**: Total count of `SOURCE_NUMERIC_TARGET` remains **0**.

---

## 13. Retention Evidence Summary

Data retention governance was audited across specification, implementation plans, and architecture guardrails:

1. **Explicit Durations Unstated**: Accessible sources do **NOT** specify concrete numeric retention durations (such as 30 days, 90 days, 1 year, or 7 years).
2. **Tenant & Legal Policy Primacy**: Retention is explicitly defined as tenant-configurable and governed by customer contracts and privacy law:
   > *"Use per-tenant data retention and export/delete policies compatible with customer contracts and applicable privacy law."* (`Complete Specification`, Section 23, p. 17; `Complete Implementation Plan`, Section 16, p. 16).
3. **Immutability vs. Retention Duration**:
   > [!IMPORTANT]
   > **Strict Governance Principle**: The requirement that audit logs and event ledgers must be **append-only** at the application level means they are **immutable while retained** (append-only != retain forever). It does **NOT** mean they must be retained forever. Parameterized via `AUDIT_RETENTION_DAYS` as a configuration placeholder, but this does not establish an approved default duration. Retention windows must be governed by authorized customer, legal, and compliance policy (`AUDIT_RETENTION_DURATION = UNRESOLVED`, `AUDIT_RETENTION_POLICY = TENANT / CUSTOMER / LEGAL POLICY DRIVEN`).

---

## 14. Operational Data Retention

Governance requirements for core operational entities:

- **Entity Scope**: Goals, plans, plan versions, WBS tasks, workflow instances, schedules, and schedule assignments.
- **System of Record**: Stored in PostgreSQL ([`ADR-006`](file:///f:/operations-engine/docs/adr/ADR-006-system-of-record.md), [`docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md`](file:///f:/operations-engine/docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md)). Approved/canonical scheduling results and required scheduling evidence are persisted in E-7 PostgreSQL according to the canonical scheduling data model.
- **Retention Duration**: **`UNRESOLVED`**.
- **Retention Policy**: **`TENANT / CUSTOMER / LEGAL POLICY DRIVEN`** (**`REQUIRES_BUSINESS_CONFIRMATION`**).
- **Lifecycle Operations**: Support tenant-configurable archiving and deletion policies. Operational state must persist across process restarts.

---

## 15. Audit / Event Retention

Governance requirements for audit ledgers and event records:

- **Application Audit Trail (`audit` table)**:
  - Captures actor ID, model ID, rule version, timestamp, correlation ID, and state change diffs (`Spec Sec. 23`, `Plan Sec. 16`).
  - Must be append-only at the application level; updates and deletes are prohibited during active retention. Append-only ensures immutability while retained, but does not mean retain forever (`append-only != retain forever`).
  - Retention duration is unquantified in source documents; parameterized via `AUDIT_RETENTION_DAYS` (`Plan Sec. 17.2`) as a configuration placeholder, not an approved default duration.
  - Status: **`AUDIT_RETENTION_DURATION = UNRESOLVED`**, **`AUDIT_RETENTION_POLICY = TENANT / CUSTOMER / LEGAL POLICY DRIVEN`** (**`REQUIRES_BUSINESS_CONFIRMATION`**).
- **Transactional Outbox (`outbox` table)**:
  - PostgreSQL transactional outbox provides durable event publication ([`ADR-005`](file:///f:/operations-engine/docs/adr/ADR-005-event-broker.md), `Spec Sec. 21`).
  - Event publication state must survive broker outages (`Plan Sec. 15`). Published/unpublished semantics exist where frozen architecture defines them.
  - No retention duration, pruning policy, or deletion rule is established in available sources. Durability does not imply indefinite retention.
  - Status: **`OUTBOX_RETENTION_DURATION = UNRESOLVED`**, **`OUTBOX_PRUNING_POLICY = NOT_DEFINED_IN_AVAILABLE_SOURCES`**.
- **Consumer Inbox / Deduplication Records**:
  - Consumer deduplication and idempotency are required by architecture invariants (`Spec Sec. 21`, [`ADR-005`](file:///f:/operations-engine/docs/adr/ADR-005-event-broker.md), `Plan Sec. 15`).
  - Available sources do not establish a specific replay window duration or inbox retention lifecycle. Idempotency requirements do not themselves define a retention period.
  - Status: **`INBOX_DEDUP_RETENTION_DURATION = UNRESOLVED`**, **`REPLAY_WINDOW_DURATION = UNRESOLVED`** (**`NOT_DEFINED_IN_AVAILABLE_SOURCES`**).

---

## 16. AI / Ephemeral Data Retention

Governance requirements for ephemeral and generative planning artifacts:

- **AI Prompts & Model Outputs**:
  - AI-generated recommendations persist with rationale, confidence, and model version metadata (`Spec Sec. 23`).
  - LLM prompts receive minimum necessary context ([`ADR-002`](file:///f:/operations-engine/docs/adr/ADR-002-task-intelligence-orchestration.md), `Spec Sec. 23`). Persistent storage of full raw prompt transcripts is unstated in source documents.
  - Status: **`PRIVACY_AND_STORAGE_MINIMIZATION_DRIVEN`** (**`APPROVED_ARCHITECTURE_CONSTRAINT`**); prompt transcript retention duration is **`UNRESOLVED`**.
- **Solver-Internal Temporary & Search State**:
  - Solver-internal temporary/search artifacts are not established as canonical E-7 records by available sources.
  - Approved/canonical scheduling results and required scheduling evidence are persisted in E-7 PostgreSQL according to the canonical scheduling data model ([`ADR-003`](file:///f:/operations-engine/docs/adr/ADR-003-scheduling-solver-strategy.md), [`ADR-006`](file:///f:/operations-engine/docs/adr/ADR-006-system-of-record.md), [`docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md`](file:///f:/operations-engine/docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md)).
  - Canonical scheduling state belongs to E-7 PostgreSQL; solver-internal temporary/search state has no source-defined retention lifecycle. Available sources do not imply that internal solver search artifacts are persisted, nor that they are deleted automatically.
  - Status: **`SOLVER_TEMPORARY_RETENTION_DURATION = UNRESOLVED`**, **`SOLVER_TEMPORARY_DATA_LIFECYCLE = NOT_DEFINED_IN_AVAILABLE_SOURCES`**.

---

## 17. Logs / Metrics / Telemetry Retention

Governance requirements for observability data:

- **Structured Logs**:
  - Emitted as structured JSON containing correlation IDs and tenant-safe attributes (`Spec Sec. 24`, `Plan Sec. 16`).
  - Inclusion of credentials, secrets, or raw sensitive model context is strictly forbidden.
  - Centralized log retention duration is an infrastructure platform concern unstated in application sources; status is **`UNRESOLVED`** (**`NOT_FOUND_IN_AVAILABLE_SOURCES`**).
- **Metrics & Traces**:
  - Exported via OpenTelemetry (OTLP) to Prometheus / Grafana.
  - Metric retention duration is determined by infrastructure deployment policy; status is **`UNRESOLVED`** (**`NOT_FOUND_IN_AVAILABLE_SOURCES`**).

---

## 18. Personal / Sensitive Data Retention & Boundary

Privacy boundaries and sensitive data governance:

- **Student Data Context Boundary**:
  - Individual `students` / `student_session` source rows are prohibited from the Task Intelligence planner/model context under the frozen aggregate-only access policy ([`docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md`](file:///f:/operations-engine/docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md), [`docs/integrations/AI_CONTEXT_ACCESS_POLICY.md`](file:///f:/operations-engine/docs/integrations/AI_CONTEXT_ACCESS_POLICY.md), [`ADR-002`](file:///f:/operations-engine/docs/adr/ADR-002-task-intelligence-orchestration.md)).
  - Adapter-derived aggregate cohort values are permitted.
  - This evidence audit does not infer a system-wide retention rule beyond that approved boundary.
  - Status: **`AGGREGATE_ONLY_FOR_AI_CONTEXT`** (**`APPROVED_ARCHITECTURE_CONSTRAINT`**); System-wide retention duration is **`UNRESOLVED`**.
- **Staff Master Data**:
  - Staff descriptive metadata (qualifications, experience) is utilized as descriptive context only. Default exposure is opaque references.
  - Staff records in E-7 are governed by institutional HR agreements; retention status is **`TENANT / CUSTOMER / LEGAL POLICY DRIVEN`** (**`REQUIRES_BUSINESS_CONFIRMATION`**).
- **Jurisdiction Independence**: No jurisdiction-specific legal mandates (e.g., GDPR, FERPA, COPPA) are fabricated. Policies remain tenant- and customer-contract driven.

---

## 19. First-Tenant Scale Evidence Summary

A primary objective of Task 0H.1 is auditing assumptions regarding the scale of the **first production tenant**.

### Audit Verdict: **`FIRST_TENANT_SCALE_PROFILE = REQUIRES_BUSINESS_CONFIRMATION`**

**Key Findings**:
1. Accessible source documents define architectural mechanisms for scaling (horizontal stateless workers, solver queue isolation, tenant-aware quotas), but **do not specify concrete numerical production scale targets** for the initial tenant.
2. The reference semester examination scenario (`E7-REF-EXAM-001`) defines representative vertical use-case semantics and fixed test fixtures, but **must never be conflated with production capacity requirements**.
3. Upstream MySQL schema inventory metrics (247 tables, 2,637 columns, 307 foreign keys) describe **schema complexity only**, and do not indicate production row volume or tenant scale.

---

## 20. Identity / User Scale

- **Source Evidence**: Specifies multi-tenant isolation, tenant-derived query filters, and authenticated principal contexts (`Spec Sec. 23`).
- **Production Values**:
  - Total registered users: **`NO_PRODUCTION_VALUE_FOUND`**
  - Daily active users (DAU): **`NO_PRODUCTION_VALUE_FOUND`**
  - Peak concurrent authenticated sessions: **`NO_PRODUCTION_VALUE_FOUND`**
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 21. Task Intelligence Scale

- **Source Evidence**: Defines hierarchical WBS decomposition, goal intake, schema validation, and CPM graph analysis.
- **Production Values**:
  - Daily goal creation rate: **`NO_PRODUCTION_VALUE_FOUND`**
  - Expected tasks per plan: **`NO_PRODUCTION_VALUE_FOUND`**
  - Maximum dependency graph depth: **`NO_PRODUCTION_VALUE_FOUND`**
- **Test Baseline**: Synthetic gold set fixtures exercise ~10–30 tasks across 9 work categories (`PHASE_0 TEST FIXTURE SCALE`).
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 22. Workflow Scale

- **Source Evidence**: Specifies Flowable BPMN 2.0 / DMN 1.3 execution with durable timers and user task queues.
- **Production Values**:
  - Daily workflow instances initiated: **`NO_PRODUCTION_VALUE_FOUND`**
  - Concurrent active workflow instances: **`NO_PRODUCTION_VALUE_FOUND`**
  - Daily human approval task volume: **`NO_PRODUCTION_VALUE_FOUND`**
- **Test Baseline**: Synthetic fixtures exercise single isolated workflow instances (`PHASE_0 TEST FIXTURE SCALE`).
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 23. Scheduling Scale

- **Source Evidence**: Specifies Google OR-Tools CP-SAT as primary solver and UniTime as educational timetabling PoC.
- **Production Values**:
  - Course sections to schedule: **`NO_PRODUCTION_VALUE_FOUND`**
  - Examination events per horizon: **`NO_PRODUCTION_VALUE_FOUND`**
  - Total constraint count / solver variables: **`NO_PRODUCTION_VALUE_FOUND`**
- **Test Baseline**: Test dataset `SC-EXAM-FEASIBLE-001.json` exercises 2 cohorts, 5 exams, and 10 sessions (`PHASE_0 TEST FIXTURE SCALE`).
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 24. Resource Scale

- **Source Evidence**: Specifies canonical resources representing staff, academic rooms, and equipment.
- **Production Values**:
  - Total faculty / staff resources: **`NO_PRODUCTION_VALUE_FOUND`**
  - Total physical rooms: **`NO_PRODUCTION_VALUE_FOUND`** (Gated by `BLK-02`)
  - Total equipment assets: **`NO_PRODUCTION_VALUE_FOUND`**
- **Test Baseline**: Synthetic resource fixture `RS-SYNTH-001.json` contains exactly 14 resources and 70 availability slots (`PHASE_0 TEST FIXTURE SCALE`).
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 25. Integration Scale

- **Source Evidence**: Documents 4 external domains (IdP, ERP/SIS, Calendar, E-8).
- **Production Values**:
  - Upstream ERP data volume: 247 tables cataloged in Task 0G.1 (`SCHEMA COMPLEXITY ONLY`; database row count unstated).
  - Daily calendar event synchronization volume: **`NO_PRODUCTION_VALUE_FOUND`**
  - Daily E-8 notification command volume: **`NO_PRODUCTION_VALUE_FOUND`**
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 26. Event Scale

- **Source Evidence**: Establishes 4 verified canonical event names (`task.blocked`, `workflow.task.created`, `workflow.sla.warning`, `schedule.published`) and transactional outbox pattern.
- **Production Values**:
  - Daily domain event volume: **`NO_PRODUCTION_VALUE_FOUND`**
  - Peak events published per second: **`NO_PRODUCTION_VALUE_FOUND`**
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 27. Storage Scale

- **Source Evidence**: PostgreSQL canonical System of Record with automated point-in-time recovery.
- **Production Values**:
  - Initial database storage capacity: **`NO_PRODUCTION_VALUE_FOUND`**
  - Annual data growth rate: **`NO_PRODUCTION_VALUE_FOUND`**
  - Document attachment / artifact storage volume: **`NO_PRODUCTION_VALUE_FOUND`**
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 28. AI Inference Scale

- **Source Evidence**: Context Builder applies goal-scoped minimization and token budgeting before prompt submission.
- **Production Values**:
  - Daily LLM inference requests: **`NO_PRODUCTION_VALUE_FOUND`**
  - Concurrent LLM planning calls: **`NO_PRODUCTION_VALUE_FOUND`**
  - Daily token consumption budget: **`NO_PRODUCTION_VALUE_FOUND`**
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 29. Concurrency Scale

- **Source Evidence**: Stateless API workers scale horizontally; solver jobs run in dedicated isolated worker pools.
- **Production Values**:
  - Peak concurrent HTTP requests: **`NO_PRODUCTION_VALUE_FOUND`**
  - Peak concurrent solver jobs: **`NO_PRODUCTION_VALUE_FOUND`**
  - Peak concurrent workflow task transitions: **`NO_PRODUCTION_VALUE_FOUND`**
- **Classification**: **`REQUIRES_BUSINESS_CONFIRMATION`**.

---

## 30. Fixture / Benchmark Scale vs Production Scale

To prevent architectural distortion, this audit strictly delineates three distinct categories of scale:

| Scale Dimension | Description | Governance Status | Permitted Usage |
| :--- | :--- | :---: | :--- |
| **Phase-0 Test Fixture Scale** | Fixed synthetic datasets created in Task 0F.2 (e.g., 14 resources, 5 exams, 10 sessions). | **`SYNTHETIC FIXTURE ONLY`** | Deterministic unit, integration, and regression testing. Strictly prohibited from being labeled production capacity. |
| **PoC Benchmark Dataset Scale** | Standard timetabling / OR-Tools benchmark datasets utilized to evaluate solver optimality and algorithm bounds. | **`BENCHMARK SCALE ONLY`** | Validating algorithmic efficiency and solver engine selection. Not a measure of production workload. |
| **First-Tenant Production Scale** | The actual operational volume, active user headcount, and problem horizon required for initial production pilot deployment. | **`REQUIRES_BUSINESS_CONFIRMATION`** | Target envelope to be defined via business confirmation and codified as candidate baselines in Task 0H.2. |

---

## 31. RTO / RPO Evidence

Disaster recovery and business continuity metrics were audited across the entire repository and source corpus:

- **Recovery Time Objective (RTO)**: **`NOT_FOUND_IN_AVAILABLE_SOURCES`**.
  - Accessible sources specify architectural recoverability principles (*"Point-in-time database backups; retry with backoff; dead-letter handling for failed events; workflow replay/recovery strategy"*, `Spec Sec. 10, p. 8`), but contain **zero numeric RTO targets** (e.g., 1 hour, 4 hours).
- **Recovery Point Objective (RPO)**: **`NOT_FOUND_IN_AVAILABLE_SOURCES`**.
  - Accessible sources require point-in-time recovery capabilities, but contain **zero numeric RPO targets** (e.g., 5 minutes, 1 hour).
- **Audit Mandate**: No numeric RTO or RPO values are fabricated in this audit report. Backup and recovery mechanisms do not themselves define an RTO or RPO.

---

## 32. Decision Gaps

Before candidate SLO, retention, and scale baselines can be proposed in Task 0H.2, the following decision gaps must be addressed using the neutral governance input classes:

| Gap ID | Operational Area | Decision Description | Required Input Class |
| :--- | :--- | :--- | :---: |
| **`GAP-SLO-01`** | **API Latency Tiers** | Confirm whether the proposed p95 < 500 ms target for CRUD APIs and asynchronous model/solver dispatch should be formalized as candidate SLOs. | `BUSINESS / PLATFORM INPUT REQUIRED` |
| **`GAP-SLO-02`** | **Uptime Tier** | Confirm whether the proposed 99.9% monthly availability target aligns with pilot customer commitments. | `BUSINESS / PLATFORM INPUT REQUIRED` |
| **`GAP-SCALE-01`**| **Pilot Tenant Profile**| Define representative first-tenant parameters (single campus vs multi-campus, active users, student headcount, faculty count). | `BUSINESS INPUT REQUIRED` |
| **`GAP-SCALE-02`**| **Operational Workload**| Define expected daily throughput (goals created, workflows executed, schedules published, concurrent users). | `BUSINESS INPUT REQUIRED` |
| **`GAP-SCALE-03`**| **Solver & AI Budgets** | Specify acceptable solve time ceiling (`SOLVER_MAX_SECONDS`), LLM timeout (`LLM_TIMEOUT`), and token budgets. | `PLATFORM INPUT REQUIRED` |
| **`GAP-RET-01`** | **Data Class Retention**| Establish retention windows for operational history, completed workflows, audit ledgers (`AUDIT_RETENTION_DAYS`), and telemetry. | `COMPLIANCE INPUT REQUIRED` |
| **`GAP-DR-01`**  | **Disaster Recovery** | Confirm whether formal numeric RTO and RPO targets are required for production pilot rollout. | `PLATFORM INPUT REQUIRED` |

---

## 33. Inputs Required for Task 0H.2

Task 0H.2 may formulate candidate engineering baselines once the missing business/platform/compliance inputs are explicitly documented. Task 0H.1 itself does not select them.

The inputs required before candidate baselines can be finalized comprise:
1. **API Responsiveness Expectations**: Business and platform confirmation of whether the proposed CRUD p95 < 500 ms and asynchronous heavy job patterns meet pilot operational needs.
2. **Target Availability Requirements**: Business and platform evaluation of the proposed 99.9% monthly uptime target against operational commitments.
3. **Pilot Tenant Sizing Envelope**: Business input defining the first-tenant institutional user base, faculty headcount, student population, and peak concurrent users.
4. **Operational Workload Horizons**: Business input defining expected daily goal creation, workflow activations, and institutional timetabling problem dimensions (courses, sections, rooms, exam windows).
5. **Computational & Cost Budgets**: Platform input defining acceptable solver duration limits (`SOLVER_MAX_SECONDS`), LLM response timeouts (`LLM_TIMEOUT`), and model token expenditure tolerances.
6. **Data Retention Policy by Class**: Compliance and customer legal input defining mandatory retention durations across operational records, audit ledgers, and observability telemetry.
7. **Disaster Recovery Objectives**: Platform input determining whether formal numeric RTO and RPO targets are required for initial production pilot rollout.

---

## 34. Change-Control Disclosure & Repository State

In accordance with strict Phase-0 governance and audit trail tracking:

- **Final Repository State**: Only the requested directory and files ([`docs/phase-0/SLO_RETENTION_SCALE_EVIDENCE_AUDIT.md`](file:///f:/operations-engine/docs/phase-0/SLO_RETENTION_SCALE_EVIDENCE_AUDIT.md) and [`docs/phase-0/SLO_RETENTION_SCALE_EVIDENCE_AUDIT.json`](file:///f:/operations-engine/docs/phase-0/SLO_RETENTION_SCALE_EVIDENCE_AUDIT.json)) remain as Task 0H.1/0H.1A outputs.
- **Execution History**: During Task 0H.1 execution, two temporary scratch extraction files (`scratch_spec_p8.txt` and `scratch_plan_extract.txt`) were transiently created in the repository root and subsequently deleted. Existing pre-task repository artifacts were not modified.
- **Governance Classification**: **`MINOR CHANGE-CONTROL DEVIATION — CLEANED UP`**.
- **Audit Verification**: Neither scratch file was recreated. Final repository state contains zero stray files. All subsequent temporary scripts and scratch data remain strictly inside external IDE artifact storage.

---

## 35. Audit Conclusion

The evidence audit for Task 0H.1/0H.1B concludes:
1. **Zero Final Baseline Fabrication**: No contractual SLOs, rigid retention periods, or production capacity limits were invented.
2. **Evidence Separation**: Proposed engineering targets (such as 99.9% monthly uptime and p95 < 500 ms CRUD latency) are properly classified as `PHASE_0_ENGINEERING_PROPOSAL`, completely separate from contractual source requirements (`SOURCE_NUMERIC_TARGET count = 0`).
3. **Metric Literal Fidelity**: Observable telemetry metrics across planning, workflow, scheduling, reliability, database, and integrations are thoroughly cataloged with direct source citations under `SOURCE_METRIC_WITHOUT_TARGET`.
4. **Retention Realism**:
   - Preserved that append-only audit trails mean immutability while retained, not indefinite retention (append-only != retain forever; `AUDIT_RETENTION_DURATION = UNRESOLVED`, `AUDIT_RETENTION_POLICY = TENANT / CUSTOMER / LEGAL POLICY DRIVEN`).
   - Transactional outbox durability and broker outage survival are preserved; outbox pruning is not asserted as a source fact (`OUTBOX_RETENTION_DURATION = UNRESOLVED`, `OUTBOX_PRUNING_POLICY = NOT_DEFINED_IN_AVAILABLE_SOURCES`).
   - Consumer deduplication and inbox idempotency are preserved; no specific replay window duration is invented (`INBOX_DEDUP_RETENTION_DURATION = UNRESOLVED`, `REPLAY_WINDOW_DURATION = UNRESOLVED`).
   - Solver search state is not established as canonical persisted records; no deletion lifecycle is invented (`SOLVER_TEMPORARY_RETENTION_DURATION = UNRESOLVED`, `SOLVER_TEMPORARY_DATA_LIFECYCLE = NOT_DEFINED_IN_AVAILABLE_SOURCES`). Canonical scheduling state belongs to E-7 PostgreSQL, while solver-internal temporary/search state has no source-defined retention lifecycle.
   - Student data boundary reflects the approved Task 0G.3 aggregate-only context policy ([`docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md`](file:///f:/operations-engine/docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md), [`docs/integrations/AI_CONTEXT_ACCESS_POLICY.md`](file:///f:/operations-engine/docs/integrations/AI_CONTEXT_ACCESS_POLICY.md), [`ADR-002`](file:///f:/operations-engine/docs/adr/ADR-002-task-intelligence-orchestration.md)) without generalizing into an unsupported system-wide retention rule.
5. **Scale Integrity**: First-tenant scale is properly classified as `REQUIRES_BUSINESS_CONFIRMATION`, rigorously preventing test fixture counts or MySQL schema complexity from distorting production capacity planning.
6. **RTO / RPO Absence**: Accurately classified as `NOT_FOUND_IN_AVAILABLE_SOURCES`.
7. **ADR Corpus Completeness**: Audited all 11 files currently present under `docs/adr/` (`ADR-000` through `ADR-009` and `README.md`), recording `ADR-009: REVIEWED — NO 0H-RELEVANT REQUIREMENT FOUND`.
8. **Neutral Ownership**: Replaced all invented organizational owner titles with approved neutral input classes (`BUSINESS INPUT REQUIRED`, `PLATFORM INPUT REQUIRED`, `COMPLIANCE INPUT REQUIRED`, etc.).
9. **Physical Citation Integrity**: Every cited repository path in both audit artifacts physically exists in the repository.
10. **Change-Control Cleanliness**: Disclosed transient execution scratch files; final repository state contains zero stray files.

---

## 36. Next Task

Next task after user review:
**Task 0H.2 — Candidate SLO, Retention & First-Tenant Scale Baseline**

> Task 0H.2 was not started.
