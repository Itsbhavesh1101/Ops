# Production External Blockers & Readiness Register

**System:** IR Infotech E-7 Operations Engine<br/>
**Classification:** Institutional Architecture & Deployment Readiness Documentation<br/>
**Current Baseline HEAD:** `8b70d24c8bca7e04f2136e3203d504e9e8460a2a`<br/>
**Phase 8 Frozen RC:** `03e6305256db3d838b472aaac50893ddb513414e`<br/>
**Security Classification:** Public / Non-Confidential (Zero Secrets)

---

## Executive Summary & Readiness State

The E-7 Operations Engine has achieved full self-contained operational readiness for interactive demonstration, local developer workflows, and automated regression testing. The local demonstration environment utilizes a real standards-compliant OpenID Connect (OIDC) identity provider (Keycloak 24.0.5) with Authorization Code Flow and PKCE, backed by native PostgreSQL persistence, Flowable workflow execution, RabbitMQ event transport, and constraint scheduling.

However, formal enterprise production deployment remains gated on four external institutional prerequisites that require IT, database administration, and external systems coordination:

| Integration Domain | Local Demo State | Production Readiness State | Blocker Classification |
| :--- | :--- | :--- | :--- |
| **Identity & SSO** | `DEMO_LOCAL_OIDC = PASS` (Local Keycloak with PKCE) | `PRODUCTION_COMPANY_OIDC = BLOCKED_EXTERNAL` | External IT Provisioning |
| **Institutional Database** | `MYSQL_COMPANY_INTEGRATION = NOT_REQUIRED_FOR_SELF_CONTAINED_DEMO` | `MYSQL_LIVE_READ_VERIFICATION = BLOCKED_ON_COMPANY_READ_ONLY_ACCESS` | Institutional DBA Access |
| **Notification Gateway** | `E8_LIVE_DELIVERY = OUT_OF_DEMO_SCOPE` | `E8_NOTIFICATION_INTEGRATION = BLOCKED_ON_COMPANY_E8_ENDPOINT` | External Webhook Contract |
| **Production Ingress** | Native Gateway (`e7-pilot-gateway` :8088) | `PRODUCTION_GATEWAY = PENDING_L7_INFRASTRUCTURE` | Enterprise Ingress / WAF |
| **Overall Phase-8 Status** | `SELF_CONTAINED_DEMO = PASS` | `PHASE_8_PRODUCTION_READINESS = NOT_READY` | External Dependencies |

---

## 1. Company OpenID Connect (OIDC) Single Sign-On

### 1.1 Requirements for Production Readiness
Corporate policy requires all human interactive browser access in production environments to be authenticated through the enterprise Google Workspace OpenID Connect provider. The company IT/Security department must provide:

1. **OAuth 2.0 Web Client Registration:**
   - Provider Issuer: `https://accounts.google.com`
   - Production Client ID corresponding to the backend token audience (e.g. `e7-production-client-id.apps.googleusercontent.com`).
   - Discovery Endpoint: `https://accounts.google.com/.well-known/openid-configuration`
   - Public Certificates Endpoint (JWKS): `https://www.googleapis.com/oauth2/v3/certs`

2. **Authorized JavaScript Origins & Redirect URIs:**
   - Authorized Javascript Origin: `https://operations.irinfotech.com` (and authorized staging domains).
   - Authorized Redirect URI: `https://operations.irinfotech.com/login` (and `/auth/callback`).

3. **Enterprise Security Constraints:**
   - Enforced hosted domain verification (`hd=irinfotech.com`).
   - Verified email claims (`email_verified = true`).
   - Mandatory multi-factor authentication (MFA) at corporate Google Workspace boundary.
   - Token signature algorithm restricted to asymmetric `RS256` keys.

### 1.2 Rationale: Why Local Keycloak is Demo-Only
The demo stack utilizes a containerized Keycloak instance (`e7-keycloak` on `127.0.0.1:8180`) strictly to provide an air-gapped, fully self-contained OIDC provider. It executes identical cryptographic validation (JWT parsing, asymmetric RSA signature verification, expiration checks, audience allowlists, and claim resolution) without requiring corporate Google Workspace directory access. Local Keycloak must never be exposed to public networks or utilized for production workloads where central corporate credential management, SAML federation, and employee lifecycle deprovisioning are mandatory.

---

## 2. Institutional MySQL Read-Only Integration

### 2.1 Requirements for Production Readiness
Production synchronization with legacy enterprise systems requires real-time read access to canonical institutional tables hosted on corporate MySQL clusters.

1. **Dedicated SELECT-Only Institutional Account:**
   - A dedicated service account must be provisioned (e.g. `e7_ro_sync`).
   - Privileges must be strictly constrained using least-privilege principles:
     ```sql
     GRANT SELECT ON institutional_db.<specific_tables> TO 'e7_ro_sync'@'<vpc_cidr>';
     ```
   - All write (`INSERT`, `UPDATE`, `DELETE`), administrative (`DROP`, `ALTER`, `GRANT`), and file/process privileges must be denied.

2. **Prohibition of Broad Administrative Accounts:**
   - Existing elevated developer or DBA accounts must **not** be utilized for production sync proof.
   - Modifying or mutating existing databases during validation is strictly forbidden.

