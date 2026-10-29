# IR INFOTECH — E-7 OPERATIONS ENGINE
## E-8 Governance Handoff Specification: Recipient Resolution and Template Ownership

**Document Path:** `docs/development-quality/E8_Governance_Handoff_Specification.md`  
**Date:** 29 October 2026  
**Repository:** `F:\operations-engine`  
**Independently Verified Remote SHA:** `2cb189b89b2cd547bfd64bc4616884b88e792a21`  
**Governing Architectural Decisions:** `DEC-E8-01`, `DEC-E8-02`, `DEC-E8-03`, `DEC-E8-04`, `DEC-E8-05`, `REC-E8-01`, `GOV-E8-01`  
**Status:** `ENGINEERING_SPECIFICATION — INSTITUTIONAL_CONFIRMATION_PENDING`  
**Canonical Blocker Association:** `BLK-03: E8_NOTIFICATION_INTEGRATION`

---

## 1. Purpose and Governance Authority

This specification defines the formal boundary, responsibility matrix, and interface contract between the **E-7 Operations Engine** and the **E-8 Notification & Engagement Subsystem**.

### 1.1 Governance Boundary Disclosure
> [!IMPORTANT]
> The architectural models and technical interfaces detailed in this specification represent the **engineering recommendations ratified under engineering delegation (`ENGINEERING_DESIGN_APPROVED`)**.  
> **Formal Institutional Confirmation Remains Outstanding.**  
> - University IT, Communications, and Registrar authorities have not yet formally confirmed downstream directory resolution ownership, channel selection policies, or message template catalogs.
> - Downstream E-8 subsystem responsibilities documented herein reflect engineering recommendations and design baselines; they are NOT formally accepted institutional responsibilities.
> - Live production delivery from E-7 to E-8 is strictly disabled (`NotificationSettings.enabled = False`).
> - This specification serves as the technical handoff document to guide upcoming institutional review and downstream E-8 subsystem development.
> - Canonical external blocker `BLK-03: E8_NOTIFICATION_INTEGRATION` remains isolated and active.

---

## 2. Canonical E-8 Architectural Decisions Classification

In accordance with [E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md](../integrations/E8_NOTIFICATION_ENGAGEMENT_CONTRACT.md), the five canonical E-8 decisions are classified as follows:

| Decision ID | Canonical Topic | Current Contract Status | Engineering Recommendation | Production Governance Status |
|---|---|---|---|---|
| **`DEC-E8-01`** | Production Wire Transport Selection | `UNRESOLVED` | Transport-neutral boundary behind `NotificationDispatcherProtocol`; RabbitMQ AMQP default for internal E-7 events; configurable HTTP POST gateway for external E-8. | `UNRESOLVED` (Transport selected at deployment time) |
| **`DEC-E8-02`** | Recipient Contact Directory Resolution | `REQUIRES_BUSINESS_CONFIRMATION` | E-8 directory services resolve opaque URNs to contact endpoints. E-7 enforces fail-closed recipient governance (`allow_provisional_recipients=False`). | `INSTITUTIONALLY_UNCONFIRMED` |
| **`DEC-E8-03`** | Delivery Feedback & Engagement Receipts | `UNRESOLVED / NOT_YET_REQUIRED` | No runtime delivery feedback loop assumed from E-8 to E-7. E-7 tracks internal outbox staging and dispatch lifecycle only. | `INSTITUTIONALLY_UNCONFIRMED` |
| **`DEC-E8-04`** | Template, Rendering, & Content Ownership | `REQUIRES_BUSINESS_CONFIRMATION` | E-8 owns template catalogs, multi-channel layouts, localization, and branding. E-7 provides typed triggers, business conditions, and structured context. | `INSTITUTIONALLY_UNCONFIRMED` |
| **`DEC-E8-05`** | Delivery Channel Selection Policy | `REQUIRES_BUSINESS_CONFIRMATION` | E-8 supported channels are known (Email, SMS, WhatsApp, In-App). Policy ownership for selecting optimal channels per recipient remains unconfirmed. | `INSTITUTIONALLY_UNCONFIRMED` |

---

## 3. Decision DEC-E8-02: Recipient Contact Directory Resolution Boundaries

### 3.1 Boundary Principles
1. **Opaque Identifier Formulation:** E-7 formulates recipient targets exclusively as canonical opaque Uniform Resource Names (URNs).
   - Valid formats:
     - Cohort reference: `urn:e7:cohort:{faculty_or_dept}:{cohort_code}` (e.g. `urn:e7:cohort:eng:cs-2026`)
     - Role reference: `urn:e7:role:{tenant_id}:{role_name}` (e.g. `urn:e7:role:dept-head`, `urn:e7:role:invigilator`)
     - User reference: `urn:e7:user:{uuid}` (opaque internal UUID)
