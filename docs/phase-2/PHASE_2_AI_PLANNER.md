# Phase 2B: PydanticAI Planner Architecture & Model Contract

**Document Identifier:** `E7-DOC-P2B-001`  
**Version:** `1.1.0` (Phase 2B Configuration & Compliance Closure)  
**Status:** COMPLETE / FROZEN (Phase 2B Core Engineering)  
**Module:** `app.task_intelligence.planner`  

---

## 1. Architectural Authority & Boundary Model

The Task Intelligence Engine preserves strict architectural authority separation:
- **PydanticAI Planner (`PydanticAiPlanner`):** Proposes structured `PlanSchema` candidates using validated external AI settings.
- **Deterministic Validators (`PlanValidator`):** Verifies schema compliance, unique temporary IDs, acyclicity, CPM computability, and policy constraints.
- **Canonical PostgreSQL Store:** Stores authoritative E-7 entities (`goals`, `plans`, `tasks`, `task_dependencies`, `milestones`).
- **Human Operations Lead:** Approves and freezes proposals into baseline execution plans.

**Critical Non-Authoritative Invariant:**  
The LLM proposal is strictly non-authoritative. Under no circumstances can model output transition a plan to `VALIDATED`, `APPROVAL_PENDING`, `APPROVED`, or `ACTIVE`. Model output persists as `PlanState.GENERATED`.

---

## 2. External Configuration Invariant & Zero Production Defaults

In accordance with project governance, production Python contains **zero hardcoded LLM defaults**:
- `llm_provider = None` (unset unless externally configured)
- `llm_primary_model = None` (unset unless externally configured)
- `llm_fallback_model = None` (unset unless externally configured)
- `llm_base_url = None` (unset unless externally configured)
- `llm_timeout_seconds = None` (unset unless externally configured)
- `llm_max_retries = None` (unset unless externally configured)
- `llm_max_repair_attempts = None` (unset unless externally configured)

### Dedicated Planner Configuration (`PlannerSettings`)
Located in `app/task_intelligence/planner/config.py`:
- Encapsulates required planner runtime settings (`provider`, `primary_model`, `timeout_seconds`, `max_retries`, `max_repair_attempts`).
- `PlannerSettings.from_app_settings(settings)` raises a clear diagnostic `PlannerConfigurationError` if any required runtime setting is missing.
- Attempting to instantiate `PydanticAiPlanner()` without explicit configuration fails immediately and clearly.
- **Safe Application Startup:** Importing `app.main`, starting API servers, or running background workers does **NOT** require LLM configuration. Configuration is validated strictly upon AI planner instantiation.

### Test Model Isolation
- Tests explicitly construct deterministic models (`TestModel`, `SequenceTestModel`) or pass `PlannerSettings(provider="test", ...)` via test fixtures (`create_test_planner_settings`).
- Silent fallback from production provider to test model has been removed from `provider.py`.

---

## 3. Official Sarvam Route Verification

Sarvam-105B is preserved at the source level as a **Benchmark Candidate** for evaluation bookkeeping.

### Verified Official Provider Metadata:
- **Official Documentation Source:** Sarvam AI Developer Documentation (`docs.sarvam.ai`)
- **API Protocol / Style:** `POST /v1/chat/completions` (OpenAI-compatible)
- **Base URL:** `https://api.sarvam.ai/v1`
- **Model Identifier:** `sarvam-105b` (105B parameter Mixture-of-Experts language model, 128k context)
- **Verification Date:** October 2026
- **Operational Status:** Benchmark candidate metadata only (never production default). Live benchmark remains `BLOCKED_EXTERNAL_ACCESS` until API key is provisioned and ADR is approved.

---

## 4. Dependency Compliance & SDK Necessity

- **PydanticAI (`pydantic-ai-slim>=2.50.0`):**
  - Upstream Repository: `https://github.com/pydantic/pydantic-ai`
  - License: MIT (`third_party/licenses/LICENSE.pydanticai.txt`)
  - Role: Structured agentic plan generation into `PlanSchema`, targeted repair loop execution.
- **OpenAI Python SDK (`openai>=3.0.0`):**
  - Upstream Repository: `https://github.com/openai/openai-python`
  - License: Apache-2.0 (`third_party/licenses/LICENSE.openai.txt`)
  - Necessity Rationale: Directly required as the underlying HTTP wire transport client for PydanticAI's `OpenAIProvider`.
- Both dependencies are recorded in `docs/OPEN_SOURCE_COMPLIANCE_REGISTER.md`.

---

## 5. Bounded Retry, Timeout & Targeted Repair Loop

- **Timeout:** Bounded by externally configured `timeout_seconds`. Timeout raises typed `PlannerTimeoutError`.
- **Retries:** Transport/provider retries bounded by externally configured `max_retries`.
- **Targeted Repair:**
  - Upon deterministic validation failure, the planner initiates bounded targeted repair.
  - Repair prompt formats machine-readable `ValidationIssue` records (code, location, safe message) and the original proposal.
  - Bounded by externally configured `max_repair_attempts`.
  - Every repaired proposal is re-validated through the complete deterministic `PlanValidator`.
  - After exhaustion, the plan remains in its invalid state and returns an `exhausted` outcome.

---

## 6. Prompt-Injection Defenses & Privacy

- Goal inputs, constraints, and external descriptions are delimited by `<<<UNTRUSTED_GOAL_INPUT>>>` and treated as untrusted data literals.
- User/external text cannot override system instructions, alter validation rules, or request database queries.
- Zero credentials or raw prompts are persisted or leaked in error responses.
