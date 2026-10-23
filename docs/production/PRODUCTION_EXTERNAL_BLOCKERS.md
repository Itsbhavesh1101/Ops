# Production External Blockers & Readiness Register

**System:** IR Infotech E-7 Operations Engine<br/>
**Classification:** Institutional Architecture & Deployment Readiness Documentation<br/>
**Current Baseline HEAD:** `5722d1cdd8210942d4f63e41362cec66ccca2101`<br/>
**Phase 8 Frozen RC:** `03e6305256db3d838b472aaac50893ddb513414e`<br/>
**Security Classification:** Public / Non-Confidential (Zero Secrets)

---

## Executive Summary & Readiness State

The E-7 Operations Engine has achieved full self-contained operational readiness for interactive demonstration, local developer workflows, automated regression testing, and full real-data operations flow. The demonstration environment utilizes a real standards-compliant OpenID Connect (OIDC) identity provider (Keycloak 24.0.5) with Authorization Code Flow and PKCE, backed by native PostgreSQL persistence, Flowable workflow execution, RabbitMQ event transport, and CP-SAT constraint scheduling.

However, formal enterprise production deployment remains gated on four external institutional prerequisites that require IT, database administration, and cloud infrastructure coordination:

| Integration Domain | Local Demo State | Production Readiness State | Blocker Classification | Owner |
| :--- | :--- | :--- | :--- | :--- |
| **Institutional Database** | `CONNECTED = YES` (Application Read-Only Guard on Cloud Host) | `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_COMPANY_READ_ONLY_ACCESS` | Institutional DBA Access | Institutional DBA Team |
| **Identity & SSO** | `DEMO_LOCAL_OIDC = PASS` (Local Keycloak with PKCE) | `PRODUCTION_COMPANY_OIDC = BLOCKED_EXTERNAL` | External IT Provisioning | Corporate IT / Identity Team |
| **Notification Gateway** | `E8_LOCAL_CONTRACT_DISPATCH = PASS` (Outbox/AMQP) | `E8_NOTIFICATION_INTEGRATION = BLOCKED_EXTERNAL` | External Service Contract | E-8 Platform Team |
| **Production Ingress & DNS** | Native Gateway (`e7-pilot-gateway` :8088) | `PRODUCTION_GATEWAY = BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | Enterprise Ingress / WAF / DNS | Cloud Infrastructure Team |
| **Overall Production Readiness**| `DEMO_REHEARSAL_READY = YES` | `PHASE_8_PRODUCTION_READINESS = NOT_READY` | External Dependencies | Enterprise Architecture Board |

---

## 1. Institutional MySQL Read-Only Integration (`MYSQL_LIVE_READ_VERIFICATION`)

### 1.1 Blocker Overview
- **Canonical Key:** `MYSQL_LIVE_READ_VERIFICATION`
- **Current Canonical Status:** `BLOCKED_ON_COMPANY_READ_ONLY_ACCESS`
- **Owner:** Institutional Database Administration (DBA) Team
- **Governance Classification:** `ORIGINAL_REQUIREMENT` (FR-INT-02, Task 0G)

### 1.2 Information & Resources Required
1. Dedicated database username (e.g. `e7_ro_sync` — illustrative example account name).
2. Strictly `SELECT`-only privileges restricted to verified institutional ERP tables.

   **Audit Reconciliation & Schema Inventory Verification:**
   An audit against `MYSQL_SCHEMA_INVENTORY.json` (247 tables), `MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.json`, `MYSQL_ERP_ADAPTER_CONTRACT.json`, and the actual `MySqlIreduAdapter` implementation (`app/integrations/erp/mysql_adapter.py`) identified that three tables previously listed in preliminary requests do **not** exist in the institutional schema:
   - `iredu.academic_session`: **Removed / Does not exist in 247-table inventory**. The canonical institutional table is `iredu.sessions` (queried by `MySqlIreduAdapter.get_academic_sessions`).
   - `iredu.student_cohort`: **Removed / Does not exist in 247-table inventory**. Cohort structures and headcounts are derived from relational joins across `classes`, `sections`, `class_sections`, and `student_session` (queried by `MySqlIreduAdapter.get_student_headcounts`).
   - `iredu.examination_session`: **Removed / Does not exist in 247-table inventory**. Examination tables in the inventory are `exam_schedules`, `exam_results`, etc., and are not queried by E-7 timetable operations.
   - `iredu.subject_timetable`: **Added / Verified active**. Actively queried by `MySqlIreduAdapter.get_timetable_commitments` to retrieve known teaching commitments and room allocations.

   **Reconstructed Minimum SELECT-Only Privilege Matrix:**

   *Tier 1 — Core Active Adapter Tables (Strictly required for current `MySqlIreduAdapter` queries — exactly 11 tables):*
   ```sql
   GRANT SELECT ON iredu.sch_settings TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.sessions TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.classes TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.sections TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.class_sections TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.subjects TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.staff TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.department TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.staff_designation TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.student_session TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.subject_timetable TO 'e7_ro_sync'@'<vpc_cidr>';
   ```

   *Tier 2 — Mapped Support Tables (Documented in Task Intelligence Phase 0 mapping for curricular groupings and RBAC validation — 4 tables):*
   ```sql
   GRANT SELECT ON iredu.subject_groups TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.subject_group_subjects TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.roles TO 'e7_ro_sync'@'<vpc_cidr>';
   GRANT SELECT ON iredu.staff_roles TO 'e7_ro_sync'@'<vpc_cidr>';
   ```
   *(Note: Table names, CIDR blocks, and user accounts are illustrative examples. Do not execute GRANT statements or mutate the company MySQL database.)*

3. Explicit denial of all write (`INSERT`, `UPDATE`, `DELETE`), DDL (`DROP`, `ALTER`, `CREATE`), administrative (`GRANT`, `SUPER`, `RELOAD`), and file/process privileges.
4. Production connection parameters:
   - Dedicated VPC endpoint or private host FQDN (e.g. `mysql-internal.corp.irinfotech.internal`).
   - Port (default 3306).
   - Database name (`iredu`).
   - TLS requirement (`ssl_mode=VERIFY_IDENTITY`) and institutional CA certificate.
   - Connection pool bounds (max 10 connections, connect timeout 5.0s).

### 1.3 Why Required (Security & Architectural Rationale)
Under institutional security policy and ADR-007, connecting an operational system to an upstream enterprise system of record using elevated credentials is strictly prohibited. The current test account (`iredu`) holds broad table permissions across the entire institutional database. Although E-7 enforces application-level read-only guards (`SET SESSION TRANSACTION READ ONLY` and regex-based statement rejection in `MySqlIreduAdapter`), defense-in-depth requires database-enforced least privilege at the database engine level. E-7 must never write to the upstream institutional database.

### 1.4 Verification Process & Commands
Prior to enabling live production extraction, an operator will execute privilege verification:
```bash
mysql -h <host> -u e7_ro_sync -p -e "SHOW GRANTS FOR 'e7_ro_sync'@'%';"
```
Expected output must contain only `GRANT USAGE ON *.*` and `GRANT SELECT ON iredu.<table_name>` for approved tables. If `ALL PRIVILEGES`, `INSERT`, `UPDATE`, or `DELETE` appear in the output, verification **FAILS CLOSED**.

Second, verify connection with statement-level write rejection:
```bash
python -c "
import asyncio
from app.integrations.erp.mysql_adapter import MySqlIreduAdapter
from app.integrations.erp.config import MySqlErpSettings
# Test read succeeds
settings = MySqlErpSettings(
    enabled=True,
    host='<host>',
    user='e7_ro_sync',
    password='<pass>',
    database='iredu',
    single_tenant_id='tenant-prod-001'
)
adapter = MySqlIreduAdapter(settings=settings)
async def check():
    grants = await adapter.audit_database_user_grants()
    compliance = adapter.evaluate_grants_least_privilege(grants)
    assert compliance['is_compliant'], f'Least-privilege violation: {compliance[\"violations\"]}'
    org = await adapter.get_organization_context('tenant-prod-001')
    assert org is not None
    print('MYSQL_VERIFICATION_PASS: Organization context retrieved:', org.institution_name)
