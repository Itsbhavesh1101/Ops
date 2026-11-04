# E-7 Operations Engine — Final External Dependencies & Blockers Register (Stage 8)

**Document:** Authoritative External Dependencies, Blockers & Production Integration Gate Register  
**System:** IR Infotech E-7 Operations Engine  
**Project Phase:** Stage 8 (Final Production Governance & Release Readiness)  
**Accepted Baseline Commit:** `a55d9ccad5e545ebd39b3cd3468f94f06b9f55fa`  
**Frozen Phase-8 Release Candidate:** `03e6305256db3d838b472aaac50893ddb513414e`  
**Overall Readiness Verdict:** `STAGE_8_INTERNAL_HANDOVER_ACCEPTED_BLOCKED_EXTERNAL`  
**Security Classification:** Non-Confidential / Technical Architecture

---

## 1. Master External Blockers Register

Full enterprise production deployment remains strictly gated on four external infrastructure prerequisites requiring coordination across enterprise IT, database administration, platform, and cloud SRE teams:

| Blocker Key | Integration Domain | Current Canonical Status | Responsible External Team | Impact on Production |
|:---|:---|:---|:---|:---|
| **`MYSQL_LIVE_READ_VERIFICATION`** | Institutional ERP Database | `BLOCKED_ON_COMPANY_READ_ONLY_ACCESS` | Institutional DBA Team | Gates live ERP student enrollment counts and teacher availability sync. |
| **`PRODUCTION_COMPANY_OIDC`** | Enterprise Single Sign-On | `BLOCKED_EXTERNAL` | Corporate IT / Identity Team | Gates production employee login via Google Workspace OIDC. |
| **`E8_NOTIFICATION_INTEGRATION`** | Messaging & Notifications | `BLOCKED_EXTERNAL` | E-8 Platform Team | Gates live operational notification dispatch to SMS/Email brokers. |
| **`PRODUCTION_GATEWAY`** | Edge Ingress, DNS & WAF | `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE` | Cloud Infrastructure / SRE Team | Gates public traffic ingress, TLS 1.3 termination, and DDoS/WAF protection. |

---

## 1.1 Current Executable Evidence (2026-11-04)

The repository now provides one non-secret, bounded probe for each blocker. The latest full verification report records all four as `BLOCKED_PREREQUISITE` because no production endpoints or credentials were supplied; this is an external dependency state, not an internal product-test failure.

| Blocker | Probe | Current evidence | Required operator settings |
|:---|:---|:---|:---|
| `MYSQL_LIVE_READ_VERIFICATION` | `python -m scripts.verification.verify_mysql_connection --output dist/verification/external/mysql.json` | `BLOCKED_PREREQUISITE` | `E7_MYSQL_HOST`, `E7_MYSQL_USER`, `E7_MYSQL_PASSWORD` |
| `PRODUCTION_COMPANY_OIDC` | `python -m scripts.verification.verify_oidc_provider --output dist/verification/external/oidc.json` | `BLOCKED_PREREQUISITE` | `E7_OIDC_ISSUER` |
| `E8_NOTIFICATION_INTEGRATION` | `python -m scripts.verification.verify_e8_broker --output dist/verification/external/e8.json` | `BLOCKED_PREREQUISITE` | `E7_E8_AMQP_URL`, `E7_E8_IDEMPOTENCY_HEADER=Idempotency-Key` |
| `PRODUCTION_GATEWAY` | `python -m scripts.verification.verify_gateway_ingress --output dist/verification/external/gateway.json` | `BLOCKED_PREREQUISITE` | `E7_PRODUCTION_GATEWAY_URL` |

Operators must inject secrets through the approved secret manager or process environment. Credentials and token-bearing URLs must never be placed in shell history, committed `.env` files, probe output, or review comments. After provisioning, run `python scripts/verification/run_verification.py --group external --output dist/verification/external-verification.json`; all four gates must be `PASS`, and the redacted evidence package must be reviewed and signed by the owning teams.

---

## 2. Deep-Dive: The Four Canonical External Blockers

| Boundary key | Responsible owning team | Mandatory deliverable scope |
|:---|:---|:---|
| `MYSQL_LIVE_READ_VERIFICATION` | Institutional DBA Team | Dedicated `e7_ro_sync` account with `SELECT` on exactly 11 tables |
| `PRODUCTION_COMPANY_OIDC` | Corporate IT / IAM Team | Corporate client registration and hosted-domain policy |
| `E8_NOTIFICATION_INTEGRATION` | E-8 Platform Team | AMQPS credentials, durable topic exchange, routing, and idempotency contract |
| `PRODUCTION_GATEWAY` | Cloud Infrastructure / SRE | Layer-7 ingress, TLS 1.3, DNS, security headers, WAF, and rate limits |

---

