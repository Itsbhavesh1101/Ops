"""Live PostgreSQL Unit of Work transaction commit and rollback verification.

Part of Phase 1 Foundation Task 1J.2A.
Validates:
- Explicit SqlAlchemyUnitOfWork commit persists changes and is visible in fresh session
- Uncommitted exit from SqlAlchemyUnitOfWork rolls back changes
- Exception path in SqlAlchemyUnitOfWork triggers rollback and preserves primary error
- Live database readiness probe (check_database_ready) against PostgreSQL
"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.audit.models import AuditEventRecord
from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate
from app.db.health import check_database_ready
from app.db.uow import SqlAlchemyUnitOfWork


@pytest.mark.integration
@pytest.mark.asyncio
async def test_uow_explicit_commit_persists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that an explicit commit within SqlAlchemyUnitOfWork persists data."""
    uow = SqlAlchemyUnitOfWork(session_factory)
    test_tenant = f"tenant-commit-test-{uuid4().hex[:8]}"

    event_in = AuditEventCreate(
        schema_version=1,
        tenant_id=test_tenant,
        actor_principal_id="principal-test-01",
        actor_principal_type="SERVICE",
        authentication_method="SERVICE_API_KEY",
        event_type="test.committed",
        resource_type="test_resource",
        resource_id="res-01",
        details={"status": "persisted"},
    )

    async with uow:
        repo = AuditRepository(uow.session)
        created_record = await repo.append(event_in)
        audit_id = created_record.audit_event_id
        await uow.commit()

    # Open a completely fresh session to verify durable persistence
    async with session_factory() as verify_session:
        stmt = select(AuditEventRecord).where(AuditEventRecord.audit_event_id == audit_id)
        result = await verify_session.execute(stmt)
        record = result.scalar_one_or_none()
        assert record is not None, "Record must be visible in fresh session after commit"
        assert record.tenant_id == test_tenant
        assert record.details == {"status": "persisted"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_uow_uncommitted_exit_rolls_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that exiting SqlAlchemyUnitOfWork without commit rolls back staged mutations."""
    uow = SqlAlchemyUnitOfWork(session_factory)
    test_tenant = f"tenant-rollback-clean-{uuid4().hex[:8]}"

    event_in = AuditEventCreate(
        schema_version=1,
        tenant_id=test_tenant,
        actor_principal_id="principal-test-02",
        actor_principal_type="SERVICE",
        authentication_method="SERVICE_API_KEY",
        event_type="test.uncommitted",
        resource_type="test_resource",
        resource_id="res-02",
        details={"status": "should_be_rolled_back"},
    )

    audit_id = None
    async with uow:
        repo = AuditRepository(uow.session)
        created_record = await repo.append(event_in)
        audit_id = created_record.audit_event_id
        # Explicitly do NOT commit before exiting context

    # Open a fresh session to verify record does NOT exist
    async with session_factory() as verify_session:
        stmt = select(AuditEventRecord).where(AuditEventRecord.audit_event_id == audit_id)
        result = await verify_session.execute(stmt)
        record = result.scalar_one_or_none()
        assert record is None, "Record must NOT exist after clean uncommitted exit"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_uow_exception_triggers_rollback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that an exception raised within SqlAlchemyUnitOfWork triggers rollback."""
    uow = SqlAlchemyUnitOfWork(session_factory)
    test_tenant = f"tenant-rollback-exc-{uuid4().hex[:8]}"

    event_in = AuditEventCreate(
        schema_version=1,
        tenant_id=test_tenant,
        actor_principal_id="principal-test-03",
        actor_principal_type="SERVICE",
        authentication_method="SERVICE_API_KEY",
        event_type="test.exception_rollback",
        resource_type="test_resource",
        resource_id="res-03",
        details={"status": "aborted"},
    )

    audit_id = None
    with pytest.raises(RuntimeError, match="Simulated business failure"):
        async with uow:
            repo = AuditRepository(uow.session)
            created_record = await repo.append(event_in)
            audit_id = created_record.audit_event_id
            raise RuntimeError("Simulated business failure")

    # Open a fresh session to verify rollback
    async with session_factory() as verify_session:
        stmt = select(AuditEventRecord).where(AuditEventRecord.audit_event_id == audit_id)
        result = await verify_session.execute(stmt)
        record = result.scalar_one_or_none()
        assert record is None, "Record must NOT exist after exception rollback"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_readiness_probe_live(test_engine: AsyncEngine) -> None:
    """Verify that check_database_ready probe succeeds against live PostgreSQL."""
    is_ready = await check_database_ready(test_engine, timeout_seconds=3.0)
    assert is_ready is True, "Database readiness probe must return True for healthy PostgreSQL"
