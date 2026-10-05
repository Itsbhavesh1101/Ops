"""Provider-neutral idempotent mutation execution orchestration.

Part of Phase 1 Foundation Task 1H.3.
Orchestrates tenant-scoped transactional idempotency marker acquisition and
protected business mutation execution:
- Exactly one UnitOfWork transaction per execution invocation.
- Insert-first provisional marker acquisition: IdempotencyRepository.acquire_or_observe
  executes in the active UnitOfWork session.
- ACQUIRED flow: protected handler executes in the exact same AsyncSession ->
  uow.commit() atomically commits both marker and business mutation ->
  uow exits -> returns EXECUTED with handler return value.
- Provisional semantics: handler failure or commit failure rolls back both the business
  mutation and the idempotency marker together.
- EXISTING_MATCH flow: business handler is skipped -> uow transaction completes cleanly ->
  uow exits -> returns ALREADY_PROCESSED (value is None).
- EXISTING_CONFLICT flow: business handler is skipped -> database read transaction completes
  cleanly -> uow exits -> raises IdempotencyConflictError outside UnitOfWork context.
- Transactional Outbox compatibility: business mutation, idempotency marker, and optional
  transactional outbox staging share the exact same AsyncSession.
- Handlers MUST NOT perform irreversible external side effects directly (such as HTTP calls,
  emails, or broker publication). Irreversible external effects must be staged via the
  Transactional Outbox or deferred until post-commit.
- Zero HTTP framework, header extraction, response replay, or distributed locking machinery.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency.contracts import (
    IdempotencyIdentity,
    RequestFingerprint,
)
from app.core.idempotency.repository import (
    IdempotencyDecision,
    IdempotencyDecisionType,
    IdempotencyRepository,
)
from app.db.uow import UnitOfWork, UnitOfWorkError

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class IdempotentExecutionOutcome(StrEnum):
    """Outcome classification for provider-neutral idempotent mutation execution.

    Invariants:
    - EXECUTED: The mutation was executed for the first time and committed with its marker.
    - ALREADY_PROCESSED: A matching committed mutation was previously processed;
      handler was skipped.
    - Zero HTTP statuses, transport acknowledgements (ACK/NACK), or response replay flags.
    """

    EXECUTED = "EXECUTED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"


@dataclass(frozen=True, slots=True)
class IdempotentExecutionResult(Generic[T]):
    """Immutable result of idempotent mutation execution.

    Invariants:
    - outcome: IdempotentExecutionOutcome (EXECUTED or ALREADY_PROCESSED).
    - identity: Canonical IdempotencyIdentity provided by caller.
    - request_fingerprint: Canonical RequestFingerprint provided by caller.
    - value: Handler return value if outcome == EXECUTED; strictly None if ALREADY_PROCESSED.
    - Privacy: Contains zero raw client keys, HTTP responses, status codes, headers,
      or raw request bodies.
    - Absence of Prior Result Replay: ALREADY_PROCESSED means the mutation previously committed
      and must not be repeated. It does not replay prior HTTP or application responses.
    """

    outcome: IdempotentExecutionOutcome
    identity: IdempotencyIdentity
    request_fingerprint: RequestFingerprint
    value: T | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, IdempotentExecutionOutcome):
            raise TypeError(
                f"outcome must be an IdempotentExecutionOutcome instance, "
                f"got '{type(self.outcome).__name__}'"
            )
        if not isinstance(self.identity, IdempotencyIdentity):
            raise TypeError(
                f"identity must be an IdempotencyIdentity instance, "
                f"got '{type(self.identity).__name__}'"
            )
        if not isinstance(self.request_fingerprint, RequestFingerprint):
            raise TypeError(
                f"request_fingerprint must be a RequestFingerprint instance, "
                f"got '{type(self.request_fingerprint).__name__}'"
            )
        if self.outcome == IdempotentExecutionOutcome.ALREADY_PROCESSED and self.value is not None:
            raise ValueError(
                "value must be None when outcome is ALREADY_PROCESSED. "
                "Task 1H does not support response payload caching or replay."
            )


class IdempotencyConflictError(Exception):
    """Raised when an idempotency identity is reused with a conflicting request fingerprint.

    Indicates that a client submitted an idempotency key matching an existing committed
    mutation for the same tenant and logical operation, but with differing canonical
    request payload bytes.

    Invariants:
    - Exposes strictly canonical identity and digest fingerprints.
    - Never contains raw idempotency keys, raw request bodies, HTTP request objects,
      HTTP status codes, or credentials.
    """

    def __init__(
        self,
        *,
        identity: IdempotencyIdentity,
        request_fingerprint: RequestFingerprint,
        existing_fingerprint: RequestFingerprint,
    ) -> None:
        if not isinstance(identity, IdempotencyIdentity):
            raise TypeError(
                f"identity must be an IdempotencyIdentity instance, got '{type(identity).__name__}'"
            )
        if not isinstance(request_fingerprint, RequestFingerprint):
            raise TypeError(
                f"request_fingerprint must be a RequestFingerprint instance, "
                f"got '{type(request_fingerprint).__name__}'"
            )
        if not isinstance(existing_fingerprint, RequestFingerprint):
            raise TypeError(
                f"existing_fingerprint must be a RequestFingerprint instance, "
                f"got '{type(existing_fingerprint).__name__}'"
            )

        self.identity = identity
        self.request_fingerprint = request_fingerprint
        self.existing_fingerprint = existing_fingerprint

        super().__init__(
            f"Idempotency conflict for operation '{identity.operation_name}' "
            f"and digest '{identity.key_digest}': requested fingerprint "
            f"'{request_fingerprint}' does not match existing committed fingerprint "
            f"'{existing_fingerprint}'."
        )


@runtime_checkable
class IdempotentMutationHandler(Protocol[T_co]):
    """Protocol defining the protected business mutation handler interface.

    Contract:
    - Receives the transactional AsyncSession managed by the UnitOfWork.
    - Participates in the exact same transaction as the IdempotencyRepository.
    - MUST NOT commit, rollback, or close the provided AsyncSession.
    - MUST NOT execute irreversible external side effects directly (such as HTTP calls,
      email notifications, or direct RabbitMQ publication). Irreversible external effects
      must be staged via the Transactional Outbox (OutboxRepository(session)) or deferred
      until post-commit.
    - Transaction boundaries are owned strictly by IdempotentMutationExecutor and UnitOfWork.
    """

    async def execute(
        self,
        *,
        session: AsyncSession,
    ) -> T_co:
        """Execute protected business mutation within the supplied transactional session."""
        ...


class IdempotentMutationExecutor(Generic[T]):
    """Orchestrates transactional idempotent business mutation execution."""

    def __init__(
        self,
        *,
        uow_factory: (Callable[[], UnitOfWork] | Callable[[], Any]),
        handler: IdempotentMutationHandler[T],
    ) -> None:
        """Initialize IdempotentMutationExecutor with dependencies.

        Args:
            uow_factory: Callable producing a fresh UnitOfWork instance per execution.
            handler: Implementation of IdempotentMutationHandler protocol.

        Raises:
            TypeError: If uow_factory is not callable or handler does not satisfy
                       IdempotentMutationHandler protocol.
        """
        if not callable(uow_factory):
            raise TypeError("uow_factory must be a callable returning a UnitOfWork")
        self._uow_factory = uow_factory

        if not isinstance(handler, IdempotentMutationHandler):
            raise TypeError(
                f"handler must implement IdempotentMutationHandler protocol, "
                f"got '{type(handler).__name__}'"
            )
        self._handler = handler

    async def execute(
        self,
        *,
        identity: IdempotencyIdentity,
        request_fingerprint: RequestFingerprint,
    ) -> IdempotentExecutionResult[T]:
        """Execute a protected business mutation idempotently within a single UnitOfWork.

        Execution guarantees:
        - Strict input validation before acquiring or entering the UnitOfWork.
        - Exactly ONE UnitOfWork is created and managed for the entire execution.
        - ACQUIRED flow:
            1. acquire_or_observe inserts provisional marker and returns ACQUIRED.
            2. handler.execute(session=session) executes in the exact same AsyncSession.
            3. Handler return value is retained in memory.
            4. uow.commit() atomically commits both the idempotency marker and business mutations.
            5. UnitOfWork context exits cleanly.
            6. IdempotentExecutionResult(outcome=EXECUTED, ..., value=handler_value) is returned.
        - EXISTING_MATCH flow:
            1. acquire_or_observe observes matching existing record and returns EXISTING_MATCH.
            2. Handler is NOT invoked (call count 0).
            3. uow.commit() cleanly completes the database transaction.
            4. UnitOfWork context exits cleanly.
            5. IdempotentExecutionResult(outcome=ALREADY_PROCESSED, ..., value=None) is returned.
        - EXISTING_CONFLICT flow:
            1. acquire_or_observe observes conflicting record and returns EXISTING_CONFLICT.
            2. Handler is NOT invoked (call count 0).
            3. uow.commit() cleanly completes the database read transaction.
            4. UnitOfWork context exits cleanly.
            5. IdempotencyConflictError is raised strictly outside the UnitOfWork context.
        - Handler failure:
            If handler raises an exception, the exception escapes without calling commit().
            The UnitOfWork context rolls back both the provisional idempotency marker and
            any uncommitted business mutations together.
        - Commit failure:
            If uow.commit() raises an exception, the exception propagates. No result is returned,
            the handler is not retried, and no semantic success/conflict masks the persistence
            error.
        - Cancellation:
            asyncio.CancelledError propagates cleanly without returning success or retrying.
        - No automatic retries:
            Infrastructure or application failures propagate directly to the caller.

        Args:
            identity: Validated, immutable canonical IdempotencyIdentity.
            request_fingerprint: Validated, immutable canonical RequestFingerprint.

        Returns:
            IdempotentExecutionResult[T]: Immutable result with outcome EXECUTED or
                ALREADY_PROCESSED.

        Raises:
            TypeError: If identity or request_fingerprint violate expected contract types.
            IdempotencyConflictError: If identity is reused with differing request fingerprint.
            Exception: Any business mutation or persistence exception propagates directly.
        """
        if not isinstance(identity, IdempotencyIdentity):
            raise TypeError(
                f"identity must be an IdempotencyIdentity instance, got '{type(identity).__name__}'"
            )
        if not isinstance(request_fingerprint, RequestFingerprint):
            raise TypeError(
                f"request_fingerprint must be a RequestFingerprint instance, "
                f"got '{type(request_fingerprint).__name__}'"
            )

        outcome: IdempotentExecutionOutcome | None = None
        handler_value: T | None = None
        conflict_decision: IdempotencyDecision | None = None

        async with self._uow_factory() as uow:
            session: Any = getattr(uow, "session", None)
            if session is None:
                raise UnitOfWorkError("UnitOfWork does not provide an active database session")

            idem_repo = IdempotencyRepository(session)
            decision = await idem_repo.acquire_or_observe(
                identity=identity,
                request_fingerprint=request_fingerprint,
            )

            if decision.decision == IdempotencyDecisionType.ACQUIRED:
                handler_value = await self._handler.execute(session=session)
                outcome = IdempotentExecutionOutcome.EXECUTED
                await uow.commit()

            elif decision.decision == IdempotencyDecisionType.EXISTING_MATCH:
                outcome = IdempotentExecutionOutcome.ALREADY_PROCESSED
                await uow.commit()

            elif decision.decision == IdempotencyDecisionType.EXISTING_CONFLICT:
                conflict_decision = decision
                await uow.commit()

            else:
                raise RuntimeError(f"Unexpected idempotency decision: '{decision.decision}'")

        # Post-UoW context exit boundary: raise semantic conflict strictly after UoW exit
        if conflict_decision is not None:
            assert conflict_decision.existing_fingerprint is not None
            raise IdempotencyConflictError(
                identity=identity,
                request_fingerprint=request_fingerprint,
                existing_fingerprint=conflict_decision.existing_fingerprint,
            )

        assert outcome is not None
        return IdempotentExecutionResult(
            outcome=outcome,
            identity=identity,
            request_fingerprint=request_fingerprint,
            value=handler_value,
        )
