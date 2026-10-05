"""Base repository abstractions and session-bound repository contracts.

Part of Phase 1 Foundation Task 1E.
Defines minimal repository contracts binding repositories to a Unit of Work's
AsyncSession without enforcing premature generic CRUD methods across future domains.
"""

from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class Repository(Protocol):
    """Protocol defining the minimal contract for a persistence repository.

    Guarantees that repositories expose access to their bound transactional session
    without enforcing universal CRUD methods (which prematurely constrain domain
    aggregate boundaries).
    """

    @property
    def session(self) -> AsyncSession:
        """Return the bound transactional AsyncSession."""
        ...


class SqlAlchemyRepository:
    """Base repository class bound to an externally managed SQLAlchemy AsyncSession.

    Invariants:
    - Receives an active AsyncSession supplied by the Unit of Work.
    - Never creates or owns an engine.
    - Never creates an independent session.
    - Never calls session.commit() or session.rollback() (owned strictly by UnitOfWork).
    - Never closes the session (owned strictly by UnitOfWork context exit).
    - Does NOT enforce mandatory generic CRUD operations.
    """

    def __init__(self, session: AsyncSession) -> None:
        if session is None:
            raise ValueError("SqlAlchemyRepository requires an active AsyncSession.")
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Return the bound AsyncSession."""
        return self._session
