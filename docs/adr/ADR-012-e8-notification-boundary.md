# ADR-012: E-8 Production Notification Boundary Architecture & Wire Protocol

## Status
Accepted

## Date
2026-10-07

## Context
Phase 7 requires formalizing the production boundary between the E-7 Operations Engine and the E-8 Notification & Engagement Subsystem (FR-INT-01).

Source documents establish strict division of responsibility:
- **E-7 Responsibility**: Evaluates operational conditions, schedules, tasks, and SLAs to determine WHEN communication is required. Formulates transport-neutral `NotificationCommand` objects with minimal structured context and commits them atomically within PostgreSQL via the Transactional Outbox pattern (`ADR-005`).
- **E-8 Responsibility**: Owns downstream engagement behavior, including template expansion, channel selection (email, SMS, WhatsApp, mobile push), quiet-hours enforcement, recipient contact resolution, delivery retries, and delivery tracking.
- **Data Minimization & Clean-Room Isolation**: E-7 passes only canonical opaque recipient references and structured metadata. Raw contact PII (emails, phone numbers, tokens) and raw upstream database primary keys (`mysql_iredu`) are strictly prohibited from crossing the boundary.

## Wire Transport Protocol Evaluation

Available source evidence identifies two potential integration topologies:
1. **Asynchronous AMQP / RabbitMQ Message Broker (`ADR-005`)**:
   - Outbox dispatcher publishes `NotificationCommand` to RabbitMQ exchange `e8.notifications` with routing key `{tenant_id}.notification.{trigger_kind}`.
   - Provides broker durability, decoupling, backpressure, and automatic redelivery during E-8 maintenance or outages.
2. **Synchronous HTTP / REST Gateway**:
   - Outbox dispatcher delivers commands via HTTP `POST /v1/notifications/commands` with Bearer token authentication.
   - Simpler point-to-point integration, but requires consumer-side availability or retry buffering in the sender.

## Decision

1. **Transactional Outbox as Canonical Persistence Authority**:
   - E-7 domain services never execute blocking network calls directly to E-8.
   - Every notification trigger is committed atomically to the PostgreSQL transactional outbox (`outbox_events`) in the same ACID transaction as the business state transition.

2. **Dual-Transport Dispatcher Architecture**:
   - The production notification boundary is implemented behind `NotificationDispatcherProtocol`.
   - **Default Production Mode (AMQP)**: Dispatches via RabbitMQ topic exchange `e8.notifications` (or topic `e7.events` under topic `{tenant_id}.notification.*`).
   - **Configurable HTTP Mode**: When `E7_NOTIFICATION_ENDPOINT` is configured, dispatches commands via authenticated HTTP POST with exponential backoff and timeout safeguards.
   - **Test / Clean-Room Mode**: Supports deterministic in-memory `MockNotificationDispatcher` for unit tests and local development.

3. **Strict Idempotency & Replay Protection**:
   - Every `NotificationCommand` carries a stable, deterministic `command_id` derived from the causation event (e.g. `notif-pub-{event_id}`).
   - Consumer processors utilize PostgreSQL `inbox_messages` to guarantee exactly-once processing at the committed-effect level.
   - Broker redeliveries or dispatcher retries with identical `command_id` are safely detected as duplicates and cause zero duplicate downstream side effects.

4. **Failure & Degraded Recovery**:
   - During E-8 or broker outages, outbound commands remain safely in `outbox_events` with `PENDING` status.
   - When connectivity recovers, the outbox dispatcher resumes bounded batch delivery with idempotency guarantees.

## Consequences
- **Positive**: Strict system boundaries preserved; zero PII leakage; zero network latency on domain write transactions; guaranteed delivery under network partition.
- **Negative**: Requires operational monitoring of outbox queue depth and E-8 delivery status.