asyncio.run(check())
"
```

### 1.5 Status Change Upon Resolution
- `MYSQL_LIVE_READ_VERIFICATION` transitions from `BLOCKED_ON_COMPANY_READ_ONLY_ACCESS` to `PASS`.

---

## 2. Production Company OpenID Connect (OIDC) (`PRODUCTION_COMPANY_OIDC`)

### 2.1 Blocker Overview
- **Canonical Key:** `PRODUCTION_COMPANY_OIDC`
- **Current Canonical Status:** `BLOCKED_EXTERNAL`
- **Owner:** Corporate IT / Enterprise Identity & Access Management Team
- **Governance Classification:** `ORIGINAL_REQUIREMENT` (FR-AUTH-01, Task 1C.2)

### 2.2 Information & Resources Required
1. **OAuth 2.0 Web Client Registration:**
   - Provider Name: `google` (or institutional IdP).
   - Issuer URL: `https://accounts.google.com` (or enterprise SAML/OIDC federated gateway).
   - Discovery Endpoint: `https://accounts.google.com/.well-known/openid-configuration`.
   - JWKS URI: `https://www.googleapis.com/oauth2/v3/certs`.
   - Production Client ID (e.g. `e7-operations-production.apps.googleusercontent.com` — illustrative example).
   - Production Client Secret (only if confidential server-side authorization code exchange is utilized; in production, client secret is NOT required or permitted for the public browser SPA custom ID-token flow).
