# E-7 ↔ E-8 Notification / Engagement Contract

## 1. Purpose

This document establishes the provider-neutral, transport-neutral integration contract between:
- **E-7 Operations Engine**: Authoritative system for operational condition detection, workflow task execution, critical-path schedule management, and notification command formulation.
- **E-8 Engagement Engine**: Authoritative downstream system for multi-channel communication execution across email, SMS, WhatsApp, and in-app channels as defined by its contract.

This specification serves as the frozen Phase-0 integration design baseline, satisfying **Phase-0 Requirement 7** from the Master Implementation Roadmap:
> *"Define exact identity provider, first ERP/SIS adapter, first calendar provider and E-8 notification contract or mock."*

This contract defines:
- Strict architectural boundaries between E-7 and E-8 without overclaiming source facts.
- The canonical `NotificationCommand` structure and field specifications.
- Provenance rules distinguishing literal source-verified event names from source-backed operational conditions.
- Idempotency and correlation tracking semantics.
- Contract validation failure taxonomy.
- Machine-readable synthetic mock definitions for contract compliance testing.

---

## 2. Source Basis

This contract is grounded directly in the primary architecture and specification baseline of the IR Infotech ecosystem:
1. **Requirements Traceability Matrix (`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`)**:
   - `FR-INT-01` (p. 22 / lines 542–560): *"Emit notification commands/events for E-8 or external messaging adapters."*
2. **Implementation Specification (`IR_Infotech_E7_Operations_Engine_Complete_Specification.pdf`)**:
   - Section 9 (p. 7): Core architectural boundaries and external messaging adapter emissions.
   - Section 21 (p. 16): Canonical Event Model and Typical Consumers table.
   - Section 22 (p. 17): E-8 Engagement multi-channel communication (email, SMS, WhatsApp, in-app).
3. **Implementation Plan (`IR_Infotech_E7_Operations_Engine_Complete_Implementation_Plan.pdf`)**:
   - Section 13.3 (p. 15): Asynchronous notification dispatch and escalation alerts.
4. **Architecture Decision Records**:
   - `ADR-005` (Event Broker & Asynchronous Processing): Establishes the Transactional Outbox pattern on PostgreSQL for state-change and event atomicity.
5. **Phase-0 Integration Inventory (`docs/integrations/EXTERNAL_INTEGRATION_INVENTORY.md`)**:
   - Section 7: Audit of verified events, business conditions, and transport neutrality.

---

## 3. Scope and Non-Scope

### 3.1 In-Scope
- Abstract semantic data model for outbound notification commands (`NotificationCommand`).
- Explicit boundary rules dividing operational intent (E-7) from downstream delivery (E-8).
- Provenance mapping of source-verified events and source-backed business conditions.
- Idempotency guarantees, correlation metadata, and tenant boundary enforcement.
- Data minimization and privacy rules for notification payloads.
- Test-harness synthetic mock specification and error classification.

### 3.2 Non-Scope
- Selection or configuration of concrete wire transport protocols.
- Runtime client libraries, network connection pools, or message serializer implementations.
- Configuration of external telecommunication gateways (SMS providers, SMTP relays, WhatsApp Business APIs, mobile push gateways).
- End-user communication templates, HTML layout rendering, and natural language localization.
- Resolution of the live MySQL multi-tenant extraction gate (`BLK-01`).

---

## 4. E-7 Responsibility

Under the unified IR Infotech ecosystem architecture, the contract establishes the following normative responsibilities for E-7:
1. **Operational Condition Detection**: E-7 MUST evaluate operational goals, WBS tasks, human approvals, SLA clocks, and schedule baseline assignments to determine when an institutional condition necessitates communication.
2. **Command Formulation**: Future implementation MUST assemble a transport-neutral `NotificationCommand` containing an authorized organization scope reference, stable command identity, correlation references, and minimal structured operational context.
3. **Recipient Identification**: E-7 SHOULD use privacy-preserving canonical/opaque recipient references where the eventual E-8 integration supports reference resolution. Raw contact PII must not be included merely for convenience.
4. **Atomic Outbox Persistence**: Future implementation MUST commit outbound notification commands atomically within the E-7 PostgreSQL transactional outbox alongside the operational state change that caused it (`ADR-005`).
5. **Command Identity**: Future implementation MUST assign a stable unique logical identifier (`command_id`) to each logical `NotificationCommand`. Its generation strategy is an implementation decision.

