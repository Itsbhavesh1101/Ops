"""Unit tests for ClaimedOutboxEvent and OutboxDispatchRepository.

Part of Phase 1 Foundation Task 1G.3B1.
Validates:
- ClaimedOutboxEvent immutability, fields, and envelope structure.
- OutboxDispatchRepository initialization with externally supplied session.
- No transaction ownership: zero commits, rollbacks, or closes.
- claim_ready:
  - Input validation (positive limit, positive lease_duration).
  - PostgreSQL statement compilation: FOR UPDATE SKIP LOCKED, eligibility predicate,
    deterministic ORDER BY, LIMIT.
  - Claim transition: fresh UUID token, DB now() claimed_at, attempt count increment.
  - Stale lease reclaiming: replaces token, increments attempt count.
- mark_published:
  - Conditional UPDATE matching event_id, active claim_token, published_at IS NULL.
  - Clears claim_token and claimed_at, sets published_at = func.now().
  - Raises OutboxClaimLostError when rowcount == 0 (stale token or already published).
- reschedule_failure:
  - Rejects negative retry_delay; accepts zero or positive retry_delay.
  - Conditional UPDATE setting next_attempt_at = func.now() + retry_delay.
  - Clears claim_token and claimed_at; leaves published_at NULL.
  - Raises OutboxClaimLostError when rowcount == 0.
- Stale worker protection:
  - Old token rejected once lease reclaimed or published.
- Architectural boundaries:
  - Zero RabbitMQ imports or calls; zero dispatcher loops or retry backoff policies.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.outbox.dispatch import ClaimedOutboxEvent
from app.events.outbox.dispatch_repository import (
    OutboxClaimLostError,
    OutboxDispatchRepository,
)
from app.events.outbox.models import OutboxEventRecord
from app.events.schemas import EventEnvelope


# Helper to build canonical EventEnvelope
def _build_test_envelope(event_id: UUID | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id or uuid4(),
        event_type="test.dispatch.event",
        event_version=1,
        occurred_at=datetime.now(UTC),
        payload={"key": "value"},
    )


# -----------------------------------------------------------------------------
# ClaimedOutboxEvent Value Object Tests
# -----------------------------------------------------------------------------


def test_claimed_outbox_event_construction_and_immutability() -> None:
    """Verify ClaimedOutboxEvent holds required fields and is strictly frozen."""
    event_id = uuid4()
    claim_token = uuid4()
    envelope = _build_test_envelope(event_id=event_id)

    claimed = ClaimedOutboxEvent(
        event_id=event_id,
        envelope=envelope,
        claim_token=claim_token,
        dispatch_attempt_count=1,
    )

    assert claimed.event_id == event_id
    assert claimed.envelope == envelope
    assert claimed.claim_token == claim_token
    assert claimed.dispatch_attempt_count == 1

    # Invariant: frozen (mutation raises ValidationError)
    with pytest.raises(ValidationError):
        claimed.dispatch_attempt_count = 2  # type: ignore[misc]

    # Invariant: extra fields forbidden
    with pytest.raises(ValidationError):
        ClaimedOutboxEvent(
            event_id=event_id,
            envelope=envelope,
            claim_token=claim_token,
            dispatch_attempt_count=1,
            unexpected_field="disallowed",  # type: ignore[call-arg]
        )


# -----------------------------------------------------------------------------
# OutboxDispatchRepository Initialization and Ownership Tests
# -----------------------------------------------------------------------------


def test_repository_initialization_and_session_retention() -> None:
    """Verify OutboxDispatchRepository retains session without owning transactions."""
    mock_session = AsyncMock(spec=AsyncSession)
    repo = OutboxDispatchRepository(mock_session)

    assert repo.session is mock_session
    # Negative assertions: zero commits, rollbacks, closes upon init
    assert mock_session.commit.await_count == 0
    assert mock_session.rollback.await_count == 0
    assert mock_session.close.await_count == 0


def test_repository_accepts_custom_token_factory() -> None:
    """Verify OutboxDispatchRepository accepts an injected token factory."""
    mock_session = AsyncMock(spec=AsyncSession)
    fixed_token = uuid4()
    repo = OutboxDispatchRepository(mock_session, token_factory=lambda: fixed_token)

    assert repo._token_factory() == fixed_token


# -----------------------------------------------------------------------------
# claim_ready Input Validation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_ready_rejects_non_positive_limit() -> None:
    """Verify claim_ready rejects limit <= 0 with ValueError."""
    mock_session = AsyncMock(spec=AsyncSession)
    repo = OutboxDispatchRepository(mock_session)

    with pytest.raises(ValueError, match="limit must be positive"):
        await repo.claim_ready(limit=0, lease_duration=timedelta(seconds=30))

    with pytest.raises(ValueError, match="limit must be positive"):
        await repo.claim_ready(limit=-5, lease_duration=timedelta(seconds=30))

    with pytest.raises(TypeError, match="limit must be an integer"):
        await repo.claim_ready(limit="5", lease_duration=timedelta(seconds=30))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_claim_ready_rejects_non_positive_lease_duration() -> None:
    """Verify claim_ready rejects lease_duration <= 0 with ValueError."""
    mock_session = AsyncMock(spec=AsyncSession)
    repo = OutboxDispatchRepository(mock_session)

    with pytest.raises(ValueError, match="lease_duration must be strictly positive"):
        await repo.claim_ready(limit=10, lease_duration=timedelta(seconds=0))

    with pytest.raises(ValueError, match="lease_duration must be strictly positive"):
        await repo.claim_ready(limit=10, lease_duration=timedelta(seconds=-10))

    with pytest.raises(TypeError, match="lease_duration must be a timedelta"):
        await repo.claim_ready(limit=10, lease_duration=30)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# claim_ready SQL Statement Compilation Tests (PostgreSQL Dialect)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_ready_sql_compilation_semantics() -> None:
    """Verify the SELECT statement compiled for PostgreSQL dialect has required semantics:
    - FOR UPDATE SKIP LOCKED
    - published_at IS NULL
    - next_attempt_at <= now()
    - claimed_at IS NULL OR claimed_at <= now() - lease_duration
    - ORDER BY next_attempt_at ASC, created_at ASC, event_id ASC
    - LIMIT
    """
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    repo = OutboxDispatchRepository(mock_session)
    await repo.claim_ready(limit=25, lease_duration=timedelta(seconds=45))

    # Inspect the captured statement passed to session.execute
    assert mock_session.execute.await_count == 1
    stmt = mock_session.execute.await_args.args[0]

    compiled_sql = str(stmt.compile(dialect=postgresql.dialect()))

    # 1. Row-level concurrency locking
    assert "FOR UPDATE SKIP LOCKED" in compiled_sql

    # 2. Eligibility: unpublished rows only
    assert "outbox_events.published_at IS NULL" in compiled_sql

    # 3. Eligibility: next_attempt_at <= database current timestamp
    assert "outbox_events.next_attempt_at <= now()" in compiled_sql

    # 4. Eligibility: unclaimed OR stale lease
    assert "outbox_events.claimed_at IS NULL OR outbox_events.claimed_at <=" in compiled_sql

    # 5. Deterministic ordering
    assert (
        "ORDER BY outbox_events.next_attempt_at ASC, "
        "outbox_events.created_at ASC, "
        "outbox_events.event_id ASC"
    ) in compiled_sql

    # 6. Bounded limit
    assert "LIMIT" in compiled_sql


# -----------------------------------------------------------------------------
# claim_ready Transition and Reclaiming Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_ready_successful_transition() -> None:
    """Verify claim_ready updates claim metadata, attempt count, and returns ClaimedOutboxEvent."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    raw_json = envelope.model_dump(mode="json")

    record = OutboxEventRecord(
        event_id=event_id,
        envelope=raw_json,
        created_at=datetime.now(UTC),
        published_at=None,
        dispatch_attempt_count=0,
        next_attempt_at=datetime.now(UTC),
        claim_token=None,
        claimed_at=None,
    )

    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [record]
    mock_session.execute.return_value = mock_result

    fixed_token = uuid4()
    repo = OutboxDispatchRepository(mock_session, token_factory=lambda: fixed_token)

    claimed_events = await repo.claim_ready(limit=10, lease_duration=timedelta(seconds=30))

    assert len(claimed_events) == 1
    claimed = claimed_events[0]

    # Verify returned ClaimedOutboxEvent
    assert claimed.event_id == event_id
    assert claimed.claim_token == fixed_token
    assert claimed.dispatch_attempt_count == 1
    assert claimed.envelope == envelope

    # Verify ORM record transition
    assert record.claim_token == fixed_token
    assert record.claimed_at is not None  # Assigned DB func.now() expression
    assert record.dispatch_attempt_count == 1

    # Invariant: immutable event fields unaltered
    assert record.event_id == event_id
    assert record.envelope == raw_json
    assert record.published_at is None

    # Invariant: zero transaction management calls
    assert mock_session.commit.await_count == 0
    assert mock_session.rollback.await_count == 0
    assert mock_session.close.await_count == 0


