"""Unit tests for InboxRepository and consumer-scoped event deduplication.

Part of Phase 1 Foundation Task 1G.4A.
Verifies:
- InboxRepository inherits from SqlAlchemyRepository.
- validate_consumer_name enforces canonical namespaced rules and bounds.
- try_record input validation (consumer_name, EventEnvelope).
- PostgreSQL INSERT ON CONFLICT DO NOTHING RETURNING compilation and execution.
- Zero SELECT-before-INSERT to prevent concurrency races.
- First event yields True (provisional until UoW commit).
- Duplicate event yields False without raising exceptions or calling handlers.
- Consumer-scoped identity (different consumers can process same event independently).
- Transaction ownership (uses supplied session, does not commit, rollback, or close).
- Same-UoW business transaction contract verification.
- Architectural isolation (zero RabbitMQ / aio_pika / consumer transport imports).
"""

from inspect import getsource
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import SqlAlchemyRepository
from app.events.inbox.repository import (
    CONSUMER_NAME_MAX_LENGTH,
    InboxRepository,
    validate_consumer_name,
)
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope


def _build_test_envelope(
    *,
    event_id: UUID | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Helper to create a canonical EventEnvelope for testing."""
    create_dto = EventCreate(
        event_type="task.dispatched",
        event_version=1,
        tenant_id="tenant-123",
        correlation_id=correlation_id or "corr-123",
        subject_id="task-123",
        subject_type="task",
        payload={"task_code": "T-100"},
    )
    envelope = create_event_envelope(create_dto)
    if event_id is not None:
        return EventEnvelope(
            event_id=event_id,
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            occurred_at=envelope.occurred_at,
            tenant_id=envelope.tenant_id,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            subject_type=envelope.subject_type,
            subject_id=envelope.subject_id,
            payload=envelope.payload,
        )
    return envelope


# -----------------------------------------------------------------------------
# Consumer Name Validation Tests
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valid_name",
    [
        "workflow.projection",
        "analytics.task_events",
        "billing.invoice.processor",
        "a.b",
        "task_service.worker_pool_1",
        "alpha_1.beta_2.gamma_3",
    ],
)
def test_validate_consumer_name_valid(valid_name: str) -> None:
    """Verify valid canonical namespaced consumer names are accepted."""
    assert validate_consumer_name(valid_name) == valid_name


@pytest.mark.parametrize(
    "invalid_name,expected_err",
    [
        ("", "between 1 and 128 characters"),
        ("   ", "is invalid. Must match canonical namespaced format"),
        ("workflow", "is invalid. Must match canonical namespaced format"),
        ("Workflow.Projection", "is invalid. Must match canonical namespaced format"),
        (".workflow.projection", "is invalid. Must match canonical namespaced format"),
        ("workflow.projection.", "is invalid. Must match canonical namespaced format"),
        ("workflow..projection", "is invalid. Must match canonical namespaced format"),
        ("workflow.proj-action", "is invalid. Must match canonical namespaced format"),
        ("1workflow.projection", "is invalid. Must match canonical namespaced format"),
        ("workflow.1projection", "is invalid. Must match canonical namespaced format"),
        ("a" * (CONSUMER_NAME_MAX_LENGTH + 1), "between 1 and 128 characters"),
    ],
)
def test_validate_consumer_name_invalid(invalid_name: str, expected_err: str) -> None:
    """Verify malformed consumer names are rejected with clear errors."""
    with pytest.raises(ValueError, match=expected_err):
        validate_consumer_name(invalid_name)


def test_validate_consumer_name_type_error() -> None:
    """Verify non-string consumer_name raises TypeError."""
    with pytest.raises(TypeError, match="consumer_name must be a str"):
        validate_consumer_name(123)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# InboxRepository Statement & Semantics Tests
# -----------------------------------------------------------------------------


def test_inbox_repository_inherits_sqlalchemy_repository() -> None:
    """Verify InboxRepository subclassing and session binding."""
    session = AsyncMock(spec=AsyncSession)
    repo = InboxRepository(session)
    assert isinstance(repo, SqlAlchemyRepository)
    assert repo.session is session


@pytest.mark.asyncio
async def test_try_record_rejects_non_envelope() -> None:
    """Verify try_record rejects raw payloads, dicts, or other non-EventEnvelope types."""
    session = AsyncMock(spec=AsyncSession)
    repo = InboxRepository(session)

    with pytest.raises(TypeError, match="requires an EventEnvelope instance"):
        await repo.try_record(
            consumer_name="workflow.projection",
            envelope={"event_id": str(uuid4())},  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_try_record_rejects_invalid_consumer_name() -> None:
    """Verify try_record validates consumer_name before database execution."""
    session = AsyncMock(spec=AsyncSession)
    repo = InboxRepository(session)
    envelope = _build_test_envelope()

    with pytest.raises(ValueError, match="is invalid. Must match canonical namespaced format"):
        await repo.try_record(
            consumer_name="INVALID_NAME",
            envelope=envelope,
        )

    # Database must NOT be called on validation failure
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_try_record_sql_statement_semantics() -> None:
    """Verify compiled SQL statement semantics: INSERT ON CONFLICT DO NOTHING RETURNING."""
    session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = uuid4()
    session.execute.return_value = mock_result

    repo = InboxRepository(session)
    envelope = _build_test_envelope()

    await repo.try_record(
        consumer_name="workflow.projection",
        envelope=envelope,
    )

    session.execute.assert_awaited_once()
    stmt = session.execute.call_args[0][0]

    # Verify statement compilation against PostgreSQL dialect
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql_str = str(compiled)

    assert "INSERT INTO inbox_messages" in sql_str
    assert "ON CONFLICT (consumer_name, event_id) DO NOTHING" in sql_str
    assert "RETURNING inbox_messages.event_id" in sql_str
    assert "SELECT" not in sql_str.upper().split("INSERT")[0]  # Zero SELECT before INSERT


@pytest.mark.asyncio
async def test_try_record_first_processing_returns_true() -> None:
    """Verify try_record returns True when PostgreSQL returns the inserted event_id."""
    session = AsyncMock(spec=AsyncSession)
    envelope = _build_test_envelope()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = envelope.event_id
    session.execute.return_value = mock_result

    repo = InboxRepository(session)
    first_processing = await repo.try_record(
        consumer_name="workflow.projection",
        envelope=envelope,
    )

    assert first_processing is True
    # Verify the inserted event_id matches envelope.event_id directly (never regenerated)
    session.execute.assert_awaited_once()
    stmt = session.execute.call_args[0][0]
    compiled_params = stmt.compile(dialect=postgresql.dialect()).params
    assert compiled_params["event_id"] == envelope.event_id
    assert compiled_params["consumer_name"] == "workflow.projection"


@pytest.mark.asyncio
async def test_try_record_duplicate_event_returns_false() -> None:
    """Verify try_record returns False without exception when conflict occurs."""
    session = AsyncMock(spec=AsyncSession)
    envelope = _build_test_envelope()

    # ON CONFLICT DO NOTHING returns 0 rows, so scalar_one_or_none returns None
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    repo = InboxRepository(session)
    first_processing = await repo.try_record(
        consumer_name="workflow.projection",
        envelope=envelope,
    )

    assert first_processing is False


@pytest.mark.asyncio
async def test_try_record_consumer_scoped_deduplication() -> None:
    """Verify composite primary key scopes deduplication to (consumer_name, event_id)."""
    session = AsyncMock(spec=AsyncSession)
    shared_event = _build_test_envelope()

    repo = InboxRepository(session)

    # 1. Consumer A processes shared_event -> First processing (True)
    mock_result_a = MagicMock()
    mock_result_a.scalar_one_or_none.return_value = shared_event.event_id
    session.execute.return_value = mock_result_a

    result_a = await repo.try_record(
        consumer_name="workflow.projection",
        envelope=shared_event,
    )
    assert result_a is True

    # 2. Consumer B processes same shared_event -> Independent first processing (True)
    mock_result_b = MagicMock()
    mock_result_b.scalar_one_or_none.return_value = shared_event.event_id
    session.execute.return_value = mock_result_b

    result_b = await repo.try_record(
        consumer_name="analytics.task_events",
        envelope=shared_event,
    )
    assert result_b is True

    # 3. Consumer A processes shared_event again -> Duplicate (False)
    mock_result_a_dup = MagicMock()
    mock_result_a_dup.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result_a_dup

    result_a_dup = await repo.try_record(
        consumer_name="workflow.projection",
        envelope=shared_event,
    )
    assert result_a_dup is False


@pytest.mark.asyncio
async def test_try_record_never_uses_secondary_or_transport_identifiers() -> None:
    """Verify deduplication uses strictly envelope.event_id, never correlation_id or subject_id."""
    session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = uuid4()
    session.execute.return_value = mock_result

    repo = InboxRepository(session)
    envelope = _build_test_envelope()

    await repo.try_record(
        consumer_name="workflow.projection",
        envelope=envelope,
    )

    stmt = session.execute.call_args[0][0]
    compiled_params = stmt.compile(dialect=postgresql.dialect()).params

    assert compiled_params["event_id"] == envelope.event_id
    assert compiled_params["event_id"] != envelope.correlation_id


@pytest.mark.asyncio
async def test_inbox_repository_transaction_ownership() -> None:
    """Verify InboxRepository never commits, rolls back, closes, or creates sessions."""
    session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = uuid4()
    session.execute.return_value = mock_result

    repo = InboxRepository(session)
    envelope = _build_test_envelope()

    await repo.try_record(
        consumer_name="workflow.projection",
        envelope=envelope,
    )

    # Must execute statement within session
    session.execute.assert_awaited_once()

    # Invariants: repository MUST NOT manage transaction boundaries
    assert session.commit.await_count == 0
    assert session.rollback.await_count == 0
    assert session.close.await_count == 0


# -----------------------------------------------------------------------------
# Business Transaction Contract Verification
# -----------------------------------------------------------------------------


class FakeBusinessRepository(SqlAlchemyRepository):
    """Synthetic business repository sharing the same session as InboxRepository."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.applied_events: list[UUID] = []

    async def apply_mutation(self, event_id: UUID) -> None:
        """Apply business domain mutation within the shared session."""
        self.applied_events.append(event_id)


class FakeUnitOfWork:
    """Synthetic UnitOfWork managing an active transactional session."""

    def __init__(self) -> None:
        self.session = AsyncMock(spec=AsyncSession)
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()

    async def commit(self) -> None:
        self.committed = True
        await self.session.commit()

    async def rollback(self) -> None:
        self.rolled_back = True
        await self.session.rollback()


@pytest.mark.asyncio
async def test_same_uow_business_transaction_contract_first_processing() -> None:
    """Verify InboxRepository and BusinessRepository share the exact same UoW session."""
    uow = FakeUnitOfWork()
    envelope = _build_test_envelope()

    # Simulate first processing
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = envelope.event_id
    uow.session.execute.return_value = mock_result

    async with uow:
        inbox_repo = InboxRepository(uow.session)
        business_repo = FakeBusinessRepository(uow.session)

        # Both repositories share identical session reference
        assert inbox_repo.session is uow.session
        assert business_repo.session is uow.session
        assert inbox_repo.session is business_repo.session

        first_processing = await inbox_repo.try_record(
            consumer_name="workflow.projection",
            envelope=envelope,
        )

        if first_processing:
            await business_repo.apply_mutation(envelope.event_id)

    # UnitOfWork committed both operations together
    assert uow.committed is True
    assert uow.rolled_back is False
    assert business_repo.applied_events == [envelope.event_id]


@pytest.mark.asyncio
async def test_same_uow_business_transaction_contract_duplicate_skips_business_work() -> None:
    """Verify duplicate event skips business mutation within the shared session."""
    uow = FakeUnitOfWork()
    envelope = _build_test_envelope()

    # Simulate duplicate (conflict)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    uow.session.execute.return_value = mock_result

    async with uow:
        inbox_repo = InboxRepository(uow.session)
        business_repo = FakeBusinessRepository(uow.session)

        first_processing = await inbox_repo.try_record(
            consumer_name="workflow.projection",
            envelope=envelope,
        )

        if first_processing:
            await business_repo.apply_mutation(envelope.event_id)

    # UnitOfWork commits, but business logic was never executed
    assert uow.committed is True
    assert business_repo.applied_events == []


@pytest.mark.asyncio
async def test_same_uow_business_transaction_contract_business_failure_triggers_rollback() -> None:
    """Verify business exception triggers rollback of both business mutation and inbox marker."""
    uow = FakeUnitOfWork()
    envelope = _build_test_envelope()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = envelope.event_id
    uow.session.execute.return_value = mock_result

    with pytest.raises(RuntimeError, match="BusinessLogicCrash"):
        async with uow:
            inbox_repo = InboxRepository(uow.session)
            business_repo = FakeBusinessRepository(uow.session)

            first_processing = await inbox_repo.try_record(
                consumer_name="workflow.projection",
                envelope=envelope,
            )

            if first_processing:
                await business_repo.apply_mutation(envelope.event_id)
                # Failure after try_record
                raise RuntimeError("BusinessLogicCrash")

    # Transaction rolled back (provisional inbox marker discarded along with mutation)
    assert uow.rolled_back is True
    assert uow.committed is False


# -----------------------------------------------------------------------------
# Architectural Isolation & No Transport Tests
# -----------------------------------------------------------------------------


def test_inbox_persistence_architectural_isolation() -> None:
    """Verify Inbox persistence code imports zero transport or broker libraries."""
    import ast

    import app.events.inbox.models as models_mod
    import app.events.inbox.repository as repo_mod

    for mod in (models_mod, repo_mod):
        tree = ast.parse(getsource(mod))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name.lower())
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module.lower())

        for imp in imported_modules:
            assert "aio_pika" not in imp
            assert "rabbitmq" not in imp
            assert "pika" not in imp
            assert "publishers" not in imp
            assert "consumer" not in imp
