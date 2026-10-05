# E-7 Phase-0 External Provider Decision Record

## 1. Purpose

This document constitutes the official decision record for the **two external-provider selections** required for Phase-0 exit of the E-7 Operations Engine:
1. **Decision D-EXT-01**: Production Identity Provider (IdP).
2. **Decision D-EXT-02**: First production calendar provider/system.

The project owner/user has explicitly authorized selection of the best-fit production providers based on E-7 requirements. This document formally records those project-level decisions with exact technical contract semantics.

> [!IMPORTANT]
> **Governance Scope & Negative Assertions**:
> - This is a **PROVIDER SELECTION / GOVERNANCE CLOSURE** record only.
> - It does **NOT** claim that these providers were previously deployed by IR Infotech.
> - It does **NOT** claim that an existing tenant or account has already been provisioned.
> - It does **NOT** claim that production credentials exist.
> - It does **NOT** claim that company licenses have already been purchased.
> - It does **NOT** claim that runtime integration has already been implemented.
> - Phase 1 implementation remains unstarted. Zero runtime code is introduced by this task.

---

## 2. Current Integration Status

Following the project-authorized selections and technical contract precision adjustments, the Phase-0 integration dependency statuses are:

| Integration Area | Current Status | Authoritative System / Selected Provider | Phase-0 Governance Note |
| :--- | :---: | :--- | :--- |
| **Exact Identity Provider (IdP)** | **`RESOLVED`** | Google Cloud Identity / Google Workspace managed organizational identity | Human authentication uses Google OpenID Connect. Google ID tokens are signed JWT identity assertions; OAuth access tokens are bearer credentials for Google APIs. Decision `D-EXT-01` resolved via `PROJECT_AUTHORIZED_PROVIDER_SELECTION`. |
| **First ERP/SIS Data Source** | **`RESOLVED`** | `mysql_iredu` (MySQL 8.0) | Read-only adapter contracted in Task 0G.4; runtime adapter intentionally `NOT_IMPLEMENTED`; live queries gated by `BLK-01`. |
| **First Calendar Provider** | **`RESOLVED`** | Google Calendar under Google Workspace | Bidirectional integration; Google Calendar REST API primary; CalDAV / RFC 5545 interoperability preserved; `UTC_SAFE_TIME_HANDLING`; `nextSyncToken` delta lifecycle; decision `D-EXT-02` resolved via `PROJECT_AUTHORIZED_PROVIDER_SELECTION`. |
| **E-8 Notification Contract / Mock** | **`RESOLVED`** | E-8 Engagement Engine | Transport-neutral contract and test mock frozen in Task 0G.6B. Production wire transport selection deferred to Phase 7. |

---

## 3. Architecture Boundary & Provider Neutrality

The selection of Google Cloud Identity and Google Calendar strictly preserves E-7's provider-neutral architecture:

1. **Preserve Adapter Boundaries (`app/integrations/...`)**:
   - All Google-specific interaction logic, API clients, and claim mappings are strictly isolated behind provider-neutral interfaces (`IdpAdapter`, `CalendarAdapter`).
   - The core domain model, workflow engine, task intelligence, and scheduling solver interact only with abstract, provider-neutral domain contracts.
2. **Canonical Entity Isolation**:
   - No Google-specific fields are placed in canonical E-7 domain entities.
   - Specifically, fields such as `google_calendar_id`, `google_user_id`, or `google_tenant_id` are **strictly prohibited** as canonical domain attributes.
   - Provider-specific IDs exist solely as external secondary mappings (e.g., `calendar_links.external_event_id`).
3. **Principal Identifier Mapping**:
   - Google `sub` remains the stable external Google account identifier.
   - Provider-neutral terminology is enforced: `external_identity_mapping` (provider/issuer, external_subject `sub`, E-7 principal reference).
   - No physical schema field such as `users.idp_sub` is frozen in Phase 0; the exact persistence schema will be defined during the appropriate foundation implementation/data-model task.
   - Email is **not** used as the canonical identity identifier.
4. **Tenant Scope Boundary (`hd != tenant_id`)**:
   - The Google hosted domain claim (`hd`) may be validated when restricting access to approved Google Workspace / Cloud Identity domains.
   - However, **`hd != canonical E-7 tenant_id`**.
   - Tenant derivation architecture:
     $$\text{Verified Google Identity} \longrightarrow \text{External Identity Mapping} \longrightarrow \text{Approved Org/Tenant Mapping} \longrightarrow \text{Authorized E-7 Principal Context}$$
   - Tenant scope remains an E-7 authorization decision. A valid Google hosted-domain claim alone MUST NOT grant arbitrary E-7 tenant access.
