# Third-Party Licensing & Compliance Governance

## Purpose

This document serves as the repository entry point and compliance governance manifest for third-party software and dependencies evaluated or integrated into the **IR Infotech E-7 Operations Engine**.

For the comprehensive technical compliance register, classification model, service/library isolation rules, code-copying policy, and release gating criteria, refer to:

👉 **[`docs/OPEN_SOURCE_COMPLIANCE_REGISTER.md`](file:///F:/operations-engine/docs/OPEN_SOURCE_COMPLIANCE_REGISTER.md)**

---

## Controlled Adoption & Notice Strategy

1. **Incremental Phase-by-Phase Adoption**:
   * Exact locked package versions, SPDX license identifiers, source URLs, and required copyright notices are formally registered in this repository **phase-by-phase** as individual dependencies are approved, introduced, and locked.
   * Premature dependencies observed in the pre-existing scaffold (e.g., `pydantic-ai`, `networkx`, `ortools` in `pyproject.toml`) will undergo formal sequencing remediation during the Phase 1 bootstrap.

2. **Research Evaluation vs. Distribution**:
   * The architectural research evaluated 30 projects across open-source, reference, benchmark, and source-available tiers.
   * **Listing in research reports or compliance registers does NOT mean a project ships with E-7.**
   * Gated and conditional technologies (`Temporal`, `LangGraph`, `Timefold`, `Valkey`, `Apache Kafka`, `Operaton`) remain strictly blocked from installation until a dedicated ADR is accepted.
   * Reference and benchmark architectures (`OpenProject`, `Plane`, `LibrePlan`, `FET`) are isolated study or benchmark targets and are **never** embedded or copied into the IR proprietary core.

3. **Attribution & Notice Manifests**:
   * As production dependencies are locked, consolidated notices will be maintained in `third_party/NOTICE.md` with upstream license texts preserved in `third_party/licenses/`.
   * Frontend authoring canvases utilizing `bpmn-js`, `dmn-js`, and `form-js` must strictly preserve visible `bpmn.io` logo watermarks.
