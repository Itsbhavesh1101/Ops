"""Shared fixtures and UAT environment context for Phase 8G.

Configures:
- Synthetic Tenants: uat-tenant-alpha, uat-tenant-beta.
- 5 Canonical UAT Personas + Negative/Isolation Personas:
  1. UAT_PLANNER (planner)
  2. UAT_APPROVER (approver)
  3. UAT_SCHEDULER (scheduler, operator)
  4. UAT_AUDITOR (auditor)
  5. UAT_TENANT_ADMIN (tenant_admin)
  6. UAT_VIEWER (viewer)
  7. UAT_FOREIGN_TENANT_USER (planner on uat-tenant-beta)
- Realistic synthetic fixture representing:
  "Semester Exam Readiness and Scheduling"
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI, Request
from starlette.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.api.idempotency import register_idempotency_exception_handler
from app.api.v1.calendar import register_calendar_routes
from app.api.v1.composition import register_domain_routes
from app.api.v1.planning import PlanResponse
from app.core.auth.models import AuthenticatedPrincipal, TenantContext
from app.core.middleware import (
    CorrelationAndLoggingMiddleware,
    RequestBodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.resources.schemas import ResourceCreate, ResourceType
from app.task_intelligence.graph.models import CPMScheduleResult
from app.task_intelligence.schemas import GoalResponse
from app.task_intelligence.service import PlanNotFoundError
from app.workflow.schemas import (
    WorkflowDefinitionResponse,
    WorkflowInstanceResponse,
    WorkflowInstanceStatus,
    WorkflowTaskResponse,
    WorkflowTaskStatus,
)
from tests.security.conftest import (
    JobNotFoundError,
    ScheduleJobRecordStub,
    ScheduleRecordStub,
    SecurityTestContext,
    StubPlanService,
    StubSchedulingService,
    StubWorkflowService,
    TaskRecordStub,
)

# -----------------------------------------------------------------------------
# Canonical UAT Tenants
# -----------------------------------------------------------------------------
UAT_TENANT_ALPHA = "uat-tenant-alpha"
UAT_TENANT_BETA = "uat-tenant-beta"

# -----------------------------------------------------------------------------
# Seed IDs for Academic Cycle "Semester Exam Readiness and Scheduling"
# -----------------------------------------------------------------------------
UAT_GOAL_ALPHA_ID = UUID("11111111-0000-4000-a000-000000000001")
UAT_PLAN_ALPHA_ID = UUID("22222222-0000-4000-a000-000000000001")
UAT_TASK_ALPHA_ID = UUID("33333333-0000-4000-a000-000000000001")
UAT_WF_DEF_ALPHA_ID = UUID("44444444-0000-4000-a000-000000000001")
UAT_WF_INST_ALPHA_ID = UUID("55555555-0000-4000-a000-000000000001")
UAT_WF_TASK_ALPHA_ID = UUID("66666666-0000-4000-a000-000000000001")
UAT_SCHED_JOB_ALPHA_ID = UUID("77777777-0000-4000-a000-000000000001")
UAT_SCHEDULE_ALPHA_ID = UUID("88888888-0000-4000-a000-000000000001")

UAT_GOAL_BETA_ID = UUID("11111111-0000-4000-b000-000000000002")
UAT_PLAN_BETA_ID = UUID("22222222-0000-4000-b000-000000000002")
UAT_TASK_BETA_ID = UUID("33333333-0000-4000-b000-000000000002")
UAT_WF_DEF_BETA_ID = UUID("44444444-0000-4000-b000-000000000002")
UAT_WF_INST_BETA_ID = UUID("55555555-0000-4000-b000-000000000002")
UAT_WF_TASK_BETA_ID = UUID("66666666-0000-4000-b000-000000000002")
UAT_SCHED_JOB_BETA_ID = UUID("77777777-0000-4000-b000-000000000002")
UAT_SCHEDULE_BETA_ID = UUID("88888888-0000-4000-b000-000000000002")


class UatStubWorkflowService(StubWorkflowService):
    """Workflow service stub supporting optional keyword arguments like version."""

    async def start_instance(
        self,
        tenant_id: str,
        workflow_key: str,
        business_key: str | None = None,
        variables: dict[str, Any] | None = None,
        actor_id: str | None = None,
        **kwargs: Any,
    ) -> WorkflowInstanceResponse:
        inst_id = uuid4()
        now = datetime.now(UTC)
        inst = WorkflowInstanceResponse(
            id=inst_id,
            tenant_id=tenant_id,
            definition_id=uuid4(),
            workflow_key=workflow_key,
            version=kwargs.get("version") or 1,
            business_reference=business_key,
            correlation_id=None,
            engine_instance_id=f"eng-{inst_id}",
            status=WorkflowInstanceStatus.RUNNING,
            input_variables=variables or {},
            started_by=actor_id or "system",
            started_at=now,
            completed_at=None,
            created_at=now,
            updated_at=now,
        )
        self.instances[(tenant_id, inst_id)] = inst
        return inst


class UatStubPlanService(StubPlanService):
    """Plan service stub providing complete CPMScheduleResult."""

    async def get_critical_path(
        self, tenant_id: str, plan_id: UUID, **kwargs: Any
    ) -> CPMScheduleResult:
        if (tenant_id, plan_id) not in self.plans:
            raise PlanNotFoundError(f"Plan {plan_id} not found for tenant {tenant_id}")
        return CPMScheduleResult(
            project_duration_minutes=180,
            schedule={},
            critical_path=["task-exam-roster-01"],
            critical_paths=[["task-exam-roster-01"]],
        )


class UatStubSchedulingService(StubSchedulingService):
    """Scheduling service stub providing idempotent publication semantics."""

    def __init__(self) -> None:
        super().__init__()
        self.published_jobs: dict[tuple[str, UUID], Any] = {}

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
        if (tenant_id, job_id) in self.published_jobs:
            return self.published_jobs[(tenant_id, job_id)]

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
        self.published_jobs[(tenant_id, job_id)] = sched
        return sched


class UatTestContext(SecurityTestContext):
    """Encapsulates test identities, tokens, and domain services for UAT."""

    def __init__(self) -> None:
        super().__init__()
        self.workflow_service = UatStubWorkflowService()
        self.plan_service = UatStubPlanService()
        self.scheduling_service = UatStubSchedulingService()
        self._register_uat_personas()
        self._seed_academic_cycle_dataset()

    def _register_uat_personas(self) -> None:
        # 1. UAT_PLANNER (Tenant Alpha)
        self.register_human_persona(
            "planner",
            "user:uat_planner_alpha",
            UAT_TENANT_ALPHA,
            roles={"planner"},
            permissions={"goal:create", "plan:create", "plan:validate", "task:read"},
        )

        # 2. UAT_APPROVER (Tenant Alpha)
        self.register_human_persona(
            "approver",
            "user:uat_approver_alpha",
            UAT_TENANT_ALPHA,
            roles={"approver"},
            permissions={"plan:approve", "schedule:publish", "workflow:approve", "task:read"},
        )

        # 3. UAT_SCHEDULER (Tenant Alpha)
        self.register_human_persona(
            "scheduler",
            "user:uat_scheduler_alpha",
            UAT_TENANT_ALPHA,
            roles={"scheduler", "operator"},
            permissions={
                "schedule:solve",
                "schedule:publish",
                "schedule:repair",
                "task:execute",
                "task:read",
            },
        )

        # 4. UAT_AUDITOR (Tenant Alpha)
        self.register_human_persona(
            "auditor",
            "user:uat_auditor_alpha",
            UAT_TENANT_ALPHA,
            roles={"auditor"},
            permissions={"audit:read", "analytics:read", "plan:read", "schedule:read"},
        )

        # 5. UAT_TENANT_ADMIN (Tenant Alpha)
        self.register_human_persona(
            "admin",
            "user:uat_admin_alpha",
            UAT_TENANT_ALPHA,
            roles={"tenant_admin"},
            permissions={"*"},
        )

        # 6. UAT_VIEWER (Tenant Alpha)
        self.register_human_persona(
            "viewer",
            "user:uat_viewer_alpha",
            UAT_TENANT_ALPHA,
            roles={"viewer"},
            permissions={"plan:read", "schedule:read"},
        )

        # 7. UAT_FOREIGN_TENANT_USER (Tenant Beta)
        self.register_human_persona(
            "foreign_user",
            "user:uat_planner_beta",
            UAT_TENANT_BETA,
            roles={"planner"},
            permissions={"goal:create", "plan:create", "plan:validate", "task:read"},
        )
        self.register_human_persona(
            "foreign_auditor",
            "user:uat_auditor_beta",
            UAT_TENANT_BETA,
            roles={"auditor"},
            permissions={"audit:read", "analytics:read", "plan:read", "schedule:read"},
        )
        self.register_human_persona(
            "foreign_approver",
            "user:uat_approver_beta",
            UAT_TENANT_BETA,
            roles={"approver"},
            permissions={"plan:approve", "schedule:publish", "workflow:approve", "task:read"},
        )

    def _seed_academic_cycle_dataset(self) -> None:
        """Seed realistic Semester Exam Readiness and Scheduling dataset."""
        now = datetime.now(UTC)

        # --- Seed UAT Tenant Alpha: Semester Exam Readiness ---
        self.plan_service.goals[(UAT_TENANT_ALPHA, UAT_GOAL_ALPHA_ID)] = GoalResponse(
            id=UAT_GOAL_ALPHA_ID,
            tenant_id=UAT_TENANT_ALPHA,
            objective="Grade 10 Semester Examination Readiness and Timetabling",
            scope="Academic Operations 2026-Q4",
            deadline=now + timedelta(days=30),
            created_at=now,
            updated_at=now,
        )
        self.plan_service.plans[(UAT_TENANT_ALPHA, UAT_PLAN_ALPHA_ID)] = PlanResponse(
            id=UAT_PLAN_ALPHA_ID,
            goal_id=UAT_GOAL_ALPHA_ID,
            version_number=1,
            plan_name="Exam Readiness Master Plan",
            status="VALIDATED",
            is_baseline=False,
            baseline_hash="hash-exam-plan-alpha",
            created_at=now,
            updated_at=now,
        )
        self.plan_service.tasks[(UAT_TENANT_ALPHA, UAT_TASK_ALPHA_ID)] = TaskRecordStub(
            id=UAT_TASK_ALPHA_ID,
            plan_id=UAT_PLAN_ALPHA_ID,
            temporary_id="task-exam-roster-01",
            title="Finalize Cohort Rosters and Room Allocations",
            status="IN_PROGRESS",
            progress_percentage=60,
            actual_duration_minutes=180,
            blocked_reason=None,
            updated_at=now,
        )
        self.workflow_service.definitions[(UAT_TENANT_ALPHA, UAT_WF_DEF_ALPHA_ID)] = (
            WorkflowDefinitionResponse(
                id=UAT_WF_DEF_ALPHA_ID,
                tenant_id=UAT_TENANT_ALPHA,
                workflow_key="semester_exam_readiness",
                name="Semester Exam Readiness Workflow",
                description="Orchestrates pre-exam inspections and approvals",
                version=1,
                bpmn_process_id="semesterExamReadinessProcess",
                artifact_hash="hash_exam_bpmn_alpha",
                deployment_id=f"deploy-{UAT_WF_DEF_ALPHA_ID}",
                engine_definition_id=f"eng-{UAT_WF_DEF_ALPHA_ID}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        self.workflow_service.instances[(UAT_TENANT_ALPHA, UAT_WF_INST_ALPHA_ID)] = (
            WorkflowInstanceResponse(
                id=UAT_WF_INST_ALPHA_ID,
                tenant_id=UAT_TENANT_ALPHA,
                definition_id=UAT_WF_DEF_ALPHA_ID,
                workflow_key="semester_exam_readiness",
                version=1,
                business_reference="exam-cycle-2026-sem1",
                correlation_id="corr-exam-001",
                engine_instance_id=f"eng-inst-{UAT_WF_INST_ALPHA_ID}",
                status=WorkflowInstanceStatus.RUNNING,
                input_variables={"tenant": UAT_TENANT_ALPHA, "cohort": "grade-10"},
                started_by="user:uat_planner_alpha",
                started_at=now,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        self.workflow_service.tasks[(UAT_TENANT_ALPHA, UAT_WF_TASK_ALPHA_ID)] = (
            WorkflowTaskResponse(
                id=UAT_WF_TASK_ALPHA_ID,
                tenant_id=UAT_TENANT_ALPHA,
                workflow_instance_id=UAT_WF_INST_ALPHA_ID,
                engine_task_id=f"eng-task-{UAT_WF_TASK_ALPHA_ID}",
                task_definition_key="approve_exam_roster",
                task_name="Approve Exam Roster and Invigilator Assignments",
                assignee="user:uat_approver_alpha",
                candidate_roles=["approver", "approvers"],
                status=WorkflowTaskStatus.PENDING,
                action=None,
                action_by=None,
                action_at=None,
                due_at=now + timedelta(days=2),
                variables={},
                created_at=now,
                updated_at=now,
            )
        )
        self.scheduling_service.jobs[(UAT_TENANT_ALPHA, UAT_SCHED_JOB_ALPHA_ID)] = (
            ScheduleJobRecordStub(
                id=UAT_SCHED_JOB_ALPHA_ID,
                schedule_key="semester-exam-timetabling",
                status="COMPLETED",
                input_snapshot_hash="hash-exam-solve-alpha",
                solver_metadata={"solve_time_seconds": 1.25, "solver": "CP-SAT"},
                diagnostics=[],
                failure_message=None,
                created_at=now,
                completed_at=now,
            )
        )
        self.scheduling_service.schedules[(UAT_TENANT_ALPHA, UAT_SCHEDULE_ALPHA_ID)] = (
            ScheduleRecordStub(
                id=UAT_SCHEDULE_ALPHA_ID,
                version=1,
                status="PUBLISHED",
                published_at=now,
                published_by="user:uat_scheduler_alpha",
                supersedes_id=None,
                assignments=[],
            )
        )
        self.scheduling_service.published_jobs[(UAT_TENANT_ALPHA, UAT_SCHED_JOB_ALPHA_ID)] = (
            self.scheduling_service.schedules[(UAT_TENANT_ALPHA, UAT_SCHEDULE_ALPHA_ID)]
        )
        self.resource_service.register_resource(
            tenant_id=UAT_TENANT_ALPHA,
            payload=ResourceCreate(
                resource_key="res-room-alpha-001",
                type=ResourceType.ROOM,
                display_label="Exam Hall Alpha",
            ),
        )
        self.audit_service.record_event(
            event_id="evt-exam-goal-alpha",
            tenant_id=UAT_TENANT_ALPHA,
            event_type="goal.created",
            actor_id="user:uat_planner_alpha",
        )

        # --- Seed UAT Tenant Beta (Foreign Tenant) ---
        self.plan_service.goals[(UAT_TENANT_BETA, UAT_GOAL_BETA_ID)] = GoalResponse(
            id=UAT_GOAL_BETA_ID,
            tenant_id=UAT_TENANT_BETA,
            objective="Tenant Beta Academic Goal",
            scope="Beta Operations",
            deadline=now + timedelta(days=14),
            created_at=now,
            updated_at=now,
        )
        self.plan_service.plans[(UAT_TENANT_BETA, UAT_PLAN_BETA_ID)] = PlanResponse(
            id=UAT_PLAN_BETA_ID,
            goal_id=UAT_GOAL_BETA_ID,
            version_number=1,
            plan_name="Beta Plan",
            status="VALIDATED",
            is_baseline=False,
            baseline_hash="hash-plan-beta",
            created_at=now,
            updated_at=now,
        )
        self.plan_service.tasks[(UAT_TENANT_BETA, UAT_TASK_BETA_ID)] = TaskRecordStub(
            id=UAT_TASK_BETA_ID,
            plan_id=UAT_PLAN_BETA_ID,
            temporary_id="task-beta-01",
            title="Beta Task",
            status="IN_PROGRESS",
            progress_percentage=10,
            actual_duration_minutes=30,
            blocked_reason=None,
            updated_at=now,
        )
        self.workflow_service.definitions[(UAT_TENANT_BETA, UAT_WF_DEF_BETA_ID)] = (
            WorkflowDefinitionResponse(
                id=UAT_WF_DEF_BETA_ID,
                tenant_id=UAT_TENANT_BETA,
                workflow_key="beta_workflow",
                name="Beta Definition",
                description="Beta workflow",
                version=1,
                bpmn_process_id="betaProcess",
                artifact_hash="hash_beta",
                deployment_id=f"deploy-{UAT_WF_DEF_BETA_ID}",
                engine_definition_id=f"eng-{UAT_WF_DEF_BETA_ID}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        self.workflow_service.instances[(UAT_TENANT_BETA, UAT_WF_INST_BETA_ID)] = (
            WorkflowInstanceResponse(
                id=UAT_WF_INST_BETA_ID,
                tenant_id=UAT_TENANT_BETA,
                definition_id=UAT_WF_DEF_BETA_ID,
                workflow_key="beta_workflow",
                version=1,
                business_reference="beta-ref-1",
                correlation_id=None,
                engine_instance_id=f"eng-inst-{UAT_WF_INST_BETA_ID}",
                status=WorkflowInstanceStatus.RUNNING,
                input_variables={"tenant": UAT_TENANT_BETA},
                started_by="user:uat_planner_beta",
                started_at=now,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        self.workflow_service.tasks[(UAT_TENANT_BETA, UAT_WF_TASK_BETA_ID)] = (
            WorkflowTaskResponse(
                id=UAT_WF_TASK_BETA_ID,
                tenant_id=UAT_TENANT_BETA,
                workflow_instance_id=UAT_WF_INST_BETA_ID,
                engine_task_id=f"eng-task-{UAT_WF_TASK_BETA_ID}",
                task_definition_key="beta_step",
                task_name="Beta Review",
                assignee="user:uat_planner_beta",
                candidate_roles=["approver"],
                status=WorkflowTaskStatus.PENDING,
                action=None,
                action_by=None,
                action_at=None,
                due_at=None,
                variables={},
                created_at=now,
                updated_at=now,
            )
        )
        self.scheduling_service.jobs[(UAT_TENANT_BETA, UAT_SCHED_JOB_BETA_ID)] = (
            ScheduleJobRecordStub(
                id=UAT_SCHED_JOB_BETA_ID,
                schedule_key="beta-schedule",
                status="COMPLETED",
                input_snapshot_hash="hash-beta-solve",
                solver_metadata={},
                diagnostics=[],
                failure_message=None,
                created_at=now,
                completed_at=now,
            )
        )
        self.scheduling_service.schedules[(UAT_TENANT_BETA, UAT_SCHEDULE_BETA_ID)] = (
            ScheduleRecordStub(
                id=UAT_SCHEDULE_BETA_ID,
                version=1,
                status="PUBLISHED",
                published_at=now,
                published_by="user:uat_planner_beta",
                supersedes_id=None,
                assignments=[],
            )
        )
        self.scheduling_service.published_jobs[(UAT_TENANT_BETA, UAT_SCHED_JOB_BETA_ID)] = (
            self.scheduling_service.schedules[(UAT_TENANT_BETA, UAT_SCHEDULE_BETA_ID)]
        )
        self.audit_service.record_event(
            event_id="evt-beta-event-1",
            tenant_id=UAT_TENANT_BETA,
            event_type="goal.created",
            actor_id="user:uat_planner_beta",
        )


@pytest.fixture
def uat_context() -> UatTestContext:
    """Provides populated UatTestContext with 7 personas across alpha and beta tenants."""
    return UatTestContext()


@pytest.fixture
def uat_app(uat_context: UatTestContext) -> FastAPI:
    """Dedicated FastAPI app with security middlewares, canonical error handling, and API routes."""
    app = FastAPI(
        title="E7 UAT Acceptance Testing Harness",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(CorrelationAndLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestBodySizeLimitMiddleware)

    register_api_exception_handlers(app)
    register_idempotency_exception_handler(app)

    async def get_uat_principal(request: Request) -> AuthenticatedPrincipal:
        return await uat_context.authenticator.authenticate(request)

    async def get_uat_tenant_context(
        principal: AuthenticatedPrincipal = pytest.importorskip("fastapi").Depends(
            get_uat_principal
        ),
    ) -> TenantContext:
        return principal.tenant_context

    domain_router = APIRouter(prefix="/api/v1")

    async def get_dummy_session() -> AsyncGenerator[Any, None]:
        yield None

    register_domain_routes(
        domain_router,
        tenant_dependency=get_uat_tenant_context,
        principal_dependency=get_uat_principal,
        session_dependency=get_dummy_session,
        plan_service_dependency=lambda: uat_context.plan_service,
        workflow_service_dependency=lambda: uat_context.workflow_service,
        scheduling_service_dependency=lambda: uat_context.scheduling_service,
        resource_service_dependency=lambda: uat_context.resource_service,
        reader_dependency=lambda: uat_context.job_reader,
        analytics_service=uat_context.analytics_service,
        audit_service=uat_context.audit_service,
    )
    register_calendar_routes(domain_router)
    app.include_router(domain_router)

    return app


@pytest.fixture
def client(uat_app: FastAPI) -> TestClient:
    """TestClient configured with uat_app."""
    return TestClient(uat_app, raise_server_exceptions=False)
