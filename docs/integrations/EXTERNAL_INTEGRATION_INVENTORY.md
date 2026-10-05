# E-7 Phase-0 External Integration Inventory

## 1. Purpose

This document establishes the canonical Phase-0 inventory and evidence audit for all external integration dependencies of the **IR Infotech E-7 Operations Engine**. 

The objective of this audit is to rigorously determine—strictly from physically verified repository source documents, architectural blueprints, approved Architecture Decision Records (ADRs), and Phase-0 baseline contracts—the precise implementation and readiness status of external systems. This audit operates under strict governance rules: **no provider decisions, communication protocols, endpoints, or credentials may be invented or assumed out of technical convenience**.

Specifically, this audit resolves:
1. The exact status of the Identity Provider (IdP) for human and machine authentication.
2. The current baseline and authority boundaries of the first upstream ERP/SIS data source (`mysql_iredu`).
3. The exact status and protocol requirements for calendar synchronization.
4. The architectural boundary, functional responsibilities, and event emission semantics between E-7 and the E-8 Engagement Engine.
5. The explicit separation between verified source facts, architectural requirements, approved engineering decisions, and unresolved business decision gaps.

> [!IMPORTANT]
> This is an **EVIDENCE AUDIT AND INVENTORY** task only (Task 0G.6A / 0G.6A.2). It does not implement integrations, write runtime code, select unverified commercial vendors, add third-party dependencies, connect to live systems, or commence Task 0G.6B or Phase 1.

---

## 2. Source Basis and Evidence Rules

### 2.1 Audited Source Material and Corpus Limitations
Every statement, classification, and status in this inventory is anchored in accessible, physically parsed source artifacts within the repository.

#### 2.1.1 Physically Available Source Documents
The following source documents were physically available in the local repository and parsed locally using **PyMuPDF (`fitz`)**:
1. **Source-of-Truth Specifications & Plans (PDFs physically accessed & parsed via PyMuPDF)**:
   - `docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf` (31 pages)
   - `docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf` (26 pages)
   - `docs/source-of-truth/IR_Infotech_E7_Operations_Engine_Open_Source_Research_Report.pdf` (19 pages)
   - `docs/source-of-truth/opes eng.pdf` (4 pages)
2. **Repository Governance and Architecture Baselines**:
   - `docs/SOURCE_OF_TRUTH.md` (Document hierarchy and precedence)
   - `docs/IMPLEMENTATION_GUARDRAILS.md` (Architectural invariants and security constraints)
   - `docs/REQUIREMENTS_TRACEABILITY_MATRIX.md` (Authoritative functional requirements FR-TI, FR-WF, FR-SC, FR-INT, FR-SEC)
   - `docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md` (System of Record and domain boundaries)
   - `docs/adr/` (Approved ADR-000 through ADR-009)
3. **Phase-0 Integration Contracts (Tasks 0G.1 through 0G.5)**:
   - `docs/integrations/MYSQL_SCHEMA_INVENTORY.md` / `.json` (Task 0G.1/0G.1A)
   - `docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md` / `.json` (Task 0G.2/0G.2B)
   - `docs/integrations/AI_CONTEXT_ACCESS_POLICY.md` / `.json` (Task 0G.3/0G.3B)
   - `docs/integrations/MYSQL_ERP_ADAPTER_CONTRACT.md` / `.json` (Task 0G.4/0G.4A)
   - `docs/integrations/CONTEXT_BUILDER_CONTRACT.md` / `.json` (Task 0G.5/0G.5A)

#### 2.1.2 Source-Corpus Limitation Explicitly Noted
The local repository currently contains only the compiled source PDFs listed above under `docs/source-of-truth/`. Early project references mention original foundation documents that are **not locally available as standalone files in the repository**, including:
- Original uncompiled *IR MAIN DOC*
- *Consolidated Engine Directory*
- Standalone *Engine Architecture — IR Infotech AI Ecosystem*
- Standalone *Task Intelligence specification*
- Standalone *Workflow specification*
- Standalone *Scheduling specification*

> [!NOTE]
> Because the local source corpus is limited to the available compiled PDFs and markdown baselines, the absence of a specific provider name in this audit establishes **`NOT FOUND IN AVAILABLE SOURCES`**, rather than an exhaustive proof of absence across unavailable uncompiled historical documents. Where an available authoritative document explicitly records that a provider decision is open (e.g., Section 39 of the *Complete Specification*), that finding is classified with **`HIGH`** confidence. Conversely, conclusions based purely on absence-of-mention across an incomplete local corpus are rated **`MEDIUM`** confidence.

### 2.2 Classification Vocabulary
All integration facts and conclusions are formally classified into one of the following five categories:
- **`SOURCE FACT`**: Directly stated in primary specifications or implementation plans without ambiguity.
- **`ARCHITECTURE REQUIREMENT`**: Established by architectural invariants, guardrails, or ADR decisions as a structural constraint.
- **`ENGINEERING DECISION ALREADY APPROVED`**: Reconciled and frozen in previous Phase-0 artifacts (Tasks 0A–0G.5).
- **`REQUIRES BUSINESS CONFIRMATION`**: A necessary decision point where sources define criteria or open questions but do not mandate a single provider or policy.
- **`NOT FOUND IN AVAILABLE SOURCES`**: Information entirely absent from current repository artifacts.

### 2.3 Provider Status Vocabulary
- **`EXACT_PROVIDER_VERIFIED`**: An exact provider or system is explicitly named and mandated by source material.
- **`PROVIDER_UNRESOLVED`**: The architectural protocol or capability is required, but no specific commercial or operational provider has been selected.
- **`PROVIDER_NOT_REQUIRED`**: The domain operates via provider-neutral open standards without requiring a proprietary provider.

### 2.4 Evidence Confidence Levels
- **`HIGH`**: Explicitly cited in an accessible authoritative source document or established by a frozen approved Phase-0 artifact.
- **`MEDIUM`**: Supported by secondary context, or represents an absence-of-mention finding across the incomplete local source corpus.
- **`LOW`**: Indirect, ambiguous, or inferred evidence.

---

## 3. Phase-0 Integration Matrix

The table below catalogs exactly the four Phase-0 external integration domains governing E-7:

