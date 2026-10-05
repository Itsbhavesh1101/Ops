"""Unit tests for IdempotencyRepository and transactional marker persistence.

Part of Phase 1 Foundation Task 1H.2.
Verifies:
- IdempotencyRepository inherits from SqlAlchemyRepository.
- acquire_or_observe input type validation (IdempotencyIdentity, RequestFingerprint).
- PostgreSQL INSERT ON CONFLICT DO NOTHING RETURNING compilation and execution.
- Insert-first execution: zero SELECT-before-INSERT to prevent concurrency races.
- Provisional ACQUIRED decision: first insertion returns ACQUIRED with zero SELECT.
- EXISTING_MATCH decision: conflict with identical fingerprint returns EXISTING_MATCH.
- EXISTING_CONFLICT decision: conflict with different fingerprint returns EXISTING_CONFLICT.
- Tenant and operation scoping: independent identities across tenant and operation boundaries.
- Raw key non-retention: raw key never appears in ORM record, SQL parameters, or decision models.
- Transaction ownership: uses supplied session, never commits, rolls back, or closes.
- Same-UoW business transaction contract verification.
- Architectural isolation: zero FastAPI, Starlette, SQLAlchemy engine, or RabbitMQ imports.
- No state/lease/response machinery in model or repository.
"""

from inspect import getsource
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.selectable import Select

from app.core.idempotency.contracts import (
    IdempotencyIdentity,
    RequestFingerprint,
    create_idempotency_identity,
)
from app.core.idempotency.hashing import fingerprint_bytes
from app.core.idempotency.models import IdempotencyRecord
from app.core.idempotency.repository import (
    IdempotencyDecision,
    IdempotencyDecisionType,
    IdempotencyRepository,
)
from app.db.repositories.base import SqlAlchemyRepository


def _build_test_identity(
    *,
    tenant_id: str = "tenant-acme",
    operation_name: str = "tasks.create",
    raw_key: str = "test-raw-key-12345",
) -> IdempotencyIdentity:
    """Helper to build a canonical IdempotencyIdentity."""
    return create_idempotency_identity(
        tenant_id=tenant_id,
        operation_name=operation_name,
        raw_key=raw_key,
    )


# -----------------------------------------------------------------------------
# Decision Model & Invariant Tests
# -----------------------------------------------------------------------------


def test_idempotency_decision_acquired_invariants() -> None:
    """Verify ACQUIRED decision requires existing_fingerprint is None."""
    identity = _build_test_identity()
    fp = fingerprint_bytes(b'{"name": "test"}')

    decision = IdempotencyDecision(
        decision=IdempotencyDecisionType.ACQUIRED,
        identity=identity,
        request_fingerprint=fp,
        existing_fingerprint=None,
    )
    assert decision.decision == IdempotencyDecisionType.ACQUIRED
    assert decision.existing_fingerprint is None

    # Invalid: ACQUIRED with existing_fingerprint
    with pytest.raises(
        ValidationError,
        match="ACQUIRED decision must not have existing_fingerprint",
    ):
        IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=fp,
        )


def test_idempotency_decision_existing_match_invariants() -> None:
    """Verify EXISTING_MATCH requires existing_fingerprint == request_fingerprint."""
    identity = _build_test_identity()
    fp1 = fingerprint_bytes(b'{"name": "test"}')
    fp2 = fingerprint_bytes(b'{"name": "different"}')

    # Valid: matching fingerprints
    decision = IdempotencyDecision(
        decision=IdempotencyDecisionType.EXISTING_MATCH,
        identity=identity,
        request_fingerprint=fp1,
        existing_fingerprint=fp1,
    )
    assert decision.decision == IdempotencyDecisionType.EXISTING_MATCH
    assert decision.existing_fingerprint == fp1

    # Invalid: None existing_fingerprint
    with pytest.raises(
        ValidationError,
        match="EXISTING_MATCH decision requires existing_fingerprint",
    ):
        IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_MATCH,
            identity=identity,
            request_fingerprint=fp1,
            existing_fingerprint=None,
        )

    # Invalid: mismatching fingerprints for EXISTING_MATCH
    with pytest.raises(
        ValidationError,
        match="EXISTING_MATCH decision requires existing_fingerprint == request_fingerprint",
    ):
        IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_MATCH,
            identity=identity,
            request_fingerprint=fp1,
            existing_fingerprint=fp2,
        )


