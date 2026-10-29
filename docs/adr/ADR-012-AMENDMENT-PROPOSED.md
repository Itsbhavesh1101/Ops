# ADR-012 Proposed Amendment: Option C — Two-Stage Notification Outbox Architecture

## Status
PROPOSED (Awaiting Formal ARB Review)

## Date
2026-10-29

## Authors / Engineering Reviewers
E-7 Core Architecture & Quality Engineering

## Target Document
This document is a formal proposed amendment to [ADR-012: E-8 Production Notification Boundary Architecture & Wire Protocol](ADR-012-e8-notification-boundary.md).  
The original accepted document `ADR-012-e8-notification-boundary.md` remains accepted and unmodified pending formal Architecture Review Board (ARB) review.

---

## 1. Context & Motivation

In original [ADR-012](ADR-012-e8-notification-boundary.md) (accepted 2026-10-07), Decision Item 1 stipulated that notification triggers are committed to the PostgreSQL transactional outbox (`outbox_events`) in the exact same ACID database transaction as the domain business state transition, and subsequently dispatched via `NotificationDispatcherProtocol`.

Subsequent integration analysis, code review, and reliability testing during Phase DQ-4 revealed that this single-stage outbox model creates operational and structural challenges at production scale:
1. **Separation of Domain Mutation from Notification Command:** Core domain transactions should only record pure domain events (`EventEnvelope`). Coupling the generation and schema of downstream engagement commands directly into operational domain services entangles business workflow boundaries with external notification concerns.
2. **Connection Pool Starvation in Direct Dispatch:** If external HTTP or AMQP delivery to E-8 is executed inline during outbox processing or event consumption, holding open database connections or message broker transactions during remote latency spikes rapidly exhausts database connection pools.
3. **Head-of-Line Blocking on Message Brokers:** When downstream E-8 experiences transient network latency or maintenance outages, unacknowledged RabbitMQ messages accumulate, blocking internal event traffic.
4. **Multi-Worker Concurrency & Lease Ambiguity:** Scaling delivery workers horizontally across multiple pods requires explicit row-level distributed locking (`SKIP LOCKED`) and clear lease expiration invalidation semantics to prevent duplicate network calls.

### 1.1 Structural Reason for Formal Amendment
Option C fundamentally separates **domain event atomicity** from **notification-command persistence**:
- Under original ADR-012, notification commands were assumed to be committed in the same database transaction as the original domain mutation.
- Under Option C, notification-command persistence occurs in a **separate, subsequent transaction** executed by the notification consumer (`NotificationWorker`) following internal domain event distribution via RabbitMQ.
Because this alters the transactional boundary of notification-command creation from a single-stage domain transaction to a two-stage decoupled pipeline, a formal ARB amendment to ADR-012 is required.

---

## 2. Decision: Option C — Two-Stage Notification Outbox Architecture

The E-7 to E-8 notification boundary is amended to a decoupled, two-stage transactional outbox pipeline:

```
[ Domain Mutation + outbox_events ] (ACID Transaction 1 - Domain)
               │
               ▼
       [ OutboxDispatcher ]
               │
               ▼ (Internal AMQP e7.events)
       [ NotificationWorker ] (Internal Consumer)
               │
               ▼ (ACID Transaction 2 - Notification Staging)
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
               ▼ (ACID Transaction 3 - Outcome Commitment)
[ notification_outbox_commands (mark DISPATCHED / FAILED / RETRY) ]
```

### 2.1 Domain State and Domain Event Atomicity (Stage 1)
- Core domain services (Workflow, Schedule, Exam, Task) execute operational state changes inside a standard PostgreSQL ACID transaction.
- In the exact same transaction, services append pure domain event envelopes (`EventEnvelope`) to `outbox_events` via `OutboxRepository`.
- Domain services remain completely decoupled from downstream engagement endpoints, recipient contact directories, or notification template structures.
- The `OutboxDispatcher` publishes events from `outbox_events` to the internal RabbitMQ topic exchange `e7.events` using routing key `{tenant_id}.event.{domain}.{action}`.

### 2.2 Transactional Inbox and Notification-Command Persistence (Stage 2)
- The notification consumer (`NotificationWorker`) subscribes to `e7.events` on RabbitMQ.
- Upon receiving a domain event, it executes a short, isolated database transaction in PostgreSQL:
  1. **Inbox Deduplication:** Checks and records the message ID in `inbox_messages` to ensure idempotent processing at the consumption boundary.
  2. **Recipient Governance Evaluation:** Evaluates recipient references. If `allow_provisional_recipients=False` (default under `DEC-E8-02`) and references are provisional or unverified, the command is staged as `BLOCKED`.
  3. **Outbox Command Staging:** Staging inserts a persistent `NotificationOutboxCommandRecord` in table `notification_outbox_commands` with state `PENDING` (or `BLOCKED` with reason `BLOCKED_PENDING_RECIPIENT_RESOLUTION_DEC_E8_02`).
- The RabbitMQ message is acknowledged immediately upon successful commit of Transaction 2. No external network call to E-8 has occurred.

