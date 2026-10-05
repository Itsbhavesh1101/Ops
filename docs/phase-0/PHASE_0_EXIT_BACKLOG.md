# E-7 Phase-0 Exit Backlog & Follow-Up Register

## 1. Summary

| Metric | Count / Status | Governance Meaning |
| :--- | :---: | :--- |
| **Total Backlog Items** | **10** | Complete register of all Phase-0 audit items across tasks. |
| **Active Blocking Exit Items** | **0** | **Zero blocking items remain.** All Phase-0 exit blockers resolved. |
| **Resolved Exit Gate Items** | **2** | Decisions `P0-EXIT-001` (IdP) and `P0-EXIT-002` (Calendar) formally resolved via project-authorized provider selections. |
| **Non-Blocking Follow-Up Items** | **8** | Future-phase benchmarks, sizing validations, institutional gates, and transport selections. |
| **Phase-0 Exit Gate Status** | **`PASS`** | All exit blockers cleared; Phase-0 governance gate is officially closed and passed. |
| **Phase-1 Start Authorization** | **`AUTHORIZED`** | Governance gate cleared; Phase 1 is authorized to proceed (**Implementation Not Started**). |

### Decision Timing vs. Technical Implementation Timing
This backlog strictly distinguishes:
- **Decision / Governance Resolution Timing**: When a business decision or stakeholder confirmation must occur to clear a governance gate (e.g., `PHASE_0_EXIT`).
- **Technical Implementation Timing**: When engineering builds the concrete runtime adapter, configuration, or infrastructure according to the project roadmap (e.g., Phase 1 perimeter auth, Phase 7 integrations).

---

## 2. Active Blocking Exit Items
Zero active blocking items remain. All Phase-0 exit-gate blocking items have been formally resolved.

| Backlog ID | Category | Title & Scope | Owner Class | Target Phase (Resolution) | Status |
| :--- | :--- | :--- | :--- | :---: | :---: |
| *None* | — | *All Phase-0 exit blockers have been formally resolved.* | — | — | **`NONE`** |

---

## 2.1 Resolved Exit Gate Items (Audit Trail)
Historical exit-gate blocking items and their formal resolutions are preserved below:

| Backlog ID | Historical Severity | Title & Resolution Summary | Decision Basis | Target Phase (Resolution) | Technical Implementation Timing | Status |
| :--- | :---: | :--- | :--- | :---: | :--- | :---: |
| **`P0-EXIT-001`** | `BLOCKER` (Historical) | **Confirm Exact Production Identity Provider (IdP)**<br>**Resolution**: Google Cloud Identity / Google Workspace organizational identity selected as first production IdP. OpenID Connect / OAuth 2.0. Google ID tokens are signed JWT identity assertions. OAuth access tokens are separate bearer credentials for calling authorized Google APIs and are not used as the canonical E-7 identity assertion. Dynamic discovery metadata + JWKS; audience matched to approved OAuth 2.0 client ID(s) for E-7 app; `sub` mapped via provider-neutral `external_identity_mapping` (no `users.idp_sub` physical schema freeze; email not canonical ID). `hd != canonical E-7 tenant_id`; tenant access requires E-7 approved mapping. E-7 RBAC / institutional-role mapping is an implementation/configuration follow-up. Service API key remains initial service-auth baseline; Google Service Accounts / WIF are `OPTIONAL_FUTURE_PROVIDER_CAPABILITY`. Organization-managed domain boundary; deployment-time configuration. Zero secrets stored. Provider-neutral architecture preserved; zero Google canonical entity fields (`google_user_id` and `google_tenant_id` prohibited). | `PROJECT_AUTHORIZED_PROVIDER_SELECTION` | **`PHASE_0_EXIT`** | Phase 1 perimeter authentication setup (11-step foundation sequence). | **`RESOLVED`** |
| **`P0-EXIT-002`** | `BLOCKER` (Historical) | **Confirm First Production Calendar System/Provider**<br>**Resolution**: Google Calendar under Google Workspace selected as first production calendar provider with BIDIRECTIONAL integration direction. Google Calendar REST API primary; CalDAV / RFC 5545 interoperability preserved. Calendar authorization topology: `TO_BE_CONFIRMED_DURING_IMPLEMENTATION` (delegated per-user OAuth, admin service account with domain-wide delegation, or another least-privilege model; domain-wide delegation is not mandatory). `UTC_SAFE_TIME_HANDLING`: internal UTC normalization while preserving original timezone metadata and all-day date semantics (no arbitrary UTC instant conversion). E-7 owns synchronization idempotency (no fabricated Google idempotency-key API). Incremental sync token lifecycle: persist `nextSyncToken`; HTTP 410 invalid sync token triggers local state invalidation and full re-sync to obtain fresh `nextSyncToken` (not security-token rotation). Push notifications: HTTPS callback; unpredictable `X-Goog-Channel-Token` verification; `X-Goog-Resource-State` treated as metadata not authentication; notifications are change signals without event payloads (sync state retrieved via API); periodic reconciliation as E-7 resilience mechanism (Google does not guarantee delivery of every notification). E-7 scheduling canonical for E-7 state; Google Calendar authoritative for native external state; secondary external ID mapping in `calendar_links`; zero Google canonical entity fields (`google_calendar_id` prohibited); zero secrets stored. | `PROJECT_AUTHORIZED_PROVIDER_SELECTION` | **`PHASE_0_EXIT`** | Later roadmap phases (Phases 4/5/7). | **`RESOLVED`** |