### 2.1 Blocker 1: Institutional MySQL Read-Only Integration (`MYSQL_LIVE_READ_VERIFICATION`)
* **Canonical Key:** `MYSQL_LIVE_READ_VERIFICATION`
* **Current Status:** `BLOCKED_ON_COMPANY_READ_ONLY_ACCESS`
* **Owning Team:** Institutional Database Administration (DBA) Team
* **Governance Mandate:** Original Specification `FR-INT-02` (Read-only Institutional MySQL ERP synchronization).

#### Exact Handover Scope & Table Grants
The E-7 Operations Engine requires a dedicated read-only service account (illustrative name: `e7_ro_sync`). In accordance with least privilege:
1. **Strictly 11 Approved Active Tables:**
   ```sql
   -- Granted on specific internal VPC subnet:
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
2. **Four Excluded Conditional Support Tables:**
   `subject_groups`, `subject_group_subjects`, `roles`, and `staff_roles` are **excluded** from initial provisioning.
3. **Explicit Denial of Write & Administrative Grants:**
   No `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `GRANT`, `SUPER`, or `FILE` privileges.
4. **Data Privacy Guardrails:**
   - **Student PII Minimization:** Zero student personal data (names, emails, phone numbers, home addresses) is stored in E-7. Enrollment headcounts are queried strictly as anonymous aggregates (`COUNT(ss.id)`).
   - **Staff Data Minimization:** Staff synchronization projects strictly operational metadata (`id`, `name`, `surname`, `department`, `designation`). Salary, banking, and tax data are excluded at the query projection layer.
5. **Verification Protocol:**
   Once DBA provisions credentials through the approved secret manager, set `E7_MYSQL_HOST`, `E7_MYSQL_USER`, `E7_MYSQL_PASSWORD`, and optional `E7_MYSQL_DATABASE`, then run the non-destructive verification probe:
   ```bash
   python -m scripts.verification.verify_mysql_connection --output dist/verification/external/mysql.json
   ```

---

### 2.2 Blocker 2: Corporate Google Workspace OIDC (`PRODUCTION_COMPANY_OIDC`)
* **Canonical Key:** `PRODUCTION_COMPANY_OIDC`
* **Current Status:** `BLOCKED_EXTERNAL`
* **Owning Team:** Corporate IT / Identity & Access Management (IAM) Team
* **Governance Mandate:** Original Specification Authentication Requirements (Enterprise Single Sign-On).

#### Exact Handover Scope & OAuth Setup
The E-7 Operations Engine implements OpenID Connect (OIDC) Core 1.0 with RS256 signature verification:
1. **Google Cloud Console OAuth 2.0 Web Client:**
   - Creation of dedicated OAuth Web Application credential.
   - Authorized Javascript Origins: `https://operations.corp.irinfotech.internal`
   - Authorized Redirect URIs: `https://operations.corp.irinfotech.internal/auth/callback`
2. **Hosted Domain Claim Enforcement:**
   - Enforce `hd == "irinfotech.com"` to restrict login to corporate accounts.
3. **Subject-First Principal Mapping:**
   - Internal RBAC principals bind to the immutable Google `sub` claim, preventing identity hijack upon corporate email recycling.
4. **IAM Security Architecture Review:**
   - The custom Google ID-token implicit authentication flow has **not** been silently approved for production. Live Google Workspace authentication requires formal Corporate IAM security review, risk assessment, and provisioning of the enterprise OAuth Web Client ID and hosted domain restrictions prior to enterprise routing.
5. **Verification Protocol:**
   Once the corporate issuer is provisioned, supply `E7_OIDC_ISSUER` and execute the discovery, issuer, HTTPS endpoint, and JWKS validation probe. Client registration and hosted-domain login remain separate IAM/UAT evidence:
   ```bash
   python -m scripts.verification.verify_oidc_provider --output dist/verification/external/oidc.json
   ```

---

### 2.3 Blocker 3: E-8 Notification Integration (`E8_NOTIFICATION_INTEGRATION`)
* **Canonical Key:** `E8_NOTIFICATION_INTEGRATION`
* **Current Status:** `BLOCKED_EXTERNAL`
* **Owning Team:** E-8 Notification Platform Team
* **Governance Mandate:** Original Specification `FR-INT-01` & ADR-012 (Reliable notification outbox relay).

#### Exact Handover Scope & Messaging Contract
The E-7 Operations Engine formulates and buffers outbound alerts in PostgreSQL transactional outbox tables:
1. **AMQP Broker Connectivity:**
   - Dedicated AMQP broker DSN (`amqps://...`), virtual host (`/e7`), and transport credentials.
2. **Exchange & Queue Architecture:**
   - Topic exchange `e8.notifications`.
   - Queues bound to routing keys: `notification.plan.approved`, `notification.schedule.published`, `notification.workflow.sla_warning`, `notification.workflow.sla_breached`.
