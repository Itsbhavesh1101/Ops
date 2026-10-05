# Phase 2B Baseline & Architectural Sign-Off

**Document Identifier:** `E7-DOC-P2B-004`  
**Version:** `1.1.0` (Phase 2B Configuration, Compliance & Evaluation Closure)  
**Status:** COMPLETE / FROZEN (Phase 2B Core Engineering)  
**Canonical Phase 2 Status:** `IN_PROGRESS_PENDING_PROVIDER_BENCHMARK`  
**Alembic Head:** `0008` (11 Application Tables)  

---

## 1. Executive Summary

Phase 2B completes the AI half of Task Intelligence while preserving the non-authoritative boundary between AI generation, deterministic validation, and canonical persistence.

### Key Milestones Achieved:
1. **PydanticAI & OpenAI Adoption:** Bounded dependency family (`pydantic-ai-slim>=2.50.0`, `openai>=3.0.0`). Full license provenance recorded in `third_party/licenses/` and `docs/OPEN_SOURCE_COMPLIANCE_REGISTER.md`.
2. **Zero Hardcoded LLM Defaults:** External configuration enforced via `PlannerSettings`. Production `Settings` defaults are all `None`. Attempting to instantiate an unconfigured planner fails with `PlannerConfigurationError`. App startup remains completely safe without AI configuration.
3. **Official Sarvam Route Verification:** Verified via `docs.sarvam.ai` as `POST /v1/chat/completions` at `https://api.sarvam.ai/v1` with model ID `sarvam-105b`. Retained as benchmark candidate metadata only (never production default). Live access remains `BLOCKED_EXTERNAL_ACCESS`.
4. **Context Builder Runtime:** Strict 7-category contract, exactly 28 allowed planner fields, student data strictly aggregate-only, opaque staff references.
5. **Targeted Repair & Deterministic Validation:** Every model proposal is verified by `PlanValidator` and NetworkX. Machine-readable validation issues drive bounded targeted repair before persistence.
6. **Observability & Privacy:** Safe low-cardinality OpenTelemetry metrics; zero secrets, prompts, or raw responses logged.
7. **Reconciled AI Evaluation Harness:** Evaluates all 5 Phase-0 gold-set cases against source-required metrics: `structured_output_validity`, `required_task_coverage`, `invalid_dependency_rate`, `hallucinated_entity_rate`, `human_correction_rate_status` (`UNAVAILABLE_NO_HUMAN_REVIEW_LABELS`), `cost` (`unavailable`), `fallback_behavior` (4 modes), and `acceptance_threshold` (`REQUIRES_BUSINESS_OR_ADR_APPROVAL`).
8. **Database Stability:** Zero new migrations introduced. Database head remains strictly at `0008` with 11 tables.

---

## 2. Preserved Boundaries & Deferred Scope

- `IDENTITY_TENANT_CREDENTIAL_PERSISTENCE = DEFERRED_REQUIRES_EXPLICIT_DATA_MODEL_TASK`
- `TASK_INTELLIGENCE_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY`
- `TASK_INTELLIGENCE_ASYNC_PLANNER_RUNTIME = DEFERRED_PENDING_EXPLICIT_JOB_EXECUTION_CONTRACT`
- `MYSQL_PRODUCTION_CONTEXT_RETRIEVAL = DISABLED_BY_GOVERNANCE`
- `BLK-01 = LIVE_MYSQL_PRODUCTION_SCOPE_UNRESOLVED`
- `BLK-02 = PHYSICAL_RESOURCE_SCHEDULING_GATE`
- `DATABASE_INSTRUMENTATION = DEFERRED_PENDING_QUERY_PRIVACY_VALIDATION`
- `TRACE_CONTEXT_BROKER_PROPAGATION = DEFERRED_CONTRACT_CHANGE`
- `SARVAM_LIVE_BENCHMARK = BLOCKED_EXTERNAL_ACCESS`
- `FALLBACK_MODEL_SELECTION = UNRESOLVED_ADR_BENCHMARK_DECISION`
- `PRODUCTION_LLM_SELECTION = PENDING_BENCHMARK_AND_ADR`
- `Phase 3 was not started.`
