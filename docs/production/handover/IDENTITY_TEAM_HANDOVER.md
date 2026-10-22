# E-7 Operations Engine — Corporate Identity & Access Management Handover

**Document:** Production Single Sign-On (SSO) & OpenID Connect Integration Guide  
**Target Audience:** Corporate IT / Enterprise Identity & Access Management (IAM) Team  
**System:** IR Infotech E-7 Operations Engine  
**Classification Key:** `PRODUCTION_COMPANY_OIDC`  
**Current Status:** `BLOCKED_EXTERNAL`  
**Protocol:** OpenID Connect Core 1.0 (Authorization Code Flow with PKCE)  

---

## 1. Executive Summary

The E-7 Operations Engine requires enterprise Single Sign-On (SSO) for all interactive browser sessions. The system is designed around standard OpenID Connect (OIDC) Authorization Code Flow with Proof Key for Code Exchange (PKCE) (RFC 7636).

During Phase 8 development and demonstration, E-7 utilized an isolated, air-gapped Keycloak 24.0.5 container strictly to validate cryptographic token verification (RS256, JWKS caching, clock skew bounds, claims resolution). For production V1, E-7 requires integration with the corporate Google Workspace / Google Cloud Identity OpenID Connect infrastructure.

No mock or synthetic identity provider may be exposed in production paths.

---

## 2. OAuth 2.0 Web Client Registration Requirements

The Identity Team is requested to register a new Web Application client in the enterprise Google Cloud Console / Identity Provider:

| Parameter | Specification | Purpose / Rationale |
|---|---|---|
| **Application Type** | Web Application | Single Page Application (React SPA) + FastAPI Backend |
| **Application Name** | `E-7 Operations Engine` | Displayed on Google OAuth consent screen |
| **Authorized JavaScript Origins** | `https://<production-domain>` (and staging origin) | Required for PKCE browser token exchange |
| **Authorized Redirect URIs** | `https://<production-domain>/login`<br/>`https://<production-domain>/auth/callback` | OAuth authorization code return endpoints |
| **Allowed Hosted Domains (`hd`)** | Corporate domain (e.g. `irinfotech.com` — institutional configuration) | Enforced by backend token verifier |
| **Scoping** | `openid`, `email`, `profile` | Minimum required OIDC scopes |

> [!NOTE]
> All hostnames and domain names (`irinfotech.com`, `https://<production-domain>`) are illustrative examples. The actual production FQDN will be determined by the Cloud Infrastructure and Enterprise Architecture teams.

---

## 3. Cryptographic Token Verification Architecture

The E-7 backend enforces asymmetric token verification via [GoogleOidcVerifier](file:///f:/operations-engine/app/core/auth/google_oidc.py):
1. **Issuer Verification:** `iss` must strictly equal `https://accounts.google.com` or `accounts.google.com`.
2. **Discovery Endpoint:** `https://accounts.google.com/.well-known/openid-configuration`.
3. **JWKS Key Verification:** Public keys fetched from `https://www.googleapis.com/oauth2/v3/certs`. Keys are verified using RS256. Symmetric algorithms (`HS256`, `HS384`) or `none` algorithm are rejected at the parser level.
4. **Key Caching & Fallback:** JWKS public certificates are cached in memory with a 1-hour TTL.
5. **Clock Skew Tolerance:** Maximum 60-second leeway enforced on `exp` and `iat` claims.
6. **Audience Enforcement:** `aud` claim must match one of the configured production Client IDs.
7. **Email Verification:** `email_verified` claim must equal `true`.

---

## 4. Identity-to-Principal & Tenant Mapping

When a user authenticates, the identity claims are mapped into an E-7 session principal:

### 4.1 Principal Identification
* `sub` (Google Subject ID) maps to `external_identity_subject`.
* `email` maps to `user_email`.
* `name` maps to `user_display_name`.

### 4.2 Role Assignment Ownership
E-7 enforces canonical Role-Based Access Control (RBAC) across 7 discrete roles:
* `planner`: Author of operational goals and schedule requests.
* `approver`: Reviews and signs off on generated plans and schedule publications.
* `scheduler`: Submits and manages solver jobs.
* `operator`: Executes runtime human tasks and workflow actions.
* `tenant_admin`: Manages tenant configuration, service API keys, and workflow models.
* `auditor`: Read-only access to immutable audit ledgers.
* `viewer`: Read-only visibility across operational dashboards.

**Corporate IAM Mapping Options (Subject to Institutional Decision):**
* **Option A (Google Groups / SAML Attribute Claim):** The corporate IdP includes a custom claim (e.g. `groups` or `roles`) containing mapped directory groups (e.g. `e7-planners@corp.irinfotech.com` -> `planner`).
* **Option B (E-7 Database Role Directory):** E-7 stores role assignments in its internal database (`user_roles`), keyed by verified corporate email address (`email`). Corporate IT provides user-to-role roster during onboarding.

### 4.3 Tenant Scoping
E-7 enforces strict multi-tenant isolation (ADR-001). Users belong to designated institutional tenants (e.g. `tenant-prod-001`). Tenant binding is resolved either via custom JWT attribute (`tenant_id`) or through internal organizational mapping tables keyed by corporate email.

---

## 5. Secret Provisioning & Configuration

The Identity Team delivers the following configuration parameters via the enterprise secret store (HashiCorp Vault / AWS Secrets Manager):

```bash
# E-7 OIDC Configuration Environment Variables
E7_AUTH_PROVIDER=google_oidc
E7_GOOGLE_OIDC_CLIENT_IDS=["<client-id-from-google-console>"]
E7_GOOGLE_OIDC_ALLOWED_DOMAINS=["<corporate-domain>"]
E7_GOOGLE_OIDC_CACHE_FALLBACK_SECONDS=3600

# Frontend SPA Environment Configuration (Injected at build/deploy)
VITE_OIDC_AUTHORITY=https://accounts.google.com
VITE_OIDC_CLIENT_ID=<client-id-from-google-console>
VITE_OIDC_REDIRECT_URI=https://<production-domain>/login
VITE_OIDC_SCOPE="openid email profile"
```

---

## 6. Production Verification Checklist

Prior to production sign-off, the Identity Team and E-7 Engineering will execute the following verification steps:

- [ ] **Discovery Verification:** Probe discovery document from production container:
  ```bash
  curl -fsS https://accounts.google.com/.well-known/openid-configuration | grep "jwks_uri"
  ```
- [ ] **JWKS Public Key Verification:**
  ```bash
  curl -fsS https://www.googleapis.com/oauth2/v3/certs | grep "keys"
  ```
- [ ] **Backend Verifier Verification:**
  ```bash
  python -c "
  from app.core.auth.google_oidc import GoogleOidcVerifier
  verifier = GoogleOidcVerifier(
      client_ids=['<client-id>'],
      allowed_hosted_domains=['<corporate-domain>']
  )
  print('VERIFIER_INITIALIZED: Ready for token verification.')
  "
  ```
- [ ] **Interactive PKCE Login Test:** Execute browser login flow on production domain; verify successful token acquisition, user profile display, and zero token storage in `localStorage`/`sessionStorage` (memory-only token retention verified).
- [ ] **Negative Domain Test:** Attempt login with personal `@gmail.com` account; verify backend rejects with `HTTP 401 Unauthorized: Invalid hosted domain`.

---

## 7. Status Transition Criteria

* Delivery of registered Google OAuth Client ID and authorized redirect URIs transitions:  
  `PRODUCTION_COMPANY_OIDC` from `BLOCKED_EXTERNAL` to `PASS`.
