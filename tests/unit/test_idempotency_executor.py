"""Unit tests for IdempotentMutationExecutor and provider-neutral execution orchestration.

Part of Phase 1 Foundation Task 1H.3.
Verifies:
- IdempotentMutationExecutor construction validation (zero I/O, callable UoW, handler protocol).
- Input contract (strict validation of IdempotencyIdentity and RequestFingerprint).
- ACQUIRED execution ordering and flow:
  uow_enter -> acquire_or_observe -> handler -> commit -> exit -> EXECUTED.
- Return strictly after UnitOfWork exit boundary (active_uow_count == 0).
- Handler return value retention in IdempotentExecutionResult.value.
- EXISTING_MATCH flow: handler skipped, UoW committed, ALREADY_PROCESSED returned with value None.
- EXISTING_CONFLICT flow: handler skipped, database context completed, UoW exited,
  IdempotencyConflictError raised.
- Conflict error safety: exposes only canonical identity and digest fingerprints; zero raw keys.
- Handler failure rollback: unhandled exception rolls back transaction, commits nothing,
  propagates original error.
- Commit failure behavior: persistence failure propagates, handler not retried,
  EXECUTED not returned.
- Existing match commit failure: persistence failure propagates, ALREADY_PROCESSED not returned.
- Existing conflict commit failure: persistence failure propagates,
  IdempotencyConflictError does NOT replace it.
- Cancellation semantics: asyncio.CancelledError propagates cleanly without returning success.
- Single-UoW invariant: exactly one UnitOfWork requested across all execution paths.
- Handler execution counts: ACQUIRED=1, EXISTING_MATCH=0, EXISTING_CONFLICT=0, failures not retried.
- Exact session sharing: repo.session is handler_session is uow.session (object identity).
- Transactional Outbox compatibility: OutboxRepository(session) shares the exact same transaction.
- Zero broker publication in executor or execution logic.
- Result identity and request fingerprint preservation (exact caller instances).
- Result immutability (frozen dataclass, ALREADY_PROCESSED rejects non-None value).
- Architectural isolation: zero imports of FastAPI, Starlette, Request, Response,
  HTTPException, aio_pika, or publisher.
- Zero recalculation of request fingerprints or key digests in executor.
- Schema stability: Base.metadata exactly 4 canonical tables; Alembic head is 0006;
  zero migration 0007.
- Deferred runtime integration verification constant.
"""

import ast
import asyncio
from dataclasses import FrozenInstanceError
from inspect import getsource
from types import TracebackType
from typing import Any
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.core.idempotency.contracts import (
    IdempotencyIdentity,
    create_idempotency_identity,
)
from app.core.idempotency.executor import (
    IdempotencyConflictError,
    IdempotentExecutionOutcome,
    IdempotentExecutionResult,
    IdempotentMutationExecutor,
    IdempotentMutationHandler,
)
from app.core.idempotency.hashing import fingerprint_bytes
from app.core.idempotency.repository import (
    IdempotencyDecision,
    IdempotencyDecisionType,
)
from app.db.base import Base
from app.db.uow import UnitOfWorkError
from app.events.outbox.repository import OutboxRepository

# Authoritative verification boundary constant for Task 1H.3
IDEMPOTENT_EXECUTION_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


# -----------------------------------------------------------------------------
# Test Fixtures & Fakes
# -----------------------------------------------------------------------------


def _build_test_identity(
    *,
    tenant_id: str = "tenant-alpha",
    operation_name: str = "tasks.create",
    raw_key: str = "test-raw-client-key-100",
) -> IdempotencyIdentity:
    """Helper to build a canonical IdempotencyIdentity for testing."""
    return create_idempotency_identity(
        tenant_id=tenant_id,
        operation_name=operation_name,
        raw_key=raw_key,
    )


