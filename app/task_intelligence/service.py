"""Canonical PlanService application domain service (Phase 2A).

Owns the lifecycle for goals, plans, tasks, dependencies, milestones,
plan baselines, and approval gating.
Coordinates transactional atomicity between planning state, audit logs,
and outbox events.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.audit.repository import AuditRepository
from app.audit.schemas import AuditEventCreate
from app.core.capacity.quotas import TenantQuotaManager
from app.db.models.planning import (
    GoalRecord,
    PlanRecord,
    TaskDependencyRecord,
    TaskRecord,
)
from app.db.models.scheduling import ResourceRecord
from app.db.repositories.planning import (
    GoalRepository,
    PlanRepository,
    TaskRepository,
)
from app.db.uow import SqlAlchemyUnitOfWork
from app.events.outbox.repository import OutboxRepository
from app.events.schemas import EventCreate, create_event_envelope
from app.task_intelligence.estimators import (
    DatabaseHistoricalMedianProvider,
    HeuristicEffortEstimator,
)
from app.task_intelligence.graph import (
    CriticalPathCalculator,
    TaskDependencyGraph,
)
from app.task_intelligence.graph.exceptions import GraphCycleError
from app.task_intelligence.graph.models import CPMScheduleResult
from app.task_intelligence.milestones import MilestoneHealthCalculator, MilestoneHealthResponse
from app.task_intelligence.planner.protocol import PlannerProtocol
from app.task_intelligence.progress import (
    TaskProgressStatus,
    TaskProgressUpdate,
    assert_valid_task_progress_transition,
)
from app.task_intelligence.recommender import (
    PlanRecommendationsResponse,
    RecommendationPolicyConfig,
    TaskRecommender,
)
from app.task_intelligence.risks import (
    DeterministicRiskDetector,
    RiskCategory,
    RiskSeverity,
    TaskRiskItem,
)
from app.task_intelligence.schemas import (
    EffortEstimateDraft,
    GoalCreate,
    GoalResponse,
    PlanSchema,
    PlanState,
    TaskDraft,
    ValidationResult,
    assert_valid_plan_transition,
)
from app.task_intelligence.validators import PlanValidator


class PlanServiceError(Exception):
    """Base exception for PlanService operations."""


class GoalNotFoundError(PlanServiceError):
    """Raised when a referenced goal is not found in the current tenant."""

    def __init__(self, goal_id: UUID):
        super().__init__(f"Goal '{goal_id}' not found.")
        self.goal_id = goal_id


class PlanNotFoundError(PlanServiceError):
    """Raised when a referenced plan is not found in the current tenant."""

    def __init__(self, plan_id: UUID):
        super().__init__(f"Plan '{plan_id}' not found.")
        self.plan_id = plan_id


class TaskNotFoundError(PlanServiceError):
    """Raised when a referenced task is not found in the current tenant."""

    def __init__(self, task_id: UUID):
        super().__init__(f"Task '{task_id}' not found.")
        self.task_id = task_id


class PlanApprovalError(PlanServiceError):
    """Raised when a plan cannot be approved due to policy or validation failure."""


def compute_plan_baseline_hash(
    tasks: list[TaskRecord],
    dependencies: list[TaskDependencyRecord],
) -> str:
    """Compute deterministic SHA-256 fingerprint for a plan baseline."""
    payload = {
        "tasks": sorted(
            [
                {
                    "tid": t.temporary_id,
                    "duration": t.estimated_duration_minutes,
                    "title": t.title,
                }
                for t in tasks
            ],
            key=lambda x: str(x["tid"]),
        ),
        "dependencies": sorted(
            [
                {
                    "pred": str(d.predecessor_id),
                    "succ": str(d.successor_id),
                    "lag": d.lag_minutes,
                }
                for d in dependencies
            ],
            key=lambda x: (str(x["pred"]), str(x["succ"])),
        ),
    }
    dump = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()


class PlanService:
    """Canonical domain service managing planning lifecycle and transactions."""

    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        validator: PlanValidator | None = None,
        planner: PlannerProtocol | None = None,
        context_builder: Any | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._validator = validator or PlanValidator()
        self._planner = planner
        self._context_builder = context_builder

    async def create_goal(
        self,
        tenant_id: str,
        goal_data: GoalCreate,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> GoalResponse:
        """Create and persist a canonical operational goal with audit trail."""
        actor = actor_id or "system"
        uow = self._uow_factory()

        async with uow:
            goal_repo = GoalRepository(uow.session)
            audit_repo = AuditRepository(uow.session)
            outbox_repo = OutboxRepository(uow.session)

            goal_record = await goal_repo.create(goal_data, tenant_id)

            # Atomic outbox domain event: goal.created
            envelope = create_event_envelope(
                EventCreate(
                    event_type="goal.created",
                    event_version=1,
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    subject_type="goal",
                    subject_id=str(goal_record.id),
                    payload={
                        "goal_id": str(goal_record.id),
                        "objective": goal_record.objective,
                        "scope": goal_record.scope,
                    },
                )
            )
            await outbox_repo.enqueue(envelope)

            # Audit goal creation
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=actor,
                    actor_principal_type="SERVICE" if actor == "system" else "HUMAN",
                    authentication_method="SERVICE_API_KEY" if actor == "system" else "OIDC",
                    event_type="goal.created",
                    resource_type="goal",
                    resource_id=str(goal_record.id),
                    outcome="SUCCESS",
                    correlation_id=correlation_id,
                    details={"objective": goal_data.objective[:100]},
                )
            )

            await uow.commit()

            return GoalResponse(
                id=goal_record.id,
                tenant_id=goal_record.tenant_id,
                objective=goal_record.objective,
                scope=goal_record.scope,
                deadline=goal_record.deadline,
                target_window=goal_data.target_window,
                constraints=goal_record.constraints,
                resource_scope=goal_record.resource_scope,
                context_references=goal_record.context_references,
                priority_target=goal_record.priority_target,
                metadata=goal_record.metadata_json,
                created_at=goal_record.created_at,
                updated_at=goal_record.updated_at,
            )

    @staticmethod
    def _to_goal_response(record: GoalRecord) -> GoalResponse:
        from app.task_intelligence.schemas import TargetWindow

        tw = TargetWindow.model_validate(record.target_window) if record.target_window else None
        return GoalResponse(
            id=record.id,
            tenant_id=record.tenant_id,
            objective=record.objective,
            scope=record.scope,
            deadline=record.deadline,
            target_window=tw,
            constraints=record.constraints,
            resource_scope=record.resource_scope,
            context_references=record.context_references,
            priority_target=record.priority_target,
            metadata=record.metadata_json,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def get_goal(self, tenant_id: str, goal_id: UUID) -> GoalResponse:
        """Retrieve a canonical goal ensuring tenant isolation."""
        uow = self._uow_factory()
        async with uow:
            goal_repo = GoalRepository(uow.session)
            record = await goal_repo.get_by_id(goal_id, tenant_id)
            if not record:
                raise GoalNotFoundError(goal_id)
            return self._to_goal_response(record)

    async def list_goals(
        self, tenant_id: str, limit: int = 50, offset: int = 0
    ) -> list[GoalResponse]:
        """List canonical goals for a tenant."""
        uow = self._uow_factory()
        async with uow:
            goal_repo = GoalRepository(uow.session)
            records = await goal_repo.list_goals(tenant_id, limit=limit, offset=offset)
            return [self._to_goal_response(r) for r in records]

    async def list_plans(
        self,
        tenant_id: str,
        goal_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PlanRecord]:
        """List plans for a tenant, optionally filtered by goal_id."""
        uow = self._uow_factory()
        async with uow:
            plan_repo = PlanRepository(uow.session)
            plans = await plan_repo.list_plans(
                tenant_id, goal_id=goal_id, limit=limit, offset=offset
            )
            uow.session.expunge_all()
            return plans

    async def get_plan_tasks(self, tenant_id: str, plan_id: UUID) -> list[TaskRecord]:
        """Retrieve tasks belonging to a plan ensuring tenant isolation."""
        uow = self._uow_factory()
        async with uow:
            plan_repo = PlanRepository(uow.session)
            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)
            task_repo = TaskRepository(uow.session)
            tasks = await task_repo.get_tasks_for_plan(plan_id, tenant_id)
            uow.session.expunge_all()
            return tasks

    async def get_plan_milestones(
        self, tenant_id: str, plan_id: UUID
    ) -> list[MilestoneHealthResponse]:
        """Retrieve milestones belonging to a plan with derived health rollups (FR-TI-06)."""
        uow = self._uow_factory()
        async with uow:
            plan_repo = PlanRepository(uow.session)
            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)
            task_repo = TaskRepository(uow.session)
            milestones = await task_repo.get_milestones_for_plan(plan_id, tenant_id)
            tasks = await task_repo.get_tasks_for_plan(plan_id, tenant_id)

            tasks_by_milestone: dict[UUID, list[TaskRecord]] = {m.id: [] for m in milestones}
            for t in tasks:
                if t.milestone_id and t.milestone_id in tasks_by_milestone:
                    tasks_by_milestone[t.milestone_id].append(t)

            now = datetime.now(UTC)
            return [
                MilestoneHealthCalculator.evaluate_milestone_health(
                    milestone=m,
                    member_tasks=tasks_by_milestone[m.id],
                    now=now,
                )
                for m in milestones
            ]

    async def generate_plan(
        self,
        tenant_id: str,
        goal_id: UUID,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[PlanRecord, PlanSchema, ValidationResult]:
        """Orchestrate AI plan proposal, deterministic validation, bounded repair,
        and persistence as GENERATED.

        Flow (Section 50):
        trusted goal
        -> authorized Context Builder
        -> PydanticAI planner proposal
        -> PlanSchema
        -> deterministic validation (and bounded targeted repair)
        -> persist GENERATED plan through PlanService
        -> plan.generated audit/outbox events emitted.

        Important: Plan status remains GENERATED until subsequent deterministic validation.
        """
        uow = self._uow_factory()
        async with uow:
            goal_repo = GoalRepository(uow.session)
            goal_record = await goal_repo.get_by_id(goal_id, tenant_id)
            if not goal_record:
                raise GoalNotFoundError(goal_id)

            goal_input = GoalCreate(
                objective=goal_record.objective,
                scope=goal_record.scope,
                constraints=goal_record.constraints,
                resource_scope=goal_record.resource_scope,
                context_references=goal_record.context_references,
                priority_target=goal_record.priority_target,
            )

        # 1. Context Builder
        cb = self._context_builder
        if cb is None:
            from app.task_intelligence.context import ContextBuilder

            cb = ContextBuilder()

        from app.task_intelligence.context import AssemblyStatus, ContextAssemblyRequest

        context_request = ContextAssemblyRequest(
            tenant_id=tenant_id,
            goal_id=goal_id,
            correlation_id=correlation_id,
            objective=goal_input.objective,
        )
        context_res = await cb.build_context(context_request)
        if context_res.assembly_status == AssemblyStatus.BLOCKED:
            raise PlanServiceError(
                f"Context assembly blocked for goal '{goal_id}': {context_res.warnings}"
            )

        # 2. Planner proposal (PydanticAiPlanner or configured planner)
        pl = self._planner
        if pl is None:
            from app.task_intelligence.planner import PydanticAiPlanner

            pl = PydanticAiPlanner(validator=self._validator)

        if hasattr(pl, "generate_plan_with_result"):
            gen_result = await pl.generate_plan_with_result(
                goal=goal_input,
                context=context_res.model_context.model_dump(exclude_none=True),
            )
            proposed_plan = gen_result.plan
            val_res = gen_result.validation_result or self._validator.validate(proposed_plan)
        else:
            proposed_plan = await pl.generate_plan(
                goal=goal_input,
                context=context_res.model_context.model_dump(exclude_none=True),
            )
            val_res = self._validator.validate(proposed_plan)

        # 3. Persist GENERATED plan
        plan_record = await self.persist_generated_plan(
            tenant_id=tenant_id,
            goal_id=goal_id,
            plan_schema=proposed_plan,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

        return plan_record, proposed_plan, val_res

    async def calibrate_plan_effort(
        self,
        tenant_id: str,
        plan_schema: PlanSchema,
        task_repo: TaskRepository,
    ) -> PlanSchema:
        """Calibrate plan task effort estimates using historical execution data (FR-TI-04).

        Precedence Hierarchy:
        1. Explicit human override (retains confidence 1.0, basis='human_override').
        2. Historical median provider derived from completed PostgreSQL tasks for this tenant
           (confidence 0.85, basis='historical_median').
        3. Category heuristic default (confidence 0.7, basis='category_heuristic:<cat>').
        4. Global fallback duration (confidence 0.5, basis='default_fallback').
        """
        historical_provider = await DatabaseHistoricalMedianProvider.load_for_tenant(
            task_repo=task_repo,
            tenant_id=tenant_id,
        )
        estimator = HeuristicEffortEstimator(historical_provider=historical_provider)

        for task in plan_schema.tasks:
            human_override = None
            if task.effort_estimate and task.effort_estimate.is_manual_override:
                human_override = float(task.effort_estimate.value)

            estimate_res = estimator.estimate(
                title=task.title,
                work_category=task.work_category,
                human_override=human_override,
            )
            task.estimated_duration_minutes = int(estimate_res.value)
            task.effort_estimate = EffortEstimateDraft(
                value=estimate_res.value,
                unit=estimate_res.unit,
                basis=estimate_res.basis,
                confidence=estimate_res.confidence,
                is_manual_override=estimate_res.is_manual_override,
            )

        return plan_schema

    async def persist_generated_plan(
        self,
        tenant_id: str,
        goal_id: UUID,
        plan_schema: PlanSchema,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> PlanRecord:
        """Persist a generated or proposed PlanSchema under a goal version."""
        actor = actor_id or "system"
        uow = self._uow_factory()

        async with uow:
            quota_mgr = TenantQuotaManager()
            await quota_mgr.check_active_plans_quota(uow.session, tenant_id)

            goal_repo = GoalRepository(uow.session)
            plan_repo = PlanRepository(uow.session)
            task_repo = TaskRepository(uow.session)
            audit_repo = AuditRepository(uow.session)
            outbox_repo = OutboxRepository(uow.session)

            goal = await goal_repo.get_by_id(goal_id, tenant_id)
            if not goal:
                raise GoalNotFoundError(goal_id)

            # Calibrate effort estimates against tenant historical execution data
            await self.calibrate_plan_effort(tenant_id, plan_schema, task_repo)

            latest_version = await plan_repo.get_latest_version_number(goal_id, tenant_id)
            new_version = latest_version + 1

            plan_record = await plan_repo.create_plan(
                tenant_id=tenant_id,
                goal_id=goal_id,
                plan_name=plan_schema.plan_name,
                version_number=new_version,
                status=PlanState.GENERATED,
                is_baseline=False,
                metadata_json=plan_schema.metadata,
            )

            # Persist tasks, dependencies, milestones
            await task_repo.persist_plan_aggregates(
                tenant_id=tenant_id,
                plan_id=plan_record.id,
                task_drafts=plan_schema.tasks,
                dependency_drafts=plan_schema.dependencies,
                milestone_drafts=plan_schema.milestones,
            )

            # Outbox event: plan.generated (data minimization: minimal metadata only)
            envelope = create_event_envelope(
                EventCreate(
                    event_type="plan.generated",
                    event_version=1,
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    subject_type="plan",
                    subject_id=str(plan_record.id),
                    payload={
                        "goal_id": str(goal_id),
                        "plan_id": str(plan_record.id),
                        "version_number": new_version,
                        "task_count": len(plan_schema.tasks),
                        "dependency_count": len(plan_schema.dependencies),
                    },
                )
            )
            await outbox_repo.enqueue(envelope)

            # Audit event
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=actor,
                    actor_principal_type="SERVICE" if actor == "system" else "HUMAN",
                    authentication_method="SERVICE_API_KEY" if actor == "system" else "OIDC",
                    event_type="plan.generated",
                    resource_type="plan",
                    resource_id=str(plan_record.id),
                    outcome="SUCCESS",
                    correlation_id=correlation_id,
                    details={
                        "goal_id": str(goal_id),
                        "version_number": new_version,
                        "task_count": len(plan_schema.tasks),
                    },
                )
            )

            await uow.commit()

            return plan_record

    async def validate_plan(
        self,
        tenant_id: str,
        plan_id: UUID,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ValidationResult:
        """Run deterministic validation on a persisted plan.

        Transitions plan to VALIDATED if zero errors found.
        """
        actor = actor_id or "system"
        uow = self._uow_factory()

        async with uow:
            plan_repo = PlanRepository(uow.session)
            task_repo = TaskRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)

            tasks = await task_repo.get_tasks_for_plan(plan_id, tenant_id)
            dependencies = await task_repo.get_dependencies_for_plan(plan_id, tenant_id)

            # Reconstruct PlanSchema for deterministic validator
            task_drafts = [
                TaskDraft(
                    temporary_id=t.temporary_id,
                    title=t.title,
                    description=t.description,
                    estimated_duration_minutes=t.estimated_duration_minutes,
                    work_category=t.work_category,
                )
                for t in tasks
            ]
            task_id_to_temp = {t.id: t.temporary_id for t in tasks}
            dep_drafts = [
                from_dep
                for d in dependencies
                if (from_dep := self._build_dep_draft(d, task_id_to_temp)) is not None
            ]

            plan_schema = PlanSchema(
                plan_name=plan.plan_name,
                goal_id=plan.goal_id,
                tasks=task_drafts,
                dependencies=dep_drafts,
            )

            result = self._validator.validate(plan_schema)

            # If valid, assert transition to VALIDATED
            if result.is_valid:
                current_st = PlanState(plan.status)
                if current_st != PlanState.VALIDATED:
                    assert_valid_plan_transition(current_st, PlanState.VALIDATED)
                    await plan_repo.update_status(plan_id, tenant_id, PlanState.VALIDATED)

            # Append audit event
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=actor,
                    actor_principal_type="SERVICE" if actor == "system" else "HUMAN",
                    authentication_method="SERVICE_API_KEY" if actor == "system" else "OIDC",
                    event_type="plan.validated",
                    resource_type="plan",
                    resource_id=str(plan_id),
                    outcome="SUCCESS" if result.is_valid else "FAILURE",
                    correlation_id=correlation_id,
                    details={
                        "is_valid": result.is_valid,
                        "issue_count": len(result.issues),
                        "evaluated_tasks": result.evaluated_task_count,
                    },
                )
            )

            await uow.commit()

            return result

    def _build_dep_draft(self, d: TaskDependencyRecord, id_map: dict[UUID, str]) -> Any:
        pred_tid = id_map.get(d.predecessor_id)
        succ_tid = id_map.get(d.successor_id)
        if pred_tid and succ_tid:
            from app.task_intelligence.schemas import DependencyDraft, DependencyType

            return DependencyDraft(
                predecessor_id=pred_tid,
                successor_id=succ_tid,
                dependency_type=DependencyType(d.dependency_type),
                lag_minutes=d.lag_minutes,
            )
        return None

    async def submit_for_approval(
        self,
        tenant_id: str,
        plan_id: UUID,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> PlanRecord:
        """Submit a validated plan for human review and approval."""
        actor = actor_id or "system"
        uow = self._uow_factory()

        async with uow:
            plan_repo = PlanRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)

            current_st = PlanState(plan.status)
            assert_valid_plan_transition(current_st, PlanState.APPROVAL_PENDING)

            updated_plan = await plan_repo.update_status(
                plan_id=plan_id,
                tenant_id=tenant_id,
                status=PlanState.APPROVAL_PENDING,
            )
            assert updated_plan is not None

            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=actor,
                    actor_principal_type="SERVICE" if actor == "system" else "HUMAN",
                    authentication_method="SERVICE_API_KEY" if actor == "system" else "OIDC",
                    event_type="plan.approval_submitted",
                    resource_type="plan",
                    resource_id=str(plan_id),
                    outcome="SUCCESS",
                    correlation_id=correlation_id,
                    details={"status": PlanState.APPROVAL_PENDING},
                )
            )

            await uow.session.flush()
            await uow.session.refresh(updated_plan)
            uow.session.expunge(updated_plan)
            await uow.commit()

            return updated_plan

    async def approve_plan(
        self,
        tenant_id: str,
        plan_id: UUID,
        approver_id: str,
        correlation_id: str | None = None,
        workflow_key: str | None = None,
        workflow_variables: dict[str, Any] | None = None,
    ) -> PlanRecord:
        """Approve and freeze a validated plan as the authoritative baseline.

        Enforces:
        - Must be in APPROVAL_PENDING state.
        - Unsets baseline flag on any prior baseline for the goal.
        - Calculates deterministic baseline fingerprint hash.
        - Emits plan.approved outbox event and audit event atomically.
        """
        uow = self._uow_factory()

        async with uow:
            plan_repo = PlanRepository(uow.session)
            task_repo = TaskRepository(uow.session)
            audit_repo = AuditRepository(uow.session)
            outbox_repo = OutboxRepository(uow.session)

            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)

            current_st = PlanState(plan.status)
            assert_valid_plan_transition(current_st, PlanState.APPROVED)

            tasks = await task_repo.get_tasks_for_plan(plan_id, tenant_id)
            dependencies = await task_repo.get_dependencies_for_plan(plan_id, tenant_id)

            baseline_hash = compute_plan_baseline_hash(tasks, dependencies)

            # Supersede prior baseline if one exists
            prior_baseline = await plan_repo.get_baseline_for_goal(plan.goal_id, tenant_id)
            if prior_baseline and prior_baseline.id != plan_id:
                prior_baseline.is_baseline = False
                prior_baseline.superseded_by_plan_id = plan_id

            # Freeze this plan as approved baseline
            approved_plan = await plan_repo.update_status(
                plan_id=plan_id,
                tenant_id=tenant_id,
                status=PlanState.APPROVED,
                is_baseline=True,
                baseline_hash=baseline_hash,
            )
            assert approved_plan is not None

            # Resolve configured workflow_key
            wf_key = workflow_key
            metadata = getattr(plan, "metadata_json", {})
            if not wf_key and isinstance(metadata, dict):
                wf_key = metadata.get("workflow_key")
                is_semester_ref = (
                    metadata.get("reference_vertical") == "SEMESTER_EXAMINATION_OPERATIONS"
                )
                if not wf_key and is_semester_ref:
                    wf_key = "exam-operations-v1"

            # Outbox event: plan.approved (atomic business state transaction + outbox write)
            payload: dict[str, Any] = {
                "plan_id": str(plan_id),
                "goal_id": str(plan.goal_id),
                "version_number": plan.version_number,
                "baseline_hash": baseline_hash,
                "approver_id": approver_id,
            }
            if wf_key:
                payload["workflow_key"] = wf_key

            if workflow_variables:
                for k, v in workflow_variables.items():
                    payload[k] = v
            elif isinstance(metadata, dict):
                wf_vars = metadata.get("workflow_variables")
                if isinstance(wf_vars, dict):
                    for k, v in wf_vars.items():
                        payload[k] = v
                for k in ("curriculum_coverage", "sla_warning_at", "sla_breach_at"):
                    if k in metadata and k not in payload:
                        payload[k] = metadata[k]

            envelope = create_event_envelope(
                EventCreate(
                    event_type="plan.approved",
                    event_version=1,
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    subject_type="plan",
                    subject_id=str(plan_id),
                    payload=payload,
                )
            )
            await outbox_repo.enqueue(envelope)

            # Audit event: plan.approved
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=approver_id,
                    actor_principal_type="HUMAN",
                    authentication_method="OIDC",
                    event_type="plan.approved",
                    resource_type="plan",
                    resource_id=str(plan_id),
                    outcome="SUCCESS",
                    correlation_id=correlation_id,
                    details={
                        "version_number": plan.version_number,
                        "baseline_hash": baseline_hash,
                    },
                )
            )

            await uow.session.flush()
            await uow.session.refresh(approved_plan)
            uow.session.expunge(approved_plan)
            await uow.commit()

            return approved_plan

    async def activate_plan(
        self,
        tenant_id: str,
        plan_id: UUID,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> PlanRecord:
        """Transition an APPROVED plan to ACTIVE when workflow execution begins.

        Enforces:
        - Must currently be in APPROVED state.
        - Emits plan.activated audit event atomically.
        """
        actor = actor_id or "system"
        uow = self._uow_factory()

        async with uow:
            plan_repo = PlanRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)

            current_st = PlanState(plan.status)
            assert_valid_plan_transition(current_st, PlanState.ACTIVE)

            updated_plan = await plan_repo.update_status(
                plan_id=plan_id,
                tenant_id=tenant_id,
                status=PlanState.ACTIVE,
            )
            assert updated_plan is not None

            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=actor,
                    actor_principal_type="SERVICE" if actor == "system" else "HUMAN",
                    authentication_method="SERVICE_API_KEY" if actor == "system" else "OIDC",
                    event_type="plan.activated",
                    resource_type="plan",
                    resource_id=str(plan_id),
                    outcome="SUCCESS",
                    correlation_id=correlation_id,
                    details={"status": PlanState.ACTIVE},
                )
            )

            await uow.session.flush()
            await uow.session.refresh(updated_plan)
            uow.session.expunge(updated_plan)
            await uow.commit()
            return updated_plan

    async def create_draft_plan(
        self,
        tenant_id: str,
        goal_id: UUID,
        plan_name: str,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> PlanRecord:
        """Create an initial empty DRAFT plan before structured generation."""
        actor = actor_id or "system"
        uow = self._uow_factory()

        async with uow:
            goal_repo = GoalRepository(uow.session)
            plan_repo = PlanRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            goal = await goal_repo.get_by_id(goal_id, tenant_id)
            if not goal:
                raise GoalNotFoundError(goal_id)

            latest_version = await plan_repo.get_latest_version_number(goal_id, tenant_id)
            new_version = latest_version + 1

            plan_record = await plan_repo.create_plan(
                tenant_id=tenant_id,
                goal_id=goal_id,
                plan_name=plan_name,
                version_number=new_version,
                status=PlanState.DRAFT,
                is_baseline=False,
            )

            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=actor,
                    actor_principal_type="SERVICE" if actor == "system" else "HUMAN",
                    authentication_method="SERVICE_API_KEY" if actor == "system" else "OIDC",
                    event_type="plan.draft_created",
                    resource_type="plan",
                    resource_id=str(plan_record.id),
                    outcome="SUCCESS",
                    correlation_id=correlation_id,
                    details={"version_number": new_version, "status": PlanState.DRAFT},
                )
            )

            await uow.commit()
            return plan_record

    async def get_plan(
        self,
        tenant_id: str,
        plan_id: UUID,
        load_relations: bool = True,
    ) -> PlanRecord | None:
        """Retrieve plan entity within tenant boundary."""
        uow = self._uow_factory()
        async with uow:
            plan_repo = PlanRepository(uow.session)
            record = await plan_repo.get_by_id(plan_id, tenant_id, load_relations=load_relations)
            if record:
                uow.session.expunge_all()
            return record

    async def get_critical_path(
        self,
        tenant_id: str,
        plan_id: UUID,
    ) -> CPMScheduleResult:
        """Compute CPM schedule metrics and critical path for a plan."""
        uow = self._uow_factory()
        async with uow:
            plan_repo = PlanRepository(uow.session)
            task_repo = TaskRepository(uow.session)

            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)

            tasks = await task_repo.get_tasks_for_plan(plan_id, tenant_id)
            dependencies = await task_repo.get_dependencies_for_plan(plan_id, tenant_id)

            graph = TaskDependencyGraph()
            for t in tasks:
                graph.add_task(
                    task_id=t.temporary_id,
                    duration_minutes=t.estimated_duration_minutes,
                )

            task_id_to_temp = {t.id: t.temporary_id for t in tasks}
            for d in dependencies:
                pred = task_id_to_temp.get(d.predecessor_id)
                succ = task_id_to_temp.get(d.successor_id)
                if pred and succ:
                    graph.add_dependency(
                        predecessor_id=pred,
                        successor_id=succ,
                        lag_minutes=d.lag_minutes,
                    )

            await uow.commit()
            return CriticalPathCalculator.calculate(graph)

    async def update_task_progress(
        self,
        tenant_id: str,
        task_id: UUID,
        update_data: TaskProgressUpdate,
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> TaskRecord:
        """Update canonical task progress and blocker status."""
        actor = actor_id or "system"
        uow = self._uow_factory()

        async with uow:
            task_repo = TaskRepository(uow.session)
            audit_repo = AuditRepository(uow.session)

            task = await task_repo.get_task_by_id(task_id, tenant_id)
            if not task:
                raise TaskNotFoundError(task_id)

            if update_data.status is not None:
                current_st = TaskProgressStatus(task.status)
                assert_valid_task_progress_transition(current_st, update_data.status)

            updated = await task_repo.update_task_progress(task_id, tenant_id, update_data)
            assert updated is not None

            # Audit progress transition
            await audit_repo.append(
                AuditEventCreate(
                    tenant_id=tenant_id,
                    actor_principal_id=actor,
                    actor_principal_type="SERVICE" if actor == "system" else "HUMAN",
                    authentication_method="SERVICE_API_KEY" if actor == "system" else "OIDC",
                    event_type="task.progress_updated",
                    resource_type="task",
                    resource_id=str(task_id),
                    outcome="SUCCESS",
                    correlation_id=correlation_id,
                    details={
                        "new_status": str(update_data.status) if update_data.status else None,
                        "progress_percentage": update_data.progress_percentage,
                        "blocked_reason": update_data.blocked_reason,
                    },
                )
            )

            # Emit canonical domain events if reaching terminal milestone states
            outbox_repo = OutboxRepository(uow.session)
            if update_data.status == TaskProgressStatus.COMPLETED:
                envelope = create_event_envelope(
                    EventCreate(
                        event_type="task.completed",
                        event_version=1,
                        tenant_id=tenant_id,
                        correlation_id=correlation_id,
                        subject_type="task",
                        subject_id=str(task_id),
                        payload={
                            "task_id": str(task_id),
                            "plan_id": str(task.plan_id),
                            "temporary_id": task.temporary_id,
                            "completed_by": actor,
                            "progress_percentage": 100,
                        },
                    )
                )
                await outbox_repo.enqueue(envelope)
            elif update_data.status == TaskProgressStatus.BLOCKED:
                envelope = create_event_envelope(
                    EventCreate(
                        event_type="task.blocked",
                        event_version=1,
                        tenant_id=tenant_id,
                        correlation_id=correlation_id,
                        subject_type="task",
                        subject_id=str(task_id),
                        payload={
                            "task_id": str(task_id),
                            "plan_id": str(task.plan_id),
                            "temporary_id": task.temporary_id,
                            "blocked_reason": update_data.blocked_reason,
                        },
                    )
                )
                await outbox_repo.enqueue(envelope)

            await uow.session.flush()
            await uow.session.refresh(updated)
            await uow.commit()

            return updated

    async def get_plan_recommendations(
        self,
        tenant_id: str,
        plan_id: UUID,
        limit: int = 10,
        include_blocked: bool = False,
        policy: RecommendationPolicyConfig | None = None,
    ) -> PlanRecommendationsResponse:
        """Compute next-best-task recommendations for a plan (FR-TI-09)."""
        uow = self._uow_factory()
        async with uow:
            plan_repo = PlanRepository(uow.session)
            task_repo = TaskRepository(uow.session)

            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)

            tasks = await task_repo.get_tasks_for_plan(plan_id, tenant_id)
            dependencies = await task_repo.get_dependencies_for_plan(plan_id, tenant_id)
            milestones = await task_repo.get_milestones_for_plan(plan_id, tenant_id)

            # Compute CPM schedule metrics
            graph = TaskDependencyGraph()
            for t in tasks:
                graph.add_task(
                    task_id=t.temporary_id,
                    duration_minutes=t.estimated_duration_minutes,
                )

            task_id_to_temp = {t.id: t.temporary_id for t in tasks}
            for d in dependencies:
                pred = task_id_to_temp.get(d.predecessor_id)
                succ = task_id_to_temp.get(d.successor_id)
                if pred and succ:
                    graph.add_dependency(
                        predecessor_id=pred,
                        successor_id=succ,
                        lag_minutes=d.lag_minutes,
                    )

            cpm_result: CPMScheduleResult | None = None
            cpm_diagnostic: str | None = None
            try:
                cpm_result = CriticalPathCalculator.calculate(graph)
            except GraphCycleError as exc:
                cpm_diagnostic = f"Dependency cycle detected in graph: {exc.cycle_path}"
            except Exception as exc:
                cpm_diagnostic = f"CPM calculation failed: {str(exc)}"

            return TaskRecommender.recommend(
                plan_id=plan_id,
                tenant_id=tenant_id,
                tasks=tasks,
                dependencies=dependencies,
                cpm_result=cpm_result,
                cpm_diagnostic=cpm_diagnostic,
                policy=policy,
                milestones=milestones,
                limit=limit,
                include_blocked=include_blocked,
            )

    async def evaluate_plan_risks(
        self,
        tenant_id: str,
        plan_id: UUID,
    ) -> list[TaskRiskItem]:
        """Evaluate deterministic operational risks for a plan (FR-TI-07).

        Invariants:
        - Pure calculation with inspectable evidence.
        - Tenant-scoped: never leaks cross-tenant information.
        - Evaluates:
          1. Critical-path vs. dependency slack slippage (CPM). Never silently swallowed.
          2. Declared blockers and successor dependency propagation.
          3. Overdue dependencies / task variance / repeated rework.
          4. Authoritative resource capacity evaluation matching canonical ResourceRecord
             skills, attributes, and types (PERSON, ROOM, EQUIPMENT, OTHER).
          5. Concurrency-aware: evaluates peak overlapping demand from CPM execution windows;
             non-overlapping tasks do not inflate peak demand.
          6. When authoritative resource registry is unavailable or empty, returns an explicit
             RESOURCE_CAPACITY_UNSUPPORTED item with is_evaluable=False.
        """
        uow = self._uow_factory()
        async with uow:
            plan_repo = PlanRepository(uow.session)
            task_repo = TaskRepository(uow.session)

            plan = await plan_repo.get_by_id(plan_id, tenant_id)
            if not plan:
                raise PlanNotFoundError(plan_id)

            tasks = await task_repo.get_tasks_for_plan(plan_id, tenant_id)
            dependencies = await task_repo.get_dependencies_for_plan(plan_id, tenant_id)

            risks: list[TaskRiskItem] = []

            # 1. Critical path and slack slippage
            graph = TaskDependencyGraph()
            for t in tasks:
                graph.add_task(
                    task_id=t.temporary_id,
                    duration_minutes=t.estimated_duration_minutes,
                )

            task_id_to_temp = {t.id: t.temporary_id for t in tasks}
            for d in dependencies:
                pred = task_id_to_temp.get(d.predecessor_id)
                succ = task_id_to_temp.get(d.successor_id)
                if pred and succ:
                    graph.add_dependency(
                        predecessor_id=pred,
                        successor_id=succ,
                        lag_minutes=d.lag_minutes,
                    )

            cpm_result: CPMScheduleResult | None = None
            try:
                cpm_result = CriticalPathCalculator.calculate(graph)
                delayed_task_ids = {
                    t.temporary_id
                    for t in tasks
                    if (
                        t.status == "BLOCKED"
                        or (
                            t.actual_duration_minutes is not None
                            and t.actual_duration_minutes > t.estimated_duration_minutes
                        )
                    )
                }
                cpm_risks = DeterministicRiskDetector.detect_critical_path_slippage(
                    cpm_result=cpm_result,
                    delayed_task_ids=delayed_task_ids,
                )
                risks.extend(cpm_risks)
            except GraphCycleError as exc:
                cycle_str = (
                    " -> ".join(exc.cycle_path)
                    if exc.cycle_path
                    else "dependency cycle detected"
                )
                risks.append(
                    TaskRiskItem(
                        task_id="GLOBAL",
                        category=RiskCategory.DEPENDENCY_SLIPPAGE,
                        severity=RiskSeverity.CRITICAL,
                        description=(
                            "CPM schedule calculation degraded: dependency cycle detected "
                            f"({cycle_str}). "
                            "Critical path and execution schedule are indeterminate."
                        ),
                        is_evaluable=True,
                    )
                )
            except Exception as exc:
                # Sanitized typed diagnostic without exposing internal secrets
                risks.append(
                    TaskRiskItem(
                        task_id="GLOBAL",
                        category=RiskCategory.DEPENDENCY_SLIPPAGE,
                        severity=RiskSeverity.HIGH,
                        description=(
                            f"CPM schedule calculation degraded ({type(exc).__name__}): "
                            "schedule and critical path are indeterminate."
                        ),
                        is_evaluable=True,
                    )
                )

            # 2. Declared blockers and dependency propagation
            blocker_risks = DeterministicRiskDetector.detect_blocked_dependencies(
                tasks=tasks,
                dependencies=dependencies,
            )
            risks.extend(blocker_risks)

            # 3. Repeated rework and duration variance
            for t in tasks:
                if (
                    t.actual_duration_minutes is not None
                    and t.estimated_duration_minutes > 0
                    and t.actual_duration_minutes > t.estimated_duration_minutes
                ):
                    ratio = (
                        t.actual_duration_minutes - t.estimated_duration_minutes
                    ) / float(t.estimated_duration_minutes)
                    if ratio > 0.5:
                        severity = RiskSeverity.HIGH if ratio > 1.0 else RiskSeverity.MEDIUM
                        risks.append(
                            TaskRiskItem(
                                task_id=t.temporary_id,
                                category=RiskCategory.DEPENDENCY_SLIPPAGE,
                                severity=severity,
                                description=(
                                    f"Task '{t.temporary_id}' duration exceeded estimate by "
                                    f"{ratio * 100:.1f}% ({t.actual_duration_minutes}m actual vs "
                                    f"{t.estimated_duration_minutes}m est)."
                                ),
                                is_evaluable=True,
                            )
                        )

            # 4. Authoritative Resource Capacity Evaluation
            stmt = select(ResourceRecord).where(
                ResourceRecord.tenant_id == tenant_id,
                ResourceRecord.active.is_(True),
            )
            res_result = await uow.session.execute(stmt)
            active_resources = list(res_result.scalars().all())

            if not active_resources:
                # Authoritative data is genuinely unavailable -> explicit unevaluable reporting
                risks.append(
                    TaskRiskItem(
                        task_id="GLOBAL",
                        category=RiskCategory.RESOURCE_CAPACITY_UNSUPPORTED,
                        severity=RiskSeverity.LOW,
                        description=(
                            "Resource capacity cannot be evaluated without authoritative "
                            "upstream resource registry data."
                        ),
                        is_evaluable=False,
                    )
                )
            else:
                # Identify tasks that declare resource requirements
                demanding_tasks = [
                    t for t in tasks
                    if t.status != "COMPLETED" and (t.owner_role or t.owner_reference)
                ]

                if demanding_tasks:
                    if cpm_result is None:
                        # When execution windows cannot be determined (due to CPM failure),
                        # concurrent demand cannot be authoritatively evaluated.
                        risks.append(
                            TaskRiskItem(
                                task_id="GLOBAL",
                                category=RiskCategory.RESOURCE_CAPACITY_UNSUPPORTED,
                                severity=RiskSeverity.MEDIUM,
                                description=(
                                    "Resource capacity concurrency cannot be evaluated because "
                                    "task execution windows are indeterminate (CPM unavailable)."
                                ),
                                is_evaluable=False,
                            )
                        )
                    else:
                        role_demands: dict[str, list[TaskRecord]] = {}
                        for t in demanding_tasks:
                            req_key = (t.owner_role or t.owner_reference or "").strip().upper()
                            if req_key:
                                role_demands.setdefault(req_key, []).append(t)

                        for req_key, req_tasks in sorted(role_demands.items()):
                            eligible_resources = [
                                res for res in active_resources
                                if (
                                    req_key in [s.strip().upper() for s in (res.skills or [])]
                                    or str(res.attributes.get("role", "")).strip().upper()
                                    == req_key
                                    or res.type.strip().upper() == req_key
                                    or res.resource_key.strip().upper() == req_key
                                )
                            ]

                            if not eligible_resources:
                                for rt in req_tasks:
                                    risks.append(
                                        TaskRiskItem(
                                            task_id=rt.temporary_id,
                                            category=RiskCategory.RESOURCE_CAPACITY_SHORTFALL,
                                            severity=RiskSeverity.HIGH,
                                            description=(
                                                f"Task '{rt.temporary_id}' requires '{req_key}', "
                                                "but no active eligible resource with matching "
                                                "skills, attributes, or type exists in registry."
                                            ),
                                            is_evaluable=True,
                                        )
                                    )
                            else:
                                total_capacity = sum(res.capacity for res in eligible_resources)

                                events: list[tuple[int, int]] = []
                                for rt in req_tasks:
                                    sched = cpm_result.schedule.get(rt.temporary_id)
                                    start = sched.early_start if sched else 0
                                    finish = (
                                        sched.early_finish
                                        if sched
                                        else rt.estimated_duration_minutes
                                    )
                                    if finish > start:
                                        events.append((start, 1))
                                        events.append((finish, -1))
                                    else:
                                        events.append((start, 1))
                                        events.append((start + 1, -1))

                                events.sort(key=lambda x: (x[0], x[1]))

                                current_demand = 0
                                peak_demand = 0
                                for _, delta in events:
                                    current_demand += delta
                                    if current_demand > peak_demand:
                                        peak_demand = current_demand

                                if peak_demand > total_capacity:
                                    risks.append(
                                        TaskRiskItem(
                                            task_id=f"RESOURCE:{req_key}",
                                            category=RiskCategory.RESOURCE_CAPACITY_SHORTFALL,
                                            severity=RiskSeverity.HIGH,
                                            description=(
                                                "Authoritative capacity shortfall for "
                                                f"'{req_key}': peak concurrent demand is "
                                                f"{peak_demand} units, but eligible capacity is "
                                                f"{total_capacity}."
                                            ),
                                            is_evaluable=True,
                                        )
                                    )

            return risks

