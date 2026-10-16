# ADR-011: Integration Governance, BLK-01 Resolution & MySQL ERP/SIS Adapter Architecture

## Status
Accepted

## Date
2026-10-07

## Context
Phase 7 introduces operational real-time external integrations for the IR Infotech E-7 Operations Engine. The first concrete upstream Enterprise Resource Planning / Student Information System (ERP/SIS) data source is:
- **Logical Provider**: `mysql_iredu`
- **Engine**: MySQL 8.0 (InnoDB)
- **Schema**: `iredu` (247 tables, 2,637 columns, 307 foreign keys; Dump20260924 baseline)
- **Access Policy**: STRICTLY READ_ONLY

PostgreSQL remains the sole canonical operational System of Record (SoR) for E-7 (ADR-006). `mysql_iredu` is authoritative solely for upstream institutional master records (curricula, grade structures, courses, sanitized staff metadata, and enrollment headcounts).

Before enabling live MySQL queries, Phase 7A requires resolving production blocker **BLK-01 / UBQ-TI-01**:
> *"Authoritative tenant/organization scoping predicate is unresolved. Do NOT enable live production MySQL retrieval before resolving BLK-01."*

## BLK-01 Investigation & Evolution of Resolution

We conducted a comprehensive audit of all schema inventory artifacts (`docs/integrations/MYSQL_SCHEMA_INVENTORY.md` / `.json`), data mapping contracts (`MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md`), adapter contracts (`MYSQL_ERP_ADAPTER_CONTRACT.md`), context builder specifications (`CONTEXT_BUILDER_CONTRACT.md`), and repository source documentation.

### Previous Status: Outcome D (No Authoritative Tenant Mapping Available)
Prior to authoritative deployment confirmation:
- **Outcome A (Authoritative Column in Schema)**: NEGATIVE. Zero of 247 tables contain a native `tenant_id` or multi-institution foreign key.
- **Outcome B (Single-Tenant Deployment)**: UNCONFIRMED. Although `sch_settings` suggested single-school architecture, no authoritative deployment confirmation had been provided.
- **Outcome C (Approved Tenant Mapping Registry)**: NEGATIVE. No external tenant registry was configured.
- **Outcome D (Fail-Closed Blocker)**: CONFIRMED. BLK-01 remained an active production blocker.

### New Authoritative Deployment Evidence
The upstream `mysql_iredu` database is authoritatively confirmed to be **dedicated to EXACTLY ONE institution only**. This deployment is not a shared multi-institution database.

### New Resolution: Outcome B — Confirmed Single-Tenant Deployment
Under this authoritative confirmation:
1. **Database / Deployment Boundary Isolation**:
   - Tenant isolation occurs at the **DATABASE / DEPLOYMENT boundary**, not at the row level.
   - Row-level tenant partitioning predicates (`WHERE tenant_id = :id`) are unnecessary for this deployment because all permitted records in this database instance belong exclusively to the single institution.
   - We do **not** claim schema-level tenant isolation.
2. **Canonical E-7 Tenant Binding**:
   - E-7 explicitly binds exactly one canonical `tenant_id` to this dedicated database instance via configuration (`E7_MYSQL_ERP_SINGLE_TENANT_ID`).
   - We do **not** invent fake organization IDs (e.g. no fabricated `"ORG-INSTITUTION-001"`). The scope reference represents the dedicated deployment boundary (`dedicated:iredu`).
3. **Fail-Closed Gate**:
   - Only the configured canonical tenant is permitted to execute queries against this database connection.
   - Any other tenant, mismatched tenant, or unconfigured tenant **FAILS CLOSED** (`ScopeUnresolvedError`) before issuing any MySQL query.
   - Missing connection credentials fail closed (`ErpConnectionError`).
4. **Non-Authorization of Shared Multi-Tenancy**:
   - This resolution applies strictly to a dedicated, single-institution database instance.
   - It does **NOT** authorize future shared or multi-tenant deployments.
