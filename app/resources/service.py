"""Resource registry and availability service layer.

Part of Phase 4A Generic Scheduling Core.
Provides canonical resource lookup, tenant scoping, and availability intervals.
"""

from datetime import datetime
from uuid import UUID, uuid4

from app.resources.schemas import (
    AvailabilityIntervalCreate,
    AvailabilityIntervalRead,
    ResourceCreate,
    ResourceRead,
)


class ResourceNotFoundError(Exception):
    """Raised when a requested resource does not exist in the tenant scope."""


class ResourceService:
    """Manages canonical resources and availability intervals."""

    def __init__(self) -> None:
        # In-memory store for unit test/adapter decoupling; real persistence handled by UoW
        self._resources: dict[tuple[str, UUID], ResourceRead] = {}
        self._availabilities: dict[tuple[str, UUID], list[AvailabilityIntervalRead]] = {}

    def register_resource(self, tenant_id: str, payload: ResourceCreate) -> ResourceRead:
        """Register a new resource in the tenant registry."""
        resource_id = uuid4()
        now = datetime.now().astimezone()
        record = ResourceRead(
            id=resource_id,
            tenant_id=tenant_id,
            resource_key=payload.resource_key,
            type=payload.type,
            display_label=payload.display_label,
            capacity=payload.capacity,
            skills=payload.skills,
            attributes=payload.attributes,
            active=payload.active,
            created_at=now,
            updated_at=now,
        )
        self._resources[(tenant_id, resource_id)] = record
        return record

    def get_resource(self, tenant_id: str, resource_id: UUID) -> ResourceRead:
        """Retrieve a single resource by UUID within the tenant."""
        key = (tenant_id, resource_id)
        if key not in self._resources:
            raise ResourceNotFoundError(
                f"Resource '{resource_id}' not found in tenant '{tenant_id}'."
            )
        return self._resources[key]

    def list_resources(
        self, tenant_id: str, active_only: bool = True
    ) -> list[ResourceRead]:
        """List all resources within tenant scope."""
        records = [
            r for (t_id, _), r in self._resources.items()
            if t_id == tenant_id and (not active_only or r.active)
        ]
        return sorted(records, key=lambda r: r.resource_key)

    def record_availability(
        self, tenant_id: str, payload: AvailabilityIntervalCreate
    ) -> AvailabilityIntervalRead:
        """Record an availability or blackout interval for a resource."""
        # Ensure resource exists in tenant
        self.get_resource(tenant_id, payload.resource_id)

        interval_id = uuid4()
        now = datetime.now().astimezone()
        interval = AvailabilityIntervalRead(
            id=interval_id,
            tenant_id=tenant_id,
            resource_id=payload.resource_id,
            start_at=payload.start_at,
            end_at=payload.end_at,
            interval_type=payload.interval_type,
            reason=payload.reason,
            created_at=now,
        )
        key = (tenant_id, payload.resource_id)
        if key not in self._availabilities:
            self._availabilities[key] = []
        self._availabilities[key].append(interval)
        return interval

    def get_availability(
        self,
        tenant_id: str,
        resource_id: UUID | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[AvailabilityIntervalRead]:
        """Get availability intervals for a resource (or all tenant resources) in window."""
        if resource_id is not None:
            self.get_resource(tenant_id, resource_id)
            intervals = list(self._availabilities.get((tenant_id, resource_id), []))
        else:
            intervals = [
                iv
                for (t_id, _), iv_list in self._availabilities.items()
                if t_id == tenant_id
                for iv in iv_list
            ]

        if start_at is None and end_at is None:
            return sorted(intervals, key=lambda i: i.start_at)

        filtered = []
        for interval in intervals:
            # Check overlap with [start_at, end_at)
            if start_at and interval.end_at <= start_at:
                continue
            if end_at and interval.start_at >= end_at:
                continue
            filtered.append(interval)
        return sorted(filtered, key=lambda i: i.start_at)

    async def get_intervals(
        self,
        tenant_id: str,
        resource_id: UUID | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[AvailabilityIntervalRead]:
        """Async accessor for resource availability intervals."""
        return self.get_availability(tenant_id, resource_id, start_at, end_at)
