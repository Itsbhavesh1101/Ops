"""Provider-neutral consumer event processing orchestration.

Part of Phase 1 Foundation Task 1G.4B.
Orchestrates consumer-scoped transactional event deduplication and business handler execution:
- Exactly one UnitOfWork transaction per processing invocation.
- First delivery: Inbox try_record and business handler execute in the same AsyncSession.
- Provisional deduplication: handler failure rolls back both the business mutation
  and the Inbox marker.
- Duplicate delivery: business handler is skipped and the transaction is cleanly completed.
- Both PROCESSED and DUPLICATE outcomes are returned strictly after the UnitOfWork context exits.
- Zero message broker transport or acknowledgement logic.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.uow import UnitOfWork, UnitOfWorkError
from app.events.inbox.repository import InboxRepository, validate_consumer_name
from app.events.schemas import EventEnvelope


class ConsumerProcessingOutcome(StrEnum):
    """Outcome classification for provider-neutral consumer event processing."""

    PROCESSED = "PROCESSED"
    DUPLICATE = "DUPLICATE"


class ConsumerProcessingResult(BaseModel):
    """Immutable result of consumer event processing.

    Contains outcome classification, logical consumer name, and canonical event_id.
    Contains zero payloads, broker transport metadata, or raw exception traces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ConsumerProcessingOutcome
    consumer_name: str
    event_id: UUID


@runtime_checkable
class ConsumerEventHandler(Protocol):
    """Protocol defining the business event handler interface.

    Contract:
    - Receives the canonical EventEnvelope and the transactional AsyncSession.
    - Participates in the exact same transaction as the Inbox deduplication marker.
    - MUST NOT commit, rollback, or close the provided AsyncSession.
    - Transaction boundaries are owned strictly by ConsumerEventProcessor and UnitOfWork.
    """

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        """Handle business event within the supplied transactional session."""
        ...


class ConsumerEventProcessor:
    """Orchestrates consumer-scoped event deduplication and business handler execution."""

    def __init__(
        self,
        *,
        consumer_name: str,
        uow_factory: (Callable[[], UnitOfWork] | Callable[[], Any]),
        handler: ConsumerEventHandler,
    ) -> None:
        """Initialize ConsumerEventProcessor with dependencies.

        Args:
            consumer_name: Canonical namespaced logical consumer identifier.
            uow_factory: Callable producing a fresh UnitOfWork instance per processing run.
            handler: ConsumerEventHandler implementation.

        Raises:
            TypeError: If arguments do not conform to expected types/protocols.
            ValueError: If consumer_name violates canonical namespaced naming format.
        """
        self._consumer_name = validate_consumer_name(consumer_name)

        if not callable(uow_factory):
            raise TypeError("uow_factory must be a callable returning a UnitOfWork")
        self._uow_factory = uow_factory

        if not isinstance(handler, ConsumerEventHandler):
            raise TypeError(
                f"handler must implement ConsumerEventHandler protocol, "
                f"got '{type(handler).__name__}'"
            )
        self._handler = handler

    @property
    def consumer_name(self) -> str:
        """Return the bound logical consumer name."""
        return self._consumer_name

    async def process(self, envelope: EventEnvelope) -> ConsumerProcessingResult:
        """Process an incoming canonical EventEnvelope with transactional deduplication.

        Orchestration guarantees:
        - Exactly one UnitOfWork instance is created and managed.
        - First delivery: try_record inserts provisional dedupe marker -> handler executes ->
          uow.commit() commits both -> uow exits -> returns PROCESSED.
        - Duplicate delivery: try_record returns False -> handler skipped ->
          uow.commit() completes transaction -> uow exits -> returns DUPLICATE.
        - Handler failure: unhandled exception escapes handler -> uow rolls back both business
          mutation and provisional inbox marker -> exception propagates.
        - Commit failure: commit raises -> uow exits with error -> exception propagates.
        - Cancellation: asyncio.CancelledError propagates without being converted to success.

        Args:
            envelope: Validated, immutable canonical EventEnvelope.

        Returns:
            ConsumerProcessingResult: Immutable processing result with outcome
                PROCESSED or DUPLICATE.

        Raises:
            TypeError: If envelope is not an EventEnvelope instance.
            Exception: Any database or business handler exception propagates without
                being swallowed.
        """
        if not isinstance(envelope, EventEnvelope):
            raise TypeError(
                f"ConsumerEventProcessor.process requires an EventEnvelope instance, "
                f"got '{type(envelope).__name__}'"
            )

        outcome: ConsumerProcessingOutcome

        async with self._uow_factory() as uow:
            session: Any = getattr(uow, "session", None)
            if session is None:
                raise UnitOfWorkError("UnitOfWork does not provide an active database session")

            inbox_repo = InboxRepository(session)
            first_processing = await inbox_repo.try_record(
                consumer_name=self._consumer_name,
                envelope=envelope,
            )

            if first_processing:
                await self._handler.handle(
                    envelope=envelope,
                    session=session,
                )
                outcome = ConsumerProcessingOutcome.PROCESSED
            else:
                outcome = ConsumerProcessingOutcome.DUPLICATE

            await uow.commit()

        # UoW context has cleanly exited and transaction is committed
        return ConsumerProcessingResult(
            outcome=outcome,
            consumer_name=self._consumer_name,
            event_id=envelope.event_id,
        )