2. **Directory Resolution Recommendation (E-8):** Under the engineering design baseline, downstream E-8 directory services own resolution of opaque URNs to physical contact endpoints:
   - Email addresses (RFC 5322)
   - Mobile telephone numbers (E.164 format for SMS / WhatsApp)
   - Mobile application device tokens (FCM / APNs)
   - Webhook callback URLs
3. **Data Minimization & Zero Raw PII Storage in E-7:**
   - In alignment with data minimization principles, raw personal email addresses, phone numbers, student names, and device push tokens are strictly prohibited from being persisted in E-7 databases, tables, outbox records, or log payloads.
   - Any attempt to stage an outbox command containing raw contact details (e.g. `@` or `+` characters in recipient identifiers) is rejected by input validation (`validate_recipients`).
   - Note: Formal FERPA and GDPR compliance guarantees require institutional legal and IT sign-off and cannot be established solely by application-level data minimization checks.

### 3.2 Fail-Closed Recipient Governance
- In `NotificationSettings`, recipient governance is fail-closed by default:
  ```python
  allow_provisional_recipients: bool = Field(
      default=False,
      description="Fail-closed guard under DEC-E8-02. Rejects provisional cohort dispatch.",
  )
  ```
- **Staging Enforcement:** When an event consumer processes an operational event targeting provisional, unverified, or ambiguous cohorts, E-7 marks the command `BLOCKED` in `notification_outbox_commands` with reason `BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02`.
- **Administrative Recovery (`REC-E8-01`):** Blocked commands cannot be dispatched automatically. Release requires administrative review via `NotificationRecoveryService` with authorized caller principal roles (`admin`, `operations_manager`). Note that production identity verification (OIDC / enterprise gateway tokens) and a mounted HTTP recovery endpoint remain unverified / deferred pending enterprise gateway infrastructure (`BLK-04`).

---

## 4. Decision DEC-E8-04: Message Template, Rendering, and Content Ownership

### 4.1 Division of Responsibilities (Engineering Recommendation)

```
┌───────────────────────────────────────┐      ┌─────────────────────────────────────────┐
│        E-7 Operations Engine          │      │     E-8 Notification Subsystem          │
├───────────────────────────────────────┤      ├─────────────────────────────────────────┤
│ • Evaluates operational workflows     │      │ • Owns message template inventory       │
│ • Detects state transitions (SLA,     │      │ • Performs multi-channel rendering      │
│   Schedule, Exam, Task breaches)      │      │   (HTML, Plaintext, SMS, Push)          │
│ • Formulates typed TriggerKind &      │─────▶│ • Applies university branding & layout  │
│   business_condition (SCHEDULE_CHANGE)│      │ • Enforces quiet-hours & user opt-outs  │
│ • Emits structured notification_context│     │ • Selects transport channel per user    │
│ • Preserves complete provenance       │      │ • Appends legal disclaimers & opt-outs  │
└───────────────────────────────────────┘      └─────────────────────────────────────────┘
```

### 4.2 Canonical Wire Payload Contract (`NotificationCommand`)
The payload emitted by E-7 adheres strictly to the Pydantic model `NotificationCommand` in [app/integrations/engagement/models.py](../../app/integrations/engagement/models.py) and the JSON contract [E8_NOTIFICATION_ENGAGEMENT_CONTRACT.json](../integrations/E8_NOTIFICATION_ENGAGEMENT_CONTRACT.json).

Below is the verified illustrative payload corresponding to a schedule revision notification (`SCHEDULE_CHANGE_NOTICE`):

```json
{
  "contract_version": "1.0.0",
  "command_id": "notif-cmd-a8f10e42-1234-4567-89ab-cdef01234567",
  "correlation_reference": "workflow-inst-exam-plan-2026",
  "causation_reference": "event-sched-revision-42",
  "organization_scope_reference": "tenant-university-main",
  "trigger": {
    "trigger_kind": "SOURCE_BACKED_BUSINESS_CONDITION",
    "business_condition": "SCHEDULE_CHANGE_NOTICE",
    "source_entity_reference": "sched-2026-fall-v2"
  },
  "recipient_references": [
    "urn:e7:cohort:eng:cs-2026",
    "urn:e7:role:invigilator"
  ],
  "notification_context": {
    "template_id": "EXAM_SCHEDULE_CHANGED",
    "schedule_id": "sched-2026-fall-v2",
    "old_version": "v1.0",
    "new_version": "v2.0",
    "repair_reason": "Room conflict resolution",
    "effective_date": "2026-11-16T14:00:00Z"
  },
  "requested_at": "2026-10-29T01:30:00Z"
}
```

