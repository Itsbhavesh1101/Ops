"""Live PostgreSQL and asynchronous scheduling worker integration tests (Stage P).

Validates against isolated PostgreSQL:
1. End-to-end solve job execution:
   - Insert REQUESTED job
   - Real SchedulerWorker claims job (SOLVING)
   - Executes OR-Tools CP-SAT solve
   - Persists FEASIBLE schedule candidate and assignments with zero hard violations
   - Completed job settled to FEASIBLE
2. Infeasible solve job execution:
   - Impossible scheduling job settled to INFEASIBLE (not FAILED, zero empty schedule)
   - Detailed diagnostics persisted in PostgreSQL
3. Multi-worker concurrent claim safety:
   - Multiple REQUESTED jobs claimed by 2 concurrent workers using FOR UPDATE SKIP LOCKED
   - Zero double-claims; exactly one worker claims each job
4. FEASIBLE -> REVIEW transition:
   - Authorized transition moves candidate schedule to REVIEW
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.engine import DatabaseManager
from app.db.models.scheduling import (
    ScheduleJobRecord,
    ScheduleRecord,
)
from app.resources.schemas import ResourceType
from app.scheduling.config import SchedulerSettings
from app.scheduling.constraints.models import (
    NoOverlapConstraint,
    TimeWindowConstraint,
)
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import (
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
    TimeWindow,
)
from app.scheduling.service import SchedulingService
from app.scheduling.worker import SchedulerWorker

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(
    os.environ.get("E7_POSTGRES_INTEGRATION_PORT") or os.environ.get("POSTGRES_PORT") or "55432"
)
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
DATABASE_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/postgres"


@pytest.fixture
def db_manager() -> DatabaseManager:
    settings = Settings(database_url=DATABASE_URL)
    return DatabaseManager(settings)


@pytest.fixture
def service() -> SchedulingService:
    return SchedulingService()


def make_worker_settings(
    worker_id: str | None = None,
    claim_batch_size: int = 5,
    solve_timeout_seconds: float = 10.0,
    poll_interval_seconds: float = 0.1,
    lease_duration_seconds: float = 60.0,
    shutdown_grace_seconds: float = 1.0,
) -> SchedulerSettings:
    """Helper creating explicit SchedulerSettings for tests with zero production defaults."""
    return SchedulerSettings(
        worker_id=worker_id or f"worker-{uuid.uuid4().hex[:6]}",
        poll_interval_seconds=poll_interval_seconds,
        claim_batch_size=claim_batch_size,
        solve_timeout_seconds=solve_timeout_seconds,
        lease_duration_seconds=lease_duration_seconds,
        shutdown_grace_seconds=shutdown_grace_seconds,
    )


@pytest.mark.asyncio
async def test_live_worker_feasible_job_execution(
    db_manager: DatabaseManager, service: SchedulingService
) -> None:
    """Verify live worker claims, solves, and persists a feasible job."""
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)

    profile = SolverProfile(
        profile_key="std",
        version=1,
        name="Standard",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )
    res = SchedulingResourceSnapshot(
        id="tech-1",
        type=ResourceType.PERSON,
        display_label="Tech 1",
        capacity=1,
    )
    act = SchedulingActivity(
        id="act-1",
        title="Repair pump",
        duration_minutes=60,
        required_resource_ids=["tech-1"],
    )
    request = SchedulingRequest(
        tenant_id=tenant_id,
        schedule_key="maint",
        horizon_start=t0,
        horizon_end=t1,
        activities=[act],
        resources=[res],
        constraints=[NoOverlapConstraint(id="c1", name="No overlap", resource_ids=["tech-1"])],
        profile=profile,
    )

    # 1. Submit job to PostgreSQL
    async with db_manager.session_factory() as session:
        async with session.begin():
            job = await service.submit_job(
                session=session,
                tenant_id=tenant_id,
                request=request,
                schedule_key="maint",
            )
            job_id = job.id

    # 2. Run worker run_once
    worker_settings = make_worker_settings(
        worker_id=f"worker-{uuid.uuid4().hex[:6]}",
        solve_timeout_seconds=10.0,
    )
    worker = SchedulerWorker(
        db_manager=db_manager,
        settings=worker_settings,
        service=service,
    )
    processed = await worker.run_once()
    assert processed >= 1

    # 3. Verify PostgreSQL job state
    async with db_manager.session_factory() as session:
        job_rec = await service.get_job(session, tenant_id, job_id)
        assert job_rec is not None
        assert job_rec.status == "FEASIBLE"
        assert job_rec.completed_at is not None
        assert job_rec.claimed_by == worker_settings.worker_id

        # Verify candidate schedule was created
        sched_stmt = select(ScheduleRecord).where(
            ScheduleRecord.source_job_id == job_id,
            ScheduleRecord.tenant_id == tenant_id,
        )
        sched = (await session.execute(sched_stmt)).scalar_one_or_none()
        assert sched is not None
        assert sched.status == "FEASIBLE"
        assert sched.version == 1

        # Verify assignments persisted
        assignments = await service.get_schedule_assignments(session, tenant_id, sched.id)
        assert len(assignments) == 1
        assert assignments[0].activity_id == "act-1"
        assert assignments[0].resource_id == "tech-1"
        assert assignments[0].start_at >= t0
        assert assignments[0].end_at <= t1

        # 4. Transition FEASIBLE -> REVIEW
        reviewed = await service.transition_to_review(session, tenant_id, sched.id)
        assert reviewed.status == "REVIEW"


@pytest.mark.asyncio
async def test_live_worker_infeasible_job_execution(
    db_manager: DatabaseManager, service: SchedulingService
) -> None:
    """Verify live worker claims, solves, and settles an infeasible job with diagnostics."""
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=2)

    profile = SolverProfile(
        profile_key="std",
        version=1,
        name="Standard",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )
    res = SchedulingResourceSnapshot(
        id="tech-1",
        type=ResourceType.PERSON,
        display_label="Tech 1",
        capacity=1,
    )
    # Impossible: 180 min duration within 120 min window
    act = SchedulingActivity(
        id="act-long",
        title="Impossible Duration",
        duration_minutes=180,
        required_resource_ids=["tech-1"],
    )
    request = SchedulingRequest(
        tenant_id=tenant_id,
        schedule_key="maint",
        horizon_start=t0,
        horizon_end=t1,
        activities=[act],
        resources=[res],
        constraints=[
            TimeWindowConstraint(
                id="c-win",
                name="Horizon Window",
                activity_id="act-long",
                allowed_windows=[TimeWindow(start_at=t0, end_at=t1)],
            )
        ],
        profile=profile,
    )

    # 1. Submit job
    async with db_manager.session_factory() as session:
        async with session.begin():
            job = await service.submit_job(
                session=session,
                tenant_id=tenant_id,
                request=request,
                schedule_key="maint",
            )
            job_id = job.id

    # 2. Worker run_once
    worker_settings = make_worker_settings(
        worker_id=f"worker-{uuid.uuid4().hex[:6]}",
        solve_timeout_seconds=10.0,
    )
    worker = SchedulerWorker(
        db_manager=db_manager,
        settings=worker_settings,
        service=service,
    )
    processed = await worker.run_once()
    assert processed >= 1

    # 3. Verify PostgreSQL job state
    async with db_manager.session_factory() as session:
        job_rec = await service.get_job(session, tenant_id, job_id)
        assert job_rec is not None
        assert job_rec.status == "INFEASIBLE"
        assert job_rec.status != "FAILED"
        assert len(job_rec.diagnostics) >= 1
        conflicting_ids = [
            cid for d in job_rec.diagnostics for cid in d.get("conflicting_constraint_ids", [])
        ]
        assert "horizon_act-long" in conflicting_ids or "c-win" in conflicting_ids
        affected_activities = [
            aid for d in job_rec.diagnostics for aid in d.get("affected_activity_ids", [])
        ]
        assert "act-long" in affected_activities

        # Verify NO schedule record created (zero fake empty success)
        sched_stmt = select(ScheduleRecord).where(
            ScheduleRecord.source_job_id == job_id,
        )
        sched = (await session.execute(sched_stmt)).scalar_one_or_none()
        assert sched is None


@pytest.mark.asyncio
async def test_live_multi_worker_concurrent_claim_safety(
    db_manager: DatabaseManager, service: SchedulingService
) -> None:
    """Verify two concurrent workers never claim or solve the same job."""
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)

    profile = SolverProfile(
        profile_key="std",
        version=1,
        name="Standard",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )
    res = SchedulingResourceSnapshot(
        id="tech-1",
        type=ResourceType.PERSON,
        display_label="Tech 1",
        capacity=1,
    )

    # Insert 4 jobs
    job_ids: list[uuid.UUID] = []
    async with db_manager.session_factory() as session:
        async with session.begin():
            for i in range(4):
                act = SchedulingActivity(
                    id=f"act-{i}",
                    title=f"Task {i}",
                    duration_minutes=30,
                    required_resource_ids=["tech-1"],
                )
                req = SchedulingRequest(
                    tenant_id=tenant_id,
                    schedule_key=f"batch-{i}",
                    horizon_start=t0,
                    horizon_end=t1,
                    activities=[act],
                    resources=[res],
                    constraints=[],
                    profile=profile,
                )
                j = await service.submit_job(session, tenant_id, req, schedule_key=f"batch-{i}")
                job_ids.append(j.id)

    worker1 = SchedulerWorker(
        db_manager=db_manager,
        settings=make_worker_settings(worker_id="worker-alpha", claim_batch_size=2),
        service=service,
    )
    worker2 = SchedulerWorker(
        db_manager=db_manager,
        settings=make_worker_settings(worker_id="worker-beta", claim_batch_size=2),
        service=service,
    )

    # Run both workers concurrently
    results = await asyncio.gather(worker1.run_once(), worker2.run_once())
    total_processed = sum(results)
    assert total_processed == 4

    # Verify each job was claimed and solved by exactly one worker
    async with db_manager.session_factory() as session:
        stmt = select(ScheduleJobRecord).where(ScheduleJobRecord.id.in_(job_ids))
        records = list((await session.execute(stmt)).scalars().all())
        assert len(records) == 4

        claimed_by_set = {r.claimed_by for r in records}
        assert "worker-alpha" in claimed_by_set
        assert "worker-beta" in claimed_by_set
        for r in records:
            assert r.status == "FEASIBLE"


@pytest.mark.asyncio
async def test_live_worker_restart_safety(
    db_manager: DatabaseManager, service: SchedulingService
) -> None:
    """Verify worker restart does not duplicate completed jobs or create duplicate schedules."""
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)

    profile = SolverProfile(
        profile_key="std",
        version=1,
        name="Standard",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )
    res = SchedulingResourceSnapshot(
        id="tech-1",
        type=ResourceType.PERSON,
        display_label="Tech 1",
        capacity=1,
    )

    act1 = SchedulingActivity(
        id="act-1", title="Task 1", duration_minutes=30, required_resource_ids=["tech-1"]
    )
    act2 = SchedulingActivity(
        id="act-2", title="Task 2", duration_minutes=30, required_resource_ids=["tech-1"]
    )

    req1 = SchedulingRequest(
        tenant_id=tenant_id,
        schedule_key="k1",
        horizon_start=t0,
        horizon_end=t1,
        activities=[act1],
        resources=[res],
        constraints=[],
        profile=profile,
    )
    req2 = SchedulingRequest(
        tenant_id=tenant_id,
        schedule_key="k2",
        horizon_start=t0,
        horizon_end=t1,
        activities=[act2],
        resources=[res],
        constraints=[],
        profile=profile,
    )

    async with db_manager.session_factory() as session:
        async with session.begin():
            j1 = await service.submit_job(session, tenant_id, req1, schedule_key="k1")
            j2 = await service.submit_job(session, tenant_id, req2, schedule_key="k2")
            j1_id = j1.id
            j2_id = j2.id

    # Worker 1 runs with batch size 1 (claims and solves ONLY j1), then terminates
    worker1 = SchedulerWorker(
        db_manager=db_manager,
        settings=make_worker_settings(worker_id="worker-init", claim_batch_size=1),
        service=service,
    )
    p1 = await worker1.run_once()
    assert p1 == 1

    # Restarted worker runs
    worker_restarted = SchedulerWorker(
        db_manager=db_manager,
        settings=make_worker_settings(worker_id="worker-restarted", claim_batch_size=5),
        service=service,
    )
    p2 = await worker_restarted.run_once()
    assert p2 == 1  # Only claims and solves j2

    # A second poll by restarted worker finds 0 pending jobs
    p3 = await worker_restarted.run_once()
    assert p3 == 0

    # Verify database: exactly 1 schedule per job, no duplicates
    async with db_manager.session_factory() as session:
        s1 = (
            (
                await session.execute(
                    select(ScheduleRecord).where(ScheduleRecord.source_job_id == j1_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(s1) == 1

        s2 = (
            (
                await session.execute(
                    select(ScheduleRecord).where(ScheduleRecord.source_job_id == j2_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(s2) == 1
