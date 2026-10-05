"""Unit tests for Phase 1 Task 1E repository foundation and session ownership rules.

Verifies:
- Repository protocol conformance and runtime checkability.
- SqlAlchemyRepository initialization requires active AsyncSession.
- Repository receives and uses the AsyncSession supplied by Unit of Work.
- Multiple repositories created within the same UoW share the exact same session.
- Repository does NOT own commit, rollback, or close (no such methods on base).
- Repository base does NOT enforce generic CRUD methods (add, get, list, update, delete).
- Session closure is owned exclusively by Unit of Work, never the repository.
- Domainless test-local repository subclass demonstrates safe query execution without commit.
"""

from unittest.mock import AsyncMock

import pytest

from app.db.repositories.base import Repository, SqlAlchemyRepository
from app.db.uow import SqlAlchemyUnitOfWork


class MockSessionFactory:
    """Mock async_sessionmaker that returns fresh AsyncMock session doubles."""

    def __call__(self) -> AsyncMock:
        session = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.close = AsyncMock()
        session.execute = AsyncMock()
        return session


def test_repository_protocol_conformance() -> None:
    """Verify SqlAlchemyRepository conforms to the Repository protocol."""
    mock_session = AsyncMock()
    repo = SqlAlchemyRepository(mock_session)
    assert isinstance(repo, Repository)


def test_repository_requires_active_session() -> None:
    """Verify SqlAlchemyRepository constructor rejects None session."""
    with pytest.raises(ValueError, match="requires an active AsyncSession"):
        SqlAlchemyRepository(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_repository_shares_session_with_uow() -> None:
    """Verify repository sees exactly the same session object as the Unit of Work."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        repo = SqlAlchemyRepository(uow.session)
        assert repo.session is uow.session
        await uow.commit()


@pytest.mark.asyncio
async def test_multiple_repositories_share_same_uow_session() -> None:
    """Verify multiple repositories created within a single UoW share the identical session."""
    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        repo_a = SqlAlchemyRepository(uow.session)
        repo_b = SqlAlchemyRepository(uow.session)

        assert repo_a.session is repo_b.session
        assert repo_a.session is uow.session
        await uow.commit()


def test_repository_base_does_not_own_transaction_lifecycle() -> None:
    """Verify SqlAlchemyRepository does not expose commit, rollback, or close methods."""
    mock_session = AsyncMock()
    repo = SqlAlchemyRepository(mock_session)

    # Invariants: repositories DO NOT own transaction boundaries
    assert not hasattr(repo, "commit"), "Repository must not expose commit()"
    assert not hasattr(repo, "rollback"), "Repository must not expose rollback()"
    assert not hasattr(repo, "close"), "Repository must not expose close()"


def test_repository_base_does_not_enforce_generic_crud() -> None:
    """Verify SqlAlchemyRepository does not prescribe mandatory generic CRUD operations.

    Future domain repositories will define domain-specific methods (e.g. find_by_correlation_id).
    Mandatory CRUD on the base prematurely constrains aggregate design.
    """
    mock_session = AsyncMock()
    repo = SqlAlchemyRepository(mock_session)

    assert not hasattr(repo, "add"), "Repository base must not prescribe generic add()"
    assert not hasattr(repo, "get"), "Repository base must not prescribe generic get()"
    assert not hasattr(repo, "list"), "Repository base must not prescribe generic list()"
    assert not hasattr(repo, "update"), "Repository base must not prescribe generic update()"
    assert not hasattr(repo, "delete"), "Repository base must not prescribe generic delete()"


@pytest.mark.asyncio
async def test_domainless_subclass_operates_without_committing() -> None:
    """Verify a domainless test subclass can execute operations via session without committing."""

    class SyntheticProbeRepository(SqlAlchemyRepository):
        async def execute_probe(self) -> str:
            await self.session.execute("SELECT 1")
            return "probe_ok"

    factory = MockSessionFactory()
    uow = SqlAlchemyUnitOfWork(factory)  # type: ignore[arg-type]

    async with uow:
        session = uow.session
        repo = SyntheticProbeRepository(session)
        result = await repo.execute_probe()
        assert result == "probe_ok"

        # Session was used for query, but commit has NOT occurred yet
        assert session.execute.call_count == 1
        assert session.commit.call_count == 0

        # Unit of Work commits explicitly
        await uow.commit()
        assert session.commit.call_count == 1
