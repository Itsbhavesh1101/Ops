"""Repository for consumer-scoped transactional inbox deduplication.

Part of Phase 1 Foundation Task 1G.4A.
Adheres strictly to the repository and Unit of Work foundation:
- Externally supplied AsyncSession from Unit of Work.
- Shared transaction: executes within the caller's active business transaction.
- Atomic deduplication: PostgreSQL INSERT ... ON CONFLICT DO NOTHING RETURNING.
- Zero SELECT-before-INSERT to prevent concurrency races.
- Provisional True result: marker is durable only when the surrounding UnitOfWork commits.
- Duplicate False result: caller skips duplicate processing without raising exceptions.
- Never commits, rolls back, or closes the session.
- Zero network I/O, zero broker message consumption, zero message acknowledgements.
"""

import re
from typing import Final

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.repositories.base import SqlAlchemyRepository
from app.events.inbox.models import InboxMessageRecord
from app.events.schemas import EventEnvelope

# Canonical namespaced consumer identifier bounds
CONSUMER_NAME_MAX_LENGTH: Final[int] = 128
CONSUMER_NAME_REGEX: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def validate_consumer_name(consumer_name: str) -> str:
    """Validate that consumer_name adheres to canonical namespaced formatting rules.

    Rules:
    - Must be a non-empty string.
    - Length bounded to CONSUMER_NAME_MAX_LENGTH (128 characters).
    - Must match regex '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$'.
    - Must have at least two dot-separated segments.
    - Lowercase alphanumeric and underscores only.
    - No leading, trailing, or consecutive dots.
    - No whitespace or uppercase characters.

    Args:
        consumer_name: Logical consumer identifier string.

    Returns:
        str: The validated canonical consumer_name.

    Raises:
        TypeError: If consumer_name is not a string.
        ValueError: If consumer_name is empty, oversized, or violates naming format.
    """
    if not isinstance(consumer_name, str):
        raise TypeError(f"consumer_name must be a str, got '{type(consumer_name).__name__}'")
    if not consumer_name or len(consumer_name) > CONSUMER_NAME_MAX_LENGTH:
        raise ValueError(
            f"consumer_name must be between 1 and {CONSUMER_NAME_MAX_LENGTH} characters, "
            f"got length {len(consumer_name)}"
        )
    if not CONSUMER_NAME_REGEX.match(consumer_name):
        raise ValueError(
            f"consumer_name '{consumer_name}' is invalid. Must match canonical namespaced format "
            f"'^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$' (e.g. 'workflow.projection')."
        )
    return consumer_name


class InboxRepository(SqlAlchemyRepository):
    """Repository managing consumer-scoped transactional event deduplication."""

    async def try_record(
        self,
        *,
        consumer_name: str,
        envelope: EventEnvelope,
    ) -> bool:
        """Attempt to atomically record an incoming event for a logical consumer.

        Uses PostgreSQL INSERT ... ON CONFLICT (consumer_name, event_id) DO NOTHING RETURNING
        to determine first-processing vs duplicate status atomically without read-then-write
        races.

        Transaction Semantics:
        - A return value of True indicates this transaction acquired the first-processing
          deduplication marker. This outcome is PROVISIONAL until the surrounding UnitOfWork
          commits. If downstream business processing raises an exception and the UnitOfWork
          rolls back, this inbox marker also rolls back, allowing subsequent redelivery
          attempts to re-process the event.
        - A return value of False indicates this (consumer_name, event_id) pair was already
          durably recorded by a previously committed transaction. The caller must treat
          the event as a duplicate and skip redundant business mutations without raising an error.

        Invariants:
        - Never generates or derives UUIDs (copies envelope.event_id unconditionally).
        - Executes within the caller's externally managed AsyncSession.
        - Does NOT commit, rollback, or close the session.
        - Does NOT invoke business handlers or callback functions.
        - Zero message broker transport or acknowledgement logic.

        Args:
            consumer_name: Logical consumer identifier string.
            envelope: Validated, immutable canonical EventEnvelope.

        Returns:
            bool: True if this invocation successfully inserted the deduplication marker
                  (provisional first processing), False if a conflicting record already exists.

        Raises:
            TypeError: If consumer_name is not str or envelope is not EventEnvelope.
            ValueError: If consumer_name violates canonical naming format.
        """
        valid_consumer_name = validate_consumer_name(consumer_name)
        if not isinstance(envelope, EventEnvelope):
            raise TypeError(
                f"InboxRepository.try_record requires an EventEnvelope instance, "
                f"got '{type(envelope).__name__}'"
            )

        stmt = (
            pg_insert(InboxMessageRecord)
            .values(
                consumer_name=valid_consumer_name,
                event_id=envelope.event_id,
            )
            .on_conflict_do_nothing(
                index_elements=["consumer_name", "event_id"],
            )
            .returning(InboxMessageRecord.event_id)
        )

        result = await self._session.execute(stmt)
        inserted_id = result.scalar_one_or_none()
        return inserted_id is not None
