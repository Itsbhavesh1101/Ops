# E-7 Provider-Neutral Context Builder Contract

**Contract Version**: `1.0.0`  
**Contract Identifier**: `E7-CTR-CB-001`  
**Status**: `PHASE_0_DESIGN_BASELINE`  
**Role**: Provider-Neutral Context Assembly, Policy Enforcement & Minimization Layer  
**Target Consumer**: E-7 Task Intelligence Orchestration & Planning Layer  
**Upstream Boundary**: Provider-Neutral Enterprise Adapters (e.g. `ERPSourceAdapter`)  
**Operating Mode**: Deterministic Policy Enforcement, Whitelist Clamping & Goal-Scoped Minimization (Normative Design)  
**Authoritative Baselines**:  
- [MYSQL_SCHEMA_INVENTORY.md](file:///F:/operations-engine/docs/integrations/MYSQL_SCHEMA_INVENTORY.md) (Task 0G.1/0G.1A)  
- [MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md](file:///F:/operations-engine/docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md) (Task 0G.2/0G.2A/0G.2B)  
- [AI_CONTEXT_ACCESS_POLICY.md](file:///F:/operations-engine/docs/integrations/AI_CONTEXT_ACCESS_POLICY.md) (Task 0G.3/0G.3A/0G.3B)  
- [MYSQL_ERP_ADAPTER_CONTRACT.md](file:///F:/operations-engine/docs/integrations/MYSQL_ERP_ADAPTER_CONTRACT.md) (Task 0G.4/0G.4A)  
- [CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md](file:///F:/operations-engine/docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md) (Task 0E)  
- [IMPLEMENTATION_GUARDRAILS.md](file:///F:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md) (Task 0C.1)

---

## 1. Purpose

This document defines the architectural contract and operational specification for the **E-7 Provider-Neutral Context Builder**.

The Context Builder operates as the trusted intermediary between external enterprise adapters (such as the MySQL ERP adapter) and the downstream Task Intelligence planning engine. Its primary objective is to guarantee that the planning model receives only the minimal, approved, sanitized, and goal-relevant institutional context necessary to generate operational work breakdowns, schedules, and estimates, while strictly preventing data leakage, schema coupling, privilege escalation, and prompt injection.

The contract establishes:
1. **Provider Neutrality**: Complete decoupling of downstream planning from upstream relational schemas, database dialects, connection configurations, and persistence mechanics.
2. **Deterministic Policy Enforcement**: Strict clamping of context retrieval to the frozen 28 model-visible fields established in Tasks 0G.2 and 0G.3.
3. **Goal-Scoped Data Minimization**: Enforcing the foundational principle that *whitelisting does not imply automatic inclusion*, filtering available context strictly to the bounded cohorts, departments, and temporal windows of the active goal.
4. **Validation Evidence Separation**: Strict boundary isolation separating model-visible domain context from internal deterministic validation evidence, ensuring that internal identifiers, foreign key bindings, and database keys never cross into LLM prompt space.
5. **Robust Degraded and Failure Semantics**: Provider-neutral handling of missing records, stale context, unresolved tenant scopes, source conflicts, and upstream schema drift without silent data fabrication.

> [!IMPORTANT]
> This document represents **contract design and architectural specification only**. It contains zero runtime code, zero database connection logic, zero SQL queries, and zero ORM models. Phase 1 implementation has not started.

---

## 2. Scope and Non-Scope

### In-Scope
- Formal specification of Context Builder responsibilities and strict non-responsibilities.
- Security preconditions: authentication vs. authorization, and tenant organization scope enforcement.
- Supported context category semantics (exactly 7 approved categories) and category selection mechanics (`REQUESTED`, `APPROVED`, `INCLUDED`).
- Definition of the provider-neutral `ContextAssemblyRequest` and `ContextAssemblyResult` contracts.
- 9-step deterministic policy precedence order governing context admissibility.
- Goal-scoped data minimization rules and boundary clamping.
- Strict isolation of `model_context`, `validation_context`, `aggregates`, `provenance`, `omissions`, and `warnings`.
- Specific domain semantics: descriptive staff context, timetable commitments vs. availability, informational calendar dates, and aggregate student headcounts.
- Source authority, multi-provider selection, conflict resolution, and staleness handling.
- Defense-in-depth handling of untrusted upstream text and prevention of autonomous LLM data-access expansion.
- Operational gating: Production Blocker `BLK-01` (Tenant/Org Scope) and Scheduling Resource Gate `BLK-02` (Physical Resource Authority).
- Synthetic, structural walkthrough of the reference semester examination scenario (`E7-REF-EXAM-001`) using symbolic placeholders.
- Recommended target implementation directory layout.

### Out-of-Scope (Strictly Prohibited in Phase 0)
- Writing Python runtime code, service classes, interface classes, Pydantic models, or SQLAlchemy entities.
- Constructing model-specific or provider-specific system/developer prompt templates.
- Writing SQL queries, executing database calls, or selecting database driver libraries.
- Authenticating end-users or validating JWT/OIDC tokens (owned by core security).
- Prescribing a specific graph algorithm (such as topological sort implementations or depth-first search variants) for cycle detection or DAG validation.
- Generating Work Breakdown Structures (WBS), task durations, or risk assessments (owned by Task Intelligence).
- Authorizing operational plans or publishing schedules (owned by human reviewers and domain services).
- Defining physical database schemas or mapping tables in PostgreSQL.
- Prescribing concrete numeric limits for cache TTL seconds, polling intervals, or context token budgets.
- Modifying existing Task 0G.1 through 0G.4 documentation, ADRs, test fixtures, or scaffold configurations.
- Starting Phase 1 application development or Task 0G.6 integration inventory.

---

## 3. Architectural Boundary

The E-7 Operations Engine enforces a strict unidirectional pipeline from raw enterprise systems to sanitized planning context. The Context Builder sits squarely within the trusted application boundary, acting as a policy firewall between raw source adapters and the planning model.

```
+-----------------------------------------------------------------------------+
| UPSTREAM ERP DATA SOURCE (Company MySQL: `iredu`)                            |
| - Read-only relational storage (247 tables, 2,637 columns)                  |
| - Unpartitioned institutional records (no tenant_id / school_id)           |
+-----------------------------------------------------------------------------+
                                       |
                                       | [1] Parameterized, Read-Only Projections
                                       v
+-----------------------------------------------------------------------------+
| E-7 ENTERPRISE SOURCE ADAPTER(S) (e.g. `ERPSourceAdapter`)                  |
| - Connection lifecycle & credentials isolation                              |
| - Pre-reviewed parameterized queries (strictly no arbitrary SQL)            |
| - Upstream normalization into typed SourceContextBundle DTOs                |
| - Source provenance stamping & schema compatibility verification            |
+-----------------------------------------------------------------------------+
                                       |
                                       | [2] Provider-Neutral SourceContextBundle(s)
                                       v
+-----------------------------------------------------------------------------+
| E-7 CONTEXT BUILDER (Provider-Neutral Assembly & Minimization Layer)        |
| - Verifies organization scope reference (enforcing BLK-01)                 |
| - Evaluates operational goal & selects approved context categories          |
| - Enforces 9-step policy precedence & AI Context Access Policy              |
| - Applies goal-scoped minimization (filters to relevant cohorts/window)     |
| - Enforces conditional identity exposure (<opaque_staff_ref> default)       |
| - Separates validation evidence & aggregates from model-visible context     |
| - Generates ContextAssemblyResult with audit provenance & omissions         |
+-----------------------------------------------------------------------------+
                                       |
                                       | [3] Structured `model_context` (Data Only)
                                       v
+-----------------------------------------------------------------------------+
| TASK INTELLIGENCE PROMPT / ORCHESTRATION LAYER                              |
| - Serializes structured `model_context` into model prompt templates         |
| - Preserves untrusted data boundaries; communicates with LLM API            |
+-----------------------------------------------------------------------------+
                                       |
                                       | [4] Model Inference (System / User Prompts)
                                       v
+-----------------------------------------------------------------------------+
| TASK INTELLIGENCE PLANNER (LLM Reasoning Engine)                            |
| - Goal interpretation & hierarchical WBS decomposition                      |
| - Proposes tasks, dependencies, durations, and milestones                   |
| - NO knowledge of MySQL tables, columns, foreign keys, SQL, or credentials  |
+-----------------------------------------------------------------------------+
                                       |
                                       | [5] Proposed PlanSchema Payload
                                       v
+-----------------------------------------------------------------------------+
| DETERMINISTIC VALIDATORS & GRAPH SERVICES (Backend Verification)             |
| - PlanSchema structural & policy validation                                 |
| - Resolves opaque references against Context Builder `validation_context`   |
| - Dependency-reference validation, DAG cycle detection, & CPM/slack analysis|
+-----------------------------------------------------------------------------+
                                       |
                                       | [6] Human Review & Persistence
                                       v
+-----------------------------------------------------------------------------+
| POSTGRESQL SYSTEM OF RECORD (Canonical Persisted Operational State)         |
| - Canonical persistence of verified plans, tasks, and dependencies          |
| - Storage of canonical/source mapping records & audit ledgers               |
+-----------------------------------------------------------------------------+
```

### Architectural Invariants
1. **Provider Neutrality**: The Context Builder interacts with enterprise data exclusively through provider-neutral adapter ports (e.g. `ERPSourceAdapter`). It contains zero MySQL-specific or vendor-specific dialect logic.
2. **Data, Not Prompts**: The Context Builder outputs structured domain data (`ContextAssemblyResult`). It does not format LLM prompts, apply markdown decorations, or manage model system instructions.
3. **No Direct Model Access to Sources**: Upstream adapters and enterprise databases are completely invisible to the planning LLM. The model cannot issue queries, navigate database schemas, or request raw tables.

---

## 4. Responsibility Matrix

The separation of operational responsibilities across system boundaries is absolute:

| Boundary / Component | Primary Responsibilities | Strict Non-Responsibilities |
| :--- | :--- | :--- |
| **Source Adapters (`ERPSourceAdapter`)** | • Low-level database connectivity & credentials isolation.<br>• Parameterized query execution against source systems.<br>• Field projection whitelist enforcement at source perimeter.<br>• Normalization into typed `SourceContextBundle` DTOs.<br>• Upstream identifier mapping & source provenance stamping. | • Does not determine goal-level context requirements.<br>• Does not assemble final LLM context.<br>• Does not evaluate user permissions or AI minimization policy.<br>• Does not evaluate cross-source authority or conflicts.<br>• Does not format prompts or interact with LLMs. |
| **E-7 Context Builder** | • Verifies pre-established organization scope (`BLK-01`).<br>• Evaluates operational goal to determine relevant categories.<br>• Selects approved source adapters via capability contracts.<br>• Requests bounded context from adapters.<br>• Enforces Task 0G.2 mapping & Task 0G.3 access policy.<br>• Enforces goal-scoped minimization & conditional identity.<br>• Separates model-visible context from validation evidence.<br>• Detects missing, stale, conflicting, or degraded context.<br>• Produces structured `ContextAssemblyResult` & audit evidence. | • Does not authenticate users or verify JWT/OIDC tokens.<br>• Does not connect to databases or issue SQL queries.<br>• Does not format LLM prompt templates.<br>• Does not generate plans, WBS, dependencies, or estimates.<br>• Does not execute DAG cycle checks or CPM calculations.<br>• Does not approve plans or mutate canonical operational state. |
| **Task Intelligence Prompt Layer** | • Consumes structured `model_context` from Context Builder.<br>• Serializes structured domain context into LLM prompt templates.<br>• Preserves untrusted-data boundaries and escaping.<br>• Manages LLM client API calls, retries, and token budgets. | • Does not evaluate context access policies.<br>• Does not query enterprise databases or adapters.<br>• Does not access internal `validation_context` evidence. |
| **Task Intelligence Planner (LLM)** | • Interprets operational goal and serialized model context.<br>• Generates hierarchical Work Breakdown Structure (WBS).<br>• Proposes operational dependencies, durations, and milestones.<br>• Identifies potential operational risks and mitigation advice. | • Does not know database tables, schemas, or SQL.<br>• Does not connect to databases or call external APIs.<br>• Does not expand its own data access scope autonomously.<br>• Does not perform deterministic DAG cycle detection.<br>• Does not persist plans or execute operational actions. |
| **Deterministic Validators & Graph Services** | • Validates PlanSchema compliance and entity references.<br>• Resolves opaque references against Context Builder `validation_context`.<br>• Dependency-reference validation, DAG cycle detection, and topological ordering.<br>• Computes Early/Late Start/Finish, Total Float, and Critical Path (CPM).<br>• Verifies institutional constraints before canonical persistence. | • Does not generate plans or task descriptions.<br>• Does not communicate with LLM endpoints.<br>• Does not normalize raw enterprise data. |
| **PostgreSQL System of Record** | • Canonical persistence of verified plans, tasks, and dependencies.<br>• Manages transactional outbox/inbox events.<br>• Stores canonical/source mapping records linking external IDs to canonical E-7 entity references.<br>• Stores immutable context and planning audit logs. | • Does not perform algorithmic CPM calculations or cycle checks.<br>• Does not query upstream enterprise databases directly. |

---

## 5. Security and Policy Preconditions

The Context Builder operates strictly downstream of enterprise authentication and scope resolution:

```
Core Security & Identity Layer
             ↓
[1] Authenticated Principal / Service Identity (JWT / OIDC)
             ↓
[2] Authoritative Organization Scope Resolution
             ↓
[3] Context Builder Request (with verified `organization_scope_reference`)
```

### Precondition Rules
1. **No User Authentication in Context Builder**: The Context Builder does NOT authenticate users, validate credentials, or parse JWT/OIDC tokens. Authentication belongs exclusively to the E-7 core security layer.
2. **Authoritative Scope Prerequisite**: Live context assembly MUST NOT proceed without an authoritative, verified `organization_scope_reference`. Untrusted client-supplied tenant identifiers are rejected.
3. **Production Blocker BLK-01 Enforced**: In accordance with `BLK-01`, live production source context retrieval against the company MySQL database remains **strictly blocked** until an authoritative organization/tenant scoping mechanism is established. Development and testing may proceed using verified synthetic scope references.
4. **Deny-by-Default Boundary**: Any request lacking verified scope, specifying unmapped context categories, or attempting to query unapproved fields is denied immediately and failed closed.

---

## 6. Supported Context Categories

The Context Builder contract formally supports **exactly seven context categories**, frozen in Task 0G.2 and Task 0G.3:

| Context Category | Status | Model-Visible Fields | Validation-Only Fields | Canonical Domain Semantics |
| :--- | :--- | :---: | :---: | :--- |
| **`organization_context`** | `SUPPORTED` | 2 | 4 | Organizational department association and job designations. Descriptive context only; does not establish operational eligibility, capability, or authorization. |
| **`academic_context`** | `SUPPORTED` | 3 | 10 | Academic year temporal bounds, target grade classes, sections, and class-section cohort structures. Scoped strictly to goal-relevant target cohorts. |
| **`subject_context`** | `SUPPORTED` | 6 | 11 | Course catalogue, subject codes, instructional types (Theory vs Practical), and curricular groupings. |
| **`staff_resource_context`** | `SUPPORTED` | 4 | 6 | Relevant normalized staff context, qualifications, work experience, and opaque reference tokens (`<opaque_staff_ref>`). Human names conditional under policy. Descriptive attributes do NOT prove task eligibility, capability, or invigilator suitability. RBAC roles (`roles`, `staff_roles`) are validation-only. |
| **`commitment_context`** | `SUPPORTED` | 5 | 5 | Published teaching schedules defining known busy periods. Scheduled period = known busy commitment; absence of timetable entry != complete availability. |
| **`calendar_context`** | `SUPPORTED` | 8 | 7 | Informational holiday dates and academic event descriptions. Informational dates do not constitute automated hard blackouts without explicit business policy. |
| **`aggregate_context`** | `SUPPORTED` | 0 | 6 | Approved adapter-derived mathematical headcounts (e.g. cohort student count). Individual student rows and columns are not exposed to the model for the current Task Intelligence scope and policy version. |

### Deferred and Gated Categories (Not Supported Initially)
- **`authoritative_future_availability`**: `DEFERRED` (Forward leave modeling deferred to Phase 2/4; daily attendance punch logs `staff_attendance` are not exposed to the model and do not establish future availability).
- **`physical_room_master`**: `UNSUPPORTED` (Upstream ERP schema lacks canonical physical academic room masters; gated by `BLK-02`).
- **`room_capacity`**: `UNSUPPORTED` (Upstream ERP schema contains zero room or seating capacity fields; gated by `BLK-02`).
- **`equipment_capability`**: `UNSUPPORTED` (Upstream ERP schema contains zero laboratory or instructional equipment inventory).
- **`authoritative_invigilator_eligibility`**: `UNSUPPORTED` (Upstream ERP schema lacks invigilation certification, training, or examination duty eligibility models).
- **`authoritative_exam_resource_rules`**: `REQUIRES_BUSINESS_CONFIRMATION` (Operational authority between CBSE and Legacy exam subsystems unresolved via `UBQ-TI-03`; physical room scheduling gated by `BLK-02`).

---

## 7. Category Selection Semantics

The Context Builder must not automatically retrieve all seven categories for every goal. Category inclusion is governed by a strict tripartite lifecycle:

```
+-----------------------------------------------------------------------------+
| REQUESTED CATEGORIES                                                        |
| Explicitly requested by upstream orchestration or goal-intake step          |
+-----------------------------------------------------------------------------+
                                       |
                                       | [1] Policy & Capability Filter
                                       v
+-----------------------------------------------------------------------------+
| APPROVED CATEGORIES                                                         |
| Permitted by AI Context Access Policy AND supported by active adapters       |
+-----------------------------------------------------------------------------+
                                       |
                                       | [2] Goal-Scoped Minimization
                                       v
+-----------------------------------------------------------------------------+
| INCLUDED CATEGORIES                                                         |
| Actually required by the bounded goal AND successfully assembled           |
+-----------------------------------------------------------------------------+
```

### Selection Rules
1. **No Universal Fetch**: The Context Builder MUST NOT issue a blanket request for all seven categories unless the specific goal demonstrably requires them.
2. **Category Clamping**: Any requested category that is unmapped, marked `DEFERRED`, or marked `UNSUPPORTED` is clamped out. If the clamped category is mandatory for goal execution, the assembly status transitions to `BLOCKED`; if optional, it transitions to `DEGRADED` or omission with an explicit record.
3. **No Dynamic Model Expansion**: If a downstream planning model generates a follow-up request such as *"query table X"* or *"retrieve all employee salaries"*, that request is advisory only and MUST be rejected by the Context Builder. Context retrieval is restricted exclusively to approved categories.

---

## 8. Context Assembly Request

The `ContextAssemblyRequest` represents the strongly typed, provider-neutral contract passed from Task Intelligence orchestration into the Context Builder:

```json
{
  "context_request_id": "<globally_unique_request_id>",
  "correlation_id": "<distributed_tracing_correlation_id>",
  "authenticated_actor_reference": "<authenticated_actor_reference>",
  "organization_scope_reference": "<authorized_organization_scope_reference>",
  "goal_reference": "E7-REF-EXAM-001",
  "bounded_goal_scope": "Plan and conduct semester examinations for target cohort",
  "requested_context_categories": [
    "organization_context",
    "academic_context",
    "subject_context",
    "staff_resource_context",
    "commitment_context",
    "calendar_context",
    "aggregate_context"
  ],
  "normalized_selectors": {
    "normalized_academic_session_ref": "<normalized_session_ref>",
    "normalized_class_refs": ["<normalized_class_ref>"],
    "normalized_department_refs": ["<normalized_dept_ref>"],
    "normalized_subject_refs": [],
    "opaque_staff_refs": []
  },
  "temporal_scope": {
    "planning_window_start": "<planning_window_start_utc>",
    "planning_window_end": "<planning_window_end_utc>"
  },
  "requested_as_of": "<optional_iso_8601_utc_timestamp>",
  "mapping_version": "1.2.0",
  "access_policy_version": "1.0.0",
  "freshness_tolerance": "DAILY_OR_PERIODIC"
}
```

### Prohibited Concepts in Request
- Database table names, column names, or raw primary keys.
- SQL syntax fragments, `WHERE` clauses, or database query strings.
- Direct database connection parameters, credentials, or host information.
- Unbounded scan parameters (*"fetch all staff"*, *"dump all records"*).
- Prompt templates or instructions directed to the LLM.

---

## 9. Normalized Selectors

Selectors supplied in a `ContextAssemblyRequest` are provider-neutral domain references:

```
Provider-Neutral Selectors:
├── normalized_academic_session_ref (e.g. "<normalized_session_ref>")
├── normalized_class_refs (e.g. ["<normalized_class_ref>"])
├── normalized_section_refs (e.g. ["<normalized_section_ref>"])
├── normalized_subject_refs (e.g. ["<normalized_subject_ref>"])
├── normalized_department_refs (e.g. ["<normalized_dept_ref>"])
├── opaque_staff_refs (e.g. ["<opaque_staff_ref>"])
└── bounded_temporal_scope (planning_window_start to planning_window_end)
```

### Selector Invariants
1. **Decoupled from Database Keys**: Selectors must never expose or depend upon raw auto-increment integers or MySQL internal primary keys.
2. **Translation at Adapter Perimeter**: Upstream adapters translate normalized selectors into provider-specific queries internally. The Context Builder operates purely on normalized references.
3. **Bounded Selectors Mandatory**: Bounded selectors must accompany every request. Requests lacking selectors (such as requesting subjects without specifying class or session) are rejected as unbounded.

---

## 10. Provider / Adapter Selection

The Context Builder coordinates one or more enterprise adapters without coupling to concrete database implementations:

1. **Capability Matching**: The Context Builder queries adapter capability manifests (`get_capabilities()`) to identify which registered adapter satisfies each approved category.
2. **Authoritative Configuration**: Where multiple adapters could supply the same logical category (e.g. MySQL ERP vs. external SIS), source selection MUST follow pre-configured institutional authority rules.
3. **No Silent Merging**: The Context Builder MUST NOT silently merge conflicting records from disparate sources. If source authority is ambiguous or unconfigured, the Context Builder must report a conflict in `unresolved_questions` and set `assembly_status` to `REQUIRES_CLARIFICATION` or `BLOCKED` as policy dictates.
4. **Adapter Health Verification**: Prior to dispatching requests, the Context Builder verifies adapter health (`get_health()`). If an adapter is `UNAVAILABLE` or `MISCONFIGURED`, context assembly fails closed. A `DEGRADED` health state does not automatically block all categories; context retrieval may proceed if approved by policy for the active goal.

---

## 11. Policy Precedence

Admissibility of any data point into the final model-visible context is governed by a strict **9-step precedence order**. A field or entity becomes model-visible **only if ALL applicable gates permit it**:

```
+-----------------------------------------------------------------------------+
| 1. Authenticated Principal & Scope Verification (BLK-01 verified)           |
+-----------------------------------------------------------------------------+
                                       ↓
+-----------------------------------------------------------------------------+
| 2. Source Adapter Capability & Health Validation (Category SUPPORTED & usable)|
+-----------------------------------------------------------------------------+
                                       ↓
+-----------------------------------------------------------------------------+
| 3. Task 0G.2 Mapping Authorization (Table mapped for TI)                    |
+-----------------------------------------------------------------------------+
                                       ↓
+-----------------------------------------------------------------------------+
| 4. Task 0G.3 Access Policy Whitelisting (Model: PLANNER_CONTEXT_ALLOWED)    |
+-----------------------------------------------------------------------------+
                                       ↓
+-----------------------------------------------------------------------------+
| 5. Context Category Goal Relevance (Category is required for active goal)   |
+-----------------------------------------------------------------------------+
                                       ↓
+-----------------------------------------------------------------------------+
| 6. Goal-Scoped Minimization (Entity matches target cohort/department/window)|
+-----------------------------------------------------------------------------+
                                       ↓
+-----------------------------------------------------------------------------+
| 7. Conditional Identity Rules (<opaque_staff_ref> default; names masked)    |
+-----------------------------------------------------------------------------+
                                       ↓
+-----------------------------------------------------------------------------+
| 8. Freshness & Staleness Policy (Context satisfies freshness tolerance)     |
+-----------------------------------------------------------------------------+
                                       ↓
+-----------------------------------------------------------------------------+
| 9. Untrusted Text Handling (Text treated as data; zero instruction authority)|
+-----------------------------------------------------------------------------+
                                       ↓
                        MODEL-VISIBLE CONTEXT APPROVED
```

### Precedence Invariants
- **Fail-Closed on Any Denial**: A denial at **any single gate** results in immediate exclusion from `model_context`.
- **Default Deny**: Any field, column, entity, or category not explicitly evaluated and approved is denied by default.
- **Model vs. Validation Policy Distinction**: Whitelisting under `PLANNER_CONTEXT_ALLOWED` or `AGGREGATE_ONLY` applies specifically to model-visible inclusion. Fields classified as `DETERMINISTIC_VALIDATION_ONLY` remain fully authorized for internal routing into `validation_context`, but MUST NOT cross into `model_context`.
- **Adapter Output Cannot Override Policy**: Even if an upstream adapter erroneously includes an unwhitelisted field, the Context Builder MUST drop it during policy evaluation.

---

## 12. Goal-Scoped Minimization

```
WHITELIST PERMISSION != AUTOMATIC INCLUSION
```

The Task 0G.3 access policy establishes an absolute maximum ceiling of **28 PLANNER_CONTEXT_ALLOWED** fields. Goal-scoped minimization mandates that the Context Builder expose only the minimal subset required for the specific operational goal:

### Minimization Invariants
1. **Cohort Bounding**: For a specific cohort examination goal, the Context Builder includes only the target academic cohorts, related curriculum subjects, and affiliated faculty. Other grades and cohorts MUST be omitted.
2. **Temporal Bounding Semantics**: Planning-window bounds are applied where requested context is temporally scoped (e.g. published teaching timetables, calendar dates). Historical context outside the planning window is not included automatically, but MAY be retained only when explicitly relevant and authorized for the goal (such as historical analysis or effort estimation basis).
3. **Department Bounding**: Staff context is restricted to departments relevant to the goal (e.g. Academic faculty). Unrelated departments are omitted unless explicitly requested and justified.
4. **No Arbitrary Limits**: Concrete context budgets or token ceilings are not defined in Phase 0; minimization is enforced semantically based on goal parameters.

---

## 13. Context Assembly Result

The Context Builder returns a strongly typed, provider-neutral `ContextAssemblyResult` comprising eight distinct functional sections:

```
ContextAssemblyResult
├── model_context (Clean, minimized data payload for Task Intelligence)
│   ├── academic_cohorts [...]
│   ├── curriculum_subjects [...]
│   ├── relevant_staff_context [...]
│   ├── published_commitments [...]
│   ├── informational_calendar_dates [...]
│   └── cohort_aggregates [...]
├── validation_context (Internal-only verification evidence - NOT model-visible)
│   ├── external_source_mappings { <opaque_ref>: { source: "mysql", ext_id: "<id>" } }
│   ├── relational_bindings { cohort_binding: "<id>", session_binding: "<id>" }
│   └── approved_status_and_timing_evidence { <opaque_ref>: "<status_val>" }
├── aggregates (Approved mathematical headcounts - zero raw student rows)
│   └── headcounts { "<cohort_ref>": <count> }
├── provenance (Audit metadata for traceability)
│   ├── context_request_id, correlation_id, timestamp
│   ├── sources_consulted [{ adapter: "mysql_iredu", mapping_version: "1.2.0" }]
│   └── freshness_evaluations { ... }
├── omissions (Record of excluded fields/categories and policy justifications)
│   └── [{ field: "staff.name", reason: "CONDITIONAL_IDENTITY_WITHHELD" }]
├── warnings (Non-fatal degraded-context diagnostics)
│   └── [{ code: "STALE_OPTIONAL_CONTEXT", message: "Calendar data exceeds daily tolerance" }]
├── unresolved_questions (Ambiguities requiring human/business clarification)
│   └── [{ topic: "EXAM_SUBSYSTEM_AUTHORITY", details: "CBSE vs Legacy authority unresolved" }]
└── assembly_status (READY | DEGRADED | BLOCKED | REQUIRES_CLARIFICATION)
```

---

## 14. Assembly Status Semantics

The Context Builder reports an explicit, provider-neutral status enum:

| Status Enum | Operational Definition | Downstream Planner Action |
| :--- | :--- | :--- |
| **`READY`** | All required context was successfully assembled, validated against policy, and minimized within approved rules. | Task Intelligence may proceed with plan generation. |
| **`DEGRADED`** | Planning may proceed under policy, but non-critical context is incomplete, stale, or partially unavailable. Warnings are explicitly surfaced. | Task Intelligence proceeds, but must surface degraded context caveats in proposed plan metadata. |
| **`BLOCKED`** | A mandatory security prerequisite, organization scope rule, source connectivity, schema compatibility, or critical context condition prevents safe planning. | Context assembly halts; no model context is emitted; planning is strictly blocked. |
| **`REQUIRES_CLARIFICATION`** | The system cannot safely resolve source conflicts, ambiguous mappings, or policy gates using available evidence alone. | Context assembly halts; unresolved questions are surfaced for administrative review. |

### Status Rules
- **No Numeric Confidence Scores**: The Context Builder does not assign synthetic numeric confidence scores (e.g. *"0.85 confidence"*). Status is deterministic and enum-driven.
- **No Silent Degradation of Blockers**: A `BLOCKED` condition (such as missing scope or unavailable critical source) must NEVER be downgraded to `DEGRADED`.

---

## 15. Model-Visible Context Contract

The `model_context` section of the result is the **only data package** that may be provided to the downstream Task Intelligence prompt layer:

### Model Context Invariants
1. **Structured Data Only**: `model_context` contains typed JSON domain objects. It is NOT a prompt template, developer prompt, or markdown document.
2. **Whitelist Clamped**: Every field in `model_context` must map to one of the 28 `PLANNER_CONTEXT_ALLOWED` fields.
3. **No Database Dialect or Keys**: Contains zero MySQL table names, column names, connection URIs, credentials, or raw auto-increment primary keys.
4. **Opaque Tokenization**: Staff members are identified by default using opaque references (`<opaque_staff_ref>`).
5. **No Individual Student Data**: Contains zero individual student rows, student names, contact details, or guardian records.
6. **Informational Tagging**: Calendar entries and timetable commitments are explicitly tagged with semantic labels (`is_known_commitment: true`, `is_informational_date: true`) to prevent model hallucination of unyielding hard constraints.

---

## 16. Validation-Only Context

The `validation_context` section contains internal evidence required exclusively by downstream deterministic validation engines:

### Validation Context Contents
- Internal external-to-canonical identity mappings linking opaque references to upstream IDs.
- Relational binding keys (e.g. `class_section_id`, `subject_group_id`, `session_id`).
- Approved validation-only timing, interval, and schedule evidence.
- Upstream lifecycle and status flags (e.g. `is_active`, `status`).

### Boundary Isolation
- **Strictly Hidden from LLM**: `validation_context` is completely omitted from the payload delivered to the prompt formatting layer.
- **Delivered to Deterministic Validators**: When the LLM subsequently proposes a plan, deterministic validators (Graph Service, Plan Validator) consume `validation_context` to resolve opaque references through validated E-7 canonical/source-reference mappings, verify entity existence, and validate institutional constraints.

---

## 17. Aggregate Context

Student cohort headcounts are governed by strict aggregate-only rules:

1. **Adapter-Derived Aggregation**: All cohort counts MUST be computed within the trusted adapter boundary via approved aggregate computation.
2. **Zero Row Passthrough**: The Context Builder never receives, stores, or forwards individual student records.
3. **PII Lockdown**: All 67 individual student columns from `students` and `student_session` remain non-model-visible for the current Task Intelligence scope and policy version.
4. **Governance-Controlled Suppression**: Differential privacy and cohort suppression policies are applied by the Context Builder based on institutional governance rules. No arbitrary numeric threshold is hardcoded in Phase 0.

---

## 18. Staff Context Semantics

In strict accordance with Task 0G.3B, staff attributes provided by the Context Builder represent **descriptive context only**:

```
STAFF DESCRIPTIVE CONTEXT != OPERATIONAL ELIGIBILITY OR CAPABILITY
```

1. **Descriptive Non-Authority**:
   - `qualification` describes academic background. It does **not** prove regulatory certification or examination board clearance.
   - `work_exp` provides seniority context. It does **not** prove competence for specialized tasks.
   - `department` and `staff_designation` indicate administrative placement. They do **not** grant supervisory or approval authority.
   - Timetable allocations do **not** prove future operational assignment suitability.
2. **Exclusion of RBAC Roles from Planner Capability Context**:
   - Application and RBAC roles (`roles`, `staff_roles`) are validation-only and MUST NOT be promoted into planner-visible capability context. They do not establish eligibility, professional skill, operational capability, approval authority, or assignment suitability.
3. **Identity Exposure Default**:
   - Staff members are exposed using opaque normalized tokens (e.g. `<opaque_staff_ref>`).
   - Real human names (`name`, `surname`) are conditional fields and remain masked unless the specific operational workflow explicitly authorizes human identity exposure.
4. **Non-Authoritative Candidate Suggestions**:
   - Any candidate assignment suggestions generated by Task Intelligence remain non-authoritative proposals requiring approved capability/policy validation and human review.

---

## 19. Commitment / Availability Semantics

The Context Builder enforces explicit semantics for scheduled teaching periods:

```
SCHEDULED TIMETABLE PERIOD = KNOWN BUSY COMMITMENT
ABSENCE OF TIMETABLE ENTRY != AUTHORITATIVE FUTURE AVAILABILITY
```

1. **Known Busy Window**: A published timetable period represents a `KNOWN_BUSY_COMMITMENT`. The Context Builder tags these windows so the planner can avoid scheduling conflicting tasks.
2. **Absence is Not Availability**: The absence of a timetable entry denotes only that no instructional class is recorded; it does **not** prove that the staff member is available or on campus. The Context Builder MUST NOT derive complete availability by negating the timetable.
3. **Attendance Logs Excluded**: Historical daily punch attendance (`staff_attendance`) is not exposed to the model for the current Task Intelligence scope and policy version (historical attendance does not establish future availability).

---

## 20. Calendar Semantics

Data from institutional calendars provides operational awareness:

```
INFORMATIONAL CALENDAR DATE != DETERMINISTIC HARD BLACKOUT
```

1. **Informational Awareness**: Calendar entries (holidays, vacations, festivals) inform the planner of institutional events.
2. **Semantic Labeling**: The Context Builder labels calendar entries as `INFORMATIONAL_DATE`. It MUST NOT classify entries as automated hard blackouts (`HARD_BLACKOUT`). Operational activities (such as exam evaluation or facility preparation) routinely take place during holiday periods.
3. **Policy-Driven Promotion**: A calendar date becomes an unbreachable scheduling blackout only if an explicit, authorized E-7 business policy designates it as such during deterministic validation.

---

## 21. External Reference Semantics

The Context Builder manages the separation between external source references and E-7 canonical references:

1. **Opaque Tokenization for Model**: The planning model receives deterministic opaque tokens (`<opaque_staff_ref>`, `<cohort_ref>`).
2. **Internal Mapping Preservation**: The Context Builder records the correlation between opaque tokens, external source identifiers, and canonical E-7 entity references in `validation_context`.
3. **PostgreSQL Ownership**: The PostgreSQL System of Record stores canonical/source mapping records linking external identifiers to canonical E-7 entity references. The Context Builder does not define physical database schemas.
4. **Unknown Model References**: If a planning model outputs an unrecognized or hallucinated token, the deterministic validator rejects the proposal during reference resolution.

---

## 22. Source Authority and Conflict Handling

When integrating multiple enterprise sources, the Context Builder handles conflicts deterministically:

1. **Configured Source Authority**: Precedence between overlapping sources must be explicitly defined in institutional system configuration.
2. **No "Latest Timestamp Always Wins"**: The Context Builder MUST NOT resolve conflicting authoritative data using arbitrary heuristics like latest timestamp or row ordering.
3. **Policy-Driven Conflict Outcomes**:
   - For required context, an unresolved source conflict or authority ambiguity MAY result in `BLOCKED` or `REQUIRES_CLARIFICATION`, depending on policy and whether administrative review can resolve it.
   - For optional context, policy MAY permit omission (`OMIT`), degraded planning (`DEGRADED`), or escalation (`REQUIRES_CLARIFICATION`).
   - All conflicts MUST be explicitly recorded in `unresolved_questions` or `warnings`.

---

## 23. Provenance

Every `ContextAssemblyResult` maintains complete audit provenance:

### Provenance Attributes
- `context_request_id` and distributed `correlation_id`.
- `organization_scope_reference` and authenticated actor reference.
- Source adapter identifier and source system name (e.g. `mysql_iredu`).
- Source entity type and external source reference.
- Upstream query or retrieval timestamps.
- Source freshness classification (`STATIC_OR_SLOW`, `DAILY_OR_PERIODIC`, etc.).
- Active Task 0G.2 mapping version and Task 0G.3 access policy version.
- Transformation, redaction, and minimization rules applied.

### Internal vs. Model Exposure
Provenance metadata is retained in the `provenance` section of the result for audit ledgers and deterministic validation. It is omitted from the minimal `model_context` delivered to the LLM.

---

## 24. Freshness / Staleness

The Context Builder applies the frozen freshness classification vocabulary:

| Freshness Class | Applicable Domain Sources | Staleness Handling Rule |
| :--- | :--- | :--- |
| **`STATIC_OR_SLOW`** | `department`, `staff_designation`, `sessions`, `classes`, `sections`, `subjects`, `subject_groups` | Curricular masters change infrequently. Daily cache snapshots acceptable. |
| **`DAILY_OR_PERIODIC`** | `staff`, `subject_timetable`, `annual_calendar`, `events`, cohort headcounts | Rosters and schedules change periodically. Stale optional context may produce `DEGRADED` or omission; stale required context may trigger `BLOCKED` or `DEGRADED` per policy. |
| **`NEAR_REAL_TIME`** | *None in initial MySQL sources* | High-frequency operational state. Not supported initially. |
| **`EVENT_DRIVEN_PREFERRED`** | *None in initial MySQL sources* | Webhook/CDC driven updates. Not supported initially. |
| **`UNKNOWN`** | Unmapped or unclassified tables | Must not be queried or returned. |

### Freshness Rules
- **Policy-Driven Outcomes**: Stale required context MAY result in `BLOCKED` when the applicable policy declares freshness critical for goal safety. Stale optional context MAY result in `DEGRADED` or omission when policy permits planning without it.
- **No Invented Timers**: Numeric TTL seconds, polling frequencies, and cron schedules are implementation details reserved for Phase 1.
- **Fail-Closed on Unknown Freshness**: Context with unknown or untracked freshness must not be represented as current.

---

## 25. Missing Data and Ambiguity

The Context Builder enforces an absolute rule against silent data fabrication:

```
NO SILENT DEFAULT FABRICATION
```

### Missing Data Invariants
1. **Missing Staff**: If staff records cannot be found for a requested department, the Context Builder MUST NOT inject "dummy" or synthetic teachers.
2. **Missing Subjects**: If course catalog records are missing, the Context Builder MUST NOT synthesize subjects.
3. **Missing Headcounts**: If cohort headcount queries fail, the Context Builder MUST NOT guess or default class size.
4. **Missing Rooms / Capacity**: The Context Builder MUST NOT invent room numbers, capacities, or dimensions.
5. **Operational Outcomes**: Depending on goal criticality, missing context is handled explicitly:
   - **Non-Critical Omission**: Context omitted with an entry in `omissions` and a structured diagnostic in `warnings` (`assembly_status: DEGRADED`).
   - **Critical Omission**: Assembly halts immediately without generating model context (`assembly_status: BLOCKED`).
   - **Ambiguity**: Unresolvable mappings are surfaced in `unresolved_questions` (`assembly_status: REQUIRES_CLARIFICATION`).

---

## 26. Untrusted Source Text

All textual data retrieved from enterprise ERP systems (e.g. subject titles, department descriptions, event names, holiday notices) is classified as **untrusted data**:

### Defense-in-Depth Rules
1. **Approved Free Text**: Text approved by Task 0G.3 enters model-visible context strictly as passive data literals. It receives zero system or developer instruction authority.
2. **Text Normalization**: Upstream text strings are normalized (stripping unprintable control characters and zero-width spaces).
3. **Suspicious Text Handling**: If an ERP string contains prompt injection signatures or unapproved control content, the Context Builder MUST omit or quarantine the text according to policy, record security evidence, and transition `assembly_status` to `DEGRADED` or `BLOCKED` as appropriate.
4. **Implementation Neutrality**: Concrete regex patterns, delimiters, or sanitization algorithms are implementation choices reserved for Phase 1.

---

## 27. Prompt Construction Boundary

The separation between Context Builder data assembly and prompt serialization is absolute:

```
+-----------------------------------------------------------------------------+
| E-7 CONTEXT BUILDER                                                         |
| - Assembles strongly typed, normalized domain data (`ContextAssemblyResult`)|
| - Enforces access policy, goal minimization, and data isolation             |
| - OUTPUT: Structured JSON Data (`model_context`)                            |
+-----------------------------------------------------------------------------+
                                       |
                                       | Clean Data Contract (Zero Prompt Logic)
                                       v
+-----------------------------------------------------------------------------+
| TASK INTELLIGENCE PROMPT / ORCHESTRATION LAYER                              |
| - Consumes structured `model_context` data                                  |
| - Owns system prompts, developer instructions, and few-shot examples        |
| - Serializes data into model-specific message envelopes                     |
| - Communicates with LLM API endpoints                                       |
+-----------------------------------------------------------------------------+
```

### Boundary Invariants
- The Context Builder **never generates prompt strings**, system instructions, or markdown prompt wrappers.
- The prompt layer **never accesses upstream adapters** or modifies policy minimization decisions.

---

## 28. Model Follow-Up / Data-Access Requests

The planning model is strictly prohibited from autonomously expanding its own data access perimeter:

```
MODEL OUTPUT IS ADVISORY PROPOSAL, NEVER DATABASE QUERY AUTHORITY
```

1. **No Autonomous Exploration**: If a model generates output requesting additional context (e.g. *"I need all employee addresses and bank details to schedule exams"*), that text has zero authority.
2. **Deterministic Gating**: Any subsequent context request must originate from authorized upstream orchestration, pass through the provider-neutral `ContextAssemblyRequest` interface, and undergo the full 9-step policy precedence check.
3. **No Dynamic SQL Generation**: The model is architecturally prevented from generating SQL queries, schema probes, or relational joins.

---

## 29. Failure and Degraded-State Matrix

The Context Builder deterministically maps integration anomalies to provider-neutral outcomes:

| Anomaly / Trigger Condition | Severity | Resulting Assembly Status | Action Taken |
| :--- | :--- | :--- | :--- |
| **Missing / unverified organization scope** | Critical | `BLOCKED` | Reject request immediately; zero queries issued (`BLK-01`). |
| **Required source adapter unavailable** | Critical | `BLOCKED` | Halt context assembly; emit structured failure diagnostic. |
| **Upstream schema incompatible (drift)** | Critical | `BLOCKED` | Halt queries against affected category; emit security alert. |
| **Requested category deferred / unsupported** | High | `BLOCKED_OR_DEGRADED` | Block if category is mandatory for goal; omit with warning if optional. |
| **Policy denial on restricted field** | Medium | `PROCEED_WITH_OMISSION` | Omit field; record in `omissions`; fail closed if field was critical. |
| **Missing required entity (e.g. session)** | Critical | `BLOCKED` | Halt assembly; do not synthesize or fabricate missing entity. |
| **Stale required context** | High | `BLOCKED_OR_DEGRADED` | Halt planning if freshness is critical; otherwise omit or degrade per policy. |
| **Stale optional context** | Low | `DEGRADED_OR_OMIT` | Include context with warning in `warnings` or omit per policy. |
| **Unavailable cohort aggregate** | High | `DEGRADED_OR_BLOCKED` | Block if headcount is mandatory; otherwise omit with warning. |
| **Conflicting source records** | High | `REQUIRES_CLARIFICATION_OR_BLOCKED` | Do not silently merge; record conflict in `unresolved_questions`. |
| **Suspicious text / prompt injection** | High | `DEGRADED_OR_BLOCKED` | Quarantine/omit suspicious text; emit security audit evidence. |

---

## 30. Production Blockers

The Context Builder contract strictly preserves the active Phase 0 architectural blockers:

### Production Blocker BLK-01: Tenant / Organization Scoping Unresolved
- **Fact**: Upstream MySQL `iredu` lacks tenant, school, or campus partitioning columns across all 247 tables.
- **Architectural Mandate**: **Live production context retrieval against the company MySQL database remains strictly blocked** until an authoritative organization/tenant scoping mechanism is established.
- **Permitted Development Scope**: Synthetic, mock, or single-institution test environments may supply synthetic scope references (e.g. `<synthetic_organization_scope_ref>`) for development and testing.

### Scheduling Resource Production Gate BLK-02: Physical Resource Authority Unresolved
- **Fact**: Upstream MySQL `iredu` lacks canonical physical academic room masters, room capacities, and equipment inventories.
- **Architectural Mandate**: Unresolved physical room authority blocks production capabilities requiring **authoritative physical-resource scheduling** (e.g. automated examination room allocation or seat assignment).
- **Narrow Impact**: BLK-02 does **not** block general Task Intelligence planning, operational WBS generation, curricular timeline planning, or staff role assignment.

---

## 31. Audit Evidence

Future implementation of the Context Builder MUST produce auditable, append-only evidence for every context assembly execution:

### Audit Evidence Fields
- `context_request_id` and distributed `correlation_id`.
- `authenticated_actor_reference` and `organization_scope_reference`.
- `goal_reference` and `bounded_goal_scope`.
- Explicit list of `requested_categories`, `approved_categories`, `included_categories`, and `omitted_categories`.
- Sources and adapters consulted, including adapter IDs and source versions.
- Governance versions applied (`mapping_version`, `access_policy_version`, `contract_version`).
- Freshness evaluation outcomes and degraded warnings.
- Summary counts: model-visible field count, validation-only field count, aggregate count, redaction count.
- Final `assembly_status` (`READY`, `DEGRADED`, `BLOCKED`, or `REQUIRES_CLARIFICATION`).

> [!CAUTION]
> Future Context Builder implementation MUST NOT retain prohibited raw source data, passwords, banking information, or individual student PII merely for auditing purposes.

---

## 32. Versioning

Every `ContextAssemblyResult` is pinned to explicit governance versions:

1. **Mapping Version**: Identifies the approved Task 0G.2 source mapping version applied (e.g. `1.2.0`).
2. **Access Policy Version**: Identifies the approved Task 0G.3 AI Context Access Policy version applied (e.g. `1.0.0`).
3. **Adapter Contract Version**: Identifies the upstream adapter contract version (e.g. `1.0.0`).
4. **Context Builder Contract Version**: Identifies this Context Builder contract version (`1.0.0`).

If runtime configuration supports multiple concurrent policy versions in Phase 1, version selection must be explicit, auditable, and immutable per request.

---

## 33. Synthetic Exam Walkthrough (`E7-REF-EXAM-001`)

### Structural / Synthetic Walkthrough Only
> [!NOTE]
> This walkthrough is a **purely synthetic and structural example** illustrating provider-neutral Context Builder execution for scenario `E7-REF-EXAM-001`: *"Plan and conduct semester examinations"*. It contains zero real company data, zero production identifiers, and zero unverified operational constraints. Symbolic placeholders (e.g. `<normalized_class_ref>`, `<opaque_staff_ref>`) are used throughout.

#### Step 1: Context Assembly Request Received
```json
{
  "context_request_id": "<globally_unique_request_id>",
  "correlation_id": "<distributed_tracing_correlation_id>",
  "authenticated_actor_reference": "<authorized_service_actor>",
  "organization_scope_reference": "<synthetic_organization_scope_ref>",
  "goal_reference": "E7-REF-EXAM-001",
  "bounded_goal_scope": "Plan and conduct semester examinations for target cohort",
  "requested_context_categories": [
    "organization_context",
    "academic_context",
    "subject_context",
    "staff_resource_context",
    "commitment_context",
    "calendar_context",
    "aggregate_context"
  ],
  "normalized_selectors": {
    "normalized_academic_session_ref": "<normalized_session_ref>",
    "normalized_class_refs": ["<normalized_class_ref>"],
    "normalized_department_refs": ["<normalized_dept_ref>"]
  },
  "temporal_scope": {
    "planning_window_start": "<planning_window_start_utc>",
    "planning_window_end": "<planning_window_end_utc>"
  },
  "mapping_version": "1.2.0",
  "access_policy_version": "1.0.0"
}
```

#### Step 2: Scope & Capability Validation
- The request uses a synthetic organization scope (`<synthetic_organization_scope_ref>`) for Phase-0 testing only. This does NOT demonstrate or resolve production tenant scoping; `BLK-01` remains active for live MySQL production retrieval.
- Requested categories evaluated against adapter capabilities: all 7 categories confirmed `SUPPORTED`.
- Unrequested categories omitted by default.

#### Step 3: Adapter Coordination & Source Bundle Ingestion
- Context Builder dispatches bounded queries to registered ERP adapter.
- Adapter returns a provider-neutral normalized `SourceContextBundle` containing academic, subject, staff, commitment, calendar, and aggregate data.

#### Step 4: Policy Enforcement & Minimization
- **Academic Context**: Filtered strictly to `<normalized_class_ref>` in `<normalized_session_ref>`. Unrelated cohorts dropped.
- **Subject Context**: Filtered to subjects mapped to target cohorts (`<subject_ref_1>`, `<subject_ref_2>`).
- **Staff Context**: Filtered to relevant department context. Passwords, salaries, banking, and phone numbers dropped at policy gate. Human names masked to opaque tokens (`<opaque_staff_ref_1>`, `<opaque_staff_ref_2>`). RBAC roles quarantined to validation evidence.
- **Commitment Context**: Filtered strictly to published timetable commitments within `<planning_window_start>` to `<planning_window_end>`.
- **Calendar Context**: Holiday dates tagged as `INFORMATIONAL_DATE`.
- **Aggregate Context**: Cohort student counts accepted (`<cohort_ref_A>` = `<aggregate_cohort_count_A>`). Zero individual student records ingested.

#### Step 5: Validation Evidence Separation
- Internal source identifiers, approved timing metadata, and foreign key bindings routed to `validation_context`. Completely omitted from `model_context`.

#### Step 6: Output Assembly (`ContextAssemblyResult`)
```json
{
  "model_context": {
    "academic_cohorts": [
      { "cohort_token": "<cohort_ref_A>", "class_name": "<class_label>", "section_name": "A" },
      { "cohort_token": "<cohort_ref_B>", "class_name": "<class_label>", "section_name": "B" }
    ],
    "curriculum_subjects": [
      { "subject_token": "<subject_token_1>", "subject_name": "<subject_label_1>", "type": "Theory" },
      { "subject_token": "<subject_token_2>", "subject_name": "<subject_label_2>", "type": "Theory" }
    ],
    "relevant_staff_context": [
      { "opaque_token": "<opaque_staff_ref_1>", "qualification": "<qualification_label_1>", "work_exp": "<exp_label_1>", "department": "<dept_label>" },
      { "opaque_token": "<opaque_staff_ref_2>", "qualification": "<qualification_label_2>", "work_exp": "<exp_label_2>", "department": "<dept_label>" }
    ],
    "published_commitments": [
      { "staff_token": "<opaque_staff_ref_1>", "day_of_week": "<day_label>", "start_time": "<time_start>", "end_time": "<time_end>", "is_known_commitment": true }
    ],
    "informational_calendar_dates": [
      { "title": "<event_label>", "start_date": "<date_start>", "end_date": "<date_end>", "is_informational_date": true }
    ],
    "cohort_aggregates": {
      "<cohort_ref_A>": "<aggregate_cohort_count_A>",
      "<cohort_ref_B>": "<aggregate_cohort_count_B>"
    }
  },
  "validation_context": {
    "external_source_mappings": {
      "<opaque_staff_ref_1>": { "source_system": "mysql_iredu", "external_id": "<ext_staff_id_1>" },
      "<opaque_staff_ref_2>": { "source_system": "mysql_iredu", "external_id": "<ext_staff_id_2>" }
    }
  },
  "aggregates": {
    "cohort_headcounts": {
      "<cohort_ref_A>": "<aggregate_cohort_count_A>",
      "<cohort_ref_B>": "<aggregate_cohort_count_B>"
    }
  },
  "provenance": {
    "context_request_id": "<globally_unique_request_id>",
    "source_system": "mysql_iredu",
    "mapping_version": "1.2.0",
    "access_policy_version": "1.0.0"
  },
  "omissions": [
    { "field": "staff.name", "reason": "CONDITIONAL_IDENTITY_DEFAULT_MASKED" },
    { "field": "staff.roles", "reason": "VALIDATION_ONLY_EXCLUDED_FROM_MODEL" }
  ],
  "warnings": [],
  "unresolved_questions": [],
  "assembly_status": "READY"
}
```

#### Step 7: Downstream Execution
- The Task Intelligence prompt layer serializes `model_context` into the model prompt.
- The planner LLM proposes an examination work breakdown structure and may propose candidate assignment suggestions using approved opaque references (`<opaque_staff_ref_1>`).
- Operational eligibility is NOT inferred by the model; suggestions remain non-authoritative.
- Deterministic validators subsequently verify task dependencies, resolve opaque references through validated E-7 canonical/source-reference mappings and authorized validation evidence, and perform DAG validation and CPM/slack/critical-path analysis.

---

## 34. Future Implementation Boundary

When Phase 1 development commences, the Context Builder will be implemented within the following modular package structure:

```
app/
└── task_intelligence/
    └── context/
        ├── builder.py          # ContextBuilder orchestration service
        ├── contracts.py        # Request & result DTO contracts (ContextAssemblyRequest, Result)
        ├── policy.py           # Access policy evaluation & field whitelist clamping
        ├── minimization.py     # Goal-scoped filtering & selector matching
        ├── provenance.py       # Provenance stamping, omissions tracking & audit logging
        └── selectors.py        # Provider-neutral normalized scope representations
```

### Module Responsibilities
- **`builder.py`**: Coordinates context assembly workflow, validates organization scope, queries adapter capabilities, and manages assembly status.
- **`contracts.py`**: Declares strongly typed DTOs for requests, results, statuses, and warnings.
- **`policy.py`**: Implements 9-step policy precedence, evaluates Task 0G.3 access rules, and masks conditional identities.
- **`minimization.py`**: Filters domain collections strictly to goal-scoped cohorts, departments, and temporal bounds.
- **`provenance.py`**: Stamps audit metadata, records omitted fields, and tracks freshness evaluations.
- **`selectors.py`**: Normalizes client selectors into provider-neutral domain references.

> [!NOTE]
> None of the files listed above are created in Phase 0. This structure is documented strictly to guide future implementation.

---

## 35. Acceptance Criteria

Future Phase 1 implementation of the Context Builder must satisfy the following acceptance criteria:

1. **Provider Neutrality**: Context Builder MUST depend exclusively on provider-neutral adapter interfaces and domain contracts.
2. **Category Clamping**: Only the 7 approved context categories may be assembled.
3. **Authentication Decoupling**: Context Builder MUST NOT perform user authentication.
4. **Scope Enforcement**: Live context assembly MUST fail closed if `organization_scope_reference` is missing or unverified (`BLK-01`).
5. **No Arbitrary Queries**: Context Builder MUST reject raw SQL, table names, column names, and arbitrary database queries.
6. **No Autonomous Model Expansion**: Advisory model follow-up requests MUST pass through authorized orchestration and deterministic policy checks.
7. **Maximum Surface Clamping**: Model-visible context MUST never exceed the 28 `PLANNER_CONTEXT_ALLOWED` fields.
8. **Goal-Scoped Minimization**: Unrelated cohorts, departments, and temporal records outside authorized purpose MUST be excluded.
9. **Zero Raw Rows**: Raw database rows and internal validation evidence MUST be isolated from model context.
10. **Zero Student PII**: Individual student records MUST be excluded; only approved adapter-derived aggregates may be exposed.
11. **Descriptive Staff Attributes**: Staff metadata MUST be treated as descriptive only; no operational eligibility may be inferred.
12. **Timetable Non-Inference**: Absence of a timetable record MUST NOT be treated as general staff availability.
13. **Informational Calendar Dates**: Calendar events MUST NOT be treated as automated hard blackouts without explicit business policy.
14. **Deterministic Reference Resolution**: Opaque references MUST resolve through validated E-7 canonical/source mappings, not raw database primary keys.
15. **No Silent Fabrication**: Missing entities, headcounts, or room metadata MUST NOT be fabricated.
16. **Untrusted Text Handling**: Upstream text literals MUST be treated as untrusted data without instruction authority.
17. **No Prompt Formatting**: Context Builder MUST output structured data only, not formatted prompt templates.
18. **Auditable Decisions**: Context policy decisions, inclusions, omissions, and statuses MUST be fully auditable without storing prohibited raw PII.

---

## 36. Unresolved Decisions

The following architectural gates and ambiguous semantics are preserved from Tasks 0G.1 through 0G.4:

| Gate Identifier | Title | Classification | Impact on Context Builder |
| :--- | :--- | :--- | :--- |
| **`UBQ-TI-01`** | **Tenant / Organization Scoping Blocker** | `ACTIVE_PRODUCTION_BLOCKER` (`BLK-01`) | Upstream MySQL lacks tenant partitioning. Live production context retrieval remains blocked until an authoritative scoping mechanism is defined. |
| **`UBQ-TI-02`** | **Active and Soft-Delete Flag Semantics** | `UNRESOLVED_SEMANTIC_GATE` | Active/status flag representations vary across tables. Allowed active flag semantics must follow authoritative business mappings; where status semantics are unresolved, Context Builder must report ambiguity or a business-confirmation gate; no universal `is_active` meaning is invented. |
| **`UBQ-TI-03`** | **Examination Subsystem Authority** | `UNRESOLVED_SEMANTIC_GATE` | Upstream contains both Legacy (`exams`, `exam_schedules`) and CBSE (`cbse_exams`) tables. Curriculum workflow authority must be formally designated before exam context integration. |
| **`UBQ-TI-04`** | **Physical Academic Resource Authority** | `SCHEDULING_RESOURCE_PRODUCTION_GATE` (`BLK-02`) | MySQL lacks room capacity and equipment data. Blocks physical exam room scheduling until resource ownership is determined. |
| **`UBQ-TI-05`** | **Staff Availability vs Historical Attendance** | `UNRESOLVED_SEMANTIC_GATE` | Forward availability relies on future leave requests and timetable commitments. Daily punch logs (`staff_attendance`) are excluded. |

---

## 37. Next Task Recommendation

### Completed Task
- **Task 0G.5 / 0G.5A — Provider-Neutral Context Builder Contract**: Completed. Provider-neutral context assembly, policy enforcement, goal-scoped minimization, data isolation, implementation-neutral semantics, and audit specifications fully documented.

### Recommended Next Task
- **Task 0G.6 — Phase-0 External Integration Inventory & Provider Decisions**:
  Consolidate and finalize the integration inventory across all institutional enterprise systems (MySQL ERP, UniTime scheduling, calendar providers, notification gateways) and formalize provider binding decisions.

> [!IMPORTANT]
> **Task 0G.6 was not started.** Work ceases at the conclusion of Task 0G.5A pending review.
