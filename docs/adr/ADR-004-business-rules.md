# ADR-004: Business Rules — Flowable DMN First

## Status
Accepted

## Date
2026-09-16

## Context
The E-7 Operations Engine requires deterministic evaluation of business conditions, including routing policies, approval thresholds, SLA calendar overrides, escalation triggers, and eligibility checks.

Earlier candidate documents (such as `opes eng.pdf` / S07) suggested standalone Java rule engines such as Drools or Easy Rules. However, the *Open-Source Research Report* (Section 2.1 & Section 14) and *Complete Implementation Plan* (Section 1.1 & Section 8.3) demonstrated that introducing Drools establishes an independent, complex rule management platform with significant operational overhead. Because Flowable natively embeds a robust, standards-compliant DMN (Decision Model and Notation 1.3) engine, unifying workflow and decision execution within Flowable minimizes moving parts while fulfilling all initial rule requirements.

## Decision
1. **Flowable DMN** is selected as the primary deterministic business rule engine for the E-7 Operations Engine.
2. **dmn-js** will be utilized within the frontend Operations UI for browser-based visual decision-table modeling and editing.
3. Standalone rule engines such as **Drools** or **Easy Rules** will **NOT** be introduced in the initial implementation.
4. All business rules must satisfy the **four determinism criteria**:
   - **Typed**: Inputs and outputs adhere to strict type schemas (string, number, boolean, date).
   - **Deterministic**: Given identical typed inputs and rule version, the decision evaluation must yield identical outputs 100% of the time.
   - **Versioned**: Rules are deployed with immutable version identifiers.
   - **Auditable**: Every rule evaluation records inputs, matched rules, outputs, timestamp, and correlation ID in the E-7 audit trail.
5. **Non-Authoritative AI Invariant**: LLMs may be utilized to explain or summarize rule outcomes in natural language, but must **never** perform authoritative rule execution or override deterministic rule decisions.

## Decision Details
* **Decision Tables**: Authored as standard DMN 1.3 XML tables with defined hit policies (e.g., `UNIQUE`, `FIRST`, `RULE ORDER`).
* **Execution Pathway**: Executed either synchronously via Flowable DMN REST endpoints or as integrated Decision Tasks embedded within BPMN workflows.
* **Storage**: Rule definition XML is version-controlled in the canonical `workflow_definitions` table and mirrored in Flowable DMN deployment tables.

## Authority Boundary
* **Flowable DMN Owns**:
  - DMN 1.3 expression evaluation, feel/MVEL condition parsing, and table hit policy resolution.
* **IR / E-7 Operations Engine Owns**:
  - Canonical rule definition metadata, version locking, and multi-tenant scoping.
  - Feeding typed domain context to the decision task.
  - Recording the input/output decision trace in the canonical `audit_events` table.
  - Applying the rule outcome to the domain state machine.

## Configuration / No-Hardcoding Implications
* Rule thresholds (e.g., financial approval limits, SLA escalation days) must be defined inside versioned DMN decision tables or tenant policy tables, never hardcoded as magic numbers in Python route handlers or domain services.

## Consequences

### Positive
* **Zero Extra Infrastructure**: Uses the existing Flowable container; requires no separate Drools server, JVM deployment, or microservice.
* **Visual Authoring**: Non-technical domain operators can inspect and modify decision tables visually via `dmn-js`.
* **Standardization**: Adheres to the OMG DMN 1.3 standard, ensuring portable, vendor-neutral rule logic.

### Negative / Trade-offs
* Complex rule chaining, forward-chaining inference (Rete algorithm), and dynamic truth maintenance available in Drools are not natively supported by standard DMN tables.

## Rejected or Deferred Alternatives
* **Drools / KIE Server**: Deferred. Overkill for table-based routing, approvals, and threshold logic in MVP; introduces high memory consumption and deployment complexity.
* **Easy Rules**: Rejected. Lacks visual authoring and enterprise DMN standard compliance.
* **Hardcoded Python If/Else Logic**: Rejected. Violates auditability, no-hardcoding, and dynamic policy governance rules.

## Conditions for Reconsideration
Drools or a specialized rule engine may be considered via a future ADR only if:
1. Business requirements emerge requiring complex pattern matching, graph-based rule chaining, or thousands of inter-dependent firing rules that exceed DMN table capabilities.
2. A formal evaluation shows Flowable DMN throughput is inadequate for high-volume automated decision tasks.

## Implementation Phase
* **Phase 3: Workflow MVP** (Flowable DMN integration, approval matrix decision tables).
* **Phase 6: Unified Vertical Slice** (End-to-end integration with semester exam workflows).
* **Phase 8: Hardening** (Rule audit trail verification and performance benchmarking).

## Validation / Evidence Required
* `tests/unit/workflow/test_dmn_eval.py`: DMN decision table accurately evaluates typed inputs against hit policies.
* `tests/contract/test_deterministic_rules.py`: 100 consecutive executions with identical inputs return identical outputs.
* `tests/integration/test_dmn_audit.py`: Rule input parameters and returned decision variables are fully captured in the audit log.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///F:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) (Requirement `FR-WF-03`)
* *Complete Specification*, Section 6 & Section 18.2.
* *Open-Source Research Report*, Section 2.1 & Section 14.
