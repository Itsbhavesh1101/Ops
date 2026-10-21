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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, Header, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import ApiError, ApiErrorResponse
from app.api.idempotency import IDEMPOTENCY_KEY_HEADER
from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.auth.models import AuthenticatedPrincipal, TenantContext
from app.core.capacity.quotas import TenantQuotaManager
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
from app.db.models.scheduling import (
    ResourceAvailabilityRecord,
    ResourceRecord,
    ScheduleRecord,
)
from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, create_event_envelope
from app.resources.schemas import AvailabilityIntervalCreate, ResourceCreate
from app.resources.service import ResourceNotFoundError, ResourceService
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


class ResourceCatalogItem(BaseModel):
    """Public representation of a resource in the tenant catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    resource_key: str
    type: str
    display_label: str
    capacity: int
    skills: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    active: bool
    created_at: datetime
    updated_at: datetime


class ScheduleListItem(BaseModel):
    """Public summary representation of a schedule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    schedule_key: str
    version: int
    status: str
    source_job_id: UUID
    input_snapshot_hash: str
    solver_profile_version: int | None = None
    supersedes_id: UUID | None = None
    published_at: datetime | None = None
    published_by: str | None = None
    repair_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ScheduleAssignmentItem(BaseModel):
    """Public representation of an activity assignment in a schedule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    schedule_id: UUID
    activity_id: str
    resource_id: str
    start_at: datetime
    end_at: datetime
    is_locked: bool
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ScheduleDetailResponse(BaseModel):
    """Full detail of a schedule with timetable assignments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    schedule_key: str
    version: int
    status: str
    source_job_id: UUID
    input_snapshot_hash: str
    solver_profile_version: int | None = None
    solver_metadata: dict[str, Any] = Field(default_factory=dict)
    score_components: dict[str, Any] = Field(default_factory=dict)
    supersedes_id: UUID | None = None
    published_at: datetime | None = None
    published_by: str | None = None
    repair_reason: str | None = None
    repair_scope: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    assignments: list[ScheduleAssignmentItem] = Field(default_factory=list)


def _null_principal_dependency() -> Any:
    return None


def _null_resource_service_dependency() -> Any:
    return None