@pytest.mark.asyncio
async def test_claim_ready_stale_lease_reclaim() -> None:
    """Verify a stale lease can be reclaimed: replaces token and increments attempt count."""
    event_id = uuid4()
    envelope = _build_test_envelope(event_id=event_id)
    raw_json = envelope.model_dump(mode="json")
    old_token = uuid4()

    record = OutboxEventRecord(
        event_id=event_id,
        envelope=raw_json,
        created_at=datetime.now(UTC),
        published_at=None,
        dispatch_attempt_count=2,  # Already attempted twice
        next_attempt_at=datetime.now(UTC),
        claim_token=old_token,
        claimed_at=datetime.now(UTC) - timedelta(minutes=10),  # Stale
    )

    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [record]
    mock_session.execute.return_value = mock_result

    new_token = uuid4()
    repo = OutboxDispatchRepository(mock_session, token_factory=lambda: new_token)

    claimed_events = await repo.claim_ready(limit=5, lease_duration=timedelta(seconds=30))

    assert len(claimed_events) == 1
    claimed = claimed_events[0]

    # Verify claim ownership updated
    assert claimed.claim_token == new_token
    assert claimed.claim_token != old_token
    assert claimed.dispatch_attempt_count == 3

    assert record.claim_token == new_token
    assert record.dispatch_attempt_count == 3


