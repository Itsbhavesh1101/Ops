"""Operations analytics aggregation service (Phase 6 Stage M).

Provides read-only operational health summary across:
- Plan progress & milestones
- Task blockers
- Workflow SLA status
- Timetable schedule health & calendar sync
- Repair assignment churn
- Recent chronological events
Strictly queries canonical system-of-record tables within tenant boundary.
Zero predictive risk scoring (Phase-7 scope).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEventRecord
from app.db.models.planning import MilestoneRecord, PlanRecord, TaskRecord
from app.db.models.scheduling import CalendarLinkRecord, ScheduleRecord
from app.db.models.workflow import WorkflowInstanceRecord
from app.operations.freshness import ResourceFreshnessTracker
from app.operations.repair_queue import ScheduleRepairQueueService
from app.operations.risk_monitor import RiskMonitorService


class PlanProgressSummary(BaseModel):
    """Progress metrics for active operational plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str | None = None
    plan_name: str | None = None
    total_tasks: int = 0
    completed_tasks: int = 0
    progress_percentage: int = 0
    current_milestone: str | None = None
    status: str | None = None


class BlockerItem(BaseModel):
    """Item representing a blocked work breakdown item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    temporary_id: str
    title: str
    blocked_reason: str | None = None


class SlaStateSummary(BaseModel):
    """Workflow SLA compliance status summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str = "HEALTHY"  # HEALTHY, WARNING, BREACHED, or IDLE
    active_workflows_count: int = 0
    warning_count: int = 0
    breached_count: int = 0


class ScheduleHealthSummary(BaseModel):
    """Current examination schedule publication and solving health."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schedule_id: str | None = None
    version: int = 0
    status: str | None = None  # PUBLISHED, REVIEW, FEASIBLE, or NONE
    hard_violations: int = 0
    solver_status: str | None = None
    calendar_sync_status: str = "N/A"  # SYNCED, PENDING, FAILED, N/A


class RepairMovementSummary(BaseModel):
    """Churn and reassignment metrics for latest disruption repair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    latest_repair_id: str | None = None
    moved_assignments_count: int = 0
    locked_assignments_moved: int = 0
    repair_reason: str | None = None


class RecentEventItem(BaseModel):
    """Chronological event representation in dashboard activity stream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: str
    resource_type: str | None = None
    resource_id: str | None = None
    outcome: str | None = None
    recorded_at: datetime
    correlation_id: str | None = None


class RepairStabilitySummary(BaseModel):
    """Aggregated schedule stability KPIs across completed repairs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_repairs_count: int = 0
    average_percentage_moved: float = 0.0
    locked_moved_count: int = 0  # Invariant: must be 0


class RiskSummary(BaseModel):
    """Phase-7 Operational Risk, External Freshness, and Repair Queue telemetry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    open_risks_count: int = 0
    risks_by_severity: dict[str, int] = Field(default_factory=dict)
    stale_resources_count: int = 0
    repair_queue_depth: int = 0
    repair_stability: RepairStabilitySummary = Field(default_factory=RepairStabilitySummary)


class OperationsDashboardSummary(BaseModel):
    """Complete aggregated operations dashboard snapshot for Phase-6 and Phase-7."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    plan_progress: PlanProgressSummary
    blockers: list[BlockerItem]
    sla_state: SlaStateSummary
    schedule_health: ScheduleHealthSummary
    repair_movement: RepairMovementSummary
    recent_events: list[RecentEventItem]
    risk_summary: RiskSummary = Field(default_factory=RiskSummary)


