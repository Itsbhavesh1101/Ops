# ADR-005: Event Broker — RabbitMQ Initial, Kafka Conditional

## Status
Accepted

## Date
2026-09-16

## Context
The E-7 Operations Engine is designed as an asynchronous, event-driven orchestration system. State transitions in goals, plans, workflows, tasks, SLAs, and schedules emit domain events to trigger downstream background jobs, notify external services (e.g., E-8 Engagement Engine), and synchronize calendars.

Earlier notes considered Apache Kafka as a messaging backbone. The *Open-Source Research Report* (Section 2.7 & Section 14) and *Complete Implementation Plan* (Section 4 & Section 10.3) concluded that Kafka introduces substantial operational footprint (ZooKeeper/KRaft clustering, heavy JVM resource demands, topic management complexity) that is disproportionate for initial development and initial tenant rollout. A lightweight, robust AMQP message broker with dead-lettering and flexible routing is required for Phase 1, while maintaining clear architecture patterns that decouple business correctness from the broker transport.

## Decision
1. **RabbitMQ (MPL-2.0 / Apache-2.0)** is selected as the **primary initial message broker** for E-7 domain events and asynchronous background work queues.
2. **Apache Kafka (Apache-2.0)** is classified as **conditional**. It may only be introduced if:
   - Platform-wide IR Infotech infrastructure standards formally mandate Kafka event hubs, or
   - High-throughput streaming requirements (e.g., millions of IoT/sensor events/sec in future engines) justify Kafka's operational overhead.
3. **Core Reliability Pattern**: Business correctness and data integrity must **NEVER** depend directly on RabbitMQ delivery guarantees alone. The system implements the **Transactional Outbox and Idempotent Consumer Pattern**:
   - **Atomic State Write**: Every state mutation and its corresponding domain event record are written atomically to PostgreSQL in a single database transaction.
   - **Outbox Relay**: A background publisher reads unpublished events from the `domain_events` table and relays them to RabbitMQ exchanges.
   - **Broker Resilience**: Even during a complete RabbitMQ outage, committed business transactions are never lost; the outbox buffers events until the broker recovers.
   - **Consumer Inbox / Deduplication**: Event consumers must be idempotent and deduplicate incoming events by `event_id`.
4. **No "Exactly-Once" Myth**: The architecture guarantees **At-Least-Once** network delivery; end-to-end correctness is achieved via **application-level idempotency**, not broker magic.

## Decision Details
* **Broker Topology**: RabbitMQ runs in containerized deployment (`rabbitmq:3-management-alpine`).
* **Topic Routing**: Events are published to topic exchanges (`e7.events`) using structured routing keys (`{tenant_id}.{domain}.{event_type}`).
* **Dead-Letter Exchanges (DLX)**: Failed worker jobs are routed to dead-letter queues after bounded retries with exponential backoff.
* **Event Schemas**: All domain events follow the CloudEvents 1.0 specification with strict semantic versioning.

## Authority Boundary
* **RabbitMQ Owns**:
  - Message transport, AMQP queue buffering, exchange routing, and consumer acknowledgement (ACK/NACK).
* **PostgreSQL / E-7 Core Owns**:
  - Authoritative event persistence (`domain_events` outbox table).
  - Outbox dispatch state (`published_at`, `retry_count`).
  - Consumer deduplication inbox (`processed_events` table).
  - Business transaction atomicity.

## Configuration / No-Hardcoding Implications
* RabbitMQ connection URLs, user credentials, exchange names, and prefetch limits must be configured via environment variables (`BROKER_URL`, `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`).
* Event schemas must declare version numbers (e.g., `v1`) in message metadata to permit non-breaking schema evolution.

## Consequences

### Positive
* **Low Operational Overhead**: RabbitMQ boots in seconds with minimal memory footprint, ideal for developer workstations and initial staging clusters.
* **Flexible AMQP Routing**: Powerful topic exchanges, message TTLs, and dead-letter queues out of the box.
* **Fail-Safe Reliability**: The transactional outbox guarantees zero data loss regardless of broker uptime.

### Negative / Trade-offs
* RabbitMQ does not support persistent replayable log offsets across weeks like Kafka; historical event replay relies on the PostgreSQL audit/outbox tables.

## Rejected or Deferred Alternatives
* **Apache Kafka for Initial MVP**: Deferred. Operational overhead and resource requirements are unnecessary for the initial transaction volumes of Phase 1–6.
* **Direct In-Memory Event Dispatch**: Rejected. In-memory queues lack worker crash resilience and cannot scale horizontally.
* **Direct Broker Publishing (Without Outbox)**: Strictly rejected. Dual-write race conditions lead to phantom events or lost messages when network errors occur during commit.

## Conditions for Reconsideration
Kafka will only be introduced via a superseding ADR if:
1. Enterprise tenant integration mandates Kafka event streaming for real-time data lakes.
2. Published domain event volume exceeds RabbitMQ cluster performance limits under benchmarked load testing.

## Implementation Phase
* **Phase 1: Platform Foundation** (Transactional outbox schema, RabbitMQ Docker service, publisher relay, consumer framework).
* **Phase 6: Unified Vertical Slice** (Cross-engine event flow verification).
* **Phase 8: Hardening** (Broker failover testing, outbox backpressure tuning, dead-letter recovery runbooks).

## Validation / Evidence Required
* `tests/integration/events/test_outbox_atomicity.py`: Rolled-back database transaction writes zero outbox records; committed transaction writes exactly one.
* `tests/integration/events/test_inbox_deduplication.py`: Delivering the same event ID 10 times results in exactly 1 business execution.
* `tests/integration/events/test_broker_outage.py`: Stopping RabbitMQ during API writes retains all events in `domain_events`; restarting broker successfully drains outbox.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/REQUIREMENTS_TRACEABILITY_MATRIX.md`](file:///F:/operations-engine/docs/REQUIREMENTS_TRACEABILITY_MATRIX.md) (Requirement `FR-INT-03`)
* *Complete Specification*, Section 21 & Section 22.
* *Open-Source Research Report*, Section 2.7 & Section 14.
