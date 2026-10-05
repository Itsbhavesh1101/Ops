"""Canonical workflow database repositories (Phase 3A).

Provides transactional persistence operations for:
- WorkflowDefinitionRecord
- WorkflowInstanceRecord
- WorkflowTaskMappingRecord
"""

from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workflow import (
    WorkflowDefinitionRecord,
    WorkflowInstanceRecord,
    WorkflowTaskMappingRecord,
)


class WorkflowDefinitionRepository:
    """Repository managing workflow definition metadata and versioning."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, record: WorkflowDefinitionRecord) -> WorkflowDefinitionRecord:
        """Insert and flush a new workflow definition record."""
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_id(
        self,
        tenant_id: str,
        definition_id: UUID,
    ) -> WorkflowDefinitionRecord | None:
        """Fetch definition by tenant ID and UUID."""
        stmt = select(WorkflowDefinitionRecord).where(
            WorkflowDefinitionRecord.tenant_id == tenant_id,
            WorkflowDefinitionRecord.id == definition_id,
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_key_and_version(
        self,
        tenant_id: str,
        workflow_key: str,
        version: int,
    ) -> WorkflowDefinitionRecord | None:
        """Fetch definition by tenant ID, workflow key, and version number."""
        stmt = select(WorkflowDefinitionRecord).where(
            WorkflowDefinitionRecord.tenant_id == tenant_id,
            WorkflowDefinitionRecord.workflow_key == workflow_key,
            WorkflowDefinitionRecord.version == version,
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_latest_active_by_key(
        self,
        tenant_id: str,
        workflow_key: str,
    ) -> WorkflowDefinitionRecord | None:
        """Fetch highest active version of a definition by workflow key."""
        stmt = (
            select(WorkflowDefinitionRecord)
            .where(
                WorkflowDefinitionRecord.tenant_id == tenant_id,
                WorkflowDefinitionRecord.workflow_key == workflow_key,
                WorkflowDefinitionRecord.is_active.is_(True),
            )
            .order_by(desc(WorkflowDefinitionRecord.version))
            .limit(1)
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_next_version(
        self,
        tenant_id: str,
        workflow_key: str,
    ) -> int:
        """Calculate next sequential version integer for a given workflow key."""
        stmt = select(func.max(WorkflowDefinitionRecord.version)).where(
            WorkflowDefinitionRecord.tenant_id == tenant_id,
            WorkflowDefinitionRecord.workflow_key == workflow_key,
        )
        res = await self._session.execute(stmt)
        max_ver = res.scalar_one_or_none()
        return (max_ver or 0) + 1

    async def list_by_tenant(
        self,
        tenant_id: str,
        workflow_key: str | None = None,
        active_only: bool = True,
    ) -> list[WorkflowDefinitionRecord]:
        """List all definitions for a tenant, optionally filtered by key."""
        stmt = select(WorkflowDefinitionRecord).where(
            WorkflowDefinitionRecord.tenant_id == tenant_id
        )
        if workflow_key:
            stmt = stmt.where(WorkflowDefinitionRecord.workflow_key == workflow_key)
        if active_only:
            stmt = stmt.where(WorkflowDefinitionRecord.is_active.is_(True))
        stmt = stmt.order_by(
            WorkflowDefinitionRecord.workflow_key,
            desc(WorkflowDefinitionRecord.version),
        )
        res = await self._session.execute(stmt)
        return list(res.scalars().all())


class WorkflowInstanceRepository:
    """Repository managing workflow instance projections."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, record: WorkflowInstanceRecord) -> WorkflowInstanceRecord:
        """Insert and flush a new workflow instance record."""
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_id(
        self,
        tenant_id: str,
        instance_id: UUID,
    ) -> WorkflowInstanceRecord | None:
        """Fetch instance projection by tenant ID and UUID."""
        stmt = select(WorkflowInstanceRecord).where(
            WorkflowInstanceRecord.tenant_id == tenant_id,
            WorkflowInstanceRecord.id == instance_id,
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_engine_id(
        self,
        tenant_id: str,
        engine_instance_id: str,
    ) -> WorkflowInstanceRecord | None:
        """Fetch instance projection by Flowable processInstanceId."""
        stmt = select(WorkflowInstanceRecord).where(
            WorkflowInstanceRecord.tenant_id == tenant_id,
            WorkflowInstanceRecord.engine_instance_id == engine_instance_id,
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: str,
        workflow_key: str | None = None,
        status: str | None = None,
        business_reference: str | None = None,
    ) -> list[WorkflowInstanceRecord]:
        """List workflow instances for a tenant with optional key/status/reference filters."""
        stmt = select(WorkflowInstanceRecord).where(WorkflowInstanceRecord.tenant_id == tenant_id)
        if workflow_key:
            stmt = stmt.where(WorkflowInstanceRecord.workflow_key == workflow_key)
        if status:
            stmt = stmt.where(WorkflowInstanceRecord.status == status)
        if business_reference:
            stmt = stmt.where(WorkflowInstanceRecord.business_reference == business_reference)
        stmt = stmt.order_by(desc(WorkflowInstanceRecord.created_at))
        res = await self._session.execute(stmt)
        return list(res.scalars().all())


class WorkflowTaskRepository:
    """Repository managing human workflow task mappings."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, record: WorkflowTaskMappingRecord) -> WorkflowTaskMappingRecord:
        """Insert and flush a new task mapping record."""
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_by_id(
        self,
        tenant_id: str,
        task_id: UUID,
    ) -> WorkflowTaskMappingRecord | None:
        """Fetch task mapping by tenant ID and UUID."""
        stmt = select(WorkflowTaskMappingRecord).where(
            WorkflowTaskMappingRecord.tenant_id == tenant_id,
            WorkflowTaskMappingRecord.id == task_id,
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_engine_id(
        self,
        tenant_id: str,
        engine_task_id: str,
    ) -> WorkflowTaskMappingRecord | None:
        """Fetch task mapping by Flowable taskId."""
        stmt = select(WorkflowTaskMappingRecord).where(
            WorkflowTaskMappingRecord.tenant_id == tenant_id,
            WorkflowTaskMappingRecord.engine_task_id == engine_task_id,
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    get_by_engine_task_id = get_by_engine_id

    async def list_by_instance(
        self,
        tenant_id: str,
        workflow_instance_id: UUID,
        status: str | None = None,
    ) -> list[WorkflowTaskMappingRecord]:
        """List task mappings for a specific workflow instance."""
        stmt = select(WorkflowTaskMappingRecord).where(
            WorkflowTaskMappingRecord.tenant_id == tenant_id,
            WorkflowTaskMappingRecord.workflow_instance_id == workflow_instance_id,
        )
        if status:
            stmt = stmt.where(WorkflowTaskMappingRecord.status == status)
        stmt = stmt.order_by(WorkflowTaskMappingRecord.created_at)
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        assignee: str | None = None,
    ) -> list[WorkflowTaskMappingRecord]:
        """List task mappings for a tenant with optional status/assignee filters."""
        stmt = select(WorkflowTaskMappingRecord).where(
            WorkflowTaskMappingRecord.tenant_id == tenant_id
        )
        if status:
            stmt = stmt.where(WorkflowTaskMappingRecord.status == status)
        if assignee:
            stmt = stmt.where(WorkflowTaskMappingRecord.assignee == assignee)
        stmt = stmt.order_by(desc(WorkflowTaskMappingRecord.created_at))
        res = await self._session.execute(stmt)
        return list(res.scalars().all())