5. **Role / RBAC Follow-Up**:
   - Standard Google OIDC claims do **not** automatically provide E-7 application roles or institutional RBAC policy.
   - E-7 RBAC / institutional-role mapping is classified as an **implementation/configuration follow-up**.
   - Potential future sources include: E-7 internal role assignments, approved Google directory/group mapping, or upstream ERP/SIS role mapping. This follow-up does not reopen `D-EXT-01`.
6. **Service Authentication Baseline**:
   - Selecting Google as the human IdP does not replace the frozen Phase-1 machine-authentication baseline: `OIDC/JWT + service API key`.
   - Google OIDC = first human/user identity-provider integration.
   - Service API key = initial E-7 service/machine credential mechanism according to frozen Phase-1 baseline.
   - Google Service Accounts / Workload Identity Federation are classified as **`OPTIONAL_FUTURE_PROVIDER_CAPABILITY`** and are **not** required for Phase 1 service authentication.
7. **Standards and Interoperability Preservation**:
   - Identity: Standard OpenID Connect / OAuth 2.0 protocol and JWT claims boundary.
   - Calendar: CalDAV and RFC 5545 (iCalendar) compatibility is preserved alongside the Google Calendar REST API adapter.

---

## 4. D-EXT-01 — Production Identity Provider (RESOLVED)

- **Decision ID**: **`D-EXT-01`**
- **Decision Description**: Confirm the exact production Identity Provider used by the organization/customer environment for E-7 authentication.
- **Previous Status**: `PHASE_0_EXIT_DECISION_REQUIRED`
- **Current Status**: **`RESOLVED`**
- **Selected Provider / System**: **`GOOGLE_CLOUD_IDENTITY_GOOGLE_WORKSPACE`**
- **Display Name**: Google Cloud Identity / Google Workspace managed organizational identity
- **Decision Basis**: **`PROJECT_AUTHORIZED_PROVIDER_SELECTION`**
- **Target Phase (Decision Resolution)**: **`PHASE_0_EXIT`**
- **Technical Implementation Timing**: Phase 1 (Perimeter Authentication Foundation Sequence)

### Technical Specification Boundary
1. **Human Authentication Protocol**: Google OpenID Connect (OIDC).
2. **Token Semantics**:
   - **Google ID Token**: Signed JWT identity assertion validated for human authentication.
   - **Google OAuth Access Token**: Separate bearer credential used to call authorized Google APIs; **must not** be treated as the E-7 identity assertion, assumed to be a JWT, or decoded as an E-7 principal token.
3. **ID-Token Cryptographic Verification**:
   - Signature validation using public keys obtained through dynamic OIDC discovery metadata (`/.well-known/openid-configuration`) and JSON Web Key Set (`JWKS`).
   - Issuer validation (`https://accounts.google.com`).
   - Audience validation: `aud` must match an approved OAuth 2.0 client ID for the E-7 application/client configuration (not an "E-7 API resource-server identifier").
   - Expiration validation.
   - Nonce/replay protection where applicable to the authentication flow.
   - Hosted-domain validation (`hd`) only where deployment policy restricts access to approved Google Workspace / Cloud Identity domains.
   - JWKS URI is discovered dynamically via provider metadata and is **never** hardcoded in domain/business logic.
4. **Deployment Boundary**: Organization-managed Google Workspace / Cloud Identity domain. Exact production organization/domain identifiers remain deployment configuration and **MUST NOT** be hardcoded in business logic.
5. **Secret Boundary**: Zero credentials, client secrets, passwords, private keys, access tokens, refresh tokens, or production tenant credentials are documented, handled, or stored.

### Follow-Up Technical Configuration Requirements
Future deployment configuration must establish:
- **OIDC issuer/discovery metadata**: Discovery issuer URI (`https://accounts.google.com`) and metadata endpoints.
- **JWKS location discovered through provider metadata**: Discovery-driven certs endpoint.
- **Client/application registration**: OAuth 2.0 Client ID registered in Google Cloud console for E-7 application configuration.
- **Expected audience (`aud`)**: OAuth 2.0 client ID(s) matching the registered E-7 client configuration.
- **Stable subject mapping**: Google `sub` mapped via provider-neutral `external_identity_mapping`.
- **Organization/domain scope validation**: Hosted domain (`hd`) validation against authorized tenant boundary where policy dictates (`hd != tenant_id`).
- **Role / RBAC mapping**: Implementation/configuration follow-up for binding authenticated principals to institutional roles.
- **Service authentication**: Initial machine-to-machine authentication baseline uses service API keys per Phase-1 foundation. Google Service Accounts / Workload Identity Federation remain optional future capabilities.

---

## 5. D-EXT-02 — First Production Calendar Provider (RESOLVED)