### 4.3 Contract Field Specifications & Validation Rules
- **`contract_version`**: Must be `"1.0.0"`.
- **`command_id`**: Non-empty unique identifier string for command instance deduplication.
- **`correlation_reference`**: Non-empty opaque identifier linking the command to the top-level workflow or operational plan.
- **`causation_reference`**: Opaque identifier of the specific causal event or state transition (optional).
- **`organization_scope_reference`**: Non-empty tenant identifier (`tenant_id`).
- **`trigger`**: `NotificationTrigger` object:
  - `trigger_kind`: Must be `TriggerKind.SOURCE_BACKED_BUSINESS_CONDITION` or `TriggerKind.SOURCE_VERIFIED_EVENT`.
  - `business_condition`: Required when `trigger_kind == SOURCE_BACKED_BUSINESS_CONDITION` (e.g. `SCHEDULE_CHANGE_NOTICE`, `APPROVAL_REMINDER`, `SLA_WARNING`, `BLOCKER_ALERT`, `SLA_BREACH_ESCALATION`).
  - `source_event_name`: Required when `trigger_kind == SOURCE_VERIFIED_EVENT` (must be one of: `task.blocked`, `workflow.task.created`, `workflow.sla.warning`, `schedule.published`).
  - `source_entity_reference`: Opaque reference to the affected entity (e.g. schedule ID, task ID).
  - Model forbids extra fields (`extra="forbid"`).
- **`recipient_references`**: Array of non-empty opaque string references (`urn:e7:...`). Rejects emails, phone numbers, raw MySQL IDs, or PII.
- **`notification_context`**: Minimal structured operational context. When `template_id` is specified (e.g. `EXAM_SCHEDULE_CHANGED`), keys are strictly validated against `ALLOWED_TEMPLATE_CONTEXT_FIELDS`. Prohibits credentials, raw SQL state, salaries, and sensitive data.
- **`requested_at`**: Normalized UTC ISO-8601 timestamp ending in `Z`.

---

## 5. Operational Invariants and Delivery Semantics

| Invariant ID | Invariant Rule | Implementation Safeguard |
|---|---|---|
| **`INV-GOV-01`** | Production delivery disabled by default | `NotificationSettings.enabled = False`. Delivery worker returns 0 and claims no rows. |
| **`INV-GOV-02`** | Zero PII persistence in E-7 | Strict rejection of non-URN recipient identifiers during staging and recovery. |
| **`INV-GOV-03`** | Fail-closed provisional recipients | `allow_provisional_recipients = False`. Commands staged as `BLOCKED`. |
| **`INV-GOV-04`** | Isolated external network I/O | All HTTP and AMQP dispatches occur outside database transactions with zero held connections. |
| **`INV-LE-01`** | Lease expiration invalidation | Completion and failure methods verify `lease_owner` and active lease (`lease_expires_at > now`), returning `False` upon expiration. |
| **`INV-DEL-01`** | At-least-once external delivery | E-7 provides at-least-once delivery with exponential retry backoff. End-to-end exactly-once delivery cannot be guaranteed without downstream E-8 deduplication verification. |

---

## 6. Handoff Checklist for Downstream Teams (E-8 & Institutional IT)

Prior to resolving blocker `BLK-03` and setting `E7_NOTIFICATION_ENABLED=True`:

- [ ] **Institutional Directory Confirmation (`DEC-E8-02`):**
  - [ ] University IT confirms LDAP/Active Directory resolution schema for `urn:e7:cohort:*` and `urn:e7:role:*`.
  - [ ] Directory resolution caching and fallback contracts documented in E-8.
  - [ ] Institutional privacy officer reviews directory resolution flows.
- [ ] **Institutional Template Confirmation (`DEC-E8-04`):**
  - [ ] University Communications reviews and approves message templates for each `business_condition`.
  - [ ] HTML email styling, SMS character truncation rules, and branding approved.
  - [ ] Multi-channel delivery rules (Email vs SMS vs Push) verified against user profile settings (`DEC-E8-05`).
- [ ] **Integration Gateway & Security Verification (`DEC-E8-01`, `BLK-04`):**
  - [ ] E-8 API endpoint URLs, mTLS certificates, and Bearer token secrets provisioned in staging.
  - [ ] Reverse proxy and firewall rules established between E-7 worker pods and E-8 ingress.
  - [ ] Disaster recovery, dead-letter queueing, and retry alerting verified.

---

## 7. Document Sign-Off Status

| Stakeholder / Role | Status | Notes |
|---|---|---|
| E-7 Engineering Architecture | **APPROVED** | Engineering design ratified; verified with 16 PostgreSQL integration tests. |
| E-7 Quality Engineering | **ENGINEERING_TEST_EVIDENCE** | 16/16 PostgreSQL integration tests and 2,348 test collection green in CI. |
| Architecture Review Board (ARB) | **FORMAL_ARB_APPROVAL_PENDING** | Scheduled for next formal convene ([ADR-012-AMENDMENT-PROPOSED.md](../adr/ADR-012-AMENDMENT-PROPOSED.md)). |
| Institutional IT / Communications | **INSTITUTIONAL_CONFIRMATION_PENDING** | Formal technical handoff initiated via this specification. |
| Production Release Operations | **PRODUCTION_ACTIVATION_BLOCKED** | Gated by canonical external blocker `BLK-03`. |