---

## 3. Non-Blocking Follow-Up Items
These items represent technical benchmarks, capacity sizing validations, integration gate closures, and governance reviews that are intentionally deferred to future project phases and do not block Phase-0 exit gate completion.

| Backlog ID | Category | Title & Scope | Owner Class | Target Phase | Status |
| :--- | :--- | :--- | :--- | :---: | :---: |
| **`P0-NB-001`** | `PERFORMANCE_BENCHMARK` | **Empirical Benchmarking of Task 0H.2 Candidate SLOs**<br>Execute load and stress testing against containerized services to benchmark candidate latency, throughput, and error rate proposals during future benchmarking. | `ENGINEERING FOLLOW-UP` | `TO_BE_PLANNED` | **`OPEN`** |
| **`P0-NB-002`** | `CAPACITY_PLANNING` | **First-Tenant Actual Business Workload Confirmation**<br>Confirm actual student enrollment, concurrent active user volume, and daily operational task load for the initial commercial deployment. | `BUSINESS / PLATFORM CONFIRMATION REQUIRED` | `TO_BE_PLANNED` | **`OPEN`** |
| **`P0-NB-003`** | `COMPLIANCE_REVIEW` | **Final Legal & Compliance Data Retention Policy Approval**<br>Statutory legal review and sign-off on operational data retention ceilings, audit trail lifecycles, and tenant purging rules. Effective retention must satisfy governing legal/regulatory, contractual, customer, and tenant policy requirements. | `COMPLIANCE INPUT REQUIRED` | `TO_BE_PLANNED` | **`OPEN`** |
| **`P0-NB-004`** | `BUSINESS_CONTINUITY` | **Formal RTO / RPO Disaster Recovery Targets Definition**<br>Business and infrastructure stakeholder specification of binding Recovery Time Objective and Recovery Point Objective targets if production requires them. RTO/RPO remain ungrounded in available sources. | `BUSINESS / PLATFORM CONFIRMATION REQUIRED` | `TO_BE_PLANNED` | **`OPEN`** |
| **`P0-NB-005`** | `CAPACITY_PLANNING` | **Physical Storage Capacity Empirical Measurement**<br>Measure physical database disk utilization per 1,000 workflows, audit records, and outbox events under test load during benchmarking. Sizing remains MEASURE_DURING_BENCHMARKING. | `ENGINEERING FOLLOW-UP` | `TO_BE_PLANNED` | **`OPEN`** |
| **`P0-NB-006`** | `INTEGRATION_SCOPE_GATE` | **`BLK-01`: Authoritative Organization / Tenant Scoping is Unresolved for Live MySQL Production Context Retrieval**<br>Upstream MySQL database (`iredu`) lacks tenant partitioning; live context retrieval is blocked until business confirmation defines the exact tenant partitioning predicate (`organization_scope_reference`). Authoritative organization / tenant scoping is unresolved for live MySQL production context retrieval. Narrow impact: does not block generic E-7 operational functions, internal PostgreSQL execution, or synthetic testing. | `BUSINESS / PLATFORM CONFIRMATION REQUIRED` | `TO_BE_PLANNED` | **`OPEN`** |
| **`P0-NB-007`** | `SCHEDULING_RESOURCE_GATE` | **`BLK-02`: Authoritative Academic Physical-Resource Model / Authority is Unresolved for Resource/Scheduling Production Use**<br>Upstream MySQL schema does not establish an authoritative academic room/resource model with required properties (capacity, type, equipment). Authoritative academic physical-resource model / authority is unresolved for resource/scheduling production use. Gates production physical-resource scheduling where authoritative room data is required. Narrow impact: does not block generic Task Intelligence planning, curricular timelines, or staff role assignment. Does not automatically imply UniTime or student cohort mapping. | `BUSINESS / PLATFORM CONFIRMATION REQUIRED` | `TO_BE_PLANNED` | **`OPEN`** |
| **`P0-NB-008`** | `INTEGRATION_DESIGN` | **Select Production E-8 Notification Wire Transport**<br>Select production wire transport binding (HTTP Webhook vs. AMQP/RabbitMQ queue) for outbound E-8 notifications. Logical payload contract and mock boundary are established (`e8_contract_mock = RESOLVED`). Production E-8 engagement integration is explicitly assigned to Phase 7 in frozen roadmap (RTM `FR-INT-01`). | `ENGINEERING FOLLOW-UP` | `PHASE_7` | **`OPEN`** |

