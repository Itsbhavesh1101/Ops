# Role Acceptance Matrix & Operational Entitlements

## 1. Overview & Policy Architecture

The E-7 Operations Engine enforces a zero-trust, role-based access control (RBAC) and attribute-based access control (ABAC) architecture. Operational authorization is derived exclusively from trusted claims validated by either:
1. Google OIDC Bearer tokens (human principals)
2. E-7 Service API Keys (`X-E7-Service-Key`, machine principals)

Tenant boundaries are strictly unbypassable: the trusted `tenant_id` is always extracted from the authenticated principal context, never accepted from unverified request headers or JSON request bodies.

---

## 2. Canonical Role Matrix

| Endpoint Route | HTTP Method | Allowed Roles | Denied Roles | Denied HTTP Status & Error Code | Audit Event Emitted | Tenant Isolation Invariant |
|---|---|---|---|---|---|---|
| `/api/v1/goals` | `POST` | `planner`, `tenant_admin`, `admin` | `auditor`, `approver`, `scheduler`, `viewer` | `403 goal.unauthorized` | `goal.created` | Payload `tenant_id` ignored; bound to principal's tenant. |
| `/api/v1/goals/{id}/generate-plan` | `POST` | `planner`, `tenant_admin`, `admin` | `auditor`, `foreign_tenant` | `404 goal.not_found` (cross-tenant), `403` (unauthorized) | `plan.generated` | Foreign tenant receives uniform `404` (anti-enumeration). |
| `/api/v1/plans/{id}/validate` | `POST` | `planner`, `approver`, `scheduler`, `tenant_admin`, `admin` | `viewer` (if non-read) | `422 validation.failed` (if graph invalid) | `plan.validated` | Cross-tenant access returns `404 plan.not_found`. |
| `/api/v1/plans/{id}/critical-path` | `GET` | `planner`, `approver`, `scheduler`, `auditor`, `tenant_admin`, `admin` | `foreign_tenant`, unauthenticated | `404 plan.not_found` (cross-tenant), `401 auth.*` | None (read) | Scoped strictly to caller's `tenant_id`. |
| `/api/v1/plans/{id}/approve` | `POST` | `approver`, `tenant_admin`, `admin` | `planner` (author/SoD), `scheduler`, `auditor` | `403 plan.sod_violation` (author), `403 plan.unauthorized` (others) | `plan.approved` | Approver must differ from plan creator (SoD). |
| `/api/v1/workflows/definitions/{id}/deploy` | `POST` | `tenant_admin`, `admin`, `system` | `planner`, `approver`, `scheduler`, `auditor` | `403 workflow.unauthorized` | `workflow.definition_deployed` | Only administrative principals may deploy BPMN to engine. |
| `/api/v1/workflows/instances` | `POST` | `planner`, `scheduler`, `tenant_admin`, `admin` | `auditor`, `viewer` | `403 workflow.unauthorized` | `workflow.instance_started` | Instance created only within caller's tenant. |
| `/api/v1/workflows/instances/{id}` | `GET` | `planner`, `approver`, `scheduler`, `auditor`, `tenant_admin` | `foreign_tenant` | `404 workflow.instance_not_found` | None (read) | Anti-enumeration prevents discovery across tenants. |
| `/api/v1/workflows/tasks/{id}/actions` | `POST` | Assigned candidate/role (e.g. `approver`) | Ineligible actor, `auditor` | `403 workflow.task_unauthorized` | `workflow.task_completed` | Enforces candidate group matching and idempotency key. |
| `/api/v1/schedule-jobs` | `POST` | `scheduler`, `operator`, `tenant_admin`, `admin` | `planner`, `approver`, `auditor`, `viewer` | `403 schedule.unauthorized` | `schedule.job_submitted` | Enforces tenant solve queue capacity quotas (429). |
| `/api/v1/schedule-jobs/{id}` | `GET` | `scheduler`, `approver`, `auditor`, `tenant_admin`, `admin` | `foreign_tenant` | `404 job.not_found` | None (read) | Cross-tenant access returns 404. |
| `/api/v1/schedule-jobs/{id}/publish` | `POST` | `scheduler`, `approver`, `tenant_admin`, `admin` | `planner`, `auditor`, `viewer` | `403 schedule_publish.unauthorized` | `schedule.published` | Job must be in FEASIBLE/OPTIMAL status to publish. |
| `/api/v1/schedules/{id}/repair` | `POST` | `scheduler`, `operator`, `tenant_admin`, `admin` | `planner`, `approver`, `auditor`, `viewer` | `403 schedule.unauthorized` | `schedule.repaired` | Preserves locked assignments (`locked_moved == 0`). |
| `/api/v1/audit` | `GET` | `auditor`, `tenant_admin`, `admin` | `planner`, `scheduler`, `viewer` (if not granted) | `403 audit.unauthorized` | None (read) | Queries scoped to caller's `tenant_id` exclusively. |
| `/api/v1/analytics/operations` | `GET` | `auditor`, `scheduler`, `tenant_admin`, `admin` | `foreign_tenant` | `403` or `404` | None (read) | Tenant telemetry isolation verified. |

---

## 3. Persona Acceptance Detail

