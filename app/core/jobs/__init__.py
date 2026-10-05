"""app.core.jobs package.

Provides provider-neutral asynchronous job contracts, identity, status, failure,
deterministic lifecycle transitions, PostgreSQL persistence model, and atomic
transition repository. Part of Phase 1 Foundation Task 1I.4A & Task 1I.4B.
"""

from app.core.jobs.contracts import (
    MAX_FAILURE_MESSAGE_LENGTH,
    MAX_JOB_FAILURE_CODE_LENGTH,
    MAX_JOB_TYPE_LENGTH,
    JobFailure,
    JobFailureCode,
    JobId,
    JobIdentity,
    JobType,
    generate_job_id,
    validate_failure_message,
    validate_job_failure_code,
    validate_job_id,
    validate_job_type,
)
from app.core.jobs.lifecycle import (
    ALLOWED_TRANSITIONS,
    InvalidJobStatusTransitionError,
    JobStatus,
    can_transition_job_status,
    validate_job_status_transition,
)
from app.core.jobs.models import JobModel
from app.core.jobs.repository import (
    POSTGRES_JOB_RUNTIME_TEST,
    JobNotFoundError,
    JobRepository,
    JobSnapshot,
    JobStatusConflictError,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "InvalidJobStatusTransitionError",
    "JobFailure",
    "JobFailureCode",
    "JobId",
    "JobIdentity",
    "JobModel",
    "JobNotFoundError",
    "JobRepository",
    "JobSnapshot",
    "JobStatus",
    "JobStatusConflictError",
    "JobType",
    "MAX_FAILURE_MESSAGE_LENGTH",
    "MAX_JOB_FAILURE_CODE_LENGTH",
    "MAX_JOB_TYPE_LENGTH",
    "POSTGRES_JOB_RUNTIME_TEST",
    "can_transition_job_status",
    "generate_job_id",
    "validate_failure_message",
    "validate_job_failure_code",
    "validate_job_id",
    "validate_job_status_transition",
    "validate_job_type",
]