| Integration Domain | Required? | Exact Provider / System | Provider Status | Direction | Authority | Current Contract Status | Evidence Quality | Production Blocker / Decision Needed |
| :--- | :---: | :--- | :---: | :---: | :--- | :--- | :---: | :--- |
| **Identity / IdP** | Yes | `Google Cloud Identity / Google Workspace managed organizational identity` | `EXACT_PROVIDER_RESOLVED` | `AUTHENTICATION_FLOW` | External IdP owns auth assertions; E-7 owns tenant resolution & RBAC | `SELECTION_RESOLVED` (Google OIDC ID tokens = signed JWTs; OAuth access tokens = API bearer credentials; aud matched to OAuth client ID; hd != tenant_id; service API key = service auth baseline) | `HIGH` (Project-authorized provider selection; Spec Sec. 39; Plan Sec. 14, 18) | **`RESOLVED`** (D-EXT-01 confirmed via `PROJECT_AUTHORIZED_PROVIDER_SELECTION`) |
| **ERP / SIS** | Yes | `mysql_iredu` (Institutional MySQL database) | `EXACT_PROVIDER_VERIFIED` (for DB schema `iredu`) | `INBOUND` (Initial) | Upstream ERP owns master records; E-7 PostgreSQL owns operational state | `PARTIALLY_VERIFIED` (Contracts 0G.1-0G.5 complete; runtime unbuilt) | `HIGH` (RTM FR-INT-02; Schema dump; Tasks 0G.1-0G.5) | `BLK-01` (Tenant scoping unresolved); `BLK-02` (Physical asset authority) |
| **Calendar** | Yes | `Google Calendar under Google Workspace` | `EXACT_PROVIDER_RESOLVED` | `BIDIRECTIONAL` | External calendar owns native state; E-7 owns schedule bindings & links | `SELECTION_RESOLVED` (REST API primary; CalDAV/RFC 5545 preserved; CALENDAR_AUTHORIZATION_TOPOLOGY to be confirmed; UTC_SAFE_TIME_HANDLING; E-7 owned idempotency; nextSyncToken lifecycle) | `HIGH` (Project-authorized provider selection; RTM FR-SC-05; Spec Sec. 39; Plan Sec. 13.2) | **`RESOLVED`** (D-EXT-02 confirmed via `PROJECT_AUTHORIZED_PROVIDER_SELECTION`) |
| **E-8 Notification / Engagement** | Yes | Engine 8 (`E-8 Engagement Engine`) | `EXACT_PROVIDER_VERIFIED` | `OUTBOUND` | E-7 owns notification triggers; E-8 owns multi-channel delivery & retries | `RESOLVED_MOCK` (Boundaries verified; transport-neutral contract & mock frozen in 0G.6B) | `HIGH` (RTM FR-INT-01; Spec Sec. 21, 22; Plan Sec. 13.3) | `PHASE_7` (Wire transport selection assigned to Phase 7; non-blocking) |

---

## 4. Identity / IdP Evidence Audit

### 4.1 Functional and Architectural Requirements
- **Standard Protocol Compliance**: The E-7 Operations Engine requires industry-standard **OpenID Connect (OIDC)** and **OAuth 2.0** utilizing JSON Web Tokens (**JWT**) for human user authentication (`IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`, Section 14, p. 15).
- **Cryptographic Token Verification**: The `api-service` ingress must validate token signatures, issuer, audience, and expiration via a JSON Web Key Set (**JWKS**) endpoint (`JWT_ISSUER` and `JWKS_URL`).
- **Machine / Service Identities**: Inter-service communication and background automated agents authenticate via scoped service API keys according to the frozen Phase-1 foundation baseline (`Complete Implementation Plan`, Section 14, p. 15).
- **Tenant Derivation & Cryptographic Enforcement**: Tenant context (`tenant_id`) **must always be derived exclusively from verified JWT token claims or authorized machine identity scopes**. The system must never accept or trust a `tenant_id` supplied in request payloads, query parameters, or URL paths without cryptographic validation (`docs/IMPLEMENTATION_GUARDRAILS.md`, Section 3).
- **Role-Based Access Control (RBAC)**: Coarse-grained roles (`TENANT_ADMIN`, `PLANNER`, `WORKFLOW_APPROVER`, `SCHEDULER`, `AUDITOR`) are enforced at the API ingress and domain handlers. Fine-grained Attribute-Based Access Control (ABAC) is layered for object, department, and task-specific transitions (`Complete Specification`, Section 23, p. 17).