### 3.1 UAT_PLANNER
- **Allowed Actions**: Authoring goals (`POST /goals`), validating WBS proposals (`POST /plans/{id}/validate`), inspecting task graph details.
- **Forbidden Actions**:
  - Cannot approve plans (`POST /plans/{id}/approve` -> `403 plan.sod_violation` or `403 plan.unauthorized`).
  - Cannot submit CP-SAT solver jobs (`POST /schedule-jobs` -> `403 schedule.unauthorized`).
  - Cannot publish timetables (`POST /schedule-jobs/{id}/publish` -> `403 schedule_publish.unauthorized`).
  - Cannot deploy BPMN definitions (`POST /workflows/definitions/{id}/deploy` -> `403 workflow.unauthorized`).

### 3.2 UAT_APPROVER
- **Allowed Actions**: Reviewing plan proposals, inspecting critical paths (`GET /plans/{id}/critical-path`), approving baselines (`POST /plans/{id}/approve`), completing approval human tasks (`POST /workflows/tasks/{id}/actions`).
- **Forbidden Actions**:
  - Cannot author goals (`POST /goals` -> `403 goal.unauthorized`).
  - Cannot submit solver jobs (`POST /schedule-jobs` -> `403 schedule.unauthorized`).
  - Cannot deploy BPMN definitions (`POST /workflows/definitions/{id}/deploy` -> `403 workflow.unauthorized`).
  - Cannot update low-level task progress (`PATCH /tasks/{id}` -> barred if not assigned).

### 3.3 UAT_SCHEDULER
- **Allowed Actions**: Submitting timetabling solve jobs (`POST /schedule-jobs`), inspecting solver status and diagnostics (`GET /schedule-jobs/{id}`), publishing schedules (`POST /schedule-jobs/{id}/publish`), executing targeted disruption repairs (`POST /schedules/{id}/repair`).
- **Forbidden Actions**:
  - Cannot approve plans (`POST /plans/{id}/approve` -> `403 plan.unauthorized`).
  - Cannot deploy BPMN definitions (`POST /workflows/definitions/{id}/deploy` -> `403 workflow.unauthorized`).
  - Cannot inspect tenant-wide audit trail (`GET /audit` -> `403 audit.unauthorized`).

### 3.4 UAT_AUDITOR
- **Allowed Actions**: Full read access across audit logs (`GET /audit`), analytical metrics (`GET /analytics/operations`), plan critical paths, workflow execution logs.
- **Forbidden Actions**:
  - **Zero Mutations Permitted**: Strictly denied across all `POST`, `PATCH`, `PUT`, `DELETE` routes across all domains (always returns `403`).
  - Cannot view audit records belonging to foreign tenants (returns empty list or 404).

### 3.5 UAT_TENANT_ADMIN & Capability Classification Truth

Audit of the mounted application routes (`app/main.py`, `app/api/v1/`) confirms:
1. Workflow deployment is mounted at `POST /api/v1/workflows/definitions/{definition_id}/deploy` (enforcing administrative roles or `workflow:deploy` permission).
2. Telemetry dashboard is mounted at `GET /api/v1/analytics/operations`. There is **no mounted `GET /api/v1/analytics/quotas` endpoint** in the application router.
3. Capacity quota inspection is performed via direct domain service (`TenantQuotaManager`).
4. Machine credential lifecycle is executed via direct repository store (`PostgresServiceApiKeyCredentialStore`).

Therefore, machine credential management and quota internals are classified as **Controlled Operational/Admin Procedures**, while mounted product capabilities are scoped strictly to verified application routes.

```python
TENANT_ADMIN_PRODUCT_CAPABILITIES = [
    "POST /api/v1/workflows/definitions/{definition_id}/deploy",  # Deploy versioned BPMN workflows
    "GET /api/v1/analytics/operations",                           # Aggregated operational telemetry dashboard
    "GET /api/v1/audit",                                          # Review tenant audit trails
    "GET /api/v1/plans/{id}/critical-path",                       # Inspect plan critical path
]

OPERATOR_ONLY_CAPABILITIES = [
    "PostgresServiceApiKeyCredentialStore.create_credential",  # Machine API key provisioning
    "PostgresServiceApiKeyCredentialStore.set_enabled(False)", # Immediate key revocation
    "alembic upgrade head",                                     # Platform schema migrations
    "TenantQuotaManager.configure_quotas",                     # Infrastructure quota tuning & capacity inspection
    "OutboxRepository.reclaim_stale_claims",                   # Broker recovery operations
]

UNIMPLEMENTED_ADMIN_CAPABILITIES = [
    "GET /api/v1/analytics/quotas",        # Non-existent route (invented in early UAT docs; not mounted)
    "POST /api/v1/admin/api-keys",         # Self-service tenant API key creation UI/API
    "DELETE /api/v1/admin/api-keys/{id}",  # Self-service tenant API key revocation UI/API
    "PATCH /api/v1/admin/tenants/quotas",  # Self-service tenant quota modification UI/API
]
```

- **Security Invariant**:
  - Machine service keys are stored strictly as SHA-256 digests.
  - Revoked credentials take effect immediately across all replicas (verified via `UAT-ADMIN-002` and `UAT-AUTH-SERVICE-KEY-REVOKED`).
