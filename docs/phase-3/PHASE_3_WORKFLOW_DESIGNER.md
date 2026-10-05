# Phase 3 Workflow Designer Architecture & Security

## 1. Operations UI BPMN Designer
The Workflow Designer (`ui/operations-ui/src/components/BpmnDesigner.tsx`) embeds `bpmn-js` to provide visual authoring of BPMN 2.0 diagrams directly in the Operations UI.

### Key Capabilities
- **Visual Diagram Canvas**: Modeler canvas supporting interactive modeling of user tasks, service tasks, parallel/exclusive gateways, and timer boundary events.
- **Template Initialization**: Default templates for sequential approval, parallel all-of, quorum N-of-M, and SLA timer boundaries.
- **Client Screening & Server Authority**: Client performs fast local linting (checking process IDs and forbidden tags), but server-side `BpmnValidator` remains the sole authority for syntax validity, structural integrity, and security rules.
- **Versioned Artifact Registration**: Direct save to `/api/v1/workflow/definitions` with tenant scoping and SHA-256 artifact hashing.
- **Engine Deployment**: Authoritative deployment trigger to Flowable engine (`flowable/flowable-rest:7.0.0`).

## 2. bpmn.io Open-Source Attribution & License Compliance
- In accordance with the `bpmn.io` license terms and the project's Open Source Compliance Register:
  - The watermark logo (`.bjs-powered-by`, `.dmn-powered-by`) is preserved on canvas.
  - CSS explicitly enforces visibility: `display: block !important; opacity: 1 !important; visibility: visible !important;`.
  - No masking, cropping, or overlapping UI components are permitted.
  - Verified by automated frontend component tests (`BpmnDesigner.test.tsx`).

## 3. Safe Execution Boundary
- Arbitrary executable script expressions (`${...}`, `#{...}`) and `<scriptTask>` elements remain strictly forbidden for user-authored BPMN models.
- Complex orchestration (e.g. multi-instance quorum completion) is compiled through E-7 controlled templates (`ApprovalCompiler`) using closed trusted expression templates (`TRUSTED_E7_GENERATED_EXPRESSION`), completely isolating the runtime from user-supplied code execution.