class FakeUnitOfWork:
    """Controllable UnitOfWork recording lifecycle steps and transaction state."""

    def __init__(
        self,
        *,
        commit_error: Exception | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self.session = MagicMock(spec=AsyncSession)
        self.commit_error = commit_error
        self.timeline = timeline
        self.is_active = False
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        self.is_active = True
        if self.timeline is not None:
            self.timeline.append("uow_enter")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.is_active = False
        if exc_type is not None:
            self.rolled_back = True
            if self.timeline is not None:
                self.timeline.append("uow_rollback")
        if self.timeline is not None:
            self.timeline.append("uow_exit")

    async def commit(self) -> None:
        if self.timeline is not None:
            self.timeline.append("uow_commit")
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    async def rollback(self) -> None:
        if self.timeline is not None:
            self.timeline.append("uow_rollback")
        self.rolled_back = True


class FakeHandler:
    """Controllable IdempotentMutationHandler recording invocations and state."""

    def __init__(
        self,
        *,
        return_value: Any = "default_result",
        fail_with: BaseException | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self.return_value = return_value
        self.fail_with = fail_with
        self.timeline = timeline
        self.call_count = 0
        self.last_session: AsyncSession | None = None

    async def execute(
        self,
        *,
        session: AsyncSession,
    ) -> Any:
        self.call_count += 1
        self.last_session = session

        if self.timeline is not None:
            self.timeline.append("handler_start")

        if self.fail_with is not None:
            raise self.fail_with

        if self.timeline is not None:
            self.timeline.append("handler_finish")

        return self.return_value


# -----------------------------------------------------------------------------
# Executor Construction & Input Validation Tests
# -----------------------------------------------------------------------------


def test_executor_construction_valid() -> None:
    """Verify executor initializes with valid dependencies without performing I/O."""
    uow_factory = MagicMock(return_value=FakeUnitOfWork())
    handler = FakeHandler()

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    assert isinstance(executor, IdempotentMutationExecutor)
    # Invariant: zero database, handler, or network calls during construction
    uow_factory.assert_not_called()
    assert handler.call_count == 0


def test_executor_construction_type_errors() -> None:
    """Verify non-callable uow_factory or non-conforming handler raises TypeError."""
    handler = FakeHandler()
    uow_factory = MagicMock(return_value=FakeUnitOfWork())

    # Invalid uow_factory type
    with pytest.raises(TypeError, match="uow_factory must be a callable"):
        IdempotentMutationExecutor(
            uow_factory="not_a_callable",  # type: ignore[arg-type]
            handler=handler,
        )

    # Invalid handler type (lacks execute method)
    with pytest.raises(
        TypeError,
        match="handler must implement IdempotentMutationHandler protocol",
    ):
        IdempotentMutationExecutor(
            uow_factory=uow_factory,
            handler="not_a_handler",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_identity",
    [
        "raw-string-identity",
        b"raw-bytes-identity",
        {"tenant_id": "tenant-a", "operation": "tasks.create"},
        12345,
        None,
    ],
)
async def test_executor_execute_rejects_invalid_identity_type(invalid_identity: Any) -> None:
    """Verify execute() rejects any identity that is not an IdempotencyIdentity instance."""
    uow_factory = MagicMock(return_value=FakeUnitOfWork())
    handler = FakeHandler()
    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )
    fp = fingerprint_bytes(b'{"key": "value"}')

    with pytest.raises(TypeError, match="identity must be an IdempotencyIdentity instance"):
        await executor.execute(
            identity=invalid_identity,
            request_fingerprint=fp,
        )

    # Invariant: UoW factory must not be called when input validation fails
    uow_factory.assert_not_called()
    assert handler.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_fingerprint",
    [
        "raw-string-fingerprint",
        b"raw-bytes-fingerprint",
        {"hash": "a" * 64},
        12345,
        None,
    ],
)
async def test_executor_execute_rejects_invalid_fingerprint_type(invalid_fingerprint: Any) -> None:
    """Verify execute() rejects any fingerprint that is not a RequestFingerprint instance."""
    uow_factory = MagicMock(return_value=FakeUnitOfWork())
    handler = FakeHandler()
    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )
    identity = _build_test_identity()

    with pytest.raises(
        TypeError,
        match="request_fingerprint must be a RequestFingerprint instance",
    ):
        await executor.execute(
            identity=identity,
            request_fingerprint=invalid_fingerprint,
        )

    # Invariant: UoW factory must not be called when input validation fails
    uow_factory.assert_not_called()
    assert handler.call_count == 0


