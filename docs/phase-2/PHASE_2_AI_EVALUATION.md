# Phase 2B: AI Evaluation & Gold-Set Harness

**Document Identifier:** `E7-DOC-P2B-003`  
**Version:** `1.1.0` (Phase 2B Source Metric Reconciliation)  
**Status:** COMPLETE / FROZEN (Phase 2B Core Engineering)  
**Module:** `tests.ai_eval`  

---

## 1. Gold-Set Inventory & Scope

The evaluation harness operates strictly on the existing Phase-0 AI gold-set fixtures:
- `tests/fixtures/ai_gold_set/TI-GOLD-001.json`
- `tests/fixtures/ai_gold_set/TI-GOLD-002.json`
- `tests/fixtures/ai_gold_set/TI-GOLD-003.json`
- `tests/fixtures/ai_gold_set/TI-GOLD-004.json`
- `tests/fixtures/ai_gold_set/TI-GOLD-005.json`

**Verified Fixture Count:** Exactly **5 gold-set cases**. Zero easier synthetic fixtures created to inflate benchmark scores.

---

## 2. Reconciled Source Metrics & Evaluation Dimensions

The harness evaluates deterministic property-based metrics matching the source specifications:

1. **Structured-Output Validity (`structured_output_validity`):** Conforms to `PlanSchema` with non-empty task list and clean parsing.
2. **Required-Task Coverage (`required_task_coverage`):** Mapped directly from frozen Phase-0 fixture truth (`expected_plan_properties.required_work_categories` with `at_least_one_task_per_required_category: true`). Measures the ratio of required categories covered with actionable tasks (exact wording not scored, preserving semantic flexibility).
3. **Invalid Dependency Rate (`invalid_dependency_rate`):** Evaluates dependency quality using NetworkX and `PlanValidator`. Ratio of invalid dependencies (cycles, unknown IDs, self-references, duplicates) to total dependencies.
4. **Hallucinated Entity Rate (`hallucinated_entity_rate`):** Ratio of unauthorized external resource references to total referenced entities, validated against the authorized Context Builder reference set (`allowed_resource_ids`).
5. **Human Correction Rate (`human_correction_rate_status`):** Status `UNAVAILABLE_NO_HUMAN_REVIEW_LABELS`. Zero fabricated human-correction data; truthfully recorded until real human review annotations exist.
6. **Latency & Token Usage (`latency_ms`, `total_tokens`):** Records actual inference execution latency in milliseconds and tokens consumed.
7. **Cost Metric (`cost_status`, `cost`):** For deterministic `TestModel`, recorded as `cost_status = UNAVAILABLE_FOR_TEST_MODEL` and `cost = "unavailable"`. Zero synthesized currency values.
8. **Fallback Behavior (`fallback_behavior`):** Evaluates all 4 source-required modes:
   - `PRIMARY_SUCCESS`
   - `FALLBACK_SUCCESS`
   - `PRIMARY_FAILURE_NO_FALLBACK`
   - `FALLBACK_FAILURE`
9. **Gold-Set Acceptance Threshold:**
   - `AI_GOLD_SET_ACCEPTANCE_THRESHOLD = REQUIRES_BUSINESS_OR_ADR_APPROVAL`
   - Actual measured values reported; no artificial threshold invented.

---

## 3. Benchmark Status & External Provider Gates

- **Sarvam-105B Benchmark:**
  - `SARVAM_BENCHMARK_ACCESS = NOT_AVAILABLE`
  - `SARVAM_LIVE_BENCHMARK = BLOCKED_EXTERNAL_ACCESS`
  - Core engineering verified with zero live network credentials.
- **Fallback Candidate Selection:**
  - `FALLBACK_MODEL_SELECTION = UNRESOLVED_ADR_BENCHMARK_DECISION`
  - `FALLBACK_LIVE_BENCHMARK = NOT_RUN`
- **Production Model Decision:**
  - `PRODUCTION_LLM_SELECTION = PENDING_BENCHMARK_AND_ADR`
