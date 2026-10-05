"""Unit tests for OutboxDispatcher, OutboxDispatchPolicy, and DispatchResult.

Part of Phase 1 Foundation Task 1G.3B2.
Validates:
- OutboxDispatchPolicy validation and deterministic capped exponential backoff.
- DispatchResult immutability and outcome enumeration.
- Exact transaction/network ordering:
  claim transaction -> commit -> exit UoW -> publish -> settlement transaction -> commit.
- Strict isolation: zero open UnitOfWork or DB transactions during publisher network calls.
- Idle handling: limit=1, zero events returns IDLE, zero publish calls, zero settlement UoW.
- Single-event invariant: claim_ready returning >1 rows raises RuntimeError.
- Claim commit failure: prevents publisher execution, propagates exception.
- Publish success: fresh settlement UoW, mark_published, returns PUBLISHED.
- Publish failure: fresh settlement UoW, reschedule_failure, returns RETRY_SCHEDULED.
- Ambiguous timeout: treated as retryable publication failure.
- Claim lost after publish: returns CLAIM_LOST_AFTER_PUBLISH without rescheduling.
- Claim lost after failure: returns CLAIM_LOST_AFTER_FAILURE without retrying.
- Settlement DB failure: propagates infrastructure exception.
- Unexpected publisher errors and asyncio.CancelledError: propagate cleanly without being swallowed.
- Event identity: publishes exact canonical envelope; claim_token never enters envelope.
- Architectural boundaries: zero broker transport, loops, or sleep calls.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.events.outbox.dispatch import ClaimedOutboxEvent
from app.events.outbox.dispatch_repository import OutboxClaimLostError
from app.events.outbox.dispatcher import (
    DispatchOutcome,
    DispatchResult,
    OutboxDispatcher,
    OutboxDispatchPolicy,
)
from app.events.outbox.models import OutboxEventRecord
from app.events.publishers.base import (
    EventPublishError,
    PublisherConnectionError,
    PublisherNotStartedError,
    PublisherStateError,
)
from app.events.schemas import EventEnvelope

# -----------------------------------------------------------------------------
# Test Helpers & Fakes
# -----------------------------------------------------------------------------


def _build_test_envelope(event_id: UUID | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id or uuid4(),
        event_type="test.dispatch.orchestration",
        event_version=1,
        occurred_at=datetime.now(UTC),
        payload={"key": "test_payload"},
    )


def _build_test_record(
    event_id: UUID | None = None,
    envelope: EventEnvelope | None = None,
    dispatch_attempt_count: int = 0,
) -> OutboxEventRecord:
    eid = event_id or uuid4()
    env = envelope or _build_test_envelope(event_id=eid)
    return OutboxEventRecord(
        event_id=eid,
        envelope=env.model_dump(mode="json"),
        created_at=datetime.now(UTC),
        published_at=None,
        dispatch_attempt_count=dispatch_attempt_count,
        next_attempt_at=datetime.now(UTC),
        claim_token=None,
        claimed_at=None,
    )


def _build_test_policy(
    lease_duration: timedelta = timedelta(seconds=30),
    publish_timeout: timedelta = timedelta(seconds=5),
    retry_base_delay: timedelta = timedelta(seconds=1),
    retry_max_delay: timedelta = timedelta(seconds=60),
    retry_multiplier: float = 2.0,
) -> OutboxDispatchPolicy:
    return OutboxDispatchPolicy(
        lease_duration=lease_duration,
        publish_timeout=publish_timeout,
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
        retry_multiplier=retry_multiplier,
    )


class FakePublisher:
    """Fake provider-neutral EventPublisher implementing EventPublisher protocol."""

    def __init__(
        self,
        *,
        fail_with: Exception | None = None,
        delay_seconds: float = 0.0,
        on_publish_check: Any = None,
    ) -> None:
        self.published_envelopes: list[EventEnvelope] = []
        self.fail_with = fail_with
        self.delay_seconds = delay_seconds
        self.on_publish_check = on_publish_check

    async def publish(self, envelope: EventEnvelope) -> None:
        if self.on_publish_check:
            self.on_publish_check()
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.fail_with:
            raise self.fail_with
        self.published_envelopes.append(envelope)


class RecordingUnitOfWork:
    """Test fake for UnitOfWork recording operations and tracking active UoW count."""

    active_count = 0

    def __init__(
        self,
        *,
        records_to_claim: tuple[OutboxEventRecord, ...] = (),
        commit_error: Exception | None = None,
        mark_error: Exception | None = None,
        reschedule_error: Exception | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self.records_to_claim = records_to_claim
        self.commit_error = commit_error
        self.mark_error = mark_error
        self.reschedule_error = reschedule_error
        self.timeline = timeline if timeline is not None else []
        self.committed = False
        self.rolled_back = False

        # Session mock with execute support for OutboxDispatchRepository
        self.session = MagicMock()
        self._setup_session_mock()

    def _setup_session_mock(self) -> None:
        async def mock_execute(stmt: Any) -> Any:
            sql_text = str(stmt).lower()
            if "select" in sql_text:
                self.timeline.append("claim_ready")
                result = MagicMock()
                result.scalars.return_value.all.return_value = list(self.records_to_claim)
                return result
            elif (
                "update" in sql_text
                and "published_at" in sql_text
                and "next_attempt_at" not in sql_text
            ):
                self.timeline.append("mark_published")
                if self.mark_error:
                    raise self.mark_error
                res = MagicMock()
                res.rowcount = 1
                return res
            elif "update" in sql_text and "next_attempt_at" in sql_text:
                self.timeline.append("reschedule_failure")
                if self.reschedule_error:
                    raise self.reschedule_error
                res = MagicMock()
                res.rowcount = 1
                return res
            res = MagicMock()
            res.rowcount = 1
            return res

        self.session.execute = mock_execute

    async def __aenter__(self) -> "RecordingUnitOfWork":
        RecordingUnitOfWork.active_count += 1
        self.timeline.append("uow_enter")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        RecordingUnitOfWork.active_count -= 1
        self.timeline.append("uow_exit")

    async def commit(self) -> None:
        self.timeline.append("uow_commit")
        if self.commit_error:
            raise self.commit_error
        self.committed = True

    async def rollback(self) -> None:
        self.timeline.append("uow_rollback")
        self.rolled_back = True


# -----------------------------------------------------------------------------
# OutboxDispatchPolicy Tests
# -----------------------------------------------------------------------------


def test_policy_validation_valid() -> None:
    """Verify valid policy construction without default values."""
    policy = OutboxDispatchPolicy(
        lease_duration=timedelta(seconds=45),
        publish_timeout=timedelta(seconds=10),
        retry_base_delay=timedelta(seconds=2),
        retry_max_delay=timedelta(seconds=120),
        retry_multiplier=2.5,
    )
    assert policy.lease_duration == timedelta(seconds=45)
    assert policy.publish_timeout == timedelta(seconds=10)
    assert policy.retry_base_delay == timedelta(seconds=2)
    assert policy.retry_max_delay == timedelta(seconds=120)
    assert policy.retry_multiplier == 2.5

    # Boundary acceptance: retry_multiplier == 1.0
    policy_min_mult = _build_test_policy(retry_multiplier=1.0)
    assert policy_min_mult.retry_multiplier == 1.0

    # Acceptance: retry_multiplier == 2.0
    policy_two_mult = _build_test_policy(retry_multiplier=2.0)
    assert policy_two_mult.retry_multiplier == 2.0


@pytest.mark.parametrize(
    "kwargs,expected_err",
    [
        (
            {
                "lease_duration": timedelta(seconds=0),
                "publish_timeout": timedelta(seconds=5),
                "retry_base_delay": timedelta(seconds=1),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": 2.0,
            },
            "lease_duration must be strictly positive",
        ),
        (
            {
                "lease_duration": timedelta(seconds=10),
                "publish_timeout": timedelta(seconds=0),
                "retry_base_delay": timedelta(seconds=1),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": 2.0,
            },
            "publish_timeout must be strictly positive",
        ),
        (
            {
                "lease_duration": timedelta(seconds=10),
                "publish_timeout": timedelta(seconds=10),
                "retry_base_delay": timedelta(seconds=1),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": 2.0,
            },
            r"publish_timeout \(0:00:10\) must be strictly less than lease_duration \(0:00:10\)",
        ),
        (
            {
                "lease_duration": timedelta(seconds=10),
                "publish_timeout": timedelta(seconds=15),
                "retry_base_delay": timedelta(seconds=1),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": 2.0,
            },
            r"publish_timeout \(0:00:15\) must be strictly less than lease_duration \(0:00:10\)",
        ),
        (
            {
                "lease_duration": timedelta(seconds=30),
                "publish_timeout": timedelta(seconds=5),
                "retry_base_delay": timedelta(seconds=0),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": 2.0,
            },
            "retry_base_delay must be strictly positive",
        ),
        (
            {
                "lease_duration": timedelta(seconds=30),
                "publish_timeout": timedelta(seconds=5),
                "retry_base_delay": timedelta(seconds=15),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": 2.0,
            },
            (
                r"retry_max_delay \(0:00:10\) must be greater than or equal to "
                r"retry_base_delay \(0:00:15\)"
            ),
        ),
        (
            {
                "lease_duration": timedelta(seconds=30),
                "publish_timeout": timedelta(seconds=5),
                "retry_base_delay": timedelta(seconds=1),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": 0.5,
            },
            r"retry_multiplier must be a finite number >= 1.0",
        ),
        (
            {
                "lease_duration": timedelta(seconds=30),
                "publish_timeout": timedelta(seconds=5),
                "retry_base_delay": timedelta(seconds=1),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": float("nan"),
            },
            r"retry_multiplier must be a finite number >= 1.0",
        ),
        (
            {
                "lease_duration": timedelta(seconds=30),
                "publish_timeout": timedelta(seconds=5),
                "retry_base_delay": timedelta(seconds=1),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": float("inf"),
            },
            r"retry_multiplier must be a finite number >= 1.0",
        ),
        (
            {
                "lease_duration": timedelta(seconds=30),
                "publish_timeout": timedelta(seconds=5),
                "retry_base_delay": timedelta(seconds=1),
                "retry_max_delay": timedelta(seconds=10),
                "retry_multiplier": float("-inf"),
            },
            r"retry_multiplier must be a finite number >= 1.0",
        ),
    ],
)
def test_policy_validation_rejections(kwargs: dict[str, Any], expected_err: str) -> None:
    """Verify invalid policy configurations are rejected strictly."""
    with pytest.raises(ValueError, match=expected_err):
        OutboxDispatchPolicy(**kwargs)


def test_policy_is_immutable() -> None:
    """Verify OutboxDispatchPolicy is frozen and forbids extra fields."""
    policy = _build_test_policy()
    with pytest.raises(ValidationError):
        policy.retry_multiplier = 3.0  # type: ignore[misc]

    with pytest.raises(ValidationError):
        OutboxDispatchPolicy(
            lease_duration=timedelta(seconds=30),
            publish_timeout=timedelta(seconds=5),
            retry_base_delay=timedelta(seconds=1),
            retry_max_delay=timedelta(seconds=10),
            retry_multiplier=2.0,
            unexpected_field="disallowed",  # type: ignore[call-arg]
        )


def test_policy_deterministic_backoff_calculation() -> None:
    """Verify capped exponential backoff computation without overflow."""
    policy = OutboxDispatchPolicy(
        lease_duration=timedelta(seconds=60),
        publish_timeout=timedelta(seconds=5),
        retry_base_delay=timedelta(seconds=2),
        retry_max_delay=timedelta(seconds=30),
        retry_multiplier=2.0,
    )

    # attempt 1: base delay (2s)
    assert policy.compute_retry_delay(1) == timedelta(seconds=2)
    # attempt 0 or negative safe fallback
    assert policy.compute_retry_delay(0) == timedelta(seconds=2)

    # attempt 2: base * 2^1 = 4s
    assert policy.compute_retry_delay(2) == timedelta(seconds=4)

    # attempt 3: base * 2^2 = 8s
    assert policy.compute_retry_delay(3) == timedelta(seconds=8)

    # attempt 4: base * 2^3 = 16s
    assert policy.compute_retry_delay(4) == timedelta(seconds=16)

    # attempt 5: base * 2^4 = 32s -> capped at 30s
    assert policy.compute_retry_delay(5) == timedelta(seconds=30)

    # attempt 10: capped at 30s
    assert policy.compute_retry_delay(10) == timedelta(seconds=30)

    # very high attempt count does not overflow float and saturates stably
    assert policy.compute_retry_delay(1_000_000) == timedelta(seconds=30)


# -----------------------------------------------------------------------------
# DispatchResult & Outcome Contract Tests
# -----------------------------------------------------------------------------


def test_dispatch_result_immutability() -> None:
    """Verify DispatchResult is immutable and validates fields."""
    res = DispatchResult(outcome=DispatchOutcome.IDLE)
    assert res.outcome == DispatchOutcome.IDLE
    assert res.event_id is None
    assert res.dispatch_attempt_count is None
    assert res.retry_delay is None

    with pytest.raises(ValidationError):
        res.outcome = DispatchOutcome.PUBLISHED  # type: ignore[misc]

    with pytest.raises(ValidationError):
        DispatchResult(
            outcome=DispatchOutcome.IDLE,
            unknown_arg="invalid",  # type: ignore[call-arg]
        )


def test_dispatch_outcomes_enumeration() -> None:
    """Verify all 5 required outcome enum values exist."""
    assert DispatchOutcome.IDLE == "IDLE"
    assert DispatchOutcome.PUBLISHED == "PUBLISHED"
    assert DispatchOutcome.RETRY_SCHEDULED == "RETRY_SCHEDULED"
    assert DispatchOutcome.CLAIM_LOST_AFTER_PUBLISH == "CLAIM_LOST_AFTER_PUBLISH"
    assert DispatchOutcome.CLAIM_LOST_AFTER_FAILURE == "CLAIM_LOST_AFTER_FAILURE"


# -----------------------------------------------------------------------------
# OutboxDispatcher Core Orchestration Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_once_idle() -> None:
    """Verify idle flow when zero events are ready to claim."""
    timeline: list[str] = []
    uow = RecordingUnitOfWork(records_to_claim=(), timeline=timeline)
    publisher = FakePublisher()
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: uow,
        publisher=publisher,
        policy=policy,
    )

    result = await dispatcher.dispatch_once()

    assert result.outcome == DispatchOutcome.IDLE
    assert result.event_id is None
    assert result.dispatch_attempt_count is None
    assert len(publisher.published_envelopes) == 0
    # Exact timeline: only 1 UoW entered, claimed, exited without commit
    assert timeline == ["uow_enter", "claim_ready", "uow_exit"]


@pytest.mark.asyncio
async def test_dispatch_once_success_ordering_and_no_open_uow_during_publish() -> None:
    """Verify exact execution order and ensure zero active UoWs during network publish."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []

    # Prepare two separate UoW instances: one for claim, one for settlement
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    settlement_uow = RecordingUnitOfWork(timeline=timeline)
    uow_instances = [claim_uow, settlement_uow]

    def uow_factory() -> RecordingUnitOfWork:
        return uow_instances.pop(0)

    # Check that active UoW count is strictly 0 when publisher executes
    def on_publish() -> None:
        timeline.append("publish_called")
        assert RecordingUnitOfWork.active_count == 0, (
            "No UoW may be open during network publication!"
        )

    publisher = FakePublisher(on_publish_check=on_publish)
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=uow_factory,
        publisher=publisher,
        policy=policy,
    )

    result = await dispatcher.dispatch_once()

    assert result.outcome == DispatchOutcome.PUBLISHED
    assert result.event_id == event_id
    assert result.dispatch_attempt_count == 1
    assert len(publisher.published_envelopes) == 1
    assert publisher.published_envelopes[0] == envelope

    # Verify exact mandatory execution order:
    expected_timeline = [
        "uow_enter",  # Claim UoW entered
        "claim_ready",  # claim_ready executed
        "uow_commit",  # Claim committed
        "uow_exit",  # Claim UoW exited completely
        "publish_called",  # Network publication occurs outside any UoW
        "uow_enter",  # Settlement UoW entered
        "mark_published",  # mark_published executed
        "uow_commit",  # Settlement committed
        "uow_exit",  # Settlement UoW exited
    ]
    assert timeline == expected_timeline