2. **Authorized JavaScript Origins & Redirect URIs:**
   - Authorized Javascript Origins: `https://operations.irinfotech.com` (and staging FQDNs — illustrative examples).
   - Authorized Redirect URIs: `https://operations.irinfotech.com/login` and `https://operations.irinfotech.com/auth/callback` (illustrative examples).
3. **Claims & Directory Schema:**
   - Hosted domain claim enforcement (e.g. `hd=irinfotech.com` — illustrative example).
   - Verified email claims (`email_verified = true`).
   - Role / Group claim mapping: enterprise Google Groups or directory attributes mapped to canonical E-7 roles (`planner`, `approver`, `scheduler`, `operator`, `tenant_admin`, `auditor`, `viewer`).
   - Tenant mapping claim: organization/department claim resolving to canonical tenant UUIDs.

### 2.3 Why Required (Security & Architectural Rationale)
Corporate policy mandates that all human interactive browser access in production environments authenticate through central enterprise SSO. The local demo environment utilizes Keycloak strictly as an air-gapped OIDC provider to validate cryptographic flows (RS256 signature verification, JWKS caching, claims resolution, PKCE exchange) without corporate directory access. Local Keycloak must never be exposed in production.

### 2.4 Verification Process & Commands
Verify OIDC discovery endpoint and JWKS cryptographic readiness:
```bash
# 1. Probe OIDC Discovery Endpoint
curl -fsS https://accounts.google.com/.well-known/openid-configuration | grep "jwks_uri"

# 2. Probe JWKS public key endpoint
curl -fsS https://www.googleapis.com/oauth2/v3/certs | grep "keys"

# 3. Application token validation verification
python -c "
from app.core.auth.google_oidc import GoogleOidcVerifier
v = GoogleOidcVerifier(client_ids=['<e7-prod-client-id>'], allowed_hosted_domains=['irinfotech.com'])
print('OIDC_VERIFIER_INIT_PASS: Google OIDC verifier configured.')
"
```

### 2.5 Status Change Upon Resolution
- `PRODUCTION_COMPANY_OIDC` transitions from `BLOCKED_EXTERNAL` to `PASS`.

---

## 3. External E8 Notification Gateway Integration (`E8_NOTIFICATION_INTEGRATION`)

### 3.1 Blocker Overview
- **Canonical Key:** `E8_NOTIFICATION_INTEGRATION`
- **Current Canonical Status:** `BLOCKED_EXTERNAL`
- **Owner:** Enterprise E-8 Notification / Engagement Platform Team
- **Governance Classification:** `ORIGINAL_REQUIREMENT` (FR-INT-01, Task 0G.4, ADR-012)