3. **Connection & Security Specifications:**
   - Dedicated VPC endpoint or private host address (e.g. `mysql-internal.corp.irinfotech.internal:3306`).
   - TLS 1.3 encryption with verified CA certificate (`ssl_mode=VERIFY_IDENTITY`).
   - Privilege verification report produced via `SHOW GRANTS FOR 'e7_ro_sync'@'%'` prior to connection activation.

### 2.2 Demo Status
In the local demo environment, the native PostgreSQL database serves as the canonical system of record for all operational entities (goals, plans, tasks, resources, availabilities, schedules, and audit events). The application functions with 100% fidelity without the external MySQL dependency (`MYSQL_COMPANY_INTEGRATION = NOT_REQUIRED_FOR_SELF_CONTAINED_DEMO`).

---

## 3. External E8 Notification Gateway Integration

### 3.1 Requirements for Production Readiness
Outbound notification delivery from the E-7 workflow engine to enterprise communications infrastructure requires active integration with the E8 notification platform.

1. **Staging / Production Endpoints:**
   - Dedicated HTTPS endpoint (e.g. `https://e8-gateway.corp.irinfotech.internal/api/v2/notifications`).

2. **Authentication Contract:**
   - Mutual TLS (mTLS) with client certificate verification, or HMAC-SHA256 payload signatures with rotating shared secrets.

3. **Webhook Payload Contract & Idempotency:**
   - Standardized JSON contract containing:
     - `notification_id`: UUIDv4
     - `idempotency_key`: Client-generated deterministic key
     - `recipient_group`: Targeted institutional distribution group
     - `urgency`: `ROUTINE`, `URGENT`, `EMERGENCY`
     - `event_timestamp`: RFC 3339 UTC timestamp
     - `summary`: Human-readable summary
     - `payload`: Structured domain event context

4. **Delivery Guarantees & SLAs:**
   - Synchronous HTTP 200/202 acknowledgement with delivery reference ID within a 3000ms SLA.
   - At-least-once outbox delivery with exponential backoff and dead-letter queue (DLQ) routing after 5 failed retry attempts.

### 3.2 Demo Status
For demo and local rehearsal purposes, outbound notifications are recorded in the internal PostgreSQL outbox table and dispatched through the local RabbitMQ event exchange. External delivery to the physical E8 system is classified as out of scope (`E8_LIVE_DELIVERY = OUT_OF_DEMO_SCOPE`) until endpoint provisioning is complete.

---

## 4. Production L7 Gateway & Ingress Infrastructure

### 4.1 Requirements for Production Readiness
While the local demo utilizes `e7-pilot-gateway` (nginx 1.31 reverse proxy on port 8088), an enterprise production rollout requires:

1. **Layer 7 Ingress Controller:**
   - Enterprise API gateway / ingress controller (e.g. AWS ALB, GCP Cloud Armor / Cloud Load Balancing, or Kong).
   - WAF (Web Application Firewall) with OWASP Core Rule Set (CRS) inspection.
   - DDoS mitigation with syn-flood and volumetric attack damping.

2. **Rate Limiting & Throttling:**
   - Per-IP rate limiting: 100 requests/second burst allowance.
   - Per-tenant rate limiting: 500 requests/second with HTTP 429 (`Too Many Requests`) fail-closed backpressure and `Retry-After` headers.

3. **TLS & Cryptographic Transport:**
   - Strict TLS 1.3 enforcement with modern cipher suites.
   - Automated certificate lifecycle management (ACME / cert-manager).
   - Security headers: `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`, `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`.

---

## 5. Summary: Demo Ready vs. Production Ready

```
+-----------------------------------------------------------------------------+
|                           LOCAL DEMO ENVIRONMENT                            |
|                            [ DEMO READY: PASS ]                             |
+-----------------------------------------------------------------------------+
| - Identity: Self-hosted Keycloak 24.0.5 with Authorization Code + PKCE     |
| - Authentication: Cryptographic RS256 JWT validation via JWKS               |
| - RBAC & Tenants: Canonical separation of duties on tenant-pilot-001       |
| - Persistence: PostgreSQL 16.15 system-of-record (Alembic Head: 0014)       |
| - Workflows: Flowable 7.0.0 BPMN/DMN runtime engine                         |
| - Messaging: RabbitMQ 3.13 event bus with transactional outbox dispatcher   |
| - Scheduling: OptaPlanner/OR-Tools constraint optimization engine           |
| - Zero Mock Data: All operations executed against real backend APIs         |
+-----------------------------------------------------------------------------+

                                      |
                                      v

+-----------------------------------------------------------------------------+
|                        ENTERPRISE PRODUCTION ENVIRONMENT                    |
|                    [ PHASE_8_PRODUCTION_READINESS: NOT_READY ]              |
+-----------------------------------------------------------------------------+
| 1. Company OIDC Client Registration: BLOCKED_EXTERNAL (Awaiting IT)         |
| 2. Read-Only Institutional MySQL:    BLOCKED_ON_COMPANY_READ_ONLY_ACCESS    |
| 3. Enterprise E8 Notification API:   BLOCKED_ON_COMPANY_E8_ENDPOINT         |
| 4. Production Ingress & L7 WAF:      PENDING_INFRASTRUCTURE_PROVISIONING    |
+-----------------------------------------------------------------------------+
```

*Document Author: IR Infotech Operations Architecture Team*<br/>
*Status: Ratified & Maintained under Baseline Version Control*
