# E-7 Operations Engine — Canonical Runtime Data Policy

**Document Reference:** `docs/data/RUNTIME_DATA_POLICY.md`
**Security Classification:** Highly Restricted / Architectural Invariant
**Enforcement Level:** Mandatory System-Wide Policy
**Effective Date:** 2026-10-20

---

## 1. Executive Summary & Policy Invariants

The E-7 Operations Engine enforces a strict **Zero-Synthetic Runtime Policy**. Under no circumstances may production, staging, demonstration, or pilot environments generate, inject, fall back to, or present synthetic, mock, or hard-coded business data.

```yaml
CANONICAL DATA POLICY:
  RUNTIME_DATA_SOURCE: REAL_DATABASE_ONLY
  MOCK_DATA_IN_PRODUCTION: FORBIDDEN
  SYNTHETIC_DATA_IN_PRODUCTION: FORBIDDEN
  FIXTURE_FALLBACK_IN_PRODUCTION: FORBIDDEN
  FAKE_ERP_IN_PRODUCTION: FORBIDDEN
  SOURCE_DB_WRITE_PATHS: NONE
```

Every operational entity consumed by the frontend, processed by workflow state machines, evaluated by task intelligence, or solved by the CP-SAT scheduling engine must originate from one of two authoritative sources:
1. **Authoritative External Database:** Read-only queries against the live institutional source database (`iredu` MySQL on dedicated cloud host `51.75.71.49:3306`).
2. **Canonical PostgreSQL Database:** User-created, operator-configured, or workflow-generated records persisted in E-7's PostgreSQL system of record (`operations_engine`).

---

## 2. Permitted Data Origins

| Category | Authoritative Origin | Ingestion Mechanism | Permitted Uses |
| :--- | :--- | :--- | :--- |
| **Institutional Staff** | `iredu.staff` (MySQL `51.75.71.49`) | `MySqlIreduAdapter` via `ExternalIdentityMapper` (`provider: mysql_iredu`) | Invigilation roster, proctoring assignments, teacher constraints |
| **Academic Calendar** | `iredu.sessions` (MySQL `51.75.71.49`) | `MySqlIreduAdapter.get_academic_sessions` | Planning horizons, academic year scoping, semester milestones |
| **School Organization** | `iredu.sch_settings` (MySQL `51.75.71.49`) | `MySqlIreduAdapter.get_organization_context` | Tenant metadata, institutional name, active session binding |
| **Curricular Hierarchy** | `iredu.classes`, `sections`, `subjects` | `MySqlIreduAdapter.get_classes_and_sections` | Cohort definition, subject constraints, course scheduling demand |
| **Physical Facilities / Rooms** | E-7 PostgreSQL `resources` table | User-created via REST API or UI (`type="ROOM"`, `facility:room:...`) | Examination halls, laboratories, lecture rooms |
| **Operational Goals & Plans** | E-7 PostgreSQL `goals`, `plans`, `tasks` | Human operator submission via Web UI / API | Work breakdown structures, milestone execution, CPM calculation |
| **Schedules & Assignments** | E-7 PostgreSQL `schedule_jobs`, `schedules` | CP-SAT solver compilation & persistence | Authoritative timetables, room assignments, proctor schedules |
| **Human Workflow Tasks** | Flowable REST Engine / E-7 PostgreSQL | Flowable BPMN workflow execution & task inbox | Human approvals, sign-offs, manual reviews |
| **Audit & Outbox Logs** | E-7 PostgreSQL `audit_events`, `outbox_events` | Append-only event store within transactional units | Non-repudiation, event streaming, compliance audits |

---

## 3. Prohibited Data Origins in Runtime

The following practices are **STRICTLY FORBIDDEN** across all runtime code paths:

1. **Hard-Coded Business Entities:**
   - No hardcoded UUIDs, names, room codes, or faculty names in components, endpoints, or services.
   - Example Violation: Static array `MOCK_INBOX_TASKS` in UI components or hardcoded `"Grade 10 Exam"` WBS templates in planners.
2. **In-Memory Fallback Adapters:**
   - Services must never catch a database exception and substitute static in-memory objects.
   - Example Violation: Catching empty database query results and substituting `ResourceService.list_resources` in-memory mocks.
3. **Synthetic Fixture Ingestion at Startup:**
   - Application startup scripts, Docker entrypoints, and demo environments must not bootstrap synthetic JSON fixtures (such as `PHASE5-SC-EXAM-FEASIBLE-001.json` or `PHASE5-RS-EDU-001.json`) into production/demo tenants.
4. **Synthetic Context Builders:**
   - AI and LLM planners must never utilize `SyntheticContextAdapter` in live runtime paths. If institutional ERP context is unavailable, planners must return an explicit degraded state.
5. **Fake External API Responses:**
   - External ERP, OIDC, or workflow adapters must never return stubbed successful responses when the underlying remote system is unreachable.

---

## 4. Permitted Scope of Test Fixtures & Synthetic Data

