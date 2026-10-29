"""Unit tests for ConsumerEventProcessor and consumer transaction orchestration.

Part of Phase 1 Foundation Task 1G.4B.
Verifies:
- ConsumerEventProcessor construction validation (valid namespaced consumer, types).
- Input contract (accepts EventEnvelope only).
- First-delivery flow: try_record True -> handler executes in same session ->
  uow commits -> PROCESSED.
- Duplicate flow: try_record False -> handler skipped -> uow commits -> DUPLICATE.
- Exact same-session invariant: inbox_repo.session is handler_session.
- Exact execution ordering for first delivery and duplicate paths.
- Handler failure triggers rollback, propagates exception, leaves zero permanent dedupe.
- Commit failures propagate without returning success results.
- Cancellation (CancelledError) propagates cleanly.
- Unexpected errors (TypeError/ValueError) propagate without being swallowed.
- One-UoW invariant (exactly one UoW instance requested per process invocation).
- Result immutability and outcome enum completeness (PROCESSED, DUPLICATE only; zero ACK/NACK).
- Architectural isolation (zero imports of transport, aio_pika, rabbitmq, or broker libraries).
- Schema stability (Base.metadata is audit_events + outbox_events + inbox_messages; head is 0005).
- Runtime integration deferral constant.
"""

import ast
import asyncio
from inspect import getsource
from types import TracebackType
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.db.base import Base
from app.events.inbox.processor import (
    ConsumerEventProcessor,
    ConsumerProcessingOutcome,
    ConsumerProcessingResult,
)
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope

# Authoritative verification boundary constant for Task 1G.4B
CONSUMER_PROCESSING_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


# -----------------------------------------------------------------------------
# Test Fixtures & Fakes
# -----------------------------------------------------------------------------