@pytest.mark.asyncio
async def test_dispatch_once_claim_commit_failure_prevents_publish() -> None:
    """Verify that a claim transaction commit failure aborts before network publication."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(
        records_to_claim=(record,),
        commit_error=RuntimeError("Database commit failure"),
        timeline=timeline,
    )
    publisher = FakePublisher()
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: claim_uow,
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(RuntimeError, match="Database commit failure"):
        await dispatcher.dispatch_once()

    # Network publication must NOT have been called
    assert len(publisher.published_envelopes) == 0
    assert "publish_called" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_publish_failure_triggers_reschedule() -> None:
    """Verify expected EventPublishError triggers retry rescheduling with backoff delay."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    # Record starts with dispatch_attempt_count=1 -> claim_ready increments it to 2
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=1)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    settlement_uow = RecordingUnitOfWork(timeline=timeline)
    uow_instances = [claim_uow, settlement_uow]

    publisher = FakePublisher(fail_with=EventPublishError("Connection reset by peer"))
    policy = _build_test_policy(
        retry_base_delay=timedelta(seconds=1),
        retry_multiplier=2.0,
    )

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: uow_instances.pop(0),
        publisher=publisher,
        policy=policy,
    )

    result = await dispatcher.dispatch_once()

    assert result.outcome == DispatchOutcome.RETRY_SCHEDULED
    assert result.event_id == event_id
    assert result.dispatch_attempt_count == 2
    assert result.retry_delay == timedelta(seconds=2)

    # Verify timeline indicates reschedule_failure was called and committed
    assert "claim_ready" in timeline
    assert "reschedule_failure" in timeline
    assert "mark_published" not in timeline
    assert settlement_uow.committed is True


