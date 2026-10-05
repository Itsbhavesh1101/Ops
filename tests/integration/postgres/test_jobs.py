"""Live PostgreSQL asynchronous job persistence, lifecycle, and constraint verification.

Part of Phase 1 Foundation Task 1J.2A.
Validates:
- Job creation in PENDING status with server-authored timestamps
- Tenant-scoped retrieval (get tenant-a succeeds, get tenant-b returns None)
- State transitions (PENDING -> RUNNING -> SUCCEEDED) with timestamp integrity
- Pre-execution failure (PENDING -> FAILED) and running failure (RUNNING -> FAILED)
- PostgreSQL database CHECK constraints enforced against invalid direct SQL
- Critical concurrent transition test: two concurrent sessions attempting PENDING -> RUNNING
  yield exactly one winner and one JobStatusConflictError
- Distinction between JobNotFoundError (nonexistent) and JobStatusConflictError (stale status)
- Server-authored timezone awareness on readback
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.jobs.contracts import (
    JobFailure,
    JobFailureCode,
    JobIdentity,
    JobType,
    generate_job_id,
)
from app.core.jobs.lifecycle import JobStatus
from app.core.jobs.repository import (
    JobNotFoundError,
    JobRepository,
    JobStatusConflictError,
)
from app.db.uow import SqlAlchemyUnitOfWork


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_create_pending(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify job creation in PENDING status with server-authored created_at."""
    test_tenant = f"tenant-job-{uuid4().hex[:8]}"
    jid = generate_job_id()
    jtype = JobType("workflow.orchestrate")
    identity = JobIdentity(job_id=jid, tenant_id=test_tenant, job_type=jtype)

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        snapshot = await repo.create(identity=identity)
        assert snapshot.identity == identity
        assert snapshot.status == JobStatus.PENDING
        assert snapshot.started_at is None
        assert snapshot.completed_at is None
        assert snapshot.failure is None
        assert snapshot.created_at is not None
        assert snapshot.created_at.tzinfo is not None
        assert snapshot.created_at.utcoffset() is not None
        await uow.commit()

    # Read back in a fresh session
    async with SqlAlchemyUnitOfWork(session_factory) as uow2:
        repo2 = JobRepository(uow2.session)
        persisted = await repo2.get(tenant_id=test_tenant, job_id=jid)
        assert persisted is not None
        assert persisted.status == JobStatus.PENDING
        assert persisted.created_at == snapshot.created_at


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_tenant_scoped_read(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify tenant isolation: job readable by owning tenant, returns None for other tenants."""
    owning_tenant = f"tenant-owner-{uuid4().hex[:8]}"
    other_tenant = f"tenant-other-{uuid4().hex[:8]}"
    jid = generate_job_id()
    identity = JobIdentity(
        job_id=jid,
        tenant_id=owning_tenant,
        job_type=JobType("reports.generate"),
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        await repo.create(identity=identity)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow_read:
        repo_read = JobRepository(uow_read.session)
        # Owning tenant gets snapshot
        owner_snapshot = await repo_read.get(tenant_id=owning_tenant, job_id=jid)
        assert owner_snapshot is not None
        assert owner_snapshot.identity.job_id == jid

        # Other tenant gets None (no leakage)
        other_snapshot = await repo_read.get(tenant_id=other_tenant, job_id=jid)
        assert other_snapshot is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_transitions_lifecycle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify PENDING -> RUNNING -> SUCCEEDED lifecycle with server timestamp progression."""
    tenant = f"tenant-lifecycle-{uuid4().hex[:8]}"
    jid = generate_job_id()
    identity = JobIdentity(job_id=jid, tenant_id=tenant, job_type=JobType("data.export"))

    # 1. Create PENDING
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        await repo.create(identity=identity)
        await uow.commit()

    # 2. PENDING -> RUNNING
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        running_snap = await repo.transition(
            tenant_id=tenant,
            job_id=jid,
            expected_status=JobStatus.PENDING,
            target_status=JobStatus.RUNNING,
        )
        assert running_snap.status == JobStatus.RUNNING
        assert running_snap.started_at is not None
        assert running_snap.completed_at is None
        assert running_snap.failure is None
        await uow.commit()

    started_at_recorded = running_snap.started_at

    # 3. RUNNING -> SUCCEEDED
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        succeeded_snap = await repo.transition(
            tenant_id=tenant,
            job_id=jid,
            expected_status=JobStatus.RUNNING,
            target_status=JobStatus.SUCCEEDED,
        )
        assert succeeded_snap.status == JobStatus.SUCCEEDED
        assert succeeded_snap.started_at == started_at_recorded
        assert succeeded_snap.completed_at is not None
        assert succeeded_snap.completed_at >= started_at_recorded
        assert succeeded_snap.failure is None
        await uow.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_pre_execution_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify pre-execution failure (PENDING -> FAILED): started_at is NULL, completed_at set."""
    tenant = f"tenant-prefail-{uuid4().hex[:8]}"
    jid = generate_job_id()
    identity = JobIdentity(job_id=jid, tenant_id=tenant, job_type=JobType("data.sync"))
    failure = JobFailure(
        code=JobFailureCode("job.invalid_config"),
        message="Invalid configuration parameters",
    )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        await repo.create(identity=identity)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        failed_snap = await repo.transition(
            tenant_id=tenant,
            job_id=jid,
            expected_status=JobStatus.PENDING,
            target_status=JobStatus.FAILED,
            failure=failure,
        )
        assert failed_snap.status == JobStatus.FAILED
        assert failed_snap.started_at is None
        assert failed_snap.completed_at is not None
        assert failed_snap.failure == failure
        await uow.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_running_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify running failure (PENDING -> RUNNING -> FAILED).

    started_at preserved, completed_at set.
    """
    tenant = f"tenant-runfail-{uuid4().hex[:8]}"
    jid = generate_job_id()
    identity = JobIdentity(job_id=jid, tenant_id=tenant, job_type=JobType("data.process"))
    failure = JobFailure(code=JobFailureCode("job.internal_error"), message="OOM killer invoked")

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        await repo.create(identity=identity)
        await repo.transition(
            tenant_id=tenant,
            job_id=jid,
            expected_status=JobStatus.PENDING,
            target_status=JobStatus.RUNNING,
        )
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        failed_snap = await repo.transition(
            tenant_id=tenant,
            job_id=jid,
            expected_status=JobStatus.RUNNING,
            target_status=JobStatus.FAILED,
            failure=failure,
        )
        assert failed_snap.status == JobStatus.FAILED
        assert failed_snap.started_at is not None
        assert failed_snap.completed_at is not None
        assert failed_snap.failure == failure
        await uow.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_check_constraints_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify PostgreSQL check constraints on jobs table reject invalid states."""
    test_tenant = f"tenant-ck-{uuid4().hex[:8]}"

    invalid_sql_cases = [
        # 1. Invalid status value
        (
            "INSERT INTO jobs (job_id, tenant_id, job_type, status) "
            "VALUES (:jid, :tid, 'tasks.run', 'invalid_status')",
            "ck_jobs_status",
        ),
        # 2. FAILED without failure_code
        (
            "INSERT INTO jobs (job_id, tenant_id, job_type, status, completed_at) "
            "VALUES (:jid, :tid, 'tasks.run', 'failed', now())",
            "ck_jobs_failure_consistency",
        ),
        # 3. Non-FAILED with failure_code
        (
            "INSERT INTO jobs (job_id, tenant_id, job_type, status, failure_code, failure_message) "
            "VALUES (:jid, :tid, 'tasks.run', 'pending', 'error.code', 'msg')",
            "ck_jobs_failure_consistency",
        ),
        # 4. PENDING with started_at
        (
            "INSERT INTO jobs (job_id, tenant_id, job_type, status, started_at) "
            "VALUES (:jid, :tid, 'tasks.run', 'pending', now())",
            "ck_jobs_timestamps_state",
        ),
        # 5. RUNNING without started_at
        (
            "INSERT INTO jobs (job_id, tenant_id, job_type, status) "
            "VALUES (:jid, :tid, 'tasks.run', 'running')",
            "ck_jobs_timestamps_state",
        ),
        # 6. SUCCEEDED without completed_at
        (
            "INSERT INTO jobs (job_id, tenant_id, job_type, status, started_at) "
            "VALUES (:jid, :tid, 'tasks.run', 'succeeded', now())",
            "ck_jobs_timestamps_state",
        ),
        # 7. Malformed job_type (uppercase / spaces)
        (
            "INSERT INTO jobs (job_id, tenant_id, job_type, status) "
            "VALUES (:jid, :tid, 'INVALID TYPE', 'pending')",
            "ck_jobs_job_type_format",
        ),
        # 8. Malformed non-null failure_code
        (
            (
                "INSERT INTO jobs (job_id, tenant_id, job_type, status, failure_code, "
                "failure_message, completed_at) "
                "VALUES (:jid, :tid, 'tasks.run', 'failed', 'INVALID CODE', 'msg', now())"
            ),
            "ck_jobs_failure_code_format",
        ),
        # 9. Timestamp ordering violation (completed_at < started_at)
        (
            "INSERT INTO jobs (job_id, tenant_id, job_type, status, started_at, completed_at) "
            "VALUES (:jid, :tid, 'tasks.run', 'succeeded', now(), now() - interval '1 hour')",
            "ck_jobs_timestamp_order",
        ),
    ]

    for sql_query, expected_constraint in invalid_sql_cases:
        async with session_factory() as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    text(sql_query),
                    {"jid": uuid4(), "tid": test_tenant},
                )
                await session.commit()

            err_text = str(exc_info.value).lower()
            assert "check constraint" in err_text or expected_constraint in err_text, (
                f"Expected violation of '{expected_constraint}', got: {exc_info.value}"
            )
            await session.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_concurrent_transition(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify atomic conditional UPDATE.

    Two concurrent PENDING->RUNNING yields exactly one winner.
    """
    tenant = f"tenant-race-{uuid4().hex[:8]}"
    jid = generate_job_id()
    identity = JobIdentity(job_id=jid, tenant_id=tenant, job_type=JobType("render.video"))

    # Create job in PENDING
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        await repo.create(identity=identity)
        await uow.commit()

    # Two concurrent tasks attempting PENDING -> RUNNING
    success_count = 0
    conflict_count = 0

    async def attempt_transition() -> None:
        nonlocal success_count, conflict_count
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            repo = JobRepository(uow.session)
            try:
                await repo.transition(
                    tenant_id=tenant,
                    job_id=jid,
                    expected_status=JobStatus.PENDING,
                    target_status=JobStatus.RUNNING,
                )
                await uow.commit()
                success_count += 1
            except JobStatusConflictError:
                conflict_count += 1

    await asyncio.wait_for(asyncio.gather(attempt_transition(), attempt_transition()), timeout=5.0)

    assert success_count == 1, f"Expected exactly 1 transition to succeed, got {success_count}"
    assert conflict_count == 1, f"Expected exactly 1 conflict error, got {conflict_count}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_job_not_found_vs_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify repository distinguishes JobNotFoundError from JobStatusConflictError."""
    tenant = f"tenant-distinguish-{uuid4().hex[:8]}"
    jid = generate_job_id()
    identity = JobIdentity(job_id=jid, tenant_id=tenant, job_type=JobType("batch.clean"))

    # 1. Nonexistent job produces JobNotFoundError
    nonexistent_jid = generate_job_id()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        with pytest.raises(JobNotFoundError) as exc_info:
            await repo.transition(
                tenant_id=tenant,
                job_id=nonexistent_jid,
                expected_status=JobStatus.PENDING,
                target_status=JobStatus.RUNNING,
            )
        assert exc_info.value.job_id == nonexistent_jid

    # 2. Existing job with stale status produces JobStatusConflictError
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        await repo.create(identity=identity)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        repo = JobRepository(uow.session)
        with pytest.raises(JobStatusConflictError) as exc_info:
            # Job is PENDING, but expected_status is RUNNING
            await repo.transition(
                tenant_id=tenant,
                job_id=jid,
                expected_status=JobStatus.RUNNING,
                target_status=JobStatus.SUCCEEDED,
            )
        assert exc_info.value.expected_status == JobStatus.RUNNING
        assert exc_info.value.actual_status == JobStatus.PENDING