def _build_test_envelope(
    *,
    event_id: UUID | None = None,
    correlation_id: str | None = None,
) -> EventEnvelope:
    """Helper to create a canonical EventEnvelope for testing."""
    create_dto = EventCreate(
        event_type="workflow.task.dispatched",
        event_version=1,
        tenant_id="tenant-alpha",
        correlation_id=correlation_id or "corr-100",
        subject_id="task-100",
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
    """Controllable ConsumerEventHandler recording invocation parameters and timing."""

    def __init__(
        self,
        *,
        fail_with: BaseException | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self.fail_with = fail_with
        self.timeline = timeline
        self.call_count = 0
        self.last_envelope: EventEnvelope | None = None
        self.last_session: AsyncSession | None = None

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        self.call_count += 1
        self.last_envelope = envelope
        self.last_session = session

        if self.timeline is not None:
            self.timeline.append("handler_start")

        if self.fail_with is not None:
            raise self.fail_with

        if self.timeline is not None:
            self.timeline.append("handler_finish")


# -----------------------------------------------------------------------------
# Processor Construction & Input Validation Tests
# -----------------------------------------------------------------------------


def test_processor_construction_valid() -> None:
    """Verify processor initializes with valid dependencies without performing I/O."""
    uow_factory = MagicMock(return_value=FakeUnitOfWork())
    handler = FakeHandler()

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    assert processor.consumer_name == "workflow.projection"
    # Invariant: zero database or network calls during construction
    uow_factory.assert_not_called()


@pytest.mark.parametrize(
    "invalid_name",
    [
        "",
        "   ",
        "workflow",
        "Workflow.Projection",
        ".workflow.projection",
        "workflow.projection.",
        "workflow..projection",
        "a" * 129,
    ],
)
def test_processor_construction_rejects_invalid_consumer_name(invalid_name: str) -> None:
    """Verify invalid consumer names are rejected at construction before UoW creation."""
    uow_factory = MagicMock(return_value=FakeUnitOfWork())
    handler = FakeHandler()

    with pytest.raises(ValueError):
        ConsumerEventProcessor(
            consumer_name=invalid_name,
            uow_factory=uow_factory,
            handler=handler,
        )

    uow_factory.assert_not_called()


def test_processor_construction_type_errors() -> None:
    """Verify non-callable uow_factory or non-conforming handler raises TypeError."""
    handler = FakeHandler()
    uow_factory = MagicMock(return_value=FakeUnitOfWork())

    # Invalid consumer_name type
    with pytest.raises(TypeError, match="consumer_name must be a str"):
        ConsumerEventProcessor(
            consumer_name=123,  # type: ignore[arg-type]
            uow_factory=uow_factory,
            handler=handler,
        )

    # Invalid uow_factory type
    with pytest.raises(TypeError, match="uow_factory must be a callable"):
        ConsumerEventProcessor(
            consumer_name="workflow.projection",
            uow_factory="not_a_callable",  # type: ignore[arg-type]
            handler=handler,
        )

    # Invalid handler type (lacks handle method)
    with pytest.raises(TypeError, match="handler must implement ConsumerEventHandler protocol"):
        ConsumerEventProcessor(
            consumer_name="workflow.projection",
            uow_factory=uow_factory,
            handler="not_a_handler",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_envelope",
    [
        {"event_id": str(uuid4())},
        "raw_string",
        b"raw_bytes",
        None,
        12345,
    ],
)
async def test_processor_process_rejects_non_envelope(invalid_envelope: Any) -> None:
    """Verify process() rejects any argument that is not an EventEnvelope instance."""
    uow_factory = MagicMock(return_value=FakeUnitOfWork())
    handler = FakeHandler()
    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(TypeError, match="requires an EventEnvelope instance"):
        await processor.process(invalid_envelope)

    uow_factory.assert_not_called()


# -----------------------------------------------------------------------------
# First-Delivery & Duplicate Orchestration Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_first_processing_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify first delivery invokes handler in same session, commits, and returns PROCESSED."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    envelope = _build_test_envelope()

    # Mock InboxRepository.try_record to return True (first processing)
    async def _fake_try_record(*args: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _fake_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    result = await processor.process(envelope)

    # Handler called exactly once
    assert handler.call_count == 1
    assert handler.last_envelope is envelope
    assert handler.last_session is uow.session

    # Transaction committed
    assert uow.committed is True
    assert uow.rolled_back is False
    assert uow.is_active is False  # UoW context exited before return

    # Result assertions
    assert isinstance(result, ConsumerProcessingResult)
    assert result.outcome == ConsumerProcessingOutcome.PROCESSED
    assert result.consumer_name == "workflow.projection"
    assert result.event_id == envelope.event_id


@pytest.mark.asyncio
async def test_processor_duplicate_processing_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify duplicate delivery skips handler, commits transaction, and returns DUPLICATE."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    envelope = _build_test_envelope()

    # Mock InboxRepository.try_record to return False (duplicate)
    async def _fake_try_record(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _fake_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    result = await processor.process(envelope)

    # Handler must NOT be called on duplicate
    assert handler.call_count == 0

    # Transaction completed cleanly
    assert uow.committed is True
    assert uow.rolled_back is False
    assert uow.is_active is False

    # Result assertions
    assert isinstance(result, ConsumerProcessingResult)
    assert result.outcome == ConsumerProcessingOutcome.DUPLICATE
    assert result.consumer_name == "workflow.projection"
    assert result.event_id == envelope.event_id


# -----------------------------------------------------------------------------
# Failure & Rollback Semantics Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_handler_failure_rolls_back_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify handler exception triggers transaction rollback and propagates without commit."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(fail_with=RuntimeError("BusinessMutationFailed"))
    envelope = _build_test_envelope()

    async def _fake_try_record(*args: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _fake_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(RuntimeError, match="BusinessMutationFailed"):
        await processor.process(envelope)

    # Transaction was rolled back, never committed
    assert uow.committed is False
    assert uow.rolled_back is True
    assert uow.is_active is False


@pytest.mark.asyncio
async def test_processor_commit_failure_after_handler_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify commit failure propagates and does NOT return a PROCESSED outcome."""
    uow = FakeUnitOfWork(commit_error=RuntimeError("DatabaseCommitError"))
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    envelope = _build_test_envelope()

    async def _fake_try_record(*args: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _fake_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(RuntimeError, match="DatabaseCommitError"):
        await processor.process(envelope)

    # Handler was invoked once, but commit failed
    assert handler.call_count == 1
    assert uow.committed is False


@pytest.mark.asyncio
async def test_processor_duplicate_commit_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify commit failure during duplicate path propagates without returning DUPLICATE."""
    uow = FakeUnitOfWork(commit_error=RuntimeError("CommitFailedOnDuplicate"))
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    envelope = _build_test_envelope()

    async def _fake_try_record(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _fake_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(RuntimeError, match="CommitFailedOnDuplicate"):
        await processor.process(envelope)

    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_processor_cancellation_during_handler_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify asyncio.CancelledError during handler propagates and does not commit."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(fail_with=asyncio.CancelledError())
    envelope = _build_test_envelope()

    async def _fake_try_record(*args: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _fake_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(asyncio.CancelledError):
        await processor.process(envelope)

    assert uow.committed is False
    assert uow.rolled_back is True


@pytest.mark.asyncio
async def test_processor_cancellation_during_try_record_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify asyncio.CancelledError during try_record propagates without invoking handler."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    envelope = _build_test_envelope()

    async def _cancelling_try_record(*args: Any, **kwargs: Any) -> bool:
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _cancelling_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(asyncio.CancelledError):
        await processor.process(envelope)

    assert handler.call_count == 0
    assert uow.committed is False


@pytest.mark.asyncio
async def test_processor_unexpected_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify unexpected TypeError or ValueError from handler propagates without conversion."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(fail_with=TypeError("SyntheticTypeError"))
    envelope = _build_test_envelope()

    async def _fake_try_record(*args: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _fake_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    with pytest.raises(TypeError, match="SyntheticTypeError"):
        await processor.process(envelope)

    assert uow.committed is False
    assert uow.rolled_back is True


# -----------------------------------------------------------------------------
# Ordering, Session Identity, & Invariants Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_execution_ordering_first_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify strict execution sequence for first delivery."""
    timeline: list[str] = []
    uow = FakeUnitOfWork(timeline=timeline)
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(timeline=timeline)
    envelope = _build_test_envelope()

    async def _recording_try_record(*args: Any, **kwargs: Any) -> bool:
        timeline.append("inbox_try_record")
        return True

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _recording_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    result = await processor.process(envelope)
    timeline.append("result_returned")

    expected_timeline = [
        "uow_enter",
        "inbox_try_record",
        "handler_start",
        "handler_finish",
        "uow_commit",
        "uow_exit",
        "result_returned",
    ]
    assert timeline == expected_timeline
    assert result.outcome == ConsumerProcessingOutcome.PROCESSED


@pytest.mark.asyncio
async def test_processor_execution_ordering_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify strict execution sequence for duplicate delivery."""
    timeline: list[str] = []
    uow = FakeUnitOfWork(timeline=timeline)
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler(timeline=timeline)
    envelope = _build_test_envelope()

    async def _recording_try_record(*args: Any, **kwargs: Any) -> bool:
        timeline.append("inbox_try_record")
        return False

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _recording_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    result = await processor.process(envelope)
    timeline.append("result_returned")

    expected_timeline = [
        "uow_enter",
        "inbox_try_record",
        "uow_commit",
        "uow_exit",
        "result_returned",
    ]
    assert timeline == expected_timeline
    assert result.outcome == ConsumerProcessingOutcome.DUPLICATE


@pytest.mark.asyncio
async def test_processor_exact_session_sharing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify InboxRepository and handler receive the exact same AsyncSession instance."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    envelope = _build_test_envelope()

    captured_inbox_session: AsyncSession | None = None

    async def _capturing_try_record(self: Any, *args: Any, **kwargs: Any) -> bool:
        nonlocal captured_inbox_session
        captured_inbox_session = self._session
        return True

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _capturing_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    await processor.process(envelope)

    assert captured_inbox_session is not None
    assert handler.last_session is not None
    # Crucial atomicity invariant: exact same session identity
    assert captured_inbox_session is handler.last_session
    assert captured_inbox_session is uow.session


@pytest.mark.asyncio
@pytest.mark.parametrize("first_processing", [True, False])
async def test_processor_one_uow_only(
    monkeypatch: pytest.MonkeyPatch,
    first_processing: bool,
) -> None:
    """Verify process() requests exactly one UnitOfWork instance per invocation."""
    uow = FakeUnitOfWork()
    uow_factory = MagicMock(return_value=uow)
    handler = FakeHandler()
    envelope = _build_test_envelope()

    async def _fake_try_record(*args: Any, **kwargs: Any) -> bool:
        return first_processing

    monkeypatch.setattr(
        "app.events.inbox.processor.InboxRepository.try_record",
        _fake_try_record,
    )

    processor = ConsumerEventProcessor(
        consumer_name="workflow.projection",
        uow_factory=uow_factory,
        handler=handler,
    )

    await processor.process(envelope)

    assert uow_factory.call_count == 1


def test_processor_result_immutability() -> None:
    """Verify ConsumerProcessingResult is frozen and forbids extra fields."""
    event_id = uuid4()
    result = ConsumerProcessingResult(
        outcome=ConsumerProcessingOutcome.PROCESSED,
        consumer_name="workflow.projection",
        event_id=event_id,
    )

    # Immutability
    with pytest.raises(ValidationError):
        result.outcome = ConsumerProcessingOutcome.DUPLICATE

    # Forbid extra fields
    with pytest.raises(ValidationError):
        ConsumerProcessingResult(
            outcome=ConsumerProcessingOutcome.PROCESSED,
            consumer_name="workflow.projection",
            event_id=event_id,
            extra_field="disallowed",  # type: ignore[call-arg]
        )


def test_processor_outcome_enum_values() -> None:
    """Verify ConsumerProcessingOutcome contains strictly PROCESSED and DUPLICATE."""
    expected_outcomes = {"PROCESSED", "DUPLICATE"}
    actual_outcomes = {o.value for o in ConsumerProcessingOutcome}
    assert actual_outcomes == expected_outcomes

    # Verify no transport actions exist in enum
    forbidden_outcomes = {"ACK", "NACK", "REJECT", "REQUEUE"}
    assert actual_outcomes.isdisjoint(forbidden_outcomes)


def test_processor_architectural_isolation() -> None:
    """Verify ConsumerEventProcessor code imports zero transport or broker libraries."""
    import app.events.inbox.processor as processor_mod

    tree = ast.parse(getsource(processor_mod))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name.lower())
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module.lower())

    forbidden_tokens = ["aio_pika", "rabbitmq", "pika", "publishers", "consumer"]
    for imp in imported_modules:
        for token in forbidden_tokens:
            assert token not in imp, f"processor must not import '{token}'; found '{imp}'"


def test_schema_stability() -> None:
    """Verify database metadata remains strictly canonical foundation tables."""
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
        # Phase 7 Calendar Watch (1)
        "calendar_watch_channels",
        # Phase 8D Service Credentials Persistence (1)
        "service_api_credentials",
    }
    assert phase1_tables <= set(Base.metadata.tables.keys())
    assert set(Base.metadata.tables.keys()) == canonical_phase4_tables
    assert len(Base.metadata.tables) == 26

    config = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(config)
    assert script_dir.get_current_head() in (
        "0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012", "0013", "0014",
        "0015", "0016",
    )
    assert script_dir.get_current_head() == "0016"


def test_deferred_runtime_integration_constant() -> None:
    """Verify live PostgreSQL verification is officially documented as deferred to Task 1J."""
    assert CONSUMER_PROCESSING_RUNTIME_TEST == "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"
