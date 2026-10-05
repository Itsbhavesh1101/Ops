"""HTTP API contracts and decoupled router factory for Generic Scheduling Core (Phase 4A).

Provides:
- POST /schedule-jobs: Asynchronous scheduling solve submission with idempotency protection.
- GET  /schedule-jobs/{job_id}: Asynchronous solve job polling and status inspection.
- GET  /resources/availability: Query availability and blackout windows for tenant resources.

Architectural & Security Invariants:
- SCHEDULING_API_PRODUCTION_MOUNT = "DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY"
- Unmounted: This router is NOT registered in app.main until trusted tenant auth exists.
- Injected dependencies: Router accepts trusted tenant context and service factories.
- Tenant isolation: Missing and cross-tenant entities return identical 404 responses.
- Idempotent submission: POST /schedule-jobs enforces canonical Phase-1 Idempotency-Key.
"""

import inspect
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import ApiError, ApiErrorResponse
from app.api.idempotency import IDEMPOTENCY_KEY_HEADER
from app.core.auth.models import TenantContext
from app.core.idempotency.contracts import (
    RequestFingerprint,
    create_idempotency_identity,
)
from app.core.idempotency.executor import IdempotencyConflictError
from app.core.idempotency.hashing import fingerprint_bytes
from app.core.idempotency.repository import (
    IdempotencyDecisionType,
    IdempotencyRepository,
)
from app.db.models.scheduling import ScheduleRecord
from app.resources.service import ResourceService
from app.scheduling.hashing import compute_input_snapshot_hash
from app.scheduling.policy import (
    PublishAuthorizationError,
    PublishPreconditionFailedError,
)
from app.scheduling.repair.models import ScheduleRepairRequest
from app.scheduling.schemas import SchedulingProblemDefinition, SchedulingRequest
from app.scheduling.service import (
    JobNotFoundError,
    ScheduleNotFoundError,
    ScheduleNotPublishedError,
    SchedulingService,
)

SCHEDULING_API_PRODUCTION_MOUNT: str = "DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY"


def _extract_tenant_id(tenant_ctx: Any) -> str:
    """Extract tenant_id string safely from TenantContext or string value."""
    if isinstance(tenant_ctx, TenantContext):
        return tenant_ctx.tenant_id
    if isinstance(tenant_ctx, str):
        return tenant_ctx
    raise ApiError(
        status_code=401,
        code="tenant.unresolved",
        message="Cannot resolve tenant context.",
    )


class ScheduleJobSubmitRequest(BaseModel):
    """Payload for submitting an asynchronous schedule job.

    Note: Client cannot supply tenant_id; tenant is derived from trusted context only.
    """

    model_config = ConfigDict(extra="forbid")

    schedule_key: str = Field(default="default", max_length=128)
    request: SchedulingProblemDefinition


class ScheduleJobResponse(BaseModel):
    """Public representation of an asynchronous schedule solve job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: UUID
    schedule_key: str
    status: str
    input_snapshot_hash: str
    solver_metadata: dict[str, Any]
    diagnostics: list[dict[str, Any]]
    failure_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class SchedulePublishRequest(BaseModel):
    """Optional payload for publishing a candidate schedule."""

    model_config = ConfigDict(extra="forbid")

    principal: str = Field(default="authorized_user", max_length=255)


class SchedulePublishResponse(BaseModel):
    """Public representation of a published schedule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule_id: UUID
    version: int
    status: str
    published_at: datetime
    published_by: str
    supersedes_id: UUID | None = None
    assignment_count: int


class ScheduleRepairResponse(BaseModel):
    """Public representation of an incremental repair solve outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: UUID
    baseline_schedule_id: UUID
    candidate_schedule_id: UUID | None = None
    candidate_version: int | None = None
    status: str
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class ResourceAvailabilityItem(BaseModel):
    """Public representation of a resource availability or blackout window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_id: UUID
    interval_type: str
    start_at: datetime
    end_at: datetime
    reason: str | None = None



