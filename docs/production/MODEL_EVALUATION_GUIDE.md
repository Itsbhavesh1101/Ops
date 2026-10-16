# E-7 Operations Engine — Model Evaluation, AI Security & Privacy Guide

**Document Version:** 1.0.0  
**Phase:** Phase 8F Canonical Production Documentation  
**Module:** Task Intelligence AI Planner (`app/task_intelligence`)  
**Framework:** PydanticAI (Provider-Neutral)  

---

## 1. Architectural Philosophy: The Non-Authoritative Model Boundary

**Governance Classification:** `ORIGINAL_REQUIREMENT` & `APPROVED_ADR_DECISION` (ADR-002, ADR-008)

The E-7 Operations Engine uses Large Language Models strictly for **plan synthesis proposals**, never for autonomous operational execution.

### 1.1 Core Safety Invariants

```
Upstream Context (ERP/Calendar)
        │
        ▼ (Strict 28-Field Allowlist & Privacy Redaction)
Planner Context Builder
        │
        ▼ (System Instructions + Zero Tool Access)
PydanticAI Planner (Model Provider)
        │
        ▼ (Enforced JSON Schema: PlanSchema)
Deterministic Plan Validator (DAG Cycles, Duration Bounds, Work Categories)
        │
        ├── Invalid ──► [Bounded Repair Loop (max: 2)] ──► Fallback Model / Failure
        │
        ▼ Valid
Draft Proposal Record (Status: DRAFT / GENERATED)
        │
        ▼ (Explicit Human Operator Inspection)
POST /api/v1/plans/{plan_id}/approve  ──►  Authoritative Plan Baseline (is_baseline = true)
```

1. **Zero Autonomous Execution:** The model cannot directly create databases, trigger external APIs, or publish schedules.
2. **Zero Tool Surface (`PLANNER_TOOL_SURFACE = NONE`):** The planner agent is provided **no callable tools**, function calls, or external network hooks.
3. **Deterministic Output Validation:** Every model proposal is evaluated by algorithmic validators (`PlanValidator`) before entering the database.
4. **Mandatory Human Approval Boundary:** Only human operators can transition a proposal to an authoritative baseline.

---

## 2. Privacy Engineering & Data Sanitization

**Governance Classification:** `ORIGINAL_REQUIREMENT` (Phase 8B)

The context builder (`app/task_intelligence/context/builder.py`) scrubs all input data before prompt injection:

### 2.1 The Frozen 28-Field Allowlist (`FROZEN_PLANNER_CONTEXT_ALLOWED_FIELDS`)
Only data from the following 28 fields across 5 categories can enter the planner prompt:

1. **Calendar Context (8 fields):**
   - `annual_calendar.from_date`, `annual_calendar.to_date`, `annual_calendar.description`
   - `events.event_title`, `events.event_description`, `events.start_date`, `events.end_date`, `events.event_type`
2. **Academic Context (3 fields):**
   - `classes.class`, `sections.section`, `sessions.session`
3. **Organization Context (2 fields):**
   - `department.department_name`, `staff_designation.designation`
4. **Staff Resource Context (4 fields):**
   - `staff.qualification`, `staff.work_exp`, `staff.name`, `staff.surname`
5. **Subject & Timetable Context (11 fields):**
   - `subjects.name`, `subjects.type`, `subjects.code`
   - `subject_groups.name`, `subject_groups.description`, `subject_group_class_sections.description`
   - `subject_timetable.day`, `subject_timetable.time_from`, `subject_timetable.time_to`
   - `subject_timetable.start_time`, `subject_timetable.end_time`

### 2.2 PII Protection Rules
- **Students: AGGREGATE ONLY:** Individual student names, student IDs, grades, medical records, addresses, phone numbers, or attendance are strictly forbidden. Only synthetic aggregate counts (e.g. `student_count: 45`) are permissible.
- **Staff Opaque References:** Real employee IDs and sensitive staff attributes are replaced with synthetic opaque tokens (e.g. `StaffRef_1`, `StaffRef_2`).
- **Forbidden PII Keyword Blocklist:** Any field matching `FORBIDDEN_PII_KEYWORDS` (`password`, `salary`, `bank`, `tax`, `ssn`, `national_id`, `medical`, `biometric`) is dropped with a security log event.