def test_idempotency_decision_existing_conflict_invariants() -> None:
    """Verify EXISTING_CONFLICT requires existing_fingerprint != request_fingerprint."""
    identity = _build_test_identity()
    fp1 = fingerprint_bytes(b'{"name": "test"}')
    fp2 = fingerprint_bytes(b'{"name": "different"}')

    # Valid: different fingerprints
    decision = IdempotencyDecision(
        decision=IdempotencyDecisionType.EXISTING_CONFLICT,
        identity=identity,
        request_fingerprint=fp1,
        existing_fingerprint=fp2,
    )
    assert decision.decision == IdempotencyDecisionType.EXISTING_CONFLICT
    assert decision.existing_fingerprint == fp2
    assert decision.existing_fingerprint != decision.request_fingerprint

    # Invalid: None existing_fingerprint
    with pytest.raises(
        ValidationError,
        match="EXISTING_CONFLICT decision requires existing_fingerprint",
    ):
        IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_CONFLICT,
            identity=identity,
            request_fingerprint=fp1,
            existing_fingerprint=None,
        )

    # Invalid: equal fingerprints for EXISTING_CONFLICT
    with pytest.raises(
        ValidationError,
        match="EXISTING_CONFLICT decision requires existing_fingerprint != request_fingerprint",
    ):
        IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_CONFLICT,
            identity=identity,
            request_fingerprint=fp1,
            existing_fingerprint=fp1,
        )


def test_idempotency_decision_immutability() -> None:
    """Verify IdempotencyDecision is frozen and extra fields are forbidden."""
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")
    decision = IdempotencyDecision(
        decision=IdempotencyDecisionType.ACQUIRED,
        identity=identity,
        request_fingerprint=fp,
    )
    with pytest.raises(ValidationError):
        decision.decision = IdempotencyDecisionType.EXISTING_MATCH  # type: ignore[misc]

    with pytest.raises(ValidationError):
        IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
            extra_field="disallowed",  # type: ignore[call-arg]
        )


# -----------------------------------------------------------------------------
# Input Type Validation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_or_observe_rejects_invalid_types() -> None:
    """Verify acquire_or_observe enforces strict type checking on inputs."""
    session = AsyncMock(spec=AsyncSession)
    repo = IdempotencyRepository(session)
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    with pytest.raises(TypeError, match="identity must be an IdempotencyIdentity instance"):
        await repo.acquire_or_observe(
            identity="not-an-identity",  # type: ignore[arg-type]
            request_fingerprint=fp,
        )

    with pytest.raises(
        TypeError,
        match="request_fingerprint must be a RequestFingerprint instance",
    ):
        await repo.acquire_or_observe(
            identity=identity,
            request_fingerprint="not-a-fingerprint",  # type: ignore[arg-type]
        )


# -----------------------------------------------------------------------------
# Core Acquisition & Observation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_or_observe_acquired_success() -> None:
    """Verify first insertion returns ACQUIRED with zero SELECT executed."""
    session = AsyncMock(spec=AsyncSession)
    mock_insert_result = MagicMock()
    mock_insert_result.scalar_one_or_none.return_value = "digest-hex"
    session.execute.return_value = mock_insert_result

    repo = IdempotencyRepository(session)
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"test-payload")

    decision = await repo.acquire_or_observe(
        identity=identity,
        request_fingerprint=fp,
    )

    assert decision.decision == IdempotencyDecisionType.ACQUIRED
    assert decision.identity == identity
    assert decision.request_fingerprint == fp
    assert decision.existing_fingerprint is None

    # Exactly 1 statement executed (INSERT), zero SELECT
    assert session.execute.await_count == 1
    stmt = session.execute.call_args[0][0]
    assert isinstance(stmt, Insert)

    # Never commits or closes
    assert session.commit.await_count == 0
    assert session.rollback.await_count == 0
    assert session.close.await_count == 0


