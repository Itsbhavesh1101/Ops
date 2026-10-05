# Implementation Guardrails & Architectural Constraints

This document defines the mandatory development guardrails and engineering constraints for the **IR Infotech E-7 Operations Engine**. Every engineer, subagent, and automated workflow must comply with these rules. Violations will result in rejected pull requests.

---

## 1. Strict No-Hardcoding Policy

* **No Secret or Credential Hardcoding**: Never hardcode API keys, tokens, passwords, private keys, or credentials anywhere in source code, tests, documentation, or commit history.
* **No Endpoint or URL Hardcoding**: Database connection strings, broker endpoints, external service URLs, and internal microservice addresses must be injected via runtime environment variables and validated via Pydantic Settings.
* **No Entity or Identifier Hardcoding**: Do not hardcode specific tenant IDs, organization IDs, user IDs, resource IDs, project keys, calendar IDs, or ERP database keys.
* **No Business Rule Magic Values**: SLA durations, escalation thresholds, solver time limits, solver objective penalty weights, business calendars, working hours, and retention policies must reside in tenant configuration or canonical database storage, never as magic constants in code.
* **No Model Name Hardcoding in Domain Logic**: Model names, LLM provider routes, temperature parameters, and inference endpoints must remain externally configurable.

---

## 2. Cross-Service Boundaries & Data Isolation

* **No Direct Cross-Service Database Shortcuts**: Services and workers must never directly query or mutate another service's private tables or bypass repository interfaces. All cross-boundary state access occurs via public domain APIs or asynchronous domain events.
* **PostgreSQL as Canonical Store**: No external runtime (such as Flowable or an external solver) may be treated as the system of record for canonical business state. State changes must commit transactionally to the E-7 PostgreSQL database alongside an outbox event.
* **Adapter Boundary for External Systems**: Every external third-party integration (ERP, CalDAV, Google Calendar, Slack, MS Teams, webhooks) must reside behind an IR-owned adapter boundary interface.

---

## 3. Separation of AI Reasoning & Deterministic Execution

* **No Authoritative LLM State Changes**: Generative AI (LLMs) is restricted to non-authoritative planning, drafting Work Breakdown Structures (WBS), and unstructured text extraction. An LLM output may never directly:
  - Transition a workflow instance state
  - Perform or record an authoritative business approval
  - Start, advance, or stop an SLA clock
  - Grant permissions or bypass security policies
* **No Schedule Generation from LLM Output**: LLM output must never be accepted as an authoritative schedule. Deterministic scheduling engines own feasibility and optimization. OR-Tools CP-SAT is the primary generic solver; Phase 5 evaluates UniTime vs an OR-Tools-native model for education scheduling; FET is a benchmark/fallback subject to licensing review; Timefold remains conditional behind an ADR/evidence gate.

---

## 4. Multi-Tenancy & Security Invariants

* **No Tenant ID Trusted from Request Payload**: Never trust a `tenant_id` provided in a request body or URL path without cryptographic verification. The tenant context must be resolved and validated exclusively from the authenticated JWT claims or trusted API gateway headers.
* **Tenant Isolation Across All Layers**: Every database query, repository lookup, event publication, cache key, and background worker job must be strictly scoped to the resolved `tenant_id`.

---

## 5. Domain Purity & API Leaking Protections

* **No External Provider Coupling in Domain Services**: Domain entities, policies, and services must remain pure and free from third-party vendor SDK imports, cloud provider types, or framework-specific annotations.
* **No Flowable-Specific Identifiers Exposed in Public APIs**: Flowable process definition IDs, deployment IDs, byte arrays, or internal task instance IDs must never leak into public E-7 API contracts. E-7 maps internal process instances to canonical `WorkflowInstance` and `Task` IDs.
* **No UniTime-Specific Identifiers Exposed in Public APIs**: UniTime internal solver IDs, course offering IDs, and database keys must be translated to and from canonical E-7 domain identifiers at the adapter boundary.
* **No Solver-Specific Objects Leaking into Public API Schemas**: OR-Tools internal objects (such as `IntVar`, `Constraint`, `CpModel`, `CpSolverResponseStatus`) must never be serialized or exposed in public API schemas. Only domain-level schedule representations, time slots, and assignments may be returned.
* **No Business Rules Buried in Route Handlers**: FastAPI route handlers are thin transport adapters. Business rules, validation logic, and state transitions belong in domain services and policies.

---

## 6. Error Handling & Operational Resilience

* **No Silent Fallback Behavior**: If a primary service, solver, or integration fails or returns an infeasible solution, the system must report an explicit error, diagnostic explanation, or degraded status. It must never silently fall back to approximate or unvalidated data and fake success.
* **No Swallowed Exceptions**: Bare `except:` clauses or empty exception blocks that swallow errors without logging structured diagnostic context and error correlation IDs are strictly prohibited.
* **Idempotency on State-Changing Operations**: Every state-changing API endpoint must require and validate an `Idempotency-Key` header to prevent duplicate execution or multiple resource allocations during network retries.
* **Immutable Auditability**: Every state transition, human decision, approval rejection, and schedule publication must emit an immutable audit event recorded in the audit trail.

---

## 7. Evolution & Architecture Gates

* **No Destructive Database Migrations Without Approval**: Schema drops, column deletions, or data-destructive Alembic migrations are prohibited without formal team review, dual-phase rollout strategies, and pre-migration backup verification.
* **No Conditional Component Introduced Before Its ADR Gate**: Components designated as conditional (Temporal, LangGraph, Timefold, Valkey, Kafka) must not be imported, instantiated, or deployed until an Architecture Decision Record (ADR) justifying their requirement is formally accepted.
