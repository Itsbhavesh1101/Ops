"""Deterministic JSON serialization boundary for canonical event envelopes.

Part of Phase 1 Foundation Task 1G.1.
Suitable for transactional Outbox storage and RabbitMQ publication.
"""

import json

from app.events.schemas import EventEnvelope


def serialize_event_envelope(envelope: EventEnvelope) -> str:
    """Serialize an EventEnvelope to a deterministic, compact, UTF-8 JSON string.

    Guarantees:
    - Standard JSON compliance (allow_nan=False).
    - Deterministic key ordering (sort_keys=True).
    - Compact formatting without superfluous whitespace (separators=(',', ':')).
    - Canonical string representations for UUID and ISO-8601 UTC datetimes.
    - Zero mutation of the source envelope.
    """
    dumped = envelope.model_dump(mode="json")
    return json.dumps(
        dumped,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
