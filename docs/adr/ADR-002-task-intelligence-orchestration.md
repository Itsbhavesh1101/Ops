# ADR-002: Task Intelligence Orchestration — PydanticAI MVP, LangGraph Conditional

## Status
Accepted

## Date
2026-09-16

## Context
The Task Intelligence Engine (Engine 2) converts natural language and structured goals into hierarchical Work Breakdown Structures (WBS), estimates durations, proposes dependencies, identifies risks, and suggests next-best actions.

Earlier exploratory materials (such as `opes eng.pdf` / S07) proposed LangGraph and LangChain for multi-agent planning. However, the subsequent *Open-Source Research Report* (Section 2.4 & Section 14) and *Complete Implementation Plan* (Section 4 & 7) established that full multi-agent graph runtimes introduce substantial latency, nondeterminism, and framework overhead for single-turn or bounded-turn goal decomposition. The project requires a lightweight, strictly typed, deterministic validation framework for the MVP, where generative AI outputs are bounded and validated prior to domain persistence.

## Decision
1. **PydanticAI (MIT)** is selected as the primary Task Intelligence orchestration framework for the MVP (Phase 2).
2. All LLM decomposition responses must be coerced directly into strictly typed **Pydantic schemas** (such as `PlanSchema` and `TaskItem`) with deterministic field validators.
3. The orchestration pattern must use **bounded structured generation and bounded repair**:
   - The worker executes an initial structured prompt.
   - If schema validation fails, the worker executes a bounded repair loop (maximum 2 retries with exact validation error feedback).
   - If repair fails, the job fails gracefully; malformed or partial output is **never** persisted to canonical storage.
4. **LLM outputs are strictly untrusted input** until schema validation, reference resolution, and DAG cycle validation succeed.
5. **LangGraph (MIT)** is classified as **conditional**. It must **NOT** become a dependency in Phase 2. LangGraph may only be introduced via a future ADR if multi-agent reviewer/critic loops are formally justified by measured quality gaps.

## Decision Details
* **Worker Runtime**: LLM calls execute asynchronously within `task-intelligence-worker` triggered via RabbitMQ.
* **Prompt Isolation**: Prompts are stored in versioned text/Jinja templates under `app/task_intelligence/prompts/` and never contain hardcoded provider names or secrets.
* **Graph Separation**: PydanticAI is used exclusively for language reasoning and structured extraction. Graph construction, topological sorting, and cycle validation are delegated strictly to `NetworkX` in `dependency-service`.

## Authority Boundary
* **LLM / PydanticAI Owns**:
  - Natural language goal interpretation and intent extraction.
  - Proposed work breakdown structure (draft task titles, descriptions, and roles).
  - Preliminary effort estimation and heuristic basis.
  - Advisory risk detection and mitigation explanations.
* **Deterministic Core / E-7 Owns**:
  - Validation of all IDs, resource references, and tenant boundaries.
  - Authoritative plan lifecycle (`DRAFT`, `VALIDATED`, `APPROVED`, `ACTIVE`).
  - Strict dependency graph cycle detection and CPM critical path math (NetworkX).
  - Human approval gating prior to plan activation.

## Configuration / No-Hardcoding Implications
* Model provider, model name, API keys, temperature, request timeouts, and max retry counts must be managed via Pydantic Settings (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_TIMEOUT`, `LLM_MAX_RETRIES`).
* Domain logic must never hardcode model identifiers or vendor endpoints.
* Prompts must receive dynamically filtered tenant context; unrestricted database dumps into prompts are strictly prohibited.

## Consequences

### Positive
* **Deterministic Contract**: Enforces strict schema conformity at the framework level using standard Pydantic v2.
* **Low Latency & Cost**: Avoids the token overhead, multi-hop state machines, and execution latency of full agent graph frameworks.
* **Pluggable Providers**: PydanticAI abstracts underlying model endpoints, enabling seamless switching between local open-weights models (Sarvam, Qwen, Mistral) and commercial APIs.
* **Permissive Licensing**: PydanticAI is MIT-licensed, presenting zero IP contamination risk.

### Negative / Trade-offs
* Complex multi-agent collaborative debates or dynamic agent handoffs require custom state handling unless LangGraph is later approved.

## Rejected or Deferred Alternatives
* **LangChain / LangGraph for Phase 2 MVP**: Deferred. Introducing LangGraph in Phase 2 adds unnecessary abstractions for standard single-turn goal decomposition.
* **Unstructured Output with Regex Parsing**: Rejected. Completely unmaintainable and vulnerable to structural drift.
* **Direct LLM State Mutation**: Rejected. Invariant violation; LLM output must remain non-authoritative.

## Conditions for Reconsideration
LangGraph may be introduced via a superseding ADR only if:
1. Multi-turn planning benchmarks demonstrate that single-agent decomposition fails to produce acceptable plan quality for complex multi-department goals.
2. A formal evaluation shows that a multi-agent critic/reviewer architecture increases WBS accuracy by a statistically significant margin without exceeding inference cost/latency budgets.

## Implementation Phase
* **Phase 2: Task Intelligence MVP** (PydanticAI agent implementation and `PlanSchema` generation).
* **Phase 6: Unified Vertical Slice** (Integration with approval workflows and scheduling).
* **Phase 8: Hardening** (DeepEval benchmark evaluation, adversarial prompt testing).
* **Phase 10: Evidence-Based Enhancements** (Reconsideration gate for LangGraph).

## Validation / Evidence Required
* `tests/unit/task_intelligence/test_wbs_schema.py`: 100% enforcement of `PlanSchema` validation rules.
* `tests/integration/task_intelligence/test_repair_loop.py`: Bounded repair loop recovers from minor formatting issues within 2 retries and rejects persistent errors.
* `tests/ai_eval/test_plan_benchmark.py`: Benchmark suite measuring plan validity, hallucinated entity rate, and execution latency.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///F:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) (Requirements `FR-TI-01`, `FR-TI-02`, `FR-TI-04`, `FR-TI-09`)
* *Complete Specification*, Section 5 & Section 18.
* *Open-Source Research Report*, Section 2.4 & Section 7.
