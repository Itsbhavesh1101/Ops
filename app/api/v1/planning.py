"""HTTP API contracts and safe router factory for Task Intelligence Planning (Phase 2A).

Provides:
- POST /goals: Goal intake
- POST /goals/{goal_id}/generate-plan: Plan generation proposal (requires PlannerProtocol)
- POST /plans/{plan_id}/validate: Deterministic PlanSchema & DAG validation
- POST /plans/{plan_id}/approve: Baseline approval freeze
- GET  /plans/{plan_id}/critical-path: CPM critical path and slack metrics
- PATCH /tasks/{task_id}: Task progress and blocker updates

Architectural & Security Invariants:
- TASK_INTELLIGENCE_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY
- Unmounted: This router is NOT registered in app.main until trusted tenant auth exists.
- Injected dependencies: Router accepts trusted tenant context and PlanService factory.
- Tenant isolation: Missing and cross-tenant entities return identical 404 responses.
- Non-authoritative model output: Approval requires prior deterministic validation.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import ApiError, ApiErrorResponse
from app.core.auth.models import AuthenticatedPrincipal, TenantContext
from app.task_intelligence.graph.models import CPMScheduleResult
from app.task_intelligence.milestones import MilestoneHealthResponse
from app.task_intelligence.progress import (
    InvalidTaskProgressTransitionError,
    TaskProgressUpdate,
)
from app.task_intelligence.recommender import (
    PlanRecommendationsResponse,
    RecommendationPolicyConfig,
)
from app.task_intelligence.schemas import (
    GoalCreate,
    GoalResponse,
    InvalidPlanStateTransitionError,
    PlanSchema,
    ValidationResult,
)
from app.task_intelligence.service import (
    GoalNotFoundError,
    PlanApprovalError,
    PlanNotFoundError,
    PlanService,
    TaskNotFoundError,
)


class PlanResponse(BaseModel):
    """Public representation of a plan version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    goal_id: UUID
    version_number: int
    plan_name: str
    status: str
    is_baseline: bool
    baseline_hash: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskResponse(BaseModel):
    """Public representation of an operational task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    plan_id: UUID
    temporary_id: str
    title: str
    status: str
    progress_percentage: int
    actual_duration_minutes: int | None = None
    blocked_reason: str | None = None
    updated_at: datetime


class TaskDetailResponse(BaseModel):
    """Detailed public representation of an operational task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    plan_id: UUID
    temporary_id: str
    title: str
    description: str | None = None
    wbs_code: str | None = None
    owner_role: str | None = None
    owner_reference: str | None = None
    estimated_duration_minutes: int
    actual_duration_minutes: int | None = None
    status: str
    progress_percentage: int
    blocked_reason: str | None = None
    priority: str
    work_category: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    parent_task_id: UUID | None = None
    milestone_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class DependencyDetailResponse(BaseModel):
    """Detailed representation of a task dependency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    predecessor_id: UUID
    successor_id: UUID
    dependency_type: str
    lag_minutes: int


class MilestoneDetailResponse(BaseModel):
    """Detailed representation of a plan milestone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    temporary_id: str
    name: str
    description: str | None = None
    target_date: datetime | None = None


class TaskRiskResponse(BaseModel):
    """Public representation of an operational risk flag."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    category: str
    severity: str
    description: str
    is_evaluable: bool
    detected_at: datetime


class RiskDetailResponse(BaseModel):
    """Detailed representation of an identified task risk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    task_id: UUID
    category: str
    severity: str
    description: str
    is_evaluable: bool
    detected_at: datetime


class PlanApprovalState(BaseModel):
    """Structured representation of plan approval state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    is_baseline: bool
    baseline_hash: str | None = None


class PlanDetailResponse(BaseModel):
    """Full detail of a plan version with tasks, milestones, dependencies, and risks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    goal_id: UUID
    version_number: int
    plan_name: str
    status: str
    is_baseline: bool
    superseded_by_plan_id: UUID | None = None
    baseline_hash: str | None = None
    approval_state: PlanApprovalState
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    tasks: list[TaskDetailResponse] = Field(default_factory=list)
    dependencies: list[DependencyDetailResponse] = Field(default_factory=list)
    milestones: list[MilestoneDetailResponse] = Field(default_factory=list)
    risks: list[RiskDetailResponse] = Field(default_factory=list)