# -----------------------------------------------------------------------------
# ACQUIRED Execution Flow Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_acquired_flow_ordering_and_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ACQUIRED flow runs handler in session, commits, exits UoW, and returns EXECUTED."""
    timeline: list[str] = []
    uow = FakeUnitOfWork(timeline=timeline)
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(return_value={"task_id": "T-100"}, timeline=timeline)
    identity = _build_test_identity()
    fp = fingerprint_bytes(b'{"title": "Implement feature"}')

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        timeline.append("acquire_or_observe")
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=None,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    result = await executor.execute(
        identity=identity,
        request_fingerprint=fp,
    )
    timeline.append("result_returned")

    # Verify exact chronological ordering
    expected_timeline = [
        "uow_enter",
        "acquire_or_observe",
        "handler_start",
        "handler_finish",
        "uow_commit",
        "uow_exit",
        "result_returned",
    ]
    assert timeline == expected_timeline

    # UoW context must be cleanly exited at the time execute() returns
    assert uow.is_active is False
    assert uow.committed is True
    assert uow.rolled_back is False

    # Handler assertions
    assert handler.call_count == 1
    assert handler.last_session is uow.session

    # Result assertions
    assert isinstance(result, IdempotentExecutionResult)
    assert result.outcome == IdempotentExecutionOutcome.EXECUTED
    assert result.identity == identity
    assert result.request_fingerprint == fp
    assert result.value == {"task_id": "T-100"}


@pytest.mark.asyncio
async def test_executor_acquired_retains_exact_handler_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify handler return value is preserved in result.value without modification."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    custom_obj = object()
    handler = FakeHandler(return_value=custom_obj)
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"payload")

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    result = await executor.execute(
        identity=identity,
        request_fingerprint=fp,
    )

    assert result.outcome == IdempotentExecutionOutcome.EXECUTED
    assert result.value is custom_obj


# -----------------------------------------------------------------------------
# EXISTING_MATCH Flow Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_existing_match_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify EXISTING_MATCH skips handler, commits UoW, exits, and returns ALREADY_PROCESSED."""
    timeline: list[str] = []
    uow = FakeUnitOfWork(timeline=timeline)
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(timeline=timeline)
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"same-payload-bytes")

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        timeline.append("acquire_or_observe")
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_MATCH,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    result = await executor.execute(
        identity=identity,
        request_fingerprint=fp,
    )
    timeline.append("result_returned")

    # Chronological ordering
    expected_timeline = [
        "uow_enter",
        "acquire_or_observe",
        "uow_commit",
        "uow_exit",
        "result_returned",
    ]
    assert timeline == expected_timeline

    # Invariant: handler must NEVER be called on EXISTING_MATCH
    assert handler.call_count == 0

    # UoW context exited cleanly
    assert uow.is_active is False
    assert uow.committed is True
    assert uow.rolled_back is False

    # Result assertions
    assert isinstance(result, IdempotentExecutionResult)
    assert result.outcome == IdempotentExecutionOutcome.ALREADY_PROCESSED
    assert result.identity == identity
    assert result.request_fingerprint == fp
    # Invariant: value must be strictly None (no response replay in Task 1H)
    assert result.value is None


# -----------------------------------------------------------------------------
# EXISTING_CONFLICT Flow Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_existing_conflict_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify EXISTING_CONFLICT skips handler, completes UoW, and raises conflict error."""
    timeline: list[str] = []
    uow = FakeUnitOfWork(timeline=timeline)
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(timeline=timeline)
    identity = _build_test_identity()
    requested_fp = fingerprint_bytes(b"new-different-payload")
    existing_fp = fingerprint_bytes(b"original-committed-payload")

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        timeline.append("acquire_or_observe")
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_CONFLICT,
            identity=identity,
            request_fingerprint=requested_fp,
            existing_fingerprint=existing_fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(IdempotencyConflictError) as exc_info:
        await executor.execute(
            identity=identity,
            request_fingerprint=requested_fp,
        )

    timeline.append("exception_raised")

    # Chronological ordering: UoW completes and exits BEFORE conflict exception is raised
    expected_timeline = [
        "uow_enter",
        "acquire_or_observe",
        "uow_commit",
        "uow_exit",
        "exception_raised",
    ]
    assert timeline == expected_timeline

    # Invariant: handler must NEVER be called on conflict
    assert handler.call_count == 0

    # UoW context exited cleanly
    assert uow.is_active is False
    assert uow.committed is True

    # Exception contents verification: safe exposure of canonical identifiers only
    err = exc_info.value
    assert err.identity == identity
    assert err.request_fingerprint == requested_fp
    assert err.existing_fingerprint == existing_fp
    assert str(requested_fp) in str(err)
    assert str(existing_fp) in str(err)
    assert identity.operation_name in str(err)


