"""Unit of Work pattern and explicit transaction management foundation.

Part of Phase 1 Foundation Task 1E.
Provides provider-neutral UnitOfWork protocol, concrete SqlAlchemyUnitOfWork implementation,
explicit commit/rollback semantics, deterministic lifecycle state validation, and factory creation.
"""

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class UnitOfWorkState(StrEnum):
    """Deterministic lifecycle state for a Unit of Work instance.

    Transitions:
        NEW -> ACTIVE (upon __aenter__)
        ACTIVE -> COMMITTED (upon explicit commit)
        ACTIVE -> ROLLED_BACK (upon explicit rollback, uncommitted exit, or error)
        ACTIVE -> FAILED (upon commit failure or rollback failure)
        COMMITTED -> CLOSED (upon __aexit__)
        ROLLED_BACK -> CLOSED (upon __aexit__)
        FAILED -> CLOSED (upon __aexit__)
    """

    NEW = "NEW"
    ACTIVE = "ACTIVE"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


def _add_safe_cleanup_note(
    exc: BaseException,
    operation: str,
    cleanup_exc: BaseException,
) -> None:
    """Safely annotate an exception with sanitized secondary cleanup failure info.

    Never includes raw database error strings, SQL queries, row data, or credentials.
    Only records the operation name and the exception type name.
    """
    if hasattr(exc, "add_note"):
        exc.add_note(
            f"Secondary UnitOfWork session {operation} cleanup failed: {type(cleanup_exc).__name__}"
        )


class UnitOfWorkError(Exception):
    """Base exception for all Unit of Work infrastructure and lifecycle errors."""


class UnitOfWorkStateError(UnitOfWorkError):
    """Raised when an illegal lifecycle state transition or operation is attempted."""


class UnitOfWorkNotActiveError(UnitOfWorkStateError):
    """Raised when operating on or accessing the session before entering the Unit of Work."""


class UnitOfWorkClosedError(UnitOfWorkStateError):
    """Raised when attempting to reuse or access a closed Unit of Work."""


@runtime_checkable
class UnitOfWork(Protocol):
    """Provider-neutral transaction boundary and unit-of-work contract.

    Guarantees explicit transaction ownership where commit and rollback are managed
    by the Unit of Work, keeping repositories decoupled from transaction boundaries.
    """

    async def __aenter__(self) -> "UnitOfWork":
        """Enter transaction context, preparing transactional resources."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit transaction context, enforcing rollback on uncommitted or failed operations."""
        ...

    async def commit(self) -> None:
        """Explicitly commit the active transaction."""
        ...

    async def rollback(self) -> None:
        """Explicitly roll back the active transaction."""
        ...


