"""Repository implementation for transactional outbox dispatch state transitions.

Part of Phase 1 Foundation Task 1G.3B1.
Adheres strictly to the repository and Unit of Work foundation:
- Externally supplied AsyncSession from Unit of Work.
- No transaction ownership: never commits, rolls back, or closes session.
- Dispatch-state transitions only: claim_ready, mark_published, reschedule_failure.
- Bounded concurrency with PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED.
- Database clock authority: all timestamps (claimed_at, published_at, next_attempt_at)
  are authored by PostgreSQL expressions (func.now()), never application clocks.
- Stale worker protection: conditional UPDATE statements verify active claim_token
  and published_at IS NULL, raising OutboxClaimLostError if rowcount == 0.
- Zero network I/O, zero message publication, zero background tasks, zero retry policy.
"""

from collections.abc import Callable
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, null, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import SqlAlchemyRepository
from app.events.outbox.dispatch import ClaimedOutboxEvent
from app.events.outbox.models import OutboxEventRecord
from app.events.schemas import EventEnvelope


class OutboxClaimLostError(Exception):
    """Raised when an outbox event claim lease has been lost, expired, or superseded."""


class OutboxDispatchRepository(SqlAlchemyRepository):
    """Repository managing dispatch claims, publication confirmations, and failure rescheduling."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        token_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        """Initialize OutboxDispatchRepository with session and optional token factory.

        Args:
            session: Externally managed AsyncSession from UnitOfWork.
            token_factory: Callable producing fresh UUID4 claim ownership tokens.
        """
        super().__init__(session)
        self._token_factory = token_factory

    async def claim_ready(
        self,
        *,
        limit: int,
        lease_duration: timedelta,
        event_id: UUID | None = None,
    ) -> tuple[ClaimedOutboxEvent, ...]:
        """Claim a batch of eligible outbox events for dispatch.

        Eligibility criteria:
        1. published_at IS NULL (event not yet published).
        2. next_attempt_at <= database current timestamp (eligible for dispatch).
        3. claimed_at IS NULL OR claimed_at <= database current timestamp - lease_duration
           (unclaimed or lease expired).
        4. event_id == event_id (optional filter when targeting a specific event).

        Concurrency semantics:
        - Uses SELECT ... FOR UPDATE SKIP LOCKED to prevent blocking or contention
          between concurrent dispatcher workers.
        - Deterministic ordering: next_attempt_at ASC, created_at ASC, event_id ASC.
        - Bounded by caller-provided limit.

        State transition:
        - claim_token = fresh UUID4.
        - claimed_at = PostgreSQL current timestamp (func.now()).
        - dispatch_attempt_count = dispatch_attempt_count + 1.
        - Immutable event content and next_attempt_at remain unaltered.

        Args:
            limit: Maximum number of events to claim. Must be positive (> 0).
            lease_duration: Duration after which an uncommitted or unfinished claim
                is considered stale and eligible for reclaim. Must be positive (> 0s).
            event_id: Optional UUID of a specific outbox event to target for claim.

        Returns:
            tuple[ClaimedOutboxEvent, ...]: Immutable claimed event value objects.

        Raises:
            ValueError: If limit <= 0 or lease_duration <= 0.
            TypeError: If limit is not an int or lease_duration is not a timedelta.
        """
        if not isinstance(limit, int):
            raise TypeError(f"limit must be an integer, got '{type(limit).__name__}'")
        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")

        if not isinstance(lease_duration, timedelta):
            raise TypeError(
                f"lease_duration must be a timedelta, got '{type(lease_duration).__name__}'"
            )
        if lease_duration.total_seconds() <= 0:
            raise ValueError(f"lease_duration must be strictly positive, got {lease_duration}")

        db_now = func.now()
        stale_threshold = db_now - lease_duration

        conditions = and_(
            OutboxEventRecord.published_at.is_(None),
            OutboxEventRecord.next_attempt_at <= db_now,
            or_(
                OutboxEventRecord.claimed_at.is_(None),
                OutboxEventRecord.claimed_at <= stale_threshold,
            ),
        )
        if event_id is not None:
            conditions = and_(conditions, OutboxEventRecord.event_id == event_id)

        stmt = (
            select(OutboxEventRecord)
            .where(conditions)
            .order_by(
                OutboxEventRecord.next_attempt_at.asc(),
                OutboxEventRecord.created_at.asc(),
                OutboxEventRecord.event_id.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

        result = await self.session.execute(stmt)
        records = result.scalars().all()

        claimed_events: list[ClaimedOutboxEvent] = []
        for record in records:
            claim_token = self._token_factory()
            record.claim_token = claim_token
            record.claimed_at = db_now
            record.dispatch_attempt_count = record.dispatch_attempt_count + 1

            envelope = (
                record.envelope
                if isinstance(record.envelope, EventEnvelope)
                else EventEnvelope.model_validate(record.envelope)
            )

            claimed_events.append(
                ClaimedOutboxEvent(
                    event_id=record.event_id,
                    envelope=envelope,
                    claim_token=claim_token,
                    dispatch_attempt_count=record.dispatch_attempt_count,
                )
            )

        return tuple(claimed_events)

    async def mark_published(
        self,
        *,
        event_id: UUID,
        claim_token: UUID,
    ) -> None:
        """Mark an outbox event as successfully published and clear its claim lease.

        State transition:
        - published_at = PostgreSQL current timestamp (func.now()).
        - claim_token = NULL.
        - claimed_at = NULL.

        Safety:
        - Conditional UPDATE requiring matching event_id, active claim_token, and
          published_at IS NULL.
        - If rowcount == 0, the claim was lost (reclaimed due to lease expiry or
          already published), raising OutboxClaimLostError.

        Args:
            event_id: Canonical event UUID.
            claim_token: The claim token issued during claim_ready.

        Raises:
            OutboxClaimLostError: If the claim is no longer active or event is already published.
            TypeError: If event_id or claim_token is not a UUID.
        """
        if not isinstance(event_id, UUID):
            raise TypeError(f"event_id must be a UUID, got '{type(event_id).__name__}'")
        if not isinstance(claim_token, UUID):
            raise TypeError(f"claim_token must be a UUID, got '{type(claim_token).__name__}'")

        stmt = (
            update(OutboxEventRecord)
            .where(
                OutboxEventRecord.event_id == event_id,
                OutboxEventRecord.claim_token == claim_token,
                OutboxEventRecord.published_at.is_(None),
            )
            .values(
                published_at=func.now(),
                claim_token=null(),
                claimed_at=null(),
            )
        )
        result = await self.session.execute(stmt)
        rowcount = getattr(result, "rowcount", 0)
        if rowcount == 0:
            raise OutboxClaimLostError(
                f"Failed to mark outbox event '{event_id}' as published: "
                f"claim token '{claim_token}' is no longer active, the lease expired, "
                f"or the event was already published."
            )

    async def reschedule_failure(
        self,
        *,
        event_id: UUID,
        claim_token: UUID,
        retry_delay: timedelta,
    ) -> None:
        """Reschedule an outbox event after a failed publication attempt and release its lease.

        State transition:
        - next_attempt_at = PostgreSQL current timestamp (func.now()) + retry_delay.
        - claim_token = NULL.
        - claimed_at = NULL.
        - published_at remains NULL.
        - dispatch_attempt_count remains unchanged (not decremented).

        Safety:
        - Conditional UPDATE requiring matching event_id, active claim_token, and
          published_at IS NULL.
        - If rowcount == 0, the claim was lost (reclaimed or already published),
          raising OutboxClaimLostError.

        Args:
            event_id: Canonical event UUID.
            claim_token: The claim token issued during claim_ready.
            retry_delay: Non-negative timedelta until the next dispatch attempt.

        Raises:
            OutboxClaimLostError: If the claim is no longer active or event is already published.
            ValueError: If retry_delay is negative (< 0s).
            TypeError: If event_id or claim_token is not a UUID, or retry_delay is not a timedelta.
        """
        if not isinstance(event_id, UUID):
            raise TypeError(f"event_id must be a UUID, got '{type(event_id).__name__}'")
        if not isinstance(claim_token, UUID):
            raise TypeError(f"claim_token must be a UUID, got '{type(claim_token).__name__}'")
        if not isinstance(retry_delay, timedelta):
            raise TypeError(f"retry_delay must be a timedelta, got '{type(retry_delay).__name__}'")
        if retry_delay.total_seconds() < 0:
            raise ValueError(f"retry_delay must be non-negative, got {retry_delay}")

        stmt = (
            update(OutboxEventRecord)
            .where(
                OutboxEventRecord.event_id == event_id,
                OutboxEventRecord.claim_token == claim_token,
                OutboxEventRecord.published_at.is_(None),
            )
            .values(
                next_attempt_at=func.now() + retry_delay,
                claim_token=null(),
                claimed_at=null(),
            )
        )

        result = await self.session.execute(stmt)
        rowcount = getattr(result, "rowcount", 0)
        if rowcount == 0:
            raise OutboxClaimLostError(
                f"Failed to reschedule outbox event '{event_id}': "
                f"claim token '{claim_token}' is no longer active, the lease expired, "
                f"or the event was already published."
            )
