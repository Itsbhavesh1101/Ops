# E-7 Company MySQL ERP Adapter Contract

**Contract Version**: `1.0.0`  
**Contract Identifier**: `E7-CTR-INT-001`  
**Status**: `PHASE_0_DESIGN_BASELINE`  
**Source System**: `mysql_iredu` (Company MySQL Upstream ERP)  
**Consumer**: `E-7 Context Builder Engine` (Provider-Neutral Integration Port)  
**Operating Mode**: `READ_ONLY` (Normative Requirement)  
**Authoritative Baselines**:  
- [MYSQL_SCHEMA_INVENTORY.md](file:///F:/operations-engine/docs/integrations/MYSQL_SCHEMA_INVENTORY.md) (Task 0G.1/0G.1A)  
- [MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md](file:///F:/operations-engine/docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md) (Task 0G.2/0G.2A/0G.2B)  
- [AI_CONTEXT_ACCESS_POLICY.md](file:///F:/operations-engine/docs/integrations/AI_CONTEXT_ACCESS_POLICY.md) (Task 0G.3/0G.3A/0G.3B)  
- [CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md](file:///F:/operations-engine/docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md) (Task 0E)  
- [IMPLEMENTATION_GUARDRAILS.md](file:///F:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md) (Task 0C.1)

---

## 1. Purpose

This document defines the provider-neutral integration boundary and architectural contract through which the E-7 Operations Engine will consume approved institutional domain context from the company upstream MySQL database (`iredu`).

The contract establishes:
1. **Architectural Isolation**: Complete decoupling between upstream relational schemas and downstream Task Intelligence planning logic.
2. **Provider Neutrality**: A stable, abstract interface (`ERPSourceAdapter`) capable of accommodating alternative institutional ERP or Student Information Systems (SIS) without altering E-7 core reasoning components.
3. **Strict Read-Only Posture**: Prevention of unauthorized mutations, write-backs, DDL execution, or arbitrary SQL execution against upstream institutional databases.
4. **Governed Data Minimization**: Enforcement of field-level projection boundaries established in Task 0G.2 and Task 0G.3 before data leaves the adapter integration perimeter.
5. **Deterministic Boundary**: Explicit handoff contracts between adapter normalization, Context Builder policy minimization, deterministic validation engines, and model-visible prompt generation.

> [!IMPORTANT]
> This document represents **contract design and architectural specification only**. It contains zero runtime code, zero database connection logic, zero SQL queries, and zero ORM models. Phase 1 implementation has not started.

---

## 2. Scope and Non-Scope

### In-Scope
- Formal specification of adapter responsibilities and non-responsibilities.
- Provider-neutral request (`ContextSourceRequest`) and response (`SourceContextBundle`) contracts.
- Contract semantics for the 7 approved context categories: `organization_context`, `academic_context`, `subject_context`, `staff_resource_context`, `commitment_context`, `calendar_context`, and `aggregate_context`.
- Upstream external identifier handling, opaque reference tokenization, and provenance tracking.
- Freshness classification, staleness semantics, health states, and error taxonomy.
- Schema compatibility verification and fail-closed schema drift behavior.
- Security, least-privilege database user principles, and PII quarantine rules.
- Operational gating: Production Blocker `BLK-01` (Tenant/Org Scope) and Scheduling Gate `BLK-02` (Physical Resource Authority).
- Conceptual walkthrough of the reference examination planning scenario (`E7-REF-EXAM-001`) using synthetic, structural representations.
- Recommended target implementation directory layout.

### Out-of-Scope (Strictly Prohibited in Phase 0)
- Writing Python runtime code, interface classes, Pydantic DTO models, or SQLAlchemy ORM mappings.
- Writing SQL queries, stored procedures, or executing live queries against MySQL.
- Selecting specific Python MySQL driver libraries (e.g. `asyncmy`, `aiomysql`, `mysqlclient`, `PyMySQL`).
- Defining concrete numeric pool sizes, timeout seconds, retry counts, or cache TTL intervals.
- Designing or authorizing write-back, data synchronization back into MySQL, or state mutation mechanisms.
- Authorizing arbitrary SQL execution or LLM-generated database query facilities.
- Prescribing a specific identifier generation algorithm or concrete UUID version.
- Prescribing an exact database grant table count in Phase 0.
- Modifying existing Task 0G.1, 0G.2, or 0G.3 documentation or machine-readable JSON artifacts.
- Modifying architectural decision records (ADRs) or test fixtures.
- Starting Phase 1 application development or Task 0G.5 Context Builder implementation.

---

## 3. Architectural Boundary

The E-7 Operations Engine enforces a strict unidirectional pipeline from raw institutional ERP data to sanitized planning context. Downstream Task Intelligence components must never be tightly coupled to upstream persistence technologies.

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
| E-7 MYSQL ERP ADAPTER (Provider-Specific Integration Boundary)               |
| - Connection lifecycle & credentials isolation                              |
| - Pre-reviewed parameterized queries (strictly no arbitrary SQL)            |
| - Field projection whitelist enforcement (28 planner / 74 validation fields)|
| - Raw data normalization & opaque reference generation (<opaque_staff_ref>) |
| - Approved adapter-derived aggregate computation (zero raw student rows)    |
| - Provenance stamping & schema compatibility verification                   |
+-----------------------------------------------------------------------------+
                                       |
                                       | [2] Provider-Neutral SourceContextBundle
                                       v
+-----------------------------------------------------------------------------+
| E-7 CONTEXT BUILDER (Provider-Neutral Assembly & Policy Minimization Layer)  |
| - Goal-scoped category filtering & context minimization                     |
| - AI Context Access Policy evaluation & field masking                       |
| - Conditional name exposure decisions (workflow-approved vs opaque default) |
| - Context audit evidence emission & prompt packet assembly                  |
+-----------------------------------------------------------------------------+
                                       |
                                       | [3] Minimal Model-Visible Context
                                       v
+-----------------------------------------------------------------------------+
| E-7 TASK INTELLIGENCE (Planner LLM Reasoning Engine)                        |
| - Goal interpretation & hierarchical WBS decomposition                      |
| - Dependency proposal & operational risk analysis                           |
| - NO knowledge of MySQL tables, columns, foreign keys, SQL, or credentials  |
+-----------------------------------------------------------------------------+
                                       |
                                       | [4] Proposed PlanSchema Payload
                                       v
+-----------------------------------------------------------------------------+
| DETERMINISTIC VALIDATORS & GRAPH SERVICES (Backend Verification)             |
| - PlanSchema structural & policy validation                                 |
| - Reference resolution through validated E-7 canonical/source mappings      |
| - Kahn's algorithm DAG cycle detection & CPM critical-path calculation      |
+-----------------------------------------------------------------------------+
                                       |
                                       | [5] Persist Approved Plan & Mappings
                                       v
+-----------------------------------------------------------------------------+
| POSTGRESQL SYSTEM OF RECORD (Canonical Persisted Operational State)         |
| - Canonical entity persistence (Plans, Tasks, Dependencies, Milestones)     |
| - Storage of canonical/source mapping records & audit ledgers               |
+-----------------------------------------------------------------------------+
```

### Decoupling Rules
1. **No Schema Leakage**: Task Intelligence planners must never receive, request, or parse MySQL table names, column names, raw primary keys, or SQL dialect fragments.
2. **No Prompt Generation in Adapter**: The adapter transforms relational records into provider-neutral typed domain contracts (`SourceContextBundle`). It does not format LLM prompts.
3. **No Direct Model Access**: Upstream databases are never exposed to LLM agents or autonomous query tools.

---

## 4. Responsibility Matrix

The separation of operational responsibilities across system boundaries is absolute:

| Boundary / Component | Primary Responsibilities | Strict Non-Responsibilities |
| :--- | :--- | :--- |
| **Upstream MySQL (`iredu`)** | • Houses legacy institutional ERP records.<br>• Serves read-only queries from adapter. | • Does not own E-7 plan state or execution metadata.<br>• Does not validate operational constraints.<br>• Does not format context for AI consumption. |
| **E-7 MySQL Adapter** | • Connection lifecycle & credential isolation.<br>• Approved parameterized query execution.<br>• Field projection whitelist enforcement.<br>• Normalization into provider-neutral DTOs.<br>• Upstream identifier mapping & opaque tokenization.<br>• Approved aggregate computation execution.<br>• Source provenance & freshness reporting.<br>• Schema compatibility verification. | • Does not determine goal-level context requirements.<br>• Does not assemble final LLM prompts.<br>• Does not evaluate user permissions or AI minimization policy.<br>• Does not persist canonical operational plans.<br>• Does not execute write-backs or DDL. |
| **E-7 Context Builder** | • Evaluates operational goal to select required context categories.<br>• Enforces AI Context Access Policy (whitelisting, masking).<br>• Goal-scoped context minimization (filtering to relevant cohorts).<br>• Decides conditional identity exposure (opaque vs human names).<br>• Assembles minimal model-visible prompt payload.<br>• Generates context audit trail and minimization evidence. | • Does not connect directly to MySQL or issue SQL queries.<br>• Does not normalize raw database rows.<br>• Does not perform DAG validation or CPM scheduling.<br>• Does not persist canonical entities to PostgreSQL. |
| **Task Intelligence** | • Interprets operational goal and context payload.<br>• Generates hierarchical Work Breakdown Structure (WBS).<br>• Proposes operational dependencies, durations, and milestones.<br>• Identifies potential operational risks and mitigation advice. | • Does not know database tables, schemas, or SQL.<br>• Does not connect to databases or call external APIs.<br>• Does not perform deterministic DAG cycle detection.<br>• Does not persist plans or execute operational actions. |
| **Deterministic Validators & Graph Services** | • Validates PlanSchema compliance and entity references.<br>• Resolves opaque references through validated E-7 canonical/source-reference mappings.<br>• Validates dependency integrity and detects cycles (Kahn's algorithm).<br>• Computes Early/Late Start/Finish, Total Float, and Critical Path (CPM).<br>• Verifies institutional constraints before persistence. | • Does not generate plans or task descriptions.<br>• Does not communicate with LLM endpoints.<br>• Does not normalize raw MySQL data. |
| **PostgreSQL System of Record** | • Canonical persistence of verified plans, tasks, and dependencies.<br>• Manages transactional outbox/inbox events.<br>• Stores canonical/source mapping records linking external IDs to canonical E-7 entity references.<br>• Stores immutable context and planning audit logs. | • Does not perform algorithmic CPM calculations or cycle checks.<br>• Does not query upstream MySQL directly. |

---

## 5. Adapter Principles

All future implementations of the MySQL ERP adapter must strictly adhere to the following architectural principles:

1. **Read-Only Enforced (`READ_ONLY`)**:
   The adapter MUST operate exclusively in read-only mode. It is architecturally prohibited from issuing `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, or mutating stored procedure calls. Any future write-back capability requires a separate functional requirement, ADR, and independent integration contract.
2. **Provider-Neutral Interface**:
   The adapter exposes a generic ERP integration port (`ERPSourceAdapter`). Downstream consumers depend solely on provider-neutral domain abstractions, allowing drop-in substitution of other ERP/SIS sources.
3. **Absolute Prohibition of Arbitrary SQL**:
   The adapter interface accepts only structured, strongly typed request objects (`ContextSourceRequest`). It MUST reject raw SQL strings, custom `WHERE` clauses, arbitrary column projections, and dynamic query text generated by LLMs.
4. **Field-Specific Projection Only (No `SELECT *`)**:
   Upstream queries MUST specify explicit column projections matching the approved whitelist (28 planner-visible and 74 deterministic-validation fields, plus approved aggregate sources). Indiscriminate table dumps and wildcards are strictly prohibited.
5. **Default Fail-Closed Posture**:
   The adapter MUST fail safely when encountering anomalies. It must never fabricate, synthesize, or default missing entities, staff records, subjects, timetable commitments, room capacities, or organizational scopes.
6. **Minimum Necessary Retrieval (Bounded Scoping)**:
   Context retrieval MUST be explicitly bounded by operational goals, academic sessions, target classes, or temporal planning windows. Unbounded "dump all ERP data" requests are rejected at the contract boundary.
7. **Complete Source Provenance**:
   Every normalized record returned by the adapter MUST be stamped with immutable provenance metadata detailing source system, source entity type, primary key reference, query timestamp, and mapping version.
8. **No Direct LLM/Database Coupling**:
   Database connection handles, SQL drivers, raw rows, and relational metadata remain encapsulated inside the adapter boundary and are never exposed to Task Intelligence reasoning components.

---

## 6. Adapter Capability Manifest

The adapter capability manifest formalizes what institutional domain context can and cannot be retrieved from the upstream MySQL source:

```
Contract Vocabulary:
- SUPPORTED: Fully backed by mapped upstream tables and approved by access policy.
- PARTIALLY_SUPPORTED: Backed by source tables, but subject to semantic gaps or strict field restrictions.
- DEFERRED: Present in upstream database, but deferred to future phases (e.g. Phase 2/4).
- UNSUPPORTED: Completely absent from the upstream MySQL schema.
- REQUIRES_BUSINESS_CONFIRMATION: Blocked by unresolved institutional policy or subsystem authority ambiguity.
```

| Capability Name | Status | Upstream Source Tables | Model-Visible Fields | Validation Fields | Architectural Notes |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **`organization_context`** | `SUPPORTED` | `department`, `staff_designation` | 2 | 4 | Institutional hierarchy and job designations for role matching. Descriptive only; does not establish individual operational capability or task eligibility. |
| **`academic_context`** | `SUPPORTED` | `sessions`, `classes`, `sections`, `class_sections` | 3 | 10 | Academic year temporal bounds and target student cohorts. Scoped strictly to goal-relevant classes. |
| **`subject_context`** | `SUPPORTED` | `subjects`, `subject_groups`, `subject_group_subjects`, `subject_group_class_sections` | 6 | 11 | Course catalogue, subject codes, instructional types (Theory vs Practical), and curricular groupings. |
| **`staff_resource_context`** | `SUPPORTED` | `staff` | 4 | 6 | Candidate staff roster, qualifications, experience, and opaque tokens (`<opaque_staff_ref>`). Names conditional; descriptive only. RBAC roles (`roles`, `staff_roles`) are validation-only and must not be promoted into planner-visible capability context. |
| **`commitment_context`** | `SUPPORTED` | `subject_timetable` | 5 | 5 | Published teaching schedules defining known busy periods. Scheduled period = busy; empty slot != available. |
| **`calendar_context`** | `SUPPORTED` | `annual_calendar`, `events` | 8 | 7 | Informational holiday dates and academic event descriptions. Informational dates do not constitute automated hard blackouts. |
| **`aggregate_context`** | `SUPPORTED` | `students`, `student_session` | 0 | 6 | Cohort headcounts computed inside adapter. Zero individual student records exposed; student rows are not exposed to the model for the current Task Intelligence scope and policy version. |
| **`authoritative_future_availability`** | `DEFERRED` | `staff_leave_request`, `staff_attendence_schedules` | 0 | 0 | Future staff availability modeling deferred to Phase 2/4. Daily punch attendance (`staff_attendance`) is not exposed to the model for the current Task Intelligence scope and policy version. |
| **`physical_room_master`** | `UNSUPPORTED` | None | 0 | 0 | MySQL `iredu` lacks a canonical academic room master. `room_no` fields are unconstrained text strings. |
| **`room_capacity`** | `UNSUPPORTED` | None | 0 | 0 | Upstream schema contains no seating or room capacity fields. Must not be synthesized or defaulted. |
| **`equipment_capability`** | `UNSUPPORTED` | None | 0 | 0 | Upstream schema contains no laboratory or instructional equipment inventory or status. |
| **`authoritative_invigilator_eligibility`** | `UNSUPPORTED` | None | 0 | 0 | No invigilation qualification, certification, or examination duty eligibility model exists in MySQL `iredu`. |
| **`authoritative_exam_resource_rules`** | `REQUIRES_BUSINESS_CONFIRMATION` | `cbse_exams`, `exam_groups` | 0 | 0 | Operational authority between CBSE and Legacy exam subsystems unresolved (UBQ-TI-03). Physical room scheduling gated by BLK-02. |

---

## 7. Provider-Neutral Adapter Operations

The logical interface `ERPSourceAdapter` defines the provider-neutral port through which E-7 interacts with institutional data sources:

```
ERPSourceAdapter (Logical Port Interface)
├── get_capabilities() -> AdapterCapabilityManifest
├── get_health() -> AdapterHealthStatus
├── fetch_context(request: ContextSourceRequest) -> SourceContextBundle
└── resolve_external_references(request: ExternalReferenceResolutionRequest) -> ExternalReferenceResolutionResult
```

### Operation Specifications

#### 1. `get_capabilities`
- **Purpose**: Query supported, deferred, and unsupported context categories and retrieval features.
- **Input Contract**: None (parameterless query).
- **Output Contract**: `AdapterCapabilityManifest` describing context categories, freshness categories, and active production gates.
- **Production Scope Required**: `false` (can be queried in unauthenticated or diagnostic mode).

#### 2. `get_health`
- **Purpose**: Retrieve connectivity, schema compatibility, and configuration state without exposing credentials or driver internals.
- **Input Contract**: None (parameterless diagnostic probe).
- **Output Contract**: `AdapterHealthStatus` containing state enum, ping latency, schema match confirmation, and warning messages.
- **Production Scope Required**: `false`.

#### 3. `fetch_context`
- **Purpose**: Execute bounded, read-only parameterized queries to retrieve, validate, and normalize approved institutional domain data.
- **Input Contract**: `ContextSourceRequest` containing bounded scope selectors, requested categories, organization scope, temporal bounds, and policy versions.
- **Output Contract**: `SourceContextBundle` containing normalized DTOs, validation evidence, aggregates, provenance, and degraded state warnings.
- **Production Scope Required**: `true` (live queries blocked without verified organization scope; BLK-01).

#### 4. `resolve_external_references`
- **Purpose**: Validate existence, active status, and identity currency of external source entity references without exposing raw external identifiers to planning models.
- **Input Contract**: `ExternalReferenceResolutionRequest` containing entity types, external IDs, and organization scope reference.
- **Output Contract**: `ExternalReferenceResolutionResult` mapping valid, missing, or altered external references.
- **Production Scope Required**: `true`.

---

## 8. Context Source Request Contract

The `ContextSourceRequest` represents a provider-neutral specification of institutional context required for operational planning:

```json
{
  "request_id": "<globally_unique_request_id>",
  "correlation_id": "<distributed_tracing_correlation_id>",
  "organization_scope_reference": "<authorized_organization_scope_reference>",
  "goal_scope": "E7-REF-EXAM-001: Plan and conduct semester examinations",
  "requested_context_categories": [
    "organization_context",
    "academic_context",
    "subject_context",
    "staff_resource_context",
    "commitment_context",
    "calendar_context",
    "aggregate_context"
  ],
  "entity_selectors": {
    "normalized_academic_session_ref": "<normalized_session_ref>",
    "normalized_class_refs": ["<normalized_class_ref_1>", "<normalized_class_ref_2>"],
    "normalized_department_refs": ["<normalized_dept_ref_1>"],
    "normalized_subject_refs": [],
    "opaque_staff_refs": []
  },
  "temporal_context": {
    "as_of": "<iso_8601_utc_timestamp>",
    "planning_window_start": "<planning_window_start_utc>",
    "planning_window_end": "<planning_window_end_utc>"
  },
  "mapping_policy_version": "1.2.0",
  "context_access_policy_version": "1.0.0",
  "freshness_tolerance": "DAILY_OR_PERIODIC"
}
```

### Contract Rules
1. **Mandatory Production Scope**: `organization_scope_reference` is strictly mandatory for live production database queries. If unresolved or absent, the adapter MUST immediately reject the request with `SCOPE_UNRESOLVED` (enforcing Production Blocker `BLK-01`). Synthetic/mock test environments may supply synthetic scope identifiers.
2. **Provider-Neutral Selectors**: Entity selectors MUST use normalized references (`normalized_academic_session_ref`, `normalized_class_refs`, `opaque_staff_refs`). They must not depend on raw MySQL primary keys or database-specific column formats.
3. **Explicit Category Whitelist**: The request MUST explicitly list desired categories. Unrequested categories must never be fetched or returned.
4. **Bounded Selectors Required**: The adapter MUST reject unbounded or wildcard requests (e.g. requesting all historical sessions or all global records).
5. **Forbidden Request Concepts**:
   - Raw SQL strings, clauses, or syntax fragments.
   - Arbitrary table or column names.
   - LLM prompt templates or prompt instructions.
   - Direct database credentials or host connection parameters.

---

## 9. Source Context Bundle Contract

The `SourceContextBundle` is the provider-neutral data structure returned by the adapter to the Context Builder:

```
SourceContextBundle
├── normalized_context (Category-partitioned normalized domain DTOs)
│   ├── organization_units [...]
│   ├── academic_cohorts [...]
│   ├── curriculum_subjects [...]
│   ├── staff_roster [...]
│   ├── published_commitments [...]
│   └── calendar_events [...]
├── validation_evidence (Internal verification data - NOT model visible)
│   ├── raw_source_identifiers { <opaque_staff_ref>: { table: "staff", id: "<external_id>" } }
│   ├── relational_bindings { class_section_ref: "<external_id>", session_ref: "<external_id>" }
│   └── schedule_bell_intervals [...]
├── aggregates (Approved mathematical headcounts - zero raw student rows)
│   └── cohort_headcounts { "<normalized_cohort_ref>": <aggregate_count> }
├── provenance (Source system, timestamps, versions, freshness classes)
│   ├── source_system: "mysql_iredu"
│   ├── retrieved_at: "<iso_8601_utc_timestamp>"
│   ├── mapping_version: "1.2.0"
│   └── category_freshness { ... }
├── warnings_or_degraded_state (Diagnostics, missing entities, stale warnings, unresolved status)
│   └── degraded_warnings [...]
└── external_reference_mappings (Internal mapping metadata for PostgreSQL resolution)
    └── mappings [...]
```

### Distinction Between Source Bundle and Model Prompt
- **The Adapter does NOT produce an LLM prompt**: The `SourceContextBundle` is a structured, strongly typed domain object containing both model-candidate data and internal validation evidence.
- **The Context Builder owns Model Visibility**: The Context Builder processes the bundle, filters out `validation_evidence` and `external_reference_mappings`, applies goal-scoped minimization and tokenization, and formats the minimal model-visible prompt payload.

---

## 10. Context Category Contracts

### 1. `organization_context`
- **Upstream Tables**: `department`, `staff_designation`.
- **Model-Visible Fields (2)**: `department_name`, `designation_title`.
- **Validation Fields (4)**: `department.id`, `department.is_active`, `staff_designation.id`, `staff_designation.is_active`.
- **Semantics**: Represents institutional administrative structure and job titles. Used for task role matching. Descriptive context only; does not establish individual operational capability or task eligibility.

### 2. `academic_context`
- **Upstream Tables**: `sessions`, `classes`, `sections`, `class_sections`.
- **Model-Visible Fields (3)**: `session_name`, `class_name`, `section_name`.
- **Validation Fields (10)**: `sessions.id`, `sessions.status`, `classes.id`, `classes.is_active`, `sections.id`, `sections.is_active`, `class_sections.id`, `class_sections.class_id`, `class_sections.section_id`, `class_sections.is_active`.
- **Semantics**: Establishes academic terms and cohort structures. Bounded strictly to active sessions and selected target grade cohorts.

### 3. `subject_context`
- **Upstream Tables**: `subjects`, `subject_groups`, `subject_group_subjects`, `subject_group_class_sections`.
- **Model-Visible Fields (6)**: `subject_name`, `subject_code`, `subject_type`, `group_name`, `class_section_binding`, `subject_group_binding`.
- **Validation Fields (11)**: `subjects.id`, `subjects.is_active`, `subject_groups.id`, `subject_groups.session_id`, `subject_groups.is_active`, `subject_group_subjects.id`, `subject_group_subjects.subject_group_id`, `subject_group_subjects.subject_id`, `subject_group_class_sections.id`, `subject_group_class_sections.subject_group_id`, `subject_group_class_sections.class_section_id`.
- **Semantics**: Details the course catalogue, distinguishing Theory vs Practical courses and curricular groupings.

### 4. `staff_resource_context`
- **Upstream Tables**: `staff`.
- **Model-Visible Fields (4)**: `qualification`, `work_exp`, `name` (conditional), `surname` (conditional).
- **Validation Fields (6)**: `staff.id`, `staff.employee_id`, `staff.department`, `staff.designation`, `staff.is_active`, `staff.updated_at`.
- **Semantics**: Provides candidate human resources. Default model exposure is strictly an opaque reference token (`<opaque_staff_ref>`). Real names exposed only upon explicit workflow requirement. Descriptive fields provide context only (see Section 12). RBAC roles (`roles`, `staff_roles`) are validation-only and MUST NOT be promoted into planner-visible capability context.

### 5. `commitment_context`
- **Upstream Tables**: `subject_timetable`.
- **Model-Visible Fields (5)**: `day_of_week`, `start_time`, `end_time`, `room_reference_unvalidated`, `subject_reference`.
- **Validation Fields (5)**: `subject_timetable.id`, `subject_timetable.session_id`, `subject_timetable.class_section_id`, `subject_timetable.staff_id`, `subject_timetable.subject_group_subject_id`.
- **Semantics**: Details scheduled instructional periods representing known busy commitments. Empty periods do not establish general availability (see Section 14).

### 6. `calendar_context`
- **Upstream Tables**: `annual_calendar`, `events`.
- **Model-Visible Fields (8)**: `holiday_title`, `holiday_type`, `holiday_start_date`, `holiday_end_date`, `event_title`, `event_type`, `event_start_date`, `event_end_date`.
- **Validation Fields (7)**: `annual_calendar.id`, `annual_calendar.session_id`, `annual_calendar.is_active`, `events.id`, `events.is_active`, `events.event_for`, `events.updated_at`.
- **Semantics**: Informational holiday and event schedules. Dates provide operational awareness and do not constitute automated hard constraints (see Section 15).

### 7. `aggregate_context`
- **Upstream Tables**: `students`, `student_session`.
- **Model-Visible Fields (0)**: Zero individual columns permitted.
- **Validation Fields (6)**: `students.id`, `students.is_active`, `student_session.id`, `student_session.session_id`, `student_session.class_id`, `student_session.section_id`.
- **Semantics**: Approved adapter-derived mathematical headcounts (e.g. cohort student count). Raw student records, PII, contacts, and personal information are not exposed to the model for the current Task Intelligence scope and policy version (see Section 13).

---

## 11. Field Projection & Data-Minimization Contract

The adapter enforces field-specific projection boundaries established in Tasks 0G.2 and 0G.3:

```
+-----------------------------------------------------------------------------+
| UPSTREAM MYSQL ROW (e.g. `staff` containing 54 columns)                     |
+-----------------------------------------------------------------------------+
                                       |
                                       | [1] Adapter Projection Whitelist (10 cols)
                                       v
+-----------------------------------------------------------------------------+
| ADAPTER INTERNAL REPRESENTATION                                             |
| - Model-Candidate: qualification, work_exp, name, surname                   |
| - Validation-Only: id, employee_id, department, designation, is_active,      |
|   updated_at                                                                |
| - Restricted Fields DROPPED (passwords, salary, bank_ac, phone, etc.)       |
+-----------------------------------------------------------------------------+
                                       |
                                       | [2] Normalized Source DTO
                                       v
+-----------------------------------------------------------------------------+
| SOURCE CONTEXT BUNDLE                                                       |
| - normalized_context.staff_roster:                                          |
|   { opaque_id: "<opaque_staff_ref>", qualification: "<qual>",               |
|     work_exp: "<exp>", name: "<name>", surname: "<surname>" }               |
| - validation_evidence:                                                      |
|   { "<opaque_staff_ref>": { external_ref: "<ext_id>", status: "<val>" } }   |
+-----------------------------------------------------------------------------+
                                       |
                                       | [3] Context Builder Policy & Minimization
                                       v
+-----------------------------------------------------------------------------+
| MODEL-VISIBLE PROJECTION (Default Anonymized View)                          |
| - { staff_token: "<opaque_staff_ref>", qualification: "<qual>",              |
|     work_exp: "<exp>" }                                                     |
|   (Human names withheld unless workflow policy explicitly permits)          |
+-----------------------------------------------------------------------------+
```

### Projection Invariants
1. **Zero Field Reclassification**: The approved baseline of **28 PLANNER_CONTEXT_ALLOWED** fields, **74 DETERMINISTIC_VALIDATION_ONLY** fields, and **2 AGGREGATE_ONLY** source tables is frozen. No field may be reclassified.
2. **Deny-by-Default**: Any column present in MySQL tables that is not explicitly whitelisted is dropped immediately upon database fetch and never instantiated in memory.
3. **Quarantine of Restricted Personnel Data**: Personal financial data (`bank_account_no`, `basic_salary`), security credentials (`password`, `verification_code`), and private contact details (`email`, `contact_no`, `emergency_contact_no`, `local_address`) are not exposed to the model for the current Task Intelligence scope and policy version. Credentials and secrets remain categorically prohibited from model context.

---

## 12. Staff Context Semantics

In strict accordance with Task 0G.3B, staff attributes provided by the adapter represent **descriptive context only**:

```
STAFF DESCRIPTIVE CONTEXT != OPERATIONAL ELIGIBILITY OR CAPABILITY
```

1. **Descriptive Non-Authority**:
   - `staff.qualification` describes academic background. It does **not** establish certification, regulatory clearance, or examination board eligibility.
   - `staff.work_exp` provides seniority context. It does **not** prove institutional authorization or competence for specialized roles.
   - `department` and `staff_designation` indicate organizational placement. They do **not** grant task approver authority or supervisory powers.
   - Past assignments or timetable allocations do **not** establish future availability or operational assignment suitability.
2. **Exclusion of RBAC Roles from Planner Capability Context**:
   - Application and RBAC roles (`roles`, `staff_roles`) are validation-only and MUST NOT be promoted into planner-visible staff capability context. They do not establish eligibility, professional skill, operational capability, approval authority, or assignment suitability.
3. **Identity Exposure Default**:
   - Candidate staff members are presented to Task Intelligence using **opaque normalized tokens** (e.g. `<opaque_staff_ref>`).
   - Human names (`name`, `surname`) are conditional fields. They are included in the `SourceContextBundle` but masked by the Context Builder unless the specific operational workflow explicitly requires and authorizes human-readable identity.

---

## 13. Aggregate Data Contract

Student information is governed by a strict aggregate-only contract:

1. **Approved Adapter-Derived Aggregate Computation**:
   All student-related context (e.g. cohort size, registered student count per class-section) MUST be computed via approved adapter-derived aggregate computation inside the trusted adapter perimeter.
2. **Zero Student Row Exposure**:
   No individual student record from `students` or `student_session` may cross the adapter boundary. The adapter DTO exposes only scalar integers representing headcounts. Individual student rows and columns are not exposed to the model for the current Task Intelligence scope and policy version.
3. **Complete PII Lockdown**:
   All 67 individual student columns—including names, birth dates, guardian contacts, addresses, medical records, and national identifiers—are strictly non-model-visible for the current Task Intelligence scope and policy version.
4. **Governance-Controlled Suppression**:
   Differential privacy and small-cohort suppression policies remain governance-controlled at the Context Builder layer. The adapter provides raw mathematical counts alongside cohort size metadata; no arbitrary suppression threshold is hardcoded in the adapter interface.

---

## 14. Commitment / Availability Semantics

The `subject_timetable` source table provides historical and recurring schedule information:

```
SCHEDULED TIMETABLE PERIOD = KNOWN BUSY COMMITMENT
NO TIMETABLE ENTRY != AUTHORITATIVE FUTURE AVAILABILITY
```

1. **Known Busy Window**:
   Where a record in `subject_timetable` links a staff member or class-section to a specific day and time window, it constitutes a `KNOWN_BUSY_COMMITMENT`. Planners should avoid scheduling conflicting operational activities during these windows.
2. **Absence of Record is Not Free Availability**:
   The absence of a timetable entry indicates only that no teaching commitment is recorded in `subject_timetable`. It does **not** establish that the staff member is on duty, present on campus, not assigned to administrative duties, or unencumbered by approved leave.
3. **Forward Availability Requires Dedicated Subsystems**:
   Authoritative staff availability modeling requires forward leave integration (`staff_leave_request`), shift rosters, and approved duty models, which are deferred to future phases (`authoritative_future_availability` is `DEFERRED`). Historical attendance daily punch logs (`staff_attendance`) are not exposed to the model for the current Task Intelligence scope and policy version (historical attendance does not establish future availability).

---

## 15. Calendar Semantics

Data from `annual_calendar` and `events` provides operational awareness of institutional schedules:

```
INFORMATIONAL CALENDAR DATE != DETERMINISTIC HARD BLACKOUT
```

1. **Informational Awareness**:
   Calendar entries (holidays, vacations, sports days, cultural festivals) inform Task Intelligence of institutional milestones.
2. **No Automated Scheduling Hard Stops**:
   The adapter MUST NOT classify calendar entries as `HARD_BLACKOUT` or `FORBIDDEN_DATE`. In academic operations, activities such as examination paper evaluation, maintenance, invigilation setup, or remedial workshops frequently occur during student vacations or declared holidays.
3. **Policy-Driven Constraints**:
   A calendar entry can only become an unbreachable scheduling blackout if an explicit, authorized E-7 business policy or human administrator designates it as such during plan validation.

---

## 16. External Identifier & Mapping Contract

The adapter contract specifies a strict separation between upstream external identifiers and E-7 canonical identifiers:

```
Upstream Source Identity (Internal to Adapter):
- source_system: "mysql_iredu"
- source_entity_type: "staff" | "classes" | "subjects" | ...
- external_id: "<upstream_source_id>"

E-7 Internal Planner Representation:
- opaque_token: "<opaque_staff_ref>" | "<opaque_class_sec_ref>"

E-7 Canonical Entity Reference (PostgreSQL System of Record):
- canonical_id: "<canonical_entity_ref>" (E-7 canonical entity reference)
```

### Identifier Invariants
1. **Opaque Planner Tokens**: Model-visible context uses deterministic, opaque tokens to prevent LLM hallucination of numerical database relationships and to mask internal database primary keys.
2. **Canonical Persistence in PostgreSQL**: E-7 PostgreSQL stores canonical/source mapping records linking `(source_system, source_entity_type, external_id)` to canonical E-7 entity references. The exact physical mapping table/schema is an implementation decision and is not defined in Task 0G.4.
3. **Opaque Reference Resolution**: Opaque model-visible references are resolved through validated E-7 canonical/source-reference mappings (identifying a canonical E-7 entity reference, an approved external-source mapping, or validated internal integration metadata), not directly to raw database primary keys.
4. **Missing Reference Detection**: The adapter operation `resolve_external_references` validates whether external IDs exist and remain active according to approved business mappings. If an external record has been deleted or deactivated upstream, it is reported as `UNRESOLVED` or `INACTIVE` in the validation evidence.

---

## 17. Provenance Contract

Every normalized domain entity returned within a `SourceContextBundle` MUST include an immutable provenance stamp:

```json
{
  "provenance": {
    "source_system": "mysql_iredu",
    "source_entity_type": "<source_entity_type>",
    "source_reference": "<external_id>",
    "retrieved_at": "<iso_8601_utc_timestamp>",
    "source_updated_at": "<optional_iso_8601_utc_timestamp>",
    "source_freshness_class": "DAILY_OR_PERIODIC",
    "mapping_version": "1.2.0",
    "adapter_contract_version": "1.0.0"
  }
}
```

### Provenance Rules
1. **Mandatory Fields**: `source_system`, `source_entity_type`, `source_reference`, `retrieved_at`, `source_freshness_class`, `mapping_version`, and `adapter_contract_version` are mandatory for every normalized entity.
2. **Conditional Source Timestamps**: `source_updated_at` MUST be populated **only** when the underlying MySQL table natively exposes an update timestamp (e.g. `staff.updated_at`, `events.updated_at`). The adapter MUST never invent or synthesize timestamps for tables lacking them.
3. **Internal Boundary**: Provenance metadata is retained in the `SourceContextBundle` for audit and validation purposes. It is omitted from the minimal model-visible prompt payload by the Context Builder.

---

## 18. Freshness / Staleness Contract

The adapter adopts the standard Task 0G.2 freshness classification vocabulary:

| Freshness Class | Applicable Domain Sources | Staleness Characteristics & Planning Impact |
| :--- | :--- | :--- |
| **`STATIC_OR_SLOW`** | `department`, `staff_designation`, `sessions`, `classes`, `sections`, `subjects`, `subject_groups` | Curricular and organizational masters change infrequently (term/year boundaries). Daily cache snapshots are acceptable. |
| **`DAILY_OR_PERIODIC`** | `staff`, `subject_timetable`, `annual_calendar`, `events`, cohort headcounts | Operational rosters, published timetables, and calendars change periodically. Cached data older than operational tolerance should be refreshed or flagged degraded. |
| **`NEAR_REAL_TIME`** | *None in initial MySQL sources* | High-frequency operational state (e.g. active live facility bookings). Not supported by initial MySQL integration. |
| **`EVENT_DRIVEN_PREFERRED`** | *None in initial MySQL sources* | CDC or webhook-driven change notifications. Not supported by static MySQL dump. |
| **`UNKNOWN`** | Unmapped or unclassified tables | Must not be queried or returned. |

### Staleness Semantics
- **Degraded vs Unavailable**: If the adapter cannot reach upstream MySQL but holds an unexpired cached snapshot, it may return the snapshot accompanied by a `STALE_SOURCE_CONTEXT` warning in `warnings_or_degraded_state`.
- **Policy Enforcement by Context Builder**: The Context Builder determines whether stale context is acceptable based on goal criticality. High-risk operational goals may reject stale context and fail closed.
- **No Invented Timers**: Concrete cache TTLs, cache engines (e.g. Redis), and polling cron schedules are not defined here; they are implementation details for Phase 1.

---

## 19. Adapter Health Contract

The adapter exposes provider-neutral health states through `get_health()`:

| Health State | Operational Definition | Consumer Action |
| :--- | :--- | :--- |
| **`HEALTHY`** | Required source connectivity and compatibility checks succeed; required organization scope is resolved. | Full live context retrieval permitted. |
| **`DEGRADED`** | Source is reachable, but optional context is incomplete or freshness is insufficient for some non-critical use. | Retrieval proceeds with structured warnings; Context Builder evaluates tolerance. |
| **`UNAVAILABLE`** | Required source cannot currently be reached or read. | Retrieval fails immediately; fail-closed. |
| **`MISCONFIGURED`** | Required adapter/source configuration is invalid or authentication cannot be established. | Adapter initialization halted; configuration review required. |
| **`SCHEMA_INCOMPATIBLE`** | Required source contract no longer matches mapping expectations (e.g. required whitelisted column missing, renamed, or altered in datatype). | Adapter enters fail-closed state; live queries blocked. |
| **`SCOPE_UNRESOLVED`** | Production organization scope has not been authoritatively resolved (`BLK-01`). | Live retrieval blocked. |

> [!CAUTION]
> Health check responses must **never** leak database passwords, usernames, hostnames, IP addresses, or internal connection strings. Implementation-specific details (such as socket connections, connection pool metrics, or TLS handshake diagnostics) are internal to adapter infrastructure and are not part of the provider-neutral health contract.

---

## 20. Error Contract

The adapter MUST standardize all integration failures into provider-neutral error categories. Raw MySQL driver exceptions MUST be caught and mapped at the adapter boundary:

| Error Category | Trigger Condition | Fail-Closed Behavior |
| :--- | :--- | :--- |
| **`SOURCE_UNAVAILABLE`** | Upstream MySQL database connection timeout, socket error, or network partition. | Return structured failure; abort context retrieval. |
| **`SOURCE_AUTH_FAILURE`** | Database user credentials rejected or insufficient SQL permissions. | Log security alert; abort initialization. |
| **`SOURCE_CONFIGURATION_INVALID`** | Missing mandatory connection parameters or malformed configuration. | Halt adapter startup. |
| **`SCHEMA_INCOMPATIBLE`** | Upstream table/column missing, renamed, or altered in SQL datatype. | Halt queries against affected table; emit schema drift alert. |
| **`SCOPE_UNRESOLVED`** | Production request lacks verified organization scope reference (`BLK-01`). | Reject request immediately; zero database queries issued. |
| **`MAPPING_MISSING`** | External entity cannot be resolved to internal canonical mapping. | Flag entity unresolved in validation evidence. |
| **`ENTITY_NOT_FOUND`** | Bounded selector references an entity that does not exist upstream. | Return empty result for selector; emit warning. |
| **`AMBIGUOUS_SOURCE_RECORD`** | Source contains conflicting data (e.g. contradictory timetable entries). | Flag entity as ambiguous; omit conflicting record from planning. |
| **`STALE_SOURCE_CONTEXT`** | Retrieved snapshot age exceeds operational freshness tolerance. | Return degraded context with warning or abort per policy. |
| **`POLICY_DENIED`** | Request attempts to query an unapproved, restricted, or forbidden field/table. | Reject request with security audit event. |
| **`UNSUPPORTED_CONTEXT_CATEGORY`** | Request specifies a category marked `DEFERRED` or `UNSUPPORTED`. | Reject category; return supported categories only. |
| **`AGGREGATE_UNAVAILABLE`** | Cohort aggregation query failed or returned invalid mathematical count. | Omit aggregate; mark aggregate context unavailable. |

---

## 21. Fail-Closed Behavior

The adapter strictly enforces fail-closed behavior across all operations:

1. **No Silent Fabrication**:
   The adapter MUST never synthesize, guess, or default missing institutional entities. If candidate staff members, academic classes, subjects, or timetable periods are missing from upstream records, the adapter must return structured warnings or empty collections—it must never inject synthetic "dummy" staff or subjects.
2. **No Hallucinated Constraints**:
   The adapter MUST not invent room capacities, equipment specifications, invigilator eligibility criteria, or teacher availability rules.
3. **No Unscoped Production Execution**:
   If an incoming production request lacks an authenticated organization scope reference, the adapter MUST not fall back to querying the entire unpartitioned database. It must fail closed immediately.
4. **Status / Soft-Delete Handling**:
   Lifecycle and status fields (such as `is_active`, `status`) may be carried as validation evidence where approved; their filtering semantics must follow authoritative business mappings. Where status semantics are unresolved, the adapter MUST report ambiguity or a business-confirmation gate; no universal `is_active` meaning is invented in Phase 0.

---

## 22. Schema Compatibility / Drift

The adapter is version-aware and implements strict schema compatibility safeguards:

```
Contract Posture: DENY_UNKNOWN / FAIL_CLOSED_ON_MUTATION
```

1. **Deny-by-Default for New Columns**:
   If an upstream MySQL table is altered to add new columns, the adapter MUST ignore them completely. Projections are strictly explicit, preventing newly added columns from leaking into adapter DTOs or downstream context.
2. **Fail-Closed on Column Mutation**:
   If any whitelisted column (from the 28 planner or 74 validation fields) is dropped, renamed, or modified to an incompatible SQL datatype, the adapter detects the anomaly during startup compatibility verification, transitions to `SCHEMA_INCOMPATIBLE`, and refuses to execute queries against the affected table.
3. **Governance-Controlled Expansion**:
   Expansion of source exposure requires at minimum: schema inventory review, Task 0G.2 mapping revision, Task 0G.3 access-policy revision, security/privacy review where applicable, tests, and governance approval. An ADR is required only when the change alters or creates an architectural decision significant enough to require ADR governance. A new ADR is NOT required for every field-level mapping change.
4. **No Cryptographic Fingerprinting in Phase 0**:
   Concrete DDL hashing or automated migration fingerprinting algorithms are implementation choices reserved for Phase 1.

---

## 23. Security & Least Privilege

The future database user provisioned for the MySQL adapter must operate under strict least-privilege principles:

1. **Minimum Read Privileges**:
   Future production credentials MUST receive the minimum read privileges required by the actually implemented and approved adapter operations.
   The authorized object set MUST be derived from:
   - Task 0G.2 mapping (`MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md`)
   - Task 0G.3 access policy (`AI_CONTEXT_ACCESS_POLICY.md`)
   - Enabled adapter capabilities
   - Reviewed repository operations

   The initial adapter MAY require approved reads from:
   - `REQUIRED_FOR_INITIAL_TI` sources (e.g. academic, organizational, subject, staff, timetable, calendar tables)
   - Validation-only sources actually needed by implemented joins/validation
   - `AGGREGATE_ONLY` sources required for approved aggregate computation (`students`, `student_session`)

   Access must NOT be granted merely because a table exists in the 70-table evaluation set. No exact table-count grant set is prescribed in Phase 0.
2. **Prohibited Permissions**:
   The user must never be granted `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `GRANT`, `TRIGGER`, `LOCK TABLES`, `EXECUTE`, or unrelated administrative privileges.
3. **Credential Isolation**:
   Credentials must be injected into the adapter runtime environment via secure environment variables or secret vaults. Database credentials must **never** be checked into version control, embedded in logs, or exposed to LLM reasoning endpoints.
4. **PII Quarantine**:
   Sensitive personal records (salaries, banking, passwords, national identity numbers, student contacts) are excluded at the SQL projection level.

---

## 24. Connection Configuration Principles

Future Phase 1 implementation of connection management must adhere to the following architectural rules:

1. **Externalized Configuration**:
   Connection parameters (host, port, database name, user, SSL/TLS mode) must be loaded from external configuration settings. No database connection strings or IP addresses may be hardcoded.
2. **Lifecycle Owned by Adapter Infrastructure**:
   The adapter infrastructure encapsulates connection pooling, connection health checking, and connection teardown. Downstream services interact exclusively through the `ERPSourceAdapter` port.
3. **Structured Transient Error Handling**:
   Transient network disconnections or pool exhaustion must be translated into structured adapter errors (`SOURCE_UNAVAILABLE`). Upstream outages must never be converted into synthetic success or fake empty datasets.
4. **No Driver Selection in Phase 0**:
   No specific Python asynchronous or synchronous MySQL driver (e.g. `asyncmy`, `aiomysql`, `mysqlclient`) is selected in this task. Driver selection is deferred to Phase 1 implementation.

---

## 25. Logging & Observability

The adapter MUST emit provider-neutral, privacy-safe telemetry:

1. **Permitted Telemetry**:
   - Request and correlation IDs (`request_id`, `correlation_id`).
   - Adapter identifier and source system name (`mysql_iredu`).
   - Requested context categories and execution duration (latency in milliseconds).
   - Count of candidate records fetched and normalized.
   - Aggregate operations executed (e.g. cohort headcount count).
   - Adapter health state transitions and schema compatibility status.
   - Structured error categories and warning messages.
2. **Strictly Prohibited in Logs**:
   - Database credentials, passwords, or connection URIs containing credentials.
   - Raw database row dumps or unrestricted query result sets.
   - Personal Identifiable Information (PII) including staff contact details or student names.
   - Raw SQL query strings containing embedded unmasked filter literals.

---

## 26. Bounded Retrieval / Performance Principles

Context retrieval from upstream ERP systems must be strictly bounded to prevent resource exhaustion:

1. **No Indiscriminate Full-Database Scans**:
   The adapter MUST not execute unbounded scans across all 247 tables or retrieve global historical archives. Every query must be scoped by academic session, target cohort, department, or temporal window.
2. **Internal Batching and Paging**:
   Where candidate record sets are large (e.g. roster checks across large cohorts), the adapter may implement batching or keyset pagination internally.
3. **No Invented Limits in Phase 0**:
   Concrete batch sizes, maximum row budgets, and timeout seconds are configurable runtime parameters to be established during Phase 1 implementation; no numeric constants are invented here.

---

## 27. Consistency Semantics

Institutional context retrieval requires internal coherence across related entities:

1. **Context Coherence**:
   When fetching related data (e.g. classes, sections, subjects, and timetables for a specific session), the adapter MUST ensure that retrieved records belong to the same coherent academic session and cohort definition.
2. **No Global Distributed Locking**:
   The adapter does not require distributed locking or prolonged global table locks across upstream MySQL. Read-only queries should execute with standard non-blocking transactional consistency.
3. **Observable Inconsistency**:
   If the adapter detects relational incoherence (e.g. a timetable entry referencing an inactive or missing class section), it MUST flag the anomaly in `warnings_or_degraded_state` rather than silently fabricating missing relationships.

---

## 28. Production Scope Requirements

Production deployment is governed by two formal architectural gates:

### Production Blocker BLK-01: Tenant / Organization Scoping Unresolved
- **Fact**: MySQL `iredu` lacks tenant, school, or campus partitioning columns across all 247 tables.
- **Architectural Mandate**: **Live production context retrieval against the company MySQL database remains strictly blocked** until an authoritative tenant/organization scoping mechanism is defined and approved.
- **Development Exception**: Synthetic, mock, or single-institution test environments may supply synthetic scope references (e.g. `<synthetic_org_scope_ref>`) for development and automated testing.

### Scheduling Resource Production Gate BLK-02: Physical Resource Authority Unresolved
- **Fact**: MySQL `iredu` lacks canonical physical academic room masters, room capacities, and equipment inventories.
- **Architectural Mandate**: Unresolved physical room authority blocks production capabilities requiring **authoritative physical-resource scheduling** (e.g. automated examination room allocation or seat assignment).
- **Narrow Impact**: BLK-02 does **not** block general Task Intelligence planning, operational WBS generation, curricular timeline planning, or staff role assignment.

---

## 29. Reference Exam Use Case (`E7-REF-EXAM-001`)

### Structural / Synthetic Example Only
> [!NOTE]
> This walkthrough is a **purely synthetic and structural example** designed to illustrate provider-neutral contract data flow. It contains zero real company data, zero production identifiers, and zero unverified operational constraints. Symbolic placeholders (e.g. `<normalized_class_ref>`, `<opaque_staff_ref>`) are used throughout.

Scenario: `E7-REF-EXAM-001`: *"Plan and conduct semester examinations"*.

### Step 1: Context Source Request (Context Builder -> Adapter)
```json
{
  "request_id": "<globally_unique_request_id>",
  "correlation_id": "<distributed_tracing_correlation_id>",
  "organization_scope_reference": "<authorized_organization_scope_reference>",
  "goal_scope": "E7-REF-EXAM-001: Plan and conduct semester examinations",
  "requested_context_categories": [
    "organization_context",
    "academic_context",
    "subject_context",
    "staff_resource_context",
    "commitment_context",
    "calendar_context",
    "aggregate_context"
  ],
  "entity_selectors": {
    "normalized_academic_session_ref": "<normalized_session_ref>",
    "normalized_class_refs": ["<normalized_class_ref>"]
  },
  "temporal_context": {
    "planning_window_start": "<planning_window_start_utc>",
    "planning_window_end": "<planning_window_end_utc>"
  }
}
```

### Step 2: Upstream Source Fetch & Adapter Normalization
1. **Academic & Subject Query**: Adapter MUST execute bounded read-only queries against academic and subject tables for the target class in the active session.
   - Extracts: Session `<normalized_session_ref>`, Class `<normalized_class_ref>`, Sections `<section_ref_A>` and `<section_ref_B>`, Subjects `<subject_ref_MATH>` (Theory) and `<subject_ref_SCI>` (Theory).
2. **Staff Query**: Adapter MUST execute bounded read-only queries against `staff` filtered by academic department and active status according to approved business mappings.
   - Extracts relevant normalized staff context. Drops passwords, salaries, banking, and personal contact info.
   - Generates opaque reference tokens: `<opaque_staff_ref_1>`, `<opaque_staff_ref_2>`.
3. **Commitment Query**: Adapter MUST query `subject_timetable` for target cohorts during the planning window.
   - Extracts published teaching periods (identifying known busy windows).
4. **Calendar Query**: Adapter MUST query `annual_calendar` and `events`.
   - Extracts informational holiday dates and institutional events.
5. **Aggregate Query**: Adapter MUST perform approved adapter-derived aggregate computation for student cohort headcounts.
   - Computes: Cohort A count = `<aggregate_cohort_count_A>`, Cohort B count = `<aggregate_cohort_count_B>`. Zero individual student rows are extracted or exposed.

### Step 3: Source Context Bundle (Adapter -> Context Builder)
```json
{
  "normalized_context": {
    "academic_cohorts": [
      { "cohort_token": "<cohort_ref_A>", "class_name": "<class_label>", "section_name": "A" },
      { "cohort_token": "<cohort_ref_B>", "class_name": "<class_label>", "section_name": "B" }
    ],
    "curriculum_subjects": [
      { "subject_token": "<subject_token_1>", "subject_name": "<subject_label_1>", "type": "Theory" },
      { "subject_token": "<subject_token_2>", "subject_name": "<subject_label_2>", "type": "Theory" }
    ],
    "staff_roster": [
      { "opaque_token": "<opaque_staff_ref_1>", "qualification": "<qualification_label_1>", "work_exp": "<exp_label_1>", "department": "<dept_label>" },
      { "opaque_token": "<opaque_staff_ref_2>", "qualification": "<qualification_label_2>", "work_exp": "<exp_label_2>", "department": "<dept_label>" }
    ],
    "published_commitments": [
      { "staff_token": "<opaque_staff_ref_1>", "day_of_week": "<day_label>", "start_time": "<time_start>", "end_time": "<time_end>" }
    ],
    "calendar_events": [
      { "title": "<event_label>", "start_date": "<date_start>", "end_date": "<date_end>", "type": "holiday" }
    ]
  },
  "validation_evidence": {
    "source_keys": {
      "<opaque_staff_ref_1>": { "source_table": "staff", "external_id": "<ext_staff_id_1>" },
      "<opaque_staff_ref_2>": { "source_table": "staff", "external_id": "<ext_staff_id_2>" }
    }
  },
  "aggregates": {
    "cohort_headcounts": {
      "<cohort_ref_A>": "<aggregate_cohort_count_A>",
      "<cohort_ref_B>": "<aggregate_cohort_count_B>"
    }
  },
  "provenance": {
    "source_system": "mysql_iredu",
    "retrieved_at": "<iso_8601_utc_timestamp>",
    "mapping_version": "1.2.0",
    "adapter_contract_version": "1.0.0"
  },
  "warnings_or_degraded_state": []
}
```

### Step 4: Context Builder Minimization (Context Builder -> Task Intelligence)
The Context Builder receives the bundle, strips internal validation evidence, applies minimization rules, and presents the clean context packet to the planner LLM:
```json
{
  "goal": "E7-REF-EXAM-001: Plan and conduct semester examinations",
  "cohorts": [
    { "cohort": "<cohort_ref_A>", "student_count": "<aggregate_cohort_count_A>" },
    { "cohort": "<cohort_ref_B>", "student_count": "<aggregate_cohort_count_B>" }
  ],
  "subjects": [
    { "name": "<subject_label_1>", "type": "Theory" },
    { "name": "<subject_label_2>", "type": "Theory" }
  ],
  "candidate_faculty": [
    { "faculty_ref": "<opaque_staff_ref_1>", "qualification": "<qualification_label_1>", "experience": "<exp_label_1>" },
    { "faculty_ref": "<opaque_staff_ref_2>", "qualification": "<qualification_label_2>", "experience": "<exp_label_2>" }
  ],
  "informational_dates": [
    { "event": "<event_label>", "dates": "<date_range_label>", "type": "holiday" }
  ]
}
```

### Operational Reasoning & Eligibility Boundary
- **WBS Decomposition**: The planner LLM decomposes the examination workflow into logical tasks (Paper Setting, Moderation, Printing, Invigilation Roster, Hall Setup, Evaluation, Grade Submission).
- **Candidate Suggestion vs Eligibility**: The planner may propose candidate assignments using opaque tokens (`<opaque_staff_ref_1>`). However, the planner MUST NOT infer that a staff member is operationally eligible, certified, or qualified for an examination role based merely on qualification, designation, work experience, class_teacher assignment, RBAC role, or timetable gap. Operational eligibility remains subject to approved capability/policy mapping, deterministic validation where available, and human administrative review.
- **Deterministic Resolution**: Deterministic validators subsequently verify task dependencies, resolve opaque references through validated E-7 canonical/source-reference mappings (identifying a canonical E-7 entity reference, an approved external-source mapping, or validated internal integration metadata), and calculate critical paths (CPM).

---

## 30. Future Implementation Directory

When Phase 1 development commences, the MySQL adapter will be implemented within the following modular package structure:

```
app/
└── integrations/
    └── erp/
        ├── base.py              # Abstract ERPSourceAdapter port interface & base DTOs
        └── mysql/
            ├── client.py        # Connection lifecycle, pooling, and health ping diagnostics
            ├── repository.py    # Approved parameterized SQL queries (explicit projections)
            ├── schemas.py       # Internal MySQL transport representations & row parsers
            ├── mapper.py        # Transformation of raw MySQL rows into provider-neutral DTOs
            └── context_provider.py # Concrete implementation of ERPSourceAdapter port
```

### Module Responsibilities
- **`base.py`**: Declares abstract base classes (`ERPSourceAdapter`), common request/response DTOs (`ContextSourceRequest`, `SourceContextBundle`), and error hierarchy. Provider-neutral.
- **`client.py`**: Manages low-level database connection pooling, retry logic, timeout enforcement, and ping diagnostics. Handles credential injection.
- **`repository.py`**: Implements pre-reviewed, parameterized queries for approved tables. Zero dynamic or arbitrary SQL.
- **`schemas.py`**: Internal typed schemas representing upstream MySQL row layouts for validation.
- **`mapper.py`**: Converts internal MySQL row schemas into provider-neutral DTOs, generates opaque reference tokens (`<opaque_staff_ref>`), and stamps provenance metadata.
- **`context_provider.py`**: Coordinates repository calls, mapper transformations, aggregate computations, and returns the assembled `SourceContextBundle`.

> [!NOTE]
> None of the files listed above are created in Phase 0. This structure is documented strictly to guide future implementation.

---

## 31. Implementation Acceptance Criteria

Future Phase 1 implementation of the MySQL ERP adapter must satisfy the following acceptance criteria:

1. **Read-Only Enforcement**: The adapter user connection MUST operate in read-only mode with zero mutation privileges.
2. **Port Conformance**: The concrete provider MUST fully implement `ERPSourceAdapter` without leaking MySQL dialect details.
3. **Whitelist Projection**: All database queries MUST explicitly project only approved fields from mapped sources. `SELECT *` is prohibited.
4. **Minimum Read Privileges**: Production credentials must be granted the minimum read privileges required by the actually implemented operations, derived from Tasks 0G.2 and 0G.3.
5. **Zero Arbitrary SQL**: The adapter MUST reject raw SQL strings, dynamic query parameters, and unreviewed `WHERE` clauses.
6. **No Student PII**: No individual student records may cross the adapter boundary. Only approved adapter-derived aggregate headcounts may be exposed.
7. **Descriptive Staff Attributes**: Staff qualifications, experience, and designations MUST be marked descriptive; no operational eligibility may be inferred.
8. **Fail-Closed on Schema Drift**: Any drop, rename, or datatype change of a whitelisted column MUST trigger `SCHEMA_INCOMPATIBLE`.
9. **Credential Security**: Credentials MUST be sourced via environment variables or secret vaults and never logged or exposed.
10. **Production Scope Enforcement**: Live production queries MUST fail closed if `organization_scope_reference` is missing or unverified (`BLK-01`).
11. **Error Categorization**: All internal database exceptions MUST be mapped to provider-neutral error categories.

---

## 32. Unresolved Decisions

The following architectural gates and ambiguous semantics are preserved from Tasks 0G.1, 0G.2, and 0G.3:

| Gate Identifier | Title | Classification | Impact on Adapter |
| :--- | :--- | :--- | :--- |
| **`UBQ-TI-01`** | **Tenant / Organization Scoping Blocker** | `ACTIVE_PRODUCTION_BLOCKER` (`BLK-01`) | Upstream MySQL lacks tenant partitioning. Live production context retrieval remains blocked until an authoritative scoping mechanism is defined. |
| **`UBQ-TI-02`** | **Active and Soft-Delete Flag Semantics** | `UNRESOLVED_SEMANTIC_GATE` | Active/status flag representations vary across tables. Allowed active flag semantics must follow authoritative business mappings; where status semantics are unresolved, the adapter must report ambiguity or a business-confirmation gate; no universal `is_active` meaning is invented. |
| **`UBQ-TI-03`** | **Examination Subsystem Authority** | `UNRESOLVED_SEMANTIC_GATE` | Upstream contains both Legacy (`exams`, `exam_schedules`) and CBSE (`cbse_exams`) tables. Curriculum workflow authority must be formally designated. |
| **`UBQ-TI-04`** | **Physical Academic Resource Authority** | `SCHEDULING_RESOURCE_PRODUCTION_GATE` (`BLK-02`) | MySQL lacks room capacity and equipment data. Blocks physical exam room scheduling until resource ownership is determined. |
| **`UBQ-TI-05`** | **Staff Availability vs Historical Attendance** | `UNRESOLVED_SEMANTIC_GATE` | Forward availability relies on future leave requests and timetable commitments. Daily punch logs (`staff_attendance`) are excluded. |

---

## 33. Next Task Recommendation

### Completed Task
- **Task 0G.4 / 0G.4A — MySQL ERP Adapter Interface & Contract Design**: Completed. Provider-neutral integration boundary, capability manifest, request/response contracts, security rules, and implementation-neutral corrections fully documented.

### Recommended Next Task
- **Task 0G.5 — Provider-Neutral Context Builder Contract**:
  Define the contract and architectural specification for the E-7 Context Builder Engine. Task 0G.5 will formalize how the Context Builder consumes the `SourceContextBundle`, applies goal-scoped minimization and AI Context Access Policy rules, decides conditional identity exposure, assembles minimal model-visible prompt payloads, and emits audit evidence.

> [!IMPORTANT]
> **Task 0G.5 was not started.** Work ceases at the conclusion of Task 0G.4A pending review.