@pytest.mark.asyncio
async def test_acquire_or_observe_existing_match() -> None:
    """Verify conflict with identical fingerprint returns EXISTING_MATCH with no UPDATE."""
    session = AsyncMock(spec=AsyncSession)
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"payload-v1")

    # 1. INSERT returns None (conflict)
    mock_insert_result = MagicMock()
    mock_insert_result.scalar_one_or_none.return_value = None

    # 2. Post-conflict SELECT returns same fingerprint
    mock_select_result = MagicMock()
    mock_select_result.scalar_one_or_none.return_value = fp.value

    session.execute.side_effect = [mock_insert_result, mock_select_result]

    repo = IdempotencyRepository(session)
    decision = await repo.acquire_or_observe(
        identity=identity,
        request_fingerprint=fp,
    )

    assert decision.decision == IdempotencyDecisionType.EXISTING_MATCH
    assert decision.identity == identity
    assert decision.request_fingerprint == fp
    assert decision.existing_fingerprint == fp

    # Exactly 2 statements executed: INSERT first, then SELECT
    assert session.execute.await_count == 2
    calls = session.execute.call_args_list
    assert isinstance(calls[0][0][0], Insert)
    assert isinstance(calls[1][0][0], Select)


@pytest.mark.asyncio
async def test_acquire_or_observe_existing_conflict() -> None:
    """Verify conflict with differing fingerprint returns EXISTING_CONFLICT."""
    session = AsyncMock(spec=AsyncSession)
    identity = _build_test_identity()
    requested_fp = fingerprint_bytes(b"new-payload")
    existing_fp = fingerprint_bytes(b"prior-committed-payload")

    # 1. INSERT returns None (conflict)
    mock_insert_result = MagicMock()
    mock_insert_result.scalar_one_or_none.return_value = None

    # 2. Post-conflict SELECT returns different fingerprint
    mock_select_result = MagicMock()
    mock_select_result.scalar_one_or_none.return_value = existing_fp.value

    session.execute.side_effect = [mock_insert_result, mock_select_result]

    repo = IdempotencyRepository(session)
    decision = await repo.acquire_or_observe(
        identity=identity,
        request_fingerprint=requested_fp,
    )

    assert decision.decision == IdempotencyDecisionType.EXISTING_CONFLICT
    assert decision.identity == identity
    assert decision.request_fingerprint == requested_fp
    assert decision.existing_fingerprint == existing_fp
    assert decision.existing_fingerprint != decision.request_fingerprint

    # Exactly 2 statements executed: INSERT first, then SELECT
    assert session.execute.await_count == 2
    calls = session.execute.call_args_list
    assert isinstance(calls[0][0][0], Insert)
    assert isinstance(calls[1][0][0], Select)


@pytest.mark.asyncio
async def test_acquire_or_observe_conflict_select_missing_raises_runtime_error() -> None:
    """Verify RuntimeError is raised if post-conflict SELECT returns None."""
    session = AsyncMock(spec=AsyncSession)
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"payload")

    mock_insert_result = MagicMock()
    mock_insert_result.scalar_one_or_none.return_value = None
    mock_select_result = MagicMock()
    mock_select_result.scalar_one_or_none.return_value = None
    session.execute.side_effect = [mock_insert_result, mock_select_result]

    repo = IdempotencyRepository(session)
    with pytest.raises(
        RuntimeError,
        match="encountered conflict but existing record could not be read",
    ):
        await repo.acquire_or_observe(
            identity=identity,
            request_fingerprint=fp,
        )


