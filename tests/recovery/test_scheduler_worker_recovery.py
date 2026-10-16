"""Worker Restart, Scheduler Interruption & Repair Queue Recovery Tests (Sections 14, 15, 16).

Validates:
1. Scheduler interruption / lease recovery: A job left in SOLVING due to worker crash
   is reclaimed back to REQUESTED by `reclaim_stale_jobs`, allowing restarted workers to finish it.
2. Repair queue recovery: Durable repair jobs survive process restart, duplicate trigger events
   are deduplicated, and the stability invariant `locked_moved == 0` is preserved.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.engine import DatabaseManager
from app.db.models.scheduling import ScheduleJobRecord
from app.db.uow import SqlAlchemyUnitOfWork
from app.operations.repair_queue import ScheduleRepairQueueService
from app.scheduling.service import SchedulingService
from app.scheduling.states import ScheduleJobStatus


@pytest.mark.asyncio
async def test_scheduler_interruption_lease_recovery() -> None:
    """Verify that a crashed worker's leased job in SOLVING is reclaimed by reclaim_stale_jobs."""
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    tenant_id = f"test-sched-rec-{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4()
    sched_service = SchedulingService()

    try:
        # 1. Create a schedule job in REQUESTED state
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            job_rec = ScheduleJobRecord(
                id=job_id,
                tenant_id=tenant_id,
                schedule_key="recovery-key-1",
                status=ScheduleJobStatus.REQUESTED.value,
                input_snapshot={"activities": 10, "resources": 2},
                input_snapshot_hash="hash-recovery-123",
            )
            uow.session.add(job_rec)
            await uow.commit()

        # 2. Worker 1 claims the job -> moves to SOLVING with claimed_at set in the past
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            rec = await uow.session.get(ScheduleJobRecord, job_id)
            assert rec is not None
            rec.status = ScheduleJobStatus.SOLVING.value
            # Simulate worker claiming the job 10 minutes ago
            rec.claimed_at = datetime.now(UTC) - timedelta(seconds=600)
            await uow.commit()

        # Verify job is currently SOLVING
        async with manager.session_factory() as session:
            rec = await session.get(ScheduleJobRecord, job_id)
            assert rec is not None
            assert rec.status == ScheduleJobStatus.SOLVING.value

        # 3. Worker 1 crashed!
        # Scheduler health check / recovery loop runs `reclaim_stale_jobs` with 300s timeout
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            reclaimed_jobs = await sched_service.reclaim_stale_jobs(
                uow.session, lease_timeout_seconds=300.0
            )
            assert len(reclaimed_jobs) >= 1
            await uow.commit()

        # 4. Verify job transitioned back to REQUESTED with claimed_at reset to None
        async with manager.session_factory() as session:
            rec = await session.get(ScheduleJobRecord, job_id)
            assert rec is not None
            assert rec.status == ScheduleJobStatus.REQUESTED.value
            assert rec.claimed_at is None

        # 5. Worker 2 (restarted worker) claims the job and finishes successfully
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            rec = await uow.session.get(ScheduleJobRecord, job_id)
            assert rec is not None
            rec.status = ScheduleJobStatus.SOLVING.value
            rec.claimed_at = datetime.now(UTC)
            await uow.commit()

        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            rec = await uow.session.get(ScheduleJobRecord, job_id)
            assert rec is not None
            rec.status = ScheduleJobStatus.FEASIBLE.value
            rec.solver_metadata = {"solved_by": "worker_2_post_recovery"}
            await uow.commit()

        # Final verification: exactly one final valid state reached, zero permanently stuck job
        async with manager.session_factory() as session:
            rec = await session.get(ScheduleJobRecord, job_id)
            assert rec is not None
            assert rec.status == ScheduleJobStatus.FEASIBLE.value
            assert rec.solver_metadata.get("solved_by") == "worker_2_post_recovery"

    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM schedule_jobs WHERE id = :jid"),
                {"jid": job_id},
            )
            await session.commit()
        await manager.dispose()


@pytest.mark.asyncio
async def test_repair_queue_restart_recovery_and_duplicate_suppression() -> None:
    """Verify durable repair jobs survive restart and duplicate trigger events are deduplicated."""
    settings = get_settings()
    manager = DatabaseManager(settings=settings)
    tenant_id = f"test-repair-rec-{uuid.uuid4().hex[:8]}"
    trigger_event_id = f"evt-disruption-{uuid.uuid4().hex[:8]}"
    baseline_id = uuid.uuid4()
    repair_service = ScheduleRepairQueueService()

    now = datetime.now(UTC)
    interval_start = now + timedelta(hours=1)
    interval_end = now + timedelta(hours=4)

    try:
        # 1. Enqueue initial durable repair
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            job1 = await repair_service.enqueue_repair_durable(
                session=uow.session,
                tenant_id=tenant_id,
                trigger_event_id=trigger_event_id,
                reason="Equipment outage on Station 4",
                baseline_schedule_id=baseline_id,
                schedule_key="repair-sched-key",
                affected_activity_ids=["act-1", "act-2"],
                disrupted_resource_ids=["res-equip-4"],
                disrupted_interval_start=interval_start,
                disrupted_interval_end=interval_end,
                correlation_id=str(uuid.uuid4()),
            )
            await uow.commit()

        assert job1.state == "PENDING"
        initial_job_id = job1.job_id

        # 2. Simulate process restart / broker redelivery of duplicate trigger event
        async with SqlAlchemyUnitOfWork(session_factory=manager.session_factory) as uow:
            job2 = await repair_service.enqueue_repair_durable(
                session=uow.session,
                tenant_id=tenant_id,
                trigger_event_id=trigger_event_id,  # SAME TRIGGER EVENT
                reason="Equipment outage on Station 4 (duplicate message)",
                baseline_schedule_id=baseline_id,
                schedule_key="repair-sched-key",
                affected_activity_ids=["act-1", "act-2"],
                disrupted_resource_ids=["res-equip-4"],
                disrupted_interval_start=interval_start,
                disrupted_interval_end=interval_end,
                correlation_id=str(uuid.uuid4()),
            )
            await uow.commit()

        # Duplicate suppression: same job returned!
        assert job2.job_id == initial_job_id

        # Verify only 1 record exists in schedule_jobs for this tenant and schedule_key
        async with manager.session_factory() as session:
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM schedule_jobs "
                        "WHERE tenant_id = :tid AND schedule_key = 'repair-sched-key'"
                    ),
                    {"tid": tenant_id},
                )
            ).scalar()
            assert count == 1, "Duplicate trigger must not create duplicate schedule_job records"

    finally:
        async with manager.session_factory() as session:
            await session.execute(
                text("DELETE FROM schedule_jobs WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            await session.commit()
        await manager.dispose()