---

## 4. Resolution Records

### Resolution for P0-EXIT-001 (Identity Provider Selection)
- **Selected Provider**: Google Cloud Identity / Google Workspace managed organizational identity (`GOOGLE_CLOUD_IDENTITY_GOOGLE_WORKSPACE`).
- **Decision Basis**: `PROJECT_AUTHORIZED_PROVIDER_SELECTION`.
- **Status**: **`RESOLVED`** at the Phase-0 exit gate (`target_phase = PHASE_0_EXIT`).
- **Technical Boundary**: OpenID Connect / OAuth 2.0. Google ID tokens are signed JWT identity assertions. OAuth access tokens are separate bearer credentials for calling authorized Google APIs and are not used as the canonical E-7 identity assertion. Verified via dynamic discovery metadata and JWKS.
- **ID-Token Validation**: Signature validation via keys obtained through dynamic OIDC discovery/JWKS (URI not hardcoded in domain logic); issuer validation (`https://accounts.google.com`); audience validation (matched to approved OAuth 2.0 client ID(s) for the E-7 application/client configuration, not an E-7 API resource-server identifier); expiration validation; nonce/replay protection where applicable; hosted-domain (`hd`) validation only where deployment policy restricts access to approved Google Workspace / Cloud Identity domains.
- **Principal & Tenant Mapping**: Google `sub` is the stable external account identifier mapped via provider-neutral `external_identity_mapping` (provider/issuer, external_subject `sub`, E-7 principal reference). No physical schema field like `users.idp_sub` is frozen; email is not used as canonical identity identifier. `hd != canonical E-7 tenant_id`: verified Google identity -> external identity mapping -> approved organization/tenant mapping -> authorized E-7 principal context. Tenant scope remains an E-7 authorization decision; `hd` alone does not grant arbitrary E-7 tenant access.
- **Role / RBAC Mapping**: E-7 RBAC / institutional-role mapping = `IMPLEMENTATION / CONFIGURATION FOLLOW-UP`. Standard Google OIDC claims do not automatically provide E-7 application roles or institutional RBAC policy. Future sources may include internal role assignments, approved Google directory/group mapping, or upstream ERP/SIS role mapping.
- **Service Authentication Baseline**: Service API key remains the frozen initial E-7 service/machine credential mechanism according to the frozen Phase-1 foundation. Google Service Accounts and Workload Identity Federation are classified as `OPTIONAL_FUTURE_PROVIDER_CAPABILITY` and are not mandatory for Phase 1.
- **Provider Neutrality**: Preserves `app/integrations/...` adapter boundary; zero Google canonical entity IDs (`google_user_id`, `google_tenant_id` prohibited); zero secrets stored.

