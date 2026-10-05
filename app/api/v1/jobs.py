"""HTTP read/polling API contract and safe router factory for asynchronous jobs.

Part of Phase 1 Foundation Task 1I.4C.
Provides:
- JobReadResponse: immutable public representation of an asynchronous job.
- JobFailureResponse: immutable public representation of sanitized failure metadata.
- JobSnapshotReader: provider-neutral read protocol for tenant-scoped job snapshots.
- job_snapshot_to_response: pure mapper from JobSnapshot to JobReadResponse.
- create_job_read_router: safe router factory requiring injected trusted tenant and reader.

Architectural & Security Invariants:
- Unmounted: This router is NOT registered in app.main or app.api.v1.router in Task 1I.4C.
  Production mounting is deferred pending the trusted HTTP tenant/database perimeter.
- Read-only: Exposes strictly GET /{job_id} relative to the router prefix. Zero mutations,
  status transitions, cancellations, or worker execution endpoints.
- Strict tenant boundary: Router accepts only an explicitly injected trusted tenant dependency.
  Never derives tenant identity from unverified client inputs (headers, query, path, or body).
- Tenant-scoped retrieval: Missing jobs and cross-tenant job lookups return identical safe
  HTTP 404 responses with code 'job.not_found', preventing existence disclosure.
- Tenant privacy: Public response models strictly omit tenant_id, principal_id, and user_id.
- Privacy & Safety: Response models omit speculative fields (result, progress, retry, cancel).
- Polling semantics: Status is resource state (all valid statuses return HTTP 200). Never
  returns HTTP 202 on GET polling. Applies 'Cache-Control: no-store' on successful responses.
- Architectural isolation: Zero SQLAlchemy, session, engine, or broker imports.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Protocol, runtime_checkable

from fastapi import APIRouter, Depends, Path, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, field_serializer, field_validator

from app.api.errors import ApiError, ApiErrorResponse
from app.core.auth.models import TenantContext
from app.core.jobs import (
    JobFailureCode,
    JobId,
    JobSnapshot,
    JobStatus,
    JobType,
    validate_failure_message,
    validate_job_failure_code,
    validate_job_id,
    validate_job_type,
)


class JobFailureResponse(BaseModel):
    """Immutable public representation of sanitized asynchronous job failure metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str

    @field_validator("code", mode="before")
    @classmethod
    def _validate_code(cls, v: Any) -> str:
        if isinstance(v, JobFailureCode):
            return v.value
        if isinstance(v, str):
            return validate_job_failure_code(v)
        raise TypeError(f"code must be a JobFailureCode or str, got '{type(v).__name__}'")

    @field_validator("message")
    @classmethod
    def _validate_message(cls, v: Any) -> str:
        return validate_failure_message(v)