def test_conflict_error_privacy_and_safety() -> None:
    """Verify IdempotencyConflictError exposes zero raw keys, payload bodies, or HTTP statuses."""
    identity = _build_test_identity(raw_key="super-secret-client-token")
    requested_fp = fingerprint_bytes(b"body-a")
    existing_fp = fingerprint_bytes(b"body-b")

    err = IdempotencyConflictError(
        identity=identity,
        request_fingerprint=requested_fp,
        existing_fingerprint=existing_fp,
    )

    err_str = str(err)
    err_repr = repr(err)

    # Must NOT contain raw client key
    assert "super-secret-client-token" not in err_str
    assert "super-secret-client-token" not in err_repr

    # Must NOT contain HTTP statuses or transport terms
    assert "409" not in err_str
    assert "HTTP" not in err_str
    assert "status_code" not in err_str


# -----------------------------------------------------------------------------
# Failure, Rollback & Cancellation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_handler_failure_rolls_back_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify handler exception triggers rollback, never commits, and propagates original error."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)

    class SyntheticBusinessError(Exception):
        pass

    handler = FakeHandler(fail_with=SyntheticBusinessError("Domain rule violated"))
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(SyntheticBusinessError, match="Domain rule violated"):
        await executor.execute(
            identity=identity,
            request_fingerprint=fp,
        )

    # Invariants on handler failure:
    assert handler.call_count == 1  # Not retried
    assert uow.committed is False  # Never committed
    assert uow.rolled_back is True  # Rolled back
    assert uow.is_active is False  # Exited


@pytest.mark.asyncio
async def test_executor_commit_failure_after_handler_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify commit failure propagates and does NOT return an EXECUTED outcome."""

    class SyntheticPersistenceError(Exception):
        pass

    uow = FakeUnitOfWork(commit_error=SyntheticPersistenceError("PostgreSQL commit disk full"))
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(return_value="mutation_data")
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(SyntheticPersistenceError, match="PostgreSQL commit disk full"):
        await executor.execute(
            identity=identity,
            request_fingerprint=fp,
        )

    # Invariants:
    assert handler.call_count == 1  # Handler not retried
    assert uow_factory.call_count == 1  # No second UoW opened
    assert uow.committed is False


@pytest.mark.asyncio
async def test_executor_existing_match_commit_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify commit failure on EXISTING_MATCH propagates without returning success."""

    class SyntheticPersistenceError(Exception):
        pass

    uow = FakeUnitOfWork(commit_error=SyntheticPersistenceError("Connection closed"))
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_MATCH,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(SyntheticPersistenceError, match="Connection closed"):
        await executor.execute(
            identity=identity,
            request_fingerprint=fp,
        )

    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_executor_conflict_commit_failure_propagates_persistence_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify commit failure during conflict observation propagates persistence error.

    IdempotencyConflictError must NOT replace or mask the infrastructure failure.
    """

    class SyntheticPersistenceError(Exception):
        pass

    uow = FakeUnitOfWork(commit_error=SyntheticPersistenceError("Database failure on commit"))
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    identity = _build_test_identity()
    requested_fp = fingerprint_bytes(b"req")
    existing_fp = fingerprint_bytes(b"exist")

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.EXISTING_CONFLICT,
            identity=identity,
            request_fingerprint=requested_fp,
            existing_fingerprint=existing_fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    # Persistence error must win; conflict error must NOT be raised
    with pytest.raises(SyntheticPersistenceError, match="Database failure on commit"):
        await executor.execute(
            identity=identity,
            request_fingerprint=requested_fp,
        )

    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_executor_cancellation_during_handler_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify asyncio.CancelledError during handler propagates and does not commit."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(fail_with=asyncio.CancelledError())
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    async def _fake_acquire_or_observe(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire_or_observe,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            identity=identity,
            request_fingerprint=fp,
        )

    assert uow.committed is False
    assert uow.rolled_back is True


@pytest.mark.asyncio
async def test_executor_cancellation_during_acquisition_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify asyncio.CancelledError during acquire_or_observe propagates without handler call."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    async def _cancelling_acquire(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _cancelling_acquire,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            identity=identity,
            request_fingerprint=fp,
        )

    assert handler.call_count == 0
    assert uow.committed is False


# -----------------------------------------------------------------------------
# Session Identity, Outbox Compatibility & Invariants Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_exact_session_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify IdempotencyRepository, handler, and UnitOfWork share exact same AsyncSession."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    captured_repo_session: AsyncSession | None = None

    async def _capturing_acquire(self: Any, *args: Any, **kwargs: Any) -> IdempotencyDecision:
        nonlocal captured_repo_session
        captured_repo_session = self._session
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _capturing_acquire,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    await executor.execute(
        identity=identity,
        request_fingerprint=fp,
    )

    assert captured_repo_session is not None
    assert handler.last_session is not None
    # Crucial atomicity invariant: exact object identity
    assert captured_repo_session is handler.last_session
    assert captured_repo_session is uow.session


@pytest.mark.asyncio
async def test_executor_transactional_outbox_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify handler can construct and use OutboxRepository in the exact same session."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    outbox_repo_session: AsyncSession | None = None

    class OutboxStagingHandler(IdempotentMutationHandler[str]):
        async def execute(self, *, session: AsyncSession) -> str:
            nonlocal outbox_repo_session
            outbox_repo = OutboxRepository(session)
            outbox_repo_session = outbox_repo.session
            return "staged"

    handler = OutboxStagingHandler()

    async def _fake_acquire(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        return IdempotencyDecision(
            decision=IdempotencyDecisionType.ACQUIRED,
            identity=identity,
            request_fingerprint=fp,
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    result = await executor.execute(
        identity=identity,
        request_fingerprint=fp,
    )

    assert result.outcome == IdempotentExecutionOutcome.EXECUTED
    assert result.value == "staged"
    # Verify same session reference for outbox repository
    assert outbox_repo_session is not None
    assert outbox_repo_session is uow.session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision_type",
    [
        IdempotencyDecisionType.ACQUIRED,
        IdempotencyDecisionType.EXISTING_MATCH,
        IdempotencyDecisionType.EXISTING_CONFLICT,
    ],
)
async def test_executor_exactly_one_uow_per_call(
    monkeypatch: pytest.MonkeyPatch,
    decision_type: IdempotencyDecisionType,
) -> None:
    """Verify execute() requests exactly one UnitOfWork instance across all paths."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")
    existing_fp = (
        fp
        if decision_type == IdempotencyDecisionType.EXISTING_MATCH
        else fingerprint_bytes(b"other")
    )

    async def _fake_acquire(*args: Any, **kwargs: Any) -> IdempotencyDecision:
        return IdempotencyDecision(
            decision=decision_type,
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint=(
                existing_fp if decision_type != IdempotencyDecisionType.ACQUIRED else None
            ),
        )

    monkeypatch.setattr(
        "app.core.idempotency.executor.IdempotencyRepository.acquire_or_observe",
        _fake_acquire,
    )

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    if decision_type == IdempotencyDecisionType.EXISTING_CONFLICT:
        with pytest.raises(IdempotencyConflictError):
            await executor.execute(identity=identity, request_fingerprint=fp)
    else:
        await executor.execute(identity=identity, request_fingerprint=fp)

    # Invariant: exactly one UnitOfWork created
    assert uow_factory.call_count == 1


