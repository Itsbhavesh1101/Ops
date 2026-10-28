"""PostgreSQL persistence repository for task SLA states (Phase DQ-4 AD-DQ4-02).

Enforces:
- Strict tenant isolation via (tenant_id, task_id).
- System of Record persistence replacing process-local in-memory state.
- Row-level locking via SELECT ... FOR UPDATE SKIP LOCKED during evaluation.
- Deterministic state recovery across worker restarts.
- Transactional atomicity: uncommitted evaluations roll back cleanly if outer transactions fail.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.sla import TaskSlaStateRecord
from app.db.repositories.base import SqlAlchemyRepository
from app.workflow.sla.models import SlaStatus, SlaTaskStateNotFoundError, TaskSlaState


class SlaRepository(SqlAlchemyRepository):
    """Repository managing persistence and queries for task_sla_states."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize SlaRepository with session."""
        super().__init__(session)

    @staticmethod
    def to_model(record: TaskSlaStateRecord) -> TaskSlaState:
        """Map SQLAlchemy TaskSlaStateRecord to domain TaskSlaState model."""
        return TaskSlaState(
            id=record.id,
            task_id=record.task_id,
            instance_id=record.instance_id,
            tenant_id=record.tenant_id,
            policy_key=record.policy_key,
            status=SlaStatus(record.status),
            created_at=record.created_at,
            warning_at=record.warning_at,
            breach_at=record.breach_at,
            paused_at=record.paused_at,
            total_paused_seconds=record.total_paused_seconds,
            remaining_warning_seconds=record.remaining_warning_seconds,
            remaining_breach_seconds=record.remaining_breach_seconds,
            warning_fired_at=record.warning_fired_at,
            breach_fired_at=record.breach_fired_at,
        )

    async def get_task_sla(
        self,
        tenant_id: str,
        task_id: UUID,
        *,
        for_update: bool = False,
    ) -> TaskSlaStateRecord | None:
        """Retrieve task SLA state by tenant_id and task_id with optional row-level locking."""
        stmt = select(TaskSlaStateRecord).where(
            TaskSlaStateRecord.tenant_id == tenant_id,
            TaskSlaStateRecord.task_id == task_id,
        )
        if for_update:
            bind = getattr(self.session, "bind", None) or getattr(
                getattr(self.session, "sync_session", None), "bind", None
            )
            dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
            if dialect_name == "postgresql":
                stmt = stmt.with_for_update()

        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create_task_sla(
        self,
        state: TaskSlaState,
    ) -> TaskSlaStateRecord:
        """Persist a new task SLA state within the active transaction."""
        record = TaskSlaStateRecord(
            id=state.id,
            tenant_id=state.tenant_id,
            task_id=state.task_id,
            instance_id=state.instance_id,
            policy_key=state.policy_key,
            status=state.status.value,
            created_at=state.created_at,
            warning_at=state.warning_at,
            breach_at=state.breach_at,
            paused_at=state.paused_at,
            total_paused_seconds=state.total_paused_seconds,
            remaining_warning_seconds=state.remaining_warning_seconds,
            remaining_breach_seconds=state.remaining_breach_seconds,
            warning_fired_at=state.warning_fired_at,
            breach_fired_at=state.breach_fired_at,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def update_task_sla(
        self,
        state: TaskSlaState,
        *,
        for_update: bool = False,
    ) -> TaskSlaStateRecord:
        """Update existing task SLA state in place.

        Strictly tenant-scoped. Raises SlaTaskStateNotFoundError if the SLA state
        record does not exist in the specified tenant. Never falls back across tenants.
        """
        record = await self.get_task_sla(
            tenant_id=state.tenant_id,
            task_id=state.task_id,
            for_update=for_update,
        )
        if record is None:
            raise SlaTaskStateNotFoundError(
                f"Task SLA state for task '{state.task_id}' not found in "
                f"tenant '{state.tenant_id}'."
            )

        record.status = state.status.value
        record.warning_at = state.warning_at
        record.breach_at = state.breach_at
        record.paused_at = state.paused_at
        record.total_paused_seconds = state.total_paused_seconds
        record.remaining_warning_seconds = state.remaining_warning_seconds
        record.remaining_breach_seconds = state.remaining_breach_seconds
        record.warning_fired_at = state.warning_fired_at
        record.breach_fired_at = state.breach_fired_at
        record.updated_at = datetime.now(UTC)

        await self.session.flush()
        return record

    async def list_evaluable_slas(
        self,
        tenant_id: str,
        as_of: datetime,
        limit: int = 100,
    ) -> list[TaskSlaStateRecord]:
        """Query active SLAs eligible for warning or breach evaluation with row-level locking."""
        conditions = and_(
            TaskSlaStateRecord.tenant_id == tenant_id,
            TaskSlaStateRecord.status.in_([SlaStatus.RUNNING.value, SlaStatus.WARNING.value]),
            or_(
                TaskSlaStateRecord.warning_at <= as_of,
                TaskSlaStateRecord.breach_at <= as_of,
            ),
        )

        stmt = (
            select(TaskSlaStateRecord)
            .where(conditions)
            .order_by(TaskSlaStateRecord.breach_at.asc(), TaskSlaStateRecord.warning_at.asc())
            .limit(limit)
        )

        # Apply FOR UPDATE SKIP LOCKED on PostgreSQL
        bind = getattr(self.session, "bind", None)
        dialect_name = getattr(bind.dialect, "name", "") if bind is not None else ""
        if dialect_name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_tenant(
        self,
        tenant_id: str,
        limit: int = 100,
    ) -> list[TaskSlaStateRecord]:
        """List all task SLA records for a given tenant."""
        stmt = (
            select(TaskSlaStateRecord)
            .where(TaskSlaStateRecord.tenant_id == tenant_id)
            .order_by(TaskSlaStateRecord.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


__all__ = ["SlaRepository"]
