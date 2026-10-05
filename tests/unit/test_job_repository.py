"""Unit tests for JobRepository, JobSnapshot, and atomic transitions.

Task 1I.4B of Phase 1 Foundation:
- JobSnapshot: immutable provider-neutral snapshot, timezone-awareness, lifecycle invariants.
- JobRepository:
  * create: unconditionally PENDING, caller-supplied JobIdentity, server-authored created_at.
  * get: strictly tenant-scoped (tenant_id + job_id).
  * transition: static lifecycle validation first, compare-and-transition conditional UPDATE.
  * atomic updates: PENDING->RUNNING, PENDING->FAILED, RUNNING->SUCCEEDED, RUNNING->FAILED.
  * conditional miss: targeted SELECT follows UPDATE, JobNotFoundError vs JobStatusConflictError.
  * transaction ownership: zero commit, rollback, close calls.
  * immutability: zero mutation methods for identity or terminal failure.
  * architectural isolation: zero HTTP, Starlette, broker, or idempotency imports.
  * absent features: zero result, progress, retry, cancel, priority, worker lease fields.
"""

import inspect
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Insert, Update
from sqlalchemy.sql.selectable import Select

from app.core.jobs import (
    InvalidJobStatusTransitionError,
    JobFailure,
    JobFailureCode,
    JobId,
    JobIdentity,
    JobModel,
    JobNotFoundError,
    JobRepository,
    JobSnapshot,
    JobStatus,
    JobStatusConflictError,
    JobType,
    generate_job_id,
)
from app.db.repositories.base import SqlAlchemyRepository

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def _build_test_identity(
    *,
    tenant_id: str = "tenant-test-1",
    job_type: str = "tasks.sync",
    job_id: JobId | None = None,
) -> JobIdentity:
    """Construct a canonical JobIdentity."""
    jid = job_id or generate_job_id()
    return JobIdentity(
        job_id=jid,
        tenant_id=tenant_id,
        job_type=JobType(job_type),
    )