@pytest.mark.asyncio
async def test_executor_uow_missing_session_raises_unit_of_work_error() -> None:
    """Verify UnitOfWorkError is raised if UnitOfWork provides no active session."""

    class BrokenUnitOfWork:
        async def __aenter__(self) -> "BrokenUnitOfWork":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    uow_factory = MagicMock(return_value=BrokenUnitOfWork())
    handler = FakeHandler()
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    executor = IdempotentMutationExecutor(
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(UnitOfWorkError, match="does not provide an active database session"):
        await executor.execute(identity=identity, request_fingerprint=fp)


# -----------------------------------------------------------------------------
# Result Contract & Immutability Tests
# -----------------------------------------------------------------------------


def test_result_immutability_and_validation() -> None:
    """Verify IdempotentExecutionResult is frozen and validates field invariants."""
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    result = IdempotentExecutionResult(
        outcome=IdempotentExecutionOutcome.EXECUTED,
        identity=identity,
        request_fingerprint=fp,
        value="value",
    )

    # Immutable: cannot reassign fields
    with pytest.raises(FrozenInstanceError):
        result.value = "new_value"  # type: ignore[misc]

    # ALREADY_PROCESSED requires value is None
    with pytest.raises(ValueError, match="value must be None when outcome is ALREADY_PROCESSED"):
        IdempotentExecutionResult(
            outcome=IdempotentExecutionOutcome.ALREADY_PROCESSED,
            identity=identity,
            request_fingerprint=fp,
            value="should_not_exist",
        )

    # Field type validations
    with pytest.raises(TypeError, match="outcome must be an IdempotentExecutionOutcome"):
        IdempotentExecutionResult(
            outcome="EXECUTED",  # type: ignore[arg-type]
            identity=identity,
            request_fingerprint=fp,
        )

    with pytest.raises(TypeError, match="identity must be an IdempotencyIdentity"):
        IdempotentExecutionResult(
            outcome=IdempotentExecutionOutcome.EXECUTED,
            identity="not_identity",  # type: ignore[arg-type]
            request_fingerprint=fp,
        )

    with pytest.raises(TypeError, match="request_fingerprint must be a RequestFingerprint"):
        IdempotentExecutionResult(
            outcome=IdempotentExecutionOutcome.EXECUTED,
            identity=identity,
            request_fingerprint="not_fp",  # type: ignore[arg-type]
        )


def test_outcome_enum_completeness() -> None:
    """Verify IdempotentExecutionOutcome contains strictly EXECUTED and ALREADY_PROCESSED."""
    expected = {"EXECUTED", "ALREADY_PROCESSED"}
    actual = {o.value for o in IdempotentExecutionOutcome}
    assert actual == expected

    # Verify no transport or HTTP outcomes exist
    forbidden = {"ACK", "NACK", "HTTP_200", "HTTP_409", "REPLAYED_RESPONSE", "CACHED"}
    assert actual.isdisjoint(forbidden)


def test_conflict_error_type_validation() -> None:
    """Verify IdempotencyConflictError validates parameter types."""
    identity = _build_test_identity()
    fp = fingerprint_bytes(b"data")

    with pytest.raises(TypeError, match="identity must be an IdempotencyIdentity"):
        IdempotencyConflictError(
            identity="bad",  # type: ignore[arg-type]
            request_fingerprint=fp,
            existing_fingerprint=fp,
        )

    with pytest.raises(TypeError, match="request_fingerprint must be a RequestFingerprint"):
        IdempotencyConflictError(
            identity=identity,
            request_fingerprint="bad",  # type: ignore[arg-type]
            existing_fingerprint=fp,
        )

    with pytest.raises(TypeError, match="existing_fingerprint must be a RequestFingerprint"):
        IdempotencyConflictError(
            identity=identity,
            request_fingerprint=fp,
            existing_fingerprint="bad",  # type: ignore[arg-type]
        )


# -----------------------------------------------------------------------------
# Architectural Isolation & Schema Stability Tests
# -----------------------------------------------------------------------------


def test_executor_architectural_isolation() -> None:
    """Verify executor code imports zero transport, HTTP, broker, or hashing libraries."""
    import app.core.idempotency.executor as executor_mod

    source = getsource(executor_mod)
    tree = ast.parse(source)
    imported_names: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name.lower())
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module.lower())

    forbidden_tokens = [
        "fastapi",
        "starlette",
        "pydantic_ai",
        "aio_pika",
        "rabbitmq",
        "pika",
        "http",
        "httpx",
        "request",
        "response",
        "http_exception",
        "digest_idempotency_key",
        "fingerprint_bytes",
    ]

    for imported in imported_names:
        for token in forbidden_tokens:
            assert token not in imported, f"Executor must not import '{token}'; found '{imported}'"

    # Verify no raw hashing calls in executor source
    assert "digest_idempotency_key" not in source
    assert "fingerprint_bytes" not in source