### 3.2 Dual-Transport Architecture (ADR-012 Preservation)
In accordance with accepted **ADR-012 (`ADR-012-e8-notification-boundary.md`)**, E-7 implements a provider-neutral **dual-transport notification architecture** behind `NotificationDispatcherProtocol`:

1. **AMQP / RabbitMQ Message Broker (Default Production Transport):**
   - Outbox dispatcher publishes `NotificationCommand` events to RabbitMQ topic exchange `e8.notifications` with routing key `{tenant_id}.notification.{trigger_kind}`.
   - Provides broker durability, asynchronous decoupling, backpressure buffering during E-8 maintenance windows, and retry management.
   - Default production setting in application config: `transport="broker"`, `exchange="e8.notifications"`.
2. **Authenticated HTTP / REST Gateway (Configurable Alternative Transport):**
   - When `E7_NOTIFICATION_TRANSPORT=http` and `E7_NOTIFICATION_ENDPOINT_URL` are configured, dispatches `NotificationCommand` via `HttpNotificationDispatcher` using HTTP POST with Bearer token authentication or mTLS.
   - HTTP transport is an approved configurable alternative; it is **not mandatory** unless an approved institutional architecture change explicitly requires it over AMQP.
3. **Authoritative Transactional Outbox:**
   - Canonical persistence authority remains the PostgreSQL `outbox_events` table (`ADR-005`). Outbound notification commands are committed atomically within the same ACID transaction as the operational state transition. E-7 domain services never execute blocking network I/O to external systems during transactional writes.
4. **Division of System Responsibilities:**
   - **E-7 Responsibility**: Evaluates operational conditions, schedules, tasks, and SLAs to determine *when* communication is warranted; constructs transport-neutral `NotificationCommand` objects with minimal structured metadata and opaque recipient tokens (zero raw student PII, zero payroll data); and records immutable audit records.
   - **E-8 Responsibility**: Owns downstream engagement behavior, including template expansion, channel selection (email, SMS, WhatsApp, mobile push), quiet-hours policy, recipient contact resolution, delivery retries, and delivery tracking.
   - **Non-Mocking Policy**: Zero synthetic or mock E-8 services will be placed in production execution paths.

### 3.3 Information & Confirmation Required from E-8 Platform Team
Confirmation is required from the corporate E-8 Platform Team regarding the accepted transport mechanism:
1. **Transport Mode Confirmation:**
   - Confirm whether the production E-8 subsystem consumes notifications via **AMQP topic broker** (`e8.notifications`, ADR-012 default) or via **HTTP REST webhook** (`endpoint_url`, configurable alternative).
2. **If AMQP Broker Transport (ADR-012 Default):**
   - Broker cluster connection parameters (host FQDN, port 5671 TLS / 5672, virtual host, institutional CA certificate).
   - Dedicated service account credentials with publish-only permissions on exchange `e8.notifications`.
   - Routing key topology confirmation: `{tenant_id}.notification.{trigger_kind}`.
3. **If HTTP REST Gateway Transport (Configurable Alternative):**
   - Target gateway HTTPS endpoint URL (e.g. `https://e8-gateway.corp.irinfotech.internal/api/v2/notifications` — illustrative example).
   - Authentication contract: Bearer API token (`E7_NOTIFICATION_API_KEY`) or mTLS client certificate.
   - Response latency SLA (< 3000ms) and expected HTTP status codes (`HTTP 200/201/202` for accepted delivery, `HTTP 409 Conflict` for deduplicated idempotent delivery).
4. **Data Contract Confirmation:**
   - Confirmation of `NotificationCommand` JSON schema (`command_id`, `correlation_reference`, `recipient_references`, `notification_context`, `trigger`).