---

## 5. E-8 Responsibility

The safe architectural baseline established by primary source documentation is:
> **"E-8 owns downstream notification/engagement delivery behavior assigned to it by the source contract."**

To maintain rigorous source fidelity, detailed runtime delivery functions are classified as follows:

| Delivery Function / Concept | Status in Task 0G.6B.2 | Source Basis / Architectural Governance |
| :--- | :---: | :--- |
| **Downstream notification dispatch** | `SOURCE-BACKED` | *Complete Specification*, Section 22 (p. 17): *"Send reminders, SLA warnings, approvals and schedule-change notifications over email/SMS/WhatsApp/in-app."* |
| **Supported channels (email, SMS, WhatsApp, in-app)** | `SOURCE-BACKED` | Explicitly enumerated in *Complete Specification*, Section 22. |
| **Selecting "optimal" channels** | `REQUIRES_BUSINESS_CONFIRMATION` | Sources mention 4 delivery channels, but do not specify who selects channels or that E-8 selects an "optimal" channel. |
| **Channel-selection ownership** | `REQUIRES_BUSINESS_CONFIRMATION` | Component responsible for choosing delivery channels is unassigned in source documents. |
| **Recipient delivery preferences** | `NOT_DEFINED_IN_AVAILABLE_SOURCES` | Source specifications do not describe recipient preference storage or routing in E-8; classified as `REQUIRES_BUSINESS_CONFIRMATION`. |
| **Urgency-based routing** | `NOT_DEFINED_IN_AVAILABLE_SOURCES` | Not established by available source documents; classified as `REQUIRES_BUSINESS_CONFIRMATION`. |
| **Institutional quiet hours** | `NOT_DEFINED_IN_AVAILABLE_SOURCES` | Not established by available source documents; classified as `REQUIRES_BUSINESS_CONFIRMATION`. |
| **Recipient endpoint directory resolution** | `REQUIRES_BUSINESS_CONFIRMATION` | E-7 provides opaque recipient references; authoritative component for resolving references into physical endpoints requires business confirmation. |
| **Localization / template translation** | `NOT_DEFINED_IN_AVAILABLE_SOURCES` | Localization requirements are not defined in available source documents; classified as `REQUIRES_BUSINESS_CONFIRMATION`. |
| **Carrier connections & rate limits** | `NOT_DEFINED_IN_AVAILABLE_SOURCES` | Specific carrier interconnects and throttling rules are not defined in available source documents; classified as `REQUIRES_BUSINESS_CONFIRMATION`. |
| **Provider failover logic** | `NOT_DEFINED_IN_AVAILABLE_SOURCES` | Automatic provider failover logic is not defined in available source documents; classified as `REQUIRES_BUSINESS_CONFIRMATION`. |
| **Detailed delivery states (`sent`, `delivered`, `bounced`, `read`)** | `NOT_DEFINED_IN_AVAILABLE_SOURCES` | Specific external lifecycle states are not defined in source documents; delivery receipt reporting to E-7 is `UNRESOLVED / NOT_YET_REQUIRED`. |

> [!CAUTION]
> **Strict Architectural Invariant**: E-7 is an operations and orchestration engine, NOT a notification delivery platform. E-7 must never manage SMS gateways, SMTP servers, WhatsApp BSPs, or push notification vendor tokens.

---

## 6. Source-Verified Events

Based on the frozen Task 0G.6A inventory, exactly four literal event names are explicitly established by primary source specifications as targeting E-8 (*Complete Specification*, Section 21, p. 16):

