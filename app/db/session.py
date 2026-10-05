"""SQLAlchemy async session factory and request-scoped session lifecycle primitives.

Part of Phase 1 Foundation Task 1D.
Session lifecycle only. Transaction orchestration and Unit of Work belong to Task 1E.
"""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

if TYPE_CHECKING:
    from app.db.engine import DatabaseManager


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create an async_sessionmaker bound to the given AsyncEngine.

    Configured with expire_on_commit=False and autoflush=False per SQLAlchemy 2.0 best practices.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db_session(
    database_manager: "DatabaseManager",
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a scoped AsyncSession lifecycle for FastAPI or background operations.

    Acquires a fresh session, yields it, and ensures closure in a finally block.
    Does NOT automatically commit transactions (Task 1E owns Unit of Work).
    Does NOT swallow exceptions.
    """
    async with database_manager.session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a scoped AsyncSession directly from an async_sessionmaker instance."""
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