### 2.3 Independent Notification Delivery & Network I/O Isolation (Stage 3)
- An autonomous background delivery worker (`NotificationDeliveryWorker`) runs independently of RabbitMQ consumers and domain services.
- The worker executes an asynchronous polling loop:
  1. Claims pending commands via `SELECT ... FOR UPDATE SKIP LOCKED` bounded by batch size.
  2. Sets `dispatch_state = 'PROCESSING'`, assigns worker ownership via `lease_owner = worker_id`, and sets an expiration timestamp `lease_expires_at = now + lease_duration`.
  3. Commits the lease transaction immediately, releasing the database row lock.
- **Strict Network I/O Boundary:** The worker executes external dispatch (via `NotificationDispatcherProtocol`, whether HTTP POST to `E7_NOTIFICATION_ENDPOINT` or AMQP publish to `e8.notifications`) **strictly outside any database transaction or connection hold**.
- Database connections are returned to the pool immediately after lease acquisition, preventing pool exhaustion during downstream latency.

### 2.4 Retry and At-Least-Once Delivery Semantics
- **At-Least-Once Delivery Semantics:** E-7 provides at-least-once delivery guarantees with exponential retry backoff. End-to-end exactly-once delivery cannot be guaranteed without downstream E-8 idempotency verification.
- **Deterministic Deduplication:** Every staged command carries a deterministic `command_id` derived from the causation event (e.g. `notif-pub-{event_id}`), enabling downstream E-8 systems to deduplicate retried dispatches.
- **Exponential Backoff:** If external delivery fails (HTTP 5xx, timeout, connection drop):
  - In a short database transaction, the worker increments `retry_count` and updates `next_attempt_at = now + (base_delay_seconds * (2 ** retry_count))`.
  - The command state is reset to `PENDING` if `retry_count < max_retries`.
  - If `retry_count >= max_retries`, the command transitions to terminal state `FAILED` and an audit alert is logged.
- **Lease Expiration Invalidation (`DEC-LE-01`):**
  - If a worker stalls or experiences prolonged delays exceeding `lease_duration`, another worker may reclaim the row once `lease_expires_at <= now`.
  - When completing or failing a command, `mark_dispatched()` and `mark_failed()` in `NotificationOutboxRepository` verify that `lease_owner` matches the caller and, when `enforce_lease_expiration=True`, that `lease_expires_at > now`.
  - If the lease has expired or the row was claimed by another worker, the repository updates 0 rows and returns `False`. The stalled worker aborts completion, preventing split-brain state overwrites. (The repository returns `False`; it does not raise custom lease exceptions).
- **Fail-Closed Recipient Governance (`DEC-E8-02`):** Provisional recipient references are staged in state `BLOCKED`, preventing dispatch until administrative recovery (`REC-E8-01`) or confirmed institutional directory authorization.

---

## 3. Differences from Existing ADR-012 Boundary

| Architectural Dimension | Existing ADR-012 (Accepted) | Proposed Option C Amendment (AD-DQ4-01) |
|---|---|---|
| **Transactional Boundary** | Notification triggers committed in the same database transaction as domain business mutations. | Decoupled: Domain transaction commits pure `EventEnvelope` to `outbox_events`; consumer transaction commits command to `notification_outbox_commands`. |
| **Outbox Table** | Shared `outbox_events` table for both internal domain events and external notification triggers. | Dedicated `notification_outbox_commands` table (migration `0015`) specifically partitioned for external notification commands. |
| **Network I/O Execution** | Dispatched during outbox polling or inline consumer processing, potentially holding connections. | Network I/O strictly isolated outside of database transactions with zero held connections. |
| **Worker Concurrency** | Basic polling without row-level lock skipping or formal lease expiration contract. | Distributed `SELECT FOR UPDATE SKIP LOCKED` with explicit lease owner tracking (`lease_owner`) and lease expiration invalidation (`DEC-LE-01`). |
| **Blocked Recipient Handling** | Emitted audit log only; no persistent outbox staging or executable unblock mechanism. | Persistent `BLOCKED` state in database with administrative recovery engine (`REC-E8-01`). |
| **Production Default** | Enabled when endpoint/broker configured. | Default-disabled fail-closed guard (`NotificationSettings.enabled = False`). |
| **Delivery Guarantee** | Described as exactly-once at committed-effect level. | Explicitly classified as at-least-once delivery; end-to-end exactly-once delivery requires verified downstream E-8 idempotency. |

---

## 4. Migration Compatibility

1. **Additive Schema Migration:**
   - Migration [`app/db/migrations/versions/0015_create_notification_outbox.py`](../../app/db/migrations/versions/0015_create_notification_outbox.py) is purely additive. It creates table `notification_outbox_commands` without altering existing tables (`outbox_events`, `inbox_messages`).
   - Reversible down-revision drop cleanly removes the table without impacting domain transactions.
2. **Backward Compatibility:**
   - The core dispatch interface `NotificationDispatcherProtocol` is preserved. Existing mock dispatchers, HTTP dispatchers, and AMQP dispatchers continue to satisfy the interface without modification.
   - Systems operating in direct mode (for local testing or unit test suites) can bypass Stage 2/3 staging via existing direct dispatch mocks without regression.
3. **Database Engine Support:**
   - Production PostgreSQL utilizes native `FOR UPDATE SKIP LOCKED`.
   - SQLite and in-memory test doubles emulate locking behavior via thread locks and parameter-filtered query logic without requiring external database services.

---

## 5. Operational Consequences

### 5.1 Positive Consequences
- **Resilience to E-8 Outages:** Downstream E-8 degradation or downtime has zero impact on internal transaction throughput or RabbitMQ event distribution.
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
