"""Canonical production route composition module (Phase 8A).

Securely composes all decoupled domain router factories onto the API v1 router:
- /goals, /plans, /tasks (Planning)
- /workflows, /workflow (Workflows & human tasks)
- /schedule-jobs, /resources (Scheduling)
- /jobs/{job_id} (Asynchronous Job Polling)
- /analytics/operations (Operations Dashboard Telemetry)
- /audit (Chronological Audit Trails)

Security Invariants:
- All business routes are bound to canonical HTTP authentication (authenticate_request).
- TenantContext is strictly derived from the verified principal (require_trusted_tenant_context).
- Zero unauthenticated access to domain operations.
- Zero mock or default tenant derivations.
"""

from collections.abc import AsyncGenerator, Callable
from typing import Any

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import OperationsAnalyticsService
from app.api.v1.analytics import create_analytics_router
from app.api.v1.audit import create_audit_router
from app.api.v1.jobs import create_job_read_router
from app.api.v1.planning import create_planning_router
from app.api.v1.scheduling import create_scheduling_router
from app.api.v1.workflows import create_workflow_router
from app.audit.service import AuditQueryService
from app.core.auth.dependency import (
    authenticate_request,
    require_trusted_tenant_context,
)
from app.db.engine import DatabaseManager
from app.db.uow import create_unit_of_work_factory
from app.resources.service import ResourceService
from app.scheduling.service import SchedulingService
from app.task_intelligence.service import PlanService
from app.workflow.adapters.flowable.adapter import FlowableAdapter
from app.workflow.adapters.protocol import WorkflowAdapterProtocol
from app.workflow.service import WorkflowService

_db_manager: DatabaseManager | None = None
_default_workflow_adapter: WorkflowAdapterProtocol | None = None


def get_shared_db_manager() -> DatabaseManager:
    """Return singleton DatabaseManager for API runtime session provisioning."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


async def get_request_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an AsyncSession per request."""
    db = get_shared_db_manager()
    async with db.session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


def get_default_plan_service() -> PlanService:
    """FastAPI dependency constructing PlanService with canonical UoW factory."""
    db = get_shared_db_manager()
    uow_factory = create_unit_of_work_factory(db.session_factory)
    return PlanService(uow_factory=uow_factory)


def get_default_workflow_adapter() -> WorkflowAdapterProtocol:
    """Return default workflow adapter for runtime execution."""
    global _default_workflow_adapter
    if _default_workflow_adapter is None:
        _default_workflow_adapter = FlowableAdapter()
    return _default_workflow_adapter


def get_default_workflow_service() -> WorkflowService:
    """FastAPI dependency constructing WorkflowService with canonical UoW factory."""
    db = get_shared_db_manager()
    uow_factory = create_unit_of_work_factory(db.session_factory)
    return WorkflowService(
        uow_factory=uow_factory,
        adapter=get_default_workflow_adapter(),
    )


def get_default_scheduling_service() -> SchedulingService:
    """FastAPI dependency constructing SchedulingService."""
    return SchedulingService()


def get_default_resource_service() -> ResourceService:
    """FastAPI dependency constructing ResourceService."""
    return ResourceService()


def register_domain_routes(
    parent_router: APIRouter,
    *,
    tenant_dependency: Callable[..., Any] = require_trusted_tenant_context,
    principal_dependency: Callable[..., Any] = authenticate_request,
    session_dependency: Callable[..., Any] = get_request_db_session,
    plan_service_dependency: Callable[..., PlanService] = get_default_plan_service,
    workflow_service_dependency: Callable[..., WorkflowService] = get_default_workflow_service,
    scheduling_service_dependency: Callable[
        ..., SchedulingService
    ] = get_default_scheduling_service,
    resource_service_dependency: Callable[..., ResourceService] = get_default_resource_service,
    reader_dependency: Callable[..., Any] | None = None,
    analytics_service: OperationsAnalyticsService | None = None,
    audit_service: AuditQueryService | None = None,
) -> None:
    """Securely mount all business domain routers onto parent APIRouter."""
    # 1. Planning routes: /goals, /plans, /tasks
    planning_router = create_planning_router(
        tenant_dependency=tenant_dependency,
        plan_service_dependency=plan_service_dependency,
        principal_dependency=principal_dependency,
    )
    parent_router.include_router(planning_router)

    # 2. Workflow routes: /definitions, /instances, /tasks, /callbacks
    workflow_router = create_workflow_router(
        tenant_dependency=tenant_dependency,
        principal_dependency=principal_dependency,
        workflow_service_dependency=workflow_service_dependency,
    )
    parent_router.include_router(workflow_router)

    # 3. Scheduling routes: /schedule-jobs, /resources
    scheduling_router = create_scheduling_router(
        tenant_dependency=tenant_dependency,
        session_dependency=session_dependency,
        scheduling_service_dependency=scheduling_service_dependency,
        resource_service_dependency=resource_service_dependency,
        principal_dependency=principal_dependency,
    )
    parent_router.include_router(scheduling_router)

    # 4. Job polling router: /jobs
    if reader_dependency is not None:
        jobs_router = create_job_read_router(
            tenant_dependency=tenant_dependency,
            reader_dependency=reader_dependency,
        )
        parent_router.include_router(jobs_router)

    # 5. Analytics router: /analytics
    analytics_router = create_analytics_router(
        tenant_dependency=tenant_dependency,
        session_dependency=session_dependency,
        analytics_service=analytics_service,
    )
    parent_router.include_router(analytics_router)

    # 6. Audit router: /audit
    audit_router = create_audit_router(
        tenant_dependency=tenant_dependency,
        session_dependency=session_dependency,
        audit_service=audit_service,
    )
    parent_router.include_router(audit_router)


__all__ = [
    "get_default_plan_service",
    "get_default_resource_service",
    "get_default_scheduling_service",
    "get_default_workflow_adapter",
    "get_default_workflow_service",
    "get_request_db_session",
    "get_shared_db_manager",
    "register_domain_routes",
]
