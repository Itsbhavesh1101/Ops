"""SQLAlchemy DeclarativeBase and metadata naming convention definition.

Part of Phase 1 Foundation Task 1D.
Registers zero business tables in Task 1D.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic naming convention for PostgreSQL constraints and indexes
POSTGRESQL_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base class for all canonical E-7 PostgreSQL entities.

    Task 1D contains zero business tables. Future entity models will inherit
    from this Base class with standardized naming conventions.
    """

    metadata = MetaData(naming_convention=POSTGRESQL_NAMING_CONVENTION)