### 4.2 Exact Provider Evidence & Authoritative Selection
- **Source Documents Baseline**: Source documents established strict architectural requirements for OpenID Connect (OIDC), OAuth 2.0, JWT tokens, and cryptographic token verification via JWKS endpoints (`Complete Implementation Plan`, Section 14, p. 15), while leaving exact provider selection as an open pre-coding decision (`Complete Specification`, Section 39, p. 24).
- **Authoritative Project Selection**: The project owner/user has explicitly authorized selection of the best-fit production provider:
  - **Selected Provider**: **Google Cloud Identity / Google Workspace managed organizational identity** (`GOOGLE_CLOUD_IDENTITY_GOOGLE_WORKSPACE`).
  - **Decision Basis**: **`PROJECT_AUTHORIZED_PROVIDER_SELECTION`**.
  - **Human Authentication Protocol**: Google OpenID Connect (OIDC) / OAuth 2.0.
  - **Token Semantics**:
    - **Google ID Token**: Signed JWT identity assertion validated for human authentication.
    - **Google OAuth Access Token**: Separate bearer credential used to call authorized Google APIs; **must not** be treated as the E-7 identity assertion, assumed to be a JWT, or decoded as an E-7 principal token.
  - **Cryptographic Verification**:
    - Signature validation using public keys obtained through dynamic OIDC discovery metadata (`/.well-known/openid-configuration`) and JSON Web Key Set (`JWKS`).
    - Issuer validation (`https://accounts.google.com`).
    - Audience validation: `aud` must match an approved OAuth 2.0 client ID for the E-7 application/client configuration (not an "E-7 API resource-server identifier").
    - Expiration validation and nonce/replay protection where applicable.
    - Hosted-domain validation (`hd`) only where deployment policy restricts access to approved Google Workspace / Cloud Identity domains.
    - JWKS URI is discovered dynamically via provider metadata and is **never** hardcoded in domain/business logic.
  - **Deployment Boundary**: Organization-managed Google Workspace / Cloud Identity domain. Exact production organization/domain identifiers remain deployment configuration and **MUST NOT** be hardcoded in business logic.
  - **Principal Identifier Mapping**:
    - Google `sub` remains the stable external Google account identifier.
    - Provider-neutral terminology is enforced: `external_identity_mapping` (provider/issuer, external_subject `sub`, E-7 principal reference).
    - No physical schema field such as `users.idp_sub` is frozen in Phase 0; the exact persistence schema will be defined during the appropriate foundation implementation/data-model task.
    - Email is **not** used as the canonical identity identifier.
  - **Tenant Authorization Boundary (`hd != tenant_id`)**:
    - Google hosted domain (`hd`) may be validated to restrict access to approved institutional domains.
    - However, **`hd != canonical E-7 tenant_id`**.
    - Architecture: $\text{Verified Google Identity} \longrightarrow \text{External Identity Mapping} \longrightarrow \text{Approved Org/Tenant Mapping} \longrightarrow \text{Authorized E-7 Principal Context}$.
    - Tenant scope remains an E-7 authorization decision. An `hd` claim alone MUST NOT grant arbitrary E-7 tenant access.
  - **Role / RBAC Mapping Follow-Up**:
    - Standard Google OIDC claims do **not** automatically provide E-7 application roles or institutional RBAC policy.
    - E-7 RBAC / institutional-role mapping is an **implementation/configuration follow-up**. Possible sources: internal role assignments, Google directory/group mapping, or upstream ERP/SIS role mapping. Does not reopen `D-EXT-01`.
  - **Service Authentication Baseline**:
    - Initial machine-to-machine authentication uses service API keys per frozen Phase-1 foundation (`OIDC/JWT + service API key`).
    - Google Service Accounts / Workload Identity Federation are classified as **`OPTIONAL_FUTURE_PROVIDER_CAPABILITY`** and are **not** mandatory for Phase 1 service auth.
  - **Provider Neutrality Preservation**: Provider-neutral architecture is strictly maintained. The `app/integrations/...` adapter boundary is preserved. Zero Google-specific fields are introduced into canonical E-7 domain entities (`google_user_id` and `google_tenant_id` are strictly prohibited as canonical domain IDs). Google identifiers remain external mappings only.
  - **Secret Boundary**: Zero credentials, client secrets, passwords, private keys, access tokens, refresh tokens, or production tenant credentials are documented, handled, or stored.
- **Provider Status Classification**: **`EXACT_PROVIDER_RESOLVED`** (`RESOLVED`).

### 4.3 Unresolved Decisions
1. **Decision ID `DEC-IDP-01`**: Confirm the organization's exact production IdP provider.
   - *Status*: **`RESOLVED`**.
   - *Resolution*: Google Cloud Identity / Google Workspace managed organizational identity selected as first production IdP via project-authorized provider selection.
2. **Decision ID `DEC-IDP-02`**: Formalize the exact JWT token claim schema for `tenant_id` and user roles.
   - *Status*: **`FOLLOW_UP_CONFIGURATION`**.
   - *Resolution*: E-7 RBAC / institutional-role mapping is an implementation/configuration follow-up. Standard Google OIDC claims do not provide E-7 application roles. Possible future sources: internal role assignments, Google directory/group mapping, ERP/SIS mapping. Does not reopen `D-EXT-01`.

### 4.4 Phase-0 Status
- **Exact IdP Status**: **`RESOLVED`** (`EXACT_PROVIDER_RESOLVED`).
- **Protocol Status**: `VERIFIED` (Google OIDC for human authentication; ID tokens = signed JWTs; OAuth access tokens = API bearer credentials; service API key = service auth baseline).

---

## 5. ERP / SIS Evidence Audit

### 5.1 Concrete Upstream Source: `mysql_iredu`
- **Current Approved Baseline**: The company MySQL database `iredu` is the first concrete upstream institutional ERP/SIS source analyzed for E-7 (`docs/integrations/MYSQL_SCHEMA_INVENTORY.md`).
- **Logical Source Identifier**: `mysql_iredu`.
- **Underlying Technology**: MySQL 8.0 / MariaDB relational database engine.
- **Source Selection Status**: **`RESOLVED`** for Phase 0 (the concrete upstream source database has been identified, analyzed, and contracted).
- **Runtime Adapter Status**: `NOT_IMPLEMENTED` (scheduled for Phase 1 / Phase 7).
- **Live Production Readiness**: `BLOCKED_BY_BLK_01` (production live database queries blocked until tenant scoping predicate is confirmed).
- **Commercial Product Name**: `NOT_FOUND_IN_AVAILABLE_SOURCES`. The physical schema dump identifies the database name `iredu`, but does not contain commercial branding or application metadata. This is an informational metadata question and is **not** a Phase-0 exit blocker.