| Event Name | Source Document & Location | Source Target | Source-Backed Description / Context | Classification |
| :--- | :--- | :--- | :--- | :--- |
| **`task.blocked`** | Spec Sec. 21 (Event Model table, p. 16) | Risk monitor, manager dashboard, E-8 notifications | Literal event name tabulated in source event model targeting Risk monitor, manager dashboard, and E-8 notifications. | `SOURCE FACT` |
| **`workflow.task.created`** | Spec Sec. 21 (Event Model table, p. 16) | Task inbox, E-8 | Literal event name tabulated in source event model targeting Task inbox and E-8. | `SOURCE FACT` |
| **`workflow.sla.warning`** | Spec Sec. 21 (Event Model table, p. 16) | E-8, manager dashboard | Literal event name tabulated in source event model targeting E-8 and manager dashboard. | `SOURCE FACT` |
| **`schedule.published`** | Spec Sec. 21 (Event Model table, p. 16) | Calendar sync, E-8 notifications | Literal event name tabulated in source event model targeting Calendar sync and E-8 notifications. | `SOURCE FACT` |

---

## 7. Source-Backed Business Conditions

Source documents mandate that E-7 trigger communications for five operational conditions (*Complete Specification*, Section 22, p. 17; *Complete Implementation Plan*, Section 13.3, p. 15). Where an exact event name is not tabulated in Section 21, its business condition is preserved, and its event name is cataloged as `UNRESOLVED`:

| Operational Condition | Source Citation | Associated Verified Event (if any) | Canonical Event Name Status | Task 0G.6B Normalized Condition Code | Code Classification |
| :--- | :--- | :--- | :---: | :--- | :--- |
| **Approval reminders** | Spec Sec. 22; Plan Sec. 13.3 | `workflow.task.created` | `workflow.task.created` | `APPROVAL_REMINDER` | `ENGINEERING DECISION — TASK 0G.6B` |
| **SLA warnings** | Spec Sec. 22; Plan Sec. 13.3 | `workflow.sla.warning` | `workflow.sla.warning` | `SLA_WARNING` | `ENGINEERING DECISION — TASK 0G.6B` |
| **Blocker alerts** | Spec Sec. 21 | `task.blocked` | `task.blocked` | `BLOCKER_ALERT` | `ENGINEERING DECISION — TASK 0G.6B` |
| **SLA breach / escalation** | Plan Sec. 13.3 | *None* | `UNRESOLVED` | `SLA_BREACH_ESCALATION` | `ENGINEERING DECISION — TASK 0G.6B` |
| **Schedule changes** | Spec Sec. 22; Plan Sec. 13.3 | *None* | `UNRESOLVED` | `SCHEDULE_CHANGE_NOTICE` | `ENGINEERING DECISION — TASK 0G.6B` |

> [!NOTE]
> Implementation labels such as `workflow.sla.breached` and `schedule.changed` are classified as **`DERIVED / PROPOSED — NOT SOURCE-VERIFIED`**. They are NOT canonical source events.

---

## 8. Contract Engineering Decisions

To construct a robust, testable integration boundary without overclaiming source facts, the following concepts are established as **`ENGINEERING DECISION — TASK 0G.6B`**:
1. **Message Envelope Name**: Standardizing on the concept name `NotificationCommand` as the provider-neutral data structure.
2. **Normalized Condition Codes**: Defining uppercase symbolic identifiers (`SLA_BREACH_ESCALATION`, `SCHEDULE_CHANGE_NOTICE`) to represent source-backed conditions lacking verified literal event names.
3. **Contract Version String**: Designating `1.0.0` as the semantic version of this specification.
4. **Validation Taxonomy**: Establishing structured error codes (`INVALID_CONTRACT_VERSION`, `MISSING_COMMAND_IDENTITY`, etc.) for contract boundary validation.
5. **Test Harness Statuses**: Defining `MOCK_ACCEPTED` and `MOCK_REJECTED` for local test harness contract validation testing only.

---

## 9. Transport Neutrality

The production communication transport between E-7 and E-8 is explicitly **`UNRESOLVED`**.

- **Transport Neutrality**: The contract is transport-neutral. Production transport remains `UNRESOLVED`.
- **Internal vs External Broker**: While RabbitMQ is an approved internal event broker for E-7 (`ADR-005`), that architectural decision governs E-7 internal background workers and does not mandate the wire protocol between E-7 and external engines.
- **Serialization Standard**: The `NotificationCommand` contract is transport-neutral and JSON-serializable, designed to bind cleanly to message frames, cloud event envelopes, or request payloads when wire transport is formalized.