def _map_task(task: Any) -> TaskDetailResponse:
    return TaskDetailResponse(
        id=task.id,
        plan_id=task.plan_id,
        temporary_id=task.temporary_id,
        title=task.title,
        description=task.description,
        wbs_code=task.wbs_code,
        owner_role=task.owner_role,
        owner_reference=task.owner_reference,
        estimated_duration_minutes=task.estimated_duration_minutes,
        actual_duration_minutes=task.actual_duration_minutes,
        status=task.status,
        progress_percentage=task.progress_percentage,
        blocked_reason=task.blocked_reason,
        priority=str(task.priority),
        work_category=task.work_category,
        acceptance_criteria=task.acceptance_criteria or [],
        parent_task_id=task.parent_task_id,
        milestone_id=task.milestone_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _map_plan_detail(plan: Any) -> PlanDetailResponse:
    tasks = [_map_task(t) for t in (plan.tasks or [])]
    dependencies = [
        DependencyDetailResponse(
            id=d.id,
            predecessor_id=d.predecessor_id,
            successor_id=d.successor_id,
            dependency_type=str(d.dependency_type),
            lag_minutes=d.lag_minutes,
        )
        for d in (plan.dependencies or [])
    ]
    milestones = [
        MilestoneDetailResponse(
            id=m.id,
            temporary_id=m.temporary_id,
            name=m.name,
            description=m.description,
            target_date=m.target_date,
        )
        for m in (plan.milestones or [])
    ]
    risks = [
        RiskDetailResponse(
            id=r.id,
            task_id=r.task_id,
            category=str(r.category),
            severity=str(r.severity),
            description=r.description,
            is_evaluable=r.is_evaluable,
            detected_at=r.detected_at,
        )
        for r in (plan.risks or [])
    ]
    return PlanDetailResponse(
        id=plan.id,
        goal_id=plan.goal_id,
        version_number=plan.version_number,
        plan_name=plan.plan_name,
        status=plan.status,
        is_baseline=plan.is_baseline,
        superseded_by_plan_id=plan.superseded_by_plan_id,
        baseline_hash=plan.baseline_hash,
        approval_state=PlanApprovalState(
            status=plan.status,
            is_baseline=plan.is_baseline,
            baseline_hash=plan.baseline_hash,
        ),
        metadata=plan.metadata_json or {},
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        tasks=tasks,
        dependencies=dependencies,
        milestones=milestones,
        risks=risks,
    )


def _extract_tenant_id(context: Any) -> str:
    """Extract canonical tenant_id from trusted context or string."""
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


def _null_planner_dependency() -> Any:
    return None


def create_planning_router(
    *,
    tenant_dependency: Callable[..., Any],
    plan_service_dependency: Callable[..., PlanService],
    planner_dependency: Callable[..., Any] | None = None,
    principal_dependency: Callable[..., Any] | None = None,
) -> APIRouter:
    """Construct a safe APIRouter for Task Intelligence planning operations.

    Invariants:
    - Unmounted: Intentionally excluded from production app.main until trusted tenant auth.
    - Requires explicitly injected trusted tenant dependency.
    - Uniform 404 responses for cross-tenant or non-existent entity access.
    """
    router = APIRouter(tags=["Planning"])
    active_planner = planner_dependency or _null_planner_dependency
    active_principal = principal_dependency or _null_planner_dependency

    @router.post(
        "/goals",
        response_model=GoalResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Create Operational Goal",
        responses={
            201: {"model": GoalResponse, "description": "Goal created."},
            403: {"model": ApiErrorResponse, "description": "Unauthorized role."},
            422: {"model": ApiErrorResponse, "description": "Validation error."},
        },
    )
    async def create_goal(
        goal: GoalCreate,
        tenant_context: Any = Depends(tenant_dependency),
        principal: Any = Depends(active_principal),
        service: PlanService = Depends(plan_service_dependency),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    ) -> GoalResponse:
        if isinstance(principal, AuthenticatedPrincipal):
            if principal.has_role("auditor") and not principal.has_any_role(
                "planner", "tenant_admin", "admin"
            ):
                raise ApiError(
                    status_code=403,
                    code="goal.unauthorized",
                    message="Auditors cannot create operational goals.",
                )
        tenant_id = _extract_tenant_id(tenant_context)
        return await service.create_goal(
            tenant_id=tenant_id,
            goal_data=goal,
            correlation_id=x_correlation_id,
        )

    @router.get(
        "/goals",
        response_model=list[GoalResponse],
        status_code=status.HTTP_200_OK,
        summary="List Operational Goals",
        responses={
            200: {"model": list[GoalResponse], "description": "Goals retrieved."},
        },
    )
    async def list_goals(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        tenant_context: Any = Depends(tenant_dependency),
        service: PlanService = Depends(plan_service_dependency),
    ) -> list[GoalResponse]:
        tenant_id = _extract_tenant_id(tenant_context)
        return await service.list_goals(tenant_id, limit=limit, offset=offset)

    @router.get(
        "/goals/{goal_id}",
        response_model=GoalResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Operational Goal Detail",
        responses={
            200: {"model": GoalResponse, "description": "Goal detail."},
            404: {"model": ApiErrorResponse, "description": "Goal not found."},
        },
    )
    async def get_goal(
        goal_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        service: PlanService = Depends(plan_service_dependency),
    ) -> GoalResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.get_goal(tenant_id, goal_id)
        except GoalNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="goal.not_found",
                message="Goal not found.",
            ) from exc

    @router.post(
        "/goals/{goal_id}/generate-plan",
        response_model=PlanResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Generate Plan for Goal",
        responses={
            201: {"model": PlanResponse, "description": "Plan generated."},
            403: {"model": ApiErrorResponse, "description": "Unauthorized role."},
            404: {"model": ApiErrorResponse, "description": "Goal not found."},
            422: {"model": ApiErrorResponse, "description": "Planner unavailable or invalid."},
        },
    )
    async def generate_plan(
        goal_id: UUID = Path(...),
        plan_proposal: PlanSchema | dict[str, Any] | None = Body(default=None),
        tenant_context: Any = Depends(tenant_dependency),
        principal: Any = Depends(active_principal),
        service: PlanService = Depends(plan_service_dependency),
        planner: Any = Depends(active_planner),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    ) -> PlanResponse:
        if isinstance(principal, AuthenticatedPrincipal):
            if principal.has_role("auditor") and not principal.has_any_role(
                "planner", "tenant_admin", "admin"
            ):
                raise ApiError(
                    status_code=403,
                    code="plan.unauthorized",
                    message="Auditors cannot generate operational plans.",
                )
        tenant_id = _extract_tenant_id(tenant_context)

        schema_to_persist: PlanSchema | None = None
        if isinstance(plan_proposal, PlanSchema):
            schema_to_persist = plan_proposal
        elif isinstance(plan_proposal, dict) and plan_proposal:
            schema_to_persist = PlanSchema.model_validate(plan_proposal)

        if schema_to_persist is None:
            if planner is not None:
                # Retrieve goal to provide to planner
                try:
                    goal_rec = await service.get_goal(tenant_id, goal_id)
                except GoalNotFoundError as exc:
                    raise ApiError(
                        status_code=404, code="goal.not_found", message="Goal not found."
                    ) from exc
                goal_dto = GoalCreate(objective=goal_rec.objective, scope=goal_rec.scope)
                schema_to_persist = await planner.generate_plan(goal_dto)
            else:
                raise ApiError(
                    status_code=422,
                    code="planner.unavailable",
                    message="Planner is unavailable.",
                )

        try:
            plan_rec = await service.persist_generated_plan(
                tenant_id=tenant_id,
                goal_id=goal_id,
                plan_schema=schema_to_persist,
                correlation_id=x_correlation_id,
            )
            return PlanResponse(
                id=plan_rec.id,
                goal_id=plan_rec.goal_id,
                version_number=plan_rec.version_number,
                plan_name=plan_rec.plan_name,
                status=plan_rec.status,
                is_baseline=plan_rec.is_baseline,
                baseline_hash=plan_rec.baseline_hash,
                created_at=plan_rec.created_at,
                updated_at=plan_rec.updated_at,
            )
        except GoalNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="goal.not_found",
                message="Goal not found.",
            ) from exc

    @router.get(
        "/plans",
        response_model=list[PlanResponse],
        status_code=status.HTTP_200_OK,
        summary="List Plans for Tenant",
        responses={
            200: {"model": list[PlanResponse], "description": "Plans retrieved."},
        },
    )
    async def list_plans(
        goal_id: UUID | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        tenant_context: Any = Depends(tenant_dependency),
        service: PlanService = Depends(plan_service_dependency),
    ) -> list[PlanResponse]:
        tenant_id = _extract_tenant_id(tenant_context)
        records = await service.list_plans(tenant_id, goal_id=goal_id, limit=limit, offset=offset)
        return [
            PlanResponse(
                id=p.id,
                goal_id=p.goal_id,
                version_number=p.version_number,
                plan_name=p.plan_name,
                status=p.status,
                is_baseline=p.is_baseline,
                baseline_hash=p.baseline_hash,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in records
        ]

    @router.get(
        "/plans/{plan_id}",
        response_model=PlanDetailResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Plan Detail with Tasks, Milestones, and Dependencies",
        responses={
            200: {"model": PlanDetailResponse, "description": "Plan detail."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
        },
    )
    async def get_plan_detail(
        plan_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        service: PlanService = Depends(plan_service_dependency),
    ) -> PlanDetailResponse:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            plan = await service.get_plan(tenant_id, plan_id, load_relations=True)
            if not plan:
                raise ApiError(
                    status_code=404,
                    code="plan.not_found",
                    message="Plan not found.",
                )
            return _map_plan_detail(plan)
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc

    @router.get(
        "/plans/{plan_id}/tasks",
        response_model=list[TaskDetailResponse],
        status_code=status.HTTP_200_OK,
        summary="Get Tasks for Plan",
        responses={
            200: {"model": list[TaskDetailResponse], "description": "Tasks for plan."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
        },
    )
    async def get_plan_tasks(
        plan_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        service: PlanService = Depends(plan_service_dependency),
    ) -> list[TaskDetailResponse]:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            tasks = await service.get_plan_tasks(tenant_id, plan_id)
            return [_map_task(t) for t in tasks]
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc

    @router.post(
        "/plans/{plan_id}/validate",
        response_model=ValidationResult,
        summary="Validate Plan Proposal",
        responses={
            200: {"model": ValidationResult, "description": "Validation passed."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
            422: {"model": ValidationResult, "description": "Validation failed with issues."},
        },
    )
    async def validate_plan(
        response: Response,
        plan_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        service: PlanService = Depends(plan_service_dependency),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    ) -> ValidationResult:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            result = await service.validate_plan(
                tenant_id=tenant_id,
                plan_id=plan_id,
                correlation_id=x_correlation_id,
            )
            if not result.is_valid:
                response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            return result
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc

    @router.post(
        "/plans/{plan_id}/submit-for-approval",
        response_model=PlanResponse,
        summary="Submit Validated Plan for Approval",
        responses={
            200: {"model": PlanResponse, "description": "Plan submitted for approval."},
            403: {"model": ApiErrorResponse, "description": "Unauthorized role."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
            422: {
                "model": ApiErrorResponse,
                "description": "Illegal state transition or unvalidated plan.",
            },
        },
    )
    async def submit_plan_for_approval(
        plan_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: Any = Depends(active_principal),
        service: PlanService = Depends(plan_service_dependency),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    ) -> PlanResponse:
        actor_id = "authorized-planner"
        if isinstance(principal, AuthenticatedPrincipal):
            actor_id = principal.principal_id
            if principal.has_role("auditor") and not principal.has_any_role(
                "planner", "operator", "tenant_admin", "admin"
            ):
                raise ApiError(
                    status_code=403,
                    code="plan.unauthorized",
                    message="Auditor role cannot submit plans for approval.",
                )
            if not (
                principal.has_any_role("planner", "operator", "tenant_admin", "admin")
                or principal.has_permission("plan:submit")
                or principal.has_permission("plan:edit")
                or principal.has_permission("*")
            ):
                raise ApiError(
                    status_code=403,
                    code="plan.unauthorized",
                    message=(
                        "Submitting plan for approval requires planner, operator, "
                        "or tenant_admin role."
                    ),
                )
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            submitted = await service.submit_for_approval(
                tenant_id=tenant_id,
                plan_id=plan_id,
                actor_id=actor_id,
                correlation_id=x_correlation_id,
            )
            return PlanResponse(
                id=submitted.id,
                goal_id=submitted.goal_id,
                version_number=submitted.version_number,
                plan_name=submitted.plan_name,
                status=submitted.status,
                is_baseline=submitted.is_baseline,
                baseline_hash=submitted.baseline_hash,
                created_at=submitted.created_at,
                updated_at=submitted.updated_at,
            )
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc
        except InvalidPlanStateTransitionError as exc:
            raise ApiError(
                status_code=422,
                code="plan.approval_rejected",
                message=str(exc),
            ) from exc

    @router.post(
        "/plans/{plan_id}/approve",
        response_model=PlanResponse,
        summary="Approve Plan Baseline",
        responses={
            200: {"model": PlanResponse, "description": "Plan approved and baseline frozen."},
            403: {"model": ApiErrorResponse, "description": "Unauthorized or SoD violation."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
            422: {
                "model": ApiErrorResponse,
                "description": "Illegal state transition or unvalidated plan.",
            },
        },
    )
    async def approve_plan(
        plan_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: Any = Depends(active_principal),
        service: PlanService = Depends(plan_service_dependency),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    ) -> PlanResponse:
        approver_id = "authorized-planner"
        if isinstance(principal, AuthenticatedPrincipal):
            approver_id = principal.principal_id
            if principal.has_role("planner") and not principal.has_any_role(
                "tenant_admin", "admin"
            ):
                raise ApiError(
                    status_code=403,
                    code="plan.sod_violation",
                    message="Separation of Duties violation: planner cannot approve plans.",
                )
            if not (
                principal.has_any_role("approver", "tenant_admin", "admin")
                or principal.has_permission("plan:approve")
                or principal.has_permission("*")
            ):
                raise ApiError(
                    status_code=403,
                    code="plan.unauthorized",
                    message="Plan approval requires approver role or plan:approve permission.",
                )
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            approved = await service.approve_plan(
                tenant_id=tenant_id,
                plan_id=plan_id,
                approver_id=approver_id,
                correlation_id=x_correlation_id,
            )
            return PlanResponse(
                id=approved.id,
                goal_id=approved.goal_id,
                version_number=approved.version_number,
                plan_name=approved.plan_name,
                status=approved.status,
                is_baseline=approved.is_baseline,
                baseline_hash=approved.baseline_hash,
                created_at=approved.created_at,
                updated_at=approved.updated_at,
            )
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc
        except (InvalidPlanStateTransitionError, PlanApprovalError) as exc:
            raise ApiError(
                status_code=422,
                code="plan.approval_rejected",
                message=str(exc),
            ) from exc

    @router.get(
        "/plans/{plan_id}/critical-path",
        response_model=CPMScheduleResult,
        summary="Get Plan Critical Path",
        responses={
            200: {"model": CPMScheduleResult, "description": "CPM calculated."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
        },
    )
    async def get_critical_path(
        plan_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        service: PlanService = Depends(plan_service_dependency),
    ) -> CPMScheduleResult:
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.get_critical_path(tenant_id=tenant_id, plan_id=plan_id)
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc

    @router.get(
        "/plans/{plan_id}/recommendations",
        response_model=PlanRecommendationsResponse,
        status_code=status.HTTP_200_OK,
        summary="Get Next-Best-Task Recommendations",
        responses={
            200: {
                "model": PlanRecommendationsResponse,
                "description": "Deterministic actionable task recommendations.",
            },
            403: {"model": ApiErrorResponse, "description": "Unauthorized role."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
            422: {"model": ApiErrorResponse, "description": "Invalid recommendation policy."},
        },
    )
    async def get_plan_recommendations(
        plan_id: UUID = Path(...),
        limit: int = Query(default=10, ge=1, le=50, description="Max recommendations to return"),
        include_blocked: bool = Query(
            default=False, description="Whether to include blocked tasks with mitigation actions"
        ),
        critical_path_weight: float | None = Query(
            default=None, ge=0.0, le=1.0, description="Optional weight for critical path factor"
        ),
        priority_weight: float | None = Query(
            default=None, ge=0.0, le=1.0, description="Optional weight for priority factor"
        ),
        progress_momentum_weight: float | None = Query(
            default=None, ge=0.0, le=1.0, description="Optional weight for momentum factor"
        ),
        readiness_weight: float | None = Query(
            default=None, ge=0.0, le=1.0, description="Optional weight for readiness factor"
        ),
        tenant_context: Any = Depends(tenant_dependency),
        principal: Any = Depends(active_principal),
        service: PlanService = Depends(plan_service_dependency),
    ) -> PlanRecommendationsResponse:
        """Get explainable, deterministic next-best-task recommendations for a plan (FR-TI-09)."""
        if isinstance(principal, AuthenticatedPrincipal):
            if not (
                principal.has_any_role(
                    "planner", "operator", "scheduler", "auditor", "tenant_admin", "admin"
                )
                or principal.has_permission("plan:read")
                or principal.has_permission("*")
            ):
                raise ApiError(
                    status_code=403,
                    code="plan.unauthorized",
                    message="Insufficient permissions to view plan recommendations.",
                )
        tenant_id = _extract_tenant_id(tenant_context)

        policy_weights = [
            critical_path_weight,
            priority_weight,
            progress_momentum_weight,
            readiness_weight,
        ]
        any_weight = any(w is not None for w in policy_weights)
        all_weights = all(w is not None for w in policy_weights)

        policy: RecommendationPolicyConfig | None = None
        if any_weight and not all_weights:
            raise ApiError(
                status_code=422,
                code="recommendation.policy_incomplete",
                message=(
                    "When supplying recommendation policy weights, all 4 weights "
                    "(critical_path_weight, priority_weight, progress_momentum_weight, "
                    "readiness_weight) must be provided."
                ),
            )
        elif all_weights:
            try:
                assert critical_path_weight is not None
                assert priority_weight is not None
                assert progress_momentum_weight is not None
                assert readiness_weight is not None
                policy = RecommendationPolicyConfig(
                    critical_path_weight=critical_path_weight,
                    priority_weight=priority_weight,
                    progress_momentum_weight=progress_momentum_weight,
                    readiness_weight=readiness_weight,
                )
            except Exception as exc:
                raise ApiError(
                    status_code=422,
                    code="recommendation.invalid_policy",
                    message=f"Invalid recommendation policy weights: {exc}",
                ) from exc

        try:
            return await service.get_plan_recommendations(
                tenant_id=tenant_id,
                plan_id=plan_id,
                policy=policy,
                limit=limit,
                include_blocked=include_blocked,
            )
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc

    @router.get(
        "/plans/{plan_id}/milestones",
        response_model=list[MilestoneHealthResponse],
        status_code=status.HTTP_200_OK,
        summary="Get Plan Milestones and Derived Health",
        responses={
            200: {
                "model": list[MilestoneHealthResponse],
                "description": "Milestones and derived operational health rollups (FR-TI-06).",
            },
            403: {"model": ApiErrorResponse, "description": "Unauthorized role."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
        },
    )
    async def get_plan_milestones(
        plan_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: Any = Depends(active_principal),
        service: PlanService = Depends(plan_service_dependency),
    ) -> list[MilestoneHealthResponse]:
        """Get milestones and derived health metrics for a plan (FR-TI-06)."""
        if isinstance(principal, AuthenticatedPrincipal):
            if not (
                principal.has_any_role(
                    "planner", "operator", "scheduler", "auditor", "tenant_admin", "admin"
                )
                or principal.has_permission("plan:read")
                or principal.has_permission("*")
            ):
                raise ApiError(
                    status_code=403,
                    code="plan.unauthorized",
                    message="Insufficient permissions to view plan milestones.",
                )
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            return await service.get_plan_milestones(
                tenant_id=tenant_id,
                plan_id=plan_id,
            )
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc

    @router.get(
        "/plans/{plan_id}/risks",
        response_model=list[TaskRiskResponse],
        status_code=status.HTTP_200_OK,
        summary="Get Plan Operational Risks",
        responses={
            200: {
                "model": list[TaskRiskResponse],
                "description": "Deterministic operational risk and blocker detection signals.",
            },
            403: {"model": ApiErrorResponse, "description": "Unauthorized role."},
            404: {"model": ApiErrorResponse, "description": "Plan not found."},
        },
    )
    async def get_plan_risks(
        plan_id: UUID = Path(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: Any = Depends(active_principal),
        service: PlanService = Depends(plan_service_dependency),
    ) -> list[TaskRiskResponse]:
        """Get operational risk signals and blocker detection for a plan (FR-TI-07)."""
        if isinstance(principal, AuthenticatedPrincipal):
            if not (
                principal.has_any_role(
                    "planner", "operator", "scheduler", "auditor", "tenant_admin", "admin"
                )
                or principal.has_permission("plan:read")
                or principal.has_permission("*")
            ):
                raise ApiError(
                    status_code=403,
                    code="plan.unauthorized",
                    message="Insufficient permissions to view plan risks.",
                )
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            risks = await service.evaluate_plan_risks(
                tenant_id=tenant_id,
                plan_id=plan_id,
            )
            return [
                TaskRiskResponse(
                    task_id=r.task_id,
                    category=str(r.category),
                    severity=str(r.severity),
                    description=r.description,
                    is_evaluable=r.is_evaluable,
                    detected_at=r.detected_at,
                )
                for r in risks
            ]
        except PlanNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="plan.not_found",
                message="Plan not found.",
            ) from exc

    @router.patch(
        "/tasks/{task_id}",
        response_model=TaskResponse,
        summary="Update Task Progress",
        responses={
            200: {"model": TaskResponse, "description": "Task progress updated."},
            404: {"model": ApiErrorResponse, "description": "Task not found."},
            422: {"model": ApiErrorResponse, "description": "Illegal progress transition."},
        },
    )
    async def update_task_progress(
        task_id: UUID = Path(...),
        update_data: TaskProgressUpdate = Body(...),
        tenant_context: Any = Depends(tenant_dependency),
        principal: Any = Depends(active_principal),
        service: PlanService = Depends(plan_service_dependency),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    ) -> TaskResponse:
        if isinstance(principal, AuthenticatedPrincipal):
            if principal.has_role("auditor") and not principal.has_any_role(
                "operator", "scheduler", "planner", "tenant_admin", "admin"
            ):
                raise ApiError(
                    status_code=403,
                    code="task.unauthorized",
                    message="Auditors cannot mutate operational tasks.",
                )
        tenant_id = _extract_tenant_id(tenant_context)
        try:
            task = await service.update_task_progress(
                tenant_id=tenant_id,
                task_id=task_id,
                update_data=update_data,
                correlation_id=x_correlation_id,
            )
            return TaskResponse(
                id=task.id,
                plan_id=task.plan_id,
                temporary_id=task.temporary_id,
                title=task.title,
                status=task.status,
                progress_percentage=task.progress_percentage,
                actual_duration_minutes=task.actual_duration_minutes,
                blocked_reason=task.blocked_reason,
                updated_at=task.updated_at,
            )
        except TaskNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="task.not_found",
                message="Task not found.",
            ) from exc
        except InvalidTaskProgressTransitionError as exc:
            raise ApiError(
                status_code=422,
                code="task.illegal_progress_transition",
                message=str(exc),
            ) from exc

    return router