---

## 3. Dual Evaluation Framework

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR`

Model testing is bifurcated into two strictly segregated operating modes:

### 3.1 Mode 1: DETERMINISTIC_OFFLINE_EVAL (Mandatory CI Gate)
- **CI Dependency:** **Zero paid/live model calls.** Requires zero internet connectivity and zero external API keys.
- **Mechanism:** Evaluates planner logic using PydanticAI `TestModel` or local synthetic response providers.
- **Evaluation Corpus:**
  - `tests/security/ai/test_prompt_injection.py`: Resistance against direct system prompt override attempts.
  - `tests/security/ai/test_indirect_injection.py`: Resistance against malicious payload injection inside ERP subject names or Google Calendar event titles.
  - `tests/security/ai/test_canary_leakage.py`: Verification that synthetic secret canary tokens placed in runtime memory are never mirrored in output plans.
  - `tests/security/ai/test_cross_tenant_isolation.py`: Verification that Tenant A context is never accessible or exposed in Tenant B planner calls.
  - `tests/security/ai/test_malicious_schema_attacks.py`: Defense against structural JSON schema attacks (e.g. cycle injection, infinite duration, buffer exhaustion).

### 3.2 Mode 2: OPTIONAL_LIVE_PROVIDER_EVAL (Staging Benchmark)
- **Execution:** Manually executed or run in staging when `E7_LLM_API_KEY` is provided.
- **Provider-Neutral Settings:**
  - `llm_provider`: e.g. `openai_compatible`
  - `llm_primary_model`: Configurable model name
  - `llm_fallback_model`: Configurable fallback model
  - `llm_timeout_seconds`: Bounded inference timeout (default: 30.0s)
  - `llm_max_retries`: Maximum network retries (default: 2)
  - `llm_max_repair_attempts`: Bounded validation repair iterations (default: 2)
- **Metrics Evaluated:**
  - First-pass JSON schema compliance rate.
  - Cycle-free DAG validity rate.
  - Repair loop convergence rate.
  - End-to-end proposal latency.

---

## 4. Deterministic Validation & Bounded Repair

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR`

When model output is received, `PlanValidator` performs algorithmic checks:

1. **DAG Acyclicity (Topological Sort):** Verifies that task dependencies contain no directed cycles.
2. **Milestone Association:** Verifies that all referenced milestones exist and target dates fall after task predecessors.
3. **Work Category Consistency:** Enforces approved taxonomy (`CURRICULUM`, `INFRASTRUCTURE`, `INVIGILATION`, `ASSESSMENT`).
4. **Duration Bounds:** Flags negative, zero, or unfeasibly large task durations.

### Bounded Repair Loop
If `PlanValidator` finds issues:
1. Formulates a targeted repair prompt listing specific algorithmic validation failures.
2. Submits repair prompt back to the model.
3. Bounded to `llm_max_repair_attempts` (default: 2 iterations). If repair fails after the ceiling is reached, planner raises `PlannerInferenceError` and fails closed without corrupting the database.

---

## 5. Telemetry & Observability Safety

**Governance Classification:** `ENGINEERING_DEFAULT`

AI telemetry follows strict low-cardinality and privacy standards (`app/task_intelligence/telemetry.py`):
- **Metrics:**
  - `e7_task_intelligence_planner_calls_total` (labels: `provider`, `model`, `status`)
  - `e7_task_intelligence_planner_duration_seconds` (histogram)
  - `e7_task_intelligence_repair_attempts_total` (counter)
  - `e7_task_intelligence_fallback_invocations_total` (counter)
- **Privacy Invariants:**
  - Prompt text, response text, user messages, and tenant IDs are **strictly forbidden** as metric labels.
  - Raw secret credentials and API keys are zero-logged.
