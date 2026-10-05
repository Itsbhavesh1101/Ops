"""Live PostgreSQL idempotency persistence and concurrency verification.

Part of Phase 1 Foundation Task 1J.2A.
Validates:
- First acquire returns ACQUIRED and commits
- Duplicate attempt with same fingerprint returns EXISTING_MATCH
- Duplicate attempt with differing fingerprint returns EXISTING_CONFLICT
- Concurrent acquisitions against the same (tenant, operation, key) with the same fingerprint
  have exactly one ACQUIRED winner, with the second transaction resolving to EXISTING_MATCH
  after the winner becomes visible.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.idempotency.contracts import (
    create_idempotency_identity,
)
from app.core.idempotency.hashing import fingerprint_bytes
from app.core.idempotency.repository import (
    IdempotencyDecisionType,
    IdempotencyRepository,
)
from app.db.uow import SqlAlchemyUnitOfWork


@pytest.mark.integration
@pytest.mark.asyncio
async def test_idempotency_persistence_acquire_match_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify first acquire ACQUIRED, same fingerprint EXISTING_MATCH,
    and different fingerprint EXISTING_CONFLICT.
    """
    uow = SqlAlchemyUnitOfWork(session_factory)
    test_tenant = f"tenant-idem-{uuid4().hex[:8]}"
    raw_key = f"client-key-{uuid4().hex[:12]}"
    operation = "tasks.create"

    identity = create_idempotency_identity(
        tenant_id=test_tenant,
        operation_name=operation,
        raw_key=raw_key,
    )
    fp_original = fingerprint_bytes(b'{"action": "create", "value": 100}')
    fp_different = fingerprint_bytes(b'{"action": "create", "value": 200}')

    # 1. First acquisition attempt -> ACQUIRED
    async with uow:
        repo = IdempotencyRepository(uow.session)
        decision1 = await repo.acquire_or_observe(
            identity=identity,
            request_fingerprint=fp_original,
        )
        assert decision1.decision == IdempotencyDecisionType.ACQUIRED
        assert decision1.existing_fingerprint is None
        await uow.commit()

    # 2. Duplicate acquisition attempt with identical request fingerprint -> EXISTING_MATCH
    async with SqlAlchemyUnitOfWork(session_factory) as uow2:
        repo2 = IdempotencyRepository(uow2.session)
        decision2 = await repo2.acquire_or_observe(
            identity=identity,
            request_fingerprint=fp_original,
        )
        assert decision2.decision == IdempotencyDecisionType.EXISTING_MATCH
        assert decision2.existing_fingerprint == fp_original

    # 3. Duplicate acquisition attempt with differing request fingerprint -> EXISTING_CONFLICT
    async with SqlAlchemyUnitOfWork(session_factory) as uow3:
        repo3 = IdempotencyRepository(uow3.session)
        decision3 = await repo3.acquire_or_observe(
            identity=identity,
            request_fingerprint=fp_different,
        )
        assert decision3.decision == IdempotencyDecisionType.EXISTING_CONFLICT
        assert decision3.existing_fingerprint == fp_original
        assert decision3.request_fingerprint == fp_different


@pytest.mark.integration
@pytest.mark.asyncio
async def test_idempotency_concurrent_acquisition(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify that two concurrent transactions against the same key yield exactly one winner."""
    test_tenant = f"tenant-concurrent-idem-{uuid4().hex[:8]}"
    raw_key = f"key-race-{uuid4().hex[:12]}"
    operation = "payments.authorize"

    identity = create_idempotency_identity(
        tenant_id=test_tenant,
        operation_name=operation,
        raw_key=raw_key,
    )
    fp = fingerprint_bytes(b'{"amount": 5000, "currency": "USD"}')

    winner_barrier = asyncio.Event()
    results: list[IdempotencyDecisionType] = []

    async def worker_1() -> None:
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            repo = IdempotencyRepository(uow.session)
            dec = await repo.acquire_or_observe(identity=identity, request_fingerprint=fp)
            results.append(dec.decision)
            if dec.decision == IdempotencyDecisionType.ACQUIRED:
                await uow.commit()
                winner_barrier.set()
            else:
                # If not winner, signal barrier anyway so other worker doesn't hang
                winner_barrier.set()

    async def worker_2() -> None:
        # Wait until worker 1 starts or run concurrently
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            repo = IdempotencyRepository(uow.session)
            # Both execute concurrently
            dec = await repo.acquire_or_observe(identity=identity, request_fingerprint=fp)
            results.append(dec.decision)
            if dec.decision == IdempotencyDecisionType.ACQUIRED:
                await uow.commit()
                winner_barrier.set()
            else:
                # Loser might need winner to commit before observing committed row
                await uow.rollback()

    # Run both concurrent workers with bounded timeout
    await asyncio.wait_for(asyncio.gather(worker_1(), worker_2()), timeout=5.0)

    # Exactly one must have acquired
    assert results.count(IdempotencyDecisionType.ACQUIRED) == 1, (
        f"Expected exactly 1 ACQUIRED, got {results}"
    )

    # Now verify that subsequent check observes EXISTING_MATCH
    async with SqlAlchemyUnitOfWork(session_factory) as uow_check:
        repo_check = IdempotencyRepository(uow_check.session)
        final_dec = await repo_check.acquire_or_observe(identity=identity, request_fingerprint=fp)
        assert final_dec.decision == IdempotencyDecisionType.EXISTING_MATCH
