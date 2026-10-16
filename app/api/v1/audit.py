"""Canonical Audit API router factory (Phase 6 Stage L).

Exposes:
- GET /api/v1/audit: Authorized, tenant-scoped chronological audit search.

Architectural Invariants:
- AUDIT_API_PRODUCTION_MOUNT = DEFERRED_PENDING_TRUSTED_HTTP_TENANT_DEPENDENCY
- Unmounted from production app.main until trusted tenant auth gateway is finalized.
- Requires injected trusted tenant context and database session.
- Enforces strict chronological ordering and tenant isolation.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiErrorResponse
from app.audit.service import AuditEventResponse, AuditQueryService
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


def create_audit_router(
    *,
    tenant_dependency: Callable[..., Any],
    session_dependency: Callable[..., AsyncSession],
    audit_service: AuditQueryService | None = None,
) -> APIRouter:
    """Construct a safe APIRouter for canonical audit trail queries."""
    router = APIRouter(tags=["Audit"])
    query_service = audit_service or AuditQueryService()

    @router.get(
        "/audit",
        response_model=list[AuditEventResponse],
        status_code=status.HTTP_200_OK,
        summary="Search Correlated Audit Events",
        responses={
            200: {
                "model": list[AuditEventResponse],
                "description": "Chronological audit records.",
            },
            401: {"model": ApiErrorResponse, "description": "Unauthorized."},
            403: {"model": ApiErrorResponse, "description": "Forbidden."},
        },
    )
    async def search_audit(
        correlation_id: str | None = Query(default=None, description="Root correlation ID."),
        event_type: str | None = Query(default=None, description="Exact event type."),
        resource_type: str | None = Query(default=None, description="Resource classification."),
        resource_id: str | None = Query(default=None, description="Resource identifier."),
        limit: int = Query(default=100, ge=1, le=500, description="Max results to return."),
        offset: int = Query(default=0, ge=0, description="Result offset."),
        tenant_context: Any = Depends(tenant_dependency),
        session: AsyncSession = Depends(session_dependency),
    ) -> list[AuditEventResponse]:
        tenant_id = _extract_tenant_id(tenant_context)
        records = await query_service.search_events(
            session=session,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            limit=limit,
            offset=offset,
        )
        return [
            AuditEventResponse(
                audit_event_id=r.audit_event_id,
                schema_version=r.schema_version,
                tenant_id=r.tenant_id,
                actor_principal_id=r.actor_principal_id,
                actor_principal_type=r.actor_principal_type,
                authentication_method=r.authentication_method,
                event_type=r.event_type,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                outcome=r.outcome,
                correlation_id=r.correlation_id,
                details=r.details,
                recorded_at=r.recorded_at,
            )
            for r in records
        ]

    return router


__all__ = ["create_audit_router"]