### 5.2 Access Mode and Operational Boundaries
- **Strict Read-Only Access**: Initial integration mode is strictly `READ_ONLY`. E-7 never executes `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, or any DDL/DML mutation against `mysql_iredu`.
- **Zero Direct Prompt / LLM Access**: Raw SQL connections, database handles, and unredacted table structures are strictly inaccessible to generative AI models. All context access is mediated by the provider-neutral Context Builder (`docs/integrations/CONTEXT_BUILDER_CONTRACT.md`).
- **PostgreSQL Canonical System of Record**: PostgreSQL is the sole authoritative System of Record (SoR) for all E-7-owned operational state, including goals, plans, WBS tasks, workflow instances, schedules, and audit logs (`docs/adr/ADR-006-system-of-record.md`).
- **Upstream Master Authority**: The external ERP remains authoritative for the upstream external/master records it owns (institutional curricula, course structures, student cohorts, HR records). E-7 maintains isolated foreign ID mappings (`external_id`, `source_system = 'mysql_iredu'`).

### 5.3 Contract Maturity (Tasks 0G.1 through 0G.5)
The integration boundary for `mysql_iredu` is fully specified across five completed Phase-0 artifacts:
1. `MYSQL_SCHEMA_INVENTORY`: Complete inventory of 247 tables, 2,637 columns, and 307 foreign keys.
2. `MYSQL_TASK_INTELLIGENCE_DATA_MAPPING`: 70 tables evaluated; 14 designated `REQUIRED_FOR_INITIAL_TI`; exactly 28 columns approved for planner context.
3. `AI_CONTEXT_ACCESS_POLICY`: Complete data governance policy; strict student PII exclusion; staff attributes treated as planning context rather than binding eligibility proof.
4. `MYSQL_ERP_ADAPTER_CONTRACT`: Complete provider-neutral adapter interface (`ERPSourceAdapter`), data extraction schemas, batching, and error handling.
5. `CONTEXT_BUILDER_CONTRACT`: Complete context assembly pipeline, token budgeting, and redaction boundary.

### 5.4 Production Blockers and Physical Asset Gates
Live production context extraction from `mysql_iredu` is currently gated by two formal architectural blockers:
- **`BLK-01` (Authoritative Tenant / Organization Scoping Unresolved)**: None of the 247 tables contain a native `tenant_id` column; 10 tables contain a `branch_id` column. Live multi-tenant database extraction is blocked until business confirmation defines the exact tenant partitioning predicate.
- **`BLK-02` (Physical Academic Resource Authority Unresolved)**: Active `SCHEDULING_RESOURCE_PRODUCTION_GATE`. No authoritative academic physical-resource model suitable for E-7 scheduling has been established. Room-related data such as hostel room structures may exist, and scheduling/exam tables may contain free-text `room_no`, but authoritative room capacity, type, and equipment semantics remain unresolved. Automated room capacity scheduling is gated until physical asset authority is formalized.

---

## 6. Calendar Evidence Audit

### 6.1 Functional and Architectural Requirements
- **Authoritative Requirement (FR-SC-05)**:
  > *"Import/export iCalendar/CalDAV-compatible events and retain mapping to internal schedule entities."* (`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`, p. 20 / lines 496-515; `Complete Specification`, Section 9, p. 7).
- **Synchronization Flows**:
  - **Outbound**: When a schedule job transitions to `PUBLISHED` (`schedule.published`), E-7 generates RFC 5545 `VEVENT` entries to synchronize timetable slots, examination schedules, and deadlines to external calendars (`Complete Implementation Plan`, Section 12.4 & 13.2).
  - **Inbound**: E-7 imports external availability intervals and blackout periods into canonical resource availability schedules (`Complete Implementation Plan`, Section 12.2 & 13.2).
- **Secondary Mapping Table**: E-7 PostgreSQL maintains the `calendar_links` mapping table, tracking `external_event_id`, `sync_status`, `sync_token`, and `tenant_id`. External calendar IDs are strictly secondary mapping attributes and are **never exposed as canonical E-7 IDs** (`docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md`, Section 4.1 & 4.2).
- **Idempotency Architecture**: E-7 owns synchronization idempotency via operation/command identity, external event mappings, persisted synchronization state, deterministic duplicate detection, and conditional/version-aware updates. Google Calendar does **not** provide generic idempotency keys; no Google Calendar idempotency-key API is fabricated.
- **Time Semantics (`UTC_SAFE_TIME_HANDLING`)**: Canonical scheduling instants may be normalized to UTC internally; original provider timezone metadata is preserved when synchronization requires it; Google Calendar all-day event date semantics are preserved; timezone/DST conversions are handled explicitly; all-day dates are **never** converted into arbitrary UTC instants.
- **Auditable Sync Failures**: When an external calendar service is unreachable, E-7 must persist an auditable failure/retry status rather than pretending success.

### 6.2 Provider Evidence Audit & Authoritative Selection
- **Source Documents Baseline**: Source documents established functional requirement FR-SC-05 for calendar synchronization, RFC 5545 iCalendar / CalDAV interchange compatibility, and secondary mapping table `calendar_links` in PostgreSQL (`Complete Implementation Plan`, Section 12.4 & 13.2), while designating the target provider selection as an open pre-coding decision (`Complete Specification`, Section 39, p. 24).
- **Authoritative Project Selection**: The project owner/user has explicitly authorized selection of the best-fit production provider:
  - **Selected Provider**: **Google Calendar under Google Workspace** (`GOOGLE_WORKSPACE_CALENDAR`).
  - **Decision Basis**: **`PROJECT_AUTHORIZED_PROVIDER_SELECTION`**.
  - **Initial Integration Direction**: **`BIDIRECTIONAL`**.
  - **Primary Provider Integration**: Google Calendar REST API (`v3`).
  - **Standards / Interoperability Boundary**: CalDAV, RFC 5545 / iCalendar.
  - **Calendar Authorization Topology**:
    - `CALENDAR_AUTHORIZATION_TOPOLOGY = TO_BE_CONFIRMED_DURING_IMPLEMENTATION`.
    - Valid implementation approaches: delegated per-user OAuth, organization/admin-authorized service account with domain-wide delegation, or another Google-supported least-privilege model.
    - Domain-wide delegation is **not** mandatory.
    - Requirement: least privilege + approved Google Workspace administrator policy + approved OAuth scopes.
  - **Time Semantics**: `UTC_SAFE_TIME_HANDLING`.
  - **Idempotency**: E-7-owned idempotency (no fabricated Google idempotency keys).
  - **Incremental Sync Token Lifecycle (`nextSyncToken`)**:
    1. Perform initial synchronization.
    2. Persist returned `nextSyncToken`.
    3. Use `nextSyncToken` for subsequent incremental delta synchronization.
    4. Persist newly returned `nextSyncToken`.
    5. If Google returns HTTP 410 / invalid sync token: invalidate local provider-sync cursor/state as appropriate, perform a new full synchronization, and obtain a fresh `nextSyncToken`. Not described as security-token rotation.
  - **Push-Notification Validation**:
    - Webhook callback endpoint must use HTTPS.
    - Track issued channel IDs and expected resource IDs/metadata.
    - Use unpredictable channel token (`X-Goog-Channel-Token`) verified against expected stored value.
    - Treat `X-Goog-Resource-State` as resource-state metadata, **not** as authentication.
    - Notifications are change signals and do **not** contain complete event data; after a valid notification, retrieve/synchronize provider state through the Google Calendar API.
  - **Sync Reliability Rule**:
    > [!IMPORTANT]
    > **Sync Reliability Rule**: Google Calendar change notifications are an optimization and change signal, **NOT** the sole source of synchronization correctness.
    > - Notifications do not contain event payloads, and Google does not guarantee delivery of every notification.
    > - E-7 scheduling remains **canonical** for E-7-owned scheduling state.
    > - Google Calendar remains **authoritative** for its native external calendar state.
    > - Synchronization must preserve the frozen external-ID mapping boundary in `calendar_links`.
    > - Provider adapter design combines: (1) push notifications to reduce polling latency, (2) incremental synchronization via `nextSyncToken` as primary delta-retrieval mechanism (with HTTP 410 invalid sync token triggering full re-sync), and (3) optional periodic reconciliation/recovery as an E-7 resilience mechanism.
  - **Provider Neutrality Preservation**: Provider-neutral architecture is strictly maintained. The `app/integrations/...` adapter boundary is preserved. Zero Google-specific fields are introduced into canonical E-7 domain entities (no `google_calendar_id`). Google Calendar identifiers remain secondary external mappings in `calendar_links`.
  - **Secret Boundary**: Zero credentials, client secrets, access tokens, refresh tokens, private keys, or production calendar IDs are documented, handled, or stored.
- **Provider Status Classification**: **`EXACT_PROVIDER_RESOLVED`** (`RESOLVED`).

### 6.3 Unresolved Decisions
1. **Decision ID `DEC-CAL-01`**: Confirm the first production calendar provider/system.
   - *Status*: **`RESOLVED`**.
   - *Resolution*: Google Calendar under Google Workspace selected as first production calendar provider with BIDIRECTIONAL integration direction via project-authorized provider selection.

### 6.4 Phase-0 Status
- **Exact First Calendar Provider Status**: **`RESOLVED`** (`EXACT_PROVIDER_RESOLVED`).
- **Interchange Standard Status**: `VERIFIED` (REST API primary; CalDAV / RFC 5545 compatibility preserved; bidirectional sync; `UTC_SAFE_TIME_HANDLING`; `nextSyncToken` lifecycle).

---

## 7. E-8 Notification / Engagement Evidence Audit

### 7.1 Separation of Responsibilities
The architectural boundary between E-7 and E-8 is an absolute system invariant established across the Complete Specification, Complete Implementation Plan, and ADR-005:

```
+-----------------------------------------------------------------------------------+
|                           E-7 OPERATIONS ENGINE                                   |
|                                                                                   |
|  - Determines OPERATIONAL CONDITIONS (goals, WBS tasks, approvals, SLA clocks)    |
|  - Decides WHEN notifications are necessary                                       |
|  - Formats NOTIFICATION COMMANDS (recipient, channel, template key, payload)      |
|  - Emits versioned events via TRANSACTIONAL OUTBOX (PostgreSQL atomic state)      |
|                                                                                   |
|  * STRICT INVARIANT: E-7 IS NOT A NOTIFICATION DELIVERY PLATFORM                  |
|  * Does NOT manage delivery gateways, SMS aggregators, SMTP, or push tokens       |
+-----------------------------------------------------------------------------------+
                                         │
                                         ▼ [Notification Commands & Domain Events]
