# ADR-012 Proposed Amendment: Option C — Two-Stage Notification Outbox Architecture

## Status
PROPOSED (Awaiting Formal ARB Review)

## Date
2026-10-29

## Authors / Engineering Reviewers
E-7 Core Architecture & Quality Engineering

## Target Document
This document is a formal proposed amendment to [ADR-012: E-8 Production Notification Boundary Architecture & Wire Protocol](file:///f:/operations-engine/docs/adr/ADR-012-e8-notification-boundary.md).  
The original `ADR-012-e8-notification-boundary.md` remains accepted and unmodified until formal Architecture Review Board (ARB) ratification.

---

## 1. Context & Motivation

In original [ADR-012](file:///f:/operations-engine/docs/adr/ADR-012-e8-notification-boundary.md) (accepted 2026-10-07), Decision Item 1 stipulated that notification triggers are committed to the PostgreSQL transactional outbox (`outbox_events`) alongside domain state transitions, and dispatched via `NotificationDispatcherProtocol`.

Subsequent integration analysis, code review, and reliability testing during Phase DQ-4 identified structural vulnerabilities in direct single-stage outbox dispatch when scaling to production workloads:
1. **Connection Pool Starvation:** In a single-stage model or inline consumer model, executing remote network calls (HTTP REST or external AMQP publish) to E-8 while holding an active database transaction or message broker acknowledgement holds database connections idle for hundreds of milliseconds or seconds during downstream latency spikes.
2. **Head-of-Line Blocking on RabbitMQ:** When E-8 experiences transient outages or latency, message broker unacknowledged message queues accumulate, blocking non-notification events or causing broker connection timeouts.
3. **Domain Event vs. Command Entanglement:** Direct generation of notification commands in core domain services entangles operational workflow boundaries with downstream engagement channel logic, violating clean-room decoupling.
4. **Lease and Concurrency Ambiguity:** Multi-pod horizontal scaling of delivery workers without explicit row-level lease semantics risks concurrent duplicate deliveries under network latency or worker restart.

To resolve these defects while preserving strict reliability, transactional integrity, and data minimization, the engineering reviewer selected **Option C: Two-Stage Notification Outbox Architecture** (`AD-DQ4-01`).

---

## 2. Decision: Option C — Two-Stage Notification Outbox Architecture

The E-7 to E-8 notification boundary is amended from a single-stage direct dispatch pipeline to a decoupled, two-stage transactional outbox pipeline:

```
[ Domain Mutation + outbox_events ] (ACID Transaction 1)
               │
               ▼
       [ OutboxDispatcher ]
               │
               ▼ (AMQP e7.events)
       [ NotificationWorker ] (RabbitMQ Consumer)
               │
               ▼ (ACID Transaction 2)
[ inbox_messages (dedup) + notification_outbox_commands (stage PENDING/BLOCKED) ]
               │
          (ACK Broker)
               │
               ▼ (Asynchronous Decoupled Polling)
   [ NotificationDeliveryWorker ]
   (SELECT FOR UPDATE SKIP LOCKED + lease_expires_at)
               │
               ▼ (External Network I/O strictly OUTSIDE DB Transaction)
   [ E-8 Notification Subsystem (HTTP / AMQP) ]
               │
               ▼ (ACID Transaction 3)
[ notification_outbox_commands (mark DISPATCHED / FAILED / RETRY) ]
```

### 2.1 Domain State and Domain Event Atomicity (Stage 1)
- Core domain services (Workflow, Schedule, Exam, Task) execute business state changes inside a standard PostgreSQL ACID transaction.
- In the exact same transaction, services append pure domain event envelopes (`EventEnvelope`) to `outbox_events` via `OutboxRepository`.
- Domain services remain completely agnostic of notification endpoints, recipient contact details, or template layouts.
- The `OutboxDispatcher` polls `outbox_events` and publishes events to the internal RabbitMQ topic exchange `e7.events` with routing key `{tenant_id}.event.{domain}.{action}`.

### 2.2 Transactional Inbox and Notification-Command Persistence (Stage 2)
- The notification event consumer (`NotificationWorker` or `NotificationEngagementConsumer`) subscribes to `e7.events` on RabbitMQ.
- Upon receiving a domain event, it executes a short, atomic database transaction in PostgreSQL:
  1. **Inbox Deduplication:** Checks and records the incoming message ID in `inbox_messages` to guarantee exactly-once processing at the consumption boundary.
  2. **Recipient Governance Evaluation:** Inspects recipient identifiers. If `allow_provisional_recipients=False` (default under `DEC-E8-02`) and references are provisional or unverified, the record is flagged for blocked staging.
  3. **Outbox Command Staging:** Staging creates a persistent `NotificationOutboxCommandRecord` in table `notification_outbox_commands` with state `PENDING` (or `BLOCKED` with reason `BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02`).
- The RabbitMQ message is acknowledged immediately upon successful database commit of Transaction 2. No external network call to E-8 has occurred.

### 2.3 Independent Notification Delivery & Network I/O Isolation (Stage 3)
- An autonomous background delivery worker (`NotificationDeliveryWorker`) runs independently of RabbitMQ consumers and domain services.
- The worker executes a polling loop:
  1. Claims pending commands via `SELECT ... FOR UPDATE SKIP LOCKED` bounded by batch size.
  2. Sets `state = PROCESSING`, sets worker ownership `locked_by = worker_id`, and calculates an expiration timestamp `lease_expires_at = now + lease_duration`.
  3. Commits the lease transaction immediately, releasing the database row lock.
- **Strict Network I/O Boundary:** The worker performs external network dispatch (via `NotificationDispatcherProtocol`, whether HTTP REST POST to `E7_NOTIFICATION_ENDPOINT` or AMQP publish to `e8.notifications`) **strictly outside any database transaction or database connection hold**.
- Database connections from the connection pool are returned immediately after lease acquisition, eliminating pool exhaustion during downstream E-8 latency.

### 2.4 Retry and Idempotency Guarantees
- **Deterministic Command Identification:** Every staged command carries a deterministic `command_id` derived from the causation event (e.g. `notif-pub-{event_id}`).
- **Exponential Backoff:** If external delivery fails (HTTP 5xx, timeout, connection drop):
  - In a short database transaction, the worker increments `retry_count` and calculates exponential backoff:  
    `next_attempt_at = now + (base_delay_seconds * (2 ** retry_count))`.
  - The command state is reset to `PENDING` if `retry_count < max_retries`.
  - If `retry_count >= max_retries`, the command transitions to terminal state `FAILED` and emits an alert audit log.
- **Lease Expiration Invalidation (`DEC-LE-01`):**
  - If a worker crashes or experiences prolonged GC pauses exceeding `lease_duration`, another worker may reclaim the expired row once `lease_expires_at <= now`.
  - When the original worker eventually completes its late network call and calls `mark_dispatched()` or `mark_failed()`, the repository enforces a strict check:  
    `UPDATE notification_outbox_commands SET state='DISPATCHED' WHERE command_id=:id AND locked_by=:worker_id AND lease_expires_at > now`.
  - If the lease has expired, the update matches 0 rows and fails closed with `InvalidLeaseError`, preventing stale workers from overwriting newer re-attempt states or introducing split-brain inconsistencies.
- **Fail-Closed Recipient Governance (`DEC-E8-02`):** Provisional recipient references are staged in state `BLOCKED`, preventing dispatch until administrative recovery (`REC-E8-01`) or institutional directory authorization.

---

## 3. Differences from Existing ADR-012 Boundary

| Architectural Dimension | Existing ADR-012 (Accepted) | Proposed Option C Amendment (AD-DQ4-01) |
|---|---|---|
| **Outbox Table** | Shared `outbox_events` table for both internal domain events and external notification triggers. | Dedicated `notification_outbox_commands` table (migration `0015`) specifically partitioned for external notification commands. |
| **Command Staging Boundary** | Formulated directly inside domain business transaction. | Decoupled: Domain transaction writes pure `EventEnvelope`; `NotificationWorker` consumes event and stages `NotificationOutboxCommandRecord`. |
| **Network I/O Execution** | Dispatched during outbox polling or inline consumer processing, potentially holding connections. | Network I/O strictly isolated outside of database transactions with zero held connections. |
| **Worker Concurrency** | Basic polling without row-level lock skipping or formal lease expiration contract. | Distributed `SELECT FOR UPDATE SKIP LOCKED` with explicit lease expiration invalidation (`DEC-LE-01`). |
| **Blocked Recipient Handling** | Emitted audit log only; no persistent outbox staging or executable unblock mechanism. | Persistent `BLOCKED` state in database with authenticated `NotificationRecoveryService` (`REC-E8-01`). |
| **Production Default** | Enabled when endpoint/broker configured. | Default-disabled fail-closed guard (`NotificationSettings.enabled = False`). |

---

## 4. Migration Compatibility

1. **Additive Schema Migration:**
   - Migration [`app/db/migrations/versions/0015_create_notification_outbox.py`](file:///f:/operations-engine/app/db/migrations/versions/0015_create_notification_outbox.py) is purely additive. It creates table `notification_outbox_commands` without altering existing tables (`outbox_events`, `inbox_messages`).
   - Reversible down-revision drop cleanly removes the table without impacting domain transactions.
2. **Backward Compatibility:**
   - The core dispatch interface `NotificationDispatcherProtocol` is preserved. Existing mock dispatchers, HTTP dispatchers, and AMQP dispatchers continue to satisfy the interface without modification.
   - Systems operating in direct mode (for local testing or unit test suites) can bypass Stage 2/3 staging via existing direct dispatch mocks without regression.
3. **Database Engine Support:**
   - Production PostgreSQL utilizes native `FOR UPDATE SKIP LOCKED`.
   - SQLite and in-memory test mocks emulate locking behavior via thread locks and parameter-filtered query logic without requiring external database services.

---

## 5. Operational Consequences

### 5.1 Positive Consequences
- **Resilience to E-8 Outages:** Downstream E-8 degradation or complete downtime has zero impact on internal transaction throughput or RabbitMQ event distribution.
- **Connection Pool Protection:** Database connection pool capacity is preserved for operational API requests, eliminating starvation during slow external notification delivery.
- **Auditability and Traceability:** Every outbound notification attempt, retry count, lock lease, error payload, and completion timestamp is persistently visible in `notification_outbox_commands`.
- **Safe Recovery:** Blocked notifications resulting from unverified recipient references are durably retained and can be reviewed and unblocked administratively without re-triggering core domain workflows.

### 5.2 Negative Consequences & Operational Requirements
- **Additional Background Worker Daemon:** Requires running `NotificationDeliveryWorker` as a supervised daemon or background task in production pods.
- **Outbox Queue Depth Monitoring:** Operations must monitor table row counts in states `PENDING`, `PROCESSING`, and `BLOCKED`, configuring alerting for queue depth growth or high retry counts.
- **Data Retention / Purge Policy:** A periodic maintenance routine must be scheduled to purge or archive rows in terminal states `DISPATCHED` and `FAILED` after the compliance retention period (e.g., 30 days).

---

## 6. Formal Approval and Governance Alignment

- **Engineering Status:** `ENGINEERING_DESIGN_APPROVED` (reviewed and verified with 16 passing PostgreSQL integration tests).
- **Formal Status:** `PROPOSED (Awaiting Formal ARB Review)`.
- **Activation Status:** Production delivery remains gated under canonical blocker `BLK-03` (`E8_NOTIFICATION_INTEGRATION`).
