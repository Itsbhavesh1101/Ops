# E-7 Operations Engine — Canonical API Reference

**Document Version:** 1.0.0  
**Phase:** Phase 8F Canonical Production Documentation  
**Base Path:** `/api/v1`  
**OpenAPI Specification:** [`docs/production/openapi.json`](file:///f:/operations-engine/docs/production/openapi.json)  
**Total Mounted Operations:** 68 paths  
**Production Docs Exposure:**  
- `STATIC_BUILD_ARTIFACT = AVAILABLE` ([`docs/production/openapi.json`](file:///f:/operations-engine/docs/production/openapi.json))  
- `PUBLIC_INTERACTIVE_DOCS = CONFIGURABLE / DISABLED_IN_PRODUCTION` (`E7_DOCS_ENABLED=false`)  

---

## 1. Authentication & Security Architecture

### 1.1 Dual Authentication Mechanisms

The E-7 Operations Engine enforces a strict dual-authentication boundary at the API layer:

| Authentication Mode | Credential Header | Token / Key Format | Target Principal | Governance Classification |
|---|---|---|---|---|
| **Human / User (OIDC)** | `Authorization: Bearer <token>` | OIDC/JWT token | Configured OIDC/JWT identity provider (Google Identity reference configuration) | `ORIGINAL_REQUIREMENT` |
| **Service-to-Service** | `X-E7-Service-Key: <key>` | `e7sk_<credential_id>.<secret>` | Machine principal with scoped roles and tenant derivation | `APPROVED_ADR_DECISION` (ADR-013) |

### 1.2 Authentication Invariants & Edge Cases

- **Mutual Exclusivity (`HTTP 400`):** Supplying *both* `Authorization` and `X-E7-Service-Key` in the same HTTP request is strictly forbidden and rejected with `400 Bad Request` (`auth.conflicting_credentials`).
- **Missing Credentials (`HTTP 401`):** Requests to authenticated business endpoints without either header fail closed with `401 Unauthorized` (`auth.credentials_missing`).
- **Tenant Context Derivation:** Tenant isolation is **strictly derived from the verified principal**. Client-supplied headers (e.g. `X-Tenant-ID`) are **never trusted** for security boundaries (`IMPLEMENTED_ENGINEERING_BEHAVIOR`).
- **Zero Plaintext Secret Storage:** Service API keys use SHA-256 one-way secret hashing (`secret_digest`) in PostgreSQL table `service_api_credentials`. Key secrets are never stored, logged, or returned in API schemas.

---

## 2. Standardized Error Contracts

All non-2xx responses conform to the canonical E-7 error envelope (`IMPLEMENTED_ENGINEERING_BEHAVIOR`):

```json
{
  "error": {
    "code": "domain.namespaced_error_code",
    "message": "Safe human-readable error description.",
    "correlation_id": "c7a8b9d0-1234-5678-90ab-cdef12345678",
    "details": [
      {
        "location": ["body", "field_name"],
        "code": "missing",
        "message": "Field required"
      }
    ]
  }
}
```

### 2.1 Standard HTTP Status Codes

| Status Code | Error Code Example | Description / Contract |
|---|---|---|
| `400 Bad Request` | `auth.conflicting_credentials` | Malformed syntax or conflicting authentication mechanisms |
| `401 Unauthorized` | `auth.credentials_missing`, `auth.token_invalid` | Missing, expired, or invalid credentials |
| `403 Forbidden` | `auth.insufficient_permissions`, `auth.tenant_mismatch` | RBAC failure or attempted cross-tenant boundary breach |
| `404 Not Found` | `request.not_found` | Resource does not exist within the authenticated tenant scope |
| `409 Conflict` | `idempotency.conflict` | Idempotency payload mismatch or concurrent modification |
| `413 Payload Too Large` | `request.payload_too_large` | Body size exceeds configured limit (default: 10 MB) |
| `422 Unprocessable Entity`| `request.validation_failed` | Pydantic schema validation failure with sanitized details |
| `429 Too Many Requests`| `capacity.quota_exceeded` | Tenant capacity quota exceeded. Response includes `Retry-After: 30` header (Strictly scoped to 4 capacity endpoints) |
| `500 Internal Server Error`| `internal.server_error` | Safe fallback envelope; raw tracebacks are never exposed to clients |

> [!IMPORTANT]
> **Capacity Quotas vs Rate Limiting:**
> - HTTP 429 (`capacity.quota_exceeded`) is an **application-layer resource quota** enforced strictly on four intensive endpoints (`/schedule-jobs`, `/schedules/{id}/repair`, `/goals/{id}/generate-plan`, `/workflows/instances`).
> - External Layer-7 volumetric rate limiting, per-IP DDoS throttling, and burst limits remain governed at the ingress edge: `RATE_LIMITING = BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION`.

---

## 3. Idempotency & Concurrency Governance

- **Idempotency Header:** Mutating POST/PATCH endpoints support `Idempotency-Key: <unique-token>`.
- **Request Fingerprinting:** The server calculates a SHA-256 hash of `(tenant_id, operation_name, body_payload)` stored in `idempotency_records`.
- **Replay Behavior:**
  - Matching key + matching fingerprint: Returns cached successful response.
  - Matching key + differing fingerprint: Returns `409 Conflict` (`idempotency.conflict`).

---

## 4. Canonical Mounted Operations Inventory (Complete 68 Paths)

The following table provides the exhaustive inventory of all 68 mounted endpoints derived from `app.main:app`:

| Path | Methods | Domain / Tag | Authentication | Quota 429 Scoped | Summary |
|---|---|---|---|---|---|
| `/` | GET | Root | Public | No | Engine status & liveness |
| `/api/v1/health` | GET | Health | Public | No | Process liveness probe |
| `/api/v1/ready` | GET | Health | Public | No | Database readiness probe |
| `/api/v1/auth/me` | GET | Auth | Bearer / Service Key | No | Get current authenticated principal identity |
| `/api/v1/analytics/operations` | GET | Analytics | Bearer / Service Key | No | Low-cardinality operational telemetry |
| `/api/v1/audit` | GET | Audit | Bearer / Service Key | No | Query chronological audit logs |
| `/api/v1/goals` | POST | Planning | Bearer / Service Key | No | Create operational planning goal |
| `/api/v1/goals/{goal_id}/generate-plan` | POST | Planning | Bearer / Service Key | **Yes (`capacity_tenant_max_active_plans: 3`)** | Trigger AI planner proposal |
| `/api/v1/plans/{plan_id}/validate` | POST | Planning | Bearer / Service Key | No | Deterministic plan validation |
| `/api/v1/plans/{plan_id}/approve` | POST | Planning | Bearer / Service Key | No | Human approval baseline transition |
| `/api/v1/plans/{plan_id}/submit-for-approval` | POST | Planning | Bearer / Service Key | No | Submit generated plan for approval |
| `/api/v1/plans/{plan_id}/tasks` | GET | Planning | Bearer / Service Key | No | List tasks for a plan |
| `/api/v1/plans/{plan_id}/critical-path` | GET | Planning | Bearer / Service Key | No | Compute Critical Path Method (CPM) |
| `/api/v1/tasks/{task_id}` | PATCH | Planning | Bearer / Service Key | No | Update task status or metadata |
| `/api/v1/schedule-jobs` | POST | Scheduling | Bearer / Service Key | **Yes (`capacity_tenant_max_queued_solves: 5`)** | Submit CP-SAT solver job |
| `/api/v1/schedule-jobs/{job_id}` | GET | Scheduling | Bearer / Service Key | No | Poll status of solve job |
| `/api/v1/schedule-jobs/{job_id}/publish` | POST | Scheduling | Bearer / Service Key | No | Authorize and publish schedule |
| `/api/v1/schedules/{schedule_id}/repair` | POST | Scheduling | Bearer / Service Key | **Yes (`capacity_tenant_max_queued_repairs: 3`)** | Targeted incremental repair |
| `/api/v1/resources/availability` | GET | Scheduling | Bearer / Service Key | No | Query blackout windows & availability |
| `/api/v1/integrations/calendar/google/webhook` | POST | Calendar | Webhook Token | No | Google Calendar push notifications |
| `/api/v1/workflows/definitions` | POST | Workflow | Bearer / Service Key | No | Register BPMN workflow definition |
| `/api/v1/workflows/definitions/{definition_id}` | GET | Workflow | Bearer / Service Key | No | Retrieve workflow definition |
| `/api/v1/workflows/definitions/{definition_id}/deploy` | POST | Workflow | Bearer / Service Key | No | Deploy definition to Flowable |
| `/api/v1/workflows/instances` | POST | Workflow | Bearer / Service Key | **Yes (`capacity_tenant_max_running_workflows: 20`)** | Start workflow process instance |
| `/api/v1/workflows/instances/{instance_id}` | GET | Workflow | Bearer / Service Key | No | Retrieve workflow instance state |
| `/api/v1/workflows/instances/{instance_id}/history` | GET | Workflow | Bearer / Service Key | No | Query workflow audit history |
| `/api/v1/workflows/tasks` | GET | Workflow | Bearer / Service Key | No | Query active human tasks |
| `/api/v1/workflows/tasks/{task_id}/actions` | POST | Workflow | Bearer / Service Key | No | Complete/claim human task |
| `/api/v1/workflows/tasks/{task_id}/action` | POST | Workflow | Bearer / Service Key | No | Complete/claim human task (alias) |
| `/api/v1/workflows/sla/policies` | GET, POST | Workflow | Bearer / Service Key | No | Manage workflow SLA policies |
| `/api/v1/workflows/validate-bpmn` | POST | Workflow | Bearer / Service Key | No | Validate raw BPMN XML |
| `/api/v1/workflows/callbacks` | POST | Workflow | Bearer / Service Key | No | Engine async callback receiver |
| `/api/v1/workflows/dmn/deploy` | POST | Workflow | Bearer / Service Key | No | Deploy DMN decision table |
| `/api/v1/workflows/dmn/execute` | POST | Workflow | Bearer / Service Key | No | Evaluate DMN decision table |
| `/api/v1/workflows/artifacts/bpmn` | POST | Workflow | Bearer / Service Key | No | Store BPMN artifact XML |
| `/api/v1/workflows/artifacts/bpmn/validate` | POST | Workflow | Bearer / Service Key | No | Validate BPMN artifact XML |
| `/api/v1/workflows/artifacts/dmn` | POST | Workflow | Bearer / Service Key | No | Store DMN artifact XML |
| `/api/v1/workflows/artifacts/dmn/deploy` | POST | Workflow | Bearer / Service Key | No | Deploy DMN artifact to Flowable |
| `/api/v1/workflows/artifacts/dmn/execute` | POST | Workflow | Bearer / Service Key | No | Execute DMN artifact rule |
| `/api/v1/workflows/artifacts/forms` | POST | Workflow | Bearer / Service Key | No | Store Form artifact schema |
| `/api/v1/workflows/artifacts/forms/validate` | POST | Workflow | Bearer / Service Key | No | Validate Form artifact schema |
| `/api/v1/workflow/definitions` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/definitions` |
| `/api/v1/workflow/definitions/{definition_id}` | GET | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/definitions/{id}` |
| `/api/v1/workflow/definitions/{definition_id}/deploy` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/definitions/{id}/deploy` |
| `/api/v1/workflow/instances` | POST | Workflow | Bearer / Service Key | **Yes (`capacity_tenant_max_running_workflows: 20`)** | Singular alias of `/workflows/instances` |
| `/api/v1/workflow/instances/{instance_id}` | GET | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/instances/{id}` |
| `/api/v1/workflow/instances/{instance_id}/history` | GET | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/instances/{id}/history` |
| `/api/v1/workflow/tasks` | GET | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/tasks` |
| `/api/v1/workflow/tasks/{task_id}/actions` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/tasks/{id}/actions` |
| `/api/v1/workflow/tasks/{task_id}/action` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/tasks/{id}/action` |
| `/api/v1/workflow/sla/policies` | GET, POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/sla/policies` |
| `/api/v1/workflow/validate-bpmn` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/validate-bpmn` |
| `/api/v1/workflow/callbacks` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/callbacks` |
| `/api/v1/workflow/dmn/deploy` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/dmn/deploy` |
| `/api/v1/workflow/dmn/execute` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/dmn/execute` |
| `/api/v1/workflow/artifacts/bpmn` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/artifacts/bpmn` |
| `/api/v1/workflow/artifacts/bpmn/validate` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/artifacts/bpmn/validate` |
| `/api/v1/workflow/artifacts/dmn` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/artifacts/dmn` |
| `/api/v1/workflow/artifacts/dmn/deploy` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/artifacts/dmn/deploy` |
| `/api/v1/workflow/artifacts/dmn/execute` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/artifacts/dmn/execute` |
| `/api/v1/workflow/artifacts/forms` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/artifacts/forms` |
| `/api/v1/workflow/artifacts/forms/validate` | POST | Workflow | Bearer / Service Key | No | Singular alias of `/workflows/artifacts/forms/validate` |

---

## 5. Domain Endpoints & Schemas

### 5.1 Planning & Task Intelligence Domain

#### `POST /api/v1/goals`
- **Summary:** Create an operational goal for task planning.
- **Auth:** Bearer or Service Key (`role: planner` or `role: admin`).
- **Response `201 Created`:** Goal record with `id`, `tenant_id`, `objective`, `target_window`.

#### `POST /api/v1/goals/{goal_id}/generate-plan`
- **Summary:** Trigger Task Intelligence AI planner to propose an operational plan.
- **Auth:** Bearer or Service Key (`role: planner`).
- **Capacity Quota:** Enforces tenant quota `capacity_tenant_max_active_plans: 3`. If exceeded, returns `429 Too Many Requests` with `Retry-After: 30`.
- **Response `200 OK`:** Returns proposed `PlanSchema` (status: `DRAFT` / `GENERATED`).

#### `POST /api/v1/plans/{plan_id}/validate`
- **Summary:** Execute deterministic validator against plan proposal.
- **Auth:** Bearer or Service Key (`role: planner`).
- **Response `200 OK`:** Validation result with `valid: true/false`, DAG cycle detection, duration bounds.

#### `POST /api/v1/plans/{plan_id}/approve`
- **Summary:** Human approval boundary transitioning draft plan to authoritative baseline.
- **Auth:** Bearer or Service Key (`role: planner` or `role: operator`).
- **Response `200 OK`:** Updated plan record with `is_baseline: true` and immutable `baseline_hash`.

#### `GET /api/v1/plans/{plan_id}/critical-path`
- **Summary:** Calculate Critical Path Method (CPM) for tasks in plan.
- **Auth:** Bearer or Service Key.
- **Response `200 OK`:** List of critical path task IDs, float duration, total plan duration.

#### `PATCH /api/v1/tasks/{task_id}`
- **Summary:** Update task status, progress percentage, or completion metadata.
- **Auth:** Bearer or Service Key.
- **Response `200 OK`:** Updated task record.

---

### 5.2 Workflows & Human Task Orchestration Domain

Mounted under canonical `/api/v1/workflows/*` and mirrored under singular alias `/api/v1/workflow/*`:

#### `POST /api/v1/workflows/definitions` & `POST /api/v1/workflow/definitions`
- **Summary:** Register BPMN 2.0 workflow definition in tenant registry.
- **Auth:** Bearer or Service Key (`role: admin` or `role: workflow_author`).
- **Request Body:** Contains `workflow_key`, `name`, `version`, `artifact_xml`.
- **Validation:** Validated against Flowable BPMN schema before insertion.

#### `GET /api/v1/workflows/definitions/{definition_id}` & `GET /api/v1/workflow/definitions/{definition_id}`
- **Summary:** Retrieve definition metadata and active status.
- **Auth:** Bearer or Service Key.

#### `POST /api/v1/workflows/definitions/{definition_id}/deploy` & `POST /api/v1/workflow/definitions/{definition_id}/deploy`
- **Summary:** Deploy validated definition to Flowable runtime engine.
- **Auth:** Bearer or Service Key (`role: admin`).
- **Response `200 OK`:** Returns `engine_definition_id` and deployment confirmation.

#### `POST /api/v1/workflows/instances` & `POST /api/v1/workflow/instances`
- **Summary:** Start a workflow instance for an operational process.
- **Auth:** Bearer or Service Key.
- **Capacity Quota:** Enforces `capacity_tenant_max_running_workflows: 20`. If exceeded, returns `429 Too Many Requests` with `Retry-After: 30`.
- **Response `201 Created`:** Instance record with `engine_instance_id`, `status: RUNNING`.

#### `GET /api/v1/workflows/instances/{instance_id}` & `GET /api/v1/workflow/instances/{instance_id}`
- **Summary:** Retrieve current instance execution state.
- **Auth:** Bearer or Service Key.

#### `GET /api/v1/workflows/instances/{instance_id}/history` & `GET /api/v1/workflow/instances/{instance_id}/history`
- **Summary:** Retrieve historical activity transitions and completed tasks.
- **Auth:** Bearer or Service Key.

#### `GET /api/v1/workflows/tasks` & `GET /api/v1/workflow/tasks`
- **Summary:** List active human tasks filtered by candidate role or assignee.
- **Auth:** Bearer or Service Key.
- **Response `200 OK`:** Array of tasks with `task_definition_key`, `form_key`, `due_at`.

#### `POST /api/v1/workflows/tasks/{task_id}/actions` & `POST /api/v1/workflow/tasks/{task_id}/actions` (alias `.../action`)
- **Summary:** Complete, claim, delegate, or reject a human task.
- **Auth:** Bearer or Service Key with Separation of Duties (SoD) verification.
- **Idempotency:** Supported via `Idempotency-Key`.
- **Response `200 OK`:** Action result, next task state or process completion.

#### `GET /api/v1/workflows/sla/policies` & `POST /api/v1/workflows/sla/policies` (also mirrored on `.../workflow/...`)
- **Summary:** Query and register SLA policies with breach escalation triggers.
- **Auth:** Bearer or Service Key.

#### `POST /api/v1/workflows/validate-bpmn` & `POST /api/v1/workflow/validate-bpmn`
- **Summary:** Validate raw BPMN XML structure without persisting.

#### `POST /api/v1/workflows/callbacks` & `POST /api/v1/workflow/callbacks`
- **Summary:** Receive asynchronous callbacks from external workers or Flowable engine.

#### `POST /api/v1/workflows/dmn/deploy` & `POST /api/v1/workflow/dmn/deploy`
- **Summary:** Deploy DMN 1.3 decision table to engine.

#### `POST /api/v1/workflows/dmn/execute` & `POST /api/v1/workflow/dmn/execute`
- **Summary:** Execute DMN 1.3 decision table evaluation.

#### `POST /api/v1/workflows/artifacts/bpmn`, `/dmn`, `/forms` (and `/validate`, `/deploy`, `/execute` endpoints)
- **Summary:** CRUD, validation, deployment, and execution of versioned BPMN, DMN, and Form artifacts.

---

### 5.3 Scheduling & Resource Optimization Domain

#### `POST /api/v1/schedule-jobs`
- **Summary:** Submit asynchronous scheduling solve job to OR-Tools CP-SAT queue.
- **Auth:** Bearer or Service Key (`role: scheduler` or `role: planner`).
- **Capacity Quota:** Enforces `capacity_tenant_max_queued_solves: 5`. If exceeded, returns `429 Too Many Requests` (`capacity.quota_exceeded`, `Retry-After: 30`).
- **Response `202 Accepted`:** Job record with `job_id`, `status: REQUESTED`.

#### `GET /api/v1/schedule-jobs/{job_id}`
- **Summary:** Poll status of asynchronous schedule solve job.
- **Response `200 OK`:** Job status (`REQUESTED`, `SOLVING`, `COMPLETED`, `FAILED`, `BUDGET_EXHAUSTED`), score components, solver metadata.

#### `POST /api/v1/schedule-jobs/{job_id}/publish`
- **Summary:** Authorize and publish candidate schedule, establishing active calendar baseline.
- **Auth:** Bearer or Service Key (`role: scheduler` or `role: admin`).
- **Locking:** Enforces publication lock. Dispatches outbox event `schedule.published`.

#### `POST /api/v1/schedules/{schedule_id}/repair`
- **Summary:** Execute targeted incremental schedule repair with locked activity pins (`locked_moved = 0`).
- **Auth:** Bearer or Service Key.
- **Capacity Quota:** Enforces `capacity_tenant_max_queued_repairs: 3`. If exceeded, returns `429 Too Many Requests` with `Retry-After: 30`.
- **Time Budget:** Default 30.0s (`capacity_solver_repair_time_budget_seconds`).

#### `GET /api/v1/resources/availability`
- **Summary:** Query resource availability intervals and blackout windows.

---

### 5.4 Analytics & Operations Dashboard

#### `GET /api/v1/analytics/operations`
- **Summary:** Retrieve operational dashboard telemetry: active plans, overdue tasks, solver queue depth, SLA breaches.
- **Auth:** Bearer or Service Key (`role: auditor`, `role: operator`, or `role: admin`).
- **Response `200 OK`:** Low-cardinality aggregation metrics scoped to authenticated tenant.

---

### 5.5 Audit Domain

#### `GET /api/v1/audit`
- **Summary:** Query chronological, immutable audit events.
- **Auth:** Bearer or Service Key (`role: auditor` or `role: admin`).
- **Parameters:** `actor_id`, `resource_type`, `resource_id`, `since`, `until`, `limit`.
- **Response `200 OK`:** Paginated audit log records.

---

### 5.6 Calendar Integrations & Callbacks

#### `POST /api/v1/integrations/calendar/google/webhook`
- **Summary:** Receive Google Calendar push notification webhooks.
- **Auth:** Webhook channel token verification (`X-Goog-Channel-Token` and channel ID mapping).
- **Processing:** Asynchronously dispatches reconciliation event to worker queue.

---

### 5.7 Operational Health & Readiness

#### `GET /api/v1/health`
- **Summary:** Liveness probe verifying process responsiveness.
- **Auth:** None (Public).
- **Response `200 OK`:** `{"status": "ok", "service": "operations-engine", "environment": "production"}`

#### `GET /api/v1/ready`
- **Summary:** Readiness probe executing non-leaking database `SELECT 1` check.
- **Auth:** None (Public).
- **Response `200 OK`:** `{"status": "ready", "service": "operations-engine", "environment": "production"}`
- **Failure `503 Service Unavailable`:** If database connection cannot be acquired within 3.0s.

#### `GET /`
- **Summary:** Root process status and metadata.
- **Auth:** None (Public).
- **Response `200 OK`:** `{"engine": "E-7 Operations Engine", "status": "online", "docs": "/docs"}`