class _MockRow(NamedTuple):
    job_id: Any
    tenant_id: str
    job_type: str
    status: str
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class _NoneOffsetTzInfo(tzinfo):
    """Test-only tzinfo whose utcoffset returns None, making datetimes effectively naive."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "NoneOffset"

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None


# ===========================================================================
# 1. JobSnapshot Invariants & Timezone Rules (Sections 17, 18, 58)
# ===========================================================================


class TestJobSnapshotContract:
    """Verify JobSnapshot validation, lifecycle invariants, and timezone awareness."""

    def test_valid_pending_snapshot(self) -> None:
        identity = _build_test_identity()
        now = datetime.now(UTC)
        snap = JobSnapshot(
            identity=identity,
            status=JobStatus.PENDING,
            failure=None,
            created_at=now,
            started_at=None,
            completed_at=None,
        )
        assert snap.identity == identity
        assert snap.status == JobStatus.PENDING
        assert snap.failure is None
        assert snap.created_at == now
        assert snap.started_at is None
        assert snap.completed_at is None

    def test_valid_running_snapshot(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        snap = JobSnapshot(
            identity=identity,
            status=JobStatus.RUNNING,
            failure=None,
            created_at=t0,
            started_at=t1,
            completed_at=None,
        )
        assert snap.status == JobStatus.RUNNING
        assert snap.started_at == t1
        assert snap.completed_at is None

    def test_valid_succeeded_snapshot(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        t2 = datetime(2026, 9, 28, 10, 2, 0, tzinfo=UTC)
        snap = JobSnapshot(
            identity=identity,
            status=JobStatus.SUCCEEDED,
            failure=None,
            created_at=t0,
            started_at=t1,
            completed_at=t2,
        )
        assert snap.status == JobStatus.SUCCEEDED
        assert snap.started_at == t1
        assert snap.completed_at == t2

    def test_valid_failed_pre_execution_snapshot(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        failure = JobFailure(
            code=JobFailureCode("infra.validation_error"),
            message="Payload validation failed before execution",
        )
        snap = JobSnapshot(
            identity=identity,
            status=JobStatus.FAILED,
            failure=failure,
            created_at=t0,
            started_at=None,  # Pre-execution failure
            completed_at=t2,
        )
        assert snap.status == JobStatus.FAILED
        assert snap.failure == failure
        assert snap.started_at is None
        assert snap.completed_at == t2

    def test_valid_failed_running_snapshot(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        t2 = datetime(2026, 9, 28, 10, 2, 0, tzinfo=UTC)
        failure = JobFailure(
            code=JobFailureCode("worker.timeout"),
            message="Job exceeded timeout limit",
        )
        snap = JobSnapshot(
            identity=identity,
            status=JobStatus.FAILED,
            failure=failure,
            created_at=t0,
            started_at=t1,
            completed_at=t2,
        )
        assert snap.status == JobStatus.FAILED
        assert snap.started_at == t1
        assert snap.completed_at == t2

    def test_snapshot_rejects_naive_created_at(self) -> None:
        identity = _build_test_identity()
        naive_dt = datetime(2026, 9, 28, 10, 0, 0)
        with pytest.raises(ValidationError, match="must be timezone-aware"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.PENDING,
                created_at=naive_dt,
            )

    def test_snapshot_rejects_naive_started_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        naive_dt = datetime(2026, 9, 28, 10, 1, 0)
        with pytest.raises(ValidationError, match="must be timezone-aware"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.RUNNING,
                created_at=t0,
                started_at=naive_dt,
            )

    def test_snapshot_rejects_naive_completed_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        naive_completed = datetime(2026, 9, 28, 10, 2, 0)
        with pytest.raises(ValidationError, match="must be timezone-aware"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.SUCCEEDED,
                created_at=t0,
                started_at=t1,
                completed_at=naive_completed,
            )

    def test_snapshot_accepts_valid_non_utc_aware_datetime(self) -> None:
        identity = _build_test_identity()
        offset = timezone(timedelta(hours=5, minutes=30))
        t0 = datetime(2026, 9, 28, 15, 30, 0, tzinfo=offset)
        t1 = datetime(2026, 9, 28, 15, 35, 0, tzinfo=offset)
        snap = JobSnapshot(
            identity=identity,
            status=JobStatus.RUNNING,
            failure=None,
            created_at=t0,
            started_at=t1,
            completed_at=None,
        )
        assert snap.created_at == t0
        assert snap.created_at.tzinfo == offset
        assert snap.started_at == t1
        assert snap.started_at.tzinfo == offset

    def test_snapshot_rejects_custom_tzinfo_with_none_utcoffset_created_at(self) -> None:
        identity = _build_test_identity()
        fake_tz = _NoneOffsetTzInfo()
        dt_with_none_offset = datetime(2026, 9, 28, 10, 0, 0, tzinfo=fake_tz)
        assert dt_with_none_offset.tzinfo is not None
        assert dt_with_none_offset.utcoffset() is None

        with pytest.raises(ValidationError, match="must be timezone-aware"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.PENDING,
                created_at=dt_with_none_offset,
            )

    def test_snapshot_rejects_custom_tzinfo_with_none_utcoffset_started_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        fake_tz = _NoneOffsetTzInfo()
        dt_with_none_offset = datetime(2026, 9, 28, 10, 1, 0, tzinfo=fake_tz)

        with pytest.raises(ValidationError, match="must be timezone-aware"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.RUNNING,
                created_at=t0,
                started_at=dt_with_none_offset,
            )

    def test_snapshot_rejects_custom_tzinfo_with_none_utcoffset_completed_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        fake_tz = _NoneOffsetTzInfo()
        dt_with_none_offset = datetime(2026, 9, 28, 10, 2, 0, tzinfo=fake_tz)

        with pytest.raises(ValidationError, match="must be timezone-aware"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.SUCCEEDED,
                created_at=t0,
                started_at=t1,
                completed_at=dt_with_none_offset,
            )

    def test_snapshot_never_silently_normalizes_or_attaches_timezone(self) -> None:
        identity = _build_test_identity()
        naive_dt = datetime(2026, 9, 28, 10, 0, 0)
        with pytest.raises(ValidationError, match="must be timezone-aware"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.PENDING,
                created_at=naive_dt,
            )

    def test_snapshot_rejects_pending_with_started_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="PENDING job snapshot must have started_at=None"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.PENDING,
                created_at=t0,
                started_at=t1,
            )

    def test_snapshot_rejects_pending_with_completed_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        match_msg = "PENDING job snapshot must have completed_at=None"
        with pytest.raises(ValidationError, match=match_msg):
            JobSnapshot(
                identity=identity,
                status=JobStatus.PENDING,
                created_at=t0,
                completed_at=t0,
            )

    def test_snapshot_rejects_pending_with_failure(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        fail = JobFailure(code=JobFailureCode("test.err"), message="error")
        with pytest.raises(ValidationError, match="PENDING job snapshot must have failure=None"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.PENDING,
                failure=fail,
                created_at=t0,
            )

    def test_snapshot_rejects_running_without_started_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="RUNNING job snapshot must have started_at set"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.RUNNING,
                created_at=t0,
                started_at=None,
            )

    def test_snapshot_rejects_running_with_completed_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        match_msg = "RUNNING job snapshot must have completed_at=None"
        with pytest.raises(ValidationError, match=match_msg):
            JobSnapshot(
                identity=identity,
                status=JobStatus.RUNNING,
                created_at=t0,
                started_at=t1,
                completed_at=t1,
            )

    def test_snapshot_rejects_succeeded_without_completed_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        match_msg = "SUCCEEDED job snapshot must have completed_at set"
        with pytest.raises(ValidationError, match=match_msg):
            JobSnapshot(
                identity=identity,
                status=JobStatus.SUCCEEDED,
                created_at=t0,
                started_at=t1,
                completed_at=None,
            )

    def test_snapshot_rejects_failed_without_failure(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 9, 28, 10, 2, 0, tzinfo=UTC)
        with pytest.raises(ValidationError, match="FAILED job snapshot must have failure set"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.FAILED,
                failure=None,
                created_at=t0,
                completed_at=t2,
            )

    def test_snapshot_rejects_failed_without_completed_at(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        fail = JobFailure(code=JobFailureCode("test.err"), message="error")
        with pytest.raises(ValidationError, match="FAILED job snapshot must have completed_at set"):
            JobSnapshot(
                identity=identity,
                status=JobStatus.FAILED,
                failure=fail,
                created_at=t0,
                completed_at=None,
            )

    def test_snapshot_timestamp_ordering_invariants(self) -> None:
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)
        t_before = datetime(2026, 9, 28, 9, 59, 0, tzinfo=UTC)

        # started_at < created_at
        msg_start = "started_at must be greater than or equal to created_at"
        with pytest.raises(ValidationError, match=msg_start):
            JobSnapshot(
                identity=identity,
                status=JobStatus.RUNNING,
                created_at=t0,
                started_at=t_before,
            )

        # completed_at < started_at
        t1 = datetime(2026, 9, 28, 10, 2, 0, tzinfo=UTC)
        t_mid = datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)
        msg_comp = "completed_at must be greater than or equal to started_at"
        with pytest.raises(ValidationError, match=msg_comp):
            JobSnapshot(
                identity=identity,
                status=JobStatus.SUCCEEDED,
                created_at=t0,
                started_at=t1,
                completed_at=t_mid,
            )

    def test_snapshot_immutability(self) -> None:
        identity = _build_test_identity()
        now = datetime.now(UTC)
        snap = JobSnapshot(
            identity=identity,
            status=JobStatus.PENDING,
            created_at=now,
        )
        with pytest.raises(ValidationError):
            snap.status = JobStatus.RUNNING  # type: ignore[misc]

        with pytest.raises(ValidationError):
            JobSnapshot(
                identity=identity,
                status=JobStatus.PENDING,
                created_at=now,
                extra_field="disallowed",  # type: ignore[call-arg]
            )


# ===========================================================================
# 2. JobRepository.create Tests (Sections 21, 22, 23, 59)
# ===========================================================================


class TestJobRepositoryCreate:
    """Verify create persists PENDING status, caller identity, and server-authored created_at."""

    @pytest.mark.asyncio
    async def test_create_persists_pending_job_successfully(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        identity = _build_test_identity()
        now = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)

        mock_row = _MockRow(
            job_id=identity.job_id.value,
            tenant_id=identity.tenant_id,
            job_type=identity.job_type.value,
            status="pending",
            failure_code=None,
            failure_message=None,
            created_at=now,
            started_at=None,
            completed_at=None,
        )
        mock_result = MagicMock()
        mock_result.one.return_value = mock_row
        session.execute.return_value = mock_result

        repo = JobRepository(session)
        snap = await repo.create(identity=identity)

        assert isinstance(snap, JobSnapshot)
        assert snap.identity == identity
        assert snap.status == JobStatus.PENDING
        assert snap.failure is None
        assert snap.created_at == now
        assert snap.started_at is None
        assert snap.completed_at is None

        # Verify SQL statement structure
        assert session.execute.await_count == 1
        stmt = session.execute.call_args[0][0]
        assert isinstance(stmt, Insert)

        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "INSERT INTO jobs" in compiled
        assert "RETURNING" in compiled

        # Transaction invariants: zero commit/rollback/close
        assert session.commit.await_count == 0
        assert session.rollback.await_count == 0
        assert session.close.await_count == 0

    @pytest.mark.asyncio
    async def test_create_rejects_invalid_identity_type(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        repo = JobRepository(session)
        with pytest.raises(TypeError, match="identity must be a JobIdentity instance"):
            await repo.create(identity="not-an-identity")  # type: ignore[arg-type]

    def test_create_does_not_accept_initial_status_parameter(self) -> None:
        sig = inspect.signature(JobRepository.create)
        assert "identity" in sig.parameters
        assert "status" not in sig.parameters
        assert "initial_status" not in sig.parameters


# ===========================================================================
# 3. JobRepository.get Tests (Sections 24, 25, 60)
# ===========================================================================


class TestJobRepositoryGet:
    """Verify get is strictly tenant-scoped and correctly distinguishes tenant boundaries."""

    @pytest.mark.asyncio
    async def test_get_successful_lookup(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        identity = _build_test_identity()
        now = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)

        mock_row = _MockRow(
            job_id=identity.job_id.value,
            tenant_id=identity.tenant_id,
            job_type=identity.job_type.value,
            status="pending",
            failure_code=None,
            failure_message=None,
            created_at=now,
            started_at=None,
            completed_at=None,
        )
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row
        session.execute.return_value = mock_result

        repo = JobRepository(session)
        snap = await repo.get(tenant_id=identity.tenant_id, job_id=identity.job_id)

        assert snap is not None
        assert snap.identity == identity
        assert snap.status == JobStatus.PENDING

        stmt = session.execute.call_args[0][0]
        assert isinstance(stmt, Select)
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "jobs.job_id = " in compiled
        assert "jobs.tenant_id = " in compiled

    @pytest.mark.asyncio
    async def test_get_not_found_returns_none(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = None
        session.execute.return_value = mock_result

        repo = JobRepository(session)
        jid = generate_job_id()
        snap = await repo.get(tenant_id="tenant-x", job_id=jid)
        assert snap is None

    @pytest.mark.asyncio
    async def test_get_validates_input_types(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        repo = JobRepository(session)
        jid = generate_job_id()

        with pytest.raises(TypeError, match="job_id must be a JobId instance"):
            await repo.get(tenant_id="tenant-x", job_id="not-a-job-id")  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="tenant_id must be a str instance"):
            await repo.get(tenant_id=123, job_id=jid)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="tenant_id must be non-empty"):
            await repo.get(tenant_id="   ", job_id=jid)


# ===========================================================================
# 4. JobRepository.transition Static Validation (Sections 27, 29, 61, 70)
# ===========================================================================


class TestJobRepositoryTransitionStaticValidation:
    """Verify static lifecycle and argument validation execute BEFORE any SQL."""

    @pytest.mark.asyncio
    async def test_invalid_lifecycle_edge_executes_zero_sql(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        repo = JobRepository(session)
        jid = generate_job_id()

        # PENDING -> SUCCEEDED is forbidden by canonical lifecycle
        with pytest.raises(InvalidJobStatusTransitionError):
            await repo.transition(
                tenant_id="tenant-acme",
                job_id=jid,
                expected_status=JobStatus.PENDING,
                target_status=JobStatus.SUCCEEDED,
            )

        # Zero SQL statements executed
        assert session.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_same_state_transition_executes_zero_sql(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        repo = JobRepository(session)
        jid = generate_job_id()

        with pytest.raises(InvalidJobStatusTransitionError):
            await repo.transition(
                tenant_id="tenant-acme",
                job_id=jid,
                expected_status=JobStatus.PENDING,
                target_status=JobStatus.PENDING,
            )

        assert session.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_failed_target_without_failure_rejected_before_sql(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        repo = JobRepository(session)
        jid = generate_job_id()

        msg_fail = "JobFailure must be provided when transitioning to FAILED"
        with pytest.raises(ValueError, match=msg_fail):
            await repo.transition(
                tenant_id="tenant-acme",
                job_id=jid,
                expected_status=JobStatus.PENDING,
                target_status=JobStatus.FAILED,
                failure=None,
            )

        assert session.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_non_failed_target_with_failure_rejected_before_sql(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        repo = JobRepository(session)
        jid = generate_job_id()
        failure = JobFailure(code=JobFailureCode("infra.error"), message="unexpected")

        msg_nonfail = "JobFailure must be None when transitioning to 'running'"
        with pytest.raises(ValueError, match=msg_nonfail):
            await repo.transition(
                tenant_id="tenant-acme",
                job_id=jid,
                expected_status=JobStatus.PENDING,
                target_status=JobStatus.RUNNING,
                failure=failure,
            )

        assert session.execute.await_count == 0


# ===========================================================================
# 5. JobRepository.transition Conditional UPDATE (Sections 26, 30-33, 62-66)
# ===========================================================================


class TestJobRepositoryAtomicTransitions:
    """Verify conditional compare-and-transition SQL compilation and timestamp behavior."""

    @pytest.mark.asyncio
    async def test_pending_to_running_transition(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 12, 1, 0, tzinfo=UTC)

        mock_row = _MockRow(
            job_id=identity.job_id.value,
            tenant_id=identity.tenant_id,
            job_type=identity.job_type.value,
            status="running",
            failure_code=None,
            failure_message=None,
            created_at=t0,
            started_at=t1,
            completed_at=None,
        )
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row
        session.execute.return_value = mock_result

        repo = JobRepository(session)
        snap = await repo.transition(
            tenant_id=identity.tenant_id,
            job_id=identity.job_id,
            expected_status=JobStatus.PENDING,
            target_status=JobStatus.RUNNING,
        )

        assert snap.status == JobStatus.RUNNING
        assert snap.started_at == t1
        assert snap.completed_at is None
        assert snap.failure is None

        # Verify UPDATE executed first (no prior SELECT)
        assert session.execute.await_count == 1
        stmt = session.execute.call_args[0][0]
        assert isinstance(stmt, Update)

        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "UPDATE jobs SET" in compiled
        assert "status = 'running'" in compiled or "status=" in compiled
        assert "jobs.status = 'pending'" in compiled or "jobs.status =" in compiled
        assert "RETURNING" in compiled

    @pytest.mark.asyncio
    async def test_running_to_succeeded_transition(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 12, 1, 0, tzinfo=UTC)
        t2 = datetime(2026, 9, 28, 12, 2, 0, tzinfo=UTC)

        mock_row = _MockRow(
            job_id=identity.job_id.value,
            tenant_id=identity.tenant_id,
            job_type=identity.job_type.value,
            status="succeeded",
            failure_code=None,
            failure_message=None,
            created_at=t0,
            started_at=t1,
            completed_at=t2,
        )
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row
        session.execute.return_value = mock_result

        repo = JobRepository(session)
        snap = await repo.transition(
            tenant_id=identity.tenant_id,
            job_id=identity.job_id,
            expected_status=JobStatus.RUNNING,
            target_status=JobStatus.SUCCEEDED,
        )

        assert snap.status == JobStatus.SUCCEEDED
        assert snap.started_at == t1
        assert snap.completed_at == t2
        assert snap.failure is None

        # started_at must not be overwritten
        stmt = session.execute.call_args[0][0]
        assert isinstance(stmt, Update)
        update_params = stmt.compile(dialect=postgresql.dialect()).params
        assert "started_at" not in update_params

    @pytest.mark.asyncio
    async def test_pending_to_failed_pre_execution_transition(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 9, 28, 12, 1, 0, tzinfo=UTC)
        failure = JobFailure(
            code=JobFailureCode("infra.invalid_input"),
            message="Input data was rejected before processing",
        )

        mock_row = _MockRow(
            job_id=identity.job_id.value,
            tenant_id=identity.tenant_id,
            job_type=identity.job_type.value,
            status="failed",
            failure_code=failure.code.value,
            failure_message=failure.message,
            created_at=t0,
            started_at=None,
            completed_at=t2,
        )
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row
        session.execute.return_value = mock_result

        repo = JobRepository(session)
        snap = await repo.transition(
            tenant_id=identity.tenant_id,
            job_id=identity.job_id,
            expected_status=JobStatus.PENDING,
            target_status=JobStatus.FAILED,
            failure=failure,
        )

        assert snap.status == JobStatus.FAILED
        assert snap.started_at is None
        assert snap.completed_at == t2
        assert snap.failure == failure

    @pytest.mark.asyncio
    async def test_running_to_failed_transition(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        identity = _build_test_identity()
        t0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
        t1 = datetime(2026, 9, 28, 12, 1, 0, tzinfo=UTC)
        t2 = datetime(2026, 9, 28, 12, 2, 0, tzinfo=UTC)
        failure = JobFailure(
            code=JobFailureCode("worker.out_of_memory"),
            message="Process exceeded allocated memory",
        )

        mock_row = _MockRow(
            job_id=identity.job_id.value,
            tenant_id=identity.tenant_id,
            job_type=identity.job_type.value,
            status="failed",
            failure_code=failure.code.value,
            failure_message=failure.message,
            created_at=t0,
            started_at=t1,
            completed_at=t2,
        )
        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row
        session.execute.return_value = mock_result

        repo = JobRepository(session)
        snap = await repo.transition(
            tenant_id=identity.tenant_id,
            job_id=identity.job_id,
            expected_status=JobStatus.RUNNING,
            target_status=JobStatus.FAILED,
            failure=failure,
        )

        assert snap.status == JobStatus.FAILED
        assert snap.started_at == t1
        assert snap.completed_at == t2
        assert snap.failure == failure


# ===========================================================================
# 6. Conditional Miss: Not Found vs Status Conflict (Sections 35-39, 67-69)
# ===========================================================================


class TestJobRepositoryConditionalMiss:
    """Verify conditional miss executes UPDATE first, then targeted SELECT to distinguish errors."""

    @pytest.mark.asyncio
    async def test_conditional_miss_raises_not_found_when_job_does_not_exist(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        jid = generate_job_id()

        # 1. UPDATE returns None (no match)
        mock_update_result = MagicMock()
        mock_update_result.one_or_none.return_value = None

        # 2. SELECT status returns None (job doesn't exist)
        mock_select_result = MagicMock()
        mock_select_result.scalar_one_or_none.return_value = None

        session.execute.side_effect = [mock_update_result, mock_select_result]

        repo = JobRepository(session)
        with pytest.raises(JobNotFoundError) as exc_info:
            await repo.transition(
                tenant_id="tenant-acme",
                job_id=jid,
                expected_status=JobStatus.PENDING,
                target_status=JobStatus.RUNNING,
            )

        assert exc_info.value.tenant_id == "tenant-acme"
        assert exc_info.value.job_id == jid

        # Verify call sequence: exactly 2 statements: UPDATE then SELECT
        assert session.execute.await_count == 2
        calls = session.execute.call_args_list
        assert isinstance(calls[0][0][0], Update)
        assert isinstance(calls[1][0][0], Select)

        # No retry
        assert session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_conditional_miss_raises_conflict_when_status_differs(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        jid = generate_job_id()

        # 1. UPDATE returns None (conflict)
        mock_update_result = MagicMock()
        mock_update_result.one_or_none.return_value = None

        # 2. SELECT returns actual status 'running' (was expecting 'pending')
        mock_select_result = MagicMock()
        mock_select_result.scalar_one_or_none.return_value = "running"

        session.execute.side_effect = [mock_update_result, mock_select_result]

        repo = JobRepository(session)
        with pytest.raises(JobStatusConflictError) as exc_info:
            await repo.transition(
                tenant_id="tenant-acme",
                job_id=jid,
                expected_status=JobStatus.PENDING,
                target_status=JobStatus.RUNNING,
            )

        err = exc_info.value
        assert err.job_id == jid
        assert err.expected_status == JobStatus.PENDING
        assert err.actual_status == JobStatus.RUNNING
        assert "expected 'pending', actual 'running'" in str(err)
        # Verify tenant_id is NOT in string representation
        assert "tenant-acme" not in str(err)

        # Verify call sequence: UPDATE first, then SELECT
        assert session.execute.await_count == 2
        calls = session.execute.call_args_list
        assert isinstance(calls[0][0][0], Update)
        assert isinstance(calls[1][0][0], Select)


# ===========================================================================
# 7. Transaction Ownership & Immutability Rules (Sections 16, 20, 40-42, 71, 72)
# ===========================================================================


class TestJobRepositoryOwnershipAndImmutability:
    """Verify repository respects UnitOfWork ownership and strictly forbids ad-hoc mutation."""

    def test_repository_inherits_from_sqlalchemy_repository(self) -> None:
        assert issubclass(JobRepository, SqlAlchemyRepository)

    def test_repository_exposes_no_transaction_control(self) -> None:
        repo_methods = dir(JobRepository)
        assert "commit" not in repo_methods
        assert "rollback" not in repo_methods
        assert "close" not in repo_methods
        assert "begin" not in repo_methods

    def test_repository_exposes_no_generic_update_or_delete(self) -> None:
        repo_methods = dir(JobRepository)
        forbidden = {
            "update",
            "update_job",
            "delete",
            "delete_job",
            "purge",
            "purge_job",
            "cleanup",
            "cleanup_job",
            "update_failure_message",
            "update_status",
        }
        assert set(repo_methods).isdisjoint(forbidden)


# ===========================================================================
# 8. Absent Features & Architectural Isolation (Sections 43-48, 73, 74)
# ===========================================================================


class TestJobPersistenceArchitecturalIsolation:
    """Verify absence of speculative machinery and strict architectural boundaries."""

    def test_absent_features_in_model_snapshot_and_repository(self) -> None:
        model_cols = set(JobModel.__table__.columns.keys())
        snapshot_fields = set(JobSnapshot.model_fields.keys())
        repo_methods = set(dir(JobRepository))

        forbidden_names = {
            # Result fields
            "result",
            "result_json",
            "result_reference",
            "resource_id",
            "resource_url",
            "response_body",
            "http_status",
            # Progress fields
            "progress",
            "percentage",
            "step",
            "total_steps",
            # Retry fields
            "attempt_count",
            "retry_count",
            "next_retry_at",
            "max_retries",
            # Cancellation fields
            "cancel_requested",
            "cancelled_at",
            # Priority / scheduling
            "priority",
            "scheduled_at",
            # Worker claiming / lease
            "claim_token",
            "locked_by",
            "lease_until",
            "heartbeat_at",
            "worker_id",
        }

        assert model_cols.isdisjoint(forbidden_names)
        assert snapshot_fields.isdisjoint(forbidden_names)
        assert repo_methods.isdisjoint(forbidden_names)

    def test_architectural_isolation_imports(self) -> None:
        import app.core.jobs.models as mod_models
        import app.core.jobs.repository as mod_repo

        models_src = inspect.getsource(mod_models)
        repo_src = inspect.getsource(mod_repo)

        forbidden_imports = [
            "fastapi",
            "FastAPI",
            "starlette",
            "Starlette",
            "Request",
            "Response",
            "ApiError",
            "aio_pika",
            "rabbitmq",
            "RabbitMQ",
            "IdempotencyRepository",
            "IdempotentMutationExecutor",
            "create_engine",
            "async_sessionmaker",
        ]

        for code_str, mod_name in [(models_src, "models.py"), (repo_src, "repository.py")]:
            for forbidden in forbidden_imports:
                assert f"import {forbidden}" not in code_str, (
                    f"Forbidden import '{forbidden}' found in {mod_name}"
                )
                assert f"from {forbidden}" not in code_str, (
                    f"Forbidden import '{forbidden}' found in {mod_name}"
                )