+-----------------------------------------------------------------------------------+
|                        E-8 ENGAGEMENT ENGINE                                      |
|                                                                                   |
|  - Owns DOWNSTREAM NOTIFICATION/ENGAGEMENT DELIVERY as defined by its contract    |
|  - Dispatches across supported channels: EMAIL, SMS, WHATSAPP, IN-APP             |
|  - Manages recipient preferences, template expansion, localized rendering,        |
|    delivery retries, provider failover, and delivery status tracking              |
+-----------------------------------------------------------------------------------+
```

- **Architectural Boundary Rule**: E-8 owns downstream notification/engagement delivery behavior as defined by its contract.
- **Directly Sourced Channels**: Section 22 of the *Complete Specification* (p. 17) explicitly specifies communication across **email, SMS, WhatsApp, and in-app** channels:
  > *"E-8 Engagement: Send reminders, SLA warnings, approvals and schedule-change notifications over email/SMS/WhatsApp/in-app."*

### 7.2 Functional Contract Evidence
- **Authoritative Requirement (FR-INT-01)**:
  > *"Emit notification commands/events for E-8 or external messaging adapters."* (`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`, p. 22 / lines 542-560; `Complete Specification`, Section 9, p. 7).
- **E-8 Operational Triggers Evidence Audit**:
  To ensure strict source fidelity, this evidence inventory distinguishes three explicit categories:

  **A. Source-Verified Event Names** (*Complete Specification*, Section 21, p. 16):
  The *Complete Specification* event model explicitly cites E-8 as a consumer for exactly four literal event names:
  1. `task.blocked` → Target in source: *"Risk monitor, manager dashboard, E-8 notifications"*
  2. `workflow.task.created` → Target in source: *"Task inbox, E-8"*
  3. `workflow.sla.warning` → Target in source: *"E-8, manager dashboard"*
  4. `schedule.published` → Target in source: *"Calendar sync, E-8 notifications"*

  **B. Source-Backed Business Conditions** (*Complete Specification*, Section 22, p. 17; *Complete Implementation Plan*, Section 13.3, p. 15):
  Source narratives explicitly mandate that E-7 must trigger E-8 communications for the following operational conditions:
  - *Approval reminders* (associated with `workflow.task.created`)
  - *SLA warnings* (associated with `workflow.sla.warning`)
  - *Blocker alerts* (associated with `task.blocked`)
  - *SLA breach / escalation notifications* (*Complete Implementation Plan*, Section 13.3)
  - *Schedule-change notifications* (*Complete Specification*, Section 22)

  **C. Derived / Proposed Implementation Labels (Not Source-Verified)**:
  For business conditions that lack an explicitly cited event name targeting E-8 in source tables:
  - **Business condition**: *"SLA breach / escalation notification"*
    - Exact canonical event name: `UNRESOLVED`
    - Possible derived/proposed label: `workflow.sla.breached`
    - Classification: `DERIVED / PROPOSED — NOT SOURCE-VERIFIED`
  - **Business condition**: *"Schedule-change notification"*
    - Exact canonical event name: `UNRESOLVED`
    - Possible derived/proposed label: `schedule.changed`
    - Classification: `DERIVED / PROPOSED — NOT SOURCE-VERIFIED`

  > [!NOTE]
  > Derived event labels must not be described as canonical, verified, or authoritative in Phase 0. Task 0G.6B will establish the formal event and command contract.

- **Reliability and Outbox Storage**:
  - E-7 emits notification commands and domain events via the **E-7 transactional outbox persisted atomically with canonical state in PostgreSQL** (`ADR-005`, Section 3).
  - Outbound commands must include a deterministic `command_id` or `event_id` to guarantee idempotent processing in E-8.
  - Payloads adhere to standard CloudEvents 1.0 metadata (`id`, `source`, `type`, `time`, `datacontenttype`, `tenant_id`, `correlation_id`).

### 7.3 Transport Protocol Status
- **Current Finding**: **`E8_TRANSPORT_PROTOCOL = UNRESOLVED`**.
- The functional boundary is fully verified, but the concrete wire transport between E-7 and E-8 (e.g., dedicated RabbitMQ exchange/queue vs Kafka topic vs asynchronous HTTP webhook relay) is not yet mandated by source documents.
- Note: While RabbitMQ is an approved internal E-7 event-broker decision (`ADR-005`), that does not prove that RabbitMQ is the inter-engine wire protocol for E-8.
- The concrete wire protocol, transport binding, and mock test fixtures remain to be established in **Task 0G.6B**.

### 7.4 Phase-0 Status
- **E-8 Integration Status**: `PARTIALLY_VERIFIED` (Functional ownership, channel types, and trigger conditions verified; wire transport contract and test mock pending Task 0G.6B).

---

## 8. Integration Direction Matrix

The table below summarizes the communication flow direction for each domain based strictly on verified requirements:

| Integration Domain | Direction | Inbound Data Flows | Outbound Data Flows | Evidence Citation |
| :--- | :---: | :--- | :--- | :--- |
| **Identity / IdP** | `AUTHENTICATION_FLOW` | Signed JWT Bearer tokens; JWKS public key rotation responses; user identity assertions | Ingress token verification; JWKS key retrieval | Spec Sec. 23; Plan Sec. 14; Guardrails Sec. 3 |
| **ERP / SIS** (`mysql_iredu`) | `INBOUND` (Initial) | Read-only extraction of institutional courses, classes, sections, staff profiles, and basic room allocations | *None* in initial read-only phase | RTM FR-INT-02; Tasks 0G.1–0G.5 |
| **Calendar** | `BIDIRECTIONAL` | Resource availability intervals, blackout periods, external event callbacks | Published timetable events (RFC 5545 VEVENTs), schedule updates, cancellations | RTM FR-SC-05; Spec Sec. 9; Plan Sec. 13.2 |
| **E-8 Notification** | `OUTBOUND` | *None* (or optional delivery status callbacks if formalized in 0G.6B) | Notification commands (approvals, SLA warnings, escalations, schedule publications, disruptions) | RTM FR-INT-01; Spec Sec. 21, 22; Plan Sec. 13.3 |

---

## 9. Authority Matrix

This matrix formalizes the definitive System of Record (SoR) and authority boundaries across all integration domains:

| Entity / Concern | Authoritative System | Subordinate / Consumer Systems | Authority Governance Rule |
| :--- | :--- | :--- | :--- |
| **User Identity & Auth Assertions** | **External IdP** | E-7 `api-service`, E-7 PostgreSQL `users` | External IdP is authoritative for authentication and identity assertions presented to E-7. E-7 maps external `idp_sub`. |
| **Tenant Context Resolution** | **E-7 Security Core** (`api-service`) | All downstream E-7 workers, services, and queries | Tenant context must be resolved from verified JWT claims. Never trust unverified client payloads. |
| **Institutional Master Records** | **Upstream ERP / SIS** (`mysql_iredu`) | E-7 Adapters, Context Builder | Upstream ERP is authoritative for the upstream external/master records it owns (curricula, student cohorts, HR records). E-7 treats as read-only. |
| **Operational State (WBS, Tasks, Workflows, Schedules)** | **E-7 Core** (PostgreSQL SoR) | UI, Analytics, Upstream ERP, Downstream Engines | E-7 is the sole System of Record for generated WBS plans, workflow execution instances, and solved schedules (`ADR-006`). |
| **External ID Mappings** | **E-7 Core** (PostgreSQL mapping tables) | E-7 domain services | E-7 isolates foreign IDs (`resources.external_id`, `calendar_links.external_event_id`). Never expose external IDs as canonical IDs. |
| **Native External Calendar State** | **External Calendar Service** | E-7 `calendar-adapter` | The external calendar service is authoritative for native external calendar events and external availability state. |
| **Calendar Mapping & Sync Tokens** | **E-7 Core** (PostgreSQL `calendar_links`) | `calendar-adapter`, `scheduling-worker` | E-7 is authoritative for linking canonical schedule assignments to external calendar event IDs and sync tokens. |
| **Notification Trigger Decisions** | **E-7 Core** (Operations Engine) | E-8 Engagement Engine | E-7 is the sole authority on *when* a task, approval, SLA threshold, or schedule disruption requires notification. |
| **Downstream Notification Delivery** | **E-8 Engagement Engine** | End users, telecom/channel providers | E-8 is authoritative for notification delivery state and functions assigned to E-8 by the source contract, including channel dispatch across email, SMS, WhatsApp, and in-app. |
| **Event Emission Durability** | **E-7 Core** (PostgreSQL Outbox) | Message Broker (RabbitMQ), E-8 | The E-7 transactional outbox persisted atomically with canonical state in PostgreSQL is the authoritative event log. Broker is an ephemeral transport. |

---

## 10. Security and Credential Boundaries

All external integrations must adhere to the following non-negotiable security boundaries:

1. **Zero Secret Exposure**:
   - No production passwords, secret keys, API client secrets, database connection strings (DSNs), or access tokens may be committed to version control or documentation.
   - All external connection settings must be injected exclusively at runtime via environment variables managed by a secure secret manager.
2. **Strict Clean-Room Isolation**:
   - External data models (such as `iredu` MySQL tables or external calendar schemas) must never bleed into canonical E-7 domain entities.
   - All data exchange must pass through dedicated adapter layers that normalize foreign schemas into E-7 domain contracts.
3. **Cryptographic Tenant Isolation**:
   - `tenant_id` must be cryptographically extracted from the authenticated JWT principal or verified service identity.
   - Cross-tenant data retrieval is strictly prohibited at both the database and cache layers.
4. **Read-Only Upstream Database Boundaries**:
   - The database user account provisioned for `mysql_iredu` context extraction must have strictly `SELECT` privileges only.
   - Direct database connections must be bounded by connection pool limits and statement timeouts to prevent impacting upstream institutional operations.
5. **Zero LLM Direct Access**:
   - LLMs and AI reasoning modules must never have direct network or credential access to external databases, IdP management APIs, or calendar servers.
   - All AI interactions must receive sanitized, redacted, and bounded context constructed exclusively by the Context Builder.

---

## 11. Phase-0 Decision Gaps

The status of Phase-0 external integration decisions is summarized below:

| Decision ID | Domain | Decision Required | Status | Resolution / Impact |
| :--- | :---: | :--- | :---: | :--- |
| **DEC-IDP-01** | Identity | Confirm the organization's exact production IdP provider | **`RESOLVED`** | Google Cloud Identity / Google Workspace managed organizational identity selected via `PROJECT_AUTHORIZED_PROVIDER_SELECTION`. |
| **DEC-IDP-02** | Identity | Formalize exact JWT token claim schema for tenant_id and institutional roles | **`FOLLOW_UP_CONFIGURATION`** | E-7 RBAC / institutional-role mapping is an implementation/configuration follow-up. Standard Google OIDC claims do not provide E-7 application roles. Possible sources: internal role assignments, Google directory/group mapping, ERP/SIS mapping. Does not reopen D-EXT-01. |
| **DEC-CAL-01** | Calendar | Confirm the first production calendar provider/system | **`RESOLVED`** | Google Calendar under Google Workspace selected with BIDIRECTIONAL integration direction via `PROJECT_AUTHORIZED_PROVIDER_SELECTION`. |
| **DEC-E8-01** | E-8 | Formalize the wire transport protocol for E-7 -> E-8 dispatch | `PHASE_7` | Logical contract and mock frozen in Task 0G.6B (`e8_contract_mock = RESOLVED`); wire transport deferred to Phase 7. |
| **META-ERP-01** | ERP/SIS | Confirm commercial product name and version corresponding to upstream database `mysql_iredu` | `INFORMATIONAL_METADATA_ONLY` | Informational only; integration proceeds using logical identifier `mysql_iredu`. |

---

## 12. Production Blockers and Gates

The following two production blockers from previous integration tasks remain active and gate production deployment:

### 12.1 BLK-01: Authoritative Tenant / Organization Scope Unresolved
- **Classification**: `PRODUCTION_BLOCKER`
- **Scope**: Live MySQL `iredu` data extraction.
- **Problem**: The 247 tables in `mysql_iredu` do not contain an explicit `tenant_id` column. Ten tables include a `branch_id` column, but its relationship to institutional multi-tenancy is unverified.
- **Mitigation & Gate**:
  - Gated Operation: Direct querying of live institutional MySQL databases during production planning.
  - Mitigation: The Context Builder Contract (`docs/integrations/CONTEXT_BUILDER_CONTRACT.md`) mandates that an authoritative organization scope must be provided by the caller or derived from the authenticated principal before querying external sources.
  - Resolution Required: Business/institutional confirmation of the SQL partitioning predicate (e.g., `WHERE branch_id = :tenant_branch_id`).

### 12.2 BLK-02: Physical Academic Resource Authority Unresolved
- **Classification**: `SCHEDULING_RESOURCE_PRODUCTION_GATE`
- **Scope**: Physical room, laboratory, and facility scheduling in the Scheduling Engine.
- **Problem**: No authoritative academic physical-resource model suitable for E-7 scheduling has been established. Room-related data such as hostel room structures may exist, and scheduling/exam tables may contain free-text `room_no`, but authoritative room capacity, type, and equipment semantics remain unresolved.
- **Mitigation & Gate**:
  - Gated Operation: Automated physical room and facility allocation algorithms in OR-Tools / Scheduling Engine.
  - Mitigation: Scheduling PoCs must utilize synthetic fixtures (`tests/fixtures/`) or manual resource definitions until physical asset authority is formalized.
  - Resolution Required: Business confirmation of whether room capacities and inventories are mastered in the ERP, an external facility database, or directly authored within E-7.

---

## 13. Evidence Traceability

The table below provides complete audit traceability for every major integration conclusion:

| Domain | Finding / Conclusion | Evidence Source | Exact Location | Classification | Confidence |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **Identity** | OIDC/JWT required; validate JWKS | `IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf` | Section 14 (p. 15) | `SOURCE FACT` | `HIGH` |
| **Identity** | Config params: `JWT_ISSUER`, `JWKS_URL` | `IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf` | Section 17.2 (p. 17) | `SOURCE FACT` | `HIGH` |
| **Identity** | Tenant derived from token; never payload | `docs/IMPLEMENTATION_GUARDRAILS.md` | Section 3 | `ENGINEERING DECISION ALREADY APPROVED` | `HIGH` |
| **Identity** | Exact IdP provider is an open decision | `IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf` | Section 39 (p. 24) | `SOURCE FACT` | `HIGH` |
| **Identity** | Absence of named IdP vendor in local corpus | Local Source Corpus Scan | `docs/source-of-truth/` | `NOT FOUND IN AVAILABLE SOURCES` | `MEDIUM` |
| **ERP/SIS** | `mysql_iredu` is first concrete source | `docs/integrations/MYSQL_SCHEMA_INVENTORY.md` | Full Document | `ENGINEERING DECISION ALREADY APPROVED` | `HIGH` |
| **ERP/SIS** | Access mode is strictly `READ_ONLY` | `docs/integrations/MYSQL_ERP_ADAPTER_CONTRACT.md` | Section 4 | `ENGINEERING DECISION ALREADY APPROVED` | `HIGH` |
| **ERP/SIS** | PostgreSQL is sole operational SoR | `docs/adr/ADR-006-system-of-record.md` | Full Document | `ENGINEERING DECISION ALREADY APPROVED` | `HIGH` |
| **ERP/SIS** | Configurable external ID mapping | `docs/REQUIREMENTS_TRACEABILITY_MATRIX.md` | FR-INT-02 (p. 23) | `SOURCE FACT` | `HIGH` |
| **ERP/SIS** | BLK-01 (Tenant scoping unresolved) | `docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md` | Section 5 | `ENGINEERING DECISION ALREADY APPROVED` | `HIGH` |
| **ERP/SIS** | BLK-02 (Academic physical resource authority unresolved) | `docs/integrations/MYSQL_TASK_INTELLIGENCE_DATA_MAPPING.md` | Section 5 | `ENGINEERING DECISION ALREADY APPROVED` | `HIGH` |
| **Calendar** | iCal / CalDAV sync required (FR-SC-05) | `docs/REQUIREMENTS_TRACEABILITY_MATRIX.md` | FR-SC-05 (p. 20) | `SOURCE FACT` | `HIGH` |
| **Calendar** | `calendar_links` owns sync state | `docs/data-model/CANONICAL_OWNERSHIP_AND_SERVICE_BOUNDARIES.md` | Section 4.1 & 4.2 | `ENGINEERING DECISION ALREADY APPROVED` | `HIGH` |
| **Calendar** | Config prefix: `CALENDAR_PROVIDER_*` | `IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf` | Section 27 (p. 19) | `SOURCE FACT` | `HIGH` |
| **Calendar** | Exact first calendar is an open decision | `IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf` | Section 39 (p. 24) | `SOURCE FACT` | `HIGH` |
| **Calendar** | Absence of named calendar provider in local corpus | Local Source Corpus Scan | `docs/source-of-truth/` | `NOT FOUND IN AVAILABLE SOURCES` | `MEDIUM` |
| **E-8** | Emit notification commands (FR-INT-01) | `docs/REQUIREMENTS_TRACEABILITY_MATRIX.md` | FR-INT-01 (p. 22) | `SOURCE FACT` | `HIGH` |
| **E-8** | E-7 emits triggers; E-8 delivers | `IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf` | Section 22 (p. 17) | `SOURCE FACT` | `HIGH` |
| **E-8** | Triggers in event table: task, SLA, schedule | `IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf` | Section 21 (p. 16) | `SOURCE FACT` | `HIGH` |
| **E-8** | Outbox pattern on PostgreSQL | `docs/adr/ADR-005-event-broker.md` | Section 3 | `ENGINEERING DECISION ALREADY APPROVED` | `HIGH` |
| **E-8** | Transport wire protocol unresolved | Phase-0 Evidence Audit Analysis | Current Document | `ARCHITECTURE REQUIREMENT` | `HIGH` |

---

## 14. Phase-0 Requirement Status

As defined in Section 18 (Step 7) of the *Complete Implementation Plan*, Phase 0 requires the formal audit and baseline establishment of four core integration areas:

1. **Exact Identity Provider (IdP)**:
   - Status: **`RESOLVED`**
   - Evaluation: Google Cloud Identity / Google Workspace managed organizational identity selected as first production IdP via project-authorized provider selection (`D-EXT-01`). Google ID tokens are signed JWT identity assertions; OAuth access tokens are bearer credentials for Google APIs; `aud` is matched to approved OAuth client ID(s); `hd != tenant_id`; service API key remains frozen Phase-1 service auth baseline; provider-neutral architecture preserved; zero credentials stored.
2. **First ERP/SIS Source**:
   - Status: **`RESOLVED`**
   - Evaluation: The upstream institutional database `mysql_iredu` (MySQL database `iredu`) is established as the first concrete source and completely mapped across Phase-0 contracts (0G.1 through 0G.5). Separately, runtime adapter implementation remains `NOT_IMPLEMENTED`, and live production extraction is gated by `BLK-01` (tenant scoping) and `BLK-02` (academic physical resource authority).
3. **First Calendar Provider**:
   - Status: **`RESOLVED`**
   - Evaluation: Google Calendar under Google Workspace selected as first production calendar provider with BIDIRECTIONAL integration direction via project-authorized provider selection (`D-EXT-02`). Google Calendar REST API primary integration; CalDAV and RFC 5545 compatibility preserved; `CALENDAR_AUTHORIZATION_TOPOLOGY` to be confirmed during implementation (domain-wide delegation not mandatory); `UTC_SAFE_TIME_HANDLING` preserves timezone and all-day semantics; E-7-owned idempotency (no fabricated Google idempotency keys); `nextSyncToken` lifecycle with HTTP 410 full re-sync; HTTPS callbacks with channel-token verification; secondary external ID mapping in `calendar_links`; zero Google canonical entity fields; zero secrets stored.
4. **E-8 Notification Contract / Mock**:
   - Status: **`RESOLVED`**
   - Evaluation: The transport-neutral notification command contract and test mock were frozen in Task 0G.6B (`docs/integrations/E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md` and `.json`). Production wire transport selection is assigned to Phase 7 in the frozen roadmap (RTM `FR-INT-01`).

---

## 15. Recommendations for Task 0G.6B

Task 0G.6B is the immediate succeeding task dedicated to establishing the formal **E-8 Notification Contract and Test Mock Boundary**. 

Based on this audit, Task 0G.6B must adhere to the following recommendations:
1. **Preserve Clean Architectural Boundary**: Maintain the strict separation of concerns wherein E-7 only decides *when* an operational event requires communication and formats the structured command, while E-8 owns *how* messages are dispatched, templated, and delivered.
2. **Define CloudEvents-Compliant Command Schemas**: Standardize notification command payloads using the CloudEvents 1.0 specification, including required attributes: `id`, `source`, `type`, `time`, `datacontenttype`, `tenant_id`, `correlation_id`, `recipient_id`, `channel_hint`, `template_key`, and `parameters`.
3. **Map Operational Triggers to Contract Commands**: Formulate explicit command schemas for:
   - Verified event triggers: `task.blocked`, `workflow.task.created`, `workflow.sla.warning`, `schedule.published`.
   - Source-backed business conditions without pre-approved event names (e.g., SLA breach escalations and schedule-change notifications), establishing their formal event/command names during Task 0G.6B contract design.
4. **Design Transport-Agnostic Interface**: Formulate a python contract interface (`NotificationGatewayClient`) that can bind cleanly to either an asynchronous message broker or an HTTP webhook outbox relay.
5. **Construct In-Memory Test Mock**: Provide a deterministic in-memory mock implementation (`MockE8NotificationGateway`) with full delivery assertion capabilities for Phase-1 unit and integration testing without requiring external infrastructure.
6. **Zero Runtime Side Effects**: Task 0G.6B must remain a contract and test fixture task, avoiding runtime dependency additions or production messaging broker code.

> [!NOTE]
> Task 0G.6B was **not** started in this turn. It will be initiated only after review and approval of Task 0G.6A / 0G.6A.2.
