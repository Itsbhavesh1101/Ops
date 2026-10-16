"""Canonical Analytics API router factory (Phase 6 Stage M).

Exposes:
- GET /api/v1/analytics/operations: Aggregated operational dashboard telemetry.

Architectural Invariants:
- ANALYTICS_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY
- Unmounted from production app.main until trusted tenant auth gateway is finalized.
- Requires injected trusted tenant context and database session.
- Real-time aggregation over canonical PostgreSQL tables without predictive risk scoring.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import (
    OperationsAnalyticsService,
    OperationsDashboardSummary,
)
from app.api.errors import ApiErrorResponse
from app.core.auth.models import TenantContext


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


def create_analytics_router(
    *,
    tenant_dependency: Callable[..., Any],
    session_dependency: Callable[..., AsyncSession],
    analytics_service: OperationsAnalyticsService | None = None,
) -> APIRouter:
    """Construct a safe APIRouter for operations analytics queries."""
    router = APIRouter(tags=["Analytics"])
    service = analytics_service or OperationsAnalyticsService()

    @router.get(
        "/analytics/operations",
        response_model=OperationsDashboardSummary,
        status_code=status.HTTP_200_OK,
        summary="Get Operations Dashboard Telemetry",
        responses={
            200: {
                "model": OperationsDashboardSummary,
                "description": "Aggregated operational snapshot.",
            },
            401: {"model": ApiErrorResponse, "description": "Unauthorized."},
            403: {"model": ApiErrorResponse, "description": "Forbidden."},
        },
    )
    async def get_operations_dashboard(
        goal_id: UUID | None = Query(default=None, description="Optional filter by parent goal."),
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> OperationsDashboardSummary:
        tenant_id = _extract_tenant_id(tenant_context)
        return await service.get_operations_summary(
            session=session,
            tenant_id=tenant_id,
            goal_id=goal_id,
        )

    return router


__all__ = ["create_analytics_router"]