---

## 10. Transactional Outbox Boundary

When an E-7 state change triggers a notification command, reliability is governed by the Transactional Outbox pattern (`ADR-005`):
1. **Atomic Persistence**: Outbound notification commands MUST be written to **E-7 PostgreSQL transactional outbox/event records** within the exact same database transaction that commits the domain state change (e.g., plan approval, task transition, schedule publication).
2. **Storage Neutrality**: This contract does not prescribe physical outbox table names.
3. **Transport Decoupling**: Downstream message brokers or relay workers are ephemeral delivery infrastructure. If the broker or transport is unavailable, outbox records remain safely persisted in PostgreSQL until connectivity is restored.

---

## 11. NotificationCommand Contract

The logical `NotificationCommand` data structure consists of the following fields:

```json
{
  "contract_version": "1.0.0",
  "command_id": "<synthetic_command_ref>",
  "correlation_reference": "<synthetic_correlation_ref>",
  "causation_reference": "<synthetic_causation_ref>",
  "organization_scope_reference": "<synthetic_organization_scope_ref>",
  "trigger": {
    "trigger_kind": "SOURCE_VERIFIED_EVENT",
    "source_event_name": "workflow.task.created",
    "source_entity_reference": "<canonical_task_ref>"
  },
  "recipient_references": [
    "<opaque_recipient_ref_1>",
    "<opaque_recipient_ref_2>"
  ],
  "notification_context": {
    "task_title": "<synthetic_task_title>",
    "action_required": "REVIEW_AND_APPROVE",
    "deadline_utc": "<synthetic_timestamp_utc>"
  },
  "requested_at": "<synthetic_timestamp_utc>"
}
```

Field specifications and governance classifications:
- **`contract_version`** (`string`, mandatory, `ENGINEERING DECISION — TASK 0G.6B`): Semantic version of the contract specification (e.g., `'1.0.0'`).
- **`command_id`** (`string`, mandatory, `ENGINEERING DECISION — TASK 0G.6B`): Stable unique identifier/reference for one logical `NotificationCommand`. Its generation strategy is an implementation decision. The contract does NOT require deterministic ID generation or prescribe any specific format (such as UUID version, ULID, sequence, or database PK format).
- **`correlation_reference`** (`string`, mandatory, `ENGINEERING DECISION — TASK 0G.6B`): Opaque reference linking this command to the broader business transaction (goal, workflow instance, or scheduling scope).
- **`causation_reference`** (`string`, optional, `ENGINEERING DECISION — TASK 0G.6B`): Opaque reference identifying the specific E-7 domain event or user action that caused this command, when available.
- **`organization_scope_reference`** (`string`, mandatory, `ENGINEERING DECISION — TASK 0G.6B`): Authorized tenant / organization scope reference where the E-7 tenancy model requires it, originating from E-7's authenticated/authorized scope resolution. Task 0G.6B does not define the tenant claim schema. `BLK-01` remains specifically relevant to live MySQL source retrieval; the E-8 contract neither resolves nor expands `BLK-01`.
- **`trigger`** (`object`, mandatory, `ENGINEERING DECISION — TASK 0G.6B`): Provenance metadata detailing the operational cause. Contains `trigger_kind` (`SOURCE_VERIFIED_EVENT` or `SOURCE_BACKED_BUSINESS_CONDITION`), along with `source_event_name` or `business_condition`.
- **`recipient_references`** (`array` of `string`, mandatory, `ENGINEERING DECISION — TASK 0G.6B`): Approved canonical / opaque recipient references. E-7 SHOULD use privacy-preserving canonical/opaque recipient references where the eventual E-8 integration supports reference resolution. Raw contact PII and raw database PKs must not be included merely for convenience.
- **`notification_context`** (`object`, mandatory, `ENGINEERING DECISION — TASK 0G.6B`): Minimal structured operational context required for downstream engagement processing under the eventual E-8 contract. Favor minimal structured data. Specific context field examples (task title, deadline, breach duration, schedule version) are illustrative engineering choices, not mandatory source requirements.
- **`requested_at`** (`string`, mandatory, `ENGINEERING DECISION — TASK 0G.6B`): Normalized UTC ISO-8601 timestamp representing when the operational condition occurred / command was produced.

