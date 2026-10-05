"""Repository implementation for durable staging of canonical event envelopes.

Part of Phase 1 Foundation Task 1G.2.
Adheres strictly to the repository and Unit of Work foundation:
- Externally supplied AsyncSession from Unit of Work.
- No transaction ownership: never commits, rolls back, or closes session.
- Staging-only: exposes ONLY enqueue().
- Returns canonical UUID (event_id) rather than exposing mutable ORM records.
- Flush policy: does NOT flush by default; relies on Unit of Work transaction demarcation.
- Zero network I/O, zero message publication, zero background tasks.
"""

from uuid import UUID

from app.db.repositories.base import SqlAlchemyRepository
from app.events.outbox.models import OutboxEventRecord
from app.events.schemas import EventEnvelope


class OutboxRepository(SqlAlchemyRepository):
    """Repository for staging canonical event envelopes in the transactional outbox."""

    async def enqueue(self, envelope: EventEnvelope) -> UUID:
        """Stage a canonical EventEnvelope for durable outbox persistence.

        Extracts canonical event_id and JSON-serialized envelope, creates an
        OutboxEventRecord, and stages it with session.add().

        Invariants:
        - Never generates a new event UUID (copies envelope.event_id unconditionally).
        - Stores the complete canonical envelope in JSONB via model_dump(mode="json").
        - published_at remains None (pending publication).
        - Does NOT flush, commit, rollback, or close the session.
        - Transaction ownership is retained strictly by the enclosing UnitOfWork.
        - Zero network I/O or broker publication.

        Args:
            envelope: Validated, immutable canonical EventEnvelope.

        Returns:
            UUID: The canonical event_id of the staged outbox record.

        Raises:
            TypeError: If envelope is not an instance of EventEnvelope.
        """
        if not isinstance(envelope, EventEnvelope):
            raise TypeError(
                f"OutboxRepository.enqueue requires an EventEnvelope instance, "
                f"got '{type(envelope).__name__}'"
            )

        canonical_json = envelope.model_dump(mode="json")
        record = OutboxEventRecord(
            event_id=envelope.event_id,
            envelope=canonical_json,
            published_at=None,
        )
        self.session.add(record)
        return envelope.event_id
