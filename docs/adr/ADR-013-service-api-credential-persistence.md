# ADR-013: Service API Key Credential Persistence and Production Auth Recovery

## Status
Accepted (Engineering Requirement Inferred from Production Recovery & Multi-Replica Correctness)

## Date
2026-10-10

## Context
During the Phase 8D Disaster Recovery and Resilience audit, finding **REC-001** identified that the initial `InMemoryServiceApiKeyCredentialStore` stored dynamically created service credentials strictly in process-local memory. Upon an API process restart (e.g., node rescheduling, deployment rollout, OOM recovery, or crash restart), all dynamically registered service credentials were lost, causing subsequent service-to-service requests to fail with HTTP 401. Furthermore, in horizontal multi-replica deployments, separate API worker processes possessed isolated in-memory stores, causing inconsistent authentication results across replicas.

An audit of the authoritative original project documents (`docs/source-of-truth/`):
- `IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`
- `IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`
- `IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf`
- `opes eng.pdf`

confirms that the original documents establish strict multi-tenancy, PostgreSQL 16 as the canonical system of record, and an `api-service` authentication boundary, but are silent on the concrete persistence mechanism for dynamic service-to-service API keys (`ORIGINAL_SOURCE_REQUIREMENT = SILENT`).

Therefore, the decision to persist service API credentials in PostgreSQL is an **engineering decision inferred from production recovery, multi-replica consistency, and disaster-recovery requirements**.

## Decision
1. **Adopt PostgreSQL Persistence (Path B)**: Persist dynamic service API credentials durably in PostgreSQL using a dedicated table: `service_api_credentials`.
2. **Schema & Migration**: Implement Alembic migration `0014_service_api_credentials.py` (down_revision `0013`), increasing the application table count from 25 to 26 (27 public tables including `alembic_version`).
3. **Classify In-Memory Store**: Explicitly designate `InMemoryServiceApiKeyCredentialStore` as `DEV_TEST_ONLY`. Prohibit reliance on in-memory credential storage in production.
4. **Implement Production Stores**:
   - `PostgresServiceApiKeyCredentialStore`: Reads directly from PostgreSQL `service_api_credentials`.
   - `PostgresServiceIdentityResolver`: Maps verified credentials to `AuthenticatedPrincipal` using persisted tenant scope, roles, and permissions.
   - `CompositeServiceApiKeyCredentialStore` and `CompositeIdentityResolver`: Bridge in-memory test fixtures with production PostgreSQL persistence.
5. **Preserve Canonical HTTP Transport Boundary**:
   - Human / OIDC Identity: `Authorization: Bearer <token>`
   - Service API Key Identity: `X-E7-Service-Key: <service_key>`
   - Mutual Exclusion: Providing both headers simultaneously yields HTTP 400 (`auth.conflicting_credentials`).
   - The persistence implementation is transport-neutral and introduces zero drift into the external HTTP header contract.

## Alternatives Considered
- **Path A: Environment Configuration Only (Static Keys)**: Require all service API keys to be injected via environment variables at process startup. Rejected for production runtime: enterprise microservices, external partner systems, and automated pipelines require programmatic key provisioning and rotation without requiring an API redeployment or cluster restart.
- **Distributed Cache (Redis / Valkey)**: Store dynamic keys in a Redis/Valkey cache. Rejected: per ADR-007, Valkey is a conditional component not yet required for MVP. PostgreSQL 16 is already the authoritative system of record (ADR-006) and provides durable ACID guarantees. Adding an external cache solely for credential persistence violates dependency minimization.

## Security Consequences
- **Secret Safety**: Raw plaintext API keys (`e7sk_<credential_id>.<secret>`) are NEVER persisted, logged, or returned in error payloads. Only 64-character one-way SHA-256 hex digests (`secret_digest`) are stored in the database.
- **Constant-Time Verification**: Verification utilizes `hmac.compare_digest` with dummy timing mitigation for nonexistent credentials to prevent timing side-channels.
- **Strict Tenant Scoping**: Every row in `service_api_credentials` is partitioned by `tenant_id`. Authenticated requests are constrained to their assigned tenant; cross-tenant access is rejected with HTTP 403 Forbidden.
- **Status Privacy**: Wrong secrets are rejected prior to status checks, ensuring attackers cannot probe whether a credential is active, disabled, or expired.

## Recovery & Resilience Consequences
- **Process Restart Resilience**: API process restarts do not disrupt valid service credentials. Incoming requests authenticate immediately post-restart via PostgreSQL queries.
- **Revocation Durability**: Revocation sets `enabled = false` and `revoked_at = NOW()` in PostgreSQL. Revoked credentials remain disabled permanently across all restarts.
- **Multi-Replica Consistency**: All API replicas query the shared database, guaranteeing 100% consistent authentication behavior without process-level drift.

## References
- Source of Truth: `docs/source-of-truth/` (Silent on service key persistence)
- ADR-000: Source of Truth & Governance Framework
- ADR-006: PostgreSQL 16 as Canonical System of Record
- Phase 8A: Authentication Boundary & Multi-Tenant Isolation
- Phase 8D: Disaster Recovery & Failure Resilience (Finding REC-001)
- Migration: `app/db/migrations/versions/0014_service_api_credentials.py`
- Test Suite: `tests/recovery/test_api_auth_recovery.py`