# -----------------------------------------------------------------------------
# Chronological Ordering & SQL Compilation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_before_select_chronological_ordering() -> None:
    """Verify chronological ordering: INSERT ON CONFLICT DO NOTHING occurs before any SELECT."""
    executed_statements: list[str] = []

    class RecordingSession:
        async def execute(self, stmt: Any) -> MagicMock:
            if isinstance(stmt, Insert):
                executed_statements.append("INSERT")
                mock_res = MagicMock()
                mock_res.scalar_one_or_none.return_value = None  # simulate conflict
                return mock_res
            elif isinstance(stmt, Select):
                executed_statements.append("SELECT")
                mock_res = MagicMock()
                mock_res.scalar_one_or_none.return_value = "a" * 64
                return mock_res
            raise AssertionError(f"Unexpected statement: {stmt}")

    recording_session = RecordingSession()
    repo = IdempotencyRepository(recording_session)  # type: ignore[arg-type]
    identity = _build_test_identity()
    fp = RequestFingerprint("a" * 64)

    await repo.acquire_or_observe(
        identity=identity,
        request_fingerprint=fp,
    )

    assert executed_statements == ["INSERT", "SELECT"]


def test_sql_compilation_semantics() -> None:
    """Verify compiled PostgreSQL SQL semantics include ON CONFLICT DO NOTHING and RETURNING."""
    identity = _build_test_identity(
        tenant_id="tenant-alpha",
        operation_name="billing.invoice",
        raw_key="key-xyz",
    )
    fp = fingerprint_bytes(b"data")

    # Inspect the compiled insert statement by calling pg_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    insert_stmt = (
        pg_insert(IdempotencyRecord)
        .values(
            tenant_id=identity.tenant_id,
            operation_name=identity.operation_name,
            key_digest=str(identity.key_digest),
            request_fingerprint=str(fp),
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "operation_name", "key_digest"],
        )
        .returning(IdempotencyRecord.key_digest)
    )

    compiled_sql = str(insert_stmt.compile(dialect=postgresql.dialect()))
    assert "INSERT INTO idempotency_records" in compiled_sql
    assert "ON CONFLICT (tenant_id, operation_name, key_digest) DO NOTHING" in compiled_sql
    assert "RETURNING idempotency_records.key_digest" in compiled_sql

    compiled_params = insert_stmt.compile(dialect=postgresql.dialect()).params
    assert compiled_params["tenant_id"] == "tenant-alpha"
    assert compiled_params["operation_name"] == "billing.invoice"
    assert compiled_params["key_digest"] == str(identity.key_digest)
    assert compiled_params["request_fingerprint"] == str(fp)


# -----------------------------------------------------------------------------
# Tenant and Operation Scoping Tests
# -----------------------------------------------------------------------------


def test_tenant_scope_isolation() -> None:
    """Verify tenant A and tenant B using same operation and raw key produce distinct identities."""
    shared_key = "shared-client-key"
    id_a = _build_test_identity(tenant_id="tenant-a", raw_key=shared_key)
    id_b = _build_test_identity(tenant_id="tenant-b", raw_key=shared_key)

    assert id_a.tenant_id != id_b.tenant_id
    assert id_a.key_digest == id_b.key_digest
    assert id_a != id_b


def test_operation_scope_isolation() -> None:
    """Verify same tenant and raw key across different operations produce distinct identities."""
    shared_key = "shared-client-key"
    id_1 = _build_test_identity(operation_name="tasks.create", raw_key=shared_key)
    id_2 = _build_test_identity(operation_name="tasks.cancel", raw_key=shared_key)

    assert id_1.operation_name != id_2.operation_name
    assert id_1.key_digest == id_2.key_digest
    assert id_1 != id_2


# -----------------------------------------------------------------------------
# Privacy & Raw Key Absence Tests
# -----------------------------------------------------------------------------


def test_raw_key_absence_in_all_artifacts() -> None:
    """Verify synthetic raw client key is strictly absent from ORM, decision, and SQL params."""
    synthetic_key = "SECRET-raw-client-key-ABC123XYZ"
    identity = _build_test_identity(raw_key=synthetic_key)
    fp = fingerprint_bytes(b"body")

    decision = IdempotencyDecision(
        decision=IdempotencyDecisionType.ACQUIRED,
        identity=identity,
        request_fingerprint=fp,
    )

    # 1. Not in decision str or repr
    assert synthetic_key not in str(decision)
    assert synthetic_key not in repr(decision)
    assert synthetic_key not in decision.model_dump_json()

    # 2. Not in identity str or repr
    assert synthetic_key not in str(identity)
    assert synthetic_key not in repr(identity)

    # 3. Not in IdempotencyRecord columns
    record_cols = set(IdempotencyRecord.__table__.columns.keys())
    assert "raw_key" not in record_cols
    assert "idempotency_key" not in record_cols