- **Decision ID**: **`D-EXT-02`**
- **Decision Description**: Confirm the first production calendar system/provider that E-7 must integrate with.
- **Previous Status**: `PHASE_0_EXIT_DECISION_REQUIRED`
- **Current Status**: **`RESOLVED`**
- **Selected Provider / System**: **`GOOGLE_WORKSPACE_CALENDAR`**
- **Display Name**: Google Calendar under Google Workspace
- **Initial Integration Direction**: **`BIDIRECTIONAL`**
- **Decision Basis**: **`PROJECT_AUTHORIZED_PROVIDER_SELECTION`**
- **Target Phase (Decision Resolution)**: **`PHASE_0_EXIT`**
- **Technical Implementation Timing**: Later roadmap phases (Phases 4/5/7)

### Technical Specification Boundary
1. **Primary Provider Integration**: Google Calendar REST API (`v3`).
2. **Standards / Interoperability Boundary**: CalDAV, RFC 5545 / iCalendar.
3. **Calendar Authorization Topology**:
   - `CALENDAR_AUTHORIZATION_TOPOLOGY = TO_BE_CONFIRMED_DURING_IMPLEMENTATION`.
   - Valid implementation approaches: delegated per-user OAuth, organization/admin-authorized service account with domain-wide delegation, or another Google-supported least-privilege model.
   - Domain-wide delegation is **not** mandatory.
   - Governing rule: least privilege + approved Google Workspace administrator policy + approved OAuth scopes.
4. **Time Semantics (`UTC_SAFE_TIME_HANDLING`)**:
   - Canonical scheduling instants may be normalized to UTC internally.
   - Original/provider timezone metadata is preserved when synchronization requires it.
   - Google Calendar all-day event date semantics are preserved; timezone/DST conversions are handled explicitly; all-day dates are **never** converted into arbitrary UTC instants.
5. **Idempotency Architecture**:
   - E-7 owns synchronization idempotency via operation/command identity, external event mappings, persisted synchronization state, deterministic duplicate detection, and conditional/version-aware updates.
   - Google Calendar does **not** provide generic idempotency keys; no Google Calendar idempotency-key API is fabricated.
6. **Incremental Synchronization Lifecycle (`nextSyncToken`)**:
   - Step 1: Perform initial synchronization.
   - Step 2: Persist returned `nextSyncToken`.
   - Step 3: Use `nextSyncToken` for subsequent incremental delta synchronization.
   - Step 4: Persist newly returned `nextSyncToken`.
   - Step 5: If Google returns HTTP 410 / invalid sync token: invalidate local provider-sync cursor/state as appropriate, perform a new full synchronization, and obtain a fresh `nextSyncToken`.
   - This lifecycle is delta-cursor management and is **not** described as security-token rotation.
7. **Push-Notification Validation**:
   - Webhook callback endpoint must use HTTPS.
   - E-7 tracks issued channel IDs and expected resource IDs/metadata.
   - Use an unpredictable channel token (`X-Goog-Channel-Token`) verified against the expected stored value.
   - `X-Goog-Resource-State` is treated as resource-state metadata, **not** as authentication.
   - Notifications are change signals and do **not** contain complete event data; after a valid notification, E-7 retrieves/synchronizes provider state through the Google Calendar API.
8. **Synchronization Reliability Architecture**:
   > [!IMPORTANT]
   > **Sync Reliability Rule**:
   > Google Calendar change notifications are an optimization and change signal, **NOT** the sole source of synchronization correctness.
   > - Notifications do not contain event payloads, and Google does not guarantee delivery of every notification.
   > - E-7 scheduling remains **canonical** for E-7-owned scheduling state.
   > - Google Calendar remains **authoritative** for its native external calendar state.
   > - Synchronization must preserve the frozen external-ID mapping boundary in `calendar_links`.
   > - Provider adapter design combines:
   >   1. **Push notifications** (reduces polling latency)
   >   2. **Incremental synchronization** (`nextSyncToken` delta queries; HTTP 410 triggers full re-sync)
   >   3. **Periodic reconciliation / recovery** (optional E-7 resilience mechanism to recover from dropped or delayed notifications)
   > This prevents missed provider notifications from causing permanent calendar divergence.

### Follow-Up Technical Configuration Requirements
Future deployment configuration must establish:
- **Authorization topology selection**: Delegated OAuth vs. admin-authorized service account per deployment policy.
- **Approved calendar scopes**: Least-privilege Google Calendar OAuth scopes.
- **External calendar IDs**: Target calendar resource/mailbox identifiers (deployment configuration; not hardcoded).
- **Push notification channel lifecycle**: Webhook watch channel creation, expiration renewal, and stop.
- **Incremental sync token persistence**: Storage and lifecycle of provider `nextSyncToken` values.
- **Event version/ETag handling**: Optimistic concurrency control using ETags.
- **Webhook callback verification**: HTTPS verification of incoming `X-Goog-Channel-Token`.
- **Reconciliation schedule**: Periodic recovery sync schedule.

