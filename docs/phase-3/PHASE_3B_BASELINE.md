# Phase 3B Baseline — Workflow Designer, DMN, Typed Forms, Approvals & SLA

## 1. Overview & Frozen Status
Phase 3B completes the remaining Workflow Subsystem requirements for the IR Infotech E-7 Operations Engine.
With the completion of Phase 3B and all acceptance gates passing:
- **Phase 0**: COMPLETE / FROZEN
- **Phase 1**: COMPLETE / FROZEN
- **Phase 2A**: COMPLETE / FROZEN
- **Phase 2B Core**: COMPLETE / FROZEN
- **Canonical Phase 2**: IN_PROGRESS_PENDING_PROVIDER_BENCHMARK
- **Phase 3A**: COMPLETE / FROZEN
- **Phase 3B**: COMPLETE / FROZEN
- **Canonical Phase 3**: COMPLETE / FROZEN
- **Phase 4 Readiness**: READY
- **Phase 4**: NOT STARTED

## 2. Invariants & Architecture Preserved
- **Flowable Native Runtime Authority**: Flowable owns engine execution state, process instances, task lifecycles, and native timers.
- **PostgreSQL E-7 Canonical Authority**: Canonical entities (`workflow_definitions`, `workflow_instances`, `workflow_task_mappings`, `workflow_artifacts`, `sla_policies`) remain authoritative for business correlation, tenancy, and audit projections.
- **Physical Schema**: Single migration `0010_create_workflow_policy_artifacts.py`. Exactly 16 canonical application tables.
- **No Client-Only Security**: All BPMN, DMN, and form schemas uploaded or edited are validated server-side (`BpmnValidator`, `DmnValidator`, `FormValidator`).
- **No Arbitrary User Scripts**: User-authored `${...}`, `#{...}`, `<scriptTask>`, or script extensions remain strictly forbidden.
- **Explicit Variable Allowlist**: All form fields map to `WorkflowVariablePolicy` fail-closed allowlists.
- **Pre-Engine Authorization**: Human task actions require tenant scoping, principal role verification, and idempotency key headers before reaching Flowable.
- **Router Mount**: `WORKFLOW_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_AUTHORIZATION_DEPENDENCIES` preserved.

## 3. Physical Data Model & Schema Baseline (Alembic Head: 0010)

The physical database schema is governed by Alembic migration `0010_create_workflow_policy_artifacts.py` (`down_revision: 0009`).
`Base.metadata` contains exactly **16 canonical application tables**:

### Phase 1 Foundation Tables (5)
1. `audit_events`: Append-only compliance and lifecycle audit logs.
2. `outbox_events`: Transactional outbox for reliable asynchronous event dispatch.
3. `inbox_messages`: Idempotent event inbox for consuming inbound integration events.
4. `idempotency_records`: Request-level idempotency tokens, request hashes, and cached responses.
5. `jobs`: Background asynchronous job dispatch and lifecycle tracking.

### Phase 2A Planning Tables (6)
6. `goals`: Operational business goals and objectives.
7. `plans`: Decomposed operational plans derived from goals.
8. `milestones`: Temporal and logical progress checkpoints within plans.
9. `tasks`: Discrete executable units of work.
10. `task_dependencies`: Directed acyclic graph dependencies between tasks.
11. `task_risks`: Risk assessments and mitigations mapped to tasks.

### Phase 3A Workflow Tables (3)
12. `workflow_definitions`: Validated versioned BPMN 2.0 workflow process definitions.
13. `workflow_instances`: Correlated process execution instances.
14. `workflow_task_mappings`: Canonical human-task mappings linking external engine tasks to E-7 tenants, actors, and lifecycle states. Extended in Phase 3B with `form_key`, `sla_policy_id`, `sla_status`, `warning_at`, `paused_at`, and `accumulated_pause_seconds`.

### Phase 3B Policy & Artifact Tables (2)
15. `sla_policies`: Tenant-scoped, versioned SLA policy definitions specifying target durations, warning thresholds, calendars, and escalation rules.
16. `workflow_artifacts`: Generic versioned artifact repository storing BPMN, DMN, and Form definitions with SHA-256 integrity hashes.

### Explicit Boundary Invariants
- `alembic_version` is an internal Alembic migration tracking table; it is not an application ORM model.
- Identity and credential persistence (`tenants`, `users`, `roles`, `permissions`, `user_roles`, `role_permissions`) remains deferred per `IDENTITY_TENANT_CREDENTIAL_PERSISTENCE = DEFERRED_REQUIRES_EXPLICIT_DATA_MODEL_TASK`.
- Zero Flowable engine tables (`ACT_*`) reside in `Base.metadata` or E-7 application migrations; Flowable's internal relational schema is completely isolated.

## 4. UI Architecture (`ui/operations-ui`)
- Vite + React 18 + TypeScript application.
- Embeds `bpmn-js` for BPMN 2.0 visual authoring.
- Embeds `dmn-js` for DMN 1.3 decision table authoring.
- Embeds typed form builder and preview for human tasks.
- Dedicated Task Inbox displaying canonical task IDs, SLA badges, and action submissions.
- Strict compliance with `bpmn.io` license requiring mandatory visible watermark (`.bjs-powered-by`, `.dmn-powered-by`).

## 5. Canonical Requirements Traceability (FR-WF-01 to FR-WF-07)

1. **FR-WF-01 — Workflow Models**:
   Versioned BPMN-style definitions containing human, service, timer, gateway and event steps.
   *Status*: **VERIFIED / FROZEN** (BPMN designer, server validation, versioned artifacts, Flowable runtime).
2. **FR-WF-02 — Approvals**:
   Single, sequential, parallel, quorum and role-based approvals.
   *Status*: **VERIFIED / FROZEN** (sequential, parallel all-of, quorum multi-instance, role/action authorization, rejection).
3. **FR-WF-03 — Business Rules**:
   Deterministic typed rule evaluation with auditable outcomes.
   *Status*: **VERIFIED / FROZEN** (Flowable DMN, deterministic same-input/version behavior, DMN designer, audit evidence).
4. **FR-WF-04 — SLA Tracking**:
   Due-at/task SLA, pause/resume, business calendars, warning/breach thresholds.
   *Status*: **VERIFIED / FROZEN** (SLA policy, business calendar, warning/breach Flowable timers, pause/resume business-time arithmetic, timer restart durability).
5. **FR-WF-05 — Escalation**:
   Reminder, reassignment, manager escalation or service action when configured SLA thresholds are crossed.
   *Status*: **VERIFIED / FROZEN** (typed escalation actions, warning/breach-driven behavior, outbox/audit emission).
6. **FR-WF-06 — Durable Execution**:
   Long-running workflow recovery/restart without duplicate committed side effects.
   *Status*: **VERIFIED / FROZEN** (Flowable persisted runtime/timers, restart durability tests, idempotent action/callback behavior).
7. **FR-WF-07 — Operational Audit**:
   Record every transition, actor, timestamp, input, rule outcome, external call and retry.
   *Status*: **VERIFIED / FROZEN** (append-only audit, normalized history, rule/action/escalation evidence).