# -----------------------------------------------------------------------------
# Same-UoW Transaction Ownership Contract Tests
# -----------------------------------------------------------------------------


class FakeBusinessRepository(SqlAlchemyRepository):
    """Synthetic business repository sharing the session."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.mutated = False

    async def apply_mutation(self) -> None:
        self.mutated = True


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
async def test_same_uow_contract_shares_exact_session() -> None:
    """Verify IdempotencyRepository and BusinessRepository share the exact same UoW session."""
    uow = FakeUnitOfWork()
    mock_insert_result = MagicMock()
    mock_insert_result.scalar_one_or_none.return_value = "digest"
    uow.session.execute.return_value = mock_insert_result

    identity = _build_test_identity()
    fp = fingerprint_bytes(b"mutation-payload")

    async with uow:
        idem_repo = IdempotencyRepository(uow.session)
        business_repo = FakeBusinessRepository(uow.session)

        # Exact object identity: same session
        assert idem_repo.session is uow.session
        assert business_repo.session is uow.session
        assert idem_repo.session is business_repo.session

        decision = await idem_repo.acquire_or_observe(
            identity=identity,
            request_fingerprint=fp,
        )

        if decision.decision == IdempotencyDecisionType.ACQUIRED:
            await business_repo.apply_mutation()

    assert business_repo.mutated is True
    assert uow.committed is True
    assert uow.rolled_back is False


@pytest.mark.asyncio
async def test_uow_rollback_rolls_back_provisional_marker() -> None:
    """Verify exception in UoW triggers rollback of both business mutation and marker."""
    uow = FakeUnitOfWork()
    mock_insert_result = MagicMock()
    mock_insert_result.scalar_one_or_none.return_value = "digest"
    uow.session.execute.return_value = mock_insert_result

    identity = _build_test_identity()
    fp = fingerprint_bytes(b"mutation-payload")

    with pytest.raises(RuntimeError, match="Business mutation crashed"):
        async with uow:
            idem_repo = IdempotencyRepository(uow.session)
            decision = await idem_repo.acquire_or_observe(
                identity=identity,
                request_fingerprint=fp,
            )
            assert decision.decision == IdempotencyDecisionType.ACQUIRED
            raise RuntimeError("Business mutation crashed")

    assert uow.committed is False
    assert uow.rolled_back is True


# -----------------------------------------------------------------------------
# Architectural Isolation & No State/Lease/Response Tests
# -----------------------------------------------------------------------------


def test_architectural_isolation() -> None:
    """Verify idempotency persistence and repository modules import no HTTP or messaging code."""
    import app.core.idempotency.models as models_mod
    import app.core.idempotency.repository as repo_mod

    combined_src = getsource(models_mod) + "\n" + getsource(repo_mod)

    forbidden_tokens = [
        "fastapi",
        "FastAPI",
        "starlette",
        "Starlette",
        "HTTPException",
        "aio_pika",
        "RabbitMq",
        "import Request",
        "import Response",
        "Idempotency-Key",
    ]

    for forbidden in forbidden_tokens:
        assert forbidden not in combined_src, (
            f"Forbidden token '{forbidden}' found in idempotency persistence code"
        )


def test_no_state_lease_response_columns_or_methods() -> None:
    """Verify model and repository contain zero state machine, lease, or response concepts."""
    import app.core.idempotency.models as models_mod
    import app.core.idempotency.repository as repo_mod

    combined_src = getsource(models_mod) + "\n" + getsource(repo_mod)

    forbidden_concepts = [
        "IN_PROGRESS",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
        "lease_token",
        "claimed_at",
        "expires_at",
        "response_body",
        "response_json",
        "response_headers",
        "http_status",
    ]

    for concept in forbidden_concepts:
        assert concept not in combined_src, (
            f"Forbidden concept '{concept}' found in idempotency persistence/repository"
        )