---

## 12. Command Identity and Idempotency

- **Stable Logical Identity**: Every outbound `NotificationCommand` MUST carry a non-empty `command_id` serving as a stable unique logical identifier for one command instance.
- **Generation Strategy Neutrality**: The `command_id` generation strategy is an implementation decision. The contract does NOT require deterministic ID generation or prescribe any specific format (such as UUID version, ULID, sequence, or database PK format).
- **Duplicate Semantics**: Repeated processing of the same logical command MUST NOT intentionally create an additional logical notification request.
- **Delivery Guarantees**: Transport/provider exactly-once delivery is NOT guaranteed by this Phase-0 contract.
- **Implementation Independence**: Storage mechanisms for idempotency (e.g., caches, database constraints, deduplication tables) remain runtime implementation decisions.

---

## 13. Correlation and Causation

- **Correlation Reference (`correlation_reference`)**: A mandatory opaque string linking the notification command to the top-level business operation (e.g., goal ID, semester examination planning cycle, workflow instance).
- **Causation Reference (`causation_reference`)**: An optional reference identifying the immediate E-7 event, state transition, or user action that triggered this command.
- **Clean-Room Isolation**: Raw MySQL database identifiers from upstream sources (`mysql_iredu`) must never be used as correlation or causation references.

---

## 14. Organization Scope

- **Mandatory Boundary**: Every `NotificationCommand` requires an authorized organization-scope reference where the E-7 tenancy model requires it.
- **Origin of Scope**: That reference must originate from E-7's authenticated/authorized scope resolution.
- **Tenant Claim Schema**: Task 0G.6B does not define the tenant claim schema.
- **Relation to BLK-01**: `BLK-01` remains specifically relevant to live MySQL source retrieval. The E-8 contract neither resolves nor expands `BLK-01`.

---

## 15. Trigger Contract

The `trigger` object establishes strict provenance for why the notification is being emitted:

```json
"trigger": {
  "trigger_kind": "SOURCE_VERIFIED_EVENT",
  "source_event_name": "schedule.published",
  "source_entity_reference": "<canonical_schedule_ref>"
}
```

### 15.1 `trigger_kind`
Must be exactly one of:
- **`SOURCE_VERIFIED_EVENT`**: Used when the trigger corresponds to one of the four verified literal event names (`task.blocked`, `workflow.task.created`, `workflow.sla.warning`, `schedule.published`). The field `source_event_name` is mandatory.
- **`SOURCE_BACKED_BUSINESS_CONDITION`**: Used when the trigger originates from an approved business condition without an established canonical event name. The field `business_condition` is mandatory (e.g., `SLA_BREACH_ESCALATION`, `SCHEDULE_CHANGE_NOTICE`).

---

## 16. Recipient Reference Contract

- **Opaque References**: `recipient_references` is an array of non-empty strings referencing canonical actors (e.g., `<opaque_recipient_ref_user_1>`, `<opaque_recipient_ref_cohort_lead>`).
- **Privacy-Preserving Baseline**: E-7 SHOULD use privacy-preserving canonical/opaque recipient references where the eventual E-8 integration supports reference resolution. Raw contact PII must not be included merely for convenience.
- **Zero Raw PII**: Email addresses, phone numbers, WhatsApp JIDs, and mobile push tokens are strictly prohibited by default.
- **Zero Raw Database PKs**: Upstream MySQL integer IDs (e.g., `students.id: 104`) are strictly forbidden.
- **Resolution Boundary Ownership**: The authoritative component and contract responsible for resolving approved recipient references into delivery endpoints is `REQUIRES_BUSINESS_CONFIRMATION` unless direct source evidence establishes E-8 ownership. Task 0G.6B does not invent an institutional directory architecture.

---

## 17. Notification Context Contract