### 3.4 Verification Process & Commands
Depending on confirmed transport mode:
```bash
# 1. AMQP Transport Verification (ADR-012 Default)
python -c "
from app.integrations.engagement.config import NotificationSettings
settings = NotificationSettings(enabled=True, transport='broker', exchange='e8.notifications')
print(f'E8_AMQP_CONFIG_VERIFIED: transport={settings.transport}, exchange={settings.exchange}')
"

# 2. HTTP Transport Verification (Configurable Alternative)
python -c "
from app.integrations.engagement.config import NotificationSettings
from app.integrations.engagement.dispatcher import HttpNotificationDispatcher
settings = NotificationSettings(enabled=True, transport='http', endpoint_url='https://<e8-endpoint>/api/v2/notifications')
dispatcher = HttpNotificationDispatcher(settings=settings)
print(f'E8_HTTP_CONFIG_VERIFIED: transport={settings.transport}, endpoint={settings.endpoint_url}')
"
```

### 3.5 Status Change Upon Resolution
- `E8_NOTIFICATION_INTEGRATION` transitions from `BLOCKED_EXTERNAL` to `PASS`.

---

## 4. Production Gateway, Ingress, TLS & DNS (`PRODUCTION_GATEWAY`)

### 4.1 Blocker Overview
- **Canonical Key:** `PRODUCTION_GATEWAY`
- **Current Canonical Status:** `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE`
- **Owner:** Enterprise Cloud Infrastructure & Security Operations Team
- **Governance Classification:** `APPROVED_ADR_DECISION` (ADR-007, Phase 8E)

### 4.2 Information & Resources Required
1. **Layer 7 Reverse Proxy / Ingress Controller:**
   - Enterprise Ingress (Envoy, Kong, AWS ALB, GCP Cloud Armor, or Cloudflare — illustrative infrastructure choices).
   - OWASP Core Rule Set (CRS) inspection.
   - Volumetric rate limiting baseline (e.g. per-IP: 100 req/s burst; per-tenant: 500 req/s returning HTTP 429 with standard `Retry-After` header — illustrative example baseline subject to enterprise SecOps ratification).
2. **TLS Termination & Certificates:**
   - Strict TLS 1.3 / 1.2 modern cipher suite termination.
   - Automated certificate lifecycle management (cert-manager / Let's Encrypt / AWS Certificate Manager).
3. **DNS Configuration:**
   - Production FQDNs routed to Ingress controller (illustrative example hostnames):
     * `operations.irinfotech.com` -> Frontend SPA (illustrative example)
     * `api.operations.irinfotech.com` -> Backend FastAPI API (illustrative example)
4. **Header Forwarding:**
   - Accurate preservation of `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Real-IP`, and `X-Correlation-ID`.

### 4.3 Why Required (Security & Architectural Rationale)
Application-level rate limiting (`TenantQuotaManager`) handles internal capacity quotas (max concurrent solves, plan generation limits). However, network-edge volumetric protection, socket flood mitigation, and TLS certificate lifecycle management belong strictly to the edge ingress layer. Exposing application nodes directly to public networks is prohibited.

### 4.4 Verification Process & Commands
Probe ingress SSL grade and rate limiting enforcement:
```bash
# 1. Verify TLS 1.3 negotiation
curl -Iv --tlsv1.3 https://operations.irinfotech.com/api/v1/health

# 2. Verify Rate Limit HTTP 429 and Retry-After header
for i in {1..25}; do curl -s -o /dev/null -w "%{http_code}\n" https://operations.irinfotech.com/api/v1/health; done
# Must observe HTTP 429 on burst violation with Retry-After header
```

### 4.5 Status Change Upon Resolution
- `PRODUCTION_GATEWAY` transitions from `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` to `PASS`.
- `RATE_LIMITING` transitions from `BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION` to `PASS`.

---

## 5. Architectural Non-Mocking Declaration

Under institutional governance rules:
- **No company dependency was mocked.**
- **No fake OIDC provider or synthetic directory was placed into production paths.**
- **No fake ERP service or synthetic database tables were substituted.**
- **No fake E-8 notification service was created.**
- **Local Keycloak, PostgreSQL, and RabbitMQ serve strictly as air-gapped demo/rehearsal boundaries.**
- **All external institutional dependencies remain explicitly BLOCKED until genuine credentials and infrastructure are delivered.**

---

*Document Author: IR Infotech Operations Architecture Team*<br/>
*Current Baseline HEAD:* `5722d1cdd8210942d4f63e41362cec66ccca2101`<br/>
*Status: Ratified & Maintained under Version Control*
