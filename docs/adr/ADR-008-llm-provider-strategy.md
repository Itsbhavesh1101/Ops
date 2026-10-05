# ADR-008: LLM Provider Strategy — Replaceable Provider with Sarvam-105B Benchmark

## Status
Accepted

## Date
2026-09-16

## Context
The Task Intelligence Engine uses Large Language Models (LLMs) to decompose unstructured natural language goals into structured Work Breakdown Structures. The open-source AI strategy of IR Infotech mandates low inference costs, data privacy, and vendor independence.

The *Open-Source Research Report* (Section 2.4 & Section 14) identified the open-weights **Sarvam-105B** model (Apache-2.0 model repository) as a primary open-source candidate to evaluate for Indian language support, enterprise reasoning, and self-hosted cost reduction. Simultaneously, exploratory notes in `opes eng.pdf` proposed commercial models or alternative open-source weights (such as Llama 3, Qwen, Mistral, and DeepSeek-R1). 

Because the AI model landscape evolves rapidly, hardcoding any specific LLM provider or model name directly into the application core introduces severe technical debt and fragility. A clean, provider-agnostic abstraction layer is required with empirical benchmarking before freezing production model selection.

## Decision
1. **Model Provider Abstraction**: All Task Intelligence components must interface LLMs exclusively through an IR-owned model-provider abstraction layer (encapsulated via PydanticAI model drivers).
2. **Sarvam-105B as Benchmark Candidate**: **Sarvam-105B is designated as an empirical benchmark and candidate model**, NOT a hardcoded application dependency.
3. **Approved Fallback Routing**: The architecture must support at least one approved fallback model/provider (e.g., hosted open-weights endpoints or bounded commercial APIs like `gpt-4o-mini`) configured for seamless failover if the primary provider experiences downtime or timeouts.
4. **Empirical Evaluation Gates**: Production model selection must be decided based on measurable evaluation metrics captured during Phase-2 benchmarking:
   - Structured-output schema validity rate (target: 100% after bounded repair).
   - Required-task coverage and hierarchical decomposition depth.
   - Invalid dependency and cycle error rate.
   - Hallucinated entity, role, or resource reference rate.
   - Human operator correction rate during plan review.
   - End-to-end token latency and throughput.
   - Inference cost per generated plan.
   - Timeout and malformed response recovery behavior.
5. **Strict No-Hardcoding Mandate**: Application code and domain logic must **NEVER** hardcode specific vendor names or model identifiers (`sarvam`, `openai`, `gemini`, `claude`, `qwen`, `mistral`, `deepseek`). All provider routing must be dynamically resolved from runtime configuration.
6. **Development Tooling vs. Runtime Disambiguation**: The Google Gemini model used by the engineering team as an agentic AI coding assistant to write code is strictly distinct from the runtime E-7 Operations Engine architecture. If Gemini is later evaluated as a runtime LLM provider for Task Intelligence, it must interface through the exact same provider abstraction and pass the exact same empirical evaluation gates.

## Decision Details
* **Provider Routing Interface**: Defined under `app/task_intelligence/planner/provider.py`. Instantiates provider clients dynamically based on Pydantic Settings.
* **Bounded Retries**: Model inference includes circuit-breaker protections: bounded timeouts (default 60s) and a maximum of 3 retries before triggering the fallback route.
* **Context Scrubbing**: Prompts receive sanitized, tenant-specific context; internal database connection strings, credentials, and unrelated tenant data are never passed to LLM context builders.

## Authority Boundary
* **LLM Provider Owns**:
  - Raw natural language generation and token completion.
* **IR / E-7 Operations Engine Owns**:
  - Dynamic prompt template assembly and context extraction.
  - Parsing and strictly validating model output against `PlanSchema`.
  - Detecting hallucinations and enforcing tenant boundaries.
  - Recording model version, token count, cost, and latency metadata in `plans`.

## Configuration / No-Hardcoding Implications
* All LLM parameters must be managed via environment variables: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_FALLBACK_MODEL`, `LLM_TIMEOUT`, `LLM_MAX_RETRIES`.
* Specific tenant prompt overrides or domain vocabularies belong in database configuration tables, never in code.

## Consequences

### Positive
* **Zero Vendor Lock-In**: Enables switching between self-hosted vLLM/Ollama deployments and cloud API providers via a simple environment variable change without modifying code.
* **Cost & Privacy Control**: Allows sensitive tenants to route inference exclusively to local/on-premise open-weights models (Sarvam, Qwen).
* **High Reliability**: Automatic fallback routing prevents user-facing outages during third-party LLM service degradation.

### Negative / Trade-offs
* Self-hosting large open-weights models (like Sarvam-105B) requires substantial GPU infrastructure during Phase 2 benchmarking.
* Prompt engineering must maintain cross-model compatibility to ensure consistent WBS quality across different tokenizers and base models.

## Rejected or Deferred Alternatives
* **Direct Vendor SDK Imports in Domain Logic**: Strictly rejected. Importing `openai`, `anthropic`, or custom vendor clients into business logic violates domain purity and creates vendor lock-in.
* **Hardcoding Sarvam-105B as Sole Execution Route**: Rejected. If local GPU infrastructure is unavailable during staging or development, development would be blocked without a fallback.

## Conditions for Reconsideration
Provider interfaces will remain pluggable permanently. The specific production default model selection will be reviewed and locked in Phase 2 upon completion of the comparative evaluation benchmark.

## Implementation Phase
* **Phase 1: Platform Foundation** (Evaluate Sarvam and fallback model endpoints; configure settings).
* **Phase 2: Task Intelligence MVP** (Implement model provider abstraction, execute evaluation suite).
* **Phase 8: Hardening** (Prompt injection stress testing, latency and token cost optimization).

## Validation / Evidence Required
* `tests/unit/task_intelligence/test_provider_factory.py`: Provider factory dynamically resolves configured provider and fallback without code changes.
* `tests/ai_eval/test_model_benchmarks.py`: Automated DeepEval test harness recording validity, hallucination rate, and latency across candidate models.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/IMPLEMENTATION_GUARDRAILS.md`](file:///F:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)
* *Complete Implementation Plan*, Section 4, Section 7.2 & Section 17.2.
* *Open-Source Research Report*, Section 2.4 & Section 14.
