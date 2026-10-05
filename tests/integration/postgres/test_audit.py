"""Live PostgreSQL append-only audit enforcement verification.

Part of Phase 1 Foundation Task 1J.2A.
Validates:
- Audit record creation with repository-generated UUID ownership
- Server-authored created_at timestamp populated by PostgreSQL
- Persistence and visibility after transaction commit
- Database-level rejection of direct SQL UPDATE mutations via append-only trigger
- Database-level rejection of direct SQL DELETE mutations via append-only trigger
- Clean rollback following rejected operations
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditEventRecord
from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate
from app.db.uow import SqlAlchemyUnitOfWork


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_event_creation_and_server_timestamp(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify audit record creation, repo UUID ownership, and server-authored timestamp."""
    uow = SqlAlchemyUnitOfWork(session_factory)
    test_tenant = f"tenant-audit-create-{uuid4().hex[:8]}"

    event_in = AuditEventCreate(
        schema_version=1,
        tenant_id=test_tenant,
        actor_principal_id="principal-audit-tester",
        actor_principal_type="SERVICE",
        authentication_method="SERVICE_API_KEY",
        event_type="audit.record.created",
        resource_type="document",
        resource_id="doc-999",
        details={"audit_test": True},
    )

    async with uow:
        repo = AuditRepository(uow.session)
        created_record = await repo.append(event_in)
        audit_id = created_record.audit_event_id
        await uow.commit()

    async with session_factory() as session:
        stmt = select(AuditEventRecord).where(AuditEventRecord.audit_event_id == audit_id)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        assert record is not None
        assert record.audit_event_id == audit_id
        assert record.tenant_id == test_tenant
        # PostgreSQL must populate recorded_at via server default now()
        assert record.recorded_at is not None
        assert record.recorded_at.tzinfo is not None
        assert record.recorded_at.utcoffset() is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_append_only_update_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that PostgreSQL append-only trigger rejects direct SQL UPDATE on audit_events."""
    uow = SqlAlchemyUnitOfWork(session_factory)
    test_tenant = f"tenant-audit-update-{uuid4().hex[:8]}"

    event_in = AuditEventCreate(
        schema_version=1,
        tenant_id=test_tenant,
        actor_principal_id="principal-audit-tester",
        actor_principal_type="SERVICE",
        authentication_method="SERVICE_API_KEY",
        event_type="audit.update.attempt",
        resource_type="document",
        resource_id="doc-immutable",
        details={"status": "initial"},
    )

    # 1. Create and commit legitimate audit record
    async with uow:
        repo = AuditRepository(uow.session)
        record = await repo.append(event_in)
        audit_id = record.audit_event_id
        await uow.commit()

    # 2. Attempt direct SQL UPDATE on the immutable audit row
    async with session_factory() as session:
        with pytest.raises(DBAPIError) as exc_info:
            await session.execute(
                update(AuditEventRecord)
                .where(AuditEventRecord.audit_event_id == audit_id)
                .values(tenant_id="tampered-tenant")
            )
            await session.commit()

        # Verify the database-level trigger exception message
        err_msg = str(exc_info.value).lower()
        assert "append-only" in err_msg or "cannot be modified" in err_msg or "trigger" in err_msg

        # Explicitly rollback the failed session
        await session.rollback()

    # 3. Verify record remains unchanged in the database
    async with session_factory() as session:
        persisted = await session.scalar(
            select(AuditEventRecord).where(AuditEventRecord.audit_event_id == audit_id)
        )
        assert persisted is not None
        assert persisted.tenant_id == test_tenant


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_append_only_delete_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that PostgreSQL append-only trigger rejects direct SQL DELETE on audit_events."""
    uow = SqlAlchemyUnitOfWork(session_factory)
    test_tenant = f"tenant-audit-delete-{uuid4().hex[:8]}"

    event_in = AuditEventCreate(
        schema_version=1,
        tenant_id=test_tenant,
        actor_principal_id="principal-audit-tester",
        actor_principal_type="SERVICE",
        authentication_method="SERVICE_API_KEY",
        event_type="audit.delete.attempt",
        resource_type="document",
        resource_id="doc-nodelete",
        details={"status": "protected"},
    )

    # 1. Create and commit legitimate audit record
    async with uow:
        repo = AuditRepository(uow.session)
        record = await repo.append(event_in)
        audit_id = record.audit_event_id
        await uow.commit()

    # 2. Attempt direct SQL DELETE on the immutable audit row
    async with session_factory() as session:
        with pytest.raises(DBAPIError) as exc_info:
            await session.execute(
                delete(AuditEventRecord).where(AuditEventRecord.audit_event_id == audit_id)
            )
            await session.commit()

        # Verify the database-level trigger exception message
        err_msg = str(exc_info.value).lower()
        assert "append-only" in err_msg or "cannot be modified" in err_msg or "trigger" in err_msg

        # Explicitly rollback the failed session
        await session.rollback()

    # 3. Verify record still exists in the database
    async with session_factory() as session:
        persisted = await session.scalar(
            select(AuditEventRecord).where(AuditEventRecord.audit_event_id == audit_id)
        )
        assert persisted is not None
        assert persisted.tenant_id == test_tenant
