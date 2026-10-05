# Phase 1 Task 1G.4C1 — RabbitMQ Inbound Message Boundary & Single-Message Disposition

**Document Version**: 1.0.0  
**Phase**: Phase 1 — Foundation  
**Task**: Task 1G.4C1 — RabbitMQ Inbound Message Boundary & Single-Message Disposition  
**Status**: `COMPLETE` (Canonical Task 1G remains `IN PROGRESS`)  
**Scope**: Inbound RabbitMQ Message Trust Boundary, Wire Validation, Canonical Envelope Reconstruction, and Single-Message Broker Disposition  

---

## 1. Purpose & Architecture Overview

Task 1G.4C1 implements the transport adapter bridging incoming RabbitMQ messages (`aio_pika.IncomingMessage`) with the frozen provider-neutral `ConsumerEventProcessor` (Task 1G.4B).

The adapter serves as the **transport trust boundary**:
- All incoming message bytes and broker metadata are treated as **untrusted**.
- Strict, ordered wire validation protects the application from payload attacks, malformed JSON, duplicate keys, and metadata mismatches.
- Validated payloads are reconstructed into canonical `EventEnvelope` instances.
- Messages are dispatched to `ConsumerEventProcessor.process(envelope)` within a transactional UnitOfWork.
- The message is acknowledged (`basic_ack`) **only after** the database transaction has committed and the `UnitOfWork` context has cleanly exited.
- Deterministic invalid wire messages are rejected without requeue (`basic_reject(requeue=False)`).
- Processing failures and cancellations leave the message completely unsettled.

```
Incoming RabbitMQ Message
       ↓
[Untrusted Transport Boundary]
       ↓ 1. Body Size <= policy.max_message_bytes? (Else reject)
       ↓ 2. Content-Type == "application/json"?     (Else reject)
       ↓ 3. Content-Encoding == "utf-8"?            (Else reject)
       ↓ 4. Strict UTF-8 Decode                     (Else reject)
       ↓ 5. Strict JSON (No Duplicate Keys)         (Else reject)
       ↓ 6. JSON Root is Object?                    (Else reject)
       ↓ 7. EventEnvelope.model_validate(...)       (Else reject)
       ↓ 8. message.message_id == envelope.event_id? (Else reject)
       ↓ 9. message.type == envelope.event_type?    (Else reject)
       ↓
ConsumerEventProcessor.process(envelope)
       ↓
Outcome PROCESSED or DUPLICATE (Committed DB Transaction)
       ↓
message.ack()
       ↓
Return Handling Result
```

---

## 2. The Untrusted Transport Boundary

RabbitMQ delivers raw byte payloads alongside AMQP message properties. In a multi-tenant distributed system, consumers cannot assume incoming messages adhere to canonical contracts.

