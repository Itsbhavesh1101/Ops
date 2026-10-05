"""Unit tests for AuditRepository and same-UoW transaction ownership contract.

Part of Phase 1 Foundation Task 1F.
Validates:
- Repository contract: append builds AuditEventRecord and stages via session.add.
- Transaction boundary: uses supplied UoW session; never calls commit, rollback, or close.
- Immutability: exposes no update, modify, or delete methods.
- Flush policy: does not flush by default.
- Same-UoW transaction ownership contract: audit append participates in same UoW session
  without committing independently.
- Error handling: append exceptions propagate cleanly and are not swallowed.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEventRecord
from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.auth.models import AuthenticationMethod, PrincipalType
from app.db.repositories.base import Repository, SqlAlchemyRepository
from app.db.uow import SqlAlchemyUnitOfWork


@pytest.mark.asyncio
async def test_audit_repository_conforms_to_repository_protocol() -> None:
    """Verify AuditRepository conforms to the canonical Repository protocol."""
    mock_session = AsyncMock(spec=AsyncSession)
    repo = AuditRepository(mock_session)

    assert isinstance(repo, Repository)
    assert isinstance(repo, SqlAlchemyRepository)
    assert repo.session is mock_session


@pytest.mark.asyncio
async def test_audit_repository_append_creates_and_adds_record() -> None:
    """Verify append() maps AuditEventCreate to AuditEventRecord and stages it with session.add."""
    mock_session = MagicMock(spec=AsyncSession)
    repo = AuditRepository(mock_session)

    event = AuditEventCreate(
        schema_version=1,
        tenant_id="tenant-alpha",
        actor_principal_id="principal-100",
        actor_principal_type=PrincipalType.SERVICE,
        authentication_method=AuthenticationMethod.SERVICE_API_KEY,
        event_type="schedule.publish.requested",
        resource_type="schedule.run",
        resource_id="run-555",
        outcome=AuditEventOutcome.SUCCESS,
        correlation_id="corr-xyz",
        details={"status": "queued"},
    )

    record = await repo.append(event)

    assert isinstance(record, AuditEventRecord)
    assert isinstance(record.audit_event_id, UUID)
    assert record.schema_version == 1
    assert record.tenant_id == "tenant-alpha"
    assert record.actor_principal_id == "principal-100"
    assert record.actor_principal_type == "SERVICE"
    assert record.authentication_method == "SERVICE_API_KEY"
    assert record.event_type == "schedule.publish.requested"
    assert record.resource_type == "schedule.run"
    assert record.resource_id == "run-555"
    assert record.outcome == "SUCCESS"
    assert record.correlation_id == "corr-xyz"
    assert record.details == {"status": "queued"}

    # Must call session.add with the exact record instance
    mock_session.add.assert_called_once_with(record)


@pytest.mark.asyncio
async def test_audit_repository_never_commits_rolls_back_or_closes() -> None:
    """Verify repository methods never touch transaction demarcation (commit/rollback/close)."""
    mock_session = AsyncMock(spec=AsyncSession)
    repo = AuditRepository(mock_session)

    event = AuditEventCreate(event_type="system.config.loaded")
    await repo.append(event)

    assert mock_session.commit.call_count == 0
    assert mock_session.rollback.call_count == 0
    assert mock_session.close.call_count == 0
    assert mock_session.flush.call_count == 0  # Flush policy: no premature flush


@pytest.mark.asyncio
async def test_audit_repository_pre_flush_application_uuid_guarantee() -> None:
    """Verify record has a valid UUID immediately upon append() before any flush or commit."""
    mock_session = AsyncMock(spec=AsyncSession)
    repo = AuditRepository(mock_session)

    # Event without explicitly providing audit_event_id (defaults in schema)
    event = AuditEventCreate(event_type="system.health.evaluated")

    record = await repo.append(event)

    # ID guarantee before any database interaction
    assert record.audit_event_id is not None
    assert isinstance(record.audit_event_id, UUID)

    # Explicit verification of zero flush and zero commit before UoW boundary
    assert mock_session.flush.await_count == 0
    assert mock_session.commit.await_count == 0
    assert mock_session.rollback.await_count == 0
    assert mock_session.close.await_count == 0


@pytest.mark.asyncio
async def test_audit_repository_append_generates_distinct_uuids() -> None:
    """Section 6.C: Verify two identical AuditEventCreate inputs receive distinct UUIDs."""
    mock_session = MagicMock(spec=AsyncSession)
    repo = AuditRepository(mock_session)

    event1 = AuditEventCreate(event_type="system.node.started")
    event2 = AuditEventCreate(event_type="system.node.started")

    record1 = await repo.append(event1)
    record2 = await repo.append(event2)

    assert isinstance(record1.audit_event_id, UUID)
    assert isinstance(record2.audit_event_id, UUID)
    assert record1.audit_event_id != record2.audit_event_id


def test_audit_repository_exposes_no_mutation_methods() -> None:
    """Verify AuditRepository strictly adheres to append-only and exposes no update/delete APIs."""
    repo_methods = dir(AuditRepository)
    forbidden = ["update", "modify", "delete", "remove", "drop", "change"]
    for method_name in repo_methods:
        if not method_name.startswith("_"):
            for bad_prefix in forbidden:
                assert not method_name.startswith(bad_prefix), (
                    f"AuditRepository must be append-only; forbidden method found: {method_name}"
                )


@pytest.mark.asyncio
async def test_same_uow_transaction_ownership_contract() -> None:
    """Transaction ownership contract test: AuditRepository operates in same UoW session cleanly.

    Verifies:
    - audit_repo.session is uow.session
    - audit append adds record to the shared session
    - mock business repository operates on the exact same session
    - UoW commit is the single, authoritative transaction commit operation
    """
    mock_session = AsyncMock(spec=AsyncSession)
    mock_factory = MagicMock(return_value=mock_session)

    uow = SqlAlchemyUnitOfWork(mock_factory)

    class MockBusinessRepository(SqlAlchemyRepository):
        async def perform_business_write(self, item_id: str) -> None:
            # Stages a business object on the shared session
            self.session.add(MagicMock(name=f"BusinessItem-{item_id}"))

    async with uow:
        # Both repositories share the single UoW session
        audit_repo = AuditRepository(uow.session)
        business_repo = MockBusinessRepository(uow.session)

        assert audit_repo.session is uow.session
        assert business_repo.session is uow.session

        # Perform business write and audit append in the same transaction scope
        await business_repo.perform_business_write("item-001")
        audit_event = AuditEventCreate(
            tenant_id="tenant-abc",
            event_type="item.create.requested",
            resource_type="item",
            resource_id="item-001",
        )
        await audit_repo.append(audit_event)

        # Repositories have added items to the session, but NOT committed
        assert mock_session.add.call_count == 2
        assert mock_session.commit.call_count == 0

        # Authoritative commit executed by the Unit of Work
        await uow.commit()

    # Commit executed exactly once at UoW boundary; session closed exactly once
    assert mock_session.commit.call_count == 1
    assert mock_session.rollback.call_count == 0
    assert mock_session.close.call_count == 1


@pytest.mark.asyncio
async def test_audit_repository_append_failure_propagates() -> None:
    """Verify if session.add or record building raises, error is not swallowed."""
    mock_session = MagicMock(spec=AsyncSession)
    mock_session.add.side_effect = RuntimeError("Simulated stage error")

    repo = AuditRepository(mock_session)
    event = AuditEventCreate(event_type="system.alert.triggered")

    with pytest.raises(RuntimeError, match="Simulated stage error"):
        await repo.append(event)