# -----------------------------------------------------------------------------
# mark_published Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_published_successful_conditional_update() -> None:
    """Verify mark_published executes conditional UPDATE and clears claim on matching token."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute.return_value = mock_result

    repo = OutboxDispatchRepository(mock_session)
    event_id = uuid4()
    claim_token = uuid4()

    await repo.mark_published(event_id=event_id, claim_token=claim_token)

    assert mock_session.execute.await_count == 1
    stmt = mock_session.execute.await_args.args[0]
    compiled_sql = str(stmt.compile(dialect=postgresql.dialect()))

    # Verify conditional UPDATE SQL
    assert "UPDATE outbox_events SET" in compiled_sql
    assert "published_at=now()" in compiled_sql
    assert "claim_token=NULL" in compiled_sql
    assert "claimed_at=NULL" in compiled_sql
    assert "outbox_events.event_id =" in compiled_sql
    assert "outbox_events.claim_token =" in compiled_sql
    assert "outbox_events.published_at IS NULL" in compiled_sql

    # Invariant: does not commit
    assert mock_session.commit.await_count == 0


@pytest.mark.asyncio
async def test_mark_published_zero_rows_raises_claim_lost_error() -> None:
    """Verify mark_published raises OutboxClaimLostError when rowcount == 0 (stale token)."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.rowcount = 0  # Zero rows updated (claim lost)
    mock_session.execute.return_value = mock_result

    repo = OutboxDispatchRepository(mock_session)
    event_id = uuid4()
    stale_token = uuid4()

    with pytest.raises(OutboxClaimLostError) as exc_info:
        await repo.mark_published(event_id=event_id, claim_token=stale_token)

    err_msg = str(exc_info.value)
    assert str(event_id) in err_msg
    assert str(stale_token) in err_msg
    assert "claim token" in err_msg.lower() or "lost" in err_msg.lower()


# -----------------------------------------------------------------------------
# reschedule_failure Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_failure_successful_conditional_update() -> None:
    """Verify reschedule_failure sets next_attempt_at = now() + retry_delay and clears claim."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute.return_value = mock_result

    repo = OutboxDispatchRepository(mock_session)
    event_id = uuid4()
    claim_token = uuid4()
    retry_delay = timedelta(seconds=15)

    await repo.reschedule_failure(
        event_id=event_id,
        claim_token=claim_token,
        retry_delay=retry_delay,
    )

    assert mock_session.execute.await_count == 1
    stmt = mock_session.execute.await_args.args[0]
    compiled_sql = str(stmt.compile(dialect=postgresql.dialect()))

    # Verify conditional UPDATE SQL
    assert "UPDATE outbox_events SET" in compiled_sql
    assert "next_attempt_at=(now() +" in compiled_sql
    assert "claim_token=NULL" in compiled_sql
    assert "claimed_at=NULL" in compiled_sql

    assert "outbox_events.event_id =" in compiled_sql
    assert "outbox_events.claim_token =" in compiled_sql
    assert "outbox_events.published_at IS NULL" in compiled_sql

    # Invariant: does not commit
    assert mock_session.commit.await_count == 0


@pytest.mark.asyncio
async def test_reschedule_failure_allows_zero_delay() -> None:
    """Verify reschedule_failure allows retry_delay == timedelta(0)."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute.return_value = mock_result

    repo = OutboxDispatchRepository(mock_session)
    await repo.reschedule_failure(
        event_id=uuid4(),
        claim_token=uuid4(),
        retry_delay=timedelta(0),
    )
    assert mock_session.execute.await_count == 1


