# IR Infotech E-7 Operations Engine

The **E-7 Operations Engine** is the enterprise orchestration layer designed to unify task intelligence, business workflows, and multi-resource constraint scheduling across institutional work, people, time, and processes.

---

## Authoritative Documentation Location

All foundational, authoritative project documentation is maintained in the following locations:

* **Source of Truth Directory**: [`docs/source-of-truth/`](file:///F:/operations-engine/docs/source-of-truth/)
  * `IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`
  * `IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`
  * `IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`
  * `opes eng.pdf`
* **Source of Truth Governance**: [`docs/SOURCE_OF_TRUTH.md`](file:///f:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* **Implementation Guardrails**: [`docs/IMPLEMENTATION_GUARDRAILS.md`](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)
* **Architecture Decision Records**: [`docs/adr/`](file:///f:/operations-engine/docs/adr/) (see `ADR-000`)
* **Chat History & State Retention**: [`docs/PROJECT_CHAT_HISTORY_AND_STATE_RETENTION.md`](file:///f:/operations-engine/docs/PROJECT_CHAT_HISTORY_AND_STATE_RETENTION.md)

---

## High-Level Purpose: Plan → Automate → Schedule → Monitor

The engine executes an integrated operational loop across four sequential pillars:

1. **Plan (Task Intelligence Engine)**: Decomposes unstructured natural language goals into hierarchical Work Breakdown Structures (WBS), estimates effort and durations, and constructs validated dependency graphs using Critical Path Method (CPM) and slack analysis.
2. **Automate (Workflow Engine)**: Drives the validated plan through deterministic, human-in-the-loop business processes, multi-stage approvals, DMN decision tables, and SLA tracking clocks via an embedded Flowable runtime adapter.
3. **Schedule (Scheduling Engine)**: Solves multi-resource constraints (people, facilities, equipment, time slots) using exact mathematical constraint programming (Google OR-Tools CP-SAT) and specialized timetabling adapters (UniTime) to generate conflict-free schedules with minimal-churn disruption repair.
4. **Monitor (Operational Feedback Loop)**: Emits immutable domain events, maintains audit histories, captures telemetry via OpenTelemetry, tracks progress, and detects execution risks or blockers in real time.

---

## Architectural Invariants

All development must uphold the following core architectural invariants:

* **API-First & Platform-Agnostic**: Designed as an orchestration and intelligence layer, not a replacement ERP, LMS, or HRMS.
* **Dual Access Modes**: Parity across *Universal Mode* (standalone site) and *Embedded/Integrated Mode* (API-driven host embedding).
* **Strict Multi-Tenancy**: Tenant context resolved at perimeter; zero data leakage across tenants.
* **Event-Driven & Auditable**: State transitions emit transactional outbox domain events to an append-only audit log.
* **Non-Authoritative AI Reasoning**: Generative AI (LLMs) is strictly confined to non-authoritative planning and task drafting. LLM output never performs authoritative approvals, transitions workflow states, runs SLA clocks, or acts as the scheduling solver.
* **Deterministic Execution**: Workflows, business rules, and SLA states are strictly deterministic.
* **Deterministic Constraint-Based Scheduling**: Scheduling is calculated strictly by exact mathematical constraint solvers, never probabilistic models.
* **PostgreSQL as Canonical System of Record**: PostgreSQL is the single source of truth for all transactional state.
* **Adapter Boundary Isolation**: All open-source engines (Flowable, UniTime, etc.) and third-party integrations remain behind IR-owned adapters; foreign identifiers and SDK types must never leak into domain models or public APIs.

---

## Current Implementation Phase

> **Current Status: Phase 0 (Repository Bootstrap & Source-of-Truth Governance)**

### Mandatory Phase Gate Warning
> [!WARNING]
> **DO NOT SKIP PHASES.** Implementation must strictly follow the phased sequence established in the *Complete Implementation Plan*. Do not proceed to Phase 0B or Phase 1 until Phase 0A deliverables and governance gates have been formally validated and approved.

---

## Local Development Setup

### Prerequisites
- Python >= 3.11

### Quality & Test Commands
```bash
# Install package in editable mode with development dependencies
pip install -e ".[dev]"

# Bytecode compilation verification
python -m compileall app

# Code formatting and linting check
ruff check .

# Type checking
mypy app

# Run unit & smoke tests
pytest -q
```