Synthetic data and test fixtures are permitted **STRICTLY AND EXCLUSIVELY** within:
- `tests/unit/`
- `tests/integration/`
- `tests/e2e/`
- `benchmarks/`

### Boundary Isolation Rules:
- Test fixtures (e.g., `tests/fixtures/scheduling/education/`) must **NEVER** be imported by modules in `app/`, `ui/`, or production startup scripts.
- When running unit tests in isolation, mock adapters are permissible only within pytest fixtures or test harness files (`tests/harness/`, `tests/fixtures/`).
- Frontend test files (`src/**/*.test.tsx`) may use local test mock handlers (e.g., Mock API client), provided the production component code (`src/**/*.tsx`) exclusively calls canonical API clients.

---

## 5. Behavior on Missing Data or Degraded Integrations

When required business entities are absent or remote services are unavailable, the system must behave deterministically without synthesizing data:

```mermaid
graph TD
    Req[Incoming Client Request] --> DBQuery[Execute Database / ERP Query]
    DBQuery -->|Records Found| ReturnReal[Return 200 OK with Canonical Records]
    DBQuery -->|Zero Records in DB| ReturnEmpty[Return 200 OK with Empty List '[]']
    DBQuery -->|Network / Auth Error| ReturnError[Raise Explicit ERP/DB Error]
    ReturnEmpty --> UIEmpty[UI Renders Genuine Empty State & Creation CTA]
    ReturnError --> UIErr[UI Renders Degraded/Unavailable Banner]
```

1. **Empty Database State:**
   - If a tenant has no goals, resources, plans, or tasks in PostgreSQL, endpoints must return `HTTP 200 OK` with an empty collection `[]`.
   - The UI must render a genuine empty state (e.g., *"No resources registered. Create your first resource"*), **NEVER** sample cards.
2. **ERP Unavailability:**
   - If the external MySQL ERP database is unreachable or times out, `MySqlIreduAdapter` must raise `ErpConnectionError` or `ErpTimeoutError`.
   - E-7 records `staleness_state = OFFLINE` or `DEGRADED` in `ErpFreshnessMetadataDTO`.
   - The system must display an explicit integration degraded banner. It must **NEVER** fall back to synthetic roster data.
3. **Solver Unsatisfiability:**
   - If insufficient resources exist to satisfy all activities, the CP-SAT solver must return `INFEASIBLE` with identified conflict constraints.
   - The system must **NEVER** invent phantom rooms or staff to force a `FEASIBLE` status.

---

## 6. Source Database Read-Only Governance

The connection to the institutional MySQL database (`iredu` on `51.75.71.49`) is protected through strict application-level write prohibitions:

1. **Session-Level Read-Only:** Every database connection executes `SET SESSION TRANSACTION READ ONLY` immediately upon acquisition.
2. **Regex Query Gate:** Every SQL statement is checked prior to execution against mutation patterns (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `REPLACE`, `CREATE`). Any matching statement raises `ReadOnlyViolationError`.
3. **Zero Write Paths:** There are zero code paths, repositories, or background tasks capable of writing to the source database (`SOURCE_DB_WRITE_PATHS = NONE`).
4. **Privilege Classification Distinctions:**
   - **APPLICATION_ENFORCED_READ_ONLY:** YES (Enforced via session state, regex statement parsing, and zero mutating code paths).
   - **DATABASE_ACCOUNT_SELECT_ONLY:** NO (`SHOW GRANTS` verifies account has database-wide privileges).
   - **Production Access Blocker:** `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_COMPANY_READ_ONLY_ACCESS` pending provision of a dedicated SELECT-only database user.

---

## 7. Tenant Data Classification & Runtime Isolation

The E-7 architecture clearly distinguishes between database-wide content and active demo tenant content:

- **Active Demo Runtime (`tenant-demo-real-001`):**
  The accepted demo runtime is isolated to a clean real-data tenant. Runtime business APIs contain no synthetic fallback, and representative demo records are provenance-traced to authoritative source data, legitimate user-created records, or deterministic derived outputs.
- **Historical Test/Pilot Data (`tenant-pilot-001`):**
  Historical pilot/test records remain preserved in PostgreSQL for compliance and forensic continuity. Strong tenant isolation guarantees that historical pilot records cannot appear in active demo tenant queries (`CROSS_TENANT_SYNTHETIC_VISIBILITY = 0`). Historical pilot data is not part of the demo runtime.

---

## 8. Compliance Verification & Audit Checklist

All future Pull Requests and migrations must verify:
- [ ] No `tests/fixtures/` imports outside of `tests/` and `benchmarks/`.
- [ ] No static fallback arrays in UI components.
- [ ] All API endpoints return `[]` (empty list) when database tables contain zero rows.
- [ ] All resource mapping preserves `attributes.external_mapping` with provider attribution.
- [ ] No student PII is persisted or logged; student data remains aggregate-only.
- [ ] Automated CI verifies that zero synthetic rows are seeded into clean demo tenants.
- [ ] Active demo identities strictly map to `tenant-demo-real-001`.