The `notification_context` map supplies the minimal structured operational context required for downstream engagement processing under the eventual E-8 contract:
- **Downstream Processing Scope**: It may later support rendering, template selection, routing, human-facing display, or other downstream processing ONLY where the final E-8 contract assigns those responsibilities. Do not assume template resolution is necessarily performed by E-8.
- **Illustrative Context Fields**: Specific context fields (such as `task_title`, `approval_deadline_utc`, `breach_timestamp_utc`, `schedule_version`, `disruption_reason`) are classified as `ENGINEERING DECISION — TASK 0G.6B` and are illustrative examples for mock scenarios, NOT mandatory source requirements.
- **Data Minimization**: Favor minimal structured data. The schema does not require specific illustrative fields.
- **Format Requirements**: All dates and times within context MUST be formatted as normalized UTC ISO-8601 strings.
- **Display Neutrality**: Context fields describe operational facts, not formatting instructions.

---

## 18. Data Minimization

In compliance with the AI Context Access Policy (`AI_CONTEXT_ACCESS_POLICY.md`) and ecosystem guardrails, the following data elements are strictly forbidden in `notification_context`:
1. Individual student academic evaluations, disciplinary remarks, or health records.
2. Employee compensation, salary amounts, or banking credentials.
3. System passwords, API secrets, authentication tokens, or private keys.
4. Unredacted raw SQL rows or full relational table snapshots.
5. Full workflow process engine execution state dumps.
6. Raw upstream MySQL database IDs (`mysql_iredu`).

---

## 19. Rendering and Template Boundary

- **E-7 Boundary Rule**: E-7 MUST NOT become a provider-specific message-rendering/delivery implementation.
- **Operational Intent**: E-7 provides notification intent and minimum structured operational context.
- **Final Rendering Ownership**: Final rendering/template-selection ownership is assigned according to the E-8 contract.
- **Template & Rendering Boundary**: The responsibility boundary for message subject/title, rendering, template selection, and structured context interpretation is `REQUIRES_BUSINESS_CONFIRMATION`.
- **Existing Fixture Difference**: The existing frozen fixture `INT-NOTIFY-001.json` contains a human-facing `subject`. The contract does not declare that such a concept can never exist in E-7; rather, the compatibility difference is documented explicitly: Task 0G.6B structures notification intent through typed triggers and structured context, whereas `INT-NOTIFY-001` included a top-level `subject`. Future reconciliation belongs to implementation/test-planning work.

---

## 20. Channel Responsibility

- **Supported Channels**: Complete Specification Section 22 (p. 17) explicitly cites four communication channels supported by E-8:
  - **Email**
  - **SMS**
  - **WhatsApp**
  - **In-App Push**
- **Channel Selection Responsibility**: Classified as `CHANNEL_SELECTION_RESPONSIBILITY = REQUIRES_BUSINESS_CONFIRMATION`.
- **Boundary Distinction**: The contract distinguishes supported channels from channel-selection ownership. Do NOT infer that E-8 necessarily selects channels according to user preference, urgency, quiet hours, or failover logic unless directly sourced. The component responsible for channel selection remains `REQUIRES_BUSINESS_CONFIRMATION`.

---

## 21. Versioning

- **Contract Document Version**: Standardized as `1.0.0` (`ENGINEERING DECISION — TASK 0G.6B`).
- **Compatibility Rule**: Future modifications to this contract must maintain backward compatibility. E-8 must reject payloads carrying unsupported major versions with `INVALID_CONTRACT_VERSION`.

---

## 22. Timestamp Semantics

- **Normalized UTC**: All timestamps (`requested_at`, `deadline_utc`, `breached_at_utc`) MUST be ISO-8601 UTC strings ending in `Z`.
- **Conceptual Semantics**: Timestamps represent the business moment the operational condition was recognized by E-7. No specific database timestamp datatype or serialization library is mandated.

---

## 23. Delivery Receipt / Feedback Status

