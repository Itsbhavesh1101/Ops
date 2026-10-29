"""Shared fixtures and persona builders for Phase 8A Security Suite.

Provides:
- Two isolated tenants: tenant-alpha and tenant-beta.
- 7 canonical personas:
  1. Planner
  2. Approver
  3. Scheduler / Operator
  4. Auditor
  5. Tenant Admin
  6. Service Identity
  7. Unauthenticated Caller
- Mock OIDC and Service Key verifiers.
- In-memory service key credential store with real constant-time hashing.
- Canonical TestClient with all domain routers mounted under /api/v1.
- In-memory stubs for PlanService, WorkflowService, SchedulingService, JobReader,
  AnalyticsService, and AuditService seeded with realistic cross-tenant records.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI
from starlette.testclient import TestClient

from app.analytics.service import (
    OperationsAnalyticsService,
    OperationsDashboardSummary,
    PlanProgressSummary,
    RepairMovementSummary,
    ScheduleHealthSummary,
    SlaStateSummary,
)
from app.api.errors import register_api_exception_handlers
from app.api.idempotency import register_idempotency_exception_handler
from app.api.v1.calendar import register_calendar_routes
from app.api.v1.composition import register_domain_routes
from app.api.v1.planning import PlanResponse
from app.audit.service import AuditEventResponse, AuditQueryService
from app.core.auth.dependency import (
    HttpAuthenticator,
)
from app.core.auth.google_oidc import (
    InvalidSignatureError,
    TokenExpiredError,
)
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
    TenantContext,
    VerifiedOidcIdentity,
)
from app.core.auth.resolvers import DefaultIdentityResolver
from app.core.auth.service_api_key import (
    InMemoryServiceApiKeyCredentialStore,
    ServiceApiKeyCredentialRecord,
    ServiceApiKeyVerifier,
    compute_secret_digest,
)
from app.core.jobs import (
    JobId,
    JobIdentity,
    JobSnapshot,
    JobStatus,
    JobType,
)
from app.core.middleware import (
    CorrelationAndLoggingMiddleware,
    RequestBodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.resources.schemas import (
    ResourceCreate,
    ResourceType,
)
from app.resources.service import ResourceService
from app.scheduling.constraints.models import NoOverlapConstraint
from app.scheduling.profiles.models import SoftObjectiveWeights, SolverProfile
from app.scheduling.schemas import (
    SchedulingActivity,
    SchedulingRequest,
    SchedulingResourceSnapshot,
)
from app.scheduling.service import (
    JobNotFoundError,
    ScheduleNotFoundError,
)
from app.task_intelligence.graph.models import CPMScheduleResult
from app.task_intelligence.schemas import (
    GoalResponse,
    ValidationResult,
)
from app.task_intelligence.service import (
    GoalNotFoundError,
    PlanNotFoundError,
    TaskNotFoundError,
)
from app.workflow.adapters.dto import (
    EngineDeploymentResult,
    EngineProcessInstance,
    EngineTask,
    EngineTaskCompletionResult,
)
from app.workflow.adapters.protocol import WorkflowAdapterProtocol
from app.workflow.authorization import WorkflowTaskUnauthorizedError
from app.workflow.schemas import (
    WorkflowDefinitionResponse,
    WorkflowInstanceResponse,
    WorkflowInstanceStatus,
    WorkflowTaskResponse,
    WorkflowTaskStatus,
)
from app.workflow.service import (
    WorkflowDefinitionNotFoundError,
    WorkflowInstanceNotFoundError,
    WorkflowTaskNotFoundError,
)

# -----------------------------------------------------------------------------
# Tenant Identifiers & Seed IDs
# -----------------------------------------------------------------------------
TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"

GOAL_A_ID = UUID("11111111-aaaa-4111-a111-111111111111")
PLAN_A_ID = UUID("22222222-aaaa-4222-a222-222222222222")
TASK_A_ID = UUID("33333333-aaaa-4333-a333-333333333333")
WF_DEF_A_ID = UUID("44444444-aaaa-4444-a444-444444444444")
WF_INST_A_ID = UUID("55555555-aaaa-4555-a555-555555555555")
WF_TASK_A_ID = UUID("66666666-aaaa-4666-a666-666666666666")
SCHED_JOB_A_ID = UUID("77777777-aaaa-4777-a777-777777777777")
SCHEDULE_A_ID = UUID("88888888-aaaa-4888-a888-888888888888")
JOB_A_ID = UUID("99999999-aaaa-4999-a999-999999999999")
RESOURCE_A_ID = UUID("00000000-aaaa-4000-a000-000000000001")

GOAL_B_ID = UUID("11111111-bbbb-4111-b111-111111111111")
PLAN_B_ID = UUID("22222222-bbbb-4222-b222-222222222222")
TASK_B_ID = UUID("33333333-bbbb-4333-b333-333333333333")
WF_DEF_B_ID = UUID("44444444-bbbb-4444-b444-444444444444")
WF_INST_B_ID = UUID("55555555-bbbb-4555-b555-555555555555")
WF_TASK_B_ID = UUID("66666666-bbbb-4666-b666-666666666666")
SCHED_JOB_B_ID = UUID("77777777-bbbb-4777-b777-777777777777")
SCHEDULE_B_ID = UUID("88888888-bbbb-4888-b888-888888888888")
JOB_B_ID = UUID("99999999-bbbb-4999-b999-999999999999")
RESOURCE_B_ID = UUID("00000000-bbbb-4000-b000-000000000002")


def make_valid_schedule_problem(schedule_key: str = "sched-job-test") -> dict[str, Any]:
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=4)
    profile = SolverProfile(
        profile_key="test-prof",
        version=1,
        name="Test",
        horizon_start=t0,
        horizon_end=t1,
        time_granularity_minutes=15,
        max_solve_seconds=10.0,
        random_seed=42,
        num_search_workers=1,
        soft_weights=SoftObjectiveWeights(
            preference_weight=0,
            idle_gap_weight=0,
            fairness_weight=0,
            utilization_weight=0,
            movement_weight=0,
        ),
    )
    res = SchedulingResourceSnapshot(
        id="tech-1",
        type=ResourceType.PERSON,
        display_label="Technician 1",
        capacity=1,
    )
    act = SchedulingActivity(
        id="act-1",
        title="Activity 1",
        duration_minutes=60,
        required_resource_ids=["tech-1"],
    )
    req = SchedulingRequest(
        tenant_id="dummy",
        schedule_key=schedule_key,
        horizon_start=t0,
        horizon_end=t1,
        activities=[act],
        resources=[res],
        constraints=[
            NoOverlapConstraint(id="c1", name="No Overlap Tech 1", resource_ids=["tech-1"])
        ],
        profile=profile,
    )
    req_dict = req.model_dump(mode="json")
    req_dict.pop("tenant_id", None)
    return {
        "schedule_key": schedule_key,
        "request": req_dict,
    }


VALID_REPAIR_PAYLOAD = {
    "disrupted_resource_ids": ["tech-1"],
    "affected_activity_ids": ["act-1"],
    "reason": "resource.unavailable",
    "movement_weight": 1.0,
}


# -----------------------------------------------------------------------------
# Fake Google OIDC Verifier (deterministic, offline, zero network I/O)
# -----------------------------------------------------------------------------
class FakeGoogleOidcVerifier:
    """Deterministic offline OIDC verifier for testing."""

    def __init__(self) -> None:
        self._tokens: dict[str, VerifiedOidcIdentity] = {}

    def register_token(self, token: str, identity: VerifiedOidcIdentity) -> None:
        self._tokens[token] = identity

    async def verify_id_token(
        self, token: str, expected_nonce: str | None = None
    ) -> VerifiedOidcIdentity:
        if token == "expired_oidc_token":
            raise TokenExpiredError("Token has expired")
        if token in self._tokens:
            return self._tokens[token]
        raise InvalidSignatureError("Invalid token signature")


# -----------------------------------------------------------------------------
# Stub/In-Memory Domain Services
# -----------------------------------------------------------------------------
class ScheduleJobRecordStub:
    """Lightweight in-memory stub for ScheduleJobRecord."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class ScheduleRecordStub:
    """Lightweight in-memory stub for ScheduleRecord."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class TaskRecordStub:
    """Lightweight in-memory stub for TaskRecord."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class StubPlanService:
    """Deterministic in-memory PlanService stub."""

    def __init__(self) -> None:
        self.goals: dict[tuple[str, UUID], GoalResponse] = {}
        self.plans: dict[tuple[str, UUID], PlanResponse] = {}
        self.tasks: dict[tuple[str, UUID], TaskRecordStub] = {}

    async def create_goal(
        self,
        tenant_id: str,
        goal_data: Any,
        correlation_id: str | None = None,
    ) -> GoalResponse:
        goal_id = uuid4()
        now = datetime.now(UTC)
        obj = getattr(goal_data, "objective", "Operational Goal")
        goal = GoalResponse(
            id=goal_id,
            tenant_id=tenant_id,
            objective=obj,
            scope=getattr(goal_data, "scope", None),
            created_at=now,
            updated_at=now,
        )
        self.goals[(tenant_id, goal_id)] = goal
        return goal

    async def persist_generated_plan(
        self,
        tenant_id: str,
        goal_id: UUID,
        plan_schema: Any,
        correlation_id: str | None = None,
    ) -> PlanResponse:
        if (tenant_id, goal_id) not in self.goals:
            raise GoalNotFoundError(f"Goal {goal_id} not found for tenant {tenant_id}")
        plan_id = uuid4()
        now = datetime.now(UTC)
        plan = PlanResponse(
            id=plan_id,
            goal_id=goal_id,
            version_number=1,
            plan_name="Generated Plan",
            status="DRAFT",
            is_baseline=False,
            baseline_hash=None,
            created_at=now,
            updated_at=now,
        )
        self.plans[(tenant_id, plan_id)] = plan
        return plan

    async def generate_plan(
        self,
        tenant_id: str,
        goal_id: UUID,
        planner: Any = None,
        parameters: dict[str, Any] | None = None,
    ) -> PlanResponse:
        if (tenant_id, goal_id) not in self.goals:
            raise GoalNotFoundError(f"Goal {goal_id} not found for tenant {tenant_id}")
        plan_id = uuid4()
        now = datetime.now(UTC)
        plan = PlanResponse(
            id=plan_id,
            goal_id=goal_id,
            version_number=1,
            plan_name="Generated Plan",
            status="DRAFT",
            is_baseline=False,
            baseline_hash=None,
            created_at=now,
            updated_at=now,
        )
        self.plans[(tenant_id, plan_id)] = plan
        return plan

    async def validate_plan(
        self, tenant_id: str, plan_id: UUID, **kwargs: Any
    ) -> ValidationResult:
        if (tenant_id, plan_id) not in self.plans:
            raise PlanNotFoundError(f"Plan {plan_id} not found for tenant {tenant_id}")
        return ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[],
            diagnostics={"task_count": 2},
        )

    async def approve_plan(
        self,
        tenant_id: str,
        plan_id: UUID,
        approver_id: str,
        correlation_id: str | None = None,
        **kwargs: Any,
    ) -> PlanResponse:
        if (tenant_id, plan_id) not in self.plans:
            raise PlanNotFoundError(f"Plan {plan_id} not found for tenant {tenant_id}")
        plan = self.plans[(tenant_id, plan_id)]
        now = datetime.now(UTC)
        approved = PlanResponse(
            id=plan.id,
            goal_id=plan.goal_id,
            version_number=plan.version_number,
            plan_name=plan.plan_name,
            status="APPROVED",
            is_baseline=True,
            baseline_hash="hash-1234",
            created_at=plan.created_at,
            updated_at=now,
        )
        self.plans[(tenant_id, plan_id)] = approved
        return approved

    async def get_critical_path(
        self, tenant_id: str, plan_id: UUID, **kwargs: Any
    ) -> CPMScheduleResult:
        if (tenant_id, plan_id) not in self.plans:
            raise PlanNotFoundError(f"Plan {plan_id} not found for tenant {tenant_id}")
        return CPMScheduleResult(
            critical_path=[],
            task_metrics={},
            total_duration_hours=0.0,
        )

    async def update_task_progress(
        self,
        tenant_id: str,
        task_id: UUID,
        update_data: Any,
        correlation_id: str | None = None,
        **kwargs: Any,
    ) -> TaskRecordStub:
        if (tenant_id, task_id) not in self.tasks:
            raise TaskNotFoundError(f"Task {task_id} not found for tenant {tenant_id}")
        task = self.tasks[(tenant_id, task_id)]
        task.status = getattr(update_data, "status", task.status)
        task.updated_at = datetime.now(UTC)
        return task

    async def get_plan_recommendations(
        self,
        tenant_id: str,
        plan_id: UUID,
        policy: Any = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> Any:
        from app.api.v1.planning import PlanRecommendationsResponse

        if (tenant_id, plan_id) not in self.plans:
            raise PlanNotFoundError(f"Plan {plan_id} not found for tenant {tenant_id}")
        return PlanRecommendationsResponse(
            plan_id=plan_id,
            tenant_id=tenant_id,
            evaluated_at=datetime.now(UTC),
            is_authoritative=False,
            total_tasks=0,
            ready_tasks_count=0,
            blocked_tasks_count=0,
            completed_tasks_count=0,
            policy=None,
            policy_applied=False,
            cpm_diagnostic=None,
            recommendations=[],
        )


class StubWorkflowService:
    """Deterministic in-memory WorkflowService stub."""

    def __init__(self) -> None:
        self.definitions: dict[tuple[str, UUID], WorkflowDefinitionResponse] = {}
        self.instances: dict[tuple[str, UUID], WorkflowInstanceResponse] = {}
        self.tasks: dict[tuple[str, UUID], WorkflowTaskResponse] = {}

    async def register_definition(
        self,
        tenant_id: str,
        workflow_key: str,
        name: str,
        description: str | None,
        bpmn_xml: str,
        actor_id: str,
    ) -> WorkflowDefinitionResponse:
        def_id = uuid4()
        now = datetime.now(UTC)
        defn = WorkflowDefinitionResponse(
            id=def_id,
            tenant_id=tenant_id,
            workflow_key=workflow_key,
            name=name,
            description=description,
            version=1,
            bpmn_process_id="process_1",
            artifact_hash="hash_1",
            deployment_id=None,
            engine_definition_id=None,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.definitions[(tenant_id, def_id)] = defn
        return defn

    async def deploy_definition(
        self,
        tenant_id: str,
        definition_id: UUID,
        actor_id: str,
    ) -> WorkflowDefinitionResponse:
        if (tenant_id, definition_id) not in self.definitions:
            raise WorkflowDefinitionNotFoundError(f"Definition {definition_id} not found")
        defn = self.definitions[(tenant_id, definition_id)]
        now = datetime.now(UTC)
        deployed = WorkflowDefinitionResponse(
            id=defn.id,
            tenant_id=tenant_id,
            workflow_key=defn.workflow_key,
            name=defn.name,
            description=defn.description,
            version=defn.version,
            bpmn_process_id=defn.bpmn_process_id,
            artifact_hash=defn.artifact_hash,
            deployment_id=f"deploy-{defn.id}",
            engine_definition_id=f"eng-{defn.id}",
            is_active=True,
            created_at=defn.created_at,
            updated_at=now,
        )
        self.definitions[(tenant_id, definition_id)] = deployed
        return deployed

    async def get_definition(
        self, tenant_id: str, definition_id: UUID
    ) -> WorkflowDefinitionResponse:
        if (tenant_id, definition_id) not in self.definitions:
            raise WorkflowDefinitionNotFoundError(f"Definition {definition_id} not found")
        return self.definitions[(tenant_id, definition_id)]

    async def start_instance(
        self,
        tenant_id: str,
        workflow_key: str,
        business_key: str | None,
        variables: dict[str, Any] | None,
        actor_id: str,
    ) -> WorkflowInstanceResponse:
        inst_id = uuid4()
        now = datetime.now(UTC)
        inst = WorkflowInstanceResponse(
            id=inst_id,
            tenant_id=tenant_id,
            definition_id=uuid4(),
            workflow_key=workflow_key,
            version=1,
            business_reference=business_key,
            correlation_id=None,
            engine_instance_id=f"eng-{inst_id}",
            status=WorkflowInstanceStatus.RUNNING,
            input_variables=variables or {},
            started_by=actor_id,
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        self.instances[(tenant_id, inst_id)] = inst
        return inst

    async def get_instance(
        self,
        tenant_id: str,
        instance_id: UUID | None = None,
        workflow_instance_id: UUID | None = None,
        **kwargs: Any,
    ) -> WorkflowInstanceResponse:
        target_id = workflow_instance_id or instance_id
        if target_id is None or (tenant_id, target_id) not in self.instances:
            raise WorkflowInstanceNotFoundError(f"Instance {target_id} not found")
        return self.instances[(tenant_id, target_id)]

    async def get_tasks(
        self,
        tenant_id: str,
        instance_id: UUID | None = None,
        assignee: str | None = None,
        candidate_group: str | None = None,
    ) -> list[WorkflowTaskResponse]:
        return [task for (t_id, _), task in self.tasks.items() if t_id == tenant_id]

    async def execute_task_action(
        self,
        tenant_id: str,
        task_id: UUID,
        action: str,
        actor_id: str,
        variables: dict[str, Any] | None = None,
    ) -> WorkflowTaskResponse:
        if (tenant_id, task_id) not in self.tasks:
            raise WorkflowTaskNotFoundError(f"Task {task_id} not found")
        task = self.tasks[(tenant_id, task_id)]
        # SoD check: assignee eligibility
        if (
            task.assignee
            and task.assignee != actor_id
            and "approver" not in actor_id
            and "admin" not in actor_id
        ):
            raise WorkflowTaskUnauthorizedError(
                principal_id=actor_id,
                task_id=str(task_id),
                reason=f"Actor {actor_id} is not assigned to task {task_id}",
            )
        return task

    async def perform_task_action(
        self,
        tenant_id: str,
        workflow_task_id: UUID,
        action: Any,
        principal: AuthenticatedPrincipal,
        idempotency_key: str | None = None,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> WorkflowTaskResponse:
        return await self.execute_task_action(
            tenant_id=tenant_id,
            task_id=workflow_task_id,
            action=str(action),
            actor_id=principal.principal_id,
            variables=variables,
        )


class StubSchedulingService:
    """Deterministic in-memory SchedulingService stub."""

    def __init__(self) -> None:
        self.jobs: dict[tuple[str, UUID], Any] = {}
        self.schedules: dict[tuple[str, UUID], Any] = {}

    async def submit_job(
        self,
        session: Any,
        tenant_id: str,
        request: Any,
        schedule_key: str = "default",
    ) -> Any:
        job_id = uuid4()
        now = datetime.now(UTC)
        job = ScheduleJobRecordStub(
            id=job_id,
            schedule_key=schedule_key,
            status="SUBMITTED",
            input_snapshot_hash="hash-solve-input",
            solver_metadata={},
            diagnostics=[],
            failure_message=None,
            created_at=now,
            completed_at=None,
        )
        self.jobs[(tenant_id, job_id)] = job
        return job

    async def get_job(self, session: Any, tenant_id: str, job_id: UUID) -> Any:
        return self.jobs.get((tenant_id, job_id))

    async def get_job_by_snapshot_hash(
        self, session: Any, tenant_id: str, snapshot_hash: str
    ) -> Any:
        for (t_id, _), job in self.jobs.items():
            if t_id == tenant_id and getattr(job, "input_snapshot_hash", None) == snapshot_hash:
                return job
        return None

    async def publish_job(
        self,
        session: Any,
        tenant_id: str,
        job_id: UUID,
        principal: str,
        publish_policy: Any = None,
        correlation_id: str | None = None,
    ) -> Any:
        if (tenant_id, job_id) not in self.jobs:
            raise JobNotFoundError(f"Job {job_id} not found for tenant {tenant_id}")
        sched_id = uuid4()
        now = datetime.now(UTC)
        sched = ScheduleRecordStub(
            id=sched_id,
            version=1,
            status="PUBLISHED",
            published_at=now,
            published_by=principal,
            supersedes_id=None,
            assignments=[],
        )
        self.schedules[(tenant_id, sched_id)] = sched
        return sched

    async def repair_schedule(
        self,
        session: Any,
        tenant_id: str,
        schedule_id: UUID,
        request: Any = None,
        repair_request: Any = None,
        **kwargs: Any,
    ) -> Any:
        if (tenant_id, schedule_id) not in self.schedules:
            raise ScheduleNotFoundError(f"Schedule {schedule_id} not found for tenant {tenant_id}")
        job = ScheduleJobRecordStub(
            id=uuid4(),
            schedule_key="repair-key",
            status="REPAIRED",
            diagnostics=[],
        )
        cand = ScheduleRecordStub(
            id=uuid4(),
            version=2,
            status="REPAIRED",
        )
        return job, cand


class StubJobSnapshotReader:
    """Deterministic in-memory JobSnapshotReader stub."""

    def __init__(self) -> None:
        self.jobs: dict[tuple[str, UUID], JobSnapshot] = {}

    async def get(self, *, tenant_id: str, job_id: JobId) -> JobSnapshot | None:
        return self.jobs.get((tenant_id, job_id.value))


class StubWorkflowAdapter(WorkflowAdapterProtocol):
    """Safe offline workflow adapter stub for security test suite."""

    async def deploy_definition(
        self, bpmn_xml: str, resource_name: str, tenant_id: str
    ) -> EngineDeploymentResult:
        return EngineDeploymentResult(
            deployment_id=f"deploy-{uuid4()}",
            definition_id=f"def-{uuid4()}",
            key=resource_name,
            version=1,
            resource_name=resource_name,
            tenant_id=tenant_id,
        )

    async def start_instance(
        self,
        process_definition_key: str,
        business_key: str | None,
        variables: dict[str, Any] | None,
        tenant_id: str,
    ) -> EngineProcessInstance:
        return EngineProcessInstance(
            instance_id=f"inst-{uuid4()}",
            process_definition_id=f"def-{process_definition_key}",
            process_definition_key=process_definition_key,
            business_key=business_key,
            tenant_id=tenant_id,
            start_time=datetime.now(UTC),
            variables=variables or {},
        )

    async def get_instance(
        self, engine_instance_id: str, tenant_id: str
    ) -> EngineProcessInstance:
        return EngineProcessInstance(
            instance_id=engine_instance_id,
            process_definition_id="def-key",
            process_definition_key="key",
            business_key=None,
            tenant_id=tenant_id,
            start_time=datetime.now(UTC),
            variables={},
        )

    async def get_tasks(
        self,
        tenant_id: str,
        engine_instance_id: str | None = None,
        assignee: str | None = None,
        candidate_group: str | None = None,
    ) -> list[EngineTask]:
        return []

    async def complete_task(
        self,
        engine_task_id: str,
        action: str,
        actor_id: str,
        tenant_id: str,
        variables: dict[str, Any] | None = None,
    ) -> EngineTaskCompletionResult:
        return EngineTaskCompletionResult(
            task_id=engine_task_id,
            action=action,
            actor_id=actor_id,
            completed_at=datetime.now(UTC),
            variables=variables or {},
        )

    async def get_history(
        self, engine_instance_id: str, tenant_id: str
    ) -> list[Any]:
        return []


class StubAnalyticsService(OperationsAnalyticsService):
    """Stub analytics service returning deterministic summaries."""

    async def get_operations_summary(
        self, session: Any, tenant_id: str, **kwargs: Any
    ) -> OperationsDashboardSummary:
        return OperationsDashboardSummary(
            tenant_id=tenant_id,
            plan_progress=PlanProgressSummary(total_tasks=10, completed_tasks=4),
            blockers=[],
            sla_state=SlaStateSummary(status="HEALTHY"),
            schedule_health=ScheduleHealthSummary(status="PUBLISHED"),
            repair_movement=RepairMovementSummary(),
            recent_events=[],
        )


class StubAuditService(AuditQueryService):
    """Stub audit query service strictly filtered by tenant_id."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_event(
        self,
        event_id: str,
        tenant_id: str,
        event_type: str,
        actor_id: str,
    ) -> None:
        self.events.append({
            "event_id": event_id,
            "tenant_id": tenant_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "recorded_at": datetime.now(UTC),
        })

    async def search_events(
        self, session: Any, tenant_id: str, **kwargs: Any
    ) -> list[AuditEventResponse]:
        event_type = kwargs.get("event_type")
        filtered = [
            AuditEventResponse(
                audit_event_id=uuid4(),
                schema_version=1,
                tenant_id=e["tenant_id"],
                actor_principal_id=e["actor_id"],
                event_type=e["event_type"],
                recorded_at=e["recorded_at"],
                details={},
            )
            for e in self.events
            if e["tenant_id"] == tenant_id
            and (event_type is None or e["event_type"] == event_type)
        ]
        return filtered


# -----------------------------------------------------------------------------
# Security Test Harness
# -----------------------------------------------------------------------------
class SecurityTestContext:
    """Encapsulates test identities, tokens, and authenticator configuration."""

    def __init__(self) -> None:
        self.oidc_verifier = FakeGoogleOidcVerifier()
        self.credential_store = InMemoryServiceApiKeyCredentialStore()
        self.identity_resolver = DefaultIdentityResolver()
        self.audit_service = StubAuditService()
        self.analytics_service = StubAnalyticsService()
        self.resource_service = ResourceService()
        self.workflow_adapter = StubWorkflowAdapter()
        self.plan_service = StubPlanService()
        self.workflow_service = StubWorkflowService()
        self.scheduling_service = StubSchedulingService()
        self.job_reader = StubJobSnapshotReader()

        self.authenticator = HttpAuthenticator(
            oidc_verifier=self.oidc_verifier,  # type: ignore[arg-type]
            service_key_verifier=ServiceApiKeyVerifier(credential_store=self.credential_store),
            external_identity_resolver=self.identity_resolver,
            service_identity_resolver=self.identity_resolver,
            credential_store=self.credential_store,
        )

        self._token_map: dict[str, str] = {}
        self._key_map: dict[str, str] = {}

        self._seed_cross_tenant_data()

    def _seed_cross_tenant_data(self) -> None:
        now = datetime.now(UTC)

        # --- Seed Tenant A records ---
        self.plan_service.goals[(TENANT_A, GOAL_A_ID)] = GoalResponse(
            id=GOAL_A_ID,
            tenant_id=TENANT_A,
            objective="Goal Alpha",
            scope="Tenant Alpha Scope",
            deadline=now + timedelta(days=7),
            created_at=now,
            updated_at=now,
        )
        self.plan_service.plans[(TENANT_A, PLAN_A_ID)] = PlanResponse(
            id=PLAN_A_ID,
            goal_id=GOAL_A_ID,
            version_number=1,
            plan_name="Plan Alpha",
            status="VALIDATED",
            is_baseline=False,
            baseline_hash="hash-plan-alpha",
            created_at=now,
            updated_at=now,
        )
        self.plan_service.tasks[(TENANT_A, TASK_A_ID)] = TaskRecordStub(
            id=TASK_A_ID,
            plan_id=PLAN_A_ID,
            temporary_id="task-alpha-1",
            title="Task Alpha",
            status="IN_PROGRESS",
            progress_percentage=50,
            actual_duration_minutes=120,
            blocked_reason=None,
            updated_at=now,
        )
        self.workflow_service.definitions[(TENANT_A, WF_DEF_A_ID)] = WorkflowDefinitionResponse(
            id=WF_DEF_A_ID,
            tenant_id=TENANT_A,
            workflow_key="order-workflow-alpha",
            name="Order Workflow Alpha",
            description="Alpha definition",
            version=1,
            bpmn_process_id="process_alpha",
            artifact_hash="hash_alpha",
            deployment_id=f"deploy-{WF_DEF_A_ID}",
            engine_definition_id=f"eng-{WF_DEF_A_ID}",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.workflow_service.instances[(TENANT_A, WF_INST_A_ID)] = WorkflowInstanceResponse(
            id=WF_INST_A_ID,
            tenant_id=TENANT_A,
            definition_id=WF_DEF_A_ID,
            workflow_key="order-workflow-alpha",
            version=1,
            business_reference="biz-alpha-1",
            correlation_id=None,
            engine_instance_id=f"eng-{WF_INST_A_ID}",
            status=WorkflowInstanceStatus.RUNNING,
            input_variables={"tenant": TENANT_A},
            started_by="usr_admin_a",
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        self.workflow_service.tasks[(TENANT_A, WF_TASK_A_ID)] = WorkflowTaskResponse(
            id=WF_TASK_A_ID,
            tenant_id=TENANT_A,
            workflow_instance_id=WF_INST_A_ID,
            engine_task_id=f"eng-task-{WF_TASK_A_ID}",
            task_definition_key="review_step",
            task_name="Review Step Alpha",
            assignee="usr_approver_a",
            candidate_roles=["approvers"],
            status=WorkflowTaskStatus.PENDING,
            action=None,
            action_by=None,
            action_at=None,
            due_at=None,
            variables={},
            created_at=now,
            updated_at=now,
        )
        self.scheduling_service.jobs[(TENANT_A, SCHED_JOB_A_ID)] = ScheduleJobRecordStub(
            id=SCHED_JOB_A_ID,
            schedule_key="schedule-alpha",
            status="COMPLETED",
            input_snapshot_hash="hash-solve-alpha",
            solver_metadata={},
            diagnostics=[],
            failure_message=None,
            created_at=now,
            completed_at=now,
        )
        self.scheduling_service.schedules[(TENANT_A, SCHEDULE_A_ID)] = ScheduleRecordStub(
            id=SCHEDULE_A_ID,
            version=1,
            status="PUBLISHED",
            published_at=now,
            published_by="usr_scheduler_a",
            supersedes_id=None,
            assignments=[],
        )
        self.job_reader.jobs[(TENANT_A, JOB_A_ID)] = JobSnapshot(
            identity=JobIdentity(
                job_id=JobId(JOB_A_ID),
                tenant_id=TENANT_A,
                job_type=JobType("operations.export"),
            ),
            status=JobStatus.SUCCEEDED,
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        self.resource_service.register_resource(
            tenant_id=TENANT_A,
            payload=ResourceCreate(
                resource_key="res-alpha-1",
                type=ResourceType.PERSON,
                display_label="Technician Alpha",
            ),
        )
        self.audit_service.record_event(
            event_id="evt-alpha-1",
            tenant_id=TENANT_A,
            event_type="plan.created",
            actor_id="usr_planner_a",
        )

        # --- Seed Tenant B records ---
        self.plan_service.goals[(TENANT_B, GOAL_B_ID)] = GoalResponse(
            id=GOAL_B_ID,
            tenant_id=TENANT_B,
            objective="Goal Beta",
            scope="Tenant Beta Scope",
            deadline=now + timedelta(days=14),
            created_at=now,
            updated_at=now,
        )
        self.plan_service.plans[(TENANT_B, PLAN_B_ID)] = PlanResponse(
            id=PLAN_B_ID,
            goal_id=GOAL_B_ID,
            version_number=1,
            plan_name="Plan Beta",
            status="VALIDATED",
            is_baseline=False,
            baseline_hash="hash-plan-beta",
            created_at=now,
            updated_at=now,
        )
        self.plan_service.tasks[(TENANT_B, TASK_B_ID)] = TaskRecordStub(
            id=TASK_B_ID,
            plan_id=PLAN_B_ID,
            temporary_id="task-beta-1",
            title="Task Beta",
            status="IN_PROGRESS",
            progress_percentage=20,
            actual_duration_minutes=60,
            blocked_reason=None,
            updated_at=now,
        )
        self.workflow_service.definitions[(TENANT_B, WF_DEF_B_ID)] = WorkflowDefinitionResponse(
            id=WF_DEF_B_ID,
            tenant_id=TENANT_B,
            workflow_key="order-workflow-beta",
            name="Order Workflow Beta",
            description="Beta definition",
            version=1,
            bpmn_process_id="process_beta",
            artifact_hash="hash_beta",
            deployment_id=f"deploy-{WF_DEF_B_ID}",
            engine_definition_id=f"eng-{WF_DEF_B_ID}",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.workflow_service.instances[(TENANT_B, WF_INST_B_ID)] = WorkflowInstanceResponse(
            id=WF_INST_B_ID,
            tenant_id=TENANT_B,
            definition_id=WF_DEF_B_ID,
            workflow_key="order-workflow-beta",
            version=1,
            business_reference="biz-beta-1",
            correlation_id=None,
            engine_instance_id=f"eng-{WF_INST_B_ID}",
            status=WorkflowInstanceStatus.RUNNING,
            input_variables={"tenant": TENANT_B},
            started_by="usr_admin_b",
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        self.workflow_service.tasks[(TENANT_B, WF_TASK_B_ID)] = WorkflowTaskResponse(
            id=WF_TASK_B_ID,
            tenant_id=TENANT_B,
            workflow_instance_id=WF_INST_B_ID,
            engine_task_id=f"eng-task-{WF_TASK_B_ID}",
            task_definition_key="review_step",
            task_name="Review Step Beta",
            assignee="usr_approver_b",
            candidate_roles=["approvers"],
            status=WorkflowTaskStatus.PENDING,
            action=None,
            action_by=None,
            action_at=None,
            due_at=None,
            variables={},
            created_at=now,
            updated_at=now,
        )
        self.scheduling_service.jobs[(TENANT_B, SCHED_JOB_B_ID)] = ScheduleJobRecordStub(
            id=SCHED_JOB_B_ID,
            schedule_key="schedule-beta",
            status="COMPLETED",
            input_snapshot_hash="hash-solve-beta",
            solver_metadata={},
            diagnostics=[],
            failure_message=None,
            created_at=now,
            completed_at=now,
        )
        self.scheduling_service.schedules[(TENANT_B, SCHEDULE_B_ID)] = ScheduleRecordStub(
            id=SCHEDULE_B_ID,
            version=1,
            status="PUBLISHED",
            published_at=now,
            published_by="usr_scheduler_b",
            supersedes_id=None,
            assignments=[],
        )
        self.job_reader.jobs[(TENANT_B, JOB_B_ID)] = JobSnapshot(
            identity=JobIdentity(
                job_id=JobId(JOB_B_ID),
                tenant_id=TENANT_B,
                job_type=JobType("operations.export"),
            ),
            status=JobStatus.SUCCEEDED,
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        self.resource_service.register_resource(
            tenant_id=TENANT_B,
            payload=ResourceCreate(
                resource_key="res-beta-1",
                type=ResourceType.PERSON,
                display_label="Technician Beta",
            ),
        )
        self.audit_service.record_event(
            event_id="evt-beta-1",
            tenant_id=TENANT_B,
            event_type="plan.created",
            actor_id="usr_planner_b",
        )

    def register_human_persona(
        self,
        persona_name: str,
        principal_id: str,
        tenant_id: str,
        roles: set[str],
        permissions: set[str],
    ) -> tuple[AuthenticatedPrincipal, str]:
        token = f"token_{persona_name}_{tenant_id}"
        sub = f"sub_{persona_name}_{tenant_id}"
        email = f"{persona_name}@{tenant_id}.example.com"

        principal = AuthenticatedPrincipal(
            principal_id=principal_id,
            principal_type=PrincipalType.HUMAN,
            authentication_method=AuthenticationMethod.OIDC,
            tenant_id=tenant_id,
            roles=frozenset(roles),
            permissions=frozenset(permissions),
            issuer="https://accounts.google.com",
            external_subject=sub,
        )

        verified = VerifiedOidcIdentity(
            issuer="https://accounts.google.com",
            subject=sub,
            audiences=frozenset({"e7-test-client-id"}),
            email=email,
        )

        self.oidc_verifier.register_token(token, verified)
        self.identity_resolver.register_oidc_mapping(sub, principal)
        self._token_map[f"{persona_name}_{tenant_id}"] = token
        return principal, token

    def register_service_persona(
        self,
        persona_name: str,
        principal_id: str,
        tenant_id: str,
        roles: set[str],
        permissions: set[str],
    ) -> tuple[AuthenticatedPrincipal, str]:
        cred_id = f"cred-{persona_name}-{tenant_id}"
        secret = "secret-" + ("x" * 45)
        raw_key = f"e7sk_{cred_id}.{secret}"
        sub = f"svc_{persona_name}_{tenant_id}"

        principal = AuthenticatedPrincipal(
            principal_id=principal_id,
            principal_type=PrincipalType.SERVICE,
            authentication_method=AuthenticationMethod.SERVICE_API_KEY,
            tenant_id=tenant_id,
            roles=frozenset(roles),
            permissions=frozenset(permissions),
            issuer="e7-service-key",
            external_subject=sub,
        )

        record = ServiceApiKeyCredentialRecord(
            credential_id=cred_id,
            service_subject=sub,
            secret_digest=compute_secret_digest(secret),
            enabled=True,
        )
        self.credential_store.add_record(record)
        self.identity_resolver.register_service_mapping(cred_id, principal)
        self.identity_resolver.register_service_mapping(sub, principal)
        self._key_map[f"{persona_name}_{tenant_id}"] = raw_key
        return principal, raw_key

    def get_auth_headers(self, persona_name: str, tenant_id: str = TENANT_A) -> dict[str, str]:
        key = f"{persona_name}_{tenant_id}"
        if key in self._token_map:
            return {"Authorization": f"Bearer {self._token_map[key]}"}
        if key in self._key_map:
            return {"X-E7-Service-Key": self._key_map[key]}
        raise ValueError(f"Unknown persona '{key}'")


@pytest.fixture
def security_context() -> SecurityTestContext:
    ctx = SecurityTestContext()

    # 1. Planner persona (Tenant A & Tenant B)
    ctx.register_human_persona(
        "planner", "usr_planner_a", TENANT_A,
        roles={"planner"},
        permissions={"goal:create", "plan:create", "plan:validate", "task:read"},
    )
    ctx.register_human_persona(
        "planner", "usr_planner_b", TENANT_B,
        roles={"planner"},
        permissions={"goal:create", "plan:create", "plan:validate", "task:read"},
    )

    # 2. Approver persona (Tenant A & Tenant B)
    ctx.register_human_persona(
        "approver", "usr_approver_a", TENANT_A,
        roles={"approver"},
        permissions={"plan:approve", "schedule:publish", "workflow:approve", "task:read"},
    )
    ctx.register_human_persona(
        "approver", "usr_approver_b", TENANT_B,
        roles={"approver"},
        permissions={"plan:approve", "schedule:publish", "workflow:approve", "task:read"},
    )

    # 3. Scheduler / Operator persona (Tenant A & Tenant B)
    ctx.register_human_persona(
        "scheduler", "usr_scheduler_a", TENANT_A,
        roles={"scheduler", "operator"},
        permissions={
            "schedule:solve", "schedule:publish", "schedule:repair", "task:execute", "task:read"
        },
    )
    ctx.register_human_persona(
        "scheduler", "usr_scheduler_b", TENANT_B,
        roles={"scheduler", "operator"},
        permissions={
            "schedule:solve", "schedule:publish", "schedule:repair", "task:execute", "task:read"
        },
    )

    # 4. Auditor persona (Tenant A & Tenant B) - Read-only!
    ctx.register_human_persona(
        "auditor", "usr_auditor_a", TENANT_A,
        roles={"auditor"},
        permissions={"audit:read", "analytics:read", "plan:read", "schedule:read"},
    )
    ctx.register_human_persona(
        "auditor", "usr_auditor_b", TENANT_B,
        roles={"auditor"},
        permissions={"audit:read", "analytics:read", "plan:read", "schedule:read"},
    )

    # 5. Tenant Admin persona (Tenant A & Tenant B)
    ctx.register_human_persona(
        "admin", "usr_admin_a", TENANT_A,
        roles={"tenant_admin"},
        permissions={"*"},
    )
    ctx.register_human_persona(
        "admin", "usr_admin_b", TENANT_B,
        roles={"tenant_admin"},
        permissions={"*"},
    )

    # 6. Service Identity persona (Tenant A & Tenant B)
    ctx.register_service_persona(
        "service", "svc_worker_a", TENANT_A,
        roles={"service_identity"},
        permissions={"task:execute", "event:publish"},
    )
    ctx.register_service_persona(
        "service", "svc_worker_b", TENANT_B,
        roles={"service_identity"},
        permissions={"task:execute", "event:publish"},
    )

    return ctx


@pytest.fixture
def security_app(security_context: SecurityTestContext) -> FastAPI:
    """Create dedicated FastAPI app with security middlewares and domain routers."""
    app = FastAPI(
        title="E7 Security Test Harness",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(CorrelationAndLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestBodySizeLimitMiddleware)

    register_api_exception_handlers(app)
    register_idempotency_exception_handler(app)

    from fastapi import Request

    async def get_test_principal(request: Request) -> AuthenticatedPrincipal:
        return await security_context.authenticator.authenticate(request)

    fastapi_mod = pytest.importorskip("fastapi")

    async def get_test_tenant_context(
        principal: AuthenticatedPrincipal = fastapi_mod.Depends(get_test_principal),
    ) -> TenantContext:
        return principal.tenant_context

    # Register domain routes with security dependencies
    domain_router = APIRouter(prefix="/api/v1")

    # In-memory session mock for analytics, audit & calendar
    async def get_dummy_session() -> AsyncGenerator[Any, None]:
        mock_session = AsyncMock()
        mock_exec_result = MagicMock()
        mock_exec_result.scalar_one_or_none.return_value = None
        mock_exec_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_exec_result
        yield mock_session

    register_domain_routes(
        domain_router,
        tenant_dependency=get_test_tenant_context,
        principal_dependency=get_test_principal,
        session_dependency=get_dummy_session,
        plan_service_dependency=lambda: security_context.plan_service,
        workflow_service_dependency=lambda: security_context.workflow_service,
        scheduling_service_dependency=lambda: security_context.scheduling_service,
        resource_service_dependency=lambda: security_context.resource_service,
        reader_dependency=lambda: security_context.job_reader,
        analytics_service=security_context.analytics_service,
        audit_service=security_context.audit_service,
    )
    register_calendar_routes(domain_router, session_dependency=get_dummy_session)
    app.include_router(domain_router)

    return app


@pytest.fixture
def client(security_app: FastAPI) -> TestClient:
    return TestClient(security_app, raise_server_exceptions=False)