def create_scheduling_router(
    tenant_dependency: Callable[..., Any],
    session_dependency: Callable[..., AsyncSession],
    scheduling_service_dependency: Callable[..., SchedulingService],
    resource_service_dependency: Callable[..., ResourceService] | None = None,
    publish_policy_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    """Factory creating decoupled scheduling API router with injected dependencies."""
    parent_router = APIRouter(tags=["Scheduling"])
    router = APIRouter()

    @router.post(
        "/schedule-jobs",
        response_model=ScheduleJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Submit Asynchronous Schedule Solve Job",
        responses={
            202: {"model": ScheduleJobResponse, "description": "Schedule job submitted."},
            400: {"model": ApiErrorResponse, "description": "Invalid input snapshot."},
            409: {"model": ApiErrorResponse, "description": "Idempotency conflict."},
        },
    )
    async def submit_schedule_job(
        payload: ScheduleJobSubmitRequest = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
        service: SchedulingService = Depends(scheduling_service_dependency),
        idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_KEY_HEADER),
    ) -> ScheduleJobResponse:
        tenant_id = _extract_tenant_id(tenant_context)

        # Construct full SchedulingRequest injecting trusted tenant_id
        full_request = SchedulingRequest(
            tenant_id=tenant_id,
            **payload.request.model_dump(),
        )

        # Handle idempotency if key is provided
        if idempotency_key:
            identity = create_idempotency_identity(
                tenant_id=tenant_id,
                operation_name="schedule.submit",
                raw_key=idempotency_key,
            )
            # Fingerprint based on canonical input snapshot hash
            input_hash = compute_input_snapshot_hash(full_request)
            fingerprint = RequestFingerprint(input_hash)

            idempotency_repo = IdempotencyRepository(session)
            decision = await idempotency_repo.acquire_or_observe(
                identity=identity,
                request_fingerprint=fingerprint,
            )

            if decision.decision == IdempotencyDecisionType.EXISTING_CONFLICT:
                assert decision.existing_fingerprint is not None
                raise IdempotencyConflictError(
                    identity=identity,
                    request_fingerprint=fingerprint,
                    existing_fingerprint=decision.existing_fingerprint,
                )
            elif decision.decision == IdempotencyDecisionType.EXISTING_MATCH:
                # Same key + same fingerprint = replay existing job
                existing_job = await service.get_job_by_snapshot_hash(
                    session=session,
                    tenant_id=tenant_id,
                    snapshot_hash=input_hash,
                )
                if existing_job:
                    return ScheduleJobResponse(
                        job_id=existing_job.id,
                        schedule_key=existing_job.schedule_key,
                        status=existing_job.status,
                        input_snapshot_hash=existing_job.input_snapshot_hash,
                        solver_metadata=existing_job.solver_metadata,
                        diagnostics=existing_job.diagnostics,
                        failure_message=existing_job.failure_message,
                        created_at=existing_job.created_at,
                        completed_at=existing_job.completed_at,
                    )

        job = await service.submit_job(
            session=session,
            tenant_id=tenant_id,
            request=full_request,
            schedule_key=payload.schedule_key,
        )

        return ScheduleJobResponse(
            job_id=job.id,
            schedule_key=job.schedule_key,
            status=job.status,
            input_snapshot_hash=job.input_snapshot_hash,
            solver_metadata=job.solver_metadata,
            diagnostics=job.diagnostics,
            failure_message=job.failure_message,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    @router.get(
        "/schedule-jobs/{job_id}",
        response_model=ScheduleJobResponse,
        summary="Get Asynchronous Schedule Job Status",
        responses={
            200: {"model": ScheduleJobResponse, "description": "Schedule job state."},
            404: {"model": ApiErrorResponse, "description": "Schedule job not found."},
        },
    )
    async def get_schedule_job(
        job_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
        service: SchedulingService = Depends(scheduling_service_dependency),
    ) -> ScheduleJobResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        job = await service.get_job(session=session, tenant_id=tenant_id, job_id=job_id)
        if not job:
            raise ApiError(
                status_code=404,
                code="schedule_job.not_found",
                message=f"Schedule job {job_id} not found.",
            )

        return ScheduleJobResponse(
            job_id=job.id,
            schedule_key=job.schedule_key,
            status=job.status,
            input_snapshot_hash=job.input_snapshot_hash,
            solver_metadata=job.solver_metadata,
            diagnostics=job.diagnostics,
            failure_message=job.failure_message,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    def _default_resource_service() -> ResourceService | None:
        return None

    @router.get(
        "/resources/availability",
        response_model=list[ResourceAvailabilityItem],
        status_code=status.HTTP_200_OK,
        summary="Query Resource Availability Intervals",
        responses={
            200: {
                "model": list[ResourceAvailabilityItem],
                "description": "Availability intervals for tenant resources.",
            },
        },
    )
    async def get_resource_availabilities(
        tenant_context: Any = Depends(tenant_dependency),
        resource_id: UUID | None = Query(None),
        resource_service: ResourceService | None = Depends(
            resource_service_dependency or _default_resource_service
        ),
    ) -> list[ResourceAvailabilityItem]:
        tenant_id = _extract_tenant_id(tenant_context)
        if not resource_service:
            return []

        res = resource_service.get_intervals(
            tenant_id=tenant_id,
            resource_id=resource_id,
        )
        if inspect.isawaitable(res):
            intervals = await res
        else:
            intervals = res

        return [
            ResourceAvailabilityItem(
                resource_id=item.resource_id,
                interval_type=item.interval_type.value,
                start_at=item.start_at,
                end_at=item.end_at,
                reason=item.reason,
            )
            for item in intervals
        ]

    @router.post(
        "/schedule-jobs/{job_id}/publish",
        response_model=SchedulePublishResponse,
        status_code=status.HTTP_200_OK,
        summary="Authorize and Publish Candidate Schedule from Solve Job",
        responses={
            200: {"model": SchedulePublishResponse, "description": "Schedule published."},
            400: {"model": ApiErrorResponse, "description": "Preconditions failed."},
            403: {"model": ApiErrorResponse, "description": "Authorization denied."},
            404: {"model": ApiErrorResponse, "description": "Job or schedule candidate not found."},
            409: {"model": ApiErrorResponse, "description": "Idempotency conflict."},
        },
    )
    async def publish_schedule_from_job(
        job_id: UUID = Path(...),
        payload: SchedulePublishRequest = Body(default_factory=SchedulePublishRequest),
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
        service: SchedulingService = Depends(scheduling_service_dependency),
        idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_KEY_HEADER),
    ) -> SchedulePublishResponse:
        tenant_id = _extract_tenant_id(tenant_context)

        if idempotency_key:
            identity = create_idempotency_identity(
                tenant_id=tenant_id,
                operation_name="schedule.publish",
                raw_key=idempotency_key,
            )
            fingerprint = fingerprint_bytes(f"{job_id}:{payload.principal}".encode())
            idempotency_repo = IdempotencyRepository(session)
            decision = await idempotency_repo.acquire_or_observe(
                identity=identity,
                request_fingerprint=fingerprint,
            )
            if decision.decision == IdempotencyDecisionType.EXISTING_CONFLICT:
                assert decision.existing_fingerprint is not None
                raise IdempotencyConflictError(
                    identity=identity,
                    request_fingerprint=fingerprint,
                    existing_fingerprint=decision.existing_fingerprint,
                )
            elif decision.decision == IdempotencyDecisionType.EXISTING_MATCH:
                # Replay existing published schedule
                sched = (
                    await session.execute(
                        select(ScheduleRecord)
                        .options(selectinload(ScheduleRecord.assignments))
                        .where(
                            ScheduleRecord.tenant_id == tenant_id,
                            ScheduleRecord.source_job_id == job_id,
                            ScheduleRecord.status == "PUBLISHED",
                        )
                    )
                ).scalar_one_or_none()
                if sched and sched.published_at and sched.published_by:
                    return SchedulePublishResponse(
                        schedule_id=sched.id,
                        version=sched.version,
                        status=sched.status,
                        published_at=sched.published_at,
                        published_by=sched.published_by,
                        supersedes_id=sched.supersedes_id,
                        assignment_count=len(sched.assignments),
                    )

        try:
            injected_policy = None
            if publish_policy_dependency:
                pol_res = publish_policy_dependency()
                if inspect.isawaitable(pol_res):
                    injected_policy = await pol_res
                else:
                    injected_policy = pol_res

            sched = await service.publish_job(
                session=session,
                tenant_id=tenant_id,
                job_id=job_id,
                principal=payload.principal,
                publish_policy=injected_policy,
            )
        except JobNotFoundError as err:
            raise ApiError(
                status_code=404,
                code="schedule_job.not_found",
                message=str(err),
            ) from err
        except ScheduleNotFoundError as err:
            raise ApiError(
                status_code=404,
                code="schedule.not_found",
                message=str(err),
            ) from err
        except PublishAuthorizationError as err:
            raise ApiError(
                status_code=403,
                code="schedule_publish.unauthorized",
                message=str(err),
            ) from err
        except PublishPreconditionFailedError as err:
            raise ApiError(
                status_code=400,
                code="schedule_publish.preconditions_failed",
                message=str(err),
            ) from err

        assert sched.published_at is not None
        assert sched.published_by is not None

        return SchedulePublishResponse(
            schedule_id=sched.id,
            version=sched.version,
            status=sched.status,
            published_at=sched.published_at,
            published_by=sched.published_by,
            supersedes_id=sched.supersedes_id,
            assignment_count=len(sched.assignments),
        )

    @router.post(
        "/schedules/{schedule_id}/repair",
        response_model=ScheduleRepairResponse,
        status_code=status.HTTP_200_OK,
        summary="Execute Targeted Incremental Schedule Repair",
        responses={
            200: {"model": ScheduleRepairResponse, "description": "Repair solve completed."},
            400: {"model": ApiErrorResponse, "description": "Invalid repair request or baseline."},
            404: {"model": ApiErrorResponse, "description": "Baseline schedule not found."},
            409: {"model": ApiErrorResponse, "description": "Idempotency conflict."},
        },
    )
    async def repair_schedule_endpoint(
        schedule_id: UUID = Path(...),
        payload: ScheduleRepairRequest = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
        service: SchedulingService = Depends(scheduling_service_dependency),
        idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_KEY_HEADER),
    ) -> ScheduleRepairResponse:
        tenant_id = _extract_tenant_id(tenant_context)

        if idempotency_key:
            identity = create_idempotency_identity(
                tenant_id=tenant_id,
                operation_name="schedule.repair",
                raw_key=idempotency_key,
            )
            fingerprint = fingerprint_bytes(
                f"{schedule_id}:{payload.model_dump_json()}".encode()
            )
            idempotency_repo = IdempotencyRepository(session)
            decision = await idempotency_repo.acquire_or_observe(
                identity=identity,
                request_fingerprint=fingerprint,
            )
            if decision.decision == IdempotencyDecisionType.EXISTING_CONFLICT:
                assert decision.existing_fingerprint is not None
                raise IdempotencyConflictError(
                    identity=identity,
                    request_fingerprint=fingerprint,
                    existing_fingerprint=decision.existing_fingerprint,
                )
            elif decision.decision == IdempotencyDecisionType.EXISTING_MATCH:
                # Find matching repair candidate if already generated
                cand_stmt = (
                    select(ScheduleRecord)
                    .where(
                        ScheduleRecord.tenant_id == tenant_id,
                        ScheduleRecord.supersedes_id == schedule_id,
                    )
                    .order_by(ScheduleRecord.version.desc())
                    .limit(1)
                )
                cand = (await session.execute(cand_stmt)).scalar_one_or_none()
                if cand:
                    return ScheduleRepairResponse(
                        job_id=cand.source_job_id,
                        baseline_schedule_id=schedule_id,
                        candidate_schedule_id=cand.id,
                        candidate_version=cand.version,
                        status=cand.status,
                        diagnostics=[],
                    )

        try:
            job, candidate = await service.repair_schedule(
                session=session,
                tenant_id=tenant_id,
                schedule_id=schedule_id,
                request=payload,
            )
        except ScheduleNotFoundError as err:
            raise ApiError(
                status_code=404,
                code="schedule.not_found",
                message=str(err),
            ) from err
        except ScheduleNotPublishedError as err:
            raise ApiError(
                status_code=400,
                code="schedule.not_published",
                message=str(err),
            ) from err
        except ValueError as err:
            raise ApiError(
                status_code=400,
                code="schedule_repair.invalid_request",
                message=str(err),
            ) from err

        return ScheduleRepairResponse(
            job_id=job.id,
            baseline_schedule_id=schedule_id,
            candidate_schedule_id=candidate.id if candidate else None,
            candidate_version=candidate.version if candidate else None,
            status=job.status,
            diagnostics=job.diagnostics,
        )

    parent_router.include_router(router)
    return parent_router

