"""Immutable value objects for outbox event dispatch and claim lifecycle.

Part of Phase 1 Foundation Task 1G.3B1.
Defines ClaimedOutboxEvent, an immutable provider-neutral result object representing
an event claimed for dispatch by an operational worker.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.events.schemas import EventEnvelope


class ClaimedOutboxEvent(BaseModel):
    """Immutable provider-neutral representation of a claimed outbox event.

    Part of Phase 1 Foundation Task 1G.3B1.
    Decouples dispatcher orchestration from SQLAlchemy ORM records.
    Contains strictly the operational metadata and canonical envelope required
    for network publication and subsequent claim settlement.

    Invariants:
    - Immutable (frozen=True, extra="forbid").
    - event_id: Canonical UUID identifying the event.
    - envelope: Canonical, immutable, validated EventEnvelope.
    - claim_token: Opaque infrastructure UUID establishing lease ownership.
    - dispatch_attempt_count: Monotonically increasing attempt counter (>= 1 upon claim).
    - Contains zero ORM session or mutable database record references.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    envelope: EventEnvelope
    claim_token: UUID
    dispatch_attempt_count: int
