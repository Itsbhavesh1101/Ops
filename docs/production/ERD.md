# E-7 Operations Engine — Database Entity Relationship Documentation (ERD)

**Document Version:** 1.0.0  
**Phase:** Phase 8F Canonical Production Documentation  
**Database Engine:** PostgreSQL 16  
**Alembic Head Revision:** `0014`  
**Application Tables:** 26  
**Total Public Tables:** 27 (26 application + `alembic_version`)  
**Mermaid Source Diagram:** [`docs/production/erd.mmd`](file:///f:/operations-engine/docs/production/erd.mmd)

---

## 1. Schema Architecture & Governance

The E-7 Operations Engine database schema is strictly managed via Alembic migrations. Direct DDL execution is strictly forbidden in all environments (`ORIGINAL_REQUIREMENT`).

### 1.1 Tenant Isolation Architecture

- **24 of 26 Application Tables** contain an explicit `tenant_id: VARCHAR(255)` column with database indexes.
- **2 Infrastructure Tables** handle tenant data defensively:
  - `outbox_events`: Stores full event envelopes as `JSONB`, where `envelope->>'tenant_id'` is validated by application-layer guards before dispatch.
  - `inbox_messages`: Consumer deduplication table keyed by `(consumer_name, event_id)` to enforce at-least-once to exactly-once message delivery idempotency.

### 1.2 Table Inventory Summary

| Domain | Table Name | PK | FKs | Tenant Scoped | Security Tier |
|---|---|---|---|---|---|
| **Planning** | `goals` | `id` | None | Yes | High |
| **Planning** | `plans` | `id` | `goal_id`, `superseded_by_plan_id` | Yes | High |
| **Planning** | `milestones` | `id` | `plan_id` | Yes | Standard |
| **Planning** | `tasks` | `id` | `plan_id`, `parent_task_id`, `milestone_id` | Yes | Standard |
| **Planning** | `task_dependencies` | `id` | `plan_id`, `predecessor_id`, `successor_id` | Yes | Standard |
| **Planning** | `task_risks` | `id` | `plan_id`, `task_id` | Yes | Standard |
| **Workflows** | `workflow_definitions`| `id` | None | Yes | High |
| **Workflows** | `workflow_instances` | `id` | `definition_id` | Yes | High |
| **Workflows** | `workflow_task_mappings`| `id`| `workflow_instance_id`, `sla_policy_id` | Yes | Critical (SoD) |
| **Workflows** | `workflow_artifacts` | `id` | None | Yes | Standard |
| **Workflows** | `sla_policies` | `id` | None | Yes | Standard |
| **Scheduling**| `resources` | `id` | None | Yes | Standard |
| **Scheduling**| `resource_availabilities`| `id`| `resource_id` | Yes | Standard |
| **Scheduling**| `schedule_jobs` | `id` | `solver_profile_id` | Yes | High |
| **Scheduling**| `schedules` | `id` | `source_job_id`, `supersedes_id` | Yes | High |
| **Scheduling**| `schedule_assignments`| `id` | `schedule_id` | Yes | Standard |
| **Scheduling**| `solver_profiles` | `id` | None | Yes | Standard |
| **Scheduling**| `constraint_definitions`| `id`| None | Yes | Standard |
| **Calendar** | `calendar_links` | `id` | None | Yes | Standard |
| **Calendar** | `calendar_watch_channels`| `id`| None | Yes | High |
| **Security** | `service_api_credentials`| `id`| None | Yes | Critical (ADR-013) |
| **Messaging** | `outbox_events` | `event_id` | None | Via Envelope | Critical |
| **Messaging** | `inbox_messages` | `(consumer_name, event_id)`| None | No | Critical |
| **Messaging** | `idempotency_records`| `(tenant_id, operation_name, key_digest)`| None | Yes | Critical |
| **Jobs** | `jobs` | `job_id` | None | Yes | Standard |
| **Audit** | `audit_events` | `id` | None | Yes | Critical |
| **Meta** | `alembic_version` | `version_num` | None | N/A | Operational |

---

## 2. Security-Sensitive Table Specifications

### 2.1 `service_api_credentials` (Migration `0014`, ADR-013)

**Governance Classification:** `APPROVED_ADR_DECISION` (ADR-013)  
*Note: Service credential persistence in PostgreSQL is an engineering decision from ADR-013 to guarantee restart durability and multi-replica visibility across distributed API nodes; it is not an original source requirement.*

- **Columns:**
  - `id: UUID` (PK, non-null)
  - `tenant_id: VARCHAR(255)` (non-null, indexed)
  - `credential_id: VARCHAR(64)` (unique, non-null, indexed) — Public identifier of the key (e.g. `cred_xyz789`)
  - `service_subject: VARCHAR(255)` (non-null) — Name or service principal identifier
  - `secret_digest: VARCHAR(64)` (non-null) — SHA-256 one-way cryptographic hash of `<secret>`
  - `roles: JSONB` (non-null) — Scoped roles (e.g. `["planner", "operator"]`)
  - `permissions: JSONB` (non-null) — Granular capabilities
  - `enabled: BOOLEAN` (non-null, default `true`) — Operational active flag
  - `not_before: DATETIME` (nullable) — Activation timestamp
  - `expires_at: DATETIME` (nullable) — Expiration timestamp
  - `issuer: VARCHAR(255)` (non-null) — Issuer system/operator
  - `revoked_at: DATETIME` (nullable) — Revocation timestamp
  - `revoked_by: VARCHAR(255)` (nullable) — Principal performing revocation
  - `description: VARCHAR(512)` (nullable)
  - `created_at: DATETIME`, `updated_at: DATETIME`
- **Security Invariant:** Plaintext API keys (`e7sk_<credential_id>.<secret>`) are **never stored** in the database. Verifiers compare `SHA256(secret)` against `secret_digest` in constant time.

### 2.2 `idempotency_records`

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR`

- **Composite PK:** `(tenant_id, operation_name, key_digest)`
- **Columns:**
  - `tenant_id: VARCHAR(128)`
  - `operation_name: VARCHAR(128)` (e.g. `workflows.task_action`, `planning.create_goal`)
  - `key_digest: VARCHAR(64)` (SHA-256 hash of client `Idempotency-Key`)
  - `request_fingerprint: VARCHAR(64)` (SHA-256 hash of request body payload)
  - `created_at: DATETIME`
- **Lifecycle:** Enforces multi-replica idempotent request deduplication without table locking.

### 2.3 `outbox_events`

**Governance Classification:** `IMPLEMENTED_ENGINEERING_BEHAVIOR`

- **Columns:**
  - `event_id: UUID` (PK)
  - `envelope: JSONB` (Full event envelope including event_id, tenant_id, correlation_id, payload)
  - `created_at: DATETIME`, `published_at: DATETIME` (nullable)
  - `dispatch_attempt_count: INTEGER` (default `0`)
  - `next_attempt_at: DATETIME` (nullable)
  - `claim_token: UUID` (nullable) — Distributed worker lease token
  - `claimed_at: DATETIME` (nullable) — Distributed worker lease timestamp

### 2.4 `audit_events`

**Governance Classification:** `ORIGINAL_REQUIREMENT`

- **Columns:**
  - `id: UUID` (PK)
  - `tenant_id: VARCHAR(255)` (non-null, indexed)
  - `event_name: VARCHAR(128)` (non-null, indexed)
  - `occurred_at: DATETIME` (non-null, indexed)
  - `actor_id: VARCHAR(255)` (non-null)
  - `actor_type: VARCHAR(64)` (non-null)
  - `correlation_id: VARCHAR(255)` (non-null, indexed)
  - `resource_type: VARCHAR(64)` (non-null)
  - `resource_id: VARCHAR(255)` (non-null)
  - `action: VARCHAR(64)` (non-null)
  - `payload: JSONB` (nullable)
  - `created_at: DATETIME`
- **Integrity Rule:** Audit records are strictly append-only. UPDATE and DELETE operations are prohibited.

---

## 3. Core Domain Relationship Models

### 3.1 Planning Domain
```
goals (1) <--- (N) plans (1) <--- (N) milestones (1) <--- (N) tasks
                    plans (1) <--- (N) task_dependencies
                    plans (1) <--- (N) task_risks
```
- A goal initiates one or more plan proposals.
- A plan owns milestones, tasks, dependencies (DAG), and detected risks.
- Tasks support hierarchical decomposition via self-referential `parent_task_id -> tasks.id`.

### 3.2 Workflow Domain
```
workflow_definitions (1) <--- (N) workflow_instances (1) <--- (N) workflow_task_mappings
                                                                  workflow_task_mappings (N) ---> (1) sla_policies
```
- Definitions store BPMN 2.0 XML and Flowable deployment IDs.
- Instances correspond to running Flowable process instances.
- Task mappings maintain the bidirectional sync between Flowable user tasks, candidate roles, assignees, and E-7 human task actions.

### 3.3 Scheduling Domain
```
solver_profiles (1) <--- (N) schedule_jobs (1) <--- (N) schedules (1) <--- (N) schedule_assignments
resources (1) <--- (N) resource_availabilities
```
- `schedule_jobs` records asynchronous solve submissions, input snapshots, and CP-SAT diagnostics.
- `schedules` represents candidate or published schedules. When published, it locks assignments.
- `schedule_assignments` stores exact `(activity_id, resource_id, start_at, end_at, is_locked)`.