def create_scheduling_router(
    tenant_dependency: Callable[..., Any],
    session_dependency: Callable[..., AsyncSession],
    scheduling_service_dependency: Callable[..., SchedulingService],
    resource_service_dependency: Callable[..., ResourceService] | None = None,
    publish_policy_dependency: Callable[..., Any] | None = None,
    principal_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    """Factory creating decoupled scheduling API router with injected dependencies."""
    parent_router = APIRouter(tags=["Scheduling"])
    router = APIRouter()
    _principal_dep = principal_dependency or _null_principal_dependency
    _res_dep = resource_service_dependency or _null_resource_service_dependency
    _pub_policy_dep = publish_policy_dependency or (lambda: None)

    @router.post(
        "/schedule-jobs",
        response_model=ScheduleJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Submit Asynchronous Schedule Solve Job",
        responses={
            202: {"model": ScheduleJobResponse, "description": "Schedule job submitted."},
            400: {"model": ApiErrorResponse, "description": "Invalid input snapshot."},
            403: {"model": ApiErrorResponse, "description": "Unauthorized to submit solve jobs."},
            409: {"model": ApiErrorResponse, "description": "Idempotency conflict."},
        },
    )
    async def submit_schedule_job(
        payload: ScheduleJobSubmitRequest = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal_ctx: Any = Depends(_principal_dep),
        session: AsyncSession = Depends(session_dependency),
        service: SchedulingService = Depends(scheduling_service_dependency),
        idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_KEY_HEADER),
    ) -> ScheduleJobResponse:
        if isinstance(principal_ctx, AuthenticatedPrincipal):
            if principal_ctx.has_any_role("auditor") and not (
                principal_ctx.has_any_role("scheduler", "operator", "tenant_admin")
                or principal_ctx.has_permission("schedule:solve")
            ):
                raise ApiError(
                    status_code=403,
                    code="schedule.unauthorized",
                    message="Auditor role cannot submit schedule solve jobs.",
                )
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

        quota_mgr = TenantQuotaManager()
        await quota_mgr.check_solve_submission_quota(session, tenant_id)

        job = await service.submit_job(
            session=session,
            tenant_id=tenant_id,
            request=full_request,
            schedule_key=payload.schedule_key,
        )
        if session is not None:
            await session.commit()

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

    @router.get(
        "/resources",
        response_model=list[ResourceCatalogItem],
        status_code=status.HTTP_200_OK,
        summary="List Resource Catalog for Tenant",
        responses={
            200: {
                "model": list[ResourceCatalogItem],
                "description": "Tenant resource catalog entries.",
            },
        },
    )
    async def list_resources(
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
        resource_service: Any = Depends(_res_dep),
        type: str | None = Query(default=None),
        active_only: bool = Query(default=True),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[ResourceCatalogItem]:
        tenant_id = _extract_tenant_id(tenant_context)
        stmt = select(ResourceRecord).where(ResourceRecord.tenant_id == tenant_id)
        if type:
            stmt = stmt.where(ResourceRecord.type == type)
        if active_only:
            stmt = stmt.where(ResourceRecord.active.is_(True))
        stmt = stmt.order_by(ResourceRecord.resource_key.asc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        records = list(result.scalars().all())

        return [
            ResourceCatalogItem(
                id=r.id,
                resource_key=r.resource_key,
                type=r.type,
                display_label=r.display_label,
                capacity=r.capacity,
                skills=r.skills,
                attributes=r.attributes,
                active=r.active,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in records
        ]

    @router.post(
        "/resources",
        response_model=ResourceCatalogItem,
        status_code=status.HTTP_201_CREATED,
        summary="Create Local Operator-Managed Resource",
        responses={
            201: {"model": ResourceCatalogItem, "description": "Resource created."},
            400: {
                "model": ApiErrorResponse,
                "description": "Validation error or invalid resource type.",
            },
            403: {"model": ApiErrorResponse, "description": "Unauthorized to create resources."},
            409: {
                "model": ApiErrorResponse,
                "description": "Duplicate resource key or idempotency conflict.",
            },
        },
    )
    async def create_resource(
        payload: ResourceCreate = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal_ctx: Any = Depends(_principal_dep),
        session: AsyncSession = Depends(session_dependency),
        idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_KEY_HEADER),
    ) -> ResourceCatalogItem:
        if isinstance(principal_ctx, AuthenticatedPrincipal):
            if principal_ctx.has_role("auditor") and not (
                principal_ctx.has_any_role(
                    "operator", "planner", "scheduler", "tenant_admin", "admin"
                )
                or principal_ctx.has_any_permission(
                    "resource:create", "resource:write", "write", "*"
                )
            ):
                raise ApiError(
                    status_code=403,
                    code="resource.unauthorized",
                    message="Auditors cannot create operational resources.",
                )
            if not (
                principal_ctx.has_any_role(
                    "operator", "planner", "scheduler", "tenant_admin", "admin"
                )
                or principal_ctx.has_any_permission(
                    "resource:create", "resource:write", "write", "*"
                )
            ):
                raise ApiError(
                    status_code=403,
                    code="resource.unauthorized",
                    message="Principal is not authorized to create operational resources.",
                )

        tenant_id = _extract_tenant_id(tenant_context)
        res_type = payload.type.value if hasattr(payload.type, "value") else str(payload.type)

        if res_type == "PERSON":
            raise ApiError(
                status_code=400,
                code="resource.invalid_type",
                message=(
                    "Local operator endpoint cannot create PERSON resources; "
                    "workforce must originate from authoritative ERP integration."
                ),
            )
        if res_type not in ("ROOM", "EQUIPMENT", "OTHER"):
            raise ApiError(
                status_code=400,
                code="resource.invalid_type",
                message=(
                    f"Unsupported resource type '{res_type}'. "
                    "Allowed types: ROOM, EQUIPMENT, OTHER."
                ),
            )
        if res_type == "ROOM" and payload.capacity < 1:
            raise ApiError(
                status_code=400,
                code="resource.invalid_capacity",
                message="Room capacity must be greater than zero.",
            )
        if not payload.display_label or not payload.display_label.strip():
            raise ApiError(
                status_code=400,
                code="resource.invalid_name",
                message="Resource display label must not be empty.",
            )

        if idempotency_key:
            identity = create_idempotency_identity(
                tenant_id=tenant_id,
                operation_name="resource.create",
                raw_key=idempotency_key,
            )
            fingerprint = fingerprint_bytes(
                f"{payload.resource_key}:{payload.display_label}:{res_type}".encode()
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
                existing_res = (
                    await session.execute(
                        select(ResourceRecord).where(
                            ResourceRecord.tenant_id == tenant_id,
                            ResourceRecord.resource_key == payload.resource_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing_res:
                    return ResourceCatalogItem(
                        id=existing_res.id,
                        resource_key=existing_res.resource_key,
                        type=existing_res.type,
                        display_label=existing_res.display_label,
                        capacity=existing_res.capacity,
                        skills=existing_res.skills,
                        attributes=existing_res.attributes,
                        active=existing_res.active,
                        created_at=existing_res.created_at,
                        updated_at=existing_res.updated_at,
                    )

        existing = (
            await session.execute(
                select(ResourceRecord).where(
                    ResourceRecord.tenant_id == tenant_id,
                    ResourceRecord.resource_key == payload.resource_key,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ApiError(
                status_code=409,
                code="resource.duplicate_key",
                message=(
                    f"Resource with key '{payload.resource_key}' "
                    f"already exists in tenant '{tenant_id}'."
                ),
            )

        now = datetime.now(UTC)
        actor_id = (
            principal_ctx.principal_id
            if isinstance(principal_ctx, AuthenticatedPrincipal)
            else "operator"
        )

        attrs = dict(payload.attributes or {})
        attrs["provenance"] = "USER_CREATED"
        attrs["source"] = "LOCAL"
        attrs["created_by"] = actor_id

        record = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            resource_key=payload.resource_key,
            type=res_type,
            display_label=payload.display_label.strip(),
            capacity=payload.capacity,
            skills=list(payload.skills),
            attributes=attrs,
            active=payload.active,
            created_at=now,
            updated_at=now,
        )
        session.add(record)

        outbox_repo = OutboxRepository(session)
        envelope = create_event_envelope(
            EventCreate(
                event_type="resource.created",
                event_version=1,
                tenant_id=tenant_id,
                correlation_id=None,
                subject_type="resource",
                subject_id=str(record.id),
                payload={
                    "resource_id": str(record.id),
                    "resource_key": record.resource_key,
                    "type": record.type,
                    "display_label": record.display_label,
                    "capacity": record.capacity,
                    "provenance": "USER_CREATED",
                },
            )
        )
        await outbox_repo.enqueue(envelope)

        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_principal_id=actor_id,
                actor_principal_type="HUMAN",
                authentication_method="OIDC",
                event_type="resource.created",
                resource_type="resource",
                resource_id=str(record.id),
                outcome=AuditEventOutcome.SUCCESS,
                details={
                    "resource_key": record.resource_key,
                    "type": record.type,
                    "display_label": record.display_label,
                    "capacity": record.capacity,
                    "provenance": "USER_CREATED",
                },
            )
        )

        await session.flush()

        return ResourceCatalogItem(
            id=record.id,
            resource_key=record.resource_key,
            type=record.type,
            display_label=record.display_label,
            capacity=record.capacity,
            skills=record.skills,
            attributes=record.attributes,
            active=record.active,
            created_at=record.created_at,
            updated_at=record.updated_at,
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
        session: AsyncSession = Depends(session_dependency),
        resource_id: UUID | None = Query(None),
        resource_service: ResourceService | None = Depends(
            resource_service_dependency or _default_resource_service
        ),
    ) -> list[ResourceAvailabilityItem]:
        tenant_id = _extract_tenant_id(tenant_context)

        # 1. First query canonical PostgreSQL ResourceAvailabilityRecord
        records = []
        try:
            stmt = select(ResourceAvailabilityRecord).where(
                ResourceAvailabilityRecord.tenant_id == tenant_id
            )
            if resource_id:
                stmt = stmt.where(ResourceAvailabilityRecord.resource_id == resource_id)
            stmt = stmt.order_by(ResourceAvailabilityRecord.start_at.asc())
            result = await session.execute(stmt)
            scalars_fn = getattr(result, "scalars", None)
            if callable(scalars_fn):
                sc = scalars_fn()
                if hasattr(sc, "all") and callable(sc.all):
                    res_all = sc.all()
                    if inspect.isawaitable(res_all):
                        res_all = await res_all
                    if isinstance(res_all, (list, tuple)):
                        records = [r for r in res_all if isinstance(r, ResourceAvailabilityRecord)]
        except Exception:
            records = []

        if records:
            return [
                ResourceAvailabilityItem(
                    resource_id=r.resource_id,
                    interval_type=r.interval_type,
                    start_at=r.start_at,
                    end_at=r.end_at,
                    reason=r.reason,
                )
                for r in records
            ]

        # 2. If resource_service is provided and has live intervals
        if resource_service:
            try:
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
                        interval_type=(
                            item.interval_type.value
                            if hasattr(item.interval_type, "value")
                            else str(item.interval_type)
                        ),
                        start_at=item.start_at,
                        end_at=item.end_at,
                        reason=item.reason,
                    )
                    for item in intervals
                ]
            except ResourceNotFoundError:
                return []

        return []

    @router.post(
        "/resources/availability",
        response_model=ResourceAvailabilityItem,
        status_code=status.HTTP_201_CREATED,
        summary="Record Resource Availability or Blackout Interval",
        responses={
            201: {"model": ResourceAvailabilityItem, "description": "Interval recorded."},
            400: {"model": ApiErrorResponse, "description": "Invalid time interval or parameters."},
            403: {"model": ApiErrorResponse, "description": "Unauthorized."},
            404: {"model": ApiErrorResponse, "description": "Resource not found."},
        },
    )
    async def create_resource_availability(
        payload: AvailabilityIntervalCreate = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal_ctx: Any = Depends(_principal_dep),
        session: AsyncSession = Depends(session_dependency),
    ) -> ResourceAvailabilityItem:
        if isinstance(principal_ctx, AuthenticatedPrincipal):
            if principal_ctx.has_role("auditor") and not (
                principal_ctx.has_any_role(
                    "operator", "planner", "scheduler", "tenant_admin", "admin"
                )
                or principal_ctx.has_any_permission(
                    "resource:create", "resource:write", "write", "*"
                )
            ):
                raise ApiError(
                    status_code=403,
                    code="resource.unauthorized",
                    message="Auditors cannot record resource availability.",
                )
            if not (
                principal_ctx.has_any_role(
                    "operator", "planner", "scheduler", "tenant_admin", "admin"
                )
                or principal_ctx.has_any_permission(
                    "resource:create", "resource:write", "write", "*"
                )
            ):
                raise ApiError(
                    status_code=403,
                    code="resource.unauthorized",
                    message="Principal is not authorized to record resource availability.",
                )

        tenant_id = _extract_tenant_id(tenant_context)

        res = (
            await session.execute(
                select(ResourceRecord).where(
                    ResourceRecord.tenant_id == tenant_id,
                    ResourceRecord.id == payload.resource_id,
                )
            )
        ).scalar_one_or_none()
        if not res:
            raise ApiError(
                status_code=404,
                code="resource.not_found",
                message=f"Resource '{payload.resource_id}' not found in tenant '{tenant_id}'.",
            )

        if payload.end_at < payload.start_at:
            raise ApiError(
                status_code=400,
                code="resource_availability.invalid_interval",
                message=(
                    f"end_at ({payload.end_at}) cannot be earlier "
                    f"than start_at ({payload.start_at})."
                ),
            )

        interval_type_val = (
            payload.interval_type.value
            if hasattr(payload.interval_type, "value")
            else str(payload.interval_type)
        )
        if interval_type_val not in ("AVAILABLE", "UNAVAILABLE"):
            raise ApiError(
                status_code=400,
                code="resource_availability.invalid_type",
                message=(
                    f"Invalid interval_type '{interval_type_val}'. Allowed: AVAILABLE, UNAVAILABLE."
                ),
            )

        now = datetime.now(UTC)
        actor_id = (
            principal_ctx.principal_id
            if isinstance(principal_ctx, AuthenticatedPrincipal)
            else "operator"
        )

        avail_record = ResourceAvailabilityRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            resource_id=payload.resource_id,
            start_at=payload.start_at,
            end_at=payload.end_at,
            interval_type=interval_type_val,
            reason=payload.reason,
            created_at=now,
        )
        session.add(avail_record)

        outbox_repo = OutboxRepository(session)
        envelope = create_event_envelope(
            EventCreate(
                event_type="resource.availability_created",
                event_version=1,
                tenant_id=tenant_id,
                correlation_id=None,
                subject_type="resource",
                subject_id=str(res.id),
                payload={
                    "availability_id": str(avail_record.id),
                    "resource_id": str(res.id),
                    "interval_type": interval_type_val,
                    "start_at": payload.start_at.isoformat(),
                    "end_at": payload.end_at.isoformat(),
                },
            )
        )
        await outbox_repo.enqueue(envelope)

        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_principal_id=actor_id,
                actor_principal_type="HUMAN",
                authentication_method="OIDC",
                event_type="resource.availability_created",
                resource_type="resource",
                resource_id=str(res.id),
                outcome=AuditEventOutcome.SUCCESS,
                details={
                    "availability_id": str(avail_record.id),
                    "resource_id": str(res.id),
                    "interval_type": interval_type_val,
                    "start_at": payload.start_at.isoformat(),
                    "end_at": payload.end_at.isoformat(),
                },
            )
        )

        await session.flush()

        return ResourceAvailabilityItem(
            resource_id=avail_record.resource_id,
            interval_type=avail_record.interval_type,
            start_at=avail_record.start_at,
            end_at=avail_record.end_at,
            reason=avail_record.reason,
        )

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
        principal_ctx: Any = Depends(_principal_dep),
        session: AsyncSession = Depends(session_dependency),
        service: SchedulingService = Depends(scheduling_service_dependency),
        idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_KEY_HEADER),
        injected_policy: Any = Depends(_pub_policy_dep),
    ) -> SchedulePublishResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        effective_principal = payload.principal
        if isinstance(principal_ctx, AuthenticatedPrincipal):
            if not (
                principal_ctx.has_permission("schedule:publish")
                or principal_ctx.has_permission("*")
                or principal_ctx.has_any_role(
                    "approver", "scheduler", "operator", "tenant_admin", "admin"
                )
            ):
                raise ApiError(
                    status_code=403,
                    code="schedule_publish.unauthorized",
                    message="Principal is not authorized to publish schedules.",
                )
            effective_principal = principal_ctx.principal_id

        if idempotency_key:
            identity = create_idempotency_identity(
                tenant_id=tenant_id,
                operation_name="schedule.publish",
                raw_key=idempotency_key,
            )
            fingerprint = fingerprint_bytes(f"{job_id}:{effective_principal}".encode())
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
            resolved_policy = injected_policy
            if inspect.isawaitable(resolved_policy):
                resolved_policy = await resolved_policy

            sched = await service.publish_job(
                session=session,
                tenant_id=tenant_id,
                job_id=job_id,
                principal=effective_principal,
                publish_policy=resolved_policy,
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
        principal_ctx: Any = Depends(_principal_dep),
        session: AsyncSession = Depends(session_dependency),
        service: SchedulingService = Depends(scheduling_service_dependency),
        idempotency_key: str | None = Header(None, alias=IDEMPOTENCY_KEY_HEADER),
    ) -> ScheduleRepairResponse:
        if isinstance(principal_ctx, AuthenticatedPrincipal):
            if principal_ctx.has_any_role("auditor") and not (
                principal_ctx.has_any_role("scheduler", "operator", "tenant_admin")
                or principal_ctx.has_permission("schedule:repair")
            ):
                raise ApiError(
                    status_code=403,
                    code="schedule.unauthorized",
                    message="Auditor role cannot execute schedule repairs.",
                )
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

        quota_mgr = TenantQuotaManager()
        await quota_mgr.check_repair_submission_quota(session, tenant_id)

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

    @router.get(
        "/schedules",
        response_model=list[ScheduleListItem],
        status_code=status.HTTP_200_OK,
        summary="List Schedules for Tenant",
        responses={
            200: {
                "model": list[ScheduleListItem],
                "description": "Tenant schedules.",
            },
        },
    )
    async def list_schedules(
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
        status_filter: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[ScheduleListItem]:
        tenant_id = _extract_tenant_id(tenant_context)
        stmt = select(ScheduleRecord).where(ScheduleRecord.tenant_id == tenant_id)
        if status_filter:
            stmt = stmt.where(ScheduleRecord.status == status_filter)
        stmt = stmt.order_by(ScheduleRecord.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        records = list(result.scalars().all())
        return [
            ScheduleListItem(
                id=s.id,
                schedule_key=s.schedule_key,
                version=s.version,
                status=s.status,
                source_job_id=s.source_job_id,
                input_snapshot_hash=s.input_snapshot_hash,
                solver_profile_version=s.solver_profile_version,
                supersedes_id=s.supersedes_id,
                published_at=s.published_at,
                published_by=s.published_by,
                repair_reason=s.repair_reason,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in records
        ]

    @router.get(
        "/schedules/{schedule_id}",
        response_model=ScheduleDetailResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Schedule Detail with Timetable Assignments",
        responses={
            200: {
                "model": ScheduleDetailResponse,
                "description": "Schedule detail and assignments.",
            },
            404: {
                "model": ApiErrorResponse,
                "description": "Schedule not found.",
            },
        },
    )
    async def get_schedule_detail(
        schedule_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> ScheduleDetailResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        stmt = (
            select(ScheduleRecord)
            .options(selectinload(ScheduleRecord.assignments))
            .where(
                ScheduleRecord.id == schedule_id,
                ScheduleRecord.tenant_id == tenant_id,
            )
        )
        result = await session.execute(stmt)
        sched = result.scalar_one_or_none()
        if not sched:
            raise ApiError(
                status_code=404,
                code="schedule.not_found",
                message=f"Schedule '{schedule_id}' not found.",
            )

        assignments = [
            ScheduleAssignmentItem(
                id=a.id,
                schedule_id=a.schedule_id,
                activity_id=a.activity_id,
                resource_id=a.resource_id,
                start_at=a.start_at,
                end_at=a.end_at,
                is_locked=a.is_locked,
                metadata_json=a.metadata_json or {},
                created_at=a.created_at,
            )
            for a in sorted(sched.assignments, key=lambda x: x.start_at)
        ]

        return ScheduleDetailResponse(
            id=sched.id,
            schedule_key=sched.schedule_key,
            version=sched.version,
            status=sched.status,
            source_job_id=sched.source_job_id,
            input_snapshot_hash=sched.input_snapshot_hash,
            solver_profile_version=sched.solver_profile_version,
            solver_metadata=sched.solver_metadata or {},
            score_components=sched.score_components or {},
            supersedes_id=sched.supersedes_id,
            published_at=sched.published_at,
            published_by=sched.published_by,
            repair_reason=sched.repair_reason,
            repair_scope=sched.repair_scope or {},
            created_at=sched.created_at,
            updated_at=sched.updated_at,
            assignments=assignments,
        )

    parent_router.include_router(router)
    return parent_router

