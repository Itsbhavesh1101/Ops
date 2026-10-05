# ADR-007: Cache and Coordination — Database First, Valkey Optional

## Status
Accepted

## Date
2026-09-16

## Context
Distributed caching and distributed locking are common patterns in enterprise orchestration architectures. Earlier exploratory notes frequently proposed Redis or Valkey as default infrastructure for session state, distributed rate limiting, ephemeral job status, and query acceleration.

However, as analyzed in the *Open-Source Research Report* (Section 2.7 & Section 14) and *Complete Implementation Plan* (Section 1.1, Section 4 & Section 17.1), introducing an in-memory cache server prematurely adds infrastructure cost, network complexity, cache invalidation bugs, and operational failure modes before actual bottlenecks are measured. PostgreSQL provides native row-level locking, transactional status updates, and PostgreSQL advisory locks (`pg_advisory_xact_lock`) which are fully capable of handling initial distributed coordination and idempotency caching.

## Decision
1. **Database-First Strategy**: E-7 will **NOT** introduce a distributed cache or in-memory key-value store in the initial architecture (Phases 1 through 6).
2. Distributed coordination, job locking, idempotency checks, and status tracking will be implemented using **PostgreSQL transactions and PostgreSQL advisory locks**.
3. **Valkey (BSD-3-Clause)** is classified as **optional and conditional**. It will **NOT** be included as a runtime service or dependency in the MVP.
4. **No Redis-Specific Coupling**: Application code must never import `redis-py` or embed Redis-specific data structures in domain services. If caching is later introduced, it must sit behind an abstract cache interface.
5. **Cache Is Never System of Record**: Authoritative business state must **never** exist exclusively in a cache. All canonical state must be committed to PostgreSQL.

## Decision Details
* **Idempotency Enforcement**: Idempotency keys are validated by checking an `idempotency_keys` table within the same transaction as the business operation.
* **Worker Coordination**: Task worker concurrency and solver job pickup use PostgreSQL row-level locks (`SELECT ... FOR UPDATE SKIP LOCKED`) or transaction-scoped advisory locks.
* **Ephemeral Job Status**: Status updates are written directly to PostgreSQL `schedule_jobs` or `plans` tables; latency is minimal given local PostgreSQL SSD storage.

## Authority Boundary
* **PostgreSQL / Transaction Manager Owns**:
  - All concurrency control, distributed lock acquisition, idempotency records, and job claim semantics.
* **Future Cache (Valkey, if approved) Will Own**:
  - Ephemeral read acceleration, non-critical session caches, and rate-limiting counters only.

## Configuration / No-Hardcoding Implications
* `VALKEY_URL` is retained as an optional, empty environment variable placeholder in `.env.example`. It remains inactive unless explicitly configured and enabled via a future ADR.

## Consequences

### Positive
* **Operational Simplicity**: One fewer stateful infrastructure container to deploy, monitor, secure, backup, and maintain in development and staging environments.
* **Strong Consistency**: Eliminates split-brain locking bugs and cache-invalidation race conditions; database locks release automatically if a worker crashes.
* **Reduced Memory Footprint**: Keeps base Docker Compose stack lightweight and fast to boot.

### Negative / Trade-offs
* Heavy polling of database tables for job queues could increase database load at very high transaction volumes (thousands of events/sec).
* High-frequency API rate limiting will execute against PostgreSQL rather than ultra-low-latency in-memory structures.

## Rejected or Deferred Alternatives
* **Redis as Default Phase-1 Dependency**: Deferred. Redis is an excellent tool, but unnecessary before measured throughput limits are reached.
* **In-Memory Application Dicts for Coordination**: Rejected. Process-local memory fails across multiple worker container replicas.
* **Zookeeper / Consul for Distributed Locking**: Rejected. Unacceptable operational footprint.

## Conditions for Reconsideration
Valkey may be introduced via a superseding ADR only if Phase-8 load testing demonstrates:
1. PostgreSQL connection pool or lock contention becomes a proven throughput bottleneck.
2. Read traffic on published schedules or tenant configurations causes measurable latency degradation that DB indexing and connection pooling cannot resolve.
3. High-throughput distributed rate limiting or pub/sub fan-out mandates an in-memory key-value engine.

## Implementation Phase
* **Phase 1: Platform Foundation** (PostgreSQL advisory lock helper, `SKIP LOCKED` job queue pattern).
* **Phase 8: Hardening** (Load testing and cache requirement review).
* **Phase 10: Evidence-Based Enhancements** (Valkey adoption gate if justified by benchmark data).

## Validation / Evidence Required
* `tests/integration/db/test_advisory_locks.py`: Concurrent workers attempting to claim the same job resolve cleanly without race conditions.
* `tests/integration/api/test_idempotency_locking.py`: Concurrent duplicate requests with identical `Idempotency-Key` are serialized and deduplicated via DB locks.

## References
* [`docs/SOURCE_OF_TRUTH.md`](file:///F:/operations-engine/docs/SOURCE_OF_TRUTH.md)
* [`docs/IMPLEMENTATION_GUARDRAILS.md`](file:///F:/operations-engine/docs/IMPLEMENTATION_GUARDRAILS.md)
* *Complete Implementation Plan*, Section 1.1, Section 4 & Section 17.1.
* *Open-Source Research Report*, Section 2.7 & Section 14.
