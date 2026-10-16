"""Audit query service for tenant-scoped, chronological event reconstruction.

Part of Phase 6 Unified Vertical Slice (Stage L).
Enforces:
- Strict tenant isolation: queries are scoped strictly to the authenticated tenant.
- Chronological ordering by recorded_at ascending.
- Correlation search reconstructing end-to-end operational histories.
- Privacy preservation: excludes unredacted credentials or sensitive tokens.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEventRecord


class AuditEventResponse(BaseModel):
    """Public read model for canonical audit events."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_event_id: UUID
    schema_version: int
    tenant_id: str | None
    actor_principal_id: str | None = None
    actor_principal_type: str | None = None
    authentication_method: str | None = None
    event_type: str
    resource_type: str | None = None
    resource_id: str | None = None
    outcome: str | None = None
    correlation_id: str | None = None
    details: dict[str, Any]
    recorded_at: datetime


class AuditQueryService:
    """Read-only service for chronological audit event search."""

    async def search_events(
        self,
        session: AsyncSession,
        tenant_id: str,
        correlation_id: str | None = None,
        event_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEventRecord]:
        """Query audit records within tenant boundary ordered chronologically."""
        if not tenant_id or not str(tenant_id).strip():
            raise ValueError("tenant_id is required for audit search")

        stmt = select(AuditEventRecord).where(AuditEventRecord.tenant_id == tenant_id)

        if correlation_id:
            stmt = stmt.where(AuditEventRecord.correlation_id == correlation_id)
        if event_type:
            stmt = stmt.where(AuditEventRecord.event_type == event_type)
        if resource_type:
            stmt = stmt.where(AuditEventRecord.resource_type == resource_type)
        if resource_id:
            stmt = stmt.where(AuditEventRecord.resource_id == resource_id)

        stmt = (
            stmt.order_by(AuditEventRecord.recorded_at.asc())
            .offset(offset)
            .limit(limit)
        )

        res = await session.execute(stmt)
        return list(res.scalars().all())


__all__ = [
    "AuditEventResponse",
    "AuditQueryService",
]