5. **Mandatory Reopening Constraint**:
   - **If this MySQL database is ever shared across multiple institutions, BLK-01 automatically reopens and production reads must fail closed until an authoritative partition or mapping is introduced.**

## Decision

1. **Resolution of Production Blocker BLK-01**:
   - BLK-01 is **RESOLVED_BY_CONFIRMED_SINGLE_TENANT_DEPLOYMENT** (Outcome B).
   - The blocker `BLOCKED_ON_BLK_01` is removed from the Phase-7 blocker list.
   - Note: Physical live read verification remains separately dependent on provisioned MySQL credentials (`BLOCKED_ON_MYSQL_CONNECTION_CREDENTIALS`), which does not reopen BLK-01.

2. **Single-Tenant Configuration & Fail-Closed Enforcement**:
   - `MySqlErpSettings` provides `single_tenant_id: str`.
   - `MySqlIreduAdapter._verify_tenant_scope` verifies:
     - If `clean_tenant == single_tenant_id`: request is authorized against the dedicated database (`dedicated:<database>`).
     - If `clean_tenant != single_tenant_id`: raises `ScopeUnresolvedError("BLK-01: Tenant mismatch")`.
     - If `single_tenant_id` is empty and no approved mapping exists: raises `ScopeUnresolvedError("BLK-01: Authoritative organization scope is unresolved")`.
     - If `tenant_id` is blank or whitespace: raises `ScopeUnresolvedError("BLK-01: Missing tenant_id")`.
     - Passing an arbitrary `scope_reference` cannot bypass the `single_tenant_id` binding.

3. **Read-Only Security Boundary**:
   - The `mysql_iredu` adapter (`MySqlIreduAdapter`) is strictly read-only:
     - Enforces read-only transactions on live connections (`SET SESSION TRANSACTION READ ONLY`).
     - Strictly prohibits `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `GRANT`, `REVOKE`, `CALL`, or DDL/DML mutation.
     - Prohibits arbitrary SQL, LLM-authored SQL, user-supplied raw SQL, and dynamic table/column interpolation.
     - All queries are bounded, predeclared, parameterized constant queries.
   - Credentials are read solely from environment variables (`E7_MYSQL_ERP_*`) via Pydantic settings and are never logged or exposed to LLM context.

4. **Data-Mapping Contract Preservation**:
   - Exactly the 14 `REQUIRED_FOR_INITIAL_TI` tables are queried for initial context.
   - Exactly the 28 `PLANNER_CONTEXT_ALLOWED` fields may be exposed to the planner context.
   - Students and student enrollment are strictly `AGGREGATE_ONLY` (mathematical headcounts per class-section; zero individual student PII).
   - Staff records are sanitized: private PII (payroll, national ID, bank details, medical notes) is strictly excluded at the adapter query boundary.

5. **Provider-Neutral External Identity Mapping**:
   - Raw MySQL primary keys are never treated as canonical E-7 IDs.
   - External identities are projected into typed `ExternalIdentityMappingDTO` records and canonical URNs (`urn:e7:<tenant_id>:mysql_iredu:<external_type>:<external_id>`).
   - Conceptual mapping fields (`provider`, `external_entity_type`, `external_id`, `canonical_entity_id`, `tenant_id`, `last_seen_at`, `source_version`, `source_updated_at`) are preserved.

## Consequences
- **Positive**: BLK-01 is soundly resolved under confirmed single-tenant deployment governance without inventing fake entity identifiers or compromising tenant isolation.
- **Security**: Isolation at the database boundary prevents cross-tenant data leakage while ensuring any mismatched tenant fails closed before query execution.
- **Phase-7 Status Impact**:
  - `BLK_01` is updated to `RESOLVED_BY_CONFIRMED_SINGLE_TENANT_DEPLOYMENT`.
  - Phase 7 status becomes: `IMPLEMENTED + BLOCKED_ON_GOOGLE_CALENDAR_CREDENTIALS + BLOCKED_ON_E8_ENDPOINT`.
  - Engineering readiness for Phase 8 remains `NOT_READY`, and Phase 8 remains `UNSTARTED`.

