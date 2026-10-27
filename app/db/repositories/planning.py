"""Planning domain repositories bound to SQLAlchemy AsyncSession.

Part of Phase 2A.
Adheres strictly to the repository foundation:
- Externally supplied AsyncSession from Unit of Work.
- No transaction ownership: never commits, rolls back, or closes session.
- Tenant-scoped: all queries require explicit tenant_id.
- Aggregate-oriented persistence for goals, plans, tasks, dependencies, milestones, and risks.
"""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.db.models.planning import (
    GoalRecord,
    MilestoneRecord,
    PlanRecord,
    TaskDependencyRecord,
    TaskRecord,
    TaskRiskRecord,
)
from app.db.repositories.base import SqlAlchemyRepository
from app.task_intelligence.progress import TaskProgressUpdate
from app.task_intelligence.risks import TaskRiskItem
from app.task_intelligence.schemas import (
    DependencyDraft,
    GoalCreate,
    MilestoneDraft,
    TaskDraft,
    TaskProgressStatus,
)


class GoalRepository(SqlAlchemyRepository):
    """Repository for managing canonical operational goals."""

    async def create(self, goal: GoalCreate, tenant_id: str) -> GoalRecord:
        """Create and stage a new canonical goal."""
        record = GoalRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            objective=goal.objective,
            scope=goal.scope,
            deadline=goal.deadline,
            target_window=goal.target_window.model_dump(mode="json")
            if goal.target_window
            else None,
            constraints=goal.constraints,
            resource_scope=goal.resource_scope,
            context_references=goal.context_references,
            priority_target=goal.priority_target,
            metadata_json=goal.metadata,
        )
        self.session.add(record)
        return record

    async def get_by_id(self, goal_id: UUID, tenant_id: str) -> GoalRecord | None:
        """Retrieve a goal by canonical UUID within tenant boundary."""
        stmt = select(GoalRecord).where(
            GoalRecord.id == goal_id,
            GoalRecord.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_goals(
        self, tenant_id: str, limit: int = 50, offset: int = 0
    ) -> list[GoalRecord]:
        """List goals for a tenant ordered by creation date."""
        stmt = (
            select(GoalRecord)
            .where(GoalRecord.tenant_id == tenant_id)
            .order_by(GoalRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class PlanRepository(SqlAlchemyRepository):
    """Repository for managing versioned plans and baselines."""

    async def create_plan(
        self,
        tenant_id: str,
        goal_id: UUID,
        plan_name: str,
        version_number: int,
        status: str = "DRAFT",
        is_baseline: bool = False,
        superseded_by_plan_id: UUID | None = None,
        baseline_hash: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> PlanRecord:
        """Create and stage a versioned plan entity."""
        record = PlanRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            goal_id=goal_id,
            version_number=version_number,
            plan_name=plan_name,
            status=status,
            is_baseline=is_baseline,
            superseded_by_plan_id=superseded_by_plan_id,
            baseline_hash=baseline_hash,
            metadata_json=metadata_json or {},
        )
        self.session.add(record)
        return record

    async def get_by_id(
        self,
        plan_id: UUID,
        tenant_id: str,
        load_relations: bool = False,
    ) -> PlanRecord | None:
        """Retrieve a plan by canonical UUID within tenant boundary."""
        stmt = select(PlanRecord).where(
            PlanRecord.id == plan_id,
            PlanRecord.tenant_id == tenant_id,
        )
        if load_relations:
            stmt = stmt.options(
                selectinload(PlanRecord.tasks),
                selectinload(PlanRecord.dependencies),
                selectinload(PlanRecord.milestones),
                selectinload(PlanRecord.risks),
            )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_latest_version_number(self, goal_id: UUID, tenant_id: str) -> int:
        """Return the highest version_number for a goal, or 0 if none exist."""
        stmt = select(func.max(PlanRecord.version_number)).where(
            PlanRecord.goal_id == goal_id,
            PlanRecord.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        max_ver = result.scalar()
        return max_ver if max_ver is not None else 0

    async def get_baseline_for_goal(self, goal_id: UUID, tenant_id: str) -> PlanRecord | None:
        """Retrieve the currently approved baseline plan for a goal if one exists."""
        stmt = select(PlanRecord).where(
            PlanRecord.goal_id == goal_id,
            PlanRecord.tenant_id == tenant_id,
            PlanRecord.is_baseline.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_plans(
        self,
        tenant_id: str,
        goal_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PlanRecord]:
        """List plans for a tenant, optionally filtered by goal_id."""
        stmt = (
            select(PlanRecord)
            .where(PlanRecord.tenant_id == tenant_id)
        )
        if goal_id is not None:
            stmt = stmt.where(PlanRecord.goal_id == goal_id)
        stmt = stmt.order_by(PlanRecord.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        plan_id: UUID,
        tenant_id: str,
        status: str,
        is_baseline: bool | None = None,
        baseline_hash: str | None = None,
    ) -> PlanRecord | None:
        """Update status and baseline flags for a plan."""
        plan = await self.get_by_id(plan_id, tenant_id)
        if not plan:
            return None

        plan.status = status
        if is_baseline is not None:
            plan.is_baseline = is_baseline
        if baseline_hash is not None:
            plan.baseline_hash = baseline_hash

        return plan


class TaskRepository(SqlAlchemyRepository):
    """Repository for managing canonical tasks, dependencies, milestones, and risks."""

    async def persist_plan_aggregates(
        self,
        tenant_id: str,
        plan_id: UUID,
        task_drafts: list[TaskDraft],
        dependency_drafts: list[DependencyDraft],
        milestone_drafts: list[MilestoneDraft],
        risks: list[TaskRiskItem] | None = None,
    ) -> tuple[dict[str, TaskRecord], list[TaskDependencyRecord], list[MilestoneRecord]]:
        """Persist all tasks, dependencies, milestones, and risks for a plan version.

        Resolves temporary plan-local IDs to canonical UUIDs in memory.
        """
        # 1. Create milestones
        milestone_record_map: dict[str, MilestoneRecord] = {}
        for md in milestone_drafts:
            m_rec = MilestoneRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                plan_id=plan_id,
                temporary_id=md.temporary_id,
                name=md.name,
                description=md.description,
                target_date=md.target_date,
            )
            self.session.add(m_rec)
            milestone_record_map[md.temporary_id] = m_rec

        # 2. First pass for tasks: mint UUIDs and instantiate records
        task_record_map: dict[str, TaskRecord] = {}
        for td in task_drafts:
            effort_json = td.effort_estimate.model_dump(mode="json") if td.effort_estimate else None
            target_milestone = (
                milestone_record_map.get(td.milestone_reference) if td.milestone_reference else None
            )

            t_rec = TaskRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                plan_id=plan_id,
                temporary_id=td.temporary_id,
                title=td.title,
                description=td.description,
                wbs_code=td.wbs_code,
                owner_role=td.owner_role,
                owner_reference=td.owner_reference,
                estimated_duration_minutes=td.estimated_duration_minutes,
                effort_estimate_json=effort_json,
                acceptance_criteria=td.acceptance_criteria,
                work_category=td.work_category,
                priority=str(td.priority),
                status="TODO",
                progress_percentage=0,
                milestone_id=target_milestone.id if target_milestone else None,
            )
            task_record_map[td.temporary_id] = t_rec
            self.session.add(t_rec)

        # 3. Second pass for tasks: resolve parent_id
        for td in task_drafts:
            if td.parent_id and td.parent_id in task_record_map:
                task_record_map[td.temporary_id].parent_task_id = task_record_map[td.parent_id].id

        # Flush milestones and tasks so their IDs exist in PostgreSQL before inserting dependencies
        await self.session.flush()

        # 4. Create dependencies
        dependency_records: list[TaskDependencyRecord] = []
        for dd in dependency_drafts:
            pred = task_record_map.get(dd.predecessor_id)
            succ = task_record_map.get(dd.successor_id)
            if pred and succ:
                dep_rec = TaskDependencyRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    predecessor_id=pred.id,
                    successor_id=succ.id,
                    dependency_type=str(dd.dependency_type),
                    lag_minutes=dd.lag_minutes,
                )
                self.session.add(dep_rec)
                dependency_records.append(dep_rec)

        # 5. Create risks if provided
        if risks:
            for r in risks:
                target_task = task_record_map.get(r.task_id)
                if target_task:
                    risk_rec = TaskRiskRecord(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        plan_id=plan_id,
                        task_id=target_task.id,
                        category=str(r.category),
                        severity=str(r.severity),
                        description=r.description,
                        is_evaluable=r.is_evaluable,
                        detected_at=r.detected_at,
                    )
                    self.session.add(risk_rec)

        return task_record_map, dependency_records, list(milestone_record_map.values())

    async def get_task_by_id(self, task_id: UUID, tenant_id: str) -> TaskRecord | None:
        """Retrieve task by canonical UUID within tenant boundary."""
        stmt = select(TaskRecord).where(
            TaskRecord.id == task_id,
            TaskRecord.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_tasks_for_plan(self, plan_id: UUID, tenant_id: str) -> list[TaskRecord]:
        """Retrieve all tasks belonging to a plan."""
        stmt = (
            select(TaskRecord)
            .where(
                TaskRecord.plan_id == plan_id,
                TaskRecord.tenant_id == tenant_id,
            )
            .order_by(TaskRecord.temporary_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_dependencies_for_plan(
        self, plan_id: UUID, tenant_id: str
    ) -> list[TaskDependencyRecord]:
        """Retrieve all dependencies belonging to a plan."""
        stmt = select(TaskDependencyRecord).where(
            TaskDependencyRecord.plan_id == plan_id,
            TaskDependencyRecord.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_milestones_for_plan(
        self, plan_id: UUID, tenant_id: str
    ) -> list[MilestoneRecord]:
        """Retrieve all milestones belonging to a plan."""
        stmt = (
            select(MilestoneRecord)
            .where(
                MilestoneRecord.plan_id == plan_id,
                MilestoneRecord.tenant_id == tenant_id,
            )
            .order_by(MilestoneRecord.temporary_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_task_progress(
        self,
        task_id: UUID,
        tenant_id: str,
        update_data: TaskProgressUpdate,
    ) -> TaskRecord | None:
        """Update task progress, actual duration, and blocker status."""
        task = await self.get_task_by_id(task_id, tenant_id)
        if not task:
            return None

        if update_data.status is not None:
            task.status = str(update_data.status)
            if update_data.status == TaskProgressStatus.COMPLETED:
                task.progress_percentage = 100

        if update_data.progress_percentage is not None:
            if (
                task.status == TaskProgressStatus.COMPLETED.value
                and update_data.progress_percentage != 100
            ):
                raise ValueError("Completed tasks must have 100% progress_percentage.")
            task.progress_percentage = update_data.progress_percentage
        elif task.status == TaskProgressStatus.COMPLETED.value:
            task.progress_percentage = 100
        if update_data.actual_duration_minutes is not None:
            task.actual_duration_minutes = update_data.actual_duration_minutes
        if update_data.blocked_reason is not None:
            task.blocked_reason = update_data.blocked_reason
        if update_data.completion_evidence is not None:
            task.completion_evidence = update_data.completion_evidence
            flag_modified(task, "effort_estimate_json")

        return task

    async def get_completed_actual_durations(
        self,
        tenant_id: str,
        work_category: str | None = None,
    ) -> list[int]:
        """Retrieve actual durations of completed tasks for statistical recalibration.

        Filters strictly by tenant_id, status='COMPLETED', non-null and positive
        actual_duration_minutes. Optionally filters by work_category.
        """
        conditions = [
            TaskRecord.tenant_id == tenant_id,
            TaskRecord.status == "COMPLETED",
            TaskRecord.actual_duration_minutes.is_not(None),
            TaskRecord.actual_duration_minutes > 0,
        ]
        if work_category:
            conditions.append(TaskRecord.work_category == work_category)

        stmt = (
            select(TaskRecord.actual_duration_minutes)
            .where(*conditions)
            .order_by(TaskRecord.actual_duration_minutes.asc())
        )
        result = await self.session.execute(stmt)
        return [int(val) for val in result.scalars().all() if val is not None]