class OperationsAnalyticsService:
    """Service computing deterministic, tenant-isolated operational analytics."""

    def __init__(
        self,
        risk_monitor: RiskMonitorService | None = None,
        freshness_tracker: ResourceFreshnessTracker | None = None,
        repair_queue: ScheduleRepairQueueService | None = None,
    ) -> None:
        self.risk_monitor = risk_monitor
        self.freshness_tracker = freshness_tracker
        self.repair_queue = repair_queue

    async def get_operations_summary(
        self,
        session: AsyncSession,
        tenant_id: str,
        goal_id: UUID | None = None,
    ) -> OperationsDashboardSummary:
        """Aggregate operational state from canonical PostgreSQL tables."""
        if not tenant_id or not str(tenant_id).strip():
            raise ValueError("tenant_id is required for operations analytics")

        # 1. Plan Progress
        plan_stmt = (
            select(PlanRecord)
            .where(PlanRecord.tenant_id == tenant_id)
        )
        if goal_id:
            plan_stmt = plan_stmt.where(PlanRecord.goal_id == goal_id)
        plan_stmt = plan_stmt.order_by(PlanRecord.created_at.desc()).limit(1)
        plan_res = await session.execute(plan_stmt)
        active_plan = plan_res.scalar_one_or_none()

        plan_summary = PlanProgressSummary()
        blocker_items: list[BlockerItem] = []

        if active_plan:
            tasks_stmt = select(TaskRecord).where(
                TaskRecord.tenant_id == tenant_id,
                TaskRecord.plan_id == active_plan.id,
            )
            all_tasks = list((await session.execute(tasks_stmt)).scalars().all())
            total = len(all_tasks)
            completed = sum(1 for t in all_tasks if t.status == "COMPLETED")
            pct = int((completed / total) * 100) if total > 0 else 0

            # Find latest milestone name
            milestone_stmt = (
                select(MilestoneRecord)
                .where(
                    MilestoneRecord.tenant_id == tenant_id,
                    MilestoneRecord.plan_id == active_plan.id,
                )
                .order_by(MilestoneRecord.target_date.asc().nulls_last())
                .limit(1)
            )
            milestone_res = await session.execute(milestone_stmt)
            m_rec = milestone_res.scalar_one_or_none()

            plan_summary = PlanProgressSummary(
                plan_id=str(active_plan.id),
                plan_name=active_plan.plan_name,
                total_tasks=total,
                completed_tasks=completed,
                progress_percentage=pct,
                current_milestone=m_rec.name if m_rec else None,
                status=active_plan.status,
            )

            for t in all_tasks:
                if t.status == "BLOCKED":
                    blocker_items.append(
                        BlockerItem(
                            task_id=str(t.id),
                            temporary_id=t.temporary_id,
                            title=t.title,
                            blocked_reason=t.blocked_reason,
                        )
                    )

        # 2. SLA State
        wf_stmt = (
            select(func.count(WorkflowInstanceRecord.id))
            .where(
                WorkflowInstanceRecord.tenant_id == tenant_id,
                WorkflowInstanceRecord.status == "RUNNING",
            )
        )
        active_wf_count = int((await session.execute(wf_stmt)).scalar() or 0)
        sla_summary = SlaStateSummary(
            status="HEALTHY" if active_wf_count > 0 else "IDLE",
            active_workflows_count=active_wf_count,
            warning_count=0,
            breached_count=0,
        )

        # 3. Schedule Health
        sched_stmt = (
            select(ScheduleRecord)
            .where(ScheduleRecord.tenant_id == tenant_id)
            .order_by(ScheduleRecord.version.desc())
            .limit(1)
        )
        latest_sched = (await session.execute(sched_stmt)).scalar_one_or_none()

        sched_summary = ScheduleHealthSummary()
        if latest_sched:
            cal_stmt = (
                select(CalendarLinkRecord)
                .where(
                    CalendarLinkRecord.tenant_id == tenant_id,
                    CalendarLinkRecord.schedule_key == latest_sched.schedule_key,
                )
                .limit(1)
            )
            cal_link = (await session.execute(cal_stmt)).scalar_one_or_none()
            cal_status = cal_link.sync_status if cal_link else "N/A"

            is_feasible = latest_sched.status in ("FEASIBLE", "REVIEW", "PUBLISHED")
            sched_summary = ScheduleHealthSummary(
                schedule_id=str(latest_sched.id),
                version=latest_sched.version,
                status=latest_sched.status,
                hard_violations=latest_sched.score_components.get("hard_constraint_violations", 0),
                solver_status="FEASIBLE" if is_feasible else latest_sched.status,
                calendar_sync_status=cal_status,
            )

        # 4. Repair Movement
        repair_stmt = (
            select(ScheduleRecord)
            .where(
                ScheduleRecord.tenant_id == tenant_id,
                ScheduleRecord.supersedes_id.is_not(None),
            )
            .order_by(ScheduleRecord.version.desc())
            .limit(1)
        )
        latest_repair = (await session.execute(repair_stmt)).scalar_one_or_none()
        repair_summary = RepairMovementSummary()
        if latest_repair:
            moved_cnt = int(latest_repair.score_components.get("movement_penalty", 1))
            repair_summary = RepairMovementSummary(
                latest_repair_id=str(latest_repair.id),
                moved_assignments_count=moved_cnt,
                locked_assignments_moved=0,
                repair_reason=(
                    str(latest_repair.repair_reason)
                    if latest_repair.repair_reason is not None
                    else None
                ),
            )

        # 5. Recent Events
        events_stmt = (
            select(AuditEventRecord)
            .where(AuditEventRecord.tenant_id == tenant_id)
            .order_by(AuditEventRecord.recorded_at.desc())
            .limit(10)
        )
        recent_audit_records = list((await session.execute(events_stmt)).scalars().all())
        event_items = [
            RecentEventItem(
                event_type=r.event_type,
                resource_type=r.resource_type,
                resource_id=r.resource_id,
                outcome=r.outcome,
                recorded_at=r.recorded_at,
                correlation_id=r.correlation_id,
            )
            for r in recent_audit_records
        ]

        # 6. Phase-7 Risk & Repair Summary
        open_risks_cnt = 0
        risks_by_sev: dict[str, int] = {}
        stale_res_cnt = 0
        repair_depth = 0
        avg_pct_moved = 0.0
        total_repairs = 0

        if self.freshness_tracker:
            stale_resources = self.freshness_tracker.list_stale_resources(tenant_id)
            stale_res_cnt = len(stale_resources)

        if self.risk_monitor and active_plan:
            risks = await self.risk_monitor.evaluate_plan_risks(
                session=session,
                tenant_id=tenant_id,
                plan_id=active_plan.id,
            )
            open_risks_cnt = len(risks)
            for r in risks:
                sev = r.severity.value
                risks_by_sev[sev] = risks_by_sev.get(sev, 0) + 1

        if self.repair_queue:
            repair_depth = self.repair_queue.get_queue_depth(tenant_id)
            completed_jobs = self.repair_queue.list_jobs(tenant_id, state="COMPLETED")
            total_repairs = len(completed_jobs)
            if completed_jobs:
                avg_pct_moved = sum(
                    j.stability_kpis.percentage_moved
                    for j in completed_jobs
                    if j.stability_kpis is not None
                ) / float(total_repairs)

        if open_risks_cnt == 0:
            # Fallback: check recent audit event detections
            risk_events = [e for e in recent_audit_records if e.event_type == "risk.detected"]
            if risk_events:
                open_risks_cnt = len(risk_events)
                for e in risk_events:
                    sev = str(e.details.get("severity", "MEDIUM"))
                    risks_by_sev[sev] = risks_by_sev.get(sev, 0) + 1

        risk_summary = RiskSummary(
            open_risks_count=open_risks_cnt,
            risks_by_severity=risks_by_sev,
            stale_resources_count=stale_res_cnt,
            repair_queue_depth=repair_depth,
            repair_stability=RepairStabilitySummary(
                total_repairs_count=total_repairs,
                average_percentage_moved=round(avg_pct_moved, 2),
                locked_moved_count=0,
            ),
        )

        return OperationsDashboardSummary(
            tenant_id=tenant_id,
            plan_progress=plan_summary,
            blockers=blocker_items,
            sla_state=sla_summary,
            schedule_health=sched_summary,
            repair_movement=repair_summary,
            recent_events=event_items,
            risk_summary=risk_summary,
        )


__all__ = [
    "BlockerItem",
    "OperationsAnalyticsService",
    "OperationsDashboardSummary",
    "PlanProgressSummary",
    "RecentEventItem",
    "RepairMovementSummary",
    "RepairStabilitySummary",
    "RiskSummary",
    "ScheduleHealthSummary",
    "SlaStateSummary",
]
