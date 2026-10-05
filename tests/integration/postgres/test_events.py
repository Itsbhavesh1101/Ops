"""Live PostgreSQL event persistence, inbox deduplication, and outbox claim verification.

Part of Phase 1 Foundation Task 1J.2A.
Validates:
- Inbox transactional deduplication (try_record): first record True, duplicate False
- Outbox event persistence: canonical JSONB envelope, server timestamps, pending state
- Outbox DB-only dispatch claim (claim_ready) with PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED
- Concurrent outbox claim isolation (no double claims)
"""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.uow import SqlAlchemyUnitOfWork
from app.events.inbox.models import InboxMessageRecord
from app.events.inbox.repository import InboxRepository
from app.events.outbox.dispatch_repository import OutboxDispatchRepository
from app.events.outbox.models import OutboxEventRecord
from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, create_event_envelope


@pytest.mark.integration
@pytest.mark.asyncio
async def test_inbox_deduplication_live(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify live PostgreSQL inbox deduplication using INSERT ... ON CONFLICT DO NOTHING."""
    consumer_name = "workflow.projection"
    envelope = create_event_envelope(
        EventCreate(
            event_type="domain.user.registered",
            tenant_id="tenant-inbox-test",
            payload={"user_id": "u-123"},
        )
    )

    # 1. First record attempt inside UoW -> True
    async with SqlAlchemyUnitOfWork(session_factory) as uow1:
        repo1 = InboxRepository(uow1.session)
        is_first = await repo1.try_record(consumer_name=consumer_name, envelope=envelope)
        assert is_first is True, "First record attempt must return True"
        await uow1.commit()

    # Verify record in inbox_messages
    async with session_factory() as session:
        stmt = select(InboxMessageRecord).where(
            InboxMessageRecord.consumer_name == consumer_name,
            InboxMessageRecord.event_id == envelope.event_id,
        )
        record = await session.scalar(stmt)
        assert record is not None
        assert record.recorded_at is not None

    # 2. Second record attempt with identical (consumer_name, event_id) -> False
    async with SqlAlchemyUnitOfWork(session_factory) as uow2:
        repo2 = InboxRepository(uow2.session)
        is_second = await repo2.try_record(consumer_name=consumer_name, envelope=envelope)
        assert is_second is False, "Duplicate attempt must return False"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_outbox_persistence_and_claim_semantics(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify live outbox envelope staging and SELECT ... FOR UPDATE SKIP LOCKED claim semantics."""
    envelope = create_event_envelope(
        EventCreate(
            event_type="order.placed",
            tenant_id="tenant-outbox-test",
            payload={"order_id": "ord-999", "amount": 149.99},
        )
    )

    # 1. Enqueue event into outbox_events inside UoW
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = OutboxRepository(uow.session)
        event_id = await repo.enqueue(envelope)
        assert event_id == envelope.event_id
        await uow.commit()

    # 2. Verify row persisted with JSONB envelope and pending state
    async with session_factory() as session:
        stmt = select(OutboxEventRecord).where(OutboxEventRecord.event_id == envelope.event_id)
        record = await session.scalar(stmt)
        assert record is not None
        assert record.published_at is None
        assert record.claimed_at is None
        assert record.claim_token is None
        assert record.dispatch_attempt_count == 0
        assert record.envelope["event_type"] == "order.placed"
        assert record.envelope["payload"]["order_id"] == "ord-999"

    # 3. Verify outbox claim semantics with SKIP LOCKED
    async with SqlAlchemyUnitOfWork(session_factory) as uow_claim1:
        dispatch_repo1 = OutboxDispatchRepository(uow_claim1.session)
        claimed_events = await dispatch_repo1.claim_ready(
            limit=10,
            lease_duration=timedelta(minutes=5),
        )
        # We must claim our event
        claimed_ids = [e.event_id for e in claimed_events]
        assert envelope.event_id in claimed_ids

        # Locate our specific claimed event
        claimed_event = next(e for e in claimed_events if e.event_id == envelope.event_id)
        assert claimed_event.claim_token is not None
        assert claimed_event.dispatch_attempt_count >= 1

        # While uow_claim1 holds the lock (uncommitted), a second concurrent session
        # attempting claim_ready must SKIP the locked row due to SKIP LOCKED
        async with SqlAlchemyUnitOfWork(session_factory) as uow_claim2:
            dispatch_repo2 = OutboxDispatchRepository(uow_claim2.session)
            claimed_concurrent = await dispatch_repo2.claim_ready(
                limit=10,
                lease_duration=timedelta(minutes=5),
            )
            concurrent_ids = [e.event_id for e in claimed_concurrent]
            assert envelope.event_id not in concurrent_ids, (
                "Locked event must be skipped by concurrent claim_ready"
            )

        # Commit claim 1
        await uow_claim1.commit()

    # 4. Verify claimed state in database
    async with session_factory() as session:
        stmt = select(OutboxEventRecord).where(OutboxEventRecord.event_id == envelope.event_id)
        claimed_record = await session.scalar(stmt)
        assert claimed_record is not None
        assert claimed_record.claimed_at is not None
        assert claimed_record.claim_token == claimed_event.claim_token
        assert claimed_record.dispatch_attempt_count == 1