The adapter performs defensive checks prior to invoking application code:
1. **Denial-of-Service Defense**: Bounding body size prior to string decoding or memory allocation prevents memory exhaustion attacks.
2. **Media-Type Integrity**: Demanding explicit `application/json` prevents MIME confusion attacks.
3. **Encoding Strictness**: Demanding explicit `utf-8` and decoding strictly (without replacement characters) ensures encoding fidelity.
4. **JSON Parser Hardening (Task 1G.4C1A)**:
   - **Key Uniqueness**: Standard Python `json.loads` silently overwrites duplicate object keys with the last value. We enforce strict key uniqueness via `object_pairs_hook` to prevent JSON smuggling (`DUPLICATE_JSON_KEY`).
   - **Non-RFC Constants**: Standard `json.loads` accepts non-standard constants (`NaN`, `Infinity`, `-Infinity`). We reject them explicitly via `parse_constant` callback without normalizing or converting to floats (`INVALID_JSON`).
   - **Parser ValueError**: Pathological integer literals (e.g. exceeding Python's integer-string conversion limit `sys.set_int_max_str_digits`) raise `ValueError` from the decoder. These are caught at the parser boundary and treated as invalid wire input (`INVALID_JSON`).
   - **Recursion Depth**: Extremely deep JSON nesting triggering `RecursionError` is caught at the parser boundary and treated as invalid wire input (`INVALID_JSON`), preventing runner termination.
5. **Contract Enforcement**: The canonical `EventEnvelope` model validation enforces all tenant, version, timestamp, and payload constraints.

---

## 3. Inbound Message Policy (`RabbitMqInboundPolicy`)

Message size limits are configured via an immutable Pydantic model:
```python
class RabbitMqInboundPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_message_bytes: int = Field(
        ...,
        gt=0,
        description="Maximum allowed message body size in bytes.",
    )
```

- **Required**: `max_message_bytes` has no hidden production default; deployment configurations must explicitly supply this threshold.
- **Strictly Positive**: Rejects zero or negative values at construction.
- **Immutable**: Frozen and forbids extra fields.

---

## 4. Wire Validation Sequence

Before reaching `ConsumerEventProcessor.process(...)`, the incoming message undergoes the following ordered validation sequence:

| Step | Validation Target | Canonical Requirement | Rejection Reason |
| :--- | :--- | :--- | :--- |
| **1. Size** | `len(message.body)` | $\le \text{policy.max\_message\_bytes}$ | `BODY_TOO_LARGE` |
| **2. Media Type** | `message.content_type` | `application/json` (case-insensitive) | `INVALID_CONTENT_TYPE` |
| **3. Encoding Header** | `message.content_encoding` | `utf-8` or `utf8` (case-insensitive) | `INVALID_CONTENT_ENCODING` |
| **4. Byte Decoding** | `message.body.decode("utf-8")` | Strict UTF-8; no replacement characters | `INVALID_UTF8` |
| **5. JSON Syntax** | `json.loads(body_str)` | Valid standard JSON; no trailing tokens | `INVALID_JSON` |
| **6. JSON Uniqueness** | `_strict_object_pairs_hook` | Zero duplicate keys across all objects | `DUPLICATE_JSON_KEY` |
| **7. JSON Root** | `isinstance(parsed_json, dict)` | Must be a JSON object (not list/scalar/null) | `INVALID_JSON_ROOT` |
| **8. Envelope Model** | `EventEnvelope.model_validate` | Authoritative canonical envelope contract | `INVALID_EVENT_ENVELOPE` |
| **9. Message ID** | `message.message_id` | Present and equals `str(envelope.event_id)` | `MISSING_MESSAGE_ID` / `MESSAGE_ID_MISMATCH` |
| **10. Event Type** | `message.type` | Present and equals `envelope.event_type` | `MISSING_EVENT_TYPE` / `EVENT_TYPE_MISMATCH` |

If any check fails, the message is rejected immediately with `message.reject(requeue=False)`, and the processor is **never invoked**.

---

## 5. Broker Metadata Consistency & Authority

- **Canonical Authority**: The decoded and validated `EventEnvelope` is the authoritative source of event identity, timestamp, and payload. Broker properties are validated for consistency, but never overwrite or mutate the envelope.
- **Message ID**: Canonical producer transport (Task 1G.3A) sets AMQP `message_id = str(envelope.event_id)`. Task 1G.4C1 cross-checks that `message.message_id == str(envelope.event_id)`.
- **Event Type**: Producer sets AMQP `type = envelope.event_type`. Task 1G.4C1 cross-checks that `message.type == envelope.event_type`.
- **Correlation ID**: Present in `EventEnvelope.correlation_id`. If AMQP `correlation_id` is present on the message, it is treated as advisory and does not override the envelope value.

---

## 6. Broker Disposition Semantics

### 6.1 PROCESSED $\to$ ACK
When `processor.process(envelope)` returns `ConsumerProcessingOutcome.PROCESSED`:
- The deduplication marker in `inbox_messages` and the business mutations have already committed in PostgreSQL.
- The `UnitOfWork` context has exited cleanly.
- The adapter executes `await message.ack()`.
- Returns `RabbitMqMessageHandlingResult(outcome=ACKED_PROCESSED, event_id=envelope.event_id)`.

### 6.2 DUPLICATE $\to$ ACK
When `processor.process(envelope)` returns `ConsumerProcessingOutcome.DUPLICATE`:
- The event was previously processed and committed by this logical consumer.
- Business handlers were skipped; the transaction completed cleanly.
- The adapter executes `await message.ack()`.
- Returns `RabbitMqMessageHandlingResult(outcome=ACKED_DUPLICATE, event_id=envelope.event_id)`.

### 6.3 Deterministic Invalid Wire $\to$ Reject (requeue=False)
When an incoming message violates wire format or schema invariants:
- The adapter executes `await message.reject(requeue=False)`.
- It does **not** call `processor.process()`.
- Returns `RabbitMqMessageHandlingResult(outcome=REJECTED_INVALID_MESSAGE, event_id=..., invalid_reason=...)`.

### 6.4 Processor Failure $\to$ Unsettled
If `processor.process(envelope)` raises any exception or `asyncio.CancelledError`:
- The adapter catches nothing; the exception propagates directly.
- **Zero ACK**: The uncommitted transaction must not be acknowledged.
- **Zero NACK / Reject**: The adapter does not settle the message.
- **Why no `nack(requeue=True)`**: Calling `nack(requeue=True)` on unhandled exceptions creates an immediate hot poison-message loop. Instead, the message is left unacknowledged. The supervisor runner (Task 1G.4C2) will handle consumer failures, channel closures, and broker redelivery.

---

## 7. ACK Failure Window & Inbox Dedupe Recovery

A network failure or broker reset can occur during `await message.ack()`:
1. `ConsumerEventProcessor.process(envelope)` successfully commits business work and the Inbox marker.
2. `message.ack()` is invoked, but the AMQP connection drops.
3. The adapter catches the broker failure and raises `RabbitMqMessageAckError`.
4. RabbitMQ detects the closed channel and redelivers the message to another consumer instance.
5. On redelivery, `InboxRepository.try_record()` encounters the existing primary key `(consumer_name, event_id)` and returns `False`.
6. `ConsumerEventProcessor` returns `ConsumerProcessingOutcome.DUPLICATE`.
7. The new consumer successfully calls `message.ack()`.

This guarantees **at-least-once transport delivery with exactly-once business processing semantics**.

---

## 8. Dead-Letter Exchange (DLX) Prerequisite

`reject(requeue=False)` tells RabbitMQ that the current queue will not process the message and it must not be requeued back into the same queue.
- If a Dead-Letter Exchange (DLX) and Dead-Letter Queue (DLQ) are configured for the queue, RabbitMQ automatically routes the rejected message to the DLQ.
- If no DLX is configured, RabbitMQ drops the message permanently.
- **Topology Requirement**: Task 1G.4C1 implements message disposition only. The queue declaration with DLX configuration is deferred to **Task 1G.4C2**. In production, DLX/DLQ topology must be provisioned before consumer activation to preserve malformed message evidence.

---

## 9. Security & Privacy Guardrails

- **Zero Body Logging**: The adapter never logs `message.body`, raw JSON strings, or payload contents.
- **Zero Input Leakage in Errors**: Pydantic `ValidationError` details containing raw user/tenant input are caught and masked; only high-level `InvalidMessageReason` enums are exposed in results.
- **Zero Credential Exposure**: Broker URLs, authentication tokens, and credentials never enter results or exceptions.
- **Claim Token Exclusion**: Producer dispatch leases (`claim_token`) are strictly isolated to outbox persistence; zero claim token concepts exist in consumer transport.

---

## 10. Architectural Isolation & Boundaries

| Component | Responsibility | Bound In |
| :--- | :--- | :--- |
| **`RabbitMqInboundMessageHandler`** | Wire validation, envelope reconstruction, ACK/reject disposition | **Task 1G.4C1** |
| **`ConsumerEventProcessor`** | Deduplication check, business handler execution, UoW commit | Task 1G.4B (Frozen) |
| **`InboxRepository`** | PostgreSQL `inbox_messages` atomic persistence | Task 1G.4A (Frozen) |
| **Topology & Lifecycle** | RabbitMQ connection, channel, queue, exchange, bindings, runner | **Task 1G.4C2** (Deferred) |

The adapter imports zero persistence libraries (`SQLAlchemy`, `AsyncSession`, `InboxRepository`, `OutboxRepository`) and zero publisher classes (`RabbitMqEventPublisher`).

---

## 11. Files Created & Modified

### Created:
- `app/events/consumers/rabbitmq_message.py`: Inbound message adapter, policy, handling results, and rejection classifications.
- `tests/unit/test_rabbitmq_consumer_message.py`: Comprehensive test suite (47 unit tests) covering policy, wire validation, ordering, disposition, and security invariants.
- `docs/phase-1/PHASE_1_RABBITMQ_CONSUMER_MESSAGE_BOUNDARY.md`: This architecture document.

### Modified:
- `app/events/consumers/__init__.py`: Exported consumer message handling types.
- `docs/phase-1/PHASE_1_FOUNDATION_BASELINE.md`: Recorded Task 1G.4C1 completion.

---

## 12. Live Integration Status & Deferral

```python
RABBITMQ_CONSUMER_MESSAGE_RUNTIME_TEST = (
    "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
)
```

Live end-to-end testing with running RabbitMQ brokers and PostgreSQL engines is deferred to **Task 1J Runtime Integration**, where it will verify:
- End-to-end dispatch-to-consumption flow.
- Real broker ACK and reject delivery.
- RabbitMQ dead-letter routing to DLQ.
- Redelivery recovery after network drops during ACK.

---

## 13. Deferred Task 1G.4C2 Work

The following operational runner and topology components remain deferred to Task 1G.4C2:
- RabbitMQ connection lifecycle management (`connect_robust`, reconnect backoff).
- Channel management and consumer channel prefetch QoS (`basic_qos`).
- Exchange declaration (`e7.events`), queue declaration, dead-letter exchange (DLX) and queue (DLQ) topology bindings.
- Consumer subscription (`queue.consume(handler.handle)`).
- Consumer worker runner, concurrency bounds, fail-fast supervision, and graceful shutdown signal handling.
