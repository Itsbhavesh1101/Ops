# ADR-009: Workflow Authoring — bpmn-js, dmn-js and form-js

## Status
Accepted

## Date
2026-09-16

## Context
The E-7 Operations Engine requires browser-based graphical authoring, inspection, and review tools for business processes (BPMN 2.0), business rule decision tables (DMN 1.3), and human task completion forms. 

Developing custom visual canvas modelers for BPMN and DMN from scratch would consume excessive engineering resources and risk introducing non-standard diagram representations. The open-source `bpmn.io` ecosystem (`bpmn-js`, `dmn-js`, `form-js`) is the industry standard JavaScript toolkit for embedding visual process modelers into web applications.

However, the *Open-Source Research Report* (Section 2.2 & Section 14) and `LICENSES.md` identified specific licensing constraints regarding the `bpmn.io` family: while permissively licensed for commercial use, the `bpmn.io` license strictly mandates that the **visible `bpmn.io` logo watermark** must remain displayed in the bottom-right corner of the canvas. An explicit architectural decision is required to govern the integration of these authoring toolkits, their boundaries, and licensing compliance.

## Decision
1. **Frontend Authoring Toolkits**: The E-7 Operations UI (`ui/operations-ui`) will embed:
   - **`bpmn-js`**: For visual BPMN 2.0 workflow diagram authoring, modeling, and live token tracking.
   - **`dmn-js`**: For visual DMN 1.3 decision table authoring and inspection.
   - **`form-js`**: For visual JSON-based form authoring used in human task data capture.
2. **Authoring Only (Non-Authoritative)**: These browser toolkits are **authoring and visualization components only**. They do **NOT** execute processes, manage workflow state, or own authoritative business logic.
3. **Runtime Execution Authority**: Flowable Engine remains the sole deterministic runtime executor for deployed BPMN and DMN definitions.
4. **IR Domain Ownership**: IR Infotech retains complete ownership of:
   - The enclosing Operations Web UI application framework.
   - Role-based permissions governing who may create, edit, or publish templates.
   - Artifact versioning, draft states, and deployment governance.
   - Tenant isolation and mapping of graphical elements to canonical E-7 domain IDs.
   - Server-side validation of exported XML schemas prior to Flowable deployment.
5. **Mandatory License Compliance (Watermark Rule)**: All embedded instances of `bpmn-js` and `dmn-js` must strictly preserve and never hide, obscure, or remove the visible `bpmn.io` watermark/attribution logo, in compliance with the `bpmn.io` license terms.

## Decision Details
* **Frontend Integration**: Toolkits are imported as npm packages within `ui/operations-ui` and mounted into React/Next.js canvas views.
* **Export Protocol**: Saving a process definition exports raw BPMN 2.0 XML (or DMN 1.3 XML) over REST to the E-7 API (`POST /api/v1/workflow-definitions`).
* **Server-Side Validation**: The backend validates the XML against the BPMN 2.0 XSD schema and verifies that all service task delegates conform to registered E-7 service interfaces before deploying to Flowable.

## Authority Boundary
* **`bpmn.io` Toolkits (`bpmn-js`, `dmn-js`, `form-js`) Own**:
  - Browser canvas rendering, drag-and-drop shape manipulation, connection routing, and XML serialization.
* **Flowable Runtime Owns**:
  - Deterministic execution of the deployed XML definitions.
* **IR / E-7 Operations Engine Owns**:
  - Canonical workflow metadata in PostgreSQL (`workflow_definitions`).
  - Access control and deployment approval gates.
  - Linking workflow tasks to canonical operational plans and schedules.

## Configuration / No-Hardcoding Implications
* Default workflow templates, department approval rules, and form schemas must be loaded dynamically from versioned filesystem or database repositories (`workflows/bpmn/`, `workflows/dmn/`, `workflows/forms/`), never hardcoded in frontend JavaScript components.

## Consequences

### Positive
* **Rich User Experience**: Provides intuitive, drag-and-drop process and decision table authoring comparable to commercial enterprise BPM suites.
* **Standards Compliance**: Native OMG BPMN 2.0 and DMN 1.3 compatibility guarantees interoperability with Flowable and third-party modeling suites.
* **Rapid Delivery**: Eliminates months of custom UI canvas development.

### Negative / Trade-offs
* UI must accommodate the visible `bpmn.io` watermark on canvas viewports to maintain license compliance.
* Increases frontend bundle size; requires lazy loading and dynamic bundle chunking in Next.js.

## Rejected or Deferred Alternatives
* **Building Custom React Flow / Canvas Modeler from Scratch**: Rejected. High engineering cost and risk of standard deviation.
* **Camunda Modeler Desktop Dependency**: Rejected. Requiring operators to install desktop modeling software breaks the web-based Universal and Embedded access mode mandate.
* **Hiding the `bpmn.io` Watermark via CSS**: Strictly rejected. Violates copyright license terms.

## Conditions for Reconsideration
Alternative web modeling toolkits will only be considered if:
1. `bpmn.io` changes its license terms to an incompatible proprietary model.
2. An enterprise client requires complete whitelabeling with zero third-party watermarks, in which case a commercial license from Camunda/bpmn.io or an approved custom canvas solution would be evaluated under a separate ADR.

## Implementation Phase
* **Phase 3: Workflow MVP** (Embed `bpmn-js` and `dmn-js` in `ui/operations-ui`, implement XML validation and deployment pipelines).
* **Phase 6: Unified Vertical Slice** (End-to-end authoring and execution of semester exam workflows).
* **Phase 8: Hardening** (Frontend performance optimization, canvas memory profiling).

## Validation / Evidence Required
* `tests/unit/workflow/test_bpmn_authoring.py`: Backend successfully parses and validates XML exported from `bpmn-js`.
* `ui/operations-ui` visual regression test: Verifies that canvas renders properly with the visible watermark in compliance with licensing requirements.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///F:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) (Requirement `FR-WF-01`)
* [`LICENSES.md`](file:///F:/operations-engine/LICENSES.md) (Section 2 & Section 3).
* *Open-Source Research Report*, Section 2.2 & Section 14.
