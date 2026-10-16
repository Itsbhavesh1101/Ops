"""Business event handler consuming canonical task.completed events to unlock scheduling demand.

Part of Phase 6 Unified Vertical Slice.
Enforces:
- Canonical task.completed consumption.
- Unlocks scheduling demand when readiness tasks/milestones are completed.
- Idempotency: checks whether scheduling job for plan/schedule_key already exists.
- Utilizes the frozen Phase-5 OR-Tools academic constraint compiler.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.db.models.scheduling import ScheduleJobRecord
from app.events.schemas import EventEnvelope
from app.scheduling.education.dataset import load_education_dataset
from app.scheduling.education.ortools_compiler import AcademicConstraintCompiler
from app.scheduling.service import SchedulingService

READINESS_TASK_IDS: set[str] = {
    "T4_RESOURCE_READINESS",
    "task-readiness",
    "READINESS_MILESTONE",
}


class TaskCompletedSchedulingHandler:
    """Consumer handler for canonical task.completed event to trigger scheduling demand."""

    def __init__(
        self,
        scheduling_service: SchedulingService | None = None,
        compiler: AcademicConstraintCompiler | None = None,
    ) -> None:
        self.scheduling_service = scheduling_service or SchedulingService()
        self.compiler = compiler or AcademicConstraintCompiler()

    async def handle(
        self,
        *,
        envelope: EventEnvelope,
        session: AsyncSession,
    ) -> ScheduleJobRecord | None:
        """Handle task.completed event idempotently within transaction.

        Flow:
        1. Validate tenant_id and task temporary_id.
        2. Check if the task is an approved readiness trigger.
        3. Check idempotency: does a schedule job already exist for this plan?
        4. Compile canonical education benchmark demand via AcademicConstraintCompiler.
        5. Submit asynchronous scheduling job via SchedulingService.
        """
        tenant_id = envelope.tenant_id
        if not tenant_id:
            return None

        payload = envelope.payload
        temporary_id = payload.get("temporary_id") or payload.get("task_id")
        plan_id = payload.get("plan_id")
        unlocks_scheduling = payload.get("unlocks_scheduling", False)

        # Only trigger scheduling if task is a known readiness milestone/task
        if temporary_id not in READINESS_TASK_IDS and not unlocks_scheduling:
            return None

        schedule_key = f"exam-plan-{plan_id}" if plan_id else "grade-10-semester-exam"

        # Idempotency check: query if a job already exists for this tenant and schedule_key
        stmt = (
            select(ScheduleJobRecord)
            .where(
                ScheduleJobRecord.tenant_id == tenant_id,
                ScheduleJobRecord.schedule_key == schedule_key,
            )
            .limit(1)
        )
        res = await session.execute(stmt)
        existing_job = res.scalar_one_or_none()
        if existing_job:
            # Duplicate task.completed delivery: idempotent return
            return existing_job

        # Load canonical Phase-5 education dataset
        dataset, raw_resources = load_education_dataset("FEASIBLE")

        # Compile into canonical SchedulingRequest
        request = self.compiler.compile_request(
            dataset=dataset,
            raw_resources=raw_resources,
        )

        # Submit scheduling job to the system of record
        job = await self.scheduling_service.submit_job(
            session=session,
            tenant_id=tenant_id,
            request=request,
            schedule_key=schedule_key,
        )

        # Append audit for schedule demand creation
        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_principal_id="e7-task-completed-handler",
                actor_principal_type="SERVICE",
                authentication_method="SERVICE_API_KEY",
                event_type="schedule.demand_created",
                resource_type="schedule_job",
                resource_id=str(job.id),
                outcome=AuditEventOutcome.SUCCESS,
                correlation_id=envelope.correlation_id,
                details={
                    "schedule_key": schedule_key,
                    "plan_id": plan_id,
                    "trigger_task_id": payload.get("task_id"),
                    "trigger_temporary_id": temporary_id,
                    "input_snapshot_hash": str(job.input_snapshot_hash),
                },
            )
        )

        return job


__all__ = ["TaskCompletedSchedulingHandler"]