class SqlAlchemyUnitOfWork:
    """SQLAlchemy 2.x asynchronous Unit of Work implementation.

    Invariants:
    - Receives async_sessionmaker via dependency injection; never creates an AsyncEngine.
    - One Unit of Work owns exactly one fresh AsyncSession created on __aenter__.
    - One-shot lifecycle: cannot be reused after closure.
    - Explicit-commit policy: exiting without calling commit() always triggers rollback.
    - Rollback on exception: errors inside context roll back uncommitted work and propagate.
    - Guaranteed cleanup: session.close() is always executed in a finally block.
    - Never shares a global mutable session across separate UoW instances.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        if session_factory is None:
            raise ValueError("SqlAlchemyUnitOfWork requires an async_sessionmaker instance.")
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._state: UnitOfWorkState = UnitOfWorkState.NEW

    @property
    def state(self) -> UnitOfWorkState:
        """Return the current lifecycle state of this Unit of Work."""
        return self._state

    @property
    def session(self) -> AsyncSession:
        """Return the bound AsyncSession for the active transaction.

        Available ONLY while the Unit of Work is in the ACTIVE state.
        All other states (NEW, COMMITTED, ROLLED_BACK, FAILED, CLOSED) reject session access.

        Raises:
            UnitOfWorkNotActiveError: If accessed in NEW state (before __aenter__).
            UnitOfWorkStateError: If accessed in COMMITTED, ROLLED_BACK, or FAILED state.
            UnitOfWorkClosedError: If accessed in CLOSED state (after __aexit__).
        """
        if self._state == UnitOfWorkState.NEW:
            raise UnitOfWorkNotActiveError(
                "Cannot access session: Unit of Work has not been entered."
            )
        if self._state == UnitOfWorkState.COMMITTED:
            raise UnitOfWorkStateError(
                "Cannot access session: Unit of Work transaction has already been committed."
            )
        if self._state == UnitOfWorkState.ROLLED_BACK:
            raise UnitOfWorkStateError(
                "Cannot access session: Unit of Work transaction has already been rolled back."
            )
        if self._state == UnitOfWorkState.FAILED:
            raise UnitOfWorkStateError(
                "Cannot access session: Unit of Work transaction has failed."
            )
        if self._state == UnitOfWorkState.CLOSED or self._session is None:
            raise UnitOfWorkClosedError("Cannot access session: Unit of Work is closed.")
        return self._session

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        """Begin transaction scope, creating a fresh AsyncSession.

        Raises:
            UnitOfWorkClosedError: If attempting to reuse a closed UoW.
            UnitOfWorkStateError: If re-entering an already active UoW or unexpected state.
        """
        if self._state == UnitOfWorkState.CLOSED:
            raise UnitOfWorkClosedError(
                "Cannot reuse a closed Unit of Work. Obtain a fresh instance from the factory."
            )
        if self._state == UnitOfWorkState.ACTIVE:
            raise UnitOfWorkStateError(
                "Cannot re-enter an already active Unit of Work. "
                "Nested transactions are unsupported."
            )
        if self._state != UnitOfWorkState.NEW:
            raise UnitOfWorkStateError(f"Cannot enter Unit of Work in state '{self._state.value}'.")

        self._session = self._session_factory()
        self._state = UnitOfWorkState.ACTIVE
        return self

    async def commit(self) -> None:
        """Explicitly commit the active transaction.

        Raises:
            UnitOfWorkNotActiveError: If commit() is called before __aenter__.
            UnitOfWorkStateError: If already committed, rolled back, failed, or in an invalid state.
            UnitOfWorkClosedError: If commit() is called after closure.
        """
        if self._state == UnitOfWorkState.NEW:
            raise UnitOfWorkNotActiveError("Cannot commit: Unit of Work has not been entered.")
        if self._state == UnitOfWorkState.COMMITTED:
            raise UnitOfWorkStateError("Cannot commit: transaction has already been committed.")
        if self._state == UnitOfWorkState.ROLLED_BACK:
            raise UnitOfWorkStateError("Cannot commit: transaction has already been rolled back.")
        if self._state == UnitOfWorkState.FAILED:
            raise UnitOfWorkStateError("Cannot commit: transaction has failed.")
        if self._state == UnitOfWorkState.CLOSED:
            raise UnitOfWorkClosedError("Cannot commit: Unit of Work is closed.")
        if self._state != UnitOfWorkState.ACTIVE or self._session is None:
            raise UnitOfWorkStateError(f"Cannot commit in state '{self._state.value}'.")

        try:
            await self._session.commit()
            self._state = UnitOfWorkState.COMMITTED
        except Exception as commit_exc:
            self._state = UnitOfWorkState.FAILED
            try:
                await self._session.rollback()
            except Exception as rb_exc:
                _add_safe_cleanup_note(commit_exc, "rollback", rb_exc)
            raise

    async def rollback(self) -> None:
        """Explicitly roll back the active transaction.

        Raises:
            UnitOfWorkNotActiveError: If rollback() is called before __aenter__.
            UnitOfWorkStateError: If already committed, rolled back, failed, or in an invalid state.
            UnitOfWorkClosedError: If rollback() is called after closure.
        """
        if self._state == UnitOfWorkState.NEW:
            raise UnitOfWorkNotActiveError("Cannot rollback: Unit of Work has not been entered.")
        if self._state == UnitOfWorkState.COMMITTED:
            raise UnitOfWorkStateError("Cannot rollback: transaction has already been committed.")
        if self._state == UnitOfWorkState.ROLLED_BACK:
            raise UnitOfWorkStateError("Cannot rollback: transaction has already been rolled back.")
        if self._state == UnitOfWorkState.FAILED:
            raise UnitOfWorkStateError("Cannot rollback: transaction has failed.")
        if self._state == UnitOfWorkState.CLOSED:
            raise UnitOfWorkClosedError("Cannot rollback: Unit of Work is closed.")
        if self._state != UnitOfWorkState.ACTIVE or self._session is None:
            raise UnitOfWorkStateError(f"Cannot rollback in state '{self._state.value}'.")

        try:
            await self._session.rollback()
            self._state = UnitOfWorkState.ROLLED_BACK
        except Exception:
            self._state = UnitOfWorkState.FAILED
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Finalize transaction scope.

        Enforces:
        - If an application or commit exception occurred: roll back active work and propagate
          the original exception without masking it with secondary cleanup failures.
        - If no exception occurred but commit() was never called: roll back uncommitted work.
        - If no primary exception occurred and cleanup (close) fails: propagate cleanup failure.
        - Always close the active session in a finally block exactly once and transition to CLOSED.
        """
        cleanup_exc: BaseException | None = None

        try:
            if exc_val is not None:
                # An exception is propagating out of the context block
                if self._state == UnitOfWorkState.ACTIVE and self._session is not None:
                    try:
                        await self._session.rollback()
                        self._state = UnitOfWorkState.ROLLED_BACK
                    except Exception as rb_exc:
                        self._state = UnitOfWorkState.FAILED
                        _add_safe_cleanup_note(exc_val, "rollback", rb_exc)
            else:
                # Clean exit without exception: enforce explicit commit policy
                if self._state == UnitOfWorkState.ACTIVE:
                    if self._session is not None:
                        try:
                            await self._session.rollback()
                            self._state = UnitOfWorkState.ROLLED_BACK
                        except Exception as rb_exc:
                            self._state = UnitOfWorkState.FAILED
                            cleanup_exc = rb_exc
        finally:
            # Deterministic session closure: close session exactly once
            if self._session is not None:
                session_to_close = self._session
                self._session = None
                try:
                    await session_to_close.close()
                except Exception as close_exc:
                    if exc_val is None and cleanup_exc is None:
                        cleanup_exc = close_exc
                    elif exc_val is not None:
                        _add_safe_cleanup_note(exc_val, "close", close_exc)
                    elif cleanup_exc is not None:
                        _add_safe_cleanup_note(cleanup_exc, "close", close_exc)
            self._state = UnitOfWorkState.CLOSED

        if exc_val is None and cleanup_exc is not None:
            raise cleanup_exc


class SqlAlchemyUnitOfWorkFactory:
    """Factory creating fresh, independent SqlAlchemyUnitOfWork instances."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        if session_factory is None:
            raise ValueError("SqlAlchemyUnitOfWorkFactory requires an async_sessionmaker instance.")
        self._session_factory = session_factory

    def create(self) -> SqlAlchemyUnitOfWork:
        """Create and return a new, independent SqlAlchemyUnitOfWork instance."""
        return SqlAlchemyUnitOfWork(self._session_factory)

    def __call__(self) -> SqlAlchemyUnitOfWork:
        """Callable shorthand to create a fresh SqlAlchemyUnitOfWork instance."""
        return self.create()


def create_unit_of_work_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyUnitOfWorkFactory:
    """Create a SqlAlchemyUnitOfWorkFactory bound to the given async_sessionmaker."""
    return SqlAlchemyUnitOfWorkFactory(session_factory)
