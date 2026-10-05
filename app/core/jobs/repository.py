"""Repository for canonical PostgreSQL asynchronous job persistence and atomic transitions.

Part of Phase 1 Foundation Task 1I.4B.
Adheres strictly to the repository and Unit of Work foundation:
- Externally supplied AsyncSession from Unit of Work.
- Shared transaction: executes within the caller's active UnitOfWork boundary.
- Zero engine or session creation; zero session commit, rollback, or close.
- Provider-neutral JobSnapshot with frozen invariants and timezone-aware timestamps.
- Tenant-scoped job creation and retrieval.
- Atomic compare-and-transition status updates using PostgreSQL conditional UPDATE ... RETURNING.
- Server-authored timestamps for creation and lifecycle transitions.
- Two-phase conditional miss inspection: distinguishes not-found from state conflict.
- Deterministic JobNotFoundError and JobStatusConflictError semantics.
- Immutability: identity fields (job_id, tenant_id, job_type, created_at) are never mutable.
- Terminal state immutability: terminal transitions are rejected before SQL.
- Architectural isolation: zero HTTP, Starlette, broker, or idempotency imports.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from app.core.jobs.contracts import (
    JobFailure,
    JobFailureCode,
    JobId,
    JobIdentity,
    JobType,
)
from app.core.jobs.lifecycle import (
    JobStatus,
    validate_job_status_transition,
)
from app.core.jobs.models import JobModel
from app.db.repositories.base import SqlAlchemyRepository

POSTGRES_JOB_RUNTIME_TEST: str = "DEFERRED_TO_TASK_1J_RUNTIME_INTEGRATION"


class JobNotFoundError(Exception):
    """Raised when a job cannot be found for the specified tenant and job_id."""

    def __init__(self, *, tenant_id: str, job_id: JobId) -> None:
        self.tenant_id = tenant_id
        self.job_id = job_id
        super().__init__(f"Job '{job_id}' not found for tenant '{tenant_id}'.")


class JobStatusConflictError(Exception):
    """Raised when a job exists but its persisted status differs from expected_status."""

    def __init__(
        self,
        *,
        job_id: JobId,
        expected_status: JobStatus,
        actual_status: JobStatus,
    ) -> None:
        self.job_id = job_id
        self.expected_status = expected_status
        self.actual_status = actual_status
        super().__init__(
            f"Job '{job_id}' status conflict: expected '{expected_status.value}', "
            f"actual '{actual_status.value}'."
        )


def _is_timezone_aware(value: datetime) -> bool:
    """Return True if the datetime value is reliably timezone-aware.

    A datetime is timezone-aware under Python semantics iff value.tzinfo is not None
    and value.utcoffset() is not None. A custom tzinfo returning None from utcoffset()
    represents an effectively naive datetime and is rejected.
    """
    return value.tzinfo is not None and value.utcoffset() is not None


class JobSnapshot(BaseModel):
    """Immutable provider-neutral snapshot of a persisted asynchronous job.

    Invariants:
    - All non-None timestamps must be timezone-aware: tzinfo must be present
      and utcoffset() must not be None.
    - Naive or effectively-naive datetimes are strictly rejected.
    - Zero silent timezone normalization or UTC attachment.
    - PENDING:
      * failure is None
      * started_at is None
      * completed_at is None
    - RUNNING:
      * failure is None
      * started_at is not None
      * completed_at is None
    - SUCCEEDED:
      * failure is None
      * started_at is not None
      * completed_at is not None
    - FAILED:
      * failure is not None
      * completed_at is not None
      * started_at is optional (supports pre-execution failure)
    - Timestamp ordering:
      * started_at >= created_at (if started_at is present)
      * completed_at >= created_at (if completed_at is present)
      * completed_at >= started_at (if both started_at and completed_at are present)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: JobIdentity
    status: JobStatus
    failure: JobFailure | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_snapshot_invariants(self) -> "JobSnapshot":
        # 1. Enforce strict timezone-awareness for all timestamps
        for dt_name, dt_val in [
            ("created_at", self.created_at),
            ("started_at", self.started_at),
            ("completed_at", self.completed_at),
        ]:
            if dt_val is not None and not _is_timezone_aware(dt_val):
                raise ValueError(
                    f"JobSnapshot field '{dt_name}' must be timezone-aware "
                    f"(tzinfo present and utcoffset() non-None)"
                )

        # 2. Status-specific invariants
        if self.status == JobStatus.PENDING:
            if self.failure is not None:
                raise ValueError("PENDING job snapshot must have failure=None")
            if self.started_at is not None:
                raise ValueError("PENDING job snapshot must have started_at=None")
            if self.completed_at is not None:
                raise ValueError("PENDING job snapshot must have completed_at=None")

        elif self.status == JobStatus.RUNNING:
            if self.failure is not None:
                raise ValueError("RUNNING job snapshot must have failure=None")
            if self.started_at is None:
                raise ValueError("RUNNING job snapshot must have started_at set")
            if self.completed_at is not None:
                raise ValueError("RUNNING job snapshot must have completed_at=None")

        elif self.status == JobStatus.SUCCEEDED:
            if self.failure is not None:
                raise ValueError("SUCCEEDED job snapshot must have failure=None")
            if self.started_at is None:
                raise ValueError("SUCCEEDED job snapshot must have started_at set")
            if self.completed_at is None:
                raise ValueError("SUCCEEDED job snapshot must have completed_at set")

        elif self.status == JobStatus.FAILED:
            if self.failure is None:
                raise ValueError("FAILED job snapshot must have failure set")
            if self.completed_at is None:
                raise ValueError("FAILED job snapshot must have completed_at set")
            # started_at is optional for FAILED (pre-execution vs running failure)

        # 3. Timestamp ordering invariants
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must be greater than or equal to created_at")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at must be greater than or equal to created_at")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must be greater than or equal to started_at")

        return self