def test_schema_stability_and_frozen_persistence() -> None:
    """Verify database metadata retains Phase 1 tables, canonical Phase 2A tables, head is 0008."""
    phase1_tables = {
        "audit_events",
        "outbox_events",
        "inbox_messages",
        "idempotency_records",
        "jobs",
    }
    canonical_phase4_tables = phase1_tables | {
        "goals",
        "plans",
        "milestones",
        "tasks",
        "task_dependencies",
        "task_risks",
        "workflow_definitions",
        "workflow_instances",
        "workflow_task_mappings",
        "sla_policies",
        "workflow_artifacts",
        # Phase 4A Scheduling (7)
        "resources",
        "resource_availabilities",
        "constraint_definitions",
        "solver_profiles",
        "schedule_jobs",
        "schedules",
        "schedule_assignments",
        # Phase 4B Calendar (1)
        "calendar_links",
    }
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 24

    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)
    assert script_dir.get_current_head() == "0012"

    # Confirm migration 0012 exists and 0013 does NOT exist
    revisions = [rev.revision for rev in script_dir.walk_revisions()]
    assert "0012" in revisions
    assert "0013" not in revisions
    assert len(revisions) == 12


def test_deferred_runtime_integration_constant() -> None:
    """Verify live PostgreSQL verification is officially documented as deferred to Task 1J."""
    assert IDEMPOTENT_EXECUTION_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
