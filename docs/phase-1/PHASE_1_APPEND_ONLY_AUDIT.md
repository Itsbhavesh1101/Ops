# Phase 1 Task 1F — Append-Only Audit Foundation

## 1. Executive Summary & Purpose

Task 1F implements the first intentional operational persistence model for the E-7 Operations Engine: the **Append-Only Audit Foundation**.
This persistence capability provides a tamper-resistant operational and security evidence trail recording significant events, state transitions, security boundary actions, and operational requests across the engine.

Audit records capture E-7 operational and security evidence; they **do not replace**:
- Flowable native workflow execution history
- ERP / SIS native system of record history
- Google Workspace native audit/calendar logs
- External identity provider authentication/event logs

---

## 2. Authority & Ownership Boundary

PostgreSQL is the canonical operational persistence store for the E-7 audit trail. E-7 owns the schema, lifecycle, and enforcement of the `audit_events` table.
- **Append-Only Invariant**: Audit records are immutable once persisted. Immutability is enforced at two distinct layers:
  1. **Application / Repository Contract**: The [`AuditRepository`](file:///f:/operations-engine/app/audit/repository.py) exposes exclusively `append()`. No `update`, `modify`, or `delete` methods exist in the application layer.
  2. **Database Immutability Trigger**: PostgreSQL migration `0002_create_audit_events` installs a database-level statement trigger (`trg_audit_events_prevent_mutation`) backed by `fn_prevent_audit_event_mutation()` that unconditionally raises an exception on any `UPDATE`, `DELETE`, or `TRUNCATE` operation.
- **No Cryptographic Tamper-Evidence Claim**: Task 1F implements database- and application-enforced append-only guarantees under the normal application/database role model. It does **not** implement row-level cryptographic hash chaining, Merkle trees, digital signatures, blockchain ledgers, or tamper-evidence against privileged database administrators who have authority to alter schema or disable triggers.

---

## 3. Canonical Schema: `audit_events`

The schema is implemented via SQLAlchemy model [`AuditEventRecord`](file:///f:/operations-engine/app/audit/models.py) mapping to table `audit_events`:

| Column Name | SQL Data Type | Nullable | Default | Description |
| :--- | :--- | :---: | :--- | :--- |
| `audit_event_id` | `UUID` | **NO** | App-generated | Primary Key; persistence-generated UUID (`uuid4()`) unconditionally assigned by `AuditRepository.append()`. Callers cannot supply or override. |
| `schema_version` | `INTEGER` | **NO** | `1` | Schema format version. Enforced positive (`schema_version > 0`). |
| `recorded_at` | `TIMESTAMPTZ` | **NO** | `now()` | Authoritative database recording timestamp (timezone-aware). |
| `tenant_id` | `VARCHAR(255)` | YES | `NULL` | Canonical tenant reference. Nullable only for system-scoped events. No premature FK. |
| `actor_principal_id` | `VARCHAR(255)` | YES | `NULL` | Canonical principal ID of actor if authenticated. Nullable for system events. No premature FK. |
| `actor_principal_type` | `VARCHAR(32)` | YES | `NULL` | Bounded string classification: `HUMAN` or `SERVICE`. (Not a PostgreSQL enum). |
| `authentication_method` | `VARCHAR(32)` | YES | `NULL` | Bounded string mechanism: `OIDC` or `SERVICE_API_KEY`. (Not a PostgreSQL enum). |
| `event_type` | `VARCHAR(128)` | **NO** | — | Dot-separated namespaced action (e.g. `auth.access.denied`). Enforced non-blank. |
| `resource_type` | `VARCHAR(128)` | YES | `NULL` | Canonical bounded identifier (e.g. `schedule.run`). URLs and SQL prohibited. |
| `resource_id` | `VARCHAR(255)` | YES | `NULL` | Opaque resource reference string. |
| `outcome` | `VARCHAR(32)` | YES | `NULL` | Provider-neutral classification: `SUCCESS`, `FAILURE`, `DENIED`. |
| `correlation_id` | `VARCHAR(128)` | YES | `NULL` | Request/transaction correlation identifier shared across operations. |
| `details` | `JSONB` | **NO** | `'{}'::jsonb` | Safe structured JSON context adhering to strict size and privacy limits. |

### 3.1 Check Constraints
1. `ck_audit_events_schema_version_positive`: `schema_version > 0`
2. `ck_audit_events_event_type_not_blank`: `length(trim(event_type)) > 0`
3. `ck_audit_events_actor_consistency`:
   `(actor_principal_id IS NULL AND actor_principal_type IS NULL AND authentication_method IS NULL) OR (actor_principal_id IS NOT NULL AND tenant_id IS NOT NULL AND actor_principal_type IS NOT NULL AND authentication_method IS NOT NULL)`
4. `ck_audit_events_actor_principal_type_valid`: `actor_principal_type IS NULL OR actor_principal_type IN ('HUMAN', 'SERVICE')`
5. `ck_audit_events_authentication_method_valid`: `authentication_method IS NULL OR authentication_method IN ('OIDC', 'SERVICE_API_KEY')`
6. `ck_audit_events_outcome_valid`: `outcome IS NULL OR outcome IN ('SUCCESS', 'FAILURE', 'DENIED')`

### 3.2 Canonical Indexes
1. `ix_audit_events_tenant_id_recorded_at`: `(tenant_id, recorded_at)` — Tenant-scoped chronological timeline queries.
2. `ix_audit_events_correlation_id`: `(correlation_id)` — Distributed operation trace correlation.
3. `ix_audit_events_resource_type_resource_id_recorded_at`: `(resource_type, resource_id, recorded_at)` — Resource-specific audit trail lookups.

---

## 4. Privacy, Secrets & Details Validation

Audit details are evidence context, not an arbitrary document store. All `details` payloads must pass defensive application validation via [`validate_audit_details`](file:///f:/operations-engine/app/audit/validation.py):

1. **Recursive Secret & Credential Denylist**:
   All dictionary keys are normalized (lowercased, stripping `_`, `-`, and spaces) and checked against denied tokens:
   - Prohibited tokens: `password`, `passwd`, `secret`, `clientsecret`, `apikey`, `accesstoken`, `refreshtoken`, `idtoken`, `authorization`, `cookie`.
   - Variations like `Api_Key`, `access-token`, `CLIENT_SECRET`, and `Authorization` are rejected at any nesting depth.
2. **Defensive Technical Bounds**:
   - `MAX_AUDIT_DETAILS_BYTES = 65536` (64 KiB maximum serialized JSON size).
   - `MAX_AUDIT_DETAILS_DEPTH = 8` (maximum nested depth).
   - `MAX_AUDIT_STRING_LENGTH = 4096` (maximum string value length).
3. **JSON Primitive Type Strictness & Strict Float Validation**:
   - Only JSON-compatible primitives (`None`, `bool`, `int`, `float`, `str`, `list`, `dict`) with string keys are permitted. Arbitrary Python objects, ORM entities, Exception instances, sets, and request objects are rejected.
   - Non-finite floating-point numbers (`float("nan")`, `float("inf")`, `float("-inf")`) are strictly rejected recursively using `math.isfinite()`.
   - JSON serialization is enforced with `allow_nan=False` in `json.dumps()`.
   - Booleans (`True`, `False`) are explicitly preserved as JSON booleans.
4. **Privacy Minimization**:
   Full PII (student records, phone numbers, addresses, chat messages, raw request bodies) is prohibited from audit records. Only opaque canonical IDs and bounded operational descriptors are recorded.

---

## 5. Input Schema & Identity Consistency

Input contract [`AuditEventCreate`](file:///f:/operations-engine/app/audit/schemas.py) is immutable (`frozen=True, extra="forbid"`):
- `audit_event_id`: Intentionally omitted from the caller input contract. Callers cannot supply or override the primary key. Supplying `audit_event_id` is rejected because extra fields are forbidden.
- `recorded_at`: Intentionally omitted; authored by database server default (`now()`).
- `event_type`: Validated against `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`.
- **Identity Consistency Rules**:
  - **Authenticated Events**: When `actor_principal_id is not None`, the full canonical actor context is strictly required: `tenant_id`, `actor_principal_type` (`HUMAN` or `SERVICE`), and `authentication_method` (`OIDC` or `SERVICE_API_KEY`) must all be non-null.
  - **Actorless System Events**: When `actor_principal_id is None`, `actor_principal_type` and `authentication_method` must both be `None`.
    - **Global System Events**: `tenant_id is None`.
    - **Tenant-Scoped System Events**: `tenant_id` is a valid tenant identifier string.

---

## 6. Repository & Same-UoW Transaction Atomicity

The [`AuditRepository`](file:///f:/operations-engine/app/audit/repository.py) subclasses [`SqlAlchemyRepository`](file:///f:/operations-engine/app/db/repositories/base.py):
- **Externally Supplied Session**: Receives an active `AsyncSession` owned by the Unit of Work.
- **Unconditional Application UUID Generation**: `AuditRepository.append()` generates `uuid4()` unconditionally when constructing `AuditEventRecord`. It does not consult or branch on any caller-provided ID.
- **Pre-Flush Application UUID Guarantee**: The primary key exists immediately upon return from `append()`, before any database flush or commit. `append()` does not call `flush()`, `commit()`, `rollback()`, or `close()`.
- **Flush Policy**: Does not call `session.flush()` by default. Audit events stage in session memory and commit atomically with business operations.
- **Same-UoW Atomicity**:
  ```python
  async with uow:
      business_repo = BusinessRepository(uow.session)
      audit_repo = AuditRepository(uow.session)

      await business_repo.save_entity(...)
      record = await audit_repo.append(audit_event)
      assert isinstance(record.audit_event_id, UUID)  # Exists immediately before flush

      await uow.commit()  # Both business mutation and audit append commit atomically
  ```
  If business logic or audit staging fails, both roll back atomically in the same Unit of Work scope.

---

## 7. PostgreSQL Immutability Trigger & Migration Structure

Migration [`0002_create_audit_events.py`](file:///f:/operations-engine/app/db/migrations/versions/0002_create_audit_events.py) manages schema evolution:
- **Upgrade**:
  1. Creates table `audit_events` with check constraints for schema version, event type, actor consistency, and bounded values.
  2. Creates indexes on `(tenant_id, recorded_at)`, `(correlation_id)`, and `(resource_type, resource_id, recorded_at)`.
  3. Creates function `fn_prevent_audit_event_mutation()` raising exception `'audit_events table is append-only: % operations are prohibited', TG_OP`.
  4. Creates statement trigger `trg_audit_events_prevent_mutation` firing `BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events FOR EACH STATEMENT`.
- **Downgrade**:
  Drops trigger, drops trigger function, drops indexes, and drops table in reverse order.

---

## 8. Verification & Integration Status

### 8.1 Unit & Static Verification
- Schema tests ([`tests/unit/test_audit_schema.py`](file:///f:/operations-engine/tests/unit/test_audit_schema.py)): 52 tests covering details validation, strict float rejection, boolean handling, key denylist, length limits, type rejection, actor consistency cases A-H, omission of `audit_event_id`, and rejection of caller-supplied IDs.
- Repository tests ([`tests/unit/test_audit_repository.py`](file:///f:/operations-engine/tests/unit/test_audit_repository.py)): 8 tests verifying protocol conformance, append-only staging, absence of mutation methods, unconditional `uuid4()` generation, distinct UUIDs on identical events, pre-flush application UUID guarantee, same-UoW transaction ownership contract, and error propagation.
- Migration tests ([`tests/unit/test_audit_migration.py`](file:///f:/operations-engine/tests/unit/test_audit_migration.py)): 6 tests verifying AST migration structure, database check constraints, trigger definitions, and model consistency.
- Suite status: **277 passed** across the entire repository.

### 8.2 PostgreSQL Live Integration Status
Unit tests verify migration syntax, AST operations, and model consistency statically without connecting to live PostgreSQL:
```python
POSTGRES_APPEND_ONLY_TRIGGER_RUNTIME_TEST = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
```
Live runtime trigger execution (confirming that PostgreSQL rejects UPDATE, DELETE, and TRUNCATE) will be verified when Task 1J provisions the containerized PostgreSQL environment.

---

## 9. Retention & Deferred Work

- **Retention Duration**: `TO_BE_CONFIRMED`. No arbitrary expiration or deletion jobs are introduced.
- **Partitioning**: Deferred until first-tenant scale profile evidence is established.
- **Deferred Task 1G Work**:
  - Transactional Outbox table and publisher.
  - RabbitMQ integration and event envelope versioning.
  - Transactional Inbox and idempotency deduplication.