- **Status**: **`UNRESOLVED / NOT_YET_REQUIRED`** (`E8_TO_E7_DELIVERY_RECEIPT_CONTRACT = UNRESOLVED_NOT_YET_REQUIRED`).
- **Source Audit Finding**: Available source documents do not establish a production callback, delivery receipt, or feedback loop from E-8 to E-7.
- **No Invented Runtime Mechanisms**: Do NOT invent delivery callbacks, provider message IDs, sent/delivered/read/bounced event schemas, or retry acknowledgments.
- **Test Mock Boundary**: Statuses such as `MOCK_ACCEPTED` and `MOCK_REJECTED` represent **local test harness validation only** and are **not** production delivery receipts.

---

## 24. Failure Categories

Contract validation errors are standardized under the following taxonomy (`ENGINEERING DECISION — TASK 0G.6B`):

| Error Code | Trigger Condition | Architectural Significance |
| :--- | :--- | :--- |
| **`INVALID_CONTRACT_VERSION`** | Missing or unsupported `contract_version` | Prevents schema divergence across engines |
| **`MISSING_COMMAND_IDENTITY`** | Missing or empty `command_id` | Enforces mandatory command identity tracking |
| **`ORGANIZATION_SCOPE_MISSING`** | Missing `organization_scope_reference` | Prevents multi-tenant boundary violation |
| **`UNSUPPORTED_TRIGGER`** | Malformed `trigger` object or unverified event | Enforces strict source fidelity |
| **`INVALID_RECIPIENT_REFERENCE`** | Missing recipient list, raw database PKs, or raw PII | Enforces clean-room reference isolation |
| **`POLICY_DENIED`** | Institutional notification policy restriction | Enforces tenant-level communication rules |
| **`PAYLOAD_NOT_ALLOWED`** | Raw PII, passwords, salaries, or secrets detected | Enforces data minimization guardrails |
| **`DUPLICATE_COMMAND`** | Identical `command_id` already processed | Enforces idempotent delivery semantics |
| **`REQUIRED_CONTEXT_MISSING`** | Trigger-specific required business facts absent | Ensures necessary operational context is present |

---

## 25. Duplicate Handling

- **Idempotency Invariant**: Repeated processing of the same logical command MUST NOT intentionally create an additional logical notification request.
- **Production Semantics**: In production semantics, receiving a duplicate command identity is not automatically an operational failure. A duplicate may be recognized, idempotently absorbed, and returned as a structured duplicate outcome without creating a second logical command.
- **Protocol Neutrality**: Transport ACK/NACK or HTTP semantics are not assigned by this contract.

---

## 26. Audit Requirements

To satisfy regulatory and operational compliance (`Complete Specification`, Section 23), every emitted `NotificationCommand` MUST generate an auditable record within E-7:
- Timestamp of emission (`requested_at`).
- Stable command identifier (`command_id`).
- Correlation and causation references.
- Trigger provenance (`trigger_kind`, `source_event_name`, or `business_condition`).
- Recipient count and opaque references.
- Outbox persistence status.

---

## 27. Security Boundary

All notification dispatches must strictly comply with ecosystem security rules:
- **No Credentials**: Payloads must never contain tokens, private keys, database passwords, or connection strings.
- **No Raw Database Primary Keys**: External database keys (`mysql_iredu`) must not cross the clean-room boundary.
- **No Direct LLM Access**: Generative AI models must not interact with notification dispatch gateways or external messaging systems.
- **Encrypted In-Transit**: When wire transport is selected, communication must require authenticated, encrypted endpoints.

---

## 28. Synthetic Mock Contract

