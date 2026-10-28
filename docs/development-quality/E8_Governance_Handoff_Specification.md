# IR INFOTECH — E-7 OPERATIONS ENGINE
## E-8 Governance Handoff Specification: Recipient Resolution and Template Ownership

**Document Path:** `docs/development-quality/E8_Governance_Handoff_Specification.md`  
**Date:** 29 October 2026  
**Repository:** `F:\operations-engine`  
**Engineering Baseline SHA:** `f01b8d76648e998d72ef58aea284957065b573f0`  
**Governing Architectural Decisions:** `DEC-E8-02`, `DEC-E8-04`, `REC-E8-01`, `GOV-E8-01`  
**Status:** `ENGINEERING_SPECIFICATION — INSTITUTIONAL_CONFIRMATION_PENDING`  
**Canonical Blocker Association:** `BLK-03: E8_NOTIFICATION_INTEGRATION`

---

## 1. Purpose and Governance Authority

This specification defines the formal boundary, responsibility matrix, and interface contract between the **E-7 Operations Engine** and the **E-8 Notification & Engagement Subsystem**.

### 1.1 Governance Boundary Disclosure
> [!IMPORTANT]
> The architectural models and technical interfaces detailed in this specification are ratified under engineering delegation (`ENGINEERING_DESIGN_APPROVED`).  
> **Formal Institutional Confirmation Remains Outstanding.**  
> - University IT, Communications, and Registrar authorities have not yet formally confirmed the downstream directory resolution schema or template inventory.
> - Live production delivery from E-7 to E-8 is strictly disabled (`NotificationSettings.enabled = False`).
> - This specification serves as the formal technical handoff document to guide upcoming institutional review and downstream E-8 subsystem development.
> - Canonical external blocker `BLK-03: E8_NOTIFICATION_INTEGRATION` remains isolated and active.

---

## 2. Decision DEC-E8-02: Recipient Contact Directory Resolution Boundaries

### 2.1 Boundary Principles
1. **Opaque Identifier Formulation:** E-7 formulates recipient targets exclusively as canonical opaque Uniform Resource Names (URNs).
   - Valid formats:
     - Cohort reference: `urn:e7:cohort:{faculty_or_dept}:{cohort_code}` (e.g. `urn:e7:cohort:eng:cs-2026`)
     - Role reference: `urn:e7:role:{tenant_id}:{role_name}` (e.g. `urn:e7:role:dept-head`, `urn:e7:role:invigilator`)
     - User reference: `urn:e7:user:{uuid}` (opaque internal UUID)
2. **Directory Resolution Ownership in E-8:** Downstream E-8 directory services own all resolution of opaque URNs to physical contact endpoints:
   - Email addresses (RFC 5322)
   - Mobile telephone numbers (E.164 format for SMS / WhatsApp)
   - Mobile application device tokens (FCM / APNs)
   - Webhook callback URLs
3. **Zero Raw PII Storage in E-7:**
   - Raw personal email addresses, phone numbers, student names, and device push tokens are strictly prohibited from being persisted in E-7 databases, tables, outbox records, or log payloads.
   - Any attempt to stage an outbox command containing raw contact details (e.g. `@` or `+` characters in recipient identifiers) is rejected by input validation.

### 2.2 Fail-Closed Recipient Governance
- In `NotificationSettings`, recipient governance is fail-closed by default:
  ```python
  allow_provisional_recipients: bool = Field(
      default=False,
      description="Fail-closed guard under DEC-E8-02. Rejects provisional cohort dispatch.",
  )
  ```
- **Staging Enforcement:** When an event consumer processes an operational event targeting provisional, unverified, or ambiguous cohorts, E-7 marks the command `BLOCKED` in `notification_outbox_commands` with reason `BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02`.
- **Administrative Recovery (`REC-E8-01`):** Blocked commands cannot be dispatched automatically. Release requires authenticated administrative review via `NotificationRecoveryService` with authorized credentials and tenant verification.

---

## 3. Decision DEC-E8-04: Message Template, Rendering, and Content Ownership

### 3.1 Division of Responsibilities

```
┌───────────────────────────────────────┐      ┌─────────────────────────────────────────┐
│        E-7 Operations Engine          │      │     E-8 Notification Subsystem          │
├───────────────────────────────────────┤      ├─────────────────────────────────────────┤
│ • Evaluates operational workflows     │      │ • Owns message template inventory       │
│ • Detects state transitions (SLA,     │      │ • Performs multi-channel rendering      │
│   Schedule, Exam, Task breaches)      │      │   (HTML, Plaintext, SMS, Push)          │
│ • Formulates typed TriggerKind &      │─────▶│ • Applies university branding & layout  │
│   BusinessCondition (e.g. EXAM_CHANGE)│      │ • Enforces quiet-hours & user opt-outs  │
│ • Emits structured context variables  │      │ • Selects transport channel per user    │
│ • Preserves complete provenance       │      │ • Appends legal disclaimers & opt-outs  │
└───────────────────────────────────────┘      └─────────────────────────────────────────┘
```

