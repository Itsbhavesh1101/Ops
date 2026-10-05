# ADR-006: Canonical System of Record — PostgreSQL

## Status
Accepted

## Date
2026-09-16

## Context
The E-7 Operations Engine coordinates complex operational data across three integrated subsystems: Task Intelligence, Workflow Management, and Constraint Scheduling. In addition, external systems (such as Flowable runtime tables, UniTime timetabling databases, external ERP databases, and third-party calendar servers) each possess their own native data models.

Without an unambiguous, central system of record, application state fragments across multiple third-party databases, leading to synchronization drift, split-brain consistency bugs, and severe vendor lock-in. A single, authoritative relational persistence layer is required to enforce multi-tenant boundaries, ACID transactions, outbox atomicity, and immutable audit trails.

## Decision
1. **PostgreSQL 16 (PostgreSQL License)** is selected as the **single canonical system of record** for all E-7 transactional business data, domain state, and operational history.
2. PostgreSQL owns canonical persistence for all core domain aggregates:
   - `tenants`, `users`
   - `goals`, `plans`, `tasks`, `task_dependencies`, `milestones`, `task_risks`
   - `workflow_definitions`, `workflow_instances`, `workflow_tasks`, `sla_policies`
   - `resources`, `resource_availability`
   - `schedule_jobs`, `schedule_assignments`, `constraints`
   - `calendar_links`, `domain_events` (outbox), `audit_events`
3. **External Runtimes Are Not Canonical Stores**: Native databases or internal tables belonging to third-party tools (such as Flowable's `ACT_*` tables, UniTime's internal schemas, or external ERP databases) own runtime operational execution but are **never** treated as E-7's authoritative persistence model.
4. All cross-engine entity references must use canonical E-7 UUIDs. Native third-party identifiers (e.g., Flowable process instance ID, UniTime assignment ID, ERP ID) are stored strictly as foreign external mapping attributes.
5. **Standard Relational Design**: All schemas are defined in standard SQL/SQLAlchemy models without proprietary vendor-locked extensions. (Extensions like `pgvector` remain optional until Phase 10).

## Decision Details
* **Async Engine**: Data access uses SQLAlchemy 2.0 with asynchronous drivers (`asyncpg`) for API request pathways, and sync drivers (`psycopg`) for Alembic migrations.
* **Multi-Tenancy Enforcement**: Every query is filtered by `tenant_id` at the repository layer.
* **Migration Governance**: All schema evolutions must be tracked via versioned Alembic migration scripts. Destructive migrations (dropping tables/columns) are prohibited without multi-phase deprecation.

## Authority Boundary
* **PostgreSQL / E-7 Core Owns**:
  - Authoritative lifecycle state for plans, workflows, tasks, and schedules.
  - Transactional consistency of outbox events and audit logs.
  - Tenant isolation and relational referential integrity.
* **Third-Party Runtimes (Flowable, UniTime, ERP) Own**:
  - Transient engine execution state, internal token positions, and solver intermediate scratchpad data.

## Configuration / No-Hardcoding Implications
* Database host, port, credentials, database name, and connection pool sizing must be injected strictly via environment variables (`DATABASE_URL`, `DATABASE_SYNC_URL`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`).
* Database connection strings must never be committed to source code or configuration files.

## Consequences

### Positive
* **ACID Guarantees**: Complete transactional consistency across task creation, workflow transitions, and outbox event emission.
* **Permissive Open Source**: PostgreSQL's license provides complete freedom for enterprise commercial distribution without copyleft obligations.
* **Unified Queryability**: Enables operational analytics, audit trails, and multi-tenant management across all three unified engines in a single database.
* **Vendor Independence**: Core domain models remain completely independent of Flowable or external solver database schemas.

### Negative / Trade-offs
* Requires bidirectional mapping between canonical E-7 entities and runtime-specific tables (e.g., synchronizing task completion between E-7 `workflow_tasks` and Flowable `ACT_RU_TASK`).

## Rejected or Deferred Alternatives
* **Directly Exposing Flowable `ACT_*` Tables as E-7 Workflow Model**: Rejected. Tightly couples E-7 to Flowable's internal Java/Hibernate schema, breaking the adapter boundary and preventing future engine migration.
* **NoSQL / Document Store as System of Record**: Rejected. Lacks the strict relational integrity, foreign key constraints, and multi-entity ACID transactions required for operations management.
* **Separate Databases per Micro-Service in Phase 1**: Deferred. A shared PostgreSQL instance with logical schema boundaries provides simpler transaction management and atomic outbox writes for the initial platform.

## Conditions for Reconsideration
Alternative canonical persistence engines will not be considered. Database sharding or read-replica scaling may be evaluated in Phase 8 if tenant scale mandates distributed data partitioning.

## Implementation Phase
* **Phase 1: Platform Foundation** (SQLAlchemy models, Alembic migrations, database connection pooling, tenant repository base).
* **Phase 8: Hardening** (Point-in-time recovery testing, index optimization, connection pool tuning).

## Validation / Evidence Required
* `tests/integration/db/test_tenant_isolation.py`: Verified that queries filtered by tenant never leak records from other tenants.
* `tests/integration/db/test_migrations.py`: Full Alembic upgrade and downgrade migrations execute with zero SQL errors.
* `tests/integration/db/test_acid_transactions.py`: Rollback correctly unwinds state and outbox writes across concurrent sessions.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///F:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) (All Functional Requirements)
* *Complete Specification*, Section 14 (Canonical Data Model).
* *Complete Implementation Plan*, Section 4 & Section 5.2.
