# Operational Runbook: Service API Key Credential Persistence & API Restart Recovery

## 1. Symptom
- API instance restarted (e.g. deployment, worker crash, node eviction, auto-scaling event).
- External microservices or automated jobs attempt to authenticate immediately post-restart using existing Service API keys.
- Multiple API replicas receive incoming requests with service credentials.

## 2. Impact
- **Severity**: Critical if credentials are lost on restart (Finding `REC-001`).
- **Remediated Status**: **RESOLVED_APPLICATION_DEFECT**. With `PostgresServiceApiKeyCredentialStore`, credentials are durably stored in PostgreSQL. API process restarts do NOT disrupt valid service credentials.
- **Source Governance**: **ENGINEERING_DECIDED** (`ENGINEERING_REQUIREMENT_INFERRED_FROM_PRODUCTION_RECOVERY` under `ADR-013`). Original project specification documents are silent on dynamic service key persistence (`ORIGINAL_SOURCE_REQUIREMENT = SILENT`).

## 3. Preconditions
- PostgreSQL cluster is operational and reachable.
- Migration `0014_service_api_credentials` applied (`alembic current` = `0014`).
- API processes initialized with `HttpAuthenticator` configured with `PostgresServiceApiKeyCredentialStore` (or `CompositeServiceApiKeyCredentialStore`).

## 4. Architecture & Credential Classes

### Class 1: STATIC_BOOTSTRAP_SERVICE_KEYS
- **Source**: Environment variables / secure secrets manager at deployment.
- **Storage**: Process configuration memory (`app/core/config.py`).
- **Lifetime**: Process lifetime (re-injected on restart).
- **Restart Behavior**: Reconstructed deterministically from environment configuration.
- **Production Use**: Initial bootstrap, root diagnostic keys.
- **Rotation Model**: Operational reconfiguration / redeployment.
- **Tenant Binding**: Fixed tenant ID configured in settings.
- **Revocation Model**: Remove from environment config and restart.
- **Audit**: Logged at startup (metadata only, no key secrets).

### Class 2: DYNAMIC_RUNTIME_SERVICE_KEYS
- **Source**: Secure runtime provisioning API or administrative command.
- **Storage**: Durable PostgreSQL table `service_api_credentials` (Migration `0014`).
- **Lifetime**: Persistent until explicit expiration (`expires_at`) or revocation (`revoked_at`).
- **Restart Behavior**: Survives process restart; queried on-demand by all API replicas.
- **Production Use**: Production service-to-service authentication across distributed components.
- **Rotation Model**: Provision new credential record with overlapping validity window, then revoke old record.
- **Tenant Binding**: Explicit foreign key / scope `tenant_id` on each record.
- **Revocation Model**: `UPDATE service_api_credentials SET enabled = false, revoked_at = NOW() WHERE credential_id = ...`.
- **Audit**: Immutable created_at, updated_at, revoked_at timestamps and audit events.

## 5. Secret Safety & Verification
- **Canonical HTTP Transport Boundary**:
  - Human / OIDC: `Authorization: Bearer <oidc_token>`
  - Service API Key: `X-E7-Service-Key: <service_key>` (`e7sk_<credential_id>.<secret>`)
  - Conflicting Mechanisms: Presenting both `Authorization: Bearer` and `X-E7-Service-Key` returns HTTP 400 (`auth.conflicting_credentials`).
- **Transport-Neutral Storage**: Persistent storage in PostgreSQL does NOT alter the external HTTP contract.
- **One-Way Digests**: Service secrets are hashed using SHA-256 before storage (`secret_digest`). Plaintext keys are NEVER written to the database or logged.
- **Constant-Time Verification**: Digests are compared via `hmac.compare_digest` to prevent timing attacks.
- **Multi-Replica Consistency**: All API replicas query the same durable PostgreSQL table; no out-of-sync in-memory state.
- **Tenant Isolation**: Every credential resolution checks tenant scope; requests attempting cross-tenant access are rejected with HTTP 403 Forbidden.

## 6. Verification Steps Post-Restart
1. **Verify Table & Schema**:
   ```sql
   SELECT count(*) FROM service_api_credentials;
   ```
2. **Verify Active Credentials Authenticate**:
   Issue request to authenticated endpoint using the canonical `X-E7-Service-Key` header:
   ```bash
   curl -H "X-E7-Service-Key: <service_key>" http://localhost:8000/api/v1/resources/availability
   ```
3. **Verify Revocation Persistence**:
   Confirm revoked credentials remain rejected post-restart:
   ```sql
   SELECT credential_id, enabled, revoked_at FROM service_api_credentials WHERE enabled = false;
   ```
   Requests with corresponding secrets must return HTTP 401 Unauthorized.

## 7. Evidence Reference
- Test Suite: `tests/recovery/test_api_auth_recovery.py`
- Canonical Contract Test: `test_canonical_http_auth_contract_and_persistence_reconciliation`
- Dedicated Drill: `REC-DRILL-12-AUTH-RESTART`
- Master Results: `docs/phase-8/recovery/recovery_results.json`
- Architecture Decision Record: `docs/adr/ADR-013-service-api-credential-persistence.md`