class JobReadResponse(BaseModel):
    """Immutable public representation of an asynchronous job polling response.

    Exposes strictly lifecycle state, server timestamps, and sanitized failure metadata.
    Omits tenant identity, principal identity, result payloads, progress, and retry details.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    job_type: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure: JobFailureResponse | None = None

    @field_validator("job_id", mode="before")
    @classmethod
    def _validate_job_id(cls, v: Any) -> str:
        if isinstance(v, JobId):
            return str(v)
        if isinstance(v, uuid.UUID):
            return str(v).lower()
        if isinstance(v, str):
            return str(validate_job_id(v)).lower()
        raise TypeError(f"job_id must be a JobId, UUID, or str, got '{type(v).__name__}'")

    @field_validator("job_type", mode="before")
    @classmethod
    def _validate_job_type(cls, v: Any) -> str:
        if isinstance(v, JobType):
            return str(v)
        if isinstance(v, str):
            return validate_job_type(v)
        raise TypeError(f"job_type must be a JobType or str, got '{type(v).__name__}'")

    @field_validator("status", mode="before")
    @classmethod
    def _validate_status(cls, v: Any) -> JobStatus:
        if isinstance(v, JobStatus):
            return v
        if isinstance(v, str):
            return JobStatus(v)
        raise TypeError(f"status must be a JobStatus or str, got '{type(v).__name__}'")

    @field_serializer("status")
    def _serialize_status(self, v: JobStatus) -> str:
        return v.value


def job_snapshot_to_response(snapshot: JobSnapshot) -> JobReadResponse:
    """Pure, deterministic mapper from domain JobSnapshot to public JobReadResponse.

    Performs:
    - Zero database work
    - Zero HTTP request inspection
    - Zero authorization logic
    - Zero logging
    - Zero network I/O
    - Zero tenant_id exposure
    """
    failure_resp: JobFailureResponse | None = None
    if snapshot.failure is not None:
        failure_resp = JobFailureResponse(
            code=snapshot.failure.code.value,
            message=snapshot.failure.message,
        )

    return JobReadResponse(
        job_id=str(snapshot.identity.job_id),
        job_type=str(snapshot.identity.job_type),
        status=snapshot.status,
        created_at=snapshot.created_at,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        failure=failure_resp,
    )


@runtime_checkable
class JobSnapshotReader(Protocol):
    """Provider-neutral port for tenant-scoped asynchronous job snapshot retrieval."""

    async def get(
        self,
        *,
        tenant_id: str,
        job_id: JobId,
    ) -> JobSnapshot | None:
        """Retrieve a job snapshot strictly scoped by tenant_id and job_id."""
        ...


def _extract_tenant_id(context: Any) -> str:
    """Extract canonical tenant_id from trusted context or string.

    Rejects unverified, non-string, or whitespace-only inputs.
    """
    if isinstance(context, TenantContext):
        return context.tenant_id
    if hasattr(context, "tenant_id") and isinstance(context.tenant_id, str):
        stripped = context.tenant_id.strip()
        if stripped:
            return stripped
    if isinstance(context, str):
        stripped = context.strip()
        if stripped:
            return stripped
    raise TypeError(
        f"Trusted tenant dependency must resolve to TenantContext or non-empty str, "
        f"got '{type(context).__name__}'"
    )


def _validate_uuid4_path(v: uuid.UUID) -> uuid.UUID:
    """Ensure path-supplied UUID is strictly a non-nil UUIDv4."""
    if v.int == 0:
        raise ValueError("JobId must not be a nil UUID")
    if v.version != 4:
        raise ValueError(f"JobId must be a UUID version 4, got version {v.version}")
    return v


ValidatedJobIdPath = Annotated[
    uuid.UUID,
    Path(
        description="Canonical UUIDv4 identifier of the asynchronous job.",
        examples=["b7906eb2-278a-4f42-9330-e72423f1ff5f"],
    ),
    AfterValidator(_validate_uuid4_path),
]


def create_job_read_router(
    *,
    tenant_dependency: Callable[..., Any],
    reader_dependency: Callable[..., Any],
) -> APIRouter:
    """Construct an isolated, safe APIRouter for asynchronous job read and polling.

    Invariants:
    - Relative prefix is '/jobs' (composes under '/api/v1' to yield '/api/v1/jobs/{job_id}').
    - Requires explicitly injected trusted tenant dependency.
    - Requires explicitly injected JobSnapshotReader dependency.
    - Strictly read-only GET /{job_id} endpoint (zero mutations, transitions, or cancels).
    - Protocol-level 'Cache-Control: no-store' header on successful 200 responses.
    - Missing or cross-tenant lookup uniformly yields safe HTTP 404 ('job.not_found').
    - Unmounted: Intentionally excluded from production application registration in Task 1I.4C.
    """
    router = APIRouter(prefix="/jobs", tags=["Jobs"])

    @router.get(
        "/{job_id}",
        response_model=JobReadResponse,
        summary="Get Asynchronous Job",
        description="Retrieve the current status and execution metadata of an asynchronous job.",
        responses={
            200: {
                "model": JobReadResponse,
                "description": "Job status and metadata successfully retrieved.",
            },
            404: {
                "model": ApiErrorResponse,
                "description": "Job not found or access denied.",
            },
        },
    )
    async def get_job(
        job_id: ValidatedJobIdPath,
        response: Response,
        tenant_context: Any = Depends(tenant_dependency),
        reader: JobSnapshotReader = Depends(reader_dependency),
    ) -> JobReadResponse:
        trusted_tenant_id = _extract_tenant_id(tenant_context)
        canonical_job_id = JobId(job_id)

        snapshot = await reader.get(
            tenant_id=trusted_tenant_id,
            job_id=canonical_job_id,
        )

        if snapshot is None:
            raise ApiError(
                status_code=404,
                code="job.not_found",
                message="Job not found.",
            )

        response.headers["Cache-Control"] = "no-store"
        return job_snapshot_to_response(snapshot)

    return router