### Resolution for P0-EXIT-002 (Calendar Provider Selection)
- **Selected Provider**: Google Calendar under Google Workspace (`GOOGLE_WORKSPACE_CALENDAR`).
- **Initial Integration Direction**: **`BIDIRECTIONAL`**.
- **Decision Basis**: `PROJECT_AUTHORIZED_PROVIDER_SELECTION`.
- **Status**: **`RESOLVED`** at the Phase-0 exit gate (`target_phase = PHASE_0_EXIT`).
- **Technical Boundary**: Google Calendar REST API primary; CalDAV and RFC 5545 / iCalendar interoperability preserved.
- **Calendar Authorization Topology**: `CALENDAR_AUTHORIZATION_TOPOLOGY = TO_BE_CONFIRMED_DURING_IMPLEMENTATION`. Valid implementation approaches include delegated per-user OAuth, organization/admin-authorized service account with domain-wide delegation, or another least-privilege model. Domain-wide delegation is not mandatory. Requirement: least privilege + approved Google Workspace admin policy + approved OAuth scopes.
- **Time Semantics**: `UTC_SAFE_TIME_HANDLING`. Canonical scheduling instants may be normalized to UTC internally; original provider timezone metadata is preserved when synchronization requires it; Google Calendar all-day event date semantics are preserved; timezone/DST conversions handled explicitly; all-day dates are never converted into arbitrary UTC instants.
- **Idempotency Architecture**: E-7 owns synchronization idempotency (operation identity, external event mappings, persisted sync state, version-aware updates). Google Calendar does not provide generic idempotency keys; no Google Calendar idempotency-key API is fabricated.
- **Sync Token Lifecycle**: Persist `nextSyncToken` after initial sync; use for subsequent incremental delta queries; persist newly returned `nextSyncToken`. If Google returns HTTP 410 / invalid sync token: invalidate local sync cursor/state and perform full re-synchronization to obtain fresh `nextSyncToken`. Not described as security-token rotation.
- **Push Notification Verification**: Webhook callbacks require HTTPS; track issued channel IDs and expected resource IDs; verify unpredictable `X-Goog-Channel-Token` against stored value; treat `X-Goog-Resource-State` as resource-state metadata, not as authentication. Notifications are change signals without complete event payloads; retrieve state via Google Calendar API.
- **Sync Reliability Rule**: Combines push notifications (to reduce polling latency) + incremental sync via `nextSyncToken` (primary delta retrieval) + periodic reconciliation (E-7 resilience mechanism against missed notifications, as Google does not guarantee delivery of every notification).
- **Provider Neutrality & Authority Boundary**: E-7 scheduling is canonical for E-7-owned scheduling state; Google Calendar is authoritative for native external state. Preserves `app/integrations/...` adapter boundary; zero Google canonical entity IDs (`google_calendar_id` prohibited); secondary external ID mapping in `calendar_links`; zero secrets stored.
---

## 5. Phase-1 Dependency & Authorization Notice
- The engineering foundation sequence for Phase 1 is fully specified and classified as `READY`.
- With all Phase-0 exit blockers cleared, Phase-1 implementation is **`AUTHORIZED`**.
- **Important Governance Notice**:
  > [!IMPORTANT]
  > **`AUTHORIZED` means the Phase-0 governance gate permits Phase 1.**
  > **It does NOT mean Phase 1 has been started.**
  > Phase 1 implementation remains unstarted; zero Phase-1 runtime code has been created.

---

## 6. Closure Rule & Gate Clearance
The Phase-0 exit gate has formally transitioned from `HOLD` to **`PASS`**:
1. `P0-EXIT-001` and `P0-EXIT-002` were formally resolved via project-authorized provider selections without introducing architectural defects or secret exposures.
2. All 13 Phase-0 deliverables are complete, all 25 requirements remain traceable, all 10 ADRs are consistent, all 18 logical fixture datasets are verified against `tests/fixtures/MANIFEST.json`, and all security scans are clean.
3. Zero exit blockers remain. The Phase-0 exit gate is officially closed and cleared.