### 3.2 Wire Payload Contract (`NotificationCommand`)
E-7 dispatches commands containing structured provenance without pre-rendered text:

```json
{
  "command_id": "notif-cmd-a8f10e42-1234-4567-89ab-cdef01234567",
  "tenant_id": "tenant-university-main",
  "trigger": {
    "trigger_kind": "SCHEDULE_PUBLISHED",
    "business_condition": "EXAM_SCHEDULE_CHANGED",
    "entity_type": "exam_schedule",
    "entity_id": "sched-2026-fall-v2",
    "severity": "WARNING",
    "context": {
      "schedule_title": "Fall 2026 Computer Science Final Examinations",
      "course_code": "CS-401",
      "previous_slot": "2026-11-15T09:00:00Z",
      "revised_slot": "2026-11-16T14:00:00Z",
      "room_number": "Auditorium-B",
      "revision_number": 3
    }
  },
  "recipients": [
    "urn:e7:cohort:eng:cs-2026",
    "urn:e7:role:invigilator"
  ],
  "dispatched_at": "2026-10-29T01:30:00Z"
}
```

### 3.3 E-8 Rendering Obligations
- **Template Mapping:** E-8 maps `(trigger_kind, business_condition)` to approved institutional template keys.
- **Localization:** E-8 determines the recipient's preferred language and expands context parameters accordingly.
- **Channel Optimization:** E-8 formats distinct layouts for HTML email, short SMS (160 characters), and push notifications using the same structured context.
- **Legal Compliance:** E-8 handles unsubscribe preferences, bounce handling, and regulatory opt-outs independently of E-7.

---

## 4. Operational Invariants and Safeguards

| Invariant ID | Invariant Rule | Implementation Safeguard |
|---|---|---|
| **INV-GOV-01** | Production delivery disabled by default | `NotificationSettings.enabled = False`. Delivery worker returns 0 and claims no rows. |
| **INV-GOV-02** | Zero PII persistence in E-7 | Strict rejection of non-URN recipient identifiers during staging and recovery. |
| **INV-GOV-03** | Fail-closed provisional recipients | `allow_provisional_recipients = False`. Commands staged as `BLOCKED`. |
| **INV-GOV-04** | Isolated external network I/O | All HTTP and AMQP dispatches occur outside database transactions with zero held connections. |
| **INV-LE-01** | Lease expiration invalidation | Outbox completion queries require active leases (`lease_expires_at > now`). |

---

## 5. Handoff Checklist for Downstream Teams (E-8 & Institutional IT)

Prior to resolving blocker `BLK-03` and setting `E7_NOTIFICATION_ENABLED=True`:

- [ ] **Institutional Directory Confirmation (DEC-E8-02):**
  - [ ] University IT confirms LDAP/Active Directory resolution schema for `urn:e7:cohort:*` and `urn:e7:role:*`.
  - [ ] Directory resolution caching and fallback contracts documented in E-8.
  - [ ] FERPA and student privacy compliance sign-off obtained from Registrar.
- [ ] **Institutional Template Confirmation (DEC-E8-04):**
  - [ ] University Communications reviews and approves message templates for each `business_condition`.
  - [ ] HTML email styling, SMS character truncation rules, and branding approved.
  - [ ] Multi-channel delivery rules (Email vs SMS vs Push) verified against user profile settings.
- [ ] **Integration Gateway & Security Verification:**
  - [ ] E-8 API endpoint URLs, mTLS certificates, and Bearer token secrets provisioned in staging.
  - [ ] Reverse proxy and firewall rules established between E-7 worker pods and E-8 ingress.
  - [ ] Disaster recovery, dead-letter queueing, and retry alerting verified.

---

## 6. Document Sign-Off Status

| Stakeholder / Role | Status | Notes |
|---|---|---|
| E-7 Engineering Architecture | **APPROVED** | Verified with 16 passing PostgreSQL integration tests. |
| E-7 Quality Assurance | **APPROVED** | Full regression and contract tests green in CI. |
| Architecture Review Board (ARB) | **PENDING** | Scheduled for next formal convene (`ADR-012-AMENDMENT-PROPOSED.md`). |
| Institutional IT / Communications | **PENDING** | Formal handoff initiated via this specification. |
| Production Release Operations | **BLOCKED** | Gated by canonical blocker `BLK-03`. |
