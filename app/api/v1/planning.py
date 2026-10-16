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

from fastapi import APIRouter, Body, Depends, Header, Path, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.errors import ApiError, ApiErrorResponse
from app.core.auth.models import AuthenticatedPrincipal, TenantContext
from app.task_intelligence.graph.models import CPMScheduleResult
from app.task_intelligence.progress import (
    InvalidTaskProgressTransitionError,
    TaskProgressUpdate,
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

    @router.post(
        "/goals/{goal_id}/generate-plan",
        response_model=PlanResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Generate Plan for Goal",
        responses={
            201: {"model": PlanResponse, "description": "Plan generated."},
            404: {"model": ApiErrorResponse, "description": "Goal not found."},
            422: {"model": ApiErrorResponse, "description": "Planner unavailable or invalid."},
        },
    )
    async def generate_plan(
        goal_id: UUID = Path(...),
        plan_proposal: PlanSchema | None = None,
        tenant_context: Any = Depends(tenant_dependency),
        service: PlanService = Depends(plan_service_dependency),
        planner: Any = Depends(active_planner),
        x_correlation_id: str | None = Header(default=None, alias="X-Correlation-ID"),
    ) -> PlanResponse:
        tenant_id = _extract_tenant_id(tenant_context)

        # In Phase 2A: if plan_proposal is provided directly (e.g. from test fixture), use it.
        # Otherwise, if planner is injected, call it.
        # If neither is present, raise 422 (do not fake AI generation).
        schema_to_persist = plan_proposal
        if schema_to_persist is None:
            if planner is not None:
                # Retrieve goal to provide to planner
                uow = service._uow_factory()
                async with uow:
                    from app.db.repositories.planning import GoalRepository

                    g_rec = await GoalRepository(uow.session).get_by_id(goal_id, tenant_id)
                    if not g_rec:
                        raise ApiError(
                            status_code=404, code="goal.not_found", message="Goal not found."
                        )
                    goal_dto = GoalCreate(objective=g_rec.objective, scope=g_rec.scope)
                schema_to_persist = await planner.generate_plan(goal_dto)
            else:
                raise ApiError(
                    status_code=422,
                    code="planner.unavailable",
                    message=(
                        "PydanticAI planner arrives in Phase 2B. "
                        "Direct PlanSchema required in Phase 2A."
                    ),
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
