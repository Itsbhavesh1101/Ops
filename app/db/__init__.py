"""PostgreSQL persistence foundation package for the IR Infotech E-7 Operations Engine.

Part of Phase 1 Foundation Task 1D.
Provides async engine, session factory, declarative base, and health primitives.
"""

from app.db.base import POSTGRESQL_NAMING_CONVENTION, Base
from app.db.engine import (
    DatabaseConfigurationError,
    DatabaseManager,
    DatabaseUnavailableError,
    create_database_engine,
    validate_postgresql_async_url,
)
from app.db.health import check_database_ready
from app.db.repositories.base import Repository, SqlAlchemyRepository
from app.db.session import create_session_factory, get_db_session, get_session
from app.db.uow import (
    SqlAlchemyUnitOfWork,
    SqlAlchemyUnitOfWorkFactory,
    UnitOfWork,
    UnitOfWorkClosedError,
    UnitOfWorkError,
    UnitOfWorkNotActiveError,
    UnitOfWorkState,
    UnitOfWorkStateError,
    create_unit_of_work_factory,
)

__all__ = [
    "Base",
    "DatabaseConfigurationError",
    "DatabaseManager",
    "DatabaseUnavailableError",
    "POSTGRESQL_NAMING_CONVENTION",
    "Repository",
    "SqlAlchemyRepository",
    "SqlAlchemyUnitOfWork",
    "SqlAlchemyUnitOfWorkFactory",
    "UnitOfWork",
    "UnitOfWorkClosedError",
    "UnitOfWorkError",
    "UnitOfWorkNotActiveError",
    "UnitOfWorkState",
    "UnitOfWorkStateError",
    "check_database_ready",
    "create_database_engine",
    "create_session_factory",
    "create_unit_of_work_factory",
    "get_db_session",
    "get_session",
    "validate_postgresql_async_url",
]
