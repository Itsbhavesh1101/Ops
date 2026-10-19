"""Scheduling application service coordinating jobs, solves, persistence, and state transitions.

Part of Phase 4A Generic Scheduling Core.
Enforces:
- Tenant-scoped job creation and input hashing.
- Atomic worker job claiming via SELECT FOR UPDATE SKIP LOCKED.
- Deterministic CP-SAT solve execution and telemetry recording.
- Normal INFEASIBLE handling (never FAILED, zero empty-success schedules).
- Feasible schedule projection with zero hard violations.
- State machine transition rules (REQUESTED -> SOLVING -> FEASIBLE/INFEASIBLE, FEASIBLE -> REVIEW).
"""

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate, AuditEventOutcome
from app.core.telemetry.scheduling import get_scheduling_telemetry
from app.db.models.scheduling import (
    ScheduleAssignmentRecord,
    ScheduleJobRecord,
    ScheduleRecord,
)
from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, create_event_envelope
from app.scheduling.constraints.models import TimeWindow
from app.scheduling.hashing import compute_input_snapshot_hash
from app.scheduling.policy import (
    DefaultSchedulePublishPolicy,
    PublishAuthorizationError,
    SchedulePublishPolicy,
    validate_publish_preconditions,
)
from app.scheduling.repair.models import ScheduleRepairRequest
from app.scheduling.repair.scope import RepairScopeBuilder
from app.scheduling.schemas import (
    LockedAssignment,
    ScheduleStatus,
    SchedulingRequest,
)
from app.scheduling.solvers.cpsat import CpSatScheduler
from app.scheduling.solvers.models import SolverResult
from app.scheduling.states import (
    ScheduleJobStatus,
    ScheduleLifecycleStatus,
    validate_job_transition,
    validate_schedule_transition,
)


class SchedulingServiceError(Exception):
    """Base exception for scheduling service operations."""


class JobNotFoundError(SchedulingServiceError):
    """Raised when a requested schedule job is not found."""


class ScheduleNotFoundError(SchedulingServiceError):
    """Raised when a requested schedule record is not found."""


class ScheduleNotPublishedError(SchedulingServiceError):
    """Raised when an operation requires a PUBLISHED schedule but it is in another state."""


class ScheduleAlreadyPublishedError(SchedulingServiceError):
    """Raised when attempting to republish an already published schedule."""


class HardConstraintViolationError(SchedulingServiceError):
    """Raised if a proposed feasible schedule contains any hard constraint violation."""