3. **Payload Contract (`NotificationCommand` v1.0.0):**
   - Canonical JSON structure adhering strictly to `app/integrations/engagement/models.py` and `docs/integrations/E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md`:
     - `contract_version` ("1.0.0")
     - `command_id` (canonical unique command identifier; consumer idempotency key)
     - `correlation_reference` (opaque business transaction / schedule reference)
     - `causation_reference` (optional immediate triggering domain event reference)
     - `organization_scope_reference` (tenant ID)
     - `trigger` (provenance metadata: `trigger_kind`, `source_event_name` / `business_condition`, `source_entity_reference`)
     - `recipient_references` (canonical opaque references, e.g. `urn:e7:cohort:faculty`; zero raw phone/email PII)
     - `notification_context` (strictly bounded template parameters, e.g. `schedule_id`, `version`, `effective_date`; zero student PII or employee compensation)
     - `requested_at` (UTC ISO-8601 timestamp)
   - Zero student PII, zero raw upstream database primary keys (`mysql_iredu`), zero raw contact credentials.
4. **At-Least-Once Delivery & Consumer Idempotency:**
   - Outbox dispatcher ensures at-least-once delivery. E-8 notification consumers must enforce idempotency using the canonical `command_id` (with `correlation_reference` and `causation_reference` provided for end-to-end tracing and causal provenance). `notification_id` and `correlation_id` do not exist in the canonical contract.
5. **Verification Protocol:**
   Supply the secret-bearing `E7_E8_AMQP_URL` and set `E7_E8_IDEMPOTENCY_HEADER=Idempotency-Key`, then execute the passive topology probe:
   ```bash
   python -m scripts.verification.verify_e8_broker --output dist/verification/external/e8.json
   ```

---

### 2.4 Blocker 4: Production Gateway & Edge Security (`PRODUCTION_GATEWAY`)
* **Canonical Key:** `PRODUCTION_GATEWAY`
* **Current Status:** `BLOCKED_ON_PRODUCTION_INFRASTRUCTURE`
* **Owning Team:** Cloud Infrastructure / SRE Team
* **Governance Mandate:** Production Infrastructure Scaffolding & Edge Protection.

#### Exact Handover Scope & Ingress Rules
The E-7 application requires enterprise Layer 7 reverse proxy ingress:
1. **DNS Routing:**
   - Internal DNS: `operations.corp.irinfotech.internal` pointing to Gateway VIP.
2. **TLS 1.3 Termination:**
   - Institutional CA certificate termination with strict cipher suite configuration (`TLS_AES_256_GCM_SHA384`, `TLS_CHACHA20_POLY1305_SHA256`).
3. **OWASP WAF Inspection:**
   - ModSecurity / AWS WAF / Cloudflare rules inspecting request bodies for SQL injection, cross-site scripting (XSS), and malicious headers.
4. **Rate Limiting & DDoS Protection:**
   - Edge rate limit: 100 requests/second per IP with burst allowance of 200.
5. **Reverse Proxy Headers:**
   - Must forward `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Correlation-ID`.
6. **Private VPC Peering:**
   - Secure private subnet routing to FastAPI backend (port 8000). Direct public internet access to FastAPI or PostgreSQL is strictly prohibited.
7. **Verification Protocol:**
   Supply `E7_PRODUCTION_GATEWAY_URL` and execute the bounded read-only edge security probe:
   ```bash
   python -m scripts.verification.verify_gateway_ingress --output dist/verification/external/gateway.json
   ```

---

## 3. Dependency Resolution & Verification Workflow

```
   ┌─────────────────────────────────────────────────────────────┐
   │         EXTERNAL TEAMS PROVISION PRODUCTION RESOURCES       │
   ├─────────────────────────────────────────────────────────────┤
   │ 1. DBA creates e7_ro_sync with SELECT on 11 active tables   │
   │ 2. IT provisions Google Workspace OAuth Web Client ID       │
   │ 3. E-8 Platform provisions AMQP DSN & notification queues   │
   │ 4. SRE provisions Layer 7 Gateway, TLS 1.3, DNS, WAF rules  │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │        OPERATIONAL VERIFICATION PROBES EXECUTED             │
   ├─────────────────────────────────────────────────────────────┤
   │ - Live MySQL SELECT-only verification probe                 │
   │ - Live Google OIDC token validation probe                   │
   │ - Live RabbitMQ outbox end-to-end publish drill             │
   │ - Edge Ingress TLS handshake & WAF injection probe          │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │      INSTITUTIONAL STAKEHOLDER FORM SIGNED & DATED          │
   ├─────────────────────────────────────────────────────────────┤
   │ - Operational Owner, IT, DBA, E-8, SRE, Release Authority   │
   └──────────────────────────────┬──────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │         PRODUCTION RELEASE AUTHORIZATION GRANTED            │
   │               (PRODUCTION_RELEASE_AUTHORIZED = TRUE)        │
   └─────────────────────────────────────────────────────────────┘
```

---

## 4. Final External Blockers Disposition

```
FINAL_VERDICT = STAGE_8_INTERNAL_HANDOVER_ACCEPTED_BLOCKED_EXTERNAL
RELEASE_AUTHORIZED = FALSE
```

*All four external integration boundaries remain gated on corporate infrastructure provisioning. No production deployment may proceed until all four external dependencies are resolved, verified by automated probes, and certified by authorized institutional stakeholders.*