class JobRepository(SqlAlchemyRepository):
    """Repository managing PostgreSQL persistence and atomic transitions for asynchronous jobs.

    Invariants:
    - Never creates engine or session.
    - Never calls commit, rollback, or close.
    - Operates strictly within the caller-managed AsyncSession / UnitOfWork.
    - Does NOT generate JobId internally; receives caller-supplied JobIdentity.
    - Creation is unconditionally PENDING with server-authored created_at.
    - Retrieval is strictly tenant-scoped (tenant_id + job_id).
    - Status transitions execute atomic conditional UPDATE first (compare-and-transition).
    - Only on conditional miss does it perform a targeted SELECT to distinguish
      not-found from state-conflict.
    - Zero autonomous retries.
    - Zero update methods for identity fields (job_id, tenant_id, job_type, created_at).
    - Zero delete, purge, or cleanup methods.
    """

    @staticmethod
    def _row_to_snapshot(row: Any) -> JobSnapshot:
        """Map a database row or entity to an immutable JobSnapshot.

        Constructs validated canonical types from persisted column values.
        Fails loudly on invalid state.
        """
        raw_jid = row.job_id
        raw_tenant = row.tenant_id
        raw_type = row.job_type
        raw_status = row.status
        raw_fail_code = row.failure_code
        raw_fail_msg = row.failure_message
        raw_created_at = row.created_at
        raw_started_at = row.started_at
        raw_completed_at = row.completed_at

        jid = JobId(raw_jid)
        jtype = JobType(raw_type)
        identity = JobIdentity(
            job_id=jid,
            tenant_id=raw_tenant,
            job_type=jtype,
        )
        status = JobStatus(raw_status)

        failure: JobFailure | None = None
        if raw_fail_code is not None or raw_fail_msg is not None:
            if raw_fail_code is None or raw_fail_msg is None:
                raise ValueError(
                    f"Corrupt failure state in job '{jid}': both failure_code and "
                    f"failure_message must be present when failure is recorded"
                )
            failure = JobFailure(
                code=JobFailureCode(raw_fail_code),
                message=raw_fail_msg,
            )

        return JobSnapshot(
            identity=identity,
            status=status,
            failure=failure,
            created_at=raw_created_at,
            started_at=raw_started_at,
            completed_at=raw_completed_at,
        )

    async def create(
        self,
        *,
        identity: JobIdentity,
    ) -> JobSnapshot:
        """Persist a new asynchronous job in PENDING status.

        Invariants:
        - JobId is caller-supplied via identity; repository does not generate it.
        - Initial status is hardcoded PENDING; caller cannot supply an initial status.
        - created_at is server-authored via database DEFAULT now().
        - started_at and completed_at are NULL.
        - failure_code and failure_message are NULL.
        - Executes SQL INSERT ... RETURNING; does not commit.

        Args:
            identity: Validated, immutable canonical JobIdentity.

        Returns:
            JobSnapshot: Provider-neutral snapshot of the persisted PENDING job.

        Raises:
            TypeError: If identity is not a JobIdentity instance.
        """
        if not isinstance(identity, JobIdentity):
            raise TypeError(
                f"identity must be a JobIdentity instance, got '{type(identity).__name__}'"
            )

        stmt = (
            pg_insert(JobModel)
            .values(
                job_id=identity.job_id.value,
                tenant_id=identity.tenant_id,
                job_type=identity.job_type.value,
                status=JobStatus.PENDING.value,
                failure_code=None,
                failure_message=None,
                started_at=None,
                completed_at=None,
            )
            .returning(
                JobModel.job_id,
                JobModel.tenant_id,
                JobModel.job_type,
                JobModel.status,
                JobModel.failure_code,
                JobModel.failure_message,
                JobModel.created_at,
                JobModel.started_at,
                JobModel.completed_at,
            )
        )

        result = await self._session.execute(stmt)
        row = result.one()
        return self._row_to_snapshot(row)

    async def get(
        self,
        *,
        tenant_id: str,
        job_id: JobId,
    ) -> JobSnapshot | None:
        """Retrieve a job by tenant_id and job_id.

        Tenant isolation is explicit and mandatory: lookup requires both tenant_id
        and job_id. Cross-tenant or un-scoped lookup is not exposed.

        Args:
            tenant_id: Trusted canonical tenant identifier.
            job_id: Validated canonical JobId.

        Returns:
            JobSnapshot if the job exists for the specified tenant, None otherwise.

        Raises:
            TypeError: If job_id is not a JobId or tenant_id is not a str.
            ValueError: If tenant_id is empty or whitespace-only.
        """
        if not isinstance(job_id, JobId):
            raise TypeError(f"job_id must be a JobId instance, got '{type(job_id).__name__}'")
        if not isinstance(tenant_id, str):
            raise TypeError(f"tenant_id must be a str instance, got '{type(tenant_id).__name__}'")
        if not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty and non-whitespace")

        stmt = select(
            JobModel.job_id,
            JobModel.tenant_id,
            JobModel.job_type,
            JobModel.status,
            JobModel.failure_code,
            JobModel.failure_message,
            JobModel.created_at,
            JobModel.started_at,
            JobModel.completed_at,
        ).where(
            JobModel.job_id == job_id.value,
            JobModel.tenant_id == tenant_id,
        )

        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        return self._row_to_snapshot(row)

    async def transition(
        self,
        *,
        tenant_id: str,
        job_id: JobId,
        expected_status: JobStatus,
        target_status: JobStatus,
        failure: JobFailure | None = None,
    ) -> JobSnapshot:
        """Atomically transition a job status using compare-and-transition semantics.

        Algorithm:
        1. Validate lifecycle transition policy statically before any database I/O.
           Illegal transitions raise InvalidJobStatusTransitionError immediately.
        2. Validate failure argument consistency before SQL:
           - target_status == FAILED requires a non-None JobFailure.
           - target_status in (RUNNING, SUCCEEDED) requires failure is None.
        3. Execute a single atomic conditional UPDATE:
           UPDATE jobs SET ...
           WHERE job_id = :jid AND tenant_id = :tid AND status = :expected_status
           RETURNING ...
        4. If the conditional UPDATE returns a row, transition succeeded: return JobSnapshot.
        5. If the conditional UPDATE returned zero rows (conditional miss):
           Execute a targeted SELECT for status to distinguish:
           - Row missing -> raise JobNotFoundError.
           - Row present with actual_status != expected_status -> raise JobStatusConflictError.
           Zero automatic retry is attempted.

        Args:
            tenant_id: Trusted canonical tenant identifier.
            job_id: Validated canonical JobId.
            expected_status: Canonical status caller expects the job to currently have.
            target_status: Canonical status to transition to.
            failure: Sanitized JobFailure (required iff target_status == FAILED).

        Returns:
            JobSnapshot: Updated job snapshot with server-authored transition timestamps.

        Raises:
            InvalidJobStatusTransitionError: If the requested transition edge is forbidden.
            ValueError: If failure argument is inconsistent with target_status, or tenant_id blank.
            TypeError: If arguments are of invalid types.
            JobNotFoundError: If no job exists for tenant_id + job_id.
            JobStatusConflictError: If job exists but persisted status != expected_status.
        """
        # 1. Type validation
        if not isinstance(job_id, JobId):
            raise TypeError(f"job_id must be a JobId instance, got '{type(job_id).__name__}'")
        if not isinstance(tenant_id, str):
            raise TypeError(f"tenant_id must be a str instance, got '{type(tenant_id).__name__}'")
        if not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty and non-whitespace")
        if not isinstance(expected_status, JobStatus):
            raise TypeError(
                f"expected_status must be a JobStatus, got '{type(expected_status).__name__}'"
            )
        if not isinstance(target_status, JobStatus):
            raise TypeError(
                f"target_status must be a JobStatus, got '{type(target_status).__name__}'"
            )

        # 2. Static lifecycle validation BEFORE any database I/O
        validate_job_status_transition(expected_status, target_status)

        # 3. Failure argument rules BEFORE SQL
        if target_status == JobStatus.FAILED:
            if failure is None:
                raise ValueError("JobFailure must be provided when transitioning to FAILED status.")
            if not isinstance(failure, JobFailure):
                raise TypeError(
                    f"failure must be a JobFailure instance, got '{type(failure).__name__}'"
                )
        else:
            if failure is not None:
                raise ValueError(
                    f"JobFailure must be None when transitioning to '{target_status.value}' status."
                )

        # 4. Construct conditional UPDATE statement with server-authored timestamps
        update_values: dict[str, Any] = {
            "status": target_status.value,
        }
        if target_status == JobStatus.RUNNING:
            update_values["started_at"] = func.now()
        elif target_status == JobStatus.SUCCEEDED:
            update_values["completed_at"] = func.now()
        elif target_status == JobStatus.FAILED:
            update_values["completed_at"] = func.now()
            assert failure is not None
            update_values["failure_code"] = failure.code.value
            update_values["failure_message"] = failure.message

        update_stmt = (
            update(JobModel)
            .where(
                JobModel.job_id == job_id.value,
                JobModel.tenant_id == tenant_id,
                JobModel.status == expected_status.value,
            )
            .values(**update_values)
            .returning(
                JobModel.job_id,
                JobModel.tenant_id,
                JobModel.job_type,
                JobModel.status,
                JobModel.failure_code,
                JobModel.failure_message,
                JobModel.created_at,
                JobModel.started_at,
                JobModel.completed_at,
            )
        )

        # 5. Execute conditional UPDATE first
        result = await self._session.execute(update_stmt)
        row = result.one_or_none()

        if row is not None:
            return self._row_to_snapshot(row)

        # 6. Conditional miss: execute targeted SELECT to distinguish not-found from conflict
        select_status_stmt = select(JobModel.status).where(
            JobModel.job_id == job_id.value,
            JobModel.tenant_id == tenant_id,
        )
        status_result = await self._session.execute(select_status_stmt)
        actual_status_str = status_result.scalar_one_or_none()

        if actual_status_str is None:
            raise JobNotFoundError(tenant_id=tenant_id, job_id=job_id)

        actual_status = JobStatus(actual_status_str)
        raise JobStatusConflictError(
            job_id=job_id,
            expected_status=expected_status,
            actual_status=actual_status,
        )