The synthetic mock boundary is defined in [`E8_NOTIFICATION_ENGAGEMENT_MOCK.json`](file:///F:/operations-engine/docs/integrations/E8_NOTIFICATION_ENGAGEMENT_MOCK.json).
- **Synthetic Only**: Uses purely symbolic identifiers (`<synthetic_command_ref>`, `<opaque_recipient_ref>`).
- **Validation Scenarios**:
  - Four accepted examples covering all source-verified events and source-backed conditions without urgency fields.
  - Six rejected examples demonstrating contract error enforcement (missing version, missing ID, raw PII, raw database PKs, unverified triggers, and forbidden context).

---

## 29. Existing INT-NOTIFY Fixture Compatibility

The existing Phase-0 fixture [`tests/fixtures/integrations/INT-NOTIFY-001.json`](file:///F:/operations-engine/tests/fixtures/integrations/INT-NOTIFY-001.json) was audited:
- **Compatibility Status**: **`PARTIALLY_COMPATIBLE`**.
- **Compatible Aspects**: Uses the verified event `schedule.published`, opaque resource references (`canonical_resource_ids`), tenant scoping, idempotency keys, and prohibits credentials and real recipient addresses.
- **Differences in Task 0G.6B**:
  - `INT-NOTIFY-001` embeds `command_type: "SCHEDULE_PUBLISHED_NOTICE"` and a human-facing `subject` string.
  - Task 0G.6B formalizes a decoupled `trigger` object and structured context, leaving subject and template rendering assigned according to the E-8 contract boundary.
  - `INT-NOTIFY-001` remains frozen as an integration test reference and is **not modified**. It is a frozen Phase-0 fixture representing an earlier contract shape; it is not incorrect, and future reconciliation belongs to implementation/test-planning work.

---

## 30. Production Decisions Still Unresolved

The following decisions remain unresolved in source documentation and require confirmation prior to production rollout:
1. **`DEC-E8-01`**: Confirm the production E-7 ↔ E-8 transport protocol. (Status: `UNRESOLVED`. The contract is transport-neutral; RabbitMQ is approved for E-7 internal events under ADR-005, but inter-engine transport remains unselected).
2. **`DEC-E8-02`**: Confirm the authoritative component and contract responsible for resolving approved recipient references into delivery endpoints. (Status: `REQUIRES_BUSINESS_CONFIRMATION`).
3. **`DEC-E8-03`**: Determine whether E-8 must provide delivery/engagement result feedback to E-7 and, if required, define that contract. (Status: `REQUIRES_BUSINESS_CONFIRMATION`).
4. **`DEC-E8-04`**: Confirm the responsibility boundary for message subject/title, rendering, template selection, and structured context interpretation. (Status: `REQUIRES_BUSINESS_CONFIRMATION`).
5. **`DEC-E8-05`**: Confirm the authoritative component and policy responsible for selecting delivery channel(s) from the source-supported channel set. (Status: `REQUIRES_BUSINESS_CONFIRMATION`).

---

## 31. Acceptance Criteria

Task 0G.6B satisfies all Phase-0 integration contract acceptance criteria:
- Clear separation between operational trigger decisions (E-7) and downstream message delivery (E-8).
- Transport neutrality strictly preserved without assuming specific transport protocols.
- Source-verified event names (4) cleanly distinguished from business conditions (5) and engineering decisions.
- Opaque recipient references enforced; raw contact PII and raw database PKs prohibited.
- Complete failure taxonomy and synthetic mock dataset created without speculative fields or options.

---

## 32. Phase-0 Requirement Status

With the completion of this contract and synthetic mock boundary, the Phase-0 integration requirement status is updated as follows:

| Phase-0 Integration Area | Status Prior to 0G.6B | Status Following 0G.6B | Justification |
| :--- | :---: | :---: | :--- |
| **Exact Identity Provider (IdP)** | `UNRESOLVED` | `UNRESOLVED` | Protocol verified; exact vendor remains open decision in Spec Sec. 39. |
| **First ERP/SIS Source** | `RESOLVED` | `RESOLVED` | `mysql_iredu` verified and contracted; live retrieval gated by `BLK-01`. |
| **First Calendar Provider** | `UNRESOLVED` | `UNRESOLVED` | RFC 5545 capability verified; exact first provider open decision in Spec Sec. 39. |
| **E-8 Notification Contract / Mock** | `PARTIALLY_RESOLVED` | **`RESOLVED`** | Formal transport-neutral contract and synthetic mock boundary complete. |

> [!NOTE]
> Phase 0 as a whole is **not complete** because Identity Provider and Calendar Provider remain `UNRESOLVED`.

---

## 33. Next Task Recommendation

Task 0G.6B.2 completes the final correction to Task 0G.6B.

**Recommendation for Next Task**:
- Proceed to review and approval of Task 0G.6B.2.
- Following approval, the team may begin Phase-0 exit review or transition to Phase 1 scaffold setup in accordance with the master roadmap.

> The next task was not started.
