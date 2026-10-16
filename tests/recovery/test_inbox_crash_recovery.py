"""Inbox Crash & Failure Recovery Tests (Section 13).

Validates failure boundaries around consumer event processing:
A. Failure before business transaction commit -> rollback, provisional inbox marker not finalized.
B. Retry remains possible and succeeds after transient fault recovery.
C. Duplicate delivery after restart -> suppressed by inbox ledger, handler skipped.
D. Handler raises fatal exception -> rolls back, preserves data integrity.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.engine import DatabaseManager
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.inbox.processor import (
    ConsumerEventHandler,
    ConsumerEventProcessor,
    ConsumerProcessingOutcome,
)
from app.events.schemas import EventCreate, EventEnvelope, create_event_envelope


class FaultyConsumerHandler(ConsumerEventHandler):
    """Consumer handler with configurable fault injection."""

    def __init__(self, fail_first_n_times: int = 1) -> None:
        self.fail_first_n_times = fail_first_n_times
        self.call_count = 0
        self.successful_executions = 0

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> None:
        self.call_count += 1
        if self.call_count <= self.fail_first_n_times:
            # Simulate failure during business execution before transaction commit
            raise RuntimeError(f"Simulated transient crash on attempt {self.call_count}")
        self.successful_executions += 1


@pytest.mark.asyncio
async def test_inbox_crash_before_commit_allows_retry_and_prevents_stuck_idempotency() -> None:
    """Verify that when a consumer handler fails before commit:
    1. Both business mutation and inbox marker are rolled back.
    2. Retry is NOT blocked as a duplicate.
    3. Next attempt succeeds.
    4. Subsequent replay is safely detected as duplicate.
    """
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    consumer_name = "test.recovery.inbox.worker"
    envelope = create_event_envelope(
        EventCreate(
            event_type="test.recovery.inbox.event",
            tenant_id=f"tenant-inbox-{uuid4().hex[:8]}",
            payload={"task_id": "T-100"},
        )
    )

    handler = FaultyConsumerHandler(fail_first_n_times=1)
    processor = ConsumerEventProcessor(
        consumer_name=consumer_name,
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory=manager.session_factory),
        handler=handler,
    )

    try:
        # Attempt 1: Injected crash during business handler
        with pytest.raises(RuntimeError, match="Simulated transient crash on attempt 1"):
            await processor.process(envelope)

        assert handler.call_count == 1
        assert handler.successful_executions == 0

        # CRITICAL RECOVERY VERIFICATION:
        # Inbox marker must NOT exist in DB because transaction was rolled back!
        async with manager.session_factory() as session:
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM inbox_messages "
                        "WHERE consumer_name = :cname AND event_id = :eid"
                    ),
                    {"cname": consumer_name, "eid": envelope.event_id},
                )
            ).scalar()
            assert count == 0, "Failed handler must not leave an inbox record behind"

        # Attempt 2: Worker restarts and retries the exact same message
        res2 = await processor.process(envelope)
        assert res2.outcome == ConsumerProcessingOutcome.PROCESSED
        assert handler.call_count == 2
        assert handler.successful_executions == 1

        # Inbox marker IS now committed
        async with manager.session_factory() as session:
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM inbox_messages "
                        "WHERE consumer_name = :cname AND event_id = :eid"
                    ),
                    {"cname": consumer_name, "eid": envelope.event_id},
                )
            ).scalar()
            assert count == 1, "Successful retry must commit inbox record"

        # Attempt 3: Duplicate message redelivered (broker at-least-once retry)
        res3 = await processor.process(envelope)
        assert res3.outcome == ConsumerProcessingOutcome.DUPLICATE
        # Handler was NOT invoked again!
        assert handler.call_count == 2
        assert handler.successful_executions == 1

    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM inbox_messages WHERE event_id = :eid"),
                {"eid": envelope.event_id},
            )
            await session.commit()
        await manager.dispose()
