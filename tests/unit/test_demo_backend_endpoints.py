"""Unit and contract tests for Demo-First backend P0 + P1 endpoints and wiring.

Tests:
1. GET /api/v1/auth/me:
   - Unauthenticated access returns 401.
   - Authenticated human principal (OIDC) returns safe identity with email/display name.
   - Authenticated service principal returns machine identity without credentials.
2. Goals APIs:
   - GET /api/v1/goals: tenant isolation, pagination (limit/offset), empty results.
   - GET /api/v1/goals/{goal_id}: detail response, 404 on missing, 404 on cross-tenant.
3. Default Planner HTTP Wiring:
   - POST /api/v1/goals/{goal_id}/generate-plan: empty body invokes default planner, 201 created.
   - POST /api/v1/goals/{goal_id}/generate-plan: explicit PlanSchema body works.
   - RBAC: auditor role rejected with 403.
   - Tenant isolation: cross-tenant goal returns 404.
4. Plans and Tasks Read APIs:
   - GET /api/v1/plans: tenant isolation, goal_id filter, pagination.
   - GET /api/v1/plans/{plan_id}: detail with tasks, dependencies, milestones, risks.
   - GET /api/v1/plans/{plan_id}/tasks: task list for plan.
   - 404 on missing/foreign plan.
5. Resource Catalog API:
   - GET /api/v1/resources: safe fields only, filter by type/active, tenant isolation.
6. Schedule Query APIs:
   - GET /api/v1/schedules: list with status filter, pagination, tenant isolation.
   - GET /api/v1/schedules/{schedule_id}: timetable detail with assignments, 404 on missing/foreign.
7. Worker Compose Configuration Sanity:
   - docker-compose.yml services definition for outbox worker and scheduler-worker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_api_exception_handlers
from app.api.v1.auth import create_auth_router
from app.api.v1.composition import get_default_planner
from app.api.v1.planning import create_planning_router
from app.api.v1.scheduling import create_scheduling_router
from app.core.auth.models import (
    AuthenticatedPrincipal,
    AuthenticationMethod,
    PrincipalType,
    TenantContext,
)
from app.db.models.planning import (
    GoalRecord,
    PlanRecord,
    TaskRecord,
)
from app.db.models.scheduling import (
    ResourceRecord,
    ScheduleAssignmentRecord,
    ScheduleRecord,
)
from app.task_intelligence.schemas import (
    GoalCreate,
    GoalResponse,
    PlanSchema,
    TargetWindow,
)


# ==============================================================================
# In-Memory Test Harness for PlanService
# ==============================================================================
class MockPlanService:
    def __init__(self) -> None:
        self.goals: dict[tuple[str, str], GoalRecord] = {}
        self.plans: dict[tuple[str, str], PlanRecord] = {}
        self.tasks: dict[tuple[str, str], list[TaskRecord]] = {}

    async def create_goal(
        self, tenant_id: str, goal_data: GoalCreate, correlation_id: str | None = None
    ) -> GoalResponse:
        gid = uuid4()
        now = datetime.now(UTC)
        tw_data = goal_data.target_window.model_dump() if goal_data.target_window else None
        rec = GoalRecord(
            id=gid,
            tenant_id=tenant_id,
            objective=goal_data.objective,
            scope=goal_data.scope,
            deadline=goal_data.deadline,
            target_window=tw_data,
            constraints=goal_data.constraints,
            resource_scope=goal_data.resource_scope,
            context_references=goal_data.context_references,
            created_at=now,
            updated_at=now,
        )
        self.goals[(tenant_id, str(gid))] = rec
        return GoalResponse(
            id=gid,
            tenant_id=tenant_id,
            objective=goal_data.objective,
            scope=goal_data.scope,
            deadline=goal_data.deadline,
            target_window=goal_data.target_window,
            constraints=goal_data.constraints,
            resource_scope=goal_data.resource_scope,
            context_references=goal_data.context_references,
            created_at=now,
            updated_at=now,
        )

    async def get_goal(self, tenant_id: str, goal_id: UUID) -> GoalResponse:
        from app.task_intelligence.service import GoalNotFoundError

        key = (tenant_id, str(goal_id))
        if key not in self.goals:
            raise GoalNotFoundError(goal_id)
        g = self.goals[key]
        tw = TargetWindow.model_validate(g.target_window) if g.target_window else None
        return GoalResponse(
            id=g.id,
            tenant_id=g.tenant_id,
            objective=g.objective,
            scope=g.scope,
            deadline=g.deadline,
            target_window=tw,
            constraints=g.constraints,
            resource_scope=g.resource_scope,
            context_references=g.context_references,
            created_at=g.created_at,
            updated_at=g.updated_at,
        )

    async def list_goals(
        self, tenant_id: str, limit: int = 50, offset: int = 0
    ) -> list[GoalResponse]:
        results: list[GoalResponse] = []
        for (t_id, _), g in self.goals.items():
            if t_id == tenant_id:
                tw = TargetWindow.model_validate(g.target_window) if g.target_window else None
                results.append(
                    GoalResponse(
                        id=g.id,
                        tenant_id=g.tenant_id,
                        objective=g.objective,
                        scope=g.scope,
                        deadline=g.deadline,
                        target_window=tw,
                        constraints=g.constraints,
                        resource_scope=g.resource_scope,
                        context_references=g.context_references,
                        created_at=g.created_at,
                        updated_at=g.updated_at,
                    )
                )
        return results[offset : offset + limit]

    async def persist_generated_plan(
        self,
        tenant_id: str,
        goal_id: UUID,
        plan_schema: PlanSchema,
        correlation_id: str | None = None,
    ) -> PlanRecord:
        from app.task_intelligence.service import GoalNotFoundError

        if (tenant_id, str(goal_id)) not in self.goals:
            raise GoalNotFoundError(goal_id)

        pid = uuid4()
        now = datetime.now(UTC)
        plan_rec = PlanRecord(
            id=pid,
            tenant_id=tenant_id,
            goal_id=goal_id,
            version_number=1,
            plan_name=plan_schema.plan_name,
            status="GENERATED",
            is_baseline=False,
            baseline_hash=None,
            created_at=now,
            updated_at=now,
        )
        task_recs: list[TaskRecord] = []
        for t in plan_schema.tasks:
            tid = uuid4()
            task_rec = TaskRecord(
                id=tid,
                plan_id=pid,
                temporary_id=t.temporary_id,
                title=t.title,
                description=t.description,
                wbs_code=t.wbs_code,
                owner_role=t.owner_role,
                owner_reference=t.owner_reference,
                estimated_duration_minutes=t.estimated_duration_minutes,
                actual_duration_minutes=None,
                status="TODO",
                progress_percentage=0,
                blocked_reason=None,
                priority=str(t.priority.value if hasattr(t.priority, "value") else t.priority),
                work_category=t.work_category,
                milestone_id=None,
                created_at=now,
                updated_at=now,
            )
            task_recs.append(task_rec)

        plan_rec.tasks = task_recs
        plan_rec.dependencies = []
        plan_rec.milestones = []
        plan_rec.risks = []
        self.plans[(tenant_id, str(pid))] = plan_rec
        self.tasks[(tenant_id, str(pid))] = task_recs
        return plan_rec

    async def get_plan(
        self, tenant_id: str, plan_id: UUID, load_relations: bool = False
    ) -> PlanRecord:
        from app.task_intelligence.service import PlanNotFoundError

        key = (tenant_id, str(plan_id))
        if key not in self.plans:
            raise PlanNotFoundError(plan_id)
        return self.plans[key]

    async def list_plans(
        self, tenant_id: str, goal_id: UUID | None = None, limit: int = 50, offset: int = 0
    ) -> list[PlanRecord]:
        results: list[PlanRecord] = []
        for (t_id, _), p in self.plans.items():
            if t_id == tenant_id:
                if goal_id is None or p.goal_id == goal_id:
                    results.append(p)
        return results[offset : offset + limit]

    async def get_plan_tasks(self, tenant_id: str, plan_id: UUID) -> list[TaskRecord]:
        from app.task_intelligence.service import PlanNotFoundError

        key = (tenant_id, str(plan_id))
        if key not in self.plans:
            raise PlanNotFoundError(plan_id)
        return self.tasks.get(key, [])


# ==============================================================================
# Test Fixtures & Principal Builders
# ==============================================================================
def _make_human_principal(
    tenant_id: str = "tenant-pilot-001",
    roles: set[str] | None = None,
    email: str = "operator@e7.company.internal",
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id="user-123",
        principal_type=PrincipalType.HUMAN,
        authentication_method=AuthenticationMethod.OIDC,
        tenant_id=tenant_id,
        roles=frozenset(roles or {"operator"}),
        permissions=frozenset({"plan:create", "plan:read", "schedule:read"}),
        issuer="https://accounts.google.com",
        external_subject=email,
    )


def _make_service_principal(
    tenant_id: str = "tenant-pilot-001",
    roles: set[str] | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id="service-scheduler-key",
        principal_type=PrincipalType.SERVICE,
        authentication_method=AuthenticationMethod.SERVICE_API_KEY,
        tenant_id=tenant_id,
        roles=frozenset(roles or {"service"}),
        permissions=frozenset({"schedule:read", "resource:read"}),
    )


# ==============================================================================
# 1. GET /api/v1/auth/me Tests
# ==============================================================================
class TestAuthMeEndpoint:
    def test_unauthenticated_request_rejected(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        async def unauth_principal() -> Any:
            return None

        router = create_auth_router(principal_dependency=unauth_principal)
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "auth.unauthenticated"

    def test_human_oidc_authenticated_user(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        principal = _make_human_principal(
            tenant_id="tenant-pilot-001",
            roles={"planner", "operator"},
            email="alice.smith@institution.edu",
        )

        async def auth_principal() -> AuthenticatedPrincipal:
            return principal

        router = create_auth_router(principal_dependency=auth_principal)
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["principal_id"] == "user-123"
        assert data["tenant_id"] == "tenant-pilot-001"
        assert sorted(data["roles"]) == ["operator", "planner"]
        assert data["principal_type"] == "HUMAN"
        assert data["authentication_method"] == "OIDC"
        assert data["email"] == "alice.smith@institution.edu"
        assert data["display_name"] == "Alice Smith"
        assert data["issuer"] == "https://accounts.google.com"

    def test_service_principal_authenticated(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        principal = _make_service_principal(tenant_id="tenant-pilot-001")

        async def auth_principal() -> AuthenticatedPrincipal:
            return principal

        router = create_auth_router(principal_dependency=auth_principal)
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["principal_id"] == "service-scheduler-key"
        assert data["principal_type"] == "SERVICE"
        assert data["authentication_method"] == "SERVICE_API_KEY"
        assert data["email"] is None
        assert data["display_name"] == "service-scheduler-key"


# ==============================================================================
# 2. Planning and Default Planner HTTP Wiring Tests
# ==============================================================================
class TestPlanningAndDefaultPlannerWiring:
    @pytest.fixture
    def harness(self) -> tuple[TestClient, MockPlanService, dict[str, Any]]:
        app = FastAPI()
        register_api_exception_handlers(app)

        mock_service = MockPlanService()
        state = {
            "tenant_id": "tenant-pilot-001",
            "principal": _make_human_principal(tenant_id="tenant-pilot-001"),
        }

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=str(state["tenant_id"]))

        async def get_principal() -> AuthenticatedPrincipal:
            p = state["principal"]
            assert isinstance(p, AuthenticatedPrincipal)
            return p

        async def get_service() -> Any:
            return mock_service

        router = create_planning_router(
            tenant_dependency=get_tenant,
            plan_service_dependency=get_service,  # type: ignore[arg-type]
            planner_dependency=get_default_planner,
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)
        return client, mock_service, state

    def test_goals_crud_and_tenant_isolation(
        self, harness: tuple[TestClient, MockPlanService, dict[str, Any]]
    ) -> None:
        client, service, state = harness

        # 1. Empty goals list
        resp = client.get("/api/v1/goals")
        assert resp.status_code == 200
        assert resp.json() == []

        # 2. Create goal
        resp = client.post(
            "/api/v1/goals",
            json={
                "objective": "Grade 10 Semester Exam Administration",
                "scope": "Grade 10 Fall 2026 Examination Session",
            },
        )
        assert resp.status_code == 201
        goal_a = resp.json()
        goal_a_id = goal_a["id"]

        # 3. Read single goal
        resp = client.get(f"/api/v1/goals/{goal_a_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == goal_a_id
        assert resp.json()["objective"] == "Grade 10 Semester Exam Administration"

        # 4. List goals
        resp = client.get("/api/v1/goals")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        # 5. Missing goal returns 404
        missing_id = uuid4()
        resp = client.get(f"/api/v1/goals/{missing_id}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "goal.not_found"

        # 6. Switch tenant: Tenant B cannot access Tenant A goal
        state["tenant_id"] = "tenant-other"
        state["principal"] = _make_human_principal(tenant_id="tenant-other")

        resp = client.get(f"/api/v1/goals/{goal_a_id}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "goal.not_found"

        # Tenant B sees empty goals list
        resp = client.get("/api/v1/goals")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_default_planner_wiring_empty_body(
        self, harness: tuple[TestClient, MockPlanService, dict[str, Any]]
    ) -> None:
        client, service, state = harness

        # Create goal first
        resp = client.post(
            "/api/v1/goals",
            json={"objective": "Grade 10 Semester Exam", "scope": "Exam Scope"},
        )
        goal_id = resp.json()["id"]

        # Call generate-plan with empty body (invokes SemesterExamPlanner)
        resp = client.post(
            f"/api/v1/goals/{goal_id}/generate-plan",
            headers={"Content-Type": "application/json"},
            content="{}",
        )
        assert resp.status_code == 201
        plan_data = resp.json()
        assert plan_data["status"] == "GENERATED"
        assert plan_data["plan_name"] == "Operational Plan: Grade 10 Semester Exam"
        plan_id = plan_data["id"]

        # Verify plan read
        resp = client.get(f"/api/v1/plans/{plan_id}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == plan_id
        assert detail["plan_name"] == "Operational Plan: Grade 10 Semester Exam"
        assert len(detail["tasks"]) == 10  # SemesterExamPlanner generates 10 canonical tasks
        assert detail["approval_state"]["status"] == "GENERATED"

        # Verify task list read
        resp = client.get(f"/api/v1/plans/{plan_id}/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) == 10
        task_titles = [t["title"] for t in tasks]
        assert "Exam timetable generation (OR-Tools native)" in task_titles
        assert "Department parallel reviews and approvals" in task_titles

    def test_planner_rbac_auditor_rejected(
        self, harness: tuple[TestClient, MockPlanService, dict[str, Any]]
    ) -> None:
        client, service, state = harness

        # Create goal as operator
        resp = client.post(
            "/api/v1/goals",
            json={"objective": "Test Exam", "scope": "Test Scope"},
        )
        goal_id = resp.json()["id"]

        # Switch to auditor role
        state["principal"] = _make_human_principal(
            tenant_id="tenant-pilot-001", roles={"auditor"}
        )

        resp = client.post(
            f"/api/v1/goals/{goal_id}/generate-plan",
            json={},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "plan.unauthorized"

    def test_plans_list_and_filter(
        self, harness: tuple[TestClient, MockPlanService, dict[str, Any]]
    ) -> None:
        client, service, state = harness

        # Create two goals
        resp1 = client.post("/api/v1/goals", json={"objective": "Goal 1", "scope": "S1"})
        g1_id = resp1.json()["id"]
        resp2 = client.post("/api/v1/goals", json={"objective": "Goal 2", "scope": "S2"})
        g2_id = resp2.json()["id"]

        # Generate plans
        p1 = client.post(f"/api/v1/goals/{g1_id}/generate-plan", json={}).json()
        _ = client.post(f"/api/v1/goals/{g2_id}/generate-plan", json={}).json()

        # List all plans
        resp = client.get("/api/v1/plans")
        assert resp.status_code == 200
        plans = resp.json()
        assert len(plans) == 2

        # Filter by goal_id
        resp = client.get(f"/api/v1/plans?goal_id={g1_id}")
        assert resp.status_code == 200
        plans_g1 = resp.json()
        assert len(plans_g1) == 1
        assert plans_g1[0]["id"] == p1["id"]

        # Cross-tenant plan read returns 404
        state["tenant_id"] = "tenant-different"
        state["principal"] = _make_human_principal(tenant_id="tenant-different")

        resp = client.get(f"/api/v1/plans/{p1['id']}")
        assert resp.status_code == 404
        resp = client.get(f"/api/v1/plans/{p1['id']}/tasks")
        assert resp.status_code == 404


# ==============================================================================
# 3. Resources Catalog API Tests
# ==============================================================================
class TestResourcesCatalogEndpoint:
    @pytest.mark.asyncio
    async def test_resources_catalog_safe_fields_and_filtering(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-pilot-001"
        now = datetime.now(UTC)

        res1 = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            resource_key="res-inv-001",
            type="PERSON",
            display_label="Senior Invigilator Alpha",
            capacity=1,
            skills=["invigilation", "exam_supervision"],
            attributes={"seniority": "senior"},
            active=True,
            created_at=now,
            updated_at=now,
        )
        res2 = ResourceRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            resource_key="res-room-001",
            type="ROOM",
            display_label="Main Examination Hall 1",
            capacity=100,
            skills=["cctv_monitored"],
            attributes={"building": "Block A"},
            active=True,
            created_at=now,
            updated_at=now,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [res1, res2]
        mock_session.execute.return_value = mock_result

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_principal() -> AuthenticatedPrincipal:
            return _make_human_principal(tenant_id=tenant_id)

        async def get_session() -> AsyncMock:
            return mock_session

        async def get_sched_svc() -> Any:
            return MagicMock()

        async def get_res_svc() -> Any:
            return MagicMock()

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=get_session,  # type: ignore[arg-type]
            scheduling_service_dependency=get_sched_svc,  # type: ignore[arg-type]
            resource_service_dependency=get_res_svc,  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        resp = client.get("/api/v1/resources")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        assert items[0]["resource_key"] == "res-inv-001"
        assert items[0]["type"] == "PERSON"
        assert items[0]["display_label"] == "Senior Invigilator Alpha"
        assert items[0]["skills"] == ["invigilation", "exam_supervision"]
        assert items[1]["resource_key"] == "res-room-001"
        assert items[1]["type"] == "ROOM"

        # Check filter params are accepted
        resp = client.get("/api/v1/resources?type=PERSON&active=true")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_resource_room_success(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-demo-real-001"
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_principal() -> AuthenticatedPrincipal:
            return _make_human_principal(tenant_id=tenant_id, roles={"operator"})

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=lambda: mock_session,  # type: ignore[arg-type]
            scheduling_service_dependency=lambda: MagicMock(),  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        payload = {
            "resource_key": "res-hall-a",
            "type": "ROOM",
            "display_label": "Examination Hall A",
            "capacity": 80,
            "skills": ["cctv_monitored", "air_conditioned"],
            "attributes": {"building": "Block B"},
            "active": True,
        }
        resp = client.post("/api/v1/resources", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["resource_key"] == "res-hall-a"
        assert data["type"] == "ROOM"
        assert data["display_label"] == "Examination Hall A"
        assert data["capacity"] == 80
        assert data["attributes"]["provenance"] == "USER_CREATED"
        assert data["attributes"]["source"] == "LOCAL"
        assert mock_session.add.called
        assert mock_session.flush.called

    @pytest.mark.asyncio
    async def test_create_resource_rbac_auditor_rejected(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-demo-real-001"
        mock_session = AsyncMock()

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_principal() -> AuthenticatedPrincipal:
            p = _make_human_principal(tenant_id=tenant_id, roles={"auditor"})
            return p.model_copy(update={"permissions": frozenset({"plan:read"})})

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=lambda: mock_session,  # type: ignore[arg-type]
            scheduling_service_dependency=lambda: MagicMock(),  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        payload = {
            "resource_key": "res-hall-a",
            "type": "ROOM",
            "display_label": "Examination Hall A",
            "capacity": 80,
        }
        resp = client.post("/api/v1/resources", json=payload)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "resource.unauthorized"

    @pytest.mark.asyncio
    async def test_create_resource_validation_person_rejected(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-demo-real-001"
        mock_session = AsyncMock()

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_principal() -> AuthenticatedPrincipal:
            return _make_human_principal(tenant_id=tenant_id, roles={"operator"})

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=lambda: mock_session,  # type: ignore[arg-type]
            scheduling_service_dependency=lambda: MagicMock(),  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        payload = {
            "resource_key": "res-fake-staff",
            "type": "PERSON",
            "display_label": "Fake Staff Member",
            "capacity": 1,
        }
        resp = client.post("/api/v1/resources", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "resource.invalid_type"

    @pytest.mark.asyncio
    async def test_create_resource_validation_room_capacity_zero(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-demo-real-001"
        mock_session = AsyncMock()

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_principal() -> AuthenticatedPrincipal:
            return _make_human_principal(tenant_id=tenant_id, roles={"operator"})

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=lambda: mock_session,  # type: ignore[arg-type]
            scheduling_service_dependency=lambda: MagicMock(),  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        payload = {
            "resource_key": "res-zero-room",
            "type": "ROOM",
            "display_label": "Zero Capacity Room",
            "capacity": 0,
        }
        resp = client.post("/api/v1/resources", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "resource.invalid_capacity"

    @pytest.mark.asyncio
    async def test_create_resource_duplicate_key_rejected(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-demo-real-001"
        mock_session = AsyncMock()
        mock_result = MagicMock()
        existing_rec = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_rec
        mock_session.execute.return_value = mock_result

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_principal() -> AuthenticatedPrincipal:
            return _make_human_principal(tenant_id=tenant_id, roles={"operator"})

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=lambda: mock_session,  # type: ignore[arg-type]
            scheduling_service_dependency=lambda: MagicMock(),  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        payload = {
            "resource_key": "res-existing-hall",
            "type": "ROOM",
            "display_label": "Existing Hall",
            "capacity": 50,
        }
        resp = client.post("/api/v1/resources", json=payload)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "resource.duplicate_key"

    @pytest.mark.asyncio
    async def test_create_resource_availability_success_and_validation(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-demo-real-001"
        res_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock(id=res_id)
        mock_session.execute.return_value = mock_result

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_principal() -> AuthenticatedPrincipal:
            return _make_human_principal(tenant_id=tenant_id, roles={"operator"})

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=lambda: mock_session,  # type: ignore[arg-type]
            scheduling_service_dependency=lambda: MagicMock(),  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        # 1. Success
        payload = {
            "resource_id": str(res_id),
            "interval_type": "AVAILABLE",
            "start_at": "2026-11-01T09:00:00Z",
            "end_at": "2026-11-01T17:00:00Z",
            "reason": "Standard operational window",
        }
        resp = client.post("/api/v1/resources/availability", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["resource_id"] == str(res_id)
        assert data["interval_type"] == "AVAILABLE"

        # 2. Invalid interval order
        bad_payload = {
            "resource_id": str(res_id),
            "interval_type": "AVAILABLE",
            "start_at": "2026-11-01T17:00:00Z",
            "end_at": "2026-11-01T09:00:00Z",
        }
        resp = client.post("/api/v1/resources/availability", json=bad_payload)
        assert resp.status_code == 422

        # 3. Resource not found
        mock_result.scalar_one_or_none.return_value = None
        resp = client.post("/api/v1/resources/availability", json=payload)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "resource.not_found"


# ==============================================================================
# 4. Schedule Query APIs Tests
# ==============================================================================
class TestScheduleQueryEndpoints:
    @pytest.mark.asyncio
    async def test_schedule_list_and_detail(self) -> None:
        app = FastAPI()
        register_api_exception_handlers(app)

        tenant_id = "tenant-pilot-001"
        now = datetime.now(UTC)
        sched_id = uuid4()
        asgn_id = uuid4()

        asgn = ScheduleAssignmentRecord(
            id=asgn_id,
            tenant_id=tenant_id,
            schedule_id=sched_id,
            activity_id="exam-session-math",
            resource_id="res-room-001",
            start_at=now,
            end_at=now,
            is_locked=False,
            metadata_json={"room_type": "HALL"},
            created_at=now,
        )
        sched = ScheduleRecord(
            id=sched_id,
            tenant_id=tenant_id,
            schedule_key="sched-001",
            version=1,
            status="PUBLISHED",
            source_job_id=uuid4(),
            input_snapshot_hash="mock-hash-1234",
            solver_profile_version=1,
            solver_metadata={},
            score_components={},
            supersedes_id=None,
            published_at=now,
            published_by="admin",
            repair_reason=None,
            repair_scope={},
            created_at=now,
            updated_at=now,
        )
        sched.assignments = [asgn]

        mock_session = AsyncMock()
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = [sched]

        detail_result = MagicMock()
        detail_result.scalar_one_or_none.return_value = sched

        async def mock_execute(query: Any) -> Any:
            # Distinguish list query from detail query
            if "schedule_assignments" in str(query) or "schedules.id =" in str(query):
                return detail_result
            return list_result

        mock_session.execute.side_effect = mock_execute

        async def get_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id)

        async def get_principal() -> AuthenticatedPrincipal:
            return _make_human_principal(tenant_id=tenant_id)

        async def get_session() -> AsyncMock:
            return mock_session

        async def get_sched_svc() -> Any:
            return MagicMock()

        async def get_res_svc() -> Any:
            return MagicMock()

        router = create_scheduling_router(
            tenant_dependency=get_tenant,
            session_dependency=get_session,  # type: ignore[arg-type]
            scheduling_service_dependency=get_sched_svc,  # type: ignore[arg-type]
            resource_service_dependency=get_res_svc,  # type: ignore[arg-type]
            principal_dependency=get_principal,
        )
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)

        # 1. List schedules
        resp = client.get("/api/v1/schedules")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["id"] == str(sched_id)
        assert items[0]["status"] == "PUBLISHED"
        assert items[0]["version"] == 1

        # 2. Detail schedule
        resp = client.get(f"/api/v1/schedules/{sched_id}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == str(sched_id)
        assert detail["status"] == "PUBLISHED"
        assert len(detail["assignments"]) == 1
        assert detail["assignments"][0]["activity_id"] == "exam-session-math"
        assert detail["assignments"][0]["resource_id"] == "res-room-001"

        # 3. Not found returns 404
        detail_result.scalar_one_or_none.return_value = None
        resp = client.get(f"/api/v1/schedules/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "schedule.not_found"


# ==============================================================================
# 5. Worker Compose Configuration Sanity
# ==============================================================================
class TestWorkerComposeSanity:
    def test_worker_services_configured_for_demo(self) -> None:
        import yaml

        with open("docker-compose.yml", encoding="utf-8") as f:
            compose = yaml.safe_load(f)

        services = compose.get("services", {})
        assert "worker" in services, "worker service must exist in docker-compose.yml"
        assert (
            "scheduler-worker" in services
        ), "scheduler-worker service must exist in docker-compose.yml"

        # Outbox worker configuration
        worker = services["worker"]
        assert worker.get("restart") == "unless-stopped"
        assert "profiles" not in worker, "worker must start by default without profiles"
        worker_env = worker.get("environment", {})
        assert "E7_DATABASE_URL" in worker_env
        assert "E7_RABBITMQ_URL" in worker_env

        # Scheduler worker configuration
        sched_worker = services["scheduler-worker"]
        assert sched_worker.get("restart") == "unless-stopped"
        assert "profiles" not in sched_worker
        assert "app.scheduling.worker" in sched_worker.get("command", "")
        sched_env = sched_worker.get("environment", {})
        assert "E7_DATABASE_URL" in sched_env
        assert "E7_SCHEDULER_CLAIM_BATCH_SIZE" in sched_env