@pytest.mark.asyncio
async def test_dispatch_once_publish_timeout_triggers_reschedule() -> None:
    """Verify publication exceeding publish_timeout is treated as ambiguous retryable failure."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    settlement_uow = RecordingUnitOfWork(timeline=timeline)
    uow_instances = [claim_uow, settlement_uow]

    # Publisher simulates a hanging network call (0.2s > 0.05s timeout)
    publisher = FakePublisher(delay_seconds=0.2)
    policy = _build_test_policy(
        lease_duration=timedelta(seconds=5),
        publish_timeout=timedelta(seconds=0.05),
        retry_base_delay=timedelta(seconds=3),
    )

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: uow_instances.pop(0),
        publisher=publisher,
        policy=policy,
    )

    result = await dispatcher.dispatch_once()

    assert result.outcome == DispatchOutcome.RETRY_SCHEDULED
    assert result.event_id == event_id
    assert result.retry_delay == timedelta(seconds=3)
    assert "reschedule_failure" in timeline


@pytest.mark.asyncio
async def test_dispatch_once_mark_claim_lost() -> None:
    """Verify OutboxClaimLostError on mark_published returns CLAIM_LOST_AFTER_PUBLISH."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    settlement_uow = RecordingUnitOfWork(
        mark_error=OutboxClaimLostError("Lease expired and reclaimed"),
        timeline=timeline,
    )
    uow_instances = [claim_uow, settlement_uow]

    publisher = FakePublisher()
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: uow_instances.pop(0),
        publisher=publisher,
        policy=policy,
    )

    result = await dispatcher.dispatch_once()

    assert result.outcome == DispatchOutcome.CLAIM_LOST_AFTER_PUBLISH
    assert result.event_id == event_id
    assert result.dispatch_attempt_count == 1
    # Does NOT call reschedule_failure or attempt republishing
    assert "reschedule_failure" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_reschedule_claim_lost() -> None:
    """Verify OutboxClaimLostError on reschedule_failure returns CLAIM_LOST_AFTER_FAILURE."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    settlement_uow = RecordingUnitOfWork(
        reschedule_error=OutboxClaimLostError("Lease expired during failed publication"),
        timeline=timeline,
    )
    uow_instances = [claim_uow, settlement_uow]

    publisher = FakePublisher(fail_with=EventPublishError("Broker unavailable"))
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: uow_instances.pop(0),
        publisher=publisher,
        policy=policy,
    )

    result = await dispatcher.dispatch_once()

    assert result.outcome == DispatchOutcome.CLAIM_LOST_AFTER_FAILURE
    assert result.event_id == event_id
    assert result.dispatch_attempt_count == 1


@pytest.mark.asyncio
async def test_dispatch_once_mark_db_failure_propagates() -> None:
    """Verify unexpected database error during mark_published propagates without rescheduling."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    settlement_uow = RecordingUnitOfWork(
        mark_error=RuntimeError("Database connection lost"),
        timeline=timeline,
    )
    uow_instances = [claim_uow, settlement_uow]

    publisher = FakePublisher()
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: uow_instances.pop(0),
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(RuntimeError, match="Database connection lost"):
        await dispatcher.dispatch_once()

    assert "reschedule_failure" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_reschedule_db_failure_propagates() -> None:
    """Verify unexpected database error during reschedule_failure propagates."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    settlement_uow = RecordingUnitOfWork(
        reschedule_error=RuntimeError("Database lock timeout"),
        timeline=timeline,
    )
    uow_instances = [claim_uow, settlement_uow]

    publisher = FakePublisher(fail_with=EventPublishError("Broker error"))
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: uow_instances.pop(0),
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(RuntimeError, match="Database lock timeout"):
        await dispatcher.dispatch_once()


@pytest.mark.asyncio
async def test_dispatch_once_unexpected_publisher_exception_propagates() -> None:
    """Verify non-PublisherError exceptions propagate directly without rescheduling."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    publisher = FakePublisher(fail_with=TypeError("Unexpected code defect"))
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: claim_uow,
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(TypeError, match="Unexpected code defect"):
        await dispatcher.dispatch_once()

    assert "reschedule_failure" not in timeline
    assert "mark_published" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_unexpected_value_error_propagates() -> None:
    """Verify unexpected ValueError propagates directly without rescheduling."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    publisher = FakePublisher(fail_with=ValueError("Invalid parameter value"))
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: claim_uow,
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(ValueError, match="Invalid parameter value"):
        await dispatcher.dispatch_once()

    assert "reschedule_failure" not in timeline
    assert "mark_published" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_publisher_state_error_propagates() -> None:
    """Verify PublisherStateError (lifecycle misuse) propagates without rescheduling."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    publisher = FakePublisher(fail_with=PublisherStateError("Publisher not in STARTED state"))
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: claim_uow,
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(PublisherStateError, match="Publisher not in STARTED state"):
        await dispatcher.dispatch_once()

    assert "reschedule_failure" not in timeline
    assert "mark_published" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_publisher_not_started_error_propagates() -> None:
    """Verify PublisherNotStartedError propagates without rescheduling."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    publisher = FakePublisher(fail_with=PublisherNotStartedError("Call start() before publishing"))
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: claim_uow,
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(PublisherNotStartedError, match=r"Call start\(\) before publishing"):
        await dispatcher.dispatch_once()

    assert "reschedule_failure" not in timeline
    assert "mark_published" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_publisher_connection_error_propagates() -> None:
    """Verify PublisherConnectionError propagates without rescheduling."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    publisher = FakePublisher(fail_with=PublisherConnectionError("Transport connection refused"))
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: claim_uow,
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(PublisherConnectionError, match="Transport connection refused"):
        await dispatcher.dispatch_once()

    assert "reschedule_failure" not in timeline
    assert "mark_published" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_cancellation_propagates() -> None:
    """Verify asyncio.CancelledError is not swallowed or converted to retry."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    record = _build_test_record(event_id=event_id, envelope=envelope, dispatch_attempt_count=0)

    timeline: list[str] = []
    claim_uow = RecordingUnitOfWork(records_to_claim=(record,), timeline=timeline)
    publisher = FakePublisher(fail_with=asyncio.CancelledError())
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: claim_uow,
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.dispatch_once()

    assert "reschedule_failure" not in timeline
    assert "mark_published" not in timeline


@pytest.mark.asyncio
async def test_dispatch_once_invariant_violation_more_than_one_claimed() -> None:
    """Verify invariant violation raises RuntimeError if claim_ready returns >1 events."""
    record1 = _build_test_record(event_id=uuid4())
    record2 = _build_test_record(event_id=uuid4())

    claim_uow = RecordingUnitOfWork(records_to_claim=(record1, record2))
    publisher = FakePublisher()
    policy = _build_test_policy()

    dispatcher = OutboxDispatcher(
        uow_factory=lambda: claim_uow,
        publisher=publisher,
        policy=policy,
    )

    with pytest.raises(RuntimeError, match="Invariant violation: claim_ready returned 2 events"):
        await dispatcher.dispatch_once()

    assert len(publisher.published_envelopes) == 0


def test_claim_token_never_enters_event_envelope() -> None:
    """Verify that claim_token is never leaked into the canonical EventEnvelope."""
    event_id = uuid4()
    claim_token = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    claimed = ClaimedOutboxEvent(
        event_id=event_id,
        envelope=envelope,
        claim_token=claim_token,
        dispatch_attempt_count=1,
    )

    # Serialize envelope
    serialized = envelope.model_dump(mode="json")
    raw_str = str(serialized)

    assert str(claim_token) not in raw_str
    assert "claim_token" not in serialized
    assert claimed.claim_token == claim_token


def test_architectural_boundaries_and_zero_forbidden_imports() -> None:
    """Verify dispatcher module has zero RabbitMQ imports, while loops, or sleep calls."""
    import app.events.outbox.dispatcher as disp_mod

    assert not hasattr(disp_mod, "aio_pika")
    assert not hasattr(disp_mod, "RabbitMqEventPublisher")

    source_code = Path(disp_mod.__file__).read_text(encoding="utf-8")
    assert "aio_pika" not in source_code
    assert "rabbitmq" not in source_code.lower()
    assert "while true" not in source_code.lower()
    assert "asyncio.sleep" not in source_code
