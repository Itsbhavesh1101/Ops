# Phase 3 DMN Decision Tables & Typed Human Task Forms

## 1. DMN Decision Table Authoring & Runtime
The DMN subsystem (`app/workflow/dmn/`) delivers deterministic business rule evaluation using DMN 1.3 decision tables:
- **Visual Editor**: Embedded `dmn-js` table modeler (`ui/operations-ui/src/components/DmnEditor.tsx`) with preserved attribution.
- **Server-Side Validation**: `DmnValidator` validates XML structure using secure `defusedxml` parsing (immune to XXE and entity expansion attacks), enforces supported hit policies (`UNIQUE`, `FIRST`, `RULE_ORDER`, `COLLECT`), and rejects any script extensions (`groovy`, `python`, etc.).
- **Flowable DMN Runtime**: Deploys to Flowable DMN engine and evaluates typed rules deterministically.
- **Audit Evidence**: Every rule evaluation records append-only audit event `workflow.dmn.executed` capturing decision key, version, input variables, output variables, and execution timestamp without leaking credentials.
- **Reproducibility Guarantee**: Given identical typed input variables and decision versions, the engine produces identical output across executions.

## 2. Typed Human-Task Forms (`form-js` & IR Mapping)
The Form subsystem (`app/workflow/forms/`) provides structured human-in-the-loop task definitions:
- **Supported Field Types**: `textfield`, `number`, `checkbox`, `select`, `radio`, `textarea`.
- **Derived Variable Policy**: Form definition schema automatically derives a fail-closed `WorkflowVariablePolicy`. All field keys become explicit allowlisted variables.
- **Server-Side Submission Validation**: `FormValidator` validates submitted payloads before engine interaction:
  - Undeclared variables are strictly rejected.
  - Required fields must be non-empty.
  - Numeric ranges (`min`, `max`) and string length limits (`max_length`) are enforced.
  - Select and radio submissions are restricted to declared option values.