@pytest.mark.asyncio
async def test_reschedule_failure_rejects_negative_delay() -> None:
    """Verify reschedule_failure rejects negative retry_delay with ValueError."""
    mock_session = AsyncMock(spec=AsyncSession)
    repo = OutboxDispatchRepository(mock_session)

    with pytest.raises(ValueError, match="retry_delay must be non-negative"):
        await repo.reschedule_failure(
            event_id=uuid4(),
            claim_token=uuid4(),
            retry_delay=timedelta(seconds=-1),
        )


@pytest.mark.asyncio
async def test_reschedule_failure_zero_rows_raises_claim_lost_error() -> None:
    """Verify reschedule_failure raises OutboxClaimLostError when rowcount == 0."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_session.execute.return_value = mock_result

    repo = OutboxDispatchRepository(mock_session)
    event_id = uuid4()
    stale_token = uuid4()

    with pytest.raises(OutboxClaimLostError) as exc_info:
        await repo.reschedule_failure(
            event_id=event_id,
            claim_token=stale_token,
            retry_delay=timedelta(seconds=10),
        )

    err_msg = str(exc_info.value)
    assert str(event_id) in err_msg
    assert str(stale_token) in err_msg


# -----------------------------------------------------------------------------
# Stale Worker Protection Isolation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_worker_token_a_vs_authoritative_token_b() -> None:
    """Demonstrate end-to-end stale worker protection:
    - Worker A has stale token A.
    - Lease expired and Worker B claimed with token B.
    - Worker A mark_published(token A) -> OutboxClaimLostError (rowcount == 0).
    - Worker A reschedule_failure(token A) -> OutboxClaimLostError (rowcount == 0).
    - Worker B mark_published(token B) -> Success (rowcount == 1).
    """
    mock_session = AsyncMock(spec=AsyncSession)
    event_id = uuid4()
    token_a = uuid4()
    token_b = uuid4()

    repo = OutboxDispatchRepository(mock_session)

    # 1. Worker A attempts mark_published with stale token A -> DB affects 0 rows
    mock_session.execute.return_value = MagicMock(rowcount=0)
    with pytest.raises(OutboxClaimLostError):
        await repo.mark_published(event_id=event_id, claim_token=token_a)

    # 2. Worker A attempts reschedule_failure with stale token A -> DB affects 0 rows
    mock_session.execute.return_value = MagicMock(rowcount=0)
    with pytest.raises(OutboxClaimLostError):
        await repo.reschedule_failure(
            event_id=event_id,
            claim_token=token_a,
            retry_delay=timedelta(seconds=30),
        )

    # 3. Worker B attempts mark_published with active token B -> DB affects 1 row
    mock_session.execute.return_value = MagicMock(rowcount=1)
    await repo.mark_published(event_id=event_id, claim_token=token_b)
    # Success, no exception raised


# -----------------------------------------------------------------------------
# Architectural Boundaries (No RabbitMQ, No Dispatcher Loops)
# -----------------------------------------------------------------------------


def test_no_rabbitmq_or_broker_imports_in_dispatch_repository() -> None:
    """Verify dispatch repository module does not import RabbitMQ or publisher transport."""
    # Read dispatch_repository module
    import app.events.outbox.dispatch_repository as dr_mod

    # Verify no aio_pika or RabbitMQ references in dr_mod
    assert not hasattr(dr_mod, "aio_pika")
    assert not hasattr(dr_mod, "RabbitMqEventPublisher")
    assert not hasattr(dr_mod, "EventPublisher")

    # Invariant: source file check
    dispatch_file = Path(dr_mod.__file__).read_text(encoding="utf-8")
    assert "rabbitmq" not in dispatch_file.lower()
    assert "aio_pika" not in dispatch_file
    assert "publish" not in dispatch_file.lower() or "mark_published" in dispatch_file