class SchedulingService:
    """Application service for scheduling jobs, execution, and lifecycle."""

    def __init__(self, scheduler: CpSatScheduler | None = None) -> None:
        self.scheduler = scheduler or CpSatScheduler()
        self.telemetry = get_scheduling_telemetry()

    async def submit_job(
        self,
        session: AsyncSession,
        tenant_id: str,
        request: SchedulingRequest,
        schedule_key: str = "default",
    ) -> ScheduleJobRecord:
        """Submit a new asynchronous scheduling job with canonical input hashing."""
        input_hash = compute_input_snapshot_hash(request)
        snapshot_dict = request.model_dump(mode="json")

        job = ScheduleJobRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_key=schedule_key,
            status=ScheduleJobStatus.REQUESTED.value,
            input_snapshot=snapshot_dict,
            input_snapshot_hash=input_hash,
            solver_metadata={},
            diagnostics=[],
        )
        session.add(job)
        await session.flush()
        return job

    async def get_job_by_snapshot_hash(
        self,
        session: AsyncSession,
        tenant_id: str,
        snapshot_hash: str,
    ) -> ScheduleJobRecord | None:
        """Retrieve an existing job matching tenant and input snapshot hash."""
        stmt = (
            select(ScheduleJobRecord)
            .where(
                ScheduleJobRecord.tenant_id == tenant_id,
                ScheduleJobRecord.input_snapshot_hash == snapshot_hash,
            )
            .order_by(ScheduleJobRecord.created_at.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def claim_jobs(
        self,
        session: AsyncSession,
        worker_id: str,
        batch_size: int = 5,
        max_concurrent_per_tenant: int | None = None,
    ) -> list[ScheduleJobRecord]:
        """Claim pending jobs atomically using FOR UPDATE SKIP LOCKED with tenant fairness."""
        if max_concurrent_per_tenant is not None and max_concurrent_per_tenant > 0:
            candidate_stmt = (
                select(ScheduleJobRecord)
                .where(ScheduleJobRecord.status == ScheduleJobStatus.REQUESTED.value)
                .order_by(ScheduleJobRecord.created_at.asc())
                .limit(batch_size * 10)
                .with_for_update(skip_locked=True)
            )
            candidate_result = await session.execute(candidate_stmt)
            candidates = list(candidate_result.scalars().all())

            active_counts_stmt = (
                select(ScheduleJobRecord.tenant_id, func.count(ScheduleJobRecord.id))
                .where(ScheduleJobRecord.status == ScheduleJobStatus.SOLVING.value)
                .group_by(ScheduleJobRecord.tenant_id)
            )
            active_counts_result = await session.execute(active_counts_stmt)
            active_per_tenant: dict[str, int] = dict(active_counts_result.all())

            now = datetime.now(UTC)
            claimed: list[ScheduleJobRecord] = []
            for job in candidates:
                if len(claimed) >= batch_size:
                    break
                active_count = active_per_tenant.get(job.tenant_id, 0)
                if active_count >= max_concurrent_per_tenant:
                    continue
                validate_job_transition(job.status, ScheduleJobStatus.SOLVING)
                job.status = ScheduleJobStatus.SOLVING.value
                job.claimed_by = worker_id
                job.claimed_at = now
                active_per_tenant[job.tenant_id] = active_count + 1
                claimed.append(job)

            await session.flush()
            return claimed
        else:
            stmt = (
                select(ScheduleJobRecord)
                .where(ScheduleJobRecord.status == ScheduleJobStatus.REQUESTED.value)
                .order_by(ScheduleJobRecord.created_at.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            jobs = list(result.scalars().all())

            now = datetime.now(UTC)
            for job in jobs:
                validate_job_transition(job.status, ScheduleJobStatus.SOLVING)
                job.status = ScheduleJobStatus.SOLVING.value
                job.claimed_by = worker_id
                job.claimed_at = now

            await session.flush()
            return jobs

    async def reclaim_stale_jobs(
        self,
        session: AsyncSession,
        lease_timeout_seconds: float = 300.0,
    ) -> list[ScheduleJobRecord]:
        """Reclaim abandoned SOLVING jobs whose worker lease has expired."""
        cutoff = datetime.now(UTC) - timedelta(seconds=lease_timeout_seconds)
        stmt = (
            select(ScheduleJobRecord)
            .where(
                ScheduleJobRecord.status == ScheduleJobStatus.SOLVING.value,
                ScheduleJobRecord.claimed_at <= cutoff,
            )
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        stale_jobs = list(result.scalars().all())

        for job in stale_jobs:
            validate_job_transition(job.status, ScheduleJobStatus.REQUESTED)
            job.status = ScheduleJobStatus.REQUESTED.value
            job.claimed_by = None
            job.claimed_at = None

        await session.flush()
        return stale_jobs

    async def execute_job_solve(
        self,
        session: AsyncSession,
        job_id: UUID,
    ) -> SolverResult:
        """Execute CP-SAT solve for a claimed job and persist results."""
        stmt = (
            select(ScheduleJobRecord)
            .where(ScheduleJobRecord.id == job_id)
            .with_for_update()
        )
        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        if not job:
            raise JobNotFoundError(f"Schedule job {job_id} not found.")

        # Reconstruct request from immutable snapshot
        job_start_mono = time.monotonic()
        request = SchedulingRequest.model_validate(job.input_snapshot)

        with self.telemetry.span_solve():
            start_time = datetime.now(UTC)
            try:
                result = self.scheduler.solve(request)
            except Exception as exc:
                validate_job_transition(job.status, ScheduleJobStatus.FAILED)
                job.status = ScheduleJobStatus.FAILED.value
                job.failure_message = f"Execution crash: {exc}"
                job.completed_at = datetime.now(UTC)
                await session.flush()
                raise

            duration_sec = (datetime.now(UTC) - start_time).total_seconds()
            self.telemetry.record_result(
                status=result.status.value,
                duration_seconds=duration_sec,
                assignment_count=len(result.assignments),
            )

        persist_start_mono = time.monotonic()

        if result.status == ScheduleStatus.FEASIBLE:
            # Hard violation invariant check
            if result.score_components and result.score_components.hard_constraint_violations > 0:
                validate_job_transition(job.status, ScheduleJobStatus.FAILED)
                job.status = ScheduleJobStatus.FAILED.value
                job.failure_message = "Solver produced FEASIBLE result with hard violations > 0"
                job.completed_at = datetime.now(UTC)
                await session.flush()
                raise HardConstraintViolationError(
                    "Feasible schedule candidate contains hard constraint violations!"
                )

            validate_job_transition(job.status, ScheduleJobStatus.FEASIBLE)
            job.status = ScheduleJobStatus.FEASIBLE.value
            job.solver_metadata = result.solver_metadata
            job.completed_at = datetime.now(UTC)

            # Determine next version
            ver_stmt = (
                select(func.coalesce(func.max(ScheduleRecord.version), 0))
                .where(
                    ScheduleRecord.tenant_id == job.tenant_id,
                    ScheduleRecord.schedule_key == job.schedule_key,
                )
            )
            max_ver = (await session.execute(ver_stmt)).scalar() or 0
            new_version = max_ver + 1

            schedule = ScheduleRecord(
                id=uuid4(),
                tenant_id=job.tenant_id,
                schedule_key=job.schedule_key,
                version=new_version,
                status=ScheduleLifecycleStatus.FEASIBLE.value,
                source_job_id=job.id,
                input_snapshot_hash=job.input_snapshot_hash,
                solver_profile_version=request.profile.version,
                solver_metadata=result.solver_metadata,
                score_components=result.score_components.model_dump(mode="json")
                if result.score_components
                else {},
            )
            session.add(schedule)
            await session.flush()

            for asgn in result.assignments:
                assignment_rec = ScheduleAssignmentRecord(
                    id=uuid4(),
                    tenant_id=job.tenant_id,
                    schedule_id=schedule.id,
                    activity_id=asgn.activity_id,
                    resource_id=asgn.resource_id,
                    start_at=asgn.start_at,
                    end_at=asgn.end_at,
                    is_locked=asgn.is_locked,
                    metadata_json=asgn.metadata,
                )
                session.add(assignment_rec)

        elif result.status == ScheduleStatus.INFEASIBLE:
            # INFEASIBLE is normal outcome! No schedule created, not FAILED!
            validate_job_transition(job.status, ScheduleJobStatus.INFEASIBLE)
            job.status = ScheduleJobStatus.INFEASIBLE.value
            job.solver_metadata = result.solver_metadata
            job.diagnostics = [d.model_dump(mode="json") for d in result.diagnostics]
            job.completed_at = datetime.now(UTC)

        elif result.status == ScheduleStatus.BUDGET_EXHAUSTED:
            # Search budget exhausted without finding solution (not INFEASIBLE, not FAILED)
            validate_job_transition(job.status, ScheduleJobStatus.BUDGET_EXHAUSTED)
            job.status = ScheduleJobStatus.BUDGET_EXHAUSTED.value
            job.solver_metadata = result.solver_metadata
            job.failure_message = (
                f"Solver search budget exhausted ({request.profile.max_solve_seconds}s) "
                "without finding a feasible solution or proving infeasibility."
            )
            job.diagnostics = [d.model_dump(mode="json") for d in result.diagnostics]
            job.completed_at = datetime.now(UTC)

        elif result.status == ScheduleStatus.FAILED:
            validate_job_transition(job.status, ScheduleJobStatus.FAILED)
            job.status = ScheduleJobStatus.FAILED.value
            job.solver_metadata = result.solver_metadata
            job.failure_message = result.solver_metadata.get("error", "Solver model failure")
            job.completed_at = datetime.now(UTC)

        await session.flush()
        persist_duration = time.monotonic() - persist_start_mono
        self.telemetry.record_persistence_duration(persist_duration)
        job_duration = time.monotonic() - job_start_mono
        self.telemetry.record_job_duration(job_duration, status=result.status.value)
        return result

    async def transition_to_review(
        self,
        session: AsyncSession,
        tenant_id: str,
        schedule_id: UUID,
        correlation_id: str | None = None,
    ) -> ScheduleRecord:
        """Transition a FEASIBLE schedule candidate to REVIEW and emit schedule.generated."""
        stmt = (
            select(ScheduleRecord)
            .options(selectinload(ScheduleRecord.assignments))
            .where(
                ScheduleRecord.id == schedule_id,
                ScheduleRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        sched = res.scalar_one_or_none()
        if not sched:
            raise ScheduleNotFoundError(f"Schedule {schedule_id} not found for tenant {tenant_id}.")

        validate_schedule_transition(sched.status, ScheduleLifecycleStatus.REVIEW)
        sched.status = ScheduleLifecycleStatus.REVIEW.value

        # Emit canonical schedule.generated event
        outbox_repo = OutboxRepository(session)
        envelope = create_event_envelope(
            EventCreate(
                event_type="schedule.generated",
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                subject_type="schedule",
                subject_id=str(sched.id),
                payload={
                    "schedule_id": str(sched.id),
                    "schedule_version": sched.version,
                    "source_job_id": str(sched.source_job_id),
                    "solver_profile_version": sched.solver_profile_version,
                    "input_snapshot_hash": sched.input_snapshot_hash,
                    "assignment_count": len(sched.assignments),
                },
            )
        )
        await outbox_repo.enqueue(envelope)

        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type="schedule.in_review",
                resource_type="schedule",
                resource_id=str(sched.id),
                outcome=AuditEventOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "schedule_id": str(sched.id),
                    "version": sched.version,
                    "status": sched.status,
                },
            )
        )

        await session.flush()
        return sched

    async def publish_schedule(
        self,
        session: AsyncSession,
        tenant_id: str,
        schedule_id: UUID,
        principal: str,
        publish_policy: SchedulePublishPolicy | None = None,
        correlation_id: str | None = None,
    ) -> ScheduleRecord:
        """Publish a candidate schedule in REVIEW atomically with audit and outbox events."""
        stmt = (
            select(ScheduleRecord)
            .options(selectinload(ScheduleRecord.assignments))
            .where(
                ScheduleRecord.id == schedule_id,
                ScheduleRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        sched = res.scalar_one_or_none()
        if not sched:
            raise ScheduleNotFoundError(f"Schedule {schedule_id} not found for tenant {tenant_id}.")

        policy = publish_policy or DefaultSchedulePublishPolicy()
        if not policy.authorize_publish(
            principal=principal,
            tenant_id=tenant_id,
            schedule=sched,
            action="publish",
        ):
            raise PublishAuthorizationError("Principal is not authorized to publish this schedule.")

        validate_publish_preconditions(sched)
        validate_schedule_transition(sched.status, ScheduleLifecycleStatus.PUBLISHED)

        now = datetime.now(UTC)
        sched.status = ScheduleLifecycleStatus.PUBLISHED.value
        sched.published_at = now
        sched.published_by = principal

        old_sched: ScheduleRecord | None = None
        if sched.supersedes_id:
            old_stmt = (
                select(ScheduleRecord)
                .where(
                    ScheduleRecord.id == sched.supersedes_id,
                    ScheduleRecord.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            old_res = await session.execute(old_stmt)
            old_sched = old_res.scalar_one_or_none()
            if old_sched and old_sched.status == ScheduleLifecycleStatus.PUBLISHED.value:
                validate_schedule_transition(old_sched.status, ScheduleLifecycleStatus.SUPERSEDED)
                old_sched.status = ScheduleLifecycleStatus.SUPERSEDED.value

        # Append audit for publication
        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                actor_principal_id=principal,
                actor_principal_type="HUMAN",
                authentication_method="OIDC",
                event_type="schedule.published",
                resource_type="schedule",
                resource_id=str(sched.id),
                outcome=AuditEventOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "schedule_id": str(sched.id),
                    "version": sched.version,
                    "published_by": principal,
                    "source_job_id": str(sched.source_job_id),
                    "supersedes_id": str(sched.supersedes_id) if sched.supersedes_id else None,
                    "assignment_count": len(sched.assignments),
                },
            )
        )

        if old_sched:
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=principal,
                    actor_principal_type="HUMAN",
                    authentication_method="OIDC",
                    event_type="schedule.superseded",
                    resource_type="schedule",
                    resource_id=str(old_sched.id),
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=correlation_id,
                    details={
                        "old_schedule_id": str(old_sched.id),
                        "old_version": old_sched.version,
                        "superseded_by_id": str(sched.id),
                        "superseded_by_version": sched.version,
                    },
                )
            )

        # Enqueue canonical outbox events
        outbox_repo = OutboxRepository(session)
        pub_envelope = create_event_envelope(
            EventCreate(
                event_type="schedule.published",
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                subject_type="schedule",
                subject_id=str(sched.id),
                payload={
                    "schedule_id": str(sched.id),
                    "schedule_version": sched.version,
                    "version": sched.version,
                    "published_at": now.isoformat(),
                    "published_by": principal,
                    "source_job_id": str(sched.source_job_id),
                    "assignment_count": len(sched.assignments),
                    "assignments": [
                        {
                            "session_id": a.activity_id,
                            "activity_id": a.activity_id,
                            "resource_id": a.resource_id,
                            "room_resource_id": a.resource_id,
                            "start_at": a.start_at.isoformat() if a.start_at else None,
                            "end_at": a.end_at.isoformat() if a.end_at else None,
                        }
                        for a in sched.assignments
                    ],
                    "recipients": ["faculty@school.edu"],
                },
            )
        )
        await outbox_repo.enqueue(pub_envelope)

        if old_sched:
            change_envelope = create_event_envelope(
                EventCreate(
                    event_type="schedule.changed",
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    subject_type="schedule",
                    subject_id=str(sched.id),
                    payload={
                        "schedule_id": str(sched.id),
                        "old_schedule_id": str(old_sched.id),
                        "old_schedule_version": old_sched.version,
                        "old_version": old_sched.version,
                        "new_schedule_id": str(sched.id),
                        "new_schedule_version": sched.version,
                        "new_version": sched.version,
                        "version": sched.version,
                        "change_reason": sched.repair_reason or "repair",
                        "repair_reason": sched.repair_reason or "resource.unavailable",
                        "movement_score": sched.score_components.get("movement_penalty", 0)
                        if sched.score_components
                        else 0,
                        "recipients": ["faculty@school.edu"],
                    },
                )
            )
            await outbox_repo.enqueue(change_envelope)

        await session.flush()
        return sched

    async def publish_job(
        self,
        session: AsyncSession,
        tenant_id: str,
        job_id: UUID,
        principal: str,
        publish_policy: SchedulePublishPolicy | None = None,
        correlation_id: str | None = None,
    ) -> ScheduleRecord:
        """Resolve a solve job to its candidate schedule and publish it."""
        job = await self.get_job(session, tenant_id, job_id)
        if not job:
            raise JobNotFoundError(f"Schedule job {job_id} not found for tenant {tenant_id}.")

        # Find schedule candidate generated from this job
        stmt = (
            select(ScheduleRecord)
            .where(
                ScheduleRecord.tenant_id == tenant_id,
                ScheduleRecord.source_job_id == job_id,
            )
            .order_by(ScheduleRecord.version.desc())
            .limit(1)
        )
        res = await session.execute(stmt)
        sched = res.scalar_one_or_none()
        if not sched:
            raise ScheduleNotFoundError(
                f"No candidate schedule found for job {job_id} for tenant {tenant_id}."
            )

        if sched.status == ScheduleLifecycleStatus.FEASIBLE.value:
            await self.transition_to_review(
                session=session,
                tenant_id=tenant_id,
                schedule_id=sched.id,
                correlation_id=correlation_id,
            )

        return await self.publish_schedule(
            session=session,
            tenant_id=tenant_id,
            schedule_id=sched.id,
            principal=principal,
            publish_policy=publish_policy,
            correlation_id=correlation_id,
        )

    async def repair_schedule(
        self,
        session: AsyncSession,
        tenant_id: str,
        schedule_id: UUID,
        request: ScheduleRepairRequest,
        correlation_id: str | None = None,
    ) -> tuple[ScheduleJobRecord, ScheduleRecord | None]:
        """Perform targeted incremental repair on a published baseline schedule."""
        stmt = (
            select(ScheduleRecord)
            .options(selectinload(ScheduleRecord.assignments))
            .where(
                ScheduleRecord.id == schedule_id,
                ScheduleRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        baseline = res.scalar_one_or_none()
        if not baseline:
            raise ScheduleNotFoundError(f"Schedule {schedule_id} not found for tenant {tenant_id}.")

        if baseline.status != ScheduleLifecycleStatus.PUBLISHED.value:
            raise ScheduleNotPublishedError(
                f"Schedule {schedule_id} is in status '{baseline.status}', but must be "
                f"'{ScheduleLifecycleStatus.PUBLISHED.value}' for repair."
            )

        # Retrieve source job snapshot to reconstruct original problem definition
        base_job = await self.get_job(session, tenant_id, baseline.source_job_id)
        if not base_job:
            raise JobNotFoundError(
                f"Baseline job {baseline.source_job_id} not found for schedule {schedule_id}."
            )

        base_request = SchedulingRequest.model_validate(base_job.input_snapshot)

        # 1. Build repair scope
        scope_builder = RepairScopeBuilder()
        scope = scope_builder.build_scope(
            activities=base_request.activities,
            baseline_assignments=baseline.assignments,
            constraints=base_request.constraints,
            repair_request=request,
        )

        # 2. Update resource blackouts for disruption
        updated_resources = []
        for r in base_request.resources:
            unavail_windows = list(r.unavailability_windows)
            if r.id in request.disrupted_resource_ids:
                if request.disrupted_interval_start and request.disrupted_interval_end:
                    unavail_windows.append(
                        TimeWindow(
                            start_at=request.disrupted_interval_start,
                            end_at=request.disrupted_interval_end,
                        )
                    )
            updated_resources.append(
                r.model_copy(update={"unavailability_windows": unavail_windows})
            )

        # 3. Update activities: freeze unaffected and hard-locked; apply movement penalty to movable
        asgn_map = {a.activity_id: a for a in baseline.assignments}
        updated_activities = []
        for act in base_request.activities:
            asgn = asgn_map.get(act.id)
            if not asgn:
                updated_activities.append(act)
                continue

            if act.id in scope.unaffected_activity_ids:
                # FREEZE unaffected assignment as hard constraint
                frozen_act = act.model_copy(
                    update={
                        "locked_assignment": LockedAssignment(
                            start_at=asgn.start_at,
                            end_at=asgn.end_at,
                            resource_ids=[asgn.resource_id],
                        ),
                        "allowed_windows": [
                            TimeWindow(start_at=asgn.start_at, end_at=asgn.end_at)
                        ],
                        "required_resource_ids": [asgn.resource_id],
                    }
                )
                updated_activities.append(frozen_act)
            elif act.id in scope.locked_baseline_activity_ids:
                # EXPLICIT HARD LOCK in baseline must remain locked!
                locked_act = act.model_copy(
                    update={
                        "locked_assignment": LockedAssignment(
                            start_at=asgn.start_at,
                            end_at=asgn.end_at,
                            resource_ids=[asgn.resource_id],
                        ),
                        "allowed_windows": [
                            TimeWindow(start_at=asgn.start_at, end_at=asgn.end_at)
                        ],
                        "required_resource_ids": [asgn.resource_id],
                    }
                )
                updated_activities.append(locked_act)
            else:
                # Movable affected assignment: apply movement penalty against baseline resource
                movable_act = act.model_copy(
                    update={
                        "locked_assignment": None,
                        "baseline_assignment": LockedAssignment(
                            resource_ids=[asgn.resource_id]
                        ),
                    }
                )
                updated_activities.append(movable_act)

        # 4. Solver profile with mandatory movement penalty weight
        effective_movement_weight = (
            request.movement_weight
            or (request.profile.soft_weights.movement_weight if request.profile else None)
            or base_request.profile.soft_weights.movement_weight
        )
        if effective_movement_weight <= 0:
            raise ValueError(
                "Repair solve requires an explicit movement_weight > 0 to "
                "minimize baseline displacement."
            )

        active_profile = request.profile or base_request.profile
        new_soft_weights = active_profile.soft_weights.model_copy(
            update={"movement_weight": effective_movement_weight}
        )
        updated_profile = active_profile.model_copy(
            update={"soft_weights": new_soft_weights}
        )

        repaired_request = SchedulingRequest(
            tenant_id=tenant_id,
            schedule_key=baseline.schedule_key,
            horizon_start=base_request.horizon_start,
            horizon_end=base_request.horizon_end,
            timezone=base_request.timezone,
            activities=updated_activities,
            resources=updated_resources,
            constraints=base_request.constraints,
            profile=updated_profile,
        )

        input_hash = compute_input_snapshot_hash(repaired_request)
        snapshot_dict = repaired_request.model_dump(mode="json")
        snapshot_dict["repair_metadata"] = {
            "baseline_schedule_id": str(baseline.id),
            "baseline_version": baseline.version,
            "scope": scope.to_metadata_dict(),
            "reason": request.reason,
            "effective_movement_weight": effective_movement_weight,
            "solver_profile_version": updated_profile.version,
            "soft_weights": updated_profile.soft_weights.model_dump(mode="json"),
        }

        # 5. Create repair solve job
        job = ScheduleJobRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            schedule_key=baseline.schedule_key,
            status=ScheduleJobStatus.SOLVING.value,
            input_snapshot=snapshot_dict,
            input_snapshot_hash=input_hash,
            solver_metadata={},
            diagnostics=[],
            claimed_by="repair_worker",
            claimed_at=datetime.now(UTC),
        )
        session.add(job)
        await session.flush()

        audit_repo = AuditRepository(session)
        await audit_repo.append(
            AuditEventCreate(
                tenant_id=tenant_id,
                event_type="repair.requested",
                resource_type="schedule",
                resource_id=str(baseline.id),
                outcome=AuditEventOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "baseline_schedule_id": str(baseline.id),
                    "baseline_version": baseline.version,
                    "job_id": str(job.id),
                    "repair_scope": scope.to_metadata_dict(),
                    "reason": request.reason,
                },
            )
        )

        # 6. Execute solve
        with self.telemetry.span_solve():
            start_time = datetime.now(UTC)
            result = self.scheduler.solve(repaired_request)
            duration_sec = (datetime.now(UTC) - start_time).total_seconds()
            self.telemetry.record_result(
                status=result.status.value,
                duration_seconds=duration_sec,
                assignment_count=len(result.assignments),
            )

        candidate_schedule: ScheduleRecord | None = None

        if result.status == ScheduleStatus.FEASIBLE:
            if result.score_components and result.score_components.hard_constraint_violations > 0:
                job.status = ScheduleJobStatus.FAILED.value
                job.failure_message = (
                    "Solver produced FEASIBLE repair result with hard violations > 0"
                )
                job.completed_at = datetime.now(UTC)
                await session.flush()
                raise HardConstraintViolationError(
                    "Repair feasible candidate contains hard constraint violations!"
                )

            job.status = ScheduleJobStatus.FEASIBLE.value
            job.solver_metadata = result.solver_metadata
            job.completed_at = datetime.now(UTC)

            # Determine next version
            ver_stmt = (
                select(func.coalesce(func.max(ScheduleRecord.version), 0))
                .where(
                    ScheduleRecord.tenant_id == tenant_id,
                    ScheduleRecord.schedule_key == baseline.schedule_key,
                )
            )
            max_ver = (await session.execute(ver_stmt)).scalar() or 0
            new_version = max_ver + 1

            candidate_schedule = ScheduleRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                schedule_key=baseline.schedule_key,
                version=new_version,
                status=ScheduleLifecycleStatus.REVIEW.value,  # Enters REVIEW!
                source_job_id=job.id,
                input_snapshot_hash=input_hash,
                solver_profile_version=updated_profile.version,
                solver_metadata=result.solver_metadata,
                score_components=result.score_components.model_dump(mode="json")
                if result.score_components
                else {},
                supersedes_id=baseline.id,
                repair_reason=request.reason,
                repair_scope=scope.to_metadata_dict(),
            )
            session.add(candidate_schedule)
            await session.flush()

            for cand_asgn in result.assignments:
                rec = ScheduleAssignmentRecord(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    schedule_id=candidate_schedule.id,
                    activity_id=cand_asgn.activity_id,
                    resource_id=cand_asgn.resource_id,
                    start_at=cand_asgn.start_at,
                    end_at=cand_asgn.end_at,
                    is_locked=cand_asgn.is_locked,
                    metadata_json=cand_asgn.metadata,
                )
                session.add(rec)

            # Enqueue schedule.generated for review candidate
            outbox_repo = OutboxRepository(session)
            envelope = create_event_envelope(
                EventCreate(
                    event_type="schedule.generated",
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    subject_type="schedule",
                    subject_id=str(candidate_schedule.id),
                    payload={
                        "schedule_id": str(candidate_schedule.id),
                        "schedule_version": candidate_schedule.version,
                        "source_job_id": str(job.id),
                        "solver_profile_version": candidate_schedule.solver_profile_version,
                        "input_snapshot_hash": candidate_schedule.input_snapshot_hash,
                        "assignment_count": len(result.assignments),
                    },
                )
            )
            await outbox_repo.enqueue(envelope)

            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    event_type="repair.candidate_generated",
                    resource_type="schedule",
                    resource_id=str(candidate_schedule.id),
                    outcome=AuditEventOutcome.SUCCESS,
                    correlation_id=correlation_id,
                    details={
                        "candidate_schedule_id": str(candidate_schedule.id),
                        "candidate_version": candidate_schedule.version,
                        "supersedes_id": str(baseline.id),
                        "status": candidate_schedule.status,
                        "repair_scope": scope.to_metadata_dict(),
                    },
                )
            )

        elif result.status == ScheduleStatus.INFEASIBLE:
            job.status = ScheduleJobStatus.INFEASIBLE.value
            job.solver_metadata = result.solver_metadata
            job.diagnostics = [d.model_dump(mode="json") for d in result.diagnostics]
            job.completed_at = datetime.now(UTC)

        elif result.status == ScheduleStatus.BUDGET_EXHAUSTED:
            job.status = ScheduleJobStatus.BUDGET_EXHAUSTED.value
            job.solver_metadata = result.solver_metadata
            job.failure_message = (
                f"Repair solver search budget exhausted ({updated_profile.max_solve_seconds}s) "
                "without finding a feasible repair solution."
            )
            job.diagnostics = [d.model_dump(mode="json") for d in result.diagnostics]
            job.completed_at = datetime.now(UTC)

        await session.flush()
        return job, candidate_schedule

    async def get_job(
        self,
        session: AsyncSession,
        tenant_id: str,
        job_id: UUID,
    ) -> ScheduleJobRecord | None:
        """Retrieve a schedule job ensuring tenant isolation."""
        stmt = select(ScheduleJobRecord).where(
            ScheduleJobRecord.id == job_id,
            ScheduleJobRecord.tenant_id == tenant_id,
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_schedule(
        self,
        session: AsyncSession,
        tenant_id: str,
        schedule_id: UUID,
    ) -> ScheduleRecord | None:
        """Retrieve a schedule record ensuring tenant isolation."""
        stmt = select(ScheduleRecord).where(
            ScheduleRecord.id == schedule_id,
            ScheduleRecord.tenant_id == tenant_id,
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_schedule_assignments(
        self,
        session: AsyncSession,
        tenant_id: str,
        schedule_id: UUID,
    ) -> list[ScheduleAssignmentRecord]:
        """Retrieve assignments for a schedule ensuring tenant isolation."""
        # First verify tenant ownership of schedule
        sched = await self.get_schedule(session, tenant_id, schedule_id)
        if not sched:
            return []

        stmt = (
            select(ScheduleAssignmentRecord)
            .where(
                ScheduleAssignmentRecord.schedule_id == schedule_id,
                ScheduleAssignmentRecord.tenant_id == tenant_id,
            )
            .order_by(ScheduleAssignmentRecord.start_at.asc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())