---

## 6. Security and Credential Boundaries

To ensure strict compliance with system security invariants ([`docs/IMPLEMENTATION_GUARDRAILS.md`](file:///f:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md), Section 4):

### Non-Secret Configuration Metadata vs. Secret Credential Material
1. **NON-SECRET CONFIGURATION METADATA (Documented / Permitted)**:
   - Provider product selections: Google Cloud Identity and Google Calendar under Google Workspace.
   - Protocols: OpenID Connect, OAuth 2.0, CalDAV, RFC 5545.
   - Integration direction: BIDIRECTIONAL.
   - Conceptual capabilities and reliability rules.
   - Parameter definitions: Issuer URL format, dynamic JWKS discovery metadata, claim mapping semantics, `nextSyncToken` lifecycle.
2. **SECRET CREDENTIAL MATERIAL (Strictly Prohibited & Zero Recorded)**:
   - Passwords
   - Client secrets
   - Private keys / Service Account JSON keys
   - Access tokens
   - Refresh tokens
   - API keys
   - Production tenant credentials / live calendar IDs

### Secret-Management Mandate
Production credentials and secrets MUST be managed through an approved secure secret-management mechanism and MUST NOT be stored in source control or decision documents.

---

## 7. Phase-0 Exit Gate Status

The precision corrections in Task 0I.2A do not alter the provider selections or reopen the Phase-0 exit gate:
- **`D-EXT-01` (IdP Selection)**: **`RESOLVED`** (Selected: `GOOGLE_CLOUD_IDENTITY_GOOGLE_WORKSPACE`).
- **`D-EXT-02` (Calendar Selection)**: **`RESOLVED`** (Selected: `GOOGLE_WORKSPACE_CALENDAR`, `BIDIRECTIONAL`).
- **Active Phase-0 Exit Blockers**: **0** (Zero remaining).
- **Phase-0 Engineering Baseline**: **`COMPLETE`**.
- **Phase-0 Overall Exit Status**: **`PASS`**.
- **Phase-1 Engineering Design Readiness**: **`READY`**.
- **Phase-1 Start Authorization**: **`AUTHORIZED`**.
- *Important*: `AUTHORIZED` means the governance gate permits Phase 1. It does NOT mean Phase 1 has been started. Phase 1 implementation remains unstarted.

---

## 8. Recorded Authoritative Selection Responses

```text
============================================================
E-7 EXTERNAL PROVIDER SELECTION RESPONSE (RECORDED)
============================================================

D-EXT-01 — Production Identity Provider (IdP)

Exact provider/system:
GOOGLE_CLOUD_IDENTITY_GOOGLE_WORKSPACE
(Google Cloud Identity / Google Workspace managed organizational identity)

Authentication protocol:
Google OpenID Connect (OIDC) / OAuth 2.0

Token model:
Google ID token = signed JWT identity assertion
Google OAuth access token = API bearer credential (not E-7 identity assertion)

Verification:
Dynamic OIDC discovery metadata + JWKS; audience matched to OAuth client ID;
hosted-domain (hd) validated where applicable; hd != canonical E-7 tenant_id

Deployment boundary:
Organization-managed Google Workspace / Cloud Identity domain
(Exact identifiers remain deployment configuration)

Decision basis:
PROJECT_AUTHORIZED_PROVIDER_SELECTION

Status:
RESOLVED

------------------------------------------------------------

D-EXT-02 — First Production Calendar Provider

Exact provider/system:
GOOGLE_WORKSPACE_CALENDAR
(Google Calendar under Google Workspace)

Initial integration direction:
BIDIRECTIONAL

Primary provider integration:
Google Calendar REST API

Standards/interoperability boundary:
CalDAV, RFC 5545 / iCalendar

Authorization topology:
CALENDAR_AUTHORIZATION_TOPOLOGY = TO_BE_CONFIRMED_DURING_IMPLEMENTATION
(Delegated per-user OAuth or admin service account; least privilege; domain-wide delegation not mandatory)

Time handling:
UTC_SAFE_TIME_HANDLING (preserves timezone and all-day date semantics)

Idempotency:
E-7-owned idempotency (Google Calendar does not provide generic idempotency keys)

Sync reliability rule:
Push notifications (change signal) + incremental sync (nextSyncToken / HTTP 410 re-sync) + periodic reconciliation

Decision basis:
PROJECT_AUTHORIZED_PROVIDER_SELECTION

Status:
RESOLVED

============================================================
```

---

## 9. Next Governance Step

The Phase-0 exit gate remains officially closed and cleared (`PASS`).
Phase 1 start is **`AUTHORIZED`**, but Phase 1 implementation is **NOT STARTED**.
